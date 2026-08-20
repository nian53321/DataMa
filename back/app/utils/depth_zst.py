# -*- coding: utf-8 -*-
"""DZST 深度序列编解码（RealSense 深度录制存储格式）

文件格式（小端）：
  v1: "DZST" | version=1 | width u32 | height u32 | frame_count u32
      逐帧: [compressed_len u32][zstd(z16 原始帧字节)]
  v2: "DZST" | version=2 | width u32 | height u32 | frame_count u32 | quantum u32
      逐帧: [flags u32][compressed_len u32][zstd payload]
        flags bit0: 0=关键帧（uint16 量化深度值），1=差分帧（int16 与上一帧差值）

v2 压缩策略（体积约为 v1 的 2/5 ~ 1/2）：
  - 量化：z16 值按 quantum=4mm 步长 round-to-nearest 量化（先 clamp 到
    65532mm 再加半步长右移），量化误差 ±2mm，低于 D455 深度噪声
    （1m 处约 ±2-4mm，随距离增大）；QUANTUM 调为 1 即回到无损差分模式；
  - 帧间差分：相邻帧绝大多数像素差值为 0 或 ±1 量化级（深度噪声），
    zstd 对差分帧压缩率极高；固定间隔插入关键帧，差分膨胀（场景突变）
    时自适应提前回退；量化步长是压缩率的主导参数（实测 2/4/8mm 步长
    在典型场景下分别为原始体积的 22.5%/14.5%/9.3%）；
  - 压缩级别 KF-L6+Δ-L3：深度数据高冗余，级别对压缩率影响小（L19→L6
    仅差 ~5%）但对速度影响巨大（1280x720 实测 L19+L12 每秒压缩耗时
    2.69s 跟不上 30fps 会积压丢帧，L6+L3 仅 0.22s/s 有 4 倍余量）；
  - 解码端输出仍为毫米深度值（v2 为量化近似），无效像素 0 语义不变。

解码器同时兼容 v1（老录制）与 v2。
"""
import struct

MAGIC = b"DZST"
VERSION = 2
QUANTUM = 4             # 量化步长（z16 单位，D455 默认 1mm/单位即 4mm；须为 2 的幂）
QMAX = 65535 // QUANTUM     # 量化值上限（QUANTUM=1 时即为无损模式）
CLAMP_MAX = QMAX * QUANTUM  # 量化前 clamp 上限（65532mm，覆盖 D455 全量程）
KEYFRAME_INTERVAL = 30  # 关键帧间隔（30fps 下约 1 秒）
DELTA_FORCE_RATIO = 0.5  # 差分帧压缩后超过关键帧大小该比例时强制回退关键帧
_QSHIFT = QUANTUM.bit_length() - 1  # log2(QUANTUM)，量化移位数


class DepthZstWriter:
    """DZST v2 流式写入器（单线程使用：由录制写线程顺序调用）"""

    def __init__(self, fobj, w, h):
        import zstandard as _zstd
        self._f = fobj
        self._w, self._h = w, h
        # 关键帧全量数据大（w*h*2 字节），高级别 + 多线程换取压缩率；
        # 差分帧数据小且高度可压，中级别即可（速度优先）
        # 级别权衡（实测 1280x720@30fps，深度数据高冗余、级别对压缩率影响小）：
        # L19+L12 每秒压缩耗时 2.69s 跟不上帧率；L6+L3 仅 0.22s/s（4 倍余量），
        # 体积只差 ~5%——实时性优先，录制丢帧的代价远大于 5% 体积
        self._kf = _zstd.ZstdCompressor(level=6, threads=-1)
        self._delta = _zstd.ZstdCompressor(level=3)
        self._prev = None      # 上一帧量化值（uint16 展平数组）
        self._kf_size = None   # 最近关键帧压缩大小（自适应回退阈值参考）
        self._since_kf = 0
        self._f.write(MAGIC)
        self._f.write(struct.pack("<IIIII", VERSION, w, h, 0, QUANTUM))

    def write_frame(self, z16_bytes):
        """写入一帧 z16 原始字节（w*h*2 字节），内部决定关键帧/差分帧"""
        import numpy as np
        arr = np.frombuffer(z16_bytes, dtype="<u2")
        # round-to-nearest 量化（误差 ±QUANTUM/2）：先 clamp 再加半步长，避免 uint16 回绕
        q = (np.minimum(arr, CLAMP_MAX) + QUANTUM // 2) >> _QSHIFT  # uint16, 0..QMAX

        is_kf = self._prev is None or self._since_kf >= KEYFRAME_INTERVAL
        if is_kf:
            comp = self._kf.compress(q.tobytes())
            flags = 0
            self._kf_size = len(comp)
            self._since_kf = 0
        else:
            d = q.astype(np.int16) - self._prev.astype(np.int16)
            comp = self._delta.compress(d.tobytes())
            if self._kf_size and len(comp) > self._kf_size * DELTA_FORCE_RATIO:
                # 场景突变：差分膨胀，改存关键帧更划算
                comp = self._kf.compress(q.tobytes())
                flags = 0
                self._kf_size = len(comp)
                self._since_kf = 0
            else:
                flags = 1
                self._since_kf += 1

        self._f.write(struct.pack("<II", flags, len(comp)))
        self._f.write(comp)
        self._prev = q

    def finish(self, frame_count):
        """回填帧数并 flush（不关闭文件，由调用方管理生命周期）"""
        self._f.seek(16)
        self._f.write(struct.pack("<I", frame_count))
        self._f.flush()


def read_header(fobj):
    """读取 DZST 头部，返回 (version, width, height, frame_count, quantum)；非 DZST 返回 None"""
    fobj.seek(0)
    if fobj.read(4) != MAGIC:
        return None
    ver, w, h, count = struct.unpack("<IIII", fobj.read(16))
    quantum = 1
    if ver >= 2:
        chunk = fobj.read(4)
        if len(chunk) < 4:
            return None
        (quantum,) = struct.unpack("<I", chunk)
    return ver, w, h, count, quantum


def iter_depth_frames(fobj, step=1):
    """DZST v1/v2 解码生成器：yield (h, w) float32 深度帧（毫米，无效像素=0）

    step>1 时每 step 帧取 1 帧（取样帧序号为 step 的整数倍，与历史行为一致）。
    v2 差分帧依赖顺序解码，step 只影响 yield 频率，不影响解码状态。
    """
    import numpy as np
    import zstandard as _zstd

    header = read_header(fobj)
    if header is None:
        return
    ver, w, h, _count, quantum = header
    dctx = _zstd.ZstdDecompressor()
    prev = None
    idx = 0
    max_out = w * h * 2
    while True:
        if ver >= 2:
            hdr = fobj.read(8)
            if len(hdr) < 8:
                break
            flags, clen = struct.unpack("<II", hdr)
        else:
            hdr = fobj.read(4)
            if len(hdr) < 4:
                break
            flags, (clen,) = 0, struct.unpack("<I", hdr)
        blk = fobj.read(clen)
        if len(blk) < clen:
            break
        raw = dctx.decompress(blk, max_output_size=max_out)
        if ver >= 2 and flags & 1:
            if prev is None:
                continue  # 损坏文件：首帧即差分，跳过
            d = np.frombuffer(raw, dtype="<i2")
            q = (prev.astype(np.int32) + d).astype(np.uint16)
        else:
            q = np.frombuffer(raw, dtype="<u2")
        prev = q
        idx += 1
        if step and idx % step != 0:
            continue
        yield (q * quantum).astype(np.float32).reshape(h, w)
