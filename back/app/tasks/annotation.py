# -*- coding: utf-8 -*-
"""标注异步任务：自动预标注（TorchServe 部署的模型）"""
import logging
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="annotation.pre_annotate")
def pre_annotate(task_id, data_type):
    """自动预标注：按模态调用对应模型，生成带置信度的标签"""
    logger.info("开始预标注 task_id=%s type=%s", task_id, data_type)
    # TODO: 调用 TorchServe 推理接口
    # - 视频: YOLOv8 + SlowFast (人脸关键点/姿态/动作片段/异常行为)
    # - 语音: Whisper + Librosa (语音片段/停顿/语速/语义异常)
    # - 生理信号: MNE + 预训练模型 (脑电频段/心电R峰/眼动注视)
    # - 量表: 规则引擎 (MMSE/AD8 计分与风险分级)
    return {"task_id": task_id, "status": "pre_annotated", "labels": []}
