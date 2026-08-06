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
# Orbbec 深度摄像头（彩色/深度/IR 预览 + 录制 + 入库）
orbbec_bp = Blueprint("orbbec", __name__, url_prefix="/api/orbbec")
# Intel RealSense 深度摄像头（D455f，pyrealsense2，容器内直连 USB）
realsense_bp = Blueprint("realsense", __name__, url_prefix="/api/realsense")
# 媒体资源短期签名 URL
media_bp = Blueprint("media", __name__, url_prefix="/api/media")

# 引入路由模块以注册视图
from app.api import auth, data, annotation, visualization, system, dashboard, orbbec, realsense, media  # noqa: E402,F401

ALL_BLUEPRINTS = [auth_bp, data_bp, annotation_bp, visualization_bp, system_bp, dashboard_bp, orbbec_bp, realsense_bp, media_bp]
