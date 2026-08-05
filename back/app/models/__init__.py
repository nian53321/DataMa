# -*- coding: utf-8 -*-
"""数据模型"""
from app.models.user import User, Role, init_default_admin
from app.models.subject import Subject
from app.models.data import DataAsset, DataVersion, DataType, DataLayer, VIDEO_TYPES, VIDEO_TYPE_LABELS
from app.models.annotation import (
    AnnotationTask,
    Annotation,
    AnnotationVersion,
    AnnotationStatus,
)
from app.models.standard import DataStandard, StandardVersion
from app.models.operation_log import OperationLog
from app.models.role_menu import (
    RoleMenu,
    RoleDef,
    MENU_DEFINITIONS,
    DEFAULT_ROLE_MENUS,
    ROLE_LABELS,
    ALL_MENU_KEYS,
    SYSTEM_ROLE_KEYS,
    get_role_menus,
    set_role_menus,
    get_role_label,
    init_default_role_menus,
    init_default_role_defs,
)
from app.models.data_snapshot import (
    DataSnapshot,
    save_snapshot,
    diff_summary,
    rollback_snapshot,
)
from app.models.label import Label, DEFAULT_LABELS, init_default_labels
from app.models.desensitize import (
    DesensitizeSetting,
    DesensitizeRule,
    DEFAULT_RULES,
    init_default_desensitize,
)
from app.models.scan_config import ScanConfig
from app.models.external_key import ExternalKey

__all__ = [
    "User",
    "Role",
    "init_default_admin",
    "Subject",
    "DataAsset",
    "DataVersion",
    "DataType",
    "DataLayer",
    "VIDEO_TYPES",
    "VIDEO_TYPE_LABELS",
    "AnnotationTask",
    "Annotation",
    "AnnotationVersion",
    "AnnotationStatus",
    "DataStandard",
    "StandardVersion",
    "OperationLog",
    "RoleMenu",
    "RoleDef",
    "MENU_DEFINITIONS",
    "DEFAULT_ROLE_MENUS",
    "ROLE_LABELS",
    "ALL_MENU_KEYS",
    "SYSTEM_ROLE_KEYS",
    "get_role_menus",
    "set_role_menus",
    "get_role_label",
    "init_default_role_menus",
    "init_default_role_defs",
    "DataSnapshot",
    "save_snapshot",
    "diff_summary",
    "rollback_snapshot",
    "Label",
    "DEFAULT_LABELS",
    "init_default_labels",
    "DesensitizeSetting",
    "DesensitizeRule",
    "DEFAULT_RULES",
    "init_default_desensitize",
    "ScanConfig",
    "ExternalKey",
]
