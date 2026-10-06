# -*- coding: utf-8 -*-
"""Gunicorn 生产环境配置"""
import multiprocessing
import os

bind = "0.0.0.0:5000"
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
# 默认 sync；启用 Orbbec 深度相机时需设为 gthread（MJPEG 长连接 + USB 单进程多线程）
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "sync")
threads = int(os.getenv("GUNICORN_THREADS", 1))
# 默认 120s；启用 Orbbec 时设为 0（MJPEG 预览流不超时）
timeout = int(os.getenv("GUNICORN_TIMEOUT", 120))
keepalive = 5
# 默认 1000；本项目设为 0（禁用 worker 自动回收）—— 这是**有意的**：
# 本服务的 worker 里除了 HTTP 请求，还挂着两类长生命周期的东西：
#   1) 批量脱敏/导出任务的后台线程（视频人脸脱敏是分钟级，一个批量可能跑半小时）
#   2) MJPEG 预览长连接
# worker 达到 max_requests 后会**优雅退出**，其 daemon 线程被一并杀掉 ——
# 正在跑的脱敏任务会**静默中断**（状态永远停在 running，前端一直转圈）。
# 前端 800ms 轮询时，1000 个请求约 13 分钟就打满，几乎必然命中。
# 任务状态已挪到 Redis（见 app/utils/task_store.py）保证"可读"，但要让长任务
# **不被中途杀掉**，必须关掉自动回收。代价是进程内存不随请求数周期性释放，
# 对本项目（常驻单 worker + 长连接）可接受。
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = 50 if max_requests > 0 else 0
preload_app = True
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")


def post_fork(server, worker):
    """子进程 fork 后立即 dispose 父进程继承来的连接池

    preload_app=True 时，SQLAlchemy 引擎与连接池在 fork 之前创建，
    fork 后多个 worker 共享同一份连接池状态但底层 socket 不能跨进程使用，
    会导致 (2014, 'Command Out of Sync') 与 (2013, 'Lost connection') 间歇性错误。

    在每个 worker 启动时 dispose 旧引擎，强制创建属于本进程的新连接池。
    参考：http://docs.sqlalchemy.org/core/pooling.html#using-connection-pools-with-multiprocessing
    """
    try:
        from run import app
        from app.extensions import db
        with app.app_context():
            db.engine.dispose()
    except Exception as e:
        server.log.error("post_fork dispose engine failed: %s", e)
