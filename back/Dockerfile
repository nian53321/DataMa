FROM python:3.13-slim

WORKDIR /app

# 使用国内apt镜像源加速
RUN sed -i 's/deb.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖（ffmpeg 用于视频转码）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（使用国内pip镜像源）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目代码
COPY . .

# 创建数据湖与日志目录
RUN mkdir -p data_lake/raw data_lake/cleaned data_lake/feature data_lake/annotation logs

EXPOSE 5000

# 生产环境使用 gunicorn
CMD ["gunicorn", "-c", "gunicorn_config.py", "run:app"]
