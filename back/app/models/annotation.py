# -*- coding: utf-8 -*-
"""标注模型：三级标注流程（预标注、人工标注、医生复核）"""
import enum
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


class AnnotationStatus(enum.Enum):
    """标注状态"""
    PENDING = "pending"            # 待预标注
    PRE_ANNOTATED = "pre_annotated" # 已预标注
    ASSIGNING = "assigning"         # 分配中
    ANNOTATING = "annotating"       # 人工标注中
    ANNOTATED = "annotated"         # 已完成人工标注
    REVIEWING = "reviewing"         # 医生复核中
    REJECTED = "rejected"           # 被驳回重标
    APPROVED = "approved"           # 已复核通过


class AnnotationTask(db.Model):
    """标注任务"""
    __tablename__ = "annotation_tasks"

    id = db.Column(db.Integer, primary_key=True)
    data_asset_id = db.Column(db.Integer, db.ForeignKey("data_assets.id"), nullable=False, index=True)
    # 批量任务分组（同一批创建的任务共享 group_id，便于工作台翻页标注）
    group_id = db.Column(db.Integer, index=True, comment="任务分组ID")
    # 任务备注
    remark = db.Column(db.String(256), comment="任务备注")
    # 预标注
    pre_annotation = db.Column(db.JSON, comment="预标注结果(含置信度)")
    pre_confidence = db.Column(db.Float, comment="预标注置信度")
    # 分配
    annotator_id = db.Column(db.Integer, db.ForeignKey("users.id"), comment="标注工程师")
    annotator2_id = db.Column(db.Integer, db.ForeignKey("users.id"), comment="交叉质控第二名标注员")
    reviewer_id = db.Column(db.Integer, db.ForeignKey("users.id"), comment="复核医生")
    # 一致性
    kappa = db.Column(db.Float, comment="标注一致性Kappa系数")
    # 复核
    review_result = db.Column(db.String(16), comment="复核结果: approved/rejected")
    review_comment = db.Column(db.Text, comment="复核意见")
    review_signature = db.Column(db.String(256), comment="医生电子签字")
    reviewed_at = db.Column(db.DateTime, comment="复核时间")
    # 状态
    status = db.Column(db.Enum(AnnotationStatus), default=AnnotationStatus.PENDING, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    data_asset = db.relationship("DataAsset", backref="tasks")
    annotations = db.relationship("Annotation", backref="task", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "data_asset_id": self.data_asset_id,
            "group_id": self.group_id,
            "remark": self.remark,
            "pre_annotation": self.pre_annotation,
            "pre_confidence": self.pre_confidence,
            "annotator_id": self.annotator_id,
            "reviewer_id": self.reviewer_id,
            "review_result": self.review_result,
            "review_comment": self.review_comment,
            "reviewed_at": to_local_str(self.reviewed_at),
            "status": self.status.value if self.status else None,
            "created_at": to_local_str(self.created_at),
        }


class Annotation(db.Model):
    """标注结果：标签+时间区间"""
    __tablename__ = "annotations"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("annotation_tasks.id"), nullable=False, index=True)
    annotator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # 标签内容
    label = db.Column(db.String(128), nullable=False, comment="标签(取自预设标签体系)")
    label_type = db.Column(db.String(32), comment="标签类型")
    # 时间区间(用于时序模态)
    start_time_ms = db.Column(db.Integer, comment="起始时间(ms)")
    end_time_ms = db.Column(db.Integer, comment="结束时间(ms)")
    # 附加信息
    confidence = db.Column(db.Float, comment="置信度")
    note = db.Column(db.Text, comment="备注")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AnnotationVersion(db.Model):
    """标注版本：全版本留存、可回溯"""
    __tablename__ = "annotation_versions"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("annotation_tasks.id"), nullable=False, index=True)
    version_no = db.Column(db.Integer, nullable=False)
    content = db.Column(db.JSON, comment="标注内容快照")
    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    operation = db.Column(db.String(32), comment="操作类型: pre_annotate/annotate/review")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
