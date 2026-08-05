# -*- coding: utf-8 -*-
"""RoleService 单元测试

覆盖角色与菜单权限业务规则：
- 创建：role_key 格式校验、admin 保留、菜单至少一个
- 更新：admin 不可停用
- 删除：admin 不可删、系统角色不可删、有用户不可删
- 菜单授权：admin 不可改、至少保留一个
"""
import pytest

from app.models import User
from app.models.role_menu import RoleDef, RoleMenu, get_role_menus, ALL_MENU_KEYS
from app.services import RoleService, ValidationError, NotFoundError, ConflictError


class TestRoleServiceCreate:
    """create_role_def 业务规则"""

    def test_create_role_success(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        result = svc.create_role_def({
            "role_key": "reviewer_a", "role_label": "复核员A",
            "menus": ["dashboard", "annotation"],
        })
        assert result["role_key"] == "reviewer_a"
        assert result["role_label"] == "复核员A"
        assert "dashboard" in result["menus"]

    def test_create_role_invalid_key_format(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="角色标识仅允许"):
            svc.create_role_def({
                "role_key": "x",  # 1 位太短
                "role_label": "X",
                "menus": ["dashboard"],
            })

    def test_create_role_admin_reserved(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="admin 为系统保留"):
            svc.create_role_def({
                "role_key": "admin", "role_label": "管理员",
                "menus": ["dashboard"],
            })

    def test_create_role_duplicate(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        svc.create_role_def({
            "role_key": "dup_role", "role_label": "重复角色",
            "menus": ["dashboard"],
        })
        with pytest.raises(ConflictError, match="角色标识已存在"):
            svc.create_role_def({
                "role_key": "dup_role", "role_label": "再次创建",
                "menus": ["dashboard"],
            })

    def test_create_role_empty_menus(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="至少需要授权一个菜单"):
            svc.create_role_def({
                "role_key": "nomenu", "role_label": "无菜单角色",
                "menus": [],
            })


class TestRoleServiceDelete:
    """delete_role_def 业务规则"""

    def test_delete_role_admin_forbidden(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="管理员角色不可删除"):
            svc.delete_role_def("admin")

    def test_delete_system_role_forbidden(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="系统内置角色"):
            svc.delete_role_def("annotator")  # 系统内置角色

    def test_delete_role_with_users_forbidden(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        svc.create_role_def({
            "role_key": "hasusers", "role_label": "有用户角色",
            "menus": ["dashboard"],
        })
        # 给该角色分配一个用户
        from app.services import UserService
        user_svc = UserService(operator_id=admin_user.id, operator_role="admin")
        user_svc.create_user({
            "username": "u_role", "password": "secret123", "role": "hasusers",
        })
        with pytest.raises(ValidationError, match="请先调整用户角色"):
            svc.delete_role_def("hasusers")

    def test_delete_role_success(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        svc.create_role_def({
            "role_key": "todelete", "role_label": "待删除",
            "menus": ["dashboard"],
        })
        label = svc.delete_role_def("todelete")
        assert label == "待删除"
        assert RoleDef.query.filter_by(role_key="todelete").first() is None


class TestRoleServiceUpdateMenus:
    """update_role_menus 业务规则"""

    def test_update_admin_menus_forbidden(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="管理员权限不可修改"):
            svc.update_role_menus("admin", {"menus": ["dashboard"]})

    def test_update_role_menus_at_least_one(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        svc.create_role_def({
            "role_key": "updm", "role_label": "测试菜单更新",
            "menus": ["dashboard"],
        })
        with pytest.raises(ValidationError, match="至少需要保留一个菜单"):
            svc.update_role_menus("updm", {"menus": []})

    def test_update_role_menus_success(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        svc.create_role_def({
            "role_key": "updm2", "role_label": "测试更新",
            "menus": ["dashboard"],
        })
        result = svc.update_role_menus("updm2", {
            "menus": ["dashboard", "annotation", "label"],
        })
        assert set(result["menus"]) == {"dashboard", "annotation", "label"}


class TestRoleServiceList:
    """list_role_menus / list_roles_simple 业务规则"""

    def test_list_role_menus_includes_system_roles(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_role_menus()
        role_keys = [r["role_key"] for r in result["roles"]]
        # 5 个系统内置角色都应存在
        assert "admin" in role_keys
        assert "annotator" in role_keys
        assert "doctor" in role_keys

    def test_list_role_menus_admin_has_all_menus(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_role_menus()
        admin_role = next(r for r in result["roles"] if r["role_key"] == "admin")
        assert set(admin_role["menus"]) == set(ALL_MENU_KEYS)
        assert admin_role["is_admin"] is True

    def test_list_roles_simple_only_active(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_roles_simple(only_active=True)
        # 所有返回的角色都应是启用的
        from app.models.role_menu import RoleDef
        for r in result:
            rd = RoleDef.query.filter_by(role_key=r["role_key"]).first()
            assert rd.is_active is True

    def test_list_menus_for_user(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        result = svc.list_menus(admin_user.id)
        assert result["my_role"] == "admin"
        assert set(result["my_menus"]) == set(ALL_MENU_KEYS)
        assert "menus" in result
        assert "role_labels" in result


class TestRoleServiceUpdateInfo:
    """update_role_info 业务规则"""

    def test_update_role_info_success(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        svc.create_role_def({
            "role_key": "info_upd", "role_label": "原名",
            "menus": ["dashboard"],
        })
        role_def = svc.update_role_info("info_upd", {
            "role_label": "新名", "description": "更新后的描述",
        })
        assert role_def.role_label == "新名"
        assert role_def.description == "更新后的描述"

    def test_update_admin_cannot_deactivate(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="管理员角色不可停用"):
            svc.update_role_info("admin", {"is_active": False})

    def test_update_role_info_not_found(self, db_app, admin_user):
        svc = RoleService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="角色不存在"):
            svc.update_role_info("nonexistent_role", {"role_label": "x"})
