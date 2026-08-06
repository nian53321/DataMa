<#
.SYNOPSIS
    Orbbec 深度摄像头固件复位脚本（等效断电重启，Windows + Docker Desktop）
.DESCRIPTION
    适用场景：界面显示"已检测到深度相机（序列号 CL8H363008G）"，但启动实时预览
    或录制时提示"相机服务无响应（设备状态异常导致启动卡死，已终止服务）"。

    原因：摄像头固件进入异常状态（USB 枚举超时 / k4a nvram 读取无限重试）。
    pyk4a 只是 USB 枚举能看到设备，打开设备读固件时卡死，需要给设备"断电重启"。

    本脚本完成：
      1. 断开 usbip 透传（避免占用）
      2. 提醒用户手动拔插摄像头（脚本不自动断电，避免引发 USB 端口/控制器异常）
      3. 重新 attach 到 WSL（usbipd）
      4. 重启 backend 容器（容器内自动重建 USB 设备节点）
      5. 容器内 sysfs 验证设备是否恢复

.USAGE
    项目根目录，右键"以管理员身份运行 PowerShell"，执行：
      ./reset_orbbec_firmware.ps1
    或指定 WSL 发行版：
      ./reset_orbbec_firmware.ps1 -WslDistro Ubuntu

.NOTES
    需要管理员权限（PnP 禁用/启用设备），脚本会自动请求提权。
    如果脚本执行后仍失败，请尝试更换 USB 端口/线材后重新运行。
#>

param(
    [string]$WslDistro = "ubuntu"
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# ---------- 自动提权（PnP 禁用/启用需要管理员） ----------
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
function Write-Err2 { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

# ---------- 1. 定位 Orbbec 设备（PNP InstanceId，按 VID 2BC5） ----------
Write-Step '定位 Orbbec 深度摄像头设备'
$devices = @(Get-PnpDevice | Where-Object { $_.InstanceId -like 'USB\VID_2BC5*' -and $_.Status -ne 'Unknown' })
if ($devices.Count -eq 0) {
    Write-Err2 '未找到 Orbbec 设备（VID_2BC5），请确认：'
    Write-Host '  1. 摄像头 USB 已插好且供电正常（指示灯稳定白色）' -ForegroundColor Yellow
    Write-Host '  2. Windows 设备管理器中能看到 Orbbec Femto Bolt' -ForegroundColor Yellow
    exit 1
}
$devices | ForEach-Object { Write-OK "$($_.FriendlyName)  ($($_.InstanceId))" }

# ---------- 2. 断开 usbip 透传（若已 attach） ----------
Write-Step '断开 usbip 透传'
$usbipd = (Get-Command usbipd -ErrorAction SilentlyContinue).Source
if (-not $usbipd) { $usbipd = 'C:\Program Files\usbipd-win\usbipd.exe' }
$busid = $null
if (Test-Path $usbipd) {
    $list = @(& $usbipd list)
    # 只从 Connected 段找设备行：Persisted 段的 GUID 不是 busid，误取会导致 attach 报错
    $connectedLines = @()
    $inConnected = $false
    foreach ($l in $list) {
        if ($l -match '^Connected:') { $inConnected = $true; continue }
        if ($l -match '^Persisted:') { $inConnected = $false }
        if ($inConnected -and $l -match '\S') { $connectedLines += $l }
    }
    $line = $connectedLines | Select-String -Pattern '2bc5:066b|Orbbec' | Select-Object -First 1
    if ($line) {
        $fields = $line.ToString() -split '\s{2,}' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        $busid = $fields[0]
        Write-OK "busid=$busid"
        & $usbipd detach --busid $busid 2>$null
        Start-Sleep -Seconds 2
    } else {
        Write-Host '  未在 usbipd list Connected 段中找到设备（可能已 detach、未 bind 或枚举失败显示为 0000:0002）' -ForegroundColor Yellow
    }
}

# ---------- 3. 提醒用户手动拔插摄像头（脚本不自动断电：PnP 断电实测会引发 USB 端口/控制器异常，设备变错误 43） ----------
Write-Step '请手动拔插摄像头（物理断电重启）'
Write-Host '  脚本不会自动断电，请手动完成以下操作：' -ForegroundColor Yellow
Write-Host '    1. 拔下摄像头 USB 线（若设备有独立电源一并断电）' -ForegroundColor Yellow
Write-Host '    2. 等待 5 秒以上' -ForegroundColor Yellow
Write-Host '    3. 重新插回（Orbbec 指示灯恢复稳定白色）' -ForegroundColor Yellow
Write-Host '    4. 若插回后仍识别异常，优先换一个主板后置 USB 3.0 口再插（避开 USB Hub/前置面板口）' -ForegroundColor Yellow
$userResp = Read-Host '  插好后按回车继续（输入 q 取消）'
if ($userResp -eq 'q' -or $userResp -eq 'Q') { Write-Host '已取消'; exit 0 }
Write-OK '已确认设备重新插好'
Start-Sleep -Seconds 3

# ---------- 5. 重新 attach 到 WSL（用户拔插后重新解析 busid） ----------
$busid = $null
if (Test-Path $usbipd) {
    $list = @(& $usbipd list)
    $connectedLines = @()
    $inConnected = $false
    foreach ($l in $list) {
        if ($l -match '^Connected:') { $inConnected = $true; continue }
        if ($l -match '^Persisted:') { $inConnected = $false }
        if ($inConnected -and $l -match '\S') { $connectedLines += $l }
    }
    $line = $connectedLines | Select-String -Pattern '2bc5:066b|Orbbec' | Select-Object -First 1
    if ($line) {
        $fields = $line.ToString() -split '\s{2,}' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        $busid = $fields[0]
        Write-Step "重新 attach 到 WSL (busid=$busid)"
        & $usbipd attach --wsl $WslDistro --busid $busid
        if ($LASTEXITCODE -ne 0) {
            Write-Err2 'attach 失败，请检查 WSL 状态（wsl --status）与网络模式（.wslconfig 勿用 mirrored）'
        }
        Start-Sleep -Seconds 4
    } else {
        Write-Host '  设备未恢复（Connected 列表无 Orbbec），跳过 attach' -ForegroundColor Yellow
    }
} else {
    Write-Host '  usbipd 未安装，跳过 attach' -ForegroundColor Yellow
}

# ---------- 6. 重启 backend 容器（重建 USB 设备节点） ----------
Write-Step '重启 backend 容器'
& docker compose restart backend
if ($LASTEXITCODE -ne 0) {
    Write-Err2 'docker compose restart 失败，请确认 Docker Desktop 正在运行'
    exit 1
}
Write-OK 'backend 容器已重启'
Start-Sleep -Seconds 10

# ---------- 7. 容器内验证 ----------
Write-Step '验证容器内设备'
$probe = & docker compose exec -T backend sh -c "grep -l 2bc5 /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
if ($probe) {
    Write-OK "深度摄像头固件已恢复（容器内 sysfs 检测到设备）"
    Write-Host ''
    Write-Host '现在可以刷新页面，在视频采集弹窗中重新启动深度相机预览/录制。' -ForegroundColor Green
} else {
    Write-Err2 '容器内仍未检测到设备，请按顺序排查：'
    Write-Host '  1. usbipd list 查看设备是否 Shared（未 Shared 需 bind）' -ForegroundColor Yellow
    Write-Host '  2. wsl -d $WslDistro -e ls /sys/bus/usb/devices/ | grep -v ":"  看 WSL 内核是否可见' -ForegroundColor Yellow
    Write-Host '  3. 若 .wslconfig 启用了 networkingMode=mirrored，改回默认 NAT 后 wsl --shutdown 再运行本脚本' -ForegroundColor Yellow
    Write-Host '  4. 仍失败则物理拔插摄像头 USB（断电几秒）后重新运行本脚本' -ForegroundColor Yellow
}
