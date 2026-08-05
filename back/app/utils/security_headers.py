# -*- coding: utf-8 -*-
"""HTTP 安全响应头工具

约束：前端可能部署在任意域名/IP，故 CSP 暂不启用（避免破坏前端加载）；
仅设置不会影响功能的通用安全头。
"""


def apply_security_headers(response):
    """after_request 钩子：附加安全响应头"""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # HSTS 仅在 HTTPS 下有效，开发环境 HTTP 不会生效，安全
    response.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=31536000; includeSubDomains",
    )
    # 缓存控制：API JSON 响应默认不缓存（避免敏感数据被中间代理缓存）
    if response.status_code == 200 and response.mimetype == "application/json":
        response.headers.setdefault("Cache-Control", "no-store")
    return response