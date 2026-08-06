"""媒体资源短期签名 URL 认证

浏览器 <video>/<audio>/<img> 标签无法携带 Authorization 头，原先依赖
?access_token=<长期JWT> 进行 query 认证，JWT 会泄漏到浏览器历史、代理日志与
Referer 头。本模块提供资源绑定的短期签名（默认 5 分钟过期）：

- media_sign(kind, payload)    签发签名（URLSafeTimedSerializer，复用 JWT_SECRET_KEY）
- media_verify(sig, kind)      校验并返回 payload
- media_auth_required(kind)    端点装饰器：优先校验 ?media_token= 短期签名，
                               否则回退标准 JWT（header 或 access_token query，向后兼容）
"""
from functools import wraps

from flask import current_app, request
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from app.utils.response import fail

MEDIA_TOKEN_NAME = "media_token"
MEDIA_MAX_AGE = 300  # 5 分钟


def _serializer():
    secret = current_app.config.get("JWT_SECRET_KEY") or "change-me-in-production"
    return URLSafeTimedSerializer(secret, salt="media-signed-url")


def media_sign(kind, payload, max_age=MEDIA_MAX_AGE):
    """签发资源绑定短期签名

    :param kind: 资源类型标识（如 asset_play / orbbec_stream）
    :param payload: 资源标识（如 {"asset_id": 1} 或 {"path": "..."}）
    :returns: URL 安全签名字符串
    """
    return _serializer().dumps({"k": kind, "d": payload})


def media_verify(sig, kind, max_age=MEDIA_MAX_AGE):
    """校验签名并返回 payload；无效或过期返回 None"""
    try:
        data = _serializer().loads(sig, max_age=max_age)
    except (SignatureExpired, BadData, Exception):
        return None
    if not isinstance(data, dict) or data.get("k") != kind:
        return None
    return data.get("d")


def media_auth_required(kind, resource_key=None, roles=None):
    """媒体端点认证装饰器

    :param kind: 资源类型标识
    :param resource_key: 可选；请求参数（query 或 path kwargs）中用于
                         校验签名绑定的资源标识字段（如 asset_id / path）。
                         绑定校验失败返回 403，防止签名 URL 被套用于其他资源。
    :param roles: 可选；允许访问的角色集合（如 (Role.ADMIN,)），
                  元素为枚举或字符串值。签名路径校验 payload["roles"] 交集，
                  JWT 回退路径校验 get_jwt()["role"]。
    """
    allowed = None
    if roles:
        allowed = [r.value if hasattr(r, "value") else r for r in roles]

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            sig = request.args.get(MEDIA_TOKEN_NAME)
            if sig:
                payload = media_verify(sig, kind)
                if payload is None:
                    return fail("签名无效或已过期", 401)
                if allowed:
                    sig_roles = payload.get("roles") if isinstance(payload, dict) else None
                    if not sig_roles or not (set(allowed) & set(sig_roles)):
                        return fail("无权限", 403)
                if resource_key:
                    request_val = request.args.get(resource_key)
                    if request_val is None:
                        request_val = kwargs.get(resource_key)
                    payload_val = payload.get(resource_key) if isinstance(payload, dict) else None
                    if request_val is None or str(request_val) != str(payload_val):
                        return fail("签名与资源不匹配", 403)
                return fn(*args, **kwargs)
            # 回退标准 JWT（header Authorization 或 ?access_token=），向后兼容
            try:
                from flask_jwt_extended import verify_jwt_in_request, get_jwt
                verify_jwt_in_request()
                if allowed and get_jwt().get("role") not in allowed:
                    return fail("无权限", 403)
            except Exception:
                return fail("未授权", 401)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
