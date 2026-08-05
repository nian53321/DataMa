# -*- coding: utf-8 -*-
"""pytest 公共 fixtures：提供测试用 Flask app 与 client

设计说明：
- 不走 create_app 工厂，避免触发数据库连接、数据湖目录创建、默认数据初始化等副作用。
- 仅构造一个最小化 Flask app，提供 crypto 等模块所需的 app context 与配置项
  （MASTER_KEY_PATH / BASE_DIR）。每个测试获得独立的临时目录，互不干扰。
- 工具类纯函数测试（desensitize.mask_value、naming.apply_naming_standard 等）
  无需 app context，可不使用本 fixture。
- service/API 集成测试使用 db_app fixture：基于 SQLite in-memory，独立数据库，
  仅注册必要模型与蓝图，不触发默认数据初始化。
"""
import os

import pytest
from flask import Flask


@pytest.fixture
def app(tmp_path):
    """创建一个最小化的 Flask app 实例（每个测试独立临时目录）

    提供：
      - app.app_context() 供依赖 current_app 的模块（如 crypto）使用
      - MASTER_KEY_PATH 指向临时目录，避免污染项目真实 master.key
    """
    app = Flask(__name__)
    app.config["BASE_DIR"] = str(tmp_path)
    app.config["MASTER_KEY_PATH"] = str(tmp_path / "master.key")
    app.config["ENCRYPT_DATA_LAKE"] = True
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """提供 Flask test client"""
    return app.test_client()


# ==================== Service / API 集成测试夹具 ====================

@pytest.fixture
def db_app(tmp_path):
    """带 SQLite 内存数据库的 Flask app（用于 service 与 API 集成测试）

    特性：
    - 使用 SQLite in-memory，每个测试独立数据库（互不干扰）
    - 关闭加密（避免依赖 master.key）
    - 注册所有模型与 API 蓝图（路由可达）
    - 创建 admin/annotator 两个测试用户
    - 不触发 create_app 中的默认数据初始化（标签/脱敏/角色菜单等）

    Yields:
        Flask app（已进入 app_context，db.create_all() 已执行）
    """
    from flask import Flask
    from flask_jwt_extended import JWTManager
    from app.extensions import db as _db
    from app.api import ALL_BLUEPRINTS

    app = Flask(__name__)
    app.config.update({
        "BASE_DIR": str(tmp_path),
        "MASTER_KEY_PATH": str(tmp_path / "master.key"),
        "DATA_LAKE_DIR": str(tmp_path / "data_lake"),
        "ENCRYPT_DATA_LAKE": False,  # 测试不加密
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "JWT_SECRET_KEY": "test-secret-key-for-testing-only",
        "CORS_ORIGINS": ["*"],
    })

    # 初始化扩展
    _db.init_app(app)
    jwt = JWTManager(app)

    # 注册全局 ServiceError 异常处理器（与生产保持一致）
    from app.services import ServiceError
    from flask import jsonify

    @app.errorhandler(ServiceError)
    def handle_service_error(e):
        return jsonify({"code": e.code, "message": e.message, "data": None}), e.code

    # 注册所有 API 蓝图
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)

    # 创建数据湖目录
    for sub in ("raw", "cleaned", "feature", "annotation"):
        os.makedirs(os.path.join(app.config["DATA_LAKE_DIR"], sub), exist_ok=True)

    with app.app_context():
        _db.create_all()
        # 创建测试用户
        from app.models.user import User
        admin = User(username="admin", real_name="管理员", role="admin", is_active=True)
        admin.set_password("admin123")
        annotator = User(username="annotator", real_name="标注员", role="annotator", is_active=True)
        annotator.set_password("annotator123")
        _db.session.add_all([admin, annotator])
        _db.session.commit()
        # 初始化默认脱敏规则（测试脱敏行为需要）
        from app.models.desensitize import init_default_desensitize
        try:
            init_default_desensitize()
        except Exception:
            pass
        # 初始化默认角色定义与菜单授权（测试 UserService/RoleService 需要）
        from app.models.role_menu import init_default_role_defs, init_default_role_menus
        try:
            init_default_role_defs()
            init_default_role_menus()
        except Exception:
            pass
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def db_session(db_app):
    """数据库 session（service 单元测试用）"""
    from app.extensions import db
    return db.session


@pytest.fixture
def admin_user(db_app):
    """admin 测试用户"""
    from app.models.user import User
    return User.query.filter_by(username="admin").first()


@pytest.fixture
def annotator_user(db_app):
    """annotator 测试用户"""
    from app.models.user import User
    return User.query.filter_by(username="annotator").first()


@pytest.fixture
def admin_token(db_app, admin_user):
    """admin 的 JWT access token（API 集成测试用）"""
    from flask_jwt_extended import create_access_token
    # 附加 role claim，与生产 JWT 一致
    return create_access_token(
        identity=str(admin_user.id),
        additional_claims={"role": admin_user.role},
    )


@pytest.fixture
def annotator_token(db_app, annotator_user):
    """annotator 的 JWT access token"""
    from flask_jwt_extended import create_access_token
    return create_access_token(
        identity=str(annotator_user.id),
        additional_claims={"role": annotator_user.role},
    )


@pytest.fixture
def admin_client(db_app, admin_token):
    """带 admin JWT 的 test client（自动附加 Authorization 头）"""
    client = db_app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {admin_token}"
    return client


@pytest.fixture
def annotator_client(db_app, annotator_token):
    """带 annotator JWT 的 test client"""
    client = db_app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {annotator_token}"
    return client
