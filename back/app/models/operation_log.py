# -*- coding: utf-8 -*-
"""操作日志模型：审计所有数据操作"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


class OperationLog(db.Model):
    """操作日志：记录用户对受试者/数据资产/标注任务等的增删改操作"""
    __tablename__ = "operation_logs"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, comment="操作人用户名")
    role = db.Column(db.String(32), nullable=True, comment="操作人身份（admin/doctor/annotator...）")
    action = db.Column(db.String(32), index=True, comment="操作类型: create/update/delete/login/upload")
    target_type = db.Column(db.String(32), index=True, comment="操作对象类型: subject/asset/annotation_task...")
    target_id = db.Column(db.String(128), nullable=True, comment="操作对象ID（支持单个或多个ID逗号拼接）")
    detail = db.Column(db.Text, comment="操作详情")
    ip = db.Column(db.String(64), nullable=True, comment="操作IP")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "detail": self.detail,
            "ip": self.ip,
            "created_at": to_local_str(self.created_at),
        }
