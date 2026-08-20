# -*- coding: utf-8 -*-
"""受试者管理服务

封装受试者 CRUD + 脱敏 + 级联删除逻辑。
对应原 api/data.py 的 list_subjects/create_subject/update_subject/delete_subject。

业务规则：
- pseudo_id 格式：3-64 位字母/数字/下划线/短横线（防路径穿越）
- pseudo_id 唯一性
- 风险分级筛选 'none' 表示 NULL 或空字符串
- 删除受试者级联删除：所有数据资产 + 标注任务 + 标注 + 版本记录 + 磁盘文件 + 转码缓存
- 列表按角色脱敏（admin 不脱敏）
"""
import os
import logging
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

from app.extensions import db
from app.models import Subject, DataAsset, DataType
from app.models.annotation import AnnotationTask, Annotation, AnnotationVersion
from app.models.data import DataVersion
from app.services.base import BaseService, ValidationError, ConflictError, NotFoundError
from app.utils.audit import log_operation, snapshot_update, snapshot_delete
from app.models.data_snapshot import save_snapshot
from app.utils.desensitize import desensitize_list
from app.utils.like_query import build_like_contains
from app.utils.response import paginate
from app.utils.scanner import _PSEUDO_ID_RE


class SubjectService(BaseService):
    """受试者管理服务"""

    def list_subjects(self, page: int = 1, page_size: int = 20,
                      keyword: str = "", gender: str = "",
                      risk_level: str = "", batch: str = "",
                      has_video: str = "", ids_only: bool = False) -> dict:
        """受试者列表（支持关键词搜索与多条件筛选）

        风险分级传 'none' 表示筛选未评估（NULL 或空字符串）
        has_video 传 'true'/'false' 筛选已采集/未采集视频
        ids_only=True 时仅返回当前筛选条件下的全部 ID 列表（不分页，轻量），
        用于前端跨页全选。返回格式：{"items": [{"id": 1}, ...], "total": N}
        返回分页 dict，items 已按角色脱敏，附带 has_video 标记。
        """
        query = Subject.query
        if keyword:
            query = query.filter(Subject.pseudo_id.like(build_like_contains(keyword), escape="|"))
        if gender:
            query = query.filter(Subject.gender == gender)
        if risk_level:
            if risk_level == "none":
                # 未评估：NULL 或空字符串
                query = query.filter(
                    db.or_(Subject.cognitive_risk_level.is_(None),
                           Subject.cognitive_risk_level == "")
                )
            else:
                query = query.filter(Subject.cognitive_risk_level == risk_level)
        if batch:
            query = query.filter(Subject.collection_batch == batch)
        # 视频采集筛选
        if has_video in ("true", "false"):
            video_subject_ids = (
                db.session.query(DataAsset.subject_id)
                .filter(DataAsset.data_type == DataType.VIDEO)
                .distinct()
                .subquery()
            )
            if has_video == "true":
                query = query.filter(Subject.id.in_(
                    db.select(video_subject_ids.c.subject_id)
                ))
            else:
                query = query.filter(~Subject.id.in_(
                    db.select(video_subject_ids.c.subject_id)
                ))
        # ids_only 模式：仅返回 ID 列表（用于跨页全选），不分页、不脱敏
        if ids_only:
            ids = [r[0] for r in query.with_entities(Subject.id).all()]
            return {"items": [{"id": i} for i in ids], "total": len(ids)}
        query = query.order_by(Subject.created_at.desc())
        result = paginate(query, page, page_size)
        # 批量查询每个受试者的视频资产，聚合出 video_types（已采集类型列表）+ has_video（向后兼容）
        items = result.get("items", [])
        if items:
            subject_ids = [s["id"] for s in items]
            rows = (
                db.session.query(
                    DataAsset.subject_id,
                    DataAsset.metadata_json,
                )
                .filter(
                    DataAsset.subject_id.in_(subject_ids),
                    DataAsset.data_type == DataType.VIDEO,
                )
                .all()
            )
            # subject_id -> set(video_type)
            video_map = {}
            for sid, meta in rows:
                vt = (meta or {}).get("video_type") if isinstance(meta, dict) else None
                video_map.setdefault(sid, set()).add(vt) if vt else video_map.setdefault(sid, set())
            for s in items:
                vtypes = sorted(video_map.get(s["id"], set()) or [])
                s["video_types"] = vtypes
                s["has_video"] = bool(vtypes)
        # 按角色脱敏（admin 不脱敏）
        desensitize_list(items, self.operator_role)
        return result

    def create_subject(self, data: dict):
        """创建受试者

        业务规则：
        - pseudo_id 必填、格式校验、唯一性校验
        """
        pseudo_id = (data.get("pseudo_id") or "").strip()
        if not pseudo_id:
            raise ValidationError("受试者伪ID不能为空")
        # 格式校验：复用 scanner.py 的正则（3-64位字母/数字/下划线/短横线），防止路径穿越
        if not _PSEUDO_ID_RE.match(pseudo_id):
            raise ValidationError("受试者伪ID格式非法（仅允许 3-64 位字母/数字/下划线/短横线）")
        if Subject.query.filter_by(pseudo_id=pseudo_id).first():
            raise ConflictError("受试者伪ID已存在")

        subject = Subject(
            pseudo_id=pseudo_id,
            real_name=(data.get("real_name") or "").strip() or None,
            age=data.get("age"),
            gender=data.get("gender"),
            education_level=data.get("education_level"),
            phone=data.get("phone"),
            id_card=data.get("id_card"),
            cognitive_risk_level=data.get("cognitive_risk_level"),
            emotion_status=data.get("emotion_status"),
            collection_batch=data.get("collection_batch"),
            collection_scene=data.get("collection_scene"),
            mmse_score=data.get("mmse_score"),
            moca_score=data.get("moca_score"),
            ad8_score=data.get("ad8_score"),
            remark=data.get("remark"),
        )
        self.session.add(subject)
        # 创建后留档（best-effort，便于历史追溯）— flush 拿 id，不 commit
        try:
            self.session.flush()  # 让 subject.id 可用
            save_snapshot("subject", subject.id, subject.to_dict(), "create",
                          self._operator_user(), "创建受试者")
        except Exception:
            pass
        log_operation("create", "subject", subject.id, f"创建受试者 {pseudo_id}",
                      operator=self._operator_user())
        # 业务数据 + 快照 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
        self._commit()
        return subject

    def update_subject(self, subject_id: int, data: dict):
        """更新受试者信息

        业务规则：
        - 修改前留档快照
        - pseudo_id 变更时重新做格式与唯一性校验
        - collection_time 字符串转 datetime
        """
        subject = self._get_or_404(Subject, subject_id, "受试者不存在")
        old_dict = subject.to_dict()  # 修改前快照

        for f in ["real_name", "age", "gender", "education_level", "phone", "id_card",
                  "cognitive_risk_level", "emotion_status", "collection_batch",
                  "collection_scene", "mmse_score", "moca_score", "ad8_score",
                  "remark"]:
            if f in data:
                setattr(subject, f, data[f])
        # 采集时间单独处理（字符串转 datetime）
        if "collection_time" in data and data["collection_time"]:
            try:
                from datetime import datetime
                subject.collection_time = datetime.strptime(
                    str(data["collection_time"])[:19], "%Y-%m-%d %H:%M:%S"
                )
            except (ValueError, TypeError):
                pass
        # 伪ID 唯一性与格式校验
        if data.get("pseudo_id") and data["pseudo_id"] != subject.pseudo_id:
            if not _PSEUDO_ID_RE.match(data["pseudo_id"].strip()):
                raise ValidationError("受试者伪ID格式非法（仅允许 3-64 位字母/数字/下划线/短横线）")
            if Subject.query.filter_by(pseudo_id=data["pseudo_id"]).first():
                raise ConflictError("受试者伪ID已存在")
            subject.pseudo_id = data["pseudo_id"]

        snapshot_update("subject", subject, old_dict, subject.to_dict(),
                        operator=self._operator_user())
        log_operation("update", "subject", subject.id, f"更新受试者 {subject.pseudo_id}",
                      operator=self._operator_user())
        # 业务数据 + 快照 + 日志一次性原子提交（避免双 commit 中途失败导致审计日志丢失）
        self._commit()
        return subject

    def delete_subject(self, subject_id: int, storage_root: str):
        """删除受试者（级联删除数据资产、标注任务、版本记录与磁盘文件）

        业务规则：
        - 删除前留档（受试者及其所有资产快照）
        - 级联删除每个资产：标注记录 + 标注版本 + 标注任务 + 数据版本 + 磁盘文件 + 转码缓存
        - 事务安全：先 DB commit（保护数据一致性），commit 成功后再删磁盘文件
          （若 commit 失败则磁盘文件保留，可重试；避免磁盘已删但 DB 未删的数据丢失）
        """
        subject = self._get_or_404(Subject, subject_id, "受试者不存在")
        pseudo_id = subject.pseudo_id
        # 删除前留档（受试者及其资产快照）
        snapshot_delete("subject", subject, f"删除受试者 {pseudo_id}",
                        operator=self._operator_user())
        # 级联删除每个资产关联记录（DB 内），磁盘文件路径预先收集，commit 后再删
        assets = DataAsset.query.filter_by(subject_id=subject_id).all()
        pending_files = []  # [(asset_id, file_path, transcode_cache_path)]
        for a in assets:
            snapshot_delete("data_asset", a, f"级联删除（受试者 {pseudo_id}）",
                            operator=self._operator_user())
            _purge_asset_records(a)
            pending_files.append(_collect_asset_file_paths(a, storage_root))
            self.session.delete(a)
        self.session.delete(subject)
        log_operation("delete", "subject", subject_id,
                      f"删除受试者 {pseudo_id}（含 {len(assets)} 个资产）",
                      operator=self._operator_user())
        # 先提交 DB（事务保护），失败时 rollback，磁盘文件未被影响
        self._commit()
        # DB commit 成功后再删磁盘文件（best-effort，失败仅记录日志，不影响已成功的 DB 操作）
        for file_path, transcode_path in pending_files:
            _remove_file_safely(file_path)
            _remove_file_safely(transcode_path)
        # 清理该受试者在数据湖各分层下的目录（含上述 remove 后的空目录，
        # 以及无资产记录/游离文件），避免 data_lake 残留
        self._remove_subject_datalake_dirs(storage_root, pseudo_id)

    def _remove_subject_datalake_dirs(self, storage_root: str, pseudo_id: str):
        """删除受试者在数据湖各分层（raw/cleaned/feature/annotation）下的整目录

        DB 提交成功后才执行，best-effort：目录不存在/被占用时仅记录，不抛错。
        pseudo_id 在创建时已过格式校验（3-64 位字母/数字/下划线/短横线，无路径穿越），
        因此 join 出的 target 是安全可控的受试者专属目录。
        """
        for layer in ("raw", "cleaned", "feature", "annotation"):
            target = os.path.join(storage_root, layer, pseudo_id)
            if not os.path.isdir(target):
                continue
            try:
                shutil.rmtree(target)
            except OSError:
                logger.exception("删除受试者数据湖目录失败: %s", target)

    def _operator_user(self):
        """获取操作员 User 对象（用于审计日志与快照）

        service 不直接依赖 jwt，但 audit 工具需要 User 对象。
        若无 operator_id 则返回 None，audit 模块会以 anonymous 记录。
        """
        if not self.operator_id:
            return None
        from app.models.user import User
        return User.query.get(self.operator_id)


# ==================== 模块级辅助函数（资产级联删除，被 SubjectService 与 AssetService 共用） ====================

def _purge_asset_records(asset):
    """级联删除数据资产关联的子表记录（外键约束 nullable=False，必须显式删除）
    涉及：DataVersion、AnnotationTask→Annotation、AnnotationTask→AnnotationVersion、AnnotationTask
    """
    # 1. 删除该资产关联的所有标注任务及其子记录
    tasks = AnnotationTask.query.filter_by(data_asset_id=asset.id).all()
    for t in tasks:
        Annotation.query.filter_by(task_id=t.id).delete(synchronize_session=False)
        AnnotationVersion.query.filter_by(task_id=t.id).delete(synchronize_session=False)
        db.session.delete(t)
    # 2. 删除该资产的所有版本记录
    DataVersion.query.filter_by(data_asset_id=asset.id).delete(synchronize_session=False)


def _collect_asset_file_paths(asset, storage_root):
    """收集资产对应的磁盘文件与转码缓存路径（不删除，供 commit 后再删）

    返回 (file_path, transcode_cache_path)，路径可能为空字符串。
    先收集再删除的原因：commit 后 asset 对象会 expire，无法再读 file_path。
    """
    file_path = os.path.join(storage_root, asset.file_path) if asset.file_path else ""
    cache_dir = os.path.join(storage_root, ".transcodes")
    transcode_path = os.path.join(cache_dir, f"{asset.id}.mp4")
    return file_path, transcode_path


def _purge_asset_files(asset, storage_root):
    """删除资产对应的磁盘文件与转码缓存（直接删除版本，用于无事务边界场景）"""
    file_path, transcode_path = _collect_asset_file_paths(asset, storage_root)
    _remove_file_safely(file_path)
    _remove_file_safely(transcode_path)


def _remove_file_safely(path):
    """安全删除文件（忽略不存在/占用错误）"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
