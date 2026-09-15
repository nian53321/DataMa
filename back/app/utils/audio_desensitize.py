# -*- coding: utf-8 -*-
"""音频脱敏 v2：定点 PCM 域确定性加噪 —— **可逆**，凭密钥逐字节还原原始音频

为什么不继续用 v1 的 rubberband 变调
------------------------------------
v1（``rubberband=pitch=r``）是相位声码器，**结构上有损**，把它改成"可逆"这条路
不存在第三条出口：

1. **反向变调只能部分恢复**（2026-09-15 实测，真实资产 52.489 s / 44.1 kHz / mono）：
   对 ``pitch=1.15`` 的输出再做 ``pitch=1/1.15``，与原始比较 —— 谱域只回退
   dB-RMSE 19% / MFCC 43%，长时平均谱（最贴近声纹系统 pooling 的特征）**零改善**
   （2.687 → 2.794 反升），波形相关 0.2667。损失全部来自相位声码器：
   同一算法 ``pitch=1.0`` 反复过 4 遍 lsd 仅 0.026 dB、corr 1.000000。
2. **"把丢掉的东西补回来"等价于再寄一份原文。** 反变换残差 = 原文 − 反变换(脱敏件)，
   而反变换是公开算法 —— 任何拿到脱敏件与残差的人可自行算出 ``x = r + inv(y)``，
   即残差本身就必须按密钥加密后再随包寄出，**体积与原文同量级**。那已经不是脱敏，
   是"把原文加密后寄出去"，与平台已有的「加密导出」完全重合。

真可逆的前提是**变换本身不丢信息**。定点整数域的确定性加噪满足这一点：
加与减互为逆运算，逐位精确。因此 v2 直接换掉变调，改做与信号链 v2 同族的
"定点 + 确定性噪声"。

算法
----
对每条音频文件（同一 ``pseudo_id`` 可复现、跨受试者不同）：

1. 定位 WAV 的 ``data`` 块，**只改这一个块的样本字节**，其余字节（含全部 RIFF
   头块、LIST/fact 等附加块）**逐字节原样保留**。因此还原产物与原文件的差异
   严格为零，而不是"重新编码出来的等价文件"。
2. 逐声道从**原始样本**算标准差 ``sigma_c``，取噪声幅度
   ``R_c = round(alpha · sigma_c · √3)``（均匀噪声 std = R/√3，故噪声 std ≈ alpha·sigma_c，
   即 SNR ≈ 20·log10(1/alpha)）。R 随录音响度自适应：安静录音不会被同一条固定幅度
   的噪声淹没，响亮录音也不会噪声过弱。
3. 逐样本加确定性噪声并**按位宽回绕**：``y = (x + n) mod 2^bits``，映射回有符号区间。
   回绕（而非裁剪）是**可逆的前提** —— 裁剪会把信号截到同一个极值上，多个不同 x
   映到同一个 y，信息不可恢复。回绕是双射，减法即逆运算。
4. 噪声流：``seed = HMAC-SHA256(密钥, tag)``，``tag = "audio-desens-v2|audio|pseudo_id|
   逻辑路径|ch<c>|k<chunk>"``，``ks = SHAKE256(seed)`` 的连续 8 字节大端整数
   （FIPS 202，跨语言跨版本稳定），``n = (u16 % (2R+1)) - R``，值域 ``[-R, R]``。
   分块（每块 ``CHUNK_FRAMES`` 帧）生成既避免长录音的密钥流撑爆内存，
   也让两侧实现逐块对齐。

还原
----
``x = (y - n) mod 2^bits``，噪声流用同一密钥与同一条目重新生成（manifest 里
``r_int_by_ch`` 已记录 R，还原端不必再统计）。

边界（必须知情）
----------------
- **加噪只降低声纹可识别性，不是密码学消除。** 强度由 ``NOISE_ALPHA`` 决定：
  alpha=3.0 对应 SNR ≈ -9.5 dB（噪声功率约为信号的 9 倍）。噪声是确定性的、
  且 R 随信号自适应，与"按伪随机序列做一次性加密"不同 —— 持密钥者可无损还原
  （这是设计目标），但**无密钥者若已知算法与路径**，仍可尝试重建噪声（这正是
  包内密钥要随包分发、且不支持无盐派生的原因）。
- **语音内容不再可懂**：SNR ≈ -9.5 dB 下语音基本不可听辨。可逆性**与强度无关**
  —— ``x = (y - n) mod 2^bits`` 是精确逆运算，``alpha`` 只是强度旋钮；
  已导出包的 R 固化在 manifest，事后改 ``alpha`` 不影响旧包还原。
- **时间结构仍然保留**：帧数 / 采样率 / 声道数逐样本不变，语音起止位置、
  节奏与时长从能量包络仍可读出；若威胁模型包含"从时长/节奏推断"，
  定点加噪覆盖不到。
- **确定性噪声使静音段的噪声可被识别为"被处理过"**：这是加噪的固有特征，
  不泄露原始内容，但无法伪装成未处理录音。
- 交付格式统一为 **16-bit PCM WAV**（源本身就是整数 PCM WAV 时**就地改写，
  格式与头部一字不改**）。非整数 PCM 容器（mp3/m4a/浮点 wav 等）会先用
  ffmpeg 解码为 16-bit PCM WAV 再处理，此时还原产物是该规范形态（采样率、
  声道数、帧数与原文件一致，样本值精确还原），不是原压缩容器 ——
  `manifest` 的 ``canonical`` 字段与 ``plain_sha256`` 都会如实反映。

强度标定（实测，非估计）
------------------------
真实资产 52.489s / 44.1kHz / mono / RMS -39.2 dBFS / peak 5524 上量得：

==========  =====  ==========  ==========  =========================
alpha       R      实测 SNR    波形相关    谱形位移 / 自然段间变化
==========  =====  ==========  ==========  =========================
0.15         93    +16.45 dB    0.989       2.87×
0.35（旧）   217     +9.12 dB    0.944       4.43×（语音清晰可懂）
0.80        497     +1.93 dB    0.781       5.96×
1.20        745     -1.57 dB    0.641       6.62×
2.00       1242     -6.04 dB    0.446       7.32×
**3.0（现）**  **1863**  **-9.52 dB**  **0.316**  **7.80×**
5.00       3105    -13.95 dB    0.197       8.20×
10.00      6211    -20.13 dB    0.098       8.56×
==========  =====  ==========  ==========  =========================

全部档位均 100% 逐字节可逆。另实测 ``mod 2^bits`` 的**回绕率恒为 0**
（即使 R=6211，``x+n`` 也未越出 16 位范围）→ 该取模在真实数据上退化为
普通加性噪声；保留取模是正确性保险（防极端动态范围），不是强度手段。
原始数据与复现命令见 ``back/tools/bench_audio_desens/``。
"""
import hashlib
import hmac
import os
import re
import subprocess
import sys
import tempfile
from array import array

from app.utils.transcode import get_ffmpeg

# 条目级算法标签：参与密钥派生域分隔；改动算法必须同时改这里
VERSION_TAG = "audio-desens-v2"

# 噪声强度（噪声 std / 该声道信号 std）。SNR = 20·log10(1/alpha)。
# 3.0 → SNR ≈ -9.5 dB：语音内容基本不可懂（实测波形相关 0.317、
# 谱形位移 8.6× 自然段间变化）。可逆性与强度无关 —— x = (y-n) mod 2^bits
# 是精确逆运算，alpha 只是强度旋钮；已导出包的 R 固化在 manifest，
# 事后调整本值不会让旧包失去可还原性。
# 实测脚本与原始数据：back/tools/bench_audio_desens/
# 结论文档：back/tools/README-音频脱敏强度实测.md
NOISE_ALPHA = 3.0

# 密钥流分块长度（帧）。脱敏端与随包还原脚本**必须一致** —— 噪声按块号派生
CHUNK_FRAMES = 1 << 14

# 只接受整数 PCM（WAVE_FORMAT_PCM）；浮点 PCM(3)/ADPCM(2) 等走转码兜底路径
WAV_PCM_FORMAT = 1
_PCM_WIDTHS = (1, 2, 3, 4)

# 派生密钥的域分隔符（与字段脱敏的 HMAC 用途隔离）
_DERIVE_VERSION = VERSION_TAG

_SQRT3 = 1.7320508075688772   # √3：均匀分布 std = R/√3

_TIMEOUT_SECONDS = 300

# ffmpeg -i 输出里的音频参数（转码兜底路径用）
_FFMPEG_SR_RE = re.compile(r"(\d+)\s*Hz")
_FFMPEG_MONO_RE = re.compile(r"\bmono\b")
_FFMPEG_STEREO_RE = re.compile(r"\bstereo\b")

# wave 模块位深 → ffmpeg 编解码器
_PCM_CODEC = {1: "pcm_u8", 2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}

__all__ = [
    "VERSION_TAG", "NOISE_ALPHA", "CHUNK_FRAMES",
    "build_tag", "noise_chunk", "parse_wav_pcm", "channel_r_int",
    "build_channel_tags", "mask_audio_bytes", "unmask_audio_bytes",
    "desensitize_audio_file", "restore_audio_file", "probe_audio",
]


# ==================== 密钥与噪声流（与随包脚本逐位一致） ====================

def build_tag(version, modality, subject_key, path, column) -> bytes:
    """噪声派生标签。**与随包脚本的同名函数必须逐字节一致**"""
    return b"|".join([
        str(version or "").encode("utf-8"),
        str(modality or "").encode("utf-8"),
        str(subject_key or "").encode("utf-8"),
        str(path or "").encode("utf-8"),
        str(column or "").encode("utf-8"),
    ])


def _seed(key, tag):
    """seed = HMAC-SHA256(密钥, tag)；无密钥时退化为 SHA256(tag)（无盐派生）"""
    if key:
        return hmac.new(key, tag, hashlib.sha256).digest()
    return hashlib.sha256(tag).digest()


def noise_chunk(key, tag, count, r_int, chunk_index):
    """生成第 ``chunk_index`` 块的 count 个噪声整数：``(u16 % (2R+1)) - R``

    :return: Python int 列表，值域 ``[-R, R]``
    """
    if count <= 0:
        return []
    sub = tag + b"|k" + str(int(chunk_index)).encode("ascii")
    raw = hashlib.shake_256(_seed(key, sub)).digest(2 * count)
    u = array("H")
    u.frombytes(raw)
    if sys.byteorder == "big":
        u.byteswap()          # 密钥流按**大端** 16 位字解析，与字节序无关
    modulus = 2 * int(r_int) + 1
    return [(v % modulus) - int(r_int) for v in u]


# ==================== WAV 解析（与随包脚本逐字节一致） ====================

def parse_wav_pcm(data):
    """定位整数 PCM WAV 的样本区

    :return: ``(layout, None)`` 或 ``(None, 原因)``。``layout`` 含
        ``bits/channels/sample_rate/block_align/data_offset/data_size/frames/audio_format``

    两个必须处理的现场情况：

    * **流式头**：``data`` 块长度写成 ``0`` 或 ``0xFFFFFFFF``（录音未收尾 /
      写头失败）。现场实测确有此类文件（``样例4/…/recording_20260820154316.wav``
      头部声称 4 GB，实际远小于此），按**文件实际剩余长度**取，否则会读到文件尾。
    * **奇数字节块**：RIFF 规定块按偶数字节对齐，遍历时必须算上填充字节，
      否则会把后面的块当成乱码。
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
                csize = avail                 # 流式头 / 截断：按实际可用长度
            info = dict(fmt)
            bits = info["bits"]
            width = bits // 8
            if info["audio_format"] != WAV_PCM_FORMAT:
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


def _channel_bytes(data, layout):
    """按声道拆出**样本值序列**（返回 int 序列，供统计与断言使用）"""
    width = layout["bits"] // 8
    ch = layout["channels"]
    off, size = layout["data_offset"], layout["data_size"]
    frames = layout["frames"]
    if width == 2:
        # array('h')：元素即 int，后续统计走 C 级迭代（不是逐样本切片）
        s = array("h")
        s.frombytes(data[off:off + size])
        if sys.byteorder == "big":
            s.byteswap()
        return [s[c::ch] for c in range(ch)]
    # 其它位深：逐样本解析后物化为 list（8/24/32 bit 在平台上是少数）
    signed = width != 1
    out = []
    for c in range(ch):
        vals = []
        p = off + c * width
        for _ in range(frames):
            vals.append(int.from_bytes(data[p:p + width], "little", signed=signed))
            p += ch * width
        out.append(vals)
    return out


def channel_r_int(data, layout, alpha=NOISE_ALPHA):
    """逐声道的噪声幅度 ``R = round(alpha · sigma · √3)``（R 至少为 1）

    ``sigma`` 是**原始样本**的标准差。R 随录制响度自适应，且被写进 manifest ——
    还原端直接用记录值，不做二次统计（否则两侧浮点差异会让噪声对不上）。
    """
    out = []
    for chan in _channel_bytes(data, layout):
        n = len(chan)
        total = 0
        total_sq = 0
        for v in chan:
            total += v
            total_sq += v * v
        if n <= 0:
            out.append(1)
            continue
        mean = total / float(n)
        var = max(0.0, total_sq / float(n) - mean * mean)
        r = int(round(alpha * (var ** 0.5) * _SQRT3))
        out.append(r if r > 0 else 1)
    return out


# ==================== 加噪 / 减噪（同一实现的两个方向） ====================

def _iter_chunks(frames):
    """产出 ``(起始帧, 本块帧数, 块号)`` —— 两侧实现必须同序"""
    done = 0
    k = 0
    while done < frames:
        n = min(CHUNK_FRAMES, frames - done)
        yield done, n, k
        done += n
        k += 1


def _apply(data, layout, key, tags, r_by_ch, reverse):
    """返回替换了 data 区样本的新 bytes；其余字节逐字节保留

    :param tags: 每声道的噪声标签
    :param r_by_ch: 每声道的噪声幅度 R（还原端来自 manifest）
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

    for start, n, k in _iter_chunks(frames):
        for c in range(ch):
            r = int(r_by_ch[c])
            noise = noise_chunk(key, tags[c], n, r, k)
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


def build_channel_tags(seed_path, subject_key, channels, version=VERSION_TAG,
                       modality="audio"):
    """每声道的噪声标签（声道号参与派生 ⇒ 立体声两声道噪声互不相同）"""
    return [build_tag(version, modality, subject_key, seed_path, "ch%d" % c)
            for c in range(channels)]


def mask_audio_bytes(data, layout, key, seed_path, subject_key,
                     alpha=NOISE_ALPHA, r_by_ch=None):
    """对 WAV 字节做可逆声纹脱敏

    :param key: 脱敏密钥（bytes；None = 无盐派生，强度有限）
    :return: ``(masked_bytes, r_by_ch)``
    """
    if r_by_ch is None:
        r_by_ch = channel_r_int(data, layout, alpha=alpha)
    tags = build_channel_tags(seed_path, subject_key, layout["channels"])
    return _apply(data, layout, key, tags, r_by_ch, reverse=False), list(r_by_ch)


def unmask_audio_bytes(data, layout, key, seed_path, subject_key, r_by_ch):
    """``mask_audio_bytes`` 的逆运算（平台内还原用；逐位精确）"""
    tags = build_channel_tags(seed_path, subject_key, layout["channels"])
    return _apply(data, layout, key, tags, r_by_ch, reverse=True)


# ==================== 探测（转码兜底路径用） ====================

def _probe_wav(path):
    """用 wave 模块读容器参数（仅用于兜底转码时确定 -ar/-ac）"""
    try:
        import wave
        with wave.open(path, "rb") as w:
            return {"sample_rate": w.getframerate(),
                    "channels": w.getnchannels(),
                    "codec": _PCM_CODEC.get(w.getsampwidth())}
    except Exception:
        return None


def _probe_via_ffmpeg(ffmpeg, path):
    """解析 ``ffmpeg -i`` 的 stderr 取音频参数（非 wav 容器的兜底）

    注意：``ffmpeg -i <file>`` 不给输出文件时返回码为 1，属正常行为。
    """
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-i", path],
                              capture_output=True, timeout=60)
        text = proc.stderr.decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = _FFMPEG_SR_RE.search(text)
    if not m:
        return None
    channels = None
    if _FFMPEG_MONO_RE.search(text):
        channels = 1
    elif _FFMPEG_STEREO_RE.search(text):
        channels = 2
    return {"sample_rate": int(m.group(1)), "channels": channels, "codec": None}


def probe_audio(ffmpeg, path):
    """探测音频参数，返回 ``{sample_rate, channels, codec}``；不可识别时 None"""
    info = _probe_wav(path)
    if info and info.get("sample_rate"):
        return info
    return _probe_via_ffmpeg(ffmpeg, path)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_safely(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ==================== 对外入口：脱敏 ====================

def desensitize_audio_file(src_path, dst_path, subject_key, seed_path=None,
                           logger=None, alpha=NOISE_ALPHA):
    """对音频文件施加**可逆**的声纹脱敏，写出到 dst_path

    :param subject_key: 受试者稳定标识（``pseudo_id``）
    :param seed_path: 参与密钥派生的逻辑路径（导出包内路径，不含 .dmec）；
                      缺省用文件名。**还原时必须提供同一个值**（manifest 已记录）
    :return: ``(成功?, 错误信息, 统计 dict)``；``stats["manifest"]`` 即还原参数。
        失败时**不留输出文件** —— 绝不回退为明文导出。
    """
    if not os.path.exists(src_path):
        return False, "源文件不存在", {}
    seed_path = seed_path or os.path.basename(src_path)
    try:
        with open(src_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return False, "读取源文件失败：%s" % e, {}

    from app.utils.signal_desensitize import get_hmac_key
    key = get_hmac_key()

    layout, why = parse_wav_pcm(raw)
    canonical = False
    origin_format = "wav"
    if layout is None:
        # ---------- 兜底：非整数 PCM WAV（mp3/m4a/浮点 wav…）先转成 16-bit PCM WAV ----------
        ffmpeg = get_ffmpeg()
        if not ffmpeg:
            return False, "无法就地处理（%s）且服务器未安装 ffmpeg" % why, {}
        info = probe_audio(ffmpeg, src_path) or {}
        fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="_audio_canon_")
        os.close(fd)
        try:
            cmd = [ffmpeg, "-y", "-i", src_path, "-vn", "-c:a", "pcm_s16le"]
            if info.get("sample_rate"):
                cmd += ["-ar", str(info["sample_rate"])]
            if info.get("channels"):
                cmd += ["-ac", str(info["channels"])]
            cmd.append(tmp_path)
            try:
                subprocess.run(cmd, capture_output=True, check=True,
                               timeout=_TIMEOUT_SECONDS)
            except subprocess.CalledProcessError as e:
                detail = (e.stderr or b"").decode("utf-8", errors="ignore")[:300]
                return False, "音频转码失败（%s）：%s" % (why, detail or "ffmpeg 非零退出"), {}
            except subprocess.TimeoutExpired:
                return False, "音频转码超时", {}
            with open(tmp_path, "rb") as f:
                raw = f.read()
            layout, why = parse_wav_pcm(raw)
            if layout is None:
                return False, "转码后的 WAV 仍不可解析：%s" % why, {}
            canonical = True
            origin_format = os.path.splitext(src_path)[1].lstrip(".").lower() or "unknown"
            if logger:
                logger.warning(
                    "音频脱敏：%s 不是整数 PCM WAV（%s），已转码为 %d bit/%d Hz/%d ch "
                    "PCM WAV 后处理；还原产物为该规范形态（样本值精确还原，"
                    "但不是原压缩容器）",
                    os.path.basename(src_path), why, layout["bits"],
                    layout["sample_rate"], layout["channels"])
        finally:
            _remove_safely(tmp_path)

    try:
        masked, r_by_ch = mask_audio_bytes(
            raw, layout, key, seed_path, subject_key, alpha=alpha)
    except Exception as e:  # noqa: BLE001
        return False, "音频脱敏失败：%s" % e, {}

    if masked == raw:
        # 噪声幅度被压到 0（全静音/单值录音）时会走到这里：不能静默当作已脱敏
        return False, "音频内容为常量（全静音），加噪后无任何变化，已跳过", {}

    try:
        with open(dst_path, "wb") as f:
            f.write(masked)
    except OSError as e:
        _remove_safely(dst_path)
        return False, "音频脱敏写入失败：%s" % e, {}
    if os.path.getsize(dst_path) == 0:
        _remove_safely(dst_path)
        return False, "音频脱敏输出为空", {}

    width = layout["bits"] // 8
    entry = {
        "path": seed_path,
        "subject_key": subject_key,
        "modality": "audio",
        "version": VERSION_TAG,
        "container": "wav",
        "audio_format": _PCM_CODEC.get(width, "pcm_s16le"),
        "bits": layout["bits"],
        "channels": layout["channels"],
        "sample_rate": layout["sample_rate"],
        "frames": layout["frames"],
        "block_align": layout["block_align"],
        "data_offset": layout["data_offset"],
        "data_size": layout["data_size"],
        "alpha": alpha,
        "r_int_by_ch": list(r_by_ch),
        # 噪声 std ≈ R/√3（用于人工核对脱敏强度）
        "noise_std_by_ch": [round(r / _SQRT3, 2) for r in r_by_ch],
        "canonical": canonical,
        "origin_format": origin_format,
        "plain_sha256": hashlib.sha256(raw).hexdigest(),
        "plain_size": len(raw),
    }
    stats = {
        "manifest": entry,
        "frames": layout["frames"],
        "channels": layout["channels"],
        "sample_rate": layout["sample_rate"],
        "bits": layout["bits"],
        "canonical": canonical,
        "r_int_by_ch": list(r_by_ch),
    }
    return True, None, stats


# ==================== 对外入口：还原（平台内） ====================

def restore_audio_file(src_path, dst_path, entry, key, verify=True):
    """按 manifest 条目把脱敏音频**逐字节还原**为原始音频

    写入策略与信号链一致：先写同目录临时文件，校验通过才原子替换；
    sha256 不符一律放弃覆盖（密钥不对时源文件保持原样）。

    :return: ``(成功?, 错误信息, 统计 dict)``
    """
    if not os.path.exists(src_path):
        return False, "源文件不存在", {}
    if not isinstance(entry, dict):
        return False, "manifest 条目非法", {}
    mod = entry.get("version")
    if mod and mod != VERSION_TAG:
        return False, ("算法版本不符（条目 %r / 代码 %r）—— 请使用导出时随包提供的"
                       "脚本或同版本平台" % (mod, VERSION_TAG)), {}
    seed_path = entry.get("path")
    subject_key = entry.get("subject_key") or ""
    r_by_ch = entry.get("r_int_by_ch")
    if not seed_path:
        return False, "manifest 条目缺少 path（无法派生密钥）", {}
    if not r_by_ch:
        return False, "manifest 条目缺少 r_int_by_ch（无法重建噪声）", {}

    with open(src_path, "rb") as f:
        data = f.read()
    layout, why = parse_wav_pcm(data)
    if layout is None:
        return False, "待还原文件不是整数 PCM WAV：%s" % why, {}
    for field in ("channels", "bits", "sample_rate", "frames"):
        if entry.get(field) is not None and layout[field] != entry[field]:
            return False, ("文件与清单的 %s 不一致（文件 %s / 清单 %s）——"
                           "这可能不是同一次导出的文件"
                           % (field, layout[field], entry[field])), {}

    try:
        plain = unmask_audio_bytes(data, layout, key, seed_path, subject_key,
                                   r_by_ch)
    except Exception as e:  # noqa: BLE001
        return False, "音频还原失败：%s" % e, {}

    dst_dir = os.path.dirname(os.path.abspath(dst_path)) or "."
    os.makedirs(dst_dir, exist_ok=True)
    tmp_path = os.path.join(dst_dir, ".%s.restore-tmp" % os.path.basename(dst_path))
    try:
        with open(tmp_path, "wb") as f:
            f.write(plain)
    except OSError as e:
        _remove_safely(tmp_path)
        return False, "写入失败：%s" % e, {}

    stats = {"frames": layout["frames"], "bytes_identical": None, "sha256": None}
    if verify and entry.get("plain_sha256"):
        digest = hashlib.sha256(plain).hexdigest()
        stats["sha256"] = digest
        stats["bytes_identical"] = (digest == entry["plain_sha256"])
        if not stats["bytes_identical"]:
            _remove_safely(tmp_path)
            return False, ("还原结果与清单 sha256 不符（已丢弃，原文件未改动）——"
                           "通常是密钥不对或包被改动过"), stats
    try:
        os.replace(tmp_path, dst_path)
    except OSError as e:
        _remove_safely(tmp_path)
        return False, "替换原文件失败：%s" % e, stats
    return True, None, stats
