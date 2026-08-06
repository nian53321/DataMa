# -*- coding: utf-8 -*-
"""Orbbec Femto Bolt 深度摄像头接口（容器内 pyk4a 直连 USB）

职责边界：
- 深度摄像头采集/录制全部在容器内完成（pyk4a SDK + ffmpeg），
  仅支持容器内模式，无宿主机降级（不再代理宿主机 exe 录制服务）。
- 预览视频由本后端在录制结束后用容器内 ffmpeg 从 mkv 提取（H.264，浏览器兼容）

路由：
- GET  /api/orbbec/status           设备状态（容器内模式，无设备时 available=false）
- POST /api/orbbec/preview/start     启动容器内实时预览（与录制互斥）
- POST /api/orbbec/preview/stop      停止实时预览
- GET  /api/orbbec/preview/stream    实时预览 MJPEG 流（<img> 直接播放）
- POST /api/orbbec/record/start     启动录制（.mkv 只留彩色+深度两轨）
- POST /api/orbbec/record/stop      停止录制，后端异步生成预览
- GET  /api/orbbec/record/status    查询最近一次录制的后处理状态
- GET  /api/orbbec/preview          返回预览 mp4（读容器内录制目录）
- POST /api/orbbec/upload           将已录制 mkv 入库为数据资产（复用 AssetService）
"""
import json
import logging
import os
import select
import subprocess
import sys
import threading
import time
from datetime import datetime

from flask import request, Response
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.api import orbbec_bp
from app.extensions import db
from app.models import Role
from app.services import AssetService
from app.utils.response import success, fail
from app.utils.decorators import role_required
from app.utils.audit import current_role
from app.utils.media_auth import media_auth_required

logger = logging.getLogger(__name__)

# ==================== 容器内录制 ====================
# 容器内 pyk4a 直接录制深度摄像头（需 usbipd 透传 USB，见 ensure_usb_nodes.py）。
try:
    from pyk4a import (Config, ColorResolution, DepthMode, FPS, ImageFormat,
                       PyK4A, PyK4ARecord, connected_device_count)
    _PY4A_OK = True
except Exception:
    _PY4A_OK = False

# 子进程探测代码：usbip 透传下 pyk4a 枚举可能卡住/崩溃（C 调用无法 Python 超时），
# 所有设备枚举走独立子进程 + 5s 超时兜底，绝不阻塞 gunicorn 线程。
_PROBE_CODE = (
    "from pyk4a import connected_device_count;"
    "print(connected_device_count())"
)


def _probe_devices():
    """子进程探测 Orbbec 设备数量（5s 超时），失败/超时返回 0"""
    if not _PY4A_OK:
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PROBE_CODE],
            capture_output=True, timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line)
        return 0
    except Exception:
        return 0


class _ContainerRecorder:
    """深度摄像头检测（sysfs 优先，绝不打开设备防 SIGSEGV 崩溃）"""

    @property
    def available(self):
        """容器内是否检测到深度摄像头

        先按 sysfs 判断设备是否存在:设备缺失时直接返回 False,绝不初始化 SDK——
        实测设备节点缺失时 SDK libusb_init 失败会直接 SIGSEGV 崩溃(无法用
        try/except 捕获)。设备存在但节点可能因重连/重 attach 丢失或过期,
        先调用 ensure_usb_nodes 修复节点再枚举（connected_device_count 只枚举
        不打开设备，安全）。
        """
        if not _PY4A_OK:
            return False
        sysfs_ok = False
        try:
            import glob
            for dev in glob.glob("/sys/bus/usb/devices/*/"):
                try:
                    vid = open(os.path.join(dev, "idVendor")).read().strip()
                except OSError:
                    continue
                if vid.lower() == "2bc5":
                    sysfs_ok = True
                    break
        except Exception:
            pass
        if not sysfs_ok:
            return False
        try:
            from ensure_usb_nodes import ensure_usb_nodes
            ensure_usb_nodes()
        except Exception:
            pass
        # 子进程探测（5s 超时），避免 pyk4a 枚举卡死阻塞 gunicorn 线程
        return _probe_devices() > 0

    @property
    def serial(self):
        """从 sysfs 读取序列号（不打开设备——实测 usbip 环境下连续开关设备后
        二次打开深度流会 nvram 读取失败无限重试，故 status 探测绝不打开设备）"""
        try:
            import glob
            for dev in glob.glob("/sys/bus/usb/devices/*/"):
                try:
                    vid = open(os.path.join(dev, "idVendor")).read().strip()
                except OSError:
                    continue
                if vid.lower() == "2bc5":
                    try:
                        return open(os.path.join(dev, "serial")).read().strip()
                    except OSError:
                        pass
        except Exception:
            pass
        return None


_container_rec = _ContainerRecorder()


class _CameraProc:
    """相机服务子进程封装：Popen + 管道行协议 + select 超时

    pyk4a 的 C 调用（start/get_capture/stop/录制）全部在独立子进程
    （scripts/orbbec_camera_proc.py）中执行。usbip 透传下这些调用可能无限
    卡死且持有 GIL——主进程内任何 Python 线程都无法调度、超时无效，表现为
    整个后端冻结（health 都无响应）。子进程方案下主进程读超时直接 kill
    子进程，gunicorn 永不冻结，预览失败也能及时返回错误。

    协议：stdin 每行一条 JSON 指令；stdout 每行一条 JSON 响应，
    响应含 frame_len 时其后紧跟原始帧数据。
    """

    _PREVIEW_INTERVAL = 0.1  # 预览节流 ~10fps（MJPG JPEG 帧，控制带宽与解码压力）
    # orbbec.py 位于 back/app/api/，相机服务脚本位于 back/scripts/
    _SCRIPT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "orbbec_camera_proc.py",
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._out = None
        self._active = False
        self._started_at = None
        self._recording = False
        self._color_res = None  # 当前相机会话的彩色分辨率（start 时设置）

    # ---------- 会话 ----------
    @property
    def active(self):
        proc = self._proc
        return proc is not None and proc.poll() is None and self._active

    @property
    def recording(self):
        return self._recording

    # ---------- 子进程控制 ----------
    def _ensure_proc(self):
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            # 协议通道走独立管道：pyk4a C 层日志会打印到 stdout，stdout 不能承载协议。
            # 注意：os.pipe() 返回的 fd 编号不固定，pass_fds 只保证继承不保证编号为 3，
            # 故用环境变量把 fd 编号传给子进程（子进程 os.fdopen(该编号)）。
            r_fd, w_fd = os.pipe()
            env = dict(os.environ)
            env["ORBBEC_CAMERA_PROC_FD"] = str(w_fd)
            self._proc = subprocess.Popen(
                [sys.executable, self._SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,  # C 层 retrying 刷屏日志不落容器
                stderr=subprocess.DEVNULL,
                env=env,
                pass_fds=(w_fd,),
                bufsize=0,
            )
            os.close(w_fd)
            self._out = os.fdopen(r_fd, "rb", buffering=0)
            self._active = False
            return True
        except Exception:
            self._proc = None
            self._out = None
            return False

    def _kill(self):
        """终止相机服务子进程：优先 SDK stop（发 stop 指令等 3s），不响应才 kill

        SDK stop（k4a.stop()）干净释放设备，直接 kill 会破坏设备深度模块状态。
        但 SDK start 卡死时 C 层持有 GIL，子进程无法响应任何指令，只能 kill——
        这是子进程隔离架构的最后兜底。
        """
        proc, self._proc = self._proc, None
        out, self._out = self._out, None
        self._active = False
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write((json.dumps({"cmd": "stop"}) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)  # SDK stop 生效则进程自然退出
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
        if out is not None:
            try:
                out.close()
            except Exception:
                pass

    def _read_exact(self, f, n, timeout):
        buf = b""
        deadline = time.time() + timeout
        while len(buf) < n:
            remain = deadline - time.time()
            if remain <= 0:
                return None
            try:
                r, _, _ = select.select([f.fileno()], [], [], min(remain, 1.0))
                if not r:
                    continue
                chunk = f.read(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            except Exception:
                return None
        return buf

    def _send(self, cmd, timeout=8, frame_timeout=5):
        """发送指令并读响应；返回 (resp_dict, err, frame_bytes)"""
        with self._lock:
            if not self._ensure_proc():
                return None, "相机服务进程启动失败", None
            proc = self._proc
            out = self._out
            try:
                proc.stdin.write((json.dumps(cmd) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except Exception as e:
                self._kill()
                return None, f"相机服务通信失败：{e}", None
            line = None
            try:
                r, _, _ = select.select([out.fileno()], [], [], timeout)
                if r:
                    line = out.readline()
            except Exception:
                pass
            if not line:
                self._kill()
                return None, "相机服务无响应（设备状态异常导致启动卡死，已终止服务）", None
            try:
                resp = json.loads(line.decode("utf-8"))
            except Exception:
                self._kill()
                return None, "相机服务响应异常", None
            if not resp.get("ok"):
                return None, resp.get("err", "相机服务错误"), None
            flen = int(resp.get("frame_len", 0) or 0)
            frame = None
            if flen > 0:
                frame = self._read_exact(out, flen, frame_timeout)
                if frame is None:
                    self._kill()
                    return None, "相机服务帧读取超时", None
            return resp, None, frame

    def start(self, color_res="720P"):
        """启动相机（k4a.start() 在子进程内执行，卡死由 _send 超时 kill 兜底）

        会话分辨率可配：默认 720P（预览场景 usbip 透传带宽有限，720P MJPG
        稳定）；录制接口请求 1080P 时会先重启会话切换分辨率（录制写本地
        mkv 管道，不依赖网络带宽，1080P 无压力）。

        usbip 透传下深度传感器 nvram 读取**间歇性失败**（实测有时 2.5s 成功，
        有时无限重试卡死）。失败后 kill 已释放设备，等待数秒让设备复位，
        重新启动子进程重试——多次尝试以提高命中"成功窗口"的概率。
        """
        if not _container_rec.available:
            return None, "未检测到深度摄像头"
        last_err = None
        for attempt in range(3):
            resp, err, _ = self._send({"cmd": "start", "config": {
                "color_resolution": color_res,
                "color_format": "MJPG",
                "depth_mode": "NFOV_UNBINNED",
                "fps": 30,
            }}, timeout=10)
            if not err:
                with self._lock:
                    self._active = True
                    self._started_at = time.time()
                    self._color_res = color_res
                return True, None
            last_err = err
            if attempt < 2:
                # 失败后子进程已被 kill、设备已释放；等待数秒让设备复位再重试
                time.sleep(4)
        return None, f"{last_err}（已自动重试 3 次仍失败）。usbip 透传下深度传感器 nvram 读取不稳定，请运行 ./reset_orbbec_firmware.ps1（管理员）或物理拔插摄像头后重试"

    def stop(self, force=False):
        """停止相机：发 SDK stop 指令（子进程内 k4a.stop() 干净释放设备）

        绝不直接 kill 子进程——实测 kill 会破坏设备深度模块状态，导致下次
        k4a.start() nvram 读取持续卡死、必须重新连接设备才能恢复；而 SDK
        stop 干净释放后，start 可反复成功（2~3s/次）。

        force=True：录制结束后的确定性关闭，跳过 3s 竞态保护——录制会话必已
        启动超过竞态窗口，必须保证释放设备，避免 usbip 透传下相机长期占用。
        """
        with self._lock:
            # 竞态保护：对话框关闭的 stop 与快速重开的 start 两个请求并发时，
            # 迟到的旧 stop 会误关刚启动的新会话。会话刚启动(<3s)即收到 stop，
            # 视为旧请求迟到，忽略本次 stop。
            if not force and self._started_at and time.time() - self._started_at < 3:
                return
            self._started_at = None
        # 发 SDK stop（子进程内 k4a.stop()）；卡死时 _send 超时兜底 kill
        self._send({"cmd": "stop"}, timeout=8)
        with self._lock:
            self._active = False

    # ---------- 预览 ----------
    def get_color_frame(self):
        """取一帧彩色 MJPG 字节；返回 (seq, bytes)；失败返回 (None, None)"""
        resp, err, frame = self._send({"cmd": "get_frame"}, timeout=8, frame_timeout=5)
        if err or frame is None:
            return None, None
        return resp.get("seq", 0), frame

    # ---------- 录制 ----------
    def start_record(self, mkv_path):
        resp, err, _ = self._send({"cmd": "start_record", "path": mkv_path}, timeout=10)
        if err:
            return None, err
        self._recording = True
        return resp.get("path"), None

    def stop_record(self):
        resp, err, _ = self._send({"cmd": "stop_record"}, timeout=15)
        if err:
            return None, err
        self._recording = False
        mkv = resp.get("path")
        if not mkv or not os.path.isfile(mkv) or os.path.getsize(mkv) < 1024 * 1024:
            return None, "录制文件过小或未生成，请重试"
        return mkv, None


_camera = _CameraProc()

# 容器内 pyk4a 录制目录（容器本地盘，写入快；预览/入库均在此目录内处理）
ORBBEC_REC_DIR = "/app/orbbec_recordings"

# 最近一次录制的后处理状态（预览由后端 ffmpeg 生成）
_rec_state = {
    "mkv": None,          # 容器内 mkv 路径
    "preview_ready": False,
    "preview_rel": None,
    "meta": {},
    "done": False,
}
_rec_state_lock = threading.Lock()


def _strip_ir_track(raw_mkv):
    """如文件含 IR 轨（视频轨 >2），用 ffmpeg 无损剥离只留 彩色+深度 两轨

    容器内录制为 ffmpeg 实时编码（彩色 MJPG + 深度 ffv1 无损），产出文件
    只有两轨，直接返回原文件；仅历史/其他来源的多轨文件才需 remux 剥离。
    剥离失败时回退原文件。
    """
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=index", "-of", "csv=p=0", raw_mkv],
            capture_output=True, text=True, timeout=60)
        vids = [l for l in probe.stdout.splitlines() if l.strip()]
        if len(vids) <= 2:
            return raw_mkv  # 已是 彩色+深度 两轨（容器内新格式），无需剥离
        final = raw_mkv[:-4] + "_cd.mkv"
        cmd = ["ffmpeg", "-y", "-i", raw_mkv, "-map", "0:0", "-map", "0:1",
               "-c", "copy",
               # ffmpeg 7 的 matroska 封装器默认禁止 rawvideo 轨(深度/IR 为 gray16be)，
               # 需显式允许 VFW 模式才能把深度轨封装进 mkv
               "-allow_raw_vfw", "1",
               final]
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode == 0 and os.path.isfile(final) and os.path.getsize(final) > 1024 * 1024:
            os.remove(raw_mkv)
            return final
    except Exception:
        pass
    return raw_mkv


def _trim_mkv(src_path, start_sec, end_sec):
    """用 ffmpeg 对 mkv 双轨（彩色+深度）精确无损裁剪，返回裁剪后路径

    前端裁剪（applyOrbbecTrim）只生成本地预览 blob；上传入库前需按裁剪
    区间对原始 mkv 的彩色(MJPG)+深度(FFV1)两轨一起裁剪，保证双轨同步。
    两轨均为帧内编码，输出端 -ss/-to + -c copy 可精确且无损（深度不重编码）。
    失败返回 None。
    """
    if start_sec < 0 or end_sec <= start_sec:
        return None
    stem = src_path[:-4] if src_path.lower().endswith(".mkv") else src_path
    out_path = f"{stem}_trimmed.mkv"
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-map", "0:v:0", "-map", "0:v:1",   # 只保留 彩色+深度 两轨（同步裁剪）
        "-ss", f"{start_sec:.3f}", "-to", f"{end_sec:.3f}",
        "-c", "copy",                        # 流复制，无损
        "-allow_raw_vfw", "1",               # ffmpeg 7 matroska 封装深度轨需允许 VFW
        "-avoid_negative_ts", "make_zero",
        out_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except Exception:
        pass
    if os.path.isfile(out_path):
        try:
            os.remove(out_path)
        except OSError:
            pass
    return None


def _post_process(mkv_path, mkv_rel, fps=30):
    """录制后处理（后台线程）：1) 剥离 IR 轨只留彩色+深度 2) 生成预览"""
    stripped = _strip_ir_track(mkv_path)
    if stripped != mkv_path:
        with _rec_state_lock:
            _rec_state["mkv"] = stripped
            _rec_state["meta"]["streams"] = ["COLOR", "DEPTH"]
        mkv_path = stripped
        mkv_rel = os.path.relpath(stripped, ORBBEC_REC_DIR).replace(os.sep, "/")
    _transcode_preview(mkv_path, mkv_rel, fps)


def _transcode_preview(mkv_path, mkv_rel, fps=30):
    """后台线程：用容器内 ffmpeg 把 mkv 彩色轨转成 H.264 mp4 预览（浏览器兼容）

    k4arecorder 写的 mkv 彩色轨 PTS 时间戳异常，ffmpeg 默认会丢弃绝大部分帧
    （曾实测 182 帧只剩 2 帧，导致预览几乎为空、进度条卡在开始处）。
    用 setpts=N/(fps*TB) 强制按帧序号重置时间戳，保证全部帧参与转码。
    预览为完整时长（不截断），转码后用 ffprobe 读取真实时长写入 meta，
    供前端显示与视频实际时间一致（不能用 start/end 时间戳差值——那包含
    k4arecorder 启动/保存的开销，会导致显示时间虚长）。
    """
    preview_path = os.path.join(os.path.dirname(mkv_path), "preview.mp4")
    preview_rel = f"{os.path.basename(os.path.dirname(mkv_path))}/preview.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", mkv_path,
        "-map", "0:v:0",              # 第一个视频轨（彩色）
        "-vf", f"setpts=N/({int(fps)}*TB)",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-g", "15", "-keyint_min", "15",   # 每 0.5s 一个关键帧，浏览器 seek 定位精确（裁剪用）
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",    # moov 前置，浏览器快速起播
        "-an",
        preview_path,
    ]
    duration_sec = None
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        ok = proc.returncode == 0 and os.path.isfile(preview_path) and os.path.getsize(preview_path) > 0
        if ok:
            # 读取预览视频真实时长（秒），供前端展示
            fp = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", preview_path],
                capture_output=True, timeout=30,
            )
            try:
                duration_sec = round(float(fp.stdout.strip()))
            except Exception:
                duration_sec = None
    except Exception:
        ok = False
    with _rec_state_lock:
        _rec_state["preview_ready"] = ok
        _rec_state["preview_rel"] = preview_rel if ok else None
        if ok and duration_sec:
            _rec_state["meta"]["duration_sec"] = duration_sec
        _rec_state["done"] = True


# ==================== 路由 ====================

@orbbec_bp.route("/status", methods=["GET"])
@jwt_required()
def status():
    """检测深度摄像头状态（容器内模式：sysfs 确认 + 枚举）"""
    if _container_rec.available:
        return success({
            "available": True,
            "count": 1,
            "serial": _container_rec.serial,
            "recording": _camera.recording,
            "previewing": _camera.active,
            "color_res": _camera._color_res,   # 当前相机会话的彩色分辨率（720P/1080P，未启动为 None）
            "mode": "container",
        })
    return success({
        "available": False,
        "count": 0,
        "serial": None,
        "mode": "none",
        "message": "未检测到深度摄像头（请确认 usbipd 已透传 USB 到容器）",
    })


# ==================== 录制 ====================

@orbbec_bp.route("/record/start", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_start():
    """启动录制（容器内 pyk4a 直连）

    录制与实时预览共用同一相机会话，相机已打开时无需重启（秒开），
    未打开则先启动。录制分辨率由请求 color_res 决定（默认 1080P）；
    若已打开的预览会话分辨率不同（预览默认 720P），会先重启会话切换
    分辨率后再录制。
    """
    try:
        data = request.get_json(silent=True) or {}
        # 相机已启动（active）时优先走容器内录制：此时设备已被容器内 SDK 独占，
        # _container_rec.available 的枚举探测反而可能因设备占用而失败。
        if not (_camera.active or _container_rec.available):
            return fail("未检测到深度摄像头（请确认 usbipd 已透传 USB 到容器）", 503)
        if _camera.recording:
            return fail("已有录制进行中", 409)
        # 录制目标分辨率（前端默认 1080P）；与当前会话不一致时重启切换
        want_res = data.get("color_res", "1080P")
        if not _camera.active:
            # 运行中设备可能重连/重 attach 导致设备号变化，先修复 USB 节点
            try:
                from ensure_usb_nodes import ensure_usb_nodes
                ensure_usb_nodes()
            except Exception:
                pass
            _, err = _camera.start(want_res)
            if err:
                return fail(err, 409)
        elif _camera._color_res != want_res:
            # 预览会话分辨率与录制请求不符：停止会话后按录制分辨率重启。
            # usbip 透传下 nvram 读取间歇性卡死，1080P 重启可能失败——
            # 失败时尝试恢复原 720P 预览会话，避免预览流永久断开（录制失败
            # 不应连带杀掉预览）。
            _camera.stop(force=True)
            _, err = _camera.start(want_res)
            if err:
                _camera.start("720P")  # 尽力恢复预览会话（失败则等待设备复位）
                return fail(err, 409)
        # 主进程创建录制目录，子进程写 mkv
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(ORBBEC_REC_DIR, f"orbbec_{ts}")
        os.makedirs(out_dir, exist_ok=True)
        mkv_path = os.path.join(out_dir, f"orbbec_{ts}.mkv")
        out_path, err = _camera.start_record(mkv_path)
        if err:
            return fail(err, 409)
        with _rec_state_lock:
            _rec_state.update({
                "mkv": None,
                "preview_ready": False,
                "preview_rel": None,
                "done": False,
                "root": ORBBEC_REC_DIR,
                "meta": {
                    "format": "mkv",
                    # 录制只保留彩色+深度两种
                    "streams": ["COLOR", "DEPTH"],
                    "color_resolution": data.get("color_res", "1080P"),
                    "depth_mode": data.get("depth_mode", "NFOV_UNBINNED"),
                    "fps": int(data.get("fps", 30)),
                    "device_serial": _container_rec.serial,
                    "start_time": datetime.now().isoformat(timespec="seconds"),
                    "mode": "container",
                },
            })
        return success({"record_id": "default", "path": out_dir}, message="录制已启动")
    except Exception:
        logger.exception("Orbbec 启动录制失败")
        return fail("启动录制失败，请检查相机状态后重试", 503)


@orbbec_bp.route("/record/stop", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_stop():
    """停止录制（立即返回；预览由后端 ffmpeg 后台转码，前端轮询 /record/status）

    停止录制后自动关闭相机会话（SDK k4a.stop()），释放 USB 设备；
    前端"重新采集"时 record/start 会检测会话未启动并自动重新打开。
    """
    try:
        if not _camera.recording:
            return fail("当前没有进行中的录制", 409)
        mkv_path, err = _camera.stop_record()
        if err:
            return fail(err, 409)
        # 录制结束自动关闭相机：usbip 透传场景相机长期占用会拖累后续
        # 重连/重 attach，录制完成即干净释放（SDK stop，非 kill 子进程）。
        _camera.stop(force=True)
        mkv_rel = os.path.relpath(mkv_path, ORBBEC_REC_DIR).replace(os.sep, "/")
        with _rec_state_lock:
            _rec_state["mkv"] = mkv_path
            _rec_state["meta"]["end_time"] = datetime.now().isoformat(timespec="seconds")
            _fps = int(_rec_state["meta"].get("fps", 30))
        # 后台：剥离 IR 轨(只留彩色+深度) → 生成预览
        threading.Thread(target=_post_process, args=(mkv_path, mkv_rel, _fps), daemon=True).start()
        return success({"path": mkv_path}, message="录制已停止")
    except Exception:
        logger.exception("Orbbec 停止录制失败")
        return fail("停止录制失败，请重试", 503)


@orbbec_bp.route("/record/status", methods=["GET"])
@jwt_required()
def record_status():
    """查询最近一次录制的后处理状态（后端维护：预览是否已由 ffmpeg 生成）"""
    with _rec_state_lock:
        st = dict(_rec_state)
        if st.get("preview_rel"):
            root = st.get("root") or ORBBEC_REC_DIR
            st["preview"] = os.path.join(root, st["preview_rel"])
    return success(st)


# ==================== 实时预览 ====================

@orbbec_bp.route("/preview/start", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def preview_start():
    """启动容器内实时预览（相机会话；与录制共用同一会话，录制中画面不断流）

    k4a.start() 在子进程内执行，卡死时主进程超时 kill 子进程并返回错误，
    不会冻结整个后端。
    """
    ok, err = _camera.start()
    if err:
        return fail(err, 409)
    return success({"stream": "/api/orbbec/preview/stream"}, message="实时预览已启动")


@orbbec_bp.route("/preview/stop", methods=["POST"])
@jwt_required()
def preview_stop():
    """停止实时预览并关闭相机会话（录制中不允许，避免中断录制）"""
    if _camera.recording:
        return fail("录制进行中，不能停止实时预览", 409)
    _camera.stop()
    return success(message="实时预览已停止")


@orbbec_bp.route("/preview/stream", methods=["GET"])
@media_auth_required("orbbec_stream")
def preview_stream():
    """实时预览 MJPEG 流（multipart/x-mixed-replace，浏览器 <img> 直接播放）

    鉴权：优先 ?media_token=<短期签名>，兼容 JWT（header / access_token query）。
    录制中从录制线程扇出的彩色帧读取（录制不丢帧），非录制时直接取设备帧，
    均节流 ~10fps。客户端断开即结束。
    """
    if not _camera.active:
        return fail("预览未启动", 409)

    def gen():
        last_seq = -1
        last_ts = 0.0
        empty_streak = 0  # 连续无帧计数（容忍相机启动首帧延迟与录制瞬态）
        session_closed_since = None  # 会话重启窗口起始（录制切换分辨率时 stop+start）
        while True:
            now = time.time()
            if not _camera.active:
                # 相机会话可能正在重启：录制请求 1080P 而预览会话为 720P 时，
                # record/start 会先 stop 再 start 切换分辨率（耗时数秒）。此期间
                # 保持流连接等待恢复，画面短暂冻结后自动续播，避免 <img> 断流
                # 冻结在最后一帧（浏览器不会自动重连）。
                # 客户端主动停止/关闭弹窗时生成器会被关闭（GeneratorExit），
                # 此处超时退出仅作兜底。
                if session_closed_since is None:
                    session_closed_since = now
                elif now - session_closed_since > 30:
                    break
                time.sleep(0.2)
                continue
            if session_closed_since is not None:
                # 会话已恢复：可能已切换分辨率（如 720P→1080P），新会话 seq 从
                # 0 重新计数，重置序号避免 seq 回绕导致误判无新帧/跳帧。
                session_closed_since = None
                last_seq = -1
                last_ts = 0.0
                empty_streak = 0
            if now - last_ts < _camera._PREVIEW_INTERVAL:
                time.sleep(0.03)
                continue
            seq, frame = _camera.get_color_frame()
            if frame is None:
                # 相机刚启动时首帧可能延迟（深度引擎初始化/首帧未就绪），
                # 若立即 break 会导致预览流建立即断（前端 <img> 空白）。
                # 连续 ~5s 无帧才判定异常断开。
                empty_streak += 1
                if empty_streak > 50:
                    break
                time.sleep(0.1)
                continue
            empty_streak = 0
            if seq == last_seq:
                continue  # 无新帧（录制中同一帧），避免重复下发
            last_seq, last_ts = seq, now
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(frame)).encode()
                   + b"\r\n\r\n" + frame + b"\r\n")

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@orbbec_bp.route("/preview", methods=["GET"])
@media_auth_required("orbbec_preview", resource_key="path")
def preview():
    """返回录制预览 mp4（从容器内录制目录读取）

    鉴权：优先 ?media_token=<短期签名>（绑定 path），兼容 JWT。
    """
    rel = (request.args.get("path") or "").lstrip("/\\")
    if not rel or ".." in rel.replace("\\", "/").split("/"):
        return fail("无效路径", 400)
    with _rec_state_lock:
        root = _rec_state.get("root") or ORBBEC_REC_DIR
    fp = os.path.normpath(os.path.join(root, rel))
    if not fp.startswith(root) or not os.path.isfile(fp):
        return fail("预览文件不存在", 404)
    from flask import send_file
    return send_file(fp, mimetype="video/mp4", as_attachment=False, download_name="preview.mp4",
                     conditional=True)


@orbbec_bp.route("/upload", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def upload_recorded():
    """将已录制的 .mkv(彩色+深度两轨)入库为数据资产

    请求体 JSON：
      path: str           录制 mkv 路径（容器内路径，来自 /record/stop 或 /record/status）
      subject_id: int     受试者 ID
      video_type: str     face/body/gait（与现有视频采集一致）
      meta: dict          深度元数据（写入 asset metadata.orbbec）
      trim_start: float   裁剪起始秒（可选；与 trim_end 一起对 mkv 彩色+深度双轨同步裁剪）
      trim_end: float     裁剪结束秒（可选）
    """
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    try:
        subject_id = int(data.get("subject_id"))
    except (TypeError, ValueError):
        return fail("subject_id 必须是数字", 422)
    video_type = (data.get("video_type") or "").strip() or None
    extra_meta = data.get("meta") or {}

    if not path or not subject_id:
        return fail("path 与 subject_id 必填", 422)
    if not os.path.isfile(path):
        # 录制停止后后台会剥离 IR 轨：原 mkv 被删除并生成 *_cd.mkv（最终文件）。
        # 前端若仍持有停止时的旧路径，这里自动回退到剥离后的文件。
        alt = path[:-4] + "_cd.mkv" if path.lower().endswith(".mkv") else ""
        if alt and os.path.isfile(alt):
            path = alt
        else:
            return fail(f"录制文件不存在：{path}", 404)
    # 路径穿越防护：仅允许容器录制目录内的文件入库（与 realsense 上传一致），
    # 防止伪造路径把容器内任意文件包装成数据资产
    _rec_root = os.path.realpath(ORBBEC_REC_DIR)
    _file_real = os.path.realpath(path)
    if not (_file_real == _rec_root or _file_real.startswith(_rec_root + os.sep)):
        return fail("无效的录制路径（仅允许容器录制目录内文件）", 422)

    # 裁剪：前端只生成本地预览 blob，这里按裁剪区间对原始 mkv 双轨（彩色+深度）
    # 一起做精确无损裁剪后再入库，确保入库文件即裁剪后的片段且深度同步。
    trim_start = data.get("trim_start")
    trim_end = data.get("trim_end")
    if trim_start is not None or trim_end is not None:
        try:
            t0 = float(trim_start or 0)
            t1 = float(trim_end or 0)
        except (TypeError, ValueError):
            return fail("裁剪时间参数无效", 422)
        if t1 - t0 < 0.1:
            return fail("裁剪区间过短", 422)
        trimmed = _trim_mkv(path, t0, t1)
        if not trimmed:
            return fail("视频裁剪失败", 500)
        path = trimmed

    # 用 AssetService.upload_asset 入库：需要 FileStorage 对象
    from werkzeug.datastructures import FileStorage
    filename = os.path.basename(path)
    is_mkv = filename.lower().endswith(".mkv")
    content_type = "video/x-matroska" if is_mkv else "application/octet-stream"
    try:
        with open(path, "rb") as f:
            file_storage = FileStorage(
                stream=f, filename=filename,
                content_type=content_type,
            )
            svc = AssetService(
                operator_id=int(get_jwt_identity()),
                operator_role=current_role(),
            )
            asset, is_dup = svc.upload_asset(
                subject_id=subject_id,
                data_type="video",
                file_storage=file_storage,
                layer="raw",
                video_type=video_type,
            )
        # 把深度元数据合并到资产 metadata
        if extra_meta:
            meta = asset.metadata_json or {}
            meta.update({"orbbec": extra_meta})
            asset.metadata_json = meta
            db.session.commit()
    except Exception:
        logger.exception("Orbbec 录制入库失败")
        return fail("入库失败，请稍后重试", 500)

    # 入库后清理容器内临时录制文件（数据湖已存副本）：
    # 1) 删除上传的 mkv；
    # 2) 若位于容器录制目录，连带删除该次录制的目录（含 preview.mp4 预览残片），
    #    避免容器磁盘被长期占用。
    # 清理前再次校验 realpath 前缀：只删除录制目录内文件，防止误删其他路径。
    try:
        _file_real = os.path.realpath(path)
        if _file_real == _rec_root or _file_real.startswith(_rec_root + os.sep):
            os.remove(path)
            parent = os.path.dirname(_file_real)
            if parent != _rec_root and parent.startswith(_rec_root + os.sep):
                import shutil
                shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass

    return success(asset.to_dict(), message="已入库", code=201)
