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
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

from flask import request, Response, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity

from app.api import realsense_bp
from app.extensions import db
from app.models import Role
from app.services import AssetService
from app.utils.response import success, fail
from app.utils.decorators import role_required
from app.utils.audit import current_role

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
    return proc.returncode, (out or b"").decode("utf-8", "replace")


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
    count, _serial, _detail = _probe()
    if count == 0:
        return fail("未检测到 RealSense 相机", 409)
    _start_proc(["stream", str(DEFAULT_FPS)])
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
@jwt_required()
def preview_stream():
    """实时预览 MJPEG 流：透传子进程 stdout（multipart/x-mixed-replace）"""
    with _proc_lock:
        proc = _stream_proc
    if proc is None or proc.poll() is not None:
        return fail("预览未启动", 409)

    def gen():
        try:
            while proc.poll() is None:
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
    """启动录制（子进程 record，彩色 MP4 + 深度 PNG）"""
    global _rec_proc
    data = request.get_json(silent=True) or {}
    with _proc_lock:
        if _rec_proc is not None and _rec_proc.poll() is None:
            return fail("已有录制进行中", 409)
        if _stream_proc is not None and _stream_proc.poll() is None:
            # 录制与预览互斥：先停预览
            _stop_proc(_stream_proc, wait=10)
            _stream_proc = None
    count, serial, _detail = _probe()
    if count == 0:
        return fail("未检测到 RealSense 相机", 409)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REALSENSE_REC_DIR, f"realsense_{ts}")
    os.makedirs(out_dir, exist_ok=True)
    fps = int(data.get("fps", DEFAULT_FPS))
    _start_proc(["record", out_dir, str(fps)], rec=True)
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
    # wait 放宽到 300s：子进程停止后需等 ffmpeg 编码器 EOF flush 收尾（含 ffv1 无损深度）
    rc, out = _stop_proc(proc, wait=300)
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
@jwt_required()
def preview():
    """返回录制预览 mp4（color.mp4）"""
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
    """将已录制的 color.mp4 入库为数据资产，深度伪彩色视频一并入库

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
    depth_mp4 = os.path.join(out_dir, "depth.mp4")
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
        # 深度伪彩色视频一并入库（不传 video_type，避免被视频重采逻辑删除）：
        # 先清理受试者名下旧的深度视频资产（metadata.depth=true），再上传新的
        if os.path.isfile(depth_mp4):
            _replace_depth_asset(svc, subject_id, depth_mp4)
        # 原始深度序列（ffv1 无损 mkv）一并入库（metadata.depth_raw=true）
        depth_raw_mkv = os.path.join(out_dir, "depth_raw.mkv")
        if os.path.isfile(depth_raw_mkv):
            _upload_raw_depth(svc, subject_id, depth_raw_mkv)
        # 深度序列信息合并到彩色资产 metadata（depth 视频是否存在、序列号等）
        meta_path = os.path.join(out_dir, "meta.json")
        extra_meta = {}
        if os.path.isfile(meta_path):
            try:
                import json as _json
                with open(meta_path, "r", encoding="utf-8") as f:
                    child_meta = _json.load(f)
                extra_meta = {
                    "depth_frames": child_meta.get("frame_count", 0),
                    "depth_video": bool(child_meta.get("depth_video")),
                    "depth_raw": bool(child_meta.get("depth_raw")),
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
        if extra_meta:
            ameta = color_asset.metadata_json or {}
            ameta.update({"realsense": extra_meta})
            color_asset.metadata_json = ameta
            db.session.commit()
    except Exception as e:
        return fail(f"入库失败：{e}", 500)

    # 入库后删除录制目录（数据湖已存副本；color.mp4/depth.mp4 已被 AssetService 复制/加密落盘）
    import shutil
    try:
        shutil.rmtree(out_dir, ignore_errors=True)
    except Exception:
        pass

    return success(asset.to_dict(), message="已入库", code=201)


def _replace_depth_asset(svc, subject_id, depth_mp4):
    """上传/替换受试者的深度伪彩色视频资产（metadata.depth=true）

    返回新资产；深度文件缺失/入库失败返回 None。
    不传 video_type，避免与彩色视频的重采逻辑（_purge_subject_video_by_type）互相删除。
    """
    from app.models import DataAsset as _DA
    from app.models import DataType as _DT
    from werkzeug.datastructures import FileStorage

    # 清理旧的深度资产（先删关联记录与磁盘文件，再删 DB 行）
    old = _DA.query.filter_by(subject_id=subject_id, data_type=_DT.VIDEO).all()
    for a in old:
        if (a.metadata_json or {}).get("depth"):
            from app.services.subject_service import (
                _purge_asset_records, _collect_asset_file_paths,
            )
            from app.utils.audit import snapshot_delete
            storage_root = current_app.config["DATA_LAKE_DIR"]
            snapshot_delete("data_asset", a, f"深度视频重采替换 {a.file_name}",
                            operator=svc._operator_user())
            _purge_asset_records(a)
            for fp in _collect_asset_file_paths(a, storage_root):
                try:
                    os.remove(fp)
                except OSError:
                    pass
            db.session.delete(a)
    db.session.commit()

    if not os.path.isfile(depth_mp4):
        return None
    with open(depth_mp4, "rb") as f:
        fs = FileStorage(stream=f, filename="depth.mp4", content_type="video/mp4")
        asset, _is_dup = svc.upload_asset(
            subject_id=subject_id,
            data_type="video",
            file_storage=fs,
            layer="raw",
            video_type=None,  # 通用视频，不参与 face/body/gait 重采
        )
    dmeta = asset.metadata_json or {}
    dmeta.update({"depth": True})
    asset.metadata_json = dmeta
    db.session.commit()
    return asset


def _upload_raw_depth(svc, subject_id, mkv_path):
    """上传/替换受试者的原始深度序列资产（ffv1 无损 mkv，metadata.depth_raw=true）

    与深度伪彩色资产（depth=true）分开管理，避免被深度视频预览/转码逻辑误用。
    返回新资产；文件缺失/入库失败返回 None。
    """
    from app.models import DataAsset as _DA
    from app.models import DataType as _DT
    from werkzeug.datastructures import FileStorage

    # 清理旧的原始深度资产
    old = _DA.query.filter_by(subject_id=subject_id, data_type=_DT.VIDEO).all()
    for a in old:
        if (a.metadata_json or {}).get("depth_raw"):
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

    if not os.path.isfile(mkv_path):
        return None
    with open(mkv_path, "rb") as f:
        fs = FileStorage(stream=f, filename="depth_raw.mkv",
                         content_type="video/x-matroska")
        asset, _is_dup = svc.upload_asset(
            subject_id=subject_id,
            data_type="video",
            file_storage=fs,
            layer="raw",
            video_type=None,  # 通用视频，不参与 face/body/gait 重采
        )
    dmeta = asset.metadata_json or {}
    dmeta.update({"depth_raw": True})
    asset.metadata_json = dmeta
    db.session.commit()
    return asset
