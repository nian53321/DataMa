# -*- coding: utf-8 -*-
"""Flask 应用工厂"""
import os
import logging
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from app.config import get_config
from app.extensions import db, migrate, cors, jwt

# 模块级日志器（用于 create_app 外部定义的函数）
logger = logging.getLogger(__name__)


def _ensure_schema_upgrade(database):
    """开发环境自动升级：为已存在的表补齐后续新增字段（不影响已有数据）
    仅做加列/扩列操作，不做删改，安全可逆。
    """
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(database.engine)
        # operation_logs 表补 role 字段
        if "operation_logs" in inspector.get_table_names():
            cols = [c["name"] for c in inspector.get_columns("operation_logs")]
            if "role" not in cols:
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE operation_logs ADD COLUMN role VARCHAR(32) NULL"))
                    conn.commit()
            # target_id 由 INT 改为 VARCHAR(128)，支持多个 ID 拼接
            target_col = next((c for c in inspector.get_columns("operation_logs") if c["name"] == "target_id"), None)
            if target_col and not str(target_col["type"]).upper().startswith("VARCHAR"):
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE operation_logs MODIFY COLUMN target_id VARCHAR(128) NULL"))
                    conn.commit()
        # users.role 由 ENUM 改为 VARCHAR(32)，支持自定义角色 key
        if "users" in inspector.get_table_names():
            role_col = next((c for c in inspector.get_columns("users") if c["name"] == "role"), None)
            if role_col and "VARCHAR" not in str(role_col["type"]).upper():
                with database.engine.connect() as conn:
                    # MySQL: ALTER MODIFY；SQLite 不支持 MODIFY（但 SQLite 列类型宽松，无需处理）
                    try:
                        conn.execute(text(
                            "ALTER TABLE users MODIFY COLUMN role VARCHAR(32) NOT NULL DEFAULT 'annotator'"
                        ))
                        conn.commit()
                    except Exception:
                        # 非 MySQL 数据库（如 SQLite）忽略；列类型本身宽松，不影响查询
                        conn.rollback()
            # 数据迁移：旧数据存的是枚举名（ADMIN/ANNOTATOR/...），需统一为小写枚举值（admin/annotator/...）
            # 注意：MySQL 默认字符串比较大小写不敏感，需用 BINARY 强制区分大小写
            try:
                with database.engine.connect() as conn:
                    conn.execute(text(
                        "UPDATE users SET role = LOWER(role) "
                        "WHERE role IS NOT NULL AND BINARY role <> BINARY LOWER(role)"
                    ))
                    conn.commit()
            except Exception as e:
                logger.warning("schema 升级失败: %s", e)
        # annotation_tasks 表补 group_id / remark 字段
        if "annotation_tasks" in inspector.get_table_names():
            ann_cols = [c["name"] for c in inspector.get_columns("annotation_tasks")]
            if "group_id" not in ann_cols:
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE annotation_tasks ADD COLUMN group_id INT NULL"))
                    conn.commit()
            if "remark" not in ann_cols:
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE annotation_tasks ADD COLUMN remark VARCHAR(256) NULL"))
                    conn.commit()
        # scan_configs 表补 collection_batch / collection_scene 字段（默认批次/场景）
        if "scan_configs" in inspector.get_table_names():
            sc_cols = [c["name"] for c in inspector.get_columns("scan_configs")]
            if "collection_batch" not in sc_cols:
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE scan_configs ADD COLUMN collection_batch VARCHAR(64) NULL"))
                    conn.commit()
            if "collection_scene" not in sc_cols:
                with database.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE scan_configs ADD COLUMN collection_scene VARCHAR(64) NULL"))
                    conn.commit()
        # 受试者模板：补齐 collection_batch / collection_scene 字段（已有模板升级）
        # 旧版默认模板没有这两个字段，或类型为 input，统一升级为 autocomplete
        if "data_standards" in inspector.get_table_names():
            import json as _json
            with database.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT id, schema_json FROM data_standards "
                    "WHERE standard_type = 'subject_template' AND is_active = 1"
                )).fetchall()
                for row in rows:
                    try:
                        raw = row[1]
                        if isinstance(raw, str):
                            schema = _json.loads(raw) if raw else {}
                        elif isinstance(raw, dict):
                            schema = raw
                        else:
                            continue
                        fields = schema.get("fields") if isinstance(schema, dict) else None
                        if not isinstance(fields, list):
                            continue
                        changed = False
                        existing_keys = {f.get("field_key") for f in fields if isinstance(f, dict)}
                        # 升级类型：input → autocomplete
                        for f in fields:
                            if isinstance(f, dict) and f.get("field_key") in (
                                "collection_batch", "collection_scene"
                            ):
                                if f.get("field_type") != "autocomplete":
                                    f["field_type"] = "autocomplete"
                                    f["placeholder"] = (
                                        "如 BATCH_001（可选/可输入新值）"
                                        if f.get("field_key") == "collection_batch"
                                        else "如 SCENE_A（可选/可输入新值）"
                                    )
                                    changed = True
                        # 缺失字段则注入默认值（sort_order 排在 remark 之前）
                        if "collection_batch" not in existing_keys:
                            fields.append({
                                "field_key": "collection_batch",
                                "field_label": "采集批次",
                                "field_type": "autocomplete",
                                "required": False,
                                "enabled": True,
                                "sort_order": 9,
                                "placeholder": "如 BATCH_001（可选/可输入新值）",
                                "options": [],
                            })
                            changed = True
                        if "collection_scene" not in existing_keys:
                            fields.append({
                                "field_key": "collection_scene",
                                "field_label": "场景代码",
                                "field_type": "autocomplete",
                                "required": False,
                                "enabled": True,
                                "sort_order": 10,
                                "placeholder": "如 SCENE_A（可选/可输入新值）",
                                "options": [],
                            })
                            changed = True
                        if changed:
                            schema["fields"] = fields
                            conn.execute(text(
                                "UPDATE data_standards SET schema_json = :sj WHERE id = :id"
                            ), {"sj": _json.dumps(schema, ensure_ascii=False), "id": row[0]})
                    except Exception as e:
                        logger.warning("subject_template 升级失败 id=%s: %s", row[0], e)
                conn.commit()
    except Exception as e:
        logger.warning("schema 升级失败: %s", e)


def create_app(env=None):
    """创建并配置 Flask 应用"""
    app = Flask(__name__)
    app.config.from_object(get_config(env))

    # 日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 文件日志：使用 RotatingFileHandler 按大小轮转
    log_dir = app.config.get("LOG_DIR")
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        file_handler.setLevel(app.config.get("LOG_LEVEL", logging.INFO))
        app.logger.addHandler(file_handler)
    app.logger.setLevel(app.config.get("LOG_LEVEL", logging.INFO))

    # 初始化扩展
    db.init_app(app)
    migrate.init_app(app, db)
    cors_origins = app.config.get("CORS_ORIGINS", ["*"])
    cors.init_app(app, resources={r"/api/*": {"origins": cors_origins}})
    jwt.init_app(app)

    # CORS 配置告警：默认 * 时记录警告（不阻止启动，满足前后端跨服务器部署约束）
    if "*" in cors_origins:
        app.logger.warning(
            "CORS_ORIGINS 配置为 '*'，任意域名可跨域访问 API。"
            "生产环境建议通过环境变量 CORS_ORIGINS 配置具体前端域名（逗号分隔）。"
        )

    # 安全响应头（after_request 钩子，所有响应均附加）
    from app.utils.security_headers import apply_security_headers
    app.after_request(apply_security_headers)

    # 注册模型（便于 migrate 识别）
    from app import models  # noqa: F401

    # 开发环境：自动创建缺失的表与字段（不影响已有数据）
    with app.app_context():
        db.create_all()
        _ensure_schema_upgrade(db)
        # 初始化角色菜单默认授权
        from app.models.role_menu import init_default_role_menus, init_default_role_defs
        try:
            init_default_role_defs()
        except Exception as e:
            app.logger.warning("角色定义初始化失败: %s", e)
        try:
            init_default_role_menus()
        except Exception as e:
            app.logger.warning("角色菜单初始化失败: %s", e)
        # 初始化默认标签库
        from app.models.label import init_default_labels
        try:
            init_default_labels()
        except Exception as e:
            app.logger.warning("默认标签初始化失败: %s", e)
        # 初始化默认脱敏配置
        from app.models.desensitize import init_default_desensitize
        try:
            init_default_desensitize()
        except Exception as e:
            app.logger.warning("默认脱敏配置初始化失败: %s", e)
        # 初始化默认管理员账号（空库启动时自动创建，已存在则跳过）
        from app.models.user import init_default_admin
        try:
            created = init_default_admin()
            if created:
                app.logger.info("已初始化默认管理员账号（admin/admin123，请尽快修改密码）")
        except Exception as e:
            app.logger.warning("默认管理员初始化失败: %s", e)

    # 注册蓝图
    from app.api import ALL_BLUEPRINTS
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)

    # 确保数据湖目录存在
    for sub in ("raw", "cleaned", "feature", "annotation"):
        os.makedirs(os.path.join(app.config["DATA_LAKE_DIR"], sub), exist_ok=True)

    # 初始化文件加密主密钥（启用加密时自动生成 master.key）
    if app.config.get("ENCRYPT_DATA_LAKE", True):
        try:
            from app.utils.crypto import get_master_key
            with app.app_context():
                get_master_key()
            app.logger.info("数据湖文件加密已启用，主密钥: %s", app.config["MASTER_KEY_PATH"])
        except Exception as e:
            app.logger.warning("主密钥初始化失败，文件加密不可用: %s", e)

    # 初始化默认命名规范（首次启动时注入）
    with app.app_context():
        try:
            from app.models.standard import DataStandard
            from app.utils.naming import DEFAULT_NAMING_STANDARDS
            for item in DEFAULT_NAMING_STANDARDS:
                exists = DataStandard.query.filter_by(
                    name=item["name"], standard_type=item["standard_type"]
                ).first()
                if not exists:
                    std = DataStandard(
                        name=item["name"],
                        standard_type=item["standard_type"],
                        data_type=item.get("data_type"),
                        version=item.get("version", "1.0.0"),
                        schema_json=item.get("schema_json"),
                        description=item.get("description"),
                        is_active=True,
                    )
                    db.session.add(std)
                    db.session.commit()
                    app.logger.info("已初始化默认规范: %s", item["name"])
        except Exception as e:
            app.logger.warning("默认命名规范初始化失败: %s", e)

    # 启动受试者文件夹自动扫描调度器
    try:
        from app.utils.scanner import start_scan_scheduler
        start_scan_scheduler(app)
        app.logger.info("受试者文件夹自动扫描调度器已启动")
    except Exception as e:
        app.logger.warning("扫描调度器启动失败: %s", e)

    # 健康检查
    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "data-management-backend"})

    # 全局异常处理
    @app.errorhandler(HTTPException)
    def handle_http_error(e):
        if e.code == 413:
            message = "上传文件过大（超过 512MB 限制），请拆分后重试"
        else:
            message = e.description
        return jsonify({"code": e.code, "message": message, "data": None}), e.code

    # 业务异常：service 层抛 ServiceError 子类，统一转 JSON 响应
    from app.services import ServiceError
    @app.errorhandler(ServiceError)
    def handle_service_error(e):
        return jsonify({"code": e.code, "message": e.message, "data": None}), e.code

    @app.errorhandler(Exception)
    def handle_error(e):
        app.logger.exception("未捕获异常")
        return jsonify({"code": 500, "message": "服务器内部错误", "data": None}), 500

    @app.shell_context_processor
    def shell_context():
        from app.models import (
            User, Subject, DataAsset, AnnotationTask, DataStandard,
        )
        return {
            "db": db,
            "User": User,
            "Subject": Subject,
            "DataAsset": DataAsset,
            "AnnotationTask": AnnotationTask,
            "DataStandard": DataStandard,
        }

    return app

