# -*- coding: utf-8 -*-
"""ExternalKeyService 单元测试

覆盖外部密钥管理的完整业务规则：
- CRUD：create / update / delete / list / get
- 校验：name 必填+长度、key/iv Base64+长度、指纹去重
- 多密钥自动解密：try_decrypt_external（数据库活跃密钥优先尝试）
- 密钥文件导入：import_from_keyfile
- 验证密钥：verify_key_with_file
- scanner 集成：数据库密钥优先 + 目录密钥回退 + 失败收集
"""
import base64
import os
import tempfile

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.extensions import db
from app.models import ExternalKey
from app.services import (
    ExternalKeyService, ValidationError, ConflictError, NotFoundError,
)
from app.utils.crypto import decrypt_external_bytes_auto


# ==================== 测试辅助 ====================

def _make_key_iv():
    """生成 32B key + 16B iv"""
    return b"\xA1" * 32, b"\xB2" * 16


def _b64(b):
    return base64.b64encode(b).decode()


def _write_keyfile(directory, key, iv, filename="密钥.txt"):
    """写一个 密钥.txt 文件"""
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"CRYPTO_AES_KEY={_b64(key)}\n")
        f.write(f"CRYPTO_AES_IV={_b64(iv)}\n")
    return path


def _aes_cbc_encrypt(plaintext, key, iv):
    """AES-256-CBC + PKCS7 加密"""
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


def _make_svc(operator_id=None, operator_role="admin"):
    """构造 ExternalKeyService"""
    return ExternalKeyService(operator_id=operator_id, operator_role=operator_role)


# ==================== create_key ====================

class TestCreateKey:
    def test_create_success(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({
                "name": "AgeCog-2026Q1",
                "description": "2026年第一季度采集",
                "key_b64": _b64(key),
                "iv_b64": _b64(iv),
            })
            assert ek.id is not None
            assert ek.name == "AgeCog-2026Q1"
            assert ek.is_active is True
            assert len(ek.key_fingerprint) == 32
            # 列表不应返回明文
            data = ek.to_dict()
            assert "key_b64" not in data
            assert "iv_b64" not in data
            # include_secret=True 时返回
            data_secret = ek.to_dict(include_secret=True)
            assert data_secret["key_b64"] == _b64(key)

    def test_create_duplicate_name_raises(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            # 同名再次创建应抛 ConflictError
            with pytest.raises(ConflictError, match="密钥名称已存在"):
                svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})

    def test_create_duplicate_fingerprint_raises(self, db_app):
        """相同 key（不同 iv）应检测指纹重复"""
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            # 不同 name 但相同 key → 指纹相同
            with pytest.raises(ConflictError, match="密钥指纹已存在"):
                svc.create_key({
                    "name": "K2", "key_b64": _b64(key),
                    "iv_b64": _b64(b"\xCC" * 16),
                })

    def test_create_invalid_key_length_raises(self, db_app):
        with db_app.app_context():
            iv = b"\xB2" * 16
            svc = _make_svc(operator_id=1)
            with pytest.raises(ValidationError, match="AES Key 长度异常"):
                svc.create_key({
                    "name": "K1",
                    "key_b64": _b64(b"short"),  # 5 字节
                    "iv_b64": _b64(iv),
                })

    def test_create_invalid_iv_length_raises(self, db_app):
        with db_app.app_context():
            key, _ = _make_key_iv()
            svc = _make_svc(operator_id=1)
            with pytest.raises(ValidationError, match="AES IV 长度异常"):
                svc.create_key({
                    "name": "K1",
                    "key_b64": _b64(key),
                    "iv_b64": _b64(b"x" * 12),  # 12 字节
                })

    def test_create_invalid_base64_raises(self, db_app):
        with db_app.app_context():
            svc = _make_svc(operator_id=1)
            with pytest.raises(ValidationError, match="Base64"):
                svc.create_key({
                    "name": "K1",
                    "key_b64": "!!!not-base64!!!",  # 含非 Base64 字符
                    "iv_b64": _b64(b"x" * 16),
                })

    def test_create_empty_name_raises(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            with pytest.raises(ValidationError, match="密钥名称不能为空"):
                svc.create_key({"name": "", "key_b64": _b64(key), "iv_b64": _b64(iv)})


# ==================== list_keys / get_key ====================

class TestListGetKey:
    def test_list_returns_no_secret(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            keys = svc.list_keys()
            assert len(keys) == 1
            assert keys[0]["name"] == "K1"
            assert "key_b64" not in keys[0]
            assert "iv_b64" not in keys[0]
            assert "key_fingerprint" in keys[0]

    def test_get_key_include_secret(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            # 默认不返回明文
            data = svc.get_key(ek.id)
            assert "key_b64" not in data
            # include_secret=True 返回
            data_secret = svc.get_key(ek.id, include_secret=True)
            assert data_secret["key_b64"] == _b64(key)
            assert data_secret["iv_b64"] == _b64(iv)

    def test_get_not_found_raises(self, db_app):
        with db_app.app_context():
            svc = _make_svc(operator_id=1)
            with pytest.raises(NotFoundError):
                svc.get_key(9999)


# ==================== update_key ====================

class TestUpdateKey:
    def test_update_name_only(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            updated = svc.update_key(ek.id, {"name": "K1-renamed"})
            assert updated.name == "K1-renamed"

    def test_update_key_iv_recomputes_fingerprint(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            old_fp = ek.key_fingerprint
            new_key = b"\xCC" * 32
            new_iv = b"\xDD" * 16
            updated = svc.update_key(ek.id, {
                "key_b64": _b64(new_key), "iv_b64": _b64(new_iv),
            })
            assert updated.key_fingerprint != old_fp
            assert updated.key_fingerprint == ExternalKey.compute_fingerprint(_b64(new_key))

    def test_update_partial_key_iv_raises(self, db_app):
        """只更新 key 不更新 iv 应抛错"""
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            with pytest.raises(ValidationError, match="必须同时提供"):
                svc.update_key(ek.id, {"key_b64": _b64(b"\xEE" * 32)})

    def test_update_duplicate_name_raises(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            ek2 = svc.create_key({
                "name": "K2", "key_b64": _b64(b"\x33" * 32), "iv_b64": _b64(iv),
            })
            with pytest.raises(ConflictError, match="密钥名称已存在"):
                svc.update_key(ek2.id, {"name": "K1"})

    def test_update_is_active(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            assert ek.is_active is True
            updated = svc.update_key(ek.id, {"is_active": False})
            assert updated.is_active is False


# ==================== delete_key ====================

class TestDeleteKey:
    def test_delete_success(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            name = svc.delete_key(ek.id)
            assert name == "K1"
            assert ExternalKey.query.get(ek.id) is None

    def test_delete_not_found_raises(self, db_app):
        with db_app.app_context():
            svc = _make_svc(operator_id=1)
            with pytest.raises(NotFoundError):
                svc.delete_key(9999)


# ==================== 多密钥自动解密 ====================

class TestAutoDecrypt:
    def test_try_decrypt_single_key(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            plaintext = b"Hello, external encryption!" * 10
            ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
            result_pt, matched_id = svc.try_decrypt_external(ciphertext)
            assert result_pt == plaintext
            assert matched_id is not None

    def test_try_decrypt_multi_keys_first_match(self, db_app):
        """多组密钥，第一组（最近添加）命中"""
        with db_app.app_context():
            key1, iv1 = b"\x11" * 32, b"\x22" * 16
            key2, iv2 = _make_key_iv()  # 第二组
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key1), "iv_b64": _b64(iv1)})
            svc.create_key({"name": "K2", "key_b64": _b64(key2), "iv_b64": _b64(iv2)})
            # 用 K1 加密的数据
            plaintext = b"first match test"
            ciphertext = _aes_cbc_encrypt(plaintext, key1, iv1)
            # K2 是最近添加的，会先尝试，但失败；K1 应命中
            result_pt, matched_id = svc.try_decrypt_external(ciphertext)
            assert result_pt == plaintext

    def test_try_decrypt_inactive_key_skipped(self, db_app):
        """禁用的密钥不参与解密"""
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            svc.update_key(ek.id, {"is_active": False})
            # 此时无活跃密钥，try_decrypt 应抛 ValueError
            plaintext = b"inactive test"
            ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
            with pytest.raises(ValueError):
                svc.try_decrypt_external(ciphertext)

    def test_try_decrypt_no_match_raises(self, db_app):
        """所有密钥都无法解密"""
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            # 用完全不同的 key 加密
            other_key = b"\xFF" * 32
            other_iv = b"\xEE" * 16
            ciphertext = _aes_cbc_encrypt(b"unknown", other_key, other_iv)
            with pytest.raises(ValueError, match="无法解密"):
                svc.try_decrypt_external(ciphertext)

    def test_try_decrypt_no_active_keys_raises(self, db_app):
        """无活跃密钥时抛 ValueError"""
        with db_app.app_context():
            svc = _make_svc(operator_id=1)
            with pytest.raises(ValueError, match="未提供候选"):
                svc.try_decrypt_external(b"\x00" * 32)

    def test_get_active_keys_for_decrypt_order(self, db_app):
        """活跃密钥按 created_at 倒序返回"""
        with db_app.app_context():
            key1, iv1 = _make_key_iv()
            key2, iv2 = b"\x33" * 32, b"\x44" * 16
            svc = _make_svc(operator_id=1)
            ek1 = svc.create_key({"name": "K1", "key_b64": _b64(key1), "iv_b64": _b64(iv1)})
            ek2 = svc.create_key({"name": "K2", "key_b64": _b64(key2), "iv_b64": _b64(iv2)})
            candidates = svc.get_active_keys_for_decrypt()
            assert len(candidates) == 2
            # ek2 创建较晚，应排在前面
            assert candidates[0][0] == ek2.id
            assert candidates[1][0] == ek1.id


# ==================== import_from_keyfile ====================

class TestImportFromKeyfile:
    def test_import_success(self, db_app, tmp_path):
        with db_app.app_context():
            key, iv = _make_key_iv()
            kf = _write_keyfile(str(tmp_path), key, iv)
            svc = _make_svc(operator_id=1)
            ek = svc.import_from_keyfile(kf, name="imported-1", description="测试导入")
            assert ek.name == "imported-1"
            assert ek.description == "测试导入"
            assert ek.is_active is True
            # 验证导入的密钥可以正确解密
            plaintext = b"import test"
            ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
            result_pt, _ = svc.try_decrypt_external(ciphertext)
            assert result_pt == plaintext

    def test_import_invalid_keyfile_raises(self, db_app, tmp_path):
        with db_app.app_context():
            kf = tmp_path / "bad.txt"
            kf.write_text("CRYPTO_AES_KEY=not_base64\n")
            svc = _make_svc(operator_id=1)
            with pytest.raises((ValueError, ValidationError)):
                svc.import_from_keyfile(str(kf), name="bad")

    def test_import_duplicate_fingerprint_raises(self, db_app, tmp_path):
        with db_app.app_context():
            key, iv = _make_key_iv()
            kf = _write_keyfile(str(tmp_path), key, iv)
            svc = _make_svc(operator_id=1)
            svc.import_from_keyfile(kf, name="first")
            # 再次导入相同密钥应抛 ConflictError
            with pytest.raises(ConflictError):
                svc.import_from_keyfile(kf, name="second")


# ==================== verify_key_with_file ====================

class TestVerifyKeyWithFile:
    def test_verify_match_success(self, db_app, tmp_path):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            # 构造加密文件
            plaintext = b"verify test content"
            ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
            enc_file = tmp_path / "test.enc"
            enc_file.write_bytes(ciphertext)
            result = svc.verify_key_with_file(ek.id, str(enc_file))
            assert result["valid"] is True
            assert result["plaintext_size"] == len(plaintext)

    def test_verify_mismatch_returns_invalid(self, db_app, tmp_path):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            # 用不同的 key 加密
            other_key = b"\xFF" * 32
            other_iv = b"\xEE" * 16
            ciphertext = _aes_cbc_encrypt(b"other", other_key, other_iv)
            enc_file = tmp_path / "test.enc"
            enc_file.write_bytes(ciphertext)
            result = svc.verify_key_with_file(ek.id, str(enc_file))
            assert result["valid"] is False
            assert "不匹配" in result["message"]

    def test_verify_missing_file_raises(self, db_app):
        with db_app.app_context():
            key, iv = _make_key_iv()
            svc = _make_svc(operator_id=1)
            ek = svc.create_key({"name": "K1", "key_b64": _b64(key), "iv_b64": _b64(iv)})
            with pytest.raises(ValidationError, match="文件不存在"):
                svc.verify_key_with_file(ek.id, "/no/such/file.enc")


# ==================== scanner 集成：仅用数据库外部密钥 ====================

class TestScannerIntegration:
    """scanner._read_file_plaintext 与 ExternalKeyService 的端到端集成

    设计原则：外部密钥集中存储在数据库中，scanner 不读取扫描目录中的密钥文件。
    正常流程：受试者目录里只有加密数据 → scanner 用数据库外部密钥尝试解密
             → 成功则用内部 DMEC 密钥加密入库；失败则提示用户上传对应外部密钥。
    """

    def test_db_key_decrypt_success(self, db_app, tmp_path):
        """数据库活跃密钥成功解密外部加密文件"""
        with db_app.app_context():
            # 数据库存 K1
            key1, iv1 = b"\x11" * 32, b"\x22" * 16
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key1), "iv_b64": _b64(iv1)})

            # 用 K1 加密的文件（模拟外部采集设备导出的加密文件）
            plaintext = b"external encrypted data" * 10
            ciphertext = _aes_cbc_encrypt(plaintext, key1, iv1)
            enc_file = tmp_path / "data.enc"
            enc_file.write_bytes(ciphertext)

            from app.utils.scanner import _read_file_plaintext
            result = _read_file_plaintext(str(enc_file))
            assert result == plaintext

    def test_db_key_multi_keys_first_match(self, db_app, tmp_path):
        """多组数据库密钥：尝试顺序命中"""
        with db_app.app_context():
            key1, iv1 = b"\x11" * 32, b"\x22" * 16
            key2, iv2 = b"\x33" * 32, b"\x44" * 16
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K1", "key_b64": _b64(key1), "iv_b64": _b64(iv1)})
            svc.create_key({"name": "K2", "key_b64": _b64(key2), "iv_b64": _b64(iv2)})

            # 用 K1 加密（K2 创建较晚应先尝试，失败后 K1 命中）
            plaintext = b"multi key test"
            ciphertext = _aes_cbc_encrypt(plaintext, key1, iv1)
            enc_file = tmp_path / "data.enc"
            enc_file.write_bytes(ciphertext)

            from app.utils.scanner import _read_file_plaintext
            result = _read_file_plaintext(str(enc_file))
            assert result == plaintext

    def test_failure_collector_records_failure(self, db_app, tmp_path):
        """数据库密钥无法解密时记录到 failure_collector，返回 None"""
        with db_app.app_context():
            # 数据库存一个不匹配的密钥
            wrong_key = b"\x33" * 32
            wrong_iv = b"\x44" * 16
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K-wrong", "key_b64": _b64(wrong_key), "iv_b64": _b64(wrong_iv)})

            # 用未知密钥加密的文件
            other_key = b"\xFF" * 32
            other_iv = b"\xEE" * 16
            plaintext = b"unknown key test"
            ciphertext = _aes_cbc_encrypt(plaintext, other_key, other_iv)
            enc_file = tmp_path / "data.enc"
            enc_file.write_bytes(ciphertext)

            from app.utils.scanner import _read_file_plaintext
            failures = []
            result = _read_file_plaintext(
                str(enc_file), failure_collector=failures,
            )
            assert result is None
            assert len(failures) == 1
            assert "无法解密" in failures[0]["reason"]
            assert "外部密钥管理" in failures[0]["reason"]  # 提示用户上传

    def test_failure_no_active_keys_records_failure(self, db_app, tmp_path):
        """数据库无任何活跃外部密钥时记录失败"""
        with db_app.app_context():
            # 数据库为空（不创建任何密钥）
            other_key = b"\xFF" * 32
            other_iv = b"\xEE" * 16
            ciphertext = _aes_cbc_encrypt(b"no keys test", other_key, other_iv)
            enc_file = tmp_path / "data.enc"
            enc_file.write_bytes(ciphertext)

            from app.utils.scanner import _read_file_plaintext
            failures = []
            result = _read_file_plaintext(
                str(enc_file), failure_collector=failures,
            )
            assert result is None
            assert len(failures) == 1
            assert "无可用外部密钥" in failures[0]["last_error"]

    def test_failure_no_collector_raises(self, db_app, tmp_path):
        """无 failure_collector 时抛异常"""
        with db_app.app_context():
            # 数据库存一个不匹配的密钥
            wrong_key = b"\x33" * 32
            wrong_iv = b"\x44" * 16
            svc = _make_svc(operator_id=1)
            svc.create_key({"name": "K-wrong", "key_b64": _b64(wrong_key), "iv_b64": _b64(wrong_iv)})

            # 用未知密钥加密的文件
            other_key = b"\xFF" * 32
            other_iv = b"\xEE" * 16
            ciphertext = _aes_cbc_encrypt(b"unknown", other_key, other_iv)
            enc_file = tmp_path / "data.enc"
            enc_file.write_bytes(ciphertext)

            from app.utils.scanner import _read_file_plaintext
            with pytest.raises(ValueError, match="无法解密"):
                _read_file_plaintext(str(enc_file))

    def test_directory_keyfile_not_used(self, db_app, tmp_path):
        """验证目录中的 密钥.txt 不会被 scanner 使用

        即使目录里放了 密钥.txt，且数据库无匹配密钥，scanner 也应失败
        而不是回退读取目录密钥文件。
        """
        with db_app.app_context():
            # 目录放密钥.txt（但 scanner 不应使用它）
            dir_key, dir_iv = _make_key_iv()
            _write_keyfile(str(tmp_path), dir_key, dir_iv)

            # 用该目录密钥加密的文件
            plaintext = b"directory key ignored test"
            ciphertext = _aes_cbc_encrypt(plaintext, dir_key, dir_iv)
            enc_file = tmp_path / "data.enc"
            enc_file.write_bytes(ciphertext)

            # 数据库为空，scanner 应失败（不应回退读 密钥.txt）
            from app.utils.scanner import _read_file_plaintext
            failures = []
            result = _read_file_plaintext(
                str(enc_file), failure_collector=failures,
            )
            assert result is None
            assert len(failures) == 1
            assert "无可用外部密钥" in failures[0]["last_error"]


# ==================== decrypt_external_bytes_auto（crypto 层）====================

class TestDecryptExternalBytesAuto:
    """crypto.decrypt_external_bytes_auto 直接测试"""

    def test_auto_decrypt_first_match(self):
        key1, iv1 = b"\x11" * 32, b"\x22" * 16
        key2, iv2 = _make_key_iv()
        plaintext = b"multi-key test"
        ciphertext = _aes_cbc_encrypt(plaintext, key1, iv1)
        candidates = [(1, key1, iv1), (2, key2, iv2)]
        result_pt, matched_id = decrypt_external_bytes_auto(ciphertext, candidates)
        assert result_pt == plaintext
        assert matched_id == 1

    def test_auto_decrypt_second_match(self):
        key1, iv1 = b"\x11" * 32, b"\x22" * 16
        key2, iv2 = _make_key_iv()
        plaintext = b"second match"
        ciphertext = _aes_cbc_encrypt(plaintext, key2, iv2)
        candidates = [(1, key1, iv1), (2, key2, iv2)]
        result_pt, matched_id = decrypt_external_bytes_auto(ciphertext, candidates)
        assert result_pt == plaintext
        assert matched_id == 2

    def test_auto_decrypt_no_match_raises(self):
        key1, iv1 = b"\x11" * 32, b"\x22" * 16
        other_key = b"\xFF" * 32
        other_iv = b"\xEE" * 16
        ciphertext = _aes_cbc_encrypt(b"unknown", other_key, other_iv)
        candidates = [(1, key1, iv1)]
        with pytest.raises(ValueError, match="无法解密"):
            decrypt_external_bytes_auto(ciphertext, candidates)

    def test_auto_decrypt_empty_candidates_raises(self):
        with pytest.raises(ValueError, match="未提供候选"):
            decrypt_external_bytes_auto(b"\x00" * 32, [])
