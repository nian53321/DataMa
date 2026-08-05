# -*- coding: utf-8 -*-
"""UserService 单元测试

覆盖用户管理业务规则（不启动 Flask 路由，直接调用 service）：
- 创建：用户名唯一性、角色合法性、密码长度
- 更新：禁止改自身角色 / 禁用自身
- 删除：禁止删除自己
"""
import pytest

from app.models import User
from app.models.role_menu import RoleDef
from app.services import UserService, ValidationError, ConflictError, NotFoundError


class TestUserServiceCreate:
    """create_user 业务规则"""

    def test_create_user_success(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        user = svc.create_user({
            "username": "newuser1", "password": "secret123",
            "real_name": "测试", "role": "annotator",
        })
        assert user.id is not None
        assert user.username == "newuser1"
        assert user.role == "annotator"
        assert user.check_password("secret123")

    def test_create_user_empty_username_raises(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="用户名不能为空"):
            svc.create_user({"username": "", "password": "secret123"})

    def test_create_user_duplicate_raises(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        svc.create_user({"username": "dup", "password": "secret123"})
        with pytest.raises(ConflictError, match="用户名已存在"):
            svc.create_user({"username": "dup", "password": "secret123"})

    def test_create_user_invalid_role_raises(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="非法角色"):
            svc.create_user({
                "username": "u2", "password": "secret123", "role": "nonexistent",
            })

    def test_create_user_short_password_raises(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="密码长度"):
            svc.create_user({"username": "u3", "password": "123"})


class TestUserServiceUpdate:
    """update_user 业务规则"""

    def test_update_user_success(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        user = svc.create_user({"username": "upd1", "password": "secret123"})
        updated = svc.update_user(user.id, {"real_name": "新名字", "phone": "13800138000"})
        assert updated.real_name == "新名字"
        assert updated.phone == "13800138000"

    def test_update_user_not_found(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="不存在"):
            svc.update_user(99999, {"real_name": "x"})

    def test_update_user_cannot_change_own_role(self, db_app, admin_user):
        """管理员不能修改自己的角色"""
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="不能修改自己的角色"):
            svc.update_user(admin_user.id, {"role": "annotator"})

    def test_update_user_cannot_disable_self(self, db_app, admin_user):
        """管理员不能禁用自己"""
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="不能禁用自己的账号"):
            svc.update_user(admin_user.id, {"is_active": False})

    def test_update_user_change_password(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        user = svc.create_user({"username": "pw1", "password": "secret123"})
        svc.update_user(user.id, {"password": "newpass456"})
        # 重新查询
        refreshed = User.query.get(user.id)
        assert refreshed.check_password("newpass456")
        assert not refreshed.check_password("secret123")


class TestUserServiceDelete:
    """delete_user 业务规则"""

    def test_delete_user_success(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        user = svc.create_user({"username": "del1", "password": "secret123"})
        username = svc.delete_user(user.id)
        assert username == "del1"
        assert User.query.get(user.id) is None

    def test_delete_user_not_found(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(NotFoundError, match="不存在"):
            svc.delete_user(99999)

    def test_delete_user_cannot_delete_self(self, db_app, admin_user):
        """管理员不能删除自己"""
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="不能删除当前登录用户"):
            svc.delete_user(admin_user.id)


class TestUserServiceList:
    """list_users 业务规则"""

    def test_list_users_basic(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        svc.create_user({"username": "list1", "password": "secret123", "role": "annotator"})
        svc.create_user({"username": "list2", "password": "secret123", "role": "annotator"})
        result = svc.list_users()
        # admin + annotator(测试夹具) + list1 + list2 = 4
        assert result["total"] == 4

    def test_list_users_filter_by_role(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        svc.create_user({"username": "r1", "password": "secret123", "role": "annotator"})
        svc.create_user({"username": "r2", "password": "secret123", "role": "annotator"})
        result = svc.list_users(role="annotator")
        # 夹具的 annotator + r1 + r2 = 3
        assert result["total"] == 3
        assert all(item["role"] == "annotator" for item in result["items"])

    def test_list_users_search_by_keyword(self, db_app, admin_user):
        svc = UserService(operator_id=admin_user.id, operator_role="admin")
        svc.create_user({"username": "kwtest1", "password": "secret123",
                         "real_name": "张三"})
        svc.create_user({"username": "other", "password": "secret123",
                         "real_name": "李四"})
        result = svc.list_users(keyword="kwtest1")
        assert result["total"] == 1
        assert result["items"][0]["username"] == "kwtest1"