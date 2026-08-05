# -*- coding: utf-8 -*-
"""标注任务管理服务

封装原 api/annotation.py 的标注任务 CRUD、分配、预标注、人工标注、医生复核、
质量看板、可用资产查询、分组导航等逻辑。

业务规则：
- 任务状态机：pending → pre_annotated → annotating → annotated → reviewing → approved/rejected
- 仅允许 pending/pre_annotated/rejected/annotating 状态的任务被分配
- 提交标注仅限任务分配人（管理员除外）
- 复核仅限医生/管理员，可在复核时修改标注
- 标注完成后数据资产归入 annotation 分层
- 状态流转必须通过专用接口（annotate/review），不能在 update_task 中改 status
- 创建/更新/删除自动留档快照 + 操作日志
- 批量创建任务共享 group_id（用首任务 ID 作为分组标识）
"""
from datetime import datetime
from typing import List, Optional

from app.extensions import db
from app.models import (
    AnnotationTask, Annotation, AnnotationStatus, AnnotationVersion,
    DataAsset, DataLayer, DataType, Role, Label,
)
from app.models.user import User
from app.services.base import (
    BaseService, NotFoundError, ValidationError, PermissionDeniedError,
)
from app.services.label_service import PRESET_LABELS
from app.utils.audit import log_operation, snapshot_update, snapshot_delete
from app.utils.like_query import build_like_contains
from app.utils.response import paginate


# 允许重新分配的任务状态集合
_REASSIGNABLE_STATUSES = {
    AnnotationStatus.PENDING, AnnotationStatus.PRE_ANNOTATED,
    AnnotationStatus.REJECTED, AnnotationStatus.ANNOTATING,
}


class AnnotationService(BaseService):
    """标注任务管理服务"""

    # ==================== 查询类 ====================

    def list_tasks(self, page: int = 1, page_size: int = 20,
                   status: str = "", annotator_id: int = None,
                   reviewer_id: int = None, data_asset_id: int = None,
                   data_type: str = "", group_id: int = None,
                   mine: int = 0, ids_only: bool = False) -> dict:
        """标注任务列表（关联用户名展示，支持多条件过滤）

        - mine=1 仅返回当前用户的任务（标注员或医生视角的"我的任务"）
        - ids_only=True 时仅返回当前筛选条件下的全部 ID 列表（不分页，轻量），
          用于前端跨页全选。返回格式：{"items": [{"id": 1}, ...], "total": N}
        """
        query = AnnotationTask.query
        if status:
            query = query.filter(AnnotationTask.status == AnnotationStatus(status))
        if annotator_id:
            query = query.filter_by(annotator_id=annotator_id)
        if reviewer_id:
            query = query.filter_by(reviewer_id=reviewer_id)
        if data_asset_id:
            query = query.filter_by(data_asset_id=data_asset_id)
        if group_id:
            query = query.filter_by(group_id=group_id)
        if data_type:
            # 关联 DataAsset 表按数据类型筛选
            query = query.join(
                DataAsset, AnnotationTask.data_asset_id == DataAsset.id
            ).filter(DataAsset.data_type == DataType(data_type))
        if mine:
            # 当前用户作为标注员或复核医生
            query = query.filter(db.or_(
                AnnotationTask.annotator_id == self.operator_id,
                AnnotationTask.reviewer_id == self.operator_id,
            ))
        query = query.order_by(AnnotationTask.created_at.desc())
        if ids_only:
            ids = [r[0] for r in query.with_entities(AnnotationTask.id).all()]
            return {"items": [{"id": i} for i in ids], "total": len(ids)}
        paged = paginate(query, page, page_size)
        # 一次性查询关联用户名和数据资产类型，避免 N+1
        items = paged.get("items", []) if isinstance(paged, dict) else []
        user_ids = set()
        asset_ids = set()
        for t in items:
            if t.get("annotator_id"):
                user_ids.add(t["annotator_id"])
            if t.get("reviewer_id"):
                user_ids.add(t["reviewer_id"])
            if t.get("data_asset_id"):
                asset_ids.add(t["data_asset_id"])
        user_map = {}
        if user_ids:
            for u in User.query.filter(User.id.in_(user_ids)).all():
                user_map[u.id] = {
                    "username": u.username, "real_name": u.real_name, "role": u.role,
                }
        asset_map = {}
        if asset_ids:
            for a in DataAsset.query.filter(DataAsset.id.in_(asset_ids)).all():
                asset_map[a.id] = {
                    "data_type": a.data_type.value if a.data_type else None,
                    "file_name": a.file_name,
                }
        for t in items:
            ann_info = user_map.get(t.get("annotator_id"), {})
            rev_info = user_map.get(t.get("reviewer_id"), {})
            t["annotator_name"] = ann_info.get("real_name") or ann_info.get("username")
            t["reviewer_name"] = rev_info.get("real_name") or rev_info.get("username")
            ai = asset_map.get(t.get("data_asset_id"), {})
            t["data_type"] = ai.get("data_type")
            t["asset_file_name"] = ai.get("file_name")
        return paged

    def list_annotators(self) -> list:
        """获取可分配的人员列表（admin/annotator/doctor）"""
        users = (
            User.query
            .filter(User.role.in_([
                Role.ANNOTATOR.value, Role.ADMIN.value, Role.DOCTOR.value,
            ]))
            .filter(User.is_active.is_(True))
            .order_by(User.role.asc(), User.id.asc())
            .all()
        )
        return [{
            "id": u.id, "username": u.username,
            "real_name": u.real_name, "role": u.role,
        } for u in users]

    def task_detail(self, task_id: int) -> dict:
        """任务详情：含数据资产信息、已标注内容"""
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        asset = DataAsset.query.get(task.data_asset_id)
        annotations = [
            {
                "id": a.id,
                "label": a.label,
                "label_type": a.label_type,
                "start_time_ms": a.start_time_ms,
                "end_time_ms": a.end_time_ms,
                "confidence": a.confidence,
                "note": a.note,
            }
            for a in task.annotations.order_by(Annotation.start_time_ms.asc())
        ]
        return {
            "task": task.to_dict(),
            "asset": asset.to_dict() if asset else None,
            "annotations": annotations,
            "preset_labels": PRESET_LABELS.get(asset.data_type.value, []) if asset else [],
        }

    def available_assets(self, page: int = 1, page_size: int = 200,
                         data_type: str = "", subject_id: int = None,
                         keyword: str = "") -> dict:
        """获取可用于创建标注任务的数据资产

        排除已有非驳回状态任务的资产，避免重复分配。
        """
        query = DataAsset.query
        if data_type:
            query = query.filter_by(data_type=data_type)
        if subject_id:
            query = query.filter_by(subject_id=subject_id)
        if keyword:
            like = build_like_contains(keyword)
            query = query.filter(DataAsset.file_name.like(like, escape="|"))

        # 排除已有非驳回状态任务的资产ID
        busy_ids = db.session.query(AnnotationTask.data_asset_id).filter(
            AnnotationTask.status != AnnotationStatus.REJECTED
        ).distinct()
        query = query.filter(~DataAsset.id.in_(busy_ids))
        query = query.order_by(DataAsset.created_at.desc())

        def _map_assets(assets):
            from app.models.subject import Subject
            subj_ids = list({a.subject_id for a in assets if a.subject_id})
            subj_map = ({s.id: s.pseudo_id
                         for s in Subject.query.filter(Subject.id.in_(subj_ids)).all()}
                        if subj_ids else {})
            return [{
                "id": a.id, "file_name": a.file_name,
                "data_type": a.data_type.value if a.data_type else None,
                "file_format": a.file_format,
                "subject_id": a.subject_id,
                "subject_pseudo_id": subj_map.get(a.subject_id, ""),
                "file_size": a.file_size,
            } for a in assets]

        return paginate(query, page, page_size, item_mapper=_map_assets)

    def task_group(self, group_id: int) -> dict:
        """获取任务分组中的所有任务（用于工作台翻页导航）"""
        tasks = (AnnotationTask.query
                 .filter_by(group_id=group_id)
                 .order_by(AnnotationTask.id.asc()).all())
        if not tasks:
            raise NotFoundError("任务分组不存在")
        return {
            "group_id": group_id,
            "total": len(tasks),
            "tasks": [{
                "id": t.id,
                "data_asset_id": t.data_asset_id,
                "status": t.status.value if t.status else None,
                "annotator_id": t.annotator_id,
                "remark": t.remark,
            } for t in tasks],
        }

    def my_tasks(self, status_param: str = "", data_type: str = "") -> dict:
        """获取当前用户的所有标注任务（用于工作台全量翻页标注）

        - status: 多个用逗号分隔
        - data_type: video/audio/eeg/ecg/eye/gait/scale/task
        """
        query = AnnotationTask.query.filter_by(annotator_id=self.operator_id)
        if status_param:
            statuses = [s.strip() for s in status_param.split(",") if s.strip()]
            status_filters = []
            for s in statuses:
                try:
                    status_filters.append(AnnotationStatus(s))
                except ValueError:
                    pass
            if status_filters:
                query = query.filter(AnnotationTask.status.in_(status_filters))
        if data_type:
            query = query.join(
                DataAsset, AnnotationTask.data_asset_id == DataAsset.id
            ).filter(DataAsset.data_type == DataType(data_type))
        tasks = query.order_by(AnnotationTask.id.asc()).all()
        return {
            "total": len(tasks),
            "tasks": [{
                "id": t.id,
                "data_asset_id": t.data_asset_id,
                "status": t.status.value if t.status else None,
                "group_id": t.group_id,
                "remark": t.remark,
            } for t in tasks],
        }

    def quality_dashboard(self) -> dict:
        """质量看板：完成率/合格率/复核通过率"""
        total = AnnotationTask.query.count()
        annotated = AnnotationTask.query.filter(AnnotationTask.status.in_([
            AnnotationStatus.ANNOTATED, AnnotationStatus.APPROVED,
        ])).count()
        approved = AnnotationTask.query.filter_by(
            status=AnnotationStatus.APPROVED
        ).count()
        completion = round(annotated / total, 4) if total else 0.0
        pass_rate = round(approved / total, 4) if total else 0.0
        review_pass = round(approved / annotated, 4) if annotated else 0.0
        return {
            "total": total,
            "completion_rate": completion,
            "pass_rate": pass_rate,
            "review_pass_rate": review_pass,
        }

    # ==================== 任务 CRUD ====================

    def create_task(self, data: dict) -> AnnotationTask:
        """创建标注任务（关联数据资产）"""
        data_asset_id = data.get("data_asset_id")
        if not data_asset_id:
            raise ValidationError("数据资产ID必填")
        asset = DataAsset.query.get(data_asset_id)
        if not asset:
            raise NotFoundError("数据资产不存在")
        task = AnnotationTask(
            data_asset_id=data_asset_id, status=AnnotationStatus.PENDING,
        )
        db.session.add(task)
        self._commit()
        self._log("create", task.id, f"创建标注任务（资产 #{data_asset_id}）")
        self._commit()
        return task

    def batch_create_tasks(self, data: dict) -> dict:
        """批量创建标注任务（同一批共享 group_id）

        跳过已存在非 rejected 状态任务的资产，避免重复创建。
        若指定 annotator_id，任务状态直接设为 pre_annotated。
        """
        asset_ids = data.get("data_asset_ids", [])
        remark = (data.get("remark") or "").strip()
        annotator_id = data.get("annotator_id")
        reviewer_id = data.get("reviewer_id")
        if not asset_ids or not isinstance(asset_ids, list):
            raise ValidationError("data_asset_ids 列表不能为空")

        # 校验标注员和复核医生
        if annotator_id:
            ann = User.query.get(annotator_id)
            if not ann:
                raise ValidationError("指定的标注员不存在")
        if reviewer_id:
            rev = User.query.get(reviewer_id)
            if not rev:
                raise ValidationError("指定的复核医生不存在")

        # 根据是否分配标注员决定初始状态
        init_status = (AnnotationStatus.PRE_ANNOTATED if annotator_id
                       else AnnotationStatus.PENDING)

        created = []
        skipped = []
        for aid in asset_ids:
            asset = DataAsset.query.get(aid)
            if not asset:
                skipped.append({"id": aid, "reason": "资产不存在"})
                continue
            # 跳过已有非终态（rejected）任务的资产
            existing = AnnotationTask.query.filter_by(data_asset_id=aid).first()
            if existing and existing.status != AnnotationStatus.REJECTED:
                skipped.append({"id": aid, "reason": "已有进行中的标注任务"})
                continue
            task = AnnotationTask(
                data_asset_id=aid, status=init_status, remark=remark,
                annotator_id=annotator_id, reviewer_id=reviewer_id,
            )
            db.session.add(task)
            created.append(task)
        db.session.flush()  # 获取自增ID
        # 同批任务共享 group_id（用第一个任务ID作为分组标识）
        if created:
            group_id = created[0].id
            for t in created:
                t.group_id = group_id
        self._commit()
        # 补记日志
        ann_name = ""
        if annotator_id:
            ann_user = User.query.get(annotator_id)
            ann_name = (f"，分配标注员 {ann_user.real_name or ann_user.username}"
                        if ann_user else "")
        for t in created:
            self._log("create", t.id,
                      f"批量创建标注任务（资产 #{t.data_asset_id}，"
                      f"分组 #{t.group_id}{ann_name}）")
        if annotator_id and created:
            self._log("assign", created[0].id,
                      f"批量分配标注员（{len(created)} 个任务{ann_name}）")
        self._commit()
        return {
            "count": len(created),
            "skipped": skipped,
            "group_id": created[0].group_id if created else None,
            "items": [t.to_dict() for t in created],
        }

    def update_task(self, task_id: int, data: dict) -> AnnotationTask:
        """编辑标注任务（可改标注员/医生/备注）

        安全限制：禁止在此接口修改 status，必须通过专用接口（annotate/review）。
        """
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        old_dict = task.to_dict()
        # 禁止通过此接口修改状态
        if data.get("status"):
            raise ValidationError("状态变更请通过标注/复核接口操作")
        for f in ["annotator_id", "reviewer_id", "remark"]:
            if f in data:
                setattr(task, f, data[f])
        snapshot_update("annotation_task", task, old_dict, task.to_dict(),
                        operator=self._operator_user())
        self._commit()
        self._log("update", task.id, "编辑标注任务")
        self._commit()
        return task

    def delete_task(self, task_id: int) -> None:
        """删除标注任务（级联删除其标注与版本记录）"""
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        # 删除前留档
        snapshot_delete("annotation_task", task, f"删除标注任务 #{task_id}",
                        operator=self._operator_user())
        # 先记录日志（提交前 task 仍存在），再删除子表与任务本身
        self._log("delete", task_id, "删除标注任务")
        Annotation.query.filter_by(task_id=task_id).delete(synchronize_session=False)
        AnnotationVersion.query.filter_by(task_id=task_id).delete(synchronize_session=False)
        db.session.delete(task)
        self._commit()

    def batch_delete_tasks(self, task_ids: list) -> dict:
        """批量删除标注任务（级联删除标注结果与版本记录）"""
        if not task_ids or not isinstance(task_ids, list):
            raise ValidationError("task_ids 列表不能为空")

        deleted = []
        not_found = []
        for tid in task_ids:
            task = AnnotationTask.query.get(tid)
            if not task:
                not_found.append(tid)
                continue
            Annotation.query.filter_by(task_id=tid).delete(synchronize_session=False)
            AnnotationVersion.query.filter_by(task_id=tid).delete(synchronize_session=False)
            self._log("delete", tid,
                      f"批量删除标注任务（资产 #{task.data_asset_id}）")
            db.session.delete(task)
            deleted.append(tid)
        self._commit()
        return {"deleted_count": len(deleted), "not_found": not_found}

    # ==================== 状态流转 ====================

    def pre_annotate(self, task_id: int) -> AnnotationTask:
        """触发自动预标注（框架阶段：生成模拟预标注结果）"""
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        asset = DataAsset.query.get(task.data_asset_id)
        if not asset:
            raise NotFoundError("关联数据资产不存在，无法预标注")
        preset = PRESET_LABELS.get(asset.data_type.value, [])
        task.pre_annotation = {
            "labels": [{"label": p["value"], "confidence": 0.85} for p in preset[:1]],
            "model": "demo",
        }
        task.pre_confidence = 0.85
        task.status = AnnotationStatus.PRE_ANNOTATED
        self._commit()
        self._log("pre_annotate", task.id, "触发自动预标注")
        self._commit()
        return task

    def assign_task(self, task_id: int, annotator_id: int = None) -> AnnotationTask:
        """分配标注任务（不传 annotator_id 则分配给当前用户）

        校验：
        - annotator_id 必须存在且角色为 annotator/admin
        - 仅允许 pending/pre_annotated/rejected/annotating 状态的任务被分配
        """
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        if task.status not in _REASSIGNABLE_STATUSES:
            raise ValidationError(
                f"当前任务状态为 {task.status.value}，不可重新分配"
                f"（仅待预标注/已预标注/被驳回/标注中可分配）"
            )
        target_id = annotator_id or self.operator_id
        target_user = User.query.get(target_id)
        if not target_user:
            raise ValidationError("目标用户不存在")
        if target_user.role not in (Role.ANNOTATOR.value, Role.ADMIN.value):
            raise ValidationError(f"仅可分配给标注员（当前角色：{target_user.role}）")
        if not target_user.is_active:
            raise ValidationError("目标用户已被禁用")
        is_reassign = task.status == AnnotationStatus.REJECTED
        task.annotator_id = target_id
        task.status = AnnotationStatus.ANNOTATING
        self._commit()
        self._log(
            "assign", task.id,
            f"{'重新分配被驳回任务' if is_reassign else '分配标注任务'}"
            f"给 {target_user.username}（#{target_id}）",
        )
        self._commit()
        return task

    def batch_assign_tasks(self, task_ids: list, annotator_id: int) -> dict:
        """批量分配标注任务（仅允许 pending/pre_annotated/rejected/annotating 状态）"""
        if not task_ids or not isinstance(task_ids, list):
            raise ValidationError("task_ids 列表不能为空")
        if not annotator_id:
            raise ValidationError("annotator_id 不能为空")
        target_user = User.query.get(annotator_id)
        if not target_user:
            raise ValidationError("目标用户不存在")
        if target_user.role not in (Role.ANNOTATOR.value, Role.ADMIN.value):
            raise ValidationError(f"仅可分配给标注员（当前角色：{target_user.role}）")
        if not target_user.is_active:
            raise ValidationError("目标用户已被禁用")

        assigned = []
        skipped = []
        for tid in task_ids:
            task = AnnotationTask.query.get(tid)
            if not task:
                skipped.append({"id": tid, "reason": "任务不存在"})
                continue
            if task.status not in _REASSIGNABLE_STATUSES:
                skipped.append({"id": tid, "reason": f"状态 {task.status.value} 不可分配"})
                continue
            task.annotator_id = annotator_id
            task.status = AnnotationStatus.ANNOTATING
            assigned.append(task)
        self._commit()
        for t in assigned:
            self._log("assign", t.id,
                      f"批量分配标注任务给 {target_user.username}（#{annotator_id}）")
        self._commit()
        return {"assigned_count": len(assigned), "skipped": skipped}

    def submit_annotation(self, task_id: int, labels: list) -> AnnotationTask:
        """提交人工标注结果（覆盖式保存标签列表）

        安全限制：只有分配的标注员或管理员可提交。
        """
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        # 校验操作者是否为该任务的标注员（管理员除外）
        if task.annotator_id and task.annotator_id != self.operator_id:
            if self.operator_role != "admin":
                raise PermissionDeniedError("只有任务分配的标注员可提交标注")
        annotator_id = self.operator_id

        # 清空原有标注后重新写入
        Annotation.query.filter_by(task_id=task_id).delete()
        for item in labels:
            ann = Annotation(
                task_id=task_id,
                annotator_id=annotator_id,
                label=item.get("label"),
                label_type=item.get("label_type"),
                start_time_ms=item.get("start_time_ms"),
                end_time_ms=item.get("end_time_ms"),
                confidence=item.get("confidence"),
                note=item.get("note"),
            )
            db.session.add(ann)

        # 留存版本快照
        version_no = AnnotationVersion.query.filter_by(task_id=task_id).count() + 1
        db.session.add(AnnotationVersion(
            task_id=task_id, version_no=version_no,
            content={"annotations": labels},
            operator_id=annotator_id, operation="annotate",
        ))
        task.status = AnnotationStatus.ANNOTATED
        # 标注结束后，将关联数据资产归入"标注数据"分层
        asset = DataAsset.query.get(task.data_asset_id)
        if asset and asset.layer != DataLayer.ANNOTATION:
            asset.layer = DataLayer.ANNOTATION
        self._commit()
        self._log("annotate", task.id, f"提交人工标注（{len(labels)} 条标签）")
        self._commit()
        return task

    def review_task(self, task_id: int, data: dict) -> AnnotationTask:
        """医生复核：确认/驳回 + 电子签字

        支持在复核时修改标注内容：若 data 中包含 annotations 字段，则先更新标注再执行复核。
        """
        task = self._get_or_404(AnnotationTask, task_id, "任务不存在")
        result = data.get("result")  # approved / rejected
        if result not in ("approved", "rejected"):
            raise ValidationError("复核结果非法")

        # 复核时修改标注：如果传了 annotations，覆盖式更新
        modified_labels = data.get("annotations")
        if modified_labels is not None:
            reviewer_id = self.operator_id
            Annotation.query.filter_by(task_id=task_id).delete()
            for item in modified_labels:
                ann = Annotation(
                    task_id=task_id,
                    annotator_id=task.annotator_id or reviewer_id,
                    label=item.get("label"),
                    label_type=item.get("label_type"),
                    start_time_ms=item.get("start_time_ms"),
                    end_time_ms=item.get("end_time_ms"),
                    confidence=item.get("confidence"),
                    note=item.get("note"),
                )
                db.session.add(ann)
            # 留存版本快照
            version_no = AnnotationVersion.query.filter_by(task_id=task_id).count() + 1
            db.session.add(AnnotationVersion(
                task_id=task_id, version_no=version_no,
                content={"annotations": modified_labels},
                operator_id=reviewer_id, operation="review_modify",
            ))

        task.review_result = result
        task.review_comment = data.get("comment", "")
        task.review_signature = data.get("signature")
        task.reviewer_id = self.operator_id
        task.reviewed_at = datetime.utcnow()
        task.status = (AnnotationStatus.APPROVED if result == "approved"
                       else AnnotationStatus.REJECTED)
        # 复核通过时兜底将数据资产归入"标注数据"分层
        if result == "approved":
            asset = DataAsset.query.get(task.data_asset_id)
            if asset and asset.layer != DataLayer.ANNOTATION:
                asset.layer = DataLayer.ANNOTATION
        self._commit()
        review_text = "通过" if result == "approved" else "驳回"
        comment = data.get("comment", "")
        detail = f"医生复核{review_text}"
        if modified_labels is not None:
            detail += f"（修改了 {len(modified_labels)} 条标注）"
        if comment:
            detail += f"：{comment}"
        self._log("review", task.id, detail)
        self._commit()
        return task

    # ==================== 内部辅助 ====================

    def _log(self, action: str, target_id: int, detail: str = "") -> None:
        """记录标注任务操作日志（target_type=annotation_task）"""
        log_operation(action, "annotation_task", target_id, detail,
                      operator=self._operator_user())