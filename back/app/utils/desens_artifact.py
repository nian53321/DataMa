# -*- coding: utf-8 -*-
"""脱敏产物的路径 / 指纹 / 查找 / 读写（纯工具层）

与 `app/services/desensitize_asset_service.py`（编排）和
`app/services/export_service.py`（导出复用）共用，避免两处各写一份口径 ——
**复用判定一旦分叉，就会出现"导出以为能复用、其实字节不同"的静默错误**。

设计要点见 `app/models/desensitize_artifact.py` 的模块 docstring。这里只强调一条：

  ⚠️ **复用的三个必要条件必须同时满足**（配置指纹 / 源文件指纹 / 文件仍在），
  任一条不满足就回落到**实时脱敏**，绝不"凑合用"已有产物。
"""
import hashlib
import json
import os

from app.extensions import db
from app.models.desensitize_artifact import (
    DESENS_MODALITIES, KEY_SCOPE_PLATFORM, MODALITY_AUDIO, MODALITY_ECG,
    MODALITY_EEG, MODALITY_USERINFO, MODALITY_VIDEO, DesensitizedArtifact,
)
from app.utils.audio_desensitize import VERSION_TAG as AUDIO_VERSION_TAG
from app.utils.crypto import decrypt_file_to_temp, is_encrypted_file
from app.utils.signal_desensitize import (
    VERSION_TAG as SIGNAL_VERSION_TAG, key_fingerprint,
)
from app.utils.video_desensitize import (
    VERSION_TAG as VIDEO_VERSION_TAG, DEFAULT_VIDEO_STRENGTH, strength_ratio,
)

__all__ = [
    "ARTIFACT_DIR_NAME", "MODALITY_USERINFO", "MODALITY_AUDIO", "MODALITY_VIDEO",
    "MODALITY_EEG", "MODALITY_ECG", "DESENS_MODALITIES",
    "REVERSIBLE_MODALITIES", "IRREVERSIBLE_MODALITIES",
    "artifact_relpath", "artifact_abspath", "source_fingerprint",
    "desens_config_hash", "userinfo_rules_fingerprint", "platform_key_fingerprint",
    "find_artifact", "artifacts_for_assets", "upsert_artifact", "delete_artifact",
    "artifact_reuse_blocker", "decrypt_artifact_to_temp",
    "cleanup_orphan_artifact_files",
]

# 产物目录前缀：与 `.transcodes` 同级，**不在 raw/cleaned/feature/annotation 四层内**
# —— 否则受试者目录扫描器会把产物当成新采集文件重新入库（且会被单实例规则收敛掉）
ARTIFACT_DIR_NAME = ".desensitized"

# 源文件 mtime 比较容差（秒）：容器内 bind-mount 与宿主机时钟可能有亚秒差
_MTIME_TOL = 1.0


# ==================== 模态分类（密钥作用域相关，**唯一口径**） ====================
# 「勾了随包还原包还能不能复用已存产物」这个问题的答案**按模态不同**，
# 分类依据是「包里有没有可回推原文的参数」，而不是"哪种模态更敏感"。

# 可逆模态：脱敏是「定点整数域确定性加噪」（`y = (x + n) mod 2^bits`），还原参数
# （每列噪声幅度 / 精度 / 时间平移量 / 每声道幅度）随包写进 `_desens_manifest.json`，
# 持**同一把噪声密钥**方可逐字节还原。
# ⇒ 与**密钥作用域强绑定**：平台主密钥口径的产物**不能**混进「随包还原包」
#    （还原包改用本包一次性密钥派生噪声），否则包内清单自相矛盾（一部分能还原、
#    一部分不能）——故勾还原包时这三类必须实时重做。
REVERSIBLE_MODALITIES = frozenset({MODALITY_AUDIO, MODALITY_EEG, MODALITY_ECG})

# 不可逆模态：人脸马赛克是**结构性破坏**（块内像素被整块替换，原始细节已不存在）、
# userInfo 是字段掩码 —— 包内**不存在任何可回推原文的参数**（故不写 manifest）。
# ⇒ 与密钥作用域无关：勾了还原包时照旧可以复用已存产物（视频动辄分钟级，收益最大）。
IRREVERSIBLE_MODALITIES = frozenset({MODALITY_VIDEO, MODALITY_USERINFO})


# ==================== 路径 ====================

def artifact_relpath(pseudo_id, layer, data_type, file_name) -> str:
    """产物相对路径（相对 DATA_LAKE_DIR），与原资产逻辑路径同构

    与原资产的 `{pseudo}/{layer}/{type}/{name}` 保持一致，便于人工对照；
    顶层换成 `.desensitized` 前缀把它与真正的采集数据分开。
    """
    safe = lambda v: str(v or "unknown").strip().strip("/\\") or "unknown"  # noqa: E731
    return "/".join([ARTIFACT_DIR_NAME, safe(pseudo_id), safe(layer),
                     safe(data_type), safe(file_name)])


def artifact_abspath(storage_root, relpath) -> str:
    return os.path.join(storage_root, relpath)


def source_fingerprint(abs_path):
    """源文件指纹 (size, mtime)；读不到返回 (0, 0.0)

    只用 size + mtime 而不是 sha256：视频动辄几百 MB，每次导出都全量哈希不划算。
    代价是"同尺寸且 mtime 未变的内容替换"识别不出来 —— 采集端文件都是
    "写入即定名、不再原地改写"，这个代价可以接受。
    """
    try:
        st = os.stat(abs_path)
    except OSError:
        return 0, 0.0
    return int(st.st_size), float(st.st_mtime)


# ==================== 配置指纹 ====================

def platform_key_fingerprint() -> str:
    """平台脱敏密钥指纹（sha256 前 16 位）—— 参与 config_hash

    信号/音频/脑电的噪声由平台 DESENS_HMAC_KEY 派生。密钥一旦变更（例如
    `back/keys/` 丢失导致"缺失即自建"静默换钥，本项目已踩过），旧产物的噪声
    就再也还原不回来 —— 把指纹写进 config_hash 能让旧产物**自动失效**，
    而不是被当成"配置一致"复用到包里。
    """
    try:
        from app.utils.signal_desensitize import get_hmac_key
        return key_fingerprint(get_hmac_key())
    except Exception:
        return ""


def userinfo_rules_fingerprint() -> str:
    """userInfo 脱敏规则指纹：字段名 + 算法 + 保留位 + 掩码字符，按字段名排序

    规则一改（改算法/改保留位数/启停某字段），旧产物里的掩码值就不再是
    "当前规则的结果" —— 必须让旧产物失效，否则导出会发出与界面说明不符的内容。
    """
    from app.models.desensitize import DesensitizeRule
    try:
        rows = DesensitizeRule.query.filter_by(is_active=True).all()
    except Exception:
        return ""
    items = sorted(
        "%s|%s|%s|%s|%s" % (r.field_key, r.algorithm, r.keep_head or 0,
                            r.keep_tail or 0, r.mask_char or "*")
        for r in rows
    )
    return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


def desens_config_hash(modality, options=None) -> str:
    """影响产物**字节**的全部参数 → 指纹（sha256 hex，64 字符）

    :param modality: DESENS_MODALITIES 之一
    :param options: {"strength": "strongest"}（仅视频用）；未知键被忽略
    :raises ValueError: 未知模态（宁可报错，也不要给一个"看起来能复用"的假指纹）

    ⚠️ 视频档位先经 `strength_ratio()` **归一化**：`None` 与 `"strongest"` 得到同一个
    指纹，所以"批量脱敏时不传档位"与"导出时不传档位"能正确对上（都走默认档）。
    """
    if modality not in DESENS_MODALITIES:
        raise ValueError("未知脱敏模态：%s" % modality)
    opts = options or {}
    payload = {"modality": modality, "key_scope": KEY_SCOPE_PLATFORM}
    if modality == MODALITY_VIDEO:
        payload["version"] = VIDEO_VERSION_TAG
        # 归一化：让 None / 未知档 / 显式默认档 得到同一指纹
        payload["strength"] = round(
            strength_ratio(opts.get("strength") or DEFAULT_VIDEO_STRENGTH), 12)
    elif modality == MODALITY_AUDIO:
        payload["version"] = AUDIO_VERSION_TAG
    elif modality in (MODALITY_EEG, MODALITY_ECG):
        payload["version"] = SIGNAL_VERSION_TAG
    else:  # userinfo
        payload["version"] = "userinfo-mask-v1"
        payload["rules"] = userinfo_rules_fingerprint()
    payload["key_fp"] = platform_key_fingerprint()
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ==================== 查询 ====================

def find_artifact(asset_id, modality):
    """取某资产某模态的产物记录（没有则 None）"""
    return DesensitizedArtifact.query.filter_by(
        data_asset_id=asset_id, modality=modality).first()


def artifacts_for_assets(asset_ids):
    """批量取产物：{asset_id: {modality: DesensitizedArtifact}}

    逐资产查询会 N+1（导出动辄上百个资产），故一次 in_ 查完在内存里分组。
    """
    ids = [int(i) for i in (asset_ids or []) if i is not None]
    if not ids:
        return {}
    mapping = {}
    # 分批 in 查询，避免超长 SQL（与 asset_service.ingest_digest 同一做法）
    chunk = 500
    for i in range(0, len(ids), chunk):
        rows = DesensitizedArtifact.query.filter(
            DesensitizedArtifact.data_asset_id.in_(ids[i:i + chunk])).all()
        for row in rows:
            mapping.setdefault(row.data_asset_id, {})[row.modality] = row
    return mapping


def artifact_reuse_blocker(artifact, storage_root, expected_hash, abs_source):
    """产物是否可复用；可复用返回 None，否则返回**人类可读的拒绝原因**

    返回原因而不是布尔值：导出界面的"可复用数量"与审计日志都需要说清"为什么没复用"，
    只报一个 False 会让现场完全无法排查（本项目反复踩过"静默降级"的坑）。

    三条必要条件（缺一不可）：
      1. 配置指纹一致（本次要用的脱敏配置 == 产物生成时的配置）
      2. 源文件未变（size + mtime 对上）
      3. 产物文件仍存在
    """
    if artifact is None:
        return "无已存脱敏产物"
    if expected_hash and artifact.config_hash != expected_hash:
        return "脱敏配置与已存产物不一致（配置指纹不同）"
    if artifact.key_scope != KEY_SCOPE_PLATFORM:
        return "已存产物使用一次性包密钥（%s），无法在本包复用" % artifact.key_scope
    size, mtime = source_fingerprint(abs_source)
    if int(artifact.source_size or 0) != size:
        return "源文件大小已变化（%s → %s）" % (artifact.source_size, size)
    if abs(float(artifact.source_mtime or 0.0) - mtime) > _MTIME_TOL:
        return "源文件修改时间已变化"
    if not artifact.file_path:
        return "产物记录缺少文件路径"
    if not os.path.exists(artifact_abspath(storage_root, artifact.file_path)):
        return "产物文件已丢失（%s）" % artifact.file_path
    return None


# ==================== 写入 / 删除 ====================

def upsert_artifact(*, asset_id, modality, storage_root, relpath, file_name,
                    file_format, file_size, encrypted, algorithm_version,
                    config_hash, source_size, source_mtime, manifest_entry=None,
                    operator_id=None):
    """登记（或覆盖）一条产物记录

    `(data_asset_id, modality)` 唯一 ⇒ 重跑即覆盖。**重跑必须先删旧文件**，
    否则换了扩展名（如 .mkv → .mp4）会留下一份永远不会被引用的孤儿文件；
    由调用方负责（`delete_artifact(..., remove_file=True)` 或自行删）。
    """
    row = find_artifact(asset_id, modality)
    if row is None:
        row = DesensitizedArtifact(data_asset_id=asset_id, modality=modality)
        db.session.add(row)
    row.file_path = relpath
    row.file_name = file_name
    row.file_format = file_format
    row.file_size = int(file_size or 0)
    row.encrypted = bool(encrypted)
    row.algorithm_version = algorithm_version
    row.config_hash = config_hash
    row.key_scope = KEY_SCOPE_PLATFORM
    row.source_size = int(source_size or 0)
    row.source_mtime = float(source_mtime or 0.0)
    row.manifest_json = manifest_entry
    if operator_id is not None:
        row.operator_id = operator_id
    return row


def delete_artifact(asset_id, modality=None, storage_root=None,
                    remove_file=True) -> int:
    """删除产物记录（可选连带删磁盘文件），返回删除的记录数"""
    query = DesensitizedArtifact.query.filter_by(data_asset_id=asset_id)
    if modality:
        query = query.filter_by(modality=modality)
    rows = query.all()
    for row in rows:
        # 先收集路径再删记录（删记录后对象会过期）
        if remove_file and storage_root and row.file_path:
            abs_path = artifact_abspath(storage_root, row.file_path)
            _remove_file_safely(abs_path)
            # 顺手收掉因此变空的目录：否则「删产物」会在数据湖里留一串空壳目录
            prune_empty_artifact_dirs(storage_root, os.path.dirname(abs_path))
        db.session.delete(row)
    return len(rows)


def _remove_file_safely(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def prune_empty_artifact_dirs(storage_root, start_dir):
    """从 start_dir 向上删除空目录，**最多删到 `.desensitized/` 为止**（不含它本身）

    安全边界是这条规则的唯一理由：一旦越界向上，一次"删产物"就可能删掉数据湖里的
    业务目录（如某个受试者的 raw/ecg）。故循环条件用"仍在 `.desensitized/` 之下"
    严格判定；遇到非空目录（rmdir 抛 OSError）立即停手，不再往上试。
    """
    root = os.path.normcase(os.path.abspath(
        os.path.join(storage_root, ARTIFACT_DIR_NAME)))
    cur = os.path.abspath(start_dir or "")
    while len(os.path.normcase(cur)) > len(root) and \
            os.path.normcase(cur).startswith(root):
        try:
            os.rmdir(cur)
        except OSError:
            break   # 非空 / 不存在 / 权限不足：停手
        cur = os.path.dirname(cur)


# ==================== 读取（导出复用） ====================

def decrypt_artifact_to_temp(artifact, storage_root, suffix=None):
    """把产物解密到临时文件，返回 (path, is_temp)

    产物在数据湖里是 DMEC 密文（不新增明文敏感数据），导出前必须解出来再按
    目标模式（明文/加密）打包。解密是毫秒级，相比重新脱敏（视频分钟级）可忽略。
    """
    abs_path = artifact_abspath(storage_root, artifact.file_path)
    name_suffix = suffix if suffix is not None else os.path.splitext(
        artifact.file_name or "")[1]
    if is_encrypted_file(abs_path):
        return decrypt_file_to_temp(abs_path, suffix=name_suffix), True
    return abs_path, False


# ==================== 孤儿清扫 ====================

def cleanup_orphan_artifact_files(storage_root, logger=None) -> int:
    """启动时清扫 `.desensitized/` 下**没有对应 DB 记录**的残留文件

    为什么会有孤儿：资产/受试者删除时磁盘清理是 commit 之后 best-effort 做的，
    进程被 kill / 清理失败都会留下文件；而清理动作本身是"先删记录再删文件"，
    所以只可能留文件、不可能留"记录指向不存在的文件"（后者会被
    `artifact_reuse_blocker` 第 3 条挡住，不会造成复用错误）。

    安全边界：**只在 `.desensitized/` 目录内遍历**，且只删"路径不在 DB 记录里"
    的**文件**，绝不递归删目录本身。
    """
    root = os.path.join(storage_root, ARTIFACT_DIR_NAME)
    if not os.path.isdir(root):
        return 0
    try:
        known = {row.file_path for row in DesensitizedArtifact.query.all()}
    except Exception:
        # 查不到 DB 就不要清 —— 宁可不清理，也不能把活着的产物删掉
        return 0
    known_abs = {os.path.normcase(os.path.join(storage_root, p)) for p in known if p}

    removed = 0
    for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.normcase(path) in known_abs:
                continue
            _remove_file_safely(path)
            removed += 1
        # 顺手收掉空目录（best-effort；非空会抛 OSError 被忽略）
        if dirpath != root:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    if removed and logger:
        logger.info("启动清扫：已删除 %d 个无记录的脱敏产物残留文件", removed)
    return removed
