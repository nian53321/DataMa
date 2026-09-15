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
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

# os.O_BINARY 仅存在于 Windows，Unix 缺失时置 0 兼容
_O_BINARY = getattr(os, "O_BINARY", 0)

# ==================== 常量 ====================
MAGIC = b"DMEC"                       # DataManagement EnCrypted
VERSION = 1
DEK_SIZE = 32                         # AES-256 密钥长度
NONCE_SIZE = 12                       # GCM 推荐 12 字节 nonce
GCM_TAG_SIZE = 16                     # GCM 认证标签
ENC_DEK_SIZE = DEK_SIZE + GCM_TAG_SIZE  # 加密后的 DEK（密文 + tag）= 48
HEADER_SIZE = 4 + 1 + NONCE_SIZE + ENC_DEK_SIZE + NONCE_SIZE  # 77

# 主密钥缓存（进程级）：(文件 mtime_ns, 密钥字节)
# 每次调用 stat 一次 master.key，文件 mtime 变化即重载 → 多 worker 轮换/导入后
# 其他 worker 自动拿到新密钥，避免进程缓存不一致导致"用旧密钥加密、新密钥解不开"。
_master_key_cache = None
# 过渡期密钥缓存（master.key.previous，轮换/导入期间存在）：(mtime_ns, 密钥字节)
_previous_key_cache = None
# 进程内互斥锁：防止同一进程内并发加载/创建/轮换主密钥
_MASTER_KEY_LOCK = threading.Lock()


def _master_key_path():
    """主密钥文件路径（从配置读取，默认 back/master.key）"""
    from flask import current_app
    return current_app.config.get("MASTER_KEY_PATH") or os.path.join(
        current_app.config["BASE_DIR"], "master.key")


def _master_key_st_mtime():
    """主密钥文件 mtime_ns；文件不存在返回 None"""
    try:
        return os.stat(_master_key_path()).st_mtime_ns
    except OSError:
        return None


def _normalize_key_bytes(raw):
    """规范化密钥字节；兼容 Windows 文本模式写入导致的 \r\n 字节膨胀

    旧版本在 Windows 上使用 os.open（默认文本模式）写入 master.key，
    密钥中的 \n 被转换为 \r\n，导致文件长度 > DEK_SIZE。剥除 \r 后
    恰好为 DEK_SIZE 的视为文本模式膨胀，返回规范化后的密钥；其余情况
    （真实损坏）返回 None，由调用方按异常处理。
    """
    if len(raw) == DEK_SIZE:
        return raw
    stripped = raw.replace(b"\r\n", b"\n")
    if len(stripped) == DEK_SIZE:
        return stripped
    return None


def _load_or_create_master_key():
    """加载主密钥文件；不存在则生成 32 字节随机密钥并写入（权限 600）

    并发安全：使用 O_CREAT|O_EXCL 原子创建。多 worker 同时首次启动时，
    只有一个进程能创建成功，其余进程读取已创建的文件，保证所有进程使用同一密钥。
    """
    with _MASTER_KEY_LOCK:
        path = _master_key_path()
        if os.path.exists(path):
            with open(path, "rb") as f:
                raw = f.read()
            key = _normalize_key_bytes(raw)
            if key is not None:
                return key
            # 文件存在但长度异常，视为损坏（不自动覆盖，避免误删）
            raise RuntimeError(f"主密钥文件已存在但长度异常: {path}（期望 {DEK_SIZE} 字节）")
        # 生成新主密钥
        key = AESGCM.generate_key(bit_length=256)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 0600 权限 + O_EXCL 原子创建（O_BINARY 防止 Windows 文本模式换行转换）
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
        except FileExistsError:
            # 竞态：另一进程刚创建成功，读取其密钥，避免出现两份不一致的密钥
            with open(path, "rb") as f:
                raw = f.read()
            key = _normalize_key_bytes(raw)
            if key is None:
                raise RuntimeError(
                    f"主密钥文件已存在但长度异常: {path}（期望 {DEK_SIZE} 字节）")
            return key
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        return key


def get_master_key():
    """获取主密钥（进程级缓存 + 文件 mtime 失效检查）

    每次调用 stat 一次 master.key（微秒级开销）；文件被其他 worker 轮换/导入后
    mtime 变化 → 自动重载，保证多进程缓存一致性。
    """
    global _master_key_cache
    mtime = _master_key_st_mtime()
    if _master_key_cache is not None:
        cached_mtime, cached_key = _master_key_cache
        if cached_mtime == mtime:
            return cached_key
    key = _load_or_create_master_key()
    _master_key_cache = (_master_key_st_mtime(), key)
    return key


def _get_previous_key():
    """读取过渡期备用密钥（master.key.previous，轮换/导入期间存在）

    返回 None 表示无过渡密钥（正常状态）。
    """
    global _previous_key_cache
    path = _master_key_path() + ".previous"
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        _previous_key_cache = None
        return None
    if _previous_key_cache is not None:
        cached_mtime, cached_key = _previous_key_cache
        if cached_mtime == mtime:
            return cached_key
    with open(path, "rb") as f:
        raw = f.read()
    key = _normalize_key_bytes(raw)
    if key is None:
        _previous_key_cache = None
        return None
    _previous_key_cache = (mtime, key)
    return key


def reset_master_key_cache():
    """清除主密钥缓存（测试用）"""
    global _master_key_cache, _previous_key_cache
    _master_key_cache = None
    _previous_key_cache = None


# ==================== 字节级加解密 ====================

def _encrypt_bytes_with_key(plaintext: bytes, mk: bytes) -> bytes:
    """用指定主密钥加密字节流：返回 [header][encrypted_content]
    （供密钥轮换/导入使用，避免依赖进程级缓存，保证新旧密钥可显式指定）
    """
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


def _parse_header(data: bytes):
    """解析 DMEC 文件头，返回 (dek_nonce, enc_dek, content_nonce, enc_content)"""
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
    return dek_nonce, enc_dek, content_nonce, data[offset:]


def _rewrap_dek(data: bytes, old_mk: bytes, new_mk: bytes) -> bytes:
    """DEK 重包裹：用 old_mk 解开 DEK，再用 new_mk 重新加密（生成新 dek_nonce）

    内容密文与 content_nonce 保持不变 → 轮换/导入后文件内容字节不变，仅头部变化。
    用于密钥轮换：比"整体解密再重加密"更快且不引入内容重加密的失败面。
    """
    dek_nonce, enc_dek, content_nonce, enc_content = _parse_header(data)
    try:
        dek = AESGCM(old_mk).decrypt(dek_nonce, enc_dek, associated_data=MAGIC)
    except InvalidTag:
        raise ValueError("DEK 解密失败：主密钥不匹配或文件头已损坏")
    new_dek_nonce = os.urandom(NONCE_SIZE)
    new_enc_dek = AESGCM(new_mk).encrypt(new_dek_nonce, dek, associated_data=MAGIC)
    return MAGIC + bytes([VERSION]) + new_dek_nonce + new_enc_dek + content_nonce + enc_content


def _rewrap_file_dek(data: bytes, old_mk: bytes, new_mk: bytes) -> bytes:
    """将单个加密文件的 DEK 从 old_mk 重包裹为 new_mk（内容不变）

    兼容轮换崩溃恢复：若 old_mk 已解不开该文件（文件在之前一次中断的轮换中
    已被其他密钥重包裹），依次回退尝试 master.key.previous 中的过渡密钥，
    最后尝试 new_mk 本身（文件已被本次轮换重包裹的情况）。
    """
    try:
        return _rewrap_dek(data, old_mk, new_mk)
    except ValueError:
        pass
    prev = _get_previous_key()
    if prev is not None and prev != old_mk and prev != new_mk:
        try:
            return _rewrap_dek(data, prev, new_mk)
        except ValueError:
            pass
    return _rewrap_dek(data, new_mk, new_mk)


def encrypt_bytes_with_key(plaintext: bytes, mk: bytes) -> bytes:
    """用**指定**主密钥加密（不读进程缓存）

    用于「本包专用主密钥」导出：包内密文只能用随包那把主密钥解开，
    平台主密钥不出包（见 :mod:`app.utils.restore_kit`）。
    """
    return _encrypt_bytes_with_key(plaintext, mk)


def rewrap_dek_prefix(prefix: bytes, new_mk: bytes, old_mk: bytes = None) -> bytes:
    """只重包裹文件头里的 DEK（前 ``5 + NONCE + ENC_DEK`` 字节），内容密文不变

    大文件因此可以流式处理（见 :func:`rewrap_dmec_file`），无需整体读入内存。
    """
    if len(prefix) < 5 + NONCE_SIZE + ENC_DEK_SIZE:
        raise ValueError("文件过短，不是有效的加密文件")
    if prefix[:4] != MAGIC:
        raise ValueError("文件不是加密格式（魔数不匹配）")
    old = old_mk if old_mk is not None else get_master_key()
    dek_nonce = prefix[5:5 + NONCE_SIZE]
    enc_dek = prefix[5 + NONCE_SIZE:5 + NONCE_SIZE + ENC_DEK_SIZE]
    try:
        dek = AESGCM(old).decrypt(dek_nonce, enc_dek, associated_data=MAGIC)
    except InvalidTag:
        raise ValueError("DEK 解密失败：主密钥不匹配或文件头已损坏")
    new_nonce = os.urandom(NONCE_SIZE)
    new_enc_dek = AESGCM(new_mk).encrypt(new_nonce, dek, associated_data=MAGIC)
    return MAGIC + bytes([prefix[4]]) + new_nonce + new_enc_dek


def rewrap_dmec_file(src_path, dst_path, new_mk: bytes, old_mk: bytes = None):
    """把已加密文件的 DEK 重包裹到 ``new_mk``（流式，内容密文逐字节原样拷贝）

    :return: 写入的目标路径
    """
    import shutil
    head_len = 5 + NONCE_SIZE + ENC_DEK_SIZE
    with open(src_path, "rb") as f:
        prefix = f.read(head_len)
    new_prefix = rewrap_dek_prefix(prefix, new_mk, old_mk)
    with open(src_path, "rb") as fin, open(dst_path, "wb") as fout:
        fin.seek(head_len)
        fout.write(new_prefix)
        shutil.copyfileobj(fin, fout, length=1 << 20)
    return dst_path


def _decrypt_bytes_with_key(data: bytes, mk: bytes) -> bytes:
    """用指定主密钥解密字节流：输入 [header][encrypted_content]，返回明文"""
    dek_nonce, enc_dek, content_nonce, enc_content = _parse_header(data)
    try:
        dek = AESGCM(mk).decrypt(dek_nonce, enc_dek, associated_data=MAGIC)
    except InvalidTag:
        raise ValueError("DEK 解密失败：主密钥不匹配或文件头已损坏")
    try:
        return AESGCM(dek).decrypt(content_nonce, enc_content,
                                   associated_data=MAGIC + bytes([VERSION]))
    except InvalidTag:
        raise ValueError("内容解密失败：文件已损坏或被篡改")


def encrypt_bytes(plaintext: bytes) -> bytes:
    """加密字节流：返回 [header][encrypted_content]（使用进程级主密钥缓存）"""
    return _encrypt_bytes_with_key(plaintext, get_master_key())


def decrypt_bytes(data: bytes) -> bytes:
    """解密字节流：输入 [header][encrypted_content]，返回明文（使用进程级主密钥缓存）

    密钥轮换/导入过渡期内（master.key 已切换但文件尚未全部重包裹），当前主密钥
    解不开的文件自动回退尝试 master.key.previous 中的过渡密钥，保证全程可读。
    仅当报错为"主密钥不匹配"时回退；内容 GCM tag 校验失败（真实损坏）不回退。
    """
    try:
        return _decrypt_bytes_with_key(data, get_master_key())
    except ValueError as e:
        if "主密钥不匹配" not in str(e):
            raise
        prev = _get_previous_key()
        if prev is not None:
            return _decrypt_bytes_with_key(data, prev)
        raise


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


def _collect_encrypted_files(data_lake_dir):
    """遍历数据湖，收集所有 DMEC 加密文件路径（跳过 .transcodes 转码缓存）"""
    result = []
    if not os.path.isdir(data_lake_dir):
        return result
    for root, dirs, files in os.walk(data_lake_dir):
        # 跳过转码缓存目录（.transcodes 内为派生临时文件，不加密）
        if ".transcodes" in dirs:
            dirs.remove(".transcodes")
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if is_encrypted_file(fpath):
                    result.append(fpath)
            except OSError:
                pass
    return result


def count_encrypted_files(data_lake_dir):
    """统计数据湖中已加密的文件数量"""
    count = 0
    total_size = 0
    for fpath in _collect_encrypted_files(data_lake_dir):
        count += 1
        try:
            total_size += os.path.getsize(fpath)
        except OSError:
            pass
    return {"encrypted_count": count, "total_size": total_size}


def _acquire_key_operation_lock():
    """获取跨进程密钥操作互斥锁（防止并发轮换/导入）

    锁文件：master.key.lock。跨平台实现：
      - Windows: msvcrt.locking（文件需至少有 1 字节才能锁定）
      - Linux/macOS: fcntl.flock
    返回锁 fd；调用方必须在 finally 中调用 _release_key_operation_lock。
    """
    path = _master_key_path() + ".lock"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | _O_BINARY, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\x00")  # 保证至少有 1 字节可锁定
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            os.lseek(fd, 0, os.SEEK_SET)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        pass  # 锁定失败不阻塞（best-effort），进程内 _MASTER_KEY_LOCK 仍有兜底
    return fd


def _release_key_operation_lock(fd):
    """释放密钥操作互斥锁"""
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _swap_master_key(old_mk, new_mk, data_lake_dir, old_fp):
    """用 new_mk 替换 old_mk 的公共流程（轮换与导入共用）

    安全顺序（重排 + 备份）：
      1. 备份旧密钥 → master.key.bak-<时间戳>（0600）
      2. 发布新密钥到 master.key.previous（过渡期解密回退）
      3. 逐文件 DEK 重包裹（master.key 仍是旧密钥；全部成功才进入第 4 步）
      4. 全部成功后原子替换 master.key
      5. 删除 master.key.previous + 刷新进程缓存

    失败安全：任一步失败，master.key 要么保持旧密钥（第 3 步失败），要么处于
    过渡态（.previous 存在，新旧文件都可被读取），数据不会丢失，重试即可；
    备份文件用于极端情况的恢复。

    返回 (old_fingerprint, new_fingerprint, reencrypted_count)
    """
    global _master_key_cache, _previous_key_cache
    path = _master_key_path()

    # 1. 备份旧密钥（时间戳含毫秒，避免同秒重复）
    backup_path = f"{path}.bak-{time.strftime('%Y%m%d%H%M%S')}{int(time.time() * 1000) % 1000:03d}"
    with open(path, "rb") as f:
        old_key_bytes = _normalize_key_bytes(f.read())
    bfd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
    try:
        os.write(bfd, old_key_bytes)
    finally:
        os.close(bfd)

    # 2. 过渡密钥管理：
    #    若 .previous 已存在（上次轮换中断的崩溃恢复），保留其中密钥供重包裹循环
    #    回退使用，避免数据被多种密钥分散包裹；否则发布新密钥到 .previous，
    #    保证循环期间"新重包裹的文件"始终可读。
    prev_path = path + ".previous"
    if not os.path.exists(prev_path):
        pfd = os.open(prev_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY, 0o600)
        try:
            os.write(pfd, new_mk)
        finally:
            os.close(pfd)

    # 3. 逐文件 DEK 重包裹（原子写回；任一文件失败即中止，master.key 不变）
    encrypted_files = _collect_encrypted_files(data_lake_dir)
    for fpath in encrypted_files:
        with open(fpath, "rb") as f:
            data = f.read()
        rewrapped = _rewrap_file_dek(data, old_mk, new_mk)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(fpath), prefix=".rewrap-")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(rewrapped)
            os.replace(tmp_path, fpath)  # 原子替换，读方永远看到完整旧头或完整新头
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    # 4. 全部成功后原子替换 master.key
    #    先刷新 .previous 为新密钥，封口"已重包裹文件"的读取间隙，再替换 master.key。
    pfd = os.open(prev_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY, 0o600)
    try:
        os.write(pfd, new_mk)
    finally:
        os.close(pfd)
    mk_tmp_fd, mk_tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".mk-")
    try:
        with os.fdopen(mk_tmp_fd, "wb") as f:
            f.write(new_mk)
        os.chmod(mk_tmp_path, 0o600)
        os.replace(mk_tmp_path, path)
    except BaseException:
        try:
            os.remove(mk_tmp_path)
        except OSError:
            pass
        raise

    # 5. 清理过渡密钥 + 刷新缓存
    try:
        os.remove(prev_path)
    except OSError:
        pass
    _master_key_cache = (_master_key_st_mtime(), new_mk)
    _previous_key_cache = None

    new_fp = get_key_fingerprint(new_mk)
    return old_fp, new_fp, len(encrypted_files)


def rotate_master_key(data_lake_dir):
    """轮换主密钥：生成新密钥，重新加密所有已加密文件（DEK 重包裹，内容不变）

    安全顺序（重排 + 备份）：
      - 先备份旧密钥，再逐文件用新密钥重包裹，全部成功后才原子替换 master.key。
      - 任一步失败 master.key 不更新（或保持过渡态可读），数据不会丢失。
      - 崩溃恢复：若存在 master.key.previous（上次轮换中断），沿用其中的密钥
        继续完成轮换，避免数据被多种密钥分散包裹。

    返回 (old_fingerprint, new_fingerprint, reencrypted_count)
    """
    lock_fd = _acquire_key_operation_lock()
    try:
        old_mk = get_master_key()
        old_fp = get_key_fingerprint(old_mk)
        prev = _get_previous_key()
        new_mk = prev if prev is not None else AESGCM.generate_key(bit_length=256)
        return _swap_master_key(old_mk, new_mk, data_lake_dir, old_fp)
    finally:
        _release_key_operation_lock(lock_fd)


def verify_key_integrity(data_lake_dir):
    """验证主密钥完整性：尝试解密全部已加密文件，检查密钥是否匹配"""
    encrypted_files = _collect_encrypted_files(data_lake_dir)
    if not encrypted_files:
        return {"valid": True, "checked": 0, "message": "数据湖目录为空，无需验证"}

    checked = 0
    failed = 0
    failed_files = []
    for fpath in encrypted_files:
        try:
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
    """导入/替换主密钥：用新密钥重新加密所有已加密文件（DEK 重包裹）

    与 rotate_master_key 相同的安全顺序（备份 → 发布 → 重包裹 → 原子替换 → 清理）。
    参数：
      key_bytes: 新主密钥字节（必须 32 字节）
      data_lake_dir: 数据湖目录
    返回 (old_fingerprint, new_fingerprint, reencrypted_count)
    """
    if not isinstance(key_bytes, bytes) or len(key_bytes) != DEK_SIZE:
        raise ValueError(f"密钥文件无效：必须为 {DEK_SIZE} 字节的二进制数据")

    lock_fd = _acquire_key_operation_lock()
    try:
        old_mk = get_master_key()
        old_fp = get_key_fingerprint(old_mk)
        return _swap_master_key(old_mk, key_bytes, data_lake_dir, old_fp)
    finally:
        _release_key_operation_lock(lock_fd)


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


