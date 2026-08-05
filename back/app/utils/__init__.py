# -*- coding: utf-8 -*-
"""工具模块"""
from app.utils.response import success, fail, paginate
from app.utils.decorators import role_required

__all__ = ["success", "fail", "paginate", "role_required"]
