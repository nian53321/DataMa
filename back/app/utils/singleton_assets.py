# -*- coding: utf-8 -*-
"""单实例模态（singleton data types）约束与收敛

业务规则（2026-09-16 定）：**每个受试者的「心电 / 脑电 / 音频 / 个人信息」
每类只允许存在一条资产**。同一受试者下出现多份同类数据，视为异常（重复入库、
命名冲突覆盖后的幽灵记录、或后一次采集未替换前一次），必须收敛为一条。

为什么现有去重链管不到：
- `purge_duplicate_source_assets` / `_collapse_subject_sources` 按 **同源名**
  分组。两份**不同源**的心电（如 `ecg_20260717.csv` 与 `ecg_20260820.csv`）
  归不到一组，从设计上就不会被收敛。
- `purge_path_collision_assets` 只管**同一存储路径被多条记录引用**的幽灵，
  也不覆盖"多份不同源文件并存"。

保留策略（用户 2026-09-16 确认）：
1. 先淘汰**幽灵记录**：磁盘文件不存在、或 `file_size` 与磁盘实际大小不一致
   （被同秒命名冲突覆盖过的记录必然对不上）
2. 存活记录中取**采集时间最新**（`timestamp_utc`）的一条 —— 语义是"后一次
   采集覆盖前一次"
3. `timestamp_utc` 缺失或相同 → 取 `id` 最大（与项目既有同源收敛口径一致）

个人信息（userInfo）的识别：`data_type=json` 里既可能是个人信息，也可能是
相机标定等辅助 JSON。只有 **userInfo** 受本约束约束，判定见
`is_userinfo_asset`。
"""

import os
from datetime import datetime

from app.models.data import DataAsset, DataType
from app.utils.collection_time import resolve_collection_time
from app.utils.source_identity import normalize_original_filename

# 每受试者只允许一条的模态（data_type.value）
SINGLETON_DATA_TYPES = frozenset({"ecg", "eeg", "audio"})

# 个人信息：data_type=json 的子集，单独判定
USERINFO_DATA_TYPE = "json"
USERINFO_SOURCE_NAME = "userinfo.json"


def _dt_value(data_type):
    """data_type 统一取 value（兼容枚举 / 字符串 / None）"""
    return getattr(data_type, "value", data_type)


def is_userinfo_asset(asset):
    """是否为「个人信息」资产（data_type=json 且源文件是 userInfo.json）

    两种识别途径，任一命中即可：
    1. metadata.original_filename 归一化后 == `userinfo.json`
       （浏览器扫描给 `userInfo.json.enc`，后端 scanner 给 `userInfo.json`，
         归一化后统一为 `userinfo.json`）
    2. 规范化存储名以 `userinfo` 开头（命名规范渲染后的形态，如
       `userinfo_17887542610890003_20260914145422_1_1.json`）

    老数据里存在 `json_<pseudo>_<ts>_...json` 形态的 userInfo（旧命名模板），
    其 file_name 不含 userinfo 字样，只能靠途径 1 识别 —— 这也是必须同时看
    metadata 的原因。
    """
    if _dt_value(getattr(asset, "data_type", None)) != USERINFO_DATA_TYPE:
        return False
    meta = getattr(asset, "metadata_json", None) or {}
    if normalize_original_filename(meta.get("original_filename")) == USERINFO_SOURCE_NAME:
        return True
    file_name = (getattr(asset, "file_name", "") or "").strip().lower()
    return file_name.startswith("userinfo")


def is_singleton_asset(asset):
    """该资产是否属于「每受试者只允许一条」的模态"""
    dt = _dt_value(getattr(asset, "data_type", None))
    if dt in SINGLETON_DATA_TYPES:
        return True
    if dt == USERINFO_DATA_TYPE:
        return is_userinfo_asset(asset)
    return False


def is_singleton_data_type(data_type):
    """data_type 是否可能受本约束约束

    与 `is_singleton_asset` 的区别：这里是**类型级**判断，不区分 json 里的
    userInfo 与辅助 JSON。用于"某模态是否可能需要收敛"的粗筛（如 upload_asset
    里决定是否要查同类型既有资产）。
    """
    return _dt_value(data_type) in SINGLETON_DATA_TYPES or \
        _dt_value(data_type) == USERINFO_DATA_TYPE


def _asset_alive(asset, storage_root):
    """记录是否真实指向磁盘上的文件（且大小一致）

    判定"幽灵"：命名冲突导致后写覆盖先写时，被覆盖的那条记录的 `file_size`
    必然与磁盘当前大小对不上；文件已删的自然也不存在。
    """
    rel_path = getattr(asset, "file_path", None)
    if not rel_path or not storage_root:
        return True  # 信息不足时保守视为存活，不据此淘汰
    abs_path = os.path.join(storage_root, rel_path)
    try:
        if not os.path.exists(abs_path):
            return False
        return os.path.getsize(abs_path) == getattr(asset, "file_size", None)
    except OSError:
        return False


def _subject_pseudo_id(asset):
    """尽力取出资产所属受试者的伪ID（取不到返回 None）

    `find_singleton_violations` 返回的是 ORM 实例，可经关系懒加载取到；
    upload_asset 的 `with_entities` 列查询取不到，需由调用方显式传参。
    """
    try:
        subject = getattr(asset, "subject", None)
    except Exception:      # 列查询 / 已 detach 的实例
        return None
    return getattr(subject, "pseudo_id", None)


def _is_generated_collection_name(asset, name, pseudo_id=None):
    """name 是否为**平台命名规范生成**的存储名（其时间戳是入库时刻）

    两个条件同时满足才判定：
    1. name 与 `file_name` 完全同名 —— 说明它是被补写的伪原始名（批量导入时
       `meta["original_filename"] = file_name`，见 asset_service.batch_create_assets）
    2. 该名里含本受试者的伪ID —— 命名模板 `{data_type}_{pseudo_id}_{timestamp}...`
       的固定特征（见 utils/naming.py，{timestamp} 取 datetime.now()）

    只靠条件 1 会误伤「命名规范未改写文件名」的正常记录（采集端原始名恰好等于
    存储名），那类记录的时间戳是**真采集时间**，必须照常参与比较。
    """
    if not name:
        return False
    fn = (getattr(asset, "file_name", "") or "").strip().lower()
    if not fn or str(name).strip().lower() != fn:
        return False
    pid = pseudo_id or _subject_pseudo_id(asset)
    if not pid:
        return False
    return f"_{str(pid).strip().lower()}_" in fn


def collection_time_of(asset, pseudo_id=None):
    """资产的**真实采集时间**（UTC naive datetime），无法判定时返回 None

    与 `DataAsset.timestamp_utc` 的区别：`timestamp_utc` 在解析不出采集时间时
    用**入库时刻**兜底。拿它做「谁更新」的比较会让入库晚的老记录恒为最新
    （入库晚 ≠ 采集晚）——2026-09-16 现场：一条脑电记录的采集时间被存储名里的
    入库时刻污染，导致同目录 12 个脑电文件全部被判为"更旧"而拒绝入库。

    解析不出即返回 None，由调用方退化为其它口径（如「后上传替换先上传」）。
    """
    meta = getattr(asset, "metadata_json", None)
    if not isinstance(meta, dict):
        meta = {}
    orig = meta.get("original_filename")
    exclude = [orig] if _is_generated_collection_name(asset, orig, pseudo_id) else []
    return resolve_collection_time(
        meta, original_filename=orig, fallback_utc=None, exclude_names=exclude,
    )


def _sort_key(asset, storage_root):
    """保留优先级排序键（越大越优先保留）"""
    ts = collection_time_of(asset)
    if ts is None:
        ts = datetime.min
    return (
        1 if _asset_alive(asset, storage_root) else 0,  # 幽灵先淘汰
        ts,                                             # 采集时间最新优先
        getattr(asset, "id", 0) or 0,                   # 最后按 id 兜底
    )


def find_singleton_violations(subject_id=None, storage_root=None):
    """查找违反「单实例」约束的分组

    :param subject_id: 限定受试者（None = 全库）
    :param storage_root: 数据湖根目录（用于幽灵判定，可空）
    :returns: {(subject_id, data_type_value): [asset, ...]} 只含条数 > 1 的分组
    """
    query = DataAsset.query
    if subject_id is not None:
        query = query.filter(DataAsset.subject_id == subject_id)
    # 粗筛：只取可能是单实例的模态，避免全表拉到内存
    candidates = [
        dt for dt in DataType
        if is_singleton_data_type(dt)
    ]
    rows = query.filter(DataAsset.data_type.in_(candidates)).all()

    groups = {}
    for asset in rows:
        if not is_singleton_asset(asset):
            continue  # json 里的辅助 JSON（相机标定等）不受约束
        key = (asset.subject_id, _dt_value(asset.data_type))
        groups.setdefault(key, []).append(asset)

    return {k: v for k, v in groups.items() if len(v) > 1}


def pick_singleton_keeper(assets, storage_root=None):
    """组内保留哪一条：幽灵先淘汰 → 采集时间最新 → id 最大

    :returns: 保留资产的 id；空列表返回 None
    """
    if not assets:
        return None
    best = max(assets, key=lambda a: _sort_key(a, storage_root))
    return best.id


# metadata_json 中的键：被本规则淘汰、但**已被吸收**的源文件清单。
#
# 为什么需要：收敛会删除多余记录，被删记录在 ingest-digest 里就消失了。前端
# 每次扫描都拿 digest 对账，判定"后端没有这个文件" → 作废本地记录 → 重传 →
# 上传后又被单实例规则淘汰 → 下一轮再重传。实测表现（2026-09-16）：同一批
# 12 个脑电文件每个扫描周期重传一次，每次还写 12 条"跳过旧采集文件"审计日志。
#
# 登记后 digest 会把这些源文件一并报为"已处理"，对账命中 → 不再重传 → 循环终止。
ABSORBED_SOURCES_KEY = "absorbed_sources"


def source_entry_of(row):
    """从资产行取出其源文件条目 (original_filename, original_size)

    无原始名（手动登记等）时返回 None —— 这类记录不在上传幂等与对账范围内。
    """
    meta = getattr(row, "metadata_json", None)
    if not isinstance(meta, dict):
        return None
    name = meta.get("original_filename")
    if not name:
        return None
    return (name, meta.get("original_size"))


def absorbed_entries(asset_or_meta):
    """取已登记的「已吸收源文件」条目 [[name, size], ...]"""
    if hasattr(asset_or_meta, "metadata_json"):
        meta = getattr(asset_or_meta, "metadata_json", None)
    else:
        meta = asset_or_meta
    if not isinstance(meta, dict):
        return []
    out = []
    for entry in (meta.get(ABSORBED_SOURCES_KEY) or []):
        if isinstance(entry, (list, tuple)) and entry:
            out.append([entry[0], entry[1] if len(entry) > 1 else None])
    return out


def absorb_sources(asset, sources):
    """把被本规则淘汰的源文件登记到保留资产的 metadata

    :param asset: 保留下来的资产（需要有 metadata_json 字段）
    :param sources: [(name, size), ...]；name 空则跳过
    :returns: bool 是否实际发生了变更（无变更时不必写库）
    """
    meta = dict(getattr(asset, "metadata_json", None) or {})
    items = []
    index = {}
    for e in (meta.get(ABSORBED_SOURCES_KEY) or []):
        if not (isinstance(e, (list, tuple)) and e):
            continue
        items.append([e[0], e[1] if len(e) > 1 else None])
        index[str(e[0]).strip().lower()] = len(items) - 1
    changed = False
    for name, size in sources or []:
        if not name:
            continue
        key = str(name).strip().lower()
        if key in index:
            # 同一源文件大小变了（磁盘上被重写/追加）→ 同步 size，否则前端
            # 按（文件名+大小）对账永远命中不了，仍会每轮重传
            at = index[key]
            if items[at][1] != size:
                items[at][1] = size
                changed = True
            continue
        index[key] = len(items)
        items.append([name, size])
        changed = True
    if not changed:
        return False
    meta[ABSORBED_SOURCES_KEY] = items
    # 整体重新赋值：SQLAlchemy 对 JSON 列的原地修改不会被标记为 dirty
    asset.metadata_json = meta
    return True


def collapse_singleton_duplicates(subject_id=None, storage_root=None):
    """计算待删除的资产 id 列表（不执行删除，交给调用方统一清理）

    与 `_purge_stale_uploads` 分工：本函数只做"判定"，删除留给调用方，
    以便复用既有的留档 + 级联 + 共享文件保护链路。

    :returns: 待删 id 列表（每组保留一条）
    """
    stale_ids = []
    for _key, assets in find_singleton_violations(subject_id, storage_root).items():
        keeper = pick_singleton_keeper(assets, storage_root)
        stale_ids.extend(a.id for a in assets if a.id != keeper)
    return sorted(set(stale_ids))
