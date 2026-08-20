<#
.SYNOPSIS
    USB 透传执行器（以登录用户身份运行 reset_orbbec_usb.ps1）
.DESCRIPTION
    由辅助计划任务 DataMaUsbReset 以「当前登录用户」上下文运行（install_usb_agent.ps1
    注册），供 usb_agent.ps1 的 /reset 经 schtasks /Run 触发。

    为什么需要它：usb_agent.ps1 常驻代理是 SYSTEM（LocalSystem）账户——WSL 发行版
    实例绑定到具体登录用户，SYSTEM 下 wsl -l / wsl -d 感知不到发行版，透传脚本的
    发行版检测会误报「未找到可用 WSL 发行版」。把真正执行透传的进程切到登录用户
    上下文即可解决，同时保留 SYSTEM 代理常驻监听（贴合远程无人值守设计）。

    本脚本自选日志文件并写退出码到状态文件，usb_agent.ps1 的 /status 据此回报
    进度与结束状态（代理进程无法持有辅助任务的进程句柄，只能靠文件轮询）。
.NOTES
    由 install_usb_agent.ps1 维护的计划任务 DataMaUsbReset 自动调用，无需手动运行。
    退出码文件：usb_agent_job_exit.txt（内容 "running"=进行中 / 数字=退出码）。
#>
param(
    # 可选：显式指定日志文件（缺省自动生成时间戳文件，保留最近 10 份）
    [string]$LogFile = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResetScript = Join-Path $Root 'reset_orbbec_usb.ps1'
$ExitFile = Join-Path $Root 'usb_agent_job_exit.txt'

if (-not $LogFile) {
    $LogFile = Join-Path $Root ("usb_agent_job_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
}
# 历史任务日志只保留最近 10 份
Get-ChildItem -Path $Root -Filter 'usb_agent_job_*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 |
    Remove-Item -Force -ErrorAction SilentlyContinue

# 标记进行中
try { [System.IO.File]::WriteAllText($ExitFile, 'running', (New-Object System.Text.UTF8Encoding($false))) } catch { }

if (-not (Test-Path $ResetScript)) {
    try { [System.IO.File]::WriteAllText($ExitFile, '404', (New-Object System.Text.UTF8Encoding($false))) } catch {}
    exit 1
}

# 以当前用户身份跑透传脚本（无人值守 + 不重启容器，与旧 usb_agent 行为一致）。
# stderr 一并并入日志；脚本内部已把控制台编码设为 UTF-8，中文正常。
# 用 cmd 包装让重定向对 PowerShell 5.1 的 native stderr 更稳。
& cmd.exe /c "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$ResetScript`" -Yes -SkipContainerRestart > `"$LogFile`" 2>&1"
$code = $LASTEXITCODE
if ($null -eq $code) { $code = -1 }

# 写退出码
try { [System.IO.File]::WriteAllText($ExitFile, "$code", (New-Object System.Text.UTF8Encoding($false))) } catch { }
exit $code
