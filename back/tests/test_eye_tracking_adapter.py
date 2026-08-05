# -*- coding: utf-8 -*-
"""眼动评估数据适配器测试

测试覆盖：
1. is_sync_data_file 文件名匹配
2. load_sync_data 仅解析原始 JSON（不做字段映射）
3. find_sync_data_file 目录查找
4. API /parse-sync-data 接口（仅返回原始 JSON，不映射字段）
5. scanner 集成（sync_data.json 存为 DataAsset，字段入 metadata_json，不映射到 Subject）
"""
import io
import os
import json
import shutil

import pytest


# ==================== 测试数据 ====================

# 基于样例数据的测试 JSON
SAMPLE_SYNC_DATA = {
    "id": 184542,
    "estimate_num": "CAD20260721171326078001",
    "risk_value": 3.384,
    "risk_proportion": 0.8192,
    "age_rang": "您当前分析年龄段为60岁以下",
    "fir_capacity_value": 0.97,
    "sec_capacity_value": 0.97,
    "third_capacity_value": 0.66,
    "fourth_capacity_value": None,
    "fifth_capacity_value": None,
    "custom_phone": "18678127321",
    "estimate_time": "2026-07-21 17:13:26",
    "estimate_end_time": "2026-07-21 17:19:54",
    "archives_no": "DA20240424105256035208818",
    "organ_num": "JG20230726388225",
    "estimate_serve_code": "EDB-AD",
    "status": 3,
    "custom_name": "崔晓雨",
}


def _write_sync_data(tmp_path, filename="CAD20260721171326078001_sync_data.json"):
    """在临时目录写入测试用 sync_data.json"""
    fpath = os.path.join(tmp_path, filename)
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(SAMPLE_SYNC_DATA, f, ensure_ascii=False)
    return fpath


# ==================== 单元测试 ====================

class TestEyeTrackingAdapter:
    """眼动评估数据适配器测试"""

    def test_is_sync_data_file_match(self):
        """文件名匹配：*_sync_data.json"""
        from app.utils.eye_tracking_adapter import is_sync_data_file
        assert is_sync_data_file("CAD20260721171326078001_sync_data.json")
        assert is_sync_data_file("abc_sync_data.json")
        assert is_sync_data_file("XYZ_sync_data.JSON")  # 大小写不敏感

    def test_is_sync_data_file_no_match(self):
        """文件名不匹配：非 sync_data 文件"""
        from app.utils.eye_tracking_adapter import is_sync_data_file
        assert not is_sync_data_file("userInfo.json")
        assert not is_sync_data_file("sync_data.json")  # 需有前缀
        assert not is_sync_data_file("sync_fields_zh.json")
        assert not is_sync_data_file("eeg_data.csv")

    def test_load_sync_data_returns_raw(self, tmp_path):
        """load_sync_data 仅解析原始 JSON，不做字段映射"""
        from app.utils.eye_tracking_adapter import load_sync_data
        fpath = _write_sync_data(tmp_path)
        raw = load_sync_data(fpath)

        assert raw is not None
        # 返回原始字段，不做映射
        assert raw["risk_value"] == 3.384
        assert raw["custom_phone"] == "18678127321"
        assert raw["estimate_num"] == "CAD20260721171326078001"
        assert raw["estimate_time"] == "2026-07-21 17:13:26"
        assert raw["age_rang"] == "您当前分析年龄段为60岁以下"
        # 不应包含映射后的 Subject 字段名
        assert "cognitive_risk_level" not in raw
        assert "collection_batch" not in raw
        assert "moca_score" not in raw

    def test_load_sync_data_no_mapping(self, tmp_path):
        """load_sync_data 不做字段映射：不生成 cognitive_risk_level 等 Subject 字段"""
        from app.utils.eye_tracking_adapter import load_sync_data
        data = dict(SAMPLE_SYNC_DATA, fifth_capacity_value=0.85)
        fpath = os.path.join(tmp_path, "TEST_sync_data.json")
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        raw = load_sync_data(fpath)
        # fifth_capacity_value 保留原值，不映射为 moca_score
        assert raw["fifth_capacity_value"] == 0.85
        assert "moca_score" not in raw

    def test_load_sync_data_no_auto_classify(self, tmp_path):
        """不自动分级：risk_value 保留原始数值，不转为 normal/mci/dementia"""
        from app.utils.eye_tracking_adapter import load_sync_data
        fpath = _write_sync_data(tmp_path)
        raw = load_sync_data(fpath)
        # 应保留原始数值 3.384，不是 normal/mci/dementia
        assert raw["risk_value"] == 3.384
        assert "cognitive_risk_level" not in raw

    def test_load_sync_data_invalid_file(self, tmp_path):
        """无效 JSON 文件返回 None"""
        from app.utils.eye_tracking_adapter import load_sync_data
        fpath = os.path.join(tmp_path, "BAD_sync_data.json")
        with open(fpath, "w") as f:
            f.write("not a json {{{")
        assert load_sync_data(fpath) is None

    def test_find_sync_data_file(self, tmp_path):
        """在目录中查找 sync_data.json"""
        from app.utils.eye_tracking_adapter import find_sync_data_file
        _write_sync_data(tmp_path)
        # 同时放置干扰文件
        with open(os.path.join(tmp_path, "userInfo.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(tmp_path, "CAD_sync_fields_zh.json"), "w") as f:
            f.write("{}")

        result = find_sync_data_file(str(tmp_path))
        assert result is not None
        assert result.endswith("_sync_data.json")

    def test_find_sync_data_file_not_found(self, tmp_path):
        """目录中无 sync_data.json 返回 None"""
        from app.utils.eye_tracking_adapter import find_sync_data_file
        assert find_sync_data_file(str(tmp_path)) is None


# ==================== API 集成测试 ====================

class TestParseSyncDataAPI:
    """API /parse-sync-data 接口测试（仅返回原始 JSON，不映射字段）"""

    def test_parse_sync_data_api(self, db_app, admin_client):
        """手动上传 sync_data.json 解析：仅返回原始 JSON，不映射字段"""
        content = json.dumps(SAMPLE_SYNC_DATA, ensure_ascii=False).encode("utf-8")
        resp = admin_client.post(
            "/api/data/parse-sync-data",
            data={
                "file": (io.BytesIO(content), "CAD20260721171326078001_sync_data.json"),
            },
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        # 仅返回 raw（原始 JSON），不返回 fields
        assert "raw" in data
        assert "fields" not in data
        # raw 中保留原始字段
        assert data["raw"]["risk_value"] == 3.384
        assert data["raw"]["custom_phone"] == "18678127321"
        assert data["raw"]["estimate_num"] == "CAD20260721171326078001"

    def test_parse_sync_data_api_no_file(self, db_app, admin_client):
        """未上传文件返回 422"""
        resp = admin_client.post("/api/data/parse-sync-data", data={})
        assert resp.status_code == 422

    def test_parse_sync_data_api_permission(self, db_app, annotator_client):
        """annotator 无权限（需要 ADMIN/NURSE/ENGINEER）"""
        resp = annotator_client.post("/api/data/parse-sync-data", data={})
        assert resp.status_code == 403


# ==================== Scanner 集成测试 ====================

class TestScannerSyncDataIntegration:
    """扫描器集成测试：sync_data.json 仅存为 DataAsset，不映射到 Subject"""

    def test_scan_imports_sync_data(self, db_app, tmp_path):
        """扫描含 sync_data.json 的文件夹：仅存为数据资产，不映射字段到 Subject"""
        from app.extensions import db
        from app.utils.scanner import scan_watch_dir
        from app.models import Subject, DataAsset
        from app.models.scan_config import ScanConfig

        # 构造扫描目录结构
        watch_dir = tmp_path / "watch"
        subj_dir = watch_dir / "SUBJ_EYE001"
        subj_dir.mkdir(parents=True)

        # 写入 userInfo.json
        with open(subj_dir / "userInfo.json", "w", encoding="utf-8") as f:
            json.dump({"id": "SUBJ_EYE001", "age": 65, "sex": 0}, f)

        # 写入 sync_data.json
        with open(subj_dir / "CAD20260721171326078001_sync_data.json", "w", encoding="utf-8") as f:
            json.dump(SAMPLE_SYNC_DATA, f, ensure_ascii=False)

        # 写入 sync_fields_zh.json（应被跳过）
        with open(subj_dir / "CAD20260721171326078001_sync_fields_zh.json", "w", encoding="utf-8") as f:
            json.dump({"id": "测试"}, f)

        # 写入一个普通数据文件
        with open(subj_dir / "eeg_test.csv", "w") as f:
            f.write("eeg,data\n1,2\n")

        # 创建扫描配置
        config = ScanConfig(
            name="test",
            watch_dir=str(watch_dir),
            is_active=True,
            auto_upload_files=True,
            interval_minutes=60,
        )
        db.session.add(config)
        db.session.commit()

        # 执行扫描
        result = scan_watch_dir(config)
        assert result["new_count"] == 1

        # 验证 Subject 字段：不映射 sync_data 字段
        subject = Subject.query.filter_by(pseudo_id="SUBJ_EYE001").first()
        assert subject is not None
        # age 来自 userInfo.json
        assert subject.age == 65
        # 以下字段不应被 sync_data 映射（保持 None 或 userInfo 原值）
        assert subject.cognitive_risk_level is None
        assert subject.phone is None
        assert subject.collection_batch is None

        # 验证数据资产
        assets = DataAsset.query.filter_by(subject_id=subject.id).all()
        # 应有 2 个资产：sync_data.json (eye/feature) + eeg_test.csv (eeg/raw)
        # sync_fields_zh.json 应被跳过
        assert len(assets) == 2

        # 找到眼动数据资产
        eye_assets = [a for a in assets if a.data_type.value == "eye"]
        assert len(eye_assets) == 1
        assert eye_assets[0].layer.value == "feature"
        assert "sync_data.json" in eye_assets[0].file_name
        # metadata_json 应包含解析后的原始字段（仅解析存储，不映射）
        assert eye_assets[0].metadata_json is not None
        assert eye_assets[0].metadata_json["risk_value"] == 3.384
        assert eye_assets[0].metadata_json["custom_phone"] == "18678127321"
        assert eye_assets[0].metadata_json["estimate_num"] == "CAD20260721171326078001"

        # 找到 EEG 数据资产
        eeg_assets = [a for a in assets if a.data_type.value == "eeg"]
        assert len(eeg_assets) == 1
        assert eeg_assets[0].layer.value == "raw"

        # sync_fields_zh.json 不应出现在资产中
        for a in assets:
            assert "sync_fields_zh" not in a.file_name
