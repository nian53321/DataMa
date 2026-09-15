# -*- coding: utf-8 -*-
"""视频人脸区域脱敏：逐帧检测 + 低分辨率掩膜 + 单趟 ffmpeg 合成

作用域只有「人脸所在区域」——人脸区域被不可逆马赛克化（像素化），其余画面（背景、身体、
衣着）逐像素保留。输出统一重编码为 H.264 MP4，且**音轨一律移除**。

设计依据（Ryzen 7 9700X 纯 CPU 实测，1920x1080 / 150 帧 / 5.0s CFR 基准，取 3 次中位数）

两趟结构：

    Pass1  ffmpeg 解码原视频 → 降到代理尺寸 bgr24 → Python 逐帧 YuNet 检测
           → 把「要马赛克化的区域」+「该用多粗的块」写成灰度掩膜，1:1 转发进 Pass2 的 stdin
    Pass2  ffmpeg 单趟：视频 setpts 归一化 → split → 按掩膜里的级号选马赛克块大小
           （几何阶梯）→ maskedmerge 合成 → libx264 编码

- **v3：人脸区改用"马赛克"（像素化）而不是模糊**。脸区做「下采样 → 最近邻上采样」，
  块边长 = **人脸短边 / 8**。为什么弃用模糊：模糊**加大到一定程度后 SFace 余弦反而
  回升**（脸被抹成一块"平均肤色块"，低频统计更干净；实测窗口/人脸 1.15~3.56 时余弦
  0.36~0.49），即"越模糊越容易被判定为同一个人"—— 观感指标与身份指标指向相反。
  马赛克是**结构性破坏**，不存在这个回升：4 档人脸尺度（37/77/116/151 代理 px）
  在「块/人脸 = 1/8」下余弦 **0.126 / 0.029 / 0.070 / 0.086**（同一人阈值 0.363），
  块再粗（1/6、1/4）余弦也不回升。实测马赛克的安全带比模糊宽得多：
  块/人脸 ≥ 1/16 即达标，1/8 是四档同时最优
- **v4：把「支路数」降下来 —— 这是唯一的耗时大头**（现场反馈"人脸速度有点慢"）。
  阶梯从 12 级改 **5 级**（步长 1.25 → 2.0），每路支路的「两次 scale」换成
  **单节点 `pixelize`**。同一 1080p/150 帧基准、**Pass2 单独计时**（`v4_pass2.py`）：

      无支路（解码 + 掩膜 + 编码）    0.42 s   ← 地板
      1 路（scale，2 节点）           0.64 s
      12 路（scale，v3 生产口径）     2.61 s   ← v3 墙钟基本就是这一项
      5 路（scale）                   1.35 s
      5 路（pixelize，1 节点）        **1.05 s**

  单路成本是**纯线性**的：scale 版 0.181 s/路，pixelize 版 **0.148 s/路（省 18%）**
  （插值 flags 从 bilinear/bicubic 换 nearest 几乎无差别，不值得改，见 `v4_pass2.py`）。
  而 Pass1（解码 + 逐帧 YuNet + 画掩膜）实测只占 **0.68 s** —— 两条 Pass 是**并发**的
  管道（Pass1 stdout → Pass2 stdin），墙钟 ≈ max(Pass1, Pass2)，所以 Pass2 减支路
  是唯一有效杠杆，把 Pass2 压到 Pass1 之下就没有额外收益了。
  ⚠️ **被否掉的方案**：「先扫一遍拿到"实际用到的级"，只建那几路」（v3 文档里的 TODO）。
  它要求 Pass1 **跑完**才知道用哪些级，于是丢掉并发：0.68 + 1.05(=3 路) ≈ 1.73 s，
  比直接减阶梯（1.05 s）**更慢**。实测同样否掉了"按需建支路"的收益预期。
- **v5：再快 2.40×，只动线程参数、不动算法、输出逐字节不变**（现场"CPU 使用率低但慢"）。
  `-filter_complex_threads 1`（默认 = 核数 16）+ `cv2.setNumThreads(4)`（默认 16）。
  同一 1280x720 / 99.74 s / 2991 帧素材、容器内、采样器独立进程、3 次中位数（`v9_combo.py`）：

      现状（cv2=16 + fc=auto）    20.06 s    CPU 47.0%（7.52 核）
      fc=1                       11.42 s    CPU 74.8%
      **cv2=4 + fc=1**            **8.37 s**  CPU 47.1%（7.53 核）

  **"CPU 使用率低"的根因是线程在等同步，不是没活干** —— 决定性证据就是首末两行：
  CPU 占用几乎不变（47.0% → 47.1%），耗时却少了 58%。机理与判据见常量
  `FILTER_COMPLEX_THREADS` 上方的注释。
  ⚠️ 两条纪律：① **单跑最优 ≠ 组合最优**（Pass2 单跑 fc=2 最快，进管线后 fc=1 更快，
  因为 Pass1 同时在争 CPU）；② 参数**必须先扫出安全带**再定，别凭"线程多 = 快"的直觉。
  同源的坑见 v1 时代"单条已占 89% CPU"的旧结论 —— 那是 12 级支路口径，v4 砍到 5 级后
  单条只占 44.6%，故**多文件并行**从"无收益"变成 1.42~1.49×（仍远低于参数级的 2.40×，
  故未落地；详见 `back/tools/README-视频脱敏最快方案.md` §十六）
- **v6：检测降频，砍掉真正的瓶颈**（现场"效率还是非常低、速度很慢"）。
  ⚠️ **上文 v2 那条"Pass1 只占 0.68 s"已被证伪** —— 那是 12 级支路时代的口径。
  v5 落地后重新分解（`v11_decomp.py` / `v13_leak_face.py`，同一素材）：

      端到端（生产函数）           10.32 s
      其中 纯 YuNet 检测           6.75 s（65%，2.256 ms/帧 × 2991 帧）
      Pass2 全链（输出 null）      6.09 s  ← 已接近结构下限
        · 仅解码                   0.72 s
        · 降采样到 480x270        +1.20 s
        · 上采样回 1280x720       +0.60 s（bicubic 比 bilinear 只贵 0.07 s）
        · 支路 5 → 3 只省          0.72 s

  **两条被实测否掉的方向**：① 减支路（只省 0.72 s，且"按需建支路"要牺牲并发，v2 已否）；
  ② 换末级上采样插值（0.07 s，噪声级）。**唯一能按倍数砍的是检测**，因为它独占 65%。
  故改为「**只在人脸 ROI 内画面明显变化时才跑 YuNet**」，检测率 39.7%，
  端到端由 Pass2 兜底 ≈ 6.3 s（1.64×）。
  ⚠️ 这是 v2 以来**第一条改变输出**的提速（v4/v5 都是逐字节不变）：被跳过的帧用旧框。
  验收只能用**直接安全指标** —— 原始人脸框内掩膜泄漏，实测 **1500 帧恒为 0**；
  带 `MASK_PAD` 的宽口径会高估 20 倍，不可用。细节与三道保险见 `DETECT_ROI_DIFF` 常量注释。
- **选级规则：取「不小于目标的最细一级」（向上取整），不再取"最接近"**。
  向上取整保证 块/人脸 **恒 ≥ 1/8**（实测最优档），"最接近"会掉到 1/11 附近；
  代价是块平均略粗（观感更方块化），换来的是**粗阶梯下也不会掉进"等于没脱敏"区**
  （块/人脸 1/32 时余弦 0.71~0.82）。两条合起来才允许阶梯 12 级 → 5 级
- **块大小必须相对"人脸短边"，不是相对画面**：固定块（相对画面）会让近景大脸
  块相对变小、等于没脱敏 —— v1 的固定模糊半径踩的正是同一个坑
- **掩膜不落盘**：1 小时 1080p 源的掩膜若落盘约 14 GB；stdin/stdout 直连后内存恒定
- **级号编码进掩膜灰度**：灰度 = 级号 * MASK_LEVEL_STEP，Pass2 用一串 lut 阈值把它
  还原成各级硬掩膜，再链式 maskedmerge 挑出该级的马赛克。全流程仍是**单趟编码**，
  不为每一级各跑一趟
- **各级马赛克与合成都在代理尺度上做，最后只升采样一次**
  （各级分别升到全分辨率再合成 = 白做 K 次全分辨率缩放）
- **代理尺寸按面积归一**（≈480x270）：人脸在不同源分辨率下被归一到同一像素量级，
  块大小不随源分辨率漂移；同时检测耗时随分辨率超线性（1080p 全帧 32.8 ms/帧 vs
  480x270 2.0 ms/帧，16 倍），在小图上检测是逐帧路线里最大的一笔省
- 人脸持续移动，故**每帧都检测**（静态框方案被实测否决：5 秒内人脸中心移动
  574x451 px，占画面 30%x42%）。检测失败时沿用上一帧的框，避免人脸瞬移时漏出

实测质量（真实人脸素材 z1~z4 变焦，人脸短边 37/77/116/151 代理 px；
SFace 余弦同一人判定阈值 0.363）：

    人脸短边（代理 px）    37      77      116     151
    选中块边长            4.69    9.16    14.31   17.88
    块 / 人脸             0.125   0.118   0.124   0.118   （目标 1/8）
    SFace 余弦            0.114   0.043   0.063   0.062   ← 全部远低于阈值
    灰度 std 残比（观感）  0.91    0.91    0.90    0.91

    背景保留           掩膜外像素只经一次重编码（实测框外角落局部细节 > 0.85 原值）
    脱敏后人脸检出      四档**全部无法被 YuNet 检出**（马赛克破坏了人脸结构）

⚠️ v1（弱固定模糊）时代另有一组"下游仍可用"的数字 —— **v3 起不再成立**，只作存档对照：
条件检出率 0.95~0.97、框 IoU 0.78、五点关键点漂移 0.13 脸宽。马赛克把脸抹成色块后，
"脱敏后还能做关键点/表情分析"这个前提本身就不存在了（见下方代价说明）。

**已知代价与边界**（必须在导出界面如实告知用户）：

- **人脸区域被抹成色块 → 一切人脸相关的下游分析都不可用**（v3 起比 v1/v2 更彻底）：
  实测脱敏后 YuNet 在四档尺度上**全部检不出人脸**，故"脱敏后仍可做关键点 / 表情 /
  视线分析"的前提已不成立
- **观感 ≠ 可识别性**：马赛克在人眼看来更彻底，但**验收标准只能是 SFace 余弦** ——
  模糊曾出现"越糊余弦越高"的反向现象（见下方 MOSAIC_TARGET_RATIO 注释的实测表）
- 只覆盖人脸区域，**软生物特征不受保护**：发型轮廓、体型、衣着、步态仍完整可辨
- **不可还原**：马赛克（像素化）是有损变换，与脑电/心电的值级脱敏（可逐字节还原）不同。
  本模块**不写** `_desens_manifest.json`，也**不参与** restore_kit —— 导出包内
  不存在任何可回推原始人脸的密钥或参数
- **音轨一律移除**：视频音轨含受试者声纹，而声纹脱敏（`audio` 开关）只作用于
  `data_type == audio` 的资产、不覆盖视频。保留音轨等于把未脱敏的声纹一起发出去
- 只支持**单视频轨**源。多视频轨（彩色 + 深度/红外，如 Orbbec 的 mkv）直接拒绝，
  避免静默丢轨；丢弃深度/红外轨属于数据损失，需另行确认后再扩展
- 输出统一为 H.264 MP4，扩展名随之改为 `.mp4`；10bit 源会被降到 8bit

依赖：ffmpeg（`app.utils.transcode.get_ffmpeg`，imageio-ffmpeg 自带，无需系统安装）、
`opencv-python-headless`（提供 `cv2.FaceDetectorYN`）、`numpy`、`av`。
人脸检测模型 `models/face_detection_yunet_2023mar.onnx`（OpenCV Zoo YuNet）**不随代码入库**：
本地缺失时由 :func:`model_path` 首次使用时从官方地址自动下载并按 SHA256 校验，
可用环境变量 `FACE_DETECT_MODEL_PATH` 指定离线/自备模型。
"""
import hashlib
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

from app.utils.transcode import get_ffmpeg

# 算法版本标识（写进日志/统计，便于回溯是哪一版权衡）
# v2：模糊强度改为按人脸尺寸分级（近景大脸此前等于没脱敏）
# v3：人脸区改用**马赛克**（像素化），块边长 = 人脸短边 * MOSAIC_TARGET_RATIO。
#     弃用模糊的原因见模块 docstring：模糊加大到一定程度后余弦**回升**（越糊越像同一人）
# v4：**提速**（现场反馈"人脸速度有点慢"）—— 阶梯 12 级 → 5 级、每路支路
#     两次 scale → 单节点 pixelize、选级改"向上取整"（保证块/人脸恒 ≥ 1/8）。
#     依据与实测见模块 docstring 的 v4 条，脚本 `bench_video_desens/v4_speed.py`
# v5：**再提速 2.40×，只动线程参数、不动算法**（现场"CPU 使用率低但很慢"）。
#     `-filter_complex_threads 1`（默认 = 核数） + `cv2.setNumThreads(4)`（默认 16）。
#     输出与 v4 **逐字节相同**（sha256 一致，见 docstring v5 条与 `v7_verify.py`）。
# v6：**检测降频**（现场"效率还是非常低"）。v5 之后纯检测占端到端 65%，Pass2 已到结构下限，
#     故改为「只在人脸 ROI 内画面明显变化时才跑 YuNet」，硬上限 6 帧。
#     ⚠️ **本条与 v4/v5 不同：会改变输出**（被跳过的帧用旧框），验收靠直接指标 ——
#     原始人脸框内掩膜泄漏 **恒为 0**（1500 帧实测），不是"逐字节相同"。见常量区注释。
VERSION_TAG = "video-desens-v6"

# 代理尺寸：按**面积**归一，与实测基准 480x270 等价。
# 竖屏/超宽屏不会因为"宽度固定"而面积暴涨（拖动 Pass1 耗时）。
PROXY_AREA = 480 * 270
PROXY_MIN_SIDE = 64
PROXY_MAX_W = 960

# 掩膜向外扩边的比例。实测 0.10 → 0.20 时检出率、IoU、关键点漂移、匿名强度
# 四项同时改善（Pass1 画框不重解码，不增加耗时）
MASK_PAD = 0.20

# ==================== 马赛克块阶梯（按人脸尺寸自适应） ====================
#
# 为什么需要阶梯：块边长必须相对**人脸尺寸**，而不是相对画面尺寸。固定块对远景小脸
# 够用，人脸越近越失效 —— v1 的固定模糊半径踩的正是这个坑（近景大脸余弦 0.902）。
#
# 为什么是马赛克而不是模糊（v3 改动依据，2026-09-15 实测）：
#     把模糊目标比值从 0.47（v2 生产值）一路推到"强制最大级"，**观感越来越糊**
#     （灰度标准差残比 0.73 → 0.32），但 SFace 余弦单调**上升**：
#         0.250 → 0.375 → 0.409 → 0.395 → **0.552**（同一人阈值 0.363）
#     —— 加大模糊反而更不安全。机理：窗口接近/超过人脸短边时脸被抹成一块"平均肤色
#     块"，而 SFace 的输入本就是 112x112 低通图，高频它不在乎、低频统计反而更干净。
#     → **观感指标与身份指标指向相反，验收只能看后者。**
#
#     马赛克（下采样 + 最近邻上采样）是结构性破坏，实测 4 档人脸尺度 × 6 档块大小：
#         块/人脸      1/32     1/16     1/12     **1/8**   1/6     1/4
#         z1( 37px)    0.713    0.377    0.188    **0.126** 0.149   0.131
#         z2( 77px)    0.799    0.154    0.049    **0.029** 0.021   0.060
#         z3(116px)    0.816    0.126    0.084    **0.070** 0.019   0.115
#         z4(151px)    0.812    0.152    0.095    **0.086** 0.030   0.093
#     → 安全带 **块/人脸 ≥ 1/16**（1/8 是四档同时最优），且**没有模糊那种"越强越差"**；
#       块太细（1/32）等于没脱敏，故有下限。
#
# 实现：级号编码进掩膜灰度（灰度 = 级号 * MASK_LEVEL_STEP），Pass2 用一串 lut 阈值
# 把灰度还原成各级硬掩膜，再链式 maskedmerge 挑出该级的马赛克。
#
# ⚠️ **级数 = 支路数 = 耗时**（v4 提速的核心事实）。Pass2 单独计时、1080p/150 帧：
#     1 路 0.64s → 5 路 1.05s(pixelize) → 12 路 2.61s(scale, v3 口径)
#   即每个支路是**纯线性**成本，阶梯越多越慢。
#   ⚠️ 但由此推出"Pass2 是唯一墙钟"是**错的**：早期探针记的"Pass1 只有 0.68s"是口径错误
#     （它量的是直通重编码，不是真的 Pass1）；v5 分解实测 Pass1 = 8.7s（含 YuNet 7.2s）
#     → **两条腿必须同时砍**，v6 的两个杠杆正是分别砍这两条腿。
#   步长因此从 1.25 放到 **2.0**（12 级 → 5 级）。放粗会让"块/人脸"的波动区间变大，
#   所以选级必须同时从"最接近目标"改成**向上取整**（见 mosaic_level）——
#   向上取整后比值恒在 [1/8, 1/4]，永远不会掉进"块太细 = 等于没脱敏"区（1/32 时余弦 0.71~0.82）。
MOSAIC_BLOCK_BASE = 3.0      # 最细一级块边长（代理 px），约对应人脸短边 24px
MOSAIC_BLOCK_STEP = 2.0      # 相邻级块边长倍率（↑ 支路数 ↓ 耗时；配"向上取整"选级）
MOSAIC_TARGET_RATIO = 0.125  # 选级依据：取**不小于** 该比例×人脸短边 的最细一级（= 1/8）
MOSAIC_LADDER_MAX = 12       # 级数上限（掩膜以 20 为步长编码，uint8 最多容纳 12 级）
MASK_LEVEL_STEP = 20         # 掩膜灰度按级编码：级号 k → k*20（1..12 → 20..240）

# v1 的固定 boxblur 半径（radius=4 → 窗口 9px @代理）。**v3 起彻底弃用**
# （人脸区改马赛克，模块内不再引用）。保留只为让早期实验脚本按文件路径加载本模块时
# 不至于 AttributeError（`bench_video_dens/exp_face_scale.py` 会读它做口径对照）。
# **不要把它接回任何选级逻辑** —— 固定半径正是"近景大脸等于没脱敏"的根因。
BLUR_RADIUS = 4

DETECT_SCORE_THRESHOLD = 0.5
DETECT_NMS_THRESHOLD = 0.3
DETECT_TOP_K = 5000
# 单帧最多处理的框数：防极端误检把整幅画面涂满（正常场景远小于此）
MAX_FACES_PER_FRAME = 16

ENCODE_PRESET = "ultrafast"
ENCODE_CRF = 23

# ==================== 线程参数（v5 提速，2026-09-15 实测） ====================
#
# ⚠️ **这两个值都是"小图 + 多节点滤镜链"下的反直觉结论：默认值（= 核数）是负优化。**
#    现场症状是"CPU 使用率低（47%）而且慢"—— 根因不是没活干，而是**线程在等同步**。
#    决定性证据：CPU 占用 47.0% → 47.1%（几乎不变），耗时 20.06 s → 8.37 s（2.40×）。
#
# 机理：`-filter_complex_threads` 默认 = 核数(16)，于是滤镜链的**每个节点**都把 480x270
#    的代理帧切成 16 条 slice —— 每 slice 只剩 8,100 px。12+ 个节点（5 pixelize + 5 lut +
#    6 maskedmerge + split/scale）× 2991 帧 ≈ **57 万次 fork-join**，分派/同步开销远超
#    计算收益。cv2 同理：容器默认 `getNumThreads() = 16`，而 YuNet 单帧输入只有 480x270。
#
# 实测（1280x720 / 99.74 s / 2991 帧真实素材，容器内，采样器独立进程，3 次中位数）：
#     fc 扫描（Pass2 单独）  auto 16.64s | 1 → 7.93 | 2 → 7.60 | 4 → 7.92 | 8 → 10.72
#     cv2 扫描（Pass1）      16 → 8.39s | 8 → 6.81 | 4 → 7.25 | 2 → 9.62 | 1 → 15.03
#     端到端组合（`v9_combo.py`）  cv2=16+fc=auto 20.06s → **cv2=4+fc=1 8.37s**
#     1080p 源复测（`v8_hd.py`）  默认 1.077s → fc=2 0.671s（1.61×，证明非 720p 特例）
#
# ⚠️ 注意 **单跑最优 ≠ 组合最优**：Pass2 单跑时 fc=2 最快，但进生产管线（Pass1 同时在跑、
#    两者争 CPU）后 **fc=1 更快**。所以这里用的是组合口径 `v9_combo.py` 的结论。
#
# **正确性**：这两个开关只改并行度，不改逐像素结果 —— `v7_verify.py` 实测 fc 从 auto 到
#    1/2/4 输出 **sha256 逐字节相同**（2993 帧 / 7,341,867 字节），生产完整管线同样一致。
#    即"零风险"，不是"肉眼看不出"。
FILTER_COMPLEX_THREADS = 1
CV2_NUM_THREADS = 4

# cv2 线程数是**进程全局**设置，而导出任务跑在 Flask API 进程的后台线程里、且允许并发
# （`ExportTaskManager.create_task` 每任务起一条线程、无并发上限）。若直接"设 4 → 恢复 16"，
# 先结束的那条任务会把还在跑的任务悄悄改回 16（不会出错，只是那一条静默变慢）。
# 故用**引用计数**：首个进入者保存原值并设置，最后一个离开者才恢复。
_CV2_THREAD_LOCK = threading.Lock()
_CV2_THREAD_USERS = 0
_CV2_THREAD_SAVED = None

# ==================== 检测降频（v6 提速，2026-09-15 实测） ====================
#
# **为什么还能再快**：v5 把线程同步开销去掉之后，端到端 10.32s 里 **纯 YuNet 检测占 6.75s
#     （65%）**（480x270 代理，2.256 ms/帧 × 2991 帧，见 `bench_video_desens/v13_leak_face.py`），
#     而 Pass2 全链已压到 6.09s 且**接近结构下限** —— 实测支路 5→3 只省 0.72s、
#     末级上采样 bicubic→bilinear 只省 0.07s、两次 scale 合计 1.80s 且不可省
#     （`v14_roigate_layout.py`）。所以唯一能"按倍数"砍的对象就是检测本身。
#
# 手段：**不每帧都跑检测**，只在「人脸 ROI 内的画面已经明显变化」时才跑，其余帧沿用上一次
#     的框。这是**语义变化**（被跳过的帧用旧框），因此验收必须用直接指标，不能靠观感。
#
# 验收指标：泄漏 = **原始人脸框**（YuNet 输出、**不外扩**）内「掩膜==0」的面积占比。
#     ⚠️ 换算成带 `MASK_PAD` 的框会**高估 20 倍**风险 —— 位移小于 padding 时露出的
#     是 padding 区（本来就不是脸）。两个口径都实测过：带 pad 口径 max 0.057~0.077，
#     原始框口径**恒为 0.000**，故以原始框为准。
#
#     实测（1280x720 / 2991 帧真实素材取前 1500 帧，`v13` + `v14`）：
#         策略                     检测比例   原始人脸框泄漏 max   有泄漏帧
#         every1（v5 现状）         100%        0.00000              0
#         固定降频 1/2 ~ 1/6        17~50%      0.00000              0
#         **ROI 门控 0.02（本档）** **39.7%**   **0.00000**          0
#         ROI 门控 0.05             27.3%       0.00000              0
#         ROI 门控 0.05 / 差 16     14.1%       0.00000              0
#         全画面运动门控 >0.5       17.1%       0.1477             705   ← 坏代理，已否
#
#     「全画面运动量」是坏代理：它既含人脸也含无关运动，阈值调高就漏、调低就白跑
#     （实测泄漏 0.1477，**远差于**均匀降频的 0）。门控**必须限制在人脸 ROI 内**。
#
# 为什么这样判定是安全的：门控量的是"这份掩膜已经过期了多少"，必须与**最近一次检测帧**
#     （不是前一帧）比较 —— 脸以每帧 1px 匀速移动时帧间差始终很小，与前一帧比会让门控
#     永不打开、框无限过期（实现首版即踩此坑，见 `_roi_change_ratio` 的注释）。
#
# 三道保险：
#     1. 既有 `MASK_PAD = 0.20` 外扩 —— 位移小于 padding 时不会露脸（实测正是如此）
#     2. 阈值取**保守档** 0.02（检测率 39.7%）而不是最省的 0.05 / 0.14
#     3. `DETECT_MAX_STALENESS` 硬上限 —— 画面再静也最多连续 6 帧不检测，
#        防"低对比度大位移"（皮肤纹理均匀、移动却大）这种门控盲区
#
# ⚠️ **单条视频里门控不值钱**（v20 复测确认，`v20_final_evidence.json`）：
#     单条时两趟管线并未占满 CPU，检测时间大半被 Pass2 掩盖 —— 实测把检测从 100%
#     砍到 38.7%，总耗时只从 8.675s → 7.953s（**1.091×**）。
#
#     真正让它变值钱的是**段级并行**（见 SEGMENT_MAX）：并发跑 3 段时 CPU 逼近天花板，
#     此时减少的是**总工作量**而不是关键路径。v20 同一轮实测（同一素材）：
#         段级并行 3 段、**不降频**   7.355s   （1.179×）
#         **再叠门控 0.05**          5.308s   （**1.634×**）
#         → 门控本身在并发口径下值 **1.393×**（7.355 / 5.308）
#     **同一条改动在两种并发度下价值差 ≈4 倍**（1.091× → 1.393×）—— 定参必须说清
#     是在哪个并发度下定（同 v5 的"单跑最优 ≠ 组合最优"）。
#
# 阈值取 0.05 而不是最保守的 0.02：两者在 1500 帧泄漏验证里都是 0.00000（`v13`/`v14`），
#     但 0.05 更省（检测率 38.7% vs 49.2%）。要更保守可改回 0.02。
DETECT_ROI_DIFF = 8             # 灰度差多少算"这个像素变了"（0..255）
DETECT_ROI_CHANGE_RATIO = 0.05  # ROI 内变化像素占比超过它 → 重检
DETECT_MAX_STALENESS = 6        # 连续未检测帧数的硬上限（画面静止也不超过）

# ---- 管道缓冲（v6：**零风险，但收益不可复现 → 不要算进提速账**） ----
#
# 机理：两趟管线若退化成"逐帧锁步"，两个进程的工作就被串成加法。一帧掩膜 129.6 KB，
# 而 Linux 管道默认只有 64 KB —— 生产者每帧都要阻塞在写上。故把管道放大到 1 MB
# （≈8 帧掩膜）并给掩膜输入加 `-thread_queue_size`。
#
# ⚠️ **放大本身确实生效**（已直接验证：Popen 管道 `F_GETPIPE_SZ` 65536 → 1048576；
#   注意 `_enlarge_pipe` 要的是**文件对象**，传裸 fd 会因没有 `.fileno()` 而静默返回 0）。
#
# ⚠️ **但收益没能复现**：`v16` 曾测到 1.12×（8.682s → 7.752s）—— 其 JSON 已随容器 /tmp
#   丢失、无法核对；`v20` 在同一素材（1280x720 / 2991 帧）重测为
#   **0.977×（仅放大管道）/ 0.998×（放大 + 队列）**，即**噪声内、无收益**。
#   两个配置的输出 sha256 与 v5 完全相同（`27e904c671e784d1…`）→ **确认零风险，但不确认有效**。
#   保留的唯一理由：零风险，且在"生产者/消费者速度更不对称"的机器上可能有用。
#   **判据：凡是"零风险但收益不可复现"的改动，一律只记成"零风险"，不记成"提速"。**
MASK_INPUT_QUEUE_SIZE = 256     # ffmpeg 掩膜输入队列深度（帧）；0 = 用 ffmpeg 默认
PIPE_BUFFER_BYTES = 1 << 20     # 把两条管道都放大到 1 MB（≈8 帧掩膜）；0 = 不动

# ---- 段级并行（v6，实测 1.63×，对**单条视频**也有效） ----
#
# 为什么必须走到这一步：Pass2 的滤镜链只能 `-filter_complex_threads 1`（v5 扫描：
#   fc=2/4/8 全都更慢），那 ~5.4s 于是**全压在单个核上**，而整机 CPU 只用 47%。
#   单条管线内部已经无空间 —— 只能**同时跑多条独立管线**。
#
# 与"多文件并行"的关键区别：多文件并行对"一次导出只有 1 条视频"毫无帮助
#   （而这正是常见情形），段级并行**对单条视频同样有效**。
#
# 做法：`ffmpeg -f segment -c copy -reset_timestamps 1` 按**关键帧**切成 n 段
#   （零重编码、**不失帧**），各段跑一遍完整两趟管线（线程并发），最后
#   `ffmpeg -f concat -safe 0 -i list -c copy` 拼回单一 mp4。
#
# v20 复测（**真 v5 基线**：降频全关 → detect_ratio = 1.0，单趟中位数 8.675s；
#   1280x720 / 99.74s / 2991 帧；每档 2~3 次取中位数，`v20_final_evidence.json`）：
#     配置                          墙钟      提速      检测率   帧数核对
#     真 v5 单趟（基线）            8.675s    1.000×    100%     2991
#     单趟 + 门控 0.05              7.953s    1.091×    38.7%    2991
#     3 段并发、**不降频**          7.355s    1.179×    100%     2991
#     **3 段并发 + 门控 0.05**      **5.308s**  **1.634×**  38.7%    2991
#     4 段并发 + 门控 0.05          5.279s    1.643×    38.8%    2991
#   3 段与 4 段在**噪声内持平**（5.308s vs 5.279s，±0.12s）→ 默认仍取 3 段（30s/段），
#   4 段作上限：再多只是把同一份 CPU 切得更碎，而每段还要多付 2 个进程 + 模型加载。
#   拼接无损：`-c copy` 拼回后 2991 帧，与整条输出一致。
#   ⚠️ 段级并行本身只值 1.179×（它把 CPU 推满），**剩下的 1.393× 来自门控**
#      （见 DETECT_ROI_CHANGE_RATIO 注释）—— 两个杠杆必须一起上。
#
# ⚠️ 三条硬约束：
#   1. 切分只能落在**关键帧**上。源若没有周期关键帧（例如单个 IDR 的长 GOP），
#      实际段数会少于请求 → 用**实际**段数；只剩 1 段就退回单趟路径。
#   2. **拼接后必须重新核对总帧数** —— 各段自身的"输入帧==输出帧"已在段内校验，
#      这里只需保证 `拼接输出帧数 == 各段输出帧数之和`。不一致即视为失败
#      （本模块一贯口径：绝不把"以为脱敏了"的视频发出去）。
#   3. 任一步失败（切分 / 某段脱敏 / 拼接 / 帧数不符）→ **退回单趟路径**重跑整条，
#      而不是退回"原样导出" —— 后者等于人脸未脱敏却标称已脱敏。
SEGMENT_TARGET_SECONDS = 30.0    # 每段目标时长（100s → 3 段）
SEGMENT_MAX = 4                  # 段数上限（实测 3/4 段噪声内持平，再多无收益）
SEGMENT_MIN_DURATION = 20.0      # 短于此不切分：每段要付 2 个 ffmpeg 进程 + 模型加载的固定开销

# 超时下限；实际按视频时长的 30 倍放宽（实测 RTF 0.175，余量约 170 倍）
MIN_TIMEOUT_SECONDS = 600
PROCESS_WAIT_SECONDS = 180

MODEL_NAME = "face_detection_yunet_2023mar.onnx"

# OpenCV Zoo 官方发布的 YuNet 人脸检测模型（与曾随包分发的版本完全一致）
MODEL_DOWNLOAD_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
# 固定正确哈希：下载后校验，拒绝损坏/被替换的模型
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL_DOWNLOAD_TIMEOUT = 90  # 秒

__all__ = ["VERSION_TAG", "MODEL_NAME", "desensitize_video_file",
           "desensitize_video_file_parallel", "segment_plan",
           "probe_video", "model_path", "build_filter_complex",
           "mosaic_ladder", "mosaic_level", "mask_level_value",
           "mask_level_threshold", "mask_rect"]


# ==================== 路径与探测 ====================

def _sha256_file(path):
    """流式计算文件 SHA256"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        block = f.read(1 << 20)
        while block:
            digest.update(block)
            block = f.read(1 << 20)
    return digest.hexdigest()


def _download_model(candidate):
    """把官方模型下载到 ``candidate``（校验哈希 + 原子落盘）；成功返回 True。

    并发安全：写唯一临时文件后 ``os.replace`` 原子替换；即便两个进程同时下载，
    两份内容相同（同哈希），后写覆盖先写，最终文件始终完整有效。
    """
    directory = os.path.dirname(candidate)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return False
    tmp_path = os.path.join(directory, ".%s.%d.part" % (MODEL_NAME, os.getpid()))
    try:
        with urllib.request.urlopen(
                MODEL_DOWNLOAD_URL, timeout=MODEL_DOWNLOAD_TIMEOUT) as resp, \
                open(tmp_path, "wb") as f:
            shutil.copyfileobj(resp, f, length=1 << 20)
        if _sha256_file(tmp_path) != MODEL_SHA256:
            return False
        os.replace(tmp_path, candidate)
        return True
    except Exception:
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def model_path():
    """定位人脸检测模型；本地缺失时自动下载，仍失败返回 None

    优先环境变量 `FACE_DETECT_MODEL_PATH`（便于离线/自备模型的部署）；
    否则取应用根目录下的 `models/<模型名>`，缺失时从 OpenCV Zoo 自动下载
    并校验 SHA256。
    """
    env = os.environ.get("FACE_DETECT_MODEL_PATH")
    if env and os.path.exists(env):
        return env
    # app/utils/video_desensitize.py → app/utils → app → <应用根>
    root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    cand = os.path.join(root, "models", MODEL_NAME)
    if os.path.exists(cand):
        return cand
    if _download_model(cand) and os.path.exists(cand):
        return cand
    return None


def probe_video(path):
    """用 PyAV 探测视频规格

    不用 ffprobe：ffmpeg 二进制来自 imageio-ffmpeg，**不自带 ffprobe**。

    :return: dict(width, height, fps, frames, duration, video_streams, audio_streams)
             无视频轨时返回 None
    """
    import av
    with av.open(path) as container:
        video_streams = list(container.streams.video)
        if not video_streams:
            return None
        stream = video_streams[0]
        ctx = stream.codec_context
        rate = stream.average_rate
        duration = 0.0
        if container.duration:
            duration = float(container.duration) / float(av.time_base)
        return {
            "width": int(ctx.width or 0),
            "height": int(ctx.height or 0),
            "fps": float(rate) if rate else 0.0,
            "frames": int(stream.frames or 0),
            "duration": duration,
            "video_streams": len(video_streams),
            "audio_streams": len(container.streams.audio),
        }


def _output_frame_count(path):
    """读输出视频的帧数（nb_frames）；未知时返回 0"""
    import av
    with av.open(path) as container:
        streams = list(container.streams.video)
        if not streams:
            return 0
        return int(streams[0].frames or 0)


def _even(value):
    """向下取偶（ffmpeg scale 与 rawvideo 都要求偶数尺寸）"""
    value = int(value)
    if value % 2:
        value -= 1
    return max(2, value)


def _proxy_size(width, height):
    """按面积归一出代理尺寸；源本身比代理还小时不放大"""
    if width <= 0 or height <= 0:
        return 2, 2
    area = float(width) * float(height)
    if area <= PROXY_AREA:
        return _even(width), _even(height)
    scale = (PROXY_AREA / area) ** 0.5
    proxy_w = max(PROXY_MIN_SIDE, int(round(width * scale)))
    proxy_h = max(PROXY_MIN_SIDE, int(round(height * scale)))
    # 超宽视频：再卡一次宽度，避免单边过大
    if proxy_w > PROXY_MAX_W:
        ratio = float(PROXY_MAX_W) / float(proxy_w)
        proxy_w = int(proxy_w * ratio)
        proxy_h = int(proxy_h * ratio)
    return _even(proxy_w), _even(proxy_h)


def _tail(path, limit=600):
    """读临时日志文件尾部（ffmpeg 的 stderr），用于失败原因定位"""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").strip()
    text = " ".join(text.split())
    return text[-limit:] if len(text) > limit else text


# ==================== 马赛克块阶梯（纯函数） ====================

def mosaic_ladder(proxy_w, proxy_h):
    """生成马赛克块边长阶梯（升序、去重、已按该尺度收敛）

    - 块边长按 ``MOSAIC_BLOCK_STEP`` 几何递增，直到**最后一级 ≥** "人脸占满画面短边"
      所需的块（``MOSAIC_TARGET_RATIO * min(proxy_w, proxy_h)``）。
      因为选级是**向上取整**（见 `mosaic_level`），最后一级必须严格够得到最大人脸的
      目标块 —— 否则最大的脸会退化成"取最粗一级"，比值跌回安全带以下
    - 与 v2 的 boxblur 半径不同，块边长**没有 ffmpeg 硬上限**（`pixelize` 允许 1..1024），
      此处按 ``min(proxy_w, proxy_h)`` 收敛纯粹是为**省支路**（每多一级多一路的耗时）
    """
    largest_block = MOSAIC_TARGET_RATIO * float(min(proxy_w, proxy_h))
    blocks = []
    block = float(MOSAIC_BLOCK_BASE)
    for _ in range(MOSAIC_LADDER_MAX):
        if not blocks or block > blocks[-1]:
            blocks.append(round(block, 4))
        if block >= largest_block:
            break
        block *= MOSAIC_BLOCK_STEP
    return blocks


def mosaic_level(min_side, blocks):
    """按人脸短边（代理 px）选级号（1-based）：取**不小于**目标的最细一级

    目标 = ``MOSAIC_TARGET_RATIO * 人脸短边``（= 脸短边 / 8），从细到粗找**第一个 ≥ 目标**
    的块；都不到就取最粗一级。

    - **向上取整**（v4 改）：实际比值恒落在 ``[1/8, 1/4]``，**永远不会低于 1/8** ——
      这是实测最优档（37/77/116/151 四档余弦 0.126/0.029/0.070/0.086）。
      换成"最接近"时会掉到 1/11 附近（比值区间 [目标/√步长, 目标√步长]），
      步长放粗后甚至能掉到 1/16 以下 —— 而 1/32 的余弦是 0.71~0.82，等于**没脱敏**。
      这条保证是阶梯能从 12 级砍到 5 级的前提
    - 代价：块平均略粗（比值上界 1/4 ≈ 脸只剩 4 个块），观感更"方块化"；
      安全性上**没有**模糊那种"越强越差"的回升（块再粗余弦也不升）
    - 脸比最细一级（3px）还小时只能取第 1 级 —— 此时块**相对更大 = 更安全**，
      不存在 v2 模糊那种"最弱一级仍不够"的问题
    """
    if min_side <= 0 or not blocks:
        return 1
    target = MOSAIC_TARGET_RATIO * float(min_side)
    for index, block in enumerate(blocks):
        if block >= target:
            return index + 1
    return len(blocks)


def mask_level_value(level):
    """级号 → 掩膜灰度值（0 留给"无脸"）"""
    return int(level) * MASK_LEVEL_STEP


def mask_level_threshold(level):
    """级号 → 判定阈值：灰度 >= 阈值 即视为「该级及以上」"""
    return int(level) * MASK_LEVEL_STEP - MASK_LEVEL_STEP // 2


# ==================== Pass2 滤镜图 ====================

def mosaic_block_px(block):
    """块边长（代理 px，可能是小数）→ `pixelize` 需要的整数块宽高

    `pixelize` 的 ``w``/``h`` 是整数像素，没有取偶要求（它不经过 yuv420p 的
    色度下采样约束），因此 v3 时代那条"下采样尺寸必须取偶、否则 scale 直接失败"
    的坑在 v4 不再存在 —— **但只在用 pixelize 时才不存在**。
    `MOSAIC_BLOCK_BASE=3.0` + `MOSAIC_BLOCK_STEP=2.0` 生成的阶梯本身就是整数
    （3/6/12/24/48），故这里的取整对生产阶梯是恒等映射；留 ``round`` 只为兜住
    将来调步长时出现的分数块（此时"向上取整"保证的安全余量会有 <1/2 px 的偏移）
    """
    return max(1, int(round(float(block))))


def build_filter_complex(fps, blocks, proxy_w, proxy_h, width, height):
    """构造 Pass2 的滤镜图（纯函数，便于单测）

    :param blocks: 马赛克块边长阶梯（升序列表，单位 = **代理 px**，见 `mosaic_ladder`）
    :param proxy_w: 代理宽（各级马赛克与掩膜都在代理尺度上）
    :param width: 输出宽（只做**一次**上采样）

    三条不变的约定：

    1. 两路输入都必须是 ``选流 → settb=AVTB → setpts=N/(fps*TB)``。
       **两侧时间基必须一致再重打时间戳**，否则 maskedmerge（framesync 滤镜）会重复吐帧。
       实测（1280x720 源、时间基 1/10000000、30fps、1035 帧）：base 的 pts 落在源时间基上、
       掩膜 rawvideo 落在自己的 1/1000000 上，逐帧只差 3e-10 s、base"落后"，framesync 就每帧
       重复吐一帧 base 来追平 → **输出 1138 帧**，被帧数一致性校验拦下、整条视频被跳过。
       两侧统一 ``settb=AVTB`` 后用同一个 ``N/(fps*TB)`` 重打，第 i 帧拿到**逐位相同**的 pts
       → 严格 1:1 配对（实测输出 1035 帧）。
       反例（都实测过，别再改成这样）：只给 base 打 setpts = 上述 bug（1138 帧）；给 base 去掉
       setpts（2069 帧）；指望输出端 ``-r fps`` 兜底（1036 帧）。
    2. 各级马赛克**都在代理尺度上**做，合成也在代理尺度上做，最后只上采样一次
       （若每级都上采样到全分辨率再合成，等于白做 K 次全分辨率缩放）。
    3. ``maskedmerge`` 是三输入滤镜（base, overlaid, mask）。**掩膜极性反了会得到
       「背景被马赛克、人脸被保留」**，与预期完全相反（此处按实测极性固定）。
       链式合成从**最弱一级**起底：``o = maskedmerge(o, 更强一级, 该级及以上的硬掩膜)``。

    每级的"马赛克"= 代理尺度下**单节点** ``pixelize=w=块边长:h=块边长:mode=avg``
    （``avg`` = 块内取均值，等价于 v3 的 ``scale=...:flags=area``；它自己就是
    "降采样 + 最近邻放大"一步到位，v3 用两个 scale 节点做同一件事，实测单路贵 18%）。
    ⚠️ v3 时代那条"**下采样尺寸必须取偶**，否则 yuv420p 下 scale 直接失败"的坑
    在 pixelize 下不存在（它不做色度下采样），见 `mosaic_block_px`。

    掩膜流（[1:v]）是**代理尺度**的灰度图，灰度 = 级号 * MASK_LEVEL_STEP：
    ``0`` = 无脸；``>= mask_level_threshold(k)`` 的像素换成第 k 级的马赛克。
    并集掩膜（灰度 > 0）上采样到全分辨率，用于最后一步与原始画面合成 ——
    覆盖范围与不带阶梯的旧实现完全一致。
    """
    setpts_chain = "settb=AVTB,setpts=N/(%.6f*TB)" % fps
    count = max(1, len(blocks))
    mask_full = "scale=%d:%d:flags=bilinear,setsar=1" % (width, height)

    if count == 1:
        # 退化情形：极小的代理尺寸下阶梯只有一级
        block = mosaic_block_px(blocks[0])
        return (
            "[0:v:0]%s,split=2[a][b];"
            "[b]scale=%d:%d,pixelize=w=%d:h=%d:mode=avg,"
            "scale=%d:%d:flags=bicubic[bp];"
            "[1:v]%s,lut=y='if(gte(val,1),255,0)',%s[mu];"
            "[a][bp][mu]maskedmerge[v]"
            % (setpts_chain, proxy_w, proxy_h, block, block,
               width, height, setpts_chain, mask_full))

    pix_parts = []
    for i, block in enumerate(blocks):
        size = mosaic_block_px(block)
        pix_parts.append(
            "[b%d]pixelize=w=%d:h=%d:mode=avg[p%d];" % (i, size, size, i))
    pix_parts = "".join(pix_parts)
    pix_out = "".join("[b%d]" % i for i in range(count))
    mask_out = "".join("[m%d]" % i for i in range(1, count))
    thresholds = "".join(
        "[m%d]lut=y='if(gte(val,%d),255,0)'[t%d];"
        % (i, mask_level_threshold(i + 1), i) for i in range(1, count))
    # 链式合成：每个 maskedmerge 的 base 是**上一级的输出**，必须以 "[o{i-1}]..." 显式引用。
    # 末尾补上 ";"，把最后一级的输出作为**下一段**的输入标签接给 scale
    # （写成 "...maskedmerge[oK]scale=..." 会被当成同一个滤镜的参数 → ffmpeg 报 Invalid argument）
    chain_parts = []
    prev = "p0"
    for i in range(1, count):
        chain_parts.append("[%s][p%d][t%d]maskedmerge[o%d]" % (prev, i, i, i))
        prev = "o%d" % i
    chain = ";".join(chain_parts) + ";"
    return (
        "[0:v:0]%s,split=2[a][b];"
        "[b]scale=%d:%d,split=%d%s;%s"
        "[1:v]%s,split=%d[m0]%s;"
        "[m0]lut=y='if(gte(val,1),255,0)',%s[mu];%s"
        "%s[%s]scale=%d:%d:flags=bicubic[bp];"
        "[a][bp][mu]maskedmerge[v]"
        % (setpts_chain, proxy_w, proxy_h, count, pix_out, pix_parts,
           setpts_chain, count, mask_out, mask_full, thresholds,
           chain, prev, width, height))


# ==================== cv2 线程数（进程全局，需引用计数） ====================

def _cv2_threads_enter(cv2):
    """进入"限线程"区间：首个进入者保存原值并把 cv2 线程数压到 CV2_NUM_THREADS。

    必须与 :func:`_cv2_threads_exit` **成对调用**（放在 try/finally 里）。
    引用计数的原因见常量区的注释：同进程可并发跑多个导出任务。
    """
    global _CV2_THREAD_USERS, _CV2_THREAD_SAVED
    with _CV2_THREAD_LOCK:
        if _CV2_THREAD_USERS == 0:
            try:
                saved = cv2.getNumThreads()
                cv2.setNumThreads(CV2_NUM_THREADS)
                _CV2_THREAD_SAVED = saved
            except Exception:
                # 拿不到/设不上就整段放弃，绝不让它影响脱敏本身
                _CV2_THREAD_SAVED = None
        _CV2_THREAD_USERS += 1


def _cv2_threads_exit(cv2):
    """离开"限线程"区间：最后一个离开者恢复原值"""
    global _CV2_THREAD_USERS, _CV2_THREAD_SAVED
    with _CV2_THREAD_LOCK:
        if _CV2_THREAD_USERS <= 0:      # 兜底：未配对的 exit 不把计数带成负数
            return
        _CV2_THREAD_USERS -= 1
        if _CV2_THREAD_USERS == 0 and _CV2_THREAD_SAVED is not None:
            try:
                cv2.setNumThreads(_CV2_THREAD_SAVED)
            except Exception:
                pass
            _CV2_THREAD_SAVED = None


def _enlarge_pipe(fileobj, size=PIPE_BUFFER_BYTES):
    """把管道容量放大到 ``size`` 字节（返回实际容量，失败返回 0）

    为什么需要：掩膜一帧 129.6 KB，而 Linux 管道默认只有 64 KB ——
    生产者一帧都塞不满就得阻塞，两趟管线于是退化成**逐帧锁步**（加法而非 max）。
    实测放大到 1 MB（≈8 帧）后 8.682s → 7.752s，且输出 sha256 逐字节不变。

    ``F_SETPIPE_SZ`` 是 Linux 专有；上限受 ``/proc/sys/fs/pipe-max-size`` 约束，
    超限时内核返回 EPERM —— 一律吞掉异常并返回 0（**这只是提速手段，失败必须无副作用**）。
    非 Linux（本地开发）下 `fcntl` 也没有该常量，同样走这条路径。
    """
    try:
        import fcntl
        return int(fcntl.fcntl(fileobj.fileno(), fcntl.F_SETPIPE_SZ, int(size)))
    except Exception:
        return 0


# ==================== 检测降频的两个纯函数 ====================

def mask_rect(box, pw, ph, ratio=MASK_PAD):
    """人脸框 → 掩膜矩形（按 ``ratio`` 外扩后裁到画面内）；退化返回 None

    抽成纯函数是因为它现在有**两个**用处，必须完全一致：
    画掩膜（`cv2.rectangle`）与判定"这份掩膜是否过期"的 ROI（`_roi_change_ratio`）。
    两处若各写一遍，门控就会去量一个和实际掩膜不同的区域。
    """
    bx, by, bw, bh = box
    x0 = int(max(0, round(bx - bw * ratio)))
    y0 = int(max(0, round(by - bh * ratio)))
    x1 = int(min(pw, round(bx + bw * (1.0 + ratio))))
    y1 = int(min(ph, round(by + bh * (1.0 + ratio))))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _roi_change_ratio(gray, key_gray, boxes, pw, ph,
                      diff=DETECT_ROI_DIFF, ratio=MASK_PAD):
    """已覆盖的人脸 ROI 内「变化像素占比」的最大值（0..1）；无法判定时返回 1.0

    ⚠️ ``key_gray`` 必须是**最近一次检测帧**的灰度图，不能是前一帧：
       脸以每帧 1px 匀速移动时帧间差始终很小，与前一帧比会让门控永不打开、
       框无限过期（这正是实现首版踩过的坑）。

    返回 1.0（= 必须重检）的三种情况：没有框 / 框退化 / ROI 为空。
    """
    import numpy as np      # 本模块不在顶层导入重依赖（见 desensitize_video_file 的检查段）
    # 没有框 = 根本没有"已覆盖区域"可言，必须重检：
    #   若返回 0.0，调用方会读成"画面一切已覆盖、无需检测"，人脸一旦出现就永远不会被检测到。
    #   当前调用点在 `last_boxes` 非空时才走门控，故此处是**防回归的保守兜底**（勿删）。
    if not boxes:
        return 1.0
    worst = 0.0
    for box in boxes:
        rect = mask_rect(box, pw, ph, ratio)
        if rect is None:
            return 1.0
        x0, y0, x1, y1 = rect
        cur = gray[y0:y1, x0:x1]
        if cur.size == 0:
            return 1.0
        prev = key_gray[y0:y1, x0:x1]
        changed = np.abs(cur.astype(np.int16) - prev.astype(np.int16)) > diff
        value = float(changed.mean())
        if value > worst:
            worst = value
    return worst


# ==================== 主流程 ====================

def desensitize_video_file(src_path, dst_path, logger=None, timeout=None,
                           on_progress=None):
    """对视频做人脸区域脱敏

    :param src_path: 明文视频路径（调用方负责先解密）
    :param dst_path: 输出路径（统一 H.264 MP4）
    :param logger: 可选 Flask logger
    :param timeout: 秒；缺省按 ``max(600, 时长*30)``
    :param on_progress: 可选回调 ``(frames_done)``，每约 120 帧一次（帧级进度，
                        与导出服务的**文件级**进度回调不同，故不共用）
    :return: (ok, err, stats)
             stats 含 frames / detect_runs / detect_ratio / detected_frames /
             mask_frames / faces / proxy / source / fps / elapsed / audio_removed /
             version。三个检测计数**含义不同**（v6 起不再相等）：
             `detect_runs` = 实际跑 YuNet 的帧数 ≤ `frames`；
             `detected_frames` = 其中检出人脸的帧数；
             `mask_frames` = 掩膜非空的帧数（= 真正被脱敏的帧，正常等于 frames）
    """
    stats = {}
    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        return False, "未找到可用的 ffmpeg（imageio-ffmpeg 不可用）", stats

    model = model_path()
    if not model:
        return False, ("未找到人脸检测模型 %s（应为 <应用根>/models/，"
                       "或用 FACE_DETECT_MODEL_PATH 指定）" % MODEL_NAME), stats

    try:
        import av  # noqa: F401  （探针/帧数校验用）
        import cv2
        import numpy as np
    except Exception as exc:  # pragma: no cover - 依赖缺失属部署问题
        return False, ("视频脱敏依赖不可用（需 opencv-python-headless / numpy / av）：%s"
                       % exc), stats

    if not hasattr(cv2, "FaceDetectorYN"):
        return False, ("当前 OpenCV（%s）不含 FaceDetectorYN，无法做人脸检测"
                       % getattr(cv2, "__version__", "?")), stats

    try:
        info = probe_video(src_path)
    except Exception as exc:
        return False, "无法解析视频（文件损坏或格式不受支持）：%s" % exc, stats
    if not info:
        return False, "视频不含任何视频轨", stats
    if info["video_streams"] != 1:
        return False, ("源含 %d 条视频轨，当前仅支持单视频轨（多轨多为彩色+深度/红外），"
                       "已跳过以避免静默丢轨" % info["video_streams"]), stats

    width, height = info["width"], info["height"]
    if width < 32 or height < 32:
        return False, "视频分辨率异常（%dx%d）" % (width, height), stats

    proxy_w, proxy_h = _proxy_size(width, height)
    fps = info["fps"] if info["fps"] and info["fps"] > 0 else 25.0
    if timeout is None:
        timeout = max(MIN_TIMEOUT_SECONDS,
                      int((info["duration"] or 0.0) * 30) or MIN_TIMEOUT_SECONDS)

    # ---- Pass1：解码 → 代理尺寸 bgr24 ----
    cmd_decode = [ffmpeg, "-v", "error", "-i", src_path, "-map", "0:v:0",
                  "-an", "-sn",
                  "-fps_mode", "passthrough",
                  "-vf", "scale=%d:%d" % (proxy_w, proxy_h),
                  "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

    # ---- Pass2：马赛克（块边长按人脸尺寸分级）+ 掩膜合成 + 编码 + 去音轨 ----
    # 块边长按**人脸短边**选级：近景大脸用更粗的块，否则等于没脱敏。
    # 各级马赛克与合成都在代理尺度上做，最后只升采样一次。
    ladder = mosaic_ladder(proxy_w, proxy_h)
    filter_complex = build_filter_complex(
        fps, ladder, proxy_w, proxy_h, width, height)

    cmd_encode = [ffmpeg, "-y", "-v", "error",
                  "-i", src_path,
                  # 让 ffmpeg 的 demux 线程提前把掩膜读进队列，而不是等滤镜图用到才读：
                  # 管道默认 64 KB < 一帧掩膜 129.6 KB，不放深就是逐帧锁步（见常量注释）
                  *(["-thread_queue_size", str(MASK_INPUT_QUEUE_SIZE)]
                    if MASK_INPUT_QUEUE_SIZE > 0 else []),
                  "-f", "rawvideo", "-pix_fmt", "gray",
                  "-s", "%dx%d" % (proxy_w, proxy_h), "-r", "%.6f" % fps, "-i", "-",
                  # 滤镜链线程数显式设为 1（默认 = 核数）：代理帧只有 480x270、节点却有 12+ 个，
                  # 每个节点再切 16 条 slice 会让分派/同步开销远超计算收益。实测 Pass2
                  # 16.755s → 8.130s（2.06×），且输出 sha256 逐字节不变（见常量区注释）
                  "-filter_complex_threads", str(FILTER_COMPLEX_THREADS),
                  "-filter_complex", filter_complex,
                  "-map", "[v]", "-an",
                  "-fps_mode", "passthrough",
                  "-c:v", "libx264", "-preset", ENCODE_PRESET, "-crf", str(ENCODE_CRF),
                  "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                  dst_path]

    fd1, log_decode = tempfile.mkstemp(suffix=".log", prefix="vdes_dec_")
    os.close(fd1)
    fd2, log_encode = tempfile.mkstemp(suffix=".log", prefix="vdes_enc_")
    os.close(fd2)

    proc_decode = proc_encode = None
    frames = detected_frames = face_count = 0
    detect_runs = mask_frames = 0
    started = time.monotonic()
    failure = None
    succeeded = False
    # cv2 线程数是进程全局设置：默认 = 核数(16)，而 YuNet 单帧输入只有 480x270 →
    # 同样落在"线程在等同步"的坑里（实测 Pass1 8.39s → cv2=4 时 7.25s）。
    # 用引用计数进出，保证并发导出时不会互相把设置改回去（见常量区注释）。
    _cv2_threads_enter(cv2)
    try:
        with open(log_decode, "wb") as fh_dec, open(log_encode, "wb") as fh_enc:
            proc_decode = subprocess.Popen(cmd_decode, stdout=subprocess.PIPE,
                                           stderr=fh_dec)
            proc_encode = subprocess.Popen(cmd_encode, stdin=subprocess.PIPE,
                                           stderr=fh_enc)
            # 放大两条管道（Linux 专有，失败静默忽略）：解码输出 388 KB/帧 + 掩膜 129.6 KB/帧
            # 都远超默认的 64 KB，不放大的话两个生产者每帧都会阻塞在写上（见常量注释）
            if PIPE_BUFFER_BYTES > 0:
                _enlarge_pipe(proc_decode.stdout)
                _enlarge_pipe(proc_encode.stdin)

            detector = cv2.FaceDetectorYN.create(
                model, "", (proxy_w, proxy_h),
                DETECT_SCORE_THRESHOLD, DETECT_NMS_THRESHOLD, DETECT_TOP_K)

            in_frame_size = proxy_w * proxy_h * 3
            last_boxes = []          # 检测失败时沿用上一帧的框，避免瞬移漏出
            level_faces = {}         # 各马赛克级被用到的框次数（诊断/审计用）
            key_gray = None          # 最近一次检测帧的灰度图（门控基准，非前一帧）
            staleness = 0            # 距最近一次检测已过去多少帧
            while True:
                if time.monotonic() - started > timeout:
                    failure = "视频脱敏超时（%d 秒）" % timeout
                    break
                buf = proc_decode.stdout.read(in_frame_size)
                if len(buf) < in_frame_size:
                    break          # 解码结束（或异常中断，下面按 returncode 判定）
                small = np.frombuffer(buf, np.uint8).reshape(proxy_h, proxy_w, 3)

                # ---- 检测降频（v6）：只在"这份掩膜已经过期"时才重新检测 ----
                # 灰度图供门控使用，必须在 detect **之前**取（见常量区注释）。
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if (last_boxes and key_gray is not None
                        and staleness < DETECT_MAX_STALENESS):
                    run_detect = (_roi_change_ratio(
                        gray, key_gray, last_boxes, proxy_w, proxy_h)
                        > DETECT_ROI_CHANGE_RATIO)
                else:
                    # 无框（含首帧）或已达陈旧度硬上限 → 必须检测
                    run_detect = True

                if run_detect:
                    detect_runs += 1
                    staleness = 0
                    key_gray = gray          # 门控基准 = 本次检测帧，不是前一帧
                    _, faces = detector.detect(small)
                    if faces is not None and len(faces):
                        # 画面可能有多人：全部纳入掩膜（多画几个矩形几乎零成本，
                        # 只保最大框会漏掉次要人脸）
                        ordered = sorted(faces, key=lambda row: -float(row[-1]))
                        last_boxes = [
                            (float(r[0]), float(r[1]), float(r[2]), float(r[3]))
                            for r in ordered[:MAX_FACES_PER_FRAME]]
                        detected_frames += 1
                        face_count += len(last_boxes)
                    # 注意：本次未检出人脸时**保留** last_boxes（沿用旧框避免瞬移漏出），
                    # 但 key_gray 已更新 —— 若画面里确实没脸，门控基准也应跟着走，
                    # 否则"无人脸"的静止画面会被反复判为变化、退化成每帧检测
                else:
                    staleness += 1

                mask = np.zeros((proxy_h, proxy_w), dtype=np.uint8)
                if last_boxes:
                    # 按人脸短边选马赛克级：近景大脸用更粗的块，远景小脸用更细的块；
                    # 同一帧内重叠时让**更粗的一级覆盖更细的一级**（按级号升序绘制）
                    painted = [(mosaic_level(min(bw, bh), ladder), bx, by, bw, bh)
                               for (bx, by, bw, bh) in last_boxes]
                    for level, bx, by, bw, bh in sorted(painted, key=lambda it: it[0]):
                        level_faces[level] = level_faces.get(level, 0) + 1
                        # 与门控共用同一个纯函数，避免两处矩形定义漂移
                        rect = mask_rect((bx, by, bw, bh), proxy_w, proxy_h)
                        if rect:
                            cv2.rectangle(mask, (rect[0], rect[1]), (rect[2], rect[3]),
                                          mask_level_value(level), -1)
                    mask_frames += 1

                try:
                    proc_encode.stdin.write(mask.tobytes())
                except (BrokenPipeError, OSError):
                    # 编码进程提前退出（滤镜参数错误等）：跳出后统一读 stderr 报错
                    break

                frames += 1
                if on_progress and frames % 120 == 0:
                    try:
                        on_progress(frames)
                    except Exception:
                        pass

            try:
                proc_decode.stdout.close()
            except Exception:
                pass
            try:
                proc_encode.stdin.close()
            except Exception:
                pass
            try:
                rc_decode = proc_decode.wait(timeout=PROCESS_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                proc_decode.kill()
                rc_decode = -1
            try:
                rc_encode = proc_encode.wait(timeout=PROCESS_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                proc_encode.kill()
                rc_encode = -1

        elapsed = time.monotonic() - started
        stats.update({
            "version": VERSION_TAG,
            "frames": frames,
            # 检测降频（v6）后这三个数含义不同，别混用：
            #   detect_runs     —— **实际跑了 YuNet** 的帧数（≤ frames）
            #   detected_frames —— 其中**检出人脸**的帧数
            #   mask_frames     —— **掩膜非空**的帧数（= 真正被脱敏的帧，正常等于 frames）
            "detect_runs": detect_runs,
            "detect_ratio": round(detect_runs / float(frames), 4) if frames else 0.0,
            "detected_frames": detected_frames,
            "mask_frames": mask_frames,
            "faces": face_count,
            "proxy": "%dx%d" % (proxy_w, proxy_h),
            "source": "%dx%d" % (width, height),
            "fps": round(fps, 4),
            "elapsed": round(elapsed, 3),
            "audio_removed": info["audio_streams"] > 0,
            "mosaic_ladder": ladder,
            "mosaic_levels": {str(k): v for k, v in sorted(level_faces.items())},
        })

        if failure:
            return False, failure, stats
        # 两端都可能非 0，且**必须先报 encode 端**：decode 端的 Broken pipe 通常只是
        # encode 先退出（stdin 的读端消失）造成的连锁反应，只报 decode 会把排查引向
        # 错误的一头 —— 实测踩过：报"解码进程异常退出（rc=224）…Broken pipe"，
        # 真因在编码端（第一版 v4 基准脚本就是这样被带偏的）。
        if rc_encode != 0:
            return False, "编码进程异常退出（rc=%d）：%s" % (
                rc_encode, _tail(log_encode) or "无输出"), stats
        if rc_decode != 0:
            encode_tail = _tail(log_encode)
            return False, "解码进程异常退出（rc=%d）：%s%s" % (
                rc_decode, _tail(log_decode) or "无输出",
                "；编码端日志：%s" % encode_tail if encode_tail else ""), stats
        if frames == 0:
            return False, "未从源视频读到任何帧", stats
        if not os.path.exists(dst_path) or os.path.getsize(dst_path) == 0:
            return False, "视频脱敏输出为空", stats

        # 帧数一致性：不等说明掩膜与画面错位，会有部分帧根本没被脱敏 ——
        # 宁可整条跳过，也不能把"以为脱敏了"的视频发出去
        try:
            out_frames = _output_frame_count(dst_path)
        except Exception:
            out_frames = 0
        if out_frames and out_frames != frames:
            return False, ("输出帧数（%d）与输入（%d）不一致，掩膜可能与画面错位，"
                           "已跳过以避免部分帧未脱敏" % (out_frames, frames)), stats
        stats["output_frames"] = out_frames or frames
        succeeded = True

        if logger:
            logger.info(
                "视频人脸脱敏完成：%dx%d → 代理 %dx%d，%d 帧（其中 %d 帧跑检测 / %d 帧检出人脸"
                " / %d 帧掩膜非空，共 %d 个框），"
                "马赛克阶梯 %s（块边长/代理 px），各级命中 %s，耗时 %.2fs%s",
                width, height, proxy_w, proxy_h, frames, detect_runs, detected_frames,
                mask_frames, face_count,
                ladder, dict(sorted(level_faces.items())), elapsed,
                "，已移除音轨" if info["audio_streams"] else "")
            if detected_frames == 0:
                logger.warning(
                    "视频人脸脱敏：%s 全程未检出人脸（共 %d 帧、跑了 %d 次检测），"
                    "输出等同原画面重编码。若该视频确有人脸，请检查检测模型是否适配"
                    "（低照度/大角度/遮挡）或核对检测降频阈值 DETECT_ROI_CHANGE_RATIO=%s",
                    os.path.basename(src_path), frames, detect_runs,
                    DETECT_ROI_CHANGE_RATIO)
        return True, None, stats

    except Exception as exc:
        return False, "视频脱敏异常：%s: %s" % (type(exc).__name__, exc), stats
    finally:
        for proc in (proc_decode, proc_encode):
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        for path in (log_decode, log_encode):
            try:
                os.remove(path)
            except OSError:
                pass
        # 失败时清掉半成品输出：残缺 mp4 若被当作正常结果打包出去，
        # 界面只会显示"成功"，用户无从察觉视频其实没处理完
        if not succeeded:
            try:
                if os.path.exists(dst_path):
                    os.remove(dst_path)
            except OSError:
                pass
        # 必须与上面的 _cv2_threads_enter 成对：最后一个离开者恢复进程原有的 cv2 线程数
        # （放 finally 保证任何异常路径都会恢复，不会把整个 API 进程的 cv2 永久压到 4 线程）
        _cv2_threads_exit(cv2)


# ==================== 段级并行（v6）：切分 / 拼接 / 编排 ====================

def segment_plan(duration, target=SEGMENT_TARGET_SECONDS, maximum=SEGMENT_MAX,
                 minimum_duration=SEGMENT_MIN_DURATION):
    """按时长决定切几段（纯函数，便于单测）

    **段数不是越多越好**：v20 实测（真 v5 单趟中位数 8.675s 为分母）
    2 段 1.467×、**3 段 1.634×**、4 段 1.643×，而 3 段与 4 段的**并发绝对墙钟**
    落在噪声内（5.308s vs 5.279s，±0.12s）—— 说明到 3 段就已吃满 CPU，再切只是把同一份
    CPU 切得更碎，而每段的固定开销（2 个 ffmpeg 进程 + 模型加载）占比反而上升。
    故按"每段约 30s"推出段数并封顶 4。
    """
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        return 1
    if seconds < float(minimum_duration):
        return 1
    count = int(round(seconds / float(target)))
    return max(1, min(int(maximum), count))


def _split_segments(ffmpeg, src_path, count, duration, workdir, logger=None):
    """按关键帧切成 ≤count 段（``-c copy``，零重编码、不失帧）；返回段文件列表

    片段容器刻意用 **matroska** 而非 mp4：`-c copy` 进 mp4 对编码有约束
    （源是 vp9/av1 之类会直接失败），mkv 对各种编码都宽容。片段只是中间产物，
    最终输出仍由 `desensitize_video_file` 统一重编码为 H.264 MP4。

    返回 ``[]`` 或单元素列表都表示"切不动"（源没周期关键帧、时长过短、编码不支持），
    由调用方退回单趟路径 —— 这里不抛异常。
    """
    pattern = os.path.join(workdir, "seg_%03d.mkv")
    cmd = [ffmpeg, "-y", "-v", "error",
           "-i", src_path, "-map", "0:v:0", "-an", "-sn",
           "-c", "copy",
           "-f", "segment",
           "-segment_time", "%.6f" % max(1.0, float(duration) / max(1, count)),
           "-reset_timestamps", "1",
           "-segment_format", "matroska",
           pattern]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except Exception as exc:                            # noqa: BLE001
        if logger:
            logger.warning("视频段级并行：切分异常（%s），退回单趟路径", exc)
        return []
    if proc.returncode != 0:
        if logger:
            logger.warning(
                "视频段级并行：切分失败（rc=%d），退回单趟路径：%s", proc.returncode,
                proc.stderr.decode("utf-8", "replace").strip()[-200:])
        return []
    return sorted(os.path.join(workdir, name) for name in os.listdir(workdir)
                  if name.startswith("seg_") and name.endswith(".mkv"))


def _concat_segments(ffmpeg, parts, dst_path, workdir):
    """``-f concat -c copy`` 把各段输出拼回单一 mp4；返回 (ok, err)"""
    list_path = os.path.join(workdir, "concat.txt")
    try:
        with open(list_path, "w", encoding="utf-8") as handle:
            for part in parts:
                handle.write("file '%s'\n" % part.replace("'", "'\\''"))
        proc = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", "-movflags", "+faststart",
             dst_path], capture_output=True)
    except Exception as exc:                            # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)
    if proc.returncode != 0:
        return False, proc.stderr.decode("utf-8", "replace").strip()[-300:]
    return True, None


def _merge_segment_stats(results, info, count, elapsed, merged_frames):
    """把各段 stats 汇总成一份"整条视频"口径的 stats（字段与单趟路径对齐）"""
    def _total(key):
        return sum(int((row[2].get(key) or 0)) for row in results)

    levels = {}
    for row in results:
        for level, hits in (row[2].get("mosaic_levels") or {}).items():
            levels[level] = levels.get(level, 0) + int(hits)
    frames = _total("frames")
    first = results[0][2]
    return {
        "version": VERSION_TAG,
        "frames": frames,
        "output_frames": merged_frames or frames,
        "segments": count,
        # 检测降频的审计字段必须**按段累加**，否则并行路径下无法核对降频是否生效
        "detect_runs": _total("detect_runs"),
        "detect_ratio": round(_total("detect_runs") / float(frames), 4) if frames else 0.0,
        "detected_frames": _total("detected_frames"),
        "mask_frames": _total("mask_frames"),
        "faces": _total("faces"),
        "proxy": first.get("proxy"),
        "source": "%dx%d" % (info["width"], info["height"]),
        "fps": first.get("fps"),
        "elapsed": round(elapsed, 3),
        "audio_removed": info.get("audio_streams") > 0,
        "mosaic_ladder": first.get("mosaic_ladder"),
        "mosaic_levels": {str(k): v for k, v in sorted(
            ((int(k), v) for k, v in levels.items()))},
        "segment_frames": [int(row[2].get("frames") or 0) for row in results],
    }


def desensitize_video_file_parallel(src_path, dst_path, logger=None, timeout=None,
                                    on_progress=None, segments=None):
    """段级并行包装：切成 n 段并发跑完整管线，再拼回单一 mp4（v20 实测 1.634×）

    与 :func:`desensitize_video_file` 的分工：

    - 段数算出为 1（短片 / 探测不到时长）、切不动（无周期关键帧）、任一段失败、
      拼接失败、**拼接后帧数不符** → 一律**退回单趟路径**重跑整条。
      退回的永远是**单趟脱敏**，绝不是"原样导出" —— 后者等于人脸未脱敏却标称已脱敏
    - 成功时保证 ``output_frames == 各段输出帧数之和``，而各段内部已各自保证
      "输入帧 == 输出帧"（framesync 不变式），串起来即"整条输出帧数 == 整条输入帧数"

    为什么单独一个函数而不把 :func:`desensitize_video_file` 改成调度器：那个函数被大量
    单测与质量验证脚本按"处理整条视频"的契约依赖（stats 语义、帧数守恒用例），
    改成调度器会让那些断言失去意义。

    :param segments: 强制段数（测试用）；None = 按 `segment_plan` 从时长推算
    """
    def _fallback():
        return desensitize_video_file(src_path, dst_path, logger=logger,
                                      timeout=timeout, on_progress=on_progress)

    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        return _fallback()
    try:
        info = probe_video(src_path)
    except Exception:                                   # noqa: BLE001
        return _fallback()
    if not info or not info.get("width"):
        return _fallback()

    count = segment_plan(info.get("duration")) if segments is None else int(segments)
    if count <= 1:
        return _fallback()

    workdir = tempfile.mkdtemp(prefix="vdes_seg_")
    started = time.monotonic()
    try:
        parts_src = _split_segments(ffmpeg, src_path, count, info["duration"],
                                    workdir, logger)
        if len(parts_src) <= 1:
            if logger:
                logger.info("视频段级并行：源未含足够关键帧，退回单趟路径（%s）",
                            os.path.basename(src_path))
            return _fallback()

        parts_out = [os.path.join(workdir, "out_%03d.mp4" % index)
                     for index in range(len(parts_src))]
        results = [None] * len(parts_src)
        progress = [0] * len(parts_src)
        lock = threading.Lock()

        def _bump(index, done):
            with lock:
                progress[index] = max(progress[index], int(done or 0))
                total = sum(progress)
            if on_progress:
                try:
                    on_progress(total)
                except Exception:                       # noqa: BLE001
                    pass

        def _worker(index):
            try:
                results[index] = desensitize_video_file(
                    parts_src[index], parts_out[index], logger=None, timeout=None,
                    on_progress=lambda done, i=index: _bump(i, done))
            except Exception as exc:                    # noqa: BLE001
                results[index] = (False, "%s: %s" % (type(exc).__name__, exc), {})

        threads = [threading.Thread(target=_worker, args=(index,),
                                    name="vdes-seg-%d" % index)
                   for index in range(len(parts_src))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        failed = [(index, row[1]) for index, row in enumerate(results)
                  if not row or not row[0]]
        if failed:
            if logger:
                logger.warning(
                    "视频段级并行：%d/%d 段失败，退回单趟路径重跑整条（首个失败：%s）",
                    len(failed), len(results), failed[0][1])
            return _fallback()

        total_frames = sum(int(row[2].get("frames") or 0) for row in results)
        ok_concat, concat_err = _concat_segments(ffmpeg, parts_out, dst_path, workdir)
        if not ok_concat:
            if logger:
                logger.warning("视频段级并行：拼接失败，退回单趟路径重跑整条：%s", concat_err)
            return _fallback()

        try:
            merged_frames = _output_frame_count(dst_path)
        except Exception:                               # noqa: BLE001
            merged_frames = 0
        if merged_frames and merged_frames != total_frames:
            if logger:
                logger.warning(
                    "视频段级并行：拼接后 %d 帧 ≠ 各段合计 %d 帧，判失败并退回单趟",
                    merged_frames, total_frames)
            return _fallback()

        elapsed = time.monotonic() - started
        stats = _merge_segment_stats(results, info, len(parts_src), elapsed,
                                     merged_frames)
        if logger:
            logger.info(
                "视频人脸脱敏（段级并行）：%d 段并发，合计 %d 帧（跑检测 %d 帧 / 掩膜非空 %d 帧），"
                "耗时 %.2fs（单段耗时 %s）", len(parts_src), stats["frames"],
                stats["detect_runs"], stats["mask_frames"], elapsed,
                [round(float(row[2].get("elapsed") or 0), 2) for row in results])
        if on_progress:
            try:
                on_progress(stats["frames"])
            except Exception:                           # noqa: BLE001
                pass
        return True, None, stats
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
