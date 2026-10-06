# -*- coding: utf-8 -*-
"""脱敏产物索引（挂在原数据资产下的「脱敏版本」）

## 为什么需要这张表

需求：清洗页可**批量脱敏**，导出时若已有脱敏产物就**直接复用**，不再临时处理。

## 为什么产物**不能**做成新的 DataAsset

本项目有两条硬约束，任一都足以否决"脱敏产物 = 新资产"：

1. **单实例模态**（`app/utils/singleton_assets.py`，2026-09-16 起）：每受试者的
   ECG / EEG / AUDIO / userInfo 每类只允许一条，后采集的覆盖先采集的。
   脱敏产物若作为独立资产入库，会被下一轮扫描按唯一性规则**收敛删除**。
2. **上传对账**（`ingest-digest` + 前端 `reconcileUploadedMap`）：前端按
   "原始文件名 + 大小"判断某文件是否处理过。派生出来的产物在后端没有对应
   "原始文件"，会被判成"没处理过" → **每轮重传**（2026-09-16 现场事故，
   12 个 EEG 每轮全量重传 + 每轮 12 条日志）。

故产物挂在**原资产的元信息**里：列表不新增行，只在原资产上标记「已有脱敏文件」。

## 存储

产物文件落在数据湖下的独立前缀目录，与原资产同构：

    {DATA_LAKE_DIR}/.desensitized/{pseudo_id}/{layer}/{data_type}/{file_name}

- 与 `.transcodes/` 同级（项目既有先例），**不落在 raw/cleaned/... 四层之内**，
  因此不会被受试者目录扫描器当成新采集文件重新入库
- 产物**仍是 DMEC 密文**（平台主密钥）：数据湖不新增明文敏感数据。导出复用时的
  额外成本只是一次毫秒级解密，相比重新脱敏（视频是分钟级）可忽略

## 失效判定

`config_hash` = 影响产物**字节**的全部参数的指纹（算法版本 + 各模态参数 + 密钥作用域）。
复用前必须同时满足：

1. `config_hash` 一致（本次要用的脱敏配置与产物生成时相同）
2. 源文件未变（`source_size` + `source_mtime` 对上）
3. 产物文件仍存在

任一条不满足 → 视为不可复用，回落到实时脱敏（**不会**静默用错的产物）。
"""
from datetime import datetime
from app.extensions import db
from app.utils.time import to_local_str


# 可脱敏模态（与 `export_service._DESENS_ITEMS` 同集合，userinfo 是 json 型资产）
MODALITY_USERINFO = "userinfo"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"
MODALITY_EEG = "eeg"
MODALITY_ECG = "ecg"
DESENS_MODALITIES = (MODALITY_USERINFO, MODALITY_AUDIO, MODALITY_VIDEO,
                     MODALITY_EEG, MODALITY_ECG)

MODALITY_LABELS = {
    MODALITY_USERINFO: "受试者信息",
    MODALITY_AUDIO: "音频",
    MODALITY_VIDEO: "视频",
    MODALITY_EEG: "脑电",
    MODALITY_ECG: "心电",
}

# 密钥作用域：批量脱敏始终用平台主密钥；导出勾选「还原包」时才会改用本包一次性密钥
KEY_SCOPE_PLATFORM = "platform"


class DesensitizedArtifact(db.Model):
    """一条脱敏产物：某资产 × 某模态，最多一条（重跑即覆盖）"""

    __tablename__ = "desensitized_artifacts"
    __table_args__ = (
        db.UniqueConstraint("data_asset_id", "modality",
                            name="uq_desens_artifact_asset_modality"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # nullable=False + 未声明 ondelete ⇒ 删除资产前必须显式删本表（否则外键约束报错），
    # 已挂进 subject_service._purge_asset_records
    data_asset_id = db.Column(db.Integer, db.ForeignKey("data_assets.id"),
                              nullable=False, index=True, comment="原数据资产")
    modality = db.Column(db.String(16), nullable=False, index=True,
                         comment="脱敏模态：video/audio/eeg/ecg/userinfo")

    file_path = db.Column(db.String(512), nullable=False,
                          comment="产物路径（相对 DATA_LAKE_DIR）")
    file_name = db.Column(db.String(256), nullable=False,
                          comment="产物文件名（扩展名可能与源不同：视频→.mp4、音频→.wav）")
    file_format = db.Column(db.String(16), comment="产物格式")
    file_size = db.Column(db.BigInteger, default=0, comment="产物大小(字节)")
    encrypted = db.Column(db.Boolean, default=True,
                          comment="产物是否 DMEC 密文（平台主密钥）")

    algorithm_version = db.Column(db.String(32), comment="算法版本标识")
    config_hash = db.Column(db.String(64), index=True,
                            comment="脱敏配置指纹（版本+参数+密钥作用域）")
    key_scope = db.Column(db.String(16), default=KEY_SCOPE_PLATFORM,
                          comment="噪声派生的密钥作用域：platform / pack")

    # 源文件指纹：源被替换/重采后旧产物必须失效（只比大小 + mtime，避免全量哈希的开销）
    source_size = db.Column(db.BigInteger, default=0, comment="源文件大小(字节)")
    # ⚠️ 必须是 DECIMAL 而**不能**是 `db.Float`：MySQL 的 FLOAT 是 4 字节（~7 位有效数字），
    #    而 epoch 秒 ≈ 1.79e9 在该精度下 ULP 高达 128 秒 —— 落库再读出会与真实 mtime
    #    差几十秒（实测 30.018 s），**恒大于** `desens_artifact._MTIME_TOL = 1.0`，
    #    于是所有产物都被判成"源文件已变化"、复用永远命中不了（2026-10-06 真实容器实测）。
    #    ⚠️ 这个 bug 在 SQLite（单测库）里**完全不可见**：SQLite 的 FLOAT 就是 8 字节 REAL。
    #    守住它的测试见 `test_export_service.py::TestReuseExistingArtifact::
    #    test_source_mtime_column_survives_mysql_float_precision`（按 MySQL 方言编译列类型断言）。
    source_mtime = db.Column(db.Numeric(20, 6), default=0,
                             comment="源文件 mtime(epoch 秒，DECIMAL 保精度)")

    # 可还原模态（音频/脑电/心电）的还原参数：与导出包内 _desens_manifest.json
    # 的单条目同结构。视频/受试者信息为不可逆脱敏 → NULL。
    manifest_json = db.Column(db.JSON, comment="还原清单条目（不可逆模态为 NULL）")

    operator_id = db.Column(db.Integer, db.ForeignKey("users.id"), comment="操作人")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "data_asset_id": self.data_asset_id,
            "modality": self.modality,
            "modality_label": MODALITY_LABELS.get(self.modality, self.modality),
            "file_name": self.file_name,
            "file_format": self.file_format,
            "file_size": int(self.file_size or 0),
            "encrypted": bool(self.encrypted),
            "algorithm_version": self.algorithm_version,
            "config_hash": self.config_hash,
            "key_scope": self.key_scope,
            "reversible": self.manifest_json is not None,
            "source_size": int(self.source_size or 0),
            # Numeric 列回来的是 Decimal，直接 jsonify 会 TypeError → 统一转 float。
            # 单位仍是 epoch 秒（列注释里有 DECIMAL(20,6) 的精度说明）。
            "source_mtime": (float(self.source_mtime)
                             if self.source_mtime is not None else None),
            "created_at": to_local_str(self.created_at),
            "updated_at": to_local_str(self.updated_at),
        }
