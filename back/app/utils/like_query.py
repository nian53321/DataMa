# -*- coding: utf-8 -*-
"""SQL LIKE 通配符转义工具

SQLAlchemy 的 .like() 不会自动转义 % 与 _，恶意用户可通过这两个字符
做模式探测。本工具统一转义并配合 escape 子句使用。

使用 | 作为转义字符，避免反斜杠在不同数据库（MySQL NO_BACKSLASH_ESCAPES 模式）
下的歧义问题。

使用示例：
    from app.utils.like_query import build_like_contains
    kw = build_like_contains(keyword)
    query = query.filter(User.username.like(kw, escape="|"))
"""

# 转义字符：使用 | 避免反斜杠歧义
_ESCAPE_CHAR = "|"


def escape_like(value):
    """转义 LIKE 中的特殊字符 % 与 _

    返回的字符串已用 | 转义，配合 .escape("|") 使用
    """
    if value is None:
        return ""
    s = str(value)
    # 注意：转义符本身必须先转义，否则会把后续的 % 或 _ 的转义符再次转义
    return (s.replace(_ESCAPE_CHAR, _ESCAPE_CHAR + _ESCAPE_CHAR)
             .replace("%", _ESCAPE_CHAR + "%")
             .replace("_", _ESCAPE_CHAR + "_"))


def build_like_contains(value):
    """构造 %value% 模糊匹配（已转义内部通配符）

    使用示例：
        kw = build_like_contains(keyword)
        query = query.filter(User.username.like(kw, escape="|"))
    """
    return f"%{escape_like(value)}%"