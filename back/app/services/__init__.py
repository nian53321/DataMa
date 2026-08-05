# -*- coding: utf-8 -*-
"""业务服务层

职责边界：
- service 层封装业务规则、数据库操作、审计日志、事务边界
- API 路由层只负责：解析 request、获取 jwt 身份、构造 response、调用 service
- service 不依赖 Flask request/jwt，操作员上下文通过构造函数注入

使用方式：
    from app.services import UserService, ValidationError

    user_service = UserService(operator_id=current_uid, operator_role=current_role_str)
    user = user_service.create_user(data)

异常处理：
    service 抛 ServiceError 子类，由 app/__init__.py 的全局 errorhandler 转为 HTTP 响应。
    路由层无需 try/except，代码更扁平。

模块组织（按业务域划分，与 API 文件解耦）：
    - subject_service       受试者管理
    - asset_service         数据资产 + 文件服务
    - data_processing_service  清洗/标准化
    - snapshot_service      快照/操作日志
    - annotation_service    标注任务 + 工作流（待迁移）
    - label_service         标签管理（待迁移）
    - user_service          用户管理
    - role_service          角色菜单管理
    - standard_service      规范 + 受试者模板
    - desensitize_service   脱敏配置
    - encryption_service    密钥管理
    - scan_config_service   扫描配置
"""
from app.services.base import (
    BaseService,
    ServiceError,
    ValidationError,
    NotFoundError,
    PermissionDeniedError,
    ConflictError,
    OperationNotAllowedError,
)
from app.services.subject_service import SubjectService
from app.services.asset_service import AssetService
from app.services.data_processing_service import DataProcessingService
from app.services.snapshot_service import SnapshotService
from app.services.user_service import UserService
from app.services.role_service import RoleService
from app.services.standard_service import StandardService
from app.services.desensitize_service import DesensitizeService
from app.services.encryption_service import EncryptionService
from app.services.scan_config_service import ScanConfigService
from app.services.annotation_service import AnnotationService
from app.services.label_service import LabelService
from app.services.external_key_service import ExternalKeyService
from app.services.export_service import ExportService, export_task_manager

__all__ = [
    "BaseService",
    "ServiceError",
    "ValidationError",
    "NotFoundError",
    "PermissionDeniedError",
    "ConflictError",
    "OperationNotAllowedError",
    "SubjectService",
    "AssetService",
    "DataProcessingService",
    "SnapshotService",
    "UserService",
    "RoleService",
    "StandardService",
    "DesensitizeService",
    "EncryptionService",
    "ScanConfigService",
    "AnnotationService",
    "LabelService",
    "ExternalKeyService",
    "ExportService",
]