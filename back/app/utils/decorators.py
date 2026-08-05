# -*- coding: utf-8 -*-
"""JWT 回调与装饰器"""
import logging
import time
from functools import wraps
from flask import request
from flask_jwt_extended import get_jwt, verify_jwt_in_request
from sqlalchemy.exc import OperationalError

from app.utils.response import fail

logger = logging.getLogger(__name__)


def retry_on_deadlock(max_retries=2, delay=0.3):
    """MySQL 死锁/锁超时/连接异常自动重试装饰器

    当出现以下情况时，自动回滚当前 session 并延迟重试，避免前端收到 500 错误：
    - 1213 Deadlock
    - 1205 Lock wait timeout
    - 2006 MySQL server has gone away（连接被服务端关闭）
    - 2013 Lost connection during query（连接丢失）
    - 2014 Command Out of Sync（连接池状态错乱）

    适用于 GET 读取接口（stats、list 等），也适用于写入接口。
    """
    # 可重试的 MySQL errno：死锁、锁超时、连接异常
    _RETRYABLE_ERRNO = (1213, 1205, 2006, 2013, 2014)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except OperationalError as e:
                    orig = getattr(e, "orig", None)
                    errno = getattr(orig, "args", [None])[0] if orig else None
                    if errno in _RETRYABLE_ERRNO and attempt < max_retries:
                        logger.warning(
                            "DB 异常 (errno=%s)，第 %d 次重试 %s",
                            errno, attempt + 1, fn.__name__
                        )
                        try:
                            from app.extensions import db
                            db.session.rollback()
                            # 连接异常时 dispose 旧连接，强制下条拿新连接
                            if errno in (2006, 2013, 2014):
                                db.engine.dispose()
                        except Exception:
                            pass
                        time.sleep(delay)
                        last_exc = e
                        continue
                    last_exc = e
                    break
                except Exception:
                    raise
            if last_exc:
                raise last_exc
        return wrapper
    return decorator


def role_required(*roles):
    """角色校验装饰器：要求当前用户至少拥有指定角色之一"""
    # 统一转为字符串值进行比较（JWT claims 中存的是字符串）
    allowed = [r.value if hasattr(r, "value") else r for r in roles]

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            user_role = claims.get("role")
            if user_role not in allowed:
                return fail("无权限访问该资源", 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
