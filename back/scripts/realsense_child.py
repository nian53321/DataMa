# -*- coding: utf-8 -*-
"""pyrealsense2 子进程执行器（Intel RealSense D455F 深度相机）

背景：pyrealsense2 底层 C 库（librealsense）在 USB 不可达或初始化失败时可能
崩溃，C 扩展的崩溃无法被 Python try/except 捕获，会杀死 Flask worker。
所有 pyrealsense2 调用都通过 subprocess 在本脚本（独立进程）中执行，即使
C 库崩溃也只影响本子进程。

子命令：
  probe                短命探测设备，输出 "OK <count> <serial>" 与 "DETAIL <json>"
  session <fps>        常驻会话：预览+录制共用同一 pipeline，父进程全程持有

session 架构（消除预览↔录制切换的进程重启与设备抢占竞态）：
- 启动即以原生最高规格（RGB 1280x800 bgr8 + 深度 1280x720 z16）打开 pipeline，
  无试错降级（实测 D455F 可直启，~0.1s）
- stdout 持续输出彩色 MJPEG（multipart/x-mixed-replace）；无消费者时写线程
  阻塞+丢帧（_put_latest 丢最旧），不回压采集循环
- stdin 每行一条 JSON 命令：
    {"cmd":"start_rec","path":"<目录>"}   开始录制
    {"cmd":"pause_rec"}                   暂停录制（帧不入编码器，预览继续）
    {"cmd":"resume_rec"}                  恢复录制
    {"cmd":"stop_rec"}                    停止录制（收尾后台线程完成）
    {"cmd":"quit"}                        退出会话
- 结果事件经独立 fd（环境变量 RS_SESSION_FD，父进程 os.pipe+pass_fds 提供）
  每行一条 JSON（与 stdout 帧流不混流，父进程免解析）：
    {"evt":"ready",...}            pipeline 就绪（fps/serial/name/firmware）
    {"evt":"fatal","err":...}      会话启动失败
    {"evt":"rec_started",...}      录制就绪（编码器已启动，目录已创建）
    {"evt":"rec_start_err","err"}  录制启动失败
    {"evt":"rec_done",...}         录制收尾完成（dir/frames）
    {"evt":"rec_done_err","err"}   录制收尾失败
- stop_rec 后收尾（编码 flush/文件校验/元数据）在后台线程，采集循环不中断、
  预览持续输出；紧接 start_rec 立即开始新录制（不同输出目录互不冲突）

数据流：只采集 彩色 + 深度 两路（不启用红外流），深度对齐到彩色坐标系。
录制产物（输出目录，全部为最终格式）：
  color.mp4         彩色 H.264（libx264 CRF 23 / GOP 30，浏览器可播；参数见下方 COLOR_ENC_*）
  depth_raw.zst     原始深度序列（DZST v2：32mm 量化+zstd，解码后为毫米近似值）
  frames.jsonl      逐帧同步记录（MP4/ZST 帧序号 + RGB/深度硬件时间戳 + 硬件帧号）
  calibration.json  相机标定（depth_scale / RGB 内参 / 畸变 / 分辨率 / 序列号）
  meta.json         序列号 / 分辨率 / 帧数 / 编码信息
"""
import json
import os
import queue
import sys
import threading
import time
from datetime import datetime

BACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # back/
SCRIPTS_DIR = os.path.join(BACK_DIR, "scripts")
sys.path.insert(0, BACK_DIR)
sys.path.insert(0, SCRIPTS_DIR)

# 默认流配置：D455F 原生 RGB 1280x800（1MP 全局快门） + 深度 1280x720，30fps。
DEFAULT_FPS = 30
# 流配置：原生最高规格直接启动，不做分辨率/帧率试错降级（实测可直启）
STREAM_COLOR = (1280, 800)
STREAM_DEPTH = (1280, 720)

# 彩色视频编码参数（**调体积只改这里**；改完必须重建 backend 与 celery-worker 镜像才生效）
#
# 实测基线（1280x800@30，CRF 18 + GOP 15）：≈20 Mbps → 2min ≈ 300MB，
#   远超该分辨率 CRF18 的正常落点 5~8 Mbps。超额来自两处：
#     ① GOP=15（每 0.5s 一个 I 帧）在 CRF 模式下额外占 20~40%；
#     ② 深度流开启时 IR 散斑落在 RGB 传感器上形成高频噪声，H.264 对高频极敏感。
#
# 2026-09-15 调整为 CRF 23 + GOP 30（预期 ≈100~120MB / 2min），依据：
#   - CRF 23 与本项目其余编码路径对齐：video_desensitize.ENCODE_CRF / orbbec.py /
#     transcode.py / visualization.py 全部为 23，CRF 18 是孤立的高码率孤岛；
#   - CRF +5 按 x264 经验约降到 42%，GOP 放宽一倍再省 8~15%。
#
# GOP 取值下限受两处约束，**勿再放大**：
#   ① 视频脱敏 v6 段级并行用 `-f segment -c copy` **按关键帧**切分，GOP 越大切点吸附
#      误差越大、各段长度越不均（SEGMENT_TARGET_SECONDS=30s，GOP 30 时误差 ≤1s，可忽略）；
#   ② 浏览器 seek / 前端裁剪依赖关键帧密度（同 orbbec.py 预览转码的口径）。
COLOR_ENC_PRESET = "fast"
COLOR_ENC_CRF = 23
COLOR_ENC_GOP = 30

# 设备忙重试预算（秒）：上一会话进程退出后内核释放 USB 需要 1~2.5s，期间
# pipe.start 报 EBUSY，短间隔重试等到释放即可；预算耗尽仍忙则失败。
BUSY_RETRY_SECONDS = 12.0
BUSY_RETRY_INTERVAL = 0.5

# 保存原始 stdout 的 fd，供 C 库日志重定向后仍能输出结果/帧
_saved_stdout_fd = None


def _setup_c_log_redirect():
    """将 fd1 重定向到 fd2：librealsense 日志直接写 stdout(fd1)，需在导入 pyrealsense2 之前重定向。"""
    global _saved_stdout_fd
    _saved_stdout_fd = os.dup(1)
    os.dup2(2, 1)


def _safe_write(data):
    """绕过重定向后的 fd1，直接写原始 stdout（结果行 / MJPEG 帧）"""
    fd = _saved_stdout_fd if _saved_stdout_fd is not None else 1
    import select
    _r, _w, _x = select.select([], [fd], [], 5.0)
    if not _w:
        raise BrokenPipeError("parent stopped consuming stream")
    try:
        os.write(fd, data)
    except OSError:
        raise


def _safe_print(text):
    _safe_write((str(text) + "\n").encode("utf-8", "replace"))


def _import_rs():
    """导入 pyrealsense2，C 库加载失败时抛异常（由调用方处理）"""
    import pyrealsense2 as rs
    return rs


def _distortion_name(rs, model):
    """pyrealsense2 畸变模型枚举 -> 标准小写名（如 inverse_brown_conrady）"""
    try:
        for _n in dir(rs.distortion):
            if _n.isupper():
                if int(getattr(rs.distortion, _n)) == int(model):
                    return _n.lower()
    except Exception:
        pass
    return str(model)


def _query_devices():
    """枚举 RealSense 设备，返回 (count, serial)；只枚举不打开流"""
    rs = _import_rs()
    ctx = rs.context()
    devices = ctx.query_devices()
    count = len(devices)
    serial = ""
    if count > 0:
        try:
            serial = devices[0].get_info(rs.camera_info.serial_number) or ""
        except Exception:
            serial = ""
    return count, serial


def _is_no_device(err):
    """pipe.start 失败是否疑似无设备在位（区别于设备忙/配置失败）"""
    s = str(err).lower()
    return any(k in s for k in (
        "no device", "no devices", "device not found",
        "failed to set power state", "not connected"))


def _is_device_busy(err):
    """判断 pipe.start 失败是否属设备被占用（V4L2 EBUSY，errno=16）"""
    s = str(err).lower()
    return any(k in s for k in (
        "errno=16", "errno 16", "device or resource busy",
        "vidioc_s_fmt", "xioctl"))


def _start_pipeline(rs, fps):
    """启动 pipeline（彩色 + 深度，原生最高规格直接启动）

    不做分辨率/帧率试错降级：D455F 原生 1280x800+1280x720 可直启，
    降级链每次失败的 pipe.start 耗时数秒，纯浪费。

    设备忙重试：上一会话进程退出与内核释放 USB 期间 pipe.start 报 EBUSY，
    属"设备忙"而非配置问题，短间隔重试等到释放即可；预算耗尽仍忙则失败。

    返回 (pipeline, profile, color_w, color_h, depth_w, depth_h, actual_fps)；
    profile 用于读取实际设备信息（型号/固件/深度缩放）。启动失败时抛异常。
    """
    fps = int(fps)
    cw, ch = STREAM_COLOR
    dw, dh = STREAM_DEPTH
    last_err = None
    busy_until = 0.0  # 首次遇到 EBUSY/瞬时抖动时起算的共享重试预算
    while True:
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.color, cw, ch, rs.format.bgr8, fps)
        cfg.enable_stream(rs.stream.depth, dw, dh, rs.format.z16, fps)
        try:
            profile = pipe.start(cfg)
            return pipe, profile, cw, ch, dw, dh, fps
        except Exception as e:
            last_err = e
            try:
                pipe.stop()
            except Exception:
                pass
            if _is_no_device(e):
                # 疑似无设备：枚举确认（仅失败路径枚举）后立即失败。
                # 设备在位却报该错属瞬时抖动，继续重试
                try:
                    count, _serial = _query_devices()
                except Exception:
                    count = 0
                if count == 0:
                    raise RuntimeError("未检测到 RealSense 相机") from e
            elif not _is_device_busy(e):
                break  # 非"忙"类失败（配置/带宽）：重试无意义，直接报错
            if busy_until == 0.0:
                busy_until = time.monotonic() + BUSY_RETRY_SECONDS
            if time.monotonic() >= busy_until:
                break  # 忙预算耗尽：设备仍被占用
            time.sleep(BUSY_RETRY_INTERVAL)
    # 启动失败：枚举一次确认设备状态，给出准确错误
    try:
        count, _serial = _query_devices()
    except Exception:
        count = 0
    if count == 0:
        raise RuntimeError("未检测到 RealSense 相机")
    hint = ("（相机可能被其他进程占用，稍后重试或重启后端容器）"
            if _is_device_busy(last_err) else "")
    raise RuntimeError(f"启动 RealSense 相机失败（{last_err}）{hint}")


def _frame_array(frame):
    """取帧数据为 (h, w, c) uint8 ndarray

    pyrealsense2 的 frame.get_data() 在 numpy 可用时返回 ndarray，但在部分
    numpy/OpenCV 组合下（如容器内 opencv 5.x）会回退返回 bytes；统一在此转换，
    供预览（cv2 编码）与录制（ffmpeg 原始帧）共用。
    """
    import numpy as np
    data = frame.get_data()
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data)
        return np.frombuffer(data, dtype=np.uint8).reshape(
            frame.get_height(), frame.get_width(), -1)
    return np.ascontiguousarray(data)


def _put_latest(q, item):
    """入队最新项：队列满时丢最旧再入队（预览只关心最新画面，绝不阻塞调用方）"""
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def _preview_writer(q, w, h):
    """预览写线程：从队列取彩色帧编码 JPEG 写 stdout（multipart 边界）

    必须独立于采集循环：预览帧超过 stdout 管道缓冲（64KB），无消费者时写
    管道会阻塞——独立线程后管道背压只阻塞本线程，采集循环满帧率运行；
    队列满丢最旧帧，消费者恢复后立即跟上最新画面。

    写入采用非阻塞 fd + 分块 + 单帧超时：消费者断开后（浏览器刷新/URL 轮换）
    管道缓冲写满，阻塞写会让本线程永久挂死——此后任何新的流请求都等不到
    数据，预览画面永久消失。非阻塞分块写超时后丢弃整帧，保证写线程永不
    挂死：无消费者时静默丢帧，新消费者接入立即恢复出图。
    """
    import cv2
    import fcntl
    import numpy as np
    import select
    fd = _saved_stdout_fd if _saved_stdout_fd is not None else 1
    try:
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
    except (OSError, AttributeError):
        pass  # 非 Linux（本不该发生）：退化为原阻塞行为

    FRAME_WRITE_TIMEOUT = 2.0  # 单帧写入预算（秒）：消费者断开/极慢时丢帧

    def _write_frame(data, deadline):
        """非阻塞分块写：超时/EPIPE 返回 False（丢帧），写完返回 True"""
        view = memoryview(data)
        while view:
            try:
                n = os.write(fd, view)
                view = view[n:]
            except BlockingIOError:
                if time.time() > deadline:
                    return False
                select.select([], [fd], [], 0.2)
            except OSError:
                return False
        return True

    while True:
        item = q.get()
        if item is None:
            break
        try:
            frame = np.frombuffer(item, np.uint8).reshape(h, w, 3)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                continue
            deadline = time.time() + FRAME_WRITE_TIMEOUT
            if not _write_frame(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n", deadline):
                continue
            if not _write_frame(buf.tobytes(), deadline):
                continue
            _write_frame(b"\r\n", deadline)
        except (OSError, BrokenPipeError):
            continue


# ==================== probe ====================
def cmd_probe():
    """探测设备，输出 "OK <count> <serial>" 与 "DETAIL <json>";失败输出 "ERR <原因>"

    DETAIL 行携带设备型号/固件/产品线（如 name="Intel RealSense D455F"），
    供后端 status 接口展示与 D455F 兼容性确认。只枚举不打开流。
    """
    try:
        rs = _import_rs()
        ctx = rs.context()
        devices = ctx.query_devices()
        count = len(devices)
        serial = ""
        if count > 0:
            try:
                serial = devices[0].get_info(rs.camera_info.serial_number) or ""
            except Exception:
                serial = ""
        _safe_print(f"OK {count} {serial}")
        if count > 0:
            dev = devices[0]
            detail = {}
            for key, info in (
                ("name", rs.camera_info.name),
                ("firmware_version", rs.camera_info.firmware_version),
                ("product_line", rs.camera_info.product_line),
            ):
                try:
                    detail[key] = dev.get_info(info) or ""
                except Exception:
                    detail[key] = ""
            _safe_print("DETAIL " + json.dumps(detail, ensure_ascii=False))
        return 0
    except Exception as e:
        _safe_print(f"ERR {e}")
        return 1


# ==================== session ====================
def _spawn_ffmpeg(args, logf=None):
    """启动 ffmpeg 编码器子进程（stdin 收 rawvideo 帧；logf 非空时 stderr 追加到该文件）"""
    import subprocess
    return subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=logf if logf is not None else subprocess.DEVNULL,
    )


def _enc_writer(q, proc):
    """写线程：从队列取帧字节写入 ffmpeg stdin；None 哨兵后关闭 stdin 让 ffmpeg EOF 正常收尾"""
    try:
        while True:
            item = q.get()
            if item is None:
                break
            try:
                proc.stdin.write(item)
            except Exception:
                break
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass


def _load_depth_zst():
    """按文件路径加载共享编解码模块（绕过 app 包导入，避免子进程拉起 Flask 依赖）"""
    import importlib.util
    path = os.path.join(BACK_DIR, "app", "utils", "depth_zst.py")
    spec = importlib.util.spec_from_file_location("depth_zst", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 深度序列文件格式（DZST v2，见 app/utils/depth_zst.py）：
#   32mm 量化 + zstd（帧间差分在这类内容上实测基本失效，详见 depth_zst 模块注释）
#   体积约为逐帧 zstd（v1）的 1/4 ~ 1/3
def _zstd_writer(q, fobj, w, h):
    """写线程：从队列取 z16 深度帧字节，DZST v2 压缩追加写入；None 哨兵后回填帧数并关闭"""
    try:
        depth_zst = _load_depth_zst()
    except Exception as e:
        sys.stderr.write(f"depth_zst 模块加载失败，无法写入深度序列: {e}\n")
        sys.stderr.flush()
        fobj.close()
        return
    try:
        writer = depth_zst.DepthZstWriter(fobj, w, h)
        count = 0
        while True:
            item = q.get()
            if item is None:
                break
            writer.write_frame(item)
            count += 1
        writer.finish(count)
    except Exception as e:
        sys.stderr.write(f"深度序列写入失败: {e}\n")
        sys.stderr.flush()
    finally:
        fobj.close()


def _jsonl_writer(q, fobj):
    """写线程：从队列取帧记录字典逐行写 JSONL；None 哨兵后关闭文件"""
    try:
        while True:
            item = q.get()
            if item is None:
                break
            try:
                fobj.write(json.dumps(item, ensure_ascii=False) + "\n")
            except Exception:
                pass
    finally:
        try:
            fobj.flush()
            fobj.close()
        except Exception:
            pass


def cmd_session(fps):
    """常驻会话：预览+录制共用 pipeline（协议见模块 docstring）"""
    fd_env = os.environ.get("RS_SESSION_FD")
    proto = None
    if fd_env:
        try:
            proto = os.fdopen(int(fd_env), "wb", buffering=0)
        except OSError:
            proto = None

    def send_evt(d):
        if proto is None:
            return
        try:
            proto.write((json.dumps(d, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    try:
        rs = _import_rs()
        pipe, profile, cw, ch, dw, dh, actual_fps = _start_pipeline(rs, int(fps))
    except Exception as e:
        send_evt({"evt": "fatal", "err": str(e)})
        return 1

    # 从已启动的 pipeline 读取设备信息（与会话同一设备），随事件上报父进程
    device = profile.get_device()
    dev_info = {}
    for key, info in (("name", rs.camera_info.name),
                      ("firmware", rs.camera_info.firmware_version),
                      ("serial", rs.camera_info.serial_number)):
        try:
            dev_info[key] = device.get_info(info) or ""
        except Exception:
            dev_info[key] = ""
    try:
        dev_info["depth_units_mm"] = round(
            device.first_depth_sensor().get_option(rs.option.depth_units) * 1000.0, 3)
    except Exception:
        dev_info["depth_units_mm"] = 1.0

    # 实际生效的彩色编码参数：回写到 meta.json 与 rec_started 事件，事后可确认某批视频
    # 是用哪档参数落的 —— 避免"改了参数却仍在跑旧镜像"时无法取证。
    eff_enc = {"preset": COLOR_ENC_PRESET, "crf": COLOR_ENC_CRF,
               "gop": COLOR_ENC_GOP, "pix_fmt": "yuv420p"}

    # 深度对齐到彩色坐标系（对齐后深度逐像素对应彩色，分辨率也变为彩色分辨率）
    align = rs.align(rs.stream.color)

    quit_evt = threading.Event()

    # 预览写线程（无消费者时阻塞+丢帧，不回压采集循环）
    preview_q = queue.Queue(maxsize=2)
    preview_writer = threading.Thread(
        target=_preview_writer, args=(preview_q, cw, ch), daemon=True)
    preview_writer.start()

    # 录制状态：命令线程（stdin watcher）与采集循环共享，操作经 rec_lock 串行；
    # 帧计数器仅在 active=True 期间被采集循环读写（start 时在锁内置零后才置
    # active，stop 快照后立即置 False），无并发写冲突
    rec_lock = threading.Lock()
    rec = {"active": False}
    finalize_threads = []

    def handle_start_rec(path):
        out_dir = os.path.abspath(path or "")
        if not out_dir:
            send_evt({"evt": "rec_start_err", "err": "缺少录制目录"})
            return
        with rec_lock:
            if rec["active"]:
                send_evt({"evt": "rec_start_err", "err": "已有录制进行中"})
                return
        os.makedirs(out_dir, exist_ok=True)
        color_mp4 = os.path.join(out_dir, "color.mp4")
        depth_zst = os.path.join(out_dir, "depth_raw.zst")
        logf = None
        try:
            logf = open(os.path.join(out_dir, "ffmpeg.log"), "ab")
        except OSError:
            pass
        try:
            enc = _spawn_ffmpeg([
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{cw}x{ch}",
                "-r", str(actual_fps), "-i", "pipe:0",
                "-c:v", "libx264", "-preset", COLOR_ENC_PRESET, "-crf", str(COLOR_ENC_CRF),
                "-pix_fmt", "yuv420p", "-g", str(COLOR_ENC_GOP), "-keyint_min", str(COLOR_ENC_GOP),
                "-movflags", "+faststart", "-an", color_mp4], logf)
        except Exception as e:
            if logf is not None:
                try:
                    logf.close()
                except Exception:
                    pass
            send_evt({"evt": "rec_start_err", "err": f"启动编码器失败: {e}"})
            return

        color_q = queue.Queue(maxsize=30)
        color_writer = threading.Thread(
            target=_enc_writer, args=(color_q, enc), daemon=True)
        color_writer.start()
        depth_q = queue.Queue(maxsize=30)
        depth_fobj = open(depth_zst, "wb")
        # 对齐后深度分辨率 = 彩色分辨率
        depth_writer = threading.Thread(
            target=_zstd_writer, args=(depth_q, depth_fobj, cw, ch), daemon=True)
        depth_writer.start()
        frames_q = queue.Queue(maxsize=60)
        frames_fobj = open(os.path.join(out_dir, "frames.jsonl"), "w", encoding="utf-8")
        frames_writer = threading.Thread(
            target=_jsonl_writer, args=(frames_q, frames_fobj), daemon=True)
        frames_writer.start()

        # 相机标定信息（每次录制保存一次）
        color_intr = None
        try:
            color_intr = profile.get_stream(
                rs.stream.color).as_video_stream_profile().get_intrinsics()
        except Exception:
            pass
        calib = {
            "depth_scale": round(dev_info["depth_units_mm"] / 1000.0, 6),
            "rgb_intrinsics": None,
            "rgb_distortion": None,
            "rgb_resolution": f"{cw}x{ch}",
            "depth_resolution": f"{cw}x{ch}",
            "serial": dev_info["serial"],
            "depth_aligned_to_color": True,
            "align_note": "color/depth 已执行 align_to_color 对齐（深度分辨率与彩色一致）；时间戳为传感器硬件时钟(ms)",
        }
        if color_intr is not None:
            calib["rgb_intrinsics"] = {
                "fx": float(color_intr.fx),
                "fy": float(color_intr.fy),
                "ppx": float(color_intr.ppx),
                "ppy": float(color_intr.ppy),
            }
            calib["rgb_distortion"] = {
                "model": _distortion_name(rs, color_intr.model),
                "coeffs": [float(c) for c in color_intr.coeffs],
            }
        try:
            with open(os.path.join(out_dir, "calibration.json"), "w", encoding="utf-8") as f:
                json.dump(calib, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 标定信息写盘失败不影响录制

        with rec_lock:
            rec.update({
                "active": True, "dir": out_dir, "color_mp4": color_mp4,
                "depth_zst": depth_zst, "enc": enc, "logf": logf,
                "color_q": color_q, "depth_q": depth_q, "frames_q": frames_q,
                "color_writer": color_writer, "depth_writer": depth_writer,
                "frames_writer": frames_writer,
                "frame_idx": 0, "mp4_count": 0, "zst_count": 0,
                "start_time": datetime.now().isoformat(timespec="seconds"),
                "paused": False, "pauses": [],
            })
        send_evt({"evt": "rec_started", "dir": out_dir, "fps": actual_fps,
                  "serial": dev_info["serial"], "name": dev_info["name"],
                  "firmware": dev_info["firmware"], "encoding": eff_enc})

    def _finalize_rec(snap):
        """录制收尾（后台线程）：哨兵→写线程 EOF→ffmpeg flush→校验→元数据→rec_done

        采集循环不等待本线程：收尾期间相机继续预览，start_rec 可立即开始新录制。
        """
        out_dir = snap["dir"]
        frame_idx = snap["frame_idx"]
        try:
            for q in (snap["color_q"], snap["depth_q"], snap["frames_q"]):
                try:
                    q.put(None)
                except Exception:
                    pass
            snap["color_writer"].join(timeout=60)
            snap["depth_writer"].join(timeout=120)
            snap["frames_writer"].join(timeout=60)
            try:
                snap["enc"].wait(timeout=60)
            except Exception:
                try:
                    snap["enc"].kill()
                except Exception:
                    pass
            if snap["logf"] is not None:
                try:
                    snap["logf"].close()
                except Exception:
                    pass
            if frame_idx == 0:
                import shutil
                shutil.rmtree(out_dir, ignore_errors=True)
                send_evt({"evt": "rec_done_err", "err": "未采集到任何帧"})
                return
            color_ok = os.path.isfile(snap["color_mp4"]) and os.path.getsize(snap["color_mp4"]) > 0
            # 深度有效性：解析 DZST 头部确认帧数>0（帧数未回填=写线程异常截断）
            raw_ok = False
            if os.path.isfile(snap["depth_zst"]) and os.path.getsize(snap["depth_zst"]) > 20:
                try:
                    _dz = _load_depth_zst()
                    with open(snap["depth_zst"], "rb") as f:
                        _hdr = _dz.read_header(f)
                    raw_ok = bool(_hdr and _hdr[3] > 0)
                except Exception:
                    raw_ok = False
            if not color_ok:
                send_evt({"evt": "rec_done_err", "err": "彩色视频编码失败（详见 ffmpeg.log）"})
                return
            meta = {
                "device_type": "realsense",
                "device_serial": dev_info["serial"],
                "device_name": dev_info["name"],
                "firmware_version": dev_info["firmware"],
                "color_resolution": f"{cw}x{ch}",
                "depth_resolution": f"{cw}x{ch}",
                "depth_units_mm": dev_info["depth_units_mm"],
                "fps": actual_fps,
                "start_time": snap["start_time"],
                "end_time": datetime.now().isoformat(timespec="seconds"),
                "frame_count": frame_idx,
                "depth_raw": bool(raw_ok),
                "color_codec": "h264",
                # 落盘回写：事后可从 meta.json 反查该视频的实际编码档位
                "color_encoding": dict(eff_enc),
                "depth_codec": "dzst2",
                "pauses": snap.get("pauses") or [],
            }
            # 量化步长与 depth_zst.QUANTUM 保持一致；下方兜底值必须同步人工更新
            # （当前 32mm，误差 ±16mm；近距人脸几何测量场景请调回 16）
            try:
                meta["depth_quantum_mm"] = int(_load_depth_zst().QUANTUM)
            except Exception:
                meta["depth_quantum_mm"] = 32
            try:
                with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception as e:
                send_evt({"evt": "rec_done_err", "err": f"写元数据失败: {e}"})
                return
            send_evt({"evt": "rec_done", "dir": out_dir, "frames": frame_idx})
        except Exception as e:
            send_evt({"evt": "rec_done_err", "err": str(e)})

    def handle_stop_rec():
        with rec_lock:
            if not rec["active"]:
                return
            rec["active"] = False
            # 停止时仍在暂停中：补全暂停区间结束时间
            if rec.get("paused") and rec["pauses"] and "end" not in rec["pauses"][-1]:
                rec["pauses"][-1]["end"] = datetime.now().isoformat(timespec="seconds")
            snap = dict(rec)
        t = threading.Thread(target=_finalize_rec, args=(snap,), daemon=True)
        finalize_threads.append(t)
        t.start()

    def handle_pause_rec():
        with rec_lock:
            if not rec["active"] or rec.get("paused"):
                return
            rec["paused"] = True
            rec["pauses"].append(
                {"start": datetime.now().isoformat(timespec="seconds")})
        send_evt({"evt": "rec_paused"})

    def handle_resume_rec():
        with rec_lock:
            if not rec["active"] or not rec.get("paused"):
                return
            rec["paused"] = False
            if rec["pauses"] and "end" not in rec["pauses"][-1]:
                rec["pauses"][-1]["end"] = datetime.now().isoformat(timespec="seconds")
        send_evt({"evt": "rec_resumed"})

    def _watch_stdin():
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    cmd = json.loads(line)
                except Exception:
                    continue
                c = cmd.get("cmd")
                if c == "start_rec":
                    handle_start_rec(cmd.get("path") or "")
                elif c == "stop_rec":
                    handle_stop_rec()
                elif c == "pause_rec":
                    handle_pause_rec()
                elif c == "resume_rec":
                    handle_resume_rec()
                elif c == "quit":
                    quit_evt.set()
                    break
        except Exception:
            pass
        finally:
            # 父进程退出/关闭 stdin（worker 重启）：退出释放相机
            quit_evt.set()

    threading.Thread(target=_watch_stdin, daemon=True).start()

    send_evt({"evt": "ready", "fps": actual_fps, **dev_info})

    try:
        while not quit_evt.is_set():
            frames = pipe.wait_for_frames()
            color = frames.get_color_frame()
            if color is not None:
                _put_latest(preview_q, _frame_array(color).tobytes())
            with rec_lock:
                active = rec["active"]
                paused = rec.get("paused")
            # 暂停中：跳过录制入队（帧不进编码器），预览流继续输出
            if not active or paused:
                continue
            aligned = align.process(frames)
            a_color = aligned.get_color_frame()
            a_depth = aligned.get_depth_frame()
            items = [None, None]
            frec = {}
            if a_color is not None:
                items[0] = _frame_array(a_color).tobytes()
                frec["mp4_frame"] = rec["mp4_count"]
                rec["mp4_count"] += 1
                frec["rgb_ts_ms"] = round(a_color.get_timestamp(), 3)
                frec["rgb_frame"] = a_color.get_frame_number()
            if a_depth is not None:
                items[1] = _frame_array(a_depth).tobytes()
                frec["zst_frame"] = rec["zst_count"]
                rec["zst_count"] += 1
                frec["depth_ts_ms"] = round(a_depth.get_timestamp(), 3)
                frec["depth_frame"] = a_depth.get_frame_number()
            for q, item in zip((rec["color_q"], rec["depth_q"]), items):
                if item is None:
                    continue
                # 带超时入队：队列满表示编码/压缩背压（限速）；停止后丢弃该帧
                while True:
                    try:
                        q.put(item, timeout=0.5)
                        break
                    except queue.Full:
                        if not rec["active"]:
                            break
            if frec:
                try:
                    rec["frames_q"].put_nowait(frec)
                except queue.Full:
                    pass  # 背压：帧同步记录非关键，丢弃
            rec["frame_idx"] += 1
    finally:
        # 会话退出（quit / stdin EOF）：录制中则先收尾再退出，避免文件截断
        with rec_lock:
            if rec["active"]:
                rec["active"] = False
                if rec.get("paused") and rec["pauses"] and "end" not in rec["pauses"][-1]:
                    rec["pauses"][-1]["end"] = datetime.now().isoformat(timespec="seconds")
                snap = dict(rec)
            else:
                snap = None
        if snap is not None:
            t = threading.Thread(target=_finalize_rec, args=(snap,), daemon=True)
            finalize_threads.append(t)
            t.start()
        for t in finalize_threads:
            t.join(timeout=30)
    return 0


# ==================== main ====================
def main():
    if len(sys.argv) < 2:
        _safe_print("usage: realsense_child.py probe | session [fps]")
        return 2
    # 先重定向 C 库日志（必须在任何 pyrealsense2 导入之前）
    _setup_c_log_redirect()
    cmd = sys.argv[1]
    try:
        if cmd == "probe":
            return cmd_probe()
        if cmd == "session":
            fps = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_FPS
            return cmd_session(fps)
    except Exception as e:
        try:
            _safe_print(f"ERR {e}")
        except Exception:
            pass
        return 1
    try:
        _safe_print(f"bad args: {sys.argv[1:]}")
    except Exception:
        pass
    return 2


if __name__ == "__main__":
    sys.exit(main())
