FROM python:3.13-slim

WORKDIR /app

# 使用国内apt镜像源加速
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖：
# - ffmpeg: 视频转码
# - build-essential: g++ 编译 pyk4a（源码包，非预编译 wheel）
# - libusb-1.0-0-dev: K4A SDK USB 通信依赖
# - libglib2.0-0: opencv-python-headless 运行时依赖
# - wget unzip: 下载解压 Orbbec SDK
# - libegl1: 深度引擎 libdepthengine.so.2.0 依赖的 libEGL.so.1
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libusb-1.0-0-dev \
    libglib2.0-0 \
    wget \
    unzip \
    libegl1 \
    && rm -rf /var/lib/apt/lists/*

# 下载并安装 Orbbec SDK K4A Wrapper v1.10.5（Linux x64）
# v1.x 基于 Azure Kinect API，pyk4a 直接兼容；v2.x 基于新 OrbbecSDK v2，API 不兼容
# 头文件 → /usr/local/include/k4a/，库文件 → /usr/local/lib/，ldconfig 刷新缓存
# 如 Gitee 下载失败，可提前下载 zip 到 back/orbbec_sdk.zip，将下面 wget 行改为 COPY orbbec_sdk.zip /tmp/
ARG ORBBEC_SDK_URL=https://gitee.com/orbbecdeveloper/OrbbecSDK-K4A-Wrapper/releases/download/v1.10.5/OrbbecSDK_K4A_Wrapper_v1.10.5_linux_202510221013.zip
RUN wget -q -O /tmp/orbbec_sdk.zip "${ORBBEC_SDK_URL}" && \
    unzip -q /tmp/orbbec_sdk.zip -d /tmp/orbbec_sdk && \
    INC_DIR=$(dirname $(dirname $(find /tmp/orbbec_sdk -name "k4a.h" -print -quit))) && \
    cp -r $INC_DIR/* /usr/local/include/ && \
    find /tmp/orbbec_sdk -name "*.so*" -exec cp -a {} /usr/local/lib/ \; && \
    ldconfig && \
    rm -rf /tmp/orbbec_sdk /tmp/orbbec_sdk.zip

# 安装 Python 依赖（使用国内pip镜像源）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 创建数据湖与日志目录
RUN mkdir -p data_lake/raw data_lake/cleaned data_lake/feature data_lake/annotation logs
# 深度录制中间产物目录（Orbbec pyk4a 与 RealSense pyrealsense2 共用，容器内本地盘保证写入速度）
RUN mkdir -p /app/orbbec_recordings /app/realsense_recordings

EXPOSE 5000

# 生产环境使用 gunicorn（启动前先创建深度摄像头 USB 设备节点，见 ensure_usb_nodes.py；
# 再等待 MySQL 就绪，见 wait_for_mysql.py，避免首次部署时 MySQL 未就绪导致反复重启）
CMD ["sh", "-c", "python ensure_usb_nodes.py && python wait_for_mysql.py && exec gunicorn -c gunicorn_config.py run:app"]
