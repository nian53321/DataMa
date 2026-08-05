# -*- coding: utf-8 -*-
"""SubjectService 单元测试

验证受试者管理业务规则（不启动 Flask 路由，直接调用 service）。
覆盖：
- 创建：格式校验、唯一性校验
- 更新：伪ID 变更重新校验、快照留档
- 查询：风险分级 'none' 筛选、按角色脱敏
- 删除：级联删除资产 + 磁盘文件清理
"""
import os
import pytest

from app.models import Subject, DataAsset
from app.services import SubjectService, ValidationError, ConflictError, NotFoundError


class TestSubjectServiceCreate:
    """create_subject 业务规则"""

    def test_create_subject_success(self, db_app, admin_user):
        """正常创建受试者"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        subject = svc.create_subject({
            "pseudo_id": "SUBJ_001",
            "age": 65,
            "gender": "男",
            "cognitive_risk_level": "normal",
        })
        assert subject.id is not None
        assert subject.pseudo_id == "SUBJ_001"
        assert subject.age == 65

    def test_create_subject_empty_pseudo_id_raises(self, db_app, admin_user):
        """pseudo_id 为空抛 ValidationError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="伪ID不能为空"):
            svc.create_subject({"pseudo_id": "", "age": 65})

    def test_create_subject_invalid_pseudo_id_format(self, db_app, admin_user):
        """pseudo_id 格式非法（含路径穿越字符）抛 ValidationError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="格式非法"):
            svc.create_subject({"pseudo_id": "../etc/passwd"})  # 含非法字符

    def test_create_subject_pseudo_id_too_short(self, db_app, admin_user):
        """pseudo_id 少于 3 位抛 ValidationError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="格式非法"):
            svc.create_subject({"pseudo_id": "ab"})  # 2 位太短

    def test_create_subject_duplicate_pseudo_id_raises(self, db_app, admin_user):
        """重复 pseudo_id 抛 ConflictError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        svc.create_subject({"pseudo_id": "SUBJ_DUP", "age": 65})
        with pytest.raises(ConflictError, match="已存在"):
            svc.create_subject({"pseudo_id": "SUBJ_DUP", "age": 70})


class TestSubjectServiceList:
    """list_subjects 业务规则"""

    def _seed(self, db_app, admin_user):
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        svc.create_subject({"pseudo_id": "SUBJ_A", "gender": "男",
                            "cognitive_risk_level": "normal"})
        svc.create_subject({"pseudo_id": "SUBJ_B", "gender": "女",
                            "cognitive_risk_level": "mci"})
        svc.create_subject({"pseudo_id": "SUBJ_C", "gender": "男",
                            "cognitive_risk_level": None})

    def test_list_subjects_basic(self, db_app, admin_user):
        """基础列表返回全部受试者"""
        self._seed(db_app, admin_user)
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_subjects(page=1, page_size=20)
        assert result["total"] == 3
        assert len(result["items"]) == 3

    def test_list_subjects_filter_by_gender(self, db_app, admin_user):
        """按性别筛选"""
        self._seed(db_app, admin_user)
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_subjects(gender="男")
        assert result["total"] == 2
        assert all(item["gender"] == "男" for item in result["items"])

    def test_list_subjects_filter_risk_level_none(self, db_app, admin_user):
        """风险分级 'none' 筛选 NULL/空字符串"""
        self._seed(db_app, admin_user)
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_subjects(risk_level="none")
        assert result["total"] == 1
        assert result["items"][0]["pseudo_id"] == "SUBJ_C"

    def test_list_subjects_filter_by_keyword(self, db_app, admin_user):
        """按关键词搜索 pseudo_id"""
        self._seed(db_app, admin_user)
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_subjects(keyword="SUBJ_A")
        assert result["total"] == 1
        assert result["items"][0]["pseudo_id"] == "SUBJ_A"


class TestSubjectServiceUpdate:
    """update_subject 业务规则"""

    def test_update_subject_success(self, db_app, admin_user):
        """正常更新"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        subject = svc.create_subject({"pseudo_id": "SUBJ_UPD", "age": 60})
        updated = svc.update_subject(subject.id, {"age": 65, "gender": "男"})
        assert updated.age == 65
        assert updated.gender == "男"

    def test_update_subject_not_found(self, db_app, admin_user):
        """更新不存在的受试者抛 NotFoundError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="不存在"):
            svc.update_subject(99999, {"age": 65})

    def test_update_subject_pseudo_id_invalid_format(self, db_app, admin_user):
        """修改 pseudo_id 为非法格式抛 ValidationError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        subject = svc.create_subject({"pseudo_id": "SUBJ_VALID"})
        with pytest.raises(ValidationError, match="格式非法"):
            svc.update_subject(subject.id, {"pseudo_id": "x"})  # 1 位太短

    def test_update_subject_pseudo_id_duplicate(self, db_app, admin_user):
        """修改 pseudo_id 为已存在的值抛 ConflictError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        svc.create_subject({"pseudo_id": "SUBJ_EXISTING"})
        subject = svc.create_subject({"pseudo_id": "SUBJ_ORIGINAL"})
        with pytest.raises(ConflictError, match="已存在"):
            svc.update_subject(subject.id, {"pseudo_id": "SUBJ_EXISTING"})


class TestSubjectServiceDelete:
    """delete_subject 业务规则"""

    def test_delete_subject_not_found(self, db_app, admin_user):
        """删除不存在的受试者抛 NotFoundError"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="不存在"):
            svc.delete_subject(99999, storage_root="/tmp")

    def test_delete_subject_cascade_assets(self, db_app, admin_user, tmp_path):
        """删除受试者级联删除其数据资产 + 磁盘文件"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        subject = svc.create_subject({"pseudo_id": "SUBJ_DEL"})

        # 创建一个数据资产 + 模拟磁盘文件
        from app.models import DataAsset, DataType, DataLayer
        asset = DataAsset(
            subject_id=subject.id,
            data_type=DataType.EEG,
            layer=DataLayer.RAW,
            file_name="eeg.csv",
            file_path="raw/SUBJ_DEL/eeg/eeg.csv",
        )
        from app.extensions import db
        db.session.add(asset)
        db.session.commit()

        # 模拟磁盘文件
        asset_path = tmp_path / "data_lake" / "raw" / "SUBJ_DEL" / "eeg" / "eeg.csv"
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text("dummy eeg data")
        assert asset_path.exists()

        # 删除受试者
        svc.delete_subject(subject.id, storage_root=str(tmp_path / "data_lake"))

        # 验证：受试者与资产记录均被删除
        assert Subject.query.get(subject.id) is None
        assert DataAsset.query.filter_by(subject_id=subject.id).count() == 0
        # 磁盘文件已清理
        assert not asset_path.exists()


class TestSubjectServiceDesensitize:
    """list_subjects 按角色脱敏"""

    def test_admin_no_desensitize(self, db_app, admin_user):
        """admin 角色不脱敏，phone 原样展示"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        svc.create_subject({"pseudo_id": "SUBJ_PHONE", "phone": "13800138000"})
        result = svc.list_subjects()
        assert result["items"][0]["phone"] == "13800138000"

    def test_annotator_desensitize(self, db_app, admin_user):
        """annotator 角色脱敏 phone"""
        svc = SubjectService(operator_id=admin_user.id, operator_role="admin")
        svc.create_subject({"pseudo_id": "SUBJ_PHONE2", "phone": "13800138000"})
        # 用 annotator 身份查询
        svc_annotator = SubjectService(operator_id=admin_user.id, operator_role="annotator")
        result = svc_annotator.list_subjects()
        # 脱敏后不应等于原始 phone
        assert result["items"][0]["phone"] != "13800138000"
        assert "*" in result["items"][0]["phone"]