# -*- coding: utf-8 -*-
"""Orbbec 相机服务子进程（stdin/stdout 行协议）

由主进程（gunicorn）Popen 启动，持有 PyK4A 会话并响应指令。所有 pyk4a
C 调用（start / get_capture / stop / 录制）都在本进程内执行——usbip 透传下
这些 C 调用可能无限卡死（如 nvram 读取失败无限重试）且持有 GIL，主进程内
无法超时。隔离到子进程后，主进程读超时直接 kill 本进程即可，永不冻结。

录制（本文件核心职责）：
  不用 PyK4ARecord——k4a_record_create 强制写 彩色+深度+IR+IMU 四轨且
  深度/IR 为 rawvideo（640x576x2B 未压缩，6 分钟 17.5GB，后处理直接卡死）。
  改为实时喂 ffmpeg 双 pipe：
    输入0 深度 rawvideo gray16le → -c:v ffv1 无损压缩（实测 737KB→17KB/帧）
    输入1 彩色 MJPG             → -c:v copy 流拷贝（零重编码开销）
  输出 mkv 只含 彩色+深度 两轨，无 IR 轨；采集时也不读 cap.ir（不取 IR 帧）。
  注意 ffmpeg 的 pipe:0/pipe:1 对应子进程 fd0/fd1，故用 stdin=深度读端、
  stdout=彩色读端 两个管道，日志走 stderr（DEVNULL）。

协议（stdin 每行一条 JSON 指令；stdout 每行一条 JSON 响应）：
  指令: {"cmd":"start", "config":{...}}
        {"cmd":"stop"}                     停止相机后进程退出
        {"cmd":"ping"}
        {"cmd":"get_frame"}                取一帧彩色 MJPG（录制中返回扇出帧）
        {"cmd":"start_record", "path":...}
        {"cmd":"stop_record"}
  响应: {"ok":true/false, "err":..., "frame_len":n}
        frame_len>0 时响应行后紧跟 n 字节原始帧数据（无换行分隔）。
"""
import json
import os
import queue as _queue
import subprocess
import sys
import threading
import time
from datetime import datetime

from pyk4a import (Config, ColorResolution, DepthMode, FPS, ImageFormat, PyK4A)

_k4a = None
_config = None
_depth_size = "640x576"     # 当前 depth_mode 的分辨率（rawvideo -s 参数）
_fps = 30                   # 当前帧率（WFOV_UNBINNED 仅 5/15，其余 30）
_ffmpeg_depth = None     # 深度 ffmpeg 子进程（ffv1 无损）
_ffmpeg_color = None     # 彩色 ffmpeg 子进程（MJPG 流拷贝）
_depth_mkv = None        # 深度临时 mkv（录制中）
_color_mkv = None        # 彩色临时 mkv（录制中）
_depth_w = None             # 深度帧写入端 fd
_color_w = None             # 彩色帧写入端 fd
_color_q = None             # 彩色帧写队列（采集线程 -> 写线程）
_depth_q = None             # 深度帧写队列
_rec_thread = None
_rec_writer_threads = []
_rec_stop_evt = None
_recording = False
_rec_mkv = None
_rec_frames = 0
_lock = threading.Lock()
_color_lock = threading.Lock()
_latest_color = None
_latest_seq = 0

# K4A depth_mode -> 深度图像分辨率（pyk4a 枚举无宽高属性，手动映射。
# 依据 Orbbec 官方《Femto Bolt 硬件规格》：NFOV unbinned 640x576、
# NFOV 2x2 binned 320x288、WFOV 2x2 binned 512x512、WFOV unbinned 1024x1024）
_DEPTH_SIZES = {
    "NFOV_UNBINNED": "640x576",
    "WFOV_UNBINNED": "1024x1024",
    "NFOV_2X2BINNED": "320x288",
    "WFOV_2X2BINNED": "512x512",
}


def _map_config(cfg):
    color_res = {
        "720P": ColorResolution.RES_720P,
        "1080P": ColorResolution.RES_1080P,
        "1440P": ColorResolution.RES_1440P,
        "2160P": ColorResolution.RES_2160P,
        # usbip 透传带宽有限：默认 720P（1080P MJPG 实测不出发色帧）
    }.get(cfg.get("color_resolution", "720P"), ColorResolution.RES_720P)
    depth_mode = {
        "NFOV_UNBINNED": DepthMode.NFOV_UNBINNED,
        "NFOV_2X2BINNED": DepthMode.NFOV_2X2BINNED,
        "WFOV_UNBINNED": DepthMode.WFOV_UNBINNED,
        "WFOV_2X2BINNED": DepthMode.WFOV_2X2BINNED,
    }.get(cfg.get("depth_mode", "NFOV_UNBINNED"), DepthMode.NFOV_UNBINNED)
    # 官方规格：WFOV unbinned 仅支持 5/15 FPS（其余模式 5/15/25/30）
    fps = FPS.FPS_15 if cfg.get("depth_mode") == "WFOV_UNBINNED" else FPS.FPS_30
    return Config(
        color_resolution=color_res,
        color_format=ImageFormat.COLOR_MJPG,  # 彩色 MJPG 压缩，避免 mkv 体积过大
        depth_mode=depth_mode,
        camera_fps=fps,
        # usbip 透传延迟高，严格同步(默认 True)会因时间戳拟合失败大量丢帧
        synchronized_images_only=False,
    )


def _spawn_ffmpeg(mkv):
    """启动两个独立 ffmpeg（各单输入单管道，规避双输入 pipe 消费慢导致的帧率掉速）：
      深度 ffv1 无损 -> {mkv}.depth.tmp.mkv；彩色 MJPG 流拷贝 -> {mkv}.color.tmp.mkv。
    各自 stdin 收帧；停止录制时关闭写端让 ffmpeg EOF 正常收尾，再由 _stop_recording
    用 -c copy 合并两轨（彩色第一轨、深度第二轨）。返回各进程、写端 fd 与临时路径。
    """
    depth_r, depth_w = os.pipe()
    color_r, color_w = os.pipe()
    base, ext = os.path.splitext(mkv)
    depth_mkv = base + ".depth.tmp.mkv"
    color_mkv = base + ".color.tmp.mkv"
    logf = os.path.join(os.path.dirname(mkv), os.path.basename(mkv) + ".ffmpeg.log")
    depth_proc = color_proc = None
    errf = None
    try:
        errf = open(logf, "ab")
        depth_proc = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             # 输入：深度 rawvideo gray16le（单输入）
             "-f", "rawvideo", "-pix_fmt", "gray16le", "-s", _depth_size,
             "-r", str(_fps), "-i", "pipe:0",
             "-c:v", "ffv1", "-threads", "0",   # 深度 ffv1 无损
             "-f", "matroska", depth_mkv],
            stdin=depth_r, stdout=subprocess.DEVNULL, stderr=errf)
        color_proc = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             # 输入：彩色 MJPG 帧流（单输入）
             "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(_fps), "-i", "pipe:0",
             "-c:v", "copy",                   # 彩色流拷贝，零重编码
             "-f", "matroska", color_mkv],
            stdin=color_r, stdout=subprocess.DEVNULL, stderr=errf)
    except Exception:
        for fd in (depth_r, depth_w, color_r, color_w):
            try:
                os.close(fd)
            except OSError:
                pass
        for proc in (depth_proc, color_proc):
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            errf.close()
        except Exception:
            pass
        raise
    os.close(depth_r)
    os.close(color_r)
    return depth_proc, color_proc, depth_w, color_w, depth_mkv, color_mkv


def _writer_loop(fd, q):
    """写线程：从队列取帧字节写入 pipe。

    独立于采集线程避免死锁——单线程顺序写两个 pipe 时，彩色帧(>64KB 管道缓冲)
    会阻塞等待 ffmpeg 读取，而 ffmpeg 可能正阻塞读深度 pipe，形成互相等待。
    写线程各自阻塞在自己 pipe 上，背压通过有界队列传递到采集线程。
    """
    while True:
        try:
            data = q.get()
            if data is None:
                break  # 停止哨兵
            os.write(fd, data)
        except (BrokenPipeError, OSError):
            break  # ffmpeg 已退出
        except Exception:
            break


def _record_loop():
    """录制线程：独占 get_capture，扇出彩色帧供预览，双路齐全的帧对入队交写线程喂 ffmpeg

    只写 彩色+深度 同时存在的帧对：usbip 透传下 get_capture 可能只返回单路帧
    （深度流带宽更大更易丢帧，实测彩色/深度帧数 193/178 差 15 帧），若各自独立
    写入会导致两轨帧数不一致，可视化页面深度视频时长比彩色短、播放不同步。
    预览扇出不受影响（任一帧存在即更新，保持预览流畅）。
    """
    global _latest_color, _latest_seq, _rec_frames
    while _rec_stop_evt is not None and not _rec_stop_evt.is_set():
        try:
            cap = _k4a.get_capture(timeout=1000)
        except Exception:
            break
        if cap is None:
            continue
        # 只读 彩色+深度，不访问 cap.ir（不取 IR 帧）
        color = cap.color
        depth = cap.depth
        # 预览扇出：彩色帧存在即更新（不依赖深度是否齐全，预览不掉帧）
        if color is not None and len(color) > 0:
            data = color.tobytes()
            with _color_lock:
                _latest_color = data
                _latest_seq += 1
        # 双路齐全才写盘：保证两轨帧数一致、内容一一对应（时间同步）
        if color is None or len(color) == 0 or depth is None:
            continue
        try:
            _color_q.put(color.tobytes(), timeout=5)
            _depth_q.put(depth.tobytes(), timeout=5)
        except Exception:
            break
        _rec_frames += 1


def _stop_recording():
    """停止录制：停采集线程 → 关写端 → 等两个 ffmpeg EOF 收尾 → 合并两轨。返回 (had_rec, resp)

    绝不因线程卡住而 kill ffmpeg：usbip 透传下 get_capture 可能阻塞在 C 层
    （线程无法 join），此时 kill ffmpeg 会丢掉已缓冲帧且文件残缺。正确做法是
    关闭两个写端，ffmpeg 读到 EOF 后正常 flush mkv 收尾（已收帧完整落盘）。
    kill 仅作为 wait 超时的最后兜底。
    """
    global _recording, _rec_stop_evt, _rec_thread
    global _depth_w, _color_w, _rec_mkv, _rec_frames, _rec_writer_threads
    global _ffmpeg_depth, _ffmpeg_color, _depth_mkv, _color_mkv
    if not _recording:
        return False, {"ok": False, "err": "没有进行中的录制"}
    _recording = False
    if _rec_stop_evt:
        _rec_stop_evt.set()
    if _rec_thread:
        _rec_thread.join(timeout=3)  # 卡在 get_capture 时超时跳过；之后不会再入队
    _rec_thread = None
    # 停止写线程：发 None 哨兵（排在数据帧之后），写线程排空队列后自然退出。
    # 不能先关闭写端——fd 关闭后写线程 os.write 抛 BrokenPipeError 丢弃残余帧，
    # 深度 ffv1 编码慢、depth_q 积压更多，丢弃也更多，两轨帧数不一致（深度偏少）。
    for q in (_color_q, _depth_q):
        if q is not None:
            try:
                q.put_nowait(None)
            except Exception:
                pass
    # 关键：先等写线程把队列排空并退出，再关闭写端
    for t in _rec_writer_threads:
        t.join(timeout=5)
    _rec_writer_threads = []
    # 队列已排空、写线程已退出；关闭写端 → ffmpeg 读到 EOF 正常收尾
    for fd in (_depth_w, _color_w):
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
    _depth_w = _color_w = None
    # 等待两个 ffmpeg 收尾（EOF 后正常 flush mkv）
    rcs = {}
    for name, proc in (("depth", _ffmpeg_depth), ("color", _ffmpeg_color)):
        if proc is not None:
            try:
                rcs[name] = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                rcs[name] = proc.wait(timeout=5)
    _ffmpeg_depth = _ffmpeg_color = None
    # 合并两个临时 mkv：彩色第一轨、深度第二轨（-c copy 无重编码，仅 remux）
    final = _rec_mkv
    merge_ok = False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", _color_mkv, "-i", _depth_mkv,
             "-map", "0:v:0", "-map", "1:v:0",
             "-c", "copy", "-f", "matroska", final],
            timeout=120, stderr=subprocess.DEVNULL)
        merge_ok = final and os.path.isfile(final) and os.path.getsize(final) > 0
    except Exception:
        merge_ok = False
    for tmp in (_color_mkv, _depth_mkv):
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    _color_mkv = _depth_mkv = None
    size = os.path.getsize(final) if final and os.path.isfile(final) else 0
    bad = [name for name, code in rcs.items() if code not in (0, None)]
    if bad:
        return True, {"ok": False, "err": f"ffmpeg 录制异常退出（{','.join(bad)}）",
                      "path": final, "size": size, "frames": _rec_frames}
    if not merge_ok:
        return True, {"ok": False, "err": "录制文件合并失败", "path": final,
                      "size": size, "frames": _rec_frames}
    return True, {"ok": True, "path": final, "size": size,
                  "frames": _rec_frames, "rc": rcs}


def handle(cmd):
    global _k4a, _config, _depth_size, _fps
    global _ffmpeg_depth, _ffmpeg_color, _depth_mkv, _color_mkv
    global _depth_w, _color_w, _rec_thread, _rec_stop_evt
    global _recording, _rec_mkv, _rec_frames, _latest_seq
    global _color_q, _depth_q, _rec_writer_threads
    op = cmd.get("cmd")
    with _lock:
        if op == "start":
            if _k4a is not None:
                return {"ok": True, "already": True}
            cfg = cmd.get("config") or {}
            _config = _map_config(cfg)
            _depth_size = _DEPTH_SIZES.get(cfg.get("depth_mode", "NFOV_UNBINNED"),
                                           "640x576")
            _fps = 15 if cfg.get("depth_mode") == "WFOV_UNBINNED" else 30
            k4a = PyK4A(config=_config)
            try:
                k4a.start()  # 卡死时由主进程 kill 本进程
            except Exception:
                # start 半初始化：显式 SDK stop 清理设备状态，避免残留占用
                try:
                    k4a.stop()
                except Exception:
                    pass
                raise
            _k4a = k4a
            return {"ok": True}
        if op == "stop":
            if _recording:
                _stop_recording()
            if _k4a is not None:
                try:
                    _k4a.stop()
                except Exception:
                    pass
                _k4a = None
            return {"ok": True}
        if op == "ping":
            return {"ok": True, "alive": True, "active": _k4a is not None}
        if op == "get_frame":
            if _k4a is None:
                return {"ok": False, "err": "相机未启动"}
            if _recording:
                # 录制中：读录制线程扇出的最新帧（不抢设备）
                with _color_lock:
                    if _latest_color is None:
                        return {"ok": True, "seq": _latest_seq, "frame_len": 0}
                    data, seq = _latest_color, _latest_seq
                return {"ok": True, "seq": seq, "frame_len": len(data), "frame": data}
            cap = _k4a.get_capture(timeout=1000)
            if cap is None or cap.color is None or len(cap.color) == 0:
                return {"ok": True, "seq": _latest_seq, "frame_len": 0}
            data = cap.color.tobytes()
            with _color_lock:
                _latest_seq += 1
                seq = _latest_seq
            return {"ok": True, "seq": seq, "frame_len": len(data), "frame": data}
        if op == "start_record":
            if _recording:
                return {"ok": False, "err": "已有录制进行中"}
            if _rec_thread is not None and _rec_thread.is_alive():
                # 上次录制线程仍卡在 get_capture（usbip C 层阻塞），
                # 此时并发 get_capture 会损坏 pyk4a 会话，需先 stop 相机重建
                return {"ok": False, "err": "上次录制线程未退出（相机采集卡住），请停止相机后重试"}
            if _k4a is None:
                return {"ok": False, "err": "相机未启动"}
            mkv = cmd.get("path")
            if not mkv:
                return {"ok": False, "err": "缺少录制路径"}
            try:
                (depth_proc, color_proc, depth_w, color_w,
                 depth_mkv, color_mkv) = _spawn_ffmpeg(mkv)
            except Exception as e:
                return {"ok": False,
                        "err": f"启动 ffmpeg 失败：{type(e).__name__}: {e}"}
            _ffmpeg_depth = depth_proc
            _ffmpeg_color = color_proc
            _depth_mkv = depth_mkv
            _color_mkv = color_mkv
            _depth_w, _color_w = depth_w, color_w
            _rec_mkv = mkv
            _rec_frames = 0
            _recording = True
            _rec_stop_evt = threading.Event()
            # 双写线程 + 有界队列：采集线程只入队，写线程各自阻塞在自己 pipe 上。
            # 单线程顺序写两个 pipe 会死锁：彩色帧(>64KB 管道缓冲)阻塞等 ffmpeg 读，
            # 而 ffmpeg 正阻塞读深度 pipe，互相等待。写线程隔离后背压经队列传回采集线程。
            _color_q = _queue.Queue(maxsize=60)   # ~2 秒缓冲，ffmpeg 挂时队列满→采集线程超时退出
            _depth_q = _queue.Queue(maxsize=60)
            _rec_writer_threads = [
                threading.Thread(target=_writer_loop, args=(_color_w, _color_q), daemon=True),
                threading.Thread(target=_writer_loop, args=(_depth_w, _depth_q), daemon=True),
            ]
            for t in _rec_writer_threads:
                t.start()
            _rec_thread = threading.Thread(target=_record_loop, daemon=True)
            _rec_thread.start()
            return {"ok": True, "path": mkv}
        if op == "stop_record":
            _, resp = _stop_recording()
            return resp
        return {"ok": False, "err": f"未知指令：{op}"}


def main():
    # 协议通道走主进程传入的管道（fd 编号经环境变量传递）：pyk4a 的 C 层日志
    # （如 nvram retrying）直接打印到 stdout，stdout 不能承载协议。
    # 注意：os.pipe() 的 fd 编号不固定，不能假设是 3，必须读环境变量。
    try:
        fd = int(os.environ.get("ORBBEC_CAMERA_PROC_FD", "3"))
        out = os.fdopen(fd, "wb")
    except Exception:
        out = sys.stdout.buffer  # 手动运行（无管道）时回退 stdout
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line.decode("utf-8"))
        except Exception:
            out.write(b'{"ok":false,"err":"bad json"}\n')
            out.flush()
            continue
        try:
            resp = handle(cmd)
        except Exception as e:
            resp = {"ok": False, "err": f"{type(e).__name__}: {e}"}
        frame = resp.pop("frame", None) or b""
        out.write(json.dumps(resp).encode("utf-8") + b"\n" + frame)
        out.flush()


if __name__ == "__main__":
    main()
