# -*- coding: utf-8 -*-
"""量表数据适配器（多量表统一解析）

适配外部量表系统推送的 JSON 文件（如 MoCA_103941_20250822.json），
统一支持多种认知量表：MoCA（蒙特利尔认知评估量表）/ MMSE（简易智能精神状态量表）/
AD8（痴呆早期筛查量表），并预留扩展机制，仅做字段解析与存储，不做字段映射、
不自动划分风险分级。

文件命名模式：{量表名}_{用户ID}_{日期}.json（如 MoCA_103941_20250822.json）

适配的采集方式：
- 自动采集：受试者目录扫描（scanner.py）按文件名识别并导入
- 手动录入：受试者表单直接录入总分（Subject.mmse_score / moca_score / ad8_score）
- 外部系统推送：parse-scale API 上传 JSON 解析（AgeCog 等产品 qstList 结构）

解析策略（仅解析存储，不映射、不分级）：
- 解析量表 JSON 得到原始字段字典
- 通用摘要解析 parse_scale_summary()：识别量表类型 → 总分 + 分项得分 + 分级参考
- 整体存入 DataAsset.metadata_json（data_type=scale, layer=feature）
- 原始 JSON 文件同时加密存入数据湖
"""
import os
import re
import json


# 量表文件名匹配模式：{量表名}_{用户ID}_{日期}.json
# 支持 MoCA、MMSE、AD8 等量表（大写前缀匹配，忽略大小写）
SCALE_FILENAME_RE = re.compile(r"^(MoCA|MMSE|AD8)_.+_\d{8}\.json$", re.IGNORECASE)

# 文件名前缀 → 量表 ID（用于 is_scale_data_file / detect_scale_type）
_SCALE_NAME_TO_ID = {
    "MOCA": "MOCA",
    "MMSE": "MMSE",
    "AD8": "AD8",
}


class ScaleSpec:
    """量表规格：定义一种量表的识别与解析规则"""

    def __init__(self, assess_ids, name, max_score, grade=None,
                 education_bonus=None, section_mode="auto"):
        """
        :param assess_ids: 该量表的 assessId 别名列表（大写），如 ["MOCA", "MOCA-B"]
        :param name: 量表中文名
        :param max_score: 满分
        :param grade: 分级函数 grade(score, bonus) -> (level, label) | (None, None)
        :param education_bonus: 教育校正函数 education_bonus(edu_years) -> float（0 表示不加）
        :param section_mode: 分项模式 auto（自动）/ sections（qstType=2 分项标题）/
                             flat（逐题平铺，无分项标题）
        """
        self.assess_ids = [str(a).upper() for a in assess_ids]
        self.name = name
        self.max_score = float(max_score)
        self.grade = grade
        self.education_bonus = education_bonus
        self.section_mode = section_mode

    def matches(self, assess_id):
        return assess_id in self.assess_ids


# ---------------- 各量表评分参考规则（仅供展示，不自动写入受试者风险分级） ----------------

def _grade_moca(score, bonus):
    """MoCA：总分 ≥26 正常；<26 提示认知功能损害"""
    score = float(score or 0) + float(bonus or 0)
    if score >= 26:
        return "normal", "正常"
    return "mci", "提示认知功能损害"


def _grade_mmse(score, bonus):
    """MMSE：≥27 正常；21~26 轻度痴呆；10~20 中度痴呆；<10 重度痴呆"""
    score = float(score or 0) + float(bonus or 0)
    if score >= 27:
        return "normal", "正常"
    if score >= 21:
        return "mci", "轻度痴呆（21~26 分）"
    if score >= 10:
        return "dementia", "中度痴呆（10~20 分）"
    return "dementia", "重度痴呆（<10 分）"


def _grade_ad8(score, bonus):
    """AD8：<2 正常；≥2 提示认知障碍（得分越高越差，反向量表）"""
    score = float(score or 0) + float(bonus or 0)
    if score < 2:
        return "normal", "正常"
    return "mci", "提示认知障碍（≥2 分）"


def _moca_education_bonus(edu_years):
    """MoCA 教育校正：受教育年限 ≤12 年，总分加 1 分（上限满分）"""
    try:
        return 1.0 if float(edu_years) <= 12 else 0.0
    except (TypeError, ValueError):
        return 0.0


# 量表注册表：key 为 assessId 大写，value 为 ScaleSpec
_SCALE_REGISTRY = {
    "MOCA": ScaleSpec(
        assess_ids=["MOCA", "MOCA-1", "MOCA-B"],
        name="蒙特利尔认知评估量表（MoCA）",
        max_score=30,
        grade=_grade_moca,
        education_bonus=_moca_education_bonus,
        section_mode="sections",
    ),
    "MMSE": ScaleSpec(
        assess_ids=["MMSE", "MMSE-1"],
        name="简易智能精神状态量表（MMSE）",
        max_score=30,
        grade=_grade_mmse,
        section_mode="sections",
    ),
    "AD8": ScaleSpec(
        assess_ids=["AD8", "AD-8"],
        name="AD8 痴呆早期筛查量表",
        max_score=8,
        grade=_grade_ad8,
        section_mode="flat",
    ),
}


def _normalize_assess_id(value):
    """归一化 assessId：去空白、去量表中文描述后的 ID 部分、转大写"""
    if not value:
        return ""
    s = str(value).strip().upper()
    # 兼容 "MMSE（简易智能精神状态量表）" 这类带括号描述的写法
    s = re.split(r"[（(]", s)[0].strip()
    return s


def get_scale_spec(assess_id, scale_type=None):
    """按 assessId 或文件名识别量表规格，返回 ScaleSpec 或 None"""
    aid = _normalize_assess_id(assess_id)
    if aid:
        # 先精确匹配，再按别名匹配
        for spec in _SCALE_REGISTRY.values():
            if spec.matches(aid):
                return spec
    if scale_type:
        key = str(scale_type).upper()
        if key in _SCALE_REGISTRY:
            return _SCALE_REGISTRY[key]
    return None


def detect_scale_type(filename):
    """从文件名识别量表类型，返回 assessId 大写（MOCA/MMSE/AD8）或 None

    用于文件名可识别但 JSON 内 assessId 缺失/不一致时的兜底。
    自动剥离外部加密后缀 .enc（如 MoCA_xxx_20250101.json.enc 同样可识别）。
    """
    if not filename:
        return None
    name = filename
    if name.lower().endswith(".enc"):
        name = name[:-4]
    m = SCALE_FILENAME_RE.match(name)
    if not m:
        return None
    return _SCALE_NAME_TO_ID.get(m.group(1).upper())


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


# ---------------- 通用摘要解析 ----------------

def _parse_qst_sections(data):
    """从 AgeCog 系统 qstList 结构中提取分项得分列表

    qstType 约定：0=具体题目（含 maxScore/score），2=分项标题，3=指导语。
    优先使用分项标题（qstType=2），无分项标题时回退逐题平铺（qstType=0 且 maxScore>0）。

    :return: (sections, total_score, total_max)；无任何计分项时返回 ([], 0.0, 0.0)
    """
    qst_list = data.get("qstList") or data.get("questions") or []
    if not isinstance(qst_list, list):
        return [], 0.0, 0.0

    sections = []
    # 模式一：分项标题（qstType=2，MoCA/MMSE 结构）
    section_items = [q for q in qst_list if isinstance(q, dict) and q.get("qstType") == 2]
    if section_items:
        for q in section_items:
            content = str(q.get("content", "")).strip()
            # 去掉前导序号（如 "1、视空间与执行功能" → "视空间与执行功能"）
            content = re.sub(r"^\d+[、．.．]\s*", "", content)
            score = float(q.get("score", 0) or 0)
            max_score = float(q.get("maxScore", 0) or 0)
            sections.append({
                "name": content or "分项",
                "score": score,
                "max_score": max_score,
            })
    else:
        # 模式二：逐题平铺（AD8 等无分项标题结构）
        for q in qst_list:
            if not isinstance(q, dict) or q.get("qstType") not in (0, None):
                continue
            max_score = float(q.get("maxScore", 0) or 0)
            if max_score <= 0:
                continue
            content = str(q.get("content", "")).strip()
            content = re.sub(r"^\d+[、．.．]\s*", "", content)
            # 截断过长的题目文本，避免可视化雷达图标签过长
            if len(content) > 18:
                content = content[:18] + "…"
            sections.append({
                "name": content or "题目",
                "score": float(q.get("score", 0) or 0),
                "max_score": max_score,
            })

    total_score = sum(s["score"] for s in sections)
    total_max = sum(s["max_score"] for s in sections)
    return sections, total_score, total_max


def _extract_top_level_score(data, spec):
    """从一级字段提取总分（兼容多种外部系统的字段命名）"""
    candidates = [
        data.get("totalScore"), data.get("total_score"),
        data.get("score"), data.get("finalScore"), data.get("total"),
    ]
    for c in candidates:
        if c is None or c == "":
            continue
        try:
            return float(c)
        except (TypeError, ValueError):
            continue
    # 量表缩写字段（如 {"moca": 28} / {"mmse": 20} / {"ad8": 2}）
    for key in ("moca", "mmse", "ad8", "moca_score", "mmse_score", "ad8_score"):
        if key in data and data[key] not in (None, ""):
            try:
                # 注意：一级字段可能是对象嵌套（{"moca": {"score": 28}}）
                v = data[key]
                if isinstance(v, dict):
                    continue
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _extract_education_years(data):
    """从量表 JSON 中提取受教育年限（兼容多种字段命名）"""
    for key in ("educationYears", "education_years", "educationLevel",
                "education_level", "education"):
        v = data.get(key)
        if v is None or v == "":
            continue
        if isinstance(v, dict):
            # 对象形式（{"name": "本科", "years": 12}）
            for sub in ("years", "year", "value"):
                if sub in v and v[sub] not in (None, ""):
                    try:
                        return float(v[sub])
                    except (TypeError, ValueError):
                        pass
            continue
        # 字符串可能是 "12年" / "本科" / "12"
        s = str(v).strip()
        m = re.search(r"(\d+(?:\.\d+)?)", s)
        if m:
            return float(m.group(1))
        # 常见学历等级粗略估值（仅当 JSON 只给学历名称时兜底，按≤12年处理）
        if re.search(r"小学|初中|高中|中专|大专以下", s):
            return 12.0
        try:
            return float(s)
        except (TypeError, ValueError):
            continue
    return None


def parse_scale_summary(data, scale_type=None):
    """通用量表摘要解析：识别量表 → 总分 + 分项得分 + 分级参考

    :param data: load_scale_data 返回的原始字典
    :param scale_type: 可选，文件名识别出的量表类型（assessId 缺失时兜底）
    :return: dict 统一摘要结构；无法识别且无可解析内容时返回 None

    统一摘要结构：
    {
        "assess_id": "MOCA",              # 识别出的量表 ID（大写）
        "assess_name": "蒙特利尔认知评估量表（MoCA）",
        "total_score": 28.0,              # 总分（含教育校正）
        "max_score": 30.0,                # 满分
        "sections": [{"name", "score", "max_score"}, ...],  # 分项得分
        "level": "normal",                # 分级参考 normal/mci/dementia（仅供展示）
        "level_label": "正常",
        "education_adjusted": True,       # 是否应用教育校正
        "education_bonus": 1.0,           # 教育校正加分
        "assess_time": "",                # 测评时间
        "comment": "",                    # 备注
        "user_id": None,                  # 外部系统用户 ID
    }
    """
    if not data or not isinstance(data, dict):
        return None

    assess_id = _normalize_assess_id(data.get("assessId") or data.get("assess_id"))
    spec = get_scale_spec(assess_id, scale_type)

    # 分项解析（AgeCog qstList 结构，MoCA/MMSE/AD8 通用）
    sections, sec_total, sec_max = _parse_qst_sections(data)
    # 一级字段总分（外部系统平铺结构）
    top_score = _extract_top_level_score(data, spec)

    if spec is None:
        # 无法识别量表类型：若仍有可解析的计分内容，返回基础摘要（assess_name 未知）
        if not sections and top_score is None:
            return None
        name = "量表"
        max_score = sec_max or 0
    else:
        name = spec.name
        max_score = spec.max_score

    # 总分确定：优先分项汇总（已由分项权威计分），其次一级字段
    if sections:
        total_score = sec_total
        if not max_score or max_score <= 0:
            max_score = sec_max or 0
    elif top_score is not None:
        total_score = top_score
    else:
        total_score = 0.0

    # 教育校正（MoCA：受教育年限 ≤12 年 +1 分，不超过满分）
    bonus = 0.0
    education_adjusted = False
    if spec is not None and spec.education_bonus is not None:
        edu_years = _extract_education_years(data)
        if edu_years is not None:
            bonus = float(spec.education_bonus(edu_years) or 0)
            if bonus > 0:
                total_score = min(total_score + bonus, max_score or total_score + bonus)
                education_adjusted = True

    # 分级参考（仅供展示，不写入受试者认知风险分级）
    level, level_label = None, None
    if spec is not None and spec.grade is not None:
        try:
            level, level_label = spec.grade(total_score, 0.0)
        except (TypeError, ValueError):
            pass

    return {
        "assess_id": assess_id or (str(scale_type).upper() if scale_type else ""),
        "assess_name": name,
        "total_score": total_score,
        "max_score": max_score,
        "sections": sections,
        "level": level,
        "level_label": level_label,
        "education_adjusted": education_adjusted,
        "education_bonus": bonus,
        "assess_time": data.get("startDatetime", data.get("assess_time", "")),
        "comment": data.get("comment", ""),
        "user_id": data.get("userId", data.get("user_id")),
    }


def parse_moca_summary(data):
    """兼容入口：MoCA 量表摘要解析（等价于 parse_scale_summary，仅保留给旧调用方）

    新版统一使用 parse_scale_summary 支持多种量表。
    """
    return parse_scale_summary(data, scale_type="MOCA")


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