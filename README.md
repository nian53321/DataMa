# 多模态数据标注与分析处理平台

前后端分离的医学多模态数据管理平台，支持 EEG / ECG / 眼动 / 量表 / 音视频 等数据的采集、脱敏、标注与分析。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + Vite + Element Plus + ECharts + Pinia + vue-router |
| 后端 | Flask 3 + Gunicorn + SQLAlchemy + Flask-Migrate + Celery |
| 认证 | JWT（flask-jwt-extended，access + refresh 双 token） |
| 权限 | 基于角色（admin/doctor/nurse/engineer/annotator）+ 菜单权限 |
| 数据库 | MySQL 8.0 |
| 缓存/队列 | Redis 7 |
| 视频转码 | ffmpeg |
| 数据加密 | 信封加密（master.key 保护每文件 DEK，AES-256-GCM） |
| 外部密钥 | 支持 AgeCog 等外部采集设备加密文件导入（AES-256-CBC + 数据库密钥管理） |
| 数据脱敏 | 字段级脱敏配置（基于角色，admin 不脱敏） |
| 审计追溯 | 操作日志 + 数据快照（修改/删除前自动留档，支持回滚） |
| 部署 | Docker Compose（5 个服务：backend / mysql / redis / celery-worker / frontend） |

## 服务架构

```
                 ┌──────────────────┐
   浏览器 ─8080─>│  frontend (nginx)│
                 │  SPA + /api 反代 │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐    ┌──────────────┐
                 │  backend (Flask) │───>│  mysql 8.0   │
                 │  gunicorn:5000   │    └──────────────┘
                 └────┬──────┬──────┘
                      │      │       ┌──────────────┐
                      │      └──────>│  redis 7     │
                      │              └──────┬───────┘
                      ▼                     │
                 ┌──────────────────┐       │
                 │  celery-worker   │<──────┘
                 │  (异步任务)       │
                 └──────────────────┘
```

## 端口映射

| 服务 | 宿主机端口 | 容器端口 | 说明 |
|---|---|---|---|
| frontend (nginx) | 8080 | 80 | 前端访问入口 `http://localhost:8080` |
| backend (Flask) | 5000 | 5000 | 后端 API（通常通过 nginx 反代，不直接访问） |
| mysql | 3307 | 3306 | 数据库（调试用） |
| redis | 6380 | 6379 | 缓存队列（调试用） |

## 部署前置要求（目标设备为 Windows）

### 必装软件

| 软件 | 版本 | 用途 | 下载 |
|---|---|---|---|
| Docker Desktop | ≥ 4.30 | 容器运行时 | https://www.docker.com/products/docker-desktop/ |

### 安装 Docker Desktop 步骤

1. 下载 Docker Desktop Installer.exe
2. 双击运行，勾选 **Use WSL 2 instead of Hyper-V**（推荐）
3. 安装完成后**重启电脑**
4. 启动 Docker Desktop，等待右下角 Docker 鲸鱼图标变为稳定状态（不再动画）
5. 打开 PowerShell 验证：

   ```powershell
   docker --version
   docker compose version
   docker info
   ```

   三条命令都有正常输出即代表 Docker 环境就绪。

### 系统要求

- Windows 10/11 64 位（Build 19041+）
- 至少 4GB 内存（推荐 8GB+）
- BIOS 开启虚拟化（VT-x / AMD-V）
- WSL2 已安装（Docker Desktop 安装时会自动处理）

### 配置 Docker 镜像加速（国内网络必做）

国内访问 Docker Hub 经常超时，**新设备部署前必须先配镜像加速**：

1. 打开 Docker Desktop → **Settings** → **Docker Engine**
2. 在 JSON 配置中加入 `registry-mirrors` 字段：

   ```json
   {
     "registry-mirrors": [
       "https://docker.m.daocloud.io",
       "https://dockerproxy.com",
       "https://docker.mirrors.ustc.edu.cn",
       "https://hub-mirror.c.163.com"
     ]
   }
   ```

3. 点 **Apply & restart**，等 Docker 重启完成

> **注意**：`docker pull` 命令走 Docker daemon（读镜像加速），但 `docker compose build` 里的 BuildKit 是独立进程，**不读镜像加速也不读 Docker 代理**。如果 build 时报 `auth.docker.io` 连接失败，先用 `docker pull` 把基础镜像拉到本地，再执行 `docker compose up -d --build`（BuildKit 看到本地有缓存就不会走网络）。

### 需要预先拉取的基础镜像

```powershell
docker pull python:3.13-slim
docker pull node:20-alpine
docker pull nginx:1.27-alpine
docker pull mysql:8.0
docker pull redis:7-alpine
```

## 部署方式

### 方式 A：一键脚本部署（推荐）

```powershell
# 1. 将整个项目目录拷贝到目标设备（如 D:\DataManagement）
# 2. 在项目根目录（docker-compose.yml 所在目录）打开 PowerShell
cd D:\DataManagement

# 3. 执行一键部署脚本
.\deploy.ps1
```

脚本会自动完成：
- 检查 Docker 环境
- 生成 `back/.env`（自动生成强密码和 JWT 密钥）
- 同步根目录 `.env`（供 docker-compose.yml 读取 DB_PASSWORD）
- 拉镜像 + 构建镜像 + 启动所有服务
- 等待后端健康检查通过
- 打印访问信息和默认账号

### 方式 B：手动部署

#### 步骤 1：配置环境变量

```powershell
cd D:\DataManagement\back
Copy-Item .env.example .env
```

用编辑器打开 `back/.env`，**必须修改**以下两项：

| 字段 | 说明 | 生成方式 |
|---|---|---|
| `DB_PASSWORD` | MySQL root 密码 | 自定义强密码 |
| `JWT_SECRET_KEY` | JWT 签名密钥 | `python -c "import secrets;print(secrets.token_hex(32))"` |

**注意**：保持 `DB_HOST=mysql`、`CELERY_BROKER_URL=redis://redis:6379/0`、`CELERY_RESULT_BACKEND=redis://redis:6379/1` 不要改回 localhost，这是容器间服务名通信的必需配置。

#### 步骤 2：同步根目录 .env

在 `docker-compose.yml` 同级目录（项目根目录）创建 `.env` 文件，写入：

```
DB_PASSWORD=与back/.env中相同的密码
```

这是为了让 mysql 容器初始化时使用相同的 root 密码。

#### 步骤 3：启动服务

```powershell
cd D:\DataManagement
docker compose up -d --build
```

首次启动需要拉取镜像 + npm install + pip install，约 5-10 分钟。

#### 步骤 4：验证

```powershell
# 查看服务状态
docker compose ps

# 健康检查（通过 nginx 反代）
curl http://localhost:8080/api/health
# 期望返回: {"code":200,"message":"ok","data":{"status":"ok",...}}

# 直连后端（调试用）
curl http://localhost:5000/api/health

# 查看日志（如有问题）
docker compose logs -f backend
docker compose logs -f frontend
```

浏览器访问 **http://localhost:8080** → 默认账号 `admin / admin123`（首次登录后请立即改密码）。

## 配置项说明

### `back/.env` 完整字段

| 字段 | 默认值 | 说明 |
|---|---|---|
| `FLASK_ENV` | `production` | 环境标识，生产环境必须为 production |
| `FLASK_APP` | `run.py` | Flask 应用入口 |
| `DB_USER` | `root` | MySQL 用户名 |
| `DB_PASSWORD` | **必改** | MySQL 密码（弱密码 123456 会拒绝启动） |
| `DB_HOST` | `mysql` | **容器部署保持 mysql**，本机直跑改 localhost |
| `DB_PORT` | `3306` | MySQL 端口（容器内端口，不是宿主机映射端口） |
| `DB_NAME` | `data_management` | 数据库名 |
| `JWT_SECRET_KEY` | **必改** | JWT 签名密钥（默认值会拒绝启动） |
| `CORS_ORIGINS` | `http://localhost,http://127.0.0.1` | 跨域白名单，逗号分隔 |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Celery broker（容器部署保持 redis） |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` | Celery 结果后端 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `GUNICORN_WORKERS` | `4` | Gunicorn worker 数量 |

> **未在 .env 中列出但代码会读取的配置**（一般用默认值即可，详见 [back/app/config.py](file:///d:/Py_Project/DataManagement/back/app/config.py)）：
> - `BASE_DIR`、`DATA_LAKE_DIR`、`MASTER_KEY_PATH`：数据湖与主密钥路径
> - `ENCRYPTION_ENABLED`：是否启用文件加密（默认启用）
> - `MAX_UPLOAD_SIZE`：上传大小限制
> - `JWT_ACCESS_TOKEN_EXPIRES` / `JWT_REFRESH_TOKEN_EXPIRES`：token 过时间

### 端口占用检查

```powershell
# 检查端口是否被占用（注意是宿主机映射端口，不是容器内端口）
netstat -ano | findstr ":8080 "   # frontend
netstat -ano | findstr ":5000 "   # backend
netstat -ano | findstr ":3307 "   # mysql
netstat -ano | findstr ":6380 "   # redis
```

如端口被占用，修改 `docker-compose.yml` 中对应服务的端口映射（左边的宿主机端口），如 `"8080:80"` 改为 `"9090:80"`，访问地址相应改为 `http://localhost:9090`。

## 常用运维命令

```powershell
# 查看服务状态
docker compose ps

# 查看实时日志
docker compose logs -f                    # 所有服务
docker compose logs -f backend            # 指定服务

# 重启服务
docker compose restart backend
docker compose restart                    # 重启所有

# 停止服务（保留数据）
docker compose stop

# 启动已停止的服务
docker compose start

# 完全卸载（保留 volume 数据）
docker compose down

# 完全卸载并删除数据（谨慎！会丢失数据库和数据湖文件）
docker compose down -v

# 重新构建镜像（代码变更后）
docker compose up -d --build backend
docker compose up -d --build frontend

# 进入容器调试
docker compose exec backend bash
docker compose exec mysql mysql -uroot -p
```

## 数据备份与迁移

### 完整备份清单（四者缺一不可）

| 备份项 | 位置 | 丢失后果 |
|---|---|---|
| MySQL 数据 | `mysql-data` volume | 业务数据全丢 |
| 数据湖文件 | `backend-data` volume | 所有原始数据全丢 |
| `master.key` | `back/master.key` | **加密文件永久无法解密** |
| 外部密钥 | MySQL 中的 `external_key` 表 | 外部加密文件无法解密 |

### 备份 MySQL 数据（含外部密钥）

```powershell
# 导出（包含 external_key 表）
docker compose exec mysql mysqldump -uroot -p<DB_PASSWORD> data_management > backup_$(Get-Date -Format 'yyyyMMdd').sql

# 恢复
Get-Content backup_20260728.sql | docker compose exec -T mysql mysql -uroot -p<DB_PASSWORD> data_management
```

### 备份数据湖文件

```powershell
# 数据湖在 docker volume 中（backend-data）
docker run --rm -v datamanagement_backend-data:/data -v ${PWD}:/backup alpine `
  tar czf /backup/data_lake.tar.gz -C /data .
```

### 备份 master.key（**关键！丢失后所有加密数据无法恢复**）

```powershell
Copy-Item back\master.key back\master.key.backup
```

> **重要**：`master.key` 是信封加密的主密钥，丢失后数据湖中所有加密文件将永久无法解密。请妥善备份，建议同时存放到独立的加密存储中。

### 迁移到新设备完整流程

```powershell
# 1. 在旧设备导出数据
docker compose exec mysql mysqldump -uroot -p<DB_PASSWORD> data_management > backup.sql
docker run --rm -v datamanagement_backend-data:/data -v ${PWD}:/backup alpine tar czf /backup/data_lake.tar.gz -C /data .

# 2. 拷贝到新设备（需要带走的文件）
#    - 整个项目源码（可删 front/dist、front/node_modules、back/__pycache__）
#    - back/master.key
#    - back/.env
#    - backup.sql
#    - data_lake.tar.gz

# 3. 在新设备部署
cd D:\DataManagement
docker compose up -d mysql redis        # 先起 mysql 和 redis
docker compose exec -T mysql mysql -uroot -p<DB_PASSWORD> data_management < backup.sql
docker run --rm -v datamanagement_backend-data:/data -v ${PWD}:/backup alpine tar xzf /backup/data_lake.tar.gz -C /data
docker compose up -d                      # 启动剩余服务
```

## 常见问题排查

### Q1：启动报 `RuntimeError: JWT_SECRET_KEY 不能使用默认值`
- **原因**：`back/.env` 中 `JWT_SECRET_KEY` 没改
- **解决**：改成随机长字符串：`python -c "import secrets;print(secrets.token_hex(32))"`

### Q2：启动报 `RuntimeError: DB_PASSWORD 不能使用弱密码 123456`
- **原因**：`back/.env` 中 `DB_PASSWORD` 还是 123456
- **解决**：改成强密码，并同步更新根目录 `.env` 中的 `DB_PASSWORD`

### Q3：backend 报 `Can't connect to MySQL`
- **原因**：`DB_HOST` 配置错误
- **解决**：确认 `back/.env` 中 `DB_HOST=mysql`（容器部署），不是 localhost

### Q4：前端访问 502 Bad Gateway
- **原因**：backend 还没起来
- **解决**：`docker compose logs backend` 查看启动日志，等待 `Booting worker` 出现

### Q5：`bind: address already in use` 端口冲突
- **原因**：8080/5000/3307/6380 端口被占用
- **解决**：改 `docker-compose.yml` 中对应端口映射，如 `"8080:80"` 改为 `"9090:80"`，访问 `http://localhost:9090`

### Q6：文件上传 413 Request Entity Too Large
- **原因**：nginx 限制（已默认配 512m）
- **解决**：如仍报错，检查 `front/nginx.conf` 中 `client_max_body_size 512m;`

### Q7：首次启动后数据库没初始化
- **原因**：应用启动时会自动 `db.create_all()` 并初始化默认 admin / 角色 / 标签 / 脱敏配置
- **解决**：检查 backend 日志：`docker compose logs backend | Select-String "已初始化"`

### Q8：Docker Desktop 启动失败
- **原因**：WSL2 未启用或版本过低
- **解决**：以管理员身份运行 PowerShell：
  ```powershell
  wsl --update
  wsl --set-default-version 2
  ```

### Q9：`docker compose build` 报 `auth.docker.io` 连接失败
- **原因**：BuildKit 是独立进程，不读 Docker daemon 的代理和镜像加速配置
- **解决**：先用 `docker pull` 把基础镜像拉到本地（docker pull 走镜像加速），再执行 `docker compose up -d --build`（BuildKit 看到本地有缓存就不会走网络）：
  ```powershell
  docker pull python:3.13-slim
  docker pull node:20-alpine
  docker pull nginx:1.27-alpine
  docker compose up -d --build
  ```

## 生产环境加固建议

1. **改默认 admin 密码**：登录后立即在用户管理中修改
2. **限定 CORS_ORIGINS**：改为前端实际访问域名
3. **关闭多余端口暴露**：注释 `docker-compose.yml` 中 mysql 和 redis 的 `ports` 映射
4. **启用 HTTPS**：在 nginx.conf 中配置 SSL 证书，或前置反向代理（如 Caddy / Traefik）
5. **定期备份**：MySQL 数据 + master.key + data_lake + 外部密钥四者必须一起备份
6. **资源限制**：在 docker-compose.yml 中为每个服务加 `mem_limit` / `cpus` 限制

## 项目结构

```
DataManagement/
├── docker-compose.yml          # 容器编排
├── deploy.ps1                  # 一键部署脚本（Windows）
├── README.md                   # 本文档
├── back/                       # 后端
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .env.example            # 环境变量模板
│   ├── .env                    # 实际环境变量（不入 git）
│   ├── master.key              # 加密主密钥（首次启动自动生成）
│   ├── requirements.txt
│   ├── gunicorn_config.py
│   ├── run.py                  # Flask 入口
│   ├── app/
│   │   ├── __init__.py         # 应用工厂
│   │   ├── config.py           # 配置（含生产环境校验）
│   │   ├── extensions.py       # db / jwt / migrate / cors 等扩展
│   │   ├── api/                # 路由蓝图（auth / data / annotation / visualization / system / dashboard）
│   │   ├── models/             # 数据模型（subject / data / annotation / user / role_menu / external_key / ...）
│   │   ├── services/           # 业务逻辑层
│   │   │   ├── base.py                 # BaseService + 自定义异常
│   │   │   ├── subject_service.py      # 受试者 CRUD
│   │   │   ├── asset_service.py        # 数据资产 + 加解密
│   │   │   ├── annotation_service.py   # 标注任务
│   │   │   ├── label_service.py        # 标签库
│   │   │   ├── user_service.py         # 用户 + 认证
│   │   │   ├── role_service.py         # 角色 + 菜单权限
│   │   │   ├── standard_service.py     # 数据标准 + 命名规范
│   │   │   ├── desensitize_service.py  # 脱敏配置
│   │   │   ├── encryption_service.py    # 主密钥管理（轮换/备份/导入）
│   │   │   ├── external_key_service.py # 外部采集设备密钥管理
│   │   │   ├── scan_config_service.py  # 扫描配置
│   │   │   ├── snapshot_service.py     # 快照 + 回滚
│   │   │   ├── data_processing_service.py # 数据清洗 + 标准化
│   │   │   └── export_service.py       # 异步导出
│   │   ├── tasks/              # Celery 异步任务
│   │   │   ├── celery_app.py
│   │   │   ├── cleaning.py             # 数据清洗
│   │   │   └── annotation.py            # 预标注
│   │   └── utils/              # 工具函数
│   │       ├── crypto.py               # 信封加密 + 外部 AES-CBC 适配
│   │       ├── scanner.py              # 目录扫描自动导入
│   │       ├── naming.py               # 命名规范引擎
│   │       ├── audit.py                # 操作日志 + 快照
│   │       ├── desensitize.py          # 脱敏处理
│   │       ├── transcode.py            # ffmpeg 转码
│   │       ├── response.py             # 统一响应格式
│   │       ├── decorators.py           # role_required / retry_on_deadlock
│   │       └── ...
│   ├── migrations/             # Alembic 迁移
│   ├── scripts/                # 一次性脚本（迁移加密、文件鉴权测试）
│   ├── tests/                  # pytest 测试套件（326 通过）
│   └── data_lake/              # 数据湖（运行时挂载为 volume）
└── front/                      # 前端
    ├── Dockerfile              # 多阶段构建
    ├── .dockerignore
    ├── nginx.conf              # SPA + /api 反代
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── api/                # 后端 API 调用封装
        ├── layouts/            # 布局组件
        ├── router/             # 路由配置
        ├── App.vue
        └── main.js
```
