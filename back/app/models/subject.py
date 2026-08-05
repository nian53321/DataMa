# -*- coding: utf-8 -*-
"""受试者模型"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


class Subject(db.Model):
    """受试者（已脱敏）"""
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    pseudo_id = db.Column(db.String(64), unique=True, nullable=False, index=True, comment="受试者伪ID")
    age = db.Column(db.Integer, comment="年龄")
    gender = db.Column(db.String(8), comment="性别")
    education_level = db.Column(db.String(32), comment="教育程度")
    # 联系信息（脱敏）
    phone = db.Column(db.String(32), comment="联系电话")
    id_card = db.Column(db.String(32), comment="身份证号")
    # 临床信息（脱敏）
    cognitive_risk_level = db.Column(db.String(32), comment="认知风险分级")
    emotion_status = db.Column(db.String(64), comment="情绪状态")
    # 量表得分快照
    mmse_score = db.Column(db.Float, comment="MMSE 总分")
    ad8_score = db.Column(db.Float, comment="AD8 总分")
    moca_score = db.Column(db.Float, comment="MoCA 总分")
    # 采集信息
    collection_batch = db.Column(db.String(32), comment="批次号")
    collection_scene = db.Column(db.String(32), comment="场景代码")
    collection_time = db.Column(db.DateTime, comment="采集时间")
    status = db.Column(db.String(32), default="collecting", comment="状态: collecting/cleaning/annotating/completed")
    remark = db.Column(db.Text, comment="备注")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关联数据
    data_assets = db.relationship("DataAsset", backref="subject", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "pseudo_id": self.pseudo_id,
            "age": self.age,
            "gender": self.gender,
            "education_level": self.education_level,
            "phone": self.phone,
            "id_card": self.id_card,
            "cognitive_risk_level": self.cognitive_risk_level,
            "emotion_status": self.emotion_status,
            "mmse_score": self.mmse_score,
            "ad8_score": self.ad8_score,
            "moca_score": self.moca_score,
            "collection_batch": self.collection_batch,
            "collection_scene": self.collection_scene,
            "collection_time": to_local_str(self.collection_time),
            "status": self.status,
            "remark": self.remark,
            "created_at": to_local_str(self.created_at),
        }
