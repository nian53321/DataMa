# -*- coding: utf-8 -*-
"""数据导出功能测试（异步任务版本）

测试覆盖：
1. 单个/批量/按受试者/按筛选条件导出
2. 加密与明文导出
3. 超限拒绝 / 空选择拒绝
4. 部分文件丢失容错
5. 权限校验（annotator 403）
6. 审计日志记录
7. 下载文件名格式

接口流程（异步）：
  POST /api/data/assets/export/start     → 返回 task_id
  GET  /api/data/assets/export/progress/<task_id>  → 轮询直到 status=success/failed
  GET  /api/data/assets/export/download/<task_id>  → 下载 zip
"""
import io
import os
import json
import time
import zipfile
import tempfile

import pytest


# ==================== 测试辅助 ====================

def _create_subject(admin_client, pseudo_id="SUBJ_001"):
    """创建受试者并返回 id"""
    resp = admin_client.post("/api/data/subjects", json={
        "pseudo_id": pseudo_id, "age": 30, "gender": "男",
    })
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["data"]["id"]


def _upload_asset(admin_client, subject_id, data_type, file_name, content):
    """上传一个数据资产并返回 id"""
    resp = admin_client.post(
        "/api/data/assets/upload",
        data={
            "subject_id": str(subject_id),
            "data_type": data_type,
            "file": (io.BytesIO(content), file_name),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["data"]["id"]


def _wait_export_done(admin_client, task_id, timeout=30):
    """轮询导出任务进度直到完成或超时

    返回: (status, task_data)
        status: "success" / "failed" / "timeout"
        task_data: 最后一次 progress 响应的 data 字段
    """
    deadline = time.time() + timeout
    last_data = {}
    while time.time() < deadline:
        resp = admin_client.get(f"/api/data/assets/export/progress/{task_id}")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["code"] == 200
        last_data = body["data"]
        status = last_data.get("status")
        if status == "success":
            return "success", last_data
        if status == "failed":
            return "failed", last_data
        time.sleep(0.05)
    return "timeout", last_data


def _run_export(admin_client, payload, timeout=30):
    """完整执行一次导出流程：启动 → 轮询 → 下载，返回 (resp, zip_bytes, status_data)

    若启动阶段非 2xx（如 422/403），直接返回 (resp, None, None) 不再轮询。
    """
    resp = admin_client.post("/api/data/assets/export/start", json=payload)
    if resp.status_code not in (200, 201):
        return resp, None, None
    task_id = resp.get_json()["data"]["task_id"]

    status, status_data = _wait_export_done(admin_client, task_id, timeout=timeout)
    if status != "success":
        return None, None, status_data

    dl_resp = admin_client.get(f"/api/data/assets/export/download/{task_id}")
    return dl_resp, dl_resp.data, status_data


# ==================== 测试用例 ====================

class TestExportService:
    """数据导出功能测试（异步任务版本）"""

    def test_export_single_asset_plain(self, db_app, admin_client):
        """单个资产明文导出：zip 内文件无 .dmec，与上传内容字节一致"""
        with db_app.app_context():
            subject_id = _create_subject(admin_client)
            content = b"hello eeg data" * 100
            asset_id = _upload_asset(admin_client, subject_id, "eeg", "test_eeg.csv", content)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "asset_ids": [asset_id], "encrypted": False,
            })
            assert resp is not None
            assert resp.status_code == 200, resp.get_data(as_text=True) if resp else "no response"
            assert resp.content_type == "application/zip"

            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            names = zf.namelist()
            assert "manifest.json" not in names
            data_files = list(names)
            assert len(data_files) == 1
            assert not data_files[0].endswith(".dmec")
            assert zf.read(data_files[0]) == content

    def test_export_batch(self, db_app, admin_client):
        """批量导出（多选）：zip 内含多个文件"""
        with db_app.app_context():
            subject_id = _create_subject(admin_client)
            id1 = _upload_asset(admin_client, subject_id, "eeg", "eeg1.csv", b"eeg1" * 50)
            id2 = _upload_asset(admin_client, subject_id, "ecg", "ecg1.csv", b"ecg1" * 50)
            id3 = _upload_asset(admin_client, subject_id, "scale", "scale1.csv", b"scale1" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "asset_ids": [id1, id2, id3], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 3

    def test_export_by_subject(self, db_app, admin_client):
        """按受试者导出：导出该受试者全部资产"""
        with db_app.app_context():
            sid1 = _create_subject(admin_client, "SUBJ_A")
            sid2 = _create_subject(admin_client, "SUBJ_B")
            _upload_asset(admin_client, sid1, "eeg", "a_eeg.csv", b"a" * 50)
            _upload_asset(admin_client, sid1, "ecg", "a_ecg.csv", b"a" * 50)
            _upload_asset(admin_client, sid2, "eeg", "b_eeg.csv", b"b" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "subject_id": sid1, "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 2
            for name in data_files:
                assert name.startswith("SUBJ_A/")

    def test_export_by_filter(self, db_app, admin_client):
        """按筛选条件导出（data_type + layer）：仅含匹配资产"""
        with db_app.app_context():
            subject_id = _create_subject(admin_client)
            _upload_asset(admin_client, subject_id, "eeg", "eeg.csv", b"eeg" * 50)
            _upload_asset(admin_client, subject_id, "ecg", "ecg.csv", b"ecg" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "subject_id": subject_id, "data_type": "eeg", "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 1
            assert "/eeg/" in data_files[0]

    def test_export_exceeds_count_limit(self, db_app, admin_client):
        """超限拒绝：>200 文件抛 422（在 start 阶段即被拒绝）"""
        with db_app.app_context():
            subject_id = _create_subject(admin_client)
            asset_ids = []
            for i in range(201):
                aid = _upload_asset(
                    admin_client, subject_id, "scale",
                    f"scale_{i}.csv", f"data{i}".encode() * 5,
                )
                asset_ids.append(aid)

            resp = admin_client.post("/api/data/assets/export/start", json={
                "asset_ids": asset_ids, "encrypted": False,
            })
            # 异步任务在后台线程中抛 ValidationError，体现在 status=failed
            body = resp.get_json()
            assert resp.status_code == 200
            task_id = body["data"]["task_id"]
            status, status_data = _wait_export_done(admin_client, task_id, timeout=30)
            assert status == "failed"
            assert "超过上限" in (status_data.get("error") or "")

    def test_export_empty_selection(self, db_app, admin_client):
        """空选择拒绝：无 asset_ids 且无筛选条件抛 422（任务失败）"""
        with db_app.app_context():
            resp = admin_client.post("/api/data/assets/export/start", json={
                "encrypted": False,
            })
            assert resp.status_code == 200
            task_id = resp.get_json()["data"]["task_id"]
            status, status_data = _wait_export_done(admin_client, task_id, timeout=30)
            assert status == "failed"
            assert "未选择" in (status_data.get("error") or "")

    def test_export_missing_file_tolerance(self, db_app, admin_client):
        """部分文件丢失：跳过丢失文件，zip 内仅含可读文件，不抛异常"""
        with db_app.app_context():
            from app.extensions import db as _db
            from app.models import DataAsset
            from app.models.data import DataLayer, DataType

            subject_id = _create_subject(admin_client)
            id1 = _upload_asset(admin_client, subject_id, "eeg", "ok.csv", b"ok" * 50)
            ghost = DataAsset(
                subject_id=subject_id,
                data_type=DataType.EEG,
                layer=DataLayer.RAW,
                file_name="ghost.csv",
                file_path="raw/SUBJ_001/eeg/ghost_not_exist.csv",
                file_format="csv",
                file_size=100,
            )
            _db.session.add(ghost)
            _db.session.commit()
            ghost_id = ghost.id

            resp, zip_bytes, _ = _run_export(admin_client, {
                "asset_ids": [id1, ghost_id], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            assert "manifest.json" not in zf.namelist()
            data_files = zf.namelist()
            assert len(data_files) == 1

    def test_export_permission_denied(self, db_app, annotator_client):
        """权限：annotator 调用 /start 返回 403（role_required 在装饰器层拦截）"""
        with db_app.app_context():
            resp = annotator_client.post("/api/data/assets/export/start", json={
                "asset_ids": [1], "encrypted": False,
            })
            assert resp.status_code == 403

    def test_export_audit_log(self, db_app, admin_client):
        """审计日志：导出后 operation_logs 含 action=export 记录"""
        with db_app.app_context():
            from app.models.operation_log import OperationLog
            from app.extensions import db as _db

            subject_id = _create_subject(admin_client)
            asset_id = _upload_asset(admin_client, subject_id, "eeg", "audit.csv", b"audit" * 50)

            resp, _, _ = _run_export(admin_client, {
                "asset_ids": [asset_id], "encrypted": True,
            })
            assert resp.status_code == 200

            _db.session.expire_all()
            logs = OperationLog.query.filter_by(action="export").all()
            assert len(logs) >= 1
            assert logs[-1].target_type == "data_asset"
            assert "加密" in logs[-1].detail

    def test_export_filename_format(self, db_app, admin_client):
        """下载文件名格式：data_export_{encrypted|plain}_{timestamp}.zip"""
        with db_app.app_context():
            subject_id = _create_subject(admin_client)
            asset_id = _upload_asset(admin_client, subject_id, "eeg", "fn.csv", b"fn" * 50)

            resp, _, _ = _run_export(admin_client, {
                "asset_ids": [asset_id], "encrypted": True,
            })
            assert resp.status_code == 200
            cd = resp.headers.get("Content-Disposition", "")
            assert "data_export_encrypted_" in cd
            assert ".zip" in cd

    # ==================== 多选参数测试 ====================

    def test_export_by_subject_ids_multi(self, db_app, admin_client):
        """多选受试者导出：subject_ids 列表筛选多个受试者"""
        with db_app.app_context():
            sid1 = _create_subject(admin_client, "SUBJ_A")
            sid2 = _create_subject(admin_client, "SUBJ_B")
            sid3 = _create_subject(admin_client, "SUBJ_C")
            _upload_asset(admin_client, sid1, "eeg", "a_eeg.csv", b"a" * 50)
            _upload_asset(admin_client, sid2, "eeg", "b_eeg.csv", b"b" * 50)
            _upload_asset(admin_client, sid3, "eeg", "c_eeg.csv", b"c" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "subject_ids": [sid1, sid2], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 2
            for name in data_files:
                assert name.startswith("SUBJ_A/") or name.startswith("SUBJ_B/")
                assert not name.startswith("SUBJ_C/")

    def test_export_by_data_types_multi(self, db_app, admin_client):
        """多选数据类型导出：data_types 列表筛选多种类型"""
        with db_app.app_context():
            subject_id = _create_subject(admin_client)
            _upload_asset(admin_client, subject_id, "eeg", "eeg.csv", b"eeg" * 50)
            _upload_asset(admin_client, subject_id, "ecg", "ecg.csv", b"ecg" * 50)
            _upload_asset(admin_client, subject_id, "scale", "scale.csv", b"scale" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "data_types": ["eeg", "ecg"], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 2
            for name in data_files:
                assert "/eeg/" in name or "/ecg/" in name
                assert "/scale/" not in name

    def test_export_by_layers_multi(self, db_app, admin_client):
        """多选数据层导出：layers 列表筛选多个层"""
        with db_app.app_context():
            from app.extensions import db as _db
            from app.models import DataAsset
            from app.models.data import DataLayer, DataType

            subject_id = _create_subject(admin_client)
            _upload_asset(admin_client, subject_id, "eeg", "raw.csv", b"raw" * 50)
            feat = DataAsset(
                subject_id=subject_id,
                data_type=DataType.EEG,
                layer=DataLayer.FEATURE,
                file_name="feat.csv",
                file_path=f"raw/SUBJ_001/eeg/feat_not_exist.csv",
                file_format="csv",
                file_size=100,
            )
            _db.session.add(feat)
            _db.session.commit()

            resp, zip_bytes, _ = _run_export(admin_client, {
                "layers": ["feature"], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            # feature 文件丢失会跳过，但 raw 不应被包含
            for name in data_files:
                assert "/feature/" in name
                assert "/raw/" not in name

    def test_export_empty_array_means_all(self, db_app, admin_client):
        """空数组 = 全选：不传筛选维度等于导出全部"""
        with db_app.app_context():
            sid1 = _create_subject(admin_client, "SUBJ_A")
            sid2 = _create_subject(admin_client, "SUBJ_B")
            _upload_asset(admin_client, sid1, "eeg", "a.csv", b"a" * 50)
            _upload_asset(admin_client, sid2, "ecg", "b.csv", b"b" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "subject_ids": [], "data_types": [], "layers": [], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 2

    def test_export_single_and_plural_merge(self, db_app, admin_client):
        """单值与复数参数混合使用：自动合并去重"""
        with db_app.app_context():
            sid1 = _create_subject(admin_client, "SUBJ_A")
            sid2 = _create_subject(admin_client, "SUBJ_B")
            sid3 = _create_subject(admin_client, "SUBJ_C")
            _upload_asset(admin_client, sid1, "eeg", "a.csv", b"a" * 50)
            _upload_asset(admin_client, sid2, "eeg", "b.csv", b"b" * 50)
            _upload_asset(admin_client, sid3, "eeg", "c.csv", b"c" * 50)

            resp, zip_bytes, _ = _run_export(admin_client, {
                "subject_id": sid2, "subject_ids": [sid2, sid3], "encrypted": False,
            })
            assert resp.status_code == 200
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            data_files = zf.namelist()
            assert len(data_files) == 2
            for name in data_files:
                assert name.startswith("SUBJ_B/") or name.startswith("SUBJ_C/")
                assert not name.startswith("SUBJ_A/")
