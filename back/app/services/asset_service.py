# -*- coding: utf-8 -*-
"""数据资产 + 文件服务

封装原 api/data.py 的资产 CRUD、文件上传、文件下载/播放逻辑。
对应路由：list_assets / create_asset / update_asset / delete_asset /
         upload_asset / batch_create_assets / serve_asset_file / play_asset

业务规则：
- 上传：扩展名校验 + magic bytes 校验 + 命名规范应用 + 路径穿越防护 + 幂等去重
- 上传：启用加密时直接将上传流加密写入磁盘（不留明文临时文件）
- 下载/播放：加密文件先解密到临时文件，响应后清理
- 播放：原生格式直接流；非原生视频格式用 ffmpeg 转码 H.264 MP4 缓存
- 删除：级联清理标注任务/标注/版本记录 + 磁盘文件 + 转码缓存
- 列表按角色脱敏（admin 不脱敏）

文件服务返回值约定：
- serve_asset_file / play_asset 返回 (path, is_temp, mimetype, after_cleanup_callback)
- 路由层负责调用 send_file + 注册 after_this_request 清理钩子
- 这样 service 不依赖 Flask 的 after_this_request，可独立测试
"""
import os
import mimetypes
import threading
from contextlib import contextmanager
from typing import Tuple, Optional

from flask import current_app

from app.extensions import db
from app.models import DataAsset, DataType, DataLayer, Subject, VIDEO_TYPES
from app.services.base import (
    BaseService, ValidationError, NotFoundError, OperationNotAllowedError,
)
from app.services.subject_service import (
    _purge_asset_records, _purge_asset_files, _remove_file_safely, _collect_asset_file_paths,
)
from app.utils.audit import log_operation, snapshot_update, snapshot_delete
from app.models.data_snapshot import save_snapshot
from app.utils.crypto import (
    encrypt_stream_to_file, is_encrypted_file, decrypt_file_to_temp,
    encrypt_bytes, decrypt_external_bytes_auto,
)
from app.utils.desensitize import desensitize_list
from app.utils.file_signature import check_signature
from app.utils.like_query import build_like_contains
from datetime import datetime
from app.utils.collection_time import resolve_collection_time
from app.utils.naming import (
    apply_naming_standard, get_naming_standard, validate_extension,
)
from app.utils.scale_adapter import detect_scale_type
from app.utils.source_identity import (
    normalize_original_filename, stored_original_filename, source_group_key,
)
from app.utils.response import paginate
from werkzeug.utils import secure_filename
from app.utils.naming import _safe_segment as safe_filename_segment


# ==================== 上传幂等互斥锁 ====================
# upload_asset 的幂等去重是 check-then-act（先查重后入库），并发上传同一文件时
# 两个请求会同时查不到既有记录而双双入库（重复录入），甚至规范化文件名不同
# 形成两条独立资产。并发来源：多标签页/多设备同时扫描同一目录（uploadedMap
# 整轮扫描结束才写回）、网络错误重试与仍在处理中的首次请求重叠、自动扫描与
# 手动上传并发。单 gunicorn worker 部署（GUNICORN_WORKERS=1）下进程内按幂等
# key 加锁即可完全串行化同一文件的上传，不同文件互不影响。
_idem_guard = threading.Lock()
_idem_locks = {}  # 幂等 key -> [Lock, refcount]


@contextmanager
def _idem_lock_for(key):
    """按上传幂等 key 获取互斥锁（引用计数管理，用完即回收，防内存增长）"""
    with _idem_guard:
        entry = _idem_locks.get(key)
        if entry is None:
            entry = [threading.Lock(), 0]
            _idem_locks[key] = entry
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _idem_guard:
            entry[1] -= 1
            if entry[1] <= 0 and _idem_locks.get(key) is entry:
                del _idem_locks[key]


class AssetService(BaseService):
    """数据资产管理服务"""

    # ==================== 资产 CRUD ====================

    def list_assets(self, page: int = 1, page_size: int = 20,
                    subject_id: Optional[int] = None, data_type: str = "",
                    layer: str = "", status: str = "", keyword: str = "",
                    ids_only: bool = False) -> dict:
        """数据资产列表（支持 status/keyword/subject_id/data_type/layer 过滤）

        ids_only=True 时仅返回当前筛选条件下的全部 ID 列表（不分页，轻量），
        用于前端跨页全选。返回格式：{"items": [{"id": 1}, ...], "total": N}
        """
        query = DataAsset.query
        if subject_id:
            query = query.filter_by(subject_id=subject_id)
        if data_type:
            query = query.filter(DataAsset.data_type == DataType(data_type))
        if layer:
            query = query.filter(DataAsset.layer == DataLayer(layer))
        if status:
            query = query.filter_by(status=status)
        if keyword:
            query = query.filter(DataAsset.file_name.like(build_like_contains(keyword), escape="|"))
        query = query.order_by(DataAsset.created_at.desc())
        if ids_only:
            rows = query.with_entities(DataAsset.id, DataAsset.data_type).all()
            return {"items": [{"id": i, "data_type": dt.value if dt else None} for i, dt in rows],
                    "total": len(rows)}
        result = paginate(query, page, page_size)
        # 按角色脱敏（admin 不脱敏）
        desensitize_list(result["items"], self.operator_role)
        return result

    def create_asset(self, data: dict):
        """登记数据资产（上传后的元数据入库）"""
        subject_id = data.get("subject_id")
        data_type = data.get("data_type")
        if not subject_id or not data_type:
            raise ValidationError("受试者ID与数据类型必填")
        try:
            dt = DataType(data_type)
        except ValueError:
            raise ValidationError("数据类型非法")

        asset = DataAsset(
            subject_id=subject_id,
            data_type=dt,
            layer=DataLayer(data.get("layer", "raw")),
            file_name=data.get("file_name", ""),
            file_path=data.get("file_path", ""),
            file_format=data.get("file_format"),
            file_size=data.get("file_size", 0),
            metadata_json=data.get("metadata"),
            sample_rate=data.get("sample_rate"),
            # 采集时间：元数据显式采集字段 > 文件名时间戳 > 入库时刻
            timestamp_utc=resolve_collection_time(
                data.get("metadata"),
                extra_names=[data.get("file_name", "")],
                fallback_utc=datetime.utcnow(),
            ),
        )
        self.session.add(asset)
        self.session.flush()  # 让 asset.id 可用
        # 创建后留档（best-effort，便于历史追溯）
        try:
            save_snapshot("data_asset", asset.id, asset.to_dict(), "create",
                          self._operator_user(), "登记数据资产")
        except Exception:
            pass
        log_operation("create", "asset", asset.id, f"登记数据资产 {asset.file_name}",
                      operator=self._operator_user())
        # 业务数据 + 快照 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
        self._commit()
        return asset

    def update_asset(self, asset_id: int, data: dict):
        """更新数据资产元数据（不重新上传文件，仅修改元信息）"""
        asset = self._get_or_404(DataAsset, asset_id, "数据资产不存在")
        old_dict = asset.to_dict()  # 修改前快照

        # 数据类型校验
        if data.get("data_type"):
            try:
                asset.data_type = DataType(data["data_type"])
            except ValueError:
                raise ValidationError("数据类型非法")
        # 分层校验
        if data.get("layer"):
            try:
                asset.layer = DataLayer(data["layer"])
            except ValueError:
                raise ValidationError("数据分层非法")
        for f in ["file_name", "file_path", "file_format", "sample_rate"]:
            if f in data:
                setattr(asset, f, data[f])
        if "file_size" in data:
            asset.file_size = data["file_size"]
        if "metadata" in data:
            asset.metadata_json = data["metadata"]

        snapshot_update("data_asset", asset, old_dict, asset.to_dict(),
                        operator=self._operator_user())
        log_operation("update", "asset", asset_id, f"更新资产 {asset.file_name}",
                      operator=self._operator_user())
        # 业务数据 + 快照 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
        self._commit()
        return asset

    def delete_asset(self, asset_id: int, storage_root: str):
        """删除单个数据资产（关联记录 + 磁盘文件 + 转码缓存）

        事务安全：先 DB commit（保护数据一致性），commit 成功后再删磁盘文件
        （若 commit 失败则磁盘文件保留，可重试；避免磁盘已删但 DB 未删的数据丢失）
        """
        asset = self._get_or_404(DataAsset, asset_id, "数据资产不存在")
        file_name = asset.file_name
        # 删除前留档
        snapshot_delete("data_asset", asset, f"删除资产 {file_name}",
                        operator=self._operator_user())
        # 先删除关联子表记录（外键约束），并预收集磁盘文件路径
        _purge_asset_records(asset)
        pending_files = _collect_asset_file_paths(asset, storage_root)
        self.session.delete(asset)
        log_operation("delete", "asset", asset_id, f"删除资产 {file_name}",
                      operator=self._operator_user())
        # 先提交 DB（事务保护），失败时 rollback，磁盘文件未被影响
        self._commit()
        # DB commit 成功后再删磁盘文件（best-effort，失败仅记录日志，不影响已成功的 DB 操作）
        _remove_file_safely(pending_files[0])
        _remove_file_safely(pending_files[1])

    def ingest_digest(self, pseudo_ids: list, chunk_size: int = 500):
        """按伪ID批量返回资产导入摘要 {pseudo_id: [[original_filename, original_size], ...]}

        浏览器目录扫描增量去重的对账数据源：前端 localStorage 的「已上传记录」
        无法感知平台侧删除动作（资产删除 / 受试者级联删除），删除过的文件会被
        本地记录永久判定为"已上传"而跳过。前端每次扫描前调用本接口对账，
        作废后端已不存在对应资产的本地记录，使删除过的文件可重新入库。

        无 original_filename 的资产（手动登记等）不参与摘要：此类资产本就不在
        上传幂等键覆盖范围内，浏览器扫描重传时会按既有幂等规则新建记录。
        """
        cleaned = [str(p).strip() for p in (pseudo_ids or []) if p and str(p).strip()]
        if not cleaned:
            return {}
        # in 查询分批执行，避免超长 SQL
        digest = {}
        for i in range(0, len(cleaned), chunk_size):
            chunk = cleaned[i:i + chunk_size]
            rows = (
                db.session.query(Subject.pseudo_id, DataAsset.metadata_json)
                .join(DataAsset, DataAsset.subject_id == Subject.id)
                .filter(Subject.pseudo_id.in_(chunk))
                .all()
            )
            for pseudo_id, meta in rows:
                # 归一化为纯文件名：存量数据可能带相对路径前缀（老版本浏览器
                # 目录扫描入库），而前端本地记录用 File.name（纯名），不归一会
                # 判为"库中不存在"而作废重传，反而制造重复。
                fn = stored_original_filename((meta or {}).get("original_filename"))
                if not fn:
                    continue
                digest.setdefault(pseudo_id, []).append(
                    [fn, (meta or {}).get("original_size")]
                )
        return digest

    def batch_create_assets(self, subject_id: int, items: list):
        """批量登记数据资产（支持整个文件夹导入）"""
        if not subject_id:
            raise ValidationError("受试者ID必填")
        if not items or not isinstance(items, list):
            raise ValidationError("待导入数据列表不能为空")

        created = []
        subject = self._get_or_404(Subject, subject_id, "受试者不存在")
        for item in items:
            data_type = item.get("data_type")
            if not data_type:
                continue
            try:
                dt = DataType(data_type)
            except ValueError:
                continue
            file_name = item.get("file_name", "")
            # 批量/文件夹导入（数据管理页）前端不发 metadata，此前这些记录的
            # metadata_json 为 None，导致它们既不参与上传幂等、也不被启动自愈
            # 纳入（自愈第一步就按 original_filename 为空跳过），成为重复数据的
            # "死角"。这里与单文件上传对齐，统一补写同源身份字段。
            raw_meta = item.get("metadata")
            meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
            if file_name and not meta.get("original_filename"):
                meta["original_filename"] = stored_original_filename(file_name)
            if meta.get("original_size") is None and item.get("file_size") is not None:
                meta["original_size"] = item.get("file_size")
            # 与单文件上传一致，批量/文件夹接入也按命名规范自动重命名
            asset = DataAsset(
                subject_id=subject_id,
                data_type=dt,
                layer=DataLayer(item.get("layer", "raw")),
                file_name=self._apply_naming(
                    subject, dt.value, file_name, item.get("file_format")
                ) or file_name,
                file_path=item.get("file_path", ""),
                file_format=item.get("file_format"),
                file_size=item.get("file_size", 0),
                metadata_json=meta or None,
                sample_rate=item.get("sample_rate"),
                # 采集时间：元数据显式采集字段 > 文件名时间戳 > 入库时刻
                timestamp_utc=resolve_collection_time(
                    meta,
                    extra_names=[file_name],
                    fallback_utc=datetime.utcnow(),
                ),
            )
            self.session.add(asset)
            created.append(asset)
        self.session.flush()  # 让所有 asset.id 可用
        log_operation("batch_import", "asset", subject_id,
                      f"批量导入 {len(created)} 条数据资产",
                      operator=self._operator_user())
        # 批量数据 + 日志一次性原子提交
        self._commit()
        return created

    def _apply_naming(self, subject, data_type, original_filename, file_format=None):
        """按启用的命名规范生成规范化文件名（与 upload_asset 逻辑一致）

        userInfo.json 等原始文件名会保留 userinfo 语义（apply_naming_standard 已处理）。
        """
        if not original_filename:
            return ""
        import re as _re
        naming_std = get_naming_standard(data_type)
        scale_type = detect_scale_type(original_filename) if data_type == "scale" else None
        norm_name, norm_ext = apply_naming_standard(
            naming_std, subject, data_type, original_filename, scale_type=scale_type,
        )
        final_ext = norm_ext or (file_format or "")
        fn = f"{norm_name}.{final_ext}" if final_ext else norm_name
        fn = safe_filename_segment(fn) or "unnamed"
        fn = _re.sub(r'_+', '_', fn).strip('_') or "unnamed"
        return fn

    # ==================== 文件上传 ====================

    def upload_asset(self, subject_id: int, data_type: str,
                     file_storage, layer: str = "raw",
                     sample_rate: Optional[int] = None,
                     video_type: Optional[str] = None,
                     naming_video_type: Optional[str] = None):
        """实际上传文件并登记数据资产，按受试者伪ID分目录存储

        存储结构：{DATA_LAKE_DIR}/{layer}/{subject_pseudo_id}/{data_type}/{filename}

        业务规则：
        1. 扩展名校验（命名规范可配置 allowed_extensions）
        2. magic bytes 校验：仅对有明确签名的格式（mp4/wav/pdf）校验
        3. 幂等检查：同受试者+同模态+同原始文件名+同原始大小，视为重复上传
        4. 应用命名规范生成规范化文件名
        5. 路径穿越防护：确保最终路径仍在 DATA_LAKE_DIR 内
        6. 落盘：启用加密时直接将上传流加密写入磁盘（不留明文临时文件）
        7. 视频类型（face/body/gait）：存入 metadata.video_type，
           并按 video_type 精准重采（删除同类型旧视频，不影响其他类型）
        """
        if not subject_id or not data_type:
            raise ValidationError("受试者ID与数据类型必填")

        subject = self._get_or_404(Subject, subject_id, "受试者不存在")
        try:
            dt = DataType(data_type)
        except ValueError:
            raise ValidationError("数据类型非法")

        # 视频子类型校验（仅 video 模态支持 face/body/gait）
        # video_type 可选：视频采集弹窗传 face/body/gait，扫描上传不传（通用视频）
        if dt == DataType.VIDEO:
            if video_type and video_type not in VIDEO_TYPES:
                raise ValidationError(f"视频类型非法，仅支持 {', '.join(VIDEO_TYPES)}")
            # video_type 未传时不报错，作为通用视频上传（扫描场景）
        else:
            # 非 video 模态忽略 video_type，避免污染 metadata
            video_type = None

        original_name = file_storage.filename or "unnamed"
        # 检测外部加密文件（.enc 后缀）：需先用数据库外部密钥解密，再用内部密钥加密入库
        # 剥离 .enc 后缀取真实扩展名（如 xxx.wav.enc -> wav），文件名不再保留 .enc
        is_external_enc = original_name.lower().endswith(".enc")
        if is_external_enc:
            base_name = original_name[:-4]  # 去掉 .enc
            ext = base_name.rsplit(".", 1)[-1].lower() if "." in base_name else ""
        else:
            ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
        ok_ext, ext_msg = validate_extension(data_type, ext)
        if not ok_ext:
            raise ValidationError(ext_msg)

        # magic bytes 校验：仅对有明确签名的格式（mp4/wav/pdf）校验，无签名格式跳过
        # 外部加密文件内容是密文，跳过 magic 校验
        if not is_external_enc:
            ok_sig, sig_msg = check_signature(file_storage, ext)
            if not ok_sig:
                raise ValidationError(sig_msg)

        # 读取原始文件字节大小（file.stream 读取后需 seek 回 0）
        file_storage.stream.seek(0, 2)  # 移到末尾
        original_size = file_storage.stream.tell()
        file_storage.stream.seek(0)  # 重置到开头

        # 幂等 key 级互斥：查重是 check-then-act，并发上传同一文件（多标签页扫描/
        # 重试/多设备）会双双未命中而重复入库。持锁覆盖"查重→落盘→入库 commit"
        # 全程：同一文件的上传串行化，后进者在锁内查到先进者已提交的记录直接返回；
        # 不同文件 key 不同，互不影响。锁必须覆盖落盘——否则并发方会在先进者
        # commit 前写同一存储路径覆盖文件。
        with _idem_lock_for((subject_id, dt.value, original_name, original_size, video_type)):
            # 幂等检查：同受试者+同模态+同原始文件名+同原始大小（视频还需 video_type 一致）
            # 视为重复上传（前端定时扫描场景）。命名规范会改写存储文件名，所以用原始文件名作为去重 key
            # 只取必要列（id/file_name/metadata_json），避免每次上传全量拉取该受试者同模态的所有资产
            stale_ids = []  # 同名不同大小的旧资产（半成品），新文件入库后替换
            # 本次上传的归一化同源名（去目录前缀 / 去 .enc / 小写）
            source_name = normalize_original_filename(original_name)
            existing = (
                DataAsset.query
                .filter_by(subject_id=subject_id, data_type=dt)
                .with_entities(
                    DataAsset.id, DataAsset.file_name, DataAsset.metadata_json,
                )
                .all()
            )
            for ex in existing:
                meta = ex.metadata_json or {}
                # 同源判定用「归一化原始名」（去目录前缀 / 去 .enc / 小写）而非
                # original_filename 字面值：同一份磁盘文件经不同入口入库时形态不同
                # （浏览器目录扫描给 `伪ID/ecg_x.csv.enc`，后端 scanner 只给
                # `ecg_x.csv.enc`），字面比较会让同一份数据被判成两份而重复入库。
                if (normalize_original_filename(meta.get("original_filename")) != source_name
                        or meta.get("video_type") != video_type):
                    continue
                if meta.get("original_size") == original_size:
                    original_ref = f"（原始: {original_name}）" if original_name != ex.file_name else ""
                    log_operation("upload", "asset", ex.id,
                                  f"重复上传跳过（命中既有资产 {ex.file_name}）{original_ref} 到 {subject.pseudo_id}/{data_type}",
                                  operator=self._operator_user())
                    self._commit()
                    # 命中既有资产：顺带清理同源其他陈旧记录（同归一化原始名 + 同
                    # video_type），让该源文件收敛为一条——覆盖存量"半成品+
                    # 完整版共存"场景（重传完整版可能命中幂等，入库替换分支不触发，
                    # 半成品靠这里清掉）
                    for other in existing:
                        if other.id == ex.id:
                            continue
                        om = other.metadata_json or {}
                        if (normalize_original_filename(om.get("original_filename")) == source_name
                                and om.get("video_type") == video_type):
                            stale_ids.append(other.id)
                    if stale_ids:
                        self._purge_stale_uploads(stale_ids)
                    # 命中既有资产：补查完整模型实例返回（调用方需 to_dict）
                    return DataAsset.query.get(ex.id), True  # (asset, is_duplicate)
                # 同名演进上传：采集端录制中的文件被扫描先上传了半成品（持续写入
                # 时大小是中途值），录制完成后完整文件再上传时同名但大小不同，
                # 幂等 key 不命中会双入库（半成品+成品两条）。视为同一文件的更新，
                # 新文件入库后替换旧资产（先写新后删旧，新文件失败旧资产保留）
                stale_ids.append(ex.id)

            # 视频重采不在此处删旧：必须等新文件成功落盘入库后再删（见下方 _commit 之后），
            # 保证"先写新、后删旧"，新文件失败时旧视频仍保留，避免重采造成数据丢失。
            # 应用命名规范：查询启用的命名规范并生成规范化文件名
            # 外部加密文件传剥离 .enc 后的文件名，确保扩展名是真实类型（wav 而非 enc）
            # 视频传 video_type 用于命名区分（face/body/gait）；
            # naming_video_type 仅影响命名后缀（如深度资产），不写 metadata/不参与重采
            naming_input = base_name if is_external_enc else original_name
            naming_std = get_naming_standard(data_type)
            norm_name, norm_ext = apply_naming_standard(
                naming_std, subject, data_type, naming_input,
                video_type=naming_video_type or video_type,
                # 量表传入量表类型（MoCA/MMSE/AD8），使重命名后仍可区分不同量表
                scale_type=detect_scale_type(naming_input) if dt == DataType.SCALE else None,
            )
            # 拼接扩展名（保留原后缀；无后缀时不追加）
            final_ext = norm_ext or ext
            filename = f"{norm_name}.{final_ext}" if final_ext else norm_name
            # 安全化文件名：保留中文等 Unicode 字母（_safe_segment），
            # 不使用 werkzeug.secure_filename（会把中文全部删除）
            filename = safe_filename_segment(filename) or "unnamed"
            # 压缩连续下划线为单个（scene/batch 为空时会产生 __）
            import re as _re
            filename = _re.sub(r'_+', '_', filename).strip('_') or "unnamed"

            storage_root = current_app.config["DATA_LAKE_DIR"]
            # 目录结构：根目录/分层/受试者伪ID/模态类型/文件名
            save_dir = os.path.join(storage_root, layer, subject.pseudo_id, data_type)
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, filename)
            # 路径穿越防护：确保最终路径仍在 DATA_LAKE_DIR 内（兜底防御）
            storage_root_abs = os.path.realpath(storage_root)
            save_path_abs = os.path.realpath(save_path)
            if not (save_path_abs == storage_root_abs
                    or save_path_abs.startswith(storage_root_abs + os.sep)):
                raise ValidationError("非法的存储路径")

            # 量表文件明文缓存：供落盘后解析摘要（外部加密文件在解密分支捕获）
            _scale_plain_bytes = None

            # 落盘：启用加密时直接将上传流加密写入磁盘（不留明文临时文件）
            # 外部加密文件（.enc）：先用数据库外部密钥解密明文，再用内部密钥加密入库
            if _encryption_enabled():
                if is_external_enc:
                    # 读取外部加密密文 → 用数据库外部密钥解密 → 用内部密钥加密 → 落盘
                    from app.services.external_key_service import ExternalKeyService
                    enc_data = file_storage.read()
                    candidate_keys = ExternalKeyService().get_active_keys_for_decrypt()
                    if not candidate_keys:
                        raise ValidationError(
                            "外部加密文件无法解密：数据库无可用外部密钥，"
                            "请先到「系统设置 → 外部密钥管理」上传对应的外部密钥（key+iv）"
                        )
                    try:
                        plaintext, _matched_key_id = decrypt_external_bytes_auto(
                            enc_data, candidate_keys
                        )
                    except ValueError as e:
                        raise ValidationError(
                            f"外部加密文件解密失败：{e}，请检查外部密钥是否正确"
                        )
                    _scale_plain_bytes = plaintext
                    encrypted = encrypt_bytes(plaintext)
                    with open(save_path, "wb") as f:
                        f.write(encrypted)
                else:
                    encrypt_stream_to_file(file_storage, save_path)
            else:
                if is_external_enc:
                    # 未启用内部加密：外部加密文件需先解密再以明文存储
                    from app.services.external_key_service import ExternalKeyService
                    enc_data = file_storage.read()
                    candidate_keys = ExternalKeyService().get_active_keys_for_decrypt()
                    if not candidate_keys:
                        raise ValidationError(
                            "外部加密文件无法解密：数据库无可用外部密钥，"
                            "请先到「系统设置 → 外部密钥管理」上传对应的外部密钥（key+iv）"
                        )
                    try:
                        plaintext, _matched_key_id = decrypt_external_bytes_auto(
                            enc_data, candidate_keys
                        )
                    except ValueError as e:
                        raise ValidationError(
                            f"外部加密文件解密失败：{e}，请检查外部密钥是否正确"
                        )
                    _scale_plain_bytes = plaintext
                    with open(save_path, "wb") as f:
                        f.write(plaintext)
                else:
                    file_storage.save(save_path)

            # 相对存储路径入库（便于跨环境迁移）
            rel_path = f"{layer}/{subject.pseudo_id}/{data_type}/{filename}"
            # 元数据：记录原始文件名+大小，用于幂等去重；视频记录 video_type
            # 写库统一为纯文件名（去相对路径前缀、保留 .enc）：与浏览器
            # File.name 对齐，保证前端 ingest-digest 对账能直接匹配；
            # 同源比对时再由 normalize_original_filename 进一步剥 .enc。
            metadata = {
                "original_filename": stored_original_filename(original_name),
                "original_size": original_size,
            }
            if video_type:
                metadata["video_type"] = video_type
            # 量表文件：解析摘要与原始字段存入 metadata（与目录扫描导入行为一致，
            # 供可视化 /visualization/scale-asset 直接读取，避免每次解密重解析）
            if dt == DataType.SCALE:
                try:
                    import json as _json
                    from app.utils.scale_adapter import parse_scale_summary
                    if is_external_enc:
                        content = _scale_plain_bytes
                    else:
                        # 明文上传：落盘后流指针已到末尾，seek 回 0 重读明文（JSON 文件体积小）
                        file_storage.stream.seek(0)
                        content = file_storage.stream.read()
                        file_storage.stream.seek(0)
                    if content:
                        try:
                            # utf-8-sig 剥离 UTF-8 BOM，兼容外部系统导出的带 BOM 文件
                            raw = _json.loads(content.decode("utf-8-sig"))
                        except Exception:
                            raw = None
                        if isinstance(raw, dict):
                            scale_summary = parse_scale_summary(
                                raw, scale_type=detect_scale_type(original_name)
                            )
                            if scale_summary:
                                metadata["raw"] = raw
                                metadata["summary"] = scale_summary
                except Exception:
                    # 解析失败不影响上传主流程（可视化可回退解密后解析）
                    pass
            asset = DataAsset(
                subject_id=subject_id,
                data_type=dt,
                layer=DataLayer(layer),
                file_name=filename,
                file_path=rel_path,
                file_format=final_ext or None,
                file_size=os.path.getsize(save_path),
                sample_rate=sample_rate,
                metadata_json=metadata,
                # 采集时间：元数据显式采集字段 > 采集端原始名 > 规范化名 > 入库时刻
                timestamp_utc=resolve_collection_time(
                    metadata,
                    original_filename=original_name,
                    extra_names=[filename],
                    fallback_utc=datetime.utcnow(),
                ),
            )
            self.session.add(asset)
            self.session.flush()  # 让 asset.id 可用
            # 创建后留档（best-effort，便于历史追溯）
            try:
                save_snapshot("data_asset", asset.id, asset.to_dict(), "create",
                              self._operator_user(), "上传数据资产")
            except Exception:
                pass
            original_ref = f"（原始: {original_name}）" if original_name != filename else ""
            log_operation("upload", "asset", asset.id,
                          f"上传文件 {filename}{original_ref} 到 {subject.pseudo_id}/{data_type}",
                          operator=self._operator_user())
            # 业务数据 + 快照 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
            self._commit()
        # 视频重采：新文件已成功入库后再删除同 video_type 的旧视频资产。
        # 顺序必须"先写新后删旧"：若新文件落盘/入库失败，旧视频仍保留，避免重采造成数据丢失。
        # exclude_asset_id=asset.id 排除刚上传的新视频，只删同类型的其他旧视频。
        if dt == DataType.VIDEO and video_type:
            self._purge_subject_video_by_type(
                subject_id, video_type, storage_root=None, exclude_asset_id=asset.id,
            )
        # 同名演进替换：删除录制半成品等旧版本资产（先写新后删旧，同视频重采顺序）
        if stale_ids:
            self._purge_stale_uploads(stale_ids)
        return asset, False  # (asset, is_duplicate) 新建资产

    # ==================== 文件服务（下载/播放） ====================

    def serve_asset_file(self, asset_id: int) -> Tuple[str, bool, str, str]:
        """流式提供数据文件原始下载（仅 ADMIN）

        返回 (path, is_temp, mimetype, download_name)：
        - is_temp=True 时调用方需在响应后删除临时文件
        - 加密文件会先解密到临时文件（带正确扩展名，保证 send_file 推断 MIME/下载名正确），
          明文文件直接流式返回
        - mimetype / download_name 供路由层 send_file 使用
        - 所有下载操作记录审计日志
        """
        asset = self._get_or_404(DataAsset, asset_id, "数据资产不存在")
        storage_root = current_app.config["DATA_LAKE_DIR"]
        if not asset.file_path:
            raise NotFoundError("该资产无关联文件")
        abs_path = os.path.join(storage_root, asset.file_path)
        if not os.path.exists(abs_path):
            raise NotFoundError("文件不存在于存储目录")

        # 解密临时文件带真实扩展名：无扩展名临时文件会让 send_file 把 MIME 推断为
        # application/octet-stream、下载文件名变成 tmpXXXX，浏览器无法识别/播放。
        ext = os.path.splitext(asset.file_name)[1] or f".{asset.file_format or 'bin'}"
        serve_path, is_temp = _decrypt_for_serving(abs_path, suffix=ext)
        # 记录下载审计日志（事后可追溯）
        operator = self._operator_user()
        log_operation(
            "download", "data_asset", asset_id,
            f"下载文件 {asset.file_name}（用户: {operator.username if operator else 'unknown'}）",
            operator=operator,
        )
        self._commit()
        mimetype = mimetypes.guess_type(asset.file_name)[0] or "application/octet-stream"
        return serve_path, is_temp, mimetype, asset.file_name

    def play_asset(self, asset_id: int) -> Tuple[str, bool, str]:
        """播放数据文件：浏览器原生格式直接流，其余视频格式自动转码为 H.264 MP4 后流式播放

        返回 (path, is_temp, mimetype)：
        - 原生视频/音频格式：解密后直接流式返回
        - 非原生视频格式：首次播放转码为 MP4 缓存到 .transcodes/{asset_id}.mp4
        - is_temp=True 时调用方需在响应后清理临时文件
        - mimetype 由调用方用于 send_file

        异常：
        - NotFoundError: 资产/文件不存在
        - OperationNotAllowedError: 服务器无 ffmpeg 无法转码
        """
        from app.utils.transcode import (
            is_native_video, is_native_audio, transcode_to_mp4, has_ffmpeg,
        )

        asset = self._get_or_404(DataAsset, asset_id, "数据资产不存在")
        storage_root = current_app.config["DATA_LAKE_DIR"]
        if not asset.file_path:
            raise NotFoundError("该资产无关联文件")
        abs_path = os.path.join(storage_root, asset.file_path)
        if not os.path.exists(abs_path):
            raise NotFoundError("文件不存在于存储目录")

        fmt = (asset.file_format or "").lower()
        # 原生视频格式：加密文件解密到临时文件后流式返回
        if is_native_video(fmt):
            serve_path, is_temp = _decrypt_for_serving(abs_path, suffix=f".{fmt}")
            return serve_path, is_temp, f"video/{fmt}"

        # 原生音频格式：解密后流式返回（不走转码，避免音频流丢失）
        if is_native_audio(fmt):
            serve_path, is_temp = _decrypt_for_serving(abs_path, suffix=f".{fmt}")
            return serve_path, is_temp, f"audio/{fmt}"

        # 非原生视频格式，需要转码
        if not has_ffmpeg():
            raise OperationNotAllowedError("服务器未配置 ffmpeg，无法转码播放，请上传 MP4/WebM")

        cache_dir = os.path.join(storage_root, ".transcodes")
        cached = os.path.join(cache_dir, f"{asset_id}.mp4")
        # 已有转码缓存直接用（缓存不加密，派生临时文件）
        if os.path.exists(cached):
            return cached, False, "video/mp4"

        # 首次播放：加密文件需先解密到临时文件，再交给 ffmpeg 转码
        src_path, is_temp = _decrypt_for_serving(abs_path)
        try:
            ok, err = transcode_to_mp4(src_path, cached)
        finally:
            if is_temp:
                _remove_file_safely(src_path)
        if not ok:
            raise OperationNotAllowedError(f"转码失败：{err}")
        return cached, False, "video/mp4"

    # ==================== 私有辅助 ====================

    def _operator_user(self):
        """获取操作员 User 对象"""
        if not self.operator_id:
            return None
        from app.models.user import User
        return User.query.get(self.operator_id)

    def _purge_subject_video_by_type(self, subject_id: int, video_type: str,
                                     storage_root: Optional[str] = None,
                                     exclude_asset_id: Optional[int] = None):
        """删除受试者名下指定 video_type 的视频资产（重采完成后调用）

        仅删除同类型视频，保留其他类型（face/body/gait 互不影响）。
        exclude_asset_id 传新建资产的 id 时，跳过该资产（重采场景新文件已入库，
        避免把刚上传的新视频一并删除）。
        删除顺序：先留档 + 收集关联记录 → DB commit → 删磁盘文件（best-effort）。
        """
        from app.services.subject_service import _purge_asset_records, _collect_asset_file_paths

        if storage_root is None:
            storage_root = current_app.config["DATA_LAKE_DIR"]

        # 仅删除 metadata.video_type == 指定类型 的视频资产
        # 注意：JSON 字段查询兼容 MySQL/SQLite，这里用 Python 过滤更稳
        candidates = DataAsset.query.filter_by(
            subject_id=subject_id,
            data_type=DataType.VIDEO,
        ).all()
        to_delete = [
            a for a in candidates
            if (a.metadata_json or {}).get("video_type") == video_type
            and (exclude_asset_id is None or a.id != exclude_asset_id)
        ]
        if not to_delete:
            return

        for asset in to_delete:
            snapshot_delete("data_asset", asset, f"重采替换 {asset.file_name}",
                            operator=self._operator_user())
            _purge_asset_records(asset)
            pending_files = _collect_asset_file_paths(asset, storage_root)
            self.session.delete(asset)
            log_operation("delete", "asset", asset.id,
                          f"重采删除视频 {asset.file_name}（{video_type}）",
                          operator=self._operator_user())
            # 提交 DB 后再删磁盘（事务保护，DB 失败则磁盘文件保留可重试）
            self._commit()
            _remove_file_safely(pending_files[0])
            _remove_file_safely(pending_files[1])

    def _purge_stale_uploads(self, asset_ids: list):
        """删除同名演进上传留下的旧版本资产（录制半成品→完整文件替换场景）

        新文件已入库后调用（先写新后删旧）：每条独立清理——留档 + 级联关联记录
        → DB commit → 删磁盘文件（best-effort），与视频重采清理同一模式。
        """
        storage_root = current_app.config["DATA_LAKE_DIR"]
        for asset_id in asset_ids:
            asset = DataAsset.query.get(asset_id)
            if asset is None:
                continue
            snapshot_delete("data_asset", asset,
                            f"同名演进替换（删除旧版本 {asset.file_name}）",
                            operator=self._operator_user())
            _purge_asset_records(asset)
            pending_files = _collect_asset_file_paths(asset, storage_root)
            shared_rel_path = asset.file_path
            self.session.delete(asset)
            log_operation("delete", "asset", asset.id,
                          f"同名演进替换删除旧版本 {asset.file_name}",
                          operator=self._operator_user())
            self._commit()
            # 共享文件保护：同一 file_path 若还有其他存活记录（同一秒内双入库
            # 留下的两行指向同一文件、或手工登记复用同一路径），删磁盘会让
            # 幸存记录变成指向不存在文件的孤儿。仅在无人引用时才删磁盘。
            still_referenced = (
                DataAsset.query.filter_by(file_path=shared_rel_path).count()
                if shared_rel_path else 0
            )
            if still_referenced == 0:
                _remove_file_safely(pending_files[0])
            else:
                current_app.logger.warning(
                    "跳过删除磁盘文件（仍被 %s 条记录引用）：%s",
                    still_referenced, shared_rel_path,
                )
            _remove_file_safely(pending_files[1])

    def purge_duplicate_source_assets(self) -> int:
        """系统自愈：清理同源重复资产，每组保留最新一条

        同源分组键 = 受试者 + 模态 + 原始文件名 + video_type（与上传幂等键同源，
        忽略大小差异）。组内多条的来源：
        - 历史并发上传竞态（同 key 双入库，锁修复前产生）
        - 录制半成品先入库：完整版再传时同 size 命中幂等直接返回，
          入库替换分支不触发，半成品残留

        保留 id 最大的一条（最新上传，即完整版/最新演进版本），其余删除
        （留档 + 级联清理 + 删磁盘）。无原始文件名元数据的老数据保守跳过。
        返回清理的记录数；重复调用幂等（组内只剩一条时无操作）。
        """
        rows = (
            DataAsset.query
            .with_entities(
                DataAsset.id, DataAsset.subject_id, DataAsset.data_type,
                DataAsset.metadata_json,
            )
            .filter(DataAsset.metadata_json.isnot(None))
            .all()
        )
        groups = {}
        for r in rows:
            meta = r.metadata_json or {}
            orig = meta.get("original_filename")
            if not orig:
                continue
            # 分组键用归一化原始名（去目录前缀 / 去 .enc / 小写），与上传幂等
            # 键口径一致：同一份源文件不同形态入库的残留才能被归到一组收敛
            key = source_group_key(r.subject_id, r.data_type, orig, meta.get("video_type"))
            groups.setdefault(key, []).append(r.id)
        stale_ids = []
        for ids in groups.values():
            if len(ids) > 1:
                stale_ids.extend(sorted(ids)[:-1])  # 保留 id 最大（最新上传）
        if stale_ids:
            self._purge_stale_uploads(stale_ids)
        return len(stale_ids)


# ==================== 模块级辅助函数（文件服务相关） ====================

def _encryption_enabled():
    """是否启用数据湖文件加密"""
    return current_app.config.get("ENCRYPT_DATA_LAKE", True)


def _decrypt_for_serving(abs_path, suffix=""):
    """为文件服务准备可读路径：加密文件解密到临时文件，明文文件直接返回

    返回 (path, is_temp)：is_temp=True 时调用方需在响应后删除临时文件
    """
    if _encryption_enabled() and is_encrypted_file(abs_path):
        tmp = decrypt_file_to_temp(abs_path, suffix=suffix)
        return tmp, True
    return abs_path, False

