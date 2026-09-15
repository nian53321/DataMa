# -*- coding: utf-8 -*-
"""数据导出服务：批量打包数据资产为 zip

支持：
- 按 asset_ids 列表或 subject_ids/data_types/layers 筛选导出
- 加密导出（保持 DMEC 格式，文件追加 .dmec 后缀）
- 明文导出（解密后打包原文件）
- 单次最多 200 文件 / 2GB
- 异步任务 + 实时压缩进度回调（ExportTaskManager）

复用：
- _decrypt_for_serving：复用 asset_service 模块级解密函数
- _remove_file_safely：复用 subject_service 安全删除文件
"""
import os
import json
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from typing import Tuple, List

from flask import current_app

from app.extensions import db
from app.models import DataAsset, DataType, DataLayer, Subject
from app.services.base import BaseService, ValidationError, NotFoundError
from app.services.asset_service import _decrypt_for_serving
from app.services.subject_service import _remove_file_safely
from app.utils.audit import log_operation
from app.utils.audio_desensitize import desensitize_audio_file
from app.utils.crypto import (encrypt_bytes, encrypt_bytes_with_key,
                              get_key_fingerprint, is_encrypted_file,
                              rewrap_dmec_file)
from app.utils.desensitize import desensitize_userinfo_text
from app.utils.signal_desensitize import (
    desensitize_eeg_file, desensitize_ecg_file,
    MANIFEST_NAME as SIGNAL_MANIFEST_NAME,
    build_manifest as build_signal_manifest,
    manifest_bytes as signal_manifest_bytes,
    use_hmac_key,
)
from app.utils.restore_kit import build_kit_files
from app.utils.video_desensitize import desensitize_video_file


class ExportService(BaseService):
    """数据资产批量导出服务"""

    EXPORT_MAX_FILE_COUNT = 200
    EXPORT_MAX_TOTAL_SIZE = 2 * 1024 ** 3  # 2GB

    def prepare_export(self, payload: dict, progress_callback=None,
                       assets: List[DataAsset] = None) -> Tuple[str, str, list]:
        """准备导出 zip

        :param payload: {
            asset_ids: [int],            # 模式一：指定资产 ID 列表
            subject_ids: [int],          # 模式二：按多个受试者筛选（空/缺省 = 全部受试者）
            data_types: [str],           # 模式二：按多个数据类型筛选（空/缺省 = 全部类型）
            layers: [str],               # 模式二：按多个数据层筛选（空/缺省 = 全部层）
            # 兼容旧版单值参数（自动并入复数集合）：
            subject_id: int,
            data_type: str,
            layer: str,
            encrypted: bool              # 是否加密导出（默认 True）
            desensitize: {               # 统一脱敏配置（推荐）
                enabled: bool,           # 总开关；False 时全部子项强制关闭
                userinfo: bool,          # 受试者信息文件（userInfo）字段级脱敏
                audio: bool,             # 音频声纹脱敏（定点 PCM 加噪，可逆）
                eeg: bool,               # 脑电值级脱敏（全列保留，可逆）
                ecg: bool,               # 心电值级脱敏（全列保留，可逆）
                video: bool,             # 视频人脸区域脱敏（不可逆，音轨移除）
            }
            # 旧版单模态键，仍兼容（仅在 desensitize 缺省时生效）：
            desensitized: bool           # → userinfo
            audio_desensitize: bool      # → audio
        }
        :param progress_callback: 可选回调(processed, total, size_done, size_total)
        :param assets: 已解析并通过限额校验的资产列表（异步任务线程传入，
                       避免与 resolve_assets 重复查询数据库）；缺省时内部解析
        :return: (zip_tmp_path, download_filename, skipped_errors)
            skipped_errors: 被跳过文件的明细列表（磁盘丢失/打包失败），
            供任务状态展示与前端提示
        :raises ValidationError: 未选择资产 / 超过数量或大小限制
        :raises NotFoundError: 资产或文件不存在
        """
        encrypted = bool(payload.get("encrypted", True))
        desens = _resolve_desens_config(payload)
        if assets is None:
            assets = self._resolve_assets(payload)
            self._validate_limits(assets)
        zip_path, skipped = self._build_zip(
            assets, encrypted, desens=desens,
            progress_callback=progress_callback)
        self._log_audit(assets, encrypted, skipped_count=len(skipped),
                        desens=desens, skipped=skipped)
        return zip_path, self._build_filename(encrypted), skipped

    def resolve_assets(self, payload: dict):
        """仅解析待导出资产（用于异步任务初始化，提前获取总数/总大小）"""
        assets = self._resolve_assets(payload)
        self._validate_limits(assets)
        return assets

    def preview_export(self, payload: dict, max_items: int = 200) -> dict:
        """预览导出范围：命中资产统计 + 明细（不打包、不记审计日志）

        供导出界面在选择条件变化时实时展示"将导出哪些数据资产"，
        并提前暴露超限风险（文件数/总大小），避免用户提交后才被拒绝。
        """
        assets = self._resolve_assets(payload, require_nonempty=False)
        total_count = len(assets)
        total_size = sum(int(a.file_size or 0) for a in assets)

        subject_ids = {a.subject_id for a in assets}
        subjects = {}
        if subject_ids:
            subjects = {s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}

        by_type, by_layer = {}, {}
        items = []
        for a in assets:
            dt = a.data_type.value if a.data_type else "unknown"
            ly = a.layer.value if a.layer else "unknown"
            by_type[dt] = by_type.get(dt, 0) + 1
            by_layer[ly] = by_layer.get(ly, 0) + 1
            if len(items) < max_items:
                subject = subjects.get(a.subject_id)
                items.append({
                    "asset_id": a.id,
                    "file_name": a.file_name,
                    "data_type": dt,
                    "layer": ly,
                    "file_size": int(a.file_size or 0),
                    "pseudo_id": subject.pseudo_id if subject else f"subject_{a.subject_id}",
                    "real_name": subject.real_name if subject else None,
                })

        return {
            "total_count": total_count,
            "total_size": total_size,
            "exceeds_count_limit": total_count > self.EXPORT_MAX_FILE_COUNT,
            "exceeds_size_limit": total_size > self.EXPORT_MAX_TOTAL_SIZE,
            "max_file_count": self.EXPORT_MAX_FILE_COUNT,
            "by_type": by_type,
            "by_layer": by_layer,
            "items": items,
        }

    # ==================== 私有辅助 ====================

    def _resolve_assets(self, payload: dict, require_nonempty: bool = True) -> List[DataAsset]:
        """根据 payload 解析待导出资产列表

        优先使用 asset_ids；否则按 subject_ids/data_types/layers 批量筛选
        （为兼容旧调用方，单值 subject_id/data_type/layer 会自动并入复数集合）
        :param require_nonempty: True 时无匹配资产抛 ValidationError（导出流程）；
                                 False 时返回空列表（预览流程，前端显示"无匹配资产"）
        """
        asset_ids = payload.get("asset_ids") or []
        if asset_ids:
            assets = DataAsset.query.filter(DataAsset.id.in_(asset_ids)).all()
            if not assets:
                raise ValidationError("未找到指定的数据资产")
            return assets

        # 合并单值与复数参数（去重，保持顺序）
        def _merge(single_key, plural_key, caster=str):
            singles = []
            v = payload.get(single_key)
            if v:
                singles = [v]
            plurals = payload.get(plural_key) or []
            seen, result = set(), []
            for x in singles + list(plurals):
                if x is None or x == "":
                    continue
                k = str(x)
                if k in seen:
                    continue
                seen.add(k)
                result.append(caster(x))
            return result

        subject_ids = _merge("subject_id", "subject_ids", caster=int)
        data_types = _merge("data_type", "data_types", caster=str)
        layers = _merge("layer", "layers", caster=str)

        query = DataAsset.query
        if subject_ids:
            query = query.filter(DataAsset.subject_id.in_(subject_ids))

        if data_types:
            valid_types = []
            for dt in data_types:
                try:
                    valid_types.append(DataType(dt))
                except ValueError:
                    raise ValidationError(f"数据类型非法：{dt}")
            query = query.filter(DataAsset.data_type.in_(valid_types))

        if layers:
            valid_layers = []
            for ly in layers:
                try:
                    valid_layers.append(DataLayer(ly))
                except ValueError:
                    raise ValidationError(f"数据分层非法：{ly}")
            query = query.filter(DataAsset.layer.in_(valid_layers))

        assets = query.order_by(DataAsset.created_at.desc()).all()
        if not assets and require_nonempty:
            raise ValidationError("未选择任何数据资产")
        return assets

    def _validate_limits(self, assets: List[DataAsset]):
        """校验导出规模上限"""
        if len(assets) > self.EXPORT_MAX_FILE_COUNT:
            raise ValidationError(
                f"导出文件数超过上限：{len(assets)} > {self.EXPORT_MAX_FILE_COUNT}，请分批导出"
            )
        total_size = sum(int(a.file_size or 0) for a in assets)
        if total_size > self.EXPORT_MAX_TOTAL_SIZE:
            raise ValidationError(
                f"导出总大小超过上限：{total_size / 1024 / 1024:.1f} MB > 2048 MB，请分批导出"
            )

    def _build_zip(self, assets: List[DataAsset], encrypted: bool,
                   desens: dict = None, progress_callback=None) -> Tuple[str, list]:
        """构建 zip 临时文件

        - encrypted=True：原 DMEC 加密文件直接打包，arcname 追加 .dmec 后缀
        - encrypted=False：解密后打包原文件，arcname 不追加 .dmec
        - desens：统一脱敏配置（见 :func:`_resolve_desens_config`）。各子项
          与 encrypted 正交 —— 加密模式下统一走"解密 → 脱敏 → 重新 DMEC 加密"：
            userinfo → userInfo 文件字段级脱敏
            audio    → 音频声纹脱敏（定点 PCM 域确定性加噪，**可逆**；帧数/采样率不变）
            eeg      → 脑电值级脱敏（全列保留：信号列加噪 + 绝对时间列整列平移）
            ecg      → 心电值级脱敏（全列保留：value 列加噪，heart_rate 列同样加噪）
            video    → 视频人脸区域脱敏（逐帧检测 + 人脸区不可逆马赛克 + 移除音轨）
            restore_kit → 是否随包附带"还原包"（离线还原脚本 + 本包专用密钥 + 说明）
          video 是唯一**不可逆**的脱敏项：马赛克是结构性破坏（块内像素被整块替换，
          原始细节已不存在），与 EEG/ECG/音频的值级脱敏不同，故不写 manifest、
          不参与 restore_kit（包内不存在任何可回推原人脸的参数）。
          v3 起弃用模糊改马赛克，依据是实测「模糊加大到一定程度后 SFace 余弦反而
          回升」（窗口/人脸 1.15~3.56 时 0.36~0.49，越过同一人阈值 0.363）；马赛克
          在 块/人脸 ≥ 1/16 时余弦单调更低且不回升。块边长按人脸短边分级（≈ 1/8），
          脸越近块越粗，详见 ``app/utils/video_desensitize.py`` 模块 docstring。
          另注：视频音轨含受试者声纹，而 audio 开关只作用于 audio 类型资产、
          不覆盖视频，故视频脱敏会**移除音轨**（`-an`）。
          EEG/ECG/音频三类均为**可逆变换**，还原参数随包写入
          `_desens_manifest.json`（不含密钥），持 DESENS_HMAC_KEY 方可逐字节还原。
          勾选 restore_kit 时改用**本包专用密钥**（`os.urandom(32)`）脱敏，密钥随包
          给出（manifest 记 `key_scope: "pack"`）—— 接收方开箱即可还原，而平台主
          密钥不出包，单包泄露不会波及历史/其他脱敏包，也不泄露 userInfo 哈希密钥。
          加密导出同时勾 restore_kit 时，还会再生成一把**本包专用主密钥**：脱敏后的
          明文用它加密、其余原文件走 DEK 流式重包裹（内容密文不变），因此包内
          `.dmec` 只认包内 `master.key`。随包脚本自带解密能力，接收方"解压即跑"
          即可一步完成解密 + 去脱敏。
          **任一脱敏失败都不回退为原样导出**，记入 errors 跳过
          （宁可缺文件，不可明文外泄）
        - 部分文件丢失：跳过，记入 errors（不写入 zip，返回给调用方展示/审计）
        - progress_callback(processed, total, size_done, size_total)：可选进度回调
        :return: (zip_tmp_path, skipped_errors)
        """
        desens = desens or {}
        do_userinfo = bool(desens.get("userinfo"))
        do_audio = bool(desens.get("audio"))
        do_eeg = bool(desens.get("eeg"))
        do_ecg = bool(desens.get("ecg"))
        do_video = bool(desens.get("video"))
        storage_root = current_app.config["DATA_LAKE_DIR"]
        # 预加载受试者 pseudo_id（避免 N+1 查询）
        subject_ids = {a.subject_id for a in assets}
        subjects = {s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}

        fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="export_")
        os.close(fd)

        total_files = len(assets)
        total_size = sum(int(a.file_size or 0) for a in assets)
        processed = 0
        size_done = 0
        errors = []
        desensitized_fields = 0   # 累计命中脱敏的字段数（用于日志可观测性）
        audio_desensitized_files = 0  # 累计完成声纹脱敏的音频文件数
        video_desensitized_files = 0  # 累计完成人脸区域脱敏的视频文件数
        signal_desensitized_files = 0  # 累计完成信号级脱敏的 EEG/ECG 文件数
        # 信号脱敏的还原参数（逐文件，含**音频**）随包写出 _desens_manifest.json，
        # 持密钥方据此无损还原；manifest 本身不含密钥
        signal_manifest_entries = []
        # 包内**其余**加密文件（视频/userInfo/眼动等）：只记路径。
        # 它们的脱敏不可逆（人脸马赛克 / 字段掩码），没有可还原的参数，
        # 但**必须被解密** —— 随包脚本按这份清单把包内所有 .dmec 解开，
        # 否则接收方拿到的包一半是明文、一半仍是密文。
        encrypted_entries = []
        # 「随包附带还原包」：本次导出改用现生成的**本包专用密钥**，并由 use_hmac_key
        # 只在本线程内生效（不污染并发导出，也不把平台主密钥写进包）。还原包要等
        # 信号文件全部处理完、知道条目数之后才写。
        do_restore_kit = bool(desens.get("restore_kit"))
        pack_key = os.urandom(32) if do_restore_kit else None
        # 加密导出 + 还原包：再生成一把**本包专用主密钥**用于封装包内密文。
        # 平台主密钥绝不出包；包密钥泄露只影响这一个包（解不开数据湖里任何文件）。
        pack_master_key = (os.urandom(32) if (do_restore_kit and encrypted) else None)

        def _encrypt_for_pack(plaintext: bytes) -> bytes:
            """按目标密钥加密：有本包主密钥就用它，否则走平台主密钥（原行为）"""
            if pack_master_key:
                return encrypt_bytes_with_key(plaintext, pack_master_key)
            return encrypt_bytes(plaintext)

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for asset in assets:
                    # zip 内已写入的条目数：用于识别"本资产新写进去的密文条目"
                    before_entries = len(zf.NameToInfo)
                    try:
                        if not asset.file_path:
                            errors.append({
                                "asset_id": asset.id,
                                "file_name": asset.file_name,
                                "reason": "资产无关联文件",
                            })
                            current_app.logger.error(
                                "导出跳过 asset_id=%s（%s）：资产无关联文件",
                                asset.id, asset.file_name)
                            continue
                        abs_path = os.path.join(storage_root, asset.file_path)
                        if not os.path.exists(abs_path):
                            errors.append({
                                "asset_id": asset.id,
                                "file_name": asset.file_name,
                                "reason": "磁盘文件丢失",
                            })
                            current_app.logger.error(
                                "导出跳过 asset_id=%s（%s）：磁盘文件丢失 %s",
                                asset.id, asset.file_name, abs_path)
                            continue

                        subject = subjects.get(asset.subject_id)
                        pseudo_id = subject.pseudo_id if subject else f"subject_{asset.subject_id}"
                        layer = asset.layer.value if asset.layer else "unknown"
                        data_type = asset.data_type.value if asset.data_type else "unknown"
                        file_name = asset.file_name or f"asset_{asset.id}"

                        fmt = (asset.file_format or "").lower()
                        suffix = f".{fmt}" if fmt else ""

                        # 逻辑路径：既是明文导出时的 arcname，也是信号脱敏的密钥派生输入
                        # （不含 .dmec 后缀）—— 保证同一文件在明文/加密两种模式下派生出
                        # 同一套噪声，还原方不必关心当初是哪种导出模式
                        logical_path = f"{pseudo_id}/{layer}/{data_type}/{file_name}"

                        if do_userinfo and _is_userinfo_asset(asset):
                            # 脱敏导出：解出明文 → 字段级脱敏 → 按目标模式打包。
                            # 加密模式必须重新 DMEC 封装，否则脱敏后的明文被原样带出
                            tmp_paths = []
                            try:
                                plain_path, plain_is_temp = _decrypt_for_serving(
                                    abs_path, suffix=suffix)
                                if plain_is_temp:
                                    tmp_paths.append(plain_path)
                                masked_path, hits = _write_masked_userinfo_temp(plain_path)
                                tmp_paths.append(masked_path)
                                # 若 userInfo 字段名未命中任何规则则 hits=0（导出内容实为明文）
                                if hits == 0:
                                    current_app.logger.warning(
                                        "脱敏导出：%s 未命中任何脱敏字段（请检查脱敏规则是否启用）",
                                        asset.file_name)
                                desensitized_fields += hits
                                if encrypted:
                                    with open(masked_path, "rb") as f:
                                        pack_path = _write_temp_bytes(
                                            _encrypt_for_pack(f.read()), suffix=".dmec")
                                    tmp_paths.append(pack_path)
                                    arc_file = (file_name if file_name.endswith(".dmec")
                                                else f"{file_name}.dmec")
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{arc_file}"
                                else:
                                    pack_path = masked_path
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{file_name}"
                                zf.write(pack_path, arcname)
                            finally:
                                for p in tmp_paths:
                                    _remove_file_safely(p)
                        elif do_audio and _is_audio_asset(asset):
                            # 音频声纹脱敏：解出明文 → **定点 PCM 域确定性加噪**（可逆）
                            # → 按目标模式打包。噪声按 pseudo_id + 逻辑路径 + 声道号
                            # 派生（同受试者可复现、跨受试者不同），幅度 R 随录音响度
                            # 自适应并被写入 manifest，接收方凭包内密钥可逐字节还原。
                            # ffmpeg 失败时**不回退为原样导出** —— 抛异常由下方 except
                            # 记入 errors 并跳过，否则用户以为已脱敏而实际明文外泄。
                            tmp_paths = []
                            try:
                                plain_path, plain_is_temp = _decrypt_for_serving(
                                    abs_path, suffix=suffix)
                                if plain_is_temp:
                                    tmp_paths.append(plain_path)
                                # 输出扩展名必须与源格式一致（就地改写时容器不变）
                                fd_out, masked_path = tempfile.mkstemp(
                                    suffix=suffix or ".wav", prefix="export_audio_")
                                os.close(fd_out)
                                tmp_paths.append(masked_path)
                                # 必须包在 use_hmac_key 里：本包密钥（pack_key）参与
                                # 噪声派生，接收方才能用包内 desens.key 还原
                                with use_hmac_key(pack_key):
                                    ok, audio_err, audio_stats = desensitize_audio_file(
                                        plain_path, masked_path, pseudo_id,
                                        seed_path=logical_path,
                                        logger=current_app.logger)
                                if not ok:
                                    raise RuntimeError(
                                        f"音频声纹脱敏未完成，已跳过以避免明文导出"
                                        f"（{audio_err or '未知原因'}）")
                                audio_desensitized_files += 1
                                audio_stats = audio_stats or {}
                                # 非整数 PCM 的源（mp3/m4a/浮点 wav）在脱敏时已被转码为
                                # 16-bit PCM WAV ⇒ 扩展名必须跟着**实际内容**走，
                                # 否则下游按扩展名挑解析器会直接失败
                                out_name = file_name
                                if audio_stats.get("canonical"):
                                    out_name = os.path.splitext(file_name)[0] + ".wav"
                                # 还原参数（含每声道噪声幅度 + 原文 sha256）随包进 manifest
                                entry = audio_stats.get("manifest")
                                if entry:
                                    entry = dict(entry)
                                    entry_arc = (
                                        out_name if not encrypted
                                        else (out_name if out_name.endswith(".dmec")
                                              else f"{out_name}.dmec"))
                                    entry["arcname"] = (
                                        f"{pseudo_id}/{layer}/{data_type}/{entry_arc}")
                                    entry["encrypted"] = bool(encrypted)
                                    entry["layer"] = layer
                                    entry["data_type"] = data_type
                                    signal_manifest_entries.append(entry)
                                if encrypted:
                                    with open(masked_path, "rb") as f:
                                        pack_path = _write_temp_bytes(
                                            _encrypt_for_pack(f.read()), suffix=".dmec")
                                    tmp_paths.append(pack_path)
                                    arc_file = (out_name if out_name.endswith(".dmec")
                                                else f"{out_name}.dmec")
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{arc_file}"
                                else:
                                    pack_path = masked_path
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{out_name}"
                                zf.write(pack_path, arcname)
                            finally:
                                for p in tmp_paths:
                                    _remove_file_safely(p)
                        elif do_video and _is_video_asset(asset):
                            # 视频人脸区域脱敏：解出明文 → 逐帧检测人脸 + 人脸区不可逆
                            # 马赛克 → 重编码 H.264 MP4（加密模式下再 DMEC 封装）。
                            # 与 EEG/ECG 不同，这是**不可逆**变换：不写 manifest、
                            # 不进 restore_kit（包内不存在任何可回推原始人脸的参数）。
                            # 音轨一律移除 —— 视频音轨含受试者声纹，而 audio 开关只作用于
                            # audio 类型资产、不覆盖视频，保留音轨等于把未脱敏的声纹
                            # 一起发出去。
                            # 处理失败**不回退为原样导出** —— 回退等于人脸未脱敏却标称
                            # 已脱敏，抛异常由下方 except 记入 errors 并跳过。
                            tmp_paths = []
                            try:
                                plain_path, plain_is_temp = _decrypt_for_serving(
                                    abs_path, suffix=suffix)
                                if plain_is_temp:
                                    tmp_paths.append(plain_path)
                                # 输出统一为 MP4，临时文件固定 .mp4 后缀
                                fd_out, masked_path = tempfile.mkstemp(
                                    suffix=".mp4", prefix="export_video_")
                                os.close(fd_out)
                                tmp_paths.append(masked_path)
                                ok, vid_err, vid_stats = desensitize_video_file(
                                    plain_path, masked_path,
                                    logger=current_app.logger)
                                # vid_stats 的明细（帧数/检出帧数/耗时）已由
                                # desensitize_video_file 内部记入日志
                                if not ok:
                                    raise RuntimeError(
                                        f"视频人脸脱敏未完成，已跳过以避免人脸明文导出"
                                        f"（{vid_err or '未知原因'}）")
                                video_desensitized_files += 1
                                # 内容已是 H.264 MP4，arcname 扩展名必须同步：
                                # 扩展名与实际容器不符时，下游按扩展名挑解析器会直接失败
                                out_name = _video_arc_name(file_name)
                                if encrypted:
                                    with open(masked_path, "rb") as f:
                                        pack_path = _write_temp_bytes(
                                            _encrypt_for_pack(f.read()), suffix=".dmec")
                                    tmp_paths.append(pack_path)
                                    arc_file = (out_name if out_name.endswith(".dmec")
                                                else f"{out_name}.dmec")
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{arc_file}"
                                else:
                                    pack_path = masked_path
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{out_name}"
                                zf.write(pack_path, arcname)
                            finally:
                                for p in tmp_paths:
                                    _remove_file_safely(p)
                        elif ((do_eeg and _is_eeg_asset(asset))
                              or (do_ecg and _is_ecg_asset(asset))):
                            # 信号级脱敏（EEG/ECG）：解出明文 → 加噪（EEG 另删绝对时间列）
                            # → 按目标模式打包。噪声按 pseudo_id 派生（同受试者可复现、
                            # 跨受试者不同）。实测"通道置换/每通道增益"对通道间相干性
                            # 零影响（相干是通道对的函数，置换只换标签），故只做加噪 ——
                            # 见 signal_desensitize 模块 docstring 的标定表。
                            # 处理失败**不回退为原样导出** —— 抛异常由下方 except 记入
                            # errors 并跳过，否则用户以为已脱敏而实际明文外泄。
                            tmp_paths = []
                            try:
                                plain_path, plain_is_temp = _decrypt_for_serving(
                                    abs_path, suffix=suffix)
                                if plain_is_temp:
                                    tmp_paths.append(plain_path)
                                fd_out, masked_path = tempfile.mkstemp(
                                    suffix=suffix or ".csv", prefix="export_sig_")
                                os.close(fd_out)
                                tmp_paths.append(masked_path)
                                if _is_eeg_asset(asset):
                                    with use_hmac_key(pack_key):
                                        ok, sig_err, sig_stats = desensitize_eeg_file(
                                            plain_path, masked_path, pseudo_id,
                                            seed_path=logical_path,
                                            logger=current_app.logger)
                                    sig_label = "脑电"
                                else:
                                    with use_hmac_key(pack_key):
                                        ok, sig_err, sig_stats = desensitize_ecg_file(
                                            plain_path, masked_path, pseudo_id,
                                            seed_path=logical_path,
                                            logger=current_app.logger)
                                    sig_label = "心电"
                                if not ok:
                                    raise RuntimeError(
                                        f"{sig_label}信号脱敏未完成，已跳过以避免明文导出"
                                        f"（{sig_err or '未知原因'}）")
                                # 记录还原参数 + 实际包内路径（加密模式带 .dmec 后缀）
                                entry = (sig_stats or {}).get("manifest")
                                if entry:
                                    entry = dict(entry)
                                    entry_arc = (
                                        file_name if not encrypted
                                        else (file_name if file_name.endswith(".dmec")
                                              else f"{file_name}.dmec"))
                                    entry["arcname"] = (
                                        f"{pseudo_id}/{layer}/{data_type}/{entry_arc}")
                                    entry["encrypted"] = bool(encrypted)
                                    entry["layer"] = layer
                                    entry["data_type"] = data_type
                                    signal_manifest_entries.append(entry)
                                signal_desensitized_files += 1
                                if encrypted:
                                    with open(masked_path, "rb") as f:
                                        pack_path = _write_temp_bytes(
                                            _encrypt_for_pack(f.read()), suffix=".dmec")
                                    tmp_paths.append(pack_path)
                                    arc_file = (file_name if file_name.endswith(".dmec")
                                                else f"{file_name}.dmec")
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{arc_file}"
                                else:
                                    pack_path = masked_path
                                    arcname = f"{pseudo_id}/{layer}/{data_type}/{file_name}"
                                zf.write(pack_path, arcname)
                            finally:
                                for p in tmp_paths:
                                    _remove_file_safely(p)
                        elif encrypted:
                            # 加密导出：原文件直接打包（保持 DMEC 格式），arcname 追加 .dmec。
                            # 随包附带还原包时改为**流式重包裹 DEK**到本包主密钥
                            # （内容密文不变，大文件也无需整体解密再加密），保证包内
                            # 密文都能被包内 master.key 解开 —— 平台主密钥不出包。
                            # ⚠ 后缀必须跟着**实际内容**走：源文件在数据湖里若本就是明文，
                            # 就不得给它套上 .dmec 后缀 —— 那会让接收方（和随包脚本）
                            # 把它当密文解、直接报错。命名与内容不符本身就是交付事故。
                            src_encrypted = is_encrypted_file(abs_path)
                            arc_file = (file_name if (file_name.endswith(".dmec")
                                                      or not src_encrypted)
                                        else f"{file_name}.dmec")
                            arcname = f"{pseudo_id}/{layer}/{data_type}/{arc_file}"
                            if pack_master_key and src_encrypted:
                                tmp_path = None
                                try:
                                    fd, tmp_path = tempfile.mkstemp(
                                        suffix=".dmec", prefix="export_rewrap_")
                                    os.close(fd)
                                    rewrap_dmec_file(abs_path, tmp_path, pack_master_key)
                                    zf.write(tmp_path, arcname)
                                finally:
                                    if tmp_path:
                                        _remove_file_safely(tmp_path)
                            else:
                                zf.write(abs_path, arcname)
                        else:
                            # 明文导出：解密到临时文件后打包，arcname 保持原文件名
                            tmp_path, is_temp = _decrypt_for_serving(abs_path, suffix=suffix)
                            try:
                                arcname = f"{pseudo_id}/{layer}/{data_type}/{file_name}"
                                zf.write(tmp_path, arcname)
                            finally:
                                if is_temp:
                                    _remove_file_safely(tmp_path)

                        # 记录本资产新写进包的密文条目：随包脚本据此把包内**所有**
                        # .dmec 解开，而不是只解信号文件（现场缺口：同一包里视频/音频
                        # /userInfo 仍是密文）。用"新增条目"而非逐分支打点，是为了
                        # 覆盖全部分支、且以后新增分支时不会漏维护这份清单。
                        if encrypted:
                            for _nm in list(zf.NameToInfo)[before_entries:]:
                                if _nm.endswith(".dmec"):
                                    encrypted_entries.append(
                                        {"arcname": _nm, "kind": data_type})
                        size_done += int(asset.file_size or 0)
                    except Exception as e:
                        errors.append({
                            "asset_id": asset.id,
                            "file_name": getattr(asset, "file_name", None),
                            "reason": f"打包失败：{e}",
                        })
                        # 必须留痕：之前这里静默吞异常，界面只报"磁盘丢失或读取失败"，
                        # 实际失败原因（脱敏异常/编解码错误/IO）在日志里完全查不到
                        current_app.logger.error(
                            "导出跳过 asset_id=%s（%s）：%s: %s",
                            asset.id, getattr(asset, "file_name", None),
                            type(e).__name__, e, exc_info=True)

                    processed += 1
                    if progress_callback:
                        try:
                            progress_callback(processed, total_files, size_done, total_size)
                        except Exception:
                            pass

                # 可逆脱敏（脑电/心电/音频）的还原参数随包写出（**不含密钥**，只含每列
                # 精度/噪声幅度/时间平移量、每声道的噪声幅度、原文校验和）。持密钥方
                # 据此可逐字节还原；无密钥方拿到它也无法回推原文。
                # manifest 在「有可还原条目」或「勾了还原包且包内有加密文件」时写。
                # 前者是既有口径（平台侧工具据此还原）；后者是随包脚本用来核对
                # "包里一共有多少密文"。两者都不满足时不写 —— 避免给一个既没有可还原
                # 文件、也没勾还原包的普通加密包凭空塞进一个 json（改变既有交付形态）。
                if signal_manifest_entries or (do_restore_kit and encrypted_entries):
                    zf.writestr(
                        SIGNAL_MANIFEST_NAME,
                        signal_manifest_bytes(build_signal_manifest(
                            signal_manifest_entries,
                            key_scope="pack" if pack_key else None,
                            key=pack_key,
                            encrypted_files=encrypted_entries)))
                # 还原包（离线脚本 + 本包专用密钥 + 说明）在「有可还原文件」或
                # 「包内有加密文件」时写。加密包哪怕只有视频，也必须随包给出主密钥
                # 才能解开 —— 漏掉这一步会让整个包永远打不开（现场缺口）。
                if do_restore_kit and (signal_manifest_entries or encrypted_entries):
                    from app.utils.signal_desensitize import key_fingerprint
                    for kit_name, kit_data in build_kit_files(
                            pack_key, len(signal_manifest_entries),
                            encrypted=bool(encrypted),
                            fingerprint=key_fingerprint(pack_key),
                            pack_master_key=pack_master_key,
                            master_fingerprint=(get_key_fingerprint(pack_master_key)
                                                if pack_master_key else ""),
                            encrypted_count=len(encrypted_entries)):
                        zf.writestr(kit_name, kit_data)
                    current_app.logger.warning(
                        "导出包含还原包：%d 个可还原文件（脑电/心电/音频）的本包专用"
                        "密钥已随包给出（非平台主密钥，单包泄露不外溢）；"
                        "包内加密文件共 %d 个%s",
                        len(signal_manifest_entries), len(encrypted_entries),
                        "，并含本包专用主密钥（可解密包内全部 .dmec）"
                        if pack_master_key else "")
                elif do_restore_kit:
                    current_app.logger.info(
                        "已勾选随包附带还原包，但本次既无脑电/心电/音频脱敏文件、"
                        "也无加密文件，还原包未写入")
        except Exception:
            # zip 构建失败，清理临时文件后重新抛出
            _remove_file_safely(zip_path)
            raise

        if do_userinfo:
            current_app.logger.info(
                "脱敏导出完成：共替换 %d 处受试者身份字段", desensitized_fields)
        if do_audio:
            current_app.logger.info(
                "音频声纹脱敏完成：共处理 %d 个音频文件", audio_desensitized_files)
        if do_video:
            current_app.logger.info(
                "视频人脸脱敏完成：共处理 %d 个视频文件（人脸区不可逆马赛克，音轨已移除）",
                video_desensitized_files)
        if do_eeg or do_ecg:
            current_app.logger.info(
                "信号级脱敏完成：共处理 %d 个 EEG/ECG 文件（全部列保留，"
                "还原参数已写入包内 %s）",
                signal_desensitized_files, SIGNAL_MANIFEST_NAME)

        return zip_path, errors

    def _log_audit(self, assets: List[DataAsset], encrypted: bool, skipped_count: int = 0,
                   desens: dict = None, skipped: list = None):
        """记录导出审计日志（在 send_file 之前 commit）"""
        desens = desens or {}
        skipped = skipped or []
        operator = self._operator_user()
        asset_ids = [a.id for a in assets]
        # 拼接 ID 串（避免日志过长，最多显示前 20 个）
        ids_display = ",".join(str(i) for i in asset_ids[:20])
        if len(asset_ids) > 20:
            ids_display += f",...（共 {len(asset_ids)} 个）"
        total_size = sum(int(a.file_size or 0) for a in assets)
        subject_ids = sorted({a.subject_id for a in assets})
        # 预加载 pseudo_id 用于日志
        subjects = {s.id: s for s in Subject.query.filter(Subject.id.in_(subject_ids)).all()}
        pseudo_ids = [subjects[sid].pseudo_id for sid in subject_ids if sid in subjects]
        # 跳过原因必须进审计：只写"缺失文件"会把脱敏失败、编解码错误等
        # 全部误报成"磁盘丢失"，排查时完全误导
        if skipped:
            reasons = []
            for s in skipped[:3]:
                reasons.append("%s（%s）" % (s.get("file_name") or s.get("asset_id"),
                                             s.get("reason")))
            more = "等 %d 个" % len(skipped) if len(skipped) > 3 else ""
            skipped_note = "，跳过 %d 个文件：%s%s" % (
                len(skipped), "；".join(reasons), more)
        elif skipped_count:
            skipped_note = "，跳过 %d 个文件" % skipped_count
        else:
            skipped_note = ""
        desens_note = "，受试者信息已脱敏" if desens.get("userinfo") else ""
        audio_note = "，音频已声纹脱敏" if desens.get("audio") else ""
        # 视频脱敏是唯一不可逆、且会移除音轨的项，审计必须写明，便于事后追溯
        # "这份导出到底动了什么"
        video_note = ("，视频人脸已脱敏（不可逆马赛克 + 音轨已移除）"
                      if desens.get("video") else "")
        signal_note = ("，脑电/心电已值级脱敏（全列保留，凭密钥可无损还原）"
                       if (desens.get("eeg") or desens.get("ecg")) else "")
        # 还原包是"把可还原性一并交出去"的动作，审计必须留痕：谁在什么时候
        # 把密钥随数据一起发出去了（加密导出还会额外给出本包专用主密钥）
        if desens.get("restore_kit") and (desens.get("eeg") or desens.get("ecg")):
            kit_note = ("，包内附本包专用还原密钥 + 本包专用主密钥"
                        "（接收方开箱即可解密并还原，均非平台主密钥）" if encrypted
                        else "，包内附本包专用还原密钥（开箱即可还原，非平台主密钥）")
        else:
            kit_note = ""
        log_operation(
            "export", "data_asset", ids_display,
            detail=(
                f"导出 {len(assets)} 个数据资产"
                f"（{'加密' if encrypted else '明文'}模式，"
                f"总大小 {total_size / 1024 / 1024:.1f} MB，"
                f"受试者: {', '.join(pseudo_ids[:10])}{'...' if len(pseudo_ids) > 10 else ''}"
                f"{desens_note}"
                f"{audio_note}"
                f"{video_note}"
                f"{signal_note}"
                f"{kit_note}"
                f"{skipped_note}）"
            ),
            operator=operator,
        )
        self._commit()

    def _build_filename(self, encrypted: bool) -> str:
        """构建下载文件名"""
        mode = "encrypted" if encrypted else "plain"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"data_export_{mode}_{timestamp}.zip"


# ==================== 模块级辅助：脱敏导出 ====================

# userInfo 文件识别标识（userInfo.json / userInfo_<伪ID>_<日期>.json 均命中）
_USERINFO_NAME_HINT = "userinfo"


def _is_userinfo_asset(asset) -> bool:
    """判断资产是否为受试者信息文件

    采集端原始文件名（metadata_json.original_filename）优先，规范化文件名兜底 ——
    同一文件在不同导入入口形态不同（浏览器扫描 / 服务端 scanner），两个名字都要看。
    """
    meta = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    for name in (meta.get("original_filename"), asset.file_name):
        if name and _USERINFO_NAME_HINT in str(name).lower():
            return True
    return False


def _is_audio_asset(asset) -> bool:
    """判断资产是否为音频数据（声纹脱敏作用域）

    只按 data_type 判定，**不**按扩展名兜底：
    - 视频（mp4/mkv）内含音轨，同样会泄露受试者声纹，但声纹处理需重编码整条视频
      （代价与音频不在同一量级），故本开关不碰视频。视频音轨改由 `video` 开关
      统一处理，且处理方式是**直接移除音轨**（见 :func:`_is_video_asset`）——
      避免"以为脱敏了实际把原始声纹一起发出去"。
    - 非 audio 类型的 wav（如任务数据里的提示音）不应被动改波形。
    """
    return asset.data_type == DataType.AUDIO


def _is_eeg_asset(asset) -> bool:
    """判断资产是否为脑电数据（信号级脱敏作用域）

    只按 data_type 判定。EEG 目录下的非信号文件同样会被送入脱敏器，
    由脱敏器按列名判定后**拒绝并跳过**（记入 errors），不会静默明文放行。
    """
    return asset.data_type == DataType.EEG


def _is_ecg_asset(asset) -> bool:
    """判断资产是否为心电数据（信号级脱敏作用域）"""
    return asset.data_type == DataType.ECG


def _is_video_asset(asset) -> bool:
    """判断资产是否为视频数据（人脸区域脱敏作用域）

    只按 data_type 判定，**不**再按 video_type 过滤：面部/身体/步态三类视频都可能
    拍到正脸，只处理 video_type == "face" 会漏掉后两类里的身份信息。
    检测不到人脸的帧掩膜留空、不做改动，故放大作用域不会误伤画面。
    """
    return asset.data_type == DataType.VIDEO


def _video_arc_name(file_name: str) -> str:
    """视频脱敏后统一封装为 H.264 MP4，包内文件名同步改为 .mp4

    扩展名与实际容器不一致时，下游按扩展名挑解析器会直接失败
    （例如 .mkv 里装的是 MP4）。对已带 .dmec 后缀的名字（对历史加密导出包再次导出）
    只替换内层扩展名 —— 与信号/音频分支的 .dmec 处理口径保持一致。
    """
    name = file_name or ""
    stem, ext = os.path.splitext(name)
    if ext.lower() == ".dmec":
        inner_stem, inner_ext = os.path.splitext(stem)
        if inner_ext.lower() in (".mp4", ".m4v"):
            return name
        return inner_stem + ".mp4.dmec"
    if ext.lower() in (".mp4", ".m4v"):
        return name
    return stem + ".mp4"


# 统一脱敏配置的子项（与前端 `desensitize` 对象键一致）
_DESENS_ITEMS = ("userinfo", "audio", "video", "eeg", "ecg")


def _resolve_desens_config(payload: dict) -> dict:
    """把新旧两种脱敏载荷收敛为统一配置

    新格式（推荐）::

        desensitize = {
            "enabled": True,     # 总开关；False 时全部子项强制关闭
            "userinfo": True,    # userInfo 文件字段级脱敏
            "audio": True,       # 音频声纹脱敏（定点 PCM 加噪，可逆）
            "video": True,       # 视频人脸区域脱敏（不可逆，音轨移除）
            "eeg": True,         # 脑电信号级脱敏（加噪 + 删绝对时间列）
            "ecg": True,         # 心电信号级脱敏（加噪）
        }

    旧格式（仍兼容，仅在 ``desensitize`` 缺省或非 dict 时生效）::

        desensitized: bool        → userinfo
        audio_desensitize: bool   → audio

    两者都未给时**全部关闭** —— 后端不主动改写数据，避免 API/CLI 直接调用时
    静默脱敏，导致下游拿不到原始数据。
    """
    cfg = {k: False for k in _DESENS_ITEMS}
    cfg["restore_kit"] = False   # 非脱敏变换项：是否随包附带还原脚本与密钥
    raw = payload.get("desensitize")
    if isinstance(raw, dict):
        if bool(raw.get("enabled", True)):
            for k in _DESENS_ITEMS:
                cfg[k] = bool(raw.get(k, False))
            cfg["restore_kit"] = bool(raw.get("restore_kit", False))
        # enabled=False → 保持全部 False（总开关优先）
        return cfg
    # 旧键兼容
    cfg["userinfo"] = bool(payload.get("desensitized", False))
    cfg["audio"] = bool(payload.get("audio_desensitize", False))
    return cfg


def _write_temp_bytes(data: bytes, suffix: str = "") -> str:
    """把字节写入新建临时文件并返回路径（调用方负责删除）"""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="export_tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        _remove_file_safely(path)
        raise
    return path


def _read_text_any_encoding(path) -> str:
    """读取文本文件，依次尝试 utf-8 / utf-8-sig / gbk（采集端导出编码不统一）"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _write_masked_userinfo_temp(src_path) -> Tuple[str, int]:
    """把 userInfo 明文按脱敏规则处理并写入新临时文件

    :return: (临时文件路径, 命中脱敏的字段数)
    """
    text = _read_text_any_encoding(src_path)
    masked, hits = desensitize_userinfo_text(text)
    return _write_temp_bytes(masked.encode("utf-8"), suffix=".json"), hits


# ==================== 异步导出任务管理 ====================

class ExportTaskManager:
    """内存级异步导出任务管理器

    流程：
    1. create_task(payload, operator_id, operator_role) → task_id
       启动后台线程打包，立即返回 task_id
    2. get_task(task_id) → 任务状态/进度
       前端轮询此接口获取实时压缩进度
    3. download_task(task_id) → (zip_path, filename)
       任务完成后前端调用，获取 zip 下载
    4. cleanup_task(task_id)
       下载完成后清理任务和临时文件
    """

    # 任务存活时间（秒），超时自动清理
    TASK_TTL = 3600
    # 已完成任务最大保留数
    MAX_COMPLETED = 20

    def __init__(self):
        self._tasks = {}
        self._lock = threading.Lock()

    def create_task(self, payload: dict, operator_id: int, operator_role: str) -> str:
        """创建异步导出任务，返回 task_id"""
        from flask import current_app as _current_app
        # 捕获真实 app 对象，供后台线程推送应用上下文
        app = _current_app._get_current_object()

        task_id = uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id,
            "status": "pending",
            "percent": 0,
            "status_text": "准备解析导出范围...",
            "total_files": 0,
            "processed_files": 0,
            "total_size": 0,
            "processed_size": 0,
            "zip_path": "",
            "filename": "",
            "skipped_files": 0,   # 被跳过的文件数（磁盘丢失/脱敏失败/打包异常）
            "skipped_detail": [],  # 逐个文件的原因，供前端如实展示（最多 10 条）
            "error": "",
            "created_at": time.time(),
        }
        with self._lock:
            self._tasks[task_id] = task
            self._cleanup_expired_locked()

        t = threading.Thread(
            target=self._run_task,
            args=(task_id, payload, operator_id, operator_role, app),
            daemon=True,
        )
        t.start()
        return task_id

    def _run_task(self, task_id: str, payload: dict, operator_id: int,
                  operator_role: str, app):
        """后台线程执行打包（需在应用上下文中运行）"""
        task = self._tasks.get(task_id)
        if not task:
            return
        # 后台线程需手动推送应用上下文，否则 db.session / current_app 不可用
        with app.app_context():
            try:
                task["status"] = "running"
                task["status_text"] = "正在解析导出范围..."

                svc = ExportService(operator_id=operator_id, operator_role=operator_role)
                encrypted = bool(payload.get("encrypted", True))

                # 先解析资产，获取总数/总大小（结果传给 prepare_export 复用，避免重复查询）
                assets = svc.resolve_assets(payload)
                task["total_files"] = len(assets)
                task["total_size"] = sum(int(a.file_size or 0) for a in assets)
                task["status_text"] = f"开始压缩 {task['total_files']} 个文件..."

                def on_progress(processed, total, size_done, size_total):
                    task["processed_files"] = processed
                    task["processed_size"] = size_done
                    if total > 0:
                        task["percent"] = min(99, int(processed / total * 95))
                    task["status_text"] = (
                        f"正在压缩 {processed}/{total} 个文件"
                        f"（{format_size(size_done)} / {format_size(size_total)}）"
                    )

                zip_path, filename, skipped = svc.prepare_export(
                    payload, progress_callback=on_progress, assets=assets)
                task["zip_path"] = zip_path
                task["filename"] = filename
                task["skipped_files"] = len(skipped)
                task["skipped_detail"] = [
                    {"file_name": s.get("file_name"),
                     "reason": str(s.get("reason"))[:200]}
                    for s in (skipped or [])[:10]
                ]
                task["percent"] = 100
                task["status"] = "success"
                if skipped:
                    task["status_text"] = f"压缩完成（{len(skipped)} 个文件被跳过），准备下载"
                else:
                    task["status_text"] = "压缩完成，准备下载"
            except ValidationError as e:
                # 业务校验错误（超限/未选择等）：消息可直接提示用户
                task["status"] = "failed"
                task["error"] = str(e)
                task["status_text"] = f"打包失败：{e}"
            except Exception as e:
                # 底层 IO/解析等异常：记录完整信息到服务端日志，前端只返回分类化提示
                app.logger.error("导出任务 %s 失败: %s", task_id, e, exc_info=True)
                task["status"] = "failed"
                task["error"] = "导出失败：服务器内部错误，请查看服务端日志"
                task["status_text"] = "导出失败，请稍后重试或联系管理员"
            finally:
                # 释放线程局部的数据库会话
                try:
                    db.session.remove()
                except Exception:
                    pass

    def get_task(self, task_id: str):
        """查询任务进度"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return dict(task)

    def download_task(self, task_id: str):
        """获取任务结果（zip 路径 + 文件名）"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or task["status"] != "success":
                return None
            return task["zip_path"], task["filename"]

    def cleanup_task(self, task_id: str):
        """下载完成后清理任务和临时文件"""
        with self._lock:
            task = self._tasks.pop(task_id, None)
        if task and task.get("zip_path") and os.path.exists(task["zip_path"]):
            _remove_file_safely(task["zip_path"])

    def _cleanup_expired_locked(self):
        """清理过期/超量任务（调用方需持有锁）"""
        now = time.time()
        # 1. 清理超时任务
        expired = [
            tid for tid, t in self._tasks.items()
            if now - t["created_at"] > self.TASK_TTL
        ]
        for tid in expired:
            t = self._tasks.pop(tid, None)
            if t and t.get("zip_path") and os.path.exists(t["zip_path"]):
                _remove_file_safely(t["zip_path"])
        # 2. 限制已完成任务数量
        completed = [
            (tid, t) for tid, t in self._tasks.items()
            if t["status"] in ("success", "failed")
        ]
        if len(completed) > self.MAX_COMPLETED:
            completed.sort(key=lambda x: x[1]["created_at"])
            for tid, t in completed[:len(completed) - self.MAX_COMPLETED]:
                self._tasks.pop(tid, None)
                if t.get("zip_path") and os.path.exists(t["zip_path"]):
                    _remove_file_safely(t["zip_path"])


# ==================== 启动清扫 ====================

def cleanup_orphan_export_zips(logger=None):
    """应用启动时清扫残留的导出临时 zip

    进程被 kill / 崩溃时 TTL 清理不会执行，export_*.zip 会残留在
    系统临时目录（tempfile.gettempdir()），启动时统一删除。
    仅适用于单进程部署：多 worker 下其他 worker 可能正在打包导出文件。
    """
    import glob
    removed = 0
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "export_*.zip")):
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    if removed and logger:
        logger.info("启动清扫：已删除 %d 个残留导出临时文件", removed)
    return removed


def format_size(bytes_val):
    """格式化文件大小"""
    if not bytes_val:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


# 模块级单例
export_task_manager = ExportTaskManager()