# -*- coding: utf-8 -*-
"""用户与角色模型"""
import enum
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db
from app.utils.time import to_local_str


class Role(enum.Enum):
    """用户角色（系统内置）：对应三级标注流程及管理职能
    说明：User.role 已改为 String(32)，可存储自定义角色 key；
    此枚举保留用于 role_required 装饰器与系统角色常量比较。
    """
    ADMIN = "admin"              # 项目管理员
    DOCTOR = "doctor"            # 医生（复核）
    ANNOTATOR = "annotator"      # 标注工程师
    NURSE = "nurse"             # 护理人员（查阅）
    ENGINEER = "engineer"       # 数据工程师


class User(db.Model):
    """系统用户"""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True, comment="登录名")
    password_hash = db.Column(db.String(256), nullable=False)
    real_name = db.Column(db.String(64), comment="真实姓名")
    role = db.Column(db.String(32), nullable=False, default="annotator", index=True, comment="角色 key")
    email = db.Column(db.String(128), index=True)
    phone = db.Column(db.String(20))
    # 医生执业资质
    license_no = db.Column(db.String(64), comment="医师执业证号")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "real_name": self.real_name,
            "role": self.role,
            "email": self.email,
            "phone": self.phone,
            "license_no": self.license_no,
            "is_active": self.is_active,
            "last_login_at": to_local_str(self.last_login_at),
            "created_at": to_local_str(self.created_at),
        }


def init_default_admin():
    """空库启动时初始化默认管理员账号（已存在 admin 则跳过，不覆盖）

    默认账号：admin / admin123
    可通过环境变量 INIT_ADMIN_USERNAME / INIT_ADMIN_PASSWORD 覆盖（仅在新库时生效）
    """
    import os
    from app.extensions import db

    username = os.getenv("INIT_ADMIN_USERNAME", "admin")
    password = os.getenv("INIT_ADMIN_PASSWORD", "admin123")

    existing = User.query.filter_by(username=username).first()
    if existing:
        return False  # 已存在，跳过

    admin = User(
        username=username,
        real_name="管理员",
        role=Role.ADMIN.value,
        is_active=True,
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return True
