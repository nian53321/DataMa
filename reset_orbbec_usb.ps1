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
      - 远程场景：摄像头 USB 枚举失败（错误代码 43）但无法物理插拔时，
        先用 pnputil 软复位设备（等价于"软件拔插"）再透传
      - 远程场景：设备卡在 WSL vhci 半挂载状态（Windows 侧消失、sysfs 不可见）时，
        可选择 wsl --shutdown 软件取回设备
      - 远程无法重启 Windows 时：设备从总线彻底消失后自动执行分级抢救
        （P1 usbipd 服务重启+幽灵节点清理 → P2 摄像头所在 Hub 复位 →
        P3 该总线根集线器复位 → P4 xHCI 控制器复位，效果最接近重启系统）；
        P3/P4 复位前自动检测"当前正在联网的网卡"挂载位置——不影响远程连接时
        自动执行，涉及时才提示确认（断网 10-30 秒，通常自动恢复）

    自动完成：
      1. 定位 usbipd 并找到深度摄像头（VID 2bc5:066b / 8086）
      2. 校验/自动选择 WSL 发行版（避免发行版名不对导致 wsl 调用静默失败）
      3. 摄像头已 attach 在 WSL 内核时跳过透传（幂等，避免反复折腾设备）
      4. 摄像头枚举失败（错误代码 43）时先软件复位再透传
      5. 按需 bind / detach / attach 到 WSL（共享内核 vhci）
      6. 检查 WSL 网络模式（mirrored 与 usbipd-win 5.x 不兼容，会提示修复）
      7. 重启 backend 容器（容器内 ensure_usb_nodes.py 自动创建 USB 设备节点）
      8. 容器内 sysfs 验证设备是否可见

    attach 的保守策略（远程场景摄像头无法物理插拔，任何失败都可能不可逆）：
      - attach 命令返回非零退出码：设备仍留在 Windows 侧，此时重试是安全的
      - attach 命令成功但设备 90 秒内未在 WSL sysfs 出现：保留现场直接退出，
        绝不自动 detach 把设备拉回 Windows——把一枚正在枚举/固件卡死边缘的
        设备反复 detach/attach 极易让其彻底掉线，这正是旧版"重试三次都失败"
        把设备搞丢的根源

    可诊断性保障（远程无人值守场景）：
      - 全程日志落盘：每次运行生成 reset_orbbec_usb_时间戳.log（保留最近 10 份），
        控制台窗口意外关闭也不丢日志
      - 失败/成功结束时暂停等待回车，不闪退
      - 全局异常捕获（trap）：任何未预料的错误都会打印并落盘，不再静默退出
.NOTES
    前置条件：
      - 已安装 Docker Desktop（WSL2 后端）
      - 已安装 usbipd-win（5.x 推荐）：winget install dorssel.usbipd-win
      - WSL2 已启用（无需特定 Ubuntu 发行版；5.x 通过共享内核 vhci 透传）
      - 摄像头已通过 USB 3.0 连接（Orbbec 指示灯稳定白色；RealSense 通电后正面蓝色灯）
    需要管理员权限（usbipd bind/attach 要求管理员），脚本会自动请求提权。
    用法（项目根目录，PowerShell）：
      ./reset_orbbec_usb.ps1                     # 透传深度摄像头（Orbbec/RealSense）
      ./reset_orbbec_usb.ps1 -WslDistro Ubuntu-22.04   # 指定发行版（默认自动选择）
      ./reset_orbbec_usb.ps1 -BusId 9-4          # 透传指定设备（调试/普通摄像头测试）
      ./reset_orbbec_usb.ps1 -Detach             # 取回深度摄像头：detach+unbind 交还 Windows 本地使用
      ./reset_orbbec_usb.ps1 -Detach -BusId 9-4  # 取回指定设备
    运行日志保存在脚本同目录 reset_orbbec_usb_*.log，排障时把日志发给维护者即可。
#>

param(
    # attach 使用的 WSL 发行版名（usbipd-win 5.x 下非必需，保留兼容旧版/多发行版场景）
    # 若指定名称不存在，脚本会自动改用机器上第一个可用的非 docker 发行版
    [string]$WslDistro = "ubuntu",
    # 显式指定设备 busid（形如 9-4）：跳过深度摄像头识别与全部抢救逻辑，直接对该设备
    # 执行 bind/attach/验证。用途：用普通 USB 摄像头等任意设备验证透传链路、调试
    [string]$BusId = "",
    # 取回模式：把设备从 WSL detach 回 Windows 并解除共享（unbind），交还本地应用使用。
    # 与透传互斥：不做 bind/attach、不重启容器、不触发任何抢救逻辑
    [switch]$Detach,
    # 卸载摄像头设备模式：移除摄像头全部 PnP 设备节点（USB 父设备、MI_xx 接口、
    # HID/相机子设备，含幽灵节点）并触发总线重扫描，让 Windows 全新重新枚举——
    # 设备管理器"卸载设备"的命令行等价，软件层面最接近物理拔插的手段。
    # 用途：设备卡在错误状态（attach 报 "Device in error state"）、设备管理器里
    # 设备带感叹号时的修复。可与 -BusId 组合卸载指定设备。
    # 注意：仅卸载设备节点，不删驱动文件；重新枚举后跑默认模式即可完成透传
    [switch]$UninstallCamera,
    # 自动卸载 VirtualBox：当 VBoxUSBMon 拦截无法通过停止服务根治（捕获节点反复重现）
    # 时，静默卸载 VirtualBox（MSI 静默卸载，不弹窗、不要求确认、不重启系统）
    [switch]$UninstallVBox,
    # 无人值守模式（Web 界面"透传深度相机"按钮经 usb_agent.ps1 调用）：
    # 所有 y/n 确认自动作答（继续分级抢救/危险复位=是，wsl --shutdown 取回=否），
    # busid 总线号选择取默认值，全程无交互
    [switch]$Yes,
    # 跳过第 7/8 步（重启 backend 容器 + 容器内验证）：usb_agent 以 SYSTEM 运行
    # 本脚本时 docker CLI 未必可用，且重启容器会打断用户正在使用的界面；容器内
    # 设备节点由前端随后的 /orbbec/status 轮询触发 ensure_usb_nodes 自动修复
    [switch]$SkipContainerRestart
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
# 注意：日志落盘在提权之后才开始，避免非提权的启动实例产生垃圾日志文件
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '需要管理员权限，正在请求提权（新窗口中继续运行，结束后窗口自动关闭，日志见脚本目录）...' -ForegroundColor Yellow
    # 提权重启进程会丢失原始参数，必须逐个转发（曾因漏转 -BusId，导致提权后
    # 走默认深度摄像头流程，用户指定的设备被无视）
    $relaunchArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -WslDistro `"$WslDistro`""
    if ($BusId)   { $relaunchArgs += " -BusId `"$BusId`"" }
    if ($Detach)  { $relaunchArgs += ' -Detach' }
    if ($UninstallCamera) { $relaunchArgs += ' -UninstallCamera' }
    if ($UninstallVBox)   { $relaunchArgs += ' -UninstallVBox' }
    if ($Yes)             { $relaunchArgs += ' -Yes' }
    if ($SkipContainerRestart) { $relaunchArgs += ' -SkipContainerRestart' }
    Start-Process powershell -Verb RunAs -ArgumentList $relaunchArgs
    exit 0
}

function Write-Step { param($msg) Write-Host "[步骤] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err2 { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

# ---------- 运行日志落盘（控制台窗口意外关闭/闪退也不丢日志） ----------
# 每次运行在脚本所在目录生成 reset_orbbec_usb_时间戳.log（Transcript 记录全部输出），
# 保留最近 10 份防止堆积。日志写入失败不阻断脚本。
$Script:LogFile = Join-Path $ProjectRoot ("reset_orbbec_usb_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
try {
    $null = Start-Transcript -Path $Script:LogFile -Append
    Get-ChildItem -Path $ProjectRoot -Filter 'reset_orbbec_usb_*.log' |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch { Write-Host "（警告：日志落盘不可用，仅屏幕输出）" -ForegroundColor DarkGray }
Write-Host "本次运行日志: $Script:LogFile" -ForegroundColor DarkGray

# 统一收尾：停日志 + 提示日志位置 + 退出（日志已落盘，窗口自动关闭不丢信息）
function Stop-Run { param($exitCode = 1, $tip)
    if ($tip) { Write-Host $tip -ForegroundColor Yellow }
    try { Stop-Transcript | Out-Null } catch {}
    Write-Host ''
    Write-Host "完整日志已保存: $Script:LogFile（排障请提供此文件）" -ForegroundColor DarkGray
    exit $exitCode
}
function Exit-Fail { param($msg) Write-Err2 $msg; Stop-Run 1 }

# 全局异常捕获：任何未预料的错误（命令不存在、cmdlet 失败等）都打印并落盘，
# 不再让提权窗口带着错误信息闪退。处理器内只用内置命令，自包含不依赖外部函数
trap {
    Write-Host "  [X] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    if ($Script:LogFile) { Write-Host "  完整日志已保存: $Script:LogFile（排障请提供此文件）" -ForegroundColor DarkGray }
    exit 1
}

# 摄像头正常态特征（Orbbec 2bc5 / Intel RealSense 8086）
$CameraPattern = '2bc5:066b|Orbbec Femto Bolt|8086:0b5c|8086:0b5d|RealSense'
# USB 枚举失败特征（错误代码 43：描述符请求失败/无效，VID:PID 显示为 0000:xxxx）
$BrokenPattern  = '0000:0002|0000:0005|0000:0006|描述符请求失败|描述符无效|配置描述符|未知 USB 设备'

function Get-UsbipdConnectedLines { param($usbipdPath)
    # 只取 usbipd list 的 Connected 段：Persisted 段的 GUID 不是 busid，误取会导致 attach 报错。
    # 注意：必须 return $lines（不带逗号）——逗号会让整个数组作为"单个对象"流过管道，
    # 下游 Where-Object 的 $_ 变成整个行列表而非逐行，导致匹配逻辑整体错乱
    # （曾导致网卡 busid 解析出 "BUSID" 这种表头值）。
    $list = & $usbipdPath list
    $lines = @()
    $inConnected = $false
    foreach ($l in $list) {
        if ($l -match '^Connected:') { $inConnected = $true; continue }
        if ($l -match '^Persisted:') { $inConnected = $false }
        if ($inConnected -and $l -match '\S') { $lines += $l }
    }
    return $lines
}

function Find-Camera { param($usbipdPath)
    # 在 Connected 段查找正常态深度摄像头行，返回行文本（$null 表示未找到）
    $lines = Get-UsbipdConnectedLines $usbipdPath
    $m = $lines | Select-String -Pattern $CameraPattern | Select-Object -First 1
    if ($m) { return $m.ToString() }
    return $null
}

function Find-Broken { param($usbipdPath)
    # 在 Connected 段查找枚举失败设备行（错误代码 43）
    $lines = Get-UsbipdConnectedLines $usbipdPath
    $m = $lines | Select-String -Pattern $BrokenPattern | Select-Object -First 1
    if ($m) { return $m.ToString() }
    return $null
}

function Test-CameraInWsl { param([string]$Vids = '2bc5 8086')
    # WSL 共享内核 sysfs 是否可见指定 VID 的 USB 设备（默认深度摄像头 Orbbec/RealSense；
    # -BusId 模式下传入目标设备的实际 VID）。
    # usbipd-win 5.x attach 后 Windows 侧 list 不再显示 Attached（设备从列表消失），
    # 唯一可靠判据是 WSL 内核 sysfs。返回设备 sysfs 路径（$null 表示不可见；
    # wsl/发行版不可用时同样返回 $null，由调用方结合上下文判断）。
    $grepArgs = (@($Vids -split '\s+' | Where-Object { $_ }) | ForEach-Object { "-e $_" }) -join ' '
    $r = & wsl -d $WslDistro -e sh -c "grep -l $grepArgs /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
    if ($r) { return ($r -join ' ') }
    return $null
}

function Invoke-UsbSoftReset {
    # 对处于枚举失败状态（VID_0000，描述符错误，即设备管理器错误代码 43）的 USB 设备
    # 执行软件复位，等价于远程场景下无法物理插拔时的"软拔插"：
    #   第一档：pnputil /restart-device（禁用-启用设备，触发 USB 端口 reset 信号）
    #   第二档：禁用-启用错误设备所在的 USB 集线器（更强的端口级复位，同集线器
    #           其他设备会短暂掉线重连，远程无人值守场景无碍）
    # 复位后轮询 usbipd list，等摄像头以正常 VID 重新枚举。返回 $true 表示恢复。
    Write-Step '软复位 USB 枚举失败设备（软件等价于拔插）'
    $errDevs = @(Get-PnpDevice -PresentOnly | Where-Object {
        $_.Class -eq 'USB' -and $_.InstanceId -like 'USB\VID_0000*' -and $_.InstanceId -notmatch 'MI_\d'
    })
    if ($errDevs.Count -eq 0) {
        Write-Warn '设备管理器中当前无在线的枚举失败设备（usbipd 列表可能滞后）'
        return $false
    }
    foreach ($d in $errDevs) {
        Write-Host "  复位设备: $($d.FriendlyName) [$($d.InstanceId)]"
        & pnputil /restart-device "$($d.InstanceId)" | Out-Null
    }
    for ($i = 0; $i -lt 4; $i++) {
        Start-Sleep -Seconds 5
        if (Find-Camera $usbipd) { Write-OK "设备已恢复枚举（等待 $((($i + 1) * 5)) 秒）"; return $true }
        Write-Host "  等待设备重新枚举...（$((($i + 1) * 5)) 秒）"
    }

    Write-Warn '单设备复位未恢复，尝试复位其所在的 USB 集线器（同集线器其他设备会短暂掉线重连）'
    $hubIds = @()
    foreach ($d in $errDevs) {
        $p = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
        if ($p -and $hubIds -notcontains $p) { $hubIds += $p }
    }
    foreach ($h in $hubIds) {
        $hd = Get-PnpDevice -InstanceId $h -ErrorAction SilentlyContinue
        Write-Host "  复位集线器: $($hd.FriendlyName) [$h]"
        Disable-PnpDevice  -InstanceId $h -Confirm:$false -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        Enable-PnpDevice   -InstanceId $h -Confirm:$false -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 4; $i++) {
        Start-Sleep -Seconds 5
        if (Find-Camera $usbipd) { Write-OK "设备已恢复枚举（等待 $((($i + 1) * 5)) 秒）"; return $true }
        Write-Host "  等待设备重新枚举...（$((($i + 1) * 5)) 秒）"
    }
    return $false
}

function Get-YesNo { param($question, $autoAnswer)
    # y/n 确认：空回车/无效输入时重复询问，必须明确输入 y 或 n。
    # 避免直接按回车被当作"否"，导致关键复位级别被无意跳过（远程排障时曾连续发生）。
    # 无人值守（-Yes，经 usb_agent 触发）时按调用方给定的 $autoAnswer 自动作答：
    # 继续抢救/危险复位=是，wsl --shutdown 取回设备=否（会杀掉 Docker Desktop）
    if ($Yes -and $null -ne $autoAnswer) {
        Write-Host "  $question" -ForegroundColor DarkGray
        Write-Host "  [自动确认] $(if ($autoAnswer) { 'y' } else { 'n' })" -ForegroundColor DarkGray
        return $autoAnswer
    }
    while ($true) {
        $a = (Read-Host "  $question").Trim()
        Write-Host "  [输入] $a" -ForegroundColor DarkGray
        if ($a -match '^[yY]') { return $true }
        if ($a -match '^[nN]') { return $false }
        Write-Host '  未识别的输入，请输入 y（是）或 n（否）后按回车' -ForegroundColor Yellow
    }
}

function Test-UsbNetConnected {
    # 是否存在处于连接状态的 USB 网卡（危险复位后判断网络是否恢复）
    return (@(Get-CimInstance Win32_NetworkAdapter -ErrorAction SilentlyContinue |
        Where-Object { $_.NetConnectionStatus -eq 2 -and $_.PNPDeviceID -like 'USB\*' })).Count -gt 0
}

function Wait-NetRecovered { param([int]$seconds = 20)
    # 危险复位（联网网卡在复位范围内）后等待网卡恢复（WiFi 重新枚举一般 10-20 秒）。
    # 返回 $true=已恢复；$false=未恢复——本机不能重启，调用方据此自动升级下一级
    # 复位（控制器级复位是软件层面最后的恢复手段），而不是靠重启兜底
    $n = [Math]::Max(1, [int]($seconds / 5))
    for ($i = 0; $i -lt $n; $i++) {
        Start-Sleep -Seconds 5
        if (Test-UsbNetConnected) { Write-OK "联网网卡已恢复（等待 $(($i + 1) * 5) 秒）"; return $true }
        Write-Host "  等待联网网卡恢复...（$(($i + 1) * 5) 秒）"
    }
    return $false
}

function Ensure-UsbipdService {
    # 确保 usbipd 服务运行；返回 $true=已运行。
    # 实测：Restart-Service usbipd -Force 可能停掉服务却没拉起来（错误被
    # SilentlyContinue 吞掉），此后所有 usbipd 命令都报
    # "The service is currently not running; a reboot should fix that"。
    # 服务起不来的最常见根因：服务停止时旧 usbipd.exe 进程挂死未退，占住实例
    # 锁（命名管道/互斥体），新实例一启动就退出，服务永远到不了 Running——
    # 先强杀残留进程再启动。启动失败的原始错误用 sc.exe start 采集显示；
    # 3 轮全失败时输出诊断（残留进程/服务配置/SCM 事件/崩溃记录）定位根因
    for ($i = 1; $i -le 3; $i++) {
        $svc = Get-Service usbipd -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq 'Running') { return $true }
        $st = if ($svc) { $svc.Status } else { '未安装' }
        Write-Warn "usbipd 服务未运行（当前: $st），尝试启动（第 $i 次）..."
        $leftover = @(Get-Process usbipd -ErrorAction SilentlyContinue)
        if ($leftover.Count -gt 0) {
            Write-Warn "发现残留 usbipd.exe 进程（PID: $($leftover.Id -join ', ')）——服务已停但进程未退（占住实例锁），强杀后再启动"
            $leftover | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        if ($svc) { Start-Service usbipd -ErrorAction SilentlyContinue }
        $t = 0
        while ($t -lt 15) {
            Start-Sleep -Seconds 3; $t += 3
            $svc = Get-Service usbipd -ErrorAction SilentlyContinue
            if ($svc -and $svc.Status -eq 'Running') {
                Write-OK "usbipd 服务已启动（等待 $t 秒）"
                return $true
            }
            Write-Host "  等待服务启动...（$t 秒）"
        }
        # Start-Service 未生效：sc.exe start 采集真实错误码（1053 未响应/1067 进程意外退出等）
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { $scOut = @(& sc.exe start usbipd 2>&1 | Where-Object { "$_" -match '\S' }) } finally { $ErrorActionPreference = $prevEap }
        if ($LASTEXITCODE -eq 0) {
            Start-Sleep -Seconds 3
            $svc = Get-Service usbipd -ErrorAction SilentlyContinue
            if ($svc -and $svc.Status -eq 'Running') {
                Write-OK 'usbipd 服务已启动（sc.exe 兜底成功）'
                return $true
            }
        } else {
            Write-Warn "sc.exe start 失败（错误码 $LASTEXITCODE）: $($scOut -join ' | ')"
            # 1068 = 依赖服务/组启动失败。usbipd 官方安装没有任何依赖；实测某机器
            # 上 usbipd 服务被配置为依赖 VBoxUSBMon（VirtualBox USB 过滤驱动），
            # 该驱动被删除后 usbipd 便永远起不来（sc start 报 1068）。修复：
            # 清除依赖配置（depend= 后必须有空格，值为空即清除全部依赖）
            if ($LASTEXITCODE -eq 1068) {
                $prevEap2 = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    $qc2 = @(& sc.exe qc usbipd 2>&1 | Where-Object { "$_" -match '\S' })
                    $depLine = @($qc2 | Where-Object { "$_" -match 'DEPENDENCIES' }) | Select-Object -First 1
                    $dep = ''
                    if ("$depLine" -match 'DEPENDENCIES\s*:\s*(.+)$') { $dep = $Matches[1].Trim() }
                    if ($dep) {
                        Write-Warn "根因找到：usbipd 服务被配置为依赖 [$dep]（官方安装无任何依赖；该依赖服务已不存在导致 1068）。清除依赖配置..."
                        # sc.exe 清除依赖的语法：depend= /（"depend=" 后必须有空格，
                        # 值为单个 /；传 "" 会被 sc.exe 当作缺参数报 1639）
                        & sc.exe config usbipd depend= / | Out-Null
                        Start-Sleep -Seconds 2
                        & sc.exe start usbipd | Out-Null
                        Start-Sleep -Seconds 3
                        $svc = Get-Service usbipd -ErrorAction SilentlyContinue
                        if ($svc -and $svc.Status -eq 'Running') {
                            Write-OK 'usbipd 服务已启动（清除依赖配置后成功）'
                            return $true
                        }
                        Write-Warn '清除依赖后仍未启动，继续下一轮尝试'
                    }
                } catch {
                    Write-Warn "依赖检测/清除过程出错: $($_.Exception.Message)"
                } finally { $ErrorActionPreference = $prevEap2 }
            }
        }
    }
    # 3 轮全失败：采集诊断信息，定位根因（不再只甩给"重启"）
    Write-Warn '服务启动失败诊断：'
    $p = @(Get-Process usbipd -ErrorAction SilentlyContinue)
    Write-Host "  残留进程: $(if ($p.Count) { ($p.Id -join ', ') } else { '无' })" -ForegroundColor Yellow
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $qc = @(& sc.exe qc usbipd 2>&1 | Where-Object { "$_" -match '\S' })
        Write-Host "  服务配置: $($qc -join ' | ')" -ForegroundColor Yellow
    } finally { $ErrorActionPreference = $prevEap }
    try {
        $scm = Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Service Control Manager' } -MaxEvents 60 -ErrorAction SilentlyContinue |
            Where-Object { $_.Message -match 'usbipd' } | Select-Object -First 5
        foreach ($e in $scm) {
            Write-Host "  SCM事件 $($e.TimeCreated): $(($e.Message -split "`n")[0])" -ForegroundColor Yellow
        }
        $crash = Get-WinEvent -FilterHashtable @{ LogName = 'Application' } -MaxEvents 200 -ErrorAction SilentlyContinue |
            Where-Object { $_.ProviderName -in @('Application Error', 'Windows Error Reporting') -and $_.Message -match 'usbipd' } | Select-Object -First 3
        foreach ($e in $crash) {
            Write-Host "  崩溃记录 $($e.TimeCreated): $(($e.Message -split "`n")[0])" -ForegroundColor Yellow
        }
    } catch { }
    return $false
}

function Invoke-CameraDeviceUninstall {
    # 卸载摄像头设备节点（设备管理器"卸载设备"的命令行等价）：移除摄像头全部
    # PnP 节点（USB 父设备、MI_xx 接口、HID/相机子设备，含幽灵节点），再触发
    # 总线重扫描让 Windows 全新重新枚举——软件层面最接近物理拔插的手段。
    # 仅卸载设备节点不删驱动文件，重新枚举后 Windows 自动重装驱动。
    # 返回 $true 表示执行了卸载（无论设备是否立即回来）
    param(
        # PnP 实例 ID 匹配模式（深度摄像头 VID:PID；-BusId 模式下由调用方传入
        # 该设备的 VID_xxxx&PID_yyyy）
        [string]$VidPattern = 'VID_8086&PID_0B5C|VID_8086&PID_0B5D|VID_2BC5'
    )
    Write-Step '卸载摄像头设备节点（卸载设备 + 重新扫描，软件级拔插）'
    $nodes = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
        $_.InstanceId -match $VidPattern })
    if ($nodes.Count -eq 0) {
        Write-Warn "未找到匹配的设备节点（模式 $VidPattern）——设备不在总线上，无需卸载"
        return $false
    }
    foreach ($n in $nodes) {
        Write-Host "  卸载节点: $($n.InstanceId) [$($n.Status)] $($n.FriendlyName)" -ForegroundColor DarkGray
        & pnputil /remove-device "$($n.InstanceId)" | Out-Null
    }
    Start-Sleep -Seconds 2
    & pnputil /scan-devices | Out-Null
    Write-OK "已卸载 $($nodes.Count) 个设备节点并触发重扫描（Windows 正在重新枚举设备）"
    return $true
}

function Invoke-VBoxFilterCleanup {
    # 清理设备类 UpperFilters 中的 VBoxUSB* 过滤驱动引用。
    # 背景：VBoxUSBMon 以 USB 设备类的上层过滤驱动方式挂载（正是它反复捕获
    # USB 设备的机制）。本脚本/卸载器删除其服务键后，类过滤引用若残留，下次
    # 开机 USB 控制器/Hub 的驱动栈将无法加载（错误代码 39），全部 USB 设备
    #（含远程访问依赖的 USB 网卡）失效且无法远程恢复——比摄像头不可用严重
    # 得多，必须与删服务同步清理。移除引用不影响 Windows 自身 USB 功能，
    # 仅 VirtualBox 虚拟机的 USB 直通失效（重装 VirtualBox 可恢复）
    foreach ($clsKey in @(Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class' -ErrorAction SilentlyContinue)) {
        $uf = (Get-ItemProperty -Path $clsKey.PSPath -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
        if ($uf -and @($uf | Where-Object { $_ -match '^VBoxUSB' }).Count -gt 0) {
            $new = @($uf | Where-Object { $_ -notmatch '^VBoxUSB' })
            if ($new.Count -gt 0) {
                Set-ItemProperty -Path $clsKey.PSPath -Name UpperFilters -Value $new -ErrorAction SilentlyContinue
            } else {
                Remove-ItemProperty -Path $clsKey.PSPath -Name UpperFilters -ErrorAction SilentlyContinue
            }
            Write-Warn "已清理设备类 $($clsKey.PSChildName) UpperFilters 中的 VBoxUSB 过滤驱动引用（防重启后 USB 设备失效）"
        }
    }
}

function Test-UsbipdRuntimeVBoxDep {
    # 运行时自证：usbipd 命令输出提及 VBoxUsbMon = 该构建 bind/attach 依赖
    # VBoxUSBMon（官方 usbipd-win 绝无此类报错）。VBoxUSBMon 服务被删后 usbipd
    # 每条命令都会报 "The VBoxUsbMon driver is not correctly installed"——
    # 正好自证依赖存在。需 usbipd 服务在运行（未运行时返回 $false，不作否定证据）
    param($usbipdPath)
    if (-not $usbipdPath) { return $false }
    $svc = Get-Service usbipd -ErrorAction SilentlyContinue
    if (-not ($svc -and $svc.Status -eq 'Running')) { return $false }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = @(& $usbipdPath list 2>&1 | ForEach-Object { "$_" })
        return ((@($out | Where-Object { $_ -match 'VBoxUsbMon' }).Count -gt 0))
    } catch { return $false } finally { $ErrorActionPreference = $prevEap }
}

function Test-UsbipdVBoxIntegration {
    # 多证据检测 usbipd-win 是否为 VBoxUSBMon 集成构建（官方 usbipd-win 不含
    # VBox 组件）。本机/远程实测（usbipd 5.3.0-54 自定义版）：bind 通过 VBoxUSBMon
    # 接管设备（设备变为 VID_80EE&PID_CAFE 的代理节点，即"捕获节点"），attach
    # 依赖 VBoxUSBMon 服务运行——此前的"VirtualBox 清理"把它们误删，导致 attach
    # 报 "The VBoxUsbMon driver is not correctly installed"。任何一条证据命中
    # 即按集成构建处理：
    #   1) usbipd 安装目录（含子目录，最深 2 层）存在 VBoxUSBMon.sys / VBoxUSB.inf
    #   2) 运行时自证（Test-UsbipdRuntimeVBoxDep）
    #   3) VBoxUSBMon 服务键存在（注册表，含被标记删除的残留键）或驱动文件在
    #      System32\drivers——宁可误判为集成而保留驱动，绝不冒误删依赖的风险
    #（远程实测教训：驱动文件不在安装目录 Drivers\ 下，只查文件导致误判非集成，
    #  脚本删掉 VBoxUSBMon 后 usbipd bind/attach 全部报废）
    param($usbipdPath)
    if ($usbipdPath) {
        $root = Split-Path -Parent $usbipdPath
        $hit = @(Get-ChildItem -Path $root -Recurse -Depth 2 -Include 'VBoxUSBMon.sys','VBoxUSB.inf' -File -ErrorAction SilentlyContinue)
        if ($hit.Count -gt 0) { return $true }
    }
    if (Test-UsbipdRuntimeVBoxDep $usbipdPath) { return $true }
    if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\VBoxUSBMon') { return $true }
    if (Test-Path "$env:SystemRoot\System32\drivers\VBoxUSBMon.sys") { return $true }
    return $false
}

function Find-VBoxMonSys {
    # 多路径查找 VBoxUSBMon.sys。sc delete 只删服务键不删文件——驱动文件必然
    # 还在磁盘某处，按可能性依次找：usbipd 安装目录（含子目录）→ 注册表残留键
    # 的 ImagePath → System32\drivers → VirtualBox 安装目录 → DriverStore 包目录
    param($usbipdPath)
    $cands = @()
    if ($usbipdPath) {
        $cands += @(Get-ChildItem -Path (Split-Path -Parent $usbipdPath) -Recurse -Depth 2 -Filter 'VBoxUSBMon.sys' -File -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
    }
    $monKey = 'HKLM:\SYSTEM\CurrentControlSet\Services\VBoxUSBMon'
    if (Test-Path $monKey) {
        $ip = (Get-ItemProperty $monKey -ErrorAction SilentlyContinue).ImagePath
        if ($ip) {
            $ip = $ip -replace '^\\\?\?\\', '' -replace '^\\SystemRoot\\', "$env:SystemRoot\"
            if ($ip -and (Test-Path $ip)) { $cands += $ip }
        }
    }
    if (Test-Path "$env:SystemRoot\System32\drivers\VBoxUSBMon.sys") { $cands += "$env:SystemRoot\System32\drivers\VBoxUSBMon.sys" }
    foreach ($vd in @('C:\Program Files\Oracle\VirtualBox\drivers\USB\filter\VBoxUSBMon.sys',
                      'C:\Program Files (x86)\Oracle\VirtualBox\drivers\USB\filter\VBoxUSBMon.sys')) {
        if (Test-Path $vd) { $cands += $vd }
    }
    $regDir = (Get-ItemProperty 'HKLM:\SOFTWARE\Oracle\VirtualBox' -ErrorAction SilentlyContinue).InstallDir
    if ($regDir) {
        $vd = Join-Path $regDir 'drivers\USB\filter\VBoxUSBMon.sys'
        if (Test-Path $vd) { $cands += $vd }
    }
    $cands += @(Get-ChildItem "$env:SystemRoot\System32\DriverStore\FileRepository" -Directory -Filter 'vboxusb*' -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName 'VBoxUSBMon.sys' } | Where-Object { Test-Path $_ })
    return @($cands | Select-Object -Unique)
}

function Invoke-UsbipdMsiRepair {
    # MSI 修复安装兜底（/fvomus：缺失/旧版文件重装 + 机器注册表重写）——驱动文件
    # 在所有常见位置都找不到时，从 usbipd-win 安装包恢复 VBoxUSBMon/VBoxUSB。
    # 修复期间 usbipd 服务会被 MSI 重启，静默无界面。返回 $true=修复成功退出
    $app = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'usbipd' } | Select-Object -First 1)
    if (-not $app) { Write-Warn '注册表中未找到 usbipd-win 卸载项，无法 MSI 修复'; return $false }
    $code = $app.PSChildName
    if ($code -notmatch '^[{(][0-9A-Fa-f-]{36}[)}]$') {
        Write-Warn "usbipd 卸载项无有效 ProductCode（$code），无法 MSI 修复"
        return $false
    }
    Write-Warn "MSI 修复安装: $($app.DisplayName)（静默 1-3 分钟，期间 usbipd 服务会重启）..."
    try {
        $p = Start-Process msiexec.exe -ArgumentList '/fvomus', $code, '/qn', '/norestart' -Wait -PassThru
        if ($p.ExitCode -eq 0 -or $p.ExitCode -eq 3010) {
            Write-OK 'MSI 修复完成'
            return $true
        }
        Write-Warn "MSI 修复退出码 $($p.ExitCode)（0/3010=成功）"
        return $false
    } catch { Write-Warn "MSI 修复执行失败: $($_.Exception.Message)"; return $false }
}

function Invoke-VBoxIntegrationRestore {
    # 重建 VBoxUSBMon / VBoxUSB 驱动服务（VBoxUSBMon 集成版 usbipd 的 bind/attach
    # 依赖）。此前脚本的 VirtualBox 清理误删了这两个服务——sc delete 只删服务键
    # 不删文件，驱动文件仍在磁盘（Find-VBoxMonSys 多路径查找），全找不到时走
    # MSI 修复兜底。恢复参数取自本机同构建实测的原始配置：Type=1(kernel)、
    # Start=3(demand)、ErrorControl=1、ImagePath=\??\<VBoxUSBMon.sys 路径>。
    # 返回 $true = VBoxUSBMon 已运行
    param($usbipdPath)
    Write-Step '恢复 VBoxUSBMon / VBoxUSB 驱动服务（VBoxUSBMon 集成版 usbipd 的 bind/attach 依赖）'
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\VBoxUSBMon'
    $mon = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
    if ($mon -and $mon.State -eq 'Running' -and -not (Test-UsbipdRuntimeVBoxDep $usbipdPath)) {
        # 运行中且 usbipd 不再报 VBoxUsbMon 错 = 状态健康，无需重建
        Write-OK 'VBoxUSBMon 服务运行中'
    } else {
        if ($mon -and $mon.State -eq 'Running') {
            # 运行中但 usbipd 仍报"未正确安装"——服务键被标记删除（此前 sc delete
            # 时驱动仍在运行，SCM 在驱动卸载后才真正删键）。停止驱动让键删除生效
            Write-Warn 'VBoxUSBMon 运行中但被 usbipd 判为未正确安装（服务键被标记删除）——停止驱动后重建'
            & sc.exe stop VBoxUSBMon | Out-Null
            $t = 0
            while ((Test-Path $key) -and ($t -lt 15)) { Start-Sleep -Seconds 3; $t += 3 }
        }
        # 找驱动文件（多路径），全找不到走 MSI 修复兜底
        $monSys = @(Find-VBoxMonSys $usbipdPath) | Select-Object -First 1
        if (-not $monSys) {
            Write-Warn '常见位置均未找到 VBoxUSBMon.sys，尝试 MSI 修复安装恢复（1-3 分钟）...'
            if (Invoke-UsbipdMsiRepair) {
                $monSys = @(Find-VBoxMonSys $usbipdPath) | Select-Object -First 1
            }
        }
        if (-not $monSys) {
            Write-Err2 'VBoxUSBMon.sys 驱动文件丢失且 MSI 修复未能恢复，无法重建服务。'
            Write-Host '  请从正常的同构建机器拷贝以下文件到本机相同路径后重跑本脚本:' -ForegroundColor Yellow
            Write-Host '    C:\Program Files\usbipd-win\Drivers\VBoxUSBMon.sys' -ForegroundColor Yellow
            Write-Host '    （建议一并拷贝 VBoxUSB.sys / VBoxUSB.inf / VBoxUSB.cat）' -ForegroundColor Yellow
            return $false
        }
        Write-Host "  驱动文件: $monSys" -ForegroundColor DarkGray
        # 键存在但服务未运行：先启动；启动失败（SCM 报 1072=键被标记删除）则删键重建
        if (Test-Path $key) {
            & sc.exe start VBoxUSBMon | Out-Null
            Start-Sleep -Seconds 2
            $mon = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
            if (-not ($mon -and $mon.State -eq 'Running')) {
                Write-Warn 'VBoxUSBMon 启动失败（服务键可能被标记删除）——删除键后重建'
                & sc.exe stop VBoxUSBMon | Out-Null
                & sc.exe delete VBoxUSBMon | Out-Null
                $t = 0
                while ((Test-Path $key) -and ($t -lt 15)) { Start-Sleep -Seconds 3; $t += 3 }
                if (Test-Path $key) {
                    Write-Warn '服务键被占用未被删除（SCM 持有句柄）——本次开机内无法重建，需重启 Windows 后重跑本脚本'
                    return $false
                }
            }
        }
        if (-not (Test-Path $key)) {
            # 重建：sc create 先注册（占位 ImagePath，避开 \??\ 前缀 + 含空格路径的
            # sc.exe binpath= 转义坑），再写注册表修正为规范 NT 路径
            Write-Warn "VBoxUSBMon 服务不存在——重建（ImagePath=\??\$monSys）..."
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $null = & sc.exe create VBoxUSBMon type= kernel start= demand error= normal displayname= "VirtualBox USB Monitor Service" binpath= "C:\placeholder" 2>&1
            } finally { $ErrorActionPreference = $prevEap }
            if (-not (Test-Path $key)) {
                # sc create 未生效（极少见）：直接写注册表键（SCM 按需读键，同样生效）
                New-Item -Path $key -Force | Out-Null
            }
            Set-ItemProperty -Path $key -Name ImagePath -Value "\??\$monSys" -Type String
            Set-ItemProperty -Path $key -Name Type -Value 1 -Type DWord
            Set-ItemProperty -Path $key -Name Start -Value 3 -Type DWord
            Set-ItemProperty -Path $key -Name ErrorControl -Value 1 -Type DWord
            Set-ItemProperty -Path $key -Name DisplayName -Value 'VirtualBox USB Monitor Service' -Type String
        }
        & sc.exe start VBoxUSBMon | Out-Null
        Start-Sleep -Seconds 2
        $mon = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
        if (-not $mon -or $mon.State -ne 'Running') {
            Write-Warn "VBoxUSBMon 启动失败（当前: $($mon.State)；若 SCM 报 1072=标记删除，需重启 Windows 后重跑本脚本）"
            return $false
        }
        Write-OK "VBoxUSBMon 服务运行中（$($mon.PathName)）"
        # VBoxUSBMon 恢复后重启 usbipd 服务：服务进程可能在启动时缓存了
        # "VBoxUSBMon 未安装"状态，不重启会继续对所有命令报错
        $null = Ensure-UsbipdService
    }
    # VBoxUSB：捕获节点（VID_80EE&PID_CAFE 代理设备）的功能驱动（PnP 驱动包）。
    # 服务键被删时代理节点无法加载驱动（attach 报 Device in error state 的一大
    # 来源）。PnP 包常驻 DriverStore，捕获节点重新枚举时 PnP 会自动重建服务键；
    # 此处仅在键完全缺失时主动补装（先找 usbipd 目录，再找 DriverStore 原始包）
    if (-not (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Services\VBoxUSB')) {
        Write-Warn 'VBoxUSB 驱动服务不存在（捕获节点的功能驱动）——重装驱动包...'
        $inf = $null
        if ($usbipdPath) {
            $inf = @(Get-ChildItem -Path (Split-Path -Parent $usbipdPath) -Recurse -Depth 2 -Filter 'VBoxUSB.inf' -File -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName }) | Select-Object -First 1
        }
        if (-not $inf) {
            $inf = @(Get-ChildItem "$env:SystemRoot\System32\DriverStore\FileRepository" -Directory -Filter 'vboxusb*' -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName 'VBoxUSB.inf' } | Where-Object { Test-Path $_ }) | Select-Object -First 1
        }
        if ($inf) {
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try { & pnputil /add-driver "$inf" /install | Out-Null } finally { $ErrorActionPreference = $prevEap }
            Write-OK "VBoxUSB 驱动包已重装（来源: $inf，PnP 按需加载）"
        } else {
            Write-Warn '未找到 VBoxUSB.inf（若后续 bind 报 Device in error state，请从正常机器拷贝 VBoxUSB.sys/.inf/.cat 到 usbipd-win\Drivers 后重跑）'
        }
    } else {
        Write-OK 'VBoxUSB 驱动服务在位'
    }
    return $true
}

function Invoke-VBoxUninstall {
    # 静默卸载 VirtualBox（仅适用于"真 VirtualBox 干扰"场景的最后手段）。
    # 从注册表卸载键定位（不用 Win32_Product——它会触发全机 MSI 自检，慢且有副作用）；
    # MSI 静默卸载：/qn 无界面、/norestart 不重启。注册表找不到卸载项（仅残留驱动）
    # 时降级为删除驱动服务。已确认无需用户交互（用户明确授权）。
    # 集成构建保护：VBoxUSBMon 集成版 usbipd 的 bind/attach 依赖 VBox 驱动，
    # 卸载=直接废掉 usbipd（实测：删 VBoxUSBMon 服务后 attach 报
    # "The VBoxUsbMon driver is not correctly installed"），绝不可执行
    if ($VBoxIntegrated -or (Test-UsbipdRuntimeVBoxDep $usbipd)) {
        Write-Warn '跳过 VirtualBox 卸载：本机 usbipd 为 VBoxUSBMon 集成构建（检测或运行时自证命中），VBox 驱动是其 bind/attach 的依赖件'
        return
    }
    # 无论走哪条路径，先清理类过滤引用（见 Invoke-VBoxFilterCleanup：删服务必须
    # 同步清引用，否则重启后 USB 设备全体失效）
    Invoke-VBoxFilterCleanup
    Write-Step '静默卸载 VirtualBox（VBoxUSBMon 拦截已无法通过停止服务根治）'
    $vboxApps = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                               'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'VirtualBox' })
    if ($vboxApps.Count -eq 0) {
        Write-Warn '注册表中未找到 VirtualBox 卸载项（可能仅残留驱动）。改用驱动级清理（仅 USB 相关，不动网络/虚拟化驱动）：'
        foreach ($svc in @('VBoxUSBMon', 'VBoxUSB')) {
            $d = Get-CimInstance Win32_SystemDriver -Filter "Name='$svc'" -ErrorAction SilentlyContinue
            if ($d) {
                & sc.exe stop $svc | Out-Null
                & sc.exe delete $svc | Out-Null
                Write-Host "  已删除驱动服务: $svc" -ForegroundColor DarkGray
            }
        }
        Write-Host '  驱动服务已删除（重启后彻底消失）。本次开机内不会再拦截 USB 设备。' -ForegroundColor Yellow
        return
    }
    foreach ($app in $vboxApps) {
        Write-Host "  卸载: $($app.DisplayName)（静默，可能需要 1-3 分钟）..." -ForegroundColor DarkGray
        if ($app.UninstallString -match '^"?(.*?\.msi)"?\s*(.*)$') {
            $msi = $Matches[1]; $msiArgs = $Matches[2]
            & msiexec /x "$msi" /qn /norestart | Out-Null
        } elseif ($app.UninstallString) {
            # 非 MSI 安装器（如 EXE）：带静默参数执行
            $us = $app.UninstallString -replace '^"', '' -replace '"\s*$', ''
            & cmd /c "$us /S /norestart" | Out-Null
        }
    }
    Start-Sleep -Seconds 3
    $stillDrv = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
    if (-not $stillDrv -or $stillDrv.State -eq 'Stopped') {
        Write-OK 'VirtualBox 已卸载/驱动已停止（USB 拦截解除）'
    } else {
        # 驱动仍在运行：卸载可能仍在后台进行，删除服务强制清除本次开机内的拦截
        & sc.exe stop VBoxUSBMon | Out-Null
        & sc.exe delete VBoxUSBMon | Out-Null
        Write-Warn 'VBoxUSBMon 服务已删除（重启后彻底消失）'
    }
}

# ---------- 0.8 VBoxUSBMon 集成构建检测（决定 VBox 驱动的处理方向） ----------
# 本机/远程实测：usbipd-win 为 VBoxUSBMon 集成构建（Drivers 目录含 VBoxUSBMon.sys /
# VBoxUSB.inf / VBoxUSB.sys），bind/attach 依赖这两个 VBox 驱动——此前的"VirtualBox
# 清理"把它们误删，导致 attach 报 "The VBoxUsbMon driver is not correctly
# installed"。
#   集成构建：绝不清理/停用/卸载 VBox 驱动，缺失时自动恢复（Invoke-VBoxIntegrationRestore）
#   非集成构建（真 VirtualBox 干扰）：保留过滤器引用清理（防重启后 USB 设备失效）
$usbipdProbe = (Get-Command usbipd -ErrorAction SilentlyContinue).Source
if (-not $usbipdProbe) {
    $candProbe = 'C:\Program Files\usbipd-win\usbipd.exe'
    if (Test-Path $candProbe) { $usbipdProbe = $candProbe }
}
$VBoxIntegrated = Test-UsbipdVBoxIntegration $usbipdProbe
if ($VBoxIntegrated) {
    Write-OK '检测到 VBoxUSBMon 集成版 usbipd——bind/attach 依赖 VBoxUSBMon/VBoxUSB 驱动，将确保其运行（绝不清理/停用/卸载）'
    # VBoxUSBMon 缺失/未运行（此前误删或异常）时后续 bind/attach 必然失败，提前恢复
    $monEarly = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
    if (-not $monEarly -or $monEarly.State -ne 'Running') {
        $null = Invoke-VBoxIntegrationRestore $usbipdProbe
    }
} else {
    Invoke-VBoxFilterCleanup
}

# ---------- 1. 定位 usbipd ----------
Write-Step '定位 usbipd'
$usbipd = (Get-Command usbipd -ErrorAction SilentlyContinue).Source
if (-not $usbipd) {
    $candidate = 'C:\Program Files\usbipd-win\usbipd.exe'
    if (Test-Path $candidate) { $usbipd = $candidate }
}
if (-not $usbipd) {
    Exit-Fail '未找到 usbipd.exe，请先安装 usbipd-win: winget install dorssel.usbipd-win'
}
Write-OK $usbipd

# ---------- 1.5 确保 usbipd 服务运行（服务被停止/杀进程后 list 无输出，需先启动） ----------
if (-not (Get-Service usbipd -ErrorAction SilentlyContinue)) {
    Exit-Fail '未找到 usbipd 服务，usbipd-win 安装可能损坏，请重装: winget install dorssel.usbipd-win'
}
if (Ensure-UsbipdService) {
    Write-OK 'usbipd 服务运行中'
} else {
    # 最后一搏：若摄像头驱动栈被 VBox 过滤驱动污染，usbipd 枚举设备时可能挂死
    #（服务一启动就崩）。卸载摄像头设备节点强制全新枚举（类过滤引用本轮已清理，
    # 新枚举的栈不再挂 VBox 过滤器），再尝试启动服务
    Write-Warn '尝试卸载摄像头设备节点（清除可能被 VBox 过滤器污染的驱动栈）后再次启动服务...'
    $null = Invoke-CameraDeviceUninstall
    if (Ensure-UsbipdService) {
        Write-OK 'usbipd 服务已恢复（摄像头重新枚举后启动成功）'
    } else {
        Exit-Fail 'usbipd 服务无法启动（上方已输出诊断：残留进程/错误码/服务配置/SCM事件/崩溃记录）。请把完整日志发回分析；仍失败则只能重启 Windows 后重跑本脚本'
    }
}

# ---------- 1.6 卸载摄像头设备模式（-UninstallCamera） ----------
if ($UninstallCamera) {
    # 不依赖 usbipd list 找设备（设备卡在错误状态/被捕获时 list 里可能看不到），
    # 直接按 PnP 实例 ID 匹配。-BusId 指定时从 usbipd list 提取该设备的 VID:PID
    $uninstallPattern = 'VID_8086&PID_0B5C|VID_8086&PID_0B5D|VID_2BC5'
    if ($BusId) {
        $bl = Get-UsbipdConnectedLines $usbipd |
            Where-Object { $_ -match ("^\s*" + [regex]::Escape($BusId) + "\s") } | Select-Object -First 1
        if ($bl -and $bl -match '([0-9a-fA-F]{4}):([0-9a-fA-F]{4})') {
            $uninstallPattern = 'VID_' + $Matches[1].ToUpper() + '&PID_' + $Matches[2].ToUpper()
        } else {
            Exit-Fail "无法从 usbipd list 解析 busid $BusId 的 VID:PID（先确认设备在列表中）"
        }
    }
    $done = Invoke-CameraDeviceUninstall -VidPattern $uninstallPattern
    if ($done) {
        # 等待重新枚举（最长 20 秒），确认设备回来了
        $t = 0; $backLine = $null
        while ($t -lt 20) {
            Start-Sleep -Seconds 5; $t += 5
            if ($BusId) {
                $backLine = Get-UsbipdConnectedLines $usbipd |
                    Where-Object { $_ -match ("^\s*" + [regex]::Escape($BusId) + "\s") } | Select-Object -First 1
            } else {
                $backLine = Find-Camera $usbipd
            }
            if ($backLine) { break }
            Write-Host "  等待设备重新枚举...（$t 秒）"
        }
        if ($backLine) {
            Write-OK "设备已重新枚举: $($backLine.Trim())"
            Write-Host '设备节点已全新安装。如需透传进容器，运行默认模式（去掉 -UninstallCamera）即可。' -ForegroundColor Cyan
        } else {
            Write-Warn '20 秒内未在 usbipd 列表看到设备——若设备管理器中设备可见且状态正常则无碍；否则设备可能已从总线掉线'
        }
    }
    Stop-Run 0
}

# ---------- 1.8 校验/自动选择 WSL 发行版 ----------
# 后续所有 wsl -d 调用（sysfs 验证、attach）都依赖发行版名正确；默认值 "ubuntu"
# 在实际机器上可能叫 "Ubuntu"/"Ubuntu-22.04"，名称不对时 wsl 静默返回非零，
# 会被误判为"设备不在 WSL"并进入错误分支，此处提前校正。
if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    Exit-Fail '未找到 wsl.exe，请确认已启用 WSL2（Docker Desktop 需 WSL2 后端）'
}
& wsl -d $WslDistro -e true | Out-Null
if ($LASTEXITCODE -ne 0) {
    # 逐个验证候选名真实可用（wsl -d 实测），防止列表输出含默认标记（*）/空字符导致选错
    $candidates = @((& wsl -l -q) | ForEach-Object { ($_ -replace "`0", '').Trim() -replace '^\*\s*', '' } |
        Where-Object { $_ -and $_ -notmatch '^docker' })
    $valid = $null
    foreach ($c in $candidates) {
        & wsl -d $c -e true | Out-Null
        if ($LASTEXITCODE -eq 0) { $valid = $c; break }
    }
    if (-not $valid) {
        Exit-Fail "未找到可用的 WSL 发行版。请先执行: wsl --install -d Ubuntu，或用 -WslDistro 指定实际发行版名（wsl -l -v 查看）"
    }
    $old = $WslDistro
    $WslDistro = $valid
    Write-Warn "指定的发行版 '$old' 不存在或不可用，自动改用 '$WslDistro'（wsl -l -v 可查看实际名称）"
}
Write-OK "WSL 发行版: $WslDistro"

# ---------- 2. 查找深度摄像头设备（Orbbec 2bc5 / Intel RealSense 8086） ----------
Write-Step '查找深度摄像头设备'
$skipPassthrough = $false
if ($BusId) {
    # 显式指定 busid：不做摄像头识别，直接定位该设备（后续所有查找/抢救逻辑因
    # 已找到设备而自然跳过）
    $deviceLine = Get-UsbipdConnectedLines $usbipd |
        Where-Object { $_ -match ("^\s*" + [regex]::Escape($BusId) + "\s") } | Select-Object -First 1
    if (-not $deviceLine) {
        Write-Host '当前 usbipd list 输出：'
        Write-Host ((& $usbipd list) -join "`n")
        Exit-Fail "指定的 busid $BusId 未找到（busid 形如 9-4，见上方列表 BUSID 列）"
    }
    Write-OK "使用指定设备（-BusId 模式）: $deviceLine"
} else {
    $deviceLine = Find-Camera $usbipd
}

if ($Detach -and $deviceLine) {
    # 取回模式：usbipd detach 把设备从 WSL 拿回 Windows（Docker 不受影响），
    # 再 unbind 解除共享（否则驱动被 usbipd stub 占用，本地应用打不开设备）
    $bid = (($deviceLine.Trim() -split '\s+')[0])
    if ($deviceLine -match 'Attached') {
        Write-Step "执行 detach --busid $bid（从 WSL 取回，Docker Desktop 不受影响）"
        & $usbipd detach --busid $bid
        if ($LASTEXITCODE -ne 0) { Write-Warn 'detach 返回非零（设备可能已不在 WSL），继续' }
        $backLine = $null; $waited = 0
        while ($waited -lt 20) {
            Start-Sleep -Seconds 5
            $waited += 5
            $backLine = Get-UsbipdConnectedLines $usbipd |
                Where-Object { $_ -match ("^\s*" + [regex]::Escape($bid) + "\s") } | Select-Object -First 1
            if ($backLine) { break }
            Write-Host "  等待设备回到 Windows 侧...（$waited 秒）"
        }
        if ($backLine) {
            Write-OK "设备已回到 Windows 侧（等待 $waited 秒）"
        } else {
            Write-Warn '20 秒内未在 usbipd 列表看到设备（若设备管理器中已可见则无碍）'
        }
    } else {
        Write-OK '设备未 attach 到 WSL（跳过 detach）'
    }
    $cur = Get-UsbipdConnectedLines $usbipd |
        Where-Object { $_ -match ("^\s*" + [regex]::Escape($bid) + "\s") } | Select-Object -First 1
    if ($cur -and $cur -match 'Shared') {
        & $usbipd unbind --busid $bid
        if ($LASTEXITCODE -eq 0) {
            Write-OK "已解除共享（unbind $bid），Windows 应用现在可以使用该设备"
        } else {
            Write-Warn "unbind 失败，可手动执行: usbipd unbind --busid $bid"
        }
    } elseif ($cur) {
        Write-OK '设备已在 Windows 侧且未共享，本地应用可直接使用'
    }
    Write-Host '如需再次透传进容器，去掉 -Detach 重新运行本脚本即可。' -ForegroundColor Cyan
    Stop-Run 0
}

if (-not $deviceLine -and -not $Detach) {
    # usbipd-win 5.3 attach 成功后设备在 list 中显示 "Attached"；若设备整个从
    # 列表消失且 WSL sysfs 也不可见，则是卡在 vhci 半挂载状态。
    # 先查 WSL 内核：若设备已透传（此前 attach 仍有效），直接跳到重启容器，
    # 绝不对其做不必要的 detach/attach 往返（幂等保护）。
    #（-Detach 取回模式下不走此分支：设备在 WSL 但列表不可见时应走下方 vhci 取回）
    Write-Host '  Windows 侧未直接找到摄像头，检查是否已 attach 在 WSL 内核...' -ForegroundColor DarkGray
    $inWsl = Test-CameraInWsl
    if ($inWsl) {
        Write-OK "深度摄像头已 attach 在 WSL 内核（$inWsl），跳过透传步骤"
        $skipPassthrough = $true
    }
}

if (-not $deviceLine -and -not $skipPassthrough -and -not $Detach) {
    # 设备可能只是未被总线轮询到，触发一次重新扫描（只读级别操作，安全）
    Write-Warn '未发现深度摄像头，触发 USB 总线重新扫描（pnputil /scan-devices）...'
    & pnputil /scan-devices | Out-Null
    Start-Sleep -Seconds 5
    $deviceLine = Find-Camera $usbipd
}

if (-not $deviceLine -and -not $skipPassthrough -and -not $Detach) {
    # 目标设备不在 Windows 侧且 WSL 里也没有 → 摄像头确实从总线消失了。
    # 进入端口级复位前先提醒：列表里若存在"非深度相机 VID 的已 bind 设备"（如用
    # -BusId 透传过的普通 USB 摄像头），用户多半是忘了带 -BusId 参数——直接复位
    # 总线既救不到它，还会让无关设备掉线。
    # 只列真正已 bind 的（"Shared"/"Shared (forced)"）；"Not shared" 含子串 shared，
    # 不能用 -match 'Shared' 匹配（曾把全部未共享设备误列进来，提示失真）
    $otherShared = @(Get-UsbipdConnectedLines $usbipd | Where-Object {
        $_ -match 'Shared' -and $_ -notmatch 'Not shared' -and $_ -notmatch '2bc5|8086' })
    if ($otherShared.Count -gt 0) {
        Write-Warn '未找到深度摄像头，但发现其他已 bind（Shared）的 USB 设备：'
        foreach ($l in $otherShared) { Write-Host "    $l" -ForegroundColor DarkGray }
        Write-Host '  若目标就是其中之一（如 -BusId 透传过的普通摄像头），请加 -BusId 参数重跑，' -ForegroundColor Yellow
        Write-Host '  例如: .\reset_orbbec_usb.ps1 -BusId 9-4' -ForegroundColor Yellow
        Write-Host '  仅当要抢救的是 Orbbec/RealSense 深度摄像头时，才继续下方的端口级复位。' -ForegroundColor Yellow
        if (-not (Get-YesNo '未找到深度摄像头，是否仍继续分级抢救（复位 USB 总线）？(y/n)' $true)) {
            Exit-Fail '未找到深度摄像头；如需操作其他设备请用 -BusId 指定（见上方列表）'
        }
    }
    $brokenLine = Find-Broken $usbipd
    if ($brokenLine) {
        # 设备枚举失败（usbipd 显示 0000:0002 "描述符请求失败" / 0000:0005 "描述符无效"，
        # Windows 设备管理器报错误代码 43）。先做设备级软复位；
        # 无效则落入下方分级抢救做端口级复位（Hub/根集线器/控制器）。
        Write-Warn "发现 USB 枚举失败设备: $brokenLine"
        $recovered = Invoke-UsbSoftReset
        if ($recovered) {
            $deviceLine = Find-Camera $usbipd
        } else {
            Write-Warn '设备级软复位无效，继续下方分级抢救（端口级复位）'
        }
    }
}

if (-not $deviceLine -and -not $skipPassthrough) {
    # 设备可能卡在 WSL vhci 半挂载状态：Windows 侧已消失、WSL sysfs 也不可见。
    # 判据：WSL 内核 USB 总线上存在真实设备（排除根集线器 usbN 和接口 xxx:yyy）
    # 却不含摄像头。注意不能用 vhci_hcd 节点是否存在判断——内核只要加载了
    # usbip 模块该节点就存在，会误报（实际总线是空的）。
    $wslRealDevs = & wsl -d $WslDistro -e sh -c "ls /sys/bus/usb/devices/ 2>/dev/null | grep -v ':' | grep -v '^usb' | head -5"
    if ($wslRealDevs) {
        # 取回后按"WSL 里实际设备的 VID"在 usbipd list 里等它回来（须在 shutdown 前
        # 抓取，之后 sysfs 就没了）。不能只按深度相机 VID 找——-BusId 透传的普通
        # 设备（如 icspring 32e6）会被永远"看不见"，导致取回明明成功却误报失败
        $wslVids = @(& wsl -d $WslDistro -e sh -c 'for d in /sys/bus/usb/devices/*; do n=$(basename $d); case $n in *:*) continue ;; usb*) continue ;; esac; [ -f $d/idVendor ] && cat $d/idVendor; done | sort -u' |
            Where-Object { $_ -match '^[0-9a-f]{4}$' })
        Write-Warn "WSL USB 总线上挂有设备（$($wslRealDevs -join ' ')）：摄像头可能卡在半挂载状态"
        if (Get-YesNo '执行 wsl --shutdown 取回设备到 Windows？(y/n，会停止 Docker Desktop；取回后自动 unbind 交还本地使用)' $false) {
            Write-Step '执行 wsl --shutdown（断开 vhci，设备将回到 Windows 侧）'
            & wsl --shutdown
            $backLine = $null; $waited = 0
            while ($waited -lt 20) {
                Start-Sleep -Seconds 5
                $waited += 5
                foreach ($v in $wslVids) {
                    $m = Get-UsbipdConnectedLines $usbipd |
                        Select-String -Pattern ("^\s*\d+-\d+\s+${v}:[0-9a-fA-F]{4}\s") | Select-Object -First 1
                    if ($m) { $backLine = $m.ToString(); break }
                }
                if ($backLine) { break }
                Write-Host "  等待设备回到 Windows 侧重新枚举...（$waited 秒）"
            }
            if ($backLine) {
                $bid = (($backLine.Trim() -split '\s+')[0])
                Write-OK "设备已回到 Windows 侧（busid=$bid，等待 $waited 秒）"
                # 取回即意味着要在 Windows 本地使用：自动解除共享（否则驱动被 usbipd
                # stub 占用，本地应用打不开设备，还得再手动 unbind 一步）
                if ($backLine -match 'Shared') {
                    & $usbipd unbind --busid $bid
                    if ($LASTEXITCODE -eq 0) {
                        Write-OK "已解除共享（unbind $bid），Windows 应用现在可以使用该设备"
                    } else {
                        Write-Warn "unbind 失败，可手动执行: usbipd unbind --busid $bid"
                    }
                } else {
                    Write-OK '设备未处于共享状态（跳过 unbind），Windows 应用可直接使用'
                }
                Write-Host '如需再次透传进容器，重新运行本脚本即可。' -ForegroundColor Cyan
                Stop-Run 0
            }
            Write-Warn 'wsl --shutdown 后 20 秒内设备未回到 Windows 侧（设备可能已从总线掉线）'
        }
    }
}

# ---------- 2.9 分级抢救（设备从总线消失且无法重启 Windows 时的端口级复位） ----------
# 原理：重启 Windows 之所以能救活卡死的 USB 设备，本质是重新初始化 USB 控制器
# 并对端口做复位——这件事可以不重启系统、直接复位控制器来完成。
# 按副作用从小到大：P1 清理（零副作用）→ P2 摄像头所在 Hub 复位 → P3 该总线
# 根集线器复位 → P4 xHCI 控制器复位（效果最接近重启）。每级后轮询等摄像头回来。
#（-Detach 取回模式不抢救：找不到设备直接报错退出）
function Test-CameraState { param($usbipdPath)
    if (Find-Camera $usbipdPath) { return 'normal' }
    if (Find-Broken $usbipdPath) { return 'broken' }
    return $null
}
function Wait-CameraBack { param($usbipdPath, $seconds)
    $t = 0
    while ($t -lt $seconds) {
        Start-Sleep -Seconds 5
        $t += 5
        $s = Test-CameraState $usbipdPath
        if ($s) { return @{ State = $s; Waited = $t } }
        Write-Host "  等待摄像头重新枚举...（$t 秒）"
    }
    return $null
}
function Handle-CameraBack { param($r)
    # 处理 Wait-CameraBack 的结果；返回 'normal'（可继续透传）或 $null（继续下一级）
    if (-not $r) { return $null }
    Write-OK "摄像头重新出现在总线上（等待 $($r.Waited) 秒，状态 $($r.State)）"
    if ($r.State -eq 'broken') {
        if (Invoke-UsbSoftReset) { return 'normal' }
        Write-Warn '设备级复位无效，继续更强的端口级复位'
            return $null
        }
        return 'normal'
}

if (-not $deviceLine -and -not $skipPassthrough -and -not $Detach) {
    Write-Warn '摄像头从总线消失（无 43 错误设备、不在 WSL）：进入分级抢救（免重启的端口级复位）'

    # --- 定位摄像头挂载点：幽灵节点/43 错误设备的父级链（幽灵节点被清除后信息即丢失，必须先取） ---
    $anchorPnp = @(Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
        $_.InstanceId -match 'VID_8086&PID_0B5C|VID_8086&PID_0B5D|VID_2BC5' })
    if ($anchorPnp.Count -eq 0) {
        $anchorPnp = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
            $_.Class -eq 'USB' -and $_.Status -eq 'Error' -and $_.InstanceId -like 'USB\VID_0000*' })
    }
    $genHubs = @(); $rootHub = $null; $controller = $null
    foreach ($n in $anchorPnp) {
        $cur = $n.InstanceId
        for ($i = 0; $i -lt 8; $i++) {
            $p = (Get-PnpDeviceProperty -InstanceId $cur -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
            if (-not $p) { break }
            if ($p -like 'PCI\*') { $controller = $p; $rootHub = $cur; break }
            $cur = $p
        }
        if ($controller) {
            $c2 = $n.InstanceId
            for ($i = 0; $i -lt 8; $i++) {
                $p = (Get-PnpDeviceProperty -InstanceId $c2 -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
                if (-not $p -or $p -eq $rootHub -or $p -like 'PCI\*') { break }
                if ($p -notmatch 'VID_8086&PID_0B5C|VID_8086&PID_0B5D|VID_2BC5') { $genHubs += $p }
                $c2 = $p
            }
            break
        }
    }
    if (-not $rootHub -and $anchorPnp.Count -eq 0) {
        # 无任何锚点（幽灵节点已被上次运行清理）：根集线器自身 BusNumber 恒为 0，
        # 须从其子设备的 busid 反推各根集线器的总线号。摄像头为 USB3 设备（插在
        # USB3 口，历史 busid 1-17 → 总线 1），总线 1 存在时直接自动选定。
        $listLines0 = Get-UsbipdConnectedLines $usbipd
        $devInfos = @()
        # 注意：不能按 Class -eq 'USB' 过滤——蓝牙/网卡/HID 类设备的总线级节点同样是
        # USB\VID_* 前缀，按类过滤会把整条总线漏掉（曾导致远程机器总线 1 上只有
        # 蓝牙+WiFi 时 busMap 为空、根集线器定位失败、P3/P4 被静默跳过）
        foreach ($d in @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
                $_.InstanceId -like 'USB\VID_*' })) {
            $vp0 = ''
            if ($d.InstanceId -match '^USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})') { $vp0 = ($Matches[1] + ':' + $Matches[2]).ToLower() }
            $devInfos += [pscustomobject]@{
                Id     = $d.InstanceId
                Parent = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
                Addr   = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Address' -ErrorAction SilentlyContinue).Data
                VidPid = $vp0
            }
        }
        $busMap = @{}
        foreach ($rh in @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'USB\ROOT_HUB*' })) {
            foreach ($di in @($devInfos | Where-Object { $_.Parent -eq $rh.InstanceId -and $_.Addr -and $_.VidPid })) {
                $bl0 = @($listLines0 | Where-Object { $_ -match "^\s*(\d+)-$($di.Addr)\s" -and $_ -match [regex]::Escape($di.VidPid) })
                if ($bl0.Count -gt 0 -and $bl0[0] -match '^\s*(\d+)-') { $busMap[$Matches[1]] = $rh.InstanceId; break }
            }
        }
        if ($busMap.ContainsKey('1')) {
            $rootHub = $busMap['1']
            $controller = (Get-PnpDeviceProperty -InstanceId $rootHub -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
            Write-OK '幽灵节点已被清理，按摄像头历史 busid（1-17，USB3 口）自动选定总线 1 的根集线器'
        } elseif ($busMap.Count -gt 0) {
            Write-Warn '未能从幽灵节点定位摄像头挂载点（幽灵记录已被清理）'
            Write-Host "  可用总线: $(($busMap.Keys | Sort-Object) -join ', ')"
            $sel = ''
            if ($Yes) {
                $sel = '1'
                Write-Host '  摄像头历史 busid 的总线号（无人值守模式自动取默认 1）' -ForegroundColor DarkGray
            } else {
                $sel = Read-Host '  请输入摄像头历史 busid 的总线号（直接回车=1）'
                Write-Host "  [输入] $sel" -ForegroundColor DarkGray
            }
            if (-not $sel) { $sel = '1' }
            if ($busMap.ContainsKey($sel)) {
                $rootHub = $busMap[$sel]
                $controller = (Get-PnpDeviceProperty -InstanceId $rootHub -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
                Write-Host "  已选定总线 $sel 的根集线器" -ForegroundColor DarkGray
            }
        }
    }
    if ($rootHub) {
        $rd = Get-PnpDevice -InstanceId $rootHub -ErrorAction SilentlyContinue
        Write-Host "  定位到根集线器: $($rd.FriendlyName) [$rootHub]" -ForegroundColor DarkGray
    }
    if ($controller) {
        $cd = Get-PnpDevice -InstanceId $controller -ErrorAction SilentlyContinue
        Write-Host "  定位到 USB 控制器: $($cd.FriendlyName) [$controller]" -ForegroundColor DarkGray
    }
    if ($genHubs.Count -gt 0) { Write-Host "  定位到中间 Hub: $($genHubs -join ' | ')" -ForegroundColor DarkGray }

    # --- 检测当前正在联网（NetConnectionStatus=2）的网卡及其 USB 挂载位置 ---
    # P3/P4 是否影响远程连接，取决于"正在用的那块网卡"是否在待复位的总线/控制器下；
    # 同总线上插着但未启用的闲置 WiFi 不算风险。USB 网卡经 PNPDeviceID 向上追溯父链定位。
    $activeNetUsb = @(); $activeNetOther = @()
    foreach ($na in @(Get-CimInstance Win32_NetworkAdapter -ErrorAction SilentlyContinue | Where-Object { $_.NetConnectionStatus -eq 2 })) {
        if (-not $na.PNPDeviceID) { continue }
        if ($na.PNPDeviceID -notlike 'USB\*') { $activeNetOther += $na.Name; continue }
        $cur = $na.PNPDeviceID; $prev = $null; $rhNet = $null; $ctrlNet = $null; $topNet = $null
        for ($i = 0; $i -lt 8; $i++) {
            $p = (Get-PnpDeviceProperty -InstanceId $cur -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
            if (-not $p) { break }
            if ($p -like 'PCI\*') { $rhNet = $cur; $ctrlNet = $p; $topNet = $prev; break }
            $prev = $cur; $cur = $p
        }
        $bidNet = $null
        if ($topNet) {
            $vpNet = ''
            if ($topNet -match '^USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})') { $vpNet = ($Matches[1] + ':' + $Matches[2]).ToLower() }
            $addrNet = (Get-PnpDeviceProperty -InstanceId $topNet -KeyName 'DEVPKEY_Device_Address' -ErrorAction SilentlyContinue).Data
            if ($vpNet -and $addrNet) {
                $blNet = @(Get-UsbipdConnectedLines $usbipd | Where-Object { $_ -match "^\s*\d+-$addrNet\s" -and $_ -match [regex]::Escape($vpNet) })
                if ($blNet.Count -gt 0 -and $blNet[0] -match '(\d+-\d+)') { $bidNet = $Matches[1] }
            }
        }
        $activeNetUsb += @{ Name = $na.Name; Busid = $bidNet; RootHub = $rhNet; Controller = $ctrlNet }
    }
    $netDetectOk = ($activeNetUsb.Count -gt 0 -or $activeNetOther.Count -gt 0)
    if ($activeNetUsb.Count -gt 0) {
        foreach ($a in $activeNetUsb) {
            $bt = if ($a.Busid) { $a.Busid } else { 'busid未知' }
            Write-Host "  当前联网 USB 网卡: $($a.Name) [$bt]" -ForegroundColor DarkGray
        }
    } elseif ($netDetectOk) {
        Write-Host "  当前联网网卡（非 USB，不受 USB 复位影响）: $($activeNetOther -join '; ')" -ForegroundColor DarkGray
    }
    $p3Declined = $false; $p4Declined = $false

    # ----- P0：VirtualBox USB 捕获检测与释放（比 Hub/控制器复位更常见的"假死"） -----
    # VirtualBox 虚拟机捕获 USB 设备后，设备在 Windows 上变成 VID_80EE&PID_CAFE
    #（VirtualBox USB）的虚拟节点，原设备从物理总线"凭空消失"——无 43 错误、
    # Hub/控制器复位全部无效（设备已不在物理总线上），事件日志也查不到掉线记录。
    # 实例号后缀即原设备序列号（如 RealSense 的 261443060470），可与摄像头序列号对照。
    # 远程机器曾出现：摄像头消失 + 错误态 VirtualBox USB 节点序列号与摄像头一致。
    # 捕获节点状态不限定（Error/OK 均算：Error=残留捕获，OK=虚拟机正在使用），
    # 80EE:CAFE 节点的存在本身即异常（它只在 VirtualBox 捕获 USB 时创建）
    $vboxNodes = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
        $_.InstanceId -like 'USB\VID_80EE&PID_CAFE*' })
    if ($vboxNodes.Count -gt 0) {
        foreach ($n in $vboxNodes) {
            $serial = ($n.InstanceId -split '\\')[-1]
            Write-Warn "发现 VirtualBox USB 捕获节点: [$($n.InstanceId)]（状态 $($n.Status)，序列号 $serial）"
        }
        if ($VBoxIntegrated) {
            Write-Host '  本机为 VBoxUSBMon 集成版 usbipd：捕获节点是 bind 接管机制的产物，不是 VirtualBox 干扰。' -ForegroundColor Yellow
            Write-Host '  节点存在但设备不在 usbipd 列表 = 接管卡在半路；确保依赖驱动在位运行，移除节点可释放设备。' -ForegroundColor Yellow
            # 集成构建：VBoxUSBMon/VBoxUSB 是 usbipd 自身的依赖件（此前可能被误删），
            # 恢复并确保运行；绝不停止/删除
            $null = Invoke-VBoxIntegrationRestore $usbipd
        } else {
            Write-Host '  摄像头很可能被 VirtualBox 的 USB 过滤驱动接管（而非固件卡死）。' -ForegroundColor Yellow
            Write-Host '  移除该节点会把设备交还 Windows 真实总线；若有虚拟机正在使用它会被强制断开。' -ForegroundColor Yellow
        }
        # 证据收集：VirtualBox 视角的主机 USB 设备与运行中的虚拟机
        # VBoxManage 可能不在默认路径（也支持从注册表 InstallDir 找），甚至 GUI 已
        # 卸载只剩驱动——此时从服务/驱动层面检测（见下方 VBoxUSBMon 检测）
        $vboxManage = @('C:\Program Files\Oracle\VirtualBox\VBoxManage.exe',
                        'C:\Program Files (x86)\Oracle\VirtualBox\VBoxManage.exe') |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $vboxManage) {
            $regDir = (Get-ItemProperty 'HKLM:\SOFTWARE\Oracle\VirtualBox' -ErrorAction SilentlyContinue).InstallDir
            if ($regDir) {
                $cand = Join-Path $regDir 'VBoxManage.exe'
                if (Test-Path $cand) { $vboxManage = $cand }
            }
        }
        if ($vboxManage) {
            try {
                $usbHostOut = & $vboxManage list usbhost 2>$null
                if ($usbHostOut) {
                    $camBlocks = ($usbHostOut -join "`n") -split '(?=UUID:)' |
                        Where-Object { $_ -match '0x8086|0x2bc5|RealSense|Femto' }
                    foreach ($b in $camBlocks) {
                        Write-Host '  VirtualBox 视角的摄像头：' -ForegroundColor DarkGray
                        ($b -split "`n" | Where-Object { $_ -match 'VendorId|ProductId|Serial|Product|Current State' }) |
                            ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
                    }
                }
                $runVms = & $vboxManage list runningvms 2>$null
                if ($runVms) {
                    Write-Warn "VirtualBox 有虚拟机正在运行: $($runVms -join '; ') —— 其 USB 过滤器可能在反复抢夺摄像头"
                }
            } catch { Write-Host '  （VBoxManage 查询失败，跳过证据收集）' -ForegroundColor DarkGray }
        }
        # VBoxUSBMon 是 VirtualBox 注册到 USB 类上的全局过滤驱动（内核态），设备
        # 释放回 Windows 总线后仍可能被它重新捕获（实测发生过：移除捕获节点后设备
        # 回到总线，attach 时被再次抢走导致 "no device with busid"）。
        # 停止策略（全自动，无需确认——捕获节点的存在已证明它在干扰透传）：
        #   1. sc stop：常规停止
        #   2. 常规停不掉（有设备引用驱动）时先移除捕获节点释放引用，再停服务
        # VirtualBox 虚拟机下次启动时会自动拉起该服务，不影响日后使用
        $vboxDrivers = @(Get-CimInstance Win32_SystemDriver -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^VBox' -and $_.State -eq 'Running' })
        if ($vboxDrivers.Count -gt 0) {
            Write-Warn "VirtualBox 驱动正在运行: $(($vboxDrivers | ForEach-Object { $_.Name }) -join ', ')"
        }
        # 非集成构建才执行停用（集成构建的 VBoxUSBMon 是 usbipd 依赖件，已在上方恢复）。
        # 删除前最后防线：运行时自证——usbipd 输出报 VBoxUsbMon 错 = 它依赖该驱动，
        # 误停/误删会直接废掉 bind/attach（远程实测教训）
        if (-not $VBoxIntegrated -and $vboxDrivers.Name -contains 'VBoxUSBMon') {
            if (Test-UsbipdRuntimeVBoxDep $usbipd) {
                $VBoxIntegrated = $true
                Write-Warn '运行时自证：usbipd 依赖 VBoxUSBMon（其输出报 VBoxUsbMon 错）——判定为集成构建，绝不停止/删除，改为恢复'
                $null = Invoke-VBoxIntegrationRestore $usbipd
            } else {
            Write-Step '停止 VBoxUSBMon 过滤驱动（根治 USB 设备被反复捕获）'
            & sc.exe stop VBoxUSBMon | Out-Null
            Start-Sleep -Seconds 2
            $still = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
            if ($still -and $still.State -eq 'Stopped') {
                Write-OK 'VBoxUSBMon 服务已停止（本次开机内不会再拦截 USB 设备）'
            } else {
                # 有设备引用时 sc stop 不生效：先移除捕获节点释放引用，再停服务
                Write-Warn "VBoxUSBMon 常规停止未生效（当前: $($still.State)），先移除捕获节点释放引用再停"
                foreach ($n in $vboxNodes) {
                    & pnputil /remove-device "$($n.InstanceId)" | Out-Null
                }
                Start-Sleep -Seconds 2
                & sc.exe stop VBoxUSBMon | Out-Null
                Start-Sleep -Seconds 2
                $still = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
                if ($still -and $still.State -eq 'Stopped') {
                    Write-OK 'VBoxUSBMon 服务已停止（移除捕获节点释放引用后成功）'
                } else {
                    # 两次停止都失败：VBoxUSBMon 被深度占用，只会反复捕获设备。
                    # 静默卸载 VirtualBox（用户已授权），这是软件层面唯一根治手段
                    Write-Warn "VBoxUSBMon 仍为 $($still.State)：驱动被深度占用，停止服务无法根治。静默卸载 VirtualBox..."
                    Invoke-VBoxUninstall
                    $u = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
                    if ($u -and $u.State -ne 'Stopped') {
                        Write-Warn '卸载后 VBoxUSBMon 仍在运行（后台卸载可能未完成）：继续尝试，attach 阶段会再次处理'
                    }
                }
            }
            }
        }
        # 移除捕获节点（自动）：节点的存在即证明 VirtualBox 在抢占设备，无需确认。
        # 循环防护：移除后设备可能立刻被再次捕获（节点重现），最多清 3 轮
        Write-Step '移除 VirtualBox USB 捕获节点'
        for ($round = 1; $round -le 3; $round++) {
            $vboxNodes = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
                $_.InstanceId -like 'USB\VID_80EE&PID_CAFE*' })
            if ($vboxNodes.Count -eq 0) { break }
            foreach ($n in $vboxNodes) {
                Write-Host "  移除捕获节点（第 $round 轮）: $($n.InstanceId)" -ForegroundColor DarkGray
                & pnputil /remove-device "$($n.InstanceId)" | Out-Null
            }
            & pnputil /scan-devices | Out-Null
            Start-Sleep -Seconds 2
        }
        if ($vboxNodes.Count -gt 0) {
            # 捕获节点反复重现：非集成构建=VBoxUSBMon 拦截未被根治，静默卸载 VirtualBox
            #（用户已授权无需确认）；集成构建=接管状态混乱（Invoke-VBoxUninstall 自动跳过）
            Invoke-VBoxUninstall
            $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
            if ($h -eq 'normal') { $deviceLine = Find-Camera $usbipd }
            if (-not $deviceLine) {
                if ($VBoxIntegrated) {
                    Write-Warn '捕获节点清理后摄像头仍未出现（接管状态混乱，继续分级抢救）'
                } else {
                    Write-Warn '卸载 VirtualBox 后摄像头仍未出现（继续分级抢救）'
                }
            }
        } else {
            $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
            if ($h -eq 'normal') { $deviceLine = Find-Camera $usbipd }
            if (-not $deviceLine) {
                Write-Warn '移除捕获节点后摄像头仍未出现：可能设备掉线（继续分级抢救）'
            }
        }
    }

    # ----- P1：重启 usbipd 服务 + 清除幽灵节点 + 总线重扫描（零副作用） -----
    # usbipd 的 stub 过滤驱动可能残留对设备的占用导致无法重新枚举，重启服务可释放
    if (-not $deviceLine) {
    Write-Step '[P1] 重启 usbipd 服务、清除摄像头幽灵节点、触发总线重扫描'
    Restart-Service usbipd -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    # Restart-Service 可能停掉服务却没拉起来（错误被吞掉），必须验证并恢复，
    # 否则后续所有 usbipd 命令（含 attach）都会报 "service is not running"
    if (-not (Ensure-UsbipdService)) {
        Exit-Fail 'P1 重启后 usbipd 服务无法恢复（已尝试 3 轮）。请手动执行: Start-Service usbipd 后重跑本脚本；仍失败则需重启 Windows'
    }
    foreach ($g in @($anchorPnp | Where-Object { $_.Status -eq 'Unknown' })) {
        Write-Host "  移除幽灵节点: $($g.InstanceId)" -ForegroundColor DarkGray
        & pnputil /remove-device "$($g.InstanceId)" | Out-Null
    }
    & pnputil /scan-devices | Out-Null
    $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
    if ($h -eq 'normal') { $deviceLine = Find-Camera $usbipd }
    }

    # ----- P2：重启摄像头所挂的中间 Hub（仅影响同 Hub 的少量设备） -----
    if (-not $deviceLine -and $genHubs.Count -gt 0) {
        $hub = $genHubs[$genHubs.Count - 1]
        $hd = Get-PnpDevice -InstanceId $hub -ErrorAction SilentlyContinue
        Write-Step "[P2] 重启摄像头所在的 USB Hub: $($hd.FriendlyName)"
        Write-Host '  同 Hub 的其他设备会短暂掉线后自动重连' -ForegroundColor DarkGray
        & pnputil /restart-device "$hub" | Out-Null
        $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
        if ($h -eq 'normal') { $deviceLine = Find-Camera $usbipd }
    }

    # ----- P3：重启摄像头所在总线的根集线器（影响该总线全部设备） -----
    if (-not $deviceLine -and $rootHub) {
        $rd = Get-PnpDevice -InstanceId $rootHub -ErrorAction SilentlyContinue
        # 根集线器自身的 BusNumber 恒为 0，须从其子设备取总线号；
        # 受影响设备 = 根集线器的直接子设备（该总线根端口上的设备/下层 Hub）
        $bus = $null; $affNames = @()
        $listLines = Get-UsbipdConnectedLines $usbipd
        foreach ($d in (Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
                $_.Class -in 'USB', 'Net', 'Bluetooth', 'Camera', 'HIDClass', 'Keyboard', 'Mouse' })) {
            $pp = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue).Data
            if ($pp -ne $rootHub) { continue }
            if ($d.Class -eq 'USB' -or $d.Class -eq 'Net') {
                # 关联 usbipd busid（端口地址+VID:PID 双重匹配），便于与 usbipd list 输出对照
                $vp = ''
                if ($d.InstanceId -match '^USB\\VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})') { $vp = ($Matches[1] + ':' + $Matches[2]).ToLower() }
                $addr = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName 'DEVPKEY_Device_Address' -ErrorAction SilentlyContinue).Data
                $bl = @($listLines | Where-Object { $vp -and $addr -and $_ -match "^\s*\d+-$addr\s" -and $_ -match [regex]::Escape($vp) })
                if ($bl.Count -gt 0) {
                    $bid = ($bl[0] -split '\s+')[0]
                    $affNames += "$bid $($d.FriendlyName)"
                    if (-not $bus -and $bid -match '^(\d+)-') { $bus = $Matches[1] }
                } else {
                    $affNames += $d.FriendlyName
                }
            }
        }
        $busTxt = if ($bus) { "（总线 $bus）" } else { '' }
        Write-Step "[P3] 重启摄像头所在总线的根集线器: $($rd.FriendlyName)$busTxt"
        if ($affNames.Count -gt 0) {
            Write-Host "  受影响设备: $(($affNames | Select-Object -Unique) -join '; ')（Hub 下游设备一并受影响）" -ForegroundColor DarkGray
        }
        # 精确风险评估：只有"当前正在联网"的网卡挂在该总线下才算风险（闲置 WiFi 不算）
        $netOnHub = @()
        if ($netDetectOk) {
            $netOnHub = @($activeNetUsb | Where-Object { ($_.RootHub -and $_.RootHub -eq $rootHub) -or ($bus -and $_.Busid -and $_.Busid -match "^$bus-") })
        }
        $netDead = $false
        $ans3 = $true
        if ($netOnHub.Count -gt 0) {
            $nn = ($netOnHub | ForEach-Object { "$($_.Name)[$($_.Busid)]" }) -join '; '
            Write-Warn "你的联网网卡就挂在该总线上: $nn —— 重置将断开远程连接 10-30 秒"
            Write-Host '  通常自动恢复（WiFi 自动重连；ToDesk/向日葵/RDP 会话保留，断开期间等待即可）' -ForegroundColor Yellow
            Write-Host '  若复位后网络 20 秒内未恢复，脚本将不再询问、自动升级到 P4 控制器复位' -ForegroundColor Yellow
            $ans3 = Get-YesNo '确认重置根集线器？(y/n)' $true
        } elseif ($netDetectOk) {
            Write-Host '  联网网卡不在该总线上：重置不影响远程连接，直接执行' -ForegroundColor DarkGray
        } else {
            $ans3 = Get-YesNo '无法确定联网网卡位置，仍要重置根集线器？(y/n)' $true
        }
        if ($ans3) {
            & pnputil /restart-device "$rootHub" | Out-Null
            $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
            if ($h -eq 'normal') { $deviceLine = Find-Camera $usbipd }
            if ($netOnHub.Count -gt 0 -and -not (Wait-NetRecovered 20)) { $netDead = $true }
        } else {
            $p3Declined = $true
        }
    }

    # ----- P4：重启 xHCI 控制器（该控制器全部 USB 断开数秒，软件手段中最接近重启） -----
    if (-not $deviceLine -and $controller) {
        $cd = Get-PnpDevice -InstanceId $controller -ErrorAction SilentlyContinue
        Write-Step "[P4] 重启 USB 控制器: $($cd.FriendlyName)"
        Write-Host '  该控制器全部 USB 设备（WiFi/蓝牙/键鼠等，含其 USB2/USB3 两条总线）断开 10-30 秒后自动重连' -ForegroundColor Yellow
        # 控制器复位同时影响其两个根集线器（总线 1 和总线 2）；判据仍是"正在联网的网卡是否在该控制器下"
        $netOnCtrl = @()
        if ($netDetectOk) {
            $netOnCtrl = @($activeNetUsb | Where-Object { $_.Controller -and $_.Controller -eq $controller })
        }
        $ans4 = $true
        if ($netDead) {
            # P3 复位后网络未恢复：远程已无法人工确认，自动升级。
            # 本机不能重启，控制器级复位是软件层面最后的恢复手段
            Write-Warn 'P3 后联网网卡未恢复：自动升级到控制器级复位（远程已无法交互，无需确认）'
        } elseif ($netOnCtrl.Count -gt 0) {
            $nn = ($netOnCtrl | ForEach-Object { "$($_.Name)[$($_.Busid)]" }) -join '; '
            Write-Warn "你的联网网卡挂在该控制器下: $nn —— 重置将断开远程连接 10-30 秒"
            Write-Host '  通常自动恢复（WiFi 自动重连；ToDesk/向日葵/RDP 会话保留，断开期间等待即可）' -ForegroundColor Yellow
            Write-Host '  极小概率网卡不复位导致失联：本机不能重启的前提下将只能现场处理，请自行权衡' -ForegroundColor Yellow
            $ans4 = Get-YesNo '确认重置控制器？(y/n)' $true
        } elseif ($netDetectOk) {
            Write-Host '  联网网卡不在该控制器下（另一控制器/内置网卡）：重置不影响远程连接，直接执行' -ForegroundColor DarkGray
        } else {
            $ans4 = Get-YesNo '无法确定联网网卡位置，仍要重置控制器？(y/n)' $true
        }
        if ($ans4) {
            & pnputil /restart-device "$controller" | Out-Null
            $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
            if ($h -eq 'normal') { $deviceLine = Find-Camera $usbipd }
            if (($netOnCtrl.Count -gt 0 -or $netDead) -and -not (Wait-NetRecovered 20)) {
                Write-Warn '控制器复位后联网网卡仍未恢复：远程访问可能已中断，软件手段已用尽（本机不能重启），需现场处理'
            }
        } else {
            $p4Declined = $true
        }
    }
}

if (-not $deviceLine -and -not $skipPassthrough) {
    if ($Detach) {
        Exit-Fail '未找到可取回的设备（既不在 usbipd 列表，也不在 WSL 内核）。请确认设备已插入，或用 -BusId 指定要取回的设备'
    }
    if ($p3Declined -or $p4Declined) {
        Write-Warn '注意：有复位级别在提示时被跳过（选了 n）——软件手段尚未全部尝试'
        Write-Host '  重新运行本脚本并在风险提示时选择 y，即可继续尝试剩余级别' -ForegroundColor Yellow
    }
    if (-not $rootHub -and -not $controller) {
        Write-Warn '根集线器/控制器未能定位：P3/P4（根集线器/控制器复位）未执行'
        Write-Host '  请把当前 usbipd list 与本行一起反馈给维护者（busMap 定位仍失败）' -ForegroundColor Yellow
    }
    Write-Err2 '分级抢救未能让摄像头回到总线'
    Write-Host '  到此软件手段已用尽。该状态为端口/固件级深度卡死，剩余选项：' -ForegroundColor Yellow
    Write-Host '    1. 等待 10-30 分钟后重新运行本脚本（部分设备固件看门狗可自行恢复，重跑成本很低）' -ForegroundColor Yellow
    Write-Host '    2. 条件允许时重启 Windows（重启会重新初始化 USB 控制器）' -ForegroundColor Yellow
    Write-Host '    3. 现场对摄像头断电重插（唯一保证有效的手段）' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '当前 usbipd list 输出：'
    Write-Host ((& $usbipd list) -join "`n")
    Exit-Fail '未找到深度摄像头，无法继续'
}

if (-not $skipPassthrough) {
    $fields = ($deviceLine -split '\s{2,}') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $busid = $fields[0]
    # 注意：Where-Object 单结果时是字符串，直接 $stateMatch[0] 会取到首字符（如 "Shared"->"S"），需 @() 包装
    $stateMatch = @($deviceLine -split '\s{2,}' | Where-Object { $_ -match 'Shared|Attached|Not shared' })
    $state = if ($stateMatch.Count -gt 0) { $stateMatch[0] } else { 'Unknown' }
    Write-OK "找到设备 busid=$busid, 当前状态: $state"
    # 目标设备实际 VID（仅 VID，不含 PID）：WSL sysfs 的 idVendor 文件只含 4 位 VID，
    # 若误取 "8086:0b5c" 整体会永远匹配失败（曾导致 attach 已成功却被误判为
    # "90 秒未完成枚举"）。识别失败时回退深度摄像头默认 VID
    $targetVids = '2bc5 8086'
    if ($deviceLine -match '\b([0-9a-fA-F]{4}):[0-9a-fA-F]{4}\b') { $targetVids = $Matches[1].ToLower() }

    # ---------- 3. bind（若未共享） ----------
    if ($state -match 'Not shared') {
        Write-Step "执行 bind --force --busid $busid"
        & $usbipd bind --force --busid $busid
        if ($LASTEXITCODE -ne 0) { Exit-Fail 'bind 失败' }
        Write-OK 'bind 完成'
    } else {
        Write-OK '设备已 bind（跳过）'
    }

    # ---------- 4. detach（若已 attach 到旧会话，先断开再重新 attach） ----------
    $connectedLines2 = Get-UsbipdConnectedLines $usbipd
    $findPat = if ($BusId) { "^\s*$([regex]::Escape($busid))\s" } else { $CameraPattern }
    $line2 = $connectedLines2 | Select-String -Pattern $findPat | Select-Object -First 1
    if ($line2 -and $line2.ToString() -match 'Attached') {
        Write-Step "执行 detach --busid $busid"
        & $usbipd detach --busid $busid
        if ($LASTEXITCODE -ne 0) { Write-Warn 'detach 失败（可能已被系统释放，继续）' }
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
        Exit-Fail "WSL 发行版 $WslDistro 启动超时/失败。请先执行: wsl --install -d Ubuntu，或用 -WslDistro 指定实际发行版名（wsl -l -v 查看）"
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
        } else {
            Write-OK '.wslconfig 未启用 mirrored 模式（NAT/默认，兼容）'
        }
    } else {
        Write-OK '未发现 .wslconfig（默认 NAT 模式，兼容）'
    }

    # ---------- 5.7 确认 Docker Desktop 为 WSL2 后端（否则透传后容器看不到设备） ----------
    $distroList = @((& wsl -l -q) | ForEach-Object { ($_ -replace "`0", '').Trim() -replace '^\*\s*', '' } | Where-Object { $_ })
    if ($distroList -notcontains 'docker-desktop') {
        Write-Warn 'WSL 发行版列表中未发现 docker-desktop，Docker Desktop 可能不是 WSL2 后端'
        Write-Host '  非 WSL2 后端时设备透传到 WSL 后，容器内不可见。请在 Docker Desktop 设置中切换为 WSL2 后端。' -ForegroundColor Yellow
    } else {
        Write-OK 'Docker Desktop 为 WSL2 后端（发现 docker-desktop 发行版）'
    }

    # ---------- 6. attach 到 WSL（usbipd-win 5.x 共享内核 vhci） ----------
    Write-Step "执行 attach --wsl $WslDistro --busid $busid"
    # attach 前最后校验服务（P0/P1 的多次服务操作后可能又掉线）
    if (-not (Ensure-UsbipdService)) {
        Exit-Fail 'attach 前 usbipd 服务未运行且无法启动。请手动执行: Start-Service usbipd 后重跑本脚本'
    }
    # 集成构建健康检查：VBoxUSBMon 缺失/未运行时 attach 必然失败（报
    # "The VBoxUsbMon driver is not correctly installed"），提前恢复。
    # 运行时自证升级：usbipd 输出报 VBoxUsbMon 错 = 依赖铁证（初始检测可能漏检）
    if (-not $VBoxIntegrated -and (Test-UsbipdRuntimeVBoxDep $usbipd)) {
        $VBoxIntegrated = $true
        Write-Warn '运行时自证：usbipd 依赖 VBoxUSBMon（其输出报 VBoxUsbMon 错）——按集成构建处理'
    }
    if ($VBoxIntegrated) {
        $monPre = Get-CimInstance Win32_SystemDriver -Filter "Name='VBoxUSBMon'" -ErrorAction SilentlyContinue
        if (-not $monPre -or $monPre.State -ne 'Running') {
            Write-Warn "VBoxUSBMon 服务缺失/未运行（当前: $($monPre.State)）——恢复后再 attach..."
            $null = Invoke-VBoxIntegrationRestore $usbipd
        }
    }
    $attached = $false
    $lastAttachErr = ''
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        # 注：usbipd 的 info/warning/error 都走 stderr。PS 5.1 中 native 命令 stderr
        # 直接重定向（2>$null）会在 $ErrorActionPreference='Stop' 下抛 NativeCommandError
        # 终止脚本；临时降为 Continue 再 2>&1 捕获，既保留输出做诊断又不中断。
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $attachOut = & $usbipd attach --wsl $WslDistro --busid $busid 2>&1
        } finally { $ErrorActionPreference = $prevEap }
        $attachExit = $LASTEXITCODE
        if ($attachOut) { $attachOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } }
        $errText = ($attachOut | ForEach-Object { "$_" }) -join ' '

        if ($attachExit -ne 0) {
            $lastAttachErr = $errText
            if ($errText -match 'VBoxUsbMon driver is not correctly installed') {
                # 集成版 usbipd 的依赖驱动缺失（此前 VirtualBox 清理误删所致）：
                # 恢复 VBoxUSBMon/VBoxUSB 服务后重试 attach。报此错即依赖铁证，
                # 同步升级判定，避免后续分支再走"停 VBoxUSBMon"的错误路径
                $VBoxIntegrated = $true
                Write-Warn 'attach 失败：VBoxUSBMon 驱动未正确安装（集成版 usbipd 的依赖件被误删）——恢复驱动服务...'
                if (Invoke-VBoxIntegrationRestore $usbipd) {
                    Write-OK '依赖驱动已恢复，重试 attach'
                    continue
                }
                Exit-Fail 'VBoxUSBMon 驱动恢复失败。请重装 usbipd-win（该集成构建的安装包）后重跑本脚本'
            }
            if ($errText -match 'Device in error state') {
                # 设备处于 usbipd stub 驱动无法接管的错误态（常见于刚从 VirtualBox
                # 捕获中释放、或经历半挂载）。第 1 次轻量处理：unbind 卸掉 stub
                # 驱动绑定再强制 re-bind 让 stub 全新接管；仍失败则升级为完整的
                # 设备卸载重装（Invoke-CameraDeviceUninstall，软件级拔插）
                if ($attempt -eq 1) {
                    Write-Warn "attach 失败：设备处于错误状态（第 $attempt/3 次），unbind + 重新 bind 让 stub 驱动全新接管..."
                    $prevEap2 = $ErrorActionPreference
                    $ErrorActionPreference = 'Continue'
                    try {
                        $unbindOut = & $usbipd unbind --busid $busid 2>&1
                        if ($unbindOut) { $unbindOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } }
                        Start-Sleep -Seconds 3
                        # unbind 后设备可能短暂从 usbipd list 消失（重新枚举），等它回来；
                        # 期间 bind 会报 "no device with busid"，属正常时序而非失败
                        $t = 0
                        while ($t -lt 20) {
                            if (Get-UsbipdConnectedLines $usbipd | Where-Object { $_ -match ("^\s*" + [regex]::Escape($busid) + "\s") }) { break }
                            Start-Sleep -Seconds 5; $t += 5
                            Write-Host "  等待设备回到 usbipd 列表...（$t 秒）"
                        }
                        $bindOut = & $usbipd bind --force --busid $busid 2>&1
                        if ($bindOut) { $bindOut | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray } }
                        $bindExit = $LASTEXITCODE
                    } finally { $ErrorActionPreference = $prevEap2 }
                    if ($bindExit -ne 0) {
                        Write-Warn "re-bind 失败，等待 5 秒后直接重试 attach"
                        Start-Sleep -Seconds 5
                    } else {
                        Write-OK '已重新 bind（stub 驱动全新接管）'
                        Start-Sleep -Seconds 2
                    }
                    continue
                }
                # 第 2 次起：unbind/rebind 无效，说明错误态在设备节点本身——
                # 卸载设备节点 + 重扫描（软件级拔插），等设备全新枚举后再 attach
                Write-Warn "attach 失败：设备仍处于错误状态（第 $attempt/3 次），卸载设备节点重装（软件级拔插）..."
                $camVp = ''
                if ($deviceLine -match "([0-9a-fA-F]{4}):([0-9a-fA-F]{4})") {
                    $camVp = 'VID_' + $Matches[1].ToUpper() + '&PID_' + $Matches[2].ToUpper()
                }
                $uDone = Invoke-CameraDeviceUninstall -VidPattern $camVp
                if ($uDone) {
                    $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
                    if ($h -eq 'normal') {
                        $deviceLine = Find-Camera $usbipd
                        if ($deviceLine) {
                            $busid = (($deviceLine.Trim() -split '\s+')[0])
                            # 卸载重装后 bind 持久化可能未命中，补一次 bind 再 attach
                            if ($deviceLine -match 'Not shared') {
                                Write-Host "  设备重新枚举后未共享，补 bind: $busid" -ForegroundColor DarkGray
                                $prevEap3 = $ErrorActionPreference
                                $ErrorActionPreference = 'Continue'
                                try { & $usbipd bind --force --busid $busid 2>&1 | Out-Null } finally { $ErrorActionPreference = $prevEap3 }
                            }
                            Write-OK "设备已全新枚举（busid=$busid），继续 attach"
                            continue
                        }
                    }
                    Write-Warn '卸载后设备未在 20 秒内回到总线，5 秒后重试 attach'
                    Start-Sleep -Seconds 5
                } else {
                    Start-Sleep -Seconds 5
                }
                continue
            }
            if ($errText -match 'no device with busid') {
                # 设备从 usbipd 列表消失：要么被 VirtualBox 过滤驱动再次捕获（VID_80EE
                # 捕获节点重现），要么从总线掉线。检测区分并给针对性指引
                Write-Warn "attach 失败：设备已从 usbipd 列表消失（第 $attempt/3 次）"
                $vboxAgain = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
                    $_.InstanceId -like 'USB\VID_80EE&PID_CAFE*' })
                if ($vboxAgain.Count -gt 0) {
                    if ($VBoxIntegrated) {
                        # 集成构建：捕获节点重现 = stub 接管状态混乱，绝不能停 VBoxUSBMon
                        #（usbipd 依赖件），只移除节点释放设备
                        Write-Warn '设备再次进入 stub 接管（捕获节点重现，接管状态混乱）：移除节点 → 等设备回总线 → 重试 attach'
                    } else {
                        Write-Warn "设备再次被 VirtualBox 捕获（捕获节点重现）：停 VBoxUSBMon → 移除节点 → 重试 attach"
                        & sc.exe stop VBoxUSBMon | Out-Null
                        Start-Sleep -Seconds 2
                    }
                    foreach ($n in $vboxAgain) {
                        Write-Host "  移除捕获节点: $($n.InstanceId)" -ForegroundColor DarkGray
                        & pnputil /remove-device "$($n.InstanceId)" | Out-Null
                    }
                    & pnputil /scan-devices | Out-Null
                    $h = Handle-CameraBack (Wait-CameraBack $usbipd 20)
                    if ($h -eq 'normal') {
                        $deviceLine = Find-Camera $usbipd
                        if ($deviceLine) {
                            $busid = (($deviceLine.Trim() -split '\s+')[0])
                            # 节点移除重扫后 bind 持久化可能未命中，补一次 bind 再 attach
                            if ($deviceLine -match 'Not shared') {
                                Write-Host "  设备回到总线后未共享，补 bind: $busid" -ForegroundColor DarkGray
                                $prevEap4 = $ErrorActionPreference
                                $ErrorActionPreference = 'Continue'
                                try { & $usbipd bind --force --busid $busid 2>&1 | Out-Null } finally { $ErrorActionPreference = $prevEap4 }
                            }
                            Write-OK "设备已回到总线（busid=$busid），继续 attach"
                            continue
                        }
                    }
                    $vboxYet = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object {
                        $_.InstanceId -like 'USB\VID_80EE&PID_CAFE*' })
                    if ($vboxYet.Count -gt 0) {
                        if ($VBoxIntegrated) {
                            # 集成构建：接管状态混乱反复重现。再清一轮节点 + 摄像头设备
                            # 卸载重装（软件级拔插）打破循环（绝不停/删 VBoxUSBMon）
                            Write-Warn '捕获节点反复重现（stub 接管状态混乱）：再清一轮节点 + 卸载摄像头设备节点（软件级拔插）...'
                            foreach ($n2 in $vboxYet) { & pnputil /remove-device "$($n2.InstanceId)" | Out-Null }
                            $camVp2 = ''
                            if ($deviceLine -match "([0-9a-fA-F]{4}):([0-9a-fA-F]{4})") {
                                $camVp2 = 'VID_' + $Matches[1].ToUpper() + '&PID_' + $Matches[2].ToUpper()
                            }
                            $null = Invoke-CameraDeviceUninstall -VidPattern $camVp2
                        } else {
                            # 拦截未被根治：静默卸载 VirtualBox（用户已授权），随后设备应能真正回到总线
                            Write-Warn '捕获节点反复重现（VBoxUSBMon 拦截未被根治），静默卸载 VirtualBox...'
                            Invoke-VBoxUninstall
                        }
                        $h2 = Handle-CameraBack (Wait-CameraBack $usbipd 20)
                        if ($h2 -eq 'normal') {
                            $deviceLine = Find-Camera $usbipd
                            if ($deviceLine) {
                                $busid = (($deviceLine.Trim() -split '\s+')[0])
                                Write-OK "卸载后设备已回到总线（busid=$busid），继续 attach"
                                continue
                            }
                        }
                        Exit-Fail '卸载 VirtualBox 后设备仍未回到总线，重新运行本脚本走分级抢救'
                    }
                    Exit-Fail '设备从总线掉线（非 VirtualBox 捕获），重新运行本脚本走分级抢救'
                } else {
                    Write-Host '  设备未再被 VirtualBox 捕获，应为从总线掉线（可能固件半死）' -ForegroundColor Yellow
                    Write-Host '  处理：重新运行本脚本走分级抢救（P0 释放/P1 清理/P3/P4 端口级复位）' -ForegroundColor Yellow
                    Exit-Fail "attach 失败（设备已离开 usbipd 列表），按上述提示处理后重跑脚本（设备无损失）"
                }
            }
            if ($errText -match 'service is currently not running') {
                # usbipd 服务挂了（不是设备问题）：启动服务后立即重试，不空耗重试次数
                Write-Warn "attach 失败：usbipd 服务未运行（第 $attempt/3 次），尝试恢复服务..."
                if (Ensure-UsbipdService) {
                    Write-OK '服务已恢复，重试 attach'
                    continue
                }
                Exit-Fail 'usbipd 服务无法启动（已尝试 3 轮）。请手动执行: Start-Service usbipd；仍失败则需重启 Windows 后重跑本脚本（设备状态无损失）'
            }
            # attach 命令本身失败：设备仍留在 Windows 侧，重试不会折腾设备，安全
            Write-Warn "attach 命令失败（退出码 $attachExit，第 $attempt/3 次），5 秒后重试..."
            Start-Sleep -Seconds 5
            continue
        }

        # attach 命令成功：设备已从 Windows 移入 WSL 共享内核 vhci。
        # 设备挂到 vhci 后固件要重新初始化，在 WSL sysfs 中出现可能要几秒到几十秒
        #（本机实测 icspring 约 5 秒）。旧版只等 3 秒就判定失败并 detach 重试，把正在
        # 枚举的设备硬拉回 Windows 极易让固件彻底卡死（远程无法物理插拔，不可逆）。
        # 轮询最长 20 秒，期间绝不做 detach。
        Write-Host '  attach 命令已受理，等待设备在 WSL 内核完成枚举（最长 20 秒）...'
        $waited = 0
        while ($waited -lt 20) {
            Start-Sleep -Seconds 3
            $waited += 3
            $wslVid = Test-CameraInWsl $targetVids
            if ($wslVid) {
                $attached = $true
                Write-OK "设备已在 WSL 内核可见（$wslVid，等待 $waited 秒）"
                break
            }
            Write-Host "  等待设备枚举完成...（$waited 秒）"
        }
        if ($attached) { break }

        # attach 命令成功但设备长时间未在 WSL 出现：设备挂在 vhci 上但枚举失败。
        # 保留现场退出，不再 detach/重试（继续折腾可能让设备彻底卡死）。
        Write-Err2 "设备已提交给 WSL 但 ${waited} 秒内未完成枚举（固件可能卡死在半枚举状态）"
        Write-Host '  当前 usbipd list（判断设备去向）：' -ForegroundColor Yellow
        Write-Host ((& $usbipd list) -join "`n")
        Write-Host '  WSL 侧 USB 设备：' -ForegroundColor Yellow
        $wslDevs = & wsl -d $WslDistro -e sh -c "ls /sys/bus/usb/devices/ 2>/dev/null"
        Write-Host ($wslDevs -join ' ')
        Write-Host '  恢复路径（按代价从小到大，依次尝试）：' -ForegroundColor Yellow
        Write-Host '    1. 直接重新运行本脚本（若设备卡在 vhci，脚本会提示是否 wsl --shutdown 取回）' -ForegroundColor Yellow
        Write-Host '    2. 重启 Windows（USB 总线断电复位，对固件卡死最有效的软手段），然后重新运行本脚本' -ForegroundColor Yellow
        Exit-Fail 'attach 后设备未在 WSL 内核完成枚举，保留现场退出'
    }
    if (-not $attached) {
        Write-Err2 'attach 命令连续 3 次失败（设备仍留在 Windows 侧，状态未受影响）'
        if ($lastAttachErr) { Write-Host "  最后一次错误: $lastAttachErr" -ForegroundColor Yellow }
        # 动态判因：服务未运行时它就是根因（其他 4 条都是误导）
        $svcNow = Get-Service usbipd -ErrorAction SilentlyContinue
        if ($svcNow -and $svcNow.Status -ne 'Running') {
            Write-Host "  根因：usbipd 服务当前为 $($svcNow.Status)——请先执行 Start-Service usbipd（或重启 Windows）后重跑本脚本" -ForegroundColor Yellow
        } elseif ($VBoxIntegrated -and $lastAttachErr -match 'VBoxUsbMon') {
            Write-Host '  根因：VBoxUSBMon 驱动未正确安装（集成版 usbipd 的依赖件）——重跑本脚本会自动恢复；仍失败则重装 usbipd-win' -ForegroundColor Yellow
        } else {
            Write-Host '  常见原因：' -ForegroundColor Yellow
            Write-Host '    1. Docker Desktop 未运行（WSL2 VM 不在线）' -ForegroundColor Yellow
            Write-Host '    2. .wslconfig 启用了 networkingMode=mirrored（见上方检查结果）' -ForegroundColor Yellow
            Write-Host '    3. WSL 内核缺少 vhci 支持（执行 wsl --update 更新后重试）' -ForegroundColor Yellow
            Write-Host '    4. 设备处于错误状态（重跑本脚本，错误态处理链会自动升级：rebind → 设备卸载重装）' -ForegroundColor Yellow
        }
        Exit-Fail 'attach 连续失败，修复上述问题后重新运行本脚本即可（设备无损失）'
    }
} # end if (-not $skipPassthrough)

# ---------- 7. 重启 backend 容器（容器内 ensure_usb_nodes.py 会自动创建设备节点） ----------
if ($SkipContainerRestart) {
    # 无人值守（usb_agent 触发）：跳过容器重启与容器内验证。任务以 SYSTEM 运行，
    # docker CLI 未必可用；且重启容器会打断用户正在使用的 Web 界面。设备节点由
    # 前端完成提示后的 /orbbec/status 轮询触发 ensure_usb_nodes 自动修复。
    Write-Step '透传完成（跳过容器重启：设备节点由后续状态检测自动修复）'
    Write-Host ''
    Write-Host '设备已透传给容器，稍后在视频采集弹窗中重新检测深度相机即可使用。' -ForegroundColor Green
    Stop-Run 0
}
Write-Step '重启 backend 容器'
Start-Sleep -Seconds 3   # 等设备在 WSL 内完成枚举
# 若走了 wsl --shutdown 取回路径，Docker Desktop 需要时间自动重启 WSL VM 和引擎，
# 轮询等待引擎就绪（最长 120 秒）。stderr 重定向交给 cmd 处理，避免 PS 5.1 的
# NativeCommandError（EAP=Stop 下重定向 native stderr 会抛异常终止脚本）。
$dockerReady = $false
for ($i = 0; $i -lt 24; $i++) {
    & cmd /c "docker version --format ok >nul 2>nul"
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    Write-Host "  等待 Docker 引擎就绪...（$($i * 5) 秒）"
    Start-Sleep -Seconds 5
}
if (-not $dockerReady) {
    Exit-Fail 'Docker 引擎未就绪（Docker Desktop 可能未启动或仍在重启中）。请启动/等待 Docker Desktop 完全就绪后重新运行本脚本'
}
& docker compose restart backend
if ($LASTEXITCODE -ne 0) {
    Exit-Fail 'docker compose restart 失败，请确认 Docker Desktop 正在运行且本目录有 docker-compose.yml'
}
Write-OK 'backend 容器已重启'
Start-Sleep -Seconds 6

# ---------- 8. 容器内验证 ----------
Write-Step '容器内验证设备'
# 用 sysfs 按目标设备实际 VID 检查（Orbbec 2bc5 / RealSense 8086，与后端
# ensure_usb_nodes 的检测逻辑一致；-BusId 模式下为指定设备的 VID）。
# 容器启动链（ensure_usb_nodes.py）可能需要几秒，轮询最多 30 秒
if (-not $targetVids) { $targetVids = '2bc5 8086' }
$probeGrep = (@($targetVids -split '\s+' | Where-Object { $_ }) | ForEach-Object { "-e $_" }) -join ' '
$probeOk = $false
for ($i = 0; $i -lt 10; $i++) {
    $probe = & docker compose exec -T backend sh -c "grep -l $probeGrep /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
    if ($probe) { $probeOk = $true; break }
    Start-Sleep -Seconds 3
}
if ($probeOk) {
    Write-OK "深度摄像头已恢复（容器内 sysfs 检测到设备）"
    Write-Host ''
    Write-Host '现在可以刷新页面，在视频采集弹窗中使用深度相机（Orbbec / RealSense）了。' -ForegroundColor Green
    Stop-Run 0
} else {
    Write-Err2 '容器内未检测到设备（WSL 侧已可见但容器内缺失），请手动检查：'
    Write-Host '  docker compose exec backend sh -c "ls /sys/bus/usb/devices/*/idVendor"' -ForegroundColor Yellow
    Write-Host '  docker compose logs -n 50 backend' -ForegroundColor Yellow
    Write-Host '  docker compose down backend && docker compose up -d backend   # 完全重建容器后重试' -ForegroundColor Yellow
    Exit-Fail '容器内未检测到设备'
}
