# -*- coding: utf-8 -*-
"""pyrealsense2 子进程执行器（Intel RealSense D455F 深度相机）

背景：与 orbbec_camera_proc.py 相同——pyrealsense2 底层 C 库（librealsense）在 USB 不可达
或初始化失败时可能崩溃，C 扩展的崩溃无法被 Python try/except 捕获，会杀死 Flask worker。
所有 pyrealsense2 调用都通过 subprocess 在本脚本（独立进程）中执行，即使 C 库崩溃
也只影响本子进程。

数据流：与 Orbbec 一致，只采集 彩色 + 深度 两路（**不启用红外流**）：
  - 彩色：D455F 原生 1280x800（bgr8，1MP 全局快门），分辨率自动协商逐级降级
  - 深度：D455F 原生 1280x720（z16，单位 mm），原始 z16 以 ffv1 无损保留，另生成 jet 伪彩色预览
  - 深度量程：D455F 理想范围 0.6m-6m（深度 200mm-8000mm 归一化到伪彩色）

录制编码（对齐 orbbec 侧"彩色零重编码/深度无损"思路，消除旧 MJPG 写盘+二次转码的双重有损）：
  color.mp4       彩色 H.264（libx264 CRF 18，浏览器可播）
  depth.mp4       深度伪彩色 H.264（libx264 CRF 23，jet 色标，0=深灰无效）
  depth_raw.mkv   原始深度序列（ffv1 无损，z16 16 位精度完整保留，供分析）
  meta.json       序列号 / 分辨率 / 帧时间戳 / 编码信息

子命令：
  probe                        探测设备，输出 "OK <count> <serial>" 与 "DETAIL <json>"
  stream                       输出彩色 MJPEG 流到 stdout（含 --frame 边界）
  record <path> <fps>          录制（彩色 MP4 + 深度伪彩色 MP4 + 原始深度 MKV + 元数据），stdin 收到 "stop" 后优雅停止
"""
import os
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
# 首选 D455/D455F 原生 1280x800 彩色 + 1280x720 深度（16:10 RGB 全幅面），
# 带宽不足时依次降级；非 D455 系（D415/D435）也总能命中其中一档。
_STREAM_COMBOS = (
    (1280, 800, 1280, 720),   # D455F 原生
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
    """启动 pipeline（彩色 + 深度），D455F 原生分辨率优先，失败自动降级。

    返回 (pipeline, profile, color_w, color_h, depth_w, depth_h, actual_fps)；
    profile 用于读取实际设备信息（型号/固件/深度缩放）。所有组合均失败时抛异常。
    """
    last_err = None
    for (cw, ch, dw, dh) in _STREAM_COMBOS:
        for f in _fps_chain(fps):
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


def _try_emit_jpeg(frame, quality=70):
    """录制中实时预览帧输出：stdout 无消费者/管道背压时静默丢弃，绝不阻塞或抛错影响录制"""
    fd = _saved_stdout_fd if _saved_stdout_fd is not None else 1
    import select
    try:
        _r, _w, _x = select.select([], [fd], [], 0)
        if fd not in _w:
            return
        import cv2
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return
        os.write(fd, b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
        os.write(fd, buf.tobytes())
        os.write(fd, b"\r\n")
    except (OSError, BrokenPipeError):
        pass


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


# zstd 深度序列文件格式（绝对无损，压缩率远高于 ffv1）：
#   magic "DZST" | version u32le | width u32le | height u32le | frame_count u32le
#   然后逐帧: [compressed_len u32le][zstd block]
def _zstd_writer(q, fobj, w, h):
    """写线程：从队列取 z16 深度帧字节，zstd 压缩追加写入；None 哨兵后回填帧数并关闭"""
    import struct
    try:
        import zstandard as _zstd
    except ImportError:
        import sys
        sys.stderr.write("zstandard 未安装，无法写入深度序列\n")
        sys.stderr.flush()
        fobj.close()
        return
    try:
        cctx = _zstd.ZstdCompressor(level=3)
        fobj.write(b"DZST")
        fobj.write(struct.pack("<IIII", 1, w, h, 0))
        count = 0
        while True:
            item = q.get()
            if item is None:
                break
            comp = cctx.compress(item)
            fobj.write(struct.pack("<I", len(comp)))
            fobj.write(comp)
            count += 1
        fobj.seek(16)
        fobj.write(struct.pack("<I", count))
        fobj.flush()
    finally:
        fobj.close()


def cmd_record(path, fps):
    """录制彩色 H.264 MP4 + 原始深度 zstd 无损压缩序列 + 元数据

    产物（输出目录保留，不打包；全部为最终格式，无需二次转码）：
      <path>/color.mp4      彩色视频（H.264，libx264 CRF 18，浏览器可播）
      <path>/depth_raw.zst  原始深度序列（zstd 无损压缩，z16 16 位精度完整保留，供分析）
      <path>/meta.json      序列号 / 配置 / 每帧时间戳 / 编码信息
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

        frame_idx = 0
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
                ts = _time.time()
                color = frames.get_color_frame()
                depth = frames.get_depth_frame()
                items = [None, None]
                if color is not None:
                    color_arr = _frame_array(color)
                    items[0] = color_arr.tobytes()
                    # 录制中同步输出 MJPEG 实时预览（stdout 无消费者/背压时静默丢弃）
                    _try_emit_jpeg(color_arr)
                if depth is not None:
                    items[1] = _frame_array(depth).tobytes()
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
                meta["frames"].append({"index": frame_idx, "t": round(ts, 4)})
                frame_idx += 1
        finally:
            try:
                pipe.stop()
            except Exception:
                pass
            stop_event.set()
            # 哨兵 → 写线程关闭 stdin → ffmpeg 读到 EOF 正常 flush 收尾
            for q in (color_q, depth_q):
                try:
                    q.put(None)
                except Exception:
                    pass
            color_writer.join(timeout=60)
            depth_writer.join(timeout=120)
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
        raw_ok = os.path.isfile(depth_zst) and os.path.getsize(depth_zst) > 16
        if not color_ok:
            _safe_print("ERR 彩色视频编码失败（详见 ffmpeg.log）")
            return 1

        meta["end_time"] = datetime.now().isoformat(timespec="seconds")
        meta["frame_count"] = frame_idx
        meta["depth_raw"] = bool(raw_ok)
        meta["color_codec"] = "h264"
        meta["depth_codec"] = "zstd"
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
