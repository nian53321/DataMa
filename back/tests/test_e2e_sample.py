# -*- coding: utf-8 -*-
"""端到端全功能测试：使用样例文件夹 17842827151830001 验证完整数据流程

测试覆盖：
1. 外部密钥导入（密钥.txt → 数据库 ExternalKey）
2. parse-userinfo API（解密 userInfo.json.enc → 字段映射）
3. 创建受试者 + 上传 .enc 文件（外部解密 → 入库，文件名去 .enc）
4. 数据资产列表（验证文件名不含 .enc、类型正确）
5. 文件下载（解密 → 还原原始内容）
6. 自动扫描配置（扫描样例目录 → 自动创建受试者 + 导入文件）
7. 数据统计 API + 操作日志 API
"""
import os
import io
import base64

import pytest


SAMPLE_DIR = r"d:\Py_Project\DataManagement\样例\17842827151830001"
KEY_FILE = os.path.join(SAMPLE_DIR, "密钥.txt")


def _read_key_file():
    """读取密钥.txt，返回 (key_b64, iv_b64)"""
    with open(KEY_FILE, "r", encoding="utf-8") as f:
        lines = f.read().strip().splitlines()
    key_b64 = iv_b64 = ""
    for line in lines:
        line = line.strip()
        if line.upper().startswith("CRYPTO_AES_KEY="):
            key_b64 = line.split("=", 1)[1].strip()
        elif line.upper().startswith("CRYPTO_AES_IV="):
            iv_b64 = line.split("=", 1)[1].strip()
    return key_b64, iv_b64


def _import_sample_key(db_app):
    """导入样例密钥到数据库"""
    from app.services.external_key_service import ExternalKeyService
    key_b64, iv_b64 = _read_key_file()
    with db_app.app_context():
        svc = ExternalKeyService()
        existing = svc.list_keys()
        if not any(k["name"] == "样例密钥" for k in existing):
            svc.create_key({
                "name": "样例密钥",
                "description": "17842827151830001 文件夹外部密钥",
                "key_b64": key_b64,
                "iv_b64": iv_b64,
                "is_active": True,
            })


# ==================== 1. 外部密钥导入 ====================

class TestExternalKeyImport:
    """外部密钥导入"""

    def test_import_key_from_keyfile(self, db_app):
        """从密钥.txt 导入外部密钥到数据库"""
        from app.services.external_key_service import ExternalKeyService
        key_b64, iv_b64 = _read_key_file()
        assert key_b64 and iv_b64, "密钥文件解析失败"

        with db_app.app_context():
            svc = ExternalKeyService()
            ek = svc.create_key({
                "name": "样例密钥",
                "key_b64": key_b64,
                "iv_b64": iv_b64,
                "is_active": True,
            })
            assert ek.id is not None
            assert ek.is_active is True
            keys = svc.get_active_keys_for_decrypt()
            assert len(keys) >= 1


# ==================== 2. parse-userinfo API ====================

class TestParseUserInfo:
    """parse-userinfo API"""

    def test_parse_encrypted_userinfo(self, db_app, admin_client):
        """测试解析加密的 userInfo.json.enc"""
        _import_sample_key(db_app)
        userinfo_path = os.path.join(SAMPLE_DIR, "userInfo.json.enc")
        with open(userinfo_path, "rb") as f:
            resp = admin_client.post(
                "/api/data/parse-userinfo",
                data={"file": (io.BytesIO(f.read()), "userInfo.json.enc")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        fields = data["fields"]
        # 验证字段映射
        assert fields["pseudo_id"] == "17842827151830001"
        assert fields["age"] == 22
        assert fields["gender"] == "男"  # sex=0 → 男
        assert fields["phone"] == "18827374828"
        assert fields["mmse_score"] == 1.0
        assert fields["moca_score"] == 2.0
        assert fields["collection_scene"] == "1"
        assert "collection_time" in fields  # 毫秒时间戳解析成功

    def test_parse_without_key_fails(self, db_app, admin_client):
        """无外部密钥时解析失败"""
        from app.models.external_key import ExternalKey
        from app.extensions import db
        with db_app.app_context():
            ExternalKey.query.delete()
            db.session.commit()

        userinfo_path = os.path.join(SAMPLE_DIR, "userInfo.json.enc")
        with open(userinfo_path, "rb") as f:
            resp = admin_client.post(
                "/api/data/parse-userinfo",
                data={"file": (io.BytesIO(f.read()), "userInfo.json.enc")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 422
        assert "外部密钥" in resp.get_json()["message"]


# ==================== 3. 上传 .enc 文件 ====================

class TestUploadEncFile:
    """创建受试者 + 上传 .enc 文件"""

    def test_upload_wav_enc_strips_suffix(self, db_app, admin_client):
        """上传 .enc 文件后，文件名不含 .enc"""
        _import_sample_key(db_app)
        # 创建受试者
        resp = admin_client.post("/api/data/subjects", json={
            "pseudo_id": "17842827151830001", "age": 22, "gender": "男",
        })
        assert resp.status_code == 201
        subject_id = resp.get_json()["data"]["id"]

        # 上传 recording_20260717184315.wav.enc
        wav_enc_path = os.path.join(SAMPLE_DIR, "recording_20260717184315.wav.enc")
        with open(wav_enc_path, "rb") as f:
            resp = admin_client.post(
                "/api/data/assets/upload",
                data={
                    "subject_id": str(subject_id),
                    "data_type": "audio",
                    "file": (io.BytesIO(f.read()), "recording_20260717184315.wav.enc"),
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 201, f"上传失败: {resp.get_json()}"
        asset = resp.get_json()["data"]
        assert not asset["file_name"].endswith(".enc"), f"文件名仍含 .enc: {asset['file_name']}"
        assert asset["file_name"].endswith(".wav"), f"文件名后缀非 wav: {asset['file_name']}"
        assert asset["file_format"] == "wav"

    def test_upload_all_enc_files(self, db_app, admin_client):
        """上传全部 .enc 文件（eeg/ecg/audio）"""
        _import_sample_key(db_app)
        resp = admin_client.post("/api/data/subjects", json={
            "pseudo_id": "17842827151830001",
        })
        subject_id = resp.get_json()["data"]["id"]

        files_to_upload = [
            ("eeg_20260717184835.csv.enc", "eeg"),
            ("ecg_20260717184835.csv.enc", "ecg"),
            ("recording_20260717184315.wav.enc", "audio"),
        ]
        for fname, dtype in files_to_upload:
            fpath = os.path.join(SAMPLE_DIR, fname)
            with open(fpath, "rb") as f:
                resp = admin_client.post(
                    "/api/data/assets/upload",
                    data={
                        "subject_id": str(subject_id),
                        "data_type": dtype,
                        "file": (io.BytesIO(f.read()), fname),
                    },
                    content_type="multipart/form-data",
                )
            assert resp.status_code == 201, f"上传 {fname} 失败: {resp.get_json()}"
            asset = resp.get_json()["data"]
            assert not asset["file_name"].endswith(".enc"), f"{fname} 文件名仍含 .enc"


# ==================== 4-5. 资产列表 + 文件下载 ====================

class TestAssetListAndDownload:
    """数据资产列表 + 文件下载"""

    def test_asset_list_and_download(self, db_app, admin_client):
        """验证资产列表 + 下载文件能还原原始内容"""
        from app.utils.crypto import decrypt_external_bytes
        _import_sample_key(db_app)
        key_b64, iv_b64 = _read_key_file()
        key_bytes = base64.b64decode(key_b64)
        iv_bytes = base64.b64decode(iv_b64)

        # 创建受试者 + 上传 wav.enc
        resp = admin_client.post("/api/data/subjects", json={
            "pseudo_id": "17842827151830001",
        })
        subject_id = resp.get_json()["data"]["id"]

        wav_enc_path = os.path.join(SAMPLE_DIR, "recording_20260717184315.wav.enc")
        with open(wav_enc_path, "rb") as f:
            enc_data = f.read()
        original_plain = decrypt_external_bytes(enc_data, key_bytes, iv_bytes)

        with open(wav_enc_path, "rb") as f:
            resp = admin_client.post(
                "/api/data/assets/upload",
                data={
                    "subject_id": str(subject_id), "data_type": "audio",
                    "file": (io.BytesIO(f.read()), "recording_20260717184315.wav.enc"),
                },
                content_type="multipart/form-data",
            )
        asset_id = resp.get_json()["data"]["id"]

        # 查询资产列表
        resp = admin_client.get(f"/api/data/assets?subject_id={subject_id}")
        assert resp.status_code == 200
        assets = resp.get_json()["data"]["items"]
        assert len(assets) >= 1
        assert all(not a["file_name"].endswith(".enc") for a in assets)

        # 下载文件（应与原始明文一致）
        resp = admin_client.get(f"/api/data/assets/{asset_id}/file")
        assert resp.status_code == 200
        assert resp.data == original_plain, "下载内容与原始明文不一致"


# ==================== 6. 自动扫描配置 ====================

class TestScanConfig:
    """自动扫描配置"""

    def test_scan_sample_dir(self, db_app, admin_client):
        """扫描样例目录 → 自动创建受试者 + 导入文件 + 解析 userInfo"""
        _import_sample_key(db_app)
        scan_dir = os.path.dirname(SAMPLE_DIR)  # 父目录（子目录成为受试者）

        # 创建扫描配置
        resp = admin_client.post("/api/system/scan-configs", json={
            "name": "样例扫描",
            "watch_dir": scan_dir,
            "scan_interval": 60,
            "is_active": True,
            "auto_upload_files": True,
        })
        assert resp.status_code in (200, 201), f"创建扫描配置失败: {resp.get_json()}"
        config_id = resp.get_json()["data"]["id"]

        # 触发扫描
        resp = admin_client.post(f"/api/system/scan-configs/{config_id}/run")
        assert resp.status_code == 200, f"扫描失败: {resp.get_json()}"
        result = resp.get_json()["data"]
        assert result["new_count"] >= 1, f"未新增受试者: {result}"

        # 验证受试者字段
        from app.models.subject import Subject
        from app.models.data import DataAsset
        with db_app.app_context():
            subj = Subject.query.filter_by(pseudo_id="17842827151830001").first()
            assert subj is not None, "受试者未创建"
            assert subj.age == 22, f"age 未填充: {subj.age}"
            assert subj.gender == "男", f"gender 未填充: {subj.gender}"
            assert subj.phone == "18827374828", f"phone 未填充: {subj.phone}"
            assert subj.mmse_score == 1.0
            assert subj.moca_score == 2.0
            assert subj.collection_time is not None, "collection_time 未填充"
            # 验证资产
            assets = DataAsset.query.filter_by(subject_id=subj.id).all()
            assert len(assets) >= 3, f"资产数不足: {len(assets)}"
            for a in assets:
                assert not a.file_name.endswith(".enc"), f"文件名仍含 .enc: {a.file_name}"


# ==================== 7. 统计 + 日志 ====================

class TestStatsAndLogs:
    """数据统计 + 操作日志"""

    def test_data_stats(self, db_app, admin_client):
        """测试数据统计 API"""
        resp = admin_client.get("/api/data/stats?days=7")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "subject_total" in data
        assert "asset_total" in data
        assert "last_n_days" in data
        assert "type_distribution" in data

    def test_operation_log_stats(self, db_app, admin_client):
        """测试操作日志统计 API"""
        resp = admin_client.get("/api/data/operation-logs/stats?days=7")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "total" in data
        assert "action_distribution" in data

    def test_operation_log_list(self, db_app, admin_client):
        """测试操作日志列表 API"""
        resp = admin_client.get("/api/data/operation-logs?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert "items" in data
        assert "total" in data
