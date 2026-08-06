<#
.SYNOPSIS
    多模态数据标注与分析处理平台 - 一键部署脚本（Windows）
.DESCRIPTION
    自动完成：检查 Docker 环境 → 生成 .env（含强密码与 JWT 密钥）→ 预拉基础镜像 → 构建并启动所有服务 → 健康检查
.NOTES
    在项目根目录（docker-compose.yml 所在目录）运行：./deploy.ps1
    注意：本文件必须为 UTF-8 带 BOM 编码，否则 Windows PowerShell 5.1 会解析中文报错。
#>

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# 端口配置（与 docker-compose.yml 中的宿主机映射端口保持一致）
$FrontendPort = 8080
$BackendPort  = 5000
$MysqlPort    = 3307
$RedisPort    = 6380

function Write-Step  { param($msg) Write-Host "`n[步骤] $msg" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2 { param($msg) Write-Host "  [!]  $msg" -ForegroundColor Yellow }
function Write-Err2  { param($msg) Write-Host "  [X]  $msg" -ForegroundColor Red }

function Test-Command {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-RandomHex {
    param([int]$Length = 32)
    # 用 .NET RandomNumberGenerator 生成密码学安全的随机字节
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $_.ToString('x2') })
}

function Get-RandomIndex {
    param($Length)
    # 用 4 字节随机数取模，避免 [ref] 传参问题（旧实现 $rng.GetBytes([ref]$idx) 会抛异常）
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buf = New-Object byte[] 4
    [void]$rng.GetBytes($buf)
    return [BitConverter]::ToUInt32($buf, 0) % $Length
}

function Get-StrongPassword {
    # 生成 24 位强密码：含大小写字母+数字+特殊字符（不含易混淆字符 0O1lI）
    # 注意：不能包含 $ —— docker compose 解析 .env 时会把 $VAR 当变量插值，
    # 含 $ 的密码会被静默改写导致 MySQL/后端密码不一致。
    # 注意：用字符串数组而非 char[]，避免 PowerShell 数组展开（@() 换行分隔会展开嵌套数组）
    $sets = @('ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnpqrstuvwxyz', '23456789', '!@#%^&*()-_=+')
    $all = $sets -join ''
    $chars = [System.Collections.Generic.List[char]]::new()
    # 每个字符集至少取 1 个，保证四类都出现
    foreach ($s in $sets) {
        $chars.Add($s[(Get-RandomIndex $s.Length)])
    }
    # 补齐到 24 位
    while ($chars.Count -lt 24) {
        $chars.Add($all[(Get-RandomIndex $all.Length)])
    }
    # Fisher-Yates 洗牌（密码学安全随机）
    for ($i = $chars.Count - 1; $i -gt 0; $i--) {
        $j = Get-RandomIndex ($i + 1)
        $tmp = $chars[$i]
        $chars[$i] = $chars[$j]
        $chars[$j] = $tmp
    }
    return -join $chars
}

# 以无 BOM 的 UTF-8 写入文件（避免 Set-Content -Encoding UTF8 在 PS5.1 下写 BOM）
function Write-EnvFile {
    param([string]$Path, [string]$Content)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ============ 1. 检查 Docker 环境 ============
Write-Step '检查 Docker 环境'

if (-not (Test-Command 'docker')) {
    Write-Err2 '未检测到 docker 命令'
    Write-Host '  请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop/' -ForegroundColor Yellow
    exit 1
}
$dockerVersion = (docker --version 2>$null)
Write-OK "Docker 已安装: $dockerVersion"

# 检查 docker compose 子命令（新版 Docker 内置，不再需要 docker-compose 可执行文件）
$composeOutput = docker compose version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err2 'docker compose 子命令不可用'
    Write-Host '  请升级 Docker Desktop 到较新版本（内置 compose 子命令）' -ForegroundColor Yellow
    exit 1
}
$composeVersion = ($composeOutput | Select-String 'Docker Compose version').ToString().Trim()
Write-OK "Docker Compose: $composeVersion"

# 检查 Docker Engine 是否在运行
try {
    docker info *> $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'docker info 失败'
    }
    Write-OK 'Docker Engine 正在运行'
} catch {
    Write-Err2 'Docker Engine 未运行'
    Write-Host '  请启动 Docker Desktop 并等待右下角鲸鱼图标变为稳定状态' -ForegroundColor Yellow
    exit 1
}

# ============ 2. 检查项目必要文件 ============
Write-Step '检查项目文件'

$requiredFiles = @(
    'docker-compose.yml',
    'back/Dockerfile',
    'back/.env.example',
    'front/Dockerfile',
    'front/nginx.conf'
)
foreach ($f in $requiredFiles) {
    if (-not (Test-Path (Join-Path $ProjectRoot $f))) {
        Write-Err2 "缺少必要文件: $f"
        exit 1
    } else {
        Write-OK "$f"
    }
}

# master.key 不存在是正常的（首次启动后端会自动生成）
$masterKeyPath = Join-Path $ProjectRoot 'back/master.key'
if (-not (Test-Path $masterKeyPath)) {
    Write-Warn2 'back/master.key 不存在，首次启动后端会自动生成'
} else {
    Write-OK 'back/master.key（已存在，将复用）'
}

# ============ 3. 生成或修补 back/.env ============
Write-Step '配置 back/.env'

$envFile = Join-Path $ProjectRoot 'back/.env'
$envExample = Join-Path $ProjectRoot 'back/.env.example'

if (-not (Test-Path $envExample)) {
    Write-Err2 "back/.env.example 不存在，无法初始化"
    exit 1
}

$needGenerate = $false
if (-not (Test-Path $envFile)) {
    Write-Warn2 'back/.env 不存在，从 .env.example 复制'
    Copy-Item $envExample $envFile -Force
    $needGenerate = $true
} else {
    # 检查是否还含占位符
    $content = Get-Content $envFile -Raw -Encoding UTF8
    if ($content -match '请使用强密码' -or $content -match '请使用随机生成的强密钥') {
        Write-Warn2 'back/.env 中仍含默认占位符，将自动生成强密码和密钥'
        $needGenerate = $true
    } else {
        Write-OK 'back/.env 已配置（跳过自动生成）'
    }
}

$dbPassword = $null
$jwtSecret = $null

if ($needGenerate) {
    $dbPassword = Get-StrongPassword
    $jwtSecret = Get-RandomHex 32

    # 读取并替换（值用双引号包裹：防止密码以 # 开头时被 dotenv/compose 当成注释；
    # 密码已不含 $，compose 不会插值）
    $content = Get-Content $envFile -Raw -Encoding UTF8
    $content = $content -replace 'DB_PASSWORD=.*', "DB_PASSWORD=`"$dbPassword`""
    $content = $content -replace 'JWT_SECRET_KEY=.*', "JWT_SECRET_KEY=`"$jwtSecret`""
    Write-EnvFile -Path $envFile -Content $content

    Write-OK "已生成 DB_PASSWORD（已脱敏显示）"
    Write-OK "已生成 JWT_SECRET_KEY: $($jwtSecret.Substring(0,8))...（已脱敏显示）"
} else {
    # 从已有 .env 读取密码（用于同步根目录 .env；兼容带引号/不带引号两种写法）
    $content = Get-Content $envFile -Raw -Encoding UTF8
    $m = [regex]::Match($content, 'DB_PASSWORD="?([^"\r\n]+)"?')
    if ($m.Success) { $dbPassword = $m.Groups[1].Value.Trim() }
    # 检测旧版本生成的含 $ 密码（compose 会插值改写，需重新生成）
    if ($dbPassword -match '\$') {
        Write-Warn2 '检测到 back/.env 中 DB_PASSWORD 含有 $ 字符（docker compose 会将其插值改写导致密码不一致）'
        $dbPassword = Get-StrongPassword
        $jwtSecret = Get-RandomHex 32
        $content = $content -replace 'DB_PASSWORD=.*', "DB_PASSWORD=`"$dbPassword`""
        $content = $content -replace 'JWT_SECRET_KEY=.*', "JWT_SECRET_KEY=`"$jwtSecret`""
        Write-EnvFile -Path $envFile -Content $content
        Write-OK '已重新生成不含 $ 字符的 DB_PASSWORD（已脱敏显示）'
    }
}

# ============ 4. 同步根目录 .env（供 docker-compose.yml 读取 ${DB_PASSWORD}） ============
Write-Step '同步根目录 .env（供 docker-compose.yml 读取）'

$rootEnv = Join-Path $ProjectRoot '.env'
if ($dbPassword) {
    # 写入或更新根目录 .env（值用双引号包裹，保持与 back/.env 完全一致）
    $rootContent = "DB_PASSWORD=`"$dbPassword`"`n"
    if (Test-Path $rootEnv) {
        $existing = Get-Content $rootEnv -Raw -Encoding UTF8
        if ($existing -match 'DB_PASSWORD=') {
            $rootContent = $existing -replace 'DB_PASSWORD=[^\r\n]*', "DB_PASSWORD=`"$dbPassword`""
        } else {
            $rootContent = $existing.TrimEnd() + "`nDB_PASSWORD=`"$dbPassword`"`n"
        }
    }
    Write-EnvFile -Path $rootEnv -Content $rootContent
    Write-OK "根目录 .env 已同步 DB_PASSWORD"
} else {
    Write-Warn2 '未能从 back/.env 读取 DB_PASSWORD，请手动确保根目录 .env 中有 DB_PASSWORD 配置'
}

# ============ 5. 检查端口占用（检查宿主机映射端口） ============
Write-Step '检查端口占用'

$portsToCheck = @(
    @{Port=$FrontendPort; Service='frontend'}
    @{Port=$BackendPort;  Service='backend'}
    @{Port=$MysqlPort;    Service='mysql'}
    @{Port=$RedisPort;    Service='redis'}
)
$hasConflict = $false
foreach ($p in $portsToCheck) {
    $conn = Get-NetTCPConnection -LocalPort $p.Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        # 排除 Docker 自己起的进程（com.docker.backend 等）
        $otherProcs = $conn | Where-Object {
            $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            $proc -and -not ($proc.ProcessName -match 'docker|com.docker')
        }
        if ($otherProcs -and $otherProcs.Count -gt 0) {
            $procName = (Get-Process -Id $otherProcs[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
            Write-Warn2 "端口 $($p.Port) ($($p.Service)) 被占用: $procName (PID $($otherProcs[0].OwningProcess))"
            $hasConflict = $true
        } else {
            Write-OK "端口 $($p.Port) ($($p.Service)) 由 Docker 占用（正常）"
        }
    } else {
        Write-OK "端口 $($p.Port) ($($p.Service)) 空闲"
    }
}

if ($hasConflict) {
    Write-Warn2 '检测到端口冲突，建议修改 docker-compose.yml 中的端口映射'
    Write-Host ("  示例：将 ""{0}:80"" 改为 ""9090:80""，访问地址相应改为 http://localhost:9090" -f $FrontendPort) -ForegroundColor Yellow
    $continue = Read-Host '  是否继续部署？(y/N)'
    if ($continue -ne 'y' -and $continue -ne 'Y') {
        Write-Host '部署已取消' -ForegroundColor Yellow
        exit 0
    }
}

# ============ 6. 预拉基础镜像（避免 BuildKit 不读代理/镜像加速导致 build 失败） ============
Write-Step '预拉基础镜像（解决 BuildKit 网络问题）'

$baseImages = @(
    'python:3.13-slim',
    'node:20-alpine',
    'nginx:1.27-alpine',
    'mysql:8.0',
    'redis:7-alpine'
)
$pullFailed = @()
foreach ($img in $baseImages) {
    # 先检查本地是否已有该镜像（避免重复拉取）
    $exists = docker image inspect $img *> $null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "$img（本地已存在）"
        continue
    }
    Write-Host "  拉取 $img ..." -ForegroundColor DarkGray
    docker pull $img *> $null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "$img"
    } else {
        Write-Warn2 "$img 拉取失败（可能是网络问题）"
        $pullFailed += $img
    }
}

if ($pullFailed.Count -gt 0) {
    Write-Warn2 "以下基础镜像拉取失败：$($pullFailed -join ', ')"
    Write-Host '  可能原因：' -ForegroundColor Yellow
    Write-Host '  1. 网络问题 - 请配置 Docker 镜像加速（Settings → Docker Engine → registry-mirrors）' -ForegroundColor Yellow
    Write-Host '  2. 也可能是代理问题 - 请配置 Docker Desktop 代理（Settings → Resources → Proxies）' -ForegroundColor Yellow
    Write-Host '  3. 继续尝试构建，BuildKit 可能会从缓存或其他源拉取' -ForegroundColor Yellow
    $continue = Read-Host '  是否继续尝试构建？(y/N)'
    if ($continue -ne 'y' -and $continue -ne 'Y') {
        Write-Host '部署已取消，请先解决网络问题再重试' -ForegroundColor Yellow
        exit 0
    }
}

# ============ 7. 构建并启动服务 ============
Write-Step '构建并启动服务（首次执行需要 5-10 分钟）'

Write-Host '  执行: docker compose up -d --build' -ForegroundColor DarkGray
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Err2 'docker compose up 失败，请查看上方日志'
    Write-Host '  常见原因：' -ForegroundColor Yellow
    Write-Host '  - 基础镜像未拉到：手动执行 docker pull python:3.13-slim 等' -ForegroundColor Yellow
    Write-Host '  - 网络问题：检查 Docker 镜像加速和代理配置' -ForegroundColor Yellow
    Write-Host '  - 端口冲突：检查端口占用情况' -ForegroundColor Yellow
    exit 1
}
Write-OK '所有服务已启动'

# ============ 8. 等待后端健康检查通过 ============
Write-Step "等待后端健康检查通过（最多 120 秒）"

# 通过 nginx 反代访问（端口 8080），更接近真实用户访问路径
$healthUrl = "http://localhost:$FrontendPort/api/health"
$maxWait = 120
$waited = 0
$healthy = $false
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 3
    $waited += 3
    try {
        $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5 -ErrorAction Stop
        # 兼容两种响应格式：{status: 'ok'} 或 {code: 200, data: {status: 'ok'}}
        $statusOk = ($resp.status -eq 'ok') -or ($resp.data.status -eq 'ok') -or ($resp.code -eq 200)
        if ($statusOk) {
            $healthy = $true
            break
        }
    } catch {
        Write-Host "  等待中... (${waited}s)" -ForegroundColor DarkGray
    }
}

if ($healthy) {
    Write-OK "后端已就绪（耗时 ${waited}s）"
} else {
    Write-Warn2 "后端 ${maxWait}s 内未通过健康检查，可能仍在启动中"
    Write-Host "  可手动执行: curl $healthUrl  或  docker compose logs -f backend" -ForegroundColor Yellow
}

# ============ 9. 打印访问信息 ============
Write-Step '部署完成'

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' 多模态数据标注与分析处理平台已部署' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ''
Write-Host " 访问地址:  http://localhost:$FrontendPort" -ForegroundColor White
Write-Host ' 默认账号:  admin / admin123' -ForegroundColor White
Write-Host " 健康检查:  http://localhost:$FrontendPort/api/health" -ForegroundColor White
Write-Host ' API 文档:  http://localhost:5000/api/health (直连后端调试)' -ForegroundColor DarkGray
Write-Host ''

if ($dbPassword) {
    Write-Host ' 已自动生成的密钥（请妥善保存）:' -ForegroundColor Yellow
    Write-Host "   DB_PASSWORD    = $dbPassword" -ForegroundColor Yellow
    if ($jwtSecret) {
        Write-Host "   JWT_SECRET_KEY = $jwtSecret" -ForegroundColor Yellow
    }
    Write-Host "   配置文件: back/.env" -ForegroundColor Yellow
    Write-Host ''
}

Write-Host ' 常用命令:' -ForegroundColor Cyan
Write-Host '   查看状态:  docker compose ps'
Write-Host '   查看日志:  docker compose logs -f backend'
Write-Host '   重启服务:  docker compose restart backend'
Write-Host '   停止服务:  docker compose stop'
Write-Host '   完全卸载:  docker compose down'
Write-Host ''
Write-Host ' 安全提醒:' -ForegroundColor Red
Write-Host '   1. 首次登录后请立即修改 admin 密码'
Write-Host '   2. 请备份 back/master.key（丢失后所有加密数据无法恢复）'
Write-Host '   3. 请备份 back/.env（含 DB_PASSWORD 和 JWT_SECRET_KEY）'
Write-Host ''
