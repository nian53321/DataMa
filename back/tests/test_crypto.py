# -*- coding: utf-8 -*-
"""crypto.py 单元测试：文件加解密模块（信封加密方案）

覆盖：
- get_master_key 主密钥获取/生成/缓存
- encrypt_bytes / decrypt_bytes 字节流加解密往返
- encrypt_file / decrypt_file 文件级加解密往返
- is_encrypted_file 加密文件识别
- decrypt_file_to_temp 解密到临时文件
- encrypt_stream_to_file 流式上传加密
- 异常输入处理
"""
import os
from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage

from app.utils import crypto


@pytest.fixture(autouse=True)
def _reset_master_key_cache():
    """每个测试前后清除主密钥缓存，避免测试间相互污染"""
    crypto.reset_master_key_cache()
    yield
    crypto.reset_master_key_cache()


# ==================== 主密钥 ====================

def test_get_master_key_generates_and_caches(app):
    """测试主密钥获取：首次调用应生成 32 字节密钥文件并缓存后续调用"""
    with app.app_context():
        key_path = app.config["MASTER_KEY_PATH"]
        # 初始不存在
        assert not os.path.exists(key_path)

        key1 = crypto.get_master_key()
        # 密钥文件已生成
        assert os.path.exists(key_path)
        # AES-256 密钥长度 32 字节
        assert len(key1) == crypto.DEK_SIZE

        # 第二次调用应命中缓存，返回相同密钥
        key2 = crypto.get_master_key()
        assert key1 == key2


def test_get_master_key_reuses_existing_file(app, tmp_path):
    """测试主密钥获取：已存在的合法密钥文件应被直接读取复用"""
    with app.app_context():
        # 预先写入一个合法的 32 字节密钥
        preset = b"\x01" * crypto.DEK_SIZE
        with open(app.config["MASTER_KEY_PATH"], "wb") as f:
            f.write(preset)

        key = crypto.get_master_key()
        assert key == preset


def test_get_master_key_corrupt_file_raises(app, tmp_path):
    """测试主密钥获取：长度异常的密钥文件应抛出 RuntimeError"""
    with app.app_context():
        # 写入长度异常的密钥文件
        with open(app.config["MASTER_KEY_PATH"], "wb") as f:
            f.write(b"tooshort")

        with pytest.raises(RuntimeError):
            crypto.get_master_key()


# ==================== 字节流加解密 ====================

def test_encrypt_decrypt_bytes_roundtrip(app):
    """测试字节流加解密往返：加密后能正确解密还原原始内容"""
    with app.app_context():
        plaintext = ("Hello, DataManagement! 这是测试数据。" * 10).encode("utf-8")
        encrypted = crypto.encrypt_bytes(plaintext)

        # 加密结果以魔数开头
        assert encrypted[:4] == crypto.MAGIC
        # 密文长度大于头部
        assert len(encrypted) > crypto.HEADER_SIZE

        # 解密还原
        decrypted = crypto.decrypt_bytes(encrypted)
        assert decrypted == plaintext


def test_encrypt_bytes_empty_content(app):
    """测试加密空内容也能正确往返"""
    with app.app_context():
        encrypted = crypto.encrypt_bytes(b"")
        assert encrypted[:4] == crypto.MAGIC
        assert crypto.decrypt_bytes(encrypted) == b""


def test_decrypt_invalid_magic_raises(app):
    """测试解密非加密数据应抛出 ValueError（魔数不匹配）"""
    with app.app_context():
        with pytest.raises(ValueError):
            crypto.decrypt_bytes(b"XXXX" + b"\x00" * 200)


def test_decrypt_short_data_raises(app):
    """测试解密过短数据应抛出 ValueError（文件过短）"""
    with app.app_context():
        with pytest.raises(ValueError):
            crypto.decrypt_bytes(b"DMEC")


# ==================== 文件级加解密 ====================

def test_encrypt_decrypt_file_roundtrip(app, tmp_path):
    """测试文件级加解密往返：流式写入后能正确解密还原"""
    with app.app_context():
        src = tmp_path / "plain.bin"
        dst = tmp_path / "enc.bin"
        content = b"\x00\x01\x02 binary content \xff\xfe" * 100
        src.write_bytes(content)

        crypto.encrypt_file(str(src), str(dst))

        # 加密后文件以魔数开头，且与原文不同
        enc_data = dst.read_bytes()
        assert enc_data[:4] == crypto.MAGIC
        assert enc_data != content

        # 解密还原
        plaintext = crypto.decrypt_file(str(dst))
        assert plaintext == content


def test_encrypt_file_inplace(app, tmp_path):
    """测试原地加密（dst_path 缺省）后内容变为加密格式"""
    with app.app_context():
        src = tmp_path / "inplace.txt"
        src.write_bytes(b"inplace content here")
        crypto.encrypt_file(str(src))  # 不传 dst_path，原地覆盖
        assert src.read_bytes()[:4] == crypto.MAGIC


def test_is_encrypted_file_detects_encrypted(app, tmp_path):
    """测试 is_encrypted_file 能正确识别加密文件"""
    with app.app_context():
        enc_path = tmp_path / "enc.bin"
        enc_path.write_bytes(b"plain")
        crypto.encrypt_file(str(enc_path))  # 原地加密
        assert crypto.is_encrypted_file(str(enc_path)) is True


def test_is_encrypted_file_rejects_plain(app, tmp_path):
    """测试 is_encrypted_file 不误判普通文件"""
    with app.app_context():
        plain = tmp_path / "plain.txt"
        plain.write_bytes(b"plain text not encrypted")
        assert crypto.is_encrypted_file(str(plain)) is False


def test_is_encrypted_file_missing_file(app, tmp_path):
    """测试 is_encrypted_file 对不存在的文件返回 False（不抛异常）"""
    with app.app_context():
        missing = str(tmp_path / "no_such_file.bin")
        assert crypto.is_encrypted_file(missing) is False


def test_decrypt_file_to_temp(app, tmp_path):
    """测试解密到临时文件后内容正确，且返回的临时路径可读取"""
    with app.app_context():
        enc = tmp_path / "enc.bin"
        content = b"temp decrypt test" * 5
        enc.write_bytes(crypto.encrypt_bytes(content))

        tmp = crypto.decrypt_file_to_temp(str(enc), suffix=".txt")
        try:
            with open(tmp, "rb") as f:
                assert f.read() == content
        finally:
            os.remove(tmp)


def test_encrypt_stream_to_file(app, tmp_path):
    """测试加密 Werkzeug FileStorage 流到目标文件后可正确解密"""
    with app.app_context():
        content = b"stream upload content" * 20
        fs = FileStorage(stream=BytesIO(content), filename="up.bin")
        dst = tmp_path / "up.enc"

        crypto.encrypt_stream_to_file(fs, str(dst))

        assert dst.read_bytes()[:4] == crypto.MAGIC
        assert crypto.decrypt_file(str(dst)) == content
