# -*- coding: utf-8 -*-
"""异步任务状态的**跨进程**存储（Redis 优先，进程内存降级）

## 为什么需要它

批量脱敏这类任务的特征是「提交后用户会离开页面」——关弹窗、切到别的页、
刷新、甚至关掉浏览器再回来。而任务状态原本只存在**创建它的那个进程的内存里**：

- `gunicorn_config.py` 的默认形态是 `workers = cpu_count()*2+1` + `sync`，
  前端只要换一条 TCP 连接（keepalive 5s 到期、或刷新页面）就会落到别的 worker，
  `GET /progress/<task_id>` 直接 404「任务不存在或已过期」；
- 当前 compose 用的是 `GUNICORN_WORKERS=1` + `gthread`（深度相机 USB 独占要求
  单进程），**但 `max_requests=1000` 仍在**：轮询 800ms 时约 13 分钟就把唯一
  worker 撑到优雅回收，正在跑任务的 daemon 线程会被连带杀掉。

两条路径都会让「挂后台」变成「进度永远卡住」。把状态挪到进程外，至少保证
**进度可读、卡死可识别**；配合 `GUNICORN_MAX_REQUESTS=0`（compose 已设）让
执行线程不被 worker 回收打断。

## 口径

- 任务快照: ``dm:job:<ns>:<task_id>`` → JSON 字符串，带 TTL
- 任务索引: ``dm:job:<ns>:index`` → ZSET，score = created_at（用于「列出最近任务」）
- 键空间与 celery 的 broker/result **分库**（db=2，见 `_derive_url`），互不干扰

## 降级策略

Redis 不可用时**不阻塞业务**：每次写操作同时落一份**本进程内存镜像**，
读操作优先 Redis、失败或缺失则回退内存。于是：

- Redis 一直不可用 → 行为退回改造前（跨进程可见性消失），只 warning 一次；
- Redis 中途挂掉 → 本进程仍能读到自己刚写的任务（内存镜像兜住）。

绝不因为「状态存储」把脱敏主流程搞挂。
"""
import copy
import json
import logging
import threading
import time
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

# 任务状态默认存活时间（秒）—— 与 DesensitizeTaskManager.TASK_TTL 口径一致
DEFAULT_TTL = 6 * 3600
# 索引里最多保留最近多少条任务（ZSET 按 created_at 排序，超出裁掉最老的）
INDEX_LIMIT = 50
# 内存镜像的过期清扫间隔（秒），避免降级模式下无限增长
_MEM_SWEEP_MIN = 60.0

_client = None
_client_lock = threading.Lock()
_degraded = False          # True = 已确认 Redis 不可用，本进程后续直接走内存
_mem = {}                  # ns -> {task_id: (expire_at, task_dict)}
_mem_lock = threading.Lock()
_mem_swept_at = 0.0


# ==================== 键 ====================

def _key(ns, task_id):
    return "dm:job:%s:%s" % (ns, task_id)


def _index_key(ns):
    return "dm:job:%s:index" % ns


# ==================== Redis 连接 ====================

def _derive_url(base):
    """从 broker URL 派生任务状态库（db 号改为 2）

    ``redis://redis:6379/0`` → ``redis://redis:6379/2``

    unix socket（``unix:///path``）没有 db 段，原样返回。
    """
    if not base:
        return None
    try:
        u = urlsplit(base)
    except Exception:                                  # noqa: BLE001
        return base
    if u.scheme not in ("redis", "rediss") or not u.netloc:
        return base
    return urlunsplit((u.scheme, u.netloc, "/2", u.query, u.fragment))


def _resolve_url():
    """取任务状态 Redis 连接串；拿不到配置时返回 None（本次走内存，不永久降级）"""
    try:
        from flask import current_app
        cfg = current_app.config
    except Exception:                                  # noqa: BLE001
        return None
    url = (cfg.get("TASK_STATE_REDIS_URL") or "").strip()
    if url:
        return url
    base = (cfg.get("CELERY_BROKER_URL") or "").strip()
    return _derive_url(base) if base else None


def _client_or_none():
    """惰性建立连接。仅在**连接测试失败**时永久降级；无 app context 只本次跳过"""
    global _client, _degraded
    if _degraded:
        return None
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        if _degraded:
            return None
        url = _resolve_url()
        if not url:
            return None
        try:
            import redis
            client = redis.Redis.from_url(
                url, socket_timeout=2, socket_connect_timeout=2,
                decode_responses=True,
            )
            client.ping()
        except Exception as e:                          # noqa: BLE001
            _degraded = True
            logger.warning(
                "任务状态存储：Redis 不可用（%s），本进程降级为内存 —— "
                "跨进程可见性失效，刷新页面/多 worker 下可能读不到任务进度", e)
            return None
        _client = client
        return _client


def _degrade(exc):
    """运行期 Redis 出错 → 本进程永久降级（内存镜像仍在，不丢已写任务）"""
    global _degraded, _client
    if not _degraded:
        _degraded = True
        _client = None
        logger.warning("任务状态存储：Redis 操作失败（%s），本进程后续改用内存存储", exc)


def backend_name():
    """当前实际使用的后端：``redis`` / ``memory``（供诊断与测试断言）"""
    return "memory" if _client_or_none() is None else "redis"


def reset_for_tests():
    """清空连接与内存镜像（单测用，避免用例互相污染）"""
    global _client, _degraded, _mem, _mem_swept_at
    with _client_lock, _mem_lock:
        _client = None
        _degraded = False
        _mem = {}
        _mem_swept_at = 0.0


# ==================== 内存镜像 ====================

def _mem_sweep_locked():
    global _mem_swept_at
    now = time.time()
    if now - _mem_swept_at < _MEM_SWEEP_MIN:
        return
    _mem_swept_at = now
    for ns in list(_mem.keys()):
        bucket = _mem[ns]
        for tid in [t for t, (exp, _d) in bucket.items() if exp <= now]:
            bucket.pop(tid, None)
        if not bucket:
            _mem.pop(ns, None)


def _mem_put(ns, task_id, task, ttl):
    # ⚠️ 必须存**深拷贝快照**，不能存引用：Redis 路径是"序列化成 JSON 字串"，
    # 读取端拿到的是当时那一刻的快照；内存镜像若存引用，写方后续原地改字段会
    # 穿透到镜像里 —— 两条路径行为就不一致了（本地测试全绿、上 Redis 变样）。
    with _mem_lock:
        _mem.setdefault(ns, {})[task_id] = (time.time() + ttl, copy.deepcopy(task))
        _mem_sweep_locked()


def _mem_get(ns, task_id):
    with _mem_lock:
        row = _mem.get(ns, {}).get(task_id)
        if not row:
            return None
        exp, task = row
        if exp <= time.time():
            _mem.get(ns, {}).pop(task_id, None)
            return None
        return copy.deepcopy(task)


def _mem_del(ns, task_id):
    with _mem_lock:
        _mem.get(ns, {}).pop(task_id, None)


def _mem_list(ns, limit):
    with _mem_lock:
        now = time.time()
        rows = [(exp, copy.deepcopy(t)) for exp, t in _mem.get(ns, {}).values()
                if exp > now]
    rows.sort(key=lambda r: r[1].get("created_at") or 0, reverse=True)
    return [t for _exp, t in rows[:limit]]


# ==================== 对外接口 ====================

def save(namespace, task, ttl=None):
    """写入任务快照（Redis + 内存镜像双写）"""
    task_id = (task or {}).get("task_id")
    if not task_id:
        return
    ttl = int(ttl or DEFAULT_TTL)
    _mem_put(namespace, task_id, task, ttl)

    client = _client_or_none()
    if client is None:
        return
    try:
        payload = json.dumps(task, ensure_ascii=False)
        pipe = client.pipeline()
        pipe.set(_key(namespace, task_id), payload, ex=ttl)
        pipe.zadd(_index_key(namespace),
                  {task_id: float(task.get("created_at") or time.time())})
        # 裁掉最老的，只留最近 INDEX_LIMIT 条（score 越大越新 → 删 rank 低的一端）
        pipe.zremrangebyrank(_index_key(namespace), 0, -INDEX_LIMIT - 1)
        pipe.expire(_index_key(namespace), ttl * 2)
        pipe.execute()
    except Exception as e:                              # noqa: BLE001
        _degrade(e)


def load(namespace, task_id):
    """读取任务快照；不存在或已过期返回 None"""
    client = _client_or_none()
    if client is not None:
        try:
            raw = client.get(_key(namespace, task_id))
            if raw:
                return json.loads(raw)
        except Exception as e:                          # noqa: BLE001
            _degrade(e)
    return _mem_get(namespace, task_id)


def remove(namespace, task_id):
    """删除任务快照与索引项"""
    _mem_del(namespace, task_id)
    client = _client_or_none()
    if client is None:
        return
    try:
        pipe = client.pipeline()
        pipe.delete(_key(namespace, task_id))
        pipe.zrem(_index_key(namespace), task_id)
        pipe.execute()
    except Exception as e:                              # noqa: BLE001
        _degrade(e)


def list_recent(namespace, limit=20):
    """按 created_at 倒序返回最近的若干任务（Redis 与内存镜像**合并去重**）

    合并是必要的：Redis 若在运行中途不可用，索引可能不完整，而本进程的内存
    镜像里还留着刚写的任务 —— 漏掉它会让用户"刚提交的任务突然查不到"。
    """
    limit = max(1, int(limit or 20))
    merged = {}
    for task in _mem_list(namespace, limit):
        tid = task.get("task_id")
        if tid:
            merged[tid] = task

    client = _client_or_none()
    if client is not None:
        try:
            ids = client.zrevrange(_index_key(namespace), 0, max(limit * 3, limit) - 1)
            if ids:
                raw = client.mget([_key(namespace, i) for i in ids])
                missing = []
                for tid, payload in zip(ids, raw):
                    if not payload:
                        missing.append(tid)          # 已过期：顺手从索引里摘掉
                        continue
                    try:
                        merged[tid] = json.loads(payload)
                    except Exception:                # noqa: BLE001
                        missing.append(tid)
                if missing:
                    client.zrem(_index_key(namespace), *missing)
        except Exception as e:                          # noqa: BLE001
            _degrade(e)

    rows = list(merged.values())
    rows.sort(key=lambda t: t.get("created_at") or 0, reverse=True)
    return rows[:limit]
