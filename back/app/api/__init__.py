# -*- coding: utf-8 -*-
"""API 蓝图注册"""
from flask import Blueprint

# 认证与用户
auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
# 数据管理（接入、清洗、标准化、版本）
data_bp = Blueprint("data", __name__, url_prefix="/api/data")
# 标注管理（预标注、人工标注、复核）
annotation_bp = Blueprint("annotation", __name__, url_prefix="/api/annotation")
# 可视化（对齐、展示）
visualization_bp = Blueprint("visualization", __name__, url_prefix="/api/visualization")
# 系统管理（用户、权限、规范、脱敏）
system_bp = Blueprint("system", __name__, url_prefix="/api/system")
# 仪表盘统计
dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")

# 引入路由模块以注册视图
from app.api import auth, data, annotation, visualization, system, dashboard  # noqa: E402,F401

ALL_BLUEPRINTS = [auth_bp, data_bp, annotation_bp, visualization_bp, system_bp, dashboard_bp]
