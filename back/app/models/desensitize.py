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
        "field_key": "remark", "field_label": "备注",
        "algorithm": "mask_tail", "keep_head": 0, "keep_tail": 0,
        "mask_char": "*", "is_active": False, "sort_order": 7,
    },
    {
        "field_key": "pseudo_id", "field_label": "伪ID",
        "algorithm": "mask_middle", "keep_head": 2, "keep_tail": 2,
        "mask_char": "*", "is_active": False, "sort_order": 8,
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
    """首次启动时写入默认脱敏配置（已存在则跳过）"""
    if DesensitizeSetting.query.count() == 0:
        db.session.add(DesensitizeSetting(id=1, enabled=True))
    if DesensitizeRule.query.count() == 0:
        for item in DEFAULT_RULES:
            db.session.add(DesensitizeRule(**item))
    db.session.commit()
