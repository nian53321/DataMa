# -*- coding: utf-8 -*-
"""外部加密格式适配单元测试

覆盖 AES-256-CBC 外部加密格式的检测、密钥加载、解密、自动探测功能：
- load_external_key: 密钥文件解析、长度校验、缺失字段
- is_external_encrypted_file: 后缀 + DMEC 排除
- decrypt_external_bytes: 二进制密文 / Base64 包装两种形态
- _try_base64_decode: 自动探测
- decrypt_external_file: 文件级解密
- _strip_enc_suffix: 后缀剥离
- 与项目 DMEC 格式互不干扰
"""
import os
import base64
import tempfile

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.utils import crypto


# ==================== 测试辅助：构造外部加密文件 ====================

def _make_external_key_iv():
    """生成测试用 32B key + 16B iv"""
    key = b"\xA1" * 32  # 32 字节固定 key
    iv = b"\xB2" * 16   # 16 字节固定 iv
    return key, iv


def _write_keyfile(directory, key, iv, filename="密钥.txt"):
    """在 directory 下写一个密钥.txt"""
    path = os.path.join(directory, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"CRYPTO_AES_KEY={base64.b64encode(key).decode()}\n")
        f.write(f"CRYPTO_AES_IV={base64.b64encode(iv).decode()}\n")
    return path


def _aes_cbc_encrypt(plaintext, key, iv):
    """用 AES-256-CBC + PKCS7 加密，返回二进制密文"""
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    return enc.update(padded) + enc.finalize()


# ==================== load_external_key ====================

class TestLoadExternalKey:
    """密钥文件加载"""

    def test_load_valid_keyfile(self, tmp_path):
        key, iv = _make_external_key_iv()
        kf = _write_keyfile(str(tmp_path), key, iv)
        loaded_key, loaded_iv = crypto.load_external_key(kf)
        assert loaded_key == key
        assert loaded_iv == iv

    def test_load_keyfile_case_insensitive(self, tmp_path):
        """字段名大小写不敏感"""
        key, iv = _make_external_key_iv()
        kf = os.path.join(str(tmp_path), "key.txt")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(f"crypto_aes_key={base64.b64encode(key).decode()}\n")
            f.write(f"CRYPTO_AES_IV={base64.b64encode(iv).decode()}\n")
        loaded_key, loaded_iv = crypto.load_external_key(kf)
        assert loaded_key == key
        assert loaded_iv == iv

    def test_load_keyfile_missing_key_raises(self, tmp_path):
        kf = os.path.join(str(tmp_path), "key.txt")
        with open(kf, "w", encoding="utf-8") as f:
            f.write("CRYPTO_AES_IV=IxaYxHjvJkcjwmmC2WR2QA==\n")
        with pytest.raises(ValueError, match="未找到 CRYPTO_AES_KEY"):
            crypto.load_external_key(kf)

    def test_load_keyfile_missing_iv_raises(self, tmp_path):
        kf = os.path.join(str(tmp_path), "key.txt")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(f"CRYPTO_AES_KEY={base64.b64encode(b'x'*32).decode()}\n")
        with pytest.raises(ValueError, match="未找到 CRYPTO_AES_IV"):
            crypto.load_external_key(kf)

    def test_load_keyfile_wrong_key_length_raises(self, tmp_path):
        kf = os.path.join(str(tmp_path), "key.txt")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(f"CRYPTO_AES_KEY={base64.b64encode(b'short').decode()}\n")  # 5 字节
            f.write(f"CRYPTO_AES_IV={base64.b64encode(b'x'*16).decode()}\n")
        with pytest.raises(ValueError, match="AES Key 长度异常"):
            crypto.load_external_key(kf)

    def test_load_keyfile_wrong_iv_length_raises(self, tmp_path):
        key, _ = _make_external_key_iv()
        kf = os.path.join(str(tmp_path), "key.txt")
        with open(kf, "w", encoding="utf-8") as f:
            f.write(f"CRYPTO_AES_KEY={base64.b64encode(key).decode()}\n")
            f.write(f"CRYPTO_AES_IV={base64.b64encode(b'x'*12).decode()}\n")  # 12 字节
        with pytest.raises(ValueError, match="AES IV 长度异常"):
            crypto.load_external_key(kf)

    def test_load_keyfile_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            crypto.load_external_key(str(tmp_path / "no_such.txt"))


# ==================== is_external_encrypted_file ====================

class TestIsExternalEncryptedFile:
    """外部加密文件检测"""

    def test_detects_enc_suffix(self, tmp_path):
        f = tmp_path / "data.csv.enc"
        f.write_bytes(b"\x00" * 100)  # 非 DMEC
        assert crypto.is_external_encrypted_file(str(f)) is True

    def test_rejects_plain_file(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_bytes(b"plain content")
        assert crypto.is_external_encrypted_file(str(f)) is False

    def test_rejects_dmec_format(self, app, tmp_path):
        """DMEC 格式即使是 .enc 后缀也应被排除"""
        with app.app_context():
            f = tmp_path / "data.bin.enc"
            f.write_bytes(crypto.encrypt_bytes(b"dmec content"))
            assert crypto.is_encrypted_file(str(f)) is True  # 是 DMEC
            assert crypto.is_external_encrypted_file(str(f)) is False

    def test_rejects_missing_file(self, tmp_path):
        missing = str(tmp_path / "no_such.enc")
        assert crypto.is_external_encrypted_file(missing) is False

    def test_rejects_no_enc_suffix(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_bytes(b"\x00" * 100)
        assert crypto.is_external_encrypted_file(str(f)) is False


# ==================== decrypt_external_bytes ====================

class TestDecryptExternalBytes:
    """外部加密数据解密"""

    def test_decrypt_binary_ciphertext(self):
        """直接二进制密文（大文件场景）"""
        key, iv = _make_external_key_iv()
        plaintext = b"Hello, external encryption!" * 10  # 260 字节
        ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
        # 模拟大文件直接二进制密文
        decrypted = crypto.decrypt_external_bytes(ciphertext, key, iv)
        assert decrypted == plaintext

    def test_decrypt_base64_wrapped(self):
        """Base64 包装密文（小文件场景，如 userInfo.json）"""
        key, iv = _make_external_key_iv()
        plaintext = b'{"mmse":1,"name":"test"}'
        ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
        # 外部工具对小文件会再 Base64 编码
        b64_wrapped = base64.b64encode(ciphertext)
        decrypted = crypto.decrypt_external_bytes(b64_wrapped, key, iv)
        assert decrypted == plaintext

    def test_decrypt_empty_plaintext(self):
        """加密空内容也能解密"""
        key, iv = _make_external_key_iv()
        ciphertext = _aes_cbc_encrypt(b"", key, iv)
        decrypted = crypto.decrypt_external_bytes(ciphertext, key, iv)
        assert decrypted == b""

    def test_decrypt_wrong_key_raises(self):
        """密钥不匹配应抛出 ValueError（PKCS7 padding 校验失败）"""
        key, iv = _make_external_key_iv()
        plaintext = b"some content here"
        ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
        wrong_key = b"\xBB" * 32
        with pytest.raises(ValueError, match="PKCS7"):
            crypto.decrypt_external_bytes(ciphertext, wrong_key, iv)

    def test_decrypt_invalid_length_raises(self):
        """非 16 倍数的密文应抛出 ValueError"""
        key, iv = _make_external_key_iv()
        with pytest.raises(ValueError, match="长度异常"):
            crypto.decrypt_external_bytes(b"\x00" * 15, key, iv)  # 15 字节

    def test_decrypt_auto_detects_base64(self):
        """自动探测：Base64 文本应先解码再 AES-CBC"""
        key, iv = _make_external_key_iv()
        plaintext = b'{"key":"value"}'
        ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
        # Base64 编码后包含换行
        b64_text = base64.b64encode(ciphertext) + b"\n"
        decrypted = crypto.decrypt_external_bytes(b64_text, key, iv)
        assert decrypted == plaintext


# ==================== _try_base64_decode ====================

class TestTryBase64Decode:
    """Base64 自动探测"""

    def test_valid_base64(self):
        result = crypto._try_base64_decode(b"aGVsbG8=")  # "hello"
        assert result == b"hello"

    def test_base64_with_whitespace(self):
        result = crypto._try_base64_decode(b"aGVs\nbG8=")
        assert result == b"hello"

    def test_binary_data_returns_none(self):
        """二进制数据（含非 ASCII 字节）应返回 None"""
        result = crypto._try_base64_decode(b"\x00\x01\x02\xff")
        assert result is None

    def test_non_base64_chars_returns_none(self):
        """含非 Base64 字符的文本应返回 None"""
        result = crypto._try_base64_decode(b"hello!@#$")
        assert result is None

    def test_empty_data_returns_none(self):
        assert crypto._try_base64_decode(b"") is None

    def test_wrong_length_returns_none(self):
        """长度非 4 倍数应返回 None"""
        result = crypto._try_base64_decode(b"aGVsbG8")  # 7 字节
        assert result is None


# ==================== decrypt_external_file ====================

class TestDecryptExternalFile:
    """文件级外部解密"""

    def test_decrypt_external_file_binary(self, tmp_path):
        key, iv = _make_external_key_iv()
        plaintext = b"binary file content" * 100
        ciphertext = _aes_cbc_encrypt(plaintext, key, iv)
        f = tmp_path / "data.csv.enc"
        f.write_bytes(ciphertext)

        result = crypto.decrypt_external_file(str(f), key, iv)
        assert result == plaintext


# ==================== 与 DMEC 格式互操作 ====================

class TestInteropWithDmec:
    """外部格式与项目 DMEC 格式应互不干扰"""

    def test_dmec_file_not_treated_as_external(self, app, tmp_path):
        """DMEC 格式文件不应被识别为外部加密"""
        with app.app_context():
            f = tmp_path / "dmec.enc"
            f.write_bytes(crypto.encrypt_bytes(b"dmec content"))
            # 是 DMEC
            assert crypto.is_encrypted_file(str(f)) is True
            # 不是外部
            assert crypto.is_external_encrypted_file(str(f)) is False

    def test_external_file_not_treated_as_dmec(self, tmp_path):
        """外部加密文件不应被 DMEC 解密器接受"""
        key, iv = _make_external_key_iv()
        ciphertext = _aes_cbc_encrypt(b"external content", key, iv)
        f = tmp_path / "external.enc"
        f.write_bytes(ciphertext)
        # 不是 DMEC
        assert crypto.is_encrypted_file(str(f)) is False
        # 是外部
        assert crypto.is_external_encrypted_file(str(f)) is True


# ==================== scanner._strip_enc_suffix ====================

class TestScannerHelpers:
    """scanner 模块辅助函数"""

    def test_strip_enc_suffix(self):
        from app.utils.scanner import _strip_enc_suffix
        assert _strip_enc_suffix("eeg_20260717.csv.enc") == "eeg_20260717.csv"
        assert _strip_enc_suffix("userinfo.json.enc") == "userinfo.json"
        assert _strip_enc_suffix("eeg_20260717.csv") == "eeg_20260717.csv"  # 无 .enc 不变
        assert _strip_enc_suffix("data.ENC") == "data"  # 大写也剥离
        assert _strip_enc_suffix("no_ext") == "no_ext"

    def test_detect_data_type_with_enc_suffix(self):
        """_detect_data_type 应识别 .enc 后缀的文件类型"""
        from app.utils.scanner import _detect_data_type
        assert _detect_data_type("eeg_20260717.csv.enc") == "eeg"
        assert _detect_data_type("ecg_20260717.csv.enc") == "ecg"
        assert _detect_data_type("recording_20260717.wav.enc") == "audio"
        assert _detect_data_type("gait_20260717.csv.enc") == "gait"
        # 不带 .enc 也应正常识别
        assert _detect_data_type("eeg_20260717.csv") == "eeg"


# ==================== 端到端：模拟样例目录扫描 ====================

class TestScannerEndToEnd:
    """模拟样例目录的完整扫描流程"""

    def test_scan_external_encrypted_directory(self, db_app, admin_user, tmp_path):
        """模拟一个外部加密的样例目录，验证扫描能正确入库

        正常流程：目录里只有加密数据，外部密钥预先存入系统数据库
        """
        from app.utils.scanner import _find_user_info, _read_file_plaintext
        from app.models.subject import Subject
        from app.models.external_key import ExternalKey

        # 1. 构造样例目录（不含密钥文件，密钥已预先存入数据库）
        sample_dir = tmp_path / "SUBJ_E2E_001"
        sample_dir.mkdir()
        key, iv = _make_external_key_iv()

        # 将外部密钥预先存入数据库（管理员通过前端上传）
        with db_app.app_context():
            ek = ExternalKey(
                name="AgeCog-E2E",
                key_b64=base64.b64encode(key).decode(),
                iv_b64=base64.b64encode(iv).decode(),
                key_fingerprint=ExternalKey.compute_fingerprint(
                    base64.b64encode(key).decode()
                ),
                is_active=True,
            )
            from app.extensions import db
            db.session.add(ek)
            db.session.commit()

        # userInfo.json.enc（Base64 包装的小文件）
        user_info = b'{"age":30,"sex":0,"phone":"13800138000","mmse":28,"moca":26,"disease":"\xe6\x9c\xaa\xe7\x9f\xa5\xe7\x97\x85\xe7\x97\x87","severity":0,"organization":"ORG_A","comment":"E2E","createDatetime":"2026-07-17 18:00:00","userName":"\xe6\xb5\x8b\xe8\xaf\x95","id":17842827151830001,"product":"AgeCog"}'
        ui_cipher = _aes_cbc_encrypt(user_info, key, iv)
        (sample_dir / "userInfo.json.enc").write_bytes(base64.b64encode(ui_cipher))

        # eeg_xxx.csv.enc（二进制大文件）
        eeg_data = b"Sample Index,CH1,CH2\n0,1.0,2.0\n1,1.1,2.1\n"
        eeg_cipher = _aes_cbc_encrypt(eeg_data, key, iv)
        (sample_dir / "eeg_20260717184835.csv.enc").write_bytes(eeg_cipher)

        # 2. 验证 _find_user_info 能解析外部加密的 userInfo
        with db_app.app_context():
            path, fields = _find_user_info(str(sample_dir))
            assert path is not None
            assert path.endswith("userInfo.json.enc")
            assert fields is not None
            assert fields["age"] == 30
            assert fields["gender"] == "男"
            assert fields["phone"] == "13800138000"
            assert fields["mmse_score"] == 28.0
            assert fields["moca_score"] == 26.0

            # 3. 验证 _read_file_plaintext 能解密外部加密的数据文件
            eeg_path = str(sample_dir / "eeg_20260717184835.csv.enc")
            plaintext = _read_file_plaintext(eeg_path)
            assert plaintext == eeg_data

    def test_scan_mixed_formats(self, db_app, admin_user, tmp_path):
        """同一目录混合三种格式：明文 + 外部加密 + DMEC 加密

        外部密钥预先存入数据库（与正常流程一致）
        """
        from app.utils.scanner import _read_file_plaintext
        from app.utils import crypto as crypto_mod
        from app.models.external_key import ExternalKey

        sample_dir = tmp_path / "SUBJ_MIXED"
        sample_dir.mkdir()
        key, iv = _make_external_key_iv()

        # 明文文件
        plain_content = b"plain text content"
        (sample_dir / "plain.txt").write_bytes(plain_content)

        # 外部加密文件
        ext_content = b"external encrypted"
        ext_cipher = _aes_cbc_encrypt(ext_content, key, iv)
        (sample_dir / "external.enc").write_bytes(ext_cipher)

        # DMEC 加密文件 + 预先将外部密钥存入数据库
        with db_app.app_context():
            ek = ExternalKey(
                name="Mixed-Key",
                key_b64=base64.b64encode(key).decode(),
                iv_b64=base64.b64encode(iv).decode(),
                key_fingerprint=ExternalKey.compute_fingerprint(
                    base64.b64encode(key).decode()
                ),
                is_active=True,
            )
            from app.extensions import db
            db.session.add(ek)
            db.session.commit()

            dmec_content = b"dmec encrypted content"
            (sample_dir / "dmec.enc").write_bytes(crypto_mod.encrypt_bytes(dmec_content))

            # 验证三种格式都能正确读取明文
            assert _read_file_plaintext(str(sample_dir / "plain.txt")) == plain_content
            assert _read_file_plaintext(str(sample_dir / "external.enc")) == ext_content
            assert _read_file_plaintext(str(sample_dir / "dmec.enc")) == dmec_content
