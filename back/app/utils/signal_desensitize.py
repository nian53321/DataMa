# -*- coding: utf-8 -*-
"""EEG/ECG 信号脱敏 v2：**保留全部列**的值级脱敏，且**可无损还原**。

与 v1 的差别（v1 已废弃）
------------------------
==================  ==============================  ==========================
维度                v1（旧）                        v2（本文件）
==================  ==============================  ==========================
列                 删绝对时间列 / 删派生生理列      **全部保留**
浮点输出            %.6f 重格式化 → 残差 5e-12      定点整数域，**逐字节一致**
噪声               逐行 float 高斯                  逐行整数确定性流（可减回）
时间列             删除                            整列常量平移（可加回）
还原               不可能                          用密钥 + manifest 精确还原
==================  ==============================  ==========================

还原性来自三点
--------------
1. **定点整数域**：``81456.976409`` → 整数 ``81456976409``（按列的固定小数位 d 缩放）。
   加噪/减噪都是整数加减，不引入任何浮点误差；回写时仍按 d 位定长输出。
2. **确定性噪声**：噪声完全由「密钥 + 受试者 + 文件路径 + 列名」派生，不依赖数据本身。
   还原方只要拿到同一个密钥，就能重建出**逐位相同**的噪声序列。
3. **manifest**：每列的 `d`、噪声幅度 `R`、时间列平移量等参数随导出包一起给出，
   还原方不需要（也无法）从数据反推这些参数。manifest **不含密钥**。

脱敏算法（可被任何语言重新实现，务必逐字对齐）
---------------------------------------------
::

    seed    = HMAC-SHA256(master_key, tag)
    tag     = b"signal-desens-v2|" + modality + b"|" + pseudo_id + b"|" + path + b"|" + column
    ks      = SHAKE256(seed).digest(8 * N)              # N = 该列参与脱敏的数值单元格数
    u_i     = int.from_bytes(ks[8i:8i+8], "big")        # 大端 uint64

    # 信号列（mode=noise）：逐行加整数噪声，幅度 R 见 manifest
    y_int   = v_int + ((u_i % (2R+1)) - R)
    v_int   = y_int - ((u_i % (2R+1)) - R)              # 还原

    # 绝对时间列（mode=shift）：整列加同一常量，保持单调与采样间隔
    y_int   = v_int + shift_int
    v_int   = y_int - shift_int                         # 还原

    # 输出/还原都按 manifest 记录的 decimals 定长输出
    text    = format_fixed(v_int, decimals)

其中 ``modality ∈ {eeg, ecg}``，``path`` 为导出包内的逻辑路径（不含 ``.dmec`` 后缀），
``N`` 按**行的先后顺序**只统计可解析为数值的非空单元格（空/非数值单元格不消耗噪声）。

为什么噪声用「整数 + 均匀分布」
------------------------------
- 均匀整数噪声是**纯整数运算**（``u % (2R+1) - R``），跨 Python / numpy / 任何语言、
  任何版本都给出完全相同的结果；``np.random.default_rng`` 的随机流在不同 numpy 版本
  间**不作兼容保证**，用它会让"若干年后还能还原"绑定在 numpy 版本上。
- 噪声方差与高斯同量级（``R = σ√3`` ⇒ ``std = R/√3 = σ``），破坏力与 v1 一致。

强度标定（真实数据实测）
------------------------
为什么是"加噪"而不是"通道置换/增益"（16 通道 × 9138 样本，500 Hz）：

===================  ==========  ============  ==================================
方案                  波形相关     频带特征改变   相干性下降（个体特征破坏）
===================  ==========  ============  ==================================
仅通道置换             1.000       1.21          **0.0%**
仅每通道增益           1.000       0.00          **0.0%**
置换+增益              1.000       1.21          **0.0%**
加噪 alpha=0.15        0.989       0.42          51.8%
相位随机化             0.015       0.88          65.3%
===================  ==========  ============  ==================================

- **通道置换对相干性零影响**：相干是通道对的函数，置换只是给通道对换标签 →
  无序相干特征集完全不变，不构成脱敏；它只破坏电极空间语义（源定位失效）。
- **每通道增益对归一化频带特征零影响**：功率占比被归一化抵消。
- 相位随机化破坏更强，但波形相关掉到 0.015（时间结构彻底损毁，ERP/微状态/
  事件对齐全废），过于激进，不采用。

alpha 扫描（EEG）：

=========  ==========  ============  ==========
alpha      波形相关     频带特征改变   相干性下降
=========  ==========  ============  ==========
0.05       0.9988      0.17          27.0%
**0.15**   **0.9890**  0.42          **51.8%**
0.30       0.9581      0.59          61.4%
0.50       0.8951      0.68          64.2%
1.00       0.7091      0.82          65.2%
=========  ==========  ============  ==========

alpha=0.15 已取得 51.8% 相干性下降，再增大 6.7 倍仅多降 13.4 个百分点，而波形相关
从 0.989 恶化到 0.709 —— **边际收益急剧递减，0.15 为性价比拐点**。

ECG 强度依据（单通道 value 列，信号 AC std=129.31 ADC LSB）：

======  ========  ==========  ============  ===============
sigma   SNR(dB)   波形相关     频带特征改变   R 峰段数(原 51)
======  ========  ==========  ============  ===============
10      22.2      0.997       0.005         51
**40**  **10.2**  **0.956**   **0.081**     **52**
80      4.2       0.854       0.269         57
200     −3.8      0.550       0.739         36（漏检）
======  ========  ==========  ============  ===============

sigma=40 在保持 R 峰计数稳定的前提下开始实质改变波形。v2 用 ``ECG_NOISE_ALPHA=0.31``
（≈ 40/129）以"列自适应"方式复现同一强度：换量程时自动等比缩放。

列分类策略
----------
====================  ==========================================================
类别                  处理
====================  ==========================================================
结构/锚点列            原样保留：``sample_index`` / ``Sample Index`` / ``time_sec`` /
                      ``source`` / ``frame`` / ``marker`` / ``event`` / ``label``。
                      理由：行对齐、相对时间轴、事件语义锚点，本身不含身份或生理
                      特征；脱敏它们只会让数据不可用而不增加任何保护。
绝对时间列             整列常量平移（``Timestamp`` / ``Board Timestamp (ms)`` /
                      ``epoch*``，或取值中位数 > 1e8 的时间类列）。整列共用同一偏移
                      ⇒ 单调性与采样间隔完全保留，但采集时刻无法定位。
                      **不能逐行加噪**：那会让时间轴非单调，数据直接报废。
信号列                 逐行整数加噪：``EXG Channel N`` / ``value``。
其余数值列             同信号列（含 ``heart_rate`` 等派生生理列 —— 保留列但值被加噪，
                      HRV 时序特征随之破坏）。
非数值列               原样保留。
====================  ==========================================================

风险边界（必须知情）
--------------------
1. **持有密钥者可以完全还原**，这是本方案的**设计目标**，不是缺陷。密钥即全部安全性：
   ``back/keys/desens.key`` 与 ``data_lake`` 同处一台机器，能拿到服务器文件的人同时拥有
   密钥与原文 —— 本方案防的是**数据交出去之后**，**不防服务器被拿到**。
2. 加噪会降低所有下游分析的信噪比：alpha=0.15 下频带功率占比改变约 42%。
3. 时间列整体平移后，与**其它设备的绝对时间对齐能力丧失**（相对间隔保留）。
4. **"抗识别强度"未在本平台验证** —— 平台内无 brainprint / ECG-biometric 识别模型，
   "相干性下降 51.8%"是**信息破坏量**，不等于识别率下降幅度。
5. 仍不处理视频音轨（mp4 内音轨同样泄露声纹，目前只覆盖 ``.wav`` 类音频资产）。
6. 文本级还原前提：源文件数值为**定长小数**（本平台采集端即如此）。若同一列内小数位
   数不齐（如 ``5.8`` 与 ``5.812578`` 混排），还原后前者会补齐为 ``5.800000`` ——
   **数值无损，文本存在定长补齐**；manifest 的 ``decimals_mixed`` 会标出这种情况。
7. ``+`` 前缀与 ``-0.000000`` 这类非常规写法会被规范化（数值不变）。
"""
import codecs
import contextlib
import csv
import hashlib
import hmac
import json
import os
import re
import threading
import zipfile
from datetime import datetime, timezone

# ==================== 常量 ====================

# 版本标签：参与密钥派生域分隔；改动算法必须同时改这里，旧包将明确报"版本不符"
VERSION_TAG = "signal-desens-v2"
# manifest 在导出包内的固定位置
MANIFEST_NAME = "_desens_manifest.json"
MANIFEST_FORMAT = "dm-signal-desens"

# 噪声强度（噪声 std / 该列信号 std）。EEG 见 docstring 标定表；
# ECG 0.31 ≈ 40/129，复现 v1 实测的 sigma=40（value std=129.31）。
EEG_NOISE_ALPHA = 0.15
ECG_NOISE_ALPHA = 0.31

# 绝对时间列整列平移幅度（秒）：1.2 ~ 116 天，方向随机。
# 量级足够大 → 无法与其它绝对时间锚点（文件 mtime 等）交叉定位；
# 平移量含随机小数部分 ⇒ 时刻在"日"内的分布也被打散（不能只按整天平移）。
TIME_SHIFT_MIN_SECONDS = 100_000.0
TIME_SHIFT_MAX_SECONDS = 10_000_000.0

# 定点整数必须落在 int64 内；小数位超过该值则放弃该列（原样保留）
MAX_DECIMALS = 9

# 非 UTF-8 字节（采集端写入故障 / 传输损坏）的处置：**透传而非拒绝**。
# 用 surrogateescape 读入 → 坏字节变成 U+DC80~U+DCFF → 写回时还原成同一批字节。
# 这样单个损坏字节不会让整个几十 MB 的信号文件被整包跳过，且还原仍逐字节一致
# （损坏字节本身不被篡改、不被"修复"成别的字符 —— 伪造数据比保留损坏更糟）。
CODEC_ERRORS = "surrogateescape"
_REPLACEMENT = "\ufffd"

# 时间类列名（timestamp / time / ts / datetime / epoch）
_TIME_NAME_RE = re.compile(r"(timestamp|\btime\b|\bts\b|datetime|epoch|_time$|_ts$)",
                           re.I)
# 列全空时用列名兜底判定"绝对时间"
_ABS_TIME_NAME_RE = re.compile(
    r"^((board|device|host|system)\s*[_\s])?timestamp(\s*\(ms\))?$|^epoch", re.I)
# 结构/锚点列：行对齐、相对时间轴、设备计数器、事件语义 —— 原样保留
_KEEP_COL_RE = re.compile(
    r"^(sample[_\s]*index|sample[_\s]*idx|time_sec|time_seconds|source|sensor|"
    r"frame|frame[_\s]*index|marker|markers|event|events|annotation|annotations|"
    r"label|labels|trigger|triggers|stim|stimulus|status|channel[_\s]*names?)$", re.I)

# 定点小数的规范形式：可选符号 + 整数部分 + 可选小数部分
_NUM_RE = re.compile(r"^([+-]?)(\d+)(?:\.(\d*))?$")

_MODE_KEEP = "keep"
_MODE_NOISE = "noise"
_MODE_SHIFT = "shift"

__all__ = [
    "VERSION_TAG", "MANIFEST_NAME", "MANIFEST_FORMAT",
    "EEG_NOISE_ALPHA", "ECG_NOISE_ALPHA",
    "desensitize_eeg_file", "desensitize_ecg_file", "desensitize_signal_file",
    "restore_signal_file", "restore_zip", "load_manifest", "verify_manifest",
    "build_manifest", "manifest_bytes", "parse_fixed", "format_fixed",
    "get_hmac_key", "set_hmac_key",
]


# ==================== 密钥派生 ====================

# 显式注入的主密钥（离线还原 CLI 用；None = 走环境变量/密钥文件）
_KEY_OVERRIDE = None
# 线程内临时覆盖的密钥：导出「随包附带还原包」时改用本包专用密钥。
# 用 thread-local 而不是全局：一个进程里可能同时跑多个导出线程，全局变量会互相覆盖。
_KEY_LOCAL = threading.local()


def get_hmac_key():
    """获取脱敏主密钥（复用 userInfo / 音频脱敏的同一把 DESENS_HMAC_KEY）

    优先级：``use_hmac_key()`` 的线程内覆盖 → ``set_hmac_key()`` 的进程内覆盖 →
    环境变量 / 密钥文件。

    全部拿不到时返回 None —— 此时退化为"域分隔 + 无盐 SHA256"。仍确定、仍可还原，
    但知道算法的人都能重建噪声，脱敏形同虚设；manifest 会记 ``keyed: false``。
    """
    override = getattr(_KEY_LOCAL, "key", None)
    if override is not None:
        return override
    if _KEY_OVERRIDE is not None:
        return _KEY_OVERRIDE
    try:
        from app.utils.desensitize import _get_hmac_key
        return _get_hmac_key()
    except Exception:
        return None


def set_hmac_key(key):
    """显式注入主密钥（离线还原 / CLI 使用），进程内优先于环境变量与密钥文件"""
    global _KEY_OVERRIDE
    _KEY_OVERRIDE = key


@contextlib.contextmanager
def use_hmac_key(key):
    """在 ``with`` 块内改用指定密钥派生噪声（线程内生效，退出自动还原）

    导出「随包附带还原包」时用它换成**本包专用密钥**：包内自带这把密钥，接收方
    能还原，而平台主密钥不出包 —— 单个包泄露不会波及历史/其他脱敏包，也不会连带
    泄露 userInfo 哈希脱敏用的那把密钥（同一把 DESENS_HMAC_KEY）。
    """
    prev = getattr(_KEY_LOCAL, "key", None)
    _KEY_LOCAL.key = key
    try:
        yield
    finally:
        _KEY_LOCAL.key = prev


def key_fingerprint(key) -> str:
    """密钥指纹（sha256 前 16 位十六进制）：只用于"密钥对不对"的诊断

    不构成新泄露面：manifest 里本来就有原文 ``plain_sha256``，持密钥方可以自行
    试算验证，指纹只省去猜的过程。
    """
    if not key:
        return ""
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hashlib.sha256(key).hexdigest()[:16]


def _column_seed(tag: bytes) -> bytes:
    key = get_hmac_key()
    if not key:
        return hashlib.sha256(tag).digest()
    return hmac.new(key, tag, hashlib.sha256).digest()


def _build_tag(modality, subject_key, path, column) -> bytes:
    return b"|".join([
        VERSION_TAG.encode("utf-8"),
        str(modality or "").encode("utf-8"),
        str(subject_key or "").encode("utf-8"),
        str(path or "").encode("utf-8"),
        str(column or "").encode("utf-8"),
    ])


def _keystream_u64(tag: bytes, count: int):
    """返回 count 个 uint64（numpy 数组；无 numpy 环境返回 bytes 供逐块解析）

    SHAKE-256（FIPS 202）扩展输出，跨语言、跨版本一致。不用
    ``np.random.default_rng``：numpy 不保证 Generator 随机流跨版本稳定。
    """
    if count <= 0:
        return b""
    raw = hashlib.shake_256(_column_seed(tag)).digest(8 * count)
    try:
        import numpy as np
        return np.frombuffer(raw, dtype=">u8")
    except Exception:
        return raw


def _as_uint64_list(u, count):
    """把密钥流统一成 Python int 列表（两条路径结果完全一致）"""
    return [int.from_bytes(u[8 * i:8 * i + 8], "big") for i in range(count)]


def _noise_ints(tag: bytes, count: int, r_int: int):
    """生成 count 个噪声整数：``(u % (2R+1)) - R``，值域 [-R, R]"""
    u = _keystream_u64(tag, count)
    modulus = 2 * r_int + 1
    try:
        import numpy as np
        arr = np.asarray(u, dtype="uint64")
        return ((arr % np.uint64(modulus)).astype("int64")
                - np.int64(r_int)).tolist()
    except Exception:
        if not isinstance(u, (bytes, bytearray)):
            u = bytes(u)
        return [(int.from_bytes(u[8 * i:8 * i + 8], "big") % modulus) - r_int
                for i in range(count)]


# ==================== 定点整数（无损的关键） ====================

def parse_fixed(text):
    """``"81456.976409"`` → ``(81456976409, 6)``；不可解析返回 None

    纯字符串运算，**不经过 float**：float 只有 15~16 位有效数字，
    ``1782959109.639000``（定点后 1.78e15）会在解析/回写时丢精度。
    """
    m = _NUM_RE.match((text or "").strip())
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


def format_fixed(value, decimals) -> str:
    """``(81456976409, 6)`` → ``"81456.976409"``（纯整数运算，定长小数）"""
    decimals = int(decimals or 0)
    neg = value < 0
    digits = str(-value if neg else value)
    if decimals > 0:
        digits = digits.rjust(decimals + 1, "0")
        digits = digits[:-decimals] + "." + digits[-decimals:]
    return ("-" + digits) if neg else digits


# ==================== 文件级辅助 ====================

def _remove_safely(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _read_header(path):
    with open(path, newline="", encoding="utf-8-sig", errors=CODEC_ERRORS) as f:
        try:
            return next(csv.reader(f))
        except StopIteration:
            return []


def _detect_newline(path):
    with open(path, "rb") as f:
        chunk = f.read(65536)
    return "\r\n" if b"\r\n" in chunk else "\n"


def _detect_bom(path):
    with open(path, "rb") as f:
        return f.read(3) == b"\xef\xbb\xbf"


def scan_bad_utf8(path, chunk=1 << 20):
    """统计文件里的非 UTF-8 字节数（增量解码，内存有界）

    用于**告警**：损坏字节会被 surrogateescape 原样透传，这里只是把"源数据本身
    有损坏"这件事说出来，避免下游误以为数据完好。

    :return: ``(坏字节近似数, 首个坏字节的近似偏移)``；无损坏返回 ``(0, -1)``
    """
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    bad = 0
    first = -1
    off = 0
    with open(path, "rb") as f:
        while True:
            blob = f.read(chunk)
            if not blob:
                break
            text = dec.decode(blob)
            if _REPLACEMENT in text:
                if first < 0:
                    # 近似定位：在本 chunk 内逐字节重放，找到第一个产出替换符的字节
                    d2 = codecs.getincrementaldecoder("utf-8")("replace")
                    for k in range(len(blob)):
                        if _REPLACEMENT in d2.decode(blob[k:k + 1]):
                            first = off + k
                            break
                bad += text.count(_REPLACEMENT)
            off += len(blob)
    return bad, first


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _int_std(values):
    """整数序列的标准差（float64，仅用于估噪声幅度）

    先减去首值再求平方和：原值绝对值可达 1e15，直接对原值求方差会吃掉有效数字。
    """
    n = len(values)
    if n < 2:
        return 0.0
    try:
        import numpy as np
        arr = np.asarray(values, dtype=np.int64)
        return float(np.std((arr - arr[0]).astype(np.float64)))
    except Exception:
        base = values[0]
        s = 0.0
        s2 = 0.0
        for v in values:
            d = float(v - base)
            s += d
            s2 += d * d
        mean = s / n
        return max(0.0, s2 / n - mean * mean) ** 0.5


def _median_int(values):
    if not values:
        return None
    s = sorted(values)
    return float(s[len(s) // 2])


def _time_unit_factor(name) -> int:
    """时间列 1 秒对应多少整数步长：毫秒列 1000，秒列 1"""
    return 1000 if re.search(r"\(ms\)|_ms\b|millisecond", name or "", re.I) else 1


# ==================== 列分类 ====================

def _classify_column(name, numeric, median):
    """决定列的脱敏方式 → _MODE_KEEP / _MODE_NOISE / _MODE_SHIFT"""
    col = (name or "").strip()
    if _KEEP_COL_RE.match(col):
        return _MODE_KEEP
    if numeric == 0:
        return _MODE_KEEP          # 全空或非数值列（如 source 字符串）
    if _TIME_NAME_RE.search(col):
        if median is not None and abs(median) > 1e8:
            return _MODE_SHIFT     # epoch 秒/毫秒、datetime 转数 —— 绝对时刻
        if _ABS_TIME_NAME_RE.match(col):
            return _MODE_SHIFT     # 值全空但列名明确是绝对时间戳
        return _MODE_KEEP          # 相对时间轴（time_sec 之类）：保留才可用
    return _MODE_NOISE             # 其余数值列（信号列、派生生理列）全部加噪


# ==================== 脱敏（mask） ====================

def desensitize_signal_file(src_path, dst_path, modality, subject_key,
                            seed_path=None, logger=None, alpha=None):
    """对 EEG/ECG CSV 做值级脱敏：**列全部保留**，逐列做确定性可逆变换

    :param modality: ``"eeg"`` / ``"ecg"``（决定默认噪声强度，并参与密钥派生）
    :param subject_key: 受试者稳定标识（``pseudo_id``）
    :param seed_path: 参与密钥派生的逻辑路径（导出包内路径，不含 .dmec）；
                      缺省用文件名。**还原时必须提供同一个值**（manifest 已记录）
    :return: ``(成功?, 错误信息, 统计 dict)``；``stats["manifest"]`` 即该文件的还原参数
    """
    if not os.path.exists(src_path):
        return False, "源文件不存在", {}
    header = _read_header(src_path)
    if not header:
        return False, "CSV 表头为空，无法识别列结构", {}
    if alpha is None:
        alpha = EEG_NOISE_ALPHA if modality == "eeg" else ECG_NOISE_ALPHA
    seed_path = seed_path or os.path.basename(src_path)

    # 源数据完整性告警：非 UTF-8 字节说明采集端/传输环节出过故障。
    # 损坏字节会被原样透传（不"修复"），但必须说出来，否则下游会当成完好数据。
    if logger:
        bad_n, bad_off = scan_bad_utf8(src_path)
        if bad_n:
            logger.warning(
                "信号脱敏：%s 含约 %d 个非 UTF-8 字节（首个约在偏移 %d，"
                "疑采集端写入故障），已原样透传、未做修复；"
                "该行对应单元格不会被加噪（无法解析为数值）",
                os.path.basename(src_path), bad_n, bad_off)

    # ---------- pass 1：逐列统计（决定列类别、decimals、噪声幅度） ----------
    n_cols = len(header)
    cols = [{"name": h, "numeric": 0, "max_decimals": 0, "decimals_set": set(),
             "values": []} for h in header]
    n_rows = 0
    with open(src_path, newline="", encoding="utf-8-sig", errors=CODEC_ERRORS) as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            n_rows += 1
            for i in range(n_cols):
                cell = row[i] if i < len(row) else ""
                parsed = parse_fixed(cell)
                if parsed is None:
                    continue
                c = cols[i]
                c["numeric"] += 1
                c["decimals_set"].add(parsed[1])
                if parsed[1] > c["max_decimals"]:
                    c["max_decimals"] = parsed[1]
                c["values"].append(parsed[0])
    if n_rows == 0:
        return False, "CSV 无数据行", {}
    if not any(c["numeric"] for c in cols):
        # 整表没有任何数值列 —— 无从脱敏，但也不允许原样放行
        # （EEG/ECG 目录下可能混入非信号文件，宁可跳过并记入 errors）
        return False, "未找到任何数值列，已跳过以避免明文导出", {}

    # ---------- 逐列定型 ----------
    spec = []
    for c in cols:
        mode = _classify_column(c["name"], c["numeric"], _median_int(c["values"]))
        item = {"name": c["name"], "mode": mode, "decimals": c["max_decimals"]}
        if mode == _MODE_NOISE:
            r_int = int(round(alpha * _int_std(c["values"]) * 1.7320508075688772))
            if r_int <= 0:
                # 恒定列（电极饱和/未接）：噪声幅度为 0，原样输出，避免造出
                # "看起来有信号"的假数据
                item["mode"] = _MODE_KEEP
                item["note"] = "constant_column"
            else:
                item.update({
                    "r_int": r_int,
                    "sigma_int": int(round(alpha * _int_std(c["values"]))),
                    "n": c["numeric"],
                    "noise": None,
                })
        elif mode == _MODE_SHIFT:
            item["factor"] = _time_unit_factor(c["name"])
            item["unit"] = "ms" if item["factor"] == 1000 else "s"
        item["mixed_precision"] = len(c["decimals_set"]) > 1
        spec.append(item)

    # 时间平移量：整文件共用一个偏移（秒）⇒ 单调性/采样间隔不变；
    # 同时也保证 Timestamp 与 Board Timestamp (ms) 之间的对应关系不被破坏
    shift_seconds = None
    for item in spec:
        if item["mode"] != _MODE_SHIFT:
            continue
        if shift_seconds is None:
            u = _keystream_u64(
                _build_tag(modality, subject_key, seed_path, "__time_shift__"), 1)
            frac = _as_uint64_list(u, 1)[0] / float(1 << 64)
            sign = -1.0 if frac >= 0.5 else 1.0
            mag = TIME_SHIFT_MIN_SECONDS + (TIME_SHIFT_MAX_SECONDS
                                            - TIME_SHIFT_MIN_SECONDS) * abs(2 * frac - 1)
            shift_seconds = sign * mag
        item["shift_int"] = int(round(
            shift_seconds * item["factor"] * (10 ** item["decimals"])))

    # 预生成各列噪声（列内按行序消耗；空/非数值单元格不消耗 ⇒ 两侧口径一致）
    for item in spec:
        if item["mode"] != _MODE_NOISE:
            continue
        item["noise"] = _noise_ints(
            _build_tag(modality, subject_key, seed_path, item["name"]),
            item["n"], item["r_int"])
        item["pos"] = 0

    # ---------- pass 2：流式写出 ----------
    nl = _detect_newline(src_path)
    bom = _detect_bom(src_path)
    encoding = "utf-8-sig" if bom else "utf-8"
    written = 0
    try:
        with open(src_path, newline="", encoding=encoding,
                  errors=CODEC_ERRORS) as fin, \
                open(dst_path, "w", newline="", encoding="utf-8",
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
                    item = spec[i]
                    if item["mode"] == _MODE_KEEP:
                        out.append(cell)
                        continue
                    parsed = parse_fixed(cell)
                    if parsed is None:
                        out.append(cell)    # 空/非数值：原样且不消耗噪声
                        continue
                    value = parsed[0]
                    if item["mode"] == _MODE_NOISE:
                        value += item["noise"][item["pos"]]
                        item["pos"] += 1
                    else:
                        value += item["shift_int"]
                    out.append(format_fixed(value, item["decimals"]))
                if len(row) > n_cols:
                    out.extend(row[n_cols:])   # 畸形多列：原样补出，保持行结构
                w.writerow(out)
                written += 1
    except Exception as e:
        _remove_safely(dst_path)
        return False, "信号脱敏写入失败：%s" % e, {}

    if written != n_rows:
        _remove_safely(dst_path)
        return False, "行数不一致（写 %d / 读 %d），已放弃输出" % (written, n_rows), {}

    # ---------- 还原参数（manifest 条目） ----------
    columns_meta = []
    for item in spec:
        meta = {"name": item["name"], "mode": item["mode"]}
        if item["mode"] in (_MODE_NOISE, _MODE_SHIFT):
            meta["decimals"] = item["decimals"]
        if item["mode"] == _MODE_NOISE:
            meta["r_int"] = item["r_int"]
            meta["sigma_int"] = item["sigma_int"]
        elif item["mode"] == _MODE_SHIFT:
            meta["shift_int"] = item["shift_int"]
            meta["unit"] = item["unit"]
        if item.get("note"):
            meta["note"] = item["note"]
        columns_meta.append(meta)

    entry = {
        "path": seed_path,
        # 显式记录参与密钥派生的受试者标识：随包还原方不必再从路径首段反推
        "subject_key": subject_key,
        "modality": modality,
        "rows": n_rows,
        "header": header,
        "newline": "\r\n" if nl == "\r\n" else "\n",
        "bom": bool(bom),
        "decimals_mixed": any(it["mixed_precision"] for it in spec),
        "plain_sha256": _sha256_file(src_path),
        "plain_size": os.path.getsize(src_path),
        "columns": columns_meta,
    }
    noise_cols = sum(1 for m in columns_meta if m["mode"] == _MODE_NOISE)
    shift_cols = sum(1 for m in columns_meta if m["mode"] == _MODE_SHIFT)
    zero_cols = sum(1 for m in columns_meta if m.get("note") == "constant_column")
    if zero_cols and logger:
        logger.warning(
            "信号脱敏：%d 个列数值恒定（电极饱和/未接），这些列原样输出未加噪", zero_cols)
    if entry["decimals_mixed"] and logger:
        logger.info("信号脱敏：%s 存在列内小数位数不一致，还原时文本会按列定长补齐"
                    "（数值无损）", os.path.basename(src_path))
    stats = {
        "rows": n_rows, "columns": n_cols,
        "noise_cols": noise_cols, "shift_cols": shift_cols,
        "keep_cols": n_cols - noise_cols - shift_cols,
        "constant_cols": zero_cols,
        "manifest": entry,
    }
    return True, None, stats


def desensitize_eeg_file(src_path, dst_path, subject_key, seed_path=None,
                         logger=None, alpha=None):
    """脑电专用入口（见 :func:`desensitize_signal_file`）"""
    return desensitize_signal_file(src_path, dst_path, "eeg", subject_key,
                                   seed_path=seed_path, logger=logger, alpha=alpha)


def desensitize_ecg_file(src_path, dst_path, subject_key, seed_path=None,
                         logger=None, alpha=None):
    """心电专用入口（见 :func:`desensitize_signal_file`）"""
    return desensitize_signal_file(src_path, dst_path, "ecg", subject_key,
                                   seed_path=seed_path, logger=logger, alpha=alpha)


# ==================== 还原（restore） ====================

def _count_numeric_cells(path, n_cols):
    """逐列统计"可解析为数值的非空单元格数"（还原侧必须与脱敏侧同口径）"""
    counts = [0] * n_cols
    with open(path, newline="", encoding="utf-8-sig", errors=CODEC_ERRORS) as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            for i in range(n_cols):
                if i < len(row) and parse_fixed(row[i]) is not None:
                    counts[i] += 1
    return counts


def restore_signal_file(src_path, dst_path, entry, subject_key,
                        verify=True, logger=None):
    """按 manifest 条目把脱敏 CSV **无损还原**为原始 CSV

    :param entry: manifest 中该文件的条目（含每列 ``mode`` / ``decimals`` /
                  ``r_int`` / ``shift_int``）
    :param subject_key: 受试者 ``pseudo_id``（与脱敏时一致）
    :param verify: 是否用 manifest 的 ``plain_sha256`` 校验还原结果
    :return: ``(成功?, 错误信息, 统计 dict)``
    """
    if not os.path.exists(src_path):
        return False, "源文件不存在", {}
    if not isinstance(entry, dict):
        return False, "manifest 条目非法", {}
    header = _read_header(src_path)
    exp_header = entry.get("header")
    if exp_header is not None and list(exp_header) != list(header):
        return False, "列结构与 manifest 不一致（可能不是同一次导出的文件）", {}
    modality = entry.get("modality") or "eeg"
    seed_path = entry.get("path")
    if not seed_path:
        return False, "manifest 条目缺少 path（无法派生密钥）", {}
    per_col = {c["name"]: c for c in (entry.get("columns") or [])}
    n_cols = len(header)
    for h in header:
        if h not in per_col:
            return False, "manifest 缺少列 %s 的还原参数" % h, {}

    numeric_counts = _count_numeric_cells(src_path, n_cols)
    prepared = []
    for i, h in enumerate(header):
        meta = per_col[h]
        mode = meta.get("mode")
        item = {"mode": mode, "meta": meta, "pos": 0}
        if mode == _MODE_NOISE:
            if not meta.get("r_int"):
                return False, "列 %s 缺少 r_int，无法还原" % h, {}
            item["noise"] = _noise_ints(
                _build_tag(modality, subject_key, seed_path, h),
                numeric_counts[i], int(meta["r_int"]))
        elif mode == _MODE_SHIFT:
            if meta.get("shift_int") is None:
                return False, "列 %s 缺少 shift_int，无法还原" % h, {}
        prepared.append(item)

    nl = entry.get("newline") or "\n"
    bom = bool(entry.get("bom"))
    encoding = "utf-8-sig" if bom else "utf-8"
    written = 0
    try:
        with open(src_path, newline="", encoding=encoding,
                  errors=CODEC_ERRORS) as fin, \
                open(dst_path, "w", newline="", encoding="utf-8",
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
                    if item["mode"] == _MODE_KEEP:
                        out.append(cell)
                        continue
                    parsed = parse_fixed(cell)
                    if parsed is None:
                        out.append(cell)
                        continue
                    value = parsed[0]
                    if item["mode"] == _MODE_NOISE:
                        value -= item["noise"][item["pos"]]
                        item["pos"] += 1
                    else:
                        value -= int(item["meta"]["shift_int"])
                    out.append(format_fixed(value, int(item["meta"]["decimals"])))
                if len(row) > n_cols:
                    out.extend(row[n_cols:])
                w.writerow(out)
                written += 1
    except Exception as e:
        _remove_safely(dst_path)
        return False, "信号还原写入失败：%s" % e, {}

    stats = {"rows": written, "verified": False, "bytes_identical": False,
             "plain_sha256": None}
    if verify and entry.get("plain_sha256"):
        digest = _sha256_file(dst_path)
        stats.update({"verified": True, "plain_sha256": digest,
                      "bytes_identical": digest == entry["plain_sha256"]})
        if not stats["bytes_identical"]:
            (logger.warning if logger else (lambda *a, **k: None))(
                "信号还原：%s 的 sha256 与 manifest 不符（%s != %s）—— "
                "请确认密钥与算法版本一致", seed_path, digest, entry["plain_sha256"])
    return True, None, stats


# ==================== manifest 读写 ====================

def build_manifest(entries, keyed=None, key_scope=None, key=None,
                   encrypted_files=None):
    """把各文件的还原参数汇总为 manifest 字典

    :param keyed: 是否用了密钥派生（False = 无盐派生，脱敏强度有限）
    :param key_scope: ``"pack"`` = 本包专用密钥（随包附带还原包时）/
                      ``"global"`` = 平台主密钥（不在包内）
    :param key: 生成该包时实际使用的密钥，仅用于写入指纹；**不会写进 manifest**
    :param encrypted_files: 包内**其余**加密文件清单
        ``[{"arcname": ..., "kind": ...}, ...]``（不含信号条目，信号在 ``files`` 里）。
        它们没有可逆的脱敏参数（视频人脸马赛克 / userInfo 字段掩码都**不可逆**），
        所以只记路径、不记参数。随包脚本据此把它们解成明文；
        留这份清单是为了让接收方在运行前就知道"这一包里一共有多少密文"，
        而不是靠扫目录猜 —— 也避免脚本漏掉某个文件而无人察觉。

    注意：``files`` 里同时承载**音频**条目（``modality: "audio"``，定点加噪、
    同样可逆）。音频条目不按列描述，而是记 ``bits/channels/frames/r_int_by_ch``
    等还原参数；消费方（随包脚本、平台内还原）必须按 ``modality`` 分派算法。
    """
    if keyed is None:
        keyed = get_hmac_key() is not None
    if key_scope is None:
        key_scope = "global" if keyed else "none"
    if key_scope == "pack":
        hint = ("包内 _restore_kit/ 已自带还原脚本与本包专用密钥："
                "python _restore_kit/restore_signal_desens.py <本包>.zip")
    elif keyed:
        hint = ("用导出平台的 DESENS_HMAC_KEY 运行 back/tools/restore_signal_desens.py "
                "即可逐字节还原；manifest 不含密钥")
    else:
        hint = "该包未配置密钥（无盐派生），无需密钥即可还原"
    manifest = {
        "format": MANIFEST_FORMAT,
        "version": VERSION_TAG,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keyed": bool(keyed),
        "key_scope": key_scope,
        "key_fingerprint": key_fingerprint(key if key is not None else get_hmac_key()),
        "algorithm": (
            "fixed-point integer keystream; "
            "seed=HMAC-SHA256(master_key, 'signal-desens-v2|modality|pseudo_id|path|column'); "
            "ks=SHAKE256(seed); noise=(u64 % (2R+1)) - R; shift=constant offset"
        ),
        "restore_hint": hint,
        "files": list(entries or []),
        "encrypted_files": list(encrypted_files or []),
    }
    return manifest


def manifest_bytes(manifest) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")


def load_manifest(source):
    """从 zip 路径 / 目录 / manifest 文件 / bytes 载入 manifest"""
    if isinstance(source, (bytes, bytearray)):
        return json.loads(source.decode("utf-8"))
    if isinstance(source, zipfile.ZipFile):
        with source.open(MANIFEST_NAME) as f:
            return json.loads(f.read().decode("utf-8"))
    if isinstance(source, str) and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as zf:
            return load_manifest(zf)
    if isinstance(source, str) and os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            return json.load(f)
    if isinstance(source, str) and os.path.isdir(source):
        path = os.path.join(source, MANIFEST_NAME)
        if os.path.isfile(path):
            return load_manifest(path)
        raise FileNotFoundError("目录下未找到 %s：%s" % (MANIFEST_NAME, source))
    raise FileNotFoundError("无法定位 manifest：%r" % (source,))


def verify_manifest(manifest):
    """基础校验：返回问题列表（空列表 = 通过）"""
    problems = []
    if manifest.get("format") != MANIFEST_FORMAT:
        problems.append("不是本脱敏格式的 manifest：%r" % manifest.get("format"))
    if manifest.get("version") != VERSION_TAG:
        problems.append("算法版本不符（包 %r / 代码 %r）—— 需使用相同版本的还原工具"
                        % (manifest.get("version"), VERSION_TAG))
    if not manifest.get("keyed"):
        problems.append("该包脱敏时未配置 DESENS_HMAC_KEY（无盐派生），"
                        "知道算法的人都能重建噪声；建议配置密钥后重新导出")
    return problems


def _pseudo_from_path(path):
    """从 ``<pseudo_id>/<layer>/<data_type>/<file>`` 形式的逻辑路径取 pseudo_id"""
    if not path:
        return None
    parts = str(path).replace("\\", "/").split("/")
    return parts[0] if parts else None


def restore_zip(zip_path, out_dir, subject_key=None, logger=None,
                decrypt_hook=None):
    """把脱敏导出包按 manifest **无损还原**到 out_dir

    :param decrypt_hook: 可选 ``callable(bytes) -> bytes|None``，把 DMEC 密文字节解成
        明文（= 值级脱敏后的 CSV）。加密导出包（条目名以 ``.dmec`` 结尾）只有提供它
        才能就地还原；为 None 时这类条目记入 ``skipped`` 并提示先在平台内解密。
        钩子抛异常或返回 None 同样记入 ``skipped``，不会静默产出错误文件。

    :return: ``{"restored": [...], "skipped": [...], "problems": [...]}``
    """
    manifest = load_manifest(zip_path)
    report = {"restored": [], "skipped": [], "problems": verify_manifest(manifest)}
    os.makedirs(out_dir, exist_ok=True)
    # 包内自带「还原包」密钥（key_scope=pack）时优先用它派生噪声：平台主密钥不参与，
    # 单个包泄露不会波及历史/其他脱敏包。局部队内导入避免与 restore_kit 循环依赖。
    from app.utils.restore_kit import pack_key_from_zip
    pack_key = pack_key_from_zip(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for entry in manifest.get("files") or []:
            seed_path = entry.get("path")
            arcname = entry.get("arcname") or seed_path
            if arcname not in names:
                cand = [n for n in names if n == arcname or n == arcname + ".dmec"]
                if not cand:
                    report["skipped"].append(
                        {"path": seed_path, "reason": "zip 内未找到该文件"})
                    continue
                arcname = cand[0]
            payload = None
            if arcname.endswith(".dmec"):
                if decrypt_hook is None:
                    report["skipped"].append({
                        "path": seed_path,
                        "reason": "该条目为加密文件（.dmec），请先在平台内解密/导入得到"
                                  "脱敏 CSV，再对其运行还原",
                    })
                    continue
                try:
                    payload = decrypt_hook(zf.read(arcname))
                except Exception as e:  # noqa: BLE001 - 逐条降级，不中断整包
                    report["skipped"].append({
                        "path": seed_path, "reason": "DMEC 解密失败：%s" % e})
                    continue
                if not payload:
                    report["skipped"].append({
                        "path": seed_path,
                        "reason": "DMEC 解密不可用（主密钥缺失或非导出机密钥）"})
                    continue
            pseudo = (subject_key or entry.get("subject_key")
                      or _pseudo_from_path(seed_path) or "")
            is_audio = str(entry.get("modality") or "").lower() == "audio"
            tmp = os.path.join(out_dir, "_restore_input.tmp"
                               + (".wav" if is_audio else ".csv"))
            with open(tmp, "wb") as f:
                f.write(payload if payload is not None else zf.read(arcname))
            out_name = arcname[:-5] if arcname.endswith(".dmec") else arcname
            target = os.path.join(out_dir, out_name.replace("/", os.sep))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with use_hmac_key(pack_key):
                if is_audio:
                    # 音频：定点 PCM 减噪（与随包脚本同一算法）
                    from app.utils.audio_desensitize import restore_audio_file
                    ok, err, st = restore_audio_file(tmp, target, entry, pack_key
                                                     or get_hmac_key())
                else:
                    ok, err, st = restore_signal_file(
                        tmp, target, entry, pseudo, logger=logger)
            _remove_safely(tmp)
            if ok:
                report["restored"].append({
                    "path": out_name, "rows": st.get("rows"),
                    "frames": st.get("frames"),
                    "bytes_identical": st.get("bytes_identical"),
                    "sha256": st.get("plain_sha256") or st.get("sha256"),
                    "was_encrypted": arcname.endswith(".dmec"),
                })
            else:
                report["skipped"].append({"path": seed_path, "reason": err})
    return report
