# -*- coding: utf-8 -*-
"""采集时间解析：为 DataAsset.timestamp_utc 提供统一取值口径

背景
----
DataAsset.timestamp_utc 此前在所有写入路径（create_asset / batch_create_assets /
upload_asset / scanner）均未赋值，DB 中恒为 NULL，导致导出界面展开受试者时
"采集时间"列显示为 "-"。

时区约定（实测确认）
--------------------
采集端（AgeCog）把 epoch 毫秒格式化成字符串时使用**北京时间**：
样例 userInfo.json 中 createDatetime="2026-06-14 12:50:46"，同目录 ID
17814126460001001 的前 13 位 = 1781412646000 ms = UTC 2026-06-14 04:50:46，
两者相差正好 +8h。文件名里的 14 位时间戳出自同一采集端，同为北京时间。

DB 列 timestamp_utc 存 UTC，展示时由 utils.time.to_local_str 加回 8 小时。
因此本模块所有返回值统一为 **UTC（即北京时间 - 8h）**，可直接写库。

取值优先级（高 → 低）
--------------------
1. 元数据中的显式采集时间字段（raw.createDatetime / raw.startDatetime / startTime ...）
2. 文件名中的时间戳：14 位 YYYYMMDDHHmmss → YYYYMMDD_HHmmss → 8 位 YYYYMMDD
   （先查 metadata.original_filename（采集端原始名，真实采集时刻），
     再查规范化 file_name（其时间戳为入库时刻，仅作兜底来源））
3. 调用方传入的 fallback_utc（入库时刻），保证该列不再出现 NULL
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

_CN_TZ = timedelta(hours=8)

# 14 位时间戳 YYYYMMDDHHmmss。前向锚定 (?<!\d) 避免从 17 位采集端 ID 中间截取；
# 尾部不锚定，以兼容 CAD20260721171326078001（时间戳后紧跟序列号）这类命名。
_TS14_RE = re.compile(
    r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"
    r"([01]\d|2[0-3])([0-5]\d)([0-5]\d)"
)
# 日期 + 分隔符 + 时间：20260609_144311 / 2026-06-09T14:43:11
_DATE_TIME_RE = re.compile(
    r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[_\-T ]"
    r"([01]\d|2[0-3])([0-5]\d)([0-5]\d)(?!\d)"
)
# 8 位日期 YYYYMMDD（仅在无更精确时间时使用）
_DATE8_RE = re.compile(
    r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"
)

_STR_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d",
)

# 元数据中显式采集时间字段（按可信度排序）。
# 刻意排除 assessTime / duration 之类的"耗时"字段（单位秒，非时间点）。
_META_TIME_KEYS = (
    "createDatetime", "startDatetime", "start_datetime", "startTime", "start_time",
    "collectTime", "collect_time", "collection_time", "recordTime", "record_time",
)


def _beijing_to_utc(dt: datetime) -> datetime:
    """无时区信息的时间按北京时间解释，转为 UTC naive datetime"""
    return dt - _CN_TZ


def _build(y, mo, d, h, mi, s) -> Optional[datetime]:
    """由 6 个字段构造时间（北京时间语义）并转 UTC；非法日期返回 None"""
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
    except (TypeError, ValueError):
        return None
    return _beijing_to_utc(dt)


def _from_epoch(value) -> Optional[datetime]:
    """epoch 秒/毫秒 → UTC datetime（epoch 本身即 UTC 语义）"""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:          # 毫秒
        ts /= 1000.0
    # 合理区间 2001-09 ~ 2100；越界说明不是时间戳（如 14 位纯数字日期串）
    if not (1e9 <= ts <= 4.1e9):
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
    except (ValueError, OSError, OverflowError):
        return None


def parse_time_from_name(name) -> Optional[datetime]:
    """从文件名/字符串中解析采集时间（返回 UTC datetime，失败返回 None）

    支持：14 位 YYYYMMDDHHmmss、YYYYMMDD_HHmmss、8 位 YYYYMMDD、ISO/常见日期串。
    传入 None / 空串 / 无日期特征的字符串均返回 None。
    """
    if not name:
        return None
    s = str(name).strip()
    if not s:
        return None
    if s.isdigit():
        dt = _from_epoch(s)
        if dt:
            return dt
    m = _TS14_RE.search(s)
    if m:
        dt = _build(*m.groups())
        if dt:
            return dt
    m = _DATE_TIME_RE.search(s)
    if m:
        dt = _build(*m.groups())
        if dt:
            return dt
    m = _DATE8_RE.search(s)
    if m:
        dt = _build(m.group(1), m.group(2), m.group(3), 0, 0, 0)
        if dt:
            return dt
    for fmt in _STR_FORMATS:
        try:
            return _beijing_to_utc(datetime.strptime(s[:19], fmt))
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return _beijing_to_utc(dt)


def _iter_meta_time_values(meta: dict) -> Iterator[Any]:
    """依次产出元数据中的候选采集时间值（raw 子对象优先于顶层）"""
    containers = []
    raw = meta.get("raw")
    if isinstance(raw, dict):
        containers.append(raw)
    summary = meta.get("summary")
    if isinstance(summary, dict):
        containers.append(summary)
    containers.append(meta)
    for c in containers:
        for k in _META_TIME_KEYS:
            v = c.get(k)
            if v not in (None, "", 0):
                yield v


def resolve_collection_time(metadata=None, original_filename=None,
                            extra_names=None, fallback_utc=None,
                            exclude_names=None) -> Optional[datetime]:
    """解析一条数据资产的采集时间（UTC datetime）

    Args:
        metadata: DataAsset.metadata_json
        original_filename: 原始文件名（可选；不传时从 metadata 里取）
        extra_names: 额外的候选文件名（如规范化 file_name），按顺序兜底
        fallback_utc: 兜底时间，必须是 **UTC naive** datetime（如 datetime.utcnow()）
        exclude_names: 需要排除的文件名（大小写不敏感）。用于剔除「入库时生成
            的存储名」——其时间戳是入库时刻而非采集时刻，拿它当采集时间会让
            该记录恒为最新（见 app.utils.singleton_assets.collection_time_of）
    Returns:
        UTC naive datetime；全部解析失败且无兜底时返回 None
    """
    meta = metadata if isinstance(metadata, dict) else {}

    # 1. 元数据显式采集时间字段
    for value in _iter_meta_time_values(meta):
        dt = parse_time_from_name(value) if isinstance(value, str) else _from_epoch(value)
        if dt:
            return dt

    # 2. 文件名（原始名优先 → 规范化名兜底）
    excluded = {str(n).strip().lower() for n in (exclude_names or []) if n}
    names = []
    candidates = (
        ([original_filename] if original_filename else [])
        + ([meta["original_filename"]] if meta.get("original_filename") else [])
        + list(extra_names or [])
    )
    for name in candidates:
        if str(name).strip().lower() in excluded:
            continue
        names.append(name)
    for name in names:
        dt = parse_time_from_name(name)
        if dt:
            return dt

    # 3. 兜底（入库时刻），保证该列不出现 NULL
    if fallback_utc is not None:
        if isinstance(fallback_utc, datetime):
            return fallback_utc
        return _from_epoch(fallback_utc)
    return None
