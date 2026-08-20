<#
.SYNOPSIS
    安装宿主机 USB 透传代理（视频采集弹窗"透传深度相机"按钮的宿主机侧组件）
.DESCRIPTION
    一键安装（可重复运行，幂等）：
      1. 生成令牌写入 usb_agent_config.json（已存在则沿用，-Force 重新生成）
      2. 把 HOST_AGENT_URL / HOST_AGENT_TOKEN 写入 back/.env（后端容器读取）
      3. 注册 URL ACL（netsh http）与防火墙入站规则（仅私网网段可访问）
      4. 注册计划任务 DataMaUsbAgent（SYSTEM 账户，开机自启，隐藏运行 usb_agent.ps1）
      5. 立即启动代理并探活验证
      6. 重建 backend 容器加载新环境变量（docker compose up -d backend）
    安装完成后，视频采集弹窗中"透传深度相机"按钮即可远程触发
    reset_orbbec_usb.ps1（无人值守模式）。
.NOTES
    需管理员权限运行（会自动提权）。
    卸载：schtasks /Delete /TN DataMaUsbAgent /F + netsh http delete urlacl url=http://+:8765/
          + Remove-NetFirewallRule -DisplayName 'DataMa USB Agent'
#>
param(
    [int]$Port = 8765,
    # 重新生成令牌（旧令牌作废，需配合 back/.env 同步更新后重建 backend 容器）
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::InputEncoding  = [System.Text.Encoding]::UTF8
} catch { }

function Write-Step { param($msg) Write-Host "`n[步骤] $msg" -ForegroundColor Cyan }
function Write-OK   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2 { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err2 { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

# ---------- 自动提权 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '需要管理员权限，正在请求提权（新窗口中继续运行，结束后窗口自动关闭）...' -ForegroundColor Yellow
    $relaunchArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Port $Port"
    if ($Force) { $relaunchArgs += ' -Force' }
    Start-Process powershell -Verb RunAs -ArgumentList $relaunchArgs
    exit 0
}

# 以无 BOM 的 UTF-8 写文件（与 deploy.ps1 一致：docker compose 的 env_file 带 BOM 会让首行变量解析失败）
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------- 1. 令牌与配置 ----------
Write-Step '生成代理配置（usb_agent_config.json）'
$ConfigFile = Join-Path $Root 'usb_agent_config.json'
if ((Test-Path $ConfigFile) -and -not $Force) {
    try {
        $cfg = Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $token = [string]$cfg.token
        $Port = [int]$cfg.port
        Write-OK "沿用现有配置（端口 $Port，令牌不变）"
    } catch {
        Write-Warn2 '现有配置解析失败，重新生成'
        $token = ''
    }
} else {
    $token = ''
}
if (-not $token) {
    # 48 位随机 hex 令牌
    $token = -join (1..48 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    [System.IO.File]::WriteAllText($ConfigFile,
        (@{ token = $token; port = $Port } | ConvertTo-Json), $utf8NoBom)
    Write-OK "已生成新令牌并写入 $ConfigFile"
}

# ---------- 2. 更新 back/.env ----------
Write-Step '更新 back/.env（HOST_AGENT_URL / HOST_AGENT_TOKEN）'
$envFile = Join-Path $Root 'back\.env'
if (-not (Test-Path $envFile)) {
    Write-Err2 "缺少 $envFile（请先完成平台部署：.\deploy.ps1）"
    exit 1
}
$envLines = @([System.IO.File]::ReadAllLines($envFile) | Where-Object { $_ -match '\S' })
$agentUrl = "http://host.docker.internal:$Port"
foreach ($kv in @{ HOST_AGENT_URL = $agentUrl; HOST_AGENT_TOKEN = $token }.GetEnumerator()) {
    $key = $kv.Key; $val = $kv.Value
    $found = $false
    for ($i = 0; $i -lt $envLines.Count; $i++) {
        if ($envLines[$i] -match "^\s*$key\s*=") { $envLines[$i] = "$key=$val"; $found = $true; break }
    }
    if (-not $found) { $envLines += "$key=$val" }
}
[System.IO.File]::WriteAllLines($envFile, $envLines, $utf8NoBom)
Write-OK "back/.env 已更新（HOST_AGENT_URL=$agentUrl）"

# ---------- 3. URL ACL ----------
Write-Step '注册 URL ACL（HttpListener 监听 http://+:PORT 需要）'
& cmd /c "netsh http delete urlacl url=http://+:$Port/ >nul 2>nul"
& netsh http add urlacl "url=http://+:$Port/" "user=NT AUTHORITY\SYSTEM" | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-OK "URL ACL 已注册（http://+:$Port/）"
} else {
    Write-Warn2 "URL ACL 注册失败（退出码 $LASTEXITCODE）——代理可能无法监听，稍后探活会验证"
}

# ---------- 4. 防火墙（仅私网网段可访问 + 令牌鉴权双保险） ----------
Write-Step '配置防火墙入站规则（仅私网网段）'
Get-NetFirewallRule -DisplayName 'DataMa USB Agent' -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
try {
    New-NetFirewallRule -DisplayName 'DataMa USB Agent' -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $Port -RemoteAddress 172.16.0.0/12,10.0.0.0/8,192.168.0.0/16 | Out-Null
    Write-OK "防火墙规则已添加（端口 $Port，仅 10/8、172.16/12、192.168/16 可访问）"
} catch {
    Write-Warn2 "防火墙规则添加失败: $($_.Exception.Message)（若代理探活正常可忽略）"
}

# ---------- 5. 计划任务（SYSTEM，开机自启） ----------
Write-Step '注册计划任务 DataMaUsbAgent（SYSTEM，开机自启，无执行时限）'
$agentPath = Join-Path $Root 'usb_agent.ps1'
if (-not (Test-Path $agentPath)) {
    Write-Err2 "缺少 $agentPath（应与 install_usb_agent.ps1 同目录）"
    exit 1
}
Stop-ScheduledTask -TaskName 'DataMaUsbAgent' -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$agentPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
# ExecutionTimeLimit=0：不设时限（默认 72h 会杀掉常驻代理）；失败自动重启 3 次
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'DataMaUsbAgent' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-OK '计划任务已注册'

# ---------- 5.5 辅助计划任务 DataMaUsbReset（登录用户上下文执行透传） ----------
# 方案 A 核心：WSL 发行版实例绑定到登录用户，SYSTEM 账户下 wsl 感知不到发行版，
# 透传脚本会误报「未找到可用 WSL 发行版」。故常驻代理保留 SYSTEM 监听，但真正
# 执行 reset_orbbec_usb.ps1 的进程切到「当前登录用户」上下文（Interactive），
# 由 usb_agent.ps1 的 /reset 经 schtasks /Run 触发本任务。
# 前提：跑视频采集界面的机器需有管理员账号处于登录状态（Interactive 任务仅在其
# 登录会话中运行；用户未登录时本按钮会失败，属预期）。
Write-Step '注册辅助计划任务 DataMaUsbReset（以登录用户身份执行透传，供代理 /reset 触发）'
$runnerPath = Join-Path $Root 'usb_reset_runner.ps1'
if (-not (Test-Path $runnerPath)) {
    Write-Err2 "缺少 $runnerPath（应与 install_usb_agent.ps1 同目录）"
    exit 1
}
# 以当前（提权用户的原）登录用户身份注册：Interactive + RunLevel Highest。
# 说明：提权后 $env:USERNAME 仍是发起提权的用户（UAC 提权不会切换用户名）；
# 交互式自动登录会话匹配「当前登录用户」，无需存储密码。
$loginUser = "$env:USERDOMAIN\$env:USERNAME"
Stop-ScheduledTask -TaskName 'DataMaUsbReset' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'DataMaUsbReset' -Confirm:$false -ErrorAction SilentlyContinue
$runAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runnerPath`""
# 占位触发：定在明天，仅让任务「已启用」（schtasks /Run 对 Disabled 任务会报错
# "could not run because it is disabled"）。实际运行只靠代理 /reset 手动 /Run 触发，
# 不会在安装后自动跑一遍透传。
$runTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays(1)
$runPrincipal = New-ScheduledTaskPrincipal -UserId $loginUser -LogonType Interactive -RunLevel Highest
$runSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew  # 30 分钟上限，禁止并发
try {
    # 必须 Enabled（schtasks /Run 对 Disabled 任务报 "because it is disabled"）；
    # 占位触发定在明天，不会在安装后自动跑透传，运行只靠代理 /reset 手动触发
    Register-ScheduledTask -TaskName 'DataMaUsbReset' -Action $runAction -Trigger $runTrigger `
        -Principal $runPrincipal -Settings $runSettings -Force | Out-Null
    Write-OK "辅助计划任务已注册（用户 $loginUser，Interactive，仅登录会话运行）"
} catch {
    Write-Warn2 "辅助计划任务注册失败: $($_.Exception.Message)（透传按钮暂不可用，请以管理员用户登录本机后重跑本脚本）"
}

Start-ScheduledTask -TaskName 'DataMaUsbAgent'
Write-Host '  已触发启动，等待代理就绪...' -ForegroundColor DarkGray

# ---------- 6. 探活验证 ----------
$alive = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/ping" -TimeoutSec 3
        if ($r.ok) { $alive = $true; break }
    } catch { }
}
if ($alive) {
    Write-OK "代理已运行（http://127.0.0.1:$Port/ping）"
} else {
    Write-Err2 "代理 30 秒内未通过探活——手动排查:"
    Write-Host '  schtasks /Run /TN DataMaUsbAgent  然后查看任务是否在运行' -ForegroundColor Yellow
    Write-Host '  或管理员 PowerShell 手动前台运行 usb_agent.ps1 观察报错' -ForegroundColor Yellow
}

# ---------- 7. 重建 backend 容器加载新环境变量 ----------
if ($alive) {
    Write-Step '重建 backend 容器（加载 HOST_AGENT_* 环境变量）'
    & docker compose up -d backend
    if ($LASTEXITCODE -eq 0) {
        Write-OK 'backend 容器已更新（透传按钮后端已就绪）'
    } else {
        Write-Warn2 'docker compose up -d backend 失败，请手动执行以加载 HOST_AGENT_* 环境变量'
    }
}

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' USB 透传代理安装完成' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host '  视频采集弹窗中"透传深度相机"按钮已可用（深度相机未检测到时显示）' -ForegroundColor White
Write-Host '  代理开机自启（计划任务 DataMaUsbAgent），无需手动维护' -ForegroundColor DarkGray
Write-Host "  代理日志: $Root\usb_agent_job_*.log（每次透传一份，保留最近 5 份）" -ForegroundColor DarkGray
Write-Host ''
