#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脑电 / 心电 / 音频「值级脱敏」导出包 —— 随包离线还原脚本

只依赖 Python 标准库（3.8+），**不需要安装本平台、不需要任何第三方包**。
把脱敏后的 CSV 与音频 WAV 逐字节还原为导出前的原始文件。

用法
----
**什么都不用传**：解压导出包后直接运行本脚本（Windows 下双击也行），它会自己找到
包根的 ``_desens_manifest.json``，把包内所有脑电/心电 CSV 与音频 WAV
**就地还原并覆盖**原文件::

    python restore_signal_desens.py

等价的显式写法（效果相同）::

    python restore_signal_desens.py . --in-place

其它用法::

    # 还原到另一个目录（不动包内原文件）
    python restore_signal_desens.py . -o 还原结果目录

    # 直接对未解压的 zip 操作（zip 无法就地覆盖，默认输出到 ./restored）
    python restore_signal_desens.py 导出包.zip

    # 只看包内清单（哪些文件、哪些列、各列怎么脱敏的），不还原
    python restore_signal_desens.py --list

    # 覆盖前给每个文件留一份 .bak 备份
    python restore_signal_desens.py --backup

就地覆盖的三条保证
------------------
1. **先写临时文件再原子替换** —— 直接往原路径写会先把源文件截断成 0 字节。
2. **sha256 不符就放弃覆盖** —— 还原结果先与清单里的原文 sha256 比对，一致才替换；
   不一致则删除临时文件、原文件保持原样（通常是密钥不对）。
3. **重复运行安全** —— 已还原过的文件 sha256 与清单一致，会被识别并跳过，
   不会被二次减噪破坏。

加密导出包（包内是 ``.dmec``）
------------------------------
导出时若选了「加密导出」，包内的数据文件是平台 DMEC 信封加密文件
（AES-256-GCM，信封结构）。本脚本**内置解密**：给得出主密钥就能一步完成
「解密 + 去脱敏」，直接拿到原始 CSV。解密同样只用标准库（脚本内自带一份
AES-256-GCM 实现；若环境里装了 ``cryptography`` 会自动改用它，快约 50 倍）。

**包内所有 ``.dmec`` 都会被解密，不限于脑电/心电。** 解密深度分两档，
因为这两类文件的脱敏性质本来就不同：

===================  ==========================  ==============================
文件                 解密后得到                  说明
===================  ==========================  ==============================
脑电 / 心电 CSV       **原始文件**（逐字节一致）   值级脱敏可逆，凭包内密钥 + 清单还原
音频 WAV             **原始文件**（逐字节一致）   定点加噪可逆，凭包内密钥 + 清单还原
其它所有文件         **脱敏后的明文**             脱敏不可逆，只能解开密文信封
===================  ==========================  ==============================

也就是说：视频解密后**人脸仍是马赛克块**（人脸马赛克是结构性破坏、不可逆，包里没有
任何可回推原人脸的参数）；userInfo 解密后身份字段仍是掩码；眼动/步态等未做值级脱敏的
资产，解密后即等于原文。脚本会在报告里把这两档分开列出，不会让"已解密"被
误读成"已还原成原始数据"。

音频（可逆脱敏，v2）
-------------------
音频**不做变调**：变调是相位声码器、结构上有损，反变换只能部分恢复（实测谱域
只回退 19%，长时平均谱零改善）。脚本改为按**定点 PCM 域确定性加噪**还原：
``x = (y - n) mod 2^bits``，减法即逆运算、逐位精确。噪声幅度 R 与密钥都由
清单给定（``r_int_by_ch`` / ``key``），样本区之外的字节（RIFF 头块等）
逐字节保留，因此还原产物与原音频文件**完全一致**，sha256 校验会确认这一点。
非整数 PCM 的源（mp3/m4a/浮点 wav）在导出侧已转成 16-bit PCM WAV，
清单里 ``canonical: true`` 会标明 —— 此时还原产物是该规范形态
（采样率/声道/帧数与样本值精确还原，但不是原压缩容器）。

    主密钥来源（按优先级）
      1. --master-key HEX      命令行直接给 64 位十六进制主密钥
      2. --master-key-file 文件  平台 master.key（32 字节二进制）或 hex 文本
      3. 包内 _restore_kit/master.key（导出时勾选了「随包附带还原包」才会有）
      4. 环境变量 DM_MASTER_KEY / MASTER_KEY_PATH

    解密后的文件会去掉 ``.dmec`` 后缀写出（``eeg_xxx.csv.dmec`` → ``eeg_xxx.csv``），
    原 ``.dmec`` 文件**保留不动**（可自行删除）。

    ⚠ 没有主密钥时，加密条目会被跳过并在结尾给出明确提示 —— 不会静默产出
    未解密/未还原的文件。DMEC 的 GCM 认证标签也会校验：密钥不对或文件被改过
    都会直接报错，不会解出看似正常实则错误的明文。

    ⚠ 包内文件是否加密**按 DMEC 魔数判定**，不按文件名后缀。若遇到以 ``.dmec``
    结尾但内容不是 DMEC 的文件（上游异常），会明确记入跳过原因，不会当成密文
    强行解密，也不会静默当作明文输出。

没有清单时也能用
----------------
``_desens_manifest.json`` 只描述脑电/心电/音频的**去脱敏参数**。若包里没有这些
文件（或清单缺失），脚本不会报错退出，而是降级为「只解密」：把所有 ``.dmec``
解成脱敏后的明文。此时不需要脱敏密钥，只需要主密钥。


退出码
------
    0 = 全部还原成功且校验和相符
    1 = 还原完成但 sha256 与清单不符（密钥不对，或包被改动过）
    2 = 有文件被跳过（需看报告里的原因）
    3 = 找不到脱敏密钥
    4 = 找不到还原清单 _desens_manifest.json
"""
import argparse
import csv
import hashlib
import hmac
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from array import array

MANIFEST_NAME = "_desens_manifest.json"
KIT_DIR = "_restore_kit"
KIT_KEY_NAME = "desens.key"
KIT_MASTER_KEY_NAME = "master.key"
VERSION_TAG = "signal-desens-v2"
AUDIO_VERSION_TAG = "audio-desens-v2"
# 音频密钥流的分块长度（帧）。**必须与 app/utils/audio_desensitize.CHUNK_FRAMES
# 完全一致** —— 噪声按「块号」派生，改了这个值噪声流就对不上。
AUDIO_CHUNK_FRAMES = 1 << 14
MAX_DECIMALS = 9
NUM_RE = re.compile(r"^([+-]?)(\d+)(?:\.(\d*))?$")

# DMEC 信封格式（与 app/utils/crypto.py 一致）
DMEC_MAGIC = b"DMEC"
DMEC_VERSION = 1
DMEC_NONCE_SIZE = 12
DMEC_ENC_DEK_SIZE = 48          # DEK 密文 32B + GCM tag 16B
DMEC_HEADER_SIZE = 4 + 1 + DMEC_NONCE_SIZE + DMEC_ENC_DEK_SIZE + DMEC_NONCE_SIZE
DMEC_SUFFIX = ".dmec"


# ==================== DMEC 解密（AES-256-GCM，纯标准库） ====================
#
# 标准库没有 AES，而随包脚本必须「解压即跑」。这里自带一份 AES-256-GCM
# 解密实现（只实现解密所需的方向：AES 加密 + GHASH）。若运行环境装有
# ``cryptography`` 则自动改用它（快约 50 倍），结果完全一致。
#
# 实现要点（两个极易踩错、且错了也能跑出「看似正常」结果的地方）：
#   1. GCM 用 **bit-reversed** 域表示（bit(127-i) = x^i 的系数）→ 乘 x 是**右移**，
#      约简常量是 0xe1||0^120；从整数低位端取出的 nibble 才是最高幂项。
#   2. GHASH 的链式结构是 Y_i = (Y_{i-1} XOR X_i)·H：必须**先异或当前块、再整体
#      乘 H**。把「乘 H 的 Horner 循环」与「跨块累积」共用一个累加器会算成
#      z·x^128 XOR y·H —— 校验必然失败。

_MASK128 = (1 << 128) - 1


def _xtime(a):
    """GF(2^8) 乘 2（生成 S-box 用）；结果保持 8 位"""
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _build_aes_tables():
    exp = [0] * 256
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = (x ^ _xtime(x)) & 0xFF
    sbox = [0] * 256
    for a in range(256):
        inv = 0 if a == 0 else exp[(255 - log[a]) % 255]
        s = inv
        for _ in range(4):
            inv = ((inv << 1) | (inv >> 7)) & 0xFF
            s ^= inv
        sbox[a] = s ^ 0x63
    te0, te1, te2, te3 = [], [], [], []
    for a in range(256):
        s = sbox[a]
        s2 = _xtime(s)
        w = (s2 << 24) | (s << 16) | (s << 8) | (s2 ^ s)
        te0.append(w)
        te1.append(((w << 24) | (w >> 8)) & 0xFFFFFFFF)
        te2.append(((w << 16) | (w >> 16)) & 0xFFFFFFFF)
        te3.append(((w << 8) | (w >> 24)) & 0xFFFFFFFF)
    return sbox, te0, te1, te2, te3


_SBOX, _TE0, _TE1, _TE2, _TE3 = _build_aes_tables()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40,
         0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


def _key_expansion(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [struct.unpack(">I", key[4 * i:4 * i + 4])[0] for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1]
        if i % nk == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = ((_SBOX[(t >> 24) & 0xFF] << 24) | (_SBOX[(t >> 16) & 0xFF] << 16)
                 | (_SBOX[(t >> 8) & 0xFF] << 8) | _SBOX[t & 0xFF])
            t ^= _RCON[i // nk - 1] << 24
        elif nk > 6 and i % nk == 4:
            t = ((_SBOX[(t >> 24) & 0xFF] << 24) | (_SBOX[(t >> 16) & 0xFF] << 16)
                 | (_SBOX[(t >> 8) & 0xFF] << 8) | _SBOX[t & 0xFF])
        w.append(w[i - nk] ^ t)
    return w, nr


def _encrypt_block(block, w, nr):
    """AES 单块加密（T-table）

    轮结构：AddRoundKey(0) → [SubBytes/ShiftRows/MixColumns + AddRoundKey(r)]
    (r=1..Nr-1) → [SubBytes/ShiftRows + AddRoundKey(Nr)]。首轮把 AddRoundKey(0)
    融进查表输入（必须在 SubBytes **之前**），并顺带加上 AddRoundKey(1)。
    """
    k0, k1, k2, k3 = w[0], w[1], w[2], w[3]
    b = block
    t0 = (_TE0[b[0] ^ ((k0 >> 24) & 0xFF)] ^ _TE1[b[5] ^ ((k1 >> 16) & 0xFF)]
          ^ _TE2[b[10] ^ ((k2 >> 8) & 0xFF)] ^ _TE3[b[15] ^ (k3 & 0xFF)] ^ w[4])
    t1 = (_TE0[b[4] ^ ((k1 >> 24) & 0xFF)] ^ _TE1[b[9] ^ ((k2 >> 16) & 0xFF)]
          ^ _TE2[b[14] ^ ((k3 >> 8) & 0xFF)] ^ _TE3[b[3] ^ (k0 & 0xFF)] ^ w[5])
    t2 = (_TE0[b[8] ^ ((k2 >> 24) & 0xFF)] ^ _TE1[b[13] ^ ((k3 >> 16) & 0xFF)]
          ^ _TE2[b[2] ^ ((k0 >> 8) & 0xFF)] ^ _TE3[b[7] ^ (k1 & 0xFF)] ^ w[6])
    t3 = (_TE0[b[12] ^ ((k3 >> 24) & 0xFF)] ^ _TE1[b[1] ^ ((k0 >> 16) & 0xFF)]
          ^ _TE2[b[6] ^ ((k1 >> 8) & 0xFF)] ^ _TE3[b[11] ^ (k2 & 0xFF)] ^ w[7])
    for r in range(2, nr):
        k = 4 * r
        u0 = (_TE0[(t0 >> 24) & 0xFF] ^ _TE1[(t1 >> 16) & 0xFF]
              ^ _TE2[(t2 >> 8) & 0xFF] ^ _TE3[t3 & 0xFF] ^ w[k])
        u1 = (_TE0[(t1 >> 24) & 0xFF] ^ _TE1[(t2 >> 16) & 0xFF]
              ^ _TE2[(t3 >> 8) & 0xFF] ^ _TE3[t0 & 0xFF] ^ w[k + 1])
        u2 = (_TE0[(t2 >> 24) & 0xFF] ^ _TE1[(t3 >> 16) & 0xFF]
              ^ _TE2[(t0 >> 8) & 0xFF] ^ _TE3[t1 & 0xFF] ^ w[k + 2])
        u3 = (_TE0[(t3 >> 24) & 0xFF] ^ _TE1[(t0 >> 16) & 0xFF]
              ^ _TE2[(t1 >> 8) & 0xFF] ^ _TE3[t2 & 0xFF] ^ w[k + 3])
        t0, t1, t2, t3 = u0, u1, u2, u3
    k = 4 * nr
    o0 = (((_SBOX[(t0 >> 24) & 0xFF] << 24) | (_SBOX[(t1 >> 16) & 0xFF] << 16)
           | (_SBOX[(t2 >> 8) & 0xFF] << 8) | _SBOX[t3 & 0xFF]) ^ w[k])
    o1 = (((_SBOX[(t1 >> 24) & 0xFF] << 24) | (_SBOX[(t2 >> 16) & 0xFF] << 16)
           | (_SBOX[(t3 >> 8) & 0xFF] << 8) | _SBOX[t0 & 0xFF]) ^ w[k + 1])
    o2 = (((_SBOX[(t2 >> 24) & 0xFF] << 24) | (_SBOX[(t3 >> 16) & 0xFF] << 16)
           | (_SBOX[(t0 >> 8) & 0xFF] << 8) | _SBOX[t1 & 0xFF]) ^ w[k + 2])
    o3 = (((_SBOX[(t3 >> 24) & 0xFF] << 24) | (_SBOX[(t0 >> 16) & 0xFF] << 16)
           | (_SBOX[(t1 >> 8) & 0xFF] << 8) | _SBOX[t2 & 0xFF]) ^ w[k + 3])
    return struct.pack(">IIII", o0, o1, o2, o3)


def _gf_mul_x(a):
    """域元素乘 x（bit-reversed 表示：右移，移出的 LSB 触发约简）"""
    lsb = a & 1
    a >>= 1
    return a ^ (0xE1 << 120) if lsb else a


def _gf_mul(a, b):
    """朴素 GF(2^128) 乘法（Horner，从最高幂 x^127 = bit0 起）"""
    z = 0
    for i in range(128):
        z = _gf_mul_x(z)
        if (b >> i) & 1:
            z ^= a
    return z


def _build_ghash_tables(h):
    tab = [0] + [_gf_mul(h, n << 124) for n in range(1, 16)]
    rtab = []
    for t in range(16):
        v = t
        for _ in range(4):
            v = _gf_mul_x(v)
        rtab.append(v)
    return tab, rtab


def _ghash(tab, rtab, data):
    """GHASH：``Y_i = (Y_{i-1} XOR X_i) · H``（data 长度必须是 16 的倍数）"""
    z = 0
    for i in range(0, len(data), 16):
        v = z ^ int.from_bytes(data[i:i + 16], "big")
        acc = 0
        for k in range(32):
            acc = (acc >> 4) ^ rtab[acc & 0xF]
            acc ^= tab[(v >> (4 * k)) & 0xF]
        z = acc
    return z


def _pad16(data):
    rem = len(data) % 16
    return data + b"\x00" * (16 - rem) if rem else data


def _aes_gcm_decrypt_pure(key, nonce, data, aad=b""):
    """纯标准库 AES-GCM 解密；``data`` = 密文 + 16 字节 tag"""
    if len(data) < 16:
        raise ValueError("GCM 数据过短（缺少认证标签）")
    body, tag = data[:-16], data[-16:]
    w, nr = _key_expansion(key)
    h = int.from_bytes(_encrypt_block(b"\x00" * 16, w, nr), "big")
    tab, rtab = _build_ghash_tables(h)
    if len(nonce) == 12:
        j0 = (int.from_bytes(nonce, "big") << 32) | 1
    else:
        j0 = int.from_bytes(_ghash(tab, rtab,
                                   _pad16(nonce) + struct.pack(">QQ", 0, len(nonce) * 8)),
                            "big")
    high = j0 & (_MASK128 ^ 0xFFFFFFFF)
    low0 = j0 & 0xFFFFFFFF
    out = bytearray()
    for i in range((len(body) + 15) // 16):
        chunk = body[16 * i:16 * i + 16]
        # GCM 计数从 inc32(J0) 起（J0 低 32 位为 1 → 首块用 2）
        ctr = (low0 + i + 1) & 0xFFFFFFFF
        ks = _encrypt_block((high | ctr).to_bytes(16, "big"), w, nr)
        n = len(chunk)
        mixed = int.from_bytes(chunk, "big") ^ int.from_bytes(ks[:n], "big")
        out.extend(mixed.to_bytes(n, "big"))
    s = _ghash(tab, rtab, _pad16(aad) + _pad16(body)
               + struct.pack(">QQ", len(aad) * 8, len(body) * 8))
    expect = ((s ^ int.from_bytes(_encrypt_block(j0.to_bytes(16, "big"), w, nr), "big"))
              .to_bytes(16, "big"))
    if expect != tag:
        raise ValueError("GCM 认证失败：密钥不对或文件已损坏")
    return bytes(out)


try:  # 有 cryptography 就用它（同样的算法，快约 50 倍）
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    from cryptography.exceptions import InvalidTag as _InvalidTag
except Exception:  # noqa: BLE001
    _AESGCM = None
    _InvalidTag = None


def aes_gcm_decrypt(key, nonce, data, aad=b""):
    """解密；认证失败一律抛 ``ValueError``（快路径的 InvalidTag 也要归一，
    否则上层 ``except ValueError`` 捕获不到，会变成整个还原任务崩溃）"""
    if _AESGCM is not None:
        try:
            return _AESGCM(key).decrypt(nonce, data, aad)
        except _InvalidTag:
            raise ValueError("GCM 认证失败：密钥不对或文件已损坏")
    return _aes_gcm_decrypt_pure(key, nonce, data, aad)


def crypto_backend():
    return "cryptography（已安装，快）" if _AESGCM is not None else "内置纯 Python（较慢）"


def is_dmec(data):
    return len(data) >= DMEC_HEADER_SIZE and data[:4] == DMEC_MAGIC


def dmec_decrypt(data, master_key):
    """解密 DMEC 信封（与 app/utils/crypto.py 的封装格式一致）

    信封结构：``[DMEC][ver][DEK nonce 12B][加密 DEK 48B][内容 nonce 12B][密文...]``
    DEK 的 AAD 为 ``b"DMEC"``，内容的 AAD 为 ``b"DMEC" + version``。
    """
    if not is_dmec(data):
        raise ValueError("不是 DMEC 加密文件（魔数不匹配或文件过短）")
    version = data[4]
    if version != DMEC_VERSION:
        raise ValueError("不支持的 DMEC 版本：%d" % version)
    off = 5
    dek_nonce = data[off:off + DMEC_NONCE_SIZE]
    off += DMEC_NONCE_SIZE
    enc_dek = data[off:off + DMEC_ENC_DEK_SIZE]
    off += DMEC_ENC_DEK_SIZE
    content_nonce = data[off:off + DMEC_NONCE_SIZE]
    off += DMEC_NONCE_SIZE
    enc_content = data[off:]
    try:
        dek = aes_gcm_decrypt(master_key, dek_nonce, enc_dek, DMEC_MAGIC)
    except ValueError:
        raise ValueError("主密钥不匹配（DEK 解不开）：请确认用的是导出该包的"
                         " master.key，或随包的 _restore_kit/master.key")
    try:
        return aes_gcm_decrypt(dek, content_nonce, enc_content,
                               DMEC_MAGIC + bytes([version]))
    except ValueError:
        raise ValueError("内容解密失败：文件已损坏或被篡改")


def read_master_key_text(text):
    """主密钥文本 → 32 字节：hex（64 字符）或原始 32 字节"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        raw = bytes.fromhex(t)
        if len(raw) == 32:
            return raw
    except ValueError:
        pass
    return t.encode("utf-8") if len(t.encode("utf-8")) == 32 else None


def read_master_key_file(path):
    """读取主密钥文件：平台 master.key 是 32 字节**二进制**，也可能是 hex 文本"""
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) == 32:
        return raw
    return read_master_key_text(raw.decode("utf-8", errors="replace"))


def read_master_key_from_source(source, kit_dir=KIT_DIR):
    """从导出包（zip 或目录）里读随包主密钥；没有返回 None"""
    name = "%s/%s" % (kit_dir, KIT_MASTER_KEY_NAME)
    try:
        if os.path.isfile(source) and zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as zf:
                if name in zf.namelist():
                    return read_master_key_text(
                        zf.read(name).decode("utf-8", errors="replace"))
        elif os.path.isdir(source):
            p = os.path.join(source, kit_dir, KIT_MASTER_KEY_NAME)
            if os.path.isfile(p):
                return read_master_key_file(p)
    except Exception:  # noqa: BLE001
        return None
    return None


# ==================== 定点整数（无损的关键） ====================

def parse_fixed(text):
    """"81456.976409" → (81456976409, 6)；不可解析返回 None

    纯字符串运算、不经过 float：float 只有 15~16 位有效数字，
    时间戳这类 1e15 量级的定点值会被浮点运算吃掉精度。
    """
    m = NUM_RE.match((text or "").strip())
    if not m:
        return None
    sign, int_part, frac_part = m.group(1), m.group(2), m.group(3)
    frac = frac_part or ""
    if len(frac) > MAX_DECIMALS:
        return None
    value = int(int_part + frac) if frac else int(int_part)
    if sign == "-":
        value = -value
    return value, len(frac)


def format_fixed(value, decimals):
    """(81456976409, 6) → "81456.976409"（纯整数运算，定长小数位）"""
    decimals = int(decimals or 0)
    neg = value < 0
    digits = str(-value if neg else value)
    if decimals > 0:
        digits = digits.rjust(decimals + 1, "0")
        digits = digits[:-decimals] + "." + digits[-decimals:]
    return ("-" + digits) if neg else digits


# ==================== 噪声重建（与脱敏端逐位一致） ====================

def build_tag(version, modality, subject_key, path, column):
    """噪声派生标签。**与脱敏端（app/utils/*_desensitize.py）必须逐字节一致**

    ``version`` 参与域分隔：CSV 用 ``VERSION_TAG``、音频用 ``AUDIO_VERSION_TAG``，
    同一把密钥下两类文件的噪声流因此互不相关。
    """
    return b"|".join([
        str(version or "").encode("utf-8"),
        str(modality or "").encode("utf-8"),
        str(subject_key or "").encode("utf-8"),
        str(path or "").encode("utf-8"),
        str(column or "").encode("utf-8"),
    ])


def noise_ints(key, tag, count, r_int):
    """重建 count 个整数噪声：(u64 % (2R+1)) - R，值域 [-R, R]

    与脱敏端同一算法：seed = HMAC-SHA256(密钥, tag)（无密钥时退化为 SHA256(tag)），
    密钥流 = SHAKE256(seed) 的连续 8 字节大端整数（FIPS 202，跨语言跨版本稳定）。
    """
    if count <= 0:
        return []
    seed = hmac.new(key, tag, hashlib.sha256).digest() if key \
        else hashlib.sha256(tag).digest()
    raw = hashlib.shake_256(seed).digest(8 * count)
    modulus = 2 * r_int + 1
    return [(int.from_bytes(raw[8 * i:8 * i + 8], "big") % modulus) - r_int
            for i in range(count)]


# ==================== 读文件 ====================

# 非 UTF-8 字节（采集端写入故障）必须**原样透传**：读进内存变 U+DC80~U+DCFF，
# 写回时还原成同一批字节。否则一个坏字节就会让整个文件还原失败。
CODEC_ERRORS = "surrogateescape"


def read_header(path):
    with open(path, newline="", encoding="utf-8-sig", errors=CODEC_ERRORS) as f:
        try:
            return next(csv.reader(f))
        except StopIteration:
            return []


def count_numeric_cells(path, n_cols):
    """逐列统计「可解析为数值的非空单元格数」——脱敏与还原必须同口径

    空值/非数值单元格在脱敏时被原样保留且**不消耗噪声**，所以这里必须按同样
    规则计数，噪声序列才能逐行对上。
    """
    counts = [0] * n_cols
    with open(path, newline="", encoding="utf-8-sig", errors=CODEC_ERRORS) as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            for i in range(n_cols):
                if i < len(row) and parse_fixed(row[i]) is not None:
                    counts[i] += 1
    return counts


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def find_manifest_dir(starts=None, max_up=4):
    """自动定位「导出包根目录」：从候选起点逐级向上找含清单的目录

    覆盖三种真实摆放方式：

    * 在包根运行 ``python _restore_kit/restore_signal_desens.py`` → 当前目录即命中
    * Windows 双击 ``_restore_kit/restore_signal_desens.py`` → 当前目录是
      ``_restore_kit``，向上 1 层命中包根
    * 脚本被复制到别处、cwd 也不在包内 → 用脚本自身所在目录向上找

    :return: ``(包根目录, 说明)``；找不到返回 ``(None, 说明)``
    """
    if starts is None:
        starts = [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    tried = []
    for start in starts:
        cur = os.path.abspath(start)
        for _ in range(max_up + 1):
            if os.path.isfile(os.path.join(cur, MANIFEST_NAME)):
                note = ("当前目录" if os.path.abspath(cur) == os.path.abspath(start)
                        else "由 %s 向上定位" % start)
                return cur, note
            tried.append(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None, "已查找：%s" % "、".join(tried[-6:] or tried)


def _has_dmec_here(d):
    """目录**两层内**是否存在 ``.dmec``（包结构是 pseudo/layer/type/file.dmec）"""
    try:
        entries = os.listdir(d)
    except OSError:
        return False
    for fn in entries:
        if fn.endswith(DMEC_SUFFIX):
            return True
        sub = os.path.join(d, fn)
        if os.path.isdir(sub):
            try:
                if any(f2.endswith(DMEC_SUFFIX) for f2 in os.listdir(sub)):
                    return True
            except OSError:
                pass
    return False


def find_encrypted_dir(starts=None, max_up=4):
    """定位「只有加密文件、没有信号清单」的包根目录

    包内没有脑电/心电时不会有 ``_desens_manifest.json``，但包里的视频/音频等
    仍是 ``.dmec``，双击运行也必须能解开。故判据放宽为"目录内有 .dmec"。

    :return: ``(包根目录, 说明)``；找不到返回 ``(None, 说明)``
    """
    if starts is None:
        starts = [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    for start in starts:
        cur = os.path.abspath(start)
        for _ in range(max_up + 1):
            # 脚本自身所在目录可能就是 _restore_kit/，此时包根是它的上一级
            root = (os.path.dirname(cur) if os.path.basename(cur) == KIT_DIR
                    else cur)
            if _has_dmec_here(root):
                note = ("当前目录" if os.path.abspath(root) == os.path.abspath(start)
                        else "由 %s 向上定位" % start)
                return root, note
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    return None, "未找到含加密文件（%s）的目录" % DMEC_SUFFIX


def _input_has_dmec(source):
    """输入（zip / 目录 / 单个文件）里是否存在加密文件"""
    try:
        if os.path.isfile(source) and zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as zf:
                return bool(_dmec_names_in_zip(zf))
        if os.path.isdir(source):
            return bool(_dmec_paths_in_dir(source))
        if os.path.isfile(source):
            with open(source, "rb") as f:
                return is_dmec(f.read(DMEC_HEADER_SIZE))
    except Exception:  # noqa: BLE001
        return False
    return False


# ==================== 还原单个文件 ====================

def restore_file(src_path, dst_path, entry, key, verify=True, backup=False):
    """按清单条目把脱敏 CSV 还原为原始 CSV

    写入策略：**先写同目录临时文件，再 ``os.replace`` 原子替换**。
    dst 允许等于 src（就地覆盖）——直接 ``open(dst, "w")`` 会先把源文件截断成
    0 字节，导致读到空文件。校验开启且 sha256 不符时不替换，原文件保持原样。

    :param backup: 替换前把原文件复制一份为 ``<dst>.bak``
    :return: ``(成功?, 错误信息, 统计 dict)``
    """
    if not os.path.exists(src_path):
        return False, "源文件不存在：%s" % src_path, {}
    header = read_header(src_path)
    exp_header = entry.get("header")
    if exp_header is not None and list(exp_header) != list(header):
        return False, ("列结构与清单不一致（共 %d 列 / 清单 %d 列），"
                       "这可能不是同一次导出的文件" % (len(header), len(exp_header))), {}
    modality = entry.get("modality") or "eeg"
    seed_path = entry.get("path")
    if not seed_path:
        return False, "清单条目缺少 path（无法派生密钥）", {}
    # 清单里已显式记录 subject_key；老包没有该字段时按逻辑路径首段（pseudo_id）兜底
    subject_key = entry.get("subject_key")
    if not subject_key:
        parts = str(seed_path).replace("\\", "/").split("/")
        subject_key = parts[0] if parts else ""
    per_col = {c["name"]: c for c in (entry.get("columns") or [])}
    n_cols = len(header)
    for h in header:
        if h not in per_col:
            return False, "清单缺少列 %s 的还原参数" % h, {}

    counts = count_numeric_cells(src_path, n_cols)
    prepared = []
    for i, h in enumerate(header):
        meta = per_col[h]
        mode = meta.get("mode")
        item = {"mode": mode, "meta": meta, "pos": 0}
        if mode == "noise":
            if not meta.get("r_int"):
                return False, "列 %s 缺少噪声幅度 r_int" % h, {}
            item["noise"] = noise_ints(
                key, build_tag(VERSION_TAG, modality, subject_key, seed_path, h),
                counts[i], int(meta["r_int"]))
        elif mode == "shift" and meta.get("shift_int") is None:
            return False, "列 %s 缺少平移量 shift_int" % h, {}
        prepared.append(item)

    nl = entry.get("newline") or "\n"
    bom = bool(entry.get("bom"))
    encoding = "utf-8-sig" if bom else "utf-8"
    # 落到同目录的临时文件，最后原子替换（dst 可能就是 src）
    dst_dir = os.path.dirname(os.path.abspath(dst_path)) or "."
    os.makedirs(dst_dir, exist_ok=True)
    tmp_path = os.path.join(dst_dir, ".%s.restore-tmp" % os.path.basename(dst_path))
    written = 0
    try:
        with open(src_path, newline="", encoding=encoding,
                  errors=CODEC_ERRORS) as fin, \
                open(tmp_path, "w", newline="", encoding="utf-8",
                     errors=CODEC_ERRORS) as fout:
            if bom:
                fout.write("\ufeff")
            r = csv.reader(fin)
            w = csv.writer(fout, lineterminator=nl)
            next(r, None)
            w.writerow(header)
            for row in r:
                out = []
                for i in range(n_cols):
                    cell = row[i] if i < len(row) else ""
                    item = prepared[i]
                    if item["mode"] == "keep":
                        out.append(cell)
                        continue
                    parsed = parse_fixed(cell)
                    if parsed is None:
                        out.append(cell)
                        continue
                    value = parsed[0]
                    if item["mode"] == "noise":
                        value -= item["noise"][item["pos"]]
                        item["pos"] += 1
                    else:
                        value -= int(item["meta"]["shift_int"])
                    out.append(format_fixed(value, int(item["meta"]["decimals"])))
                if len(row) > n_cols:
                    out.extend(row[n_cols:])
                w.writerow(out)
                written += 1
    except Exception as e:  # noqa: BLE001
        _remove_quietly(tmp_path)
        return False, "写入失败：%s" % e, {}

    stats = {"rows": written, "bytes_identical": None, "sha256": None}
    if verify and entry.get("plain_sha256"):
        digest = sha256_file(tmp_path)
        stats["sha256"] = digest
        stats["bytes_identical"] = (digest == entry["plain_sha256"])
        if not stats["bytes_identical"]:
            # 绝不用错误结果覆盖原文件：密钥不对时源文件必须原样留下
            _remove_quietly(tmp_path)
            return False, ("还原结果与清单 sha256 不符（已丢弃，原文件未改动）——"
                           "通常是密钥不对或包被改动过"), stats
    if backup and os.path.exists(dst_path):
        try:
            shutil.copy2(dst_path, dst_path + ".bak")
        except OSError:
            pass
    try:
        os.replace(tmp_path, dst_path)
    except OSError as e:
        _remove_quietly(tmp_path)
        return False, "替换原文件失败：%s" % e, stats
    return True, None, stats


# ==================== 音频：定点 PCM 加噪的逆运算 ====================
#
# 与 app/utils/audio_desensitize.py 的算法**逐位对应**：同一标签构造、同一分块
# 长度、同一回绕语义。两侧都只用整数运算，不存在浮点差异；噪声幅度 R 由清单
# 直接给出（``r_int_by_ch``），还原端**不做二次统计** —— 否则两侧浮点误差
# 会让噪声流对不上，还原结果就成了看似正常的错误数据。
#
# 回绕（而非裁剪）是唯一可逆的选择：``y = (x + n) mod 2^bits`` 是双射，减法
# 就是逆运算；裁剪会把多个不同的 x 映到同一个极值上，信息不可恢复。


def parse_wav_pcm(data):
    """定位整数 PCM WAV 的样本区（与脱敏端同实现）

    :return: ``(layout, None)`` 或 ``(None, 原因)``

    两个现场必须处理的情况：

    * **流式头**：``data`` 块长度写成 0 或 0xFFFFFFFF（录音未收尾/写头失败），
      按文件实际剩余长度取，否则会读到文件尾。
    * **奇数字节块**：RIFF 规定块按偶数字节对齐，遍历时必须算上填充字节。
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None, "不是 RIFF/WAVE 容器"
    pos = 12
    fmt = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if cid == b"fmt ":
            if csize < 16 or body + 16 > len(data):
                return None, "fmt 块不完整"
            fmt = {
                "audio_format": int.from_bytes(data[body:body + 2], "little"),
                "channels": int.from_bytes(data[body + 2:body + 4], "little"),
                "sample_rate": int.from_bytes(data[body + 4:body + 8], "little"),
                "block_align": int.from_bytes(data[body + 12:body + 14], "little"),
                "bits": int.from_bytes(data[body + 14:body + 16], "little"),
            }
        elif cid == b"data":
            if fmt is None:
                return None, "data 块出现在 fmt 块之前"
            avail = len(data) - body
            if csize == 0 or csize > avail:
                csize = avail
            info = dict(fmt)
            bits = info["bits"]
            width = bits // 8
            if info["audio_format"] != 1:
                return None, "非整数 PCM 编码（audio_format=%d）" % info["audio_format"]
            if bits not in (8, 16, 24, 32):
                return None, "不支持的位深（%d bit）" % bits
            if info["channels"] < 1:
                return None, "声道数为 0"
            ba = info["block_align"] or info["channels"] * width
            if ba != info["channels"] * width:
                return None, "block_align(%d) 与声道×位深(%d)矛盾" % (
                    ba, info["channels"] * width)
            if csize <= 0 or csize % ba != 0:
                return None, "样本区长度(%d)不是帧长的整数倍" % csize
            info.update({"block_align": ba, "data_offset": body,
                         "data_size": csize, "frames": csize // ba})
            return info, None
        pos = body + csize + (csize & 1)      # 块按偶数字节对齐
    return None, "未找到 data 块"


def audio_noise_chunk(key, tag, count, r_int, chunk_index):
    """第 ``chunk_index`` 块的 count 个噪声整数：``(u16 % (2R+1)) - R``

    密钥流按**大端** 16 位字解析，因此与机器字节序无关。
    """
    if count <= 0:
        return []
    sub = tag + b"|k" + str(int(chunk_index)).encode("ascii")
    seed = hmac.new(key, sub, hashlib.sha256).digest() if key \
        else hashlib.sha256(sub).digest()
    raw = hashlib.shake_256(seed).digest(2 * count)
    u = array("H")
    u.frombytes(raw)
    if sys.byteorder == "big":
        u.byteswap()
    modulus = 2 * int(r_int) + 1
    return [(v % modulus) - int(r_int) for v in u]


def _iter_audio_chunks(frames):
    """产出 ``(起始帧, 本块帧数, 块号)`` —— 必须与脱敏端同序"""
    done = 0
    k = 0
    while done < frames:
        n = min(AUDIO_CHUNK_FRAMES, frames - done)
        yield done, n, k
        done += n
        k += 1


def apply_audio_noise(data, layout, key, tags, r_by_ch, reverse):
    """返回替换了样本区的新 bytes；样本区之外逐字节保留

    :param reverse: False = 加噪（脱敏方向），True = 减噪（还原方向）
    """
    width = layout["bits"] // 8
    ch = layout["channels"]
    off, size = layout["data_offset"], layout["data_size"]
    frames = layout["frames"]

    if width == 2:
        s = array("h")
        s.frombytes(data[off:off + size])
        if sys.byteorder == "big":
            s.byteswap()
    else:
        buf = bytearray(data[off:off + size])
        bits = 8 * width
        full = (1 << bits) - 1
        sign = 1 << (bits - 1)
        signed = width != 1

    for start, n, k in _iter_audio_chunks(frames):
        for c in range(ch):
            r = int(r_by_ch[c])
            noise = audio_noise_chunk(key, tags[c], n, r, k)
            if width == 2:
                j = start * ch + c
                if reverse:
                    for i in range(n):
                        v = (s[j] - noise[i]) & 0xFFFF
                        s[j] = v - 0x10000 if v > 0x7FFF else v
                        j += ch
                else:
                    for i in range(n):
                        v = (s[j] + noise[i]) & 0xFFFF
                        s[j] = v - 0x10000 if v > 0x7FFF else v
                        j += ch
            else:
                p = (start * ch + c) * width
                step = ch * width
                for i in range(n):
                    x = int.from_bytes(buf[p:p + width], "little", signed=signed)
                    if reverse:
                        y = (x - noise[i]) & full
                    else:
                        y = (x + noise[i]) & full
                    if signed and y >= sign:
                        y -= full + 1
                    buf[p:p + width] = y.to_bytes(width, "little", signed=signed)
                    p += step

    out = bytearray(data)
    if width == 2:
        if sys.byteorder == "big":
            s.byteswap()
        out[off:off + size] = s.tobytes()
    else:
        out[off:off + size] = bytes(buf)
    return bytes(out)


def restore_audio_file(src_path, dst_path, entry, key, verify=True, backup=False):
    """按清单条目把脱敏音频**逐字节还原**为原始音频

    写入策略同 :func:`restore_file`：先写同目录临时文件，sha256 相符才原子替换；
    不符则丢弃临时文件、原文件保持原样。

    :return: ``(成功?, 错误信息, 统计 dict)``；``stats["already"]`` 为真表示
        源文件本就是原文（就地重复运行），已跳过未做二次减噪
    """
    if not os.path.exists(src_path):
        return False, "源文件不存在：%s" % src_path, {}
    ver = entry.get("version")
    if ver and ver != AUDIO_VERSION_TAG:
        return False, ("算法版本不符（条目 %r / 脚本 %r）—— 请使用导出时随包提供的"
                       "脚本或同版本平台" % (ver, AUDIO_VERSION_TAG)), {}
    seed_path = entry.get("path")
    if not seed_path:
        return False, "清单条目缺少 path（无法派生密钥）", {}
    subject_key = entry.get("subject_key") or ""
    r_by_ch = entry.get("r_int_by_ch") or []
    if not r_by_ch:
        return False, "清单条目缺少 r_int_by_ch（无法重建噪声）", {}
    with open(src_path, "rb") as f:
        data = f.read()
    layout, why = parse_wav_pcm(data)
    if layout is None:
        return False, "待还原文件不是整数 PCM WAV：%s" % why, {}
    for field in ("channels", "bits", "sample_rate", "frames"):
        exp = entry.get(field)
        if exp is not None and layout[field] != exp:
            return False, ("文件与清单的 %s 不一致（文件 %s / 清单 %s）——"
                           " 这可能不是同一次导出的文件"
                           % (field, layout[field], exp)), {}
    if len(r_by_ch) < layout["channels"]:
        return False, "清单的 r_int_by_ch 声道数少于文件声道数", {}
    # 幂等保护：就地覆盖时若文件已是原文，再减一次噪声会毁掉数据
    if (entry.get("plain_sha256")
            and os.path.abspath(src_path) == os.path.abspath(dst_path)
            and sha256_file(src_path) == entry["plain_sha256"]):
        return True, None, {"already": True, "frames": layout["frames"],
                            "bytes_identical": True}
    tags = [build_tag(AUDIO_VERSION_TAG, "audio", subject_key, seed_path,
                      "ch%d" % c) for c in range(layout["channels"])]
    try:
        plain = apply_audio_noise(data, layout, key, tags, r_by_ch, reverse=True)
    except Exception as e:  # noqa: BLE001
        return False, "音频还原失败：%s" % e, {}

    dst_dir = os.path.dirname(os.path.abspath(dst_path)) or "."
    os.makedirs(dst_dir, exist_ok=True)
    tmp_path = os.path.join(dst_dir, ".%s.restore-tmp" % os.path.basename(dst_path))
    try:
        with open(tmp_path, "wb") as f:
            f.write(plain)
    except OSError as e:
        _remove_quietly(tmp_path)
        return False, "写入失败：%s" % e, {}

    stats = {"frames": layout["frames"], "bytes_identical": None, "sha256": None}
    if verify and entry.get("plain_sha256"):
        digest = sha256_file(tmp_path)
        stats["sha256"] = digest
        stats["bytes_identical"] = (digest == entry["plain_sha256"])
        if not stats["bytes_identical"]:
            _remove_quietly(tmp_path)
            return False, ("还原结果与清单 sha256 不符（已丢弃，原文件未改动）——"
                           "通常是密钥不对或包被改动过"), stats
    if backup and os.path.exists(dst_path):
        try:
            shutil.copy2(dst_path, dst_path + ".bak")
        except OSError:
            pass
    try:
        os.replace(tmp_path, dst_path)
    except OSError as e:
        _remove_quietly(tmp_path)
        return False, "替换原文件失败：%s" % e, stats
    return True, None, stats


def _entry_is_audio(entry):
    """条目模态判定：音频与 CSV 信号的还原算法完全不同，必须按模态分派"""
    return str(entry.get("modality") or "").strip().lower() == "audio"


def _restore_entry(src_path, dst_path, entry, key, verify=True, backup=False):
    """按条目模态分派还原实现

    * 音频 → 定点 PCM 减噪（``restore_audio_file``）
    * 其余 → 脑电/心电 CSV 去脱敏（``restore_file``）
    """
    if _entry_is_audio(entry):
        return restore_audio_file(src_path, dst_path, entry, key,
                                  verify=verify, backup=backup)
    return restore_file(src_path, dst_path, entry, key,
                        verify=verify, backup=backup)


# ==================== 清单与密钥 ====================

def load_manifest(source):
    if isinstance(source, (bytes, bytearray)):
        return json.loads(source.decode("utf-8"))
    if isinstance(source, zipfile.ZipFile):
        with source.open(MANIFEST_NAME) as f:
            return json.loads(f.read().decode("utf-8"))
    if os.path.isfile(source) and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            return load_manifest(zf)
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            return json.load(f)
    if os.path.isdir(source):
        p = os.path.join(source, MANIFEST_NAME)
        if os.path.isfile(p):
            return load_manifest(p)
    raise FileNotFoundError("找不到 %s（请确认传入的是本平台导出的脱敏包）" % MANIFEST_NAME)


def read_key_text(text):
    """密钥文本 → 32 字节：优先按 64 位十六进制解析，否则按原始字节"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        raw = bytes.fromhex(t)
        if len(raw) == 32:
            return raw
    except ValueError:
        pass
    raw = t.encode("utf-8")
    return raw if len(raw) == 32 else None


def resolve_master_key(args, source):
    """按 显式参数 → 包内自带 → 环境变量 的顺序取主密钥"""
    if getattr(args, "master_key", None):
        k = read_master_key_text(args.master_key)
        if k:
            return k, "命令行 --master-key"
    if getattr(args, "master_key_file", None):
        k = read_master_key_file(args.master_key_file)
        if k:
            return k, "主密钥文件 %s" % args.master_key_file
    kit_key = read_master_key_from_source(source)
    if kit_key:
        return kit_key, "包内自带 %s/%s（本包专用主密钥）" % (KIT_DIR, KIT_MASTER_KEY_NAME)
    env = os.environ.get("DM_MASTER_KEY")
    if env:
        k = read_master_key_text(env)
        if k:
            return k, "环境变量 DM_MASTER_KEY"
    env_path = os.environ.get("MASTER_KEY_PATH")
    if env_path and os.path.isfile(env_path):
        k = read_master_key_file(env_path)
        if k:
            return k, "环境变量 MASTER_KEY_PATH → %s" % env_path
    return None, None


def resolve_key(args, source):
    """按 显式参数 → 包内自带密钥 → 环境变量 的顺序取密钥"""
    if args.key:
        k = read_key_text(args.key)
        if k:
            return k, "命令行 --key"
    if args.key_file:
        with open(args.key_file, "r", encoding="utf-8", errors="replace") as f:
            k = read_key_text(f.read())
        if k:
            return k, "密钥文件 %s" % args.key_file
    # 包内自带（导出时勾选了「随包附带还原包」）
    kit_key = None
    if os.path.isfile(source) and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            name = "%s/%s" % (KIT_DIR, KIT_KEY_NAME)
            if name in zf.namelist():
                kit_key = zf.read(name).decode("utf-8", errors="replace")
    elif os.path.isdir(source):
        p = os.path.join(source, KIT_DIR, KIT_KEY_NAME)
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                kit_key = f.read()
    if kit_key:
        k = read_key_text(kit_key)
        if k:
            return k, "包内自带 %s/%s" % (KIT_DIR, KIT_KEY_NAME)
    env = os.environ.get("DESENS_HMAC_KEY")
    if env:
        k = read_key_text(env)
        if k:
            return k, "环境变量 DESENS_HMAC_KEY"
    return None, None


def print_listing(manifest):
    print("清单格式 : %s" % manifest.get("format"))
    print("算法版本 : %s" % manifest.get("version"))
    print("生成时间 : %s" % manifest.get("created_at"))
    print("含密钥   : %s" % ("是" if manifest.get("keyed") else "否（无盐派生）"))
    keyscope = manifest.get("key_scope") or "global"
    print("密钥范围 : %s" % ("本包专用密钥（随包附带）" if keyscope == "pack"
                             else "平台主密钥（不在包内）"))
    files = manifest.get("files") or []
    print("文件数   : %d" % len(files))
    for e in files:
        print("")
        print("  %s" % (e.get("path") or e.get("arcname")))
        form = ("加密 .dmec（需主密钥解密）" if _entry_is_encrypted(e) else "明文")
        if _entry_is_audio(e):
            print("    音频 / %s / %s 帧 / %s 声道 / %s Hz / 算法 %s / 包内形态 %s"
                  % (e.get("audio_format") or "pcm", e.get("frames"),
                     e.get("channels"), e.get("sample_rate"),
                     e.get("version"), form))
            print("      逐声道噪声幅度 R = %s（约 σ = %s）；样本区之外的字节"
                  "（RIFF 头块等）原样保留，故还原可逐字节一致"
                  % ("、".join(str(r) for r in (e.get("r_int_by_ch") or [])) or "-",
                     "、".join(str(s) for s in (e.get("noise_std_by_ch") or [])) or "-"))
            if e.get("canonical"):
                print("      注意：源容器是 %s（非整数 PCM WAV），导出时已转码为 "
                      "16-bit PCM WAV；还原产物为该规范形态（样本值精确还原）"
                      % (e.get("origin_format") or "未知"))
            continue
        print("    模态 %s / 行数 %s / 列数 %s / 包内形态 %s"
              % (e.get("modality"), e.get("rows"), len(e.get("header") or []),
                 form))
        for c in (e.get("columns") or []):
            mode = c.get("mode")
            if mode == "noise":
                extra = "噪声幅度 R=%s（约 σ=%s），小数位 %s" % (
                    c.get("r_int"), c.get("sigma_int"), c.get("decimals"))
            elif mode == "shift":
                extra = "绝对时间列整列平移 %s（小数位 %s）" % (
                    c.get("shift_int"), c.get("decimals"))
            else:
                extra = "原样保留"
            print("      [%-5s] %-28s %s" % (mode, c.get("name"), extra))


# ==================== 主流程 ====================

NO_MASTER_KEY_HINT = ("该文件在包内是加密态（.dmec），本次没有可用的主密钥；"
                      "请用 --master-key-file 指定 master.key，或确认包内有 "
                      "_restore_kit/master.key（导出时勾选「随包附带还原包」）")


def _entry_is_encrypted(entry):
    return (bool(entry.get("encrypted"))
            or str(entry.get("arcname") or entry.get("path") or "").endswith(DMEC_SUFFIX))


def _strip_dmec(name):
    """``eeg_xxx.csv.dmec`` → ``eeg_xxx.csv``（解密后的文件不再带加密后缀）"""
    name = str(name)
    return name[:-len(DMEC_SUFFIX)] if name.endswith(DMEC_SUFFIX) else name


def _write_temp_bytes(data, tmp_dir=None):
    fd, path = tempfile.mkstemp(suffix=".csv", prefix=".restore-plain-", dir=tmp_dir)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def _decrypt_bytes(data, master_key):
    """密文 → 明文；非 DMEC 原样返回。缺主密钥抛 ValueError（带可操作的提示）"""
    if not is_dmec(data):
        return data, False
    if master_key is None:
        raise ValueError(NO_MASTER_KEY_HINT)
    return dmec_decrypt(data, master_key), True


# ---------- 非信号文件的「只解密」路径 ----------
#
# 加密导出包里有些文件同样是 .dmec，但**没有**去脱敏所需的清单条目：视频（人脸
# 不可逆马赛克）、userInfo（身份字段掩码）、眼动/步态（未做值级脱敏）等。
# 它们的脱敏不可逆，所以只能解开信封、拿不到原始内容。但信封**必须**被解开 ——
# 否则接收方拿到的包一半是明文、一半仍是密文。
#
# 注意：音频**不在**这一类里 —— 音频 v2 是定点加噪、可逆，会走 restore_audio_file
# 逐字节还原（清单里有 r_int_by_ch）。只有清单缺失/版本不符时才会落到这里。

def _decrypt_side(data, master_key):
    """非信号文件的解密（不做去脱敏）

    :return: ``(plain_bytes, error)``；``error is None`` 表示成功
    """
    if not is_dmec(data):
        return None, ("不是 DMEC 密文（魔数不匹配），已跳过 —— 该文件疑似"
                      "以 .dmec 命名的明文，请人工核对")
    if master_key is None:
        return None, NO_MASTER_KEY_HINT
    try:
        return dmec_decrypt(data, master_key), None
    except ValueError as e:
        return None, str(e)


def _dmec_names_in_zip(zf):
    """zip 内所有候选密文条目名（按 ``.dmec`` 后缀；目录条目排除）"""
    return [n for n in zf.namelist()
            if not n.endswith("/") and n.endswith(DMEC_SUFFIX)]


def _dmec_paths_in_dir(root):
    """目录内所有候选密文文件路径（排序，保证报告顺序稳定）"""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(DMEC_SUFFIX):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _write_plain_file(target, data):
    """写解密结果：先建父目录、写同目录临时文件，再 ``os.replace`` 原子替换"""
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, ".%s.decrypt-tmp" % os.path.basename(target))
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError:
        _remove_quietly(tmp)
        raise


def restore_from_zip(zip_path, out_dir, key, verify=True, master_key=None):
    """还原 zip 包

    两类产物：
    * 清单里的条目（脑电/心电 CSV、音频 WAV）→ 解密 + 去脱敏（逐字节回到原文）
    * 包内其余 ``.dmec``                      → **只解密**（脱敏不可逆，只能拿到脱敏后的明文）
    """
    report = {"restored": [], "decrypted": [], "skipped": [],
              "already_restored": [], "problems": []}
    try:
        manifest = load_manifest(zip_path)
    except Exception:  # noqa: BLE001
        # 无清单（如包内只有视频，或清单被删）：不报错退出，降级为「只解密」
        manifest = None
    if manifest is not None:
        report["problems"] = verify_manifest(manifest)
    os.makedirs(out_dir, exist_ok=True)
    consumed = set()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for entry in (manifest or {}).get("files") or []:
            arcname = entry.get("arcname") or entry.get("path")
            if arcname not in names:
                cand = [n for n in names
                        if n == arcname or n == arcname + DMEC_SUFFIX
                        or _strip_dmec(n) == _strip_dmec(arcname)]
                if not cand:
                    report["skipped"].append(
                        {"path": entry.get("path"), "reason": "包内未找到该文件"})
                    continue
                arcname = cand[0]
            consumed.add(arcname)
            consumed.add(_strip_dmec(arcname))
            tmp = None
            try:
                data = zf.read(arcname)
                plain, decrypted = _decrypt_bytes(data, master_key)
                tmp = _write_temp_bytes(plain)
                target = os.path.join(out_dir,
                                      _strip_dmec(arcname).replace("/", os.sep))
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                ok, err, st = _restore_entry(tmp, target, entry, key,
                                             verify=verify)
            except ValueError as e:
                # 缺主密钥 / 密钥不对 / 文件损坏：都是"这个文件没法处理"，不是整体失败
                report["skipped"].append({"path": arcname, "reason": str(e)})
                continue
            except Exception as e:  # noqa: BLE001
                report["skipped"].append(
                    {"path": arcname, "reason": "解密或还原失败：%s" % e})
                continue
            finally:
                if tmp:
                    _remove_quietly(tmp)
            if ok:
                if st.get("already"):
                    report["already_restored"].append({"path": arcname})
                    continue
                item = {"path": arcname,
                        "bytes_identical": st.get("bytes_identical")}
                if st.get("frames") is not None:
                    item["frames"] = st.get("frames")
                else:
                    item["rows"] = st.get("rows")
                if decrypted:
                    item["decrypted"] = True
                report["restored"].append(item)
                continue
            item = {"path": arcname, "reason": err}
            if st.get("bytes_identical") is False:
                item["sha256"] = st.get("sha256")
                report.setdefault("mismatched", []).append(item)
            else:
                report["skipped"].append(item)

        # ---------- 清单未覆盖的密文：只解密 ----------
        # 不能只处理清单条目：包内视频/音频/userInfo/眼动同样是 .dmec，
        # 漏掉它们等于"解密了一半"。
        for name in _dmec_names_in_zip(zf):
            if name in consumed or _strip_dmec(name) in consumed:
                continue
            plain, err = _decrypt_side(zf.read(name), master_key)
            if err:
                report["skipped"].append({"path": name, "reason": err})
                continue
            target = os.path.join(out_dir, _strip_dmec(name).replace("/", os.sep))
            try:
                _write_plain_file(target, plain)
            except OSError as e:
                report["skipped"].append(
                    {"path": name, "reason": "解密结果写出失败：%s" % e})
                continue
            report["decrypted"].append({"path": name, "target": target})
    return report


def _locate_in_dir(root, arcname):
    """在解压目录里定位清单条目对应的文件

    容忍三种偏差：清单路径带/不带 ``.dmec`` 后缀；用户把层级拖平了；
    文件被挪到了子目录里。
    """
    rel = str(arcname).replace("/", os.sep)
    for cand in (os.path.join(root, rel), os.path.join(root, rel) + ".dmec"):
        if os.path.isfile(cand):
            return cand
    base = os.path.basename(rel)
    for dirpath, _dirs, files in os.walk(root):
        if base in files:
            return os.path.join(dirpath, base)
        if base + ".dmec" in files:
            return os.path.join(dirpath, base + ".dmec")
    return None


def restore_from_dir(root, out_dir, key, verify=True, in_place=False, backup=False,
                     master_key=None):
    """还原解压后的包目录

    两类产物：清单条目（脑电/心电 CSV、音频 WAV）→ 解密 + 去脱敏；
    其余 ``.dmec`` → **只解密**。

    :param in_place: True 时输出到 ``root`` 自身（就地覆盖原文件），忽略 out_dir
    :param master_key: 平台/本包主密钥；包内是 ``.dmec`` 加密态时需要它先解密
    """
    report = {"restored": [], "decrypted": [], "skipped": [],
              "already_restored": [], "already_decrypted": [], "problems": []}
    try:
        manifest = load_manifest(root)
    except Exception:  # noqa: BLE001
        # 无清单（如包内只有视频，或清单被删）：降级为「只解密」，不报错退出
        manifest = None
    if manifest is not None:
        report["problems"] = verify_manifest(manifest)
    target_root = os.path.abspath(root) if in_place else out_dir
    os.makedirs(target_root, exist_ok=True)
    consumed = set()
    for entry in (manifest or {}).get("files") or []:
        arcname = entry.get("arcname") or entry.get("path")
        src = _locate_in_dir(root, arcname)
        if not src:
            report["skipped"].append({"path": entry.get("path"),
                                      "reason": "目录内未找到该文件"})
            continue
        consumed.add(os.path.abspath(src))
        tmp = None
        try:
            with open(src, "rb") as f:
                data = f.read()
            plain, decrypted = _decrypt_bytes(data, master_key)
            if decrypted:
                # 解密产物先在临时文件里，校验通过后才落到目标路径
                tmp = _write_temp_bytes(plain, tmp_dir=target_root)
                src_for_restore = tmp
            else:
                src_for_restore = src
            # 幂等保护：就地覆盖时，若该文件已经是还原后的原文，再减一次噪声会毁掉数据
            if in_place and entry.get("plain_sha256") and \
                    sha256_file(src_for_restore) == entry["plain_sha256"]:
                report["already_restored"].append({"path": arcname})
                continue
            target = os.path.join(target_root,
                                  _strip_dmec(arcname).replace("/", os.sep))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            ok, err, st = _restore_entry(src_for_restore, target, entry, key,
                                         verify=verify, backup=backup)
        except ValueError as e:
            report["skipped"].append({"path": arcname, "reason": str(e)})
            continue
        except Exception as e:  # noqa: BLE001
            report["skipped"].append(
                {"path": arcname, "reason": "解密或还原失败：%s" % e})
            continue
        finally:
            if tmp:
                _remove_quietly(tmp)
        if ok:
            if st.get("already"):
                report["already_restored"].append({"path": arcname})
                continue
            item = {"path": arcname,
                    "bytes_identical": st.get("bytes_identical")}
            if st.get("frames") is not None:
                item["frames"] = st.get("frames")
            else:
                item["rows"] = st.get("rows")
            if decrypted:
                item["decrypted"] = True
            report["restored"].append(item)
            continue
        item = {"path": arcname, "reason": err}
        if st.get("bytes_identical") is False:
            item["sha256"] = st.get("sha256")
            report.setdefault("mismatched", []).append(item)
        else:
            report["skipped"].append(item)

    # ---------- 清单未覆盖的密文：只解密 ----------
    # 包内视频/音频/userInfo/眼动同样是 .dmec，漏掉它们等于"只解密了一半"。
    for src in _dmec_paths_in_dir(root):
        if os.path.abspath(src) in consumed:
            continue
        try:
            with open(src, "rb") as f:
                data = f.read()
        except OSError as e:
            report["skipped"].append({"path": src, "reason": "读取失败：%s" % e})
            continue
        plain, err = _decrypt_side(data, master_key)
        if err:
            report["skipped"].append({"path": src, "reason": err})
            continue
        rel = os.path.relpath(src, root)
        target = os.path.join(target_root, _strip_dmec(rel))
        if os.path.exists(target):
            try:
                with open(target, "rb") as f:
                    if f.read() == plain:
                        report["already_decrypted"].append({"path": rel})
                        continue
            except OSError:
                pass
        try:
            _write_plain_file(target, plain)
        except OSError as e:
            report["skipped"].append(
                {"path": src, "reason": "解密结果写出失败：%s" % e})
            continue
        report["decrypted"].append({"path": rel, "target": target})
    return report


def verify_manifest(manifest):
    problems = []
    if manifest.get("format") != "dm-signal-desens":
        problems.append("这不是本平台信号脱敏包的清单（format=%r）" % manifest.get("format"))
    if manifest.get("version") != VERSION_TAG:
        problems.append("算法版本不符（包 %r / 脚本 %r）—— 请使用导出时随包提供的脚本"
                        % (manifest.get("version"), VERSION_TAG))
    if not manifest.get("keyed"):
        problems.append("该包脱敏时未使用密钥（无盐派生）：还原仍可行，但任何知道算法"
                        "的人都能重建噪声，脱敏强度有限")
    return problems


def _pause_if_double_clicked(args):
    """Windows 下无参数运行（多半是资源管理器双击）时暂停，避免窗口一闪而过

    判据：Windows + 命令行没给任何参数 + 输入输出都在真实控制台。
    管道/重定向调用、或显式给了参数时一律不暂停；``--no-pause`` 可强制关闭。
    """
    if getattr(args, "no_pause", False) or sys.platform != "win32":
        return
    if len(sys.argv) > 1:
        return
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        input("\n按回车键关闭窗口…")
    except Exception:
        pass


def main(argv=None):
    # Windows 控制台默认 GBK，直接 print 中文/符号会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="脑电/心电/音频值级脱敏包 —— 离线还原（纯标准库）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", default=None,
                    help="导出包 zip / 解压后的目录 / 单个脱敏文件（CSV 或 WAV）；"
                         "省略则自动定位当前所在的解压目录")
    ap.add_argument("-o", "--out-dir", default=None,
                    help="输出目录；省略时目录输入就地覆盖、zip 输入用 ./restored")
    ap.add_argument("--in-place", action="store_true",
                    help="就地覆盖（目录输入且未给 -o 时即为默认行为）")
    ap.add_argument("--backup", action="store_true", help="覆盖前留一份 .bak")
    ap.add_argument("--key", help="脱敏密钥（64 位十六进制，通常无需指定）")
    ap.add_argument("--key-file", help="脱敏密钥文件路径")
    ap.add_argument("--master-key", help="主密钥（64 位十六进制）：包内是 .dmec 时用于解密")
    ap.add_argument("--master-key-file",
                    help="主密钥文件路径（平台 master.key，32 字节二进制或 hex 文本）")
    ap.add_argument("--list", action="store_true", help="只列出包内清单，不还原")
    ap.add_argument("--no-verify", action="store_true", help="跳过 sha256 校验")
    ap.add_argument("--no-pause", action="store_true",
                    help="Windows 下结束不暂停（双击运行时默认会暂停以便看结果）")
    args = ap.parse_args(argv)

    # ---------- 自动定位：不传参 = 还原「当前解压完的文件夹」 ----------
    auto_hint = ""
    if not args.input:
        found, note = find_manifest_dir()
        if not found:
            # 没有信号清单不代表不可用：包里可能只有视频/音频等加密文件。
            # 只要目录里有 .dmec，就降级为「只解密」而不是直接退出。
            found, note = find_encrypted_dir()
        if not found:
            print("错误：没能自动找到 %s，也没找到任何加密文件（%s）。"
                  % (MANIFEST_NAME, DMEC_SUFFIX))
            print("      请把本脚本放在解压后的导出包目录里运行（包根或 %s/ 均可），"
                  % KIT_DIR)
            print("      或者显式指定：python %s <导出包.zip 或 解压目录>"
                  % os.path.basename(__file__))
            print("      %s" % note)
            return 4
        args.input = found
        auto_hint = note

    manifest = None
    manifest_err = None
    try:
        manifest = load_manifest(args.input)
    except Exception as e:  # noqa: BLE001
        manifest_err = str(e)

    if args.list:
        if manifest is not None:
            print_listing(manifest)
        else:
            print("本包没有 %s：" % MANIFEST_NAME)
            print("  %s" % manifest_err)
            print("  —— 说明包内没有需要去脱敏的脑电/心电/音频文件；加密文件仍会被解密。")
        return 0

    signal_entries = list((manifest or {}).get("files") or [])
    has_dmec = _input_has_dmec(args.input)
    if manifest is None and not has_dmec:
        # 既没有清单、也没有加密文件：确实无从下手（多半是传错目录/文件）
        print("错误：%s" % manifest_err)
        print("      且输入内没有任何加密文件（%s）—— 请确认传入的是本平台导出的包。"
              % DMEC_SUFFIX)
        return 4
    if manifest is None:
        print("提示     ：未找到 %s —— 本包只做解密，不做信号去脱敏。"
              % MANIFEST_NAME)

    # 脱敏密钥：只在确有信号条目时才必须（纯解密不需要它）
    key, key_src = (None, None)
    if signal_entries:
        key, key_src = resolve_key(args, args.input)
        if key is None:
            print("错误：找不到还原密钥。可用 --key / --key-file 指定，"
                  "或把密钥放到环境变量 DESENS_HMAC_KEY。")
            print("      若导出时未勾选「随包附带还原包」，包内不会有密钥，"
                  "需向数据提供方索取。")
            return 3
        print("脱敏密钥 ：%s" % key_src)

    # 主密钥：包内存在加密文件时才需要；缺失不在这里直接退出，
    # 而是让这些文件在报告里明确标记"缺少主密钥"，明文条目照常还原
    master_key, mk_src = (None, None)
    if has_dmec:
        master_key, mk_src = resolve_master_key(args, args.input)
        print("主密钥   ：%s" % (mk_src or "未提供（加密文件将被跳过）"))
        print("解密后端 ：%s" % crypto_backend())

    is_zip = os.path.isfile(args.input) and zipfile.is_zipfile(args.input)
    is_dir = os.path.isdir(args.input)
    # 就地覆盖：目录输入且没给 -o（或显式 --in-place）；zip 无法就地，回落到 ./restored
    in_place = (not is_zip) and bool((is_dir and not args.out_dir) or args.in_place)
    if args.out_dir:
        out_dir = args.out_dir
    elif is_dir:
        out_dir = args.input
    else:
        out_dir = "restored"

    print("包目录   ：%s" % os.path.abspath(args.input))
    if auto_hint:
        print("自动定位 ：%s" % auto_hint)
    if in_place:
        print("模式     ：就地还原并覆盖原文件%s"
              % ("（覆盖前留 .bak 备份）" if args.backup else ""))
    else:
        print("模式     ：还原到 %s" % os.path.abspath(out_dir))

    if is_zip:
        report = restore_from_zip(args.input, out_dir, key, not args.no_verify,
                                  master_key=master_key)
    elif is_dir:
        report = restore_from_dir(args.input, out_dir, key, not args.no_verify,
                                  in_place=in_place, backup=args.backup,
                                  master_key=master_key)
    elif os.path.isfile(args.input):
        report = {"restored": [], "decrypted": [], "skipped": [],
                  "already_restored": [], "already_decrypted": [],
                  "problems": verify_manifest(manifest) if manifest else []}
        # 单个文件：在信号清单里 → 解密 + 去脱敏；不在 → 只解密
        entry = None
        for e in (manifest or {}).get("files") or []:
            names = (os.path.basename(str(e.get("arcname") or e.get("path"))),
                     os.path.basename(_strip_dmec(e.get("arcname") or e.get("path"))))
            if os.path.basename(_strip_dmec(args.input)) in names:
                entry = e
                break
        with open(args.input, "rb") as f:
            data = f.read()
        if entry is None:
            # 非信号文件：只解密（脱敏不可逆，包里没有可还原的参数）
            plain, derr = _decrypt_side(data, master_key)
            if derr:
                report["skipped"].append({"path": args.input, "reason": derr})
            else:
                if in_place:
                    target = _strip_dmec(args.input)
                else:
                    os.makedirs(out_dir, exist_ok=True)
                    target = os.path.join(out_dir,
                                          os.path.basename(_strip_dmec(args.input)))
                try:
                    _write_plain_file(target, plain)
                    report["decrypted"].append({"path": args.input,
                                                "target": target})
                except OSError as e:
                    report["skipped"].append(
                        {"path": args.input, "reason": "解密结果写出失败：%s" % e})
        else:
            tmp = None
            ok, err, st = False, None, {}
            try:
                plain, decrypted = _decrypt_bytes(data, master_key)
                src_path = args.input
                if decrypted:
                    tmp = _write_temp_bytes(plain)
                    src_path = tmp
                if in_place:
                    target = _strip_dmec(args.input)
                else:
                    os.makedirs(out_dir, exist_ok=True)
                    target = os.path.join(out_dir,
                                          os.path.basename(_strip_dmec(args.input)))
                ok, err, st = _restore_entry(src_path, target, entry, key,
                                             not args.no_verify,
                                             backup=args.backup)
            except ValueError as e:
                ok, err, st = False, str(e), {}
            finally:
                if tmp:
                    _remove_quietly(tmp)
            if ok:
                if st.get("already"):
                    report["already_restored"].append({"path": args.input})
                else:
                    item = {"path": args.input,
                            "bytes_identical": st.get("bytes_identical")}
                    if st.get("frames") is not None:
                        item["frames"] = st.get("frames")
                    else:
                        item["rows"] = st.get("rows")
                    report["restored"].append(item)
            elif st.get("bytes_identical") is False:
                report["mismatched"] = [{"path": args.input, "reason": err}]
            else:
                report["skipped"].append({"path": args.input, "reason": err})
    else:
        print("错误：输入既不是 zip、也不是目录或文件：%s" % args.input)
        return 4

    for p in report.get("problems") or []:
        print("提示：%s" % p)
    for item in report["restored"]:
        flag = {True: "sha256 相符", False: "sha256 不符", None: "未校验"}[
            item.get("bytes_identical")]
        mark = "解密+" if item.get("decrypted") else ""
        if item.get("frames") is not None:
            what = "帧数=%-8s" % item.get("frames")
        else:
            what = "行数=%-8s" % item.get("rows")
        print("%s已还原  %-60s %s %s" % (mark, item["path"], what, flag))
    for item in report.get("already_restored") or []:
        print("已还原过 %-60s 跳过（文件内容已是原文）" % item["path"])
    for item in report.get("decrypted") or []:
        print("已解密  %-60s → %s" % (item["path"], item.get("target")))
    for item in report.get("already_decrypted") or []:
        print("已解密过 %-60s 跳过（明文已存在且一致）" % item["path"])
    for item in report["skipped"]:
        print("已跳过  %-60s 原因：%s" % (item["path"], item["reason"]))
    for item in report.get("mismatched") or []:
        print("未覆盖  %-60s 原因：%s" % (item["path"], item["reason"]))

    # 报告落盘：就地模式下优先放进 _restore_kit/，避免污染数据根目录
    report_dir = os.path.join(out_dir, KIT_DIR)
    if not os.path.isdir(report_dir):
        report_dir = out_dir
    try:
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "restore_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("报告    ：%s" % report_path)
    except OSError as e:
        print("提示：报告写入失败：%s" % e)

    bad = report.get("mismatched") or []
    if bad:
        print("")
        print("⚠ 有 %d 个文件还原后 sha256 与清单不符，已放弃覆盖（原文件未改动）——"
              " 通常是密钥不对（请确认用的是随包密钥），也可能是包被改动过。" % len(bad))
        _pause_if_double_clicked(args)
        return 1
    if report["skipped"]:
        print("")
        print("完成，但有 %d 项被跳过（见上方原因）。" % len(report["skipped"]))
        if master_key is None and any(
                NO_MASTER_KEY_HINT in str(i.get("reason") or "")
                for i in report["skipped"]):
            print("其中加密文件需要主密钥：--master-key-file <master.key>，"
                  "或导出时勾选「随包附带还原包」让密钥随包走。")
        _pause_if_double_clicked(args)
        return 2
    n_restored = (len(report["restored"])
                  + len(report.get("already_restored") or []))
    n_decrypted = (len(report.get("decrypted") or [])
                   + len(report.get("already_decrypted") or []))
    print("")
    if not n_restored and not n_decrypted:
        print("完成：包内没有需要处理的文件。")
    elif n_decrypted:
        print("还原完成：%d 个文件已逐字节还原为原始数据（脑电/心电/音频）；"
              "另有 %d 个文件已解密为明文。" % (n_restored, n_decrypted))
        print("          后者（视频/userInfo/眼动等）没有可还原的参数，"
              "解出来的仍是脱敏后的内容：")
        print("          视频人脸仍是马赛克块、userInfo 身份字段仍是掩码。")
    else:
        print("全部还原完成，共 %d 个文件与原始文件的 sha256 完全一致（逐字节无损）。"
              % n_restored)
    _pause_if_double_clicked(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
