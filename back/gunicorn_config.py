# -*- coding: utf-8 -*-
"""Gunicorn 生产环境配置"""
import multiprocessing
import os

bind = "0.0.0.0:5000"
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
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
