# -*- coding: utf-8 -*-
"""数据清洗与标准化服务

封装原 api/data.py 的 trigger_cleaning / trigger_standardization 逻辑。
框架阶段同步执行，后续可替换为 Celery 异步任务。

业务规则：
- 清洗：跳过已清洗/已标准化的资产；更新状态 + 创建版本记录 + 记录日志
- 标准化：跳过已标准化的资产；更新状态 + 按模态设置采样率 + 创建版本记录
"""
from typing import List

from app.extensions import db
from app.models import DataAsset, DataLayer
from app.models.data import DataVersion
from app.services.base import BaseService, ValidationError
from app.utils.audit import log_operation


class DataProcessingService(BaseService):
    """数据清洗与标准化服务"""

    def trigger_cleaning(self, asset_ids: List[int], config: dict):
        """触发数据清洗任务

        Args:
            asset_ids: 待清洗数据资产 ID 列表
            config: 清洗配置 {missing, outlier, denoise}

        Returns:
            dict: {cleaned_ids, skipped, count}
        """
        if not asset_ids:
            raise ValidationError("请选择待清洗数据")

        # 清洗配置（记录到版本变更说明中）
        config_desc = "缺失填补={}, 离群检测={}, 去噪={}".format(
            config.get("missing"), config.get("outlier"), config.get("denoise")
        )

        cleaned = []
        skipped = []
        for aid in asset_ids:
            asset = DataAsset.query.get(aid)
            if not asset:
                skipped.append({"id": aid, "reason": "资产不存在"})
                continue
            if asset.status in ("cleaned", "standardized"):
                skipped.append({"id": aid, "reason": "已清洗，无需重复"})
                continue
            # 更新状态：uploaded/cleaning -> cleaned
            asset.status = "cleaned"
            asset.layer = DataLayer.CLEANED
            # 创建版本记录
            version_no = DataVersion.query.filter_by(data_asset_id=aid).count() + 1
            self.session.add(DataVersion(
                data_asset_id=aid,
                version_no=version_no,
                file_path=asset.file_path,
                operator_id=self.operator_id,
                operation="clean",
                change_log=f"数据清洗（{config_desc}）",
            ))
            cleaned.append(aid)

        log_operation("clean", "asset", ",".join(str(i) for i in cleaned),
                      f"清洗 {len(cleaned)} 个数据资产（{config_desc}）",
                      operator=self._operator_user())
        # 批量更新 + 日志一次性原子提交
        self._commit()
        return {
            "cleaned_ids": cleaned,
            "skipped": skipped,
            "count": len(cleaned),
        }

    def trigger_standardization(self, asset_ids: List[int], rates: dict):
        """触发数据标准化任务

        Args:
            asset_ids: 待标准化数据资产 ID 列表
            rates: 重采样配置 {eeg, ecg, eye, gait}

        Returns:
            dict: {standardized_ids, skipped, count}
        """
        if not asset_ids:
            raise ValidationError("请选择待标准化数据")

        # 标准化配置（重采样率）
        config_desc = "重采样: EEG={}Hz, ECG={}Hz, 眼动={}Hz, 步态={}Hz".format(
            rates.get("eeg"), rates.get("ecg"), rates.get("eye"), rates.get("gait")
        )

        standardized = []
        skipped = []
        for aid in asset_ids:
            asset = DataAsset.query.get(aid)
            if not asset:
                skipped.append({"id": aid, "reason": "资产不存在"})
                continue
            if asset.is_standardized:
                skipped.append({"id": aid, "reason": "已标准化，无需重复"})
                continue
            # 更新状态
            asset.status = "standardized"
            asset.is_standardized = True
            # 根据模态设置采样率
            type_key = asset.data_type.value if asset.data_type else None
            if type_key in rates and rates[type_key]:
                asset.sample_rate = rates[type_key]
            # 创建版本记录
            version_no = DataVersion.query.filter_by(data_asset_id=aid).count() + 1
            self.session.add(DataVersion(
                data_asset_id=aid,
                version_no=version_no,
                file_path=asset.file_path,
                operator_id=self.operator_id,
                operation="standardize",
                change_log=f"数据标准化（{config_desc}）",
            ))
            standardized.append(aid)

        log_operation("standardize", "asset", ",".join(str(i) for i in standardized),
                      f"标准化 {len(standardized)} 个数据资产（{config_desc}）",
                      operator=self._operator_user())
        # 批量更新 + 日志一次性原子提交
        self._commit()
        return {
            "standardized_ids": standardized,
            "skipped": skipped,
            "count": len(standardized),
        }

    def _operator_user(self):
        """获取操作员 User 对象"""
        if not self.operator_id:
            return None
        from app.models.user import User
        return User.query.get(self.operator_id)