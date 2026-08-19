# -*- coding: utf-8 -*-
"""数据规范与受试者信息模板服务

封装原 api/system.py 的规范 CRUD 与受试者模板管理逻辑。

业务规则：
- 规范类型必须在白名单内（naming/storage/quality/security/process/dictionary/metadata）
- 受试者模板存储为 DataStandard(standard_type="subject_template", schema_json={fields: [...]})
- 受试者模板字段标识唯一、中文名必填、类型合法
- 创建/更新/删除自动留档快照 + 操作日志
"""
from typing import List

from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import DataStandard
from app.services.base import (
    BaseService, NotFoundError, ValidationError,
)
from app.utils.audit import log_operation, snapshot_update, snapshot_delete


# 规范类型白名单
VALID_STANDARD_TYPES = {
    "naming", "storage", "quality", "security",
    "process", "dictionary", "metadata", "subject_template",
}

# 受试者模板字段类型白名单
VALID_FIELD_TYPES = {"input", "number", "select", "textarea", "date"}

# 默认受试者字段模板
DEFAULT_SUBJECT_FIELDS = [
    {"field_key": "pseudo_id", "field_label": "受试者伪ID", "field_type": "input", "required": True, "enabled": True, "sort_order": 1, "placeholder": "如 SUBJ_001", "options": []},
    {"field_key": "age", "field_label": "年龄", "field_type": "number", "required": True, "enabled": True, "sort_order": 2, "placeholder": "0-120", "options": [], "max": 120},
    {"field_key": "gender", "field_label": "性别", "field_type": "select", "required": True, "enabled": True, "sort_order": 3, "placeholder": "", "options": [{"label": "男", "value": "男"}, {"label": "女", "value": "女"}]},
    {"field_key": "education_level", "field_label": "教育程度", "field_type": "input", "required": False, "enabled": True, "sort_order": 4, "placeholder": "如 高中/本科", "options": []},
    {"field_key": "phone", "field_label": "联系电话", "field_type": "input", "required": False, "enabled": True, "sort_order": 5, "placeholder": "如 13800138000", "options": []},
    {"field_key": "id_card", "field_label": "身份证号", "field_type": "input", "required": False, "enabled": True, "sort_order": 6, "placeholder": "如 110101199001011234", "options": []},
    {"field_key": "cognitive_risk_level", "field_label": "认知风险分级", "field_type": "select", "required": False, "enabled": True, "sort_order": 7, "placeholder": "", "options": [{"label": "正常", "value": "normal"}, {"label": "轻度认知障碍", "value": "mci"}, {"label": "痴呆", "value": "dementia"}]},
    {"field_key": "emotion_status", "field_label": "情绪状态", "field_type": "input", "required": False, "enabled": True, "sort_order": 8, "placeholder": "如 焦虑/抑郁", "options": []},
    # 量表得分（手动录入；与自动采集/外部推送的量表资产摘要并存，供列表与雷达图直接展示）
    {"field_key": "moca_score", "field_label": "MoCA 得分", "field_type": "number", "required": False, "enabled": True, "sort_order": 9, "placeholder": "0-30", "options": [], "max": 30},
    {"field_key": "mmse_score", "field_label": "MMSE 得分", "field_type": "number", "required": False, "enabled": True, "sort_order": 10, "placeholder": "0-30", "options": [], "max": 30},
    {"field_key": "ad8_score", "field_label": "AD8 得分", "field_type": "number", "required": False, "enabled": True, "sort_order": 11, "placeholder": "0-8", "options": [], "max": 8},
    {"field_key": "collection_batch", "field_label": "采集批次", "field_type": "autocomplete", "required": False, "enabled": True, "sort_order": 12, "placeholder": "如 BATCH_001（可选/可输入新值）", "options": []},
    {"field_key": "collection_scene", "field_label": "场景代码", "field_type": "autocomplete", "required": False, "enabled": True, "sort_order": 13, "placeholder": "如 SCENE_A（可选/可输入新值）", "options": []},
    {"field_key": "remark", "field_label": "备注", "field_type": "textarea", "required": False, "enabled": True, "sort_order": 14, "placeholder": "其他说明", "options": []},
]

# 字段类型中文名映射（供前端下拉使用）
FIELD_TYPE_OPTIONS = [
    {"value": "input", "label": "单行文本"},
    {"value": "number", "label": "数字"},
    {"value": "select", "label": "下拉选择"},
    {"value": "textarea", "label": "多行文本"},
    {"value": "date", "label": "日期"},
]


class StandardService(BaseService):
    """数据规范管理服务"""

    # ==================== 规范 CRUD ====================

    def list_standards(self, standard_type: str = "") -> List[dict]:
        """规范列表（按 standard_type 过滤）"""
        query = DataStandard.query
        if standard_type:
            query = query.filter_by(standard_type=standard_type)
        query = query.order_by(DataStandard.updated_at.desc())
        return [s.to_dict() for s in query.all()]

    def create_standard(self, data: dict) -> DataStandard:
        """创建规范"""
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("规范名称必填")
        standard_type = data.get("standard_type", "metadata")
        if standard_type not in VALID_STANDARD_TYPES:
            raise ValidationError(
                f"规范类型非法，可选：{', '.join(sorted(VALID_STANDARD_TYPES))}"
            )
        standard = DataStandard(
            name=name,
            standard_type=standard_type,
            data_type=data.get("data_type"),
            version=data.get("version", "1.0.0"),
            schema_json=data.get("schema"),
            description=data.get("description"),
            is_active=bool(data.get("is_active", True)),
        )
        db.session.add(standard)
        self.session.flush()  # 让 standard.id 可用
        log_operation(
            "create", "standard", standard.id,
            f"创建规范 {name}（类型：{standard_type}）",
            operator=self._operator_user(),
        )
        # 业务数据 + 日志一次性原子提交
        self._commit()
        return standard

    def update_standard(self, standard_id: int, data: dict) -> DataStandard:
        """更新规范"""
        standard = self._get_or_404(DataStandard, standard_id, "规范不存在")
        old_dict = standard.to_dict()

        if data.get("name") is not None:
            if not data["name"].strip():
                raise ValidationError("规范名称不能为空")
            standard.name = data["name"].strip()
        if data.get("standard_type"):
            if data["standard_type"] not in VALID_STANDARD_TYPES:
                raise ValidationError("规范类型非法")
            standard.standard_type = data["standard_type"]
        if "data_type" in data:
            standard.data_type = data.get("data_type")
        if data.get("version"):
            standard.version = data["version"]
        if "schema" in data:
            standard.schema_json = data["schema"]
            # JSON 列是可变对象，需显式标记修改以确保持久化
            flag_modified(standard, "schema_json")
        if "description" in data:
            standard.description = data.get("description")
        if "is_active" in data:
            standard.is_active = bool(data["is_active"])

        snapshot_update("data_standard", standard, old_dict, standard.to_dict(),
                        operator=self._operator_user())
        log_operation("update", "standard", standard_id, f"更新规范 {standard.name}",
                      operator=self._operator_user())
        # 业务数据 + 快照 + 日志一次性原子提交
        self._commit()
        return standard

    def delete_standard(self, standard_id: int) -> str:
        """删除规范"""
        standard = self._get_or_404(DataStandard, standard_id, "规范不存在")
        name = standard.name
        snapshot_delete("data_standard", standard, f"删除规范 {name}",
                        operator=self._operator_user())
        log_operation("delete", "standard", standard_id, f"删除规范 {name}",
                      operator=self._operator_user())
        db.session.delete(standard)
        self._commit()
        return name

    # ==================== 受试者信息模板 ====================

    def get_subject_template(self) -> dict:
        """获取受试者信息模板（含字段列表与可用字段类型）"""
        std = DataStandard.query.filter_by(
            standard_type="subject_template", is_active=True
        ).first()
        if std and std.schema_json and isinstance(std.schema_json, dict) \
                and std.schema_json.get("fields"):
            fields = std.schema_json["fields"]
        else:
            fields = DEFAULT_SUBJECT_FIELDS
        # 按 sort_order 稳定排序（旧库模板无量表字段时，升级注入的字段也能按预期顺序展示）
        fields = sorted(
            fields,
            key=lambda f: (
                f.get("sort_order") if isinstance(f, dict) and isinstance(f.get("sort_order"), (int, float)) else 999,
                fields.index(f),
            ),
        ) if isinstance(fields, list) else fields
        return {"fields": fields, "field_types": FIELD_TYPE_OPTIONS}

    def update_subject_template(self, fields: list) -> dict:
        """保存受试者信息模板（管理员）

        - 字段标识不能为空且唯一
        - 字段中文名不能为空
        - 字段类型必须在白名单内
        - 至少保留一个字段
        """
        if not isinstance(fields, list):
            raise ValidationError("fields 必须为数组")
        if not fields:
            raise ValidationError("至少需要保留一个字段")

        seen_keys = set()
        for f in fields:
            key = (f.get("field_key") or "").strip()
            if not key:
                raise ValidationError("字段标识不能为空")
            if key in seen_keys:
                raise ValidationError(f"字段标识重复: {key}")
            seen_keys.add(key)
            if not (f.get("field_label") or "").strip():
                raise ValidationError(f"字段【{key}】中文名不能为空")
            ft = f.get("field_type")
            if ft not in VALID_FIELD_TYPES:
                raise ValidationError(f"字段【{key}】类型非法: {ft}")

        std = DataStandard.query.filter_by(
            standard_type="subject_template", is_active=True
        ).first()
        is_new = False
        if not std:
            std = DataStandard(
                name="受试者信息模板",
                standard_type="subject_template",
                version="1.0.0",
                is_active=True,
            )
            db.session.add(std)
            is_new = True
        # 新建对象需先 flush 获取自增 ID，否则 snapshot_update 会写入 model_id=None
        if is_new:
            db.session.flush()
        old_dict = std.to_dict()
        std.schema_json = {"fields": fields}
        # 版本号自增：解析 major.minor.patch 格式，minor 递增
        parts = (std.version or "1.0.0").split(".")
        major = parts[0] if parts and parts[0].isdigit() else "1"
        minor = str(int(parts[1]) + 1) if len(parts) > 1 and parts[1].isdigit() else "1"
        std.version = f"{major}.{minor}.0"
        flag_modified(std, "schema_json")
        snapshot_update("standard", std, old_dict, std.to_dict(),
                        operator=self._operator_user())
        log_operation(
            "update", "subject_template", std.id,
            f"更新受试者信息模板（{len(fields)}个字段）",
            operator=self._operator_user(),
        )
        self._commit()
        return {"fields": fields}