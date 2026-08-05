# -*- coding: utf-8 -*-
"""仪表盘统计接口"""
from flask import request
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func

from app.api import dashboard_bp
from app.models import (
    Subject, DataAsset, AnnotationTask, AnnotationStatus, DataType, DataLayer,
    Role,
)
from app.utils.response import success
from app.utils.audit import get_current_user, current_role


@dashboard_bp.route("/stats", methods=["GET"])
@jwt_required()
def stats():
    """综合统计：受试者/数据资产/标注任务概览 + 分布 + 最近动态

    权限分级：
    - admin 角色：可查看全部用户操作动态，支持 username 参数筛选特定用户
    - 其他角色：仅能查看自己的操作动态（username 参数被忽略）
    """
    user = get_current_user()
    role = current_role()
    is_admin = role == Role.ADMIN.value if isinstance(role, str) else role == Role.ADMIN
    # admin 主动按账号筛选（仅 admin 生效；非 admin 强制限定自己）
    filter_username = request.args.get("username", "").strip() if is_admin else ""

    subject_total = Subject.query.count()
    asset_total = DataAsset.query.count()
    task_total = AnnotationTask.query.count()

    # 标注任务按状态分布
    status_rows = (
        AnnotationTask.query
        .with_entities(AnnotationTask.status, func.count(AnnotationTask.id))
        .group_by(AnnotationTask.status)
        .all()
    )
    status_dist = {s.value if s else "unknown": c for s, c in status_rows}

    # 数据资产按模态类型分布
    type_rows = (
        DataAsset.query
        .with_entities(DataAsset.data_type, func.count(DataAsset.id))
        .group_by(DataAsset.data_type)
        .all()
    )
    type_dist = {t.value if t else "unknown": c for t, c in type_rows}

    # 数据湖分层分布
    layer_rows = (
        DataAsset.query
        .with_entities(DataAsset.layer, func.count(DataAsset.id))
        .group_by(DataAsset.layer)
        .all()
    )
    layer_dist = {l.value if l else "unknown": c for l, c in layer_rows}

    # 数据资产状态分布（用于清洗看板）
    asset_status_rows = (
        DataAsset.query
        .with_entities(DataAsset.status, func.count(DataAsset.id))
        .group_by(DataAsset.status)
        .all()
    )
    asset_status_dist = {s or "unknown": c for s, c in asset_status_rows}

    # 待办：待预标注 + 待复核
    pending_pre = AnnotationTask.query.filter_by(
        status=AnnotationStatus.PENDING
    ).count()
    pending_review = AnnotationTask.query.filter_by(
        status=AnnotationStatus.ANNOTATED
    ).count()

    # 最近受试者
    recent_subjects = [
        s.to_dict() for s in
        Subject.query.order_by(Subject.created_at.desc()).limit(5).all()
    ]

    # 最近标注任务（含数据资产与用户名）
    from app.models.user import User
    recent_tasks_query = (
        AnnotationTask.query
        .order_by(AnnotationTask.created_at.desc())
        .limit(10)
        .all()
    )
    user_ids = set()
    asset_ids = set()
    for t in recent_tasks_query:
        if t.annotator_id: user_ids.add(t.annotator_id)
        if t.reviewer_id: user_ids.add(t.reviewer_id)
        if t.data_asset_id: asset_ids.add(t.data_asset_id)
    user_map = {u.id: (u.real_name or u.username) for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    asset_map = {a.id: a for a in DataAsset.query.filter(DataAsset.id.in_(asset_ids)).all()} if asset_ids else {}
    recent_tasks = [{
        "id": t.id,
        "status": t.status.value if t.status else None,
        "data_asset_id": t.data_asset_id,
        "data_type": asset_map.get(t.data_asset_id).data_type.value if asset_map.get(t.data_asset_id) else None,
        "file_name": asset_map.get(t.data_asset_id).file_name if asset_map.get(t.data_asset_id) else None,
        "annotator_name": user_map.get(t.annotator_id) if t.annotator_id else None,
        "reviewer_name": user_map.get(t.reviewer_id) if t.reviewer_id else None,
        "created_at": t.created_at,
    } for t in recent_tasks_query]
    # 时间字段统一格式化
    from app.utils.time import to_local_str
    for t in recent_tasks:
        t["created_at"] = to_local_str(t["created_at"])

    # 最近操作日志（按权限分级过滤）
    from app.models.operation_log import OperationLog
    log_query = OperationLog.query
    if is_admin:
        # admin 可按账号筛选；未传 username 则看全部
        if filter_username:
            log_query = log_query.filter(OperationLog.username == filter_username)
    else:
        # 非 admin 仅看自己的操作（按 username 匹配）
        own_username = user.username if user else None
        if own_username:
            log_query = log_query.filter(OperationLog.username == own_username)
        else:
            # 无法识别身份时返回空集，避免泄露他人数据
            log_query = log_query.filter(False)
    recent_logs = [
        {
            "id": log.id,
            "username": log.username,
            "role": log.role,
            "action": log.action,
            "target_type": log.target_type,
            "detail": log.detail,
            "created_at": to_local_str(log.created_at),
        }
        for log in log_query
        .order_by(OperationLog.created_at.desc())
        .limit(10)
        .all()
    ]
    # 顺便告诉前端当前权限视图与可筛选用户列表（仅 admin 有意义）
    admin_user_list = []
    if is_admin:
        admin_user_list = [
            {"username": u.username, "real_name": u.real_name or u.username}
            for u in User.query.order_by(User.username).all()
        ]

    # 合格率
    qualified = DataAsset.query.filter_by(is_qualified=True).count()

    return success({
        "subject_total": subject_total,
        "asset_total": asset_total,
        "task_total": task_total,
        "pending_pre_annotate": pending_pre,
        "pending_review": pending_review,
        "qualified_rate": round(qualified / asset_total, 4) if asset_total else 0.0,
        "status_distribution": status_dist,
        "type_distribution": type_dist,
        "layer_distribution": layer_dist,
        "asset_status_distribution": asset_status_dist,
        "recent_subjects": recent_subjects,
        "recent_tasks": recent_tasks,
        "recent_logs": recent_logs,
        "logs_is_admin": is_admin,
        "logs_filter_username": filter_username,
        "logs_user_list": admin_user_list,
    })
