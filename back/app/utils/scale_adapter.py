# -*- coding: utf-8 -*-
"""量表数据适配器

解析外部量表系统推送的 JSON 文件（如 MoCA_103941_20250822.json），
仅做字段解析与存储，不做字段映射，也不自动划分。

文件命名模式：{量表名}_{用户ID}_{日期}.json（如 MoCA_103941_20250822.json）

支持量表：
- MoCA（蒙特利尔认知评估量表）

解析策略（仅解析存储，不映射、不分级）：
- 解析量表 JSON 得到原始字段字典
- 计算 MoCA 总分与各分项得分（从 qstList 中 qstType=2 的分项汇总）
- 整体存入 DataAsset.metadata_json（data_type=scale, layer=feature）
- 原始 JSON 文件同时加密存入数据湖
"""
import os
import re
import json


# 量表文件名匹配模式：{量表名}_{用户ID}_{日期}.json
# 支持 MoCA、MMSE、AD8 等量表
SCALE_FILENAME_RE = re.compile(r"^(MoCA|MMSE|AD8)_.+_\d{8}\.json$", re.IGNORECASE)


def is_scale_data_file(filename):
    """判断文件名是否为量表数据文件"""
    return bool(SCALE_FILENAME_RE.match(filename))


def load_scale_data(file_path):
    """解析量表 JSON，返回原始字段字典（不做任何字段映射）

    :param file_path: 量表 JSON 文件路径
    :return: dict，原始 JSON 解析结果；解析失败返回 None
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = f.read()
        return json.loads(raw)
    except Exception:
        return None


def parse_moca_summary(data):
    """从 MoCA 量表数据中提取总分与分项得分

    MoCA 结构：qstList 中 qstType=2 为分项标题（含 maxScore 和 score），
    qstType=0 为具体题目，qstType=3 为指导语。

    :param data: load_scale_data 返回的原始字典
    :return: dict，包含 total_score、max_score、sections 列表；非 MoCA 数据返回 None
    """
    if not data or not isinstance(data, dict):
        return None
    assess_id = str(data.get("assessId", "")).upper()
    if assess_id != "MOCA":
        return None

    qst_list = data.get("qstList", [])
    sections = []
    total_score = 0.0
    total_max = 0.0
    for q in qst_list:
        if q.get("qstType") != 2:
            continue
        content = q.get("content", "").strip()
        # 去掉前导序号（如 "1、视空间与执行功能" → "视空间与执行功能"）
        content = re.sub(r"^\d+、\s*", "", content)
        score = float(q.get("score", 0) or 0)
        max_score = float(q.get("maxScore", 0) or 0)
        sections.append({
            "name": content,
            "score": score,
            "max_score": max_score,
        })
        total_score += score
        total_max += max_score

    return {
        "assess_id": "MoCA",
        "total_score": total_score,
        "max_score": total_max or 30,
        "sections": sections,
        "assess_time": data.get("startDatetime", ""),
        "user_id": data.get("userId"),
        "comment": data.get("comment", ""),
    }


def find_scale_data_file(sub_dir):
    """在受试者目录下查找量表数据文件

    匹配模式：{量表名}_{用户ID}_{日期}.json
    返回: 文件路径或 None
    """
    if not os.path.isdir(sub_dir):
        return None
    for fname in os.listdir(sub_dir):
        if is_scale_data_file(fname):
            return os.path.join(sub_dir, fname)
    return None
