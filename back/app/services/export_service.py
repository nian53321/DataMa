# -*- coding: utf-8 -*-
"""数据导出服务：批量打包数据资产为 zip

支持：
- 按 asset_ids 列表或 subject_ids/data_types/layers 筛选导出
- 加密导出（保持 DMEC 格式，文件追加 .dmec 后缀）
- 明文导出（解密后打包原文件）
- 单次最多 200 文件 / 2GB
- 异步任务 + 实时压缩进度回调（ExportTaskManager）

复用：
- _decrypt_for_serving：复用 asset_service 模块级解密函数
- _remove_file_safely：复用 subject_service 安全删除文件
"""
import os
import json
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from typing import Tuple, List

from flask import current_app

from app.extensions import db
from app.models import DataAsset, DataType, DataLayer, Subject
from app.services.base import BaseService, ValidationError, NotFoundError
from app.services.asset_service import _decrypt_for_serving
from app.services.subject_service import _remove_file_safely
from app.utils.audit import log_operation


class ExportService(BaseService):
    """数据资产批量导出服务"""

    EXPORT_MAX_FILE_COUNT = 200
    EXPORT_MAX_TOTAL_SIZE = 2 * 1024 ** 3  # 2GB

    def prepare_export(self, payload: dict, progress_callback=None,
                       assets: List[DataAsset] = None) -> Tuple[str, str, list]:
        """准备导出 zip

        :param payload: {
            asset_ids: [int],            # 模式一：指定资产 ID 列表
            subject_ids: [int],          # 模式二：按多个受试者筛选（空/缺省 = 全部受试者）
            data_types: [str],           # 模式二：按多个数据类型筛选（空/缺省 = 全部类型）
            layers: [str],               # 模式二：按多个数据层筛选（空/缺省 = 全部层）
            # 兼容旧版单值参数（自动并入复数集合）：
            subject_id: int,
            data_type: str,
            layer: str,
            encrypted: bool              # 是否加密导出（默认 True）
        }
        :param progress_callback: 可选回调(processed, total, size_done, size_total)
        :param assets: 已解析并通过限额校验的资产列表（异步任务线程传入，
                       避免与 resolve_assets 重复查询数据库）；缺省时内部解析
        :return: (zip_tmp_path, download_filename, skipped_errors)
            skipped_errors: 被跳过文件的明细列表（磁盘丢失/打包失败），
            供任务状态展示与前端提示
        :raises ValidationError: 未选择资产 / 超过数量或大小限制
        :raises NotFoundError: 资产或文件不存在
        """
        encrypted = bool(payload.get("encrypted", True))
        if assets is None:
            assets = self._resolve_assets(payload)
            self._validate_limits(assets)
        zip_path, skipped = self._build_zip(assets, encrypted, progress_callback=progress_callback)
        self._log_audit(assets, encrypted, skipped_count=len(skipped))
        return zip_path, self._build_filename(encrypted), skipped

    def resolve_assets(self, payload: dict):
        """仅解析待导出资产（用于异步任务初始化，提前获取总数/总大小）"""
        assets = self._resolve_assets(payload)
        self._validate_limits(assets)
        return assets

    def preview_export(self, payload: dict, max_items: int = 200) -> dict:
        """预览导出范围：命中资产统计 + 明细（不打包、不记审计日志）

        供导出界面在选择条件变化时实时展示"将导出哪些数据资产"，
        并提前暴露超限风险（文件数/总大小），避免用户提交后才被拒绝。
        """
        assets = self._resolve_assets(payload, require_nonempty=False)
        total_count = len(assets)
        total_size = sum(int(a.file_size or 0) for a in assets)

        subject_ids = {a.subject_id for a in assets}
        subjects = {}
        if subject_ids:
            subjects = {s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}

        by_type, by_layer = {}, {}
        items = []
        for a in assets:
            dt = a.data_type.value if a.data_type else "unknown"
            ly = a.layer.value if a.layer else "unknown"
            by_type[dt] = by_type.get(dt, 0) + 1
            by_layer[ly] = by_layer.get(ly, 0) + 1
            if len(items) < max_items:
                subject = subjects.get(a.subject_id)
                items.append({
                    "asset_id": a.id,
                    "file_name": a.file_name,
                    "data_type": dt,
                    "layer": ly,
                    "file_size": int(a.file_size or 0),
                    "pseudo_id": subject.pseudo_id if subject else f"subject_{a.subject_id}",
                })

        return {
            "total_count": total_count,
            "total_size": total_size,
            "exceeds_count_limit": total_count > self.EXPORT_MAX_FILE_COUNT,
            "exceeds_size_limit": total_size > self.EXPORT_MAX_TOTAL_SIZE,
            "max_file_count": self.EXPORT_MAX_FILE_COUNT,
            "by_type": by_type,
            "by_layer": by_layer,
            "items": items,
        }

    # ==================== 私有辅助 ====================

    def _resolve_assets(self, payload: dict, require_nonempty: bool = True) -> List[DataAsset]:
        """根据 payload 解析待导出资产列表

        优先使用 asset_ids；否则按 subject_ids/data_types/layers 批量筛选
        （为兼容旧调用方，单值 subject_id/data_type/layer 会自动并入复数集合）
        :param require_nonempty: True 时无匹配资产抛 ValidationError（导出流程）；
                                 False 时返回空列表（预览流程，前端显示"无匹配资产"）
        """
        asset_ids = payload.get("asset_ids") or []
        if asset_ids:
            assets = DataAsset.query.filter(DataAsset.id.in_(asset_ids)).all()
            if not assets:
                raise ValidationError("未找到指定的数据资产")
            return assets

        # 合并单值与复数参数（去重，保持顺序）
        def _merge(single_key, plural_key, caster=str):
            singles = []
            v = payload.get(single_key)
            if v:
                singles = [v]
            plurals = payload.get(plural_key) or []
            seen, result = set(), []
            for x in singles + list(plurals):
                if x is None or x == "":
                    continue
                k = str(x)
                if k in seen:
                    continue
                seen.add(k)
                result.append(caster(x))
            return result

        subject_ids = _merge("subject_id", "subject_ids", caster=int)
        data_types = _merge("data_type", "data_types", caster=str)
        layers = _merge("layer", "layers", caster=str)

        query = DataAsset.query
        if subject_ids:
            query = query.filter(DataAsset.subject_id.in_(subject_ids))

        if data_types:
            valid_types = []
            for dt in data_types:
                try:
                    valid_types.append(DataType(dt))
                except ValueError:
                    raise ValidationError(f"数据类型非法：{dt}")
            query = query.filter(DataAsset.data_type.in_(valid_types))

        if layers:
            valid_layers = []
            for ly in layers:
                try:
                    valid_layers.append(DataLayer(ly))
                except ValueError:
                    raise ValidationError(f"数据分层非法：{ly}")
            query = query.filter(DataAsset.layer.in_(valid_layers))

        assets = query.order_by(DataAsset.created_at.desc()).all()
        if not assets and require_nonempty:
            raise ValidationError("未选择任何数据资产")
        return assets

    def _validate_limits(self, assets: List[DataAsset]):
        """校验导出规模上限"""
        if len(assets) > self.EXPORT_MAX_FILE_COUNT:
            raise ValidationError(
                f"导出文件数超过上限：{len(assets)} > {self.EXPORT_MAX_FILE_COUNT}，请分批导出"
            )
        total_size = sum(int(a.file_size or 0) for a in assets)
        if total_size > self.EXPORT_MAX_TOTAL_SIZE:
            raise ValidationError(
                f"导出总大小超过上限：{total_size / 1024 / 1024:.1f} MB > 2048 MB，请分批导出"
            )

    def _build_zip(self, assets: List[DataAsset], encrypted: bool,
                   progress_callback=None) -> Tuple[str, list]:
        """构建 zip 临时文件

        - encrypted=True：原 DMEC 加密文件直接打包，arcname 追加 .dmec 后缀
        - encrypted=False：解密后打包原文件，arcname 不追加 .dmec
        - 部分文件丢失：跳过，记入 errors（不写入 zip，返回给调用方展示/审计）
        - progress_callback(processed, total, size_done, size_total)：可选进度回调
        :return: (zip_tmp_path, skipped_errors)
        """
        storage_root = current_app.config["DATA_LAKE_DIR"]
        # 预加载受试者 pseudo_id（避免 N+1 查询）
        subject_ids = {a.subject_id for a in assets}
        subjects = {s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}

        fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="export_")
        os.close(fd)

        total_files = len(assets)
        total_size = sum(int(a.file_size or 0) for a in assets)
        processed = 0
        size_done = 0
        errors = []

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for asset in assets:
                    try:
                        if not asset.file_path:
                            errors.append({
                                "asset_id": asset.id,
                                "file_name": asset.file_name,
                                "reason": "资产无关联文件",
                            })
                            continue
                        abs_path = os.path.join(storage_root, asset.file_path)
                        if not os.path.exists(abs_path):
                            errors.append({
                                "asset_id": asset.id,
                                "file_name": asset.file_name,
                                "reason": "磁盘文件丢失",
                            })
                            continue

                        subject = subjects.get(asset.subject_id)
                        pseudo_id = subject.pseudo_id if subject else f"subject_{asset.subject_id}"
                        layer = asset.layer.value if asset.layer else "unknown"
                        data_type = asset.data_type.value if asset.data_type else "unknown"
                        file_name = asset.file_name or f"asset_{asset.id}"

                        # 加密导出：原文件直接打包（保持 DMEC 格式），arcname 追加 .dmec
                        # 明文导出：解密到临时文件后打包，arcname 保持原文件名
                        if encrypted:
                            arc_file = file_name if file_name.endswith(".dmec") else f"{file_name}.dmec"
                            arcname = f"{pseudo_id}/{layer}/{data_type}/{arc_file}"
                            zf.write(abs_path, arcname)
                        else:
                            fmt = (asset.file_format or "").lower()
                            suffix = f".{fmt}" if fmt else ""
                            tmp_path, is_temp = _decrypt_for_serving(abs_path, suffix=suffix)
                            try:
                                arcname = f"{pseudo_id}/{layer}/{data_type}/{file_name}"
                                zf.write(tmp_path, arcname)
                            finally:
                                if is_temp:
                                    _remove_file_safely(tmp_path)

                        size_done += int(asset.file_size or 0)
                    except Exception as e:
                        errors.append({
                            "asset_id": asset.id,
                            "file_name": getattr(asset, "file_name", None),
                            "reason": f"打包失败：{e}",
                        })

                    processed += 1
                    if progress_callback:
                        try:
                            progress_callback(processed, total_files, size_done, total_size)
                        except Exception:
                            pass
        except Exception:
            # zip 构建失败，清理临时文件后重新抛出
            _remove_file_safely(zip_path)
            raise

        return zip_path, errors

    def _log_audit(self, assets: List[DataAsset], encrypted: bool, skipped_count: int = 0):
        """记录导出审计日志（在 send_file 之前 commit）"""
        operator = self._operator_user()
        asset_ids = [a.id for a in assets]
        # 拼接 ID 串（避免日志过长，最多显示前 20 个）
        ids_display = ",".join(str(i) for i in asset_ids[:20])
        if len(asset_ids) > 20:
            ids_display += f",...（共 {len(asset_ids)} 个）"
        total_size = sum(int(a.file_size or 0) for a in assets)
        subject_ids = sorted({a.subject_id for a in assets})
        # 预加载 pseudo_id 用于日志
        subjects = {s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}
        pseudo_ids = [subjects[sid].pseudo_id for sid in subject_ids if sid in subjects]
        skipped_note = f"，跳过 {skipped_count} 个缺失文件" if skipped_count else ""
        log_operation(
            "export", "data_asset", ids_display,
            detail=(
                f"导出 {len(assets)} 个数据资产"
                f"（{'加密' if encrypted else '明文'}模式，"
                f"总大小 {total_size / 1024 / 1024:.1f} MB，"
                f"受试者: {', '.join(pseudo_ids[:10])}{'...' if len(pseudo_ids) > 10 else ''}"
                f"{skipped_note}）"
            ),
            operator=operator,
        )
        self._commit()

    def _build_filename(self, encrypted: bool) -> str:
        """构建下载文件名"""
        mode = "encrypted" if encrypted else "plain"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"data_export_{mode}_{timestamp}.zip"


# ==================== 异步导出任务管理 ====================

class ExportTaskManager:
    """内存级异步导出任务管理器

    流程：
    1. create_task(payload, operator_id, operator_role) → task_id
       启动后台线程打包，立即返回 task_id
    2. get_task(task_id) → 任务状态/进度
       前端轮询此接口获取实时压缩进度
    3. download_task(task_id) → (zip_path, filename)
       任务完成后前端调用，获取 zip 下载
    4. cleanup_task(task_id)
       下载完成后清理任务和临时文件
    """

    # 任务存活时间（秒），超时自动清理
    TASK_TTL = 3600
    # 已完成任务最大保留数
    MAX_COMPLETED = 20

    def __init__(self):
        self._tasks = {}
        self._lock = threading.Lock()

    def create_task(self, payload: dict, operator_id: int, operator_role: str) -> str:
        """创建异步导出任务，返回 task_id"""
        from flask import current_app as _current_app
        # 捕获真实 app 对象，供后台线程推送应用上下文
        app = _current_app._get_current_object()

        task_id = uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id,
            "status": "pending",
            "percent": 0,
            "status_text": "准备解析导出范围...",
            "total_files": 0,
            "processed_files": 0,
            "total_size": 0,
            "processed_size": 0,
            "zip_path": "",
            "filename": "",
            "skipped_files": 0,   # 因磁盘丢失/打包失败被跳过的文件数
            "error": "",
            "created_at": time.time(),
        }
        with self._lock:
            self._tasks[task_id] = task
            self._cleanup_expired_locked()

        t = threading.Thread(
            target=self._run_task,
            args=(task_id, payload, operator_id, operator_role, app),
            daemon=True,
        )
        t.start()
        return task_id

    def _run_task(self, task_id: str, payload: dict, operator_id: int,
                  operator_role: str, app):
        """后台线程执行打包（需在应用上下文中运行）"""
        task = self._tasks.get(task_id)
        if not task:
            return
        # 后台线程需手动推送应用上下文，否则 db.session / current_app 不可用
        with app.app_context():
            try:
                task["status"] = "running"
                task["status_text"] = "正在解析导出范围..."

                svc = ExportService(operator_id=operator_id, operator_role=operator_role)
                encrypted = bool(payload.get("encrypted", True))

                # 先解析资产，获取总数/总大小（结果传给 prepare_export 复用，避免重复查询）
                assets = svc.resolve_assets(payload)
                task["total_files"] = len(assets)
                task["total_size"] = sum(int(a.file_size or 0) for a in assets)
                task["status_text"] = f"开始压缩 {task['total_files']} 个文件..."

                def on_progress(processed, total, size_done, size_total):
                    task["processed_files"] = processed
                    task["processed_size"] = size_done
                    if total > 0:
                        task["percent"] = min(99, int(processed / total * 95))
                    task["status_text"] = (
                        f"正在压缩 {processed}/{total} 个文件"
                        f"（{format_size(size_done)} / {format_size(size_total)}）"
                    )

                zip_path, filename, skipped = svc.prepare_export(
                    payload, progress_callback=on_progress, assets=assets)
                task["zip_path"] = zip_path
                task["filename"] = filename
                task["skipped_files"] = len(skipped)
                task["percent"] = 100
                task["status"] = "success"
                if skipped:
                    task["status_text"] = f"压缩完成（{len(skipped)} 个文件被跳过），准备下载"
                else:
                    task["status_text"] = "压缩完成，准备下载"
            except ValidationError as e:
                # 业务校验错误（超限/未选择等）：消息可直接提示用户
                task["status"] = "failed"
                task["error"] = str(e)
                task["status_text"] = f"打包失败：{e}"
            except Exception as e:
                # 底层 IO/解析等异常：记录完整信息到服务端日志，前端只返回分类化提示
                app.logger.error("导出任务 %s 失败: %s", task_id, e, exc_info=True)
                task["status"] = "failed"
                task["error"] = "导出失败：服务器内部错误，请查看服务端日志"
                task["status_text"] = "导出失败，请稍后重试或联系管理员"
            finally:
                # 释放线程局部的数据库会话
                try:
                    db.session.remove()
                except Exception:
                    pass

    def get_task(self, task_id: str):
        """查询任务进度"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return dict(task)

    def download_task(self, task_id: str):
        """获取任务结果（zip 路径 + 文件名）"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task["status"] != "success":
                return None
            return task["zip_path"], task["filename"]

    def cleanup_task(self, task_id: str):
        """下载完成后清理任务和临时文件"""
        with self._lock:
            task = self._tasks.pop(task_id, None)
        if task and task.get("zip_path") and os.path.exists(task["zip_path"]):
            _remove_file_safely(task["zip_path"])

    def _cleanup_expired_locked(self):
        """清理过期/超量任务（调用方需持有锁）"""
        now = time.time()
        # 1. 清理超时任务
        expired = [
            tid for tid, t in self._tasks.items()
            if now - t["created_at"] > self.TASK_TTL
        ]
        for tid in expired:
            t = self._tasks.pop(tid, None)
            if t and t.get("zip_path") and os.path.exists(t["zip_path"]):
                _remove_file_safely(t["zip_path"])
        # 2. 限制已完成任务数量
        completed = [
            (tid, t) for tid, t in self._tasks.items()
            if t["status"] in ("success", "failed")
        ]
        if len(completed) > self.MAX_COMPLETED:
            completed.sort(key=lambda x: x[1]["created_at"])
            for tid, t in completed[:len(completed) - self.MAX_COMPLETED]:
                self._tasks.pop(tid, None)
                if t.get("zip_path") and os.path.exists(t["zip_path"]):
                    _remove_file_safely(t["zip_path"])


# ==================== 启动清扫 ====================

def cleanup_orphan_export_zips(logger=None):
    """应用启动时清扫残留的导出临时 zip

    进程被 kill / 崩溃时 TTL 清理不会执行，export_*.zip 会残留在
    系统临时目录（tempfile.gettempdir()），启动时统一删除。
    仅适用于单进程部署：多 worker 下其他 worker 可能正在打包导出文件。
    """
    import glob
    removed = 0
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "export_*.zip")):
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    if removed and logger:
        logger.info("启动清扫：已删除 %d 个残留导出临时文件", removed)
    return removed


def format_size(bytes_val):
    """格式化文件大小"""
    if not bytes_val:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


# 模块级单例
export_task_manager = ExportTaskManager()