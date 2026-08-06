<#
.SYNOPSIS
    多模态数据标注与分析处理平台 - 统一启动脚本（Windows + Docker Desktop）
.DESCRIPTION
    深度摄像头（Orbbec Femto Bolt / Intel RealSense D455f）由容器内
    pyk4a / pyrealsense2 直连 USB 采集（需先透传：usbipd-win 5.x 共享内核 vhci，
    见 reset_orbbec_usb.ps1），宿主机无需运行任何采集进程。
    本脚本：
      1. 检查深度摄像头是否已透传给 WSL2（未透传时提示运行 reset_orbbec_usb.ps1）
      2. 启动全部 Docker 服务（后端/前端/MySQL/Redis/Celery）
      3. 等待后端健康检查
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
    # 注意：usbipd-win 5.x attach 后设备挂到 WSL 共享内核 vhci，从 Windows 侧
    # usbipd list 中消失，因此"列表里找不到"通常意味着已透传（或未插/未 bind）；
    # 若设备仍在列表（Shared/Not shared）则未透传。
    $listOut = @(& $usbipd list)
    $line = $listOut | Select-String '2bc5:066b|8086:0b5c|8086:0b5d|Orbbec Femto Bolt|RealSense' | Select-Object -First 1
    if ($line) {
        $stateMatch = @($line.ToString() -split '\s{2,}' | Where-Object { $_ -match 'Shared|Attached|Not shared' })
        $state = if ($stateMatch.Count -gt 0) { $stateMatch[0] } else { 'Unknown' }
        if ($state -match 'Attached') {
            Write-OK "深度摄像头已透传给 WSL（busid=$((($line.ToString()) -split '\s{2,}')[0].Trim())），容器内可直接识别"
        } else {
            Write-Warn "深度摄像头未透传给 WSL2（usbipd list 中状态: $state）"
            Write-Host '  请以管理员身份运行: ./reset_orbbec_usb.ps1' -ForegroundColor Yellow
            Write-Host '  （首次部署或拔插摄像头后必做；完成后深度相机在视频采集弹窗中可用）' -ForegroundColor Yellow
        }
    } else {
        # 设备枚举失败（usbipd 显示 0000:0005 "描述符无效"/Windows 报错误 43）时软件无法修复，
        # 必须物理断电重启设备或排查 USB 链路（端口/线材）。给出明确排查步骤，避免盲目重试。
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
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Err2 'docker compose up 失败，请查看上方日志'
    exit 1
}
Write-OK 'Docker 服务已启动'

# ============ 3. 等待后端健康检查 ============
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

# ============ 4. 完成 ============
Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' 平台已启动' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host "  访问地址:  http://localhost:$FrontendPort" -ForegroundColor White
Write-Host '  停止:      docker compose stop' -ForegroundColor DarkGray
Write-Host '  深度相机:  容器内直连 USB（透传异常时运行 ./reset_orbbec_usb.ps1）' -ForegroundColor DarkGray
Write-Host ''
