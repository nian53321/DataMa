# -*- coding: utf-8 -*-
"""可视化接口：时间轴对齐、单受试者多模态同步展示"""
import os
import io
import csv
import json
import math
import struct
from flask import request, current_app
from flask_jwt_extended import jwt_required

from app.api import visualization_bp
from app.models import Subject, DataAsset, DataType
from app.utils.response import success, fail
from app.utils.time import to_local_str
from app.utils.media_auth import media_auth_required
from app.services.asset_service import _decrypt_for_serving


@visualization_bp.route("/subjects/<int:subject_id>", methods=["GET"])
@jwt_required()
def subject_overview(subject_id):
    """受试者总览面板：基本信息+量表+采集信息"""
    subject = Subject.query.get(subject_id)
    if not subject:
        return fail("受试者不存在", 404)
    assets = DataAsset.query.filter_by(subject_id=subject_id).all()
    return success({
        "subject": subject.to_dict(),
        "modalities": [
            {"id": a.id, "data_type": a.data_type.value, "file_name": a.file_name,
             "file_url": f"/api/data/assets/{a.id}/file",
             "file_format": a.file_format,
             "sample_rate": a.sample_rate, "timestamp_utc": to_local_str(a.timestamp_utc)}
            for a in assets
        ],
    })


@visualization_bp.route("/align", methods=["POST"])
@jwt_required()
def align_modalities():
    """多模态时间轴对齐（以采集触发信号为基准）"""
    data = request.get_json(silent=True) or {}
    subject_id = data.get("subject_id")
    anchor = data.get("anchor", "trigger")  # trigger / utc
    if not subject_id:
        return fail("受试者ID必填", 422)
    # TODO: 调用对齐引擎计算时钟偏差、插值重采样
    return success({"subject_id": subject_id, "anchor": anchor, "status": "queued"}, message="对齐任务已提交")


@visualization_bp.route("/timeline/<int:subject_id>", methods=["GET"])
@jwt_required()
def timeline(subject_id):
    """统一时间轴数据（10ms 粒度同步序列 + 事件锚点）"""
    # TODO: 返回对齐后的多模态同步序列
    assets = DataAsset.query.filter_by(subject_id=subject_id).all()
    return success({
        "subject_id": subject_id,
        "granularity_ms": 10,
        "tracks": [
            {"id": a.id, "data_type": a.data_type.value, "file_name": a.file_name,
             "file_url": f"/api/data/assets/{a.id}/file",
             "file_format": a.file_format,
             "offset_ms": a.align_offset_ms or 0,
             # 附带元数据（含 orbbec 深度视频信息），供前端识别深度视频
             "metadata": a.metadata_json or {}}
            for a in assets
        ],
    })


def _get_asset_file_path(asset):
    """获取数据资产的可读文件路径（自动处理加密文件解密）
    返回 (file_path, tmp_path_or_none)：tmp_path 非空时调用方需负责删除

    复用 asset_service._decrypt_for_serving 统一解密包装器，避免重复实现。
    """
    if not asset.file_path:
        return None, None
    storage_root = current_app.config["DATA_LAKE_DIR"]
    abs_path = os.path.join(storage_root, asset.file_path)
    # 防护：路径为目录或不存在时返回 None（避免 open() 目录触发系统错误）
    if not os.path.isfile(abs_path):
        return None, None
    ext = os.path.splitext(asset.file_name)[1] or ".bin"
    path, is_temp = _decrypt_for_serving(abs_path, suffix=ext)
    return path, (path if is_temp else None)


def _read_asset_text(asset):
    """读取数据资产文件文本内容（自动处理加密文件解密）
    返回 (text, tmp_path_or_none)：tmp_path 非空时调用方需负责删除
    """
    file_path, tmp_path = _get_asset_file_path(asset)
    if file_path is None:
        return None, None
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return text, tmp_path


def _parse_eeg_edf(asset):
    """解析 EDF/EDF+ 格式脑电文件（纯 Python 实现，无需第三方库）
    EDF 格式：256字节固定头 + 每信号256字节 + 数据记录
    支持 EDF+（含时间戳通道）和普通 EDF。
    """
    file_path, tmp_path = _get_asset_file_path(asset)
    if file_path is None:
        return fail("EDF 文件不存在于存储目录", 404)
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        current_app.logger.warning("EDF 文件读取失败: %s", e)
        return fail("EDF 文件读取失败，文件可能被占用或已损坏", 422)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if len(raw) < 256:
        return fail("EDF 文件头不完整（<256字节）", 422)

    try:
        # 解析固定头部（256字节）
        version = raw[0:8].decode("ascii", errors="replace").strip()
        patient_id = raw[8:88].decode("ascii", errors="replace").strip()
        recording_id = raw[88:168].decode("ascii", errors="replace").strip()
        start_date = raw[168:176].decode("ascii", errors="replace").strip()
        start_time = raw[176:184].decode("ascii", errors="replace").strip()
        header_bytes = int(raw[184:192].decode("ascii", errors="replace").strip())
        num_records = int(raw[236:244].decode("ascii", errors="replace").strip())
        record_duration = float(raw[244:252].decode("ascii", errors="replace").strip())
        num_signals = int(raw[252:256].decode("ascii", errors="replace").strip())

        if num_signals <= 0 or num_signals > 256:
            return fail(f"EDF 信号数异常: {num_signals}", 422)
        if num_records <= 0:
            return fail("EDF 数据记录数为 0", 422)

        # 解析每个信号的头部（每信号256字节）
        offset = 256
        labels = []
        phys_dims = []
        phys_mins = []
        phys_maxs = []
        dig_mins = []
        dig_maxs = []
        samples_per_record = []

        for i in range(num_signals):
            base = offset + i * 256
            labels.append(raw[base:base + 16].decode("ascii", errors="replace").strip())
            phys_dims.append(raw[base + 16:base + 24].decode("ascii", errors="replace").strip())
            phys_mins.append(float(raw[base + 56:base + 64].decode("ascii", errors="replace").strip()))
            phys_maxs.append(float(raw[base + 64:base + 72].decode("ascii", errors="replace").strip()))
            dig_mins.append(int(raw[base + 72:base + 80].decode("ascii", errors="replace").strip()))
            dig_maxs.append(int(raw[base + 80:base + 88].decode("ascii", errors="replace").strip()))
            samples_per_record.append(int(raw[base + 104:base + 112].decode("ascii", errors="replace").strip()))

        # 过滤掉 EDF+ 的时间戳通道（标签以 "EDF Annotations" 开头）
        data_channels = []
        for i in range(num_signals):
            if "EDF Annotations" not in labels[i] and samples_per_record[i] > 0:
                data_channels.append(i)

        if not data_channels:
            return fail("EDF 文件未找到有效数据通道", 422)

        # 采样率 = samples_per_record / record_duration
        ch0 = data_channels[0]
        sample_rate = round(samples_per_record[ch0] / record_duration) if record_duration > 0 else 500
        duration_sec = round(num_records * record_duration, 2)

        # 解析数据记录
        # 每个记录包含所有信号，按信号顺序排列
        data_offset = header_bytes
        total_samples_per_record = sum(samples_per_record)

        # 降采样目标
        max_points = 1500
        total_samples = num_records * samples_per_record[ch0]
        step = max(1, total_samples // max_points)

        # 预计算每个通道的数据点索引
        channels = []
        for ci in data_channels:
            spr = samples_per_record[ci]
            # 该通道在每条记录中的起始偏移
            ch_offset_in_record = sum(samples_per_record[j] for j in range(ci))
            # 物理值转换
            p_min = phys_mins[ci]
            p_max = phys_maxs[ci]
            d_min = dig_mins[ci]
            d_max = dig_maxs[ci]
            scale = (p_max - p_min) / (d_max - d_min) if d_max != d_min else 1.0

            downsampled = []
            for rec_idx in range(num_records):
                rec_start = data_offset + rec_idx * total_samples_per_record * 2 + ch_offset_in_record * 2
                # 该通道在此记录中的数据
                for s in range(spr):
                    global_sample = rec_idx * spr + s
                    if global_sample % step != 0:
                        continue
                    byte_pos = rec_start + s * 2
                    if byte_pos + 2 > len(raw):
                        break
                    dig_val = struct.unpack("<h", raw[byte_pos:byte_pos + 2])[0]
                    phys_val = dig_val * scale + p_min
                    downsampled.append(round(phys_val, 2))

            label = labels[ci] or f"Channel {ci}"
            channels.append({"name": label, "data": downsampled})

        return success({
            "meta": {
                "sampleRate": sample_rate,
                "duration": duration_sec,
                "channels": len(channels),
                "totalSamples": total_samples,
                "device": f"EDF ({len(channels)}ch)",
            },
            "channels": channels,
        })
    except (ValueError, struct.error, IndexError) as e:
        current_app.logger.warning("EDF 解析失败: %s", e)
        return fail("EDF 文件解析失败，格式可能不符合 EDF 规范", 422)


def _parse_eeg_csv(text):
    """解析 OpenBCI 风格 CSV 脑电数据
    格式：Sample Index,EXG Channel 0..15,Timestamp
    相同时间戳的连续行属于同一秒数据，自适应计算采样率。
    返回 16 通道波形数据（降采样到便于绘制的规模）。
    """
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return fail("CSV 文件为空", 422)
    header = [h.strip() for h in rows[0]]

    # 定位通道列和时间戳列
    channel_indices = []  # [(col_idx, channel_name), ...]
    ts_idx = None
    for i, h in enumerate(header):
        if h.startswith("EXG Channel"):
            channel_indices.append((i, h))
        elif h == "Timestamp":
            ts_idx = i

    if not channel_indices:
        return fail("CSV 未找到 EXG Channel 列", 422)

    num_channels = len(channel_indices)

    # 解析数据行：收集每行各通道值和时间戳
    # 按时间戳分组：相同时间戳的连续行 = 1 秒数据
    channel_data = [[] for _ in range(num_channels)]  # 每个通道的完整数据
    timestamps = []
    for row in rows[1:]:
        if not row:
            continue
        try:
            for ci, (col_idx, _) in enumerate(channel_indices):
                if col_idx < len(row):
                    channel_data[ci].append(float(row[col_idx]))
                else:
                    channel_data[ci].append(0.0)
            if ts_idx is not None and ts_idx < len(row):
                timestamps.append(float(row[ts_idx]))
        except (ValueError, IndexError):
            continue

    total = len(channel_data[0]) if channel_data else 0
    if total == 0:
        return fail("CSV 无有效数据行", 422)

    # 自适应计算采样率：从时间戳实际跨度推算
    if len(timestamps) >= 2:
        duration = timestamps[-1] - timestamps[0]
        if duration > 0:
            sample_rate = round(total / duration)
            duration_sec = round(duration, 2)
        else:
            sample_rate = 500
            duration_sec = round(total / sample_rate, 2)
    else:
        # 无时间戳列，fallback
        sample_rate = 500
        duration_sec = round(total / sample_rate, 2)

    # 降采样：每通道最多保留 1500 点（便于前端绘制）
    max_points = 1500
    step = max(1, total // max_points)

    channels = []
    for ci, (_, ch_name) in enumerate(channel_indices):
        downsampled = [round(channel_data[ci][i], 2) for i in range(0, total, step)]
        channels.append({"name": ch_name, "data": downsampled})

    return success({
        "meta": {
            "sampleRate": sample_rate,
            "duration": duration_sec,
            "channels": num_channels,
            "totalSamples": total,
            "device": f"OpenBCI ({num_channels}ch)",
        },
        "channels": channels,
    })


def _parse_eeg_json(text):
    """解析 BrainLink 风格 JSON 脑电数据，转换为通道波形格式"""
    data = json.loads(text)
    samples = data.get("samples", [])
    # BrainLink 只有 1 通道原始波，转换为单通道
    raw_all = []
    for s in samples[:10]:
        for v in s.get("raw", []):
            raw_all.append(v)
    raw_down = raw_all[::2]
    raw_uv = [round(v * (1.8 / 4096) / 2000 * 1e6, 2) for v in raw_down]
    return success({
        "meta": {
            "sampleRate": data.get("rawSampleRateHz", 512),
            "duration": data.get("durationSeconds", 0),
            "channels": 1,
            "totalSamples": len(raw_uv),
            "device": "BrainLink Pro",
        },
        "channels": [{"name": "FP1", "data": raw_uv}],
    })


@visualization_bp.route("/eeg-asset/<int:asset_id>", methods=["GET"])
@jwt_required()
def eeg_asset_parse(asset_id):
    """解析指定数据资产的脑电文件内容并返回多通道波形数据
    支持：
    - CSV 格式（OpenBCI 风格：Sample Index,EXG Channel 0..15,Timestamp）
    - JSON 格式（BrainLink 风格）
    - EDF/EDF+ 格式（标准脑电二进制格式，纯Python解析）
    自动处理加密文件解密，自适应计算采样率。
    """
    asset = DataAsset.query.get(asset_id)
    if not asset:
        return fail("数据资产不存在", 404)
    if asset.data_type != DataType.EEG:
        return fail("该资产非脑电类型", 422)
    if not asset.file_path:
        return fail("该资产无关联文件，请重新上传脑电文件", 404)

    fmt = (asset.file_format or "").lower()

    # EDF 是二进制格式，需要单独处理
    if fmt in ("edf", "edf+", "bdf"):
        return _parse_eeg_edf(asset)

    text, tmp_path = _read_asset_text(asset)
    if text is None:
        return fail("文件不存在于存储目录，请检查文件是否已正确上传", 404)

    try:
        # 按内容嗅探格式
        stripped = text.lstrip()
        if fmt == "csv" or stripped.startswith("Sample Index"):
            return _parse_eeg_csv(text)
        elif fmt == "json" or stripped.startswith("{"):
            return _parse_eeg_json(text)
        else:
            # 兜底：按首字符嗅探
            if stripped.startswith("{"):
                return _parse_eeg_json(text)
            # 尝试当作 CSV 解析
            if "," in text.split("\n")[0]:
                return _parse_eeg_csv(text)
            return fail(f"不支持的脑电文件格式: {fmt or '未知'}，支持 CSV/JSON/EDF", 422)
    except (json.JSONDecodeError, ValueError, IndexError) as e:
        current_app.logger.warning("脑电文件解析失败: %s", e)
        return fail("脑电文件解析失败，文件格式不受支持或已损坏", 422)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@visualization_bp.route("/ecg-asset/<int:asset_id>", methods=["GET"])
@jwt_required()
def ecg_asset_parse(asset_id):
    """解析指定数据资产的心电文件内容并返回单通道波形数据
    支持 CSV 格式（sample_index,time_sec,source,frame,value）。
    自动处理加密文件解密，自适应计算采样率。
    """
    asset = DataAsset.query.get(asset_id)
    if not asset:
        return fail("数据资产不存在", 404)
    if asset.data_type != DataType.ECG:
        return fail("该资产非心电类型", 422)
    if not asset.file_path:
        return fail("该资产无关联文件", 404)

    text, tmp_path = _read_asset_text(asset)
    if text is None:
        return fail("文件不存在于存储目录", 404)

    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return fail("CSV 文件为空", 422)

        header = [h.strip().lower() for h in rows[0]]
        # 查找 value 列和 time 列
        value_col = None
        time_col = None
        for i, h in enumerate(header):
            if h in ("value", "ecg", "voltage", "mv"):
                value_col = i
            elif h in ("time_sec", "time", "timestamp", "t"):
                time_col = i
        # 兜底：如果没找到 value 列，取最后一列
        if value_col is None:
            value_col = len(header) - 1

        data_values = []
        time_values = []
        for row in rows[1:]:
            if len(row) <= value_col:
                continue
            try:
                data_values.append(float(row[value_col]))
                if time_col is not None and time_col < len(row):
                    time_values.append(float(row[time_col]))
            except (ValueError, IndexError):
                continue

        total = len(data_values)
        if total == 0:
            return fail("CSV 无有效数据行", 422)

        # 降采样到最多 2000 点
        max_points = 2000
        step = max(1, total // max_points)
        downsampled = [round(data_values[i], 2) for i in range(0, total, step)]

        # 计算采样率和时长
        sample_rate = 0
        duration_sec = 0
        if len(time_values) >= 2:
            dt = time_values[-1] - time_values[0]
            duration_sec = round(dt, 2)
            if dt > 0:
                sample_rate = round(total / dt)
        elif total > 1:
            sample_rate = 250  # 默认假设

        return success({
            "meta": {
                "channels": 1,
                "sampleRate": sample_rate,
                "duration": duration_sec,
                "device": "CSV",
                "points": len(downsampled),
            },
            "data": downsampled,
        })
    except (ValueError, IndexError) as e:
        current_app.logger.warning("心电文件解析失败: %s", e)
        return fail("心电文件解析失败，文件格式不受支持或已损坏", 422)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@visualization_bp.route("/eye-asset/<int:asset_id>", methods=["GET"])
@jwt_required()
def eye_asset_parse(asset_id):
    """解析眼动评估数据资产，返回关键指标用于可视化

    优先从 metadata_json 读取（扫描导入时已解析），
    若无则解密文件后用 load_sync_data 解析。
    返回：风险指数、能力值、眼跳统计、图片回忆统计等
    """
    asset = DataAsset.query.get(asset_id)
    if not asset:
        return fail("数据资产不存在", 404)
    if asset.data_type != DataType.EYE_TRACKING:
        return fail("该资产非眼动类型", 422)

    # 优先从 metadata_json 读取（扫描导入时已解析 sync_data 字段）
    data = asset.metadata_json
    if not data or not isinstance(data, dict) or "risk_value" not in data:
        # 回退：解密文件后解析
        if not asset.file_path:
            return fail("该资产无关联文件，请重新上传", 404)
        text, tmp_path = _read_asset_text(asset)
        if text is None:
            return fail("文件不存在于存储目录", 404)
        try:
            from app.utils.eye_tracking_adapter import load_sync_data
            import tempfile
            fd, tmp_json = tempfile.mkstemp(suffix=".json")
            try:
                os.write(fd, text.encode("utf-8"))
                os.close(fd)
                data = load_sync_data(tmp_json)
            finally:
                try:
                    os.remove(tmp_json)
                except OSError:
                    pass
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    if not data:
        return fail("眼动数据解析失败", 422)

    # 提取关键指标用于可视化
    result = {
        "risk_value": data.get("risk_value"),
        "risk_proportion": data.get("risk_proportion"),
        "age_rang": data.get("age_rang"),
        "capacity_values": {
            "注意力": data.get("fir_capacity_value"),
            "执行力": data.get("sec_capacity_value"),
            "记忆力": data.get("third_capacity_value"),
            "言语理解": data.get("fourth_capacity_value"),
            "MoCA数理": data.get("fifth_capacity_value"),
        },
        "eye_jump": data.get("fir_statistics"),
        "image_recall": data.get("sec_statistics"),
        "custom_name": data.get("custom_name"),
        "estimate_time": data.get("estimate_time"),
        "estimate_num": data.get("estimate_num"),
    }
    return success(result)


@visualization_bp.route("/scale-asset/<int:asset_id>", methods=["GET"])
@jwt_required()
def scale_asset_parse(asset_id):
    """解析量表数据资产，返回得分摘要用于可视化

    优先从 metadata_json 读取（扫描导入/上传时已解析），
    若无则解密文件后用 load_scale_data 解析。
    支持多种量表（MoCA/MMSE/AD8 等），返回量表名称、总分与分项得分。
    """
    asset = DataAsset.query.get(asset_id)
    if not asset:
        return fail("数据资产不存在", 404)
    if asset.data_type != DataType.SCALE:
        return fail("该资产非量表类型", 422)

    # 优先从 metadata_json 读取（扫描导入/上传时已解析，含 raw + summary）
    meta = asset.metadata_json
    if meta and isinstance(meta, dict) and "summary" in meta:
        # 导入时已解析（含 raw + summary）
        return success({
            "summary": meta["summary"],
            "raw": meta.get("raw"),
        })

    # 回退：解密文件后解析
    if not asset.file_path:
        return fail("该资产无关联文件，请重新上传", 404)
    text, tmp_path = _read_asset_text(asset)
    if text is None:
        return fail("文件不存在于存储目录", 404)
    try:
        from app.utils.scale_adapter import (
            load_scale_data, parse_scale_summary, detect_scale_type,
        )
        import tempfile
        fd, tmp_json = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, text.encode("utf-8"))
            os.close(fd)
            raw = load_scale_data(tmp_json)
        finally:
            try:
                os.remove(tmp_json)
            except OSError:
                pass

        if raw is None:
            return fail("量表数据解析失败", 422)

        result = {"raw": raw}
        # 统一量表摘要（MoCA/MMSE/AD8 等）
        summary = parse_scale_summary(raw, scale_type=detect_scale_type(asset.file_name or ""))
        if summary:
            result["summary"] = summary
        return success(result)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _probe_video_streams(file_path):
    """ffprobe 探测视频轨，返回流列表"""
    import subprocess
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=index,codec_name,width,height,pix_fmt",
         "-of", "json", file_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return (json.loads(p.stdout) or {}).get("streams", [])
    except Exception:
        return []


def _is_depth_stream(s):
    """判断视频流是否为深度轨（排除彩色/IR 轨）

    兼容两类录制产物：
    - 旧方案（pyk4a/k4arecorder）：深度轨 rawvideo（gray16be / rgb555le）；
    - 新方案（orbbec_camera_proc 双 ffmpeg）：深度轨 ffv1 无损（gray16le/gray16be）。
    """
    codec = (s.get("codec_name") or "").lower()
    pix_fmt = (s.get("pix_fmt") or "").lower()
    if codec == "rawvideo":
        return True
    if codec == "ffv1" and pix_fmt in ("gray16le", "gray16be"):
        return True
    return False


def _read_depth_frames(file_path, stream_index, w, h, step=1, src_pix_fmt=""):
    """生成器：提取深度帧，yield 每帧 float32 二维数组（无效像素=0）

    step 为取样步长（select 滤镜每 step 帧取 1 帧），第一遍扫描用大步长提速。

    深度数据来源两类：
    - realsense 新方案：zstd 无损压缩序列（.zst，格式见 realsense_child.py _zstd_writer）：
      直接解压逐帧 yield，16 位精度原样保留；
    - 深度轨视频（orbbec mkv 等）：ffmpeg 提取，pix_fmt 因录制器而异：
      rgb555le（16bit 小端，bit15=无效标志，低13位为毫米值）必须按原格式透传，
      gray16be/gray16le 统一输出 gray16be（16bit→16bit 仅换字节序，数值不变）。
    """
    import numpy as np
    import struct

    if (file_path or "").lower().endswith(".zst"):
        try:
            import zstandard as _zstd
        except ImportError:
            return
        dctx = _zstd.ZstdDecompressor()
        with open(file_path, "rb") as f:
            if f.read(4) != b"DZST":
                return
            _ver, fw, fh, _count = struct.unpack("<IIII", f.read(16))
            idx = 0
            while True:
                hdr = f.read(4)
                if not hdr or len(hdr) < 4:
                    break
                (clen,) = struct.unpack("<I", hdr)
                blk = f.read(clen)
                if len(blk) < clen:
                    break
                raw = dctx.decompress(blk, max_output_size=fw * fh * 2)
                idx += 1
                if step and idx % step != 0:
                    continue
                frame = np.frombuffer(raw, dtype="<u2").reshape(fh, fw).astype(np.float32)
                yield frame
        return

    import subprocess
    frame_bytes = w * h * 2
    is_555 = "555" in (src_pix_fmt or "")
    out_fmt = "rgb555le" if is_555 else "gray16be"
    dtype = "<u2" if is_555 else ">u2"
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-i", file_path, "-map", f"0:{stream_index}",
         "-vf", f"select='not(mod(n,{step}))'",
         "-f", "rawvideo", "-pix_fmt", out_fmt, "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        buf = b""
        while True:
            chunk = proc.stdout.read(1024 * 1024)
            if not chunk:
                break
            buf += chunk
            while len(buf) >= frame_bytes:
                raw = np.frombuffer(buf[:frame_bytes], dtype=dtype).reshape(h, w)
                if is_555:
                    # rgb555le：bit15=1 为无效像素（深度不可信），低 13 位为毫米值
                    frame = (raw & 0x1FFF).astype(np.float32)
                    frame[(raw & 0x8000) != 0] = 0
                else:
                    frame = raw.astype(np.float32)
                yield frame
                buf = buf[frame_bytes:]
    finally:
        proc.stdout.close()
        proc.wait(timeout=30)


def _transcode_depth_video(file_path, stream_index, w, h, fps, out_path, src_pix_fmt=""):
    """把 mkv 深度轨转码为伪彩色 MP4（H.264），像彩色视频一样可逐帧播放

    两遍处理：第一遍下采样扫描全局深度范围（1%~99% 分位，排除 0 无效值），
    第二遍逐帧归一化 + jet 伪彩色写入 H.264。

    编码方式：
    - 优先 ffmpeg 管道直接编码 libx264（浏览器可播，+faststart 秒开）。
      opencv-python-headless 在部分环境（如 python:slim 镜像）不带 MPEG-4
      Part 2(mp4v) 编码器，cv2.VideoWriter 打不开，因此主链路不再依赖它；
    - 回退：cv2.mp4v 中间文件 + ffmpeg 转 H.264（兼容本地/桌面环境）。
    """
    import subprocess
    import numpy as np
    import cv2

    # 第一遍：扫描全局深度范围
    vals = []
    for arr in _read_depth_frames(file_path, stream_index, w, h, step=30, src_pix_fmt=src_pix_fmt):
        v = arr[arr > 0]
        if v.size:
            vals.append(v)
    if not vals:
        return False
    all_v = np.concatenate(vals)
    lo = float(np.percentile(all_v, 1))
    hi = float(np.percentile(all_v, 99))
    span = (hi - lo) or 1.0

    # 伪彩色帧生成器（第二遍逐帧归一化 + jet 伪彩色，无效像素置深灰）
    def frames():
        for arr in _read_depth_frames(file_path, stream_index, w, h, step=1, src_pix_fmt=src_pix_fmt):
            norm = np.zeros((h, w), dtype=np.uint8)
            mask = arr > 0
            norm[mask] = np.clip(((arr[mask] - lo) / span * 255), 0, 255).astype(np.uint8)
            color = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
            color[~mask] = (16, 16, 16)  # 无效像素置深灰
            yield color

    # 优先：ffmpeg 管道直接编码 H.264（libx264），写临时文件成功后原子 rename 到缓存
    # 临时文件保留 .mp4 扩展名并显式 -f mp4，避免 ffmpeg 无法根据扩展名推断输出格式
    tmp_h264 = out_path.rsplit(".", 1)[0] + ".tmp.mp4"
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-preset", "medium", "-crf", "23",
         "-movflags", "+faststart", "-f", "mp4", "-an", tmp_h264],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        for color in frames():
            p.stdin.write(color.tobytes())
    except BrokenPipeError:
        pass
    try:
        p.stdin.close()
    except Exception:
        pass
    try:
        _out, _err = p.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        p.kill()
        _out, _err = p.communicate()
    if p.returncode == 0 and os.path.isfile(tmp_h264) and os.path.getsize(tmp_h264) > 0:
        os.replace(tmp_h264, out_path)
        return True
    current_app.logger.warning("深度转码 ffmpeg 管道失败 rc=%s: %s",
                               p.returncode, _err[-500:].decode("utf-8", "replace"))
    try:
        os.remove(tmp_h264)
    except OSError:
        pass

    # 回退：cv2.mp4v 中间文件 + ffmpeg 转 H.264
    import tempfile as _tf
    _fd, tmp_raw = _tf.mkstemp(suffix=".tmp.mp4", dir=os.path.dirname(out_path))
    os.close(_fd)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(tmp_raw, fourcc, float(fps), (w, h))
    if writer.isOpened():
        try:
            for color in frames():
                writer.write(color)
        finally:
            writer.release()
        p2 = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_raw,
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-preset", "medium", "-crf", "23",
             "-movflags", "+faststart", "-f", "mp4", "-an", tmp_h264],
            capture_output=True, timeout=600,
        )
        if p2.returncode == 0 and os.path.isfile(tmp_h264) and os.path.getsize(tmp_h264) > 0:
            os.replace(tmp_h264, out_path)
            try:
                os.remove(tmp_raw)
            except OSError:
                pass
            return True
        current_app.logger.warning("深度转码 cv2 回退失败 rc=%s: %s", p2.returncode,
                                   p2.stderr[-500:].decode("utf-8", "replace"))
        try:
            os.remove(tmp_h264)
        except OSError:
            pass
    try:
        os.remove(tmp_raw)
    except OSError:
        pass
    return False


@visualization_bp.route("/depth-video/<int:asset_id>", methods=["GET"])
@media_auth_required("depth_video", resource_key="asset_id")
def depth_video(asset_id):
    """深度视频播放：把深度数据转码为伪彩色 MP4，浏览器 <video> 直接播放

    鉴权：优先 ?media_token=<短期签名>，兼容 JWT（header / access_token query）。
    深度数据源两类：
    - 资产本身是深度轨视频（orbbec mkv 等）：直接探测深度轨转码；
    - 资产是 realsense 彩色视频（无深度轨）：通过 metadata.realsense.depth_asset_id
      定位原始深度序列（.zst）再转码，实现"点一下从深度通道转换"。
    首次请求触发转码（耗时较长），结果缓存到临时目录，后续秒开。
    自动处理加密文件解密。
    """
    import tempfile
    from flask import send_file

    asset = DataAsset.query.get(asset_id)
    if not asset:
        return fail("数据资产不存在", 404)

    file_path, tmp_path = _get_asset_file_path(asset)
    if file_path is None:
        return fail("文件不存在于存储目录", 404)
    try:
        src_asset = asset
        is_zst = (file_path or "").lower().endswith(".zst")
        depth_streams = []
        while not is_zst:
            streams = _probe_video_streams(file_path)
            depth_streams = [s for s in streams if _is_depth_stream(s)]
            if depth_streams:
                break
            # 无深度轨：realsense 彩色资产通过 metadata 定位原始深度资产
            ameta = src_asset.metadata_json or {}
            depth_asset_id = (ameta.get("realsense") or {}).get("depth_asset_id")
            if not depth_asset_id:
                return fail("非深度视频（无深度轨）", 404)
            dasset = DataAsset.query.get(depth_asset_id)
            if not dasset:
                return fail("深度资产不存在", 404)
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            src_asset = dasset
            file_path, tmp_path = _get_asset_file_path(dasset)
            if file_path is None:
                return fail("文件不存在于存储目录", 404)
            is_zst = (file_path or "").lower().endswith(".zst")

        if is_zst:
            with open(file_path, "rb") as f:
                if f.read(4) != b"DZST":
                    return fail("深度数据格式错误", 422)
                _ver, w, h, _cnt = struct.unpack("<IIII", f.read(16))
            fps = 30.0
            rmeta = (src_asset.metadata_json or {}).get("realsense") or {}
            try:
                fps = float(rmeta.get("fps") or 30.0) or 30.0
            except (TypeError, ValueError):
                pass
            stream_index = -1
            src_pix_fmt = "zstd"
        else:
            d = depth_streams[0]
            try:
                w, h = int(d["width"]), int(d["height"])
            except (KeyError, TypeError, ValueError):
                return fail("深度轨分辨率未知", 422)
            fps = 30.0
            try:
                num, den = d.get("r_frame_rate", "30/1").split("/")
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            except Exception:
                pass
            stream_index = d["index"]
            src_pix_fmt = d.get("pix_fmt", "")

        # 转码结果缓存（按资产 ID），避免每次播放都重新转码
        # v2：修复 rgb555le 深度轨被色彩转换破坏的 bug；
        # v3：改用 H.264 编码（mp4v/MPEG-4 Part 2 浏览器不支持，会触发 video error）
        cache = os.path.join(tempfile.gettempdir(), f"depth_video_v3_{asset_id}.mp4")
        for stale_name in (f"depth_video_{asset_id}.mp4", f"depth_video_v2_{asset_id}.mp4"):
            stale = os.path.join(tempfile.gettempdir(), stale_name)
            if os.path.isfile(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass
        if not (os.path.isfile(cache) and os.path.getsize(cache) > 0):
            ok = _transcode_depth_video(file_path, stream_index, w, h, fps, cache,
                                        src_pix_fmt=src_pix_fmt)
            if not ok:
                try:
                    os.remove(cache)
                except OSError:
                    pass
                return fail("深度视频转码失败", 500)
        return send_file(cache, mimetype="video/mp4", as_attachment=False,
                         download_name=f"depth_{src_asset.file_name}.mp4", conditional=True)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
