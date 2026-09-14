# -*- coding: utf-8 -*-
"""Intel RealSense D455f 深度摄像头接口（容器内 pyrealsense2 直连 USB）

pyrealsense2 是 C 库，崩溃会杀死 Flask worker，所有调用通过子进程
realsense_child.py 隔离。预览与录制共用一个常驻会话子进程（session 模式）：
pipeline 只打开一次，开始/停止录制只是向子进程发 JSON 命令（stdin），结果
经独立 fd 管道回传（RS_SESSION_FD），MJPEG 预览流持续走 stdout——录制切换
零进程重启、零设备释放等待。

录制产物（容器内 /app/realsense_recordings/<ts>/）：
  color.mp4          彩色视频（H.264，libx264 直接编码，浏览器可播）
  depth_raw.zst      原始深度序列（DZST v2，解码后为毫米深度值，入库供分析）
  frames.jsonl       逐帧同步记录（MP4/ZST 帧序号 + 硬件时间戳）
  calibration.json   相机标定
  meta.json          序列号 / 分辨率 / 帧时间戳 / 编码信息

路由：
- GET  /api/realsense/status           设备状态（无设备时 available=false）
- POST /api/realsense/preview/start    启动相机会话（常驻子进程）
- POST /api/realsense/preview/stop     停止相机会话
- GET  /api/realsense/preview/stream   MJPEG 流（<img> 直接播放，录制中不断流）
- POST /api/realsense/record/start     启动录制
- POST /api/realsense/record/pause     暂停录制（帧不入编码器，预览继续）
- POST /api/realsense/record/resume    恢复录制
- POST /api/realsense/record/stop      停止录制
- GET  /api/realsense/record/status    查询录制状态
- GET  /api/realsense/preview          返回录制预览 mp4
- POST /api/realsense/upload           将录制入库为数据资产（复用 AssetService）
- POST /api/realsense/passthrough      触发宿主机执行 reset_orbbec_usb.ps1（USB 透传自愈）
- GET  /api/realsense/passthrough/status  透传任务进度（宿主机 usb_agent 代理）
"""
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime

from flask import request, Response, send_file, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.api import realsense_bp
from app.extensions import db
from app.models import Role
from app.services import AssetService
from app.utils.response import success, fail
from app.utils.host_agent import agent_call as _agent_call

logger = logging.getLogger(__name__)
from app.utils.decorators import role_required
from app.utils.audit import current_role
from app.utils.media_auth import media_auth_required

# ==================== 常量 ====================
# 容器内录制目录（与 orbbec_recordings 相同：容器本地盘，写入快）
REALSENSE_REC_DIR = "/app/realsense_recordings"
# 子进程脚本路径：容器内为 /app/scripts/realsense_child.py（Dockerfile COPY back/* -> /app）
_BACK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHILD_SCRIPT = os.path.join(_BACK_DIR, "scripts", "realsense_child.py")
DEFAULT_FPS = 30


def _run_child(args, timeout=30, **kw):
    """运行 realsense_child.py 子进程，返回 (returncode, stdout_text)"""
    cmd = [sys.executable, CHILD_SCRIPT] + args
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


# ==================== 设备检测 ====================
def _sysfs_camera_present():
    """sysfs 是否存在 RealSense 设备（VID 0x8086），只读不打开设备"""
    import glob
    try:
        for dev in glob.glob("/sys/bus/usb/devices/*/"):
            try:
                vid = open(os.path.join(dev, "idVendor")).read().strip()
            except OSError:
                continue
            if vid.lower() == "8086":
                return True
    except Exception:
        pass
    return False


def _ensure_usb_nodes():
    """按当前 sysfs 重建 /dev/bus/usb 设备节点（与 orbbec.py 一致的自愈）

    透传脚本无人值守模式跳过容器重启：usbip 重新 attach 后 devnum 变化，
    容器内旧设备节点失效（无 udev 守护进程不会自动重建），librealsense 枚举
    不到设备——这里在检测/开会话前修复节点，透传完成后无需重启容器。
    """
    try:
        from ensure_usb_nodes import ensure_usb_nodes
        ensure_usb_nodes()
    except Exception:
        pass


def _probe():
    """检测 RealSense 设备，返回 (count, serial, detail)；失败返回 (0, None, {})

    detail 为子进程 DETAIL 行携带的设备信息（name / firmware_version / product_line），
    用于确认设备型号（如 D455F）与固件版本。
    """
    if not _sysfs_camera_present():
        return 0, None, {}
    _ensure_usb_nodes()
    count, serial, detail = 0, None, {}
    try:
        code, out = _run_child(["probe"], timeout=15)
        if code == 0:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("OK "):
                    parts = line.split()
                    if len(parts) >= 2:
                        count = int(parts[1])
                        serial = parts[2] if len(parts) > 2 else ""
                elif line.startswith("DETAIL "):
                    try:
                        detail = json.loads(line[len("DETAIL "):])
                    except Exception:
                        pass
    except Exception:
        pass
    return count, serial, detail


# ==================== 常驻会话子进程 ====================
# 单一子进程承载预览+录制（realsense_child.py session 模式）：
#   stdin  发 JSON 命令（start_rec/stop_rec/quit）
#   fd 管道（RS_SESSION_FD）回传 JSON 事件（ready/rec_started/rec_done/...）
#   stdout 持续输出 MJPEG 预览（preview/stream 直接透传，录制中不断流）
# 录制开始/停止只是向常驻进程发命令：无进程切换、无相机释放等待（旧双进程
# 架构每次切换需等旧进程退出+内核释放 USB，实测开始采集延迟 4~5s）
_proc_lock = threading.Lock()
_cmd_lock = threading.Lock()
_sess = None  # 当前会话：{"proc","out","ready_evt","rec_evt","rec_payload","fatal","info"}

# 录制状态（前端轮询 /record/status）
_rec_state = {
    "dir": None,
    "preview_ready": False,
    "preview_rel": None,
    "frames": 0,
    "meta": {},
    "done": False,
    "error": None,
    "recording": False,
    "paused": False,
}
_rec_state_lock = threading.Lock()
# 收尾中的目录集合：rec_done 事件前同目录 upload 拒绝（color.mp4 可能尚未写完）
_finishing_dirs = set()
_finishing_lock = threading.Lock()


def _session_reader(sess):
    """读取会话子进程协议事件行并更新状态；EOF 即子进程退出，清理会话"""
    global _sess
    out = sess["out"]
    try:
        while True:
            line = out.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except Exception:
                continue
            et = evt.get("evt")
            if et == "ready":
                sess["info"] = evt
                sess["ready_evt"].set()
            elif et == "fatal":
                sess["fatal"] = evt.get("err") or "相机启动失败"
                sess["ready_evt"].set()
            elif et == "rec_started":
                sess["rec_payload"] = evt
                sess["rec_evt"].set()
            elif et == "rec_start_err":
                sess["rec_payload"] = {"err": evt.get("err") or "录制启动失败"}
                sess["rec_evt"].set()
            elif et == "rec_paused":
                with _rec_state_lock:
                    _rec_state["paused"] = True
            elif et == "rec_resumed":
                with _rec_state_lock:
                    _rec_state["paused"] = False
            elif et == "rec_done":
                _on_rec_done(evt)
            elif et == "rec_done_err":
                _on_rec_done_err(evt)
    except Exception:
        pass
    finally:
        with _proc_lock:
            if _sess is sess:
                _sess = None
        try:
            out.close()
        except Exception:
            pass
        # 子进程退出（崩溃/被杀）：录制中标记中断，前端轮询可见错误
        with _rec_state_lock:
            if _rec_state.get("recording"):
                _rec_state.update({
                    "done": True, "recording": False,
                    "error": "相机会话进程退出，录制中断"})


def _ffprobe_duration(color_mp4):
    """读视频真实时长（秒）。不能用 start/end 时间戳差值——那包含录制启动与
    收尾（编码 flush）开销，会导致显示的"录制完成时长"虚长。"""
    try:
        fp = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", color_mp4],
            capture_output=True, timeout=30,
        )
        if fp.returncode == 0:
            return round(float(fp.stdout.strip()))
    except Exception:
        pass
    return None


def _on_rec_done(evt):
    """录制收尾成功：补齐帧数/预览/时长（rec_done 到达时文件均已写完）"""
    out_dir = evt.get("dir") or ""
    frames = int(evt.get("frames") or 0)
    color_mp4 = os.path.join(out_dir, "color.mp4")
    color_rel = os.path.relpath(color_mp4, REALSENSE_REC_DIR).replace(os.sep, "/")
    duration_sec = _ffprobe_duration(color_mp4)
    with _rec_state_lock:
        meta = dict(_rec_state["meta"], **{
            "end_time": datetime.now().isoformat(timespec="seconds"),
            "frame_count": frames})
        if duration_sec:
            meta["duration_sec"] = duration_sec
        _rec_state.update({
            "dir": out_dir,
            "preview_ready": True,
            "preview_rel": color_rel,
            "frames": frames,
            "done": True,
            "error": None,
            "recording": False,
            "paused": False,
            "meta": meta,
        })
    with _finishing_lock:
        _finishing_dirs.discard(out_dir)


def _on_rec_done_err(evt):
    out_dir = evt.get("dir") or ""
    with _rec_state_lock:
        _rec_state.update({
            "done": True,
            "recording": False,
            "paused": False,
            "error": evt.get("err") or "录制收尾失败",
        })
    with _finishing_lock:
        _finishing_dirs.discard(out_dir)


def _ensure_session(timeout=25):
    """确保常驻会话子进程在运行，返回 (sess, err)"""
    global _sess
    with _proc_lock:
        sess = _sess
        if sess is not None and sess["proc"].poll() is None:
            return sess, ""
    # 透传自愈后节点可能尚未修复（用户未触发状态检测直接开预览）：会话子进程
    # 启动即打开设备，节点失效会启动失败，先按 sysfs 重建
    _ensure_usb_nodes()
    # 协议通道走独立 fd 管道：stdout 承载 MJPEG 帧流，不能混入协议行
    # （fd 编号不固定，pass_fds 只保证继承，编号经环境变量告知子进程）
    r_fd, w_fd = os.pipe()
    env = dict(os.environ)
    env["RS_SESSION_FD"] = str(w_fd)
    # stderr 落盘（循环覆盖）：librealsense C 库崩溃/帧超时日志原被 DEVNULL
    # 吞掉，gunicorn 环境下曾出现 ready 后 0 帧，需要现场证据
    err_log = open("/tmp/rs_session_err.log", "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, CHILD_SCRIPT, "session", str(DEFAULT_FPS)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=err_log, env=env, pass_fds=(w_fd,),
        )
    except Exception as e:
        os.close(r_fd)
        os.close(w_fd)
        return None, f"相机会话进程启动失败：{e}"
    finally:
        err_log.close()
    os.close(w_fd)
    out = os.fdopen(r_fd, "rb", buffering=0)
    sess = {
        "proc": proc, "out": out,
        "ready_evt": threading.Event(),
        "rec_evt": threading.Event(),
        "rec_payload": {}, "fatal": None, "info": {},
    }
    with _proc_lock:
        _sess = sess
    threading.Thread(target=_session_reader, args=(sess,), daemon=True).start()
    if not sess["ready_evt"].wait(timeout=timeout):
        _kill_session()
        return None, "RealSense 相机启动超时"
    if sess["fatal"]:
        err = sess["fatal"]
        _kill_session()
        return None, err
    return sess, ""


def _kill_session():
    """停止会话子进程：发 quit 优雅退出（录制中先收尾），超时 kill 兜底"""
    global _sess
    with _proc_lock:
        sess = _sess
        _sess = None
    if sess is None:
        return
    proc = sess["proc"]
    try:
        proc.stdin.write(b'{"cmd":"quit"}\n')
        proc.stdin.flush()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def _send_cmd(sess, obj):
    """向会话子进程发一条 JSON 命令"""
    try:
        with _cmd_lock:
            sess["proc"].stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            sess["proc"].stdin.flush()
        return None
    except Exception as e:
        return f"相机会话通信失败：{e}"


# ==================== 路由 ====================

@realsense_bp.route("/status", methods=["GET"])
@jwt_required()
def status():
    """检测 RealSense 设备状态（型号/固件来自子进程 DETAIL，非硬编码）"""
    count, serial, detail = _probe()
    with _proc_lock:
        previewing = _sess is not None and _sess["proc"].poll() is None
    with _rec_state_lock:
        recording = bool(_rec_state.get("recording"))
    return success({
        "available": count > 0,
        "count": count,
        "serial": serial,
        "name": detail.get("name") or "RealSense D455f",
        "firmware": detail.get("firmware_version") or "",
        "product_line": detail.get("product_line") or "",
        "recording": recording,
        "previewing": previewing,
        "mode": "realsense",
    })


# ==================== USB 透传（宿主机代理） ====================
# RealSense 为主用深度相机：透传按钮经此触发宿主机 reset_orbbec_usb.ps1
# （无人值守 -Yes -SkipContainerRestart），脚本同时自愈 Orbbec/RealSense 两路
# USB，进度经 /passthrough/status 轮询（代理调用见 app.utils.host_agent）。

@realsense_bp.route("/passthrough", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def passthrough_start():
    """触发宿主机执行 reset_orbbec_usb.ps1（USB 透传自愈抢救）

    代理侧同一时刻仅允许一个任务（运行中重复触发返回"已有透传任务在执行中"）。
    脚本含 P3/P4 USB 总线复位：若远程联网网卡在受影响总线上会断网 10-30 秒，
    期间本接口与 /passthrough/status 轮询可能短暂失败，前端需容忍重试。
    """
    with _rec_state_lock:
        recording = bool(_rec_state.get("recording"))
    with _proc_lock:
        previewing = _sess is not None and _sess["proc"].poll() is None
    if recording:
        return fail("深度相机录制进行中，请先停止采集再透传", 409)
    if previewing:
        return fail("深度相机预览会话进行中，请先停止预览再透传", 409)
    data, err = _agent_call("/reset", method="POST", timeout=10)
    if err:
        if "已有透传任务" in err:
            return success({"running": True}, message="透传任务已在执行中，继续查询进度")
        return fail(err, 502)
    return success(data, message="透传任务已启动")


@realsense_bp.route("/passthrough/status", methods=["GET"])
@jwt_required()
def passthrough_status():
    """透传任务进度（running / exit_code / 日志尾部 tail）

    后端容器重启不影响宿主机任务本体；前端轮询失败（断网/后端重启）应重试
    而非立即报错。
    """
    data, err = _agent_call("/status", timeout=5)
    if err:
        return fail(err, 502)
    return success(data)


# ==================== 实时预览 ====================

@realsense_bp.route("/preview/start", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def preview_start():
    """启动相机会话（常驻子进程；录制共用同一会话，预览流全程不断）"""
    sess, err = _ensure_session()
    if err:
        return fail(err, 409)
    return success({"stream": "/api/realsense/preview/stream"}, message="实时预览已启动")


@realsense_bp.route("/preview/stop", methods=["POST"])
@jwt_required()
def preview_stop():
    """停止相机会话（子进程退出释放设备；录制收尾自动完成）"""
    _kill_session()
    return success(message="实时预览已停止")


# 流读取互斥锁：同一时刻只允许一个消费者读会话子进程 stdout。
# <img> 的 src 轮换（签名 URL 刷新）会让浏览器断旧连接、发起新请求，新旧
# gen() 并发读同一 BufferedReader 会撕裂 MJPEG 帧——两个连接都无法解码。
# 排队等锁：旧消费者随浏览器断开由 gunicorn 关闭生成器后释放，新消费者接管。
_stream_read_lock = threading.Lock()


@realsense_bp.route("/preview/stream", methods=["GET"])
@media_auth_required("realsense_stream")
def preview_stream():
    """实时预览 MJPEG 流：透传会话子进程 stdout（multipart/x-mixed-replace）

    鉴权：优先 ?media_token=<短期签名>，兼容 JWT（header / access_token query）。
    预览与录制共用会话子进程，开始/停止录制不影响本流。
    """
    with _proc_lock:
        sess = _sess
    if sess is None or sess["proc"].poll() is not None:
        return fail("预览未启动", 409)
    proc = sess["proc"]

    def gen():
        acquired = _stream_read_lock.acquire(timeout=10)
        try:
            if not acquired:
                return  # 前一个消费者迟迟不退出（异常态），本次放弃，前端轮换 URL 后会重试
            while proc.poll() is None:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            if acquired:
                _stream_read_lock.release()

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ==================== 录制 ====================

@realsense_bp.route("/record/start", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_start():
    """启动录制（会话内发 start_rec 命令；无进程切换，秒级开始）"""
    data = request.get_json(silent=True) or {}
    with _rec_state_lock:
        if _rec_state.get("recording"):
            return fail("已有录制进行中", 409)
    sess, err = _ensure_session()
    if err:
        return fail(err, 409)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REALSENSE_REC_DIR, f"realsense_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    fps = int(data.get("fps", DEFAULT_FPS))
    sess["rec_evt"].clear()
    sess["rec_payload"] = {}
    err = _send_cmd(sess, {"cmd": "start_rec", "path": out_dir, "fps": fps})
    if err:
        return fail(f"录制启动失败：{err}", 409)
    if not sess["rec_evt"].wait(timeout=15):
        return fail("录制启动超时", 409)
    payload = sess["rec_payload"]
    if payload.get("err"):
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)  # 启动失败清理空目录
        return fail(f"录制启动失败：{payload['err']}", 409)
    with _rec_state_lock:
        _rec_state.update({
            "dir": out_dir,
            "preview_ready": False,
            "preview_rel": None,
            "frames": 0,
            "done": False,
            "error": None,
            "recording": True,
            "paused": False,
            "meta": {
                "device_type": "realsense",
                "device_serial": payload.get("serial") or "",
                "device_name": payload.get("name") or "",
                "firmware_version": payload.get("firmware") or "",
                "fps": int(payload.get("fps") or fps),
                "start_time": datetime.now().isoformat(timespec="seconds"),
            },
        })
    return success({"path": out_dir}, message="录制已启动")


@realsense_bp.route("/record/stop", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_stop():
    """停止录制：发 stop_rec 后立即返回，收尾在子进程后台线程完成

    结果（帧数/最终目录/错误）经 /record/status 轮询获取（rec_done 事件补齐）。
    收尾期间预览持续、相机不释放，可立即开始下一段录制。
    """
    with _rec_state_lock:
        if not _rec_state.get("recording"):
            return fail("没有进行中的录制", 409)
        stop_dir = _rec_state.get("dir")
        _rec_state.update({"done": False, "error": None})
    with _proc_lock:
        sess = _sess
    if sess is None or sess["proc"].poll() is not None:
        # 会话已死：reader 清理路径会标记错误，这里兜底返回
        with _rec_state_lock:
            _rec_state.update({"done": True, "recording": False,
                               "error": "相机会话进程退出，录制中断"})
        return fail("录制会话已中断", 409)
    err = _send_cmd(sess, {"cmd": "stop_rec"})
    if err:
        return fail(err, 409)
    if stop_dir:
        with _finishing_lock:
            _finishing_dirs.add(stop_dir)
    return success({"path": stop_dir}, message="录制已停止")


@realsense_bp.route("/record/pause", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_pause():
    """暂停录制：帧不再进入编码器（视频里不存在暂停区间），预览流不受影响"""
    with _rec_state_lock:
        if not _rec_state.get("recording"):
            return fail("没有进行中的录制", 409)
        if _rec_state.get("paused"):
            return fail("录制已处于暂停状态", 409)
    with _proc_lock:
        sess = _sess
    if sess is None or sess["proc"].poll() is not None:
        return fail("相机会话已中断", 409)
    err = _send_cmd(sess, {"cmd": "pause_rec"})
    if err:
        return fail(err, 409)
    # 状态由子进程 rec_paused 事件回填；此处乐观置位，前端按钮即时切换
    with _rec_state_lock:
        _rec_state["paused"] = True
    return success(message="录制已暂停")


@realsense_bp.route("/record/resume", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_resume():
    """恢复录制（从上次暂停处继续采集）"""
    with _rec_state_lock:
        if not _rec_state.get("recording"):
            return fail("没有进行中的录制", 409)
        if not _rec_state.get("paused"):
            return fail("录制未处于暂停状态", 409)
    with _proc_lock:
        sess = _sess
    if sess is None or sess["proc"].poll() is not None:
        return fail("相机会话已中断", 409)
    err = _send_cmd(sess, {"cmd": "resume_rec"})
    if err:
        return fail(err, 409)
    with _rec_state_lock:
        _rec_state["paused"] = False
    return success(message="录制已继续")


@realsense_bp.route("/record/status", methods=["GET"])
@jwt_required()
def record_status():
    """查询最近一次录制状态"""
    with _rec_state_lock:
        st = dict(_rec_state)
        if st.get("preview_rel"):
            st["preview"] = os.path.join(REALSENSE_REC_DIR, st["preview_rel"])
    return success(st)


@realsense_bp.route("/preview", methods=["GET"])
@media_auth_required("realsense_preview", resource_key="path")
def preview():
    """返回录制预览 mp4（color.mp4）

    鉴权：优先 ?media_token=<短期签名>（绑定 path），兼容 JWT。
    """
    rel = (request.args.get("path") or "").lstrip("/\\")
    if not rel or ".." in rel.replace("\\", "/").split("/"):
        return fail("无效路径", 400)
    fp = os.path.normpath(os.path.join(REALSENSE_REC_DIR, rel))
    if not fp.startswith(REALSENSE_REC_DIR) or not os.path.isfile(fp):
        return fail("预览文件不存在", 404)
    return send_file(fp, mimetype="video/mp4", as_attachment=False,
                     download_name="preview.mp4", conditional=True)


@realsense_bp.route("/upload", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def upload_recorded():
    """将已录制的 color.mp4 与原始深度序列一并入库

    请求体 JSON：
      dir: str          录制输出目录（来自 /record/stop 返回的 path）
      subject_id: int   受试者 ID
      video_type: str   face/body/gait
    """
    data = request.get_json(silent=True) or {}
    out_dir = data.get("dir")
    try:
        subject_id = int(data.get("subject_id"))
    except (TypeError, ValueError):
        return fail("subject_id 必须是数字", 422)
    video_type = (data.get("video_type") or "").strip() or None

    if not out_dir or not subject_id:
        return fail("dir 与 subject_id 必填", 422)
    # 路径约束：仅允许容器内录制目录下的产物，防止伪造路径上传任意文件
    out_dir = os.path.normpath(os.path.abspath(out_dir))
    if not (out_dir == REALSENSE_REC_DIR
            or out_dir.startswith(REALSENSE_REC_DIR + os.sep)):
        return fail("无效的录制目录", 422)
    # 录制收尾中 color.mp4 可能尚未写完（编码器 flush 未完成），
    # 拒绝上传避免入库截断损坏的文件
    with _finishing_lock:
        if os.path.normpath(out_dir) in {os.path.normpath(d) for d in _finishing_dirs}:
            return fail("录制仍在收尾中，请稍候重试", 409)
    color_mp4 = os.path.join(out_dir, "color.mp4")
    if not os.path.isfile(color_mp4):
        return fail(f"录制文件不存在：{color_mp4}", 404)

    from werkzeug.datastructures import FileStorage
    asset = None
    try:
        with open(color_mp4, "rb") as f:
            file_storage = FileStorage(
                stream=f, filename="color.mp4",
                content_type="video/mp4",
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
        color_asset = asset
        # 先读子进程 meta.json（失败不影响入库），深度上传时把 fps 写进深度资产
        # 自身 metadata——直接访问深度资产播放时（不走彩色资产定位链）也能取到 fps，
        # 否则 usbip 带宽下降级到 5/15fps 的录制会按默认 30fps 转码导致快放
        meta_path = os.path.join(out_dir, "meta.json")
        child_meta = {}
        if os.path.isfile(meta_path):
            try:
                import json as _json
                with open(meta_path, "r", encoding="utf-8") as f:
                    child_meta = _json.load(f)
            except Exception:
                child_meta = {}
        # 原始深度序列（DZST 压缩）一并入库（metadata.depth_raw=true），
        # 同时把深度资产 id 记到彩色资产 metadata，供可视化从深度通道转伪彩色时定位
        depth_raw_zst = os.path.join(out_dir, "depth_raw.zst")
        depth_asset_id = None
        if os.path.isfile(depth_raw_zst):
            # 视频重采时同类型深度一并重采（_upload_raw_depth 按类型清理旧深度）
            depth_asset = _upload_raw_depth(svc, subject_id, depth_raw_zst,
                                            video_type=video_type,
                                            fps=child_meta.get("fps"))
            depth_asset_id = depth_asset.id if depth_asset else None
        # 深度信息与关联立即合并到彩色资产 metadata：不依赖后续辅助 JSON 上传，
        # 避免辅助 JSON 入库失败时深度关联丢失（否则可视化定位深度资产失败，报"无深度轨"）
        extra_meta = {}
        if child_meta:
            extra_meta = {
                "depth_frames": child_meta.get("frame_count", 0),
                "depth_raw": bool(child_meta.get("depth_raw")),
                "depth_codec": child_meta.get("depth_codec", "zstd"),
                "device_serial": child_meta.get("device_serial", ""),
                "device_name": child_meta.get("device_name", ""),
                "firmware_version": child_meta.get("firmware_version", ""),
                "color_resolution": child_meta.get("color_resolution", ""),
                "depth_resolution": child_meta.get("depth_resolution", ""),
                "depth_units_mm": child_meta.get("depth_units_mm", 1.0),
                "fps": child_meta.get("fps", 30),
                "start_time": child_meta.get("start_time", ""),
                "end_time": child_meta.get("end_time", ""),
            }
        if depth_asset_id:
            extra_meta["depth_asset_id"] = depth_asset_id
        if extra_meta:
            ameta = dict(color_asset.metadata_json or {})
            ameta.update({"realsense": extra_meta})
            color_asset.metadata_json = ameta
            db.session.commit()
        # 辅助 JSON（逐帧同步 frames.jsonl / 相机标定 calibration.json）一并入库，
        # 重采时与深度/视频同类型替换；失败仅记录警告，不影响主资产入库
        frames_jsonl = os.path.join(out_dir, "frames.jsonl")
        calib_json = os.path.join(out_dir, "calibration.json")
        frames_asset_id = None
        calib_asset_id = None
        try:
            if os.path.isfile(frames_jsonl):
                fa = _upload_rec_json(svc, subject_id, frames_jsonl, "frames", video_type)
                frames_asset_id = fa.id if fa else None
            if os.path.isfile(calib_json):
                ca = _upload_rec_json(svc, subject_id, calib_json, "calibration", video_type)
                calib_asset_id = ca.id if ca else None
        except Exception:
            logger.exception("辅助 JSON 入库失败（不影响主资产入库）")
        # JSON 资产关联补写到彩色资产 metadata
        if frames_asset_id or calib_asset_id:
            ameta = dict(color_asset.metadata_json or {})
            rmeta = dict(ameta.get("realsense") or {})
            if frames_asset_id:
                rmeta["frames_asset_id"] = frames_asset_id
            if calib_asset_id:
                rmeta["calibration_asset_id"] = calib_asset_id
            ameta["realsense"] = rmeta
            color_asset.metadata_json = ameta
            db.session.commit()
    except Exception:
        logger.exception("RealSense 录制入库失败")
        return fail("入库失败，请稍后重试", 500)

    # 入库后删除录制目录（数据湖已存副本；color.mp4/depth_raw.zst 已被 AssetService 复制/加密落盘）
    import shutil
    try:
        shutil.rmtree(out_dir, ignore_errors=True)
    except Exception:
        pass

    return success(asset.to_dict(), message="已入库", code=201)


def _upload_raw_depth(svc, subject_id, zst_path, video_type=None, fps=None):
    """上传/替换受试者指定视频类型的原始深度序列资产（DZST 压缩）

    独立于彩色视频资产管理，避免被视频重采逻辑（video_type）误删。
    - 命名带 video_type 后缀（face/gait 等，便于区分），但 metadata 不写
      video_type（普通视频重采按 metadata.video_type 删除，深度资产会被误删）；
      类型记录在独立字段 depth_video_type，按类型各自保留一份。
    - fps 写入 metadata.realsense（深度视频转码按真实帧率编码，usbip 降级
      到 5/15fps 的录制不会按默认 30fps 转码导致快放）。
    - 返回新资产；文件缺失/入库失败返回 None。
    """
    from app.models import DataAsset as _DA
    from app.models import DataType as _DT
    from werkzeug.datastructures import FileStorage

    # 清理同类型旧的原始深度资产（不同类型各自保留）。
    # 兼容旧数据：无 depth_video_type 标记的旧深度（每受试者仅一份）视为可替换，
    # 新上传带类型时一并清理，避免历史深度残留。
    old = _DA.query.filter_by(subject_id=subject_id, data_type=_DT.VIDEO).all()
    for a in old:
        meta = a.metadata_json or {}
        old_vt = meta.get("depth_video_type") or ""
        if meta.get("depth_raw") and (
            old_vt == (video_type or "")
            or (video_type and not old_vt)
        ):
            from app.services.subject_service import (
                _purge_asset_records, _collect_asset_file_paths,
            )
            from app.utils.audit import snapshot_delete
            storage_root = current_app.config["DATA_LAKE_DIR"]
            snapshot_delete("data_asset", a, f"原始深度重采替换 {a.file_name}",
                            operator=svc._operator_user())
            _purge_asset_records(a)
            for fp in _collect_asset_file_paths(a, storage_root):
                try:
                    os.remove(fp)
                except OSError:
                    pass
            db.session.delete(a)
    db.session.commit()

    if not os.path.isfile(zst_path):
        return None
    # 原始文件名带 video_type，幂等检查按类型区分（同类型重复录制时长相同也不误判）
    fname = f"depth_raw_{video_type}.zst" if video_type else "depth_raw.zst"
    with open(zst_path, "rb") as f:
        fs = FileStorage(stream=f, filename=fname,
                         content_type="application/octet-stream")
        asset, _is_dup = svc.upload_asset(
            subject_id=subject_id,
            data_type="video",
            file_storage=fs,
            layer="raw",
            video_type=None,  # 不参与 face/body/gait 重采，避免普通视频上传误删深度资产
            naming_video_type=video_type,  # 仅命名加类型后缀便于区分
        )
    dmeta = dict(asset.metadata_json or {})
    dmeta.update({"depth_raw": True, "depth_video_type": video_type})
    if fps:
        dmeta["realsense"] = {"fps": fps}
    asset.metadata_json = dmeta
    db.session.commit()
    return asset


def _upload_rec_json(svc, subject_id, file_path, kind, video_type=None):
    """上传/替换受试者指定视频类型的辅助 JSON 资产（frames.jsonl / calibration.json）

    kind: "frames" | "calibration"。metadata 标记 realsense_meta_kind + depth_video_type，
    重采时同类型替换（与深度资产一致，互不影响）。返回新资产；文件缺失/失败返回 None。
    """
    from app.models import DataAsset as _DA
    from app.models import DataType as _DT
    from werkzeug.datastructures import FileStorage

    # 清理同类型旧的辅助 JSON（兼容无类型标记的旧数据，与深度资产清理规则一致）
    old = _DA.query.filter_by(subject_id=subject_id, data_type=_DT.JSON).all()
    for a in old:
        meta = a.metadata_json or {}
        old_vt = meta.get("depth_video_type") or ""
        if meta.get("realsense_meta_kind") == kind and (
            old_vt == (video_type or "")
            or (video_type and not old_vt)
        ):
            from app.services.subject_service import (
                _purge_asset_records, _collect_asset_file_paths,
            )
            from app.utils.audit import snapshot_delete
            storage_root = current_app.config["DATA_LAKE_DIR"]
            snapshot_delete("data_asset", a, f"RealSense {kind} 重采替换 {a.file_name}",
                            operator=svc._operator_user())
            _purge_asset_records(a)
            for fp in _collect_asset_file_paths(a, storage_root):
                try:
                    os.remove(fp)
                except OSError:
                    pass
            db.session.delete(a)
    db.session.commit()

    if not os.path.isfile(file_path):
        return None
    # 原始文件名带 video_type，幂等检查按类型区分
    base = os.path.basename(file_path)
    stem, ext = os.path.splitext(base)
    fname = f"{stem}_{video_type}{ext}" if video_type else base
    with open(file_path, "rb") as f:
        fs = FileStorage(stream=f, filename=fname,
                         content_type="application/json")
        asset, _is_dup = svc.upload_asset(
            subject_id=subject_id,
            data_type="json",
            file_storage=fs,
            layer="raw",
            video_type=None,
            naming_video_type=video_type,
        )
    dmeta = dict(asset.metadata_json or {})
    dmeta.update({"realsense_meta_kind": kind, "depth_video_type": video_type})
    asset.metadata_json = dmeta
    db.session.commit()
    return asset
