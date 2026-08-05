# -*- coding: utf-8 -*-
"""密钥管理服务

封装原 api/system.py 的密钥信息/轮换/校验/备份/导入逻辑。

业务规则：
- 所有操作仅限管理员（路由层 role_required 拦截）
- 轮换 / 备份 / 导入 需二次密码确认
- 备份返回 (key_bytes, filename)，路由层用 send_file 返回二进制
- 所有操作记录操作日志（target_type='master_key'）
"""
import io
from datetime import datetime
from typing import Tuple

from app.extensions import db
from app.models import User
from app.services.base import BaseService, ValidationError
from app.utils.audit import log_operation
from app.utils.crypto import (
    get_key_info, count_encrypted_files,
    rotate_master_key, verify_key_integrity,
    export_master_key, get_key_fingerprint, import_master_key,
)


class EncryptionService(BaseService):
    """密钥管理服务"""

    def __init__(self, operator_id=None, operator_role=None, session=None,
                 data_lake_dir: str = ""):
        super().__init__(operator_id, operator_role, session)
        self.data_lake_dir = data_lake_dir

    def _verify_password(self, confirm_password: str) -> User:
        """二次密码验证，返回当前操作员 User"""
        if not confirm_password:
            raise ValidationError("密码验证失败")
        user = User.query.get(self.operator_id) if self.operator_id else None
        if not user or not user.check_password(confirm_password):
            raise ValidationError("密码验证失败")
        return user

    def get_info(self) -> dict:
        """获取加密系统信息：主密钥指纹、创建时间、加密文件统计"""
        key_info = get_key_info()
        file_stats = count_encrypted_files(self.data_lake_dir)
        return {
            "key": key_info,
            "files": file_stats,
            "data_lake_dir": self.data_lake_dir,
        }

    def rotate(self, confirm_password: str) -> dict:
        """轮换主密钥：生成新密钥并重新加密所有已加密文件（高风险，需二次密码）"""
        self._verify_password(confirm_password)
        try:
            old_fp, new_fp, count = rotate_master_key(self.data_lake_dir)
        except Exception as e:
            raise ValidationError(f"密钥轮换失败：{e}")
        log_operation(
            "rotate", "master_key", 0,
            f"轮换主密钥（旧指纹: {old_fp[:17]}... → 新指纹: {new_fp[:17]}...，重加密 {count} 个文件）",
            operator=self._operator_user(),
        )
        self._commit()
        return {
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "reencrypted_count": count,
        }

    def verify(self) -> dict:
        """验证主密钥完整性：尝试解密样本文件"""
        result = verify_key_integrity(self.data_lake_dir)
        log_operation("verify", "master_key", 0, result["message"],
                      operator=self._operator_user())
        self._commit()
        return result

    def backup(self, confirm_password: str) -> Tuple[bytes, str]:
        """下载主密钥备份文件（需二次密码）

        Returns:
            (key_bytes, filename)
        """
        self._verify_password(confirm_password)
        key_bytes = export_master_key()
        fp = get_key_fingerprint(key_bytes)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"master_key_backup_{timestamp}.key"
        log_operation(
            "download", "master_key", 0,
            f"下载主密钥备份（指纹: {fp[:17]}...）",
            operator=self._operator_user(),
        )
        self._commit()
        return key_bytes, filename

    def import_key(self, key_bytes: bytes, confirm_password: str) -> dict:
        """上传/替换主密钥（需二次密码确认，重新加密所有已加密文件）

        Raises:
            ValidationError: 密码错误 / 密钥格式非法 / 重新加密失败
        """
        self._verify_password(confirm_password)
        if not key_bytes:
            raise ValidationError("密钥文件为空")
        try:
            old_fp, new_fp, count = import_master_key(key_bytes, self.data_lake_dir)
        except ValueError as e:
            raise ValidationError(str(e))
        except Exception as e:
            raise ValidationError(f"密钥导入失败：{e}")
        log_operation(
            "import", "master_key", 0,
            f"导入/替换主密钥（旧指纹: {old_fp[:17]}... → 新指纹: {new_fp[:17]}...，重加密 {count} 个文件）",
            operator=self._operator_user(),
        )
        self._commit()
        return {
            "old_fingerprint": old_fp,
            "new_fingerprint": new_fp,
            "reencrypted_count": count,
        }