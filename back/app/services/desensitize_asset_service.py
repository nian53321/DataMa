# -*- coding: utf-8 -*-
"""数据资产**批量脱敏**服务：先把脱敏产物做好，导出时直接复用

## 与导出脱敏的关系

导出界面已有 5 类脱敏（userinfo / audio / video / eeg / ecg）。本服务的脱敏
**逐模态复用同一批实现**（`desensitize_video_file_parallel` / `desensitize_audio_file` /
`desensitize_eeg_file` / `desensitize_ecg_file` / `desensitize_userinfo_text`），
只是把结果**落盘留档**而不是直接塞进 zip —— 于是同一份数据被多次导出时，
分钟级的视频人脸脱敏只跑一次。

派生口径必须与导出一致（否则"复用"会与"实时处理"产出不同字节）：

- `logical_path = f"{pseudo_id}/{layer}/{data_type}/{file_name}"`（**不含 .dmec**）
  —— 信号/音频的噪声按它派生；两处不一致会导致"复用来的产物"与"实时脱敏"噪声不同
- 输出扩展名跟随实际内容：视频 → `.mp4`、非整数 PCM 音频 → `.wav`

## 密钥作用域

批量脱敏**一律使用平台主密钥**（`use_hmac_key(None)`），产物 `key_scope="platform"`。

导出勾选「随包附带还原包」时改用本包一次性密钥。此时**按模态分别对待**：

- **可逆模态**（音频 / 脑电 / 心电）：产物不复用 —— 还原清单会写明用哪把密钥还原，
  平台密钥口径的产物混进包密钥的包 ⇒ 清单自相矛盾（一部分能还原、一部分不能）
- **不可逆模态**（视频 / 受试者信息）：照旧复用 —— 包内不存在任何可回推原文的参数
  （马赛克是结构性破坏、userinfo 是字段掩码），与密钥作用域无关

分类唯一出口是 `desens_artifact.REVERSIBLE_MODALITIES`；判定见
`export_service._reuse_allowed_for`。

## 安全底线（与导出一致）

- **任一资产脱敏失败都不回退为原始数据**：失败即记入 errors 并跳过
  （宁可没有产物，也不能留下"以为脱敏了"的明文）
- **产物仍是 DMEC 密文**：数据湖不新增明文敏感数据；导出复用只需一次毫秒级解密
"""
import logging
import os
import tempfile
import threading
import time
import uuid

from flask import current_app

from app.extensions import db
from app.models import DataAsset, Subject
from app.models.desensitize_artifact import (
    MODALITY_AUDIO, MODALITY_ECG, MODALITY_EEG,
    MODALITY_LABELS, MODALITY_USERINFO, MODALITY_VIDEO,
)
from app.services.asset_service import _decrypt_for_serving
from app.services.base import BaseService, ValidationError
from app.services.export_service import (
    _asset_modality, _read_text_any_encoding, _video_arc_name,
)
from app.services.subject_service import _remove_file_safely
from app.utils.audit import log_operation
from app.utils.audio_desensitize import (
    VERSION_TAG as AUDIO_VERSION_TAG, desensitize_audio_file,
)
from app.utils.crypto import encrypt_file
from app.utils.desens_artifact import (
    IRREVERSIBLE_MODALITIES, REVERSIBLE_MODALITIES,  # noqa: F401（口径唯一出口，见下方说明）
    artifact_abspath, artifact_relpath, artifact_reuse_blocker,
    artifacts_for_assets, delete_artifact, desens_config_hash, find_artifact,
    prune_empty_artifact_dirs, source_fingerprint, upsert_artifact,
)
from app.utils.desensitize import desensitize_userinfo_text
from app.utils.signal_desensitize import (
    VERSION_TAG as SIGNAL_VERSION_TAG, desensitize_ecg_file, desensitize_eeg_file,
    use_hmac_key,
)
from app.utils import task_store
from app.utils.video_desensitize import (
    DEFAULT_VIDEO_STRENGTH, VERSION_TAG as VIDEO_VERSION_TAG,
    desensitize_video_file_parallel,
)

logger = logging.getLogger(__name__)

# 与导出 `export_service._DESENS_ITEMS` 同集合（顺序也保持一致）
_DESENS_ITEMS = ("userinfo", "audio", "video", "eeg", "ecg")

# 用户信息脱敏的"算法版本"（掩码规则本身由规则指纹跟踪，这里只标实现版本）
USERINFO_VERSION_TAG = "userinfo-mask-v1"

# 不可逆模态（产物没有还原参数、不进 _desens_manifest.json）的定义已移到
# `app.utils.desens_artifact` —— 导出复用也要按它判断「勾了还原包还能不能复用」，
# 两处各写一份必然分叉（已踩过同类坑）。上面 import 处已转发同名常量。
#
# 由 `_manifest_entry` 的实现自然保证"这两类不写还原清单"：它们的 stats 里本就没有
# `manifest` 字段，这里只是把"为什么视频/受试者信息不该有还原清单"写下来，
# 避免后人"顺手补一个"。


def resolve_desens_config(payload: dict) -> dict:
    """批量脱敏配置（与导出 `desensitize` 对象同形，便于前端一套心智模型）

    推荐格式::

        desensitize = {
            "enabled": True,          # 总开关；False 时全部子项强制关闭
            "userinfo": True, "audio": True, "video": True, "eeg": True, "ecg": True,
            "video_strength": "strongest"  # 视频马赛克档位（缺省 = 默认最强档）
        }
        force = True                  # 可选：已有可用产物也重做

    两种写法都接受（便于 CLI 直接调）：顶层 `desensitize` 对象，或直接扁平传子项。

    **缺省全关**（与导出一致）：后端不主动改写数据，避免 API/CLI 误调时静默脱敏。
    """
    raw = payload.get("desensitize")
    if not isinstance(raw, dict):
        raw = payload
    enabled = bool(raw.get("enabled", True))
    cfg = {k: (enabled and bool(raw.get(k, False))) for k in _DESENS_ITEMS}
    strength = raw.get("video_strength") or DEFAULT_VIDEO_STRENGTH
    cfg["video_strength"] = strength
    cfg["force"] = bool(raw.get("force", payload.get("force", False)))
    return cfg


def _modality_of(asset):
    """资产 → 脱敏模态；不参与脱敏的资产返回 None

    **仅作本模块内的短别名** —— 真正的判定在
    `export_service._asset_modality`（唯一出口）。两处各写一份必然分叉：
    一边认成 video、另一边认不出来，复用就永远命中不了，而且**完全静默**。
    """
    return _asset_modality(asset)


class AssetDesensitizeService(BaseService):
    """批量脱敏：计划 → 执行 → 登记产物"""

    # ==================== 查询 ====================

    def existing(self, asset_ids) -> dict:
        """已有产物映射：{asset_id(str): {modality: {...}}}（供列表打标）"""
        mapping = artifacts_for_assets(asset_ids)
        return {
            str(aid): {mod: row.to_dict() for mod, row in mods.items()}
            for aid, mods in mapping.items()
        }

    def plan(self, asset_ids, config) -> list:
        """解析每个资产的处置方式，不产生任何副作用

        每条：``{asset_id, file_name, data_type, layer, subject_pseudo_id, modality,
        modality_label, action, reason, params}``

        action:
          - ``desensitize`` —— 需要（重新）脱敏
          - ``reuse``       —— 已有**可用**产物且配置一致，跳过
          - ``skip``        —— 该资产不该被处理（模态不支持 / 未勾选）
          - ``fail``        —— 前置条件不满足（源文件丢失等），执行时必然失败
        """
        assets = self._resolve_assets(asset_ids)
        storage_root = current_app.config["DATA_LAKE_DIR"]
        subject_ids = {a.subject_id for a in assets}
        subjects = {}
        if subject_ids:
            subjects = {s.id: s for s in Subject.query.filter(
                Subject.id.in_(subject_ids)).all()}
        artifacts = artifacts_for_assets([a.id for a in assets])

        plans = []
        for asset in assets:
            subject = subjects.get(asset.subject_id)
            pseudo_id = (subject.pseudo_id if subject
                         else f"subject_{asset.subject_id}")
            data_type = asset.data_type.value if asset.data_type else "unknown"
            layer = asset.layer.value if asset.layer else "unknown"
            modality = _modality_of(asset)
            item = {
                "asset_id": asset.id,
                "file_name": asset.file_name,
                "data_type": data_type,
                "layer": layer,
                "subject_pseudo_id": pseudo_id,
                "modality": modality,
                "modality_label": MODALITY_LABELS.get(modality or "", "不支持"),
                "action": "skip",
                "reason": "",
                "params": None,
            }

            if modality is None:
                item["reason"] = ("该模态不参与脱敏（仅视频/音频/脑电/心电/受试者信息）")
                plans.append(item)
                continue
            if not config.get(modality):
                item["reason"] = "%s 未勾选" % MODALITY_LABELS[modality]
                plans.append(item)
                continue

            expected_hash = self._config_hash(modality, config)
            item["params"] = {"strength": config.get("video_strength")} \
                if modality == MODALITY_VIDEO else None

            if not asset.file_path:
                item["action"], item["reason"] = "fail", "资产无关联文件"
                plans.append(item)
                continue
            abs_src = os.path.join(storage_root, asset.file_path)
            if not os.path.exists(abs_src):
                item["action"], item["reason"] = "fail", "磁盘文件丢失"
                plans.append(item)
                continue

            existing = (artifacts.get(asset.id) or {}).get(modality)
            blocker = artifact_reuse_blocker(
                existing, storage_root, expected_hash, abs_src)
            if existing is not None and blocker is None and not config.get("force"):
                item["action"], item["reason"] = "reuse", "已是最新脱敏产物，跳过"
                item["artifact"] = existing.to_dict()
            else:
                item["action"] = "desensitize"
                # 有旧产物但不可用 ⇒ 说明为什么重做（前端/日志据此解释）
                item["reason"] = ("" if blocker in (None, "无已存脱敏产物")
                                  else blocker)
                if existing is not None:
                    item["replaces"] = existing.to_dict()
            plans.append(item)
        return plans

    def preview(self, asset_ids, config, max_items: int = 200) -> dict:
        """预览：按 action / 模态聚合 + 明细（不产生副作用、不写审计）"""
        plans = self.plan(asset_ids, config)
        by_action, by_modality = {}, {}
        for p in plans:
            by_action[p["action"]] = by_action.get(p["action"], 0) + 1
            if p["modality"]:
                key = p["modality"]
                by_modality[key] = by_modality.get(key, 0) + 1
        todo = [p for p in plans if p["action"] == "desensitize"]
        return {
            "total": len(plans),
            "by_action": by_action,
            "by_modality": by_modality,
            "to_process": len(todo),
            "reuse_count": by_action.get("reuse", 0),
            "skip_count": by_action.get("skip", 0),
            "fail_count": by_action.get("fail", 0),
            "video_strength": config.get("video_strength"),
            "items": plans[:max_items],
            "truncated": max(0, len(plans) - max_items),
        }

    # ==================== 执行 ====================

    def run(self, asset_ids, config, progress_cb=None, force=None) -> dict:
        """执行批量脱敏（同步；视频很慢，生产走 `DesensitizeTaskManager` 异步）

        :param progress_cb: ``cb(processed, total, current=None, frame_hint=None)``
        :return: ``{total, done, reused, skipped, failed, errors, by_modality}``
        """
        if force is not None:
            config = dict(config, force=force)
        plans = self.plan(asset_ids, config)
        total = len(plans)
        result = {
            "total": total, "done": 0, "reused": 0, "skipped": 0, "failed": 0,
            "by_modality": {}, "errors": [],
        }

        for index, item in enumerate(plans):
            if progress_cb:
                progress_cb(index, total, current=item.get("file_name"))
            if item["action"] == "reuse":
                result["reused"] += 1
                continue
            if item["action"] == "skip":
                result["skipped"] += 1
                continue
            if item["action"] == "fail":
                result["failed"] += 1
                result["errors"].append({
                    "asset_id": item["asset_id"], "file_name": item["file_name"],
                    "reason": item["reason"]})
                continue

            try:
                self._desensitize_one(item["asset_id"], config,
                                      frame_cb=(
                                          (lambda frames, _i=index:
                                           progress_cb(_i, total,
                                                       current=item.get("file_name"),
                                                       frame_hint=frames))
                                          if progress_cb else None))
                result["done"] += 1
                mod = item["modality"]
                result["by_modality"][mod] = result["by_modality"].get(mod, 0) + 1
            except Exception as e:
                # 必须留痕：静默吞异常时界面只会说"部分失败"，原因无处可查
                result["failed"] += 1
                result["errors"].append({
                    "asset_id": item["asset_id"], "file_name": item["file_name"],
                    "reason": "%s: %s" % (type(e).__name__, e)})
                logger.error("批量脱敏失败 asset_id=%s（%s）: %s",
                             item["asset_id"], item["file_name"], e, exc_info=True)
                # 单个资产失败不能污染后续资产的事务
                try:
                    self.session.rollback()
                except Exception:
                    pass

        if progress_cb:
            progress_cb(total, total)
        self._log_audit(plans, config, result)
        return result

    def _desensitize_one(self, asset_id, config, frame_cb=None):
        """单个资产：解密源 → 脱敏 → 加密落盘 → 登记产物

        失败**不留半成品**：临时文件全部在 finally 清理，产物路径只在成功后写入，
        且先写文件后写 DB 记录（崩在中间只会留下无记录的孤儿文件，启动清扫会收掉，
        不可能出现"记录指向不存在/未完成的产物"）。
        """
        asset = DataAsset.query.get(asset_id)
        if asset is None:
            raise ValidationError("数据资产不存在（id=%s）" % asset_id)
        modality = _modality_of(asset)
        if modality is None:
            raise ValidationError("该模态不参与脱敏")

        storage_root = current_app.config["DATA_LAKE_DIR"]
        subject = Subject.query.get(asset.subject_id)
        pseudo_id = subject.pseudo_id if subject else f"subject_{asset.subject_id}"
        layer = asset.layer.value if asset.layer else "unknown"
        data_type = asset.data_type.value if asset.data_type else "unknown"
        file_name = asset.file_name or f"asset_{asset.id}"
        # ⚠️ 派生口径必须与导出完全一致（不含 .dmec）—— 见模块 docstring
        logical_path = f"{pseudo_id}/{layer}/{data_type}/{file_name}"

        abs_src = os.path.join(storage_root, asset.file_path)
        if not os.path.exists(abs_src):
            raise ValidationError("源文件丢失：%s" % asset.file_path)
        source_size, source_mtime = source_fingerprint(abs_src)
        config_hash = self._config_hash(modality, config)
        strength = config.get("video_strength") or DEFAULT_VIDEO_STRENGTH
        suffix = f".{asset.file_format.lower()}" if asset.file_format else ""

        tmp_paths = []
        try:
            plain_path, plain_is_temp = _decrypt_for_serving(abs_src, suffix=suffix)
            if plain_is_temp:
                tmp_paths.append(plain_path)

            out_suffix = self._output_suffix(modality, suffix)
            fd, out_tmp = tempfile.mkstemp(suffix=out_suffix, prefix="desens_")
            os.close(fd)
            tmp_paths.append(out_tmp)

            out_name, manifest_entry, version = self._apply(
                modality, plain_path, out_tmp, pseudo_id, logical_path,
                file_name, strength, frame_cb)

            rel = artifact_relpath(pseudo_id, layer, data_type, out_name)
            dst_abs = artifact_abspath(storage_root, rel)
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)

            # 先清掉旧产物文件：换了扩展名（.mkv → .mp4、mp3 → .wav）时若不删，
            # 会在 `.desensitized/` 里留下一份永远不被引用的孤儿
            old = find_artifact(asset_id, modality)
            if old is not None and old.file_path and old.file_path != rel:
                old_abs = artifact_abspath(storage_root, old.file_path)
                _remove_file_safely(old_abs)
                prune_empty_artifact_dirs(storage_root, os.path.dirname(old_abs))

            # 产物以 DMEC 密文落盘（复用时的额外成本只有一次毫秒级解密）
            encrypt_file(out_tmp, dst_abs)
            file_size = os.path.getsize(dst_abs)

            upsert_artifact(
                asset_id=asset_id, modality=modality, storage_root=storage_root,
                relpath=rel, file_name=out_name,
                file_format=os.path.splitext(out_name)[1].lstrip(".").lower(),
                file_size=file_size, encrypted=True,
                algorithm_version=version, config_hash=config_hash,
                source_size=source_size, source_mtime=source_mtime,
                manifest_entry=manifest_entry,
                operator_id=self.operator_id,
            )
            self._commit()
            logger.info(
                "批量脱敏完成 asset_id=%s（%s）modal=%s 产物=%s（%.1f MB，%s）",
                asset_id, file_name, modality, rel,
                file_size / 1024 / 1024, version)
        except Exception:
            # 失败时清掉可能已写下的产物文件（DB 记录尚未提交，无残留记录）
            try:
                if 'dst_abs' in locals() and os.path.exists(dst_abs):
                    _remove_file_safely(dst_abs)
            except Exception:
                pass
            raise
        finally:
            for path in tmp_paths:
                _remove_file_safely(path)

    def _apply(self, modality, plain_path, out_tmp, pseudo_id, logical_path,
               file_name, strength, frame_cb):
        """按模态分派到对应的脱敏器，返回 (产物文件名, 还原清单条目, 算法版本)"""
        if modality == MODALITY_VIDEO:
            ok, err, stats = desensitize_video_file_parallel(
                plain_path, out_tmp, logger=logger, strength=strength,
                on_progress=frame_cb)
            if not ok:
                raise RuntimeError("视频人脸脱敏未完成（%s），已中止以避免留下明文"
                                   % (err or "未知原因"))
            stats = stats or {}
            return (_video_arc_name(file_name), None,
                    stats.get("version") or VIDEO_VERSION_TAG)

        if modality == MODALITY_AUDIO:
            # use_hmac_key(None) 显式钉住"平台主密钥"作用域：万一本线程此前
            # 正处在某次导出的 use_hmac_key(pack_key) 上下文里，也不会串到包密钥
            with use_hmac_key(None):
                ok, err, stats = desensitize_audio_file(
                    plain_path, out_tmp, pseudo_id, seed_path=logical_path,
                    logger=logger)
            if not ok:
                raise RuntimeError("音频声纹脱敏未完成（%s），已中止以避免留下明文"
                                   % (err or "未知原因"))
            stats = stats or {}
            out_name = file_name
            # 非整数 PCM 源（mp3/m4a/浮点 wav）会被转成 16-bit PCM WAV ⇒
            # 扩展名必须跟着**实际内容**走，否则下游按扩展名挑解析器会直接失败
            if stats.get("canonical"):
                out_name = os.path.splitext(file_name)[0] + ".wav"
            return (out_name, self._manifest_entry(stats),
                    stats.get("version") or AUDIO_VERSION_TAG)

        if modality in (MODALITY_EEG, MODALITY_ECG):
            fn = desensitize_eeg_file if modality == MODALITY_EEG else desensitize_ecg_file
            label = "脑电" if modality == MODALITY_EEG else "心电"
            with use_hmac_key(None):
                ok, err, stats = fn(plain_path, out_tmp, pseudo_id,
                                    seed_path=logical_path, logger=logger)
            if not ok:
                raise RuntimeError("%s信号脱敏未完成（%s），已中止以避免留下明文"
                                   % (label, err or "未知原因"))
            stats = stats or {}
            return (file_name, self._manifest_entry(stats),
                    stats.get("version") or SIGNAL_VERSION_TAG)

        # userinfo：纯文本字段级掩码（不可逆），没有还原参数
        text = _read_text_any_encoding(plain_path)
        masked, hits = desensitize_userinfo_text(text)
        if hits == 0:
            # 与导出口径一致：脱敏规则一条都没命中时明确告警（内容实为明文）
            logger.warning("批量脱敏：%s 未命中任何脱敏字段（请检查脱敏规则是否启用）",
                           file_name)
        with open(out_tmp, "wb") as fh:
            fh.write(masked.encode("utf-8"))
        return (file_name, None, USERINFO_VERSION_TAG)

    @staticmethod
    def _manifest_entry(stats):
        """取还原参数（仅可还原模态）；没有就返回 None（视频/受试者信息）"""
        entry = (stats or {}).get("manifest")
        return dict(entry) if entry else None

    @staticmethod
    def _output_suffix(modality, source_suffix):
        """脱敏产物临时文件的扩展名（影响 ffmpeg 选容器，必须与实际输出格式一致）"""
        if modality == MODALITY_VIDEO:
            return ".mp4"
        if modality == MODALITY_AUDIO:
            return source_suffix or ".wav"
        if modality == MODALITY_USERINFO:
            return ".json"
        return source_suffix or ".csv"

    @staticmethod
    def _config_hash(modality, config):
        return desens_config_hash(
            modality, {"strength": config.get("video_strength")})

    def _resolve_assets(self, asset_ids):
        ids = [int(i) for i in (asset_ids or []) if i is not None]
        if not ids:
            raise ValidationError("未指定任何数据资产")
        assets = DataAsset.query.filter(DataAsset.id.in_(ids)).all()
        if not assets:
            raise ValidationError("未找到指定的数据资产")
        return assets

    def _log_audit(self, plans, config, result):
        """审计：批量脱敏是"提前把数据加工成脱敏形态"，必须留痕"""
        assets_done = [p for p in plans if p["action"] == "desensitize"]
        if not assets_done and not result["reused"]:
            return
        picked = [k for k in _DESENS_ITEMS if config.get(k)]
        note = []
        if picked:
            note.append("模态: %s" % "/".join(MODALITY_LABELS.get(k, k) for k in picked))
        if config.get("video"):
            note.append("视频档位: %s" % config.get("video_strength"))
        if assets_done:
            note.append("新生成 %d 个" % result["done"])
        if result["reused"]:
            note.append("复用已有 %d 个" % result["reused"])
        if result["failed"]:
            note.append("失败 %d 个" % result["failed"])
        log_operation(
            "desensitize", "data_asset",
            ",".join(str(p["asset_id"]) for p in assets_done[:20]) or "-",
            detail="批量脱敏：%s（产物为平台密钥脱敏文件，可被导出直接复用）"
                   % ("，".join(note) or "无可处理资产"),
            operator=self._operator_user(),
        )
        self._commit()

    # ==================== 产物删除 ====================

    def delete(self, asset_id, modality=None) -> int:
        """删除某资产（或某资产某模态）的脱敏产物（记录 + 磁盘文件）"""
        storage_root = current_app.config["DATA_LAKE_DIR"]
        removed = delete_artifact(asset_id, modality, storage_root,
                                 remove_file=True)
        if removed:
            log_operation("desensitize_delete", "data_asset", str(asset_id),
                          detail="删除脱敏产物 %d 份%s" % (
                              removed, "（模态 %s）" % modality if modality else ""),
                          operator=self._operator_user())
        self._commit()
        return removed


# ==================== 异步批量脱敏任务 ====================

class DesensitizeTaskManager:
    """异步批量脱敏任务管理器：状态**跨进程**存 Redis（见 `app/utils/task_store.py`）

    视频脱敏是分钟级，批量跑十几个视频必须异步，否则 HTTP 直接超时。
    进度按**文件数**（不像导出那样按字节）—— 这里每个资产的耗时差异主要来自
    "是否视频"，而界面上已经能看见当前文件是谁。

    ## 为什么状态不能只放进程内存

    这个任务的使用方式就是**提交后用户会离开页面**（关弹窗、切页、刷新）。
    而任务状态原本只活在创建它的进程里，于是：

    - 前端换了 TCP 连接（keepalive 到期/刷新）落到别的 worker → `/progress` 404；
    - `gunicorn max_requests` 让 worker 周期性优雅退出 → 执行线程被连带杀掉。

    因此唯一权威来源是 task_store（Redis 优先、内存降级），保证**离开页面后
    回来仍能读到进度**。

    ## 中断的如实暴露

    执行线程仍在本进程内（daemon），进程若崩溃/被强杀，任务会永远停在 running。
    每次进度写入都会刷新 `updated_at`，`get_task` 对长时间没有心跳的 running
    任务打 `stale` 标记 —— 让前端能提示"可能已中断"，而不是无限转圈。
    """

    # 任务存活时间（秒），超时自动清理（与 task_store.DEFAULT_TTL 一致）
    TASK_TTL = 6 * 3600
    # 进度写入的最小间隔（秒）：视频脱敏每 120 帧回调一次，段级并行时还会聚合上报
    SAVE_MIN_INTERVAL = 0.8
    # running 任务多久没有心跳就判定"可能已中断"（秒）
    STALE_AFTER = 300
    NS = "desens"

    def __init__(self, namespace=None):
        self._ns = namespace or self.NS
        self._save_lock = threading.Lock()
        self._last_save = {}          # task_id -> (saved_at, status)，仅用于节流

    # ---------------- 读写 ----------------

    def _save(self, task, force=False):
        """写任务快照（终态必须 force=True，不能被节流吞掉）"""
        task["updated_at"] = time.time()
        task_id = task.get("task_id")
        if not task_id:
            return
        with self._save_lock:
            last = self._last_save.get(task_id)
            changed = (not last) or (last[1] != task["status"])
            if not force and not changed and (task["updated_at"] - last[0]) < self.SAVE_MIN_INTERVAL:
                return
            self._last_save[task_id] = (task["updated_at"], task["status"])
        task_store.save(self._ns, task, ttl=self.TASK_TTL)

    def get_task(self, task_id):
        """读取任务快照（任意 worker 进程、任意时间都能读到）

        额外补 `idle_seconds` / `stale`：running 但长时间没有心跳 ⇒ 可能已中断。
        """
        task = task_store.load(self._ns, task_id)
        if not task:
            return None
        if task.get("status") in ("pending", "running"):
            last = task.get("updated_at") or task.get("created_at") or 0
            idle = max(0.0, time.time() - float(last))
            task["idle_seconds"] = int(idle)
            task["stale"] = idle > self.STALE_AFTER
        else:
            task["idle_seconds"] = 0
            task["stale"] = False
        return task

    def list_tasks(self, operator_id=None, active_only=False, limit=10):
        """最近的若干任务，供前端在页面加载时"接管"未完成的任务

        :param operator_id: 只要该用户的任务（前端只关心自己的）
        :param active_only: 只要 pending/running
        """
        fetch = limit * 5 if active_only else limit
        rows = task_store.list_recent(self._ns, limit=max(fetch, 1))
        if operator_id is not None:
            rows = [t for t in rows if str(t.get("operator_id")) == str(operator_id)]
        if active_only:
            rows = [t for t in rows if t.get("status") in ("pending", "running")]
        return [self._with_heartbeat(t) for t in rows[:limit]]

    def _with_heartbeat(self, task):
        """给列表里的任务补上同样的心跳字段（与 get_task 同口径）"""
        task = dict(task)
        if task.get("status") in ("pending", "running"):
            last = task.get("updated_at") or task.get("created_at") or 0
            idle = max(0.0, time.time() - float(last))
            task["idle_seconds"] = int(idle)
            task["stale"] = idle > self.STALE_AFTER
        else:
            task["idle_seconds"] = 0
            task["stale"] = False
        return task

    def cleanup_task(self, task_id):
        """清除任务记录（**不**影响已在跑的执行线程）"""
        with self._save_lock:
            self._last_save.pop(task_id, None)
        task_store.remove(self._ns, task_id)

    # ---------------- 创建 ----------------

    def create_task(self, asset_ids, config, operator_id, operator_role) -> str:
        app = current_app._get_current_object()
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        task = {
            "task_id": task_id,
            "status": "pending",
            "percent": 0,
            "status_text": "准备解析待脱敏资产...",
            "total": 0,
            "processed": 0,
            "current": "",
            "frame_hint": None,
            "done": 0,
            "reused": 0,
            "skipped": 0,
            "failed": 0,
            "by_modality": {},
            "errors": [],
            "error": "",
            # 归属人：列表接口按它过滤，避免多用户/多浏览器互相看到对方任务
            "operator_id": operator_id,
            "created_at": now,
            "updated_at": now,
        }
        # 先落库再起线程：前端拿到 task_id 后立刻轮询，不能出现"查不到"的窗口
        self._save(task, force=True)
        threading.Thread(
            target=self._run_task,
            args=(task, asset_ids, config, operator_id, operator_role, app),
            daemon=True,
        ).start()
        return task_id

    def _run_task(self, task, asset_ids, config, operator_id, operator_role, app):
        """执行线程（daemon）。

        它**独占**该 task 字典的写权限，每次改动后经 `_save()` 落到 task_store；
        前台读的是 store 里的快照，所以本进程关了/换了进程，进度依然可读。
        """
        task_id = task["task_id"]
        with app.app_context():
            try:
                task["status"] = "running"
                self._save(task, force=True)
                svc = AssetDesensitizeService(operator_id=operator_id,
                                              operator_role=operator_role)

                def on_progress(processed, total, current=None, frame_hint=None):
                    task["processed"] = processed
                    task["total"] = total
                    if current:
                        task["current"] = current
                    if frame_hint is not None:
                        task["frame_hint"] = frame_hint
                    task["percent"] = (int(processed * 100 / total) if total else 0)
                    label = (("，视频脱敏 %d 帧" % int(frame_hint))
                             if frame_hint else "")
                    task["status_text"] = (
                        "正在处理第 %d/%d 个：%s%s" % (processed + 1, total,
                                                     current or "", label)
                        if processed < total else "正在收尾...")
                    self._save(task)          # 顺带刷新心跳（节流）

                result = svc.run(asset_ids, config, progress_cb=on_progress)
                task["done"] = result["done"]
                task["reused"] = result["reused"]
                task["skipped"] = result["skipped"]
                task["failed"] = result["failed"]
                task["by_modality"] = result["by_modality"]
                task["errors"] = result["errors"][:10]
                task["percent"] = 100
                task["status"] = "success"
                parts = ["新生成 %d 个" % result["done"]]
                if result["reused"]:
                    parts.append("复用 %d 个" % result["reused"])
                if result["skipped"]:
                    parts.append("跳过 %d 个（未勾选或不支持）" % result["skipped"])
                if result["failed"]:
                    parts.append("失败 %d 个" % result["failed"])
                task["status_text"] = "脱敏完成：" + "，".join(parts)
                self._save(task, force=True)
            except ValidationError as e:
                task["status"] = "failed"
                task["error"] = str(e)
                task["status_text"] = "脱敏失败：%s" % e
                self._save(task, force=True)
            except Exception as e:
                app.logger.error("批量脱敏任务 %s 失败: %s", task_id, e, exc_info=True)
                task["status"] = "failed"
                task["error"] = "脱敏失败：服务器内部错误，请查看服务端日志"
                task["status_text"] = "脱敏失败，请稍后重试或联系管理员"
                self._save(task, force=True)
            finally:
                with self._save_lock:
                    self._last_save.pop(task_id, None)
                try:
                    db.session.remove()
                except Exception:
                    pass


desensitize_task_manager = DesensitizeTaskManager()
