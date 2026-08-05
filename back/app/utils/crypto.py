# -*- coding: utf-8 -*-
"""文件加解密模块：信封加密方案（Envelope Encryption）

设计：
- 主密钥 (Master Key, MK)：AES-256，存放于本地文件 back/master.key（权限 600）
- 数据加密密钥 (DEK)：每个文件独立生成，用 MK 加密后写入文件头
- 内容加密：AES-256-GCM（带认证的对称加密），DEK 加密文件内容

文件格式（header 共 77 字节）：
    [Magic 4B "DMEC"][Version 1B][DEK Nonce 12B][Encrypted DEK 48B][Content Nonce 12B][Encrypted Content ...]

说明：
- AES-GCM 需要完整密文才能校验 tag，因此采用整文件加解密（适合本平台数据规模）
- 转码缓存 (.transcodes/) 为派生临时文件，不加密（源文件已加密，缓存可随时清除）
- 解密播放时先解密到临时文件再 send_file，响应结束后清理临时文件
"""
import os
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

# ==================== 常量 ====================
MAGIC = b"DMEC"                       # DataManagement EnCrypted
VERSION = 1
DEK_SIZE = 32                         # AES-256 密钥长度
NONCE_SIZE = 12                       # GCM 推荐 12 字节 nonce
GCM_TAG_SIZE = 16                     # GCM 认证标签
ENC_DEK_SIZE = DEK_SIZE + GCM_TAG_SIZE  # 加密后的 DEK（密文 + tag）= 48
HEADER_SIZE = 4 + 1 + NONCE_SIZE + ENC_DEK_SIZE + NONCE_SIZE  # 77

# 主密钥缓存（进程级）
_master_key_cache = None


def _master_key_path():
    """主密钥文件路径（从配置读取，默认 back/master.key）"""
    from flask import current_app
    return current_app.config.get("MASTER_KEY_PATH") or os.path.join(
        current_app.config["BASE_DIR"], "master.key")


def _load_or_create_master_key():
    """加载主密钥文件；不存在则生成 32 字节随机密钥并写入（权限 600）"""
    path = _master_key_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            key = f.read()
        if len(key) == DEK_SIZE:
            return key
        # 文件存在但长度异常，视为损坏（不自动覆盖，避免误删）
        raise RuntimeError(f"主密钥文件已存在但长度异常: {path}（期望 {DEK_SIZE} 字节）")
    # 生成新主密钥
    key = AESGCM.generate_key(bit_length=256)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # 以 0600 权限写入（仅所有者可读写）
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def get_master_key():
    """获取主密钥（进程级缓存，首次访问时加载/生成）"""
    global _master_key_cache
    if _master_key_cache is None:
        _master_key_cache = _load_or_create_master_key()
    return _master_key_cache


def reset_master_key_cache():
    """清除主密钥缓存（测试用）"""
    global _master_key_cache
    _master_key_cache = None


# ==================== 字节级加解密 ====================

def encrypt_bytes(plaintext: bytes) -> bytes:
    """加密字节流：返回 [header][encrypted_content]"""
    mk = get_master_key()
    # 1. 生成独立 DEK
    dek = AESGCM.generate_key(bit_length=256)
    # 2. 用 MK 加密 DEK（AAD = MAGIC，防止头篡改）
    dek_nonce = os.urandom(NONCE_SIZE)
    enc_dek = AESGCM(mk).encrypt(dek_nonce, dek, associated_data=MAGIC)
    # 3. 用 DEK 加密内容（AAD = MAGIC + version，绑定版本）
    content_nonce = os.urandom(NONCE_SIZE)
    aad = MAGIC + bytes([VERSION])
    enc_content = AESGCM(dek).encrypt(content_nonce, plaintext, associated_data=aad)
    # 4. 拼装：magic + version + dek_nonce + enc_dek + content_nonce + enc_content
    return MAGIC + bytes([VERSION]) + dek_nonce + enc_dek + content_nonce + enc_content


def decrypt_bytes(data: bytes) -> bytes:
    """解密字节流：输入 [header][encrypted_content]，返回明文"""
    if len(data) < HEADER_SIZE:
        raise ValueError("文件过短，不是有效的加密文件")
    if data[:4] != MAGIC:
        raise ValueError("文件不是加密格式（魔数不匹配）")
    version = data[4]
    if version != VERSION:
        raise ValueError(f"不支持的加密版本: {version}")
    offset = 5
    dek_nonce = data[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    enc_dek = data[offset:offset + ENC_DEK_SIZE]
    offset += ENC_DEK_SIZE
    content_nonce = data[offset:offset + NONCE_SIZE]
    offset += NONCE_SIZE
    enc_content = data[offset:]
    mk = get_master_key()
    try:
        dek = AESGCM(mk).decrypt(dek_nonce, enc_dek, associated_data=MAGIC)
    except InvalidTag:
        raise ValueError("DEK 解密失败：主密钥不匹配或文件头已损坏")
    try:
        return AESGCM(dek).decrypt(content_nonce, enc_content,
                                   associated_data=MAGIC + bytes([VERSION]))
    except InvalidTag:
        raise ValueError("内容解密失败：文件已损坏或被篡改")


# ==================== 文件级加解密 ====================

def is_encrypted_file(path) -> bool:
    """检查文件是否为加密格式（读取前 4 字节魔数）"""
    try:
        with open(path, "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False


def encrypt_file(src_path, dst_path=None):
    """加密文件：src_path -> dst_path（默认原地覆盖）
    适合小/中文件（整文件读入内存）。大文件可改用流式分块方案。
    """
    if dst_path is None:
        dst_path = src_path
    with open(src_path, "rb") as f:
        plaintext = f.read()
    encrypted = encrypt_bytes(plaintext)
    with open(dst_path, "wb") as f:
        f.write(encrypted)


def decrypt_file(src_path) -> bytes:
    """读取加密文件并返回明文字节"""
    with open(src_path, "rb") as f:
        data = f.read()
    return decrypt_bytes(data)


def decrypt_file_to_temp(src_path, suffix="") -> str:
    """解密文件到临时文件（用于 send_file / ffmpeg 等需要文件路径的场景）
    返回临时文件路径，调用方负责删除。
    """
    plaintext = decrypt_file(src_path)
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        os.write(fd, plaintext)
    finally:
        os.close(fd)
    return tmp_path


def encrypt_stream_to_file(file_storage, dst_path):
    """加密 Werkzeug FileStorage 流到目标文件
    用于上传接口：直接读取上传流 -> 加密 -> 落盘，避免中间临时文件
    """
    plaintext = file_storage.read()
    encrypted = encrypt_bytes(plaintext)
    with open(dst_path, "wb") as f:
        f.write(encrypted)


# ==================== 密钥管理 ====================

import hashlib
import time


def get_key_fingerprint(key_bytes=None):
    """计算主密钥指纹（SHA-256 前 16 字节的十六进制，格式：XX:XX:XX:...）"""
    mk = key_bytes if key_bytes is not None else get_master_key()
    digest = hashlib.sha256(mk).hexdigest()[:32]
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2)).upper()


def get_key_info():
    """获取主密钥的元信息（不泄露密钥本身）"""
    path = _master_key_path()
    exists = os.path.exists(path)
    info = {
        "key_path": path,
        "exists": exists,
        "key_size": DEK_SIZE,
        "algorithm": "AES-256-GCM",
        "encrypt_enabled": True,
    }
    if exists:
        stat = os.stat(path)
        info["fingerprint"] = get_key_fingerprint()
        info["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime))
        info["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        info["file_size"] = stat.st_size
    else:
        info["fingerprint"] = None
        info["created_at"] = None
        info["updated_at"] = None
        info["file_size"] = 0
    return info


def count_encrypted_files(data_lake_dir):
    """统计数据湖中已加密的文件数量"""
    count = 0
    total_size = 0
    if not os.path.isdir(data_lake_dir):
        return {"encrypted_count": 0, "total_size": 0}
    for root, dirs, files in os.walk(data_lake_dir):
        # 跳过转码缓存目录（.transcodes 内为派生临时文件，不加密）
        if ".transcodes" in dirs:
            dirs.remove(".transcodes")
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if is_encrypted_file(fpath):
                    count += 1
                    total_size += os.path.getsize(fpath)
            except OSError:
                pass
    return {"encrypted_count": count, "total_size": total_size}


def rotate_master_key(data_lake_dir):
    """轮换主密钥：生成新密钥，重新加密所有已加密文件
    返回 (old_fingerprint, new_fingerprint, reencrypted_count)
    """
    old_mk = get_master_key()
    old_fp = get_key_fingerprint(old_mk)

    # 1. 收集所有已加密文件
    encrypted_files = []
    if os.path.isdir(data_lake_dir):
        for root, dirs, files in os.walk(data_lake_dir):
            if ".transcodes" in dirs:
                dirs.remove(".transcodes")
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if is_encrypted_file(fpath):
                        encrypted_files.append(fpath)
                except OSError:
                    pass

    # 2. 用旧密钥解密所有文件内容（暂存内存）
    decrypted_data = []
    for fpath in encrypted_files:
        with open(fpath, "rb") as f:
            data = f.read()
        plaintext = decrypt_bytes(data)
        decrypted_data.append((fpath, plaintext))

    # 3. 生成新主密钥并写入文件
    path = _master_key_path()
    new_mk = AESGCM.generate_key(bit_length=256)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, new_mk)
    finally:
        os.close(fd)

    # 4. 更新缓存
    global _master_key_cache
    _master_key_cache = new_mk

    # 5. 用新密钥重新加密所有文件
    for fpath, plaintext in decrypted_data:
        encrypted = encrypt_bytes(plaintext)
        with open(fpath, "wb") as f:
            f.write(encrypted)

    new_fp = get_key_fingerprint(new_mk)
    return old_fp, new_fp, len(encrypted_files)


def verify_key_integrity(data_lake_dir):
    """验证主密钥完整性：尝试解密全部已加密文件，检查密钥是否匹配"""
    if not os.path.isdir(data_lake_dir):
        return {"valid": True, "checked": 0, "message": "数据湖目录为空，无需验证"}

    checked = 0
    failed = 0
    failed_files = []
    for root, dirs, files in os.walk(data_lake_dir):
        if ".transcodes" in dirs:
            dirs.remove(".transcodes")
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if is_encrypted_file(fpath):
                    decrypt_file(fpath)
                    checked += 1
            except Exception:
                failed += 1
                checked += 1
                failed_files.append(fpath)

    if failed == 0:
        return {"valid": True, "checked": checked, "message": f"已验证全部 {checked} 个加密文件，主密钥匹配正常"}
    else:
        return {
            "valid": False, "checked": checked, "failed": failed,
            "failed_files": failed_files[:20],
            "message": f"验证失败：{failed}/{checked} 个文件解密异常，主密钥可能不匹配",
        }


def export_master_key():
    """导出主密钥字节（用于备份下载，仅管理员可调用）"""
    return get_master_key()


def import_master_key(key_bytes, data_lake_dir):
    """导入/替换主密钥：用新密钥重新加密所有已加密文件
    参数：
      key_bytes: 新主密钥字节（必须 32 字节）
      data_lake_dir: 数据湖目录
    返回 (old_fingerprint, new_fingerprint, reencrypted_count)
    """
    if not isinstance(key_bytes, bytes) or len(key_bytes) != DEK_SIZE:
        raise ValueError(f"密钥文件无效：必须为 {DEK_SIZE} 字节的二进制数据")

    old_mk = get_master_key()
    old_fp = get_key_fingerprint(old_mk)

    # 1. 收集所有已加密文件并用旧密钥解密
    decrypted_data = []
    if os.path.isdir(data_lake_dir):
        for root, dirs, files in os.walk(data_lake_dir):
            if ".transcodes" in dirs:
                dirs.remove(".transcodes")
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if is_encrypted_file(fpath):
                        with open(fpath, "rb") as f:
                            data = f.read()
                        plaintext = decrypt_bytes(data)
                        decrypted_data.append((fpath, plaintext))
                except Exception:
                    pass  # 跳过无法解密的文件

    # 2. 写入新主密钥
    path = _master_key_path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key_bytes)
    finally:
        os.close(fd)

    # 3. 更新缓存
    global _master_key_cache
    _master_key_cache = key_bytes

    # 4. 用新密钥重新加密所有文件
    for fpath, plaintext in decrypted_data:
        encrypted = encrypt_bytes(plaintext)
        with open(fpath, "wb") as f:
            f.write(encrypted)

    new_fp = get_key_fingerprint(key_bytes)
    return old_fp, new_fp, len(decrypted_data)


# ==================== 外部加密格式适配（AES-256-CBC） ====================
#
# 应用场景：外部采集设备（如 AgeCog）导出的加密文件，使用固定 AES-256-CBC + PKCS7
# 方案，密钥与 IV 存放在同目录 `密钥.txt` 中（Base64 编码）。
# 与项目自身的 DMEC 信封加密完全不兼容，本节提供独立适配函数，让 scanner 能
# 透明地读取这类外部加密文件并将其重新加密为 DMEC 格式存入数据湖。
#
# 密钥.txt 格式：
#     CRYPTO_AES_KEY=<base64-encoded 32-byte key>
#     CRYPTO_AES_IV=<base64-encoded 16-byte iv>
#
# 文件特征：
#     - 后缀 .enc
#     - 内容为二进制密文（无魔数/无结构化头部）
#     - 部分小文件（如 userInfo.json.enc）整体为 Base64 编码后再 AES-CBC 加密
#       —— 实测发现外部工具对小文件会做 Base64 包装，对大文件直接输出二进制密文
#       本适配层会自动探测并处理两种形态

import base64
import binascii

# 密钥.txt 中允许的密钥行前缀（兼容大小写）
_EXT_KEY_PREFIXES = ("CRYPTO_AES_KEY=", "crypto_aes_key=")
_EXT_IV_PREFIXES = ("CRYPTO_AES_IV=", "crypto_aes_iv=")

# 外部加密文件后缀
EXTERNAL_ENC_SUFFIX = ".enc"


def load_external_key(key_file_path):
    """从外部密钥文件加载 (key, iv)

    密钥文件格式（每行一个 KEY=VALUE）：
        CRYPTO_AES_KEY=<base64 32字节>
        CRYPTO_AES_IV=<base64 16字节>

    返回: (key_bytes, iv_bytes)
    异常: FileNotFoundError / ValueError / binascii.Error
    """
    with open(key_file_path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    key_b64, iv_b64 = None, None
    for ln in lines:
        if "=" not in ln:
            continue
        prefix, _, value = ln.partition("=")
        prefix_lower = prefix.lower()
        if prefix_lower == "crypto_aes_key":
            key_b64 = value
        elif prefix_lower == "crypto_aes_iv":
            iv_b64 = value

    if not key_b64:
        raise ValueError(f"密钥文件未找到 CRYPTO_AES_KEY: {key_file_path}")
    if not iv_b64:
        raise ValueError(f"密钥文件未找到 CRYPTO_AES_IV: {key_file_path}")

    key_bytes = base64.b64decode(key_b64)
    iv_bytes = base64.b64decode(iv_b64)

    if len(key_bytes) != DEK_SIZE:
        raise ValueError(
            f"外部 AES Key 长度异常: {len(key_bytes)} 字节（期望 {DEK_SIZE}）"
        )
    if len(iv_bytes) != 16:
        raise ValueError(
            f"外部 AES IV 长度异常: {len(iv_bytes)} 字节（期望 16）"
        )
    return key_bytes, iv_bytes


def is_external_encrypted_file(path):
    """检测文件是否为外部加密格式（.enc 后缀且非 DMEC 格式）

    判定规则：
    - 必须有 .enc 后缀
    - 前 4 字节不是 DMEC 魔数（避免与项目自身格式混淆）
    """
    if not path.lower().endswith(EXTERNAL_ENC_SUFFIX):
        return False
    if not os.path.isfile(path):
        return False
    # 排除项目自身的 DMEC 格式
    if is_encrypted_file(path):
        return False
    return True


def _try_base64_decode(data):
    """尝试将数据当作 Base64 解码；失败返回 None

    外部工具对小文件（如 userInfo.json）会先 AES-CBC 加密再 Base64 编码，
    大文件直接输出二进制密文。本函数用于自动探测。
    """
    # 快速预筛：Base64 文本只含 [A-Za-z0-9+/=] 与空白
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    import re
    if not re.match(r"^[A-Za-z0-9+/=\s]+$", text):
        return None
    # 移除所有空白后解码
    cleaned = "".join(text.split())
    # 长度必须是 4 的倍数
    if len(cleaned) % 4 != 0:
        return None
    try:
        return base64.b64decode(cleaned)
    except (binascii.Error, ValueError):
        return None


def decrypt_external_bytes(data, key, iv):
    """用 AES-256-CBC + PKCS7 解密外部加密数据

    自动处理两种形态：
    1. 整体为 Base64 文本 → 先 Base64 解码再 AES-CBC 解密
    2. 直接二进制密文 → 直接 AES-CBC 解密

    返回: 明文字节
    异常: ValueError（数据非 16 倍数 / padding 错误 / 解密失败）
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    # 自动探测 Base64 包装
    b64_decoded = _try_base64_decode(data)
    cipher_data = b64_decoded if b64_decoded is not None else data

    if len(cipher_data) == 0 or len(cipher_data) % 16 != 0:
        raise ValueError(
            f"外部加密数据长度异常: {len(cipher_data)} 字节（需为 16 的倍数）"
        )

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_data) + decryptor.finalize()

    # 去 PKCS7 padding
    unpadder = PKCS7(128).unpadder()
    try:
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except ValueError as e:
        raise ValueError(f"PKCS7 去填充失败（密钥/IV 可能不匹配）: {e}")
    return plaintext


def decrypt_external_file(file_path, key, iv):
    """读取外部加密文件并返回明文字节"""
    with open(file_path, "rb") as f:
        data = f.read()
    return decrypt_external_bytes(data, key, iv)


def decrypt_external_bytes_auto(data, candidate_keys):
    """用多组候选密钥自动尝试解密外部加密数据

    应用场景：数据库中存储了多组外部密钥（不同来源/批次），scanner 扫描到 .enc
    文件时不知道属于哪一组，需依次尝试，PKCS7 padding 校验通过的即为正确密钥。

    :param data: 加密数据（二进制密文 或 Base64 包装文本）
    :param candidate_keys: 候选密钥列表，每项为 (key_id, key_bytes, iv_bytes)
                           key_id 用于标识匹配的密钥（如数据库主键）
    :returns: (plaintext_bytes, matched_key_id) 匹配成功
    :raises ValueError: 所有候选密钥都无法解密
    :raises ValueError: candidate_keys 为空
    """
    if not candidate_keys:
        raise ValueError("未提供候选外部密钥，无法尝试解密")

    last_error = None
    for key_id, key_bytes, iv_bytes in candidate_keys:
        try:
            plaintext = decrypt_external_bytes(data, key_bytes, iv_bytes)
            return plaintext, key_id
        except (ValueError, Exception) as e:
            # PKCS7 校验失败 / 长度异常 → 尝试下一组
            last_error = e
            continue
    # 所有密钥都失败
    raise ValueError(
        f"所有 {len(candidate_keys)} 组候选外部密钥均无法解密此文件"
        + (f"（最后错误: {last_error}）" if last_error else "")
    )


def decrypt_external_file_auto(file_path, candidate_keys):
    """文件级多密钥自动解密（decrypt_external_bytes_auto 的文件封装）

    :returns: (plaintext_bytes, matched_key_id)
    """
    with open(file_path, "rb") as f:
        data = f.read()
    return decrypt_external_bytes_auto(data, candidate_keys)


