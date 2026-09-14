# -*- coding: utf-8 -*-
"""同源身份（source identity）判定工具

背景（为什么要归一化）：
同一份磁盘文件在不同导入入口下，落到 `metadata_json.original_filename` 的
字符串形态并不一致，已观测到的三种差异：

1. 目录前缀：浏览器目录扫描（webkitRelativePath / 拖拽）会带相对路径
   （`17887542610890001/ecg_20260715171022.csv.enc`），而手动选择单个
   受试者文件夹或后端 scanner 只给纯文件名（`ecg_20260715171022.csv.enc`）
2. `.enc` 后缀：外部加密文件落库时保留 `.enc`（upload_asset 用用户给的
   original_name），而命名规范渲染用的是剥离 `.enc` 后的名字
3. 大小写与分隔符：Windows 上传路径可能用 `\\`

幂等键若直接使用 `original_filename` 的**字面值**，上述任一差异都会导致
"同一份数据不认为是同一份"，进而重复入库（资产列表里同一份心电两条）。

本模块提供唯一的归一化口径：
- `normalize_original_filename`：用于**比对**（去目录前缀 + 去 .enc + 小写）
- `stored_original_filename`：用于**写库**（仅去目录前缀，保留 .enc，
  与浏览器 `File.name` 对齐，保证 ingest-digest 对账能直接匹配）
- `source_group_key`：同源分组键（受试者 + 模态 + 归一化名 + video_type）
"""


def normalize_original_filename(name):
    """原始文件名 → 归一化同源名（用于同源比对）

    - 反斜杠统一为正斜杠后取最后一段（去目录前缀）
    - 剥离 `.enc` 外部加密后缀
    - 统一小写

    :returns: 归一化后的名字；输入为空时返回 None
    """
    if not name:
        return None
    text = str(name).strip().replace("\\", "/")
    text = text.rsplit("/", 1)[-1]
    if text.lower().endswith(".enc"):
        text = text[:-4]
    text = text.strip().lower()
    return text or None


def stored_original_filename(name):
    """原始文件名 → 写库形态（仅去目录前缀，保留 .enc 后缀）

    与浏览器 `File.name`（纯文件名、含 .enc）保持一致，使前端
    `reconcileUploadedMap` 用 `fn === fname` 对账时能直接命中。
    """
    if not name:
        return name
    text = str(name).strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1]


def source_group_key(subject_id, data_type, original_filename, video_type=None):
    """同源分组键：受试者 + 模态 + 归一化原始名 + 视频子类型

    data_type 兼容枚举与字符串（取 value）。
    """
    dt = getattr(data_type, "value", data_type)
    vt = video_type or None
    return (subject_id, dt, normalize_original_filename(original_filename), vt)


def is_same_source(meta_a, meta_b):
    """两个 metadata dict 是否指向同一物理源文件（归一化后同名 + 同大小）

    大小任一缺失（老数据）时仅按归一化名判定，与前端对账的保守策略一致。
    """
    a, b = meta_a or {}, meta_b or {}
    if normalize_original_filename(a.get("original_filename")) != \
            normalize_original_filename(b.get("original_filename")):
        return False
    sa, sb = a.get("original_size"), b.get("original_size")
    if sa is None or sb is None:
        return True
    return sa == sb
