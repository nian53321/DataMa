# -*- coding: utf-8 -*-
"""受试者自动扫描配置模型"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


class ScanConfig(db.Model):
    """文件夹自动扫描配置：定期扫描指定文件夹，自动创建新受试者"""
    __tablename__ = "scan_configs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, comment="配置名称")
    watch_dir = db.Column(db.String(512), nullable=False, comment="监控文件夹路径")
    interval_minutes = db.Column(db.Integer, default=60, comment="扫描间隔（秒，字段名保留历史兼容）")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    # 子文件夹命名规则：pseudo_id 从子文件夹名提取的正则或前缀
    id_pattern = db.Column(db.String(128), default="SUBJ_{seq:04d}", comment="伪ID生成模板")
    auto_upload_files = db.Column(db.Boolean, default=True, comment="是否自动将子文件夹内文件上传为数据资产")
    # 默认采集批次/场景：扫描创建受试者时，若 userInfo.json 未提供则使用此默认值
    collection_batch = db.Column(db.String(64), nullable=True, comment="默认采集批次（userInfo 未提供时使用）")
    collection_scene = db.Column(db.String(64), nullable=True, comment="默认采集场景（userInfo 未提供时使用）")
    last_scan_at = db.Column(db.DateTime, comment="上次扫描时间")
    last_scan_count = db.Column(db.Integer, default=0, comment="上次扫描新增受试者数")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "watch_dir": self.watch_dir,
            "interval_minutes": self.interval_minutes,
            "is_active": self.is_active,
            "id_pattern": self.id_pattern,
            "auto_upload_files": self.auto_upload_files,
            "collection_batch": self.collection_batch or "",
            "collection_scene": self.collection_scene or "",
            "last_scan_at": to_local_str(self.last_scan_at),
            "last_scan_count": self.last_scan_count,
            "created_at": to_local_str(self.created_at),
        }
