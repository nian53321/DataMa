# -*- coding: utf-8 -*-
"""pyrealsense2 子进程执行器（Intel RealSense D455F 深度相机）

背景：与 orbbec_camera_proc.py 相同——pyrealsense2 底层 C 库（librealsense）在 USB 不可达
或初始化失败时可能崩溃，C 扩展的崩溃无法被 Python try/except 捕获，会杀死 Flask worker。
所有 pyrealsense2 调用都通过 subprocess 在本脚本（独立进程）中执行，即使 C 库崩溃
也只影响本子进程。

数据流：与 Orbbec 一致，只采集 彩色 + 深度 两路（**不启用红外流**）：
  - 彩色：D455F 原生 1280x800（bgr8，1MP 全局快门），分辨率自动协商逐级降级
  - 深度：D455F 原生 1280x720（z16，单位 mm），DZST v2 存储（11bit 量化 + 帧间差分 + zstd，
    量化误差 ≤2mm 低于深度噪声，体积约为逐帧 zstd 的 2/5）
  - 深度量程：D455F 理想范围 0.6m-6m（深度 200mm-8000mm 归一化到伪彩色）

录制产物（输出目录，全部为最终格式）：
  color.mp4         彩色 H.264（libx264 CRF 18，浏览器可播）
  depth_raw.zst     原始深度序列（DZST v2：量化+差分 zstd 压缩，解码后为毫米深度值，供分析）
  frames.jsonl      逐帧同步记录（MP4/ZST 帧序号 + RGB/深度硬件时间戳 + 硬件帧号）
  calibration.json  相机标定（depth_scale / RGB 内参 / 畸变 / 分辨率 / 序列号 / 对齐状态）
  meta.json         序列号 / 分辨率 / 帧时间戳 / 编码信息

子命令：
  probe                        探测设备，输出 "OK <count> <serial>" 与 "DETAIL <json>"
  stream                       输出彩色 MJPEG 流到 stdout（含 --frame 边界）
  record <path> <fps>          录制（彩色 MP4 + 原始深度 zstd + 逐帧同步与标定 JSON），stdin 收到 "stop" 后优雅停止
"""
import os
import queue
import sys
import threading
from datetime import datetime

BACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # back/
SCRIPTS_DIR = os.path.join(BACK_DIR, "scripts")
sys.path.insert(0, BACK_DIR)
sys.path.insert(0, SCRIPTS_DIR)

# 默认流配置：D455F 原生 RGB 1280x800（1MP 全局快门） + 深度 1280x720，30fps 起。
# D455F 深度最高 90fps、RGB 最高 60fps；usbip 透传带宽有限，默认 30fps 稳定。
DEFAULT_FPS = 30
# 分辨率协商降级链 (color_w, color_h, depth_w, depth_h)：
# usbip 透传 USB2（480Mbps，实测 speed=480）下各组合带宽估算：
#   1280x800@30+1280x720@30 ≈ 147MB/s 超 USB2（仅 5fps 可跑）
#   848x480@15 ≈ 30MB/s / 640x480@30 ≈ 46MB/s 在 USB2 带宽内（可命中高帧率）
# 帧率优先策略（_start_pipeline 先降分辨率再降 fps）会优先命中 640x480@30
# 或 848x480@15，USB 链路升级到 USB3（5000M）后才能跑原生分辨率高帧率。
_STREAM_COMBOS = (
    (1280, 800, 1280, 720),   # D455F 原生（USB2 下仅 5fps）
    (1280, 720, 1280, 720),
    (848, 480, 848, 480),
    (640, 480, 640, 480),
)

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
    """枚举 RealSense 设备，返回 (count, serial)
    只枚举不打开流，避免长时间占用设备。
    """
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


def _fps_chain(fps):
    """请求 fps -> 逐级降级链（只降不升），如 30 -> [30, 15, 5]"""
    std = [60, 30, 15, 5]
    chain = []
    for f in [int(fps)] + std:
        if f not in chain and f <= int(fps):
            chain.append(f)
    return chain


def _start_pipeline(rs, fps):
    """启动 pipeline（彩色 + 深度），帧率优先降级，失败自动降档。

    usbip 透传下 RealSense 被识别为 USB2（480Mbps，实测 speed=480），带宽预算约
    40-60MB/s，D455F 原生 1280x800@30+1280x720@30 需约 147MB/s 必然失败。
    帧率优先：先以请求 fps 试所有分辨率（USB2 内 640x480@30 约 46MB/s 可命中），
    全失败再降 fps（15fps 命中 848x480，5fps 才跑得起原生 1280x800）。

    返回 (pipeline, profile, color_w, color_h, depth_w, depth_h, actual_fps)；
    profile 用于读取实际设备信息（型号/固件/深度缩放）。所有组合均失败时抛异常。
    """
    last_err = None
    for f in _fps_chain(fps):
        for (cw, ch, dw, dh) in _STREAM_COMBOS:
            pipe = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(rs.stream.color, cw, ch, rs.format.bgr8, f)
            cfg.enable_stream(rs.stream.depth, dw, dh, rs.format.z16, f)
            try:
                profile = pipe.start(cfg)
                return pipe, profile, cw, ch, dw, dh, f
            except Exception as e:
                last_err = e
                try:
                    pipe.stop()
                except Exception:
                    pass
    raise RuntimeError(f"启动 RealSense 相机失败（{last_err}）")


def _frame_array(frame):
    """取帧数据为 (h, w, c) uint8 ndarray

    pyrealsense2 的 frame.get_data() 在 numpy 可用时返回 ndarray，但在部分
    numpy/OpenCV 组合下（如容器内 opencv 5.x）会回退返回 bytes；统一在此转换，
    供 stream（cv2 编码）与 record（ffmpeg 原始帧）共用。
    """
    import numpy as np
    data = frame.get_data()
    if isinstance(data, (bytes, bytearray, memoryview)):
        data = bytes(data)
        return np.frombuffer(data, dtype=np.uint8).reshape(
            frame.get_height(), frame.get_width(), -1)
    return np.ascontiguousarray(data)


def _emit_jpeg(frame, quality=80):
    """将一帧编码为 JPEG 并写入 stdout（含 MJPEG 边界）"""
    import cv2
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return
    _safe_write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
    _safe_write(buf.tobytes())
    _safe_write(b"\r\n")


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
    """录制中实时预览写线程：从队列取彩色帧编码 JPEG 写 stdout（multipart 边界）

    必须独立于采集循环：预览帧（~100KB）超过 stdout 管道缓冲（64KB），录制
    刚开始前端尚未重连预览流（无消费者）时写管道会阻塞——曾内联在采集循环
    里执行，开头阻塞 ~1s 导致 librealsense 帧队列溢出丢 29 帧（硬件帧号
    1→31）。独立线程后管道背压只阻塞本线程，采集循环满帧率运行；队列满
    丢最旧帧，消费者恢复后立即跟上最新画面。
    """
    import cv2
    import numpy as np
    fd = _saved_stdout_fd if _saved_stdout_fd is not None else 1
    while True:
        item = q.get()
        if item is None:
            break
        try:
            frame = np.frombuffer(item, np.uint8).reshape(h, w, 3)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                os.write(fd, b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                os.write(fd, buf.tobytes())
                os.write(fd, b"\r\n")
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
            import json as _json
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
            _safe_print("DETAIL " + _json.dumps(detail, ensure_ascii=False))
        return 0
    except Exception as e:
        _safe_print(f"ERR {e}")
        return 1


# ==================== stream ====================
def cmd_stream(fps):
    """持续输出彩色 MJPEG 流。stdin 收到 "stop" 时优雅退出；父进程断连自动退出。"""
    try:
        rs = _import_rs()
        count, _serial = _query_devices()
        if count == 0:
            _emit_jpeg(_placeholder_frame("未检测到 RealSense 相机"))
            return 1
        pipe, profile, cw, ch, dw, dh, actual_fps = _start_pipeline(rs, int(fps))

        stop_event = threading.Event()

        def _watch_stdin():
            try:
                for line in sys.stdin:
                    if line.strip() == "stop":
                        stop_event.set()
                        break
            except Exception:
                pass

        threading.Thread(target=_watch_stdin, daemon=True).start()
        # 就绪行：父进程据此确认 pipeline 已成功启动（stdin "stop" 即优雅退出）
        _safe_print(f"READY {actual_fps}")
        try:
            while not stop_event.is_set():
                frames = pipe.wait_for_frames()
                color = frames.get_color_frame()
                if color is None:
                    continue
                # color.get_data() 为 (h, w, 3) 的 BGR ndarray（bgr8 格式）
                _emit_jpeg(_frame_array(color))
        finally:
            pipe.stop()
        return 0
    except Exception as e:
        _safe_print(f"ERR {e}")
        return 1


def _placeholder_frame(text):
    """生成一张深灰色占位提示帧（640x480），设备不可用时显示给用户"""
    import numpy as np
    import cv2
    img = np.full((480, 640, 3), 28, dtype=np.uint8)
    cv2.putText(img, text, (24, 245), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2)
    return img


# ==================== record ====================
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
#   12bit 量化 + 关键帧/帧间差分 + zstd，体积约为逐帧 zstd 的 1/8 ~ 1/15
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
    import json
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


def cmd_record(path, fps):
    """录制彩色 H.264 MP4 + DZST v2 深度压缩序列 + 逐帧同步/标定信息

    产物（输出目录保留，不打包；全部为最终格式，无需二次转码）：
      <path>/color.mp4         彩色视频（H.264，libx264 CRF 18，浏览器可播）
      <path>/depth_raw.zst     原始深度序列（DZST v2：11bit 量化 + 帧间差分 zstd，解码后为毫米深度值）
      <path>/frames.jsonl      逐帧记录：mp4_frame/zst_frame + RGB/深度硬件时间戳(ms) + 硬件帧号
      <path>/calibration.json  相机标定：depth_scale/RGB 内参/畸变/分辨率/序列号/对齐状态
      <path>/meta.json         序列号 / 配置 / 每帧时间戳 / 编码信息
    深度伪彩色不再录制：可视化播放时由后端从原始深度实时转码（depth-video 端点）。
    输出 "DONE <out_dir> <frames>" 或 "ERR <原因>" 到 stdout。
    """
    import json
    import queue
    import struct
    import time as _time

    try:
        rs = _import_rs()
        count, _serial = _query_devices()
        if count == 0:
            _safe_print("ERR 未检测到 RealSense 相机")
            return 1
        pipe, profile, cw, ch, dw, dh, actual_fps = _start_pipeline(rs, int(fps))

        # 从已启动的 pipeline 读取设备信息（与录制同一设备），写入 meta 供入库
        device = profile.get_device()
        device_name = ""
        firmware = ""
        depth_units_mm = 1.0
        try:
            device_name = device.get_info(rs.camera_info.name) or ""
        except Exception:
            pass
        try:
            firmware = device.get_info(rs.camera_info.firmware_version) or ""
        except Exception:
            pass
        try:
            depth_units_mm = round(device.first_depth_sensor()
                                   .get_option(rs.option.depth_units) * 1000.0, 3)
        except Exception:
            pass

        # 深度对齐到彩色坐标系（depth aligned to color）：
        # 对齐后深度图逐像素与彩色图对应（分辨率也变为彩色分辨率）
        align = rs.align(rs.stream.color)
        dw, dh = cw, ch  # 对齐后深度分辨率 = 彩色分辨率

        stop_event = threading.Event()

        def _watch_stdin():
            try:
                for line in sys.stdin:
                    if line.strip() == "stop":
                        stop_event.set()
                        break
            except Exception:
                pass

        threading.Thread(target=_watch_stdin, daemon=True).start()

        out_dir = os.path.abspath(path)
        os.makedirs(out_dir, exist_ok=True)

        color_mp4 = os.path.join(out_dir, "color.mp4")
        depth_zst = os.path.join(out_dir, "depth_raw.zst")

        # 编码器：仅彩色 bgr24 -> libx264（一次有损，直接 H.264 浏览器可播）
        # 深度原始帧不走 ffmpeg，由 zstd 写线程直接压缩，省掉 ffv1 EOF flush 卡顿
        logf = None
        try:
            logf = open(os.path.join(out_dir, "ffmpeg.log"), "ab")
        except OSError:
            pass
        encs = []
        try:
            encs.append(_spawn_ffmpeg([
                "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{cw}x{ch}",
                "-r", str(actual_fps), "-i", "pipe:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-g", "15", "-keyint_min", "15",
                "-movflags", "+faststart", "-an", color_mp4], logf))
        except Exception as e:
            for p in encs:
                try:
                    p.kill()
                except Exception:
                    pass
            if logf is not None:
                try:
                    logf.close()
                except Exception:
                    pass
            _safe_print(f"ERR 启动编码器失败: {e}")
            return 1

        color_q = queue.Queue(maxsize=30)
        color_writer = threading.Thread(
            target=_enc_writer, args=(color_q, encs[0]), daemon=True)
        color_writer.start()

        depth_q = queue.Queue(maxsize=30)
        depth_fobj = open(depth_zst, "wb")
        depth_writer = threading.Thread(
            target=_zstd_writer, args=(depth_q, depth_fobj, dw, dh), daemon=True)
        depth_writer.start()

        # 逐帧同步记录（frames.jsonl：MP4/ZST 帧序号 + RGB/深度硬件时间戳 + 硬件帧号）
        frames_q = queue.Queue(maxsize=60)
        frames_fobj = open(os.path.join(out_dir, "frames.jsonl"), "w", encoding="utf-8")
        frames_writer = threading.Thread(
            target=_jsonl_writer, args=(frames_q, frames_fobj), daemon=True)
        frames_writer.start()

        # 实时预览写线程（独立于采集循环，见 _preview_writer 注释）
        preview_q = queue.Queue(maxsize=2)
        preview_writer = threading.Thread(
            target=_preview_writer, args=(preview_q, cw, ch), daemon=True)
        preview_writer.start()

        # 相机标定信息（每次录制保存一次，写入 out_dir/calibration.json）
        color_intr = None
        try:
            color_intr = profile.get_stream(
                rs.stream.color).as_video_stream_profile().get_intrinsics()
        except Exception:
            pass
        calib = {
            "depth_scale": round(depth_units_mm / 1000.0, 6),  # 米/单位（depth_units）
            "rgb_intrinsics": None,
            "rgb_distortion": None,
            "rgb_resolution": f"{cw}x{ch}",
            "depth_resolution": f"{dw}x{dh}",
            "serial": _serial,
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

        frame_idx = 0
        mp4_count = 0
        zst_count = 0
        meta = {
            "device_type": "realsense",
            "device_serial": _serial,
            "device_name": device_name,
            "firmware_version": firmware,
            "color_resolution": f"{cw}x{ch}",
            "depth_resolution": f"{dw}x{dh}",
            "depth_units_mm": depth_units_mm,
            "fps": actual_fps,
            "start_time": datetime.now().isoformat(timespec="seconds"),
            "frames": [],
        }
        # 就绪行：所有编码器/写线程就绪后才输出，父进程据此确认录制已可用
        _safe_print(f"READY {actual_fps}")
        try:
            while not stop_event.is_set():
                frames = pipe.wait_for_frames()
                frames = align.process(frames)  # 深度对齐到彩色坐标系
                ts = _time.time()
                color = frames.get_color_frame()
                depth = frames.get_depth_frame()
                items = [None, None]
                rec = {}
                if color is not None:
                    color_arr = _frame_array(color)
                    items[0] = color_arr.tobytes()
                    # 实时预览帧入队（复用 items[0] 的字节拷贝，零额外拷贝）：
                    # 编码与写管道由预览线程承担，不占采集循环的帧率预算
                    _put_latest(preview_q, items[0])
                    rec["mp4_frame"] = mp4_count
                    mp4_count += 1
                    rec["rgb_ts_ms"] = round(color.get_timestamp(), 3)
                    rec["rgb_frame"] = color.get_frame_number()
                if depth is not None:
                    items[1] = _frame_array(depth).tobytes()
                    rec["zst_frame"] = zst_count
                    zst_count += 1
                    rec["depth_ts_ms"] = round(depth.get_timestamp(), 3)
                    rec["depth_frame"] = depth.get_frame_number()
                for q, item in zip((color_q, depth_q), items):
                    if item is None:
                        continue
                    # 带超时入队：队列满表示编码/压缩背压（限速）；收到停止信号时丢弃该帧
                    while True:
                        try:
                            q.put(item, timeout=0.5)
                            break
                        except queue.Full:
                            if stop_event.is_set():
                                break
                if rec:
                    try:
                        frames_q.put_nowait(rec)
                    except queue.Full:
                        pass  # 背压：帧同步记录非关键，丢弃
                meta["frames"].append({"index": frame_idx, "t": round(ts, 4)})
                frame_idx += 1
        finally:
            try:
                pipe.stop()
            except Exception:
                pass
            stop_event.set()
            # 哨兵 → 写线程关闭 stdin → ffmpeg 读到 EOF 正常 flush 收尾
            for q in (color_q, depth_q, frames_q):
                try:
                    q.put(None)
                except Exception:
                    pass
            # 预览线程哨兵（丢旧策略，队列满也不阻塞收尾）并等它退出：
            # 确保 DONE 结果行输出前不再有预览帧写 stdout，避免 JPEG 字节
            # 与结果行交错导致父进程解析不到 DONE
            _put_latest(preview_q, None)
            preview_writer.join(timeout=5)
            color_writer.join(timeout=60)
            depth_writer.join(timeout=120)
            frames_writer.join(timeout=60)
            for p in encs:
                try:
                    p.wait(timeout=60)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            if logf is not None:
                try:
                    logf.close()
                except Exception:
                    pass

        if frame_idx == 0:
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)
            _safe_print("ERR 未采集到任何帧")
            return 1

        color_ok = os.path.isfile(color_mp4) and os.path.getsize(color_mp4) > 0
        # 深度有效性：解析 DZST 头部确认帧数>0（v2 头部 20 字节，0 帧空文件
        # 也会超过旧的 ">16 字节" 阈值被误判有效；帧数未回填=写线程异常截断）
        raw_ok = False
        if os.path.isfile(depth_zst) and os.path.getsize(depth_zst) > 20:
            try:
                _dz = _load_depth_zst()
                with open(depth_zst, "rb") as f:
                    _hdr = _dz.read_header(f)
                raw_ok = bool(_hdr and _hdr[3] > 0)
            except Exception:
                raw_ok = False
        if not color_ok:
            _safe_print("ERR 彩色视频编码失败（详见 ffmpeg.log）")
            return 1

        meta["end_time"] = datetime.now().isoformat(timespec="seconds")
        meta["frame_count"] = frame_idx
        meta["depth_raw"] = bool(raw_ok)
        meta["color_codec"] = "h264"
        meta["depth_codec"] = "dzst2"
        meta["depth_quantum_mm"] = 4
        try:
            with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _safe_print(f"ERR 写元数据失败: {e}")
            return 1
        _safe_print(f"DONE {out_dir} {frame_idx}")
        return 0
    except Exception as e:
        _safe_print(f"ERR {e}")
        return 1


# ==================== main ====================
def main():
    if len(sys.argv) < 2:
        _safe_print("usage: realsense_child.py probe | stream [fps] | record <path> <fps>")
        return 2
    # 先重定向 C 库日志（必须在任何 pyrealsense2 导入之前）
    _setup_c_log_redirect()
    cmd = sys.argv[1]
    try:
        if cmd == "probe":
            return cmd_probe()
        if cmd == "stream":
            fps = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_FPS
            return cmd_stream(fps)
        if cmd == "record" and len(sys.argv) >= 4:
            return cmd_record(sys.argv[2], int(sys.argv[3]))
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
