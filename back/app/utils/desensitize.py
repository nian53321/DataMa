# -*- coding: utf-8 -*-
"""脱敏工具：根据配置规则对字段值进行脱敏处理
支持算法：
  mask_all    全部替换为 mask_char
  mask_middle 保留首尾 N 位，中间替换
  mask_head   替换前 N 位（keep_head 控制替换位数）
  mask_tail   替换后 N 位（keep_tail 控制替换位数，0 则全部替换为单个 mask_char）
  mask_email  邮箱专用：@ 前部分做 mask_middle
  hash        HMAC-SHA256 前 8 位（密钥自动生成并持久化；极端情况回退无盐 SHA256）
  redact      替换为 [REDACTED]
"""
import hashlib
import hmac
import json
import logging
import os
import re
import threading

# os.O_BINARY 仅存在于 Windows，Unix 缺失时置 0 兼容
_O_BINARY = getattr(os, "O_BINARY", 0)


_logger = logging.getLogger(__name__)

# 模块级缓存：避免每次查询都读 DB
_cache_lock = threading.Lock()
_cache = {
    "enabled": True,
    "rules": {},  # field_key -> rule_dict
    "loaded": False,
}

# HMAC 警告标志：未配置密钥时首次记录警告，避免日志刷屏
_hmac_warned = False


def _load_or_create_desens_key():
    """加载或自动生成脱敏 HMAC 密钥文件（默认 BASE_DIR/desens.key，0600）

    密钥为 32 字节随机数（hex 编码 64 字符 ASCII 存储）。
    - 持久化保证重启后同一值的 hash 脱敏结果稳定一致
    - O_EXCL 原子创建，多进程并发首启时只有一方生成，其余读取
    - 仅在 app context 下工作；无 context（纯函数调用）返回 None

    :returns: 32 字节密钥；不可用时返回 None
    """
    try:
        from flask import current_app
        path = current_app.config.get("DESENS_KEY_PATH") or os.path.join(
            current_app.config["BASE_DIR"], "desens.key")
    except Exception:
        return None

    def _read(path):
        with open(path, "r", encoding="ascii") as f:
            text = f.read().strip()
        key = bytes.fromhex(text)
        return key if len(key) == 32 else None

    try:
        if os.path.exists(path):
            key = _read(path)
            if key is not None:
                return key
            _logger.warning("脱敏密钥文件长度异常，将重新生成: %s", path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        key = os.urandom(32)
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
        try:
            os.write(fd, key.hex().encode("ascii"))
        finally:
            os.close(fd)
        return key
    except FileExistsError:
        # 竞态：另一进程刚生成，读取其密钥，避免多份不一致密钥
        try:
            return _read(path)
        except Exception:
            return None
    except Exception as e:
        _logger.warning("脱敏密钥文件加载/生成失败，hash 回退无盐 SHA256: %s", e)
        return None


def _get_hmac_key():
    """获取脱敏 HMAC 密钥（bytes）

    优先级：
    1. 环境变量 DESENS_HMAC_KEY（显式配置，多实例部署时共享）
    2. 自动生成并持久化的密钥文件（DESENS_KEY_PATH / BASE_DIR/desens.key）
    3. 均不可用 → None（回退无盐 SHA256，仅记录警告）
    """
    try:
        from flask import current_app
        key = current_app.config.get("DESENS_HMAC_KEY", "")
        if key:
            return key.encode("utf-8")
    except Exception:
        pass
    return _load_or_create_desens_key()


def _load_config():
    """从数据库加载脱敏配置到缓存"""
    from app.models.desensitize import DesensitizeSetting, DesensitizeRule
    setting = DesensitizeSetting.query.first()
    enabled = setting.enabled if setting else True
    rules = {}
    for r in DesensitizeRule.query.filter_by(is_active=True).all():
        rules[r.field_key] = {
            "algorithm": r.algorithm,
            "keep_head": r.keep_head or 0,
            "keep_tail": r.keep_tail or 0,
            "mask_char": r.mask_char or "*",
        }
    with _cache_lock:
        _cache["enabled"] = enabled
        _cache["rules"] = rules
        _cache["loaded"] = True


def clear_cache():
    """清除缓存（配置变更后调用）"""
    with _cache_lock:
        _cache["loaded"] = False
        _cache["rules"] = {}
        _cache["enabled"] = True


def is_enabled():
    """脱敏功能是否启用"""
    if not _cache["loaded"]:
        try:
            _load_config()
        except Exception:
            return False
    with _cache_lock:
        return _cache["enabled"]


def mask_value(value, algorithm, keep_head=0, keep_tail=0, mask_char="*"):
    """对单个值应用指定算法的脱敏处理

    Args:
        value: 原始值（会被转为 str 处理）
        algorithm: 算法名
        keep_head: 保留前 N 位
        keep_tail: 保留后 N 位
        mask_char: 替换字符
    Returns:
        脱敏后的字符串
    """
    if value is None:
        return None
    s = str(value)
    if not s:
        return s

    if algorithm == "redact":
        return "[REDACTED]"

    if algorithm == "hash":
        s_bytes = s.encode("utf-8")
        hmac_key = _get_hmac_key()
        if hmac_key is not None:
            # HMAC-SHA256，取前 8 位十六进制（保持与旧版输出长度一致）
            return hmac.new(hmac_key, s_bytes, hashlib.sha256).hexdigest()[:8]
        # 密钥不可用（无 app context / 密钥文件生成失败）：回退无盐 SHA256，首次记录警告
        global _hmac_warned
        if not _hmac_warned:
            _logger.warning(
                "脱敏 HMAC 密钥不可用（未配置 DESENS_HMAC_KEY 且无法生成密钥文件），"
                "hash 算法回退为无盐 SHA256，存在彩虹表攻击风险。"
            )
            _hmac_warned = True
        return hashlib.sha256(s_bytes).hexdigest()[:8]

    if algorithm == "mask_all":
        return mask_char * len(s)

    if algorithm == "mask_email":
        at = s.find("@")
        if at > 0:
            local = s[:at]
            domain = s[at:]
            if len(local) <= 1:
                masked_local = mask_char
            elif len(local) <= 3:
                masked_local = local[0] + mask_char * (len(local) - 1)
            else:
                masked_local = local[:1] + mask_char * (len(local) - 2) + local[-1:]
            return masked_local + domain
        # 没有 @，按 mask_middle 处理
        algorithm = "mask_middle"

    if algorithm == "mask_middle":
        if len(s) <= keep_head + keep_tail:
            # 长度不足，全部脱敏
            return mask_char * len(s)
        head = s[:keep_head]
        tail = s[-keep_tail:] if keep_tail > 0 else ""
        middle_len = len(s) - keep_head - keep_tail
        return head + mask_char * middle_len + tail

    if algorithm == "mask_head":
        # 保留前 keep_head 位，其余替换
        n = keep_head
        if n <= 0:
            return mask_char * len(s)
        if n >= len(s):
            return s
        return s[:n] + mask_char * (len(s) - n)

    if algorithm == "mask_tail":
        # 保留后 keep_tail 位，其余替换
        n = keep_tail
        if n <= 0:
            return mask_char * len(s)
        if n >= len(s):
            return s
        return mask_char * (len(s) - n) + s[-n:]

    # 未知算法，兜底
    return mask_char * len(s)


def desensitize_field(field_key, value):
    """根据配置脱敏单个字段（按 field_key 查规则）"""
    if not _cache["loaded"]:
        try:
            _load_config()
        except Exception:
            return value
    with _cache_lock:
        rule = _cache["rules"].get(field_key)
    if not rule:
        return value
    return mask_value(
        value,
        rule["algorithm"],
        rule["keep_head"],
        rule["keep_tail"],
        rule["mask_char"],
    )


def desensitize_dict(data_dict, role):
    """按角色与规则对 dict 进行脱敏（admin 不脱敏，原样返回）

    Args:
        data_dict: 模型 to_dict() 输出的 dict
        role: 当前用户角色字符串（admin/doctor/annotator/nurse/engineer）
    Returns:
        脱敏后的 dict（原地修改并返回）
    """
    if not data_dict:
        return data_dict
    # admin 角色不脱敏
    if role == "admin":
        return data_dict
    if not is_enabled():
        return data_dict
    if not _cache["loaded"]:
        try:
            _load_config()
        except Exception:
            return data_dict
    with _cache_lock:
        rules = dict(_cache["rules"])
    for key in list(data_dict.keys()):
        if key in rules:
            data_dict[key] = mask_value(
                data_dict[key],
                rules[key]["algorithm"],
                rules[key]["keep_head"],
                rules[key]["keep_tail"],
                rules[key]["mask_char"],
            )
    return data_dict


def desensitize_list(items, role):
    """批量脱敏列表中的每个 dict"""
    if role == "admin" or not is_enabled():
        return items
    for item in items:
        desensitize_dict(item, role)
    return items


# ==================== userInfo.json 内容脱敏（导出场景） ====================

# 采集端 userInfo.json 的字段名 → 平台脱敏规则的 field_key
# 采集端键名与平台模型字段名不一致（userName vs real_name），必须显式映射
USERINFO_KEY_ALIASES = {
    "username": "real_name",
    "name": "real_name",
    "realname": "real_name",
    "real_name": "real_name",
    "patientname": "real_name",
    "phone": "phone",
    "telephone": "phone",
    "tel": "phone",
    "mobile": "phone",
    "phonenumber": "phone",
    "email": "email",
    "mail": "email",
    "idcard": "id_card",
    "id_card": "id_card",
    "cardno": "id_card",
    "address": "address",
    "homeaddress": "address",
    "comment": "remark",
    "remark": "remark",
    # 准标识符：性别/年龄（采集端 sex 为 0/1/2 编码，平台侧为 gender 中文）
    "sex": "gender",
    "gender": "gender",
    "usersex": "gender",
    "usergender": "gender",
    "age": "age",
    "userage": "age",
}

# 匹配 JSON 文本中的 "key": "value" 字符串字段（值为字符串）
# 用正则而非 json.loads：采集端 userInfo.json 常见缺逗号/尾逗号等非法 JSON，
# 解析会直接失败；文本替换同时保证原缩进、注释、字段顺序一律不变
_USERINFO_STR_FIELD_RE = re.compile(
    r'("([A-Za-z_][A-Za-z0-9_]*)"[ \t]*:[ \t]*")((?:[^"\\]|\\.)*)(")'
)

# 匹配 "key": <数字> 数值型字段
# 必要性：采集端把性别/年龄写成数值（"sex": 0、"age": 30），只处理字符串值
# 会让这两条规则在导出路径上完全失效
# 不含 true/false/null：null 表示"无此信息"，脱敏会无中生有造出值（"phone": null
#   → "phone": "****"），布尔同理，二者都不承载身份信息
# 尾部 (?![0-9A-Za-z_.]) 防止从更长 token 中间截断匹配
_USERINFO_SCALAR_FIELD_RE = re.compile(
    r'("([A-Za-z_][A-Za-z0-9_]*)"[ \t]*:[ \t]*)'
    r'(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
    r'(?![0-9A-Za-z_.])'
)


def _mask_userinfo_field(key: str, raw_value: str, is_quoted: bool):
    """按脱敏规则处理 userInfo 文本中的单个字段值

    :param key: JSON 键名（采集端命名，需经 USERINFO_KEY_ALIASES 映射）
    :param raw_value: 值的文本形态（不含外层引号）
    :param is_quoted: 该值在原文本中是否带引号（字符串值）
    :return: (写回的文本, 是否命中脱敏)；未命中时原样返回 raw_value
    """
    field_key = USERINFO_KEY_ALIASES.get(key.lower())
    if not field_key:
        return raw_value, False
    try:
        masked = desensitize_field(field_key, raw_value)
    except Exception:
        return raw_value, False
    if masked is None or masked == raw_value:
        return raw_value, False
    masked = str(masked)
    if is_quoted:
        # 字符串值：直接嵌回原引号内，保持既有转义结构不变
        return masked, True
    # 非字符串标量：脱敏结果按 JSON 字符串写回（"age": 30 → "age": "**"）
    # 不能裸写 mask_char —— 会产出 `"age": **` 这种非法 JSON
    # 类型由 number 变为 string 是有意的：值已不可用，类型随之失去语义；
    # scanner._parse_user_info 对 int()/映射失败均有容错，二次导入不会崩
    return json.dumps(masked, ensure_ascii=False), True


def desensitize_userinfo_text(text: str):
    """对 userInfo.json 文本按脱敏规则处理受试者身份字段

    - 仅替换能映射到脱敏规则且规则启用（is_active=True）的字段
    - 规则取值用 desensitize_field()，因此天然独立于脱敏总开关 enabled：
      导出脱敏由用户在导出界面显式勾选，不应被平台全局开关静默吞掉
    - 覆盖字符串值与数值两类值；容忍非法 JSON（采集端数据常见）
    - null/布尔值不做脱敏（非身份信息，替换会无中生有）
    - 未命中任何规则的键（如 moca/mmse/disease）与原有缩进、空白一律不动

    :param text: userInfo.json 原始文本
    :return: (脱敏后文本, 命中字段数)
    """
    if not text:
        return text, 0

    hits = 0

    def _replace_str(match):
        nonlocal hits
        prefix, key, value, quote = match.group(1), match.group(2), match.group(3), match.group(4)
        if not value:
            return match.group(0)
        new_value, hit = _mask_userinfo_field(key, value, True)
        if not hit:
            return match.group(0)
        hits += 1
        return f"{prefix}{new_value}{quote}"

    def _replace_scalar(match):
        nonlocal hits
        prefix, key, value = match.group(1), match.group(2), match.group(3)
        new_value, hit = _mask_userinfo_field(key, value, False)
        if not hit:
            return match.group(0)
        hits += 1
        return f"{prefix}{new_value}"

    text = _USERINFO_STR_FIELD_RE.sub(_replace_str, text)
    text = _USERINFO_SCALAR_FIELD_RE.sub(_replace_scalar, text)
    return text, hits
