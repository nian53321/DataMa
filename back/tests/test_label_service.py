# -*- coding: utf-8 -*-
"""LabelService 单元测试

覆盖标签管理业务规则：
- 创建：name/value/data_type 必填、(value, data_type) 唯一
- 更新：value 变更级联更新标注记录
- 删除：已被使用时禁止删除
- 替换：批量替换为目标标签
- preset_labels：数据库为空时回退硬编码
"""
import pytest

from app.models import Label, Annotation, AnnotationTask, AnnotationStatus, DataAsset
from app.services import LabelService, ValidationError, NotFoundError, ConflictError
from app.extensions import db


class TestLabelServiceCreate:
    """create_label 业务规则"""

    def test_create_label_success(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        label = svc.create_label({
            "name": "测试标签", "value": "test_label", "data_type": "eeg",
        })
        assert label.id is not None
        assert label.name == "测试标签"
        assert label.value == "test_label"
        assert label.data_type == "eeg"

    def test_create_label_missing_fields(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="标签名称、标识、模态均不能为空"):
            svc.create_label({"name": "x", "value": "", "data_type": "eeg"})

    def test_create_label_duplicate_value(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        svc.create_label({"name": "标签1", "value": "dup", "data_type": "eeg"})
        with pytest.raises(ConflictError, match="该模态下标签标识已存在"):
            svc.create_label({"name": "标签2", "value": "dup", "data_type": "eeg"})

    def test_create_label_same_value_different_modality(self, db_app, admin_user):
        """同一 value 在不同模态下允许"""
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        svc.create_label({"name": "EEG标签", "value": "shared", "data_type": "eeg"})
        # 不同模态应该可以创建
        svc.create_label({"name": "ECG标签", "value": "shared", "data_type": "ecg"})


class TestLabelServiceUpdate:
    """update_label 业务规则"""

    def test_update_label_name(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        label = svc.create_label({
            "name": "原名", "value": "upd1", "data_type": "eeg",
        })
        updated = svc.update_label(label.id, {"name": "新名"})
        assert updated.name == "新名"

    def test_update_label_value_cascade(self, db_app, admin_user):
        """value 变更时级联更新标注记录"""
        from app.models import AnnotationStatus, DataType, DataLayer, Subject
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        label = svc.create_label({
            "name": "级联测试", "value": "old_val", "data_type": "eeg",
        })
        # 创建受试者 + 数据资产 + 任务 + 标注
        subj = Subject(pseudo_id="CASCADE_TEST")
        db.session.add(subj)
        db.session.flush()
        asset = DataAsset(subject_id=subj.id, data_type=DataType.EEG,
                          layer=DataLayer.RAW, file_name="x.csv", file_path="x")
        db.session.add(asset)
        db.session.flush()
        task = AnnotationTask(data_asset_id=asset.id, status=AnnotationStatus.PENDING)
        db.session.add(task)
        db.session.flush()
        ann = Annotation(task_id=task.id, annotator_id=admin_user.id, label="old_val")
        db.session.add(ann)
        db.session.commit()
        # 修改 value
        svc.update_label(label.id, {"value": "new_val"})
        # 验证标注记录已更新
        assert Annotation.query.filter_by(label="new_val").count() == 1
        assert Annotation.query.filter_by(label="old_val").count() == 0

    def test_update_label_duplicate_value_raises(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        svc.create_label({"name": "A", "value": "v1", "data_type": "eeg"})
        label_b = svc.create_label({"name": "B", "value": "v2", "data_type": "eeg"})
        with pytest.raises(ConflictError, match="该模态下标签标识已存在"):
            svc.update_label(label_b.id, {"value": "v1"})


class TestLabelServiceDelete:
    """delete_label 业务规则"""

    def test_delete_label_success(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        label = svc.create_label({
            "name": "待删除", "value": "del_me", "data_type": "eeg",
        })
        name, value = svc.delete_label(label.id)
        assert name == "待删除"
        assert value == "del_me"
        assert Label.query.get(label.id) is None

    def test_delete_label_in_use_forbidden(self, db_app, admin_user):
        """已被标注使用的标签禁止删除"""
        from app.models import DataType, DataLayer, Subject
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        label = svc.create_label({
            "name": "使用中", "value": "in_use", "data_type": "eeg",
        })
        # 创建使用该标签的标注（必须先创建 Subject，因为 subject_id NOT NULL）
        subj = Subject(pseudo_id="DEL_IN_USE_TEST")
        db.session.add(subj)
        db.session.flush()
        asset = DataAsset(subject_id=subj.id, data_type=DataType.EEG,
                          layer=DataLayer.RAW, file_name="x.csv", file_path="x")
        db.session.add(asset)
        db.session.flush()
        task = AnnotationTask(data_asset_id=asset.id, status=AnnotationStatus.PENDING)
        db.session.add(task)
        db.session.flush()
        ann = Annotation(task_id=task.id, annotator_id=admin_user.id, label="in_use")
        db.session.add(ann)
        db.session.commit()
        with pytest.raises(ValidationError, match="已被 .* 条标注使用"):
            svc.delete_label(label.id)


class TestLabelServiceReplace:
    """replace_label 业务规则"""

    def test_replace_label_success(self, db_app, admin_user):
        from app.models import DataType, DataLayer, Subject
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        src = svc.create_label({"name": "源", "value": "src_val", "data_type": "eeg"})
        tgt = svc.create_label({"name": "目标", "value": "tgt_val", "data_type": "eeg"})
        # 创建使用 src 的标注（必须先创建 Subject，因为 subject_id NOT NULL）
        subj = Subject(pseudo_id="REPLACE_TEST")
        db.session.add(subj)
        db.session.flush()
        asset = DataAsset(subject_id=subj.id, data_type=DataType.EEG,
                          layer=DataLayer.RAW, file_name="x.csv", file_path="x")
        db.session.add(asset)
        db.session.flush()
        task = AnnotationTask(data_asset_id=asset.id, status=AnnotationStatus.PENDING)
        db.session.add(task)
        db.session.flush()
        for _ in range(3):
            db.session.add(Annotation(
                task_id=task.id, annotator_id=admin_user.id, label="src_val",
            ))
        db.session.commit()
        # 替换
        result = svc.replace_label(src.id, {"target_value": "tgt_val"})
        assert result["affected"] == 3
        assert Annotation.query.filter_by(label="src_val").count() == 0
        assert Annotation.query.filter_by(label="tgt_val").count() == 3

    def test_replace_same_label_raises(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        label = svc.create_label({"name": "X", "value": "x_val", "data_type": "eeg"})
        with pytest.raises(ValidationError, match="目标标签与原标签相同"):
            svc.replace_label(label.id, {"target_value": "x_val"})


class TestLabelServicePreset:
    """preset_labels 业务规则"""

    def test_preset_labels_fallback_when_db_empty(self, db_app, admin_user):
        """数据库无标签时回退硬编码"""
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        result = svc.preset_labels()
        assert "eeg" in result
        assert "video" in result
        assert len(result["eeg"]) == 4  # 硬编码 EEG 有 4 个标签

    def test_preset_labels_from_db(self, db_app, admin_user):
        """数据库有标签时按模态分组返回"""
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        svc.create_label({"name": "自定义", "value": "custom1", "data_type": "eeg"})
        result = svc.preset_labels()
        assert "eeg" in result
        # 只返回启用标签
        values = [item["value"] for item in result["eeg"]]
        assert "custom1" in values

    def test_preset_labels_filter_by_data_type(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        result = svc.preset_labels(data_type="eeg")
        assert "eeg" in result
        assert "video" not in result  # 仅返回 eeg


class TestLabelServiceUsage:
    """label_usage 业务规则"""

    def test_label_usage_returns_zero_for_unused(self, db_app, admin_user):
        svc = LabelService(operator_id=admin_user.id, operator_role="admin")
        svc.create_label({"name": "未使用", "value": "unused", "data_type": "eeg"})
        result = svc.label_usage()
        # 至少应包含刚创建的标签
        target = next((r for r in result if r["value"] == "unused"), None)
        assert target is not None
        assert target["usage_count"] == 0
