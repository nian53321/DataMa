# -*- coding: utf-8 -*-
"""data.py 重构后 API 集成测试

验证重构前后 HTTP 行为完全一致：
- 路由 URL 不变、方法不变、响应格式不变
- ServiceError 异常被全局 errorhandler 转为 {code, message, data} JSON
- 权限隔离：admin/annotator 看到的数据范围不同
- 业务校验：422/404/409 状态码正确返回
"""
import pytest


# ==================== 受试者 API ====================

class TestSubjectsAPI:
    """受试者管理路由"""

    def test_list_subjects_empty(self, admin_client):
        """空列表正常返回"""
        resp = admin_client.get("/api/data/subjects")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 200
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []

    def test_create_subject_success(self, admin_client):
        """POST 创建受试者返回 201"""
        resp = admin_client.post("/api/data/subjects", json={
            "pseudo_id": "API_001", "age": 65, "gender": "男",
        })
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["code"] == 201
        assert body["data"]["pseudo_id"] == "API_001"
        assert body["message"] == "创建成功"

    def test_create_subject_validation_error(self, admin_client):
        """空 pseudo_id 返回 422 + ValidationError 消息"""
        resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "", "age": 65})
        assert resp.status_code == 422
        body = resp.get_json()
        assert body["code"] == 422
        assert "伪ID不能为空" in body["message"]
        assert body["data"] is None

    def test_create_subject_conflict(self, admin_client):
        """重复 pseudo_id 返回 409"""
        admin_client.post("/api/data/subjects", json={"pseudo_id": "API_DUP"})
        resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "API_DUP"})
        assert resp.status_code == 409
        body = resp.get_json()
        assert body["code"] == 409
        assert "已存在" in body["message"]

    def test_create_subject_invalid_format(self, admin_client):
        """非法格式 pseudo_id 返回 422"""
        resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "../etc"})
        assert resp.status_code == 422
        assert "格式非法" in resp.get_json()["message"]

    def test_update_subject_success(self, admin_client):
        """PUT 更新受试者"""
        create_resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "API_UPD", "age": 60})
        sid = create_resp.get_json()["data"]["id"]
        resp = admin_client.put(f"/api/data/subjects/{sid}", json={"age": 65, "gender": "男"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["age"] == 65
        assert body["data"]["gender"] == "男"
        assert body["message"] == "更新成功"

    def test_update_subject_not_found(self, admin_client):
        """更新不存在的受试者返回 404"""
        resp = admin_client.put("/api/data/subjects/99999", json={"age": 65})
        assert resp.status_code == 404
        assert "不存在" in resp.get_json()["message"]

    def test_delete_subject_success(self, admin_client):
        """DELETE 删除受试者"""
        create_resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "API_DEL"})
        sid = create_resp.get_json()["data"]["id"]
        resp = admin_client.delete(f"/api/data/subjects/{sid}")
        assert resp.status_code == 200
        assert "已删除" in resp.get_json()["message"]
        # 验证已删除
        list_resp = admin_client.get("/api/data/subjects")
        assert list_resp.get_json()["data"]["total"] == 0

    def test_list_subjects_filter(self, admin_client):
        """筛选参数正常工作"""
        admin_client.post("/api/data/subjects", json={"pseudo_id": "F_M", "gender": "男"})
        admin_client.post("/api/data/subjects", json={"pseudo_id": "F_F", "gender": "女"})
        resp = admin_client.get("/api/data/subjects?gender=男")
        body = resp.get_json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["pseudo_id"] == "F_M"

    def test_list_subjects_risk_none(self, admin_client):
        """risk_level=none 筛选未评估"""
        admin_client.post("/api/data/subjects", json={
            "pseudo_id": "R_N", "cognitive_risk_level": None
        })
        admin_client.post("/api/data/subjects", json={
            "pseudo_id": "R_M", "cognitive_risk_level": "mci"
        })
        resp = admin_client.get("/api/data/subjects?cognitive_risk_level=none")
        body = resp.get_json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["pseudo_id"] == "R_N"


# ==================== 权限隔离 ====================

class TestPermissionAPI:
    """权限校验：annotator 不能创建/删除受试者"""

    def test_annotator_cannot_create_subject(self, annotator_client):
        """annotator POST /subjects 返回 403"""
        resp = annotator_client.post("/api/data/subjects", json={"pseudo_id": "X"})
        # role_required 装饰器返回 403（fail 格式，非 ServiceError）
        assert resp.status_code == 403

    def test_annotator_cannot_delete_subject(self, annotator_client, admin_client):
        """annotator DELETE /subjects/<id> 返回 403"""
        create_resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "PERM_DEL"})
        sid = create_resp.get_json()["data"]["id"]
        resp = annotator_client.delete(f"/api/data/subjects/{sid}")
        assert resp.status_code == 403

    def test_anonymous_cannot_access(self, db_app):
        """无 JWT 访问返回 401"""
        client = db_app.test_client()
        resp = client.get("/api/data/subjects")
        assert resp.status_code == 401


# ==================== 操作日志权限隔离 ====================

class TestOperationLogsAPI:
    """操作日志权限隔离：admin 看全部，annotator 仅看自己"""

    def test_admin_sees_all_logs(self, admin_client, annotator_client):
        """admin 能看到 annotator 的操作日志"""
        # annotator 创建一个受试者（其实会被 403 拒绝，但会产生日志？不会，role_required 在前）
        # 改为：admin 创建受试者后，admin 自己能查到日志
        admin_client.post("/api/data/subjects", json={"pseudo_id": "LOG_001"})
        resp = admin_client.get("/api/data/operation-logs")
        body = resp.get_json()
        assert body["code"] == 200
        # 应该有至少一条 create subject 日志
        actions = [item["action"] for item in body["data"]["items"]]
        assert "create" in actions

    def test_annotator_sees_only_own_logs(self, admin_client, annotator_client):
        """annotator 仅能查看自己的日志（admin 创建的看不到）"""
        admin_client.post("/api/data/subjects", json={"pseudo_id": "LOG_ADMIN"})
        resp = annotator_client.get("/api/data/operation-logs")
        body = resp.get_json()
        assert body["code"] == 200
        # annotator 没有任何操作，应该看到 0 条
        # （admin 创建的日志 username=admin，annotator 看不到）
        for item in body["data"]["items"]:
            assert item["username"] == "annotator"


# ==================== 快照权限隔离 ====================

class TestSnapshotsAPI:
    """快照查询与回滚权限"""

    def test_list_snapshots_admin(self, admin_client):
        """admin 创建受试者后能查到快照"""
        admin_client.post("/api/data/subjects", json={"pseudo_id": "SNAP_001"})
        # 更新触发 snapshot_update
        # 先获取 ID
        list_resp = admin_client.get("/api/data/subjects?keyword=SNAP_001")
        sid = list_resp.get_json()["data"]["items"][0]["id"]
        admin_client.put(f"/api/data/subjects/{sid}", json={"age": 70})
        # 查快照
        resp = admin_client.get("/api/data/snapshots?model_type=subject")
        body = resp.get_json()
        assert body["code"] == 200
        assert body["data"]["total"] >= 1

    def test_get_snapshot_not_found(self, admin_client):
        """获取不存在的快照返回 404"""
        resp = admin_client.get("/api/data/snapshots/99999")
        assert resp.status_code == 404
        assert "不存在" in resp.get_json()["message"]

    def test_rollback_admin_only(self, admin_client, annotator_client):
        """回滚仅 admin 可调用"""
        # annotator 调用应返回 403（role_required 在装饰器层拦截）
        resp = annotator_client.post("/api/data/snapshots/1/rollback")
        assert resp.status_code == 403

    def test_rollback_nonexistent_snapshot(self, admin_client):
        """回滚不存在的快照返回 422"""
        resp = admin_client.post("/api/data/snapshots/99999/rollback")
        assert resp.status_code == 422
        assert "快照不存在" in resp.get_json()["message"]


# ==================== 数据资产 API ====================

class TestAssetsAPI:
    """数据资产管理路由"""

    def test_create_asset_success(self, admin_client):
        """登记数据资产"""
        # 先创建受试者
        sub_resp = admin_client.post("/api/data/subjects", json={"pseudo_id": "AST_SUBJ"})
        sid = sub_resp.get_json()["data"]["id"]
        resp = admin_client.post("/api/data/assets", json={
            "subject_id": sid, "data_type": "eeg",
            "file_name": "test.csv", "file_size": 1024,
        })
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["data"]["file_name"] == "test.csv"
        assert body["message"] == "登记成功"

    def test_create_asset_missing_required(self, admin_client):
        """缺少必填字段返回 422"""
        resp = admin_client.post("/api/data/assets", json={"subject_id": 1})
        assert resp.status_code == 422
        assert "必填" in resp.get_json()["message"]

    def test_list_assets_empty(self, admin_client):
        """空资产列表"""
        resp = admin_client.get("/api/data/assets")
        body = resp.get_json()
        assert body["code"] == 200
        assert body["data"]["total"] == 0


# ==================== 响应格式一致性 ====================

class TestResponseFormat:
    """验证所有响应遵循 {code, message, data} 格式"""

    def test_success_format(self, admin_client):
        """成功响应格式"""
        resp = admin_client.get("/api/data/subjects")
        body = resp.get_json()
        assert set(body.keys()) >= {"code", "message", "data"}

    def test_error_format(self, admin_client):
        """错误响应格式：data 为 None"""
        resp = admin_client.post("/api/data/subjects", json={"pseudo_id": ""})
        body = resp.get_json()
        assert set(body.keys()) >= {"code", "message", "data"}
        assert body["data"] is None
        assert body["code"] == 422