# -*- coding: utf-8 -*-
"""角色与菜单权限服务

封装原 api/system.py 的角色菜单管理逻辑。

业务规则：
- admin 角色始终拥有全部菜单（不可修改、不可删除、不可停用）
- 自定义角色 key 仅允许字母数字下划线，长度 2-32
- admin 为保留 key，不可创建同名角色
- 每个角色至少保留一个菜单
- 系统内置角色不可删除
- 删除角色前需确认无用户使用
- 角色 CRUD 与菜单修改均需记录操作日志（target_type='role_def' 或 'role_menu'）
"""
import string

from app.extensions import db
from app.models import User
from app.models.role_menu import (
    MENU_DEFINITIONS, ALL_MENU_KEYS,
    RoleDef, RoleMenu,
    get_role_menus, set_role_menus,
)
from app.services.base import (
    BaseService, NotFoundError, ValidationError, ConflictError,
)
from app.utils.audit import log_operation


class RoleService(BaseService):
    """角色与菜单权限服务"""

    def list_menus(self, user_id: int) -> dict:
        """返回系统菜单定义 + 当前用户可访问的菜单 key 列表"""
        from app.models import User as _User
        user = _User.query.get(user_id)
        if not user:
            raise NotFoundError("用户不存在")
        role_key = user.role
        # 动态构建 role_labels（含自定义角色）
        role_labels = {rd.role_key: rd.role_label for rd in RoleDef.query.all()}
        return {
            "menus": MENU_DEFINITIONS,
            "role_labels": role_labels,
            "my_menus": get_role_menus(role_key),
            "my_role": role_key,
        }

    def list_role_menus(self) -> dict:
        """返回所有角色的菜单授权配置（管理员查看）

        数据源：RoleDef 表（含系统内置角色 + 管理员自定义角色）
        """
        role_defs = RoleDef.query.order_by(
            RoleDef.sort_order.asc(), RoleDef.id.asc()
        ).all()
        # 一次 group_by 聚合各角色用户数，避免逐角色 COUNT 查询（N → 1 条 SQL）
        role_keys = [rd.role_key for rd in role_defs]
        user_count_rows = (
            db.session.query(User.role, db.func.count(User.id))
            .filter(User.role.in_(role_keys))
            .group_by(User.role)
            .all()
        )
        user_count_map = dict(user_count_rows)
        result = []
        for rd in role_defs:
            result.append({
                "role_key": rd.role_key,
                "role_label": rd.role_label,
                "description": rd.description or "",
                "is_system": bool(rd.is_system),
                "is_active": bool(rd.is_active),
                "sort_order": rd.sort_order or 0,
                "menus": get_role_menus(rd.role_key),
                "is_admin": rd.role_key == "admin",
                "user_count": user_count_map.get(rd.role_key, 0),
            })
        return {
            "roles": result,
            "menu_definitions": MENU_DEFINITIONS,
            "all_menu_keys": ALL_MENU_KEYS,
        }

    def list_roles_simple(self, only_active: bool = True) -> list:
        """返回启用的角色列表（供下拉选择使用，所有登录用户可访问）"""
        query = RoleDef.query
        if only_active:
            query = query.filter_by(is_active=True)
        rows = query.order_by(RoleDef.sort_order.asc(), RoleDef.id.asc()).all()
        return [{
            "role_key": r.role_key,
            "role_label": r.role_label,
            "is_system": bool(r.is_system),
        } for r in rows]

    def create_role_def(self, data: dict) -> dict:
        """创建自定义角色（含菜单授权）

        - role_key 仅允许字母数字下划线，长度 2-32
        - admin 为保留 key
        - 至少授权一个菜单
        """
        role_key = (data.get("role_key") or "").strip().lower()
        role_label = (data.get("role_label") or "").strip()
        if not role_key:
            raise ValidationError("角色标识不能为空")
        if not role_label:
            raise ValidationError("角色名称不能为空")
        # key 格式校验：仅允许字母/数字/下划线，长度 2-32
        if not all(c.isalnum() or c == "_" for c in role_key) \
                or len(role_key) < 2 or len(role_key) > 32:
            raise ValidationError("角色标识仅允许字母数字下划线，长度 2-32")
        if role_key == "admin":
            raise ValidationError("admin 为系统保留角色标识，不可使用")
        if RoleDef.query.filter_by(role_key=role_key).first():
            raise ConflictError(f"角色标识已存在：{role_key}")

        menu_keys = data.get("menus", [])
        if not isinstance(menu_keys, list):
            raise ValidationError("menus 必须为数组")
        menu_keys = [k for k in menu_keys if k in ALL_MENU_KEYS]
        if not menu_keys:
            raise ValidationError("至少需要授权一个菜单")

        # 计算排序值（追加到末尾）
        max_sort = db.session.query(db.func.max(RoleDef.sort_order)).scalar() or 0
        role_def = RoleDef(
            role_key=role_key,
            role_label=role_label,
            description=(data.get("description") or "").strip() or None,
            is_system=False,
            is_active=bool(data.get("is_active", True)),
            sort_order=max_sort + 1,
        )
        db.session.add(role_def)
        db.session.flush()  # 拿到 id
        for mk in menu_keys:
            db.session.add(RoleMenu(role_key=role_key, menu_key=mk))
        log_operation(
            "create", "role_def", role_key,
            f"创建角色【{role_label}】（key={role_key}），授权菜单：{', '.join(menu_keys)}",
            operator=self._operator_user(),
        )
        # 角色 + 菜单授权 + 日志一次性原子提交
        self._commit()
        return {
            "role_key": role_key,
            "role_label": role_label,
            "menus": get_role_menus(role_key),
        }

    def update_role_info(self, role_key: str, data: dict) -> RoleDef:
        """更新角色基本信息（label/description/is_active/sort_order）

        - 不影响菜单授权
        - admin 角色不允许停用
        """
        role_def = RoleDef.query.filter_by(role_key=role_key).first()
        if not role_def:
            raise NotFoundError("角色不存在")
        old_label = role_def.role_label
        if data.get("role_label") is not None:
            new_label = data["role_label"].strip()
            if not new_label:
                raise ValidationError("角色名称不能为空")
            role_def.role_label = new_label
        if "description" in data:
            role_def.description = (data.get("description") or "").strip() or None
        if "is_active" in data:
            new_active = bool(data["is_active"])
            if not new_active and role_key == "admin":
                raise ValidationError("管理员角色不可停用")
            role_def.is_active = new_active
        if "sort_order" in data:
            try:
                role_def.sort_order = int(data["sort_order"])
            except (TypeError, ValueError):
                pass
        log_operation(
            "update", "role_def", role_key,
            f"更新角色信息【{old_label}→{role_def.role_label}】",
            operator=self._operator_user(),
        )
        # 角色 + 日志一次性原子提交
        self._commit()
        return role_def

    def delete_role_def(self, role_key: str) -> str:
        """删除自定义角色

        - admin 不可删除
        - 系统内置角色不可删除
        - 角色下有用户不可删除
        - 同时清理 RoleMenu 关联记录
        """
        if role_key == "admin":
            raise ValidationError("管理员角色不可删除")
        role_def = RoleDef.query.filter_by(role_key=role_key).first()
        if not role_def:
            raise NotFoundError("角色不存在")
        if role_def.is_system:
            raise ValidationError(f"系统内置角色【{role_def.role_label}】不可删除")
        user_count = User.query.filter_by(role=role_key).count()
        if user_count > 0:
            raise ValidationError(
                f"该角色下还有 {user_count} 个用户，请先调整用户角色后再删除"
            )
        # 清理 RoleMenu 关联
        RoleMenu.query.filter_by(role_key=role_key).delete(synchronize_session=False)
        label = role_def.role_label
        db.session.delete(role_def)
        log_operation(
            "delete", "role_def", role_key,
            f"删除角色【{label}】（key={role_key}）",
            operator=self._operator_user(),
        )
        self._commit()
        return label

    def update_role_menus(self, role_key: str, data: dict) -> dict:
        """更新某角色的菜单授权

        - admin 角色不可修改（始终保持全部）
        - 至少保留一个菜单
        - 过滤非法 key
        """
        role_def = RoleDef.query.filter_by(role_key=role_key).first()
        if not role_def:
            raise NotFoundError("角色不存在")
        if role_key == "admin":
            raise ValidationError("管理员权限不可修改（始终保持全部）")
        menu_keys = data.get("menus", [])
        if not isinstance(menu_keys, list):
            raise ValidationError("menus 必须为数组")
        # 过滤非法 key
        menu_keys = [k for k in menu_keys if k in ALL_MENU_KEYS]
        if not menu_keys:
            raise ValidationError("至少需要保留一个菜单权限")
        set_role_menus(role_key, menu_keys)

        log_operation(
            "update", "role_menu", role_key,
            f"更新角色【{role_def.role_label}】菜单权限：{', '.join(menu_keys)}",
            operator=self._operator_user(),
        )
        self._commit()
        return {"role_key": role_key, "menus": get_role_menus(role_key)}