# -*- coding: utf-8 -*-
"""上传文件 magic bytes 校验

仅校验有明确文件签名的格式（mp4/wav/pdf）；无签名格式（csv/json/edf/txt）
跳过校验，避免误杀。
"""


# 格式 -> (签名校验函数, 格式描述)
# 一个格式可能有多种签名（如 mp4 有多种 ftyp 子类型）
_SIGNATURES = {
    # mp4: ....ftyp 起始（前 4 字节任意，后 8 字节是 "ftypXXXX"）
    "mp4": (lambda b: len(b) >= 12 and b[4:8] == b"ftyp", "MP4"),
    # wav: RIFF....WAVE
    "wav": (lambda b: len(b) >= 12 and b[0:4] == b"RIFF" and b[8:12] == b"WAVE", "WAV"),
    # pdf: %PDF-
    "pdf": (lambda b: len(b) >= 5 and b[0:5] == b"%PDF-", "PDF"),
}


def check_signature(file_storage, ext):
    """校验 FileStorage 前若干字节是否符合 ext 期望签名

    Args:
        file_storage: werkzeug FileStorage（会被 seek 回 0）
        ext: 文件扩展名（小写，无点）
    Returns:
        (ok, error_message)  ok=True 表示通过；未配置签名的格式直接返回 (True, None)
    """
    ext = (ext or "").lower().lstrip(".")
    checker = _SIGNATURES.get(ext)
    if not checker:
        # 无签名定义的格式跳过校验（csv/json/edf/txt 等）
        return True, None
    sig_check, label = checker
    # 读取前 32 字节足够覆盖上述所有签名
    try:
        file_storage.stream.seek(0)
        head = file_storage.stream.read(32)
        file_storage.stream.seek(0)
    except Exception:
        # 读取失败时不阻塞上传（扩展名校验已通过）
        return True, None
    if not sig_check(head):
        return False, f"文件内容与扩展名 .{ext} 不匹配（非合法 {label} 文件）"
    return True, None