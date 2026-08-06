# -*- coding: utf-8 -*-
"""等待 MySQL 就绪后再启动 gunicorn

背景：首次部署时 MySQL 容器需要初始化（创建 root 密码/数据库），
即便 docker-compose 配置了 depends_on + healthcheck，某些环境下
backend 仍可能在 MySQL 尚未监听 TCP 3306 时启动，导致 db.create_all()
连接被拒（pymysql.err.OperationalError），gunicorn 反复退出重启。

本脚本在启动 gunicorn 前以 TCP 方式轮询 MySQL，最多等待 60 秒，
MySQL 就绪后返回 0；超时也返回 0（由后续 gunicorn 自行重试/退出），
避免把容器卡死在等待阶段。
"""
import os
import sys
import time

import pymysql


def wait_for_mysql(max_wait: int = 60, interval: int = 3) -> bool:
    host = os.getenv("DB_HOST", "mysql")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")

    waited = 0
    while waited < max_wait:
        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                connect_timeout=3,
            )
            conn.close()
            print(f"[wait_for_mysql] MySQL 已就绪（等待 {waited}s）", flush=True)
            return True
        except Exception:
            waited += interval
            print(
                f"[wait_for_mysql] MySQL 未就绪，{waited}s 后重试（{host}:{port}）",
                flush=True,
            )
            time.sleep(interval)
    print(f"[wait_for_mysql] 等待 {max_wait}s 超时，继续尝试启动（由 gunicorn 自行处理）", flush=True)
    return False


if __name__ == "__main__":
    ok = wait_for_mysql()
    sys.exit(0 if ok else 0)
