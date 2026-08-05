# -*- coding: utf-8 -*-
"""naming.py 单元测试：命名规范引擎

覆盖：
- apply_naming_standard：模板变量替换、defaults 填充、自定义时间戳格式、
  扩展名提取、无 subject、空模板回退、未知变量回退、特殊字符安全化、
  {seq} / {original} 变量
- validate_extension：合法/非法扩展名校验、大小写不敏感、无规范放行、
  空允许列表放行（通过 monkeypatch 注入 get_naming_standard 避免数据库）
- DEFAULT_NAMING_STANDARDS 结构校验
- get_naming_standard：依赖数据库，测试环境跳过
"""
import pytest

from app.utils import naming


class FakeStandard:
    """模拟 DataStandard 实例（仅含 schema_json 等必要属性）"""

    def __init__(self, schema_json, data_type="all"):
        self.schema_json = schema_json
        self.standard_type = "naming"
        self.data_type = data_type
        self.is_active = True


class FakeSubject:
    """模拟 Subject 实例（仅含命名所需属性）"""

    def __init__(self, pseudo_id="SUBJ001", scene="SC01", batch="B01", sid=1):
        self.id = sid
        self.pseudo_id = pseudo_id
        self.collection_scene = scene
        self.collection_batch = batch


# ==================== apply_naming_standard ====================

def test_apply_naming_standard_template_substitution():
    """测试模板变量替换：{data_type}/{pseudo_id}/{timestamp}/{scene}/{batch}"""
    std = FakeStandard({
        "template": "{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}",
        "defaults": {"scene": "SC", "batch": "B01"},
        "timestamp_format": "%Y%m%d%H%M%S",
        "allowed_extensions": ["mp4", "wav"],
    })
    subject = FakeSubject(pseudo_id="SUBJ001", scene="ROOM_A", batch="B02")
    name, ext = naming.apply_naming_standard(std, subject, "video", "raw.mp4")

    # 扩展名小写无点
    assert ext == "mp4"
    # 首段为 data_type，第二段为 pseudo_id
    parts = name.split("_")
    assert parts[0] == "video"
    assert parts[1] == "SUBJ001"
    # 时间戳为 14 位数字
    assert len(parts[2]) == 14 and parts[2].isdigit()
    # 末尾为场景与批次
    assert name.endswith("_ROOM_A_B02")


def test_apply_naming_standard_defaults_applied():
    """测试 defaults 填充空字段（scene/batch 为 None 时用 defaults）"""
    std = FakeStandard({
        "template": "{data_type}_{pseudo_id}_{scene}_{batch}",
        "defaults": {"scene": "DEFAULT_SC", "batch": "DEFAULT_B"},
    })
    subject = FakeSubject(pseudo_id="S1", scene=None, batch=None)
    name, ext = naming.apply_naming_standard(std, subject, "audio", "x.wav")
    assert name == "audio_S1_DEFAULT_SC_DEFAULT_B"


def test_apply_naming_standard_custom_timestamp_format():
    """测试自定义 timestamp_format（如 %Y-%m-%d）"""
    std = FakeStandard({
        "template": "{data_type}_{timestamp}",
        "timestamp_format": "%Y-%m-%d",
    })
    subject = FakeSubject()
    name, ext = naming.apply_naming_standard(std, subject, "eeg", "a.edf")
    parts = name.split("_")
    # 日期格式 YYYY-MM-DD（长度 10，含两个连字符）
    assert len(parts[1]) == 10
    assert parts[1][4] == "-" and parts[1][7] == "-"


def test_apply_naming_standard_extension_extraction():
    """测试扩展名提取：小写、无点、多点取最后一段、无扩展名返回空"""
    std = FakeStandard({"template": "{data_type}"})
    subject = FakeSubject()

    # 大写扩展名转小写
    _, ext = naming.apply_naming_standard(std, subject, "video", "FILE.MP4")
    assert ext == "mp4"
    # 多点文件取最后一段
    _, ext = naming.apply_naming_standard(std, subject, "video", "a.b.c.wav")
    assert ext == "wav"
    # 无扩展名
    _, ext = naming.apply_naming_standard(std, subject, "video", "noext")
    assert ext == ""


def test_apply_naming_standard_no_subject():
    """测试无 subject 时 pseudo_id 等为空，并用 defaults/NA 兜底"""
    std = FakeStandard({
        "template": "{data_type}_{pseudo_id}_{scene}",
        "defaults": {"scene": "NA"},
    })
    name, ext = naming.apply_naming_standard(std, None, "video", "f.mp4")
    # pseudo_id 为空经 _safe_segment 兜底为 "NA"，scene 由 defaults 填为 "NA"
    assert name == "video_NA_NA"


def test_apply_naming_standard_empty_template_fallback():
    """测试空模板时回退到默认模板"""
    std = FakeStandard({"template": ""})
    subject = FakeSubject(pseudo_id="S1")
    name, ext = naming.apply_naming_standard(std, subject, "video", "f.mp4")
    # 默认模板含 data_type 与 pseudo_id
    assert "video" in name
    assert "S1" in name


def test_apply_naming_standard_unknown_variable():
    """测试模板含未知变量时回退到 original 文件名"""
    std = FakeStandard({"template": "{data_type}_{nonexistent_var}"})
    subject = FakeSubject(pseudo_id="S1")
    name, ext = naming.apply_naming_standard(std, subject, "video", "myfile.mp4")
    # 触发 KeyError 后回退到 original
    assert name == "myfile"


def test_apply_naming_standard_special_chars_sanitized():
    """测试特殊字符（空格、斜杠等）被替换为下划线"""
    std = FakeStandard({"template": "{data_type}_{pseudo_id}"})
    subject = FakeSubject(pseudo_id="SUBJ 01/2024")
    name, ext = naming.apply_naming_standard(std, subject, "video", "f.mp4")
    assert " " not in name
    assert "/" not in name
    # 含被替换后的伪 ID
    assert "SUBJ_01_2024" in name


def test_apply_naming_standard_none_standard():
    """测试 standard 为 None 时使用默认模板生成"""
    subject = FakeSubject(pseudo_id="S1", scene="SC", batch="B01")
    name, ext = naming.apply_naming_standard(None, subject, "video", "f.mp4")
    assert "video" in name
    assert "S1" in name


def test_apply_naming_standard_seq_variable():
    """测试 {seq} 变量：无 DB 时 _next_seq 回退为 1，seq 渲染为 001"""
    std = FakeStandard({"template": "{data_type}_{pseudo_id}_{seq}"})
    subject = FakeSubject(pseudo_id="S1")
    name, ext = naming.apply_naming_standard(std, subject, "video", "f.mp4")
    assert name.endswith("_001")


def test_apply_naming_standard_original_variable():
    """测试 {original} 变量使用原始文件名（不含扩展名，且安全化）"""
    std = FakeStandard({"template": "{original}"})
    subject = FakeSubject()
    name, ext = naming.apply_naming_standard(std, subject, "video", "my recording.mp4")
    # original_stem="my recording"，安全化后空格变下划线
    assert name == "my_recording"


def test_apply_naming_standard_date_variable():
    """测试 {date} 变量为北京时间日期 YYYYMMDD"""
    std = FakeStandard({"template": "{data_type}_{date}"})
    subject = FakeSubject()
    name, ext = naming.apply_naming_standard(std, subject, "video", "f.mp4")
    parts = name.split("_")
    assert len(parts[1]) == 8 and parts[1].isdigit()


# ==================== validate_extension ====================

def test_validate_extension_allowed(monkeypatch):
    """测试扩展名在允许列表内：通过"""
    fake_std = FakeStandard({"allowed_extensions": ["mp4", "wav", "csv"]})
    monkeypatch.setattr(naming, "get_naming_standard", lambda dt: fake_std)

    ok, msg = naming.validate_extension("video", "mp4")
    assert ok is True
    assert msg is None


def test_validate_extension_not_allowed(monkeypatch):
    """测试扩展名不在允许列表内：拒绝并返回提示"""
    fake_std = FakeStandard({"allowed_extensions": ["mp4", "wav"]})
    monkeypatch.setattr(naming, "get_naming_standard", lambda dt: fake_std)

    ok, msg = naming.validate_extension("video", "exe")
    assert ok is False
    assert "exe" in msg


def test_validate_extension_case_insensitive(monkeypatch):
    """测试允许列表大小写不敏感（allowed 大写，ext 小写也能通过）"""
    fake_std = FakeStandard({"allowed_extensions": ["MP4", "WAV"]})
    monkeypatch.setattr(naming, "get_naming_standard", lambda dt: fake_std)

    ok, _ = naming.validate_extension("video", "mp4")
    assert ok is True


def test_validate_extension_no_standard(monkeypatch):
    """测试无命名规范时一律放行"""
    monkeypatch.setattr(naming, "get_naming_standard", lambda dt: None)

    ok, msg = naming.validate_extension("video", "anything")
    assert ok is True
    assert msg is None


def test_validate_extension_empty_allowed_list(monkeypatch):
    """测试 allowed_extensions 为空列表时一律放行"""
    fake_std = FakeStandard({"allowed_extensions": []})
    monkeypatch.setattr(naming, "get_naming_standard", lambda dt: fake_std)

    ok, msg = naming.validate_extension("video", "exe")
    assert ok is True
    assert msg is None


def test_validate_extension_no_schema_json(monkeypatch):
    """测试规范存在但 schema_json 为 None 时放行"""
    fake_std = FakeStandard(None)
    monkeypatch.setattr(naming, "get_naming_standard", lambda dt: fake_std)

    ok, msg = naming.validate_extension("video", "exe")
    assert ok is True
    assert msg is None


# ==================== DEFAULT_NAMING_STANDARDS ====================

def test_default_naming_standards_structure():
    """测试 DEFAULT_NAMING_STANDARDS 默认命名规范结构完整"""
    assert isinstance(naming.DEFAULT_NAMING_STANDARDS, list)
    assert len(naming.DEFAULT_NAMING_STANDARDS) >= 1

    item = naming.DEFAULT_NAMING_STANDARDS[0]
    assert item["standard_type"] == "naming"
    assert item["name"]
    assert item["version"]

    schema = item["schema_json"]
    assert "template" in schema
    assert "defaults" in schema
    assert "timestamp_format" in schema
    assert "allowed_extensions" in schema

    # 模板含核心变量
    template = schema["template"]
    assert "{data_type}" in template
    assert "{pseudo_id}" in template
    assert "{timestamp}" in template


# ==================== get_naming_standard（依赖数据库） ====================

def test_get_naming_standard_needs_database():
    """测试 get_naming_standard：依赖数据库 DataStandard 查询，测试环境跳过"""
    pytest.skip("get_naming_standard 依赖数据库 DataStandard 查询，测试环境无 DB 连接")
