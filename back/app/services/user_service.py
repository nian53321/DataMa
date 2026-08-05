# -*- coding: utf-8 -*-
"""用户管理服务

封装原 api/system.py 的 list_users / create_user / update_user / delete_user 逻辑。

业务规则：
- 仅管理员可增删改用户
- 用户名唯一性校验
- 角色必须存在于 RoleDef 表且启用
- 密码长度 ≥ 6
- 不能修改自己的角色 / 不能禁用自己 / 不能删除自己
- 创建/更新/删除自动留档快照 + 操作日志
"""
from typing import Optional

from app.extensions import db
from app.models import User
from app.models.role_menu import RoleDef
from app.services.base import (
    BaseService, NotFoundError, ValidationError, ConflictError,
)
from app.utils.audit import log_operation, snapshot_update, snapshot_delete
from app.utils.like_query import build_like_contains
from app.utils.response import paginate


class UserService(BaseService):
    """用户管理服务"""

    def list_users(self, page: int = 1, page_size: int = 20,
                   keyword: str = "", role: str = "") -> dict:
        """用户列表（支持按用户名/姓名/角色搜索）"""
        query = User.query
        if keyword:
            like = build_like_contains(keyword)
            query = query.filter(db.or_(
                User.username.like(like, escape="|"),
                User.real_name.like(like, escape="|"),
            ))
        if role:
            query = query.filter(User.role == role)
        query = query.order_by(User.created_at.desc())
        return paginate(query, page, page_size)

    def create_user(self, data: dict) -> User:
        """管理员创建用户"""
        username = (data.get("username") or "").strip()
        if not username:
            raise ValidationError("用户名不能为空")
        if User.query.filter_by(username=username).first():
            raise ConflictError("用户名已存在")

        role_key = (data.get("role") or "annotator").strip()
        role_def = RoleDef.query.filter_by(role_key=role_key).first()
        if not role_def:
            raise ValidationError(f"非法角色：{role_key}")
        if not role_def.is_active:
            raise ValidationError(f"角色已停用：{role_def.role_label}")

        password = (data.get("password") or "").strip()
        if not password:
            raise ValidationError("密码不能为空")
        if len(password) < 6:
            raise ValidationError("密码长度不能少于 6 位")

        user = User(
            username=username,
            real_name=data.get("real_name"),
            role=role_key,
            email=data.get("email"),
            phone=data.get("phone"),
            license_no=data.get("license_no"),
            is_active=data.get("is_active", True),
        )
        user.set_password(password)
        db.session.add(user)
        self.session.flush()  # 让 user.id 可用
        log_operation(
            "create", "user", user.id,
            f"创建用户 {username}（角色：{role_def.role_label}）",
            operator=self._operator_user(),
        )
        # 业务数据 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
        self._commit()
        return user

    def update_user(self, user_id: int, data: dict) -> User:
        """管理员更新用户信息（可改密码）

        - 不能修改自己的角色
        - 不能禁用自己的账号
        """
        user = self._get_or_404(User, user_id, "用户不存在")
        old_dict = user.to_dict()

        # 防止管理员修改自己的角色或禁用自己
        if user.id == self.operator_id:
            if data.get("role") and data["role"] != user.role:
                raise ValidationError("不能修改自己的角色")
            if "is_active" in data and not bool(data["is_active"]):
                raise ValidationError("不能禁用自己的账号")

        for f in ["real_name", "email", "phone", "license_no"]:
            if f in data:
                setattr(user, f, data[f])

        if data.get("role"):
            new_role = data["role"].strip()
            role_def = RoleDef.query.filter_by(role_key=new_role).first()
            if not role_def:
                raise ValidationError(f"非法角色：{new_role}")
            if not role_def.is_active:
                raise ValidationError(f"角色已停用：{role_def.role_label}")
            user.role = new_role

        if "is_active" in data:
            user.is_active = bool(data["is_active"])

        new_password = (data.get("password") or "").strip()
        if new_password:
            if len(new_password) < 6:
                raise ValidationError("新密码长度不能少于 6 位")
            user.set_password(new_password)

        snapshot_update("user", user, old_dict, user.to_dict(),
                        operator=self._operator_user())
        log_operation("update", "user", user.id, f"更新用户 {user.username}",
                      operator=self._operator_user())
        # 业务数据 + 快照 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
        self._commit()
        return user

    def delete_user(self, user_id: int) -> str:
        """管理员删除用户（不能删除自己）

        外键依赖校验：删除前检查该用户是否仍作为标注员/复核医生/操作员关联到
        标注任务、标注记录、标注版本、数据版本等。若有依赖，禁止删除，
        避免外键约束错误或留下孤儿数据。
        """
        user = self._get_or_404(User, user_id, "用户不存在")
        if user.id == self.operator_id:
            raise ValidationError("不能删除当前登录用户")

        # 外键依赖检查：用户作为标注员/复核医生仍关联到活跃任务时禁止删除
        from app.models.annotation import (
            AnnotationTask, Annotation, AnnotationVersion,
        )
        from app.models.data import DataVersion

        # 1. 标注任务：annotator_id / annotator2_id / reviewer_id
        active_task_count = AnnotationTask.query.filter(
            db.or_(
                AnnotationTask.annotator_id == user_id,
                AnnotationTask.annotator2_id == user_id,
                AnnotationTask.reviewer_id == user_id,
            ),
            AnnotationTask.status.in_(["pending", "pre_annotated", "assigning",
                                      "annotating", "annotated", "reviewing",
                                      "rejected"]),
        ).count()
        if active_task_count > 0:
            raise ValidationError(
                f"该用户仍有 {active_task_count} 个未完成的标注/复核任务，"
                "请先重新分配任务（更换标注员/复核医生）后再删除"
            )

        # 2. 数据版本：该用户操作过的数据版本
        version_count = DataVersion.query.filter_by(operator_id=user_id).count()
        if version_count > 0:
            raise ValidationError(
                f"该用户操作过 {version_count} 条数据版本记录，"
                "为保留审计追溯，无法直接删除，建议改为禁用账号"
            )

        username = user.username
        snapshot_delete("user", user, f"删除用户 {username}",
                        operator=self._operator_user())
        log_operation("delete", "user", user_id, f"删除用户 {username}",
                      operator=self._operator_user())
        db.session.delete(user)
        self._commit()
        return username

    def get_user(self, user_id: int) -> Optional[User]:
        """按 ID 查询用户（供 menus/加密等场景使用）"""
        return User.query.get(user_id)