# -*- coding: utf-8 -*-
"""标签管理服务

封装原 api/annotation.py 的标签库 CRUD 与使用统计逻辑。

业务规则：
- 标签由 (value, data_type) 联合唯一
- 创建/修改/删除仅限管理员（路由层 role_required 拦截）
- value 变更时级联更新所有标注记录
- 已被标注使用的标签禁止删除（需先替换）
- 标签替换：将所有使用某标签的标注替换为目标标签
"""
from sqlalchemy import func

from app.extensions import db
from app.models import Label, Annotation, AnnotationTask, DataAsset
from app.services.base import (
    BaseService, NotFoundError, ValidationError, ConflictError,
)
from app.utils.audit import log_operation
from app.utils.like_query import build_like_contains
from app.utils.response import paginate
from app.utils.time import to_local_str


class LabelService(BaseService):
    """标签管理服务"""

    def list_labels(self, data_type: str = "", keyword: str = "",
                    is_active: str = "") -> list:
        """标签库列表（支持按模态筛选、关键词搜索、启用状态筛选）"""
        q = Label.query
        if data_type:
            q = q.filter_by(data_type=data_type)
        if keyword:
            like = build_like_contains(keyword)
            q = q.filter(db.or_(
                Label.name.like(like, escape="|"),
                Label.value.like(like, escape="|"),
            ))
        if is_active:
            q = q.filter_by(is_active=(is_active.lower() == "true"))
        rows = q.order_by(Label.data_type, Label.id).all()
        return [r.to_dict() for r in rows]

    def create_label(self, data: dict) -> Label:
        """新增标签"""
        name = (data.get("name") or "").strip()
        value = (data.get("value") or "").strip()
        data_type = (data.get("data_type") or "").strip()
        if not name or not value or not data_type:
            raise ValidationError("标签名称、标识、模态均不能为空")
        if Label.query.filter_by(value=value, data_type=data_type).first():
            raise ConflictError("该模态下标签标识已存在")
        label = Label(
            name=name, value=value, data_type=data_type,
            color=data.get("color", ""),
            description=data.get("description", ""),
            is_active=data.get("is_active", True),
        )
        db.session.add(label)
        self.session.flush()  # 让 label.id 可用
        log_operation(
            "create", "label", label.id,
            f"新增标签 {name}({value}) 模态:{data_type}",
            operator=self._operator_user(),
        )
        # 业务数据 + 日志一次性原子提交
        self._commit()
        return label

    def update_label(self, label_id: int, data: dict) -> Label:
        """修改标签（value 变更时同步更新所有标注记录）"""
        label = self._get_or_404(Label, label_id, "标签不存在")

        if "name" in data:
            label.name = (data["name"] or "").strip()
        if "value" in data:
            new_value = (data["value"] or "").strip()
            dup = Label.query.filter(
                Label.value == new_value,
                Label.data_type == label.data_type,
                Label.id != label_id,
            ).first()
            if dup:
                raise ConflictError("该模态下标签标识已存在")
            # value 变更：级联更新所有标注记录
            if new_value and new_value != label.value:
                Annotation.query.filter_by(label=label.value).update(
                    {Annotation.label: new_value}
                )
            label.value = new_value
        if "color" in data:
            label.color = data["color"]
        if "description" in data:
            label.description = data["description"]
        if "is_active" in data:
            label.is_active = data["is_active"]

        log_operation(
            "update", "label", label.id,
            f"修改标签 {label.name}({label.value})",
            operator=self._operator_user(),
        )
        # 业务数据 + 日志一次性原子提交
        self._commit()
        return label

    def delete_label(self, label_id: int) -> tuple:
        """删除标签（已被标注使用时禁止删除，需先替换）

        Returns:
            (name, value): 被删除的标签名与标识
        """
        label = self._get_or_404(Label, label_id, "标签不存在")
        usage_count = Annotation.query.filter_by(label=label.value).count()
        if usage_count > 0:
            raise ValidationError(
                f"该标签已被 {usage_count} 条标注使用，请先替换后再删除"
            )
        name, value = label.name, label.value
        db.session.delete(label)
        log_operation(
            "delete", "label", label_id,
            f"删除标签 {name}({value})",
            operator=self._operator_user(),
        )
        self._commit()
        return name, value

    def label_usage(self) -> list:
        """标签使用统计：每个标签被多少标注使用"""
        rows = db.session.query(
            Annotation.label, func.count(Annotation.id).label("cnt"),
        ).group_by(Annotation.label).all()
        usage_map = {r[0]: r[1] for r in rows}

        labels = Label.query.order_by(Label.data_type, Label.id).all()
        result = []
        for l in labels:
            d = l.to_dict()
            d["usage_count"] = usage_map.get(l.value, 0)
            result.append(d)
        return result

    def replace_label(self, label_id: int, data: dict) -> dict:
        """批量替换标签：将所有使用该标签的标注替换为目标标签"""
        label = self._get_or_404(Label, label_id, "标签不存在")
        target_value = (data.get("target_value") or "").strip()
        if not target_value:
            raise ValidationError("请指定目标标签标识")
        target = Label.query.filter_by(
            value=target_value, data_type=label.data_type
        ).first()
        if not target:
            raise ValidationError("目标标签不存在")
        if target.id == label.id:
            raise ValidationError("目标标签与原标签相同")

        count = Annotation.query.filter_by(label=label.value).update(
            {Annotation.label: target_value}
        )
        log_operation(
            "update", "label", label.id,
            f"替换标签 {label.name}({label.value}) -> "
            f"{target.name}({target_value})，影响 {count} 条标注",
            operator=self._operator_user(),
        )
        # 级联更新 + 日志一次性原子提交
        self._commit()
        return {"affected": count, "from": label.value, "to": target_value}

    def sample_labels(self, subject_id: int = None, data_type: str = "",
                      label_value: str = "", page: int = 1,
                      page_size: int = 20) -> dict:
        """样本标签情况：按数据资产/受试者聚合查看标注标签分布"""
        from app.models.subject import Subject

        q = (db.session.query(Annotation, AnnotationTask, DataAsset)
             .join(AnnotationTask, Annotation.task_id == AnnotationTask.id)
             .join(DataAsset, AnnotationTask.data_asset_id == DataAsset.id))
        if subject_id:
            q = q.filter(DataAsset.subject_id == subject_id)
        if data_type:
            q = q.filter(DataAsset.data_type == data_type)
        if label_value:
            q = q.filter(Annotation.label == label_value)
        q = q.order_by(Annotation.created_at.desc())

        def _map_rows(rows):
            subject_ids = list({r[2].subject_id for r in rows if r[2].subject_id})
            subjects = ({s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}
                        if subject_ids else {})
            items = []
            for ann, task, asset in rows:
                subj = subjects.get(asset.subject_id)
                items.append({
                    "annotation_id": ann.id,
                    "label": ann.label,
                    "label_type": ann.label_type,
                    "start_time_ms": ann.start_time_ms,
                    "end_time_ms": ann.end_time_ms,
                    "confidence": ann.confidence,
                    "note": ann.note,
                    "created_at": to_local_str(ann.created_at),
                    "task_id": task.id,
                    "task_status": task.status.value if task.status else None,
                    "asset_id": asset.id,
                    "asset_name": asset.file_name,
                    "data_type": asset.data_type.value if asset.data_type else None,
                    "subject_id": asset.subject_id,
                    "subject_pseudo_id": subj.pseudo_id if subj else None,
                })
            return items

        return paginate(q, page, page_size, item_mapper=_map_rows)

    def preset_labels(self, data_type: str = "") -> dict:
        """获取标签体系（优先从数据库读取，数据库为空时回退到硬编码）"""
        labels = Label.query
        if data_type:
            labels = labels.filter_by(data_type=data_type, is_active=True)
        else:
            labels = labels.filter_by(is_active=True)
        rows = labels.order_by(Label.data_type, Label.id).all()
        if rows:
            grouped = {}
            for l in rows:
                grouped.setdefault(l.data_type, []).append(
                    {"value": l.value, "label": l.name}
                )
            if data_type:
                return {data_type: grouped.get(data_type, [])}
            return grouped
        # 数据库为空时回退到硬编码
        if data_type and data_type in PRESET_LABELS:
            return {data_type: PRESET_LABELS[data_type]}
        return PRESET_LABELS


# 预设标签体系（按模态分组，确保标注规范统一）
# 放在模块末尾，避免循环导入；LabelService.preset_labels 引用此常量
PRESET_LABELS = {
    "video": [
        {"value": "face_keypoint", "label": "人脸关键点"},
        {"value": "pose", "label": "姿态"},
        {"value": "action_segment", "label": "动作片段"},
        {"value": "abnormal_behavior", "label": "异常行为"},
    ],
    "audio": [
        {"value": "speech_segment", "label": "语音片段"},
        {"value": "pause", "label": "停顿"},
        {"value": "speech_rate_abnormal", "label": "语速异常"},
        {"value": "semantic_abnormal", "label": "语义异常"},
    ],
    "eeg": [
        {"value": "alpha_band", "label": "α频段"},
        {"value": "beta_band", "label": "β频段"},
        {"value": "theta_band", "label": "θ频段"},
        {"value": "abnormal_wave", "label": "异常波形"},
    ],
    "ecg": [
        {"value": "r_peak", "label": "R峰"},
        {"value": "arrhythmia", "label": "心律失常"},
        {"value": "artifact", "label": "伪迹"},
    ],
    "eye": [
        {"value": "fixation", "label": "注视"},
        {"value": "saccade", "label": "扫视"},
        {"value": "blink", "label": "眨眼"},
    ],
    "gait": [
        {"value": "step", "label": "步态周期"},
        {"value": "freeze", "label": "冻结步态"},
        {"value": "abnormal_gait", "label": "异常步态"},
    ],
    "scale": [
        {"value": "risk_high", "label": "高风险"},
        {"value": "risk_medium", "label": "中风险"},
        {"value": "risk_low", "label": "低风险"},
        {"value": "abnormal_item", "label": "异常分项"},
    ],
    "task": [
        {"value": "response_correct", "label": "反应正确"},
        {"value": "response_error", "label": "反应错误"},
        {"value": "response_timeout", "label": "反应超时"},
        {"value": "abnormal_performance", "label": "表现异常"},
    ],
}