# -*- coding: utf-8 -*-
"""认证接口：登录、当前用户、刷新token"""
from datetime import datetime
from flask import request, current_app
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)

from app.api import auth_bp
from app.extensions import db
from app.models import User
from app.models.operation_log import OperationLog
from app.models.role_menu import get_role_menus
from app.utils.response import success, fail
from app.utils.rate_limit import is_locked, record_failure, reset


def _record_login_log(action, username, user, ip, detail):
    """直接构造 OperationLog 记录（不依赖 log_operation 的 get_current_user，避免无 JWT 时异常）"""
    try:
        log = OperationLog(
            username=username or (user.username if user else "anonymous"),
            role=(user.role if user and user.role else None),
            action=action,
            target_type="user",
            target_id=str(user.id) if user else None,
            detail=detail,
            ip=ip,
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


@auth_bp.route("/login", methods=["POST"])
def login():
    """用户登录"""
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    client_ip = request.remote_addr

    if not username or not password:
        return fail("用户名或密码不能为空", 422)

    # 1. 先检查是否被锁定（按 username+ip 组合计数，不拒绝任何 IP）
    max_fails = current_app.config.get("LOGIN_MAX_FAILS", 5)
    lock_seconds = current_app.config.get("LOGIN_LOCK_SECONDS", 900)
    locked, remain = is_locked(username, client_ip)
    if locked:
        return fail(f"登录失败次数过多，已锁定，请 {remain} 秒后重试", 429)

    user = User.query.filter_by(username=username).first()
    # 2. 统一返回"用户名或密码错误"，避免用户名枚举
    if not user or not user.check_password(password):
        triggered, fails = record_failure(username, client_ip, max_fails, lock_seconds)
        _record_login_log(
            "login_fail", username, user, client_ip,
            f"登录失败（第 {fails} 次，用户名: {username}）",
        )
        if triggered:
            return fail(
                f"连续登录失败 {fails} 次，账号已锁定 {lock_seconds // 60} 分钟，请稍后重试",
                429,
            )
        return fail("用户名或密码错误", 401)
    if not user.is_active:
        return fail("账号已被禁用", 403)

    # 3. 登录成功：清计数、记录审计
    reset(username, client_ip)
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    _record_login_log(
        "login", username, user, client_ip,
        f"用户登录成功（IP: {client_ip}）",
    )

    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "username": user.username},
    )
    # 生成 refresh token，用于 access token 过期后换取新 token
    refresh_token = create_refresh_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "username": user.username},
    )
    # 返回当前用户可访问菜单 key 列表（基于角色配置）
    menus = get_role_menus(user.role)
    return success(
        {"token": token, "refresh_token": refresh_token, "user": user.to_dict(), "menus": menus},
        message="登录成功",
    )


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """使用 refresh token 换取新的 access token"""
    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if not user or not user.is_active:
        return fail("用户不存在或已被禁用", 401)
    token = create_access_token(
        identity=str(user.id),
        additional_claims={"role": user.role, "username": user.username},
    )
    return success({"token": token}, message="刷新成功")


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def current_user():
    """获取当前登录用户（含菜单权限）"""
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if not user:
        return fail("用户不存在", 404)
    data = user.to_dict()
    data["menus"] = get_role_menus(user.role)
    return success(data)
