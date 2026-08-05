# -*- coding: utf-8 -*-
"""命名规范引擎：解析命名规范模板并生成规范化文件名

命名规范 schema_json 结构：
{
  "template": "{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}",
  "defaults": {"scene": "SC", "batch": "B01"},
  "timestamp_format": "%Y%m%d%H%M%S",
  "allowed_extensions": ["mp4", "mkv", "wav", "csv", "json", "edf"]
}

支持变量：
  {data_type}  - 数据模态类型 (video/audio/eeg/ecg/eye/gait/scale/task)
  {pseudo_id}  - 受试者伪ID
  {timestamp}  - 北京时间戳（格式由 timestamp_format 指定，默认 %Y%m%d%H%M%S）
  {date}       - 北京日期 YYYYMMDD
  {scene}      - 采集场景代码（来自 subject.collection_scene）
  {batch}      - 采集批次号（来自 subject.collection_batch）
  {seq}        - 同受试者同模态当日序号（3位，如 001）
  {original}   - 原始文件名（不含扩展名）

扩展名自动保留原文件后缀。若模板已含 .{ext}，则不再追加。
"""
import os
import re
from datetime import datetime, timezone, timedelta

BEIJING_TZ = timezone(timedelta(hours=8))

# 合法文件名字符（字母/数字/下划线/连字符/点），其余替换为下划线
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_segment(value):
    """将变量值规范化为文件名安全片段"""
    if value is None:
        return ""
    s = str(value).strip()
    return _SAFE_NAME_RE.sub("_", s) or "NA"


def _build_context(subject, data_type, original_filename, seq=None):
    """构建模板变量上下文"""
    now = datetime.now(BEIJING_TZ)
    # 原始文件名（不含扩展名）
    original_stem = os.path.splitext(original_filename)[0] if original_filename else ""
    ctx = {
        "data_type": data_type or "",
        "pseudo_id": subject.pseudo_id if subject else "",
        "timestamp": now.strftime("%Y%m%d%H%M%S"),
        "date": now.strftime("%Y%m%d"),
        "scene": getattr(subject, "collection_scene", None) or "",
        "batch": getattr(subject, "collection_batch", None) or "",
        "seq": f"{seq:03d}" if seq is not None else "001",
        "original": original_stem,
    }
    return ctx


def _next_seq(subject_id, data_type):
    """查询同受试者同模态当日已上传数量，生成序号（避免重名）
    返回 seq（从1开始）；查询失败返回 1。
    """
    try:
        from app.models import DataAsset
        today_prefix = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
        # 简单统计当日该受试者该模态的资产数（不严格匹配文件名前缀，保证唯一性）
        count = DataAsset.query.filter_by(
            subject_id=subject_id, data_type=data_type
        ).count()
        return count + 1
    except Exception:
        return 1


def apply_naming_standard(standard, subject, data_type, original_filename):
    """应用命名规范生成规范化文件名

    Args:
        standard: DataStandard 实例（schema_json 含 template 等配置）
        subject: Subject 实例
        data_type: 模态类型字符串
        original_filename: 上传原始文件名

    Returns:
        (normalized_name, ext): 规范化文件名(不含扩展名), 扩展名(小写无点)
    """
    # 解析 schema
    schema = {}
    if standard and standard.schema_json:
        schema = standard.schema_json if isinstance(standard.schema_json, dict) else {}
    template = schema.get("template") or "{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}"
    defaults = schema.get("defaults") or {}
    ts_format = schema.get("timestamp_format") or "%Y%m%d%H%M%S"

    # 原始扩展名
    ext = ""
    if "." in (original_filename or ""):
        ext = original_filename.rsplit(".", 1)[-1].lower()

    # 构建上下文
    seq = _next_seq(subject.id if subject else None, data_type) if subject else 1
    ctx = _build_context(subject, data_type, original_filename, seq)

    # 时间戳自定义格式
    ctx["timestamp"] = datetime.now(BEIJING_TZ).strftime(ts_format)

    # 应用 defaults 填充空值
    for k, v in defaults.items():
        if k in ctx and not ctx[k]:
            ctx[k] = str(v)

    # 安全化所有变量值
    safe_ctx = {k: _safe_segment(v) for k, v in ctx.items()}

    # 渲染模板
    try:
        name = template.format(**safe_ctx)
    except KeyError as e:
        # 模板含未知变量，回退用原始文件名
        name = _safe_segment(ctx.get("original") or "unnamed")
    name = _safe_segment(name)
    return name, ext


def get_naming_standard(data_type):
    """查询某模态启用的命名规范（优先精确匹配模态，其次通用规范）
    返回 DataStandard 实例或 None。
    """
    from app.models.standard import DataStandard
    from sqlalchemy import or_
    # 精确匹配模态的命名规范
    std = DataStandard.query.filter_by(
        standard_type="naming", data_type=data_type, is_active=True
    ).order_by(DataStandard.updated_at.desc()).first()
    if std:
        return std
    # 通用规范（data_type 为 NULL、'all' 或空字符串）
    std = DataStandard.query.filter(
        DataStandard.standard_type == "naming",
        DataStandard.is_active.is_(True),
        or_(
            DataStandard.data_type.is_(None),
            DataStandard.data_type.in_(["all", ""]),
        ),
    ).order_by(DataStandard.updated_at.desc()).first()
    return std


def validate_extension(data_type, ext):
    """校验文件扩展名是否被该模态命名规范允许
    返回 (ok, message)。无规范时一律放行。
    """
    std = get_naming_standard(data_type)
    if not std or not std.schema_json:
        return True, None
    schema = std.schema_json if isinstance(std.schema_json, dict) else {}
    allowed = schema.get("allowed_extensions")
    if not allowed:
        return True, None
    if ext not in [e.lower() for e in allowed]:
        return False, f"文件扩展名 .{ext} 不在允许列表内：{', '.join(allowed)}"
    return True, None


# 默认命名规范（初始化用）
DEFAULT_NAMING_STANDARDS = [
    {
        "name": "通用文件命名规范",
        "standard_type": "naming",
        "data_type": "all",
        "version": "1.0.0",
        "schema_json": {
            "template": "{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}",
            "defaults": {"scene": "SC", "batch": "B01"},
            "timestamp_format": "%Y%m%d%H%M%S",
            "allowed_extensions": [],
            "variables_doc": {
                "data_type": "模态类型(video/audio/eeg/ecg/eye/gait/scale/task)",
                "pseudo_id": "受试者伪ID",
                "timestamp": "北京时间戳",
                "scene": "采集场景(来自受试者)",
                "batch": "采集批次(来自受试者)",
                "seq": "同受试者同模态序号(3位)",
                "original": "原始文件名(不含扩展名)",
            },
        },
        "description": "默认命名规则：模态_伪ID_时间戳_场景_批次.扩展名",
    },
]
