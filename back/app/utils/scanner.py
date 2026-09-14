# -*- coding: utf-8 -*-
"""受试者文件夹自动扫描器

扫描逻辑：
- 扫描 watch_dir 下的子文件夹，每个子文件夹视为一个新受试者
- 子文件夹名作为 pseudo_id（需通过格式校验，数据库不存在时创建）
- 自动解析 userInfo.json 填充受试者元数据（姓名/年龄/性别/量表得分等）
- 可选：将子文件夹内的数据文件自动上传为数据资产（按文件名前缀识别模态）
- 通过 threading.Timer 实现定时扫描，不依赖额外库
"""
import os
import re
import json
import time
import logging
import threading
from datetime import datetime

from app.extensions import db
from app.models.subject import Subject
from app.models.scan_config import ScanConfig
from app.models.data import DataAsset, DataType, DataLayer

logger = logging.getLogger(__name__)
from app.utils.crypto import (
    encrypt_bytes, is_encrypted_file,
    is_external_encrypted_file,
    decrypt_external_file_auto,
)
from app.utils.naming import apply_naming_standard, _safe_segment, BEIJING_TZ
from app.utils.source_identity import (
    normalize_original_filename, stored_original_filename, source_group_key,
)
from app.utils.eye_tracking_adapter import (
    load_sync_data, is_sync_data_file,
)
from app.utils.scale_adapter import (
    load_scale_data, is_scale_data_file, parse_scale_summary,
    detect_scale_type,
)

# 全局定时器引用
_scan_timer = None
_lock = threading.Lock()
# 扫描执行互斥锁：防止手动"立即扫描"与定时扫描并发执行同一目录
_scan_run_lock = threading.Lock()

# 伪ID合法格式：3-64位字母/数字/下划线/短横线，不以 .~$ 开头
_PSEUDO_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,64}$")

# sex 字段数值到中文映射（0/1 二值）
_SEX_MAP = {0: "男", 1: "女", "0": "男", "1": "女"}

# severity 字段数值到认知风险分级映射（0-3：无/轻度/中度/重度）
_SEVERITY_MAP = {0: "无", 1: "轻度", 2: "中度", 3: "重度",
                 "0": "无", "1": "轻度", "2": "中度", "3": "重度"}

# 文件写入稳定窗口：mtime 距今不足该秒数的文件视为仍在写入
_FILE_STABLE_SECONDS = 30


def _is_file_writing(path):
    """判断文件是否可能仍在被写入（mtime 距今过近）

    外部采集工具向监控目录渐进写入文件，扫描线程可能读到半截密文，
    此时解密必然失败（长度非 16 倍数 / PKCS7 去填充失败），
    会被误报为"密钥不匹配"。mtime 很新的文件应推迟到下一轮扫描。

    注意：这是解密失败后的"归因判断"（用于区分"文件没写完"与"密钥不对"），
    不承担"是否该导入"的职责 —— 导入时机由 _file_state_ready 的双轮读数
    比对决定。
    """
    try:
        return (time.time() - os.path.getmtime(path)) < _FILE_STABLE_SECONDS
    except OSError:
        return True  # stat 失败（文件被独占锁定/已删除）按仍在写入处理


# 快照条目过期时间（秒）：超过该时长未再出现在监控目录中的条目被清理，
# 避免进程内快照无界增长
_FILE_OBSERVE_STALE_SECONDS = 3600

# 文件观察快照：{绝对路径: {"sig": (size, mtime_ns), "seen_at": ts}}
# 存在进程内存即可：容器为 GUNICORN_WORKERS=1 + preload_app=True
# （见 gunicorn_config.py），create_app 只在 gunicorn master 执行，扫描定时器
# 线程不会被 fork 进 worker，因此扫描是单实例、快照不会跨进程分裂。容器重启
# 后只是重新观察一轮，无正确性损失（源文件仍在监控目录里）。
_file_observe_snapshot = {}


def _observe_file_state(path, size):
    """登记 / 刷新文件的写入观察基线，返回「读数已稳定」标记

    观察期 = 一个自动扫描周期：本轮读到的（大小 + mtime_ns）与上一轮完全
    一致才算稳定。

    返回 False 的情况（本轮不入库，等下一轮再判）：
      - 首次观察到的文件 —— 只登记基线，导入推迟到下一轮；
      - 与上一轮读数不一致的文件（仍在写入 / 仍在拷贝）—— 刷新基线，继续
        观察。这类文件的实际观察期会超过一个周期，直到出现连续两轮读数相同。

    调用位置刻意放在"读取文件之前"：解密失败（外部密钥未上传）的轮次也要
    登记基线，否则密钥补传后该文件仍处于"首见"状态，还要再多等一轮才入库。

    为什么不用"mtime 距今 N 秒"这类绝对时间窗：外部拷贝工具可能保留源文件
    mtime（robocopy /COPY:DAT、rsync -t、带时间戳的解压），新落盘文件的 mtime
    是旧日期，"距今很久"会立即放行一个正在写入的半成品。本判据只看读数是否
    变化，与 mtime 绝对值无关，对"保留 mtime 的拷贝"同样有效。
    """
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        return False  # stat 失败（被独占锁定 / 已删除）按仍在写入处理
    sig = (size, mtime_ns)
    rec = _file_observe_snapshot.get(path)
    if rec is None:
        # 首见：登记基线，本轮不导入
        _file_observe_snapshot[path] = {"sig": sig, "seen_at": time.time()}
        return False
    rec["seen_at"] = time.time()
    if rec["sig"] != sig:
        # 读数仍在变化：刷新基线，继续观察
        rec["sig"] = sig
        return False
    # 与上一轮读数完全一致 → 写入已结束
    return True


def _expire_file_snapshots():
    """清理长时间未再出现的文件观察快照，避免进程内快照无界增长"""
    cutoff = time.time() - _FILE_OBSERVE_STALE_SECONDS
    for p in [p for p, r in _file_observe_snapshot.items()
              if r.get("seen_at", 0) < cutoff]:
        _file_observe_snapshot.pop(p, None)


def _get_db_external_keys():
    """从数据库加载所有启用的外部密钥，返回 [(key_id, key_bytes, iv_bytes), ...]

    返回 None 表示数据库访问异常；返回 [] 表示无可用密钥。
    scanner 据此提示用户到「外部密钥管理」上传对应密钥。
    """
    try:
        from app.models.external_key import ExternalKey
        import base64
        keys = ExternalKey.query.filter_by(is_active=True).order_by(
            ExternalKey.created_at.desc()
        ).all()
        result = []
        for k in keys:
            try:
                key_bytes = base64.b64decode(k.key_b64)
                iv_bytes = base64.b64decode(k.iv_b64)
                if len(key_bytes) == 32 and len(iv_bytes) == 16:
                    result.append((k.id, key_bytes, iv_bytes))
            except Exception:
                continue
        return result
    except Exception:
        return None


def _validate_pseudo_id(name):
    """校验伪ID格式：3-64位字母/数字/下划线/短横线"""
    return bool(_PSEUDO_ID_RE.match(name))


def _detect_data_type(filename):
    """根据文件名前缀 + 扩展名识别数据类型
    样例文件命名规律：eeg_时间戳.csv / ecg_时间戳.csv / recording_时间戳.wav
    外部加密文件后缀 .enc 会先被剥离再做识别
    """
    name_lower = filename.lower()
    # 剥离外部加密后缀 .enc（如 eeg_xxx.csv.enc -> eeg_xxx.csv）
    if name_lower.endswith(".enc"):
        name_lower = name_lower[:-4]
    # 按文件名前缀识别模态（优先级高于扩展名）
    if name_lower.startswith("eeg_") or name_lower.startswith("eeg-"):
        return "eeg"
    if name_lower.startswith("ecg_") or name_lower.startswith("ecg-"):
        return "ecg"
    if name_lower.startswith("gait_") or name_lower.startswith("gait-"):
        return "gait"
    if name_lower.startswith("eye_") or name_lower.startswith("eye-"):
        return "eye"
    # 量表文件（MoCA_用户ID_日期.json 等；.enc 已剥离，外部加密量表同样可识别）
    if is_scale_data_file(name_lower):
        return "scale"
    # 通用扩展名兜底
    ext = name_lower.rsplit(".", 1)[-1] if "." in name_lower else ""
    if ext in ("mp4", "avi", "mov", "mkv"):
        return "video"
    if ext in ("wav", "mp3", "m4a", "flac"):
        return "audio"
    if ext in ("edf", "bdf"):
        return "eeg"
    return "task"


def _strip_enc_suffix(filename):
    """剥离外部加密文件后缀 .enc，返回去除后的文件名"""
    if filename.lower().endswith(".enc"):
        return filename[:-4]
    return filename


def _read_file_plaintext(src_path, failure_collector=None):
    """读取文件并返回明文字节，自动适配三种格式

    解密策略（针对外部 AES-256-CBC 加密文件）：
    - 仅使用数据库中所有 is_active=True 的外部密钥依次尝试
    - 全部失败或无可用密钥 → 记录到 failure_collector，提示用户上传外部密钥

    设计原则：外部密钥集中存储在系统数据库中，扫描目录中不应包含密钥文件。
    管理员通过「系统设置 → 外部密钥管理」上传/管理外部密钥。

    :param src_path: 源文件路径
    :param failure_collector: 失败信息收集列表（None 时不收集，直接抛异常）
    :returns: 明文字节；解密失败且 failure_collector 非 None 时返回 None
    :raises ValueError: 外部加密文件无法解密（且 failure_collector 为 None）
    """
    # 1. 项目自身 DMEC 格式
    if is_encrypted_file(src_path):
        from app.utils.crypto import decrypt_file
        return decrypt_file(src_path)

    # 2. 外部 AES-256-CBC 格式：仅用数据库活跃密钥尝试
    if is_external_encrypted_file(src_path):
        last_error = None
        db_keys = _get_db_external_keys()
        if db_keys:
            try:
                plaintext, _matched_id = decrypt_external_file_auto(src_path, db_keys)
                return plaintext
            except Exception as e:
                last_error = e

        # 解密失败时排除文件仍在写入的情况（仅扫描场景：collector 模式下静默
        # 推迟到下一轮重试，半截密文解密必然失败，与密钥是否正确无关）。
        # 手动上传接口走临时文件（刚创建，mtime 必然很新），不做此判断，
        # 避免把真正的密钥错误误报为"文件仍在写入"
        if failure_collector is not None and _is_file_writing(src_path):
            return None  # 静默推迟，下一轮扫描自动重试

        # 数据库密钥全部失败或无可用密钥
        if not db_keys:
            last_error_msg = "数据库无可用外部密钥"
        else:
            last_error_msg = f"所有 {len(db_keys)} 组密钥均无法解密"
        msg = (f"外部加密文件无法解密，请到「系统设置 → 外部密钥管理」"
               f"上传对应的外部密钥（key+iv）: {os.path.basename(src_path)}")
        if failure_collector is not None:
            failure_collector.append({
                "file": src_path,
                "reason": msg,
                "last_error": str(last_error) if last_error else last_error_msg,
            })
            return None  # 调用方负责跳过
        raise ValueError(msg + f"（{last_error_msg}）")

    # 3. 明文文件
    with open(src_path, "rb") as f:
        return f.read()



def _find_user_info(sub_dir, failure_collector=None):
    """在受试者目录下查找并解析 userInfo.json

    支持三种形态：
    1. userInfo.json（明文）
    2. userInfo.json.enc（外部 AES-CBC 加密）
    3. userInfo.json 用项目 DMEC 格式加密（罕见，但兼容）

    返回: (file_path, fields_dict) 或 (None, None)
    解密失败时若提供 failure_collector，则记录到列表并返回 (None, None)
    """
    # 优先级 1: 明文 userInfo.json
    plain_path = os.path.join(sub_dir, "userInfo.json")
    if os.path.isfile(plain_path):
        if _is_file_writing(plain_path):
            return None, None  # 文件仍在写入，下一轮扫描再解析
        return plain_path, _parse_user_info(plain_path)

    # 优先级 2: 外部加密 userInfo.json.enc
    enc_path = os.path.join(sub_dir, "userInfo.json.enc")
    if os.path.isfile(enc_path):
        if _is_file_writing(enc_path):
            return None, None  # 文件仍在写入，下一轮扫描再解析
        try:
            plaintext = _read_file_plaintext(
                enc_path, failure_collector=failure_collector,
            )
            if plaintext is None:
                return None, None  # 失败已记录到 collector
            # 解密后写入临时文件供 _parse_user_info 解析
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".json")
            try:
                os.write(fd, plaintext)
                os.close(fd)
                return enc_path, _parse_user_info(tmp_path)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception:
            return None, None

    return None, None


def _backfill_subject_fields(subject, sub_dir, failure_collector=None):
    """为已存在受试者补齐 userInfo 字段（仅填充空字段，不覆盖已有值）

    场景：受试者首次扫描时 userInfo.json(.enc) 仍在写入，解析失败导致
    受试者创建时缺失元数据。后续扫描检测到关键字段（年龄/性别）均为空
    时重新解析并补齐，写入由调用方统一 commit。
    """
    if subject.age is not None or subject.gender:
        return  # 已有 userInfo 数据，无需补齐
    _, fields = _find_user_info(sub_dir, failure_collector=failure_collector)
    if not fields:
        return
    for k, v in fields.items():
        # pseudo_id 以目录名为准，不覆盖
        if k == "pseudo_id":
            continue
        if getattr(subject, k, None) in (None, ""):
            setattr(subject, k, v)



def _parse_json_lenient(raw_text):
    """容错解析 JSON 文本，返回 dict 或 None

    处理两类常见语法错误：
    1. 行尾值后缺失逗号（如 `"organization": "1001"` 后直接换行）
    2. 对象/数组末尾的尾逗号（如 `"mmse": 20,` 紧跟 `}`）
    """
    try:
        # 容错 1：行尾值后缺失逗号（"key": value\n → "key": value,\n）
        raw_text = re.sub(r'("\w+"\s*:\s*[^,\s\}\]]+)\s*\n', r'\1,\n', raw_text)
        # 容错 2：对象/数组末尾的尾逗号（,} 或 ,]）
        raw_text = re.sub(r',\s*}', '}', raw_text)
        raw_text = re.sub(r',\s*\]', ']', raw_text)
        return json.loads(raw_text)
    except Exception:
        return None


def _parse_user_info(user_info_path):
    """解析 userInfo.json，返回 Subject 字段映射字典"""
    try:
        with open(user_info_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    data = _parse_json_lenient(raw)
    if data is None:
        return None

    fields = {}
    # 基础信息
    # id 字段作为伪ID（userInfo.json 中的 id 通常是长整型受试者编号）
    if "id" in data and data["id"] not in ("", None):
        fields["pseudo_id"] = str(data["id"])
    # 真实姓名（核心键为 userName；兼容 name / realName / real_name 兜底）
    for _name_key in ("userName", "name", "realName", "real_name"):
        if data.get(_name_key) not in ("", None):
            fields["real_name"] = str(data[_name_key])
            break
    if "age" in data:
        try:
            fields["age"] = int(data["age"])
        except (ValueError, TypeError):
            pass
    if "sex" in data:
        fields["gender"] = _SEX_MAP.get(data["sex"], str(data.get("sex", "")))
    if "education" in data:
        fields["education_level"] = str(data["education"])
    if "phone" in data:
        fields["phone"] = str(data["phone"])
    # 临床信息
    # severity 0-3 → 无/轻度/中度/重度，作为认知风险分级；无 severity 时回退用疾病名
    disease = str(data.get("disease", "")).strip()
    severity = data.get("severity", "")
    severity_text = _SEVERITY_MAP.get(severity) if severity not in ("", None) else None
    if severity_text is not None:
        fields["cognitive_risk_level"] = severity_text
    elif disease and disease != "未知病症":
        fields["cognitive_risk_level"] = disease
    if "comment" in data and data["comment"]:
        fields["remark"] = str(data["comment"])
    # 量表得分（兼容多字段命名：量表缩写 / 缩写_score / 中文拼音等）
    _scale_fields = [
        ("moca", "moca_score"), ("mmse", "mmse_score"), ("ad8", "ad8_score"),
        ("moca_score", "moca_score"), ("mmse_score", "mmse_score"),
        ("ad8_score", "ad8_score"),
    ]
    for src_key, dst_key in _scale_fields:
        if src_key in data and data[src_key] not in (None, ""):
            try:
                v = data[src_key]
                # 嵌套对象（如 {"moca": {"score": 28}}）时取其 score
                if isinstance(v, dict):
                    v = v.get("score") or v.get("total") or v.get("totalScore")
                fields[dst_key] = float(v)
            except (ValueError, TypeError):
                pass
    # 采集信息
    if "organization" in data:
        fields["collection_scene"] = str(data["organization"])
    if "createDatetime" in data:
        fields["collection_time"] = _parse_create_datetime(data["createDatetime"])
    return fields


def _parse_create_datetime(value):
    """解析 createDatetime 字段，支持三种格式：
    1. 毫秒时间戳（如 1784282715000）
    2. 秒级时间戳（如 1784282715）
    3. 字符串日期（如 "2024-01-01 12:00:00"）
    返回 datetime 对象，解析失败返回 None
    """
    # 数值型时间戳
    if isinstance(value, (int, float)):
        ts = float(value)
        # 毫秒时间戳（13位）→ 转秒
        if ts > 1e12:
            ts = ts / 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except (ValueError, OSError):
            return None
    # 字符串：先尝试数值解析，再尝试日期格式
    s = str(value).strip()
    if s.isdigit():
        ts = float(s)
        if ts > 1e12:
            ts = ts / 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except (ValueError, OSError):
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def scan_watch_dir(config):
    """扫描单个配置的监控目录

    返回 dict：
        {
          "new_count": int,             # 新增受试者数
          "skipped": int,               # 跳过的子目录数
          "failures": [                 # 解密失败文件列表
            {"file": str, "reason": str, "last_error": str}
          ]
        }
    """
    result = {"new_count": 0, "skipped": 0, "failures": []}
    if not config or not config.is_active:
        return result
    watch_dir = config.watch_dir
    if not os.path.isdir(watch_dir):
        return result

    # 扫描互斥：手动"立即扫描"与定时器并发时，后到者直接跳过本次，避免并发
    # add Subject 撞唯一索引 / 并发写同一数据湖文件互相覆盖损坏
    if not _scan_run_lock.acquire(blocking=False):
        return result
    try:
        new_count = 0
        skipped = 0
        # 同名演进待删清单：录制半成品先入库、完整版后入库时，旧版本在本次
        # 主事务提交后清理（先写新后删旧），不必等下次重启自愈才收敛
        pending_stale_ids = []
        try:
            entries = sorted(os.listdir(watch_dir))
            for entry in entries:
                sub_dir = os.path.join(watch_dir, entry)
                if not os.path.isdir(sub_dir):
                    continue
                pseudo_id = entry.strip()
                if not pseudo_id:
                    continue
                # 伪ID格式校验
                if not _validate_pseudo_id(pseudo_id):
                    skipped += 1
                    continue
                # 检查是否已存在
                existing = Subject.query.filter_by(pseudo_id=pseudo_id).first()
                if existing:
                    # 受试者已存在：检查是否有未导入的新文件（增量导入）
                    if config.auto_upload_files:
                        # 文件级增量去重：按（原始文件名+原始大小）跳过已导入文件，
                        # 与 upload_asset 幂等键一致。之前解密失败/被推迟的文件
                        # 不在集合中，下一轮自动重试（密钥补传后无需手动干预）
                        # 文件级增量去重：按（归一化原始名 + 原始大小）跳过已导入
                        # 文件。归一化是必需的——库里存的 original_filename 可能是
                        # 老版本带相对路径前缀的形态，与本轮 fname（纯文件名）字面
                        # 不等会漏判，导致同一文件重复导入。
                        existing_files = set()
                        for a in DataAsset.query.filter_by(subject_id=existing.id).all():
                            meta = a.metadata_json or {}
                            existing_files.add((
                                normalize_original_filename(meta.get("original_filename")),
                                meta.get("original_size"),
                            ))
                        pending_stale_ids += _import_files_for_subject(
                            sub_dir, existing,
                            skip_files=existing_files,
                            failure_collector=result["failures"],
                        ) or []
                    # 存量同源收敛：即使本轮没有新文件（源文件已稳定，全部命中
                    # 增量去重被跳过），也把该受试者名下的同源重复组收敛为一条
                    # ——覆盖"半成品+完整版共存"的历史存量，不必等下次重启的
                    # 启动自愈才消失。
                    pending_stale_ids += _collapse_subject_sources(existing)
                    # userInfo 字段自愈：首次扫描时 userInfo 尚在写入导致解析失败，
                    # 受试者创建时无元数据；此处检测到关键字段为空则重新解析补齐
                    _backfill_subject_fields(
                        existing, sub_dir, failure_collector=result["failures"],
                    )
                    continue
                # 创建新受试者
                subject = Subject(
                    pseudo_id=pseudo_id,
                    status="collecting",
                )
                # 应用扫描配置的默认批次/场景（作为兜底，userInfo.json 字段优先）
                if getattr(config, "collection_batch", None):
                    subject.collection_batch = config.collection_batch
                if getattr(config, "collection_scene", None):
                    subject.collection_scene = config.collection_scene
                # 解析 userInfo.json 填充元数据（支持明文 / 外部加密 / DMEC 加密三种形态）
                _, fields = _find_user_info(
                    sub_dir, failure_collector=result["failures"],
                )
                if fields:
                    for k, v in fields.items():
                        # pseudo_id 以目录名为准（目录是用户组织数据的结构），不覆盖
                        if k == "pseudo_id":
                            continue
                        setattr(subject, k, v)

                # 眼动评估数据 sync_data.json 仅解析存储为 DataAsset（data_type=eye, layer=feature），
                # 不映射字段到 Subject，也不自动划分风险分级
                db.session.add(subject)
                db.session.flush()  # 获取 id

                # 可选：自动上传文件
                if config.auto_upload_files:
                    pending_stale_ids += _import_files_for_subject(
                        sub_dir, subject,
                        failure_collector=result["failures"],
                    ) or []

                new_count += 1

            config.last_scan_at = datetime.utcnow()
            config.last_scan_count = new_count
            db.session.commit()
            # 新版本已落库提交后，再删除同名演进的旧版本记录与磁盘文件
            # （先写新后删旧：新文件失败时旧记录保留，不丢数据）
            if pending_stale_ids:
                _purge_stale_assets(pending_stale_ids)
        except Exception as e:
            db.session.rollback()
            raise e

        result["new_count"] = new_count
        result["skipped"] = skipped
        return result
    finally:
        # 清理长时间不再出现的文件观察快照，避免进程内快照无界增长
        _expire_file_snapshots()
        _scan_run_lock.release()


def _import_files_for_subject(sub_dir, subject, skip_files=None, failure_collector=None):
    """将子文件夹内的数据文件导入为数据资产

    自动适配三种源文件格式：
    1. 项目自身 DMEC 加密格式 → 解密后重新加密写入
    2. 外部 AES-256-CBC 加密格式 → 用数据库外部密钥尝试解密
    3. 明文文件 → 直接读取

    所有文件统一用项目 DMEC 格式加密后写入数据湖。
    跳过 userInfo.json / userInfo.json.enc
    解密失败的文件记录到 failure_collector 并跳过（不中断扫描）

    :param skip_files: 已导入文件集合 {(original_filename, original_size), ...}，
                       命中的文件跳过（文件级增量去重，与 upload_asset 幂等键一致）；
                       未命中文件（含之前解密失败的）正常处理，实现失败自动重试
    :param failure_collector: 失败信息收集列表（None 时不收集，直接抛异常）
    """
    from flask import current_app
    from app.utils.naming import get_naming_standard

    data_lake = current_app.config["DATA_LAKE_DIR"]
    # 受试者数据目录基: data_lake/raw/{pseudo_id}/ 和 data_lake/feature/{pseudo_id}/
    # 实际文件存储在 data_lake/{layer}/{pseudo_id}/{data_type}/{filename}，与 upload_asset 保持一致
    subject_raw_base = os.path.join(data_lake, "raw", subject.pseudo_id)
    subject_feature_base = os.path.join(data_lake, "feature", subject.pseudo_id)
    os.makedirs(subject_raw_base, exist_ok=True)

    # 跳过文件名集合：仅防御性跳过密钥文件（扫描目录正常情况下不含）
    # （userInfo.json / userInfo.json.enc 需作为数据资产入库，不再跳过）
    skip_filenames_lower = {
        "密钥.txt", "key.txt", "secret.txt",
    }

    # 该受试者已有资产的同源索引：归一化原始名 -> [asset, ...]
    # 用于识别「同名演进」——录制中的半成品先被扫描入库（大小是中途值），
    # 录制完成后完整文件再次扫描入库时同名但大小不同；此时旧记录必须收敛，
    # 否则资产列表里同一份心电会出现两条（且要等下次重启自愈才消失）。
    existing_by_source = {}
    for a in DataAsset.query.filter_by(subject_id=subject.id).all():
        k = normalize_original_filename((a.metadata_json or {}).get("original_filename"))
        if k:
            existing_by_source.setdefault(k, []).append(a)

    # 本次需清理的旧版本资产 id（由调用方在主事务提交后删除，先写新后删旧）
    stale_ids = []

    for fname in os.listdir(sub_dir):
        # 跳过元数据文件
        if fname.lower() in skip_filenames_lower:
            continue
        # 跳过字段映射文件（_sync_fields_zh.json，仅供查阅）
        if fname.lower().endswith("_sync_fields_zh.json"):
            continue
        src_path = os.path.join(sub_dir, fname)
        if not os.path.isfile(src_path):
            continue
        # 跳过临时文件（Office 锁文件 ~$ 开头、. 开头的隐藏文件）
        if fname.startswith("~$") or fname.startswith("."):
            continue
        try:
            src_size = os.path.getsize(src_path)
        except OSError:
            continue
        # 增量导入去重：跳过已导入的文件（归一化原始名 + 原始大小）；
        # 已入库文件不再需要观察，顺手清掉快照
        if skip_files and (normalize_original_filename(fname), src_size) in skip_files:
            _file_observe_snapshot.pop(src_path, None)
            continue
        # 写入观察（观察期 = 一个自动扫描周期）：读取之前先登记 / 刷新基线。
        # 基线不因本轮读取失败而丢失，密钥补传后无需多等一轮。稳定标记在下文
        # 读取成功之后才用于决定是否入库 —— 这样"密钥缺失"的失败告警仍在
        # 本轮记录，不因观察期而推迟。
        file_stable = _observe_file_state(src_path, src_size)
        # 剥离 .enc 后缀得到原始文件名（用于识别类型与应用命名规范）
        original_name = _strip_enc_suffix(fname)
        ext = original_name.rsplit(".", 1)[-1] if "." in original_name else ""
        # 眼动评估数据 sync_data.json → data_type=eye, layer=feature
        # （用剥离 .enc 后的文件名判断，外部加密的 sync_data/量表同样可识别）
        is_sync = is_sync_data_file(original_name)
        # 量表数据（MoCA/MMSE/AD8 等）→ data_type=scale, layer=feature
        is_scale = is_scale_data_file(original_name)
        # userInfo 元数据文件：剥离 .enc 后若为 userInfo.json，作为 json 资产入库
        # （重命名时显式加上 "userInfo" 标识词并附伪ID/日期，便于在数据资产中识别这是
        #   哪个受试者的 userInfo JSON；内容与其他模态同样 DMEC 加密落盘）
        is_userinfo = original_name.lower() == "userinfo.json"
        if is_userinfo:
            data_type = "json"
        elif is_sync:
            data_type = "eye"
        elif is_scale:
            data_type = "scale"
        else:
            data_type = _detect_data_type(fname)
        # 应用命名规范（按模态精确匹配命名规范，回退到通用规范）；userInfo 单独命名
        new_name = original_name
        naming_std = None if is_userinfo else get_naming_standard(data_type)
        if naming_std:
            try:
                norm_name, norm_ext = apply_naming_standard(
                    standard=naming_std,
                    subject=subject,
                    data_type=data_type,
                    original_filename=original_name,
                    # 量表传入量表类型（MoCA/MMSE/AD8），使重命名后仍可区分不同量表
                    scale_type=detect_scale_type(original_name) if is_scale else None,
                )
                # 重新拼装为含扩展名的完整文件名
                new_name = f"{norm_name}.{norm_ext}" if norm_ext else norm_name
                # 优先使用规范返回的扩展名（已小写），否则保留原扩展名
                if norm_ext:
                    ext = norm_ext
            except Exception:
                new_name = original_name
        # userInfo 文件：文件名显式带上 "userInfo" 标识词 + 伪ID + 日期，
        # 让人一看就知道这是哪个受试者的 userInfo JSON（不再使用笼统的 userInfo.json）
        if is_userinfo:
            new_name = "userInfo_{}_{}.json".format(
                _safe_segment(subject.pseudo_id) if subject else "NA",
                datetime.now(BEIJING_TZ).strftime("%Y%m%d"),
            )
            ext = "json"

        # 根据数据类型选择存储目录和分层
        # 目录结构: data_lake/{layer}/{pseudo_id}/{data_type}/{filename}，与 upload_asset 保持一致
        if is_sync or is_scale:
            target_dir = os.path.join(subject_feature_base, data_type)
            os.makedirs(target_dir, exist_ok=True)
            layer_name = "feature"
            asset_layer = DataLayer.FEATURE
        else:
            target_dir = os.path.join(subject_raw_base, data_type)
            os.makedirs(target_dir, exist_ok=True)
            layer_name = "raw"
            asset_layer = DataLayer.RAW
        dst_path = os.path.join(target_dir, new_name)
        # 读取源文件明文（自动适配 DMEC / 外部 AES-CBC / 明文三种格式）
        # 失败时记录到 failure_collector 并跳过该文件
        try:
            plaintext = _read_file_plaintext(
                src_path, failure_collector=failure_collector,
            )
        except Exception as e:
            # 文件 IO/解析失败（权限不足、文件正被占用、读取期间被删除等）：
            # 记录后跳过该文件，不中断整批扫描（与解密失败的处理一致）
            if failure_collector is not None:
                failure_collector.append({
                    "file": src_path,
                    "reason": "文件读取失败（IO/权限错误），已跳过该文件",
                    "last_error": str(e),
                })
            else:
                raise
            continue
        if plaintext is None:
            continue  # 解密失败已记录，跳过此文件

        # 首次观察 / 读数仍在变化：本轮不入库，等下一轮读数一致再导入 ——
        # 录制中、拷贝中的半成品不会入库（观察期 = 一个自动扫描周期）
        if not file_stable:
            continue

        # 用项目 DMEC 格式加密写入数据湖
        encrypted = encrypt_bytes(plaintext)
        with open(dst_path, "wb") as f:
            f.write(encrypted)

        # 创建数据资产记录
        file_size = os.path.getsize(dst_path)
        valid_types = [t.value for t in DataType]
        # 相对存储路径入库（与 upload_asset 保持一致：{layer}/{pseudo_id}/{data_type}/{filename}）
        rel_path = f"{layer_name}/{subject.pseudo_id}/{data_type}/{new_name}"
        # 元数据：记录原始文件名+大小（与 upload_asset 一致，支持幂等去重）
        # 眼动/量表数据额外解析 JSON 字段存入 metadata_json
        asset_metadata = {
            # 写库统一为纯文件名（保留 .enc），与 upload_asset / 前端 File.name 对齐
            "original_filename": stored_original_filename(fname),
            "original_size": src_size,
        }
        if is_sync:
            sync_data = load_sync_data(src_path)
            if sync_data:
                asset_metadata.update(sync_data)
        elif is_scale:
            scale_data = load_scale_data(src_path)
            if scale_data:
                asset_metadata["raw"] = scale_data
                # 量表统一摘要（MoCA/MMSE/AD8 等）：总分与分项得分
                scale_summary = parse_scale_summary(
                    scale_data, scale_type=detect_scale_type(original_name),
                )
                if scale_summary:
                    asset_metadata["summary"] = scale_summary
        # 同名演进：存在同源记录但原始大小不同 → 本次是更新版本（完整版），
        # 旧记录（半成品）待本次扫描提交后删除。仅收敛 video_type 为空的通用
        # 记录；带 video_type 的视频（face/body/gait）各自独立，互不干扰。
        for old in existing_by_source.get(normalize_original_filename(fname), []):
            om = old.metadata_json or {}
            if om.get("video_type") is None and om.get("original_size") != src_size:
                stale_ids.append(old.id)

        asset = DataAsset(
            subject_id=subject.id,
            file_name=new_name,
            file_path=rel_path,
            file_format=ext.lower(),
            file_size=file_size,
            data_type=DataType(data_type) if data_type in valid_types else DataType.TASK,
            layer=asset_layer,
            metadata_json=asset_metadata,
        )
        db.session.add(asset)
        # 已登记入库：清掉观察快照（后续轮次交给 skip_files 增量去重跳过）
        _file_observe_snapshot.pop(src_path, None)

    # 去重：同一批内同名文件只登记一次删除
    return list(dict.fromkeys(stale_ids))


def _collapse_subject_sources(subject):
    """对单个受试者做同源收敛，返回待删资产 id 列表

    分组键与 AssetService.purge_duplicate_source_assets 一致（归一化原始名 +
    video_type），组内保留 id 最大的一条（最新入库，即完整版/最新演进版本）。
    无 original_filename 的老数据跳过（保守，不误删）。

    与启动自愈的区别：本函数在每轮扫描时调用，使存量重复在运行期即被收敛，
    不必等下次重启。
    """
    groups = {}
    for a in DataAsset.query.filter_by(subject_id=subject.id).all():
        meta = a.metadata_json or {}
        key = source_group_key(subject.id, a.data_type,
                               meta.get("original_filename"), meta.get("video_type"))
        if key[2] is None:
            continue  # 无原始文件名（手工登记等）不参与收敛
        groups.setdefault(key, []).append(a.id)
    stale = []
    for ids in groups.values():
        if len(ids) > 1:
            stale.extend(sorted(ids)[:-1])
    return stale


def _purge_stale_assets(stale_ids):
    """删除同名演进 / 存量重复留下的旧版本资产（新版本已入库后调用）

    复用 AssetService 的清理链（留档 + 级联关联记录 + 删磁盘，并带共享
    文件保护），避免 utils 层重复实现一套删除逻辑。延迟导入以避免
    utils -> services 的模块级循环依赖。
    """
    ids = list(dict.fromkeys(stale_ids))  # 去重：同名演进与存量收敛可能重叠
    if not ids:
        return
    try:
        from app.services.asset_service import AssetService
        AssetService(operator_id=None)._purge_stale_uploads(ids)
    except Exception:
        logger.exception("同源重复清理失败（不影响本次扫描结果）：%s", ids)


def run_scan_once():
    """执行一次全量扫描（所有启用的配置）

    返回 dict: {new_count, failures} —— 失败文件累计供前端提示上传外部密钥
    """
    configs = ScanConfig.query.filter_by(is_active=True).all()
    total_new = 0
    all_failures = []
    for config in configs:
        try:
            result = scan_watch_dir(config)
            total_new += result.get("new_count", 0)
            all_failures.extend(result.get("failures", []))
        except Exception:
            logger.exception("扫描配置异常：%s", config.handle)
    return {"new_count": total_new, "failures": all_failures}



def _schedule_next(app):
    """安排下一次扫描"""
    global _scan_timer
    with _lock:
        if _scan_timer is not None:
            _scan_timer.cancel()
            _scan_timer = None

        # 取所有启用配置中最小的间隔（interval_minutes 实际单位为秒）
        with app.app_context():
            configs = ScanConfig.query.filter_by(is_active=True).all()
            if not configs:
                return
            min_interval = min(c.interval_minutes for c in configs)
            # 最小 10 秒，避免过于频繁拖累系统
            if min_interval < 10:
                min_interval = 10

        interval_seconds = int(min_interval)
        _scan_timer = threading.Timer(interval_seconds, _scan_tick, args=[app])
        _scan_timer.daemon = True
        _scan_timer.start()


def _scan_tick(app):
    """定时器回调"""
    with app.app_context():
        try:
            run_scan_once()
        except Exception:
            logger.exception("定时扫描执行失败")
    # 安排下一次
    _schedule_next(app)


def start_scan_scheduler(app):
    """启动扫描调度器（在应用启动时调用）"""
    _schedule_next(app)


def stop_scan_scheduler():
    """停止扫描调度器"""
    global _scan_timer
    with _lock:
        if _scan_timer is not None:
            _scan_timer.cancel()
            _scan_timer = None
