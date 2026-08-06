# -*- coding: utf-8 -*-
"""Celery 应用实例与配置"""
from celery import Celery

from app.config import get_config

config = get_config()

celery_app = Celery(
    "data_management",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.cleaning",
        "app.tasks.annotation",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_track_started=True,
    result_expires=86400,
    # Celery 5.x 起启动时不再默认重试 broker 连接；显式开启，
    # 避免 Redis 尚未就绪时 worker 直接退出（Celery 6.0 将强制要求该配置）
    broker_connection_retry_on_startup=True,
)
