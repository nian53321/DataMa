# -*- coding: utf-8 -*-
"""导出打包基准：定位「压缩/打包」这一层的耗时与吞吐

测量维度（同一批素材，逐方案计时）：
  - 压缩方式：ZIP_STORED / DEFLATED(level 6，即当前默认) / DEFLATED(level 1)
  - 写入方式：zf.write(磁盘文件) / zf.open(流式分块)
  - 素材类型：不可压缩（模拟 DMEC 密文、视频）/ 可压缩（模拟 CSV 文本）

用法：
  python back/tools/bench_zip_pack.py [--size-mb 512] [--out <json路径>]

输出：每种组合的 [耗时 s, 吞吐 MB/s, zip 大小, 压缩率]，落盘 JSON + 控制台
"""
import argparse
import json
import os
import shutil
import tempfile
import time
import zipfile

CHUNK = 1024 * 1024  # 1MB 分块


def _ensure_material(path, size_mb, kind):
    """生成基准素材；已存在且大小一致则复用（避免每次重造）"""
    target = size_mb * 1024 * 1024
    if os.path.exists(path) and os.path.getsize(path) == target:
        return path
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        if kind == "random":
            # 不可压缩：模拟加密密文 / 已编码视频
            written = 0
            block = os.urandom(CHUNK)
            while written < target:
                n = min(CHUNK, target - written)
                f.write(block[:n])
                written += n
        else:
            # 可压缩：模拟 EEG/ECG CSV（重复结构的十进制文本）
            row = b"12345,0.004000,realtime,17,-123456.789012,72.5\n"
            reps = target // len(row) + 1
            buf = row * min(reps, 4096)
            written = 0
            while written < target:
                n = min(len(buf), target - written)
                f.write(buf[:n])
                written += n
    os.replace(tmp, path)
    return path


def _zip_write(src, dst, mode, level=None, stream=False):
    """把一个文件打进 zip：zf.write（磁盘）或 zf.open（流式分块）"""
    t0 = time.perf_counter()
    if mode == "stored":
        comp, lvl = zipfile.ZIP_STORED, None
    else:
        comp, lvl = zipfile.ZIP_DEFLATED, level
    with zipfile.ZipFile(dst, "w", comp, compresslevel=lvl, allowZip64=True) as zf:
        if stream:
            with zf.open("payload.bin", "w", force_zip64=True) as zo:
                with open(src, "rb") as fi:
                    while True:
                        b = fi.read(CHUNK)
                        if not b:
                            break
                        zo.write(b)
        else:
            zf.write(src, "payload.bin")
    dt = time.perf_counter() - t0
    return dt, os.path.getsize(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-mb", type=int, default=512)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--out", default=r"D:\Py_Project\DataManagement\back\tools\_bench_zip_out.json")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    workdir = args.workdir or tempfile.mkdtemp(prefix="bench_zip_")
    os.makedirs(workdir, exist_ok=True)

    mats = {}
    for kind, name in (("random", "rand.bin"), ("text", "text.csv")):
        p = os.path.join(workdir, name)
        t0 = time.perf_counter()
        _ensure_material(p, args.size_mb, kind)
        mats[kind] = (p, time.perf_counter() - t0)

    plans = [
        ("stored_write", "stored", None, False),
        ("deflate6_write", "deflated", None, False),   # None → zlib 默认(6)，即现状
        ("deflate1_write", "deflated", 1, False),
        ("deflate3_write", "deflated", 3, False),
        ("stored_stream", "stored", None, True),
        ("deflate1_stream", "deflated", 1, True),
    ]

    results = {"size_mb": args.size_mb, "workdir": workdir, "runs": []}
    for kind, (src, gen_s) in mats.items():
        src_size = os.path.getsize(src)
        for label, mode, level, stream in plans:
            best = None
            for r in range(args.rounds):
                dst = os.path.join(workdir, "out_%s_%s_%d.zip" % (kind, label, r))
                dt, zsize = _zip_write(src, dst, mode, level, stream)
                os.remove(dst)
                if best is None or dt < best[0]:
                    best = (dt, zsize)
            dt, zsize = best
            results["runs"].append({
                "material": kind,
                "plan": label,
                "seconds": round(dt, 3),
                "MBps": round(src_size / 1024 / 1024 / dt, 1),
                "zip_MB": round(zsize / 1024 / 1024, 1),
                "ratio": round(zsize / src_size, 4),
            })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 控制台摘要
    print("size_mb=%d  workdir=%s" % (args.size_mb, workdir))
    print("%-12s %-16s %8s %10s %10s %8s" % ("material", "plan", "sec", "MB/s", "zip_MB", "ratio"))
    for r in results["runs"]:
        print("%-12s %-16s %8.3f %10.1f %10.1f %8.4f" % (
            r["material"], r["plan"], r["seconds"], r["MBps"], r["zip_MB"], r["ratio"]))

    if args.workdir is None:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
