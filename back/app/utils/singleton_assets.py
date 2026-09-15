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


def _sort_key(asset, storage_root):
    """保留优先级排序键（越大越优先保留）"""
    ts = getattr(asset, "timestamp_utc", None)
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
