# -*- coding: utf-8 -*-
"""脱敏配置模型：持久化字段级脱敏规则，支持多种算法与开关控制"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


# 默认脱敏规则（首次启动写入数据库）
# field_key 对应各模型 to_dict() 输出的 key
DEFAULT_RULES = [
    {
        "field_key": "real_name", "field_label": "姓名",
        "algorithm": "mask_middle", "keep_head": 1, "keep_tail": 0,
        "mask_char": "*", "is_active": True, "sort_order": 1,
    },
    {
        "field_key": "phone", "field_label": "电话",
        "algorithm": "mask_middle", "keep_head": 3, "keep_tail": 4,
        "mask_char": "*", "is_active": True, "sort_order": 2,
    },
    {
        "field_key": "email", "field_label": "邮箱",
        "algorithm": "mask_email", "keep_head": 0, "keep_tail": 0,
        "mask_char": "*", "is_active": True, "sort_order": 3,
    },
    {
        "field_key": "license_no", "field_label": "执业证号",
        "algorithm": "mask_middle", "keep_head": 4, "keep_tail": 4,
        "mask_char": "*", "is_active": True, "sort_order": 4,
    },
    {
        "field_key": "id_card", "field_label": "身份证号",
        "algorithm": "mask_middle", "keep_head": 6, "keep_tail": 4,
        "mask_char": "*", "is_active": True, "sort_order": 5,
    },
    {
        "field_key": "address", "field_label": "地址",
        "algorithm": "mask_tail", "keep_head": 0, "keep_tail": 0,
        "mask_char": "*", "is_active": True, "sort_order": 6,
    },
    {
        # 性别与年龄属准标识符（quasi-identifier）：与姓名/电话组合可反推个体，
        # 因此默认启用、整体替换（保留位数只会泄露取值长度，无保留价值）
        "field_key": "gender", "field_label": "性别",
        "algorithm": "mask_all", "keep_head": 0, "keep_tail": 0,
        "mask_char": "*", "is_active": True, "sort_order": 7,
    },
    {
        "field_key": "age", "field_label": "年龄",
        "algorithm": "mask_all", "keep_head": 0, "keep_tail": 0,
        "mask_char": "*", "is_active": True, "sort_order": 8,
    },
    {
        "field_key": "remark", "field_label": "备注",
        "algorithm": "mask_tail", "keep_head": 0, "keep_tail": 0,
        "mask_char": "*", "is_active": False, "sort_order": 9,
    },
    {
        "field_key": "pseudo_id", "field_label": "伪ID",
        "algorithm": "mask_middle", "keep_head": 2, "keep_tail": 2,
        "mask_char": "*", "is_active": False, "sort_order": 10,
    },
]


class DesensitizeSetting(db.Model):
    """脱敏全局开关（单行，id=1）"""
    __tablename__ = "desensitize_settings"

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False, comment="脱敏总开关")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "enabled": self.enabled,
            "updated_at": to_local_str(self.updated_at),
        }


class DesensitizeRule(db.Model):
    """脱敏字段规则：每行一条字段级脱敏配置"""
    __tablename__ = "desensitize_rules"

    id = db.Column(db.Integer, primary_key=True)
    field_key = db.Column(db.String(64), unique=True, nullable=False, index=True,
                          comment="字段标识(对应 to_dict 输出的 key)")
    field_label = db.Column(db.String(64), nullable=False, comment="字段中文名")
    algorithm = db.Column(db.String(32), nullable=False, default="mask_middle",
                          comment="算法: mask_all/mask_middle/mask_head/mask_tail/mask_email/hash/redact")
    keep_head = db.Column(db.Integer, default=0, comment="保留前N位")
    keep_tail = db.Column(db.Integer, default=0, comment="保留后N位")
    mask_char = db.Column(db.String(4), default="*", comment="替换字符")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用此规则")
    sort_order = db.Column(db.Integer, default=0, comment="排序")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "field_key": self.field_key,
            "field_label": self.field_label,
            "algorithm": self.algorithm,
            "keep_head": self.keep_head,
            "keep_tail": self.keep_tail,
            "mask_char": self.mask_char or "*",
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "updated_at": to_local_str(self.updated_at),
        }


def init_default_desensitize():
    """初始化默认脱敏配置

    - 总开关行不存在则创建（id=1）
    - 默认规则采用「仅补插缺失」策略：按 field_key 判断，已存在的规则一律不动
      （既保证老库升级后能拿到新增的默认规则，又不会覆盖管理员的改动）
    - 补插时 sort_order 一律追加到当前最大值之后：老库中默认位置常已被占用，
      沿用默认值会与前序规则并列，导致 ORDER BY sort_order 结果不确定
    - 管理员通过界面删除过的默认规则，会在下次启动时被重新补插——这是有意为之：
      DEFAULT_RULES 代表"系统默认规则"集合；只想停用某条规则请置 is_active=False

    :return: 本次新增的规则条数
    """
    if DesensitizeSetting.query.count() == 0:
        db.session.add(DesensitizeSetting(id=1, enabled=True))
    existing_keys = {key for (key,) in db.session.query(DesensitizeRule.field_key).all()}
    # 空库时 max_order=0 → 各规则落回默认 sort_order（1..N，与 DEFAULT_RULES 顺序一致）
    max_order = db.session.query(
        db.func.max(DesensitizeRule.sort_order)).scalar() or 0
    added = 0
    for item in DEFAULT_RULES:
        if item["field_key"] in existing_keys:
            continue
        rule = dict(item)
        max_order += 1
        rule["sort_order"] = max_order
        db.session.add(DesensitizeRule(**rule))
        added += 1
    db.session.commit()
    return added
