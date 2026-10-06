# -*- coding: utf-8 -*-
"""导出链路分段基准：用**真实数据湖文件**测各阶段耗时与吞吐

导出一次的总耗时 ≈ 解密 + (脱敏) + 重加密/重包裹 + zip 打包。
本脚本逐段计时，定位真正的瓶颈在哪一段（不靠直觉）。

用法：
  python back/tools/bench_export_path.py <文件1> [<文件2> ...] [--out <json>]

⚠ 离线脚本：**不使用 create_app()**（避免 schema 升级 / 删除残留导出 / 启动扫描等副作用），
只用裸 Flask + MASTER_KEY_PATH + app_context().push()。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
import zipfile

BACK = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHUNK = 1024 * 1024


def _boot():
    """裸 Flask + 应用上下文，专供 crypto 使用（无 create_app 副作用）"""
    sys.path.insert(0, BACK)
    from flask import Flask
    app = Flask(__name__)
    app.config["BASE_DIR"] = BACK
    app.config["MASTER_KEY_PATH"] = os.path.join(BACK, "keys", "master.key")
    app.config["ENCRYPT_DATA_LAKE"] = True
    app.config["DATA_LAKE_DIR"] = os.path.join(BACK, "data_lake")
    ctx = app.app_context()
    ctx.push()
    return app, ctx


def _zip_one(src, dst, comp, level=None, stream=False):
    t0 = time.perf_counter()
    with zipfile.ZipFile(dst, "w", comp, compresslevel=level, allowZip64=True) as zf:
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
    zsize = os.path.getsize(dst)
    os.remove(dst)
    return dt, zsize


def bench_file(path):
    from app.utils.crypto import (is_encrypted_file, decrypt_file, encrypt_bytes,
                                  rewrap_dmec_file)
    size = os.path.getsize(path)
    res = {"path": path, "size_MB": round(size / 1024 / 1024, 1),
           "encrypted": is_encrypted_file(path), "steps": []}

    def add(name, seconds, extra=None):
        item = {"step": name, "sec": round(seconds, 3),
                "MBps": round(size / 1024 / 1024 / seconds, 1) if seconds > 0 else None}
        if extra:
            item.update(extra)
        res["steps"].append(item)
        print("  %-22s %7.3f s %8.1f MB/s %s" % (
            name, seconds, item["MBps"] or 0, extra or ""))

    print("== %s (%.1f MB, encrypted=%s)" % (path, size / 1024 / 1024, res["encrypted"]))

    # 1) 解密（当前实现：整文件读入内存）
    if res["encrypted"]:
        tracemalloc.start()
        t0 = time.perf_counter()
        plain = decrypt_file(path)
        dt = time.perf_counter() - t0
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        add("decrypt_file(整内存)", dt, {"peak_mem_MB": round(peak / 1024 / 1024, 1)})
    else:
        with open(path, "rb") as f:
            plain = f.read()

    # 2) 加密（encrypt_bytes：整块）
    if len(plain) <= 512 * 1024 * 1024:
        t0 = time.perf_counter()
        cipher = encrypt_bytes(plain)
        dt = time.perf_counter() - t0
        add("encrypt_bytes(整块)", dt)
        del cipher
    else:
        add("encrypt_bytes(整块)", 0, {"note": "跳过（文件过大）"})

    # 3) 流式重包裹（restore_kit 加密包路径）
    if res["encrypted"]:
        tmpdir = tempfile.mkdtemp(prefix="bench_rewrap_")
        try:
            dst = os.path.join(tmpdir, "rewrap.dmec")
            t0 = time.perf_counter()
            rewrap_dmec_file(path, dst, os.urandom(32))
            dt = time.perf_counter() - t0
            add("rewrap_dmec_file(流式)", dt)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 4) zip 打包：三种方案（当前默认 = deflate6_write）
    tmpdir = tempfile.mkdtemp(prefix="bench_zip_")
    try:
        for label, comp, level, stream in (
            ("zip deflate6 write(现状)", zipfile.ZIP_DEFLATED, None, False),
            ("zip deflate1 write", zipfile.ZIP_DEFLATED, 1, False),
            ("zip stored write", zipfile.ZIP_STORED, None, False),
            ("zip stored stream1M", zipfile.ZIP_STORED, None, True),
        ):
            dt, zsize = _zip_one(path, os.path.join(tmpdir, "o.zip"), comp, level, stream)
            add(label, dt, {"zip_MB": round(zsize / 1024 / 1024, 1),
                            "ratio": round(zsize / size, 4)})
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    del plain
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(__file__), "_bench_path_out.json"))
    args = ap.parse_args()

    _boot()
    results = []
    for p in args.files:
        if not os.path.exists(p):
            print("跳过（不存在）：%s" % p)
            continue
        results.append(bench_file(p))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("结果已写入 %s" % args.out)


if __name__ == "__main__":
    main()
