# -*- coding: utf-8 -*-
"""Intel RealSense D455f 深度摄像头接口（容器内 pyrealsense2 直连 USB）

与 /api/orbbec/* 接口协议保持一致（status/preview/record/upload），前端交互模式相同。
pyrealsense2 是 C 库，所有调用通过子进程 realsense_child.py 隔离（崩溃不影响 Flask worker）。

录制产物（容器内 /app/realsense_recordings/<ts>/）：
  color.mp4          彩色视频（H.264，libx264 直接编码，浏览器可播）
  depth.mp4          深度伪彩色视频（H.264，jet 色标，0=深灰无效）
  depth_raw.mkv      原始深度序列（ffv1 无损，z16 16 位精度，入库供分析）
  meta.json          序列号 / 分辨率 / 帧时间戳 / 编码信息

路由：
- GET  /api/realsense/status           设备状态（无设备时 available=false）
- POST /api/realsense/preview/start    启动实时预览（MJPEG 流）
- POST /api/realsense/preview/stop     停止实时预览
- GET  /api/realsense/preview/stream   MJPEG 流（<img> 直接播放）
- POST /api/realsense/record/start     启动录制
- POST /api/realsense/record/stop      停止录制
- GET  /api/realsense/record/status    查询录制状态
- GET  /api/realsense/preview          返回录制预览 mp4
- POST /api/realsense/upload           将录制入库为数据资产（复用 AssetService）
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

from flask import request, Response, send_file, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.api import realsense_bp
from app.extensions import db
from app.models import Role
from app.services import AssetService
from app.utils.response import success, fail

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
# 子进程退出后 USB 设备释放余量（秒）：pyrealsense2 子进程退出后立即重启
# 新进程（预览↔录制切换）可能因设备未完全释放导致 pipe.start() 失败/卡住
USB_RELEASE_DELAY = 2.0


def _run_child(args, timeout=30, **kw):
    """运行 realsense_child.py 子进程，返回 (returncode, stdout_text)"""
    cmd = [sys.executable, CHILD_SCRIPT] + args
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


# ==================== 设备检测 ====================
def _probe():
    """检测 RealSense 设备，返回 (count, serial, detail)；失败返回 (0, None, {})

    detail 为子进程 DETAIL 行携带的设备信息（name / firmware_version / product_line），
    用于确认设备型号（如 D455F）与固件版本。
    """
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


# ==================== 子进程会话管理 ====================
_proc_lock = threading.Lock()
_stream_proc = None   # 预览子进程（stdin 写 stop 优雅退出）
_rec_proc = None      # 录制子进程

# USB 释放跟踪：记录最近一次子进程停止时刻，启动新进程前等待设备完全释放
_last_stop_ts = 0.0
_stop_ts_lock = threading.Lock()


def _mark_stopped():
    """记录一次子进程停止时刻（进程退出即释放 USB，供下次启动前等待）"""
    global _last_stop_ts
    with _stop_ts_lock:
        _last_stop_ts = time.time()


def _wait_usb_free():
    """启动新子进程前等待 USB 设备释放完成，避免跨进程重开相机失败/卡住"""
    with _stop_ts_lock:
        last = _last_stop_ts
    remain = USB_RELEASE_DELAY - (time.time() - last)
    if remain > 0:
        time.sleep(remain)


def _wait_child_ready(proc, timeout=20):
    """等待子进程输出就绪行（READY 成功 / ERR 失败），并消费该行。

    返回 (ok, message)。超时未就绪则杀掉进程并报错，避免残留进程
    或未消费的输出污染后续 MJPEG 流。
    """
    import queue
    ready_q = queue.Queue(maxsize=1)

    def _reader():
        try:
            for raw in proc.stdout:
                text = raw.decode("utf-8", "replace").strip()
                if text.startswith(("READY", "ERR ")):
                    try:
                        ready_q.put_nowait(text)
                    except Exception:
                        pass
                    return
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True).start()
    try:
        text = ready_q.get(timeout=timeout)
    except queue.Empty:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            # 等待进程真正退出，避免孤儿进程继续占用 USB 导致后续启动失败
            proc.wait(timeout=5)
        except Exception:
            pass
        return False, "RealSense 相机启动超时"
    if text.startswith("READY"):
        return True, ""
    return False, text[4:]

# 录制状态（前端轮询 /record/status）
_rec_state = {
    "dir": None,
    "preview_ready": False,
    "preview_rel": None,
    "frames": 0,
    "meta": {},
    "done": False,
}
_rec_state_lock = threading.Lock()


def _start_proc(args, rec=False):
    """启动子进程（stdin/stdout 管道）。rec=True 时同时更新录制状态。"""
    global _stream_proc, _rec_proc
    proc = subprocess.Popen(
        [sys.executable, CHILD_SCRIPT] + args,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    with _proc_lock:
        if rec:
            _rec_proc = proc
        else:
            _stream_proc = proc
    return proc


def _stop_proc(proc, wait=30):
    """向子进程 stdin 写 stop 并等待退出，返回 (returncode, stdout_text)"""
    if proc is None or proc.poll() is not None:
        _mark_stopped()
        return proc.returncode if proc else 0, ""
    try:
        if proc.stdin:
            proc.stdin.write(b"stop\n")
            proc.stdin.flush()
    except Exception:
        pass
    try:
        out, _ = proc.communicate(timeout=wait)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=10)
    _mark_stopped()
    return proc.returncode, (out or b"").decode("utf-8", "replace")


def _stop_rec_proc(proc, wait=30):
    """停止录制子进程并解析结果行（stdout 混有 MJPEG 实时预览帧）

    录制子进程在录制期间持续向 stdout 输出 MJPEG 帧，若用 communicate 会把全程
    预览帧读入内存；这里改为后台线程流式消费并丢弃帧数据，只保留尾部最近 64KB
    供 DONE/ERR 行解析。返回 (returncode, tail_text)。
    """
    if proc is None or proc.poll() is not None:
        _mark_stopped()
        return proc.returncode if proc else 0, ""
    try:
        if proc.stdin:
            proc.stdin.write(b"stop\n")
            proc.stdin.flush()
    except Exception:
        pass
    tail = []

    def _drain():
        buf = b""
        try:
            for raw in proc.stdout:
                buf = (buf + raw)[-65536:]
        except Exception:
            pass
        tail.append(buf)

    threading.Thread(target=_drain, daemon=True).start()
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    _mark_stopped()
    return proc.returncode, (tail[0] if tail else b"").decode("utf-8", "replace")


def _parse_done(out):
    """解析子进程输出的 DONE/ERR 行，返回 (ok, dir_or_err, frames)"""
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("DONE "):
            parts = line.split()
            if len(parts) >= 3:
                return True, parts[1], int(parts[2])
        elif line.startswith("ERR "):
            return False, line[4:], 0
    return False, "子进程未输出结果", 0


# ==================== 路由 ====================

@realsense_bp.route("/status", methods=["GET"])
@jwt_required()
def status():
    """检测 RealSense 设备状态（型号/固件来自子进程 DETAIL，非硬编码）"""
    count, serial, detail = _probe()
    with _proc_lock:
        recording = _rec_proc is not None and _rec_proc.poll() is None
        previewing = _stream_proc is not None and _stream_proc.poll() is None
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


# ==================== 实时预览 ====================

@realsense_bp.route("/preview/start", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def preview_start():
    """启动实时预览（子进程 stream，输出 MJPEG 到 stdout）"""
    global _stream_proc
    with _proc_lock:
        if _stream_proc is not None and _stream_proc.poll() is None:
            return success({"stream": "/api/realsense/preview/stream"}, message="实时预览已启动")
        if _rec_proc is not None and _rec_proc.poll() is None:
            return fail("录制进行中，不能启动实时预览", 409)
    _wait_usb_free()
    count, _serial, _detail = _probe()
    if count == 0:
        return fail("未检测到 RealSense 相机", 409)
    proc = _start_proc(["stream", str(DEFAULT_FPS)])
    ok, msg = _wait_child_ready(proc, timeout=15)
    if not ok:
        with _proc_lock:
            if _stream_proc is proc:
                _stream_proc = None
        return fail(msg, 409)
    return success({"stream": "/api/realsense/preview/stream"}, message="实时预览已启动")


@realsense_bp.route("/preview/stop", methods=["POST"])
@jwt_required()
def preview_stop():
    """停止实时预览（子进程优雅退出）"""
    global _stream_proc
    with _proc_lock:
        proc = _stream_proc
        _stream_proc = None
    _stop_proc(proc, wait=10)
    return success(message="实时预览已停止")


@realsense_bp.route("/preview/stream", methods=["GET"])
@media_auth_required("realsense_stream")
def preview_stream():
    """实时预览 MJPEG 流：透传子进程 stdout（multipart/x-mixed-replace）

    鉴权：优先 ?media_token=<短期签名>，兼容 JWT（header / access_token query）。
    录制中预览子进程已停止，改透传录制子进程 stdout（record 子进程同步输出 MJPEG 帧）。
    """
    with _proc_lock:
        if _stream_proc is not None and _stream_proc.poll() is None:
            proc = _stream_proc
        else:
            proc = _rec_proc
    if proc is None or proc.poll() is not None:
        return fail("预览未启动", 409)

    def gen():
        try:
            while proc.poll() is None:
                # 停止/切换时让出 stdout 读取权（避免与 _stop_rec_proc 的解析线程竞争）
                with _proc_lock:
                    still_live = (_stream_proc is proc) or (_rec_proc is proc)
                if not still_live:
                    break
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            # 客户端断开：子进程 _safe_write 检测到管道关闭会自我退出
            pass

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ==================== 录制 ====================

@realsense_bp.route("/record/start", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_start():
    """启动录制（子进程 record，彩色 MP4 + 深度 zstd 序列）"""
    global _rec_proc, _stream_proc
    data = request.get_json(silent=True) or {}
    with _proc_lock:
        if _rec_proc is not None and _rec_proc.poll() is None:
            return fail("已有录制进行中", 409)
        if _stream_proc is not None and _stream_proc.poll() is None:
            # 录制与预览互斥：先停预览
            _stop_proc(_stream_proc, wait=10)
            _stream_proc = None
    # 预览子进程刚退出，等待 USB 释放后再探测/启动录制，避免 pipe.start() 失败
    _wait_usb_free()
    count, serial, _detail = _probe()
    if count == 0:
        return fail("未检测到 RealSense 相机", 409)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REALSENSE_REC_DIR, f"realsense_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    fps = int(data.get("fps", DEFAULT_FPS))
    proc = _start_proc(["record", out_dir, str(fps)], rec=True)
    ok, msg = _wait_child_ready(proc, timeout=20)
    if not ok:
        with _proc_lock:
            if _rec_proc is proc:
                _rec_proc = None
        return fail(f"录制启动失败：{msg}", 409)
    with _rec_state_lock:
        _rec_state.update({
            "dir": out_dir,
            "preview_ready": False,
            "preview_rel": None,
            "frames": 0,
            "done": False,
            "meta": {
                "device_type": "realsense",
                "device_serial": serial,
                "device_name": _detail.get("name") or "",
                "firmware_version": _detail.get("firmware_version") or "",
                "fps": fps,
                "start_time": datetime.now().isoformat(timespec="seconds"),
            },
        })
    return success({"path": out_dir}, message="录制已启动")


@realsense_bp.route("/record/stop", methods=["POST"])
@jwt_required()
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def record_stop():
    """停止录制（子进程优雅停止，输出 DONE <dir> <frames>）"""
    global _rec_proc
    with _proc_lock:
        proc = _rec_proc
        _rec_proc = None
    if proc is None or proc.poll() is not None:
        return fail("没有进行中的录制", 409)
    # 深度 zstd 序列不走 ffmpeg，停止时仅需等彩色 libx264 EOF flush（秒级）；
    # 30s 上限兜底 USB 掉线等异常，避免阻塞请求线程过长。
    # 录制中 stdout 持续输出 MJPEG 预览帧，用流式解析（丢弃帧数据，只取 DONE/ERR 行）
    rc, out = _stop_rec_proc(proc, wait=30)
    ok, result, frames = _parse_done(out)
    if not ok or rc != 0:
        with _rec_state_lock:
            _rec_state["done"] = True
        return fail(result, 409)
    out_dir = result
    color_rel = os.path.relpath(os.path.join(out_dir, "color.mp4"),
                                REALSENSE_REC_DIR).replace(os.sep, "/")
    with _rec_state_lock:
        _rec_state.update({
            "dir": out_dir,
            "preview_ready": True,
            "preview_rel": color_rel,
            "frames": frames,
            "done": True,
            "meta": dict(_rec_state["meta"], **{"end_time": datetime.now().isoformat(timespec="seconds"),
                                                "frame_count": frames}),
        })
    return success({"path": out_dir, "frames": frames}, message="录制已停止")


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
        # 原始深度序列（zstd 无损压缩）一并入库（metadata.depth_raw=true），
        # 同时把深度资产 id 记到彩色资产 metadata，供可视化从深度通道转伪彩色时定位
        depth_raw_zst = os.path.join(out_dir, "depth_raw.zst")
        depth_asset_id = None
        if os.path.isfile(depth_raw_zst):
            # 视频重采时同类型深度一并重采（_upload_raw_depth 按类型清理旧深度）
            depth_asset = _upload_raw_depth(svc, subject_id, depth_raw_zst,
                                            video_type=video_type)
            depth_asset_id = depth_asset.id if depth_asset else None
        # 深度信息与关联立即合并到彩色资产 metadata：不依赖后续辅助 JSON 上传，
        # 避免辅助 JSON 入库失败时深度关联丢失（否则可视化定位深度资产失败，报"无深度轨"）
        meta_path = os.path.join(out_dir, "meta.json")
        extra_meta = {}
        if os.path.isfile(meta_path):
            try:
                import json as _json
                with open(meta_path, "r", encoding="utf-8") as f:
                    child_meta = _json.load(f)
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
            except Exception:
                pass
        if depth_asset_id:
            extra_meta["depth_asset_id"] = depth_asset_id
        if extra_meta:
            ameta = color_asset.metadata_json or {}
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
            ameta = color_asset.metadata_json or {}
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


def _upload_raw_depth(svc, subject_id, zst_path, video_type=None):
    """上传/替换受试者指定视频类型的原始深度序列资产（zstd 无损压缩）

    独立于彩色视频资产管理，避免被视频重采逻辑（video_type）误删。
    - 命名带 video_type 后缀（face/gait 等，便于区分），但 metadata 不写
      video_type（普通视频重采按 metadata.video_type 删除，深度资产会被误删）；
      类型记录在独立字段 depth_video_type，按类型各自保留一份。
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
    dmeta = asset.metadata_json or {}
    dmeta.update({"depth_raw": True, "depth_video_type": video_type})
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
    dmeta = asset.metadata_json or {}
    dmeta.update({"realsense_meta_kind": kind, "depth_video_type": video_type})
    asset.metadata_json = dmeta
    db.session.commit()
    return asset
