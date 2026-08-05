# -*- coding: utf-8 -*-
"""业务服务层基础设施

提供：
- 业务异常类（ServiceError 体系）：service 层抛出，由全局 errorhandler 统一转 HTTP
- BaseService 基类：封装通用事务边界、按 ID 查询、操作员上下文

设计原则：
1. service 不依赖 Flask request/jwt，operator 上下文通过参数显式传入
2. service 抛业务异常而非返回 (None, msg) 元组，调用方代码更扁平
3. 事务由 service 内部管理（commit/rollback），路由层不再手动 commit
"""
from typing import Type, TypeVar, Optional

from app.extensions import db

T = TypeVar("T")


# ==================== 业务异常 ====================

class ServiceError(Exception):
    """业务异常基类，携带 HTTP 状态码

    所有 service 层抛出的异常都应继承自此类。
    app/__init__.py 中注册全局 errorhandler 统一转为 JSON 响应。
    """
    def __init__(self, message: str, code: int = 422):
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self):
        return self.message


class ValidationError(ServiceError):
    """参数校验失败（422）"""
    def __init__(self, message: str = "参数校验失败"):
        super().__init__(message, 422)


class NotFoundError(ServiceError):
    """资源不存在（404）"""
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, 404)


class PermissionDeniedError(ServiceError):
    """权限不足（403）

    命名加 Denied 后缀避免与 Python 内置 PermissionError 冲突。
    """
    def __init__(self, message: str = "无权限执行该操作"):
        super().__init__(message, 403)


class ConflictError(ServiceError):
    """资源冲突（409），如唯一性校验失败"""
    def __init__(self, message: str = "资源已存在"):
        super().__init__(message, 409)


class OperationNotAllowedError(ServiceError):
    """操作不允许（422），如状态机流转非法"""
    def __init__(self, message: str = "当前状态不允许该操作"):
        super().__init__(message, 422)


# ==================== BaseService ====================

class BaseService:
    """所有 service 的基类

    提供：
    - self.session：SQLAlchemy session（默认 db.session，可注入便于测试）
    - self._commit()：提交事务（service 内部使用）
    - self._get_or_404()：按 ID 查询，不存在抛 NotFoundError
    - self._operator：操作员上下文（id + role + user 对象）

    使用示例：
        class UserService(BaseService):
            def create_user(self, data: dict) -> User:
                if not data.get("username"):
                    raise ValidationError("用户名不能为空")
                if User.query.filter_by(username=data["username"]).first():
                    raise ConflictError("用户名已存在")
                user = User(...)
                self.session.add(user)
                self._commit()
                return user

    路由层调用：
        @system_bp.route("/users", methods=["POST"])
        @role_required(Role.ADMIN)
        def create_user():
            data = request.get_json(silent=True) or {}
            user = user_service.create_user(data)
            return success(user.to_dict(), message="创建成功", code=201)
    """

    def __init__(self, operator_id: Optional[int] = None,
                 operator_role: Optional[str] = None,
                 session=None):
        """
        :param operator_id: 当前操作员用户 ID（来自 jwt identity）
        :param operator_role: 当前操作员角色 key
        :param session: SQLAlchemy session（测试时可注入）
        """
        self.session = session if session is not None else db.session
        self.operator_id = operator_id
        self.operator_role = operator_role

    def _commit(self):
        """提交当前事务"""
        self.session.commit()

    def _get_or_404(self, model: Type[T], obj_id: int, message: str = "资源不存在") -> T:
        """按主键查询，不存在抛 NotFoundError

        :param model: SQLAlchemy 模型类
        :param obj_id: 主键 ID
        :param message: 不存在时的错误消息
        """
        obj = model.query.get(obj_id)
        if obj is None:
            raise NotFoundError(message)
        return obj

    def _require_admin(self, message: str = "仅管理员可执行该操作"):
        """校验当前操作员是否为管理员，否则抛 PermissionDeniedError"""
        if self.operator_role != "admin":
            raise PermissionDeniedError(message)

    def _is_admin(self) -> bool:
        """当前操作员是否为管理员"""
        return self.operator_role == "admin"

    def _operator_user(self):
        """获取操作员 User 对象（供 audit 日志参数使用）

        - 若未注入 operator_id 则返回 None
        - 若用户已删除也返回 None（不抛异常，避免日志失败阻塞主流程）
        """
        if not self.operator_id:
            return None
        from app.models.user import User
        return User.query.get(self.operator_id)
