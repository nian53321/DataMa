# -*- coding: utf-8 -*-
"""导出打包端到端对比：旧方案（整包 deflate + zf.write）vs 新方案（逐条目分派 + 流式）

正确性优先：两种方案打出的 zip 必须**逐条目 sha256 一致**，否则提速无效。

用法：
  python back/tools/bench_export_e2e.py [--root <数据湖目录>] [--limit N] [--out <json>]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from unittest.mock import MagicMock

BACK = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACK)
sys.modules.setdefault("av", MagicMock())   # 宿主机无 av；本脚本不碰视频脱敏

from app.services.export_service import _zip_add_file  # noqa: E402


def _collect(root, limit):
    files = []
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            if os.path.isfile(p):
                files.append((p, os.path.relpath(p, root).replace("\\", "/")))
    files.sort(key=lambda x: -os.path.getsize(x[0]))
    return files[:limit] if limit else files


def _sha_of_member(zf, name):
    h = hashlib.sha256()
    with zf.open(name) as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _build(files, plan, dst):
    t0 = time.perf_counter()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for src, arc in files:
            if plan == "old":
                zf.write(src, arc)
            else:
                # 数据湖文件全是密文 → cipher=True（与加密导出一致）
                _zip_add_file(zf, src, arc, cipher=True)
    dt = time.perf_counter() - t0
    return dt, os.path.getsize(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(BACK, "data_lake"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                  "_bench_e2e_out.json"))
    args = ap.parse_args()

    files = _collect(args.root, args.limit)
    total = sum(os.path.getsize(p) for p, _ in files)
    print("素材：%d 个文件，%.1f MB（%s）" % (len(files), total / 1024 / 1024, args.root))

    tmpdir = tempfile.mkdtemp(prefix="bench_e2e_")
    res = {"files": len(files), "total_MB": round(total / 1024 / 1024, 1), "plans": {}}
    try:
        zips = {}
        for plan in ("old", "new"):
            dst = os.path.join(tmpdir, "%s.zip" % plan)
            dt, size = _build(files, plan, dst)
            zips[plan] = dst
            res["plans"][plan] = {
                "sec": round(dt, 3),
                "MBps": round(total / 1024 / 1024 / dt, 1),
                "zip_MB": round(size / 1024 / 1024, 1),
                "ratio": round(size / total, 4),
            }
            print("  %-4s %8.3f s %8.1f MB/s  zip=%.1f MB (ratio %.4f)"
                  % (plan, dt, total / 1024 / 1024 / dt, size / 1024 / 1024, size / total))

        # 正确性：逐条目 sha256 必须一致
        with zipfile.ZipFile(zips["old"]) as zo, zipfile.ZipFile(zips["new"]) as zn:
            names_o, names_n = zo.namelist(), zn.namelist()
            same_names = names_o == names_n
            mismatched = []
            types = {}
            for nm in names_n:
                info = zn.getinfo(nm)
                types[info.compress_type] = types.get(info.compress_type, 0) + 1
                if _sha_of_member(zo, nm) != _sha_of_member(zn, nm):
                    mismatched.append(nm)
            # CRC 自检（zipfile 读时自动校验）
            bad = zn.testzip()
        res["same_namelist"] = same_names
        res["sha_mismatched"] = mismatched
        res["testzip_bad_member"] = bad
        res["new_compress_type_hist"] = {str(k): v for k, v in types.items()}
        res["speedup"] = round(res["plans"]["old"]["sec"] / res["plans"]["new"]["sec"], 2)
        print("  一致性：namelist=%s，sha256 不一致=%d，testzip=%s，提速 %.2f×"
              % (same_names, len(mismatched), bad, res["speedup"]))
        print("  新方案条目压缩方式分布（0=STORED, 8=DEFLATED）：%s" % res["new_compress_type_hist"])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("结果已写入 %s" % args.out)


if __name__ == "__main__":
    main()
