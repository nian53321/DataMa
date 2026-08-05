# -*- coding: utf-8 -*-
"""数据资产模型（轻量级数据湖分层）"""
import enum
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


class DataType(enum.Enum):
    """多模态数据类型"""
    VIDEO = "video"          # 视频
    AUDIO = "audio"          # 音频
    EEG = "eeg"             # 脑电
    ECG = "ecg"             # 心电
    EYE_TRACKING = "eye"    # 眼动
    GAIT = "gait"           # 步态
    SCALE = "scale"         # 量表（结构化）
    TASK = "task"           # 认知任务数据


class DataLayer(enum.Enum):
    """数据湖分层"""
    RAW = "raw"             # 原始数据
    CLEANED = "cleaned"     # 清洗数据
    FEATURE = "feature"     # 特征数据
    ANNOTATION = "annotation"  # 标注数据


class DataAsset(db.Model):
    """数据资产：每条多模态数据记录"""
    __tablename__ = "data_assets"

    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False, index=True)
    data_type = db.Column(db.Enum(DataType), nullable=False, index=True, comment="模态类型")
    layer = db.Column(db.Enum(DataLayer), default=DataLayer.RAW, comment="数据湖分层")
    # 规范化文件名：模态类型_受试者伪ID_采集时间戳_场景代码_批次号.后缀
    file_name = db.Column(db.String(256), nullable=False, comment="规范化文件名")
    file_path = db.Column(db.String(512), nullable=False, comment="存储路径")
    file_format = db.Column(db.String(16), comment="文件格式: mp4/wav/edf/csv")
    file_size = db.Column(db.BigInteger, default=0, comment="文件大小(字节)")
    # 元数据(JSONSchema 校验后存储)
    metadata_json = db.Column(db.JSON, comment="元数据")
    # 质量评估
    quality_score = db.Column(db.Float, comment="质量评分")
    quality_report_path = db.Column(db.String(512), comment="质量报告路径")
    is_qualified = db.Column(db.Boolean, default=True, comment="是否合格")
    # 标准化状态
    is_standardized = db.Column(db.Boolean, default=False, comment="是否已标准化")
    sample_rate = db.Column(db.Integer, comment="采样率")
    # 对齐信息
    timestamp_utc = db.Column(db.DateTime, comment="UTC采集时间戳")
    align_offset_ms = db.Column(db.Integer, default=0, comment="对齐时钟偏移(ms)")
    # 状态
    status = db.Column(db.String(32), default="uploaded", comment="uploaded/cleaning/standardized/annotating/done")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    versions = db.relationship("DataVersion", backref="data_asset", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "data_type": self.data_type.value if self.data_type else None,
            "layer": self.layer.value if self.layer else None,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "file_url": f"/api/data/assets/{self.id}/file",
            "file_format": self.file_format,
            "file_size": self.file_size,
            "metadata": self.metadata_json,
            "quality_score": self.quality_score,
            "is_qualified": self.is_qualified,
            "is_standardized": self.is_standardized,
            "sample_rate": self.sample_rate,
            "timestamp_utc": to_local_str(self.timestamp_utc),
            "align_offset_ms": self.align_offset_ms,
            "status": self.status,
            "created_at": to_local_str(self.created_at),
        }


class DataVersion(db.Model):
    """数据版本：全数据全版本管理"""
    __tablename__ = "data_versions"

    id = db.Column(db.Integer, primary_key=True)
    data_asset_id = db.Column(db.Integer, db.ForeignKey("data_assets.id"), nullable=False, index=True)
    version_no = db.Column(db.Integer, nullable=False, comment="版本号")
    file_path = db.Column(db.String(512), nullable=False, comment="该版本存储路径")
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), comment="操作人")
    operation = db.Column(db.String(32), comment="操作类型: upload/clean/standardize/annotate")
    change_log = db.Column(db.Text, comment="变更说明")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
