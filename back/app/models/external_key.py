# -*- coding: utf-8 -*-
"""外部密钥模型：集中管理外部 AES-256-CBC 加密数据的 key+iv

应用场景：
- 外部采集设备（如 AgeCog）导出的加密文件使用固定 AES-256-CBC + PKCS7
- 同一来源/批次共用一组 key+iv，不同批次可能用不同密钥
- scanner 扫描到 .enc 文件时，依次尝试所有 is_active=True 的密钥解密
- 全部失败时，前端提示用户上传对应的外部密钥

字段说明：
- name: 密钥名称（唯一），如 "AgeCog-2026Q1"，便于识别
- description: 描述（来源/批次/用途）
- key_b64: Base64 编码的 32 字节 AES-256 key
- iv_b64: Base64 编码的 16 字节 IV
- key_fingerprint: 密钥指纹（sha256(key)[:32]），列表展示用，不泄露 key 本身
- is_active: 是否启用（禁用的密钥不参与解密尝试，但保留记录）
"""
import base64
import hashlib
from datetime import datetime

from app.extensions import db
from app.utils.time import to_local_str


def _compute_fingerprint(key_b64: str) -> str:
    """根据 Base64 key 计算指纹：sha256(decoded_key)[:32]"""
    key_bytes = base64.b64decode(key_b64)
    return hashlib.sha256(key_bytes).hexdigest()[:32]


class ExternalKey(db.Model):
    """外部 AES-256-CBC 密钥（多组管理）"""
    __tablename__ = "external_key"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False, index=True,
                     comment="密钥名称（唯一，便于识别）")
    description = db.Column(db.String(256), nullable=True, default="",
                            comment="描述：来源/批次/用途")
    key_b64 = db.Column(db.String(64), nullable=False,
                        comment="Base64 编码的 32 字节 AES key")
    iv_b64 = db.Column(db.String(32), nullable=False,
                       comment="Base64 编码的 16 字节 IV")
    key_fingerprint = db.Column(db.String(32), nullable=False,
                                comment="密钥指纹 sha256(key)[:32]")
    is_active = db.Column(db.Boolean, default=True, nullable=False,
                          comment="是否启用（禁用的密钥不参与解密）")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    def to_dict(self, include_secret: bool = False) -> dict:
        """序列化

        :param include_secret: True 时返回 key_b64/iv_b64（仅管理界面编辑时需要）
        """
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "key_fingerprint": self.key_fingerprint,
            "is_active": self.is_active,
            "created_at": to_local_str(self.created_at),
            "updated_at": to_local_str(self.updated_at),
        }
        if include_secret:
            data["key_b64"] = self.key_b64
            data["iv_b64"] = self.iv_b64
        return data

    @staticmethod
    def compute_fingerprint(key_b64: str) -> str:
        """公开静态方法：根据 Base64 key 计算指纹"""
        return _compute_fingerprint(key_b64)
