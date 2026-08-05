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
)
