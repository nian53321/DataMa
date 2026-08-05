# -*- coding: utf-8 -*-
"""视频转码工具：将浏览器不支持的格式（mkv/avi/flv 等）转为 H.264 MP4
使用 imageio-ffmpeg 自带的 ffmpeg 可执行文件，无需系统安装。
"""
import os
import subprocess

_FFMPEG_EXE = None


def get_ffmpeg():
    """获取 ffmpeg 可执行文件路径"""
    global _FFMPEG_EXE
    if _FFMPEG_EXE is None:
        try:
            import imageio_ffmpeg
            _FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return _FFMPEG_EXE


def has_ffmpeg():
    return get_ffmpeg() is not None


# 浏览器原生可播放的视频格式
NATIVE_VIDEO = {"mp4", "webm", "ogg", "ogv", "mov", "m4v"}

# 浏览器原生可播放的音频格式
NATIVE_AUDIO = {"mp3", "wav", "m4a", "aac", "ogg", "oga", "flac", "weba"}


def is_native_video(file_format):
    return (file_format or "").lower() in NATIVE_VIDEO


def is_native_audio(file_format):
    return (file_format or "").lower() in NATIVE_AUDIO


def transcode_to_mp4(src_path, dst_path):
    """将源视频转码为 H.264 + AAC 的 MP4（浏览器可直接播放）
    返回 (成功?, 错误信息)
    """
    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        return False, "服务器未安装 ffmpeg（imageio-ffmpeg）"
    if not os.path.exists(src_path):
        return False, "源文件不存在"
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    # -y 覆盖；-c:v libx264 H.264；-preset fast 速度优先；-crf 23 质量；-c:a aac 音频；-movflags +faststart 支持流式播放
    cmd = [
        ffmpeg, "-y", "-i", src_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        dst_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=600, check=True)
        return True, None
    except subprocess.CalledProcessError as e:
        return False, e.stderr.decode("utf-8", errors="ignore")[:500]
    except subprocess.TimeoutExpired:
        return False, "转码超时（文件过大）"
