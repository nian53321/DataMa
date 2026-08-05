# -*- coding: utf-8 -*-
"""审计与快照公共工具：抽取自 data/annotation/system 三个 API 模块的重复逻辑。
统一职责：
  - 获取当前登录用户对象
  - 记录操作日志（best-effort，失败不阻塞主流程）
  - 修改/删除前留档快照（best-effort）
"""
from flask import request, has_request_context
from flask_jwt_extended import get_jwt_identity

from app.extensions import db
from app.models.data_snapshot import save_snapshot, diff_summary_for
from app.models.operation_log import OperationLog
from app.models.user import User


def get_current_user():
    """获取当前登录用户对象（基于 JWT identity）"""
    try:
        uid = int(get_jwt_identity())
        return User.query.get(uid)
    except (ValueError, TypeError):
        return None


def current_role():
    """获取当前用户角色字符串（用于脱敏判断，admin 不脱敏）"""
    u = get_current_user()
    return u.role if u and u.role else None


def log_operation(action, target_type, target_id, detail="", operator=None):
    """记录操作日志（best-effort，失败不阻塞主流程）

    Args:
        action: 操作类型 create/update/delete/upload/...
        target_type: 目标对象类型 subject/asset/annotation_task/user/...
        target_id: 目标对象 ID（支持字符串拼接的多个 ID）
        detail: 操作详情描述
        operator: 显式指定的操作人 User 对象；不传则从 JWT 解析
    """
    try:
        u = operator if operator is not None else get_current_user()
        username = u.username if u else "anonymous"
        role = u.role if u and u.role else None
        ip = request.remote_addr if has_request_context() else None
        log = OperationLog(
            username=username,
            role=role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip=ip,
        )
        db.session.add(log)
    except Exception:
        # 审计日志失败不应影响主流程
        pass


def snapshot_update(model_type, obj, old_dict, new_dict, summary_prefix="", operator=None):
    """修改前留档（best-effort，失败不阻塞主流程）

    Args:
        operator: 显式指定的操作人 User 对象；不传则从 JWT 解析（兼容旧 API 调用）
    """
    try:
        op = operator if operator is not None else get_current_user()
        summary = diff_summary_for(model_type, old_dict, new_dict)
        if summary_prefix:
            summary = f"{summary_prefix}{('；' + summary) if summary else ''}"
        save_snapshot(model_type, obj.id, old_dict, "update", op, summary)
    except Exception:
        pass


def snapshot_delete(model_type, obj, summary="", operator=None):
    """删除前留档（best-effort）

    Args:
        operator: 显式指定的操作人 User 对象；不传则从 JWT 解析（兼容旧 API 调用）
    """
    try:
        op = operator if operator is not None else get_current_user()
        save_snapshot(model_type, obj.id, obj.to_dict(), "delete", op, summary or None)
    except Exception:
        pass
