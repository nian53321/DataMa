# -*- coding: utf-8 -*-
"""desensitize.py 单元测试：脱敏工具

覆盖：
- mask_value 的全部算法：mask_all / mask_middle / mask_head / mask_tail /
  mask_email / hash / redact / 未知算法兜底
- 边界情况：None、空字符串、非字符串输入
- desensitize_dict 字典脱敏（含 admin 免脱敏、空值）
- desensitize_list 列表批量脱敏（含空列表、admin）

说明：mask_value 为纯函数；desensitize_dict / desensitize_list 通过直接注入
模块级 _cache 规则避免访问数据库。
"""
import hashlib

import pytest

from app.utils import desensitize


# ==================== mask_value 各算法 ====================

def test_mask_all():
    """测试 mask_all：全部字符替换为 mask_char"""
    assert desensitize.mask_value("1234567", "mask_all") == "*******"
    # 自定义替换字符
    assert desensitize.mask_value("abc", "mask_all", mask_char="#") == "###"


def test_mask_middle():
    """测试 mask_middle：保留首尾 N 位，中间替换"""
    result = desensitize.mask_value("13812345678", "mask_middle",
                                    keep_head=3, keep_tail=4)
    assert result == "138****5678"
    # 长度不足 keep_head + keep_tail 时全部脱敏
    assert desensitize.mask_value("123", "mask_middle",
                                  keep_head=3, keep_tail=4) == "***"
    # keep_tail=0 时尾部不留
    assert desensitize.mask_value("abcdef", "mask_middle",
                                  keep_head=2, keep_tail=0) == "ab****"


def test_mask_head():
    """测试 mask_head：保留前 keep_head 位，其余替换"""
    # 保留前 2 位
    assert desensitize.mask_value("abcdef", "mask_head", keep_head=2) == "ab****"
    # keep_head=0 全部替换
    assert desensitize.mask_value("abcdef", "mask_head", keep_head=0) == "******"
    # keep_head >= 长度时保持原样
    assert desensitize.mask_value("abc", "mask_head", keep_head=5) == "abc"


def test_mask_tail():
    """测试 mask_tail：保留后 keep_tail 位，其余替换"""
    # 保留后 2 位
    assert desensitize.mask_value("abcdef", "mask_tail", keep_tail=2) == "****ef"
    # keep_tail=0 全部替换
    assert desensitize.mask_value("abcdef", "mask_tail", keep_tail=0) == "******"
    # keep_tail >= 长度时保持原样
    assert desensitize.mask_value("abc", "mask_tail", keep_tail=5) == "abc"


def test_mask_email():
    """测试 mask_email：邮箱本地部分脱敏，域名保留"""
    # 长本地部分(>3)：保留首尾各 1 位
    assert desensitize.mask_value("user@example.com", "mask_email") == "u**r@example.com"
    # 短本地部分(2~3)：保留首位
    assert desensitize.mask_value("ab@example.com", "mask_email") == "a*@example.com"
    # 单字符本地部分
    assert desensitize.mask_value("a@example.com", "mask_email") == "*@example.com"
    # 无 @ 回退为 mask_middle 处理
    assert desensitize.mask_value("noreply", "mask_email",
                                  keep_head=1, keep_tail=1) == "n*****y"


def test_hash():
    """测试 hash：返回 SHA256 前 8 位十六进制"""
    expected = hashlib.sha256("hello".encode("utf-8")).hexdigest()[:8]
    assert desensitize.mask_value("hello", "hash") == expected
    # 相同输入结果稳定
    assert desensitize.mask_value("hello", "hash") == expected


def test_redact():
    """测试 redact：整体替换为 [REDACTED]"""
    assert desensitize.mask_value("secret", "redact") == "[REDACTED]"
    assert desensitize.mask_value("any value", "redact") == "[REDACTED]"


def test_unknown_algorithm_fallback():
    """测试未知算法兜底为全替换（mask_char * len）"""
    assert desensitize.mask_value("abc", "unknown_algo") == "***"


# ==================== mask_value 边界情况 ====================

def test_mask_value_none():
    """测试 None 输入直接返回 None"""
    assert desensitize.mask_value(None, "mask_all") is None


def test_mask_value_empty_string():
    """测试空字符串返回空字符串"""
    assert desensitize.mask_value("", "mask_all") == ""


def test_mask_value_non_string_input():
    """测试非字符串输入会被转为 str 后处理"""
    assert desensitize.mask_value(12345, "mask_all") == "*****"
    assert desensitize.mask_value(0, "redact") == "[REDACTED]"


# ==================== desensitize_dict / desensitize_list ====================

@pytest.fixture
def _inject_rules():
    """注入自定义脱敏规则到模块缓存，避免访问数据库

    规则：
      name   -> mask_all
      phone  -> mask_middle (保留前3后4)
      email  -> mask_email
      secret -> redact
    """
    rules = {
        "name": {"algorithm": "mask_all", "keep_head": 0,
                 "keep_tail": 0, "mask_char": "*"},
        "phone": {"algorithm": "mask_middle", "keep_head": 3,
                  "keep_tail": 4, "mask_char": "*"},
        "email": {"algorithm": "mask_email", "keep_head": 0,
                  "keep_tail": 0, "mask_char": "*"},
        "secret": {"algorithm": "redact", "keep_head": 0,
                   "keep_tail": 0, "mask_char": "*"},
    }
    desensitize._cache["loaded"] = True
    desensitize._cache["enabled"] = True
    desensitize._cache["rules"] = rules
    yield rules
    desensitize.clear_cache()


def test_desensitize_dict_applies_rules(_inject_rules):
    """测试 desensitize_dict 对字典各字段应用对应脱敏规则"""
    data = {
        "name": "ZhangSan",
        "phone": "13812345678",
        "email": "user@example.com",
        "secret": "topsecret",
        "unmapped": "keep_as_is",
    }
    result = desensitize.desensitize_dict(data, role="doctor")

    assert result["name"] == "********"            # mask_all 8 字符
    assert result["phone"] == "138****5678"          # mask_middle
    assert result["email"] == "u**r@example.com"     # mask_email
    assert result["secret"] == "[REDACTED]"          # redact
    # 未配置规则的字段保持原样
    assert result["unmapped"] == "keep_as_is"


def test_desensitize_dict_admin_no_desensitize(_inject_rules):
    """测试 admin 角色不脱敏，原样返回"""
    data = {"name": "ZhangSan", "secret": "topsecret"}
    result = desensitize.desensitize_dict(data, role="admin")
    assert result["name"] == "ZhangSan"
    assert result["secret"] == "topsecret"


def test_desensitize_dict_empty_input(_inject_rules):
    """测试空字典与 None 输入原样返回"""
    assert desensitize.desensitize_dict({}, role="doctor") == {}
    assert desensitize.desensitize_dict(None, role="doctor") is None


def test_desensitize_dict_missing_fields(_inject_rules):
    """测试字典中缺失部分规则字段时不报错，仅脱敏存在的字段"""
    data = {"name": "AB", "extra": "x"}  # 缺 phone/email/secret
    result = desensitize.desensitize_dict(data, role="nurse")
    assert result["name"] == "**"
    assert result["extra"] == "x"


def test_desensitize_list_applies_rules(_inject_rules):
    """测试 desensitize_list 批量脱敏列表中的每个字典"""
    items = [
        {"name": "AB", "phone": "13812345678"},
        {"name": "CD", "phone": "13900000000"},
    ]
    result = desensitize.desensitize_list(items, role="nurse")
    assert result[0]["name"] == "**"
    assert result[0]["phone"] == "138****5678"
    assert result[1]["name"] == "**"
    assert result[1]["phone"] == "139****0000"


def test_desensitize_list_empty(_inject_rules):
    """测试空列表输入返回空列表"""
    assert desensitize.desensitize_list([], role="doctor") == []


def test_desensitize_list_admin_no_desensitize(_inject_rules):
    """测试 admin 角色列表不脱敏"""
    items = [{"name": "ZhangSan", "secret": "topsecret"}]
    result = desensitize.desensitize_list(items, role="admin")
    assert result[0]["name"] == "ZhangSan"
    assert result[0]["secret"] == "topsecret"
