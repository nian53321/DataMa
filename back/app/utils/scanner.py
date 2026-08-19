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
from app.utils.naming import apply_naming_standard
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
        return plain_path, _parse_user_info(plain_path)

    # 优先级 2: 外部加密 userInfo.json.enc
    enc_path = os.path.join(sub_dir, "userInfo.json.enc")
    if os.path.isfile(enc_path):
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
    disease = str(data.get("disease", "")).strip()
    severity = data.get("severity", "")
    if disease and disease != "未知病症":
        risk = disease
        if severity not in (0, "0", "", None):
            risk = f"{disease}（严重度: {severity}）"
        fields["cognitive_risk_level"] = risk
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
                    # 受试者已存在：检查是否有未导入的新类型文件（增量导入）
                    if config.auto_upload_files:
                        existing_types = {
                            a.data_type.value for a in
                            DataAsset.query.filter_by(subject_id=existing.id).all()
                        }
                        _import_files_for_subject(
                            sub_dir, existing,
                            skip_data_types=existing_types,
                            failure_collector=result["failures"],
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
                    _import_files_for_subject(
                        sub_dir, subject,
                        failure_collector=result["failures"],
                    )

                new_count += 1

            config.last_scan_at = datetime.utcnow()
            config.last_scan_count = new_count
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise e

        result["new_count"] = new_count
        result["skipped"] = skipped
        return result
    finally:
        _scan_run_lock.release()


def _import_files_for_subject(sub_dir, subject, skip_data_types=None, failure_collector=None):
    """将子文件夹内的数据文件导入为数据资产

    自动适配三种源文件格式：
    1. 项目自身 DMEC 加密格式 → 解密后重新加密写入
    2. 外部 AES-256-CBC 加密格式 → 用数据库外部密钥尝试解密
    3. 明文文件 → 直接读取

    所有文件统一用项目 DMEC 格式加密后写入数据湖。
    跳过 userInfo.json / userInfo.json.enc
    解密失败的文件记录到 failure_collector 并跳过（不中断扫描）

    :param skip_data_types: 已导入的数据类型集合（set[str]），这些类型的文件将被跳过（增量导入去重）
    """
    from flask import current_app
    from app.utils.naming import get_naming_standard

    data_lake = current_app.config["DATA_LAKE_DIR"]
    # 受试者数据目录基: data_lake/raw/{pseudo_id}/ 和 data_lake/feature/{pseudo_id}/
    # 实际文件存储在 data_lake/{layer}/{pseudo_id}/{data_type}/{filename}，与 upload_asset 保持一致
    subject_raw_base = os.path.join(data_lake, "raw", subject.pseudo_id)
    subject_feature_base = os.path.join(data_lake, "feature", subject.pseudo_id)
    os.makedirs(subject_raw_base, exist_ok=True)

    # 跳过文件名集合：元数据文件
    # （正常情况下扫描目录不含密钥文件，但防御性跳过避免误入库）
    skip_filenames_lower = {
        "userinfo.json", "userinfo.json.enc",
        "密钥.txt", "key.txt", "secret.txt",
    }

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
        # 剥离 .enc 后缀得到原始文件名（用于识别类型与应用命名规范）
        original_name = _strip_enc_suffix(fname)
        ext = original_name.rsplit(".", 1)[-1] if "." in original_name else ""
        # 眼动评估数据 sync_data.json → data_type=eye, layer=feature
        # （用剥离 .enc 后的文件名判断，外部加密的 sync_data/量表同样可识别）
        is_sync = is_sync_data_file(original_name)
        # 量表数据（MoCA/MMSE/AD8 等）→ data_type=scale, layer=feature
        is_scale = is_scale_data_file(original_name)
        if is_sync:
            data_type = "eye"
        elif is_scale:
            data_type = "scale"
        else:
            data_type = _detect_data_type(fname)
        # 增量导入去重：跳过已导入的数据类型
        if skip_data_types and data_type in skip_data_types:
            continue
        # 应用命名规范（按模态精确匹配命名规范，回退到通用规范）
        new_name = original_name
        naming_std = get_naming_standard(data_type)
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
        src_size = os.path.getsize(src_path)
        asset_metadata = {
            "original_filename": fname,
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
