# -*- coding: utf-8 -*-
"""Celery 任务模块"""
from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
