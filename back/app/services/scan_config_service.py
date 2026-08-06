# -*- coding: utf-8 -*-
"""扫描配置服务

封装原 api/system.py 的扫描配置 CRUD 与立即扫描逻辑。

业务规则：
- 仅管理员可操作（路由层 role_required 拦截）
- watch_dir 必须存在且为目录
- interval_minutes 单位为秒，范围 [10, 86400]
- 立即扫描失败抛 ServiceError(code=500)
- 扫描调度（_schedule_next）由路由层在 CRUD 完成后调用，保持 service 独立性
"""
import logging
import os
from typing import List

logger = logging.getLogger(__name__)

from app.extensions import db
from app.models.scan_config import ScanConfig
from app.services.base import (
    BaseService, NotFoundError, ValidationError, ServiceError,
)
from app.utils.audit import log_operation


# 扫描间隔范围（秒）
MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 86400


def _normalize_interval(raw) -> int:
    """规范化扫描间隔：强制 int + 区间限制"""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = MIN_INTERVAL_SECONDS
    if v < MIN_INTERVAL_SECONDS:
        v = MIN_INTERVAL_SECONDS
    if v > MAX_INTERVAL_SECONDS:
        v = MAX_INTERVAL_SECONDS
    return v


class ScanConfigService(BaseService):
    """扫描配置服务"""

    def list_scan_configs(self) -> List[dict]:
        """获取所有扫描配置"""
        configs = ScanConfig.query.order_by(ScanConfig.created_at.desc()).all()
        return [c.to_dict() for c in configs]

    def create_scan_config(self, data: dict) -> ScanConfig:
        """新增扫描配置"""
        watch_dir = (data.get("watch_dir") or "").strip()
        if not watch_dir:
            raise ValidationError("监控文件夹路径不能为空")
        if not os.path.isdir(watch_dir):
            raise ValidationError("监控文件夹路径不存在")
        interval = _normalize_interval(data.get("interval_minutes", 60))
        config = ScanConfig(
            name=data.get("name", watch_dir),
            watch_dir=watch_dir,
            interval_minutes=interval,
            is_active=data.get("is_active", True),
            id_pattern=data.get("id_pattern", "SUBJ_{seq:04d}"),
            auto_upload_files=data.get("auto_upload_files", True),
            collection_batch=(data.get("collection_batch") or "").strip() or None,
            collection_scene=(data.get("collection_scene") or "").strip() or None,
        )
        db.session.add(config)
        self.session.flush()  # 让 config.id 可用
        log_operation(
            "create", "scan_config", config.id,
            f"新增扫描配置：{config.name}（路径: {watch_dir}）",
            operator=self._operator_user(),
        )
        # 业务数据 + 日志一次性原子提交
        self._commit()
        return config

    def update_scan_config(self, config_id: int, data: dict) -> ScanConfig:
        """更新扫描配置"""
        config = self._get_or_404(ScanConfig, config_id, "扫描配置不存在")
        if "name" in data:
            config.name = data["name"]
        if "watch_dir" in data:
            watch_dir = data["watch_dir"].strip()
            if not os.path.isdir(watch_dir):
                raise ValidationError("监控文件夹路径不存在")
            config.watch_dir = watch_dir
        if "interval_minutes" in data:
            config.interval_minutes = _normalize_interval(data["interval_minutes"])
        if "is_active" in data:
            config.is_active = data["is_active"]
        if "id_pattern" in data:
            config.id_pattern = data["id_pattern"]
        if "auto_upload_files" in data:
            config.auto_upload_files = data["auto_upload_files"]
        if "collection_batch" in data:
            config.collection_batch = (data["collection_batch"] or "").strip() or None
        if "collection_scene" in data:
            config.collection_scene = (data["collection_scene"] or "").strip() or None
        log_operation(
            "update", "scan_config", config.id,
            f"更新扫描配置：{config.name}",
            operator=self._operator_user(),
        )
        self._commit()
        return config

    def delete_scan_config(self, config_id: int) -> str:
        """删除扫描配置"""
        config = self._get_or_404(ScanConfig, config_id, "扫描配置不存在")
        name = config.name
        db.session.delete(config)
        log_operation(
            "delete", "scan_config", config_id,
            f"删除扫描配置：{name}",
            operator=self._operator_user(),
        )
        self._commit()
        return name

    def run_scan_now(self, config_id: int) -> dict:
        """立即执行一次扫描

        Returns:
            {new_count: int, failures: [{file, reason, last_error}]}
            失败文件列表供前端提示用户上传对应的外部密钥
        Raises:
            NotFoundError: 扫描配置不存在
            ServiceError: 扫描执行失败
        """
        from app.utils.scanner import scan_watch_dir
        config = self._get_or_404(ScanConfig, config_id, "扫描配置不存在")
        try:
            result = scan_watch_dir(config)
        except Exception as e:
            logger.error("立即扫描失败 config_id=%s: %s", config_id, e, exc_info=True)
            raise ServiceError("扫描失败，请检查监控目录可读性与外部密钥配置", code=500)
        new_count = result.get("new_count", 0)
        failures = result.get("failures", [])
        log_msg = f"手动扫描：{config.name}，新增 {new_count} 个受试者"
        if failures:
            log_msg += f"，{len(failures)} 个文件解密失败（待上传外部密钥）"
        log_operation(
            "scan", "scan_config", config_id, log_msg,
            operator=self._operator_user(),
        )
        self._commit()
        return {"new_count": new_count, "failures": failures}
