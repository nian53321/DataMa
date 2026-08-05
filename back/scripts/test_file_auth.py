# -*- coding: utf-8 -*-
"""测试 /api/data/assets/<id>/file 和 /play 接口的鉴权与角色控制。
覆盖：
  - 未授权拦截
  - 非 admin 角色调 /file 返回 403
  - admin 角色调 /file 通过
  - 所有登录角色调 /play 通过
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, DataAsset, Role
from flask_jwt_extended import create_access_token

app = create_app()
client = app.test_client()


def login_token(username):
    """通过数据库直接签发 token（避免依赖密码）"""
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            return None, None
        token = create_access_token(
            identity=str(u.id),
            additional_claims={"role": u.role.value, "username": u.username},
        )
        return token, u.role.value


def pick_asset_id():
    with app.app_context():
        a = DataAsset.query.first()
        return a.id if a else None


def main():
    asset_id = pick_asset_id()
    if not asset_id:
        print("SKIP: 数据库中没有数据资产")
        return
    print(f"测试资产 ID: {asset_id}")

    # 找出各角色的用户
    with app.app_context():
        users = {}
        for r in [Role.ADMIN, Role.DOCTOR, Role.ANNOTATOR, Role.NURSE, Role.ENGINEER]:
            u = User.query.filter_by(role=r, is_active=True).first()
            if u:
                users[r.value] = u.username
        print(f"找到的角色用户: {users}")

    if not users:
        print("SKIP: 数据库没有用户")
        return

    print("\n=== 场景 1：未授权访问 /file ===")
    rv = client.get(f"/api/data/assets/{asset_id}/file")
    print(f"HTTP {rv.status_code}")
    assert rv.status_code == 401, f"期望 401，实际 {rv.status_code}"
    print("PASS")

    print("\n=== 场景 2：未授权访问 /play ===")
    rv = client.get(f"/api/data/assets/{asset_id}/play")
    print(f"HTTP {rv.status_code}")
    assert rv.status_code == 401, f"期望 401，实际 {rv.status_code}"
    print("PASS")

    # 测试每个角色调 /file
    print("\n=== 场景 3：各角色调 /file（仅 ADMIN 应通过）===")
    for role, username in users.items():
        token, _ = login_token(username)
        rv = client.get(f"/api/data/assets/{asset_id}/file?access_token={token}")
        if role == "admin":
            expected = (200, 404)  # admin 通过鉴权（资源不存在也算通过）
            label = "通过"
        else:
            expected = (403,)
            label = "拒绝"
        ok = rv.status_code in expected
        print(f"  [{role:10s}] HTTP {rv.status_code} 期望 {expected} → {'PASS' if ok else 'FAIL'} ({label})")
        assert ok, f"{role} 调 /file 期望 {expected}，实际 {rv.status_code}"

    # 测试每个角色调 /play（所有登录用户应通过）
    print("\n=== 场景 4：各角色调 /play（均应通过鉴权）===")
    for role, username in users.items():
        token, _ = login_token(username)
        rv = client.get(f"/api/data/assets/{asset_id}/play?access_token={token}")
        ok = rv.status_code in (200, 404, 500)  # 通过鉴权，资源/转码问题属正常
        print(f"  [{role:10s}] HTTP {rv.status_code} → {'PASS' if ok else 'FAIL'}")
        assert ok, f"{role} 调 /play 失败: {rv.status_code}"

    print("\n=== 场景 5：错误 token ===")
    rv = client.get(f"/api/data/assets/{asset_id}/file?access_token=invalid_xxx")
    print(f"HTTP {rv.status_code}")
    assert rv.status_code in (401, 422), f"期望 401/422，实际 {rv.status_code}"
    print("PASS")

    print("\n========== 全部测试通过 ==========")


if __name__ == "__main__":
    main()
