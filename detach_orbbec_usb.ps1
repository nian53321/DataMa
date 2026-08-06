<#
.SYNOPSIS
    深度摄像头（Orbbec Femto Bolt / Intel RealSense D455f）取消 USB 透传脚本（Windows + Docker Desktop）
.DESCRIPTION
    取消深度摄像头到 WSL2 的 USB 透传（usbipd detach），将设备归还 Windows 本机使用
    （例如在 Windows 上用官方 SDK / 采集软件直接读取摄像头）。

    支持设备（按厂商 VID 匹配）：
      - Orbbec Femto Bolt（VID 2bc5，pyk4a）
      - Intel RealSense D455f 等 D400 系（VID 8086，pyrealsense2）

    自动完成：
      1. 定位 usbipd 并找到深度摄像头（VID 2bc5:066b / 8086）
      2. detach 全部深度摄像头（取消透传，设备重新对 Windows 可见）
      3. 验证设备已从 WSL 归还本机

    参数：
      -Unbind  同时执行 unbind（取消共享注册，设备完全归还本机且不再被标记为可共享）。
               未指定时仅 detach（透传取消但保留共享标记，之后可直接再次 attach）。
.NOTES
    前置条件：
      - 已安装 usbipd-win（5.x 推荐）：winget install dorssel.usbipd-win
      - 摄像头已通过 USB 3.0 连接（Orbbec 指示灯稳定白色；RealSense 通电后正面蓝色灯）
    需要管理员权限（usbipd detach 要求管理员），脚本会自动请求提权。
    用法（项目根目录，PowerShell）：
      ./detach_orbbec_usb.ps1                 # 仅取消透传（归还本机，保留共享标记）
      ./detach_orbbec_usb.ps1 -Unbind         # 取消透传并取消共享注册（完全归还本机）
      ./detach_orbbec_usb.ps1 -All            # 取消全部 USB 设备的透传（不限于深度摄像头）
#>

param(
    # 同时执行 unbind（取消共享注册，设备完全归还本机）
    [switch]$Unbind,
    # 取消所有 USB 设备的透传（默认仅处理深度摄像头）
    [switch]$All
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

# ---------- 自动提权（usbipd detach 需要管理员） ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '需要管理员权限，正在请求提权...' -ForegroundColor Yellow
    $extraArgs = ''
    if ($Unbind) { $extraArgs += ' -Unbind' }
    if ($All)    { $extraArgs += ' -All' }
    Start-Process powershell -Verb RunAs -ArgumentList (
        "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"$extraArgs"
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

# ---------- 2. 确保 usbipd 服务运行（服务被停止/杀进程后 list 无输出，需先启动） ----------
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

# ---------- 3. 查找深度摄像头（Orbbec 2bc5 / Intel RealSense 8086） ----------
$devicePattern = '2bc5:066b|Orbbec Femto Bolt|8086:0b5c|8086:0b5d|RealSense'

if ($All) {
    Write-Step '取消全部 USB 设备的透传'
} else {
    Write-Step '查找深度摄像头设备'
}

$list = & $usbipd list
# 只从 Connected 段取设备行（Persisted 段的 GUID 不是 busid，误取会报错）
$connectedLines = @()
$inConnected = $false
foreach ($l in $list) {
    if ($l -match '^Connected:') { $inConnected = $true; continue }
    if ($l -match '^Persisted:') { $inConnected = $false }
    if ($inConnected -and $l -match '\S') { $connectedLines += $l }
}

if ($All) {
    # -All 模式：全部设备行（跳过表头）
    $targetLines = $connectedLines | Where-Object { $_ -notmatch '^(BUSID|[-=]+)' }
} else {
    $targetLines = $connectedLines | Select-String -Pattern $devicePattern
}

if (-not $targetLines -or @($targetLines).Count -eq 0) {
    if ($All) {
        Write-OK '当前没有处于透传状态的 USB 设备'
    } else {
        Write-OK '未发现处于透传状态的深度摄像头（Orbbec / RealSense），无需取消'
    }
    Write-Host ''
    Write-Host '当前 usbipd list 输出：'
    Write-Host ($list -join "`n")
    exit 0
}

Write-Host "找到 $(@($targetLines).Count) 个设备，开始取消透传..." -ForegroundColor Cyan
foreach ($line in $targetLines) {
    $fields = ($line.ToString() -split '\s{2,}') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $busid = $fields[0]
    $vidPid = $fields[1]
    $devName = $fields[2]
    Write-Host "  处理: busid=$busid VID:PID=$vidPid $devName" -ForegroundColor DarkGray

    # ---------- detach（取消透传，归还本机） ----------
    Write-Step "执行 detach --busid $busid"
    & $usbipd detach --busid $busid
    if ($LASTEXITCODE -ne 0) {
        Write-Err2 "detach 失败（busid=$busid）"
        continue
    }
    Write-OK "已取消透传（busid=$busid）"

    # ---------- 可选 unbind（取消共享注册，完全归还本机） ----------
    if ($Unbind) {
        Write-Step "执行 unbind --busid $busid"
        & $usbipd unbind --busid $busid
        if ($LASTEXITCODE -ne 0) {
            Write-Err2 "unbind 失败（busid=$busid）"
            continue
        }
        Write-OK "已取消共享注册（busid=$busid）"
    }
    Start-Sleep -Seconds 1
}

# ---------- 4. 验证 ----------
Write-Step '验证取消透传结果'
$listAfter = & $usbipd list
$remaining = @($listAfter | Select-String -Pattern $devicePattern)
if ($remaining.Count -gt 0) {
    Write-Warn "仍有 $($remaining.Count) 个深度摄像头处于透传/共享状态，请检查上方日志"
    Write-Host ($remaining | ForEach-Object { $_.ToString() }) -ForegroundColor Yellow
} else {
    Write-OK '深度摄像头已全部归还本机'
}

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' 深度摄像头已取消 USB 透传' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ''
Write-Host ' 现在摄像头可在 Windows 本机直接使用（官方 SDK / 采集软件）。' -ForegroundColor White
if (-not $Unbind) {
    Write-Host ' 提示：未执行 unbind，设备仍保留共享标记（Shared），' -ForegroundColor Yellow
    Write-Host '       之后可再次运行 ./reset_orbbec_usb.ps1 恢复容器内透传。' -ForegroundColor Yellow
    Write-Host '       如需完全取消共享注册，可加 -Unbind 参数再次运行。' -ForegroundColor Yellow
} else {
    Write-Host ' 已同时取消共享注册（unbind），设备完全归还本机。' -ForegroundColor White
    Write-Host ' 若之后需要重新在容器内使用深度相机，请先运行 ./reset_orbbec_usb.ps1（会自动重新 bind + attach）。' -ForegroundColor Yellow
}
Write-Host ' 如需在容器内恢复深度相机透传: ./reset_orbbec_usb.ps1' -ForegroundColor Cyan
Write-Host ''
