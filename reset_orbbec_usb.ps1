<#
.SYNOPSIS
    深度摄像头（Orbbec Femto Bolt / Intel RealSense D455f）USB 透传与恢复脚本（Windows + Docker Desktop）
.DESCRIPTION
    Docker Desktop（WSL2 后端）的 Linux 容器无法直接访问 Windows 宿主机 USB 设备。
    本脚本用 usbipd-win 把深度摄像头附加到 WSL2（usbipd-win 5.x 通过共享内核 vhci
    透传，无需在 WSL 发行版内安装 usbip 客户端；设备对 docker-desktop 与所有
    WSL2 发行版可见），随后重启 backend 容器并验证容器内设备是否可见。

    支持设备（按厂商 VID 匹配）：
      - Orbbec Femto Bolt（VID 2bc5，pyk4a）
      - Intel RealSense D455f 等 D400 系（VID 8086，pyrealsense2）

    适用场景：
      - 首次部署（摄像头已插好、未透传时）
      - 物理拔插摄像头后容器内检测不到设备时
      - 容器内 orbbec/status 返回 available=false 时

    自动完成：
      1. 定位 usbipd 并找到深度摄像头（VID 2bc5:066b / 8086）
      2. 按需 bind / detach / attach 到 WSL（共享内核 vhci）
      3. 检查 WSL 网络模式（mirrored 与 usbipd-win 5.x 不兼容，会提示修复）
      4. 重启 backend 容器（容器内 ensure_usb_nodes.py 自动创建 USB 设备节点）
      5. 容器内 sysfs 验证设备是否可见
.NOTES
    前置条件：
      - 已安装 Docker Desktop（WSL2 后端）
      - 已安装 usbipd-win（5.x 推荐）：winget install dorssel.usbipd-win
      - WSL2 已启用（无需特定 Ubuntu 发行版；5.x 通过共享内核 vhci 透传）
      - 摄像头已通过 USB 3.0 连接（Orbbec 指示灯稳定白色；RealSense 通电后正面蓝色灯）
    需要管理员权限（usbipd bind/attach 要求管理员），脚本会自动请求提权。
    用法（项目根目录，PowerShell）：
      ./reset_orbbec_usb.ps1
      ./reset_orbbec_usb.ps1 -WslDistro Ubuntu-22.04   # 指定发行版（5.x 下非必需）
#>

param(
    # attach 使用的 WSL 发行版名（usbipd-win 5.x 下非必需，保留兼容旧版/多发行版场景）
    [string]$WslDistro = "ubuntu"
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# 修复 WSL 输出中文乱码：WSL 内部输出 UTF-8，而 Windows PowerShell 5.1
# 默认控制台代码页是 GBK（936），UTF-8 字节流被按 GBK 解码会显示为乱码
#（例如 wsl 提示"检测到 localhost 代理配置"时显示为乱码）。
# 将控制台输入/输出编码统一设为 UTF-8，保证 wsl 命令的中文提示正常显示。
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { /* 部分宿主（如受限终端）不支持修改编码时静默忽略 */ }
# WSL 官方支持的环境变量：强制 wsl.exe 以 UTF-8 输出（否则在管道/重定向下
# 可能输出 UTF-16，同样导致中文乱码），且避免 localhost 代理警告被 GBK 解码
$env:WSL_UTF8 = '1'

# ---------- 自动提权（usbipd bind/attach 需要管理员） ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '需要管理员权限，正在请求提权...' -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList (
        "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -WslDistro `"$WslDistro`""
    )
    exit 0
}

function Write-Step { param($msg) Write-Host "[步骤] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err2 { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

# ---------- 1. 定位 usbipd ----------
Write-Step '定位 usbipd'
$usbipd = (Get-Command usbipd -ErrorAction SilentlyContinue).Source
if (-not $usbipd) {
    $candidate = 'C:\Program Files\usbipd-win\usbipd.exe'
    if (Test-Path $candidate) { $usbipd = $candidate }
}
if (-not $usbipd) {
    Write-Err2 '未找到 usbipd.exe，请先安装 usbipd-win: winget install dorssel.usbipd-win'
    exit 1
}
Write-OK $usbipd

# ---------- 1.5 确保 usbipd 服务运行（服务被停止/杀进程后 list 无输出，需先启动） ----------
$usbipdSvc = Get-Service usbipd -ErrorAction SilentlyContinue
if (-not $usbipdSvc) {
    Write-Err2 '未找到 usbipd 服务，usbipd-win 安装可能损坏，请重装: winget install dorssel.usbipd-win'
    exit 1
}
if ($usbipdSvc.Status -ne 'Running') {
    Write-Warn "usbipd 服务当前为 $($usbipdSvc.Status)，正在启动..."
    Start-Service usbipd
    Start-Sleep -Seconds 2
    $usbipdSvc.Refresh()
    if ($usbipdSvc.Status -ne 'Running') {
        Write-Err2 'usbipd 服务启动失败，请检查 services.msc 中的 usbipd 服务（设为自动）'
        exit 1
    }
    Write-OK 'usbipd 服务已启动'
} else {
    Write-OK 'usbipd 服务运行中'
}

# ---------- 2. 查找深度摄像头（Orbbec 2bc5 / Intel RealSense 8086） ----------
Write-Step '查找深度摄像头设备'
$devicePattern = '2bc5:066b|Orbbec Femto Bolt|8086:0b5c|8086:0b5d|RealSense'
$list = & $usbipd list
# 只从 Connected 段找设备行：Persisted 段的 GUID 不是 busid，误取会导致 attach 报错
$connectedLines = @()
$inConnected = $false
foreach ($l in $list) {
    if ($l -match '^Connected:') { $inConnected = $true; continue }
    if ($l -match '^Persisted:') { $inConnected = $false }
    if ($inConnected -and $l -match '\S') { $connectedLines += $l }
}
$deviceLine = $connectedLines | Select-String -Pattern $devicePattern | Select-Object -First 1
if (-not $deviceLine) {
    # 设备枚举失败（usbipd 显示 0000:0002 "描述符请求失败" / 0000:0005 "描述符无效"，
    # Windows 设备管理器报错误代码 43）时，bind/detach/attach 均无能为力，
    # 必须由用户物理断电重启设备或排查 USB 链路。脚本绝不自动断电/重启，只提醒用户手动操作。
    $broken = $connectedLines | Select-String -Pattern '0000:0002|0000:0005|描述符请求失败|描述符无效|未知 USB 设备' | Select-Object -First 1
    if ($broken) {
        Write-Err2 '深度摄像头 USB 枚举失败（Windows 无法识别设备，错误代码 43 / 描述符无效）'
        Write-Host '  原因：摄像头固件卡死，或 USB 链路异常（端口/线材问题）' -ForegroundColor Yellow
        Write-Host '  处理（依次尝试，恢复后重新运行本脚本）：' -ForegroundColor Yellow
        Write-Host '    1. 拔下摄像头 USB 线，断电 5 秒以上再插回（物理断电最有效）' -ForegroundColor Yellow
        Write-Host '    2. 更换 USB 端口（优先主板后置 USB 3.0 口，避开 USB Hub/前置面板口）' -ForegroundColor Yellow
        Write-Host '    3. 更换 USB 数据线（USB 3.0 线）' -ForegroundColor Yellow
        Write-Host '    4. 仍无效则重启 Windows 后重新插拔摄像头' -ForegroundColor Yellow
        exit 1
    }
    Write-Err2 '未找到深度摄像头（Orbbec Femto Bolt / Intel RealSense），请确认：'
    Write-Host '  1. 摄像头 USB 已插好' -ForegroundColor Yellow
    Write-Host '  2. 供电正常（Orbbec 指示灯稳定白色；RealSense 通电后正面蓝色灯）' -ForegroundColor Yellow
    Write-Host '  3. Windows 设备管理器中能看到对应摄像头设备' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '当前 usbipd list 输出：'
    Write-Host ($list -join "`n")
    exit 1
}

$fields = ($deviceLine.ToString() -split '\s{2,}') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$busid = $fields[0]
# 注意：Where-Object 单结果时是字符串，直接 $stateMatch[0] 会取到首字符（如 "Shared"->"S"），需 @() 包装
$stateMatch = @($deviceLine.ToString() -split '\s{2,}' | Where-Object { $_ -match 'Shared|Attached|Not shared' })
$state = if ($stateMatch.Count -gt 0) { $stateMatch[0] } else { 'Unknown' }
Write-OK "找到设备 busid=$busid, 当前状态: $state"

# ---------- 3. bind（若未共享） ----------
if ($state -match 'Not shared') {
    Write-Step "执行 bind --force --busid $busid"
    & $usbipd bind --force --busid $busid
    if ($LASTEXITCODE -ne 0) { Write-Err2 'bind 失败'; exit 1 }
    Write-OK 'bind 完成'
} else {
    Write-OK '设备已 bind（跳过）'
}

# ---------- 4. detach（若已 attach，先断开再重新 attach） ----------
$list2 = & $usbipd list
$connectedLines2 = @()
$inConnected = $false
foreach ($l in $list2) {
    if ($l -match '^Connected:') { $inConnected = $true; continue }
    if ($l -match '^Persisted:') { $inConnected = $false }
    if ($inConnected -and $l -match '\S') { $connectedLines2 += $l }
}
$line2 = ($connectedLines2 | Select-String -Pattern $devicePattern | Select-Object -First 1).ToString()
if ($line2 -match 'Attached') {
    Write-Step "执行 detach --busid $busid"
    & $usbipd detach --busid $busid
    if ($LASTEXITCODE -ne 0) { Write-Err2 'detach 失败（可能已被系统释放，继续）' }
    Start-Sleep -Seconds 2
    Write-OK 'detach 完成'
} else {
    Write-OK '设备当前未 attach（跳过 detach）'
}

# ---------- 5. 确保 WSL 发行版在线（usbip attach 需要目标发行版运行） ----------
Write-Step "确保 WSL 发行版 $WslDistro 运行（attach 需要目标发行版在线）"
# 后台保持会话存活（sleep 86400），否则 attach 后发行版退出会导致设备 detach
Start-Process -WindowStyle Hidden -FilePath 'wsl.exe' -ArgumentList '-d', $WslDistro, '-e', 'sleep', '86400'
$wslReady = $false
for ($i = 0; $i -lt 40; $i++) {
    & wsl -d $WslDistro -e echo ready | Out-Null
    if ($LASTEXITCODE -eq 0) { $wslReady = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $wslReady) {
    Write-Err2 "WSL 发行版 $WslDistro 启动超时/失败。请先执行: wsl --install -d Ubuntu，或用 -WslDistro 指定实际发行版名（wsl -l -v 查看）"
    exit 1
}
Write-OK "WSL 发行版 $WslDistro 已就绪"

# ---------- 5.5 检查 WSL 网络模式（usbipd-win 5.x + mirrored 已知不兼容） ----------
Write-Step '检查 WSL 网络模式'
$wslconfigPath = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $wslconfigPath) {
    $wslCfg = Get-Content $wslconfigPath -Raw -ErrorAction SilentlyContinue
    if ($wslCfg -match 'networkingMode\s*=\s*mirrored') {
        Write-Warn '检测到 %USERPROFILE%\.wslconfig 启用了 networkingMode=mirrored'
        Write-Host '  usbipd-win 5.x 通过共享内核 vhci 透传，与 WSL mirrored 网络模式存在已知兼容问题，attach 会失败。' -ForegroundColor Yellow
        Write-Host '  修复：' -ForegroundColor Yellow
        Write-Host '    1. 编辑 %USERPROFILE%\.wslconfig，移除 networkingMode=mirrored 一行（或改为 networkingMode=default）' -ForegroundColor Yellow
        Write-Host '    2. 执行 wsl --shutdown 重启 WSL' -ForegroundColor Yellow
        Write-Host '    3. 重新运行本脚本' -ForegroundColor Yellow
        Write-Host '  如果必须使用 mirrored（例如需要 WSL 内直连 Windows 服务），请参考 usbipd-win 官方 issue 中的兼容方案。' -ForegroundColor DarkGray
    } else {
        Write-OK '.wslconfig 未启用 mirrored 模式（NAT/默认，兼容）'
    }
} else {
    Write-OK '未发现 .wslconfig（默认 NAT 模式，兼容）'
}

# ---------- 6. attach 到 WSL（usbipd-win 5.x 共享内核 vhci） ----------
Write-Step "执行 attach --wsl $WslDistro --busid $busid"
$attached = $false
for ($i = 0; $i -lt 3; $i++) {
    # 注：usbipd 的 info/warning 走 stderr。PS 5.1 中 native 命令 stderr 一旦重定向
    # （2>$null）就会在 $ErrorActionPreference='Stop' 下抛 NativeCommandError 终止脚本，
    # 因此这里不做 stderr 重定向，让 info 直接显示（如 5.x 的 "Selecting a specific
    # distribution is no longer required"），$LASTEXITCODE 判定不受影响。
    & $usbipd attach --wsl $WslDistro --busid $busid
    Start-Sleep -Seconds 3
    # 关键：usbipd-win 5.x attach 后设备挂到 WSL 共享内核 vhci，Windows 侧 usbipd list
    # 不再显示 "Attached"（甚至设备从列表消失），因此不能再用 -match 'Attached' 判断。
    # 正确判据：WSL 内核 sysfs 是否能看到设备（VID 2bc5/8086）。
    $wslVid = & wsl -d $WslDistro -e sh -c "grep -l -e 2bc5 -e 8086 /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
    if ($wslVid) {
        $attached = $true
        break
    }
    if ($i -lt 2) {
        Write-Host "  attach 未生效（第 $($i+1) 次），重试 attach..." -ForegroundColor Yellow
        # 设备可能已被 attach 拿走（Windows 侧无该 busid），detach 报 no device 属正常，忽略
        & $usbipd detach --busid $busid   # no device 属正常（设备已被 attach 拿走）
        Start-Sleep -Seconds 1
    }
}
if (-not $attached) {
    Write-Err2 'attach 失败，请确认 Docker Desktop 正在运行且 WSL 正常（wsl --status）'
    Write-Host '  若 .wslconfig 启用了 networkingMode=mirrored，usbipd-win 5.x 可能无法透传，请按上方提示改回默认 NAT' -ForegroundColor Yellow
    exit 1
}
Write-OK 'attach 完成（设备已进入 WSL 内核，容器内可见）'

# ---------- 7. 重启 backend 容器（容器内 ensure_usb_nodes.py 会自动创建设备节点） ----------
Write-Step '重启 backend 容器'
Start-Sleep -Seconds 3   # 等设备在 WSL 内完成枚举
& docker compose restart backend
if ($LASTEXITCODE -ne 0) {
    Write-Err2 'docker compose restart 失败，请确认 Docker Desktop 正在运行'
    exit 1
}
Write-OK 'backend 容器已重启'
Start-Sleep -Seconds 6

# ---------- 8. 容器内验证 ----------
Write-Step '容器内验证设备'
# 用 sysfs 检查 VID（Orbbec 2bc5 / Intel RealSense 8086，与后端 ensure_usb_nodes 的检测逻辑一致）
$probe = & docker compose exec -T backend sh -c "grep -l -e 2bc5 -e 8086 /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
if ($probe) {
    Write-OK "深度摄像头已恢复（容器内 sysfs 检测到设备）"
    Write-Host ''
    Write-Host '现在可以刷新页面，在视频采集弹窗中使用深度相机（Orbbec / RealSense）了。' -ForegroundColor Green
} else {
    Write-Err2 '容器内仍未检测到设备，请手动检查：'
    Write-Host '  docker compose exec backend sh -c "ls /sys/bus/usb/devices/*/idVendor"' -ForegroundColor Yellow
    Write-Host '  docker compose logs -n 50 backend' -ForegroundColor Yellow
}
