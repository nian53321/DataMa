# -*- coding: utf-8 -*-
"""眼动评估数据适配器

解析外部眼动评估系统推送的 sync_data.json，仅做字段解析存储，
不做字段映射（不映射到 Subject），也不自动划分风险分级。

文件命名模式：{评估单号}_sync_data.json（如 CAD20260721171326078001_sync_data.json）
配套文件：{评估单号}_sync_fields_zh.json（字段中文含义映射，仅供查阅，不导入）

适配策略（仅解析存储，不映射、不分级）：
- 解析 sync_data.json 得到原始字段字典
- 整体存入 DataAsset.metadata_json（data_type=eye, layer=feature）
- 原始 JSON 文件同时加密存入数据湖

不做的事：
- 不映射 risk_value → cognitive_risk_level
- 不映射 custom_phone → phone
- 不自动划分风险分级（normal/mci/dementia）
"""
import os
import re
import json


# sync_data 文件名匹配模式：{任意前缀}_sync_data.json
SYNC_DATA_FILENAME_RE = re.compile(r".+_sync_data\.json$", re.IGNORECASE)


def is_sync_data_file(filename):
    """判断文件名是否为眼动评估数据文件"""
    return bool(SYNC_DATA_FILENAME_RE.match(filename))


def load_sync_data(file_path):
    """解析 sync_data.json，返回原始字段字典（不做任何字段映射）

    :param file_path: sync_data.json 文件路径
    :return: dict，原始 JSON 解析结果；解析失败返回 None
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def find_sync_data_file(sub_dir):
    """在受试者目录下查找 sync_data.json 文件

    匹配模式：{任意前缀}_sync_data.json
    返回: 文件路径或 None
    """
    if not os.path.isdir(sub_dir):
        return None
    for fname in os.listdir(sub_dir):
        if is_sync_data_file(fname):
            return os.path.join(sub_dir, fname)
    return None
