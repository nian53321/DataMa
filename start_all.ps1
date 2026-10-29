<#
.SYNOPSIS
    多模态数据标注与分析处理平台 - 统一启动脚本（Windows + Docker Desktop）
.DESCRIPTION
    深度摄像头（Orbbec Femto Bolt / Intel RealSense D455f）由容器内
    pyk4a / pyrealsense2 直连 USB 采集（需先透传：usbipd-win 5.x 共享内核 vhci，
    见 reset_orbbec_usb.ps1），宿主机无需运行任何采集进程。
    本脚本：
      1. 检查深度摄像头是否已透传给 WSL2（未透传时提示运行 reset_orbbec_usb.ps1）
      2. 检查 back/.env 与根目录 .env 是否就绪（首次部署需先跑 deploy.ps1）
      3. 检查 Docker Engine 是否在运行
      4. docker compose up -d --build 启动全部服务（后端/前端/MySQL/Redis/Celery）
      5. 等待后端健康检查
.NOTES
    用法（项目根目录，PowerShell）：
      ./start_all.ps1                # 日常启动（深度相机需先透传）
    首次启动会自动构建镜像（需 5-10 分钟）。
    历史说明：camera_service/（宿主机 orbbec_server.exe 采集服务）是旧架构的降级
    路径，已被容器内 pyk4a 直连模式取代，本脚本不再启动任何宿主机 exe；
    若确需手动启动降级服务：camera_service\orbbec_server.exe --port 8099 --output output
#>

param(
    # 兼容旧调用（已弃用：宿主机采集服务不再由本脚本启动）
    [string]$DemoPath = ""
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# 修复 WSL / usbipd 输出中文乱码：这些工具输出 UTF-8，而 Windows PowerShell 5.1
# 默认控制台代码页是 GBK（936），UTF-8 字节流被按 GBK 解码会显示为乱码，
# 且会导致中文字符串匹配失败（如 usbipd list 中的"描述符请求失败"）。
# 将控制台输入/输出编码统一设为 UTF-8，保证中文提示正常显示与匹配。
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { /* 部分宿主（如受限终端）不支持修改编码时静默忽略 */ }

$FrontendPort = 8080

function Write-Step { param($msg) Write-Host "`n[步骤] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err2 { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

# ============ 1. 检查深度摄像头 USB 透传状态（容器内 pyk4a 需要 attach 到 WSL） ============
Write-Step '检查深度摄像头 USB 透传状态'
$usbipd = (Get-Command usbipd -ErrorAction SilentlyContinue).Source
if (-not $usbipd -and (Test-Path 'C:\Program Files\usbipd-win\usbipd.exe')) {
    $usbipd = 'C:\Program Files\usbipd-win\usbipd.exe'
}
if ($usbipd) {
    # usbipd 服务可能被停止（手动停止/杀进程后不会自动恢复）——此时 list 无输出且透传不可用
    $usbipdSvc = Get-Service usbipd -ErrorAction SilentlyContinue
    if ($usbipdSvc -and $usbipdSvc.Status -ne 'Running') {
        Write-Warn "usbipd 服务当前未运行（$($usbipdSvc.Status)），深度相机 USB 透传不可用"
        Write-Host '  请以管理员身份运行: ./reset_orbbec_usb.ps1 （会自动启动服务并透传设备）' -ForegroundColor Yellow
        Write-Host '  或管理员 PowerShell 执行: Start-Service usbipd' -ForegroundColor Yellow
    }
    # usbipd list 的输出分两段：
    #   Connected:  BUSID  VID:PID  DEVICE  STATE   ← 仍挂在 Windows 侧的设备（有 STATE 列）
    #   Persisted:  GUID   DEVICE                    ← 历史上 bind 过的设备（**没有** STATE 列）
    # 关键事实：usbipd-win 5.x attach 成功后设备会从 Connected 段消失、只留 Persisted 记录。
    # 所以"VID:PID 不在列表里"既可能是已透传，也可能是没插 —— 不能一句话下结论。
    # 旧实现对任意匹配行都去解析 STATE，而命中 Persisted 段那行永远解不出状态 → 得到
    # 'Unknown' → 把"已透传"误报成"未透传"，反过来引导用户去反复 reset。
    # 另外 usbipd 会往 stderr 写 warning（如 UsbDk 冲突），在 Stop 偏好下会中断脚本，
    # 故临时降偏好取值。
    $prevEapUsb = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $listOut = @(& $usbipd list 2>&1)
    $ErrorActionPreference = $prevEapUsb

    $inPersisted = $false
    $connectedLine = $null
    $persistedLine = $null
    foreach ($rawLine in $listOut) {
        $t = [string]$rawLine
        if ($t -match '^\s*Persisted:') { $inPersisted = $true; continue }
        if ($t -match '^\s*Connected:') { $inPersisted = $false; continue }
        if ($t -notmatch '2bc5:066b|8086:0b5c|8086:0b5d|Orbbec Femto Bolt|RealSense') { continue }
        if ($inPersisted) {
            if (-not $persistedLine) { $persistedLine = $t }
        } elseif (-not $connectedLine) {
            $connectedLine = $t
        }
    }

    if ($connectedLine) {
        # 仍在 Connected 段 → 可以读到真实 STATE
        $fields = @($connectedLine -split '\s{2,}' | Where-Object { $_ -and $_.Trim() })
        $busid = $fields[0].Trim()
        $state = 'Unknown'
        foreach ($f in $fields) {
            if ($f -match 'Attached|Shared|Not shared') { $state = $f.Trim(); break }
        }
        if ($state -match 'Attached') {
            Write-OK "深度摄像头已 attach 到 WSL（busid=$busid），容器内可直接识别"
        } elseif ($state -match 'Shared') {
            Write-Warn "深度摄像头已共享但尚未 attach 到 WSL（busid=$busid）"
            Write-Host '  请以管理员身份运行: ./reset_orbbec_usb.ps1' -ForegroundColor Yellow
        } else {
            Write-Warn "深度摄像头未透传（busid=$busid，状态: $state）"
            Write-Host '  请以管理员身份运行: ./reset_orbbec_usb.ps1（会自动 bind + attach）' -ForegroundColor Yellow
        }
    } elseif ($persistedLine) {
        # 不在 Connected 段，但 Persisted 段留有记录 → 最可能是已 attach 给 WSL
        Write-OK '深度摄像头未出现在 Windows 设备列表中（Persisted 段留有历史记录），通常表示已 attach 给 WSL'
        Write-Host '  若视频采集弹窗里仍看不到相机，再以管理员身份运行: ./reset_orbbec_usb.ps1' -ForegroundColor DarkGray
    } else {
        # 两段都没有 → 没插 / 没 bind / USB 枚举失败，需要分情况提示
        $broken = $listOut | Select-String '0000:0002|0000:0005|描述符请求失败|描述符无效|未知 USB 设备' | Select-Object -First 1
        if ($broken) {
            Write-Err2 '深度摄像头 USB 枚举失败（Windows 无法识别设备，错误代码 43 / 描述符无效）'
            Write-Host '  原因：摄像头固件卡死，或 USB 链路异常（端口/线材问题）' -ForegroundColor Yellow
            Write-Host '  处理（依次尝试，恢复后运行 ./reset_orbbec_usb.ps1 重新透传）：' -ForegroundColor Yellow
            Write-Host '    1. 拔下摄像头电源/USB 线，断电 5 秒以上再插回（物理断电最有效）' -ForegroundColor Yellow
            Write-Host '    2. 更换 USB 端口（优先主板后置 USB 3.0 口，避开 USB Hub/前置面板口）' -ForegroundColor Yellow
            Write-Host '    3. 更换 USB 数据线（USB 3.0 线）' -ForegroundColor Yellow
            Write-Host '    4. 仍无效则重启 Windows 后重新插拔摄像头' -ForegroundColor Yellow
        } else {
            Write-Warn 'usbipd list 中未列出深度摄像头（可能已透传给 WSL，或未插/未 bind）'
            Write-Host '  若容器内仍检测不到深度相机，请以管理员身份运行: ./reset_orbbec_usb.ps1' -ForegroundColor Yellow
        }
    }
} else {
    Write-Warn '未安装 usbipd-win，深度相机无法透传给容器（仅普通摄像头可用）'
    Write-Host '  安装: winget install dorssel.usbipd-win，然后运行 ./reset_orbbec_usb.ps1' -ForegroundColor Yellow
}

# ============ 2. 检查环境文件（首次部署需先运行 deploy.ps1） ============
Write-Step '检查环境文件'
$envFile = Join-Path $ProjectRoot 'back/.env'
$rootEnv = Join-Path $ProjectRoot '.env'
if (-not (Test-Path $envFile)) {
    Write-Err2 '缺少 back/.env'
    Write-Host '  首次部署请先运行: .\deploy.ps1 （自动生成强密码与密钥）' -ForegroundColor Yellow
    Write-Host '  或手动复制 back/.env.example 为 back/.env 并填写配置' -ForegroundColor Yellow
    exit 1
}
if (-not (Test-Path $rootEnv)) {
    Write-Warn '缺少根目录 .env（docker-compose 会使用默认 MySQL 密码，可能与 back/.env 不一致）'
    Write-Host '  建议先运行: .\deploy.ps1 同步根目录 .env' -ForegroundColor Yellow
    $continue = Read-Host '  是否继续启动？(y/N)'
    if ($continue -ne 'y' -and $continue -ne 'Y') { exit 0 }
}
Write-OK '环境文件检查通过'

# ============ 3. 启动 Docker 服务 ============
Write-Step '启动 Docker 服务'

# Docker Desktop 未启动（或 WSL 后端还没就绪）是最常见的失败原因。先做一次明确判定，
# 否则用户只会看到一串 "failed to connect to the docker API at npipe://..."。
# 注意：Windows PowerShell 5.1 在 $ErrorActionPreference='Stop' 下会把原生命令写到
# stderr 的任意一行当终止错误抛出，`2>$null` / `2>&1` / `*> $null 2>&1` 都挡不住（实测），
# 所以这里临时把偏好降为 Continue 再取值，靠退出码判定。
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$dockerInfoOut = @(docker info --format '{{.ServerVersion}}' 2>&1)
$dockerInfoExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
$serverVersion = (($dockerInfoOut | Where-Object { $_ -is [string] }) -join '').Trim()
if ($dockerInfoExit -ne 0 -or $serverVersion -notmatch '^\d+\.\d+') {
    Write-Err2 'Docker Engine 未运行（或 Docker Desktop 尚未就绪）'
    Write-Host '  请先启动 Docker Desktop，等右下角鲸鱼图标变为稳定状态后再运行本脚本' -ForegroundColor Yellow
    exit 1
}
Write-OK "Docker Engine 正在运行（Server $serverVersion）"

& docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Err2 'docker compose up 失败，请查看上方日志'
    exit 1
}
Write-OK 'Docker 服务已启动'

# ============ 4. 等待后端健康检查 ============
Write-Step '等待后端就绪'
$healthUrl = "http://localhost:$FrontendPort/api/health"
$healthy = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 3
    try {
        $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5 -ErrorAction Stop
        if (($resp.status -eq 'ok') -or ($resp.data.status -eq 'ok') -or ($resp.code -eq 200)) {
            $healthy = $true
            break
        }
    } catch {}
}
if ($healthy) { Write-OK '后端已就绪' }
else { Write-Warn '后端 120s 内未通过健康检查，请查看 docker compose logs backend' }

# ============ 5. 完成 ============
Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' 平台已启动' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host "  访问地址:  http://localhost:$FrontendPort" -ForegroundColor White
Write-Host '  停止:      docker compose stop' -ForegroundColor DarkGray
Write-Host '  深度相机:  容器内直连 USB（透传异常时运行 ./reset_orbbec_usb.ps1）' -ForegroundColor DarkGray
Write-Host ''
