# -*- coding: utf-8 -*-
"""「随包附带还原包」的装配模块

勾选导出对话框里的「随包附带还原包（含密钥）」后，导出 zip 里会多出一个目录：

::

    导出包.zip
    ├── <pseudo_id>/<layer>/<data_type>/*.csv|*.wav    脱敏后的数据（加密导出时为 .dmec）
    ├── _desens_manifest.json                          可还原文件的还原参数（不含密钥）
    └── _restore_kit/
        ├── restore_signal_desens.py                 离线还原脚本（纯标准库）
        ├── desens.key                               本包专用脱敏密钥（64 位 hex）
        ├── master.key                               本包专用主密钥（仅加密导出时有）
        └── README-还原说明.txt                       使用说明

两把随包密钥的分工
------------------
* ``desens.key`` —— 重建脑电/心电/音频的**确定性噪声**（去脱敏）。明文导出、加密导出都有。
* ``master.key`` —— 解开包内 ``.dmec`` 的 DMEC 信封（**解密**），只在"加密导出"时才写。
  同样不是平台主密钥：导出时现生成一把**本包专用主密钥**，或用 ``encrypt_bytes_with_key``
  直接加密脱敏后的明文，或用 ``rewrap_dmec_file`` 把原文件的 DEK 重包裹到这把密钥上
  （内容密文不变，因此大文件无需整体解密再加密）。

这样接收方"解压 → 运行脚本"就能一步完成 **解密 + 去脱敏**，而平台主密钥始终不出包：
包密钥泄露只影响这一个包，解不开数据湖里的任何其它文件。

**为什么要用「本包专用密钥」而不是平台主密钥**

``desensitize.desens.key`` 是平台级密钥，同时用于 userInfo 哈希脱敏、音频声纹脱敏
噪声与脑电/心电噪声；而且噪声种子里的 ``pseudo_id`` / 包内路径 / 列名本来就写在
manifest 里 —— 只要拿到这把主密钥，就能重建**任何一次历史导出**的噪声、还原任意
脱敏包，还能拿它反查 userInfo 哈希。把它放进每一个外发包里等于把平台整体脱敏
一次性交出去。

因此勾选还原包时，本次导出改用 ``os.urandom(32)`` 现生成的**本包专用密钥**脱敏
（噪声算法逐位不变，只是换了密钥），密钥随包给出、manifest 记 ``key_scope: "pack"``。
接收方体验完全一样（脚本自动读包内密钥），但单包泄露不外溢。

**注意**：随包的这把密钥**只能**还原脑电/心电的数值变换与音频的样本加噪，
还原不了 userInfo 的哈希脱敏（那部分仍由平台主密钥派生）。
"""
import os

# 包内固定路径
KIT_DIR = "_restore_kit"
KIT_KEY_NAME = "desens.key"
KIT_MASTER_KEY_NAME = "master.key"
KIT_SCRIPT_NAME = "restore_signal_desens.py"
KIT_README_NAME = "README-还原说明.txt"

# 还原脚本模板（随镜像分发；导出时读入并原样写进 zip）
TEMPLATE_RELATIVE = os.path.join("tools", "restore_kit")


def _candidate_dirs():
    """还原脚本模板的候选目录（按优先级）"""
    here = os.path.dirname(os.path.abspath(__file__))        # <base>/app/utils
    base = os.path.dirname(os.path.dirname(here))            # <base>
    dirs = [os.path.join(base, TEMPLATE_RELATIVE)]
    try:
        from flask import current_app
        cfg_base = current_app.config.get("BASE_DIR")
        if cfg_base:
            dirs.append(os.path.join(cfg_base, TEMPLATE_RELATIVE))
    except Exception:
        pass
    return dirs


def template_script_path():
    """定位随包还原脚本模板；找不到返回 None"""
    for d in _candidate_dirs():
        p = os.path.join(d, KIT_SCRIPT_NAME)
        if os.path.isfile(p):
            return p
    return None


def load_script_bytes():
    """读入还原脚本源码（bytes）

    :raises RuntimeError: 模板缺失 —— 宁可让这次导出失败，也不能让用户拿到一个
        号称"可还原"却没有还原脚本的包
    """
    path = template_script_path()
    if not path:
        raise RuntimeError(
            "还原包脚本模板缺失（%s/%s）：请确认镜像内包含 tools/restore_kit/，"
            "或取消「随包附带还原包」后重试" % (TEMPLATE_RELATIVE, KIT_SCRIPT_NAME))
    with open(path, "rb") as f:
        return f.read()


def read_key_text(text):
    """密钥文本 → 32 字节：优先按 64 位十六进制解析，否则按原始 32 字节"""
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


def pack_key_from_zip(zip_path):
    """从导出包内取「本包专用密钥」；没有（未勾选还原包）返回 None"""
    import zipfile
    if not (isinstance(zip_path, str) and os.path.isfile(zip_path)
            and zipfile.is_zipfile(zip_path)):
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            name = "%s/%s" % (KIT_DIR, KIT_KEY_NAME)
            if name not in zf.namelist():
                return None
            return read_key_text(zf.read(name).decode("utf-8", errors="replace"))
    except Exception:
        return None


def _readme_text(entry_count, encrypted, fp, master_fp=None, encrypted_count=0):
    lines = [
        "导出包 —— 解密 / 还原说明",
        "=" * 40,
        "",
    ]
    if entry_count:
        lines += [
            "本包内有 %d 个可还原文件做过「值级脱敏」—— 脑电（EEG）/ 心电（ECG）CSV"
            % entry_count,
            "与音频 WAV。CSV 的列全部保留，只是信号数值加了确定性噪声、绝对时间列整列",
            "平移过；音频只改样本区（RIFF 头块等一字不改），加的是定点域确定性噪声。",
            "这些都是可逆变换，用本脚本可以逐字节还原成原始文件。",
            "",
        ]
    if encrypted_count:
        lines += [
            "本包共有 %d 个文件是加密态（.dmec）。脚本会把它们**全部解密**，"
            % encrypted_count,
            "解密深度分两档 —— 因为两类文件的脱敏性质本来就不同：",
            "",
            "  · 脑电 / 心电 / 音频 → 解密 + 去脱敏 = **原始文件**（逐字节一致）",
            "  · 其余所有文件       → 只解密 = 脱敏后的明文",
            "    （视频人脸仍是马赛克块、userInfo 身份字段仍掩码 ——",
            "     这些脱敏不可逆，包里没有任何可回推原始内容的参数）",
            "",
            "请不要把「已解密」误读成「已还原成原始数据」：脚本的报告里会把",
            "「已还原」与「已解密」分开列出，一眼能看出哪些是原始文件。",
            "",
        ]
    lines += [
        "一、怎么运行",
        "-" * 40,
        "  1) 需要 Python 3.8 以上（只用到标准库，不需要联网、不需要装任何包）",
        "  2) 解压本包，然后**直接运行**随包脚本（不指定任何文件）：",
        "",
        "       python %s/%s" % (KIT_DIR, KIT_SCRIPT_NAME),
        "",
        "     Windows 下也可以直接双击这个 .py 文件。脚本会自动定位包根，",
        "     把包内所有加密文件解密、所有可还原文件（脑电/心电/音频）就地还原并",
        "     覆盖原文件（脚本放在 _restore_kit/ 里、或复制到包根运行都可以）。",
        "",
        "  3) 想输出到另一个目录、不动包内文件，加 -o：",
        "",
        "       python %s/%s -o 还原结果" % (KIT_DIR, KIT_SCRIPT_NAME),
        "",
        "     想先看看包里有什么、每列怎么脱敏的，加 --list：",
        "",
        "       python %s/%s --list" % (KIT_DIR, KIT_SCRIPT_NAME),
        "",
        "  4) 脚本会用包内自带的密钥自动完成：",
        "       %s/%s   —— 去脱敏（重建并减掉噪声）" % (KIT_DIR, KIT_KEY_NAME),
    ]
    if master_fp:
        lines += [
            "       %s/%s   —— 解密包内的 .dmec 文件" % (KIT_DIR, KIT_MASTER_KEY_NAME),
        ]
    lines += [
        "     可还原文件的还原结果会与原始文件的 sha256 逐一比对，相符即表示无损；",
        "     只有比对通过才覆盖原文件：密钥不对时原文件保持原样，不会被写坏；",
        "     已经还原/解密过的文件会被识别并跳过，重复运行也安全。",
        "",
    ]
    if encrypted and master_fp:
        lines += [
            "  5) 本包是**加密导出**：包内数据文件带 .dmec 后缀（平台 DMEC 信封加密，",
            "     AES-256-GCM）。脚本会先用包内主密钥解密，再对脑电/心电/音频做去脱敏",
            "     还原，最终写出**去掉 .dmec 后缀**的文件（原 .dmec 保留不动，可自行删除）。",
            "     解密能力是脚本自带的（纯标准库实现），无需安装任何密码学包；",
            "     若环境里恰好装了 cryptography，会自动改用它，速度更快。",
            "",
        ]
    elif encrypted_count:
        lines += [
            "  ⚠ 本包是「加密导出」，包内数据文件是 .dmec 加密态，且本次没有随包",
            "    给出主密钥 —— 请用 --master-key-file <master.key> 指定平台主密钥，",
            "    或重新导出并勾选「随包附带还原包」。",
            "",
        ]
    lines += [
        "二、本包信息",
        "-" * 40,
        "  可还原文件数      : %d  （脑电 / 心电 CSV + 音频 WAV）" % entry_count,
        "  加密文件数        : %d" % encrypted_count,
        "  本包脱敏密钥指纹  : %s" % (fp or "-"),
        "  本包主密钥指纹    : %s" % (master_fp or "-（明文导出，无需解密）"),
        "  密钥范围          : 本包专用密钥（只对本次导出有效）",
        "  导出时是否加密    : %s" % ("是（包内为 .dmec 加密态）" if encrypted
                                     else "否（包内是明文）"),
        "",
        "三、这把密钥能做什么、不能做什么",
        "-" * 40,
    ]
    if entry_count:
        lines += ["  能：还原本包内脑电/心电的数值（加噪、时间平移）与音频的样本（减噪）"]
    if master_fp:
        lines += ["  能：解密本包内**所有** .dmec 文件（视频/userInfo/眼动都在内）"]
    lines += [
        "  不能：还原视频人脸、userInfo 身份字段 —— 这两类脱敏不可逆，",
        "        解密只能拿到脱敏后的明文（人脸仍是马赛克块），拿不到原始内容。",
        "  不能：还原 userInfo 里的哈希脱敏字段 —— 那部分用的是平台主密钥派生，",
        "        与本包密钥无关；也就是说拿到本包也反查不出受试者身份。",
        "  不能：解开平台数据湖里的任何其它文件 —— 本包主密钥是导出时现生成的，",
        "        与平台主密钥无关，单包泄露不外溢。",
        "  提醒：本包密钥只对本包有效。请像对待受试者数据一样妥善保管，",
        "        不要把 _restore_kit/ 目录单独转发给第三方。",
        "",
        "四、还原参数在哪",
        "-" * 40,
        "  都在包根的 _desens_manifest.json 里（不含密钥）。按条目 modality 分派算法：",
        "",
        "  · 脑电 / 心电（modality=eeg/ecg）：每列的小数位、噪声幅度 R、时间平移量",
        "      seed = HMAC-SHA256(密钥, \"signal-desens-v2|模态|受试者|包内路径|列名\")",
        "      噪声流 = SHAKE256(seed) 的连续 8 字节大端整数",
        "      噪声值 = (整数 % (2R+1)) - R      （逐行相减即还原）",
        "",
        "  · 音频（modality=audio，version=audio-desens-v2）：定点 PCM 域加噪",
        "      每声道 seed = HMAC-SHA256(密钥, \"audio-desens-v2|audio|受试者|包内路径|chN|k<块号>\")",
        "      噪声流 = SHAKE256(seed) 的连续 2 字节**大端**整数（每块 16384 帧）",
        "      噪声值 = (整数 % (2R_c+1)) - R_c，R_c 取清单 r_int_by_ch[声道]",
        "      样本还原：x = (y - n) mod 2^bits      （bits 取清单 bits，回绕而非裁剪）",
        "      只看清单的 data_offset/data_size 就够定位样本区；样本区之外的字节不动。",
        "",
        "  DMEC 信封：DMEC|版本|DEK nonce|加密 DEK|内容 nonce|AES-256-GCM 密文，",
        "  用主密钥解开 DEK、再用 DEK 解开内容。",
        "  因此任何语言都能按此重新实现，不依赖本平台。",
        "",
    ]
    return "\n".join(lines)


def build_kit_files(pack_key, entry_count, encrypted=False, fingerprint="",
                    pack_master_key=None, master_fingerprint="",
                    encrypted_count=0):
    """装配还原包内容

    :param pack_key: 本包专用**脱敏**密钥（去噪用）
    :param pack_master_key: 本包专用**主**密钥（解密 .dmec 用）；仅加密导出时给出
    :param encrypted_count: 包内加密文件（.dmec）总数，含信号文件；仅用于说明与核对
    :return: ``[(zip 内路径, bytes), ...]``；键为 ``<KIT_DIR>/...``
    :raises RuntimeError: 密钥非法或脚本模板缺失
    """
    if not pack_key or len(pack_key) != 32:
        raise RuntimeError("还原包脱敏密钥非法（需 32 字节）")
    files = [
        ("%s/%s" % (KIT_DIR, KIT_SCRIPT_NAME), load_script_bytes()),
        ("%s/%s" % (KIT_DIR, KIT_KEY_NAME), pack_key.hex().encode("ascii")),
        ("%s/%s" % (KIT_DIR, KIT_README_NAME),
         _readme_text(entry_count, encrypted, fingerprint,
                      master_fingerprint if pack_master_key else None,
                      encrypted_count=encrypted_count).encode("utf-8")),
    ]
    if pack_master_key:
        if len(pack_master_key) != 32:
            raise RuntimeError("还原包主密钥非法（需 32 字节）")
        files.insert(2, ("%s/%s" % (KIT_DIR, KIT_MASTER_KEY_NAME),
                         pack_master_key.hex().encode("ascii")))
    return files
