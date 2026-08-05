# -*- coding: utf-8 -*-
"""登录失败次数限制（内存版，单 worker 部署适用）

约束：项目无 Redis 依赖，故采用进程内字典 + threading.Lock 实现。
gunicorn 多 worker 模式下各 worker 计数独立，安全性略降（5*N 次才能完全锁定），
建议生产环境单 worker (-w 1) 部署，或后续接入 Redis。
"""
import threading
import time

_LOCK = threading.Lock()
# key=(username_lower, ip) -> {"fails": int, "locked_until": float}
_store = {}

# 默认参数（可通过 config.py 的 LOGIN_MAX_FAILS / LOGIN_LOCK_SECONDS 覆盖）
MAX_FAILS = 5            # 5 次失败后锁定
LOCK_SECONDS = 15 * 60   # 锁定 15 分钟


def _now():
    return time.monotonic()


def _key(username, ip):
    return ((username or "").strip().lower(), ip or "")


def is_locked(username, ip):
    """是否处于锁定状态。返回 (locked, remain_seconds)"""
    k = _key(username, ip)
    with _LOCK:
        rec = _store.get(k)
        if not rec:
            return False, 0
        if rec["locked_until"] > _now():
            return True, int(rec["locked_until"] - _now())
        # 锁已过期，清掉
        if rec["locked_until"] > 0:
            _store.pop(k, None)
        return False, 0


def record_failure(username, ip, max_fails=None, lock_seconds=None):
    """记录一次失败，返回 (是否刚刚触发锁定, 当前失败次数)"""
    mf = max_fails if max_fails is not None else MAX_FAILS
    ls = lock_seconds if lock_seconds is not None else LOCK_SECONDS
    k = _key(username, ip)
    with _LOCK:
        rec = _store.get(k)
        now = _now()
        if not rec:
            rec = {"fails": 0, "locked_until": 0}
        elif rec["locked_until"] > 0 and rec["locked_until"] <= now:
            # 锁已过期，重置计数（locked_until==0 表示从未锁定，不重置）
            rec = {"fails": 0, "locked_until": 0}
        rec["fails"] += 1
        triggered = False
        if rec["fails"] >= mf:
            rec["locked_until"] = now + ls
            triggered = True
        _store[k] = rec
        return triggered, rec["fails"]


def reset(username, ip):
    """登录成功时清除计数"""
    k = _key(username, ip)
    with _LOCK:
        _store.pop(k, None)