# -*- coding: utf-8 -*-
"""AnnotationService 单元测试

覆盖标注任务业务规则：
- 创建任务：必填数据资产、资产必须存在
- 批量创建：共享 group_id、跳过已有非驳回状态任务的资产、可指定初始标注员
- 更新任务：禁止通过 update 改 status
- 删除任务：级联删除标注与版本
- 分配任务：仅允许 pending/pre_annotated/rejected/annotating 状态被分配
- 提交标注：仅分配标注员可提交（管理员除外），数据资产归入 annotation 分层
- 复核任务：通过/驳回、可在复核时修改标注
- 查询：列表/详情/可用资产/我的任务/质量看板
"""
import pytest

from app.extensions import db
from app.models import (
    AnnotationTask, Annotation, AnnotationStatus, AnnotationVersion,
    DataAsset, DataLayer, DataType, Role, Subject,
)
from app.services import (
    AnnotationService, ValidationError, NotFoundError, PermissionDeniedError,
)


# ==================== 测试辅助 ====================

def _make_asset(pseudo_id="SUBJ_TEST", data_type=DataType.EEG):
    """创建测试用受试者 + 数据资产"""
    subj = Subject(pseudo_id=pseudo_id)
    db.session.add(subj)
    db.session.flush()
    asset = DataAsset(
        subject_id=subj.id, data_type=data_type, layer=DataLayer.RAW,
        file_name=f"{pseudo_id}.csv", file_path=f"/tmp/{pseudo_id}.csv",
    )
    db.session.add(asset)
    db.session.flush()
    return asset


# ==================== 创建任务 ====================

class TestCreateTask:
    """create_task 业务规则"""

    def test_create_task_success(self, db_app, admin_user):
        asset = _make_asset("CREATE_TASK_1")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        assert task.id is not None
        assert task.data_asset_id == asset.id
        assert task.status == AnnotationStatus.PENDING

    def test_create_task_missing_asset_id(self, db_app, admin_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="数据资产ID必填"):
            svc.create_task({})

    def test_create_task_asset_not_found(self, db_app, admin_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="数据资产不存在"):
            svc.create_task({"data_asset_id": 99999})


class TestBatchCreateTasks:
    """batch_create_tasks 业务规则"""

    def test_batch_create_share_group_id(self, db_app, admin_user):
        a1 = _make_asset("BATCH_1")
        a2 = _make_asset("BATCH_2")
        a3 = _make_asset("BATCH_3")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        result = svc.batch_create_tasks({
            "data_asset_ids": [a1.id, a2.id, a3.id],
            "remark": "批量测试",
        })
        assert result["count"] == 3
        assert len(result["skipped"]) == 0
        group_ids = {t["group_id"] for t in result["items"]}
        assert len(group_ids) == 1
        first_id = result["items"][0]["id"]
        assert result["items"][0]["group_id"] == first_id

    def test_batch_create_skip_busy_assets(self, db_app, admin_user):
        """已有非驳回状态任务的资产应被跳过"""
        a1 = _make_asset("SKIP_BUSY_1")
        a2 = _make_asset("SKIP_BUSY_2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        svc.create_task({"data_asset_id": a1.id})
        result = svc.batch_create_tasks({"data_asset_ids": [a1.id, a2.id]})
        assert result["count"] == 1
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["id"] == a1.id

    def test_batch_create_with_annotator_sets_pre_annotated(self, db_app, admin_user, annotator_user):
        """指定 annotator_id 时初始状态应为 pre_annotated"""
        a1 = _make_asset("WITH_ANN")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        result = svc.batch_create_tasks({
            "data_asset_ids": [a1.id],
            "annotator_id": annotator_user.id,
        })
        task = AnnotationTask.query.get(result["items"][0]["id"])
        assert task.status == AnnotationStatus.PRE_ANNOTATED
        assert task.annotator_id == annotator_user.id

    def test_batch_create_empty_list_raises(self, db_app, admin_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="data_asset_ids 列表不能为空"):
            svc.batch_create_tasks({"data_asset_ids": []})

    def test_batch_create_invalid_annotator_raises(self, db_app, admin_user):
        a1 = _make_asset("INVALID_ANN")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="指定的标注员不存在"):
            svc.batch_create_tasks({
                "data_asset_ids": [a1.id],
                "annotator_id": 99999,
            })


# ==================== 更新任务 ====================

class TestUpdateTask:
    """update_task 业务规则"""

    def test_update_task_remark(self, db_app, admin_user):
        asset = _make_asset("UPD_REMARK")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        updated = svc.update_task(task.id, {"remark": "新备注"})
        assert updated.remark == "新备注"

    def test_update_task_forbid_status_change(self, db_app, admin_user):
        """禁止通过 update_task 修改 status"""
        asset = _make_asset("UPD_STATUS")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        with pytest.raises(ValidationError, match="状态变更请通过标注/复核接口"):
            svc.update_task(task.id, {"status": "annotating"})

    def test_update_task_not_found(self, db_app, admin_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="任务不存在"):
            svc.update_task(99999, {"remark": "x"})


# ==================== 删除任务 ====================

class TestDeleteTask:
    """delete_task 业务规则"""

    def test_delete_task_cascade(self, db_app, admin_user, annotator_user):
        """删除任务应级联删除标注与版本记录"""
        asset = _make_asset("DEL_CASCADE")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        svc.assign_task(task.id, annotator_user.id)
        svc.submit_annotation(task.id, [{"label": "x"}])
        assert Annotation.query.filter_by(task_id=task.id).count() == 1
        assert AnnotationVersion.query.filter_by(task_id=task.id).count() == 1
        svc.delete_task(task.id)
        assert AnnotationTask.query.get(task.id) is None
        assert Annotation.query.filter_by(task_id=task.id).count() == 0
        assert AnnotationVersion.query.filter_by(task_id=task.id).count() == 0

    def test_delete_task_not_found(self, db_app, admin_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="任务不存在"):
            svc.delete_task(99999)

    def test_batch_delete_tasks(self, db_app, admin_user):
        a1 = _make_asset("BATCH_DEL_1")
        a2 = _make_asset("BATCH_DEL_2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        t1 = svc.create_task({"data_asset_id": a1.id})
        t2 = svc.create_task({"data_asset_id": a2.id})
        result = svc.batch_delete_tasks([t1.id, t2.id, 99999])
        assert result["deleted_count"] == 2
        assert len(result["not_found"]) == 1
        assert AnnotationTask.query.get(t1.id) is None
        assert AnnotationTask.query.get(t2.id) is None


# ==================== 状态流转：分配 ====================

class TestAssignTask:
    """assign_task 业务规则"""

    def test_assign_to_annotator_success(self, db_app, admin_user, annotator_user):
        asset = _make_asset("ASSIGN_OK")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        updated = svc.assign_task(task.id, annotator_user.id)
        assert updated.annotator_id == annotator_user.id
        assert updated.status == AnnotationStatus.ANNOTATING

    def test_assign_to_self_when_no_annotator_id(self, db_app, annotator_user):
        """未传 annotator_id 时分配给当前用户"""
        asset = _make_asset("ASSIGN_SELF")
        svc = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        task = svc.create_task({"data_asset_id": asset.id})
        updated = svc.assign_task(task.id)
        assert updated.annotator_id == annotator_user.id

    def test_assign_invalid_status_raises(self, db_app, admin_user, annotator_user):
        """非可分配状态的任务不可分配"""
        asset = _make_asset("ASSIGN_BAD_STATUS")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        task.status = AnnotationStatus.ANNOTATED
        db.session.commit()
        with pytest.raises(ValidationError, match="不可重新分配"):
            svc.assign_task(task.id, annotator_user.id)

    def test_assign_to_non_annotator_role_raises(self, db_app, admin_user):
        """不可分配给非标注员角色"""
        from app.models.user import User
        doctor = User(username="doctor1", real_name="医生", role="doctor", is_active=True)
        doctor.set_password("doctor123")
        db.session.add(doctor)
        db.session.commit()
        asset = _make_asset("ASSIGN_TO_DOCTOR")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        with pytest.raises(ValidationError, match="仅可分配给标注员"):
            svc.assign_task(task.id, doctor.id)

    def test_batch_assign(self, db_app, admin_user, annotator_user):
        a1 = _make_asset("BATCH_ASSIGN_1")
        a2 = _make_asset("BATCH_ASSIGN_2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        t1 = svc.create_task({"data_asset_id": a1.id})
        t2 = svc.create_task({"data_asset_id": a2.id})
        result = svc.batch_assign_tasks([t1.id, t2.id], annotator_user.id)
        assert result["assigned_count"] == 2
        assert AnnotationTask.query.get(t1.id).annotator_id == annotator_user.id
        assert AnnotationTask.query.get(t2.id).annotator_id == annotator_user.id


# ==================== 状态流转：提交标注 ====================

class TestSubmitAnnotation:
    """submit_annotation 业务规则"""

    def test_submit_success_and_layer_change(self, db_app, admin_user, annotator_user):
        """提交标注后任务状态变为 annotated，资产归入 annotation 分层"""
        asset = _make_asset("SUBMIT_OK")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)

        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        updated = svc_ann.submit_annotation(task.id, [
            {"label": "seizure", "label_type": "event"},
            {"label": "normal", "label_type": "background"},
        ])
        assert updated.status == AnnotationStatus.ANNOTATED
        assert Annotation.query.filter_by(task_id=task.id).count() == 2
        assert AnnotationVersion.query.filter_by(task_id=task.id).count() == 1
        refreshed_asset = DataAsset.query.get(asset.id)
        assert refreshed_asset.layer == DataLayer.ANNOTATION

    def test_submit_overwrites_previous(self, db_app, admin_user, annotator_user):
        """覆盖式保存：第二次提交替换第一次的标签"""
        asset = _make_asset("SUBMIT_OVERWRITE")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        svc_ann.submit_annotation(task.id, [{"label": "old1"}, {"label": "old2"}])
        svc_ann.submit_annotation(task.id, [{"label": "new1"}])
        annotations = Annotation.query.filter_by(task_id=task.id).all()
        assert len(annotations) == 1
        assert annotations[0].label == "new1"

    def test_submit_by_non_annotator_forbidden(self, db_app, admin_user, annotator_user):
        """非分配标注员不可提交（管理员除外）"""
        from app.models.user import User
        other = User(username="other_ann", real_name="其他标注员",
                     role="annotator", is_active=True)
        other.set_password("other123")
        db.session.add(other)
        db.session.commit()

        asset = _make_asset("SUBMIT_FORBID")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)

        svc_other = AnnotationService(operator_id=other.id, operator_role="annotator")
        with pytest.raises(PermissionDeniedError, match="只有任务分配的标注员可提交"):
            svc_other.submit_annotation(task.id, [{"label": "x"}])

    def test_admin_can_submit_any_task(self, db_app, admin_user, annotator_user):
        """管理员可提交任意任务（即使未分配给自己）"""
        asset = _make_asset("SUBMIT_ADMIN")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        updated = svc_admin.submit_annotation(task.id, [{"label": "by_admin"}])
        assert updated.status == AnnotationStatus.ANNOTATED


# ==================== 状态流转：复核 ====================

class TestReviewTask:
    """review_task 业务规则"""

    def test_review_approve(self, db_app, admin_user, annotator_user):
        asset = _make_asset("REVIEW_APP")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        svc_ann.submit_annotation(task.id, [{"label": "x"}])

        updated = svc_admin.review_task(task.id, {
            "result": "approved", "comment": "通过", "signature": "DR.SIG",
        })
        assert updated.status == AnnotationStatus.APPROVED
        assert updated.review_result == "approved"
        assert updated.review_signature == "DR.SIG"
        assert updated.reviewer_id == admin_user.id

    def test_review_reject(self, db_app, admin_user, annotator_user):
        asset = _make_asset("REVIEW_REJ")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        svc_ann.submit_annotation(task.id, [{"label": "x"}])

        updated = svc_admin.review_task(task.id, {"result": "rejected", "comment": "重做"})
        assert updated.status == AnnotationStatus.REJECTED
        assert updated.review_result == "rejected"

    def test_review_invalid_result_raises(self, db_app, admin_user):
        asset = _make_asset("REVIEW_BAD")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc.create_task({"data_asset_id": asset.id})
        with pytest.raises(ValidationError, match="复核结果非法"):
            svc.review_task(task.id, {"result": "unknown"})

    def test_review_with_modified_annotations(self, db_app, admin_user, annotator_user):
        """复核时可修改标注内容，应留档新版本"""
        asset = _make_asset("REVIEW_MOD")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        svc_ann.submit_annotation(task.id, [{"label": "original"}])

        svc_admin.review_task(task.id, {
            "result": "approved",
            "annotations": [{"label": "modified1"}, {"label": "modified2"}],
        })
        annotations = Annotation.query.filter_by(task_id=task.id).all()
        labels = {a.label for a in annotations}
        assert labels == {"modified1", "modified2"}
        assert AnnotationVersion.query.filter_by(task_id=task.id).count() == 2


# ==================== 查询 ====================

class TestQuery:
    """查询类方法"""

    def test_list_tasks_basic(self, db_app, admin_user):
        a1 = _make_asset("LIST_1")
        a2 = _make_asset("LIST_2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        svc.create_task({"data_asset_id": a1.id})
        svc.create_task({"data_asset_id": a2.id})
        result = svc.list_tasks()
        assert result["total"] == 2

    def test_list_tasks_filter_by_status(self, db_app, admin_user, annotator_user):
        a1 = _make_asset("LIST_S1")
        a2 = _make_asset("LIST_S2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        t1 = svc.create_task({"data_asset_id": a1.id})
        svc.create_task({"data_asset_id": a2.id})
        svc.assign_task(t1.id, annotator_user.id)
        result = svc.list_tasks(status="annotating")
        assert result["total"] == 1
        assert result["items"][0]["id"] == t1.id

    def test_list_annotators(self, db_app, admin_user, annotator_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        annotators = svc.list_annotators()
        usernames = [a["username"] for a in annotators]
        assert "admin" in usernames
        assert "annotator" in usernames

    def test_task_detail(self, db_app, admin_user, annotator_user):
        asset = _make_asset("DETAIL")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": asset.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        svc_ann.submit_annotation(task.id, [{"label": "lbl"}])

        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        detail = svc.task_detail(task.id)
        assert detail["task"]["id"] == task.id
        assert detail["asset"]["id"] == asset.id
        assert len(detail["annotations"]) == 1
        assert detail["annotations"][0]["label"] == "lbl"
        assert "preset_labels" in detail

    def test_available_assets_excludes_busy(self, db_app, admin_user):
        """available_assets 应排除已有非驳回状态任务的资产"""
        a1 = _make_asset("AVAIL_BUSY")
        a2 = _make_asset("AVAIL_FREE")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        svc.create_task({"data_asset_id": a1.id})
        result = svc.available_assets()
        asset_ids = [item["id"] for item in result["items"]]
        assert a2.id in asset_ids
        assert a1.id not in asset_ids

    def test_my_tasks(self, db_app, admin_user, annotator_user):
        a1 = _make_asset("MINE_1")
        a2 = _make_asset("MINE_2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        t1 = svc.create_task({"data_asset_id": a1.id})
        svc.create_task({"data_asset_id": a2.id})
        svc.assign_task(t1.id, annotator_user.id)

        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        result = svc_ann.my_tasks()
        assert result["total"] == 1
        assert result["tasks"][0]["id"] == t1.id

    def test_quality_dashboard(self, db_app, admin_user, annotator_user):
        a1 = _make_asset("QA_1")
        svc_admin = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        task = svc_admin.create_task({"data_asset_id": a1.id})
        svc_admin.assign_task(task.id, annotator_user.id)
        svc_ann = AnnotationService(operator_id=annotator_user.id, operator_role="annotator")
        svc_ann.submit_annotation(task.id, [{"label": "x"}])
        svc_admin.review_task(task.id, {"result": "approved"})

        dashboard = svc_admin.quality_dashboard()
        assert dashboard["total"] == 1
        assert dashboard["completion_rate"] == 1.0
        assert dashboard["pass_rate"] == 1.0
        assert dashboard["review_pass_rate"] == 1.0

    def test_task_group(self, db_app, admin_user):
        """批量创建的任务共享 group_id，task_group 可查询整组"""
        a1 = _make_asset("GRP_1")
        a2 = _make_asset("GRP_2")
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        result = svc.batch_create_tasks({"data_asset_ids": [a1.id, a2.id]})
        group_id = result["group_id"]
        group = svc.task_group(group_id)
        assert group["total"] == 2
        assert group["group_id"] == group_id
        # 两个任务的 id 都应属于该组
        task_ids = [t["id"] for t in group["tasks"]]
        assert len(task_ids) == 2

    def test_task_group_not_found(self, db_app, admin_user):
        svc = AnnotationService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="任务分组不存在"):
            svc.task_group(99999)
