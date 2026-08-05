# -*- coding: utf-8 -*-
"""时间处理工具：统一将 UTC 时间转换为北京时间字符串"""
from datetime import datetime, timedelta

# 北京时间时区 UTC+8
_CN_TZ = timedelta(hours=8)


def to_local_str(dt, fmt="%Y-%m-%d %H:%M:%S"):
    """将 UTC datetime 转换为北京时间字符串
    Args:
        dt: datetime 对象或 None
        fmt: 时间格式，默认 "%Y-%m-%d %H:%M:%S"
    Returns:
        北京时间字符串，如 "2026-07-04 15:30:00"；dt 为 None 时返回 None
    """
    if not dt:
        return None
    return (dt + _CN_TZ).strftime(fmt)
