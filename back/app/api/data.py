# -*- coding: utf-8 -*-
"""数据管理接口：接入、清洗、标准化、版本、受试者

路由层职责：
- 解析 request 参数
- 获取 JWT 身份
- 调用 service 层处理业务
- 构造 response

业务逻辑全部委托给 app.services 下的 service 类：
- SubjectService         受试者 CRUD
- AssetService           数据资产 + 文件上传/下载/播放
- DataProcessingService  清洗/标准化
- SnapshotService        快照/操作日志
"""
import logging
import os
from datetime import datetime, timedelta

from flask import request, current_app, send_file, after_this_request
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import func

from app.api import data_bp
from app.extensions import db
from app.models import Role, Subject, DataAsset, DataType, DataLayer
from app.services import (
    SubjectService, AssetService, DataProcessingService, SnapshotService,
    export_task_manager,
)
from app.utils.response import success, fail
from app.utils.media_auth import media_auth_required

logger = logging.getLogger(__name__)
from app.utils.decorators import role_required, retry_on_deadlock
from app.utils.audit import get_current_user, current_role

# 北京时间时区偏移（与 app.utils.time._CN_TZ 一致）
_CN_TZ_DELTA = timedelta(hours=8)


def _svc_subject():
    """构造 SubjectService（注入操作员上下文）"""
    return SubjectService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_asset():
    """构造 AssetService

    media_token 签名路径（/play、/file 在线播放/下载）无 JWT 上下文，
    get_jwt_identity() 会抛 RuntimeError；此时 operator 传 None 即可
    （服务层 _operator_user() 返回 None，审计日志跳过操作员字段）。
    """
    try:
        operator_id = int(get_jwt_identity())
    except RuntimeError:
        operator_id = None
    operator_role = None
    if operator_id is not None:
        try:
            operator_role = current_role()
        except RuntimeError:
            operator_role = None
    return AssetService(operator_id=operator_id, operator_role=operator_role)


def _svc_processing():
    """构造 DataProcessingService"""
    return DataProcessingService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _svc_snapshot():
    """构造 SnapshotService"""
    return SnapshotService(operator_id=int(get_jwt_identity()), operator_role=current_role())


def _register_temp_cleanup(tmp_path):
    """注册响应后清理临时文件的钩子（仅文件下载/播放路由使用）"""
    @after_this_request
    def _cleanup(response):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return response


# ====================== 受试者管理 ======================

@data_bp.route("/subjects", methods=["GET"])
@jwt_required()
@retry_on_deadlock(max_retries=2, delay=0.3)
def list_subjects():
    """受试者列表（支持关键词搜索与多条件筛选）
    风险分级传 'none' 表示筛选未评估（NULL 或空字符串）
    """
    result = _svc_subject().list_subjects(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
        keyword=request.args.get("keyword", "").strip(),
        gender=request.args.get("gender", "").strip(),
        risk_level=request.args.get("cognitive_risk_level", "").strip(),
        batch=request.args.get("collection_batch", "").strip(),
        has_video=request.args.get("has_video", "").strip(),
        ids_only=request.args.get("ids_only", "false").strip().lower() == "true",
    )
    return success(result)


@data_bp.route("/subjects/batches", methods=["GET"])
@jwt_required()
def list_subject_batches():
    """获取所有不重复的采集批次（用于下拉筛选）"""
    rows = db.session.query(Subject.collection_batch)\
        .filter(Subject.collection_batch.isnot(None))\
        .filter(Subject.collection_batch != "")\
        .distinct()\
        .order_by(Subject.collection_batch)\
        .all()
    result = [r[0] for r in rows]
    return success(result)


@data_bp.route("/subjects/batches-and-scenes", methods=["GET"])
@jwt_required()
def list_subject_batches_and_scenes():
    """获取所有不重复的采集批次与场景列表（用于表单下拉选择，最新优先）

    返回: { batches: [str], scenes: [str] }
    排序规则：按最近一次使用时间倒序（最新使用的排最前面），便于快速复用。
    """
    # 批次：按最近使用时间倒序（最近一次出现该批次的受试者创建时间）
    batch_rows = db.session.query(
        Subject.collection_batch,
    ).filter(
        Subject.collection_batch.isnot(None),
        Subject.collection_batch != "",
    ).order_by(Subject.created_at.desc()).all()
    # 去重保序（dict.fromkeys 保持插入顺序，最新出现的在前）
    batches = list(dict.fromkeys(r[0] for r in batch_rows))

    # 场景：同上
    scene_rows = db.session.query(
        Subject.collection_scene,
    ).filter(
        Subject.collection_scene.isnot(None),
        Subject.collection_scene != "",
    ).order_by(Subject.created_at.desc()).all()
    scenes = list(dict.fromkeys(r[0] for r in scene_rows))

    return success({"batches": batches, "scenes": scenes})


@data_bp.route("/subjects", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
@retry_on_deadlock(max_retries=2, delay=0.3)
def create_subject():
    """创建受试者"""
    data = request.get_json(silent=True) or {}
    subject = _svc_subject().create_subject(data)
    return success(subject.to_dict(), message="创建成功", code=201)


@data_bp.route("/subjects/<int:subject_id>", methods=["PUT"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def update_subject(subject_id):
    """更新受试者信息"""
    data = request.get_json(silent=True) or {}
    subject = _svc_subject().update_subject(subject_id, data)
    return success(subject.to_dict(), message="更新成功")


@data_bp.route("/subjects/<int:subject_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_subject(subject_id):
    """删除受试者（级联删除数据资产、标注任务、版本记录与磁盘文件）"""
    storage_root = current_app.config["DATA_LAKE_DIR"]
    _svc_subject().delete_subject(subject_id, storage_root)
    return success(message="受试者及其数据资产已删除")


# ====================== 数据资产管理 ======================

@data_bp.route("/assets", methods=["GET"])
@jwt_required()
def list_assets():
    """数据资产列表（支持 status/keyword/subject_id/data_type/layer 过滤）"""
    result = _svc_asset().list_assets(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
        subject_id=request.args.get("subject_id", type=int),
        data_type=request.args.get("data_type", "").strip(),
        layer=request.args.get("layer", "").strip(),
        status=request.args.get("status", "").strip(),
        keyword=request.args.get("keyword", "").strip(),
        ids_only=request.args.get("ids_only", "false").lower() == "true",
    )
    return success(result)


@data_bp.route("/assets", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def create_asset():
    """登记数据资产（上传后的元数据入库，正式实现含文件上传校验）"""
    data = request.get_json(silent=True) or {}
    asset = _svc_asset().create_asset(data)
    return success(asset.to_dict(), message="登记成功", code=201)


@data_bp.route("/assets/<int:asset_id>", methods=["PUT"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def update_asset(asset_id):
    """更新数据资产元数据（不重新上传文件，仅修改元信息）"""
    data = request.get_json(silent=True) or {}
    asset = _svc_asset().update_asset(asset_id, data)
    return success(asset.to_dict(), message="更新成功")


@data_bp.route("/assets/<int:asset_id>", methods=["DELETE"])
@role_required(Role.ADMIN)
def delete_asset(asset_id):
    """删除单个数据资产（关联记录 + 磁盘文件 + 转码缓存）"""
    storage_root = current_app.config["DATA_LAKE_DIR"]
    _svc_asset().delete_asset(asset_id, storage_root)
    return success(message="数据资产已删除")


@data_bp.route("/parse-userinfo", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def parse_userinfo():
    """解析 userInfo.json 文件，返回受试者字段映射
    前端新增受试者选择文件夹时调用：自动读取 userInfo 填充表单

    支持三种文件形态：
    1. userInfo.json（明文）
    2. userInfo.json.enc（外部 AES-CBC 加密，用数据库外部密钥解密）
    3. DMEC 加密格式

    请求：multipart/form-data，字段 file = userInfo 文件
    返回：{fields: {pseudo_id, age, gender, ...}, raw: {...原始JSON}}
    """
    if "file" not in request.files:
        return fail("未检测到上传文件", 422)
    file = request.files["file"]
    if not file.filename:
        return fail("文件名为空", 422)

    import tempfile, os
    from app.utils.scanner import _parse_user_info, _read_file_plaintext

    # 读取上传文件内容
    raw_bytes = file.read()
    filename_lower = (file.filename or "").lower()

    try:
        # 明文 JSON
        if filename_lower.endswith(".json"):
            plaintext = raw_bytes
        else:
            # 加密文件（.enc 或 DMEC）：写临时文件用 _read_file_plaintext 解密
            fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(filename_lower)[1])
            try:
                os.write(fd, raw_bytes)
                os.close(fd)
                plaintext = _read_file_plaintext(tmp_path)
                if plaintext is None:
                    return fail("文件解密失败：数据库无可用外部密钥或密钥不匹配，请先到「系统设置 → 外部密钥管理」上传外部密钥", 422)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 解析 JSON 并映射字段
        # 复用 scanner 的容错 JSON 解析 + 字段映射逻辑
        from app.utils.scanner import _parse_json_lenient, _parse_user_info
        raw_text = plaintext.decode("utf-8", errors="replace")
        data = _parse_json_lenient(raw_text)
        if data is None:
            return fail("解析失败：JSON 格式错误", 422)

        # 写临时文件用 _parse_user_info 解析（复用字段映射逻辑）
        fd2, tmp_json = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd2, plaintext)
            os.close(fd2)
            fields = _parse_user_info(tmp_json) or {}
        finally:
            try:
                os.remove(tmp_json)
            except OSError:
                pass

        # collection_time 转字符串（前端 date-picker 需要）
        from app.utils.time import to_local_str
        if fields.get("collection_time"):
            fields["collection_time"] = to_local_str(fields["collection_time"])

        return success({"fields": fields, "raw": data}, message="解析成功")
    except ValueError as e:
        # 外部加密文件无可用密钥：保留可操作提示（用户需先上传外部密钥）
        if "外部密钥" in str(e):
            return fail(
                "文件解密失败：数据库无可用外部密钥或密钥不匹配，"
                "请先到「系统设置 → 外部密钥管理」上传外部密钥", 422)
        logger.exception("解析 userInfo.json 失败")
        return fail("解析失败，请检查文件格式后重试", 422)
    except Exception:
        logger.exception("解析 userInfo.json 失败")
        return fail("解析失败，请检查文件格式后重试", 422)


@data_bp.route("/parse-sync-data", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def parse_sync_data():
    """解析眼动评估数据 sync_data.json，返回原始字段（仅解析存储，不映射、不分级）

    支持明文 .json 和加密文件（.enc / DMEC）。
    不做字段映射（不映射到 Subject 字段），仅返回解析后的原始 JSON。

    请求：multipart/form-data，字段 file = sync_data.json 文件
    返回：{raw: {...原始JSON}}
    """
    if "file" not in request.files:
        return fail("未检测到上传文件", 422)
    file = request.files["file"]
    if not file.filename:
        return fail("文件名为空", 422)

    import tempfile, os
    from app.utils.eye_tracking_adapter import load_sync_data
    from app.utils.scanner import _read_file_plaintext

    raw_bytes = file.read()
    filename_lower = (file.filename or "").lower()

    try:
        # 明文 JSON
        if filename_lower.endswith(".json"):
            plaintext = raw_bytes
        else:
            # 加密文件：写临时文件用 _read_file_plaintext 解密
            fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(filename_lower)[1])
            try:
                os.write(fd, raw_bytes)
                os.close(fd)
                plaintext = _read_file_plaintext(tmp_path)
                if plaintext is None:
                    return fail("文件解密失败：数据库无可用外部密钥或密钥不匹配", 422)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 写临时文件用 load_sync_data 解析（仅解析，不映射）
        fd2, tmp_json = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd2, plaintext)
            os.close(fd2)
            raw = load_sync_data(tmp_json)
            if raw is None:
                return fail("sync_data.json 解析失败：无效的 JSON", 422)
        finally:
            try:
                os.remove(tmp_json)
            except OSError:
                pass

        return success({"raw": raw}, message="解析成功")
    except ValueError as e:
        # 外部加密文件无可用密钥：保留可操作提示（用户需先上传外部密钥）
        if "外部密钥" in str(e):
            return fail(
                "文件解密失败：数据库无可用外部密钥或密钥不匹配，"
                "请先到「系统设置 → 外部密钥管理」上传外部密钥", 422)
        logger.exception("解析 sync_data.json 失败")
        return fail("解析失败，请检查文件格式后重试", 422)
    except Exception:
        logger.exception("解析 sync_data.json 失败")
        return fail("解析失败，请检查文件格式后重试", 422)


@data_bp.route("/parse-scale", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def parse_scale_data():
    """解析量表数据 JSON（如 MoCA），返回原始字段与得分摘要（仅解析存储，不映射、不分级）

    支持明文 .json 和加密文件（.enc / DMEC）。
    MoCA 量表额外返回总分与分项得分摘要。

    请求：multipart/form-data，字段 file = 量表 JSON 文件
    返回：{raw: {...原始JSON}, summary: {...MoCA摘要}} 或 {raw: {...}}
    """
    if "file" not in request.files:
        return fail("未检测到上传文件", 422)
    file = request.files["file"]
    if not file.filename:
        return fail("文件名为空", 422)

    import tempfile, os
    from app.utils.scale_adapter import load_scale_data, parse_moca_summary
    from app.utils.scanner import _read_file_plaintext

    raw_bytes = file.read()
    filename_lower = (file.filename or "").lower()

    try:
        # 明文 JSON
        if filename_lower.endswith(".json"):
            plaintext = raw_bytes
        else:
            fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(filename_lower)[1])
            try:
                os.write(fd, raw_bytes)
                os.close(fd)
                plaintext = _read_file_plaintext(tmp_path)
                if plaintext is None:
                    return fail("文件解密失败：数据库无可用外部密钥或密钥不匹配", 422)
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        # 写临时文件解析
        fd2, tmp_json = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd2, plaintext)
            os.close(fd2)
            raw = load_scale_data(tmp_json)
            if raw is None:
                return fail("量表 JSON 解析失败：无效的 JSON", 422)
        finally:
            try:
                os.remove(tmp_json)
            except OSError:
                pass

        result = {"raw": raw}
        # MoCA 量表额外返回得分摘要
        summary = parse_moca_summary(raw)
        if summary:
            result["summary"] = summary

        return success(result, message="解析成功")
    except ValueError as e:
        # 外部加密文件无可用密钥：保留可操作提示（用户需先上传外部密钥）
        if "外部密钥" in str(e):
            return fail(
                "文件解密失败：数据库无可用外部密钥或密钥不匹配，"
                "请先到「系统设置 → 外部密钥管理」上传外部密钥", 422)
        logger.exception("解析量表 JSON 失败")
        return fail("解析失败，请检查文件格式后重试", 422)
    except Exception:
        logger.exception("解析量表 JSON 失败")
        return fail("解析失败，请检查文件格式后重试", 422)


@data_bp.route("/assets/upload", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
@retry_on_deadlock(max_retries=2, delay=0.3)
def upload_asset():
    """实际上传文件并登记数据资产，按受试者伪ID分目录存储
    存储结构：{DATA_LAKE_DIR}/{layer}/{subject_pseudo_id}/{data_type}/{filename}

    视频可选参数：video_type（face/body/gait），用于受试者视频采集的子类型。
    重采时会按 video_type 精准替换（不影响其他类型视频）。
    """
    subject_id = request.form.get("subject_id", type=int)
    data_type = request.form.get("data_type")
    layer = request.form.get("layer", "raw")
    sample_rate = request.form.get("sample_rate", type=int)
    video_type = (request.form.get("video_type") or "").strip() or None

    if "file" not in request.files:
        return fail("未检测到上传文件", 422)
    file = request.files["file"]
    if not file.filename:
        return fail("文件名为空", 422)

    asset, is_duplicate = _svc_asset().upload_asset(
        subject_id=subject_id, data_type=data_type,
        file_storage=file, layer=layer, sample_rate=sample_rate,
        video_type=video_type,
    )
    if is_duplicate:
        return success(asset.to_dict(), message="文件已存在，跳过重复上传")
    return success(asset.to_dict(), message="上传成功", code=201)


@data_bp.route("/assets/batch", methods=["POST"])
@role_required(Role.ADMIN, Role.NURSE, Role.ENGINEER)
def batch_create_assets():
    """批量登记数据资产（支持整个文件夹导入）"""
    data = request.get_json(silent=True) or {}
    subject_id = data.get("subject_id")
    items = data.get("assets", [])
    created = _svc_asset().batch_create_assets(subject_id, items)
    return success(
        {"count": len(created), "items": [a.to_dict() for a in created]},
        message=f"成功导入 {len(created)} 条数据资产",
        code=201,
    )


# ====================== 文件下载与播放 ======================

@data_bp.route("/assets/<int:asset_id>/file", methods=["GET"])
@media_auth_required("asset_file", resource_key="asset_id", roles=(Role.ADMIN,))
def serve_asset_file(asset_id):
    """流式提供数据文件原始下载（仅 ADMIN）
    鉴权：仅管理员可调用，用于下载原始文件。普通角色请用 /play 在线播放。
    所有下载操作记录审计日志。加密文件会先解密到临时文件，响应结束后自动清理。
    """
    serve_path, is_temp, mimetype, download_name = _svc_asset().serve_asset_file(asset_id)
    if is_temp:
        _register_temp_cleanup(serve_path)
    # send_file 自动处理 Range 请求；显式指定 mimetype 与 download_name——
    # 加密文件解密临时文件若无扩展名，send_file 会把 MIME 推断为 octet-stream、
    # 下载名为 tmpXXXX，导致浏览器下载/播放均无法识别（用户误以为格式不支持）。
    resp = send_file(serve_path, conditional=True, mimetype=mimetype,
                     download_name=download_name, as_attachment=True)
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


@data_bp.route("/assets/<int:asset_id>/play", methods=["GET"])
@media_auth_required("asset_play", resource_key="asset_id")
def play_asset(asset_id):
    """播放数据文件：浏览器原生格式直接流，其余视频格式自动转码为 H.264 MP4 后流式播放
    鉴权：优先 ?media_token=<短期签名>（由 /api/media/signed-url 签发，资源绑定、5 分钟过期）；
    兼容 ?access_token=<JWT> 或 Authorization 头（仅在线播放，不能下载原文件）。
    转码结果缓存到 {DATA_LAKE_DIR}/.transcodes/{asset_id}.mp4，首次较慢，后续秒开。
    加密文件播放流程：源文件解密到临时文件 -> 转码/直接播放 -> 响应后清理临时文件。
    """
    serve_path, is_temp, mimetype = _svc_asset().play_asset(asset_id)
    if is_temp:
        _register_temp_cleanup(serve_path)
    # 显式声明 Accept-Ranges，避免 200 响应缺失该头导致浏览器中止媒体请求
    resp = send_file(serve_path, conditional=True, mimetype=mimetype)
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


@data_bp.route("/assets/export/start", methods=["POST"])
@role_required(Role.ADMIN)
def export_assets_start():
    """启动异步导出任务（仅 ADMIN）

    请求体支持：
      asset_ids: [int]                # 模式一：指定资产 ID 列表
      subject_ids: [int] / subject_id: int      # 模式二：按受试者筛选（空=全部）
      data_types: [str] / data_type: str         # 按数据类型筛选（空=全部）
      layers: [str] / layer: str                 # 按数据层筛选（空=全部）
      encrypted: bool                           # 是否加密导出（默认 True）

    立即返回 task_id，后台线程打包，前端轮询 /progress 获取进度。

    返回: {task_id: str}
    """
    data = request.get_json(silent=True) or {}
    task_id = export_task_manager.create_task(
        data,
        operator_id=int(get_jwt_identity()),
        operator_role=current_role(),
    )
    return success({"task_id": task_id}, message="导出任务已启动")


@data_bp.route("/assets/export/progress/<task_id>", methods=["GET"])
@role_required(Role.ADMIN)
def export_assets_progress(task_id):
    """查询异步导出任务进度（仅 ADMIN）

    返回: {
        task_id, status(pending/running/success/failed),
        percent, status_text,
        total_files, processed_files,
        total_size, processed_size,
        filename, error
    }
    """
    task = export_task_manager.get_task(task_id)
    if not task:
        return fail("任务不存在或已过期", 404)
    return success(task)


@data_bp.route("/assets/export/download/<task_id>", methods=["GET"])
@media_auth_required("asset_export", resource_key="task_id", roles=(Role.ADMIN,))
def export_assets_download(task_id):
    """下载已完成的导出 zip（仅 ADMIN）

    流式发送（不整读内存），响应 body 发送完成后自动清理临时文件与任务。
    仅限 status=success 的任务。
    """
    result = export_task_manager.download_task(task_id)
    if not result:
        return fail("任务未完成或不存在", 404)
    zip_path, filename = result
    if not os.path.exists(zip_path):
        export_task_manager.cleanup_task(task_id)
        return fail("导出文件不存在或已清理", 404)

    resp = send_file(
        zip_path, as_attachment=True,
        download_name=filename, mimetype="application/zip",
        max_age=0, conditional=True,
    )
    # call_on_close 在响应 body 完全发送后执行，避免 after_request 在
    # 大文件流式传输前删除文件（Windows 上删除被占用文件会失败）
    resp.call_on_close(lambda: export_task_manager.cleanup_task(task_id))
    return resp


# ====================== 数据清洗与标准化 ======================

@data_bp.route("/cleaning/tasks", methods=["POST"])
@role_required(Role.ADMIN, Role.ENGINEER)
def trigger_cleaning():
    """触发数据清洗任务（同步模拟：更新状态 + 创建版本 + 记录日志）
    框架阶段同步执行，后续可替换为 Celery 异步任务。
    """
    data = request.get_json(silent=True) or {}
    config = {
        "missing": data.get("missing"),
        "outlier": data.get("outlier"),
        "denoise": data.get("denoise"),
    }
    result = _svc_processing().trigger_cleaning(data.get("asset_ids", []), config)
    return success(
        result,
        message=f"清洗完成，共处理 {result['count']} 个数据资产",
    )


@data_bp.route("/standardization/tasks", methods=["POST"])
@role_required(Role.ADMIN, Role.ENGINEER)
def trigger_standardization():
    """触发数据标准化任务（同步模拟：更新状态 + 创建版本 + 记录日志）
    框架阶段同步执行，后续可替换为 Celery 异步任务。
    """
    data = request.get_json(silent=True) or {}
    rates = {
        "eeg": data.get("eeg_rate"),
        "ecg": data.get("ecg_rate"),
        "eye": data.get("eye_rate"),
        "gait": data.get("gait_rate"),
    }
    result = _svc_processing().trigger_standardization(data.get("asset_ids", []), rates)
    return success(
        result,
        message=f"标准化完成，共处理 {result['count']} 个数据资产",
    )


# ====================== 操作日志 ======================

@data_bp.route("/operation-logs", methods=["GET"])
@jwt_required()
def list_operation_logs():
    """操作日志列表
    - admin：可查看全部日志，并按 username/action/target_type 筛选
    - 其他角色：仅可查看自己的操作日志（满足自审需求），筛选用户名参数自动忽略
    """
    result = _svc_snapshot().list_operation_logs(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
        username=request.args.get("username", "").strip(),
        action=request.args.get("action", "").strip(),
        target_type=request.args.get("target_type", "").strip(),
    )
    return success(result)


@data_bp.route("/operation-logs/stats", methods=["GET"])
@jwt_required()
@retry_on_deadlock(max_retries=2, delay=0.3)
def get_operation_log_stats():
    """操作日志统计（用于可视化）
    - admin：可查看全部日志统计，支持按 username 模糊筛选
    - 其他角色：仅统计自己的操作日志，username 参数自动忽略
    - 参数 days：趋势图天数（默认 7，范围 1-90）
    """
    result = _svc_snapshot().get_operation_log_stats(
        username=request.args.get("username", "").strip(),
        days=request.args.get("days", 7, type=int),
    )
    return success(result)


@data_bp.route("/stats", methods=["GET"])
@jwt_required()
@retry_on_deadlock(max_retries=2, delay=0.3)
def get_data_stats():
    """数据管理综合统计（用于数据动态可视化）

    返回受试者与数据资产的实时统计：
    - subject_total / asset_total: 受试者/数据资产总数
    - today_subjects / today_assets: 今日（北京时间）新增数
    - last_n_days: 最近 N 天每日新增 [{date, subject_count, asset_count}]（北京时间）
    - type_distribution: 数据资产模态分布 [{data_type, count}]
    - layer_distribution: 数据湖分层分布 [{layer, count}]
    - risk_distribution: 受试者认知风险分级分布 [{risk_level, count}]
    - gender_distribution: 受试者性别分布 [{gender, count}]
    - days: 实际统计的天数
    """
    days = request.args.get("days", 30, type=int)
    if days < 1:
        days = 30
    if days > 365:
        days = 365

    # 总数
    subject_total = Subject.query.count()
    asset_total = DataAsset.query.count()

    # 今日（北京时间 00:00 起）新增
    now_cn = datetime.utcnow() + _CN_TZ_DELTA
    today_cn_start_utc = datetime(now_cn.year, now_cn.month, now_cn.day) - _CN_TZ_DELTA
    today_subjects = Subject.query.filter(Subject.created_at >= today_cn_start_utc).count()
    today_assets = DataAsset.query.filter(DataAsset.created_at >= today_cn_start_utc).count()

    # 最近 N 天每日新增（按北京时间分组）
    n_days_ago_cn_start_utc = today_cn_start_utc - timedelta(days=days - 1)
    subject_rows = Subject.query.filter(
        Subject.created_at >= n_days_ago_cn_start_utc
    ).with_entities(Subject.created_at).all()
    asset_rows = DataAsset.query.filter(
        DataAsset.created_at >= n_days_ago_cn_start_utc
    ).with_entities(DataAsset.created_at).all()

    daily_map = {}
    for i in range(days):
        d = (now_cn - timedelta(days=i)).strftime("%Y-%m-%d")
        daily_map[d] = {"subject": 0, "asset": 0}
    for (created_at,) in subject_rows:
        if not created_at:
            continue
        d = (created_at + _CN_TZ_DELTA).strftime("%Y-%m-%d")
        if d in daily_map:
            daily_map[d]["subject"] += 1
    for (created_at,) in asset_rows:
        if not created_at:
            continue
        d = (created_at + _CN_TZ_DELTA).strftime("%Y-%m-%d")
        if d in daily_map:
            daily_map[d]["asset"] += 1
    last_n_days = [
        {"date": d, "subject_count": daily_map[d]["subject"], "asset_count": daily_map[d]["asset"]}
        for d in sorted(daily_map.keys())
    ]

    # 数据资产模态分布
    type_rows = DataAsset.query.with_entities(
        DataAsset.data_type, func.count(DataAsset.id)
    ).group_by(DataAsset.data_type).all()
    type_distribution = [
        {"data_type": t.value if t else "unknown", "count": c} for t, c in type_rows
    ]
    type_distribution.sort(key=lambda x: x["count"], reverse=True)

    # 数据湖分层分布
    layer_rows = DataAsset.query.with_entities(
        DataAsset.layer, func.count(DataAsset.id)
    ).group_by(DataAsset.layer).all()
    layer_distribution = [
        {"layer": l.value if l else "unknown", "count": c} for l, c in layer_rows
    ]
    layer_distribution.sort(key=lambda x: x["count"], reverse=True)

    # 受试者认知风险分级分布（NULL 与空字符串合并为 "none"）
    risk_rows = Subject.query.with_entities(
        Subject.cognitive_risk_level, func.count(Subject.id)
    ).group_by(Subject.cognitive_risk_level).all()
    risk_map = {}
    for r, c in risk_rows:
        key = r if (r and r.strip()) else "none"
        risk_map[key] = risk_map.get(key, 0) + c
    risk_distribution = [{"risk_level": k, "count": v} for k, v in risk_map.items()]
    risk_distribution.sort(key=lambda x: x["count"], reverse=True)

    # 受试者性别分布
    gender_rows = Subject.query.with_entities(
        Subject.gender, func.count(Subject.id)
    ).group_by(Subject.gender).all()
    gender_distribution = [
        {"gender": g or "unknown", "count": c} for g, c in gender_rows
    ]
    gender_distribution.sort(key=lambda x: x["count"], reverse=True)

    return success({
        "subject_total": subject_total,
        "asset_total": asset_total,
        "today_subjects": today_subjects,
        "today_assets": today_assets,
        "last_n_days": last_n_days,
        "type_distribution": type_distribution,
        "layer_distribution": layer_distribution,
        "risk_distribution": risk_distribution,
        "gender_distribution": gender_distribution,
        "days": days,
    })


# ====================== 数据快照与版本回滚 ======================

@data_bp.route("/snapshots", methods=["GET"])
@jwt_required()
def list_snapshots():
    """数据快照列表（支持 model_type/model_id 筛选）
    权限隔离：admin 可查看全部；其他角色仅能查看自己创建的快照（operator_id == 自己）
    非 admin 用户查看快照内容时会自动脱敏敏感字段。
    """
    result = _svc_snapshot().list_snapshots(
        page=request.args.get("page", 1, type=int),
        page_size=request.args.get("page_size", 20, type=int),
        model_type=request.args.get("model_type", "").strip(),
        model_id=request.args.get("model_id", type=int),
        action=request.args.get("action", "").strip(),
    )
    return success(result)


@data_bp.route("/snapshots/<int:snapshot_id>", methods=["GET"])
@jwt_required()
def get_snapshot(snapshot_id):
    """获取单条快照详情
    权限隔离：非 admin 仅能查看自己创建的快照；非 admin 查看时快照内容自动脱敏。
    """
    data = _svc_snapshot().get_snapshot(snapshot_id)
    return success(data)


@data_bp.route("/snapshots/<int:snapshot_id>/rollback", methods=["POST"])
@role_required(Role.ADMIN)
def rollback_snapshot_api(snapshot_id):
    """回滚到指定快照版本（仅管理员，回滚前会自动留档当前状态）"""
    result = _svc_snapshot().rollback_snapshot(snapshot_id)
    return success(result, message="已回滚到历史版本")