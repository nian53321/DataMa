# -*- coding: utf-8 -*-
"""脱敏配置服务

封装原 api/system.py 的脱敏配置查询/更新/预览逻辑。

业务规则：
- 总开关 enabled 控制脱敏是否生效
- 规则字段：field_key（唯一）/ field_label / algorithm / keep_head / keep_tail / mask_char / is_active / sort_order
- 算法白名单：mask_all / mask_middle / mask_head / mask_tail / mask_email / hash / redact
- 更新采用全量重建策略（按 field_key upsert，不在列表中的规则删除）
- 配置变更后必须 clear_cache 让新规则立即生效
- 非 admin 角色不可编辑（路由层 role_required 拦截）
"""
from typing import List

from app.extensions import db
from app.models.desensitize import DesensitizeSetting, DesensitizeRule
from app.services.base import BaseService, ValidationError
from app.utils.audit import log_operation
from app.utils.desensitize import mask_value, clear_cache


# 脱敏算法白名单（含中文标签供前端下拉使用）
DESENSITIZE_ALGORITHMS = [
    {"value": "mask_all", "label": "全部替换"},
    {"value": "mask_middle", "label": "保留首尾(中间替换)"},
    {"value": "mask_head", "label": "替换前N位"},
    {"value": "mask_tail", "label": "替换后N位(0=全部)"},
    {"value": "mask_email", "label": "邮箱专用"},
    {"value": "hash", "label": "哈希(SHA256前8位)"},
    {"value": "redact", "label": "完全抹除([REDACTED])"},
]

VALID_ALGORITHMS = {a["value"] for a in DESENSITIZE_ALGORITHMS}

# 默认预览样例数据（覆盖常见敏感字段）
DEFAULT_PREVIEW_SAMPLE = [
    {"field_key": "real_name", "value": "张三丰"},
    {"field_key": "phone", "value": "13800138000"},
    {"field_key": "email", "value": "zhangsanfeng@example.com"},
    {"field_key": "license_no", "value": "110123456789012"},
    {"field_key": "id_card", "value": "110101199001011234"},
    {"field_key": "gender", "value": "男"},
    {"field_key": "age", "value": "62"},
    {"field_key": "address", "value": "北京市海淀区中关村大街1号"},
    {"field_key": "remark", "value": "受试者主诉偶有头晕，家属反馈近期记忆力下降"},
    {"field_key": "pseudo_id", "value": "SUB-2026-001"},
]


class DesensitizeService(BaseService):
    """脱敏配置服务"""

    def get_config(self) -> dict:
        """获取脱敏配置（总开关 + 规则列表 + 算法字典）"""
        setting = DesensitizeSetting.query.first()
        enabled = setting.enabled if setting else True
        rules = DesensitizeRule.query.order_by(DesensitizeRule.sort_order.asc()).all()
        return {
            "enabled": enabled,
            "rules": [r.to_dict() for r in rules],
            "algorithms": DESENSITIZE_ALGORITHMS,
        }

    def update_config(self, data: dict) -> dict:
        """保存脱敏配置（管理员）

        - rules 必须为数组
        - field_key 不能为空且唯一
        - field_label 不能为空
        - algorithm 必须在白名单内
        - 全量重建规则（按 field_key upsert，删除不在列表中的）
        - 变更后清缓存
        """
        enabled = bool(data.get("enabled", True))
        rules_data = data.get("rules", [])
        if not isinstance(rules_data, list):
            raise ValidationError("rules 必须为数组")

        # 字段校验
        seen_keys = set()
        for r in rules_data:
            key = (r.get("field_key") or "").strip()
            if not key:
                raise ValidationError("字段标识不能为空")
            if key in seen_keys:
                raise ValidationError(f"字段标识重复: {key}")
            seen_keys.add(key)
            if not (r.get("field_label") or "").strip():
                raise ValidationError(f"字段【{key}】中文名不能为空")
            alg = r.get("algorithm")
            if alg not in VALID_ALGORITHMS:
                raise ValidationError(f"字段【{key}】算法非法: {alg}")

        # 更新总开关
        setting = DesensitizeSetting.query.first()
        if not setting:
            setting = DesensitizeSetting(id=1, enabled=enabled)
            db.session.add(setting)
        else:
            setting.enabled = enabled

        # 全量重建规则（按 field_key upsert，删除不在列表中的规则）
        existing_map = {r.field_key: r for r in DesensitizeRule.query.all()}
        keep_keys = set()
        for idx, r in enumerate(rules_data):
            key = r["field_key"].strip()
            keep_keys.add(key)
            rule = existing_map.get(key)
            if not rule:
                rule = DesensitizeRule(field_key=key)
                db.session.add(rule)
            rule.field_label = r["field_label"].strip()
            rule.algorithm = r["algorithm"]
            rule.keep_head = int(r.get("keep_head", 0) or 0)
            rule.keep_tail = int(r.get("keep_tail", 0) or 0)
            rule.mask_char = (r.get("mask_char") or "*")[:4]
            rule.is_active = bool(r.get("is_active", True))
            rule.sort_order = int(r.get("sort_order", idx + 1))

        # 删除已移除的字段规则
        for key, rule in existing_map.items():
            if key not in keep_keys:
                db.session.delete(rule)

        self._commit()
        # 清缓存让新配置立即生效
        clear_cache()

        log_operation(
            "update", "desensitize_config", "config",
            f"更新脱敏配置：总开关={'开启' if enabled else '关闭'}，规则数={len(rules_data)}",
            operator=self._operator_user(),
        )
        self._commit()
        return {"enabled": enabled, "rules_count": len(rules_data)}

    def preview(self, sample: list = None) -> dict:
        """脱敏效果预览：基于当前配置规则对样例数据做脱敏对比

        :param sample: 自定义样例 [{field_key, value}]，为空则使用内置默认样例
        """
        custom_sample = sample
        sample_data = (custom_sample if isinstance(custom_sample, list) and custom_sample
                       else DEFAULT_PREVIEW_SAMPLE)

        # 加载当前配置规则（含未启用规则，用于预览）
        setting = DesensitizeSetting.query.first()
        enabled = setting.enabled if setting else True
        all_rules = {r.field_key: r for r in DesensitizeRule.query.all()}

        before = []
        after = []
        for s in sample_data:
            key = s.get("field_key", "")
            val = s.get("value", "")
            before.append({"field_key": key, "value": val})
            rule = all_rules.get(key)
            if rule and rule.is_active and enabled:
                masked = mask_value(
                    val, rule.algorithm,
                    rule.keep_head, rule.keep_tail, rule.mask_char or "*",
                )
                after.append({
                    "field_key": key, "value": masked,
                    "desensitized": True,
                    "field_label": rule.field_label,
                    "algorithm": rule.algorithm,
                })
            else:
                after.append({
                    "field_key": key, "value": val,
                    "desensitized": False,
                    "field_label": rule.field_label if rule else key,
                    "algorithm": rule.algorithm if rule else None,
                })

        return {
            "before": before,
            "after": after,
            "enabled": enabled,
            "rules_count": len(all_rules),
        }