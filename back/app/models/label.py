# -*- coding: utf-8 -*-
"""标签模型：统一管理标注标签体系，支持按模态分类、增删改查与批量替换"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


# 默认标签库（按模态分组），首次启动时写入数据库
DEFAULT_LABELS = {
    "video": [
        {"name": "人脸关键点", "value": "face_keypoint", "color": "#409EFF"},
        {"name": "姿态", "value": "pose", "color": "#67C23A"},
        {"name": "动作片段", "value": "action_segment", "color": "#E6A23C"},
        {"name": "异常行为", "value": "abnormal_behavior", "color": "#F56C6C"},
    ],
    "audio": [
        {"name": "语音片段", "value": "speech_segment", "color": "#409EFF"},
        {"name": "停顿", "value": "pause", "color": "#909399"},
        {"name": "语速异常", "value": "speech_rate_abnormal", "color": "#F56C6C"},
        {"name": "语义异常", "value": "semantic_abnormal", "color": "#F56C6C"},
    ],
    "eeg": [
        {"name": "α频段", "value": "alpha_band", "color": "#409EFF"},
        {"name": "β频段", "value": "beta_band", "color": "#67C23A"},
        {"name": "θ频段", "value": "theta_band", "color": "#E6A23C"},
        {"name": "异常波形", "value": "abnormal_wave", "color": "#F56C6C"},
    ],
    "ecg": [
        {"name": "R峰", "value": "r_peak", "color": "#409EFF"},
        {"name": "心律失常", "value": "arrhythmia", "color": "#F56C6C"},
        {"name": "伪迹", "value": "artifact", "color": "#909399"},
    ],
    "eye": [
        {"name": "注视", "value": "fixation", "color": "#409EFF"},
        {"name": "扫视", "value": "saccade", "color": "#67C23A"},
        {"name": "眨眼", "value": "blink", "color": "#E6A23C"},
    ],
    "gait": [
        {"name": "步态周期", "value": "step", "color": "#409EFF"},
        {"name": "冻结步态", "value": "freeze", "color": "#F56C6C"},
        {"name": "异常步态", "value": "abnormal_gait", "color": "#F56C6C"},
    ],
    "scale": [
        {"name": "高风险", "value": "risk_high", "color": "#F56C6C"},
        {"name": "中风险", "value": "risk_medium", "color": "#E6A23C"},
        {"name": "低风险", "value": "risk_low", "color": "#67C23A"},
        {"name": "异常分项", "value": "abnormal_item", "color": "#F56C6C"},
    ],
    "task": [
        {"name": "反应正确", "value": "response_correct", "color": "#67C23A"},
        {"name": "反应错误", "value": "response_error", "color": "#F56C6C"},
        {"name": "反应超时", "value": "response_timeout", "color": "#E6A23C"},
        {"name": "表现异常", "value": "abnormal_performance", "color": "#F56C6C"},
    ],
}


class Label(db.Model):
    """标签库：按模态分组的标注标签体系"""
    __tablename__ = "labels"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, comment="标签中文名")
    value = db.Column(db.String(64), nullable=False, comment="标签标识(英文)")
    data_type = db.Column(db.String(32), nullable=False, index=True,
                          comment="所属模态: video/audio/eeg/ecg/eye/gait/scale/task")
    color = db.Column(db.String(16), default="", comment="标签颜色(十六进制)")
    description = db.Column(db.String(256), default="", comment="标签描述")
    is_active = db.Column(db.Boolean, default=True, comment="是否启用")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("value", "data_type", name="uq_label_value_type"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "value": self.value,
            "data_type": self.data_type,
            "color": self.color,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": to_local_str(self.created_at),
        }


def init_default_labels():
    """首次启动时把默认标签写入数据库（已存在则跳过）"""
    if Label.query.count() > 0:
        return
    for data_type, items in DEFAULT_LABELS.items():
        for item in items:
            db.session.add(Label(
                name=item["name"],
                value=item["value"],
                data_type=data_type,
                color=item.get("color", ""),
            ))
    db.session.commit()
