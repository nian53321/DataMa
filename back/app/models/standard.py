# -*- coding: utf-8 -*-
"""规范与标准模型（数据字典、元数据模板、命名规范）"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


class DataStandard(db.Model):
    """数据规范：包含命名规范、数据字典、元数据模板(JSONSchema)"""
    __tablename__ = "data_standards"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, comment="规范名称")
    standard_type = db.Column(db.String(32), nullable=False, comment="类型: naming/dictionary/metadata")
    data_type = db.Column(db.String(32), index=True, comment="适用模态类型")
    version = db.Column(db.String(16), nullable=False, default="1.0.0", comment="版本号")
    # JSONSchema 定义或命名规则
    schema_json = db.Column(db.JSON, comment="JSONSchema/规则定义")
    description = db.Column(db.Text, comment="说明")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "standard_type": self.standard_type,
            "data_type": self.data_type,
            "version": self.version,
            "schema": self.schema_json,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": to_local_str(self.created_at),
            "updated_at": to_local_str(self.updated_at),
        }


class StandardVersion(db.Model):
    """规范版本管理"""
    __tablename__ = "standard_versions"

    id = db.Column(db.Integer, primary_key=True)
    standard_id = db.Column(db.Integer, db.ForeignKey("data_standards.id"), nullable=False, index=True)
    version = db.Column(db.String(16), nullable=False)
    schema_json = db.Column(db.JSON)
    change_log = db.Column(db.Text, comment="变更说明")
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
