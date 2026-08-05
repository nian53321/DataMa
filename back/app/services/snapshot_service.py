# -*- coding: utf-8 -*-
"""快照与操作日志服务

封装原 api/data.py 的 list_operation_logs / list_snapshots / get_snapshot /
rollback_snapshot_api 逻辑。

业务规则：
- 操作日志权限隔离：admin 可查全部；其他角色仅查自己
- 快照权限隔离：admin 可查全部；其他角色仅查自己创建的
- 快照内容脱敏：非 admin 用户查看快照时自动脱敏敏感字段
- 回滚：仅管理员，回滚前自动留档当前状态
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func

from app.extensions import db
from app.models import Role, DataSnapshot
from app.models.data_snapshot import rollback_snapshot
from app.models.operation_log import OperationLog
from app.models.user import User
from app.services.base import (
    BaseService, NotFoundError, PermissionDeniedError, ValidationError,
)
from app.utils.audit import log_operation
from app.utils.desensitize import desensitize_dict, desensitize_list
from app.utils.like_query import build_like_contains
from app.utils.response import paginate

# 北京时间时区偏移量（与 app.utils.time._CN_TZ 保持一致）
_CN_TZ_DELTA = timedelta(hours=8)


class SnapshotService(BaseService):
    """快照与操作日志服务"""

    def list_operation_logs(self, page: int = 1, page_size: int = 20,
                            username: str = "", action: str = "",
                            target_type: str = "") -> dict:
        """操作日志列表

        权限隔离：
        - admin：可查看全部日志，并按 username/action/target_type 筛选
        - 其他角色：仅可查看自己的操作日志（满足自审需求），筛选用户名参数自动忽略
        """
        query = OperationLog.query
        # 权限隔离：非 admin 仅能查看自己的日志
        current_user = self._operator_user()
        is_admin = current_user and current_user.role == Role.ADMIN.value
        if not is_admin:
            query = query.filter(OperationLog.username == (current_user.username if current_user else ""))
        elif username:
            query = query.filter(OperationLog.username.like(build_like_contains(username), escape="|"))
        if action:
            query = query.filter(OperationLog.action == action)
        if target_type:
            query = query.filter(OperationLog.target_type == target_type)
        query = query.order_by(OperationLog.created_at.desc())
        return paginate(query, page, page_size)

    def get_operation_log_stats(self, username: str = "", days: int = 7) -> dict:
        """操作日志统计（用于可视化）

        权限隔离：
        - admin：可查看全部日志统计；支持按 username 筛选
        - 其他角色：仅统计自己的操作日志（username 参数自动忽略）

        Args:
            username: 仅 admin 生效，按用户名模糊筛选
            days: 趋势图天数（默认 7，限制 1-90）

        Returns:
            total: 全部日志总数
            today: 今日（北京时间 00:00 至今）操作数
            active_users: 活跃用户数（不同 username 计数）
            last_n_days: 最近 N 天每日操作数 [{date, count}]（按北京时间分组）
            action_distribution: 操作类型分布 [{action, count}]
            target_distribution: 对象类型分布 [{target_type, count}]
            role_distribution: 角色分布 [{role, count}]
            top_users: Top 10 活跃用户 [{username, count}]
            days: 实际统计的天数
            is_admin: 当前是否为管理员视角
        """
        # 参数规范化
        if days < 1:
            days = 7
        if days > 90:
            days = 90

        # 基础查询 + 权限隔离（与 list_operation_logs 保持一致）
        query = OperationLog.query
        current_user = self._operator_user()
        is_admin = current_user and current_user.role == Role.ADMIN.value
        if not is_admin:
            own_username = current_user.username if current_user else ""
            query = query.filter(OperationLog.username == own_username)
        elif username:
            query = query.filter(
                OperationLog.username.like(build_like_contains(username), escape="|")
            )

        # 总数
        total = query.count()

        # 今日（北京时间）操作数
        # 北京时间今日 00:00 对应的 UTC 时刻
        now_cn = datetime.utcnow() + _CN_TZ_DELTA
        today_cn_start_utc = datetime(now_cn.year, now_cn.month, now_cn.day) - _CN_TZ_DELTA
        today = query.filter(OperationLog.created_at >= today_cn_start_utc).count()

        # 活跃用户数
        active_users = query.with_entities(OperationLog.username).distinct().count()

        # 最近 N 天每日操作数（按北京时间分组）
        n_days_ago_cn_start_utc = today_cn_start_utc - timedelta(days=days - 1)
        recent_logs = query.filter(
            OperationLog.created_at >= n_days_ago_cn_start_utc
        ).with_entities(OperationLog.created_at).all()

        daily_map = {}
        for i in range(days):
            d = (now_cn - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_map[d] = 0
        for (created_at,) in recent_logs:
            if not created_at:
                continue
            d = (created_at + _CN_TZ_DELTA).strftime("%Y-%m-%d")
            if d in daily_map:
                daily_map[d] += 1
        last_n_days = [{"date": d, "count": daily_map[d]} for d in sorted(daily_map.keys())]

        # 操作类型分布（全量 group_by）
        action_rows = query.with_entities(
            OperationLog.action, func.count(OperationLog.id)
        ).group_by(OperationLog.action).all()
        action_distribution = [
            {"action": a or "unknown", "count": c} for a, c in action_rows
        ]
        action_distribution.sort(key=lambda x: x["count"], reverse=True)

        # 对象类型分布
        target_rows = query.with_entities(
            OperationLog.target_type, func.count(OperationLog.id)
        ).group_by(OperationLog.target_type).all()
        target_distribution = [
            {"target_type": t or "unknown", "count": c} for t, c in target_rows
        ]
        target_distribution.sort(key=lambda x: x["count"], reverse=True)

        # 角色分布
        role_rows = query.with_entities(
            OperationLog.role, func.count(OperationLog.id)
        ).group_by(OperationLog.role).all()
        role_distribution = [
            {"role": r or "unknown", "count": c} for r, c in role_rows
        ]
        role_distribution.sort(key=lambda x: x["count"], reverse=True)

        # Top 10 活跃用户
        user_rows = query.with_entities(
            OperationLog.username, func.count(OperationLog.id)
        ).group_by(OperationLog.username) \
         .order_by(func.count(OperationLog.id).desc()).limit(10).all()
        top_users = [
            {"username": u or "anonymous", "count": c} for u, c in user_rows
        ]

        return {
            "total": total,
            "today": today,
            "active_users": active_users,
            "last_n_days": last_n_days,
            "action_distribution": action_distribution,
            "target_distribution": target_distribution,
            "role_distribution": role_distribution,
            "top_users": top_users,
            "days": days,
            "is_admin": bool(is_admin),
        }

    def list_snapshots(self, page: int = 1, page_size: int = 20,
                       model_type: str = "", model_id: Optional[int] = None,
                       action: str = "") -> dict:
        """数据快照列表

        权限隔离：admin 可查看全部；其他角色仅能查看自己创建的快照（operator_id == 自己）
        非 admin 用户查看快照内容时会自动脱敏敏感字段。
        """
        query = DataSnapshot.query
        # 权限隔离：非 admin 仅查自己创建的快照
        current_user = self._operator_user()
        is_admin = current_user and current_user.role == Role.ADMIN.value
        if not is_admin:
            query = query.filter(DataSnapshot.operator_id == self.operator_id)
        if model_type:
            query = query.filter_by(model_type=model_type)
        if model_id:
            query = query.filter_by(model_id=model_id)
        if action:
            query = query.filter_by(action=action)
        query = query.order_by(DataSnapshot.created_at.desc())
        result = paginate(query, page, page_size)
        # 非 admin 用户的快照内容脱敏（snapshot 字段可能含 phone/id_card 等敏感字段）
        if not is_admin and current_user:
            for item in result.get("items", []):
                snap_data = item.get("snapshot")
                if isinstance(snap_data, dict):
                    desensitize_dict(snap_data, current_user.role)
        return result

    def get_snapshot(self, snapshot_id: int):
        """获取单条快照详情

        权限隔离：非 admin 仅能查看自己创建的快照；非 admin 查看时快照内容自动脱敏。
        """
        snap = self._get_or_404(DataSnapshot, snapshot_id, "快照不存在")
        current_user = self._operator_user()
        is_admin = current_user and current_user.role == Role.ADMIN.value
        if not is_admin and snap.operator_id != self.operator_id:
            raise PermissionDeniedError("无权限查看该快照")
        data = snap.to_dict()
        # 非 admin 用户的快照内容脱敏
        if not is_admin and current_user and isinstance(data.get("snapshot"), dict):
            desensitize_dict(data["snapshot"], current_user.role)
        return data

    def rollback_snapshot(self, snapshot_id: int):
        """回滚到指定快照版本（仅管理员，回滚前会自动留档当前状态）

        事务边界：rollback_snapshot() 仅 flush 不 commit，本方法控制最终事务提交，
        保证"回滚数据 + 留档快照 + 操作日志"同生共死；任何步骤异常均 rollback。

        Raises:
            PermissionDeniedError: 非管理员
            ValidationError: 回滚失败（快照不存在 / 不支持回滚的模型类型）
        """
        self._require_admin("仅管理员可回滚快照")
        operator = self._operator_user()
        try:
            result, err = rollback_snapshot(snapshot_id, operator)
            if err:
                self.session.rollback()
                raise ValidationError(err)
            log_operation("update", "snapshot", snapshot_id,
                          f"回滚 {result['model_type']}#{result['model_id']} 到 v{result['restored_from_version']}",
                          operator=operator)
            # 回滚数据 + 留档快照 + 操作日志一次性原子提交
            self._commit()
            return result
        except Exception:
            self.session.rollback()
            raise

    def _operator_user(self):
        """获取操作员 User 对象"""
        if not self.operator_id:
            return None
        return User.query.get(self.operator_id)
