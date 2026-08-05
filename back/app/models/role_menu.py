# -*- coding: utf-8 -*-
"""角色菜单权限模型：管理员可动态配置每个角色可访问的菜单"""
from datetime import datetime

from app.extensions import db
from app.utils.time import to_local_str


# 系统菜单定义（key 与前端路由 meta.menuKey 一一对应）
MENU_DEFINITIONS = [
    {"key": "dashboard", "title": "工作台", "path": "/dashboard", "icon": "Odometer"},
    {"key": "management", "title": "数据管理", "path": "/management", "icon": "Coin"},
    {"key": "data", "title": "数据清洗及标准化", "path": "/data", "icon": "Brush"},
    {"key": "annotation", "title": "数据标注", "path": "/annotation", "icon": "EditPen"},
    {"key": "label", "title": "标签管理", "path": "/label", "icon": "PriceTag"},
    {"key": "visualization", "title": "数据对齐与可视化", "path": "/visualization", "icon": "DataLine"},
    {"key": "system", "title": "系统管理", "path": "/system", "icon": "Setting"},
    {"key": "operation_log", "title": "操作日志", "path": "/operation-log", "icon": "Document"},
]

# 默认角色菜单授权（沿用历史硬编码配置，首次启动时写入数据库）
# operation_log 仅 admin 可见（与原 system 页面操作日志按钮 v-if="isAdmin" 对齐）
DEFAULT_ROLE_MENUS = {
    "admin": ["dashboard", "management", "data", "annotation", "label", "visualization", "system", "operation_log"],
    "doctor": ["dashboard", "management", "annotation", "label", "visualization"],
    "annotator": ["dashboard", "management", "annotation", "label", "visualization"],
    "nurse": ["dashboard", "management", "visualization"],
    "engineer": ["dashboard", "management", "data", "visualization"],
}

# 角色中文标签
ROLE_LABELS = {
    "admin": "管理员",
    "doctor": "医生",
    "annotator": "标注员",
    "nurse": "护理人员",
    "engineer": "数据工程师",
}

ALL_MENU_KEYS = [m["key"] for m in MENU_DEFINITIONS]

# 系统内置角色 key（不可删除）
SYSTEM_ROLE_KEYS = set(DEFAULT_ROLE_MENUS.keys())


class RoleDef(db.Model):
    """角色定义：支持系统内置角色与管理员自定义角色"""
    __tablename__ = "role_defs"
    id = db.Column(db.Integer, primary_key=True)
    role_key = db.Column(db.String(32), unique=True, nullable=False, index=True, comment="角色 key")
    role_label = db.Column(db.String(64), nullable=False, comment="角色中文名")
    description = db.Column(db.String(255), nullable=True, comment="角色说明")
    is_system = db.Column(db.Boolean, default=False, nullable=False, comment="是否系统内置角色（不可删除）")
    is_active = db.Column(db.Boolean, default=True, nullable=False, comment="是否启用")
    sort_order = db.Column(db.Integer, default=0, comment="排序权重，小在前")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "role_key": self.role_key,
            "role_label": self.role_label,
            "description": self.description or "",
            "is_system": bool(self.is_system),
            "is_active": bool(self.is_active),
            "sort_order": self.sort_order or 0,
            "created_at": to_local_str(self.created_at),
            "updated_at": to_local_str(self.updated_at),
        }


class RoleMenu(db.Model):
    """角色菜单授权：记录每个角色可访问的菜单项"""
    __tablename__ = "role_menus"
    id = db.Column(db.Integer, primary_key=True)
    role_key = db.Column(db.String(32), nullable=False, index=True, comment="角色 key")
    menu_key = db.Column(db.String(32), nullable=False, comment="菜单 key")

    __table_args__ = (
        db.UniqueConstraint("role_key", "menu_key", name="uq_role_menu"),
    )

    def to_dict(self):
        return {"role_key": self.role_key, "menu_key": self.menu_key}


def get_role_label(role_key):
    """获取角色中文名：优先查 RoleDef 表，回退到 ROLE_LABELS"""
    rd = RoleDef.query.filter_by(role_key=role_key).first()
    if rd:
        return rd.role_label
    return ROLE_LABELS.get(role_key, role_key)


def get_role_menus(role_key):
    """获取某角色被授权的菜单 key 列表。
    admin 角色始终拥有全部菜单，避免管理员被锁死。
    """
    if role_key == "admin":
        return list(ALL_MENU_KEYS)
    rows = RoleMenu.query.filter_by(role_key=role_key).all()
    keys = [r.menu_key for r in rows]
    # 兜底：若数据库无配置则回退到默认
    if not keys:
        keys = list(DEFAULT_ROLE_MENUS.get(role_key, ["dashboard"]))
    return keys


def set_role_menus(role_key, menu_keys):
    """覆盖式更新某角色的菜单授权

    事务安全：delete + insert 包裹在 try-except 中，任意步骤异常均 rollback，
    避免出现"原授权已删除但新授权未写入"导致角色被锁死的情况。
    """
    if role_key == "admin":
        # 管理员不可修改，始终保持全部
        menu_keys = list(ALL_MENU_KEYS)
    try:
        RoleMenu.query.filter_by(role_key=role_key).delete(synchronize_session=False)
        for mk in menu_keys:
            if mk in ALL_MENU_KEYS:
                db.session.add(RoleMenu(role_key=role_key, menu_key=mk))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def init_default_role_menus():
    """首次启动时把默认授权写入数据库（已存在则跳过）"""
    try:
        for role_key, keys in DEFAULT_ROLE_MENUS.items():
            existing = RoleMenu.query.filter_by(role_key=role_key).count()
            if existing:
                continue
            for mk in keys:
                db.session.add(RoleMenu(role_key=role_key, menu_key=mk))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def init_default_role_defs():
    """首次启动时把 5 个系统内置角色写入 RoleDef 表（已存在则跳过）"""
    sort_map = {"admin": 1, "doctor": 2, "annotator": 3, "nurse": 4, "engineer": 5}
    try:
        for role_key, label in ROLE_LABELS.items():
            existing = RoleDef.query.filter_by(role_key=role_key).first()
            if existing:
                continue
            db.session.add(RoleDef(
                role_key=role_key,
                role_label=label,
                description=f"系统内置角色：{label}",
                is_system=True,
                is_active=True,
                sort_order=sort_map.get(role_key, 99),
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
