# -*- coding: utf-8 -*-
"""StandardService 单元测试

覆盖数据规范与受试者信息模板业务规则：
- 规范 CRUD：名称必填、类型在白名单内、更新/删除留档
- 受试者模板：字段标识唯一/必填、中文名必填、类型合法、至少一个字段
- get_subject_template：数据库无记录时回退默认模板
"""
import pytest

from app.extensions import db
from app.models import DataStandard
from app.services import StandardService, ValidationError, NotFoundError
from app.services.standard_service import (
    DEFAULT_SUBJECT_FIELDS, FIELD_TYPE_OPTIONS, VALID_FIELD_TYPES,
)


class TestStandardCRUD:
    """规范 CRUD 业务规则"""

    def test_create_standard_success(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        std = svc.create_standard({
            "name": "命名规范1", "standard_type": "naming",
            "version": "1.0.0", "description": "测试",
        })
        assert std.id is not None
        assert std.name == "命名规范1"
        assert std.standard_type == "naming"

    def test_create_standard_missing_name(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="规范名称必填"):
            svc.create_standard({"name": "", "standard_type": "naming"})

    def test_create_standard_invalid_type(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="规范类型非法"):
            svc.create_standard({"name": "x", "standard_type": "unknown_type"})

    def test_create_standard_default_type(self, db_app, admin_user):
        """未指定 standard_type 时默认 metadata"""
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        std = svc.create_standard({"name": "默认类型"})
        assert std.standard_type == "metadata"

    def test_list_standards_filter_by_type(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        svc.create_standard({"name": "n1", "standard_type": "naming"})
        svc.create_standard({"name": "q1", "standard_type": "quality"})
        result = svc.list_standards(standard_type="naming")
        assert all(item["standard_type"] == "naming" for item in result)
        assert len(result) == 1

    def test_update_standard_success(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        std = svc.create_standard({"name": "原名", "standard_type": "naming"})
        updated = svc.update_standard(std.id, {
            "name": "新名", "description": "更新后",
        })
        assert updated.name == "新名"
        assert updated.description == "更新后"

    def test_update_standard_empty_name_raises(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        std = svc.create_standard({"name": "x", "standard_type": "naming"})
        with pytest.raises(ValidationError, match="规范名称不能为空"):
            svc.update_standard(std.id, {"name": ""})

    def test_update_standard_not_found(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="规范不存在"):
            svc.update_standard(99999, {"name": "x"})

    def test_delete_standard_success(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        std = svc.create_standard({"name": "待删", "standard_type": "naming"})
        name = svc.delete_standard(std.id)
        assert name == "待删"
        assert DataStandard.query.get(std.id) is None

    def test_delete_standard_not_found(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="规范不存在"):
            svc.delete_standard(99999)


class TestSubjectTemplate:
    """受试者信息模板业务规则"""

    def test_get_subject_template_fallback(self, db_app, admin_user):
        """数据库无模板时返回默认字段"""
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        result = svc.get_subject_template()
        assert "fields" in result
        assert "field_types" in result
        # 默认字段非空
        assert len(result["fields"]) == len(DEFAULT_SUBJECT_FIELDS)
        # field_types 与常量一致
        assert result["field_types"] == FIELD_TYPE_OPTIONS

    def test_update_subject_template_success(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        fields = [
            {"field_key": "pseudo_id", "field_label": "伪ID",
             "field_type": "input", "required": True, "enabled": True,
             "sort_order": 1, "placeholder": "", "options": []},
            {"field_key": "age", "field_label": "年龄",
             "field_type": "number", "required": True, "enabled": True,
             "sort_order": 2, "placeholder": "", "options": []},
        ]
        result = svc.update_subject_template(fields)
        assert result["fields"] == fields
        # 应创建一条 subject_template 规范
        std = DataStandard.query.filter_by(standard_type="subject_template").first()
        assert std is not None
        assert std.schema_json["fields"] == fields

    def test_update_subject_template_empty_fields_raises(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="至少需要保留一个字段"):
            svc.update_subject_template([])

    def test_update_subject_template_duplicate_key_raises(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        fields = [
            {"field_key": "x", "field_label": "X", "field_type": "input"},
            {"field_key": "x", "field_label": "重复", "field_type": "input"},
        ]
        with pytest.raises(ValidationError, match="字段标识重复: x"):
            svc.update_subject_template(fields)

    def test_update_subject_template_empty_key_raises(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        fields = [{"field_key": "", "field_label": "空键", "field_type": "input"}]
        with pytest.raises(ValidationError, match="字段标识不能为空"):
            svc.update_subject_template(fields)

    def test_update_subject_template_empty_label_raises(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        fields = [{"field_key": "k", "field_label": "", "field_type": "input"}]
        with pytest.raises(ValidationError, match="中文名不能为空"):
            svc.update_subject_template(fields)

    def test_update_subject_template_invalid_type_raises(self, db_app, admin_user):
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        fields = [{"field_key": "k", "field_label": "K", "field_type": "invalid_type"}]
        with pytest.raises(ValidationError, match="类型非法"):
            svc.update_subject_template(fields)

    def test_update_subject_template_version_increments(self, db_app, admin_user):
        """每次更新模板，version 的 minor 段应递增"""
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        fields = [{"field_key": "k1", "field_label": "K1", "field_type": "input"}]
        svc.update_subject_template(fields)
        std = DataStandard.query.filter_by(standard_type="subject_template").first()
        v1 = std.version
        # 再次更新
        svc.update_subject_template(fields)
        std = DataStandard.query.filter_by(standard_type="subject_template").first()
        # minor 段应递增
        minor_v1 = int(v1.split(".")[1])
        minor_v2 = int(std.version.split(".")[1])
        assert minor_v2 == minor_v1 + 1

    def test_get_subject_template_after_update(self, db_app, admin_user):
        """更新后 get_subject_template 应返回自定义字段"""
        svc = StandardService(operator_id=admin_user.id, operator_role="admin")
        custom = [
            {"field_key": "custom_field", "field_label": "自定义",
             "field_type": "textarea", "required": False, "enabled": True,
             "sort_order": 1, "placeholder": "", "options": []},
        ]
        svc.update_subject_template(custom)
        result = svc.get_subject_template()
        assert len(result["fields"]) == 1
        assert result["fields"][0]["field_key"]