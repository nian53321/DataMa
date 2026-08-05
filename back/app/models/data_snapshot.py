# -*- coding: utf-8 -*-
"""通用数据快照模型：统一留档所有数据修改，支持回滚防止错误修改导致数据丢失"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


# 支持快照留档的模型注册表：model_type -> (Model类, 可回滚字段列表)
# 字段列表用于回滚时按字段赋值（排除 id/created_at 等系统字段）
MODEL_REGISTRY = {
    "subject": None,         # 延迟引用，运行时填充
    "data_asset": None,
    "user": None,
    "data_standard": None,
    "annotation_task": None,
}


def _fill_registry():
    """延迟填充模型注册表，避免循环导入"""
    if MODEL_REGISTRY["subject"] is None:
        from app.models.subject import Subject
        from app.models.data import DataAsset
        from app.models.user import User
        from app.models.standard import DataStandard
        from app.models.annotation import AnnotationTask
        MODEL_REGISTRY.update({
            "subject": (Subject, [
                "pseudo_id", "age", "gender", "education_level",
                "cognitive_risk_level", "emotion_status", "mmse_score",
                "ad8_score", "moca_score", "collection_batch",
                "collection_scene", "collection_time", "status", "remark",
            ], {
                "id": "ID", "pseudo_id": "伪ID", "age": "年龄", "gender": "性别",
                "education_level": "教育程度", "cognitive_risk_level": "认知风险分级",
                "emotion_status": "情绪状态", "mmse_score": "MMSE评分",
                "ad8_score": "AD8评分", "moca_score": "MoCA评分",
                "collection_batch": "采集批次", "collection_scene": "采集场景",
                "collection_time": "采集时间", "status": "状态",
                "remark": "备注", "created_at": "创建时间",
            }),
            "data_asset": (DataAsset, [
                "file_name", "file_path", "file_format", "file_size",
                "data_type", "layer", "metadata_json", "quality_score",
                "is_qualified", "is_standardized", "sample_rate",
                "timestamp_utc", "align_offset_ms", "status",
            ], {
                "id": "ID", "file_name": "文件名", "file_path": "文件路径",
                "file_format": "文件格式", "file_size": "文件大小",
                "data_type": "数据类型", "layer": "数据分层",
                "metadata_json": "元数据", "quality_score": "质量评分",
                "is_qualified": "是否合格", "is_standardized": "是否标准化",
                "sample_rate": "采样率", "timestamp_utc": "时间戳",
                "align_offset_ms": "对齐偏移", "status": "状态",
                "subject_id": "受试者ID", "created_at": "创建时间",
            }),
            "user": (User, [
                "real_name", "role", "email", "phone", "license_no",
                "is_active",
            ], {
                "id": "ID", "username": "用户名", "real_name": "姓名",
                "role": "角色", "email": "邮箱", "phone": "电话",
                "license_no": "执业证号", "is_active": "启用状态",
                "created_at": "创建时间",
            }),
            "data_standard": (DataStandard, [
                "name", "standard_type", "data_type", "version",
                "schema_json", "description", "is_active",
            ], {
                "id": "ID", "name": "规范名称", "standard_type": "规范类型",
                "data_type": "适用模态", "version": "版本",
                "schema_json": "Schema", "description": "描述",
                "is_active": "启用状态", "created_at": "创建时间",
            }),
            "annotation_task": (AnnotationTask, [
                "annotator_id", "reviewer_id", "status", "pre_annotation",
                "pre_confidence", "review_result", "review_comment",
                "reviewed_at", "remark",
            ], {
                "id": "ID", "data_asset_id": "数据资产ID",
                "annotator_id": "标注员ID", "reviewer_id": "复核医生ID",
                "status": "状态", "pre_annotation": "预标注结果",
                "pre_confidence": "预标注置信度", "review_result": "复核结果",
                "review_comment": "复核意见", "reviewed_at": "复核时间",
                "remark": "备注", "created_at": "创建时间",
                "updated_at": "更新时间",
            }),
        })


class DataSnapshot(db.Model):
    """数据快照：记录每次 create/update/delete 前的完整数据状态"""
    __tablename__ = "data_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    model_type = db.Column(db.String(32), nullable=False, index=True, comment="模型类型")
    model_id = db.Column(db.Integer, nullable=False, index=True, comment="记录ID")
    version_no = db.Column(db.Integer, nullable=False, comment="版本号(同记录递增)")
    snapshot = db.Column(db.JSON, nullable=False, comment="修改前的完整数据快照")
    action = db.Column(db.String(16), nullable=False, comment="操作: create/update/delete/rollback")
    operator_id = db.Column(db.Integer, nullable=True, comment="操作人ID")
    operator_name = db.Column(db.String(64), nullable=True, comment="操作人用户名")
    role = db.Column(db.String(32), nullable=True, comment="操作人角色")
    change_summary = db.Column(db.String(512), nullable=True, comment="变更摘要")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        _fill_registry()
        entry = MODEL_REGISTRY.get(self.model_type)
        field_labels = entry[2] if entry and len(entry) > 2 else {}
        return {
            "id": self.id,
            "model_type": self.model_type,
            "model_id": self.model_id,
            "version_no": self.version_no,
            "snapshot": self.snapshot,
            "action": self.action,
            "operator_id": self.operator_id,
            "operator_name": self.operator_name,
            "role": self.role,
            "change_summary": self.change_summary,
            "field_labels": field_labels,
            "created_at": to_local_str(self.created_at),
        }


def save_snapshot(model_type, model_id, snapshot_dict, action, operator=None,
                  change_summary=None):
    """保存一条数据快照。

    Args:
        model_type: 模型类型（subject/data_asset/user/data_standard/annotation_task）
        model_id: 记录ID
        snapshot_dict: 修改前的完整数据（dict，通常为 model.to_dict()）
        action: create/update/delete
        operator: 操作人 User 对象（可选）
        change_summary: 变更摘要文本
    Returns:
        DataSnapshot 实例
    """
    # 计算版本号：同 model_type+model_id 的最大 version_no + 1
    last = (DataSnapshot.query
            .filter_by(model_type=model_type, model_id=model_id)
            .order_by(DataSnapshot.version_no.desc())
            .first())
    version_no = (last.version_no + 1) if last else 1

    snap = DataSnapshot(
        model_type=model_type,
        model_id=model_id,
        version_no=version_no,
        snapshot=snapshot_dict,
        action=action,
        operator_id=operator.id if operator else None,
        operator_name=operator.username if operator else None,
        role=operator.role if operator else None,
        change_summary=change_summary,
    )
    db.session.add(snap)
    db.session.flush()  # 拿到 id，但不提交（由调用方控制事务）
    return snap


def diff_summary(old_dict, new_dict, fields=None, field_labels=None):
    """生成两个 dict 的字段变更摘要（field_labels 用于将英文字段名转为中文）"""
    if not old_dict or not new_dict:
        return None
    labels = field_labels or {}
    keys = fields or list(old_dict.keys())
    changes = []
    for k in keys:
        if k in ("id", "created_at", "updated_at", "file_url"):
            continue
        old_v = old_dict.get(k)
        new_v = new_dict.get(k)
        if old_v != new_v:
            changes.append(labels.get(k, k))
    if not changes:
        return None
    return f"变更字段: {', '.join(changes[:8])}" + (f" 等{len(changes)}项" if len(changes) > 8 else "")


def diff_summary_for(model_type, old_dict, new_dict):
    """生成指定模型类型的字段变更摘要（自动使用中文字段名）"""
    _fill_registry()
    entry = MODEL_REGISTRY.get(model_type)
    field_labels = entry[2] if entry and len(entry) > 2 else {}
    return diff_summary(old_dict, new_dict, field_labels=field_labels)


def rollback_snapshot(snapshot_id, operator):
    """回滚到指定快照版本。

    策略:
      - update 类快照: 用快照数据覆盖当前记录字段
      - delete 类快照: 用快照数据重新插入记录(新 id)，并更新 snap.model_id 指向新 id
    回滚前会先保存当前状态作为新快照，便于再次回滚。

    事务边界：本函数仅 flush 不 commit，由调用方控制事务提交，
    保证"回滚数据 + 留档快照 + 操作日志"同生共死。
    """
    _fill_registry()
    snap = DataSnapshot.query.get(snapshot_id)
    if not snap:
        return None, "快照不存在"

    model_type = snap.model_type
    if model_type not in MODEL_REGISTRY or MODEL_REGISTRY[model_type] is None:
        return None, f"不支持回滚的模型类型: {model_type}"

    ModelClass, fields = MODEL_REGISTRY[model_type]
    snapshot_data = snap.snapshot or {}
    current = ModelClass.query.get(snap.model_id)

    # 回滚前先留档当前状态（便于再次回滚）
    if current:
        current_dict = current.to_dict()
        save_snapshot(
            model_type, snap.model_id, current_dict,
            action="rollback",
            operator=operator,
            change_summary=f"回滚前自动留档(目标版本 v{snap.version_no})",
        )

    if current:
        # update 回滚：按字段覆盖
        for f in fields:
            if f in snapshot_data:
                val = snapshot_data[f]
                # 处理枚举字段
                col = getattr(ModelClass, f, None)
                if col is not None and hasattr(col, "property"):
                    # 去掉类型转换的复杂判断，直接尝试赋值
                    pass
                setattr(current, f, _coerce_field(ModelClass, f, val))
        db.session.flush()
        result_id = snap.model_id
    else:
        # delete 回滚：重新插入（新 id）
        new_obj = ModelClass()
        for f in fields:
            if f in snapshot_data:
                setattr(new_obj, f, _coerce_field(ModelClass, f, snapshot_data[f]))
        db.session.add(new_obj)
        db.session.flush()
        result_id = new_obj.id
        # 关键：更新 snap.model_id 指向新 id，避免再次回滚时找不到记录导致重复插入
        snap.model_id = result_id
        db.session.flush()

    # 仅 flush，不 commit（由调用方 SnapshotService.rollback_snapshot 控制事务）
    return {
        "model_type": model_type,
        "model_id": result_id,
        "restored_from_version": snap.version_no,
    }, None


def _coerce_field(model_class, field_name, value):
    """将快照中的值转换为模型字段可接受的类型（处理枚举）"""
    if value is None:
        return None
    col = getattr(model_class, field_name, None)
    if col is None:
        return value
    # SQLAlchemy Enum 列：尝试用枚举类还原
    try:
        prop = col.property
        if hasattr(prop, "columns") and prop.columns:
            col_obj = prop.columns[0]
            enum_cls = getattr(col_obj, "enum_class", None)
            if enum_cls is not None and isinstance(value, str):
                return enum_cls(value)
    except Exception:
        pass
    return value
