"""媒体资源短期签名 URL 签发接口

前端通过标准 Authorization JWT 调用本接口获取"资源绑定 + 5 分钟过期"的
签名 URL，用于 <video>/<audio>/<img> 标签加载，避免长期 JWT 进入 URL。
"""
from flask import request

from flask_jwt_extended import jwt_required, get_jwt

from app.api import media_bp
from app.models import Role
from app.utils.media_auth import media_sign
from app.utils.response import fail, success

# kind -> 生成签名 URL 的模板；{asset_id} / {path} / {task_id} 由 payload 填充
_URL_TEMPLATES = {
    "asset_play": "/api/data/assets/{asset_id}/play",
    "asset_file": "/api/data/assets/{asset_id}/file",
    "asset_export": "/api/data/assets/export/download/{task_id}",
    "depth_video": "/api/visualization/depth-video/{asset_id}",
    "orbbec_preview": "/api/orbbec/preview?path={path}",
    "orbbec_stream": "/api/orbbec/preview/stream",
    "realsense_preview": "/api/realsense/preview?path={path}",
    "realsense_stream": "/api/realsense/preview/stream",
}

# 仅管理员可访问的资源类型（原始文件下载 / 导出 zip 下载）
_ADMIN_ONLY_KINDS = {"asset_file", "asset_export"}


@media_bp.route("/signed-url", methods=["GET"])
@jwt_required()
def signed_url():
    """签发媒体资源短期签名 URL

    请求参数：
      kind      资源类型（asset_play / asset_file / asset_export / depth_video /
                orbbec_preview / orbbec_stream / realsense_preview / realsense_stream）
      asset_id  资源资产 ID（asset_play / asset_file / depth_video 必填）
      path      相对路径（orbbec_preview / realsense_preview 必填）
      task_id   导出任务 ID（asset_export 必填）
    返回：{url: "<带 media_token 的完整 URL>"}
    """
    kind = request.args.get("kind")
    template = _URL_TEMPLATES.get(kind)
    if not template:
        return fail("未知的媒体资源类型", 422)

    asset_id = request.args.get("asset_id", type=int)
    path = request.args.get("path")
    task_id = request.args.get("task_id")

    if kind == "asset_export":
        if not task_id:
            return fail("task_id 必填", 422)
        payload = {"task_id": task_id}
    elif kind in ("asset_play", "asset_file", "depth_video"):
        if not asset_id:
            return fail("asset_id 必填", 422)
        payload = {"asset_id": asset_id}
    elif kind in ("orbbec_preview", "realsense_preview"):
        if not path:
            return fail("path 必填", 422)
        payload = {"path": path}
    else:
        payload = {}

    # 仅 ADMIN 可签发敏感资源（原始文件 / 导出包）签名，且签名携带签发角色
    if kind in _ADMIN_ONLY_KINDS:
        role = get_jwt().get("role")
        if role != Role.ADMIN.value:
            return fail("无权限", 403)
        payload["roles"] = [role]

    sig = media_sign(kind, payload)
    url = template.format(asset_id=asset_id, path=path, task_id=task_id)
    sep = "&" if "?" in url else "?"
    return success({"url": f"{url}{sep}media_token={sig}"})
