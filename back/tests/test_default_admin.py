# -*- coding: utf-8 -*-
"""测试默认管理员初始化（空库启动场景）"""
import pytest
from app.extensions import db
from app.models.user import User, init_default_admin


class TestInitDefaultAdmin:
    """init_default_admin 行为测试"""

    def test_create_admin_on_empty_db(self, db_app):
        """空库时应创建默认 admin 账号"""
        with db_app.app_context():
            # 清空所有用户，模拟空库
            User.query.delete()
            db.session.commit()
            assert User.query.count() == 0

            created = init_default_admin()
            assert created is True

            admin = User.query.filter_by(username="admin").first()
            assert admin is not None
            assert admin.role == "admin"
            assert admin.is_active is True
            assert admin.check_password("admin123") is True

    def test_skip_when_admin_exists(self, db_app):
        """已存在 admin 用户时应跳过，不覆盖密码"""
        with db_app.app_context():
            # db_app fixture 已预创建 admin/admin123
            admin = User.query.filter_by(username="admin").first()
            assert admin is not None
            original_hash = admin.password_hash

            created = init_default_admin()
            assert created is False

            # 密码未被覆盖
            admin = User.query.filter_by(username="admin").first()
            assert admin.password_hash == original_hash

    def test_custom_admin_via_env(self, db_app, monkeypatch):
        """通过环境变量自定义管理员账号"""
        monkeypatch.setenv("INIT_ADMIN_USERNAME", "superadmin")
        monkeypatch.setenv("INIT_ADMIN_PASSWORD", "secret456")

        with db_app.app_context():
            User.query.delete()
            db.session.commit()

            created = init_default_admin()
            assert created is True

            admin = User.query.filter_by(username="superadmin").first()
            assert admin is not None
            assert admin.check_password("secret456") is True
            assert admin.check_password("admin123") is False
            # 默认 admin 账号不应被创建
            assert User.query.filter_by(username="admin").first() is None
