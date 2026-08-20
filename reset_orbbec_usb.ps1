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
.NOTES
    前置条件：
      - 已安装 Docker Desktop（WSL2 后端）
      - 已安装 usbipd-win（5.x 推荐）：winget install dorssel.usbipd-win
      - WSL2 已启用（无需特定 Ubuntu 发行版；5.x 通过共享内核 vhci 透传）
      - 摄像头已通过 USB 3.0 连接（Orbbec 指示灯稳定白色；RealSense 通电后正面蓝色灯）
    需要管理员权限（usbipd bind/attach 要求管理员），脚本会自动请求提权。
    用法（项目根目录，PowerShell）：
      ./reset_orbbec_usb.ps1
      ./reset_orbbec_usb.ps1 -WslDistro Ubuntu-22.04   # 指定发行版（默认自动选择）
#>

param(
    # attach 使用的 WSL 发行版名（usbipd-win 5.x 下非必需，保留兼容旧版/多发行版场景）
    # 若指定名称不存在，脚本会自动改用机器上第一个非 docker 发行版
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

# 摄像头正常态特征（Orbbec 2bc5 / Intel RealSense 8086）
$CameraPattern = '2bc5:066b|Orbbec Femto Bolt|8086:0b5c|8086:0b5d|RealSense'
# USB 枚举失败特征（错误代码 43：描述符请求失败/无效，VID:PID 显示为 0000:xxxx）
$BrokenPattern  = '0000:0002|0000:0005|0000:0006|描述符请求失败|描述符无效|配置描述符|未知 USB 设备'

function Get-UsbipdConnectedLines { param($usbipdPath)
    # 只取 usbipd list 的 Connected 段：Persisted 段的 GUID 不是 busid，误取会导致 attach 报错
    $list = & $usbipdPath list
    $lines = @()
    $inConnected = $false
    foreach ($l in $list) {
        if ($l -match '^Connected:') { $inConnected = $true; continue }
        if ($l -match '^Persisted:') { $inConnected = $false }
        if ($inConnected -and $l -match '\S') { $lines += $l }
    }
    return ,$lines
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

function Test-CameraInWsl {
    # WSL 共享内核 sysfs 是否可见深度摄像头（VID 2bc5/8086）。
    # usbipd-win 5.x attach 后 Windows 侧 list 不再显示 Attached（设备从列表消失），
    # 唯一可靠判据是 WSL 内核 sysfs。返回设备 sysfs 路径（$null 表示不可见；
    # wsl/发行版不可用时同样返回 $null，由调用方结合上下文判断）。
    $r = & wsl -d $WslDistro -e sh -c "grep -l -e 2bc5 -e 8086 /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
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
    for ($i = 0; $i -lt 12; $i++) {
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
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 5
        if (Find-Camera $usbipd) { Write-OK "设备已恢复枚举（等待 $((($i + 1) * 5)) 秒）"; return $true }
        Write-Host "  等待设备重新枚举...（$((($i + 1) * 5)) 秒）"
    }
    return $false
}

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

# ---------- 1.8 校验/自动选择 WSL 发行版 ----------
# 后续所有 wsl -d 调用（sysfs 验证、attach）都依赖发行版名正确；默认值 "ubuntu"
# 在实际机器上可能叫 "Ubuntu"/"Ubuntu-22.04"，名称不对时 wsl 静默返回非零，
# 会被误判为"设备不在 WSL"并进入错误分支，此处提前校正。
if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
    Write-Err2 '未找到 wsl.exe，请确认已启用 WSL2（Docker Desktop 需 WSL2 后端）'
    exit 1
}
& wsl -d $WslDistro -e true | Out-Null
if ($LASTEXITCODE -ne 0) {
    # 逐个验证候选名真实可用（wsl -d 实测），防止列表输出含本地化标记/前缀导致选错
    $candidates = @((& wsl -l -q) | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ -and $_ -notmatch '^docker' })
    $valid = $null
    foreach ($c in $candidates) {
        & wsl -d $c -e true | Out-Null
        if ($LASTEXITCODE -eq 0) { $valid = $c; break }
    }
    if (-not $valid) {
        Write-Err2 "未找到可用的 WSL 发行版。请先执行: wsl --install -d Ubuntu，或用 -WslDistro 指定实际发行版名（wsl -l -v 查看）"
        exit 1
    }
    $old = $WslDistro
    $WslDistro = $valid
    Write-Warn "指定的发行版 '$old' 不存在或不可用，自动改用 '$WslDistro'（wsl -l -v 可查看实际名称）"
}
Write-OK "WSL 发行版: $WslDistro"

# ---------- 2. 查找深度摄像头（Orbbec 2bc5 / Intel RealSense 8086） ----------
Write-Step '查找深度摄像头设备'
$deviceLine = Find-Camera $usbipd
$skipPassthrough = $false

if (-not $deviceLine) {
    # usbipd-win 5.x attach 成功后设备会从 Windows 侧 usbipd list 消失。
    # 先查 WSL 内核：若设备已透传（此前 attach 仍有效），直接跳到重启容器，
    # 绝不对其做不必要的 detach/attach 往返（幂等保护）。
    Write-Host '  Windows 侧未直接找到摄像头，检查是否已 attach 在 WSL 内核...' -ForegroundColor DarkGray
    $inWsl = Test-CameraInWsl
    if ($inWsl) {
        Write-OK "深度摄像头已 attach 在 WSL 内核（$inWsl），跳过透传步骤"
        $skipPassthrough = $true
    }
}

if (-not $deviceLine -and -not $skipPassthrough) {
    # 设备可能只是未被总线轮询到，触发一次重新扫描（只读级别操作，安全）
    Write-Warn '未发现深度摄像头，触发 USB 总线重新扫描（pnputil /scan-devices）...'
    & pnputil /scan-devices | Out-Null
    Start-Sleep -Seconds 5
    $deviceLine = Find-Camera $usbipd
}

if (-not $deviceLine -and -not $skipPassthrough) {
    $brokenLine = Find-Broken $usbipd
    if ($brokenLine) {
        # 设备枚举失败（usbipd 显示 0000:0002 "描述符请求失败" / 0000:0005 "描述符无效"，
        # Windows 设备管理器报错误代码 43）。远程无法物理插拔，先软件复位；
        # 复位成功设备会以正常 VID 重新出现，复位失败则只剩重启机器一条路。
        Write-Warn "发现 USB 枚举失败设备: $brokenLine"
        $recovered = Invoke-UsbSoftReset
        if ($recovered) {
            $deviceLine = Find-Camera $usbipd
        } else {
            Write-Err2 '软复位未能让摄像头恢复枚举（固件深度卡死）'
            Write-Host '  远程场景最后的恢复手段：重启 Windows（USB 总线断电复位）后再运行本脚本' -ForegroundColor Yellow
            exit 1
        }
    }
}

if (-not $deviceLine -and -not $skipPassthrough) {
    Write-Err2 '未找到深度摄像头（Orbbec Femto Bolt / Intel RealSense），请确认：'
    Write-Host '  1. 摄像头 USB 已插好、供电正常（Orbbec 指示灯稳定白色；RealSense 通电后正面蓝色灯）' -ForegroundColor Yellow
    Write-Host '  2. Windows 设备管理器中能看到对应摄像头设备' -ForegroundColor Yellow
    Write-Host '  3. 若之前运行过本脚本且 attach 过：设备可能挂在 WSL vhci 上进退不得，' -ForegroundColor Yellow
    Write-Host '     可执行 wsl --shutdown 断开 vhci 让设备回到 Windows（会停止 Docker Desktop，需重启 Docker），再运行本脚本' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '当前 usbipd list 输出：'
    Write-Host ((& $usbipd list) -join "`n")
    exit 1
}

if (-not $skipPassthrough) {
    $fields = ($deviceLine -split '\s{2,}') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    $busid = $fields[0]
    # 注意：Where-Object 单结果时是字符串，直接 $stateMatch[0] 会取到首字符（如 "Shared"->"S"），需 @() 包装
    $stateMatch = @($deviceLine -split '\s{2,}' | Where-Object { $_ -match 'Shared|Attached|Not shared' })
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

    # ---------- 4. detach（若已 attach 到旧会话，先断开再重新 attach） ----------
    $connectedLines2 = Get-UsbipdConnectedLines $usbipd
    $line2 = $connectedLines2 | Select-String -Pattern $CameraPattern | Select-Object -First 1
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
        } else {
            Write-OK '.wslconfig 未启用 mirrored 模式（NAT/默认，兼容）'
        }
    } else {
        Write-OK '未发现 .wslconfig（默认 NAT 模式，兼容）'
    }

    # ---------- 5.7 确认 Docker Desktop 为 WSL2 后端（否则透传后容器看不到设备） ----------
    $distroList = @((& wsl -l -q) | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
    if ($distroList -notcontains 'docker-desktop') {
        Write-Warn 'WSL 发行版列表中未发现 docker-desktop，Docker Desktop 可能不是 WSL2 后端'
        Write-Host '  非 WSL2 后端时设备透传到 WSL 后，容器内不可见。请在 Docker Desktop 设置中切换为 WSL2 后端。' -ForegroundColor Yellow
    } else {
        Write-OK 'Docker Desktop 为 WSL2 后端（发现 docker-desktop 发行版）'
    }

    # ---------- 6. attach 到 WSL（usbipd-win 5.x 共享内核 vhci） ----------
    Write-Step "执行 attach --wsl $WslDistro --busid $busid"
    $attached = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        # 注：usbipd 的 info/warning 走 stderr。PS 5.1 中 native 命令 stderr 一旦重定向
        # （2>$null）就会在 $ErrorActionPreference='Stop' 下抛 NativeCommandError 终止脚本，
        # 因此这里不做 stderr 重定向，让 info 直接显示（如 5.x 的 "Selecting a specific
        # distribution is no longer required"），$LASTEXITCODE 判定不受影响。
        & $usbipd attach --wsl $WslDistro --busid $busid
        $attachExit = $LASTEXITCODE

        if ($attachExit -ne 0) {
            # attach 命令本身失败：设备仍留在 Windows 侧，重试不会折腾设备，安全
            Write-Warn "attach 命令失败（退出码 $attachExit，第 $attempt/3 次），5 秒后重试..."
            Start-Sleep -Seconds 5
            continue
        }

        # attach 命令成功：设备已从 Windows 移入 WSL 共享内核 vhci。
        # 深度摄像头是 USB 3.0 设备，挂到 vhci 后固件要重新初始化，在 WSL sysfs
        # 中出现可能需要 10 秒以上。旧版只等 3 秒就判定失败并 detach 重试，把正在
        # 枚举的设备硬拉回 Windows 极易让固件彻底卡死（远程无法物理插拔，不可逆）。
        # 这里改为最长轮询 90 秒，期间绝不做 detach。
        Write-Host '  attach 命令已受理，等待设备在 WSL 内核完成枚举（最长 90 秒）...'
        $waited = 0
        while ($waited -lt 90) {
            Start-Sleep -Seconds 3
            $waited += 3
            $wslVid = Test-CameraInWsl
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
        Write-Host '    1. 直接重新运行本脚本（若 usbipd list 已能看到设备则会走正常透传）' -ForegroundColor Yellow
        Write-Host '    2. 执行 wsl --shutdown 断开 vhci 让设备回到 Windows（Docker Desktop 会停止，' -ForegroundColor Yellow
        Write-Host '       需重启 Docker Desktop），然后重新运行本脚本' -ForegroundColor Yellow
        Write-Host '    3. 重启 Windows（USB 总线断电复位，对固件卡死最有效的软手段），然后重新运行本脚本' -ForegroundColor Yellow
        exit 1
    }
    if (-not $attached) {
        Write-Err2 'attach 命令连续 3 次失败（设备仍留在 Windows 侧，状态未受影响）'
        Write-Host '  常见原因：' -ForegroundColor Yellow
        Write-Host '    1. Docker Desktop 未运行（WSL2 VM 不在线）' -ForegroundColor Yellow
        Write-Host '    2. .wslconfig 启用了 networkingMode=mirrored（见上方检查结果）' -ForegroundColor Yellow
        Write-Host '    3. WSL 内核缺少 vhci 支持（执行 wsl --update 更新后重试）' -ForegroundColor Yellow
        Write-Host '  修复后重新运行本脚本即可，设备仍在 Windows 侧，没有损失。' -ForegroundColor Yellow
        exit 1
    }
} # end if (-not $skipPassthrough)

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
# 容器启动链（ensure_usb_nodes.py）可能需要几秒，轮询最多 30 秒
$probeOk = $false
for ($i = 0; $i -lt 10; $i++) {
    $probe = & docker compose exec -T backend sh -c "grep -l -e 2bc5 -e 8086 /sys/bus/usb/devices/*/idVendor 2>/dev/null | head -1"
    if ($probe) { $probeOk = $true; break }
    Start-Sleep -Seconds 3
}
if ($probeOk) {
    Write-OK "深度摄像头已恢复（容器内 sysfs 检测到设备）"
    Write-Host ''
    Write-Host '现在可以刷新页面，在视频采集弹窗中使用深度相机（Orbbec / RealSense）了。' -ForegroundColor Green
} else {
    Write-Err2 '容器内未检测到设备（WSL 侧已可见但容器内缺失），请手动检查：'
    Write-Host '  docker compose exec backend sh -c "ls /sys/bus/usb/devices/*/idVendor"' -ForegroundColor Yellow
    Write-Host '  docker compose logs -n 50 backend' -ForegroundColor Yellow
    Write-Host '  docker compose down backend && docker compose up -d backend   # 完全重建容器后重试' -ForegroundColor Yellow
}
