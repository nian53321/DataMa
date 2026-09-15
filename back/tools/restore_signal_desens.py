#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""EEG/ECG 脱敏导出包 **无损还原**工具

与 `app/utils/signal_desensitize.py` 的 `signal-desens-v2` 算法配套：导出包里会带一份
`_desens_manifest.json`（不含密钥），记录每个文件每列的精度、噪声幅度、时间平移量与
原文 sha256。持 `DESENS_HMAC_KEY` 的人用本工具即可把脱敏 CSV **逐字节还原**为原文。

用法
----
::

    # 1) 明文导出包（zip 内就是脱敏 CSV）——直接还原
    python tools/restore_signal_desens.py data_export_plain_20260914_190000.zip -o restored

    # 2) 先看看包里有什么、每列怎么处理的（不写文件）
    python tools/restore_signal_desens.py data_export_plain_xxx.zip --list

    # 3) 加密导出包（条目是 .dmec）——需先在平台内解密/导入得到脱敏 CSV 后再还原
    python tools/restore_signal_desens.py restored_dir/ -o out

    # 4) 只还原单个 CSV（配合包内 manifest）
    python tools/restore_signal_desens.py eeg_xxx.csv --manifest _desens_manifest.json \\
        --pseudo-id 111111112 -o out

    # 5) 显式指定密钥文件（默认读 DESENS_HMAC_KEY / DESENS_KEY_PATH / /app/keys/desens.key）
    python tools/restore_signal_desens.py export.zip -o out --key-file keys/desens.key

注意
----
- 还原产物是**未脱敏的原始受试者数据**，落盘后请按原始数据的保密要求管理。
- 还原依赖**同一把密钥**与**同版本算法**：密钥不符或版本不符时 sha256 校验会失败，
  工具会明确报错而不会静默输出错误数据（可用 ``--no-verify`` 跳过校验，不建议）。
- 列分类：结构/锚点列（Sample Index / time_sec / source / frame …）本就未改动，
  还原时原样透传；被加噪的是信号列，被整列平移的是绝对时间列。
"""
import argparse
import json
import os
import sys
import zipfile

# 允许直接以 `python tools/restore_signal_desens.py` 运行（等价于容器内 /app/tools/...）
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT = os.path.dirname(_HERE)
for _p in (_APP_ROOT, os.path.dirname(_APP_ROOT)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from app.utils.signal_desensitize import (  # noqa: E402
    MANIFEST_NAME, VERSION_TAG, load_manifest, manifest_bytes, restore_signal_file,
    set_hmac_key, verify_manifest,
)

_DEFAULT_KEY_PATHS = (
    "/app/keys/desens.key",
    os.path.join(_APP_ROOT, "keys", "desens.key"),
    os.path.join(_APP_ROOT, "desens.key"),
    os.path.join(os.path.dirname(_APP_ROOT), "back", "keys", "desens.key"),
)


def _resolve_key(explicit_path=None):
    """按 显式路径 → 环境变量 → 常见路径 解析主密钥，返回 (key_bytes|None, 来源说明)"""
    if explicit_path:
        with open(explicit_path, "r", encoding="ascii") as f:
            key = bytes.fromhex(f.read().strip())
        if len(key) != 32:
            raise ValueError("密钥长度异常（应为 32 字节）：%s" % explicit_path)
        return key, "文件 %s" % explicit_path
    env = os.getenv("DESENS_HMAC_KEY", "")
    if env:
        return env.encode("utf-8"), "环境变量 DESENS_HMAC_KEY"
    candidates = []
    if os.getenv("DESENS_KEY_PATH"):
        candidates.append(os.getenv("DESENS_KEY_PATH"))
    candidates.extend(_DEFAULT_KEY_PATHS)
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="ascii") as f:
                    key = bytes.fromhex(f.read().strip())
                if len(key) == 32:
                    return key, "密钥文件 %s" % path
            except Exception:
                continue
    return None, "未找到"


def _find_entry_file(root, entry):
    """在目录中定位某 manifest 条目对应的文件（容忍 .dmec 后缀与扁平化布局）"""
    arcname = entry.get("arcname") or entry.get("path") or ""
    cands = [arcname, arcname[:-5] if arcname.endswith(".dmec") else arcname + ".dmec"]
    for c in cands:
        p = os.path.join(root, c.replace("/", os.sep))
        if os.path.isfile(p):
            return p
    base = os.path.basename(arcname)
    for dirpath, _dirs, files in os.walk(root):
        if base in files:
            return os.path.join(dirpath, base)
        if base.endswith(".dmec") is False and (base + ".dmec") in files:
            return os.path.join(dirpath, base + ".dmec")
    return None


def _pseudo_of(entry, override):
    if override:
        return override
    path = entry.get("path") or entry.get("arcname") or ""
    parts = str(path).replace("\\", "/").split("/")
    return parts[0] if parts else ""


def _human(v, decimals):
    """整数域标量 -> 人类可读（按 decimals 还原小数位，去掉多余 0）"""
    if v is None:
        return "-"
    try:
        s = ("%.*f" % (int(decimals or 0), int(v) / float(10 ** int(decimals or 0))))
    except Exception:
        return str(v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _print_listing(manifest):
    problems = verify_manifest(manifest)
    print("manifest 版本 : %s (format=%s)" % (manifest.get("version"),
                                              manifest.get("format")))
    print("生成时间     : %s" % manifest.get("created_at"))
    print("密钥派生     : %s" % ("已配置 DESENS_HMAC_KEY"
                                 if manifest.get("keyed") else "未配置（无盐派生）"))
    scope = manifest.get("key_scope") or "global"
    if scope == "pack":
        print("密钥范围     : 本包专用密钥（包内 _restore_kit/desens.key，"
              "本工具会自动读取）")
    else:
        print("密钥范围     : 平台主密钥（不在包内，需 DESENS_HMAC_KEY / --key-file）")
    if manifest.get("key_fingerprint"):
        print("密钥指纹     : %s（核对是否用对了密钥）"
              % manifest.get("key_fingerprint"))
    for p in problems:
        print("  [!] %s" % p)
    for entry in manifest.get("files") or []:
        print("\n%s" % entry.get("path"))
        print("  行数 %s / sha256 %s" % (entry.get("rows"),
                                        (entry.get("plain_sha256") or "")[:16]))
        for col in entry.get("columns") or []:
            mode = col.get("mode")
            dec = col.get("decimals")
            if mode == "noise":
                extra = "decimals=%s  噪声上限=±%s  ≈σ=%s" % (
                    dec, _human(col.get("r_int"), dec),
                    _human(col.get("sigma_int"), dec))
            elif mode == "shift":
                extra = "decimals=%s  整列平移=%s %s" % (
                    dec, _human(col.get("shift_int"), dec), col.get("unit") or "")
            else:
                extra = "原样保留"
            print("    - %-22s %-5s %s" % (col.get("name"), mode, extra))


def _resolve_master_key_path():
    """定位平台主密钥文件（master.key），不生成、不修改"""
    env = os.getenv("MASTER_KEY_PATH")
    if env:
        return env
    for p in (os.path.join(_APP_ROOT, "keys", "master.key"),
              os.path.join(_APP_ROOT, "master.key")):
        if os.path.exists(p):
            return p
    return os.path.join(_APP_ROOT, "keys", "master.key")


def _make_dmec_decryptor(logger=None):
    """构造 DMEC 解密钩子 ``callable(bytes) -> bytes|None``

    加密导出包里的信号文件是平台 DMEC 信封加密的，明文本身**就是脱敏后的 CSV**。
    解密需要平台主密钥（master.key）。

    刻意**不用** ``create_app()``：那会连带启动扫描调度器、跑 schema 升级与启动清扫，
    对一个离线还原命令属于不可接受的副作用。这里只建一个裸 app context，
    仅供 ``crypto`` 读取 MASTER_KEY_PATH 配置。密钥文件缺失时直接判失败，
    绝不触发 ``crypto`` 的"不存在就新建主密钥"分支（会写出无效密钥）。
    """
    state = {"ready": False, "fn": None, "err": None}

    def _init():
        mk_path = _resolve_master_key_path()
        if not os.path.exists(mk_path):
            raise RuntimeError("未找到主密钥文件 %s（--no-dmec 可跳过 .dmec 条目）"
                               % mk_path)
        from flask import Flask
        from app.utils.crypto import decrypt_bytes
        app = Flask("signal-restore-offline")
        app.config["BASE_DIR"] = _APP_ROOT
        app.config["MASTER_KEY_PATH"] = mk_path
        app.app_context().push()
        state["fn"] = decrypt_bytes
        state["ready"] = True
        if logger:
            logger.info("DMEC 解密已就绪，主密钥：%s", mk_path)

    def _hook(blob):
        if state["err"] is not None:
            return None
        if not state["ready"]:
            try:
                _init()
            except Exception as e:  # noqa: BLE001
                state["err"] = str(e)
                if logger:
                    logger.warning("DMEC 解密环境初始化失败：%s", e)
                return None
        try:
            return state["fn"](blob)
        except Exception as e:  # noqa: BLE001 - 主密钥不匹配/文件损坏
            state["err"] = str(e)
            if logger:
                logger.warning("DMEC 解密失败：%s", e)
            return None

    return _hook


def _restore_from_dir(root, manifest, out_dir, pseudo_override, verify,
                      logger=None, decrypt_hook=None):
    report = {"restored": [], "skipped": []}
    for entry in manifest.get("files") or []:
        path = _find_entry_file(root, entry)
        if not path:
            report["skipped"].append({"path": entry.get("path"),
                                      "reason": "目录内未找到该文件"})
            continue
        arcname = entry.get("arcname") or entry.get("path")
        out_name = str(arcname)
        if str(path).endswith(".dmec"):
            if decrypt_hook is None:
                report["skipped"].append({
                    "path": entry.get("path"),
                    "reason": "该文件仍是加密态（.dmec），请先在平台内解密/导入得到"
                              "脱敏 CSV"})
                continue
            with open(path, "rb") as f:
                blob = f.read()
            plain = decrypt_hook(blob)
            if not plain:
                report["skipped"].append({
                    "path": entry.get("path"),
                    "reason": "DMEC 解密不可用（主密钥缺失或非导出机密钥）"})
                continue
            if out_name.endswith(".dmec"):
                out_name = out_name[:-5]
            tmp_dir = os.path.join(out_dir, "_dmec_tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            src = os.path.join(tmp_dir, os.path.basename(out_name))
            with open(src, "wb") as f:
                f.write(plain)
        else:
            src = path
        target = os.path.join(out_dir, out_name.replace("/", os.sep))
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        ok, err, st = restore_signal_file(
            src, target, entry, _pseudo_of(entry, pseudo_override),
            verify=verify, logger=logger)
        if ok:
            report["restored"].append({"path": out_name, "rows": st.get("rows"),
                                       "bytes_identical": st.get("bytes_identical"),
                                       "sha256": st.get("plain_sha256")})
        else:
            report["skipped"].append({"path": out_name, "reason": err})
    return report


def _apply_master_key_file(path):
    """用指定的主密钥文件覆盖平台主密钥（写临时文件 + 设 MASTER_KEY_PATH）

    「随包附带还原包」的加密包，其包内密文是用**本包专用主密钥**封装的，
    平台主密钥解不开 —— 此时把 --master-key-file 指向包内
    ``_restore_kit/master.key`` 即可。包内密钥是 hex 文本，而 crypto 要求
    32 字节二进制，故统一归一化后落到临时文件。
    """
    import tempfile
    with open(path, "rb") as f:
        raw = f.read()
    key = raw if len(raw) == 32 else None
    if key is None:
        try:
            cand = bytes.fromhex(raw.decode("utf-8", "replace").strip())
            key = cand if len(cand) == 32 else None
        except ValueError:
            key = None
    if key is None:
        raise SystemExit("主密钥文件无效：%s（需 32 字节二进制或 64 位十六进制）" % path)
    fd, tmp_path = tempfile.mkstemp(prefix="dm-master-", suffix=".key")
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    os.chmod(tmp_path, 0o600)
    os.environ["MASTER_KEY_PATH"] = tmp_path
    print("主密钥   ：%s" % path)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="EEG/ECG 脱敏导出包无损还原（signal-desens-v2）")
    ap.add_argument("input", help="脱敏导出包 .zip / 含 manifest 的目录 / 单个脱敏 CSV")
    ap.add_argument("-o", "--out-dir", default="restored", help="还原输出目录")
    ap.add_argument("--manifest", help="manifest 路径（单个 CSV 输入时必需）")
    ap.add_argument("--key-file", help="脱敏密钥文件（hex，32 字节）")
    ap.add_argument("--pseudo-id", help="覆盖 pseudo_id（默认从 manifest 的 path 推断）")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过 sha256 校验（不建议；无法证明还原正确）")
    ap.add_argument("--list", action="store_true", help="只列出包内文件与列参数，不还原")
    ap.add_argument("--no-dmec", action="store_true",
                    help="不解密包内 .dmec 条目（默认会用平台主密钥自动解密）")
    ap.add_argument("--master-key-file",
                    help="主密钥文件（32 字节二进制或 64 位 hex），覆盖平台主密钥。"
                         "解密「随包附带还原包」导出的加密包时，指向包内 "
                         "_restore_kit/master.key 即可")
    args = ap.parse_args(argv)

    if args.master_key_file:
        _apply_master_key_file(args.master_key_file)

    try:
        manifest = load_manifest(args.manifest or args.input)
    except FileNotFoundError as e:
        print("[错误] 找不到还原清单：%s\n"
              "       请确认传入的是本平台导出的脱敏包（zip）或解压后的目录，"
              "且其根目录含 %s。" % (e, MANIFEST_NAME), file=sys.stderr)
        return 4
    except Exception as e:  # noqa: BLE001 - 清单损坏/不是 JSON
        print("[错误] 无法解析还原清单：%s" % e, file=sys.stderr)
        return 4

    if args.list:
        _print_listing(manifest)
        return 0

    # 密钥优先级：**包内自带的「本包专用密钥」排在最先** —— 随包导出的包其噪声就是用那把
    # 一次性密钥派的，平台主密钥对它必然是错的（会 sha256 不符）。包内没有密钥时，
    # 才回落到 --key-file / DESENS_HMAC_KEY / 平台密钥文件。
    from app.utils.restore_kit import (KIT_DIR, KIT_KEY_NAME, read_key_text,
                                       pack_key_from_zip)
    key = pack_key_from_zip(args.input)
    src = "包内自带 %s/%s（本包专用密钥）" % (KIT_DIR, KIT_KEY_NAME)
    if key is None and os.path.isdir(args.input):
        kp = os.path.join(args.input, KIT_DIR, KIT_KEY_NAME)
        if os.path.isfile(kp):
            with open(kp, encoding="utf-8", errors="replace") as f:
                key = read_key_text(f.read())
    if key is None:
        key, src = _resolve_key(args.key_file)
    if key is None:
        print("[错误] 未找到脱敏密钥（%s）。\n"
              "       还原必须使用与导出时相同的密钥：平台主密钥导出的包用 "
              "DESENS_HMAC_KEY，\n"
              "       勾选「随包附带还原包」导出的包则用它自带的 "
              "%s/%s（本工具会自动读取）；\n"
              "       也可用 --key-file 显式指定。" % (src, KIT_DIR, KIT_KEY_NAME),
              file=sys.stderr)
        return 3
    set_hmac_key(key)

    if args.no_verify:
        print("[警告] 已跳过 sha256 校验：无法证明还原结果与原文完全一致", file=sys.stderr)

    if os.path.isdir(args.input):
        report = _restore_from_dir(args.input, manifest, args.out_dir,
                                   args.pseudo_id, not args.no_verify,
                                   decrypt_hook=None if args.no_dmec
                                   else _make_dmec_decryptor())
    elif zipfile.is_zipfile(args.input):
        from app.utils.signal_desensitize import restore_zip
        report = restore_zip(args.input, args.out_dir,
                             subject_key=args.pseudo_id,
                             decrypt_hook=None if args.no_dmec
                             else _make_dmec_decryptor())
    else:
        entry = None
        name = os.path.basename(args.input)
        for e in manifest.get("files") or []:
            if os.path.basename(e.get("path") or "") == name or \
                    os.path.basename(e.get("arcname") or "") == name:
                entry = e
                break
        if entry is None:
            print("[错误] manifest 中找不到与 %s 匹配的条目" % name, file=sys.stderr)
            return 4
        os.makedirs(args.out_dir, exist_ok=True)
        target = os.path.join(args.out_dir, name)
        ok, err, st = restore_signal_file(
            args.input, target, entry, _pseudo_of(entry, args.pseudo_id),
            verify=not args.no_verify)
        report = {"restored": ([{"path": name, "rows": st.get("rows"),
                                 "bytes_identical": st.get("bytes_identical")}]
                               if ok else []),
                  "skipped": ([] if ok else [{"path": name, "reason": err}])}

    report["key_source"] = src
    report["out_dir"] = os.path.abspath(args.out_dir)
    report["algorithm_version"] = VERSION_TAG
    os.makedirs(args.out_dir, exist_ok=True)
    report_path = os.path.join(args.out_dir, "restore_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    for p in report.get("problems") or []:
        print("[!] %s" % p, file=sys.stderr)
    ok_n = len(report["restored"])
    bad = [r for r in report["restored"] if r.get("bytes_identical") is False]
    print("密钥来源 : %s" % src)
    print("还原成功 : %d 个文件" % ok_n)
    print("跳过     : %d 个" % len(report["skipped"]))
    for s in report["skipped"]:
        print("   - %s：%s" % (s.get("path"), s.get("reason")))
    if bad:
        print("\n[错误] 以下文件 sha256 与 manifest 不符 —— 还原结果**不是**原文：",
              file=sys.stderr)
        for b in bad:
            print("   - %s" % b.get("path"), file=sys.stderr)
        print("       可能原因：密钥不同 / 算法版本不同 / manifest 与文件不配对。",
              file=sys.stderr)
    elif ok_n:
        print("\n[完成] 全部文件已逐字节还原（sha256 校验通过）。")
        print("       ** 输出目录内含未脱敏原始数据，请按原始数据保密要求管理 **")
    print("报告      : %s" % report_path)
    if bad:
        return 1
    return 0 if report["skipped"] == [] else 2


if __name__ == "__main__":
    sys.exit(main())
