# -*- coding: utf-8 -*-
"""外部密钥管理服务

集中管理外部 AES-256-CBC 加密数据的 key+iv，支持多组密钥（不同来源/批次）。

业务规则：
- 所有操作仅限管理员（路由层 role_required 拦截）
- name 唯一，重名抛 ConflictError
- key_b64 解码后必须 32 字节，iv_b64 解码后必须 16 字节
- key_fingerprint 自动计算（sha256(key)[:32]），不接受外部传入
- 列表/详情接口不返回 key_b64/iv_b64 明文（仅编辑接口可显式请求）
- scanner 调用 get_active_keys_for_decrypt 获取候选密钥列表用于自动解密
- 提供 import_from_keyfile 从外部 密钥.txt 文件批量导入
- 所有变更操作记录操作日志（target_type='external_key'）

权限边界（与内部主密钥对齐）：
- 查看/管理仅 admin
- scanner 解密过程对其他角色透明
"""
import base64
import os
from typing import List, Tuple

from app.extensions import db
from app.models import ExternalKey
from app.services.base import (
    BaseService, ValidationError, NotFoundError, ConflictError,
)
from app.utils.audit import log_operation
from app.utils.crypto import (
    load_external_key, decrypt_external_bytes_auto,
    DEK_SIZE,
)


# 外部 IV 固定 16 字节（AES-CBC 块大小）
_EXTERNAL_IV_SIZE = 16


class ExternalKeyService(BaseService):
    """外部密钥管理服务"""

    def __init__(self, operator_id=None, operator_role=None, session=None,
                 data_lake_dir: str = ""):
        super().__init__(operator_id, operator_role, session)
        self.data_lake_dir = data_lake_dir

    # ==================== 校验 ====================

    @staticmethod
    def _validate_key_iv(key_b64: str, iv_b64: str) -> Tuple[bytes, bytes]:
        """校验并解码 key/iv，返回 (key_bytes, iv_bytes)

        :raises ValidationError: Base64 非法 / 长度异常
        """
        if not key_b64 or not iv_b64:
            raise ValidationError("key_b64 与 iv_b64 不能为空")
        try:
            key_bytes = base64.b64decode(key_b64)
        except Exception:
            raise ValidationError("key_b64 不是合法的 Base64 编码")
        try:
            iv_bytes = base64.b64decode(iv_b64)
        except Exception:
            raise ValidationError("iv_b64 不是合法的 Base64 编码")
        if len(key_bytes) != DEK_SIZE:
            raise ValidationError(
                f"AES Key 长度异常: {len(key_bytes)} 字节（期望 {DEK_SIZE}）"
            )
        if len(iv_bytes) != _EXTERNAL_IV_SIZE:
            raise ValidationError(
                f"AES IV 长度异常: {len(iv_bytes)} 字节（期望 {_EXTERNAL_IV_SIZE}）"
            )
        return key_bytes, iv_bytes

    # ==================== CRUD ====================

    def list_keys(self) -> List[dict]:
        """列出所有外部密钥（不返回 key/iv 明文）"""
        keys = ExternalKey.query.order_by(ExternalKey.created_at.desc()).all()
        return [k.to_dict() for k in keys]

    def get_key(self, key_id: int, include_secret: bool = False) -> dict:
        """获取单个外部密钥详情

        :param include_secret: True 时返回 key_b64/iv_b64（编辑界面用）
        """
        key = self._get_or_404(ExternalKey, key_id, "外部密钥不存在")
        return key.to_dict(include_secret=include_secret)

    def create_key(self, data: dict) -> ExternalKey:
        """创建外部密钥

        :param data: {name, description?, key_b64, iv_b64, is_active?}
        :raises ConflictError: name 已存在
        :raises ValidationError: key/iv 格式异常
        """
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("密钥名称不能为空")
        if len(name) > 64:
            raise ValidationError("密钥名称长度不能超过 64 字符")

        if ExternalKey.query.filter_by(name=name).first():
            raise ConflictError(f"密钥名称已存在: {name}")

        key_b64 = (data.get("key_b64") or "").strip()
        iv_b64 = (data.get("iv_b64") or "").strip()
        self._validate_key_iv(key_b64, iv_b64)

        fingerprint = ExternalKey.compute_fingerprint(key_b64)
        # 指纹去重（同一密钥不应重复存储）
        existing_fp = ExternalKey.query.filter_by(key_fingerprint=fingerprint).first()
        if existing_fp:
            raise ConflictError(
                f"密钥指纹已存在（与「{existing_fp.name}」相同），请勿重复添加"
            )

        key = ExternalKey(
            name=name,
            description=(data.get("description") or "").strip(),
            key_b64=key_b64,
            iv_b64=iv_b64,
            key_fingerprint=fingerprint,
            is_active=bool(data.get("is_active", True)),
        )
        self.session.add(key)
        self.session.flush()  # 让 key.id 可用
        log_operation(
            "create", "external_key", key.id,
            f"创建外部密钥「{name}」（指纹: {fingerprint[:17]}...）",
            operator=self._operator_user(),
        )
        # 业务数据 + 日志一次性原子提交
        self._commit()
        return key

    def update_key(self, key_id: int, data: dict) -> ExternalKey:
        """更新外部密钥（name/description/key/iv/is_active）

        :raises NotFoundError: 密钥不存在
        :raises ConflictError: name 已被其他密钥占用 / 指纹重复
        :raises ValidationError: key/iv 格式异常
        """
        key = self._get_or_404(ExternalKey, key_id, "外部密钥不存在")
        old_dict = key.to_dict()

        # name
        if "name" in data:
            new_name = (data["name"] or "").strip()
            if not new_name:
                raise ValidationError("密钥名称不能为空")
            if len(new_name) > 64:
                raise ValidationError("密钥名称长度不能超过 64 字符")
            if new_name != key.name:
                dup = ExternalKey.query.filter_by(name=new_name).first()
                if dup and dup.id != key.id:
                    raise ConflictError(f"密钥名称已存在: {new_name}")
                key.name = new_name

        if "description" in data:
            key.description = (data["description"] or "").strip()

        # key/iv 变更需重新校验并计算指纹
        new_key_b64 = (data.get("key_b64") or "").strip()
        new_iv_b64 = (data.get("iv_b64") or "").strip()
        if new_key_b64 or new_iv_b64:
            # 必须同时提供两者
            if not new_key_b64 or not new_iv_b64:
                raise ValidationError("更新密钥时 key_b64 与 iv_b64 必须同时提供")
            self._validate_key_iv(new_key_b64, new_iv_b64)
            new_fp = ExternalKey.compute_fingerprint(new_key_b64)
            # 指纹去重（排除自身）
            dup_fp = ExternalKey.query.filter_by(key_fingerprint=new_fp).first()
            if dup_fp and dup_fp.id != key.id:
                raise ConflictError(
                    f"密钥指纹已存在（与「{dup_fp.name}」相同），请勿重复添加"
                )
            key.key_b64 = new_key_b64
            key.iv_b64 = new_iv_b64
            key.key_fingerprint = new_fp

        if "is_active" in data:
            key.is_active = bool(data["is_active"])

        log_operation(
            "update", "external_key", key.id,
            f"更新外部密钥「{key.name}」",
            operator=self._operator_user(),
        )
        self._commit()
        return key

    def delete_key(self, key_id: int) -> str:
        """删除外部密钥（返回名称供前端提示）"""
        key = self._get_or_404(ExternalKey, key_id, "外部密钥不存在")
        name = key.name
        self.session.delete(key)
        log_operation(
            "delete", "external_key", key_id,
            f"删除外部密钥「{name}」",
            operator=self._operator_user(),
        )
        self._commit()
        return name

    # ==================== scanner 解密支持 ====================

    def get_active_keys_for_decrypt(self) -> List[Tuple[int, bytes, bytes]]:
        """获取所有启用状态的外部密钥，供 scanner 自动解密使用

        返回 [(key_id, key_bytes, iv_bytes), ...] 按 created_at 倒序
        （最近添加的密钥优先尝试，更可能匹配新批次的数据）
        """
        keys = ExternalKey.query.filter_by(is_active=True).order_by(
            ExternalKey.created_at.desc()
        ).all()
        result = []
        for k in keys:
            try:
                key_bytes = base64.b64decode(k.key_b64)
                iv_bytes = base64.b64decode(k.iv_b64)
                if len(key_bytes) == DEK_SIZE and len(iv_bytes) == _EXTERNAL_IV_SIZE:
                    result.append((k.id, key_bytes, iv_bytes))
            except Exception:
                # 数据库中存了非法 Base64 → 跳过（不应发生，但防御性处理）
                continue
        return result

    def try_decrypt_external(self, encrypted_data: bytes) -> Tuple[bytes, int]:
        """用所有活跃外部密钥尝试解密

        :returns: (plaintext, matched_key_id)
        :raises ValueError: 没有活跃密钥 / 所有密钥都无法解密
        """
        candidates = self.get_active_keys_for_decrypt()
        return decrypt_external_bytes_auto(encrypted_data, candidates)

    # ==================== 密钥文件导入 ====================

    def import_from_keyfile(self, key_file_path: str, name: str,
                            description: str = "") -> ExternalKey:
        """从外部 密钥.txt 文件导入密钥

        密钥文件格式：
            CRYPTO_AES_KEY=<base64 32字节>
            CRYPTO_AES_IV=<base64 16字节>

        :raises ConflictError: name 已存在 / 指纹重复
        :raises ValidationError: 密钥文件格式异常
        :raises FileNotFoundError: 文件不存在
        """
        key_bytes, iv_bytes = load_external_key(key_file_path)
        key_b64 = base64.b64encode(key_bytes).decode()
        iv_b64 = base64.b64encode(iv_bytes).decode()
        return self.create_key({
            "name": name,
            "description": description,
            "key_b64": key_b64,
            "iv_b64": iv_b64,
            "is_active": True,
        })

    # ==================== 验证 ====================

    def verify_key_with_file(self, key_id: int, encrypted_file_path: str) -> dict:
        """验证指定密钥能否解密指定加密文件

        :returns: {valid: bool, message: str, plaintext_size: int}
        """
        import os
        from app.utils.crypto import decrypt_external_bytes

        key = self._get_or_404(ExternalKey, key_id, "外部密钥不存在")
        if not os.path.isfile(encrypted_file_path):
            raise ValidationError("待验证的加密文件不存在")

        try:
            key_bytes = base64.b64decode(key.key_b64)
            iv_bytes = base64.b64decode(key.iv_b64)
            with open(encrypted_file_path, "rb") as f:
                data = f.read()
            plaintext = decrypt_external_bytes(data, key_bytes, iv_bytes)
            log_operation(
                "verify", "external_key", key.id,
                f"验证外部密钥「{key.name}」对文件 {os.path.basename(encrypted_file_path)}：成功",
                operator=self._operator_user(),
            )
            self._commit()
            return {
                "valid": True,
                "message": f"密钥匹配，已成功解密（明文 {len(plaintext)} 字节）",
                "plaintext_size": len(plaintext),
            }
        except Exception as e:
            log_operation(
                "verify", "external_key", key.id,
                f"验证外部密钥「{key.name}」对文件 {os.path.basename(encrypted_file_path)}：失败（{e}）",
                operator=self._operator_user(),
            )
            self._commit()
            return {
                "valid": False,
                "message": f"密钥不匹配：{e}",
                "plaintext_size": 0,
            }

    # ==================== .enc 文件浏览（验证界面辅助） ====================

    def list_enc_files(self, keyword: str = "", limit: int = 500) -> List[dict]:
        """遍历 DATA_LAKE_DIR 下所有 .enc 文件，返回相对路径列表

        用于"验证外部密钥"弹窗的文件选择器，避免用户手输长路径。

        :param keyword: 路径子串过滤（不区分大小写），为空则返回全部
        :param limit: 最多返回条目数，避免数据湖过大时响应过载
        :returns: [{path, size, modified}, ...]
                  path 为相对 DATA_LAKE_DIR 的路径（使用 / 分隔，跨平台一致）
        """
        if not self.data_lake_dir or not os.path.isdir(self.data_lake_dir):
            return []
        kw = (keyword or "").strip().lower()
        results = []
        # 跳过转码缓存目录等隐藏目录
        skip_dirs = {".transcodes"}
        for root, dirs, files in os.walk(self.data_lake_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for name in files:
                if not name.lower().endswith(".enc"):
                    continue
                abs_path = os.path.join(root, name)
                rel_path = os.path.relpath(abs_path, self.data_lake_dir).replace(
                    os.sep, "/"
                )
                if kw and kw not in rel_path.lower():
                    continue
                try:
                    stat = os.stat(abs_path)
                    results.append({
                        "path": rel_path,
                        "size": stat.st_size,
                        "modified": int(stat.st_mtime),
                    })
                except OSError:
                    continue
                if len(results) >= limit:
                    return results
        return results
