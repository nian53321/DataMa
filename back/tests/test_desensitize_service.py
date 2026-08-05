# -*- coding: utf-8 -*-
"""DesensitizeService 单元测试

覆盖脱敏配置业务规则：
- get_config：默认 enabled=True，返回规则列表+算法字典
- update_config：全量重建、field_key 唯一/必填、algorithm 白名单、清缓存
- preview：基于当前规则做脱敏对比、未启用规则不脱敏、总开关关闭时不脱敏
- 默认规则：conftest 已调用 init_default_desensitize 初始化 8 条默认规则
"""
import pytest

from app.extensions import db
from app.models.desensitize import DesensitizeSetting, DesensitizeRule
from app.services import DesensitizeService, ValidationError
from app.services.desensitize_service import (
    DESENSITIZE_ALGORITHMS, VALID_ALGORITHMS, DEFAULT_PREVIEW_SAMPLE,
)


class TestGetConfig:
    """get_config 业务规则"""

    def test_get_config_returns_defaults(self, db_app, admin_user):
        """conftest 已初始化 8 条默认规则，应全部返回"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        config = svc.get_config()
        assert "enabled" in config
        assert "rules" in config
        assert "algorithms" in config
        # 默认总开关应为 True
        assert config["enabled"] is True
        # 至少包含默认 8 条规则
        assert len(config["rules"]) >= 8
        # 算法字典完整
        assert len(config["algorithms"]) == len(DESENSITIZE_ALGORITHMS)

    def test_get_config_rules_ordered_by_sort_order(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        config = svc.get_config()
        sort_orders = [r["sort_order"] for r in config["rules"]]
        assert sort_orders == sorted(sort_orders)


class TestUpdateConfig:
    """update_config 业务规则"""

    def test_update_config_success(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        result = svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_middle", "keep_head": 3, "keep_tail": 4,
                 "mask_char": "*", "is_active": True, "sort_order": 1},
                {"field_key": "email", "field_label": "邮箱",
                 "algorithm": "mask_email", "keep_head": 0, "keep_tail": 0,
                 "mask_char": "*", "is_active": True, "sort_order": 2},
            ],
        })
        assert result["enabled"] is True
        assert result["rules_count"] == 2
        # 验证数据库已更新
        assert DesensitizeRule.query.count() == 2
        # 旧规则应被删除（全量重建）
        assert DesensitizeRule.query.filter_by(field_key="real_name").first() is None

    def test_update_config_disables_existing_rules(self, db_app, admin_user):
        """全量重建：不在新列表中的规则应被删除"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        # 默认有 8 条规则，现仅保留 1 条
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_all", "is_active": True, "sort_order": 1},
            ],
        })
        assert DesensitizeRule.query.count() == 1

    def test_update_config_duplicate_key_raises(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="字段标识重复: phone"):
            svc.update_config({
                "enabled": True,
                "rules": [
                    {"field_key": "phone", "field_label": "A",
                     "algorithm": "mask_all", "is_active": True, "sort_order": 1},
                    {"field_key": "phone", "field_label": "B",
                     "algorithm": "mask_all", "is_active": True, "sort_order": 2},
                ],
            })

    def test_update_config_empty_key_raises(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="字段标识不能为空"):
            svc.update_config({
                "enabled": True,
                "rules": [
                    {"field_key": "", "field_label": "X",
                     "algorithm": "mask_all", "is_active": True, "sort_order": 1},
                ],
            })

    def test_update_config_empty_label_raises(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="中文名不能为空"):
            svc.update_config({
                "enabled": True,
                "rules": [
                    {"field_key": "phone", "field_label": "",
                     "algorithm": "mask_all", "is_active": True, "sort_order": 1},
                ],
            })

    def test_update_config_invalid_algorithm_raises(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="算法非法"):
            svc.update_config({
                "enabled": True,
                "rules": [
                    {"field_key": "phone", "field_label": "X",
                     "algorithm": "unknown_algo", "is_active": True, "sort_order": 1},
                ],
            })

    def test_update_config_rules_not_list_raises(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        with pytest.raises(ValidationError, match="rules 必须为数组"):
            svc.update_config({"enabled": True, "rules": "not_a_list"})

    def test_update_config_upsert_keeps_id(self, db_app, admin_user):
        """对已存在的 field_key 应 upsert（保留原 id），而非删除后新建"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        # 先建一条规则
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_all", "keep_head": 0, "keep_tail": 0,
                 "mask_char": "*", "is_active": True, "sort_order": 1},
            ],
        })
        rule_before = DesensitizeRule.query.filter_by(field_key="phone").first()
        old_id = rule_before.id
        # 再次更新该规则（改变 algorithm）
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_middle", "keep_head": 3, "keep_tail": 4,
                 "mask_char": "#", "is_active": True, "sort_order": 1},
            ],
        })
        rule_after = DesensitizeRule.query.filter_by(field_key="phone").first()
        # 同一 field_key 应保留原 id
        assert rule_after.id == old_id
        assert rule_after.algorithm == "mask_middle"
        assert rule_after.keep_head == 3
        assert rule_after.mask_char == "#"


class TestPreview:
    """preview 业务规则"""

    def test_preview_with_default_sample(self, db_app, admin_user):
        """使用默认样例预览：应返回 before/after 两份对比"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        result = svc.preview()
        assert "before" in result
        assert "after" in result
        assert "enabled" in result
        assert "rules_count" in result
        # 默认样例 8 条
        assert len(result["before"]) == len(DEFAULT_PREVIEW_SAMPLE)
        assert len(result["after"]) == len(DEFAULT_PREVIEW_SAMPLE)

    def test_preview_with_custom_sample(self, db_app, admin_user):
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        custom = [
            {"field_key": "phone", "value": "13800138000"},
            {"field_key": "email", "value": "test@example.com"},
        ]
        result = svc.preview(custom)
        assert len(result["before"]) == 2
        assert result["before"][0]["value"] == "13800138000"

    def test_preview_masked_when_rule_active(self, db_app, admin_user):
        """已启用规则 + 总开关开启 → 应脱敏"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        # 配置 phone 用 mask_middle 保留前3后4
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_middle", "keep_head": 3, "keep_tail": 4,
                 "mask_char": "*", "is_active": True, "sort_order": 1},
            ],
        })
        result = svc.preview([{"field_key": "phone", "value": "13800138000"}])
        after = result["after"][0]
        assert after["desensitized"] is True
        # 13800138000 (11位) 保留前3后4 → 138****8000
        assert after["value"] == "138****8000"

    def test_preview_not_masked_when_rule_inactive(self, db_app, admin_user):
        """规则未启用 → 不脱敏，原样返回"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_middle", "keep_head": 3, "keep_tail": 4,
                 "mask_char": "*", "is_active": False, "sort_order": 1},
            ],
        })
        result = svc.preview([{"field_key": "phone", "value": "13800138000"}])
        after = result["after"][0]
        assert after["desensitized"] is False
        assert after["value"] == "13800138000"

    def test_preview_not_masked_when_global_disabled(self, db_app, admin_user):
        """总开关关闭 → 即使规则启用也不脱敏"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        svc.update_config({
            "enabled": False,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_middle", "keep_head": 3, "keep_tail": 4,
                 "mask_char": "*", "is_active": True, "sort_order": 1},
            ],
        })
        result = svc.preview([{"field_key": "phone", "value": "13800138000"}])
        after = result["after"][0]
        assert after["desensitized"] is False
        assert after["value"] == "13800138000"

    def test_preview_not_masked_when_no_rule(self, db_app, admin_user):
        """无对应规则的字段 → 原样返回"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        result = svc.preview([{"field_key": "unknown_field", "value": "abc"}])
        after = result["after"][0]
        assert after["desensitized"] is False
        assert after["value"] == "abc"

    def test_preview_redact_algorithm(self, db_app, admin_user):
        """redact 算法应返回 [REDACTED]"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "remark", "field_label": "备注",
                 "algorithm": "redact", "is_active": True, "sort_order": 1},
            ],
        })
        result = svc.preview([{"field_key": "remark", "value": "敏感备注内容"}])
        after = result["after"][0]
        assert after["desensitized"] is True
        assert after["value"] == "[REDACTED]"

    def test_preview_hash_algorithm(self, db_app, admin_user):
        """hash 算法应返回 SHA256 前 8 位"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "id_card", "field_label": "身份证",
                 "algorithm": "hash", "is_active": True, "sort_order": 1},
            ],
        })
        result = svc.preview([{"field_key": "id_card", "value": "110101199001011234"}])
        after = result["after"][0]
        assert after["desensitized"] is True
        # hash 返回 8 位十六进制
        assert len(after["value"]) == 8
        int(after["value"], 16)  # 应能解析为十六进制

    def test_preview_mask_all_algorithm(self, db_app, admin_user):
        """mask_all 算法应全部替换为 mask_char"""
        svc = DesensitizeService(operator_id=admin_user.id, operator_role="admin")
        svc.update_config({
            "enabled": True,
            "rules": [
                {"field_key": "phone", "field_label": "电话",
                 "algorithm": "mask_all", "mask_char": "#",
                 "is_active": True, "sort_order": 1},
            ],
        })
        result = svc.preview([{"field_key": "phone", "value": "13800138000"}])
        after = result["after"][0]
        assert after["desensitized"] is True
        # 11 位全部替换为 #
        assert after["value"] == "#" * 11
