<#
.SYNOPSIS
    宿主机 USB 透传代理（视频采集弹窗"透传深度相机"按钮的宿主机侧组件）
.DESCRIPTION
    由 install_usb_agent.ps1 注册为计划任务（SYSTEM，开机自启，隐藏窗口）常驻运行，
    监听 HTTP 端口（默认 8765）。后端容器经 host.docker.internal 调用本代理，
    代理以子进程执行同目录的 reset_orbbec_usb.ps1（-Yes 无人值守 +
    -SkipContainerRestart 不重启容器），并实时回报执行进度（日志尾部）。

    鉴权：令牌位于 usb_agent_config.json（install_usb_agent.ps1 生成，并同步到
    back/.env 的 HOST_AGENT_TOKEN），请求需带 token 查询参数或 X-Agent-Token 头。

    端点：
      GET  /ping            探活（无鉴权，仅返回服务名，用于安装验证/健康检查）
      GET  /status?token=   任务状态（running / exit_code / 日志尾部 tail）
      POST /reset?token=    启动透传任务（同一时刻仅允许一个任务，重复触发返回 409）

    设计要点：任务状态保存在本代理进程内（不在后端容器内）——透传脚本可能复位
    USB 总线导致后端容器/网络短暂中断，任务本体不受影响，恢复后继续查询即可。
.NOTES
    必须以管理员/SYSTEM 运行（HttpListener 监听 http://+:port/ 需要权限，
    子进程 reset_orbbec_usb.ps1 也需要管理员权限操作 usbipd）。
    手动调试：管理员 PowerShell 直接 .\usb_agent.ps1（Ctrl+C 停止）。
#>
param(
    # 监听端口；0 或不传 = 使用 usb_agent_config.json 中配置的端口
    [int]$Port = 0
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigFile = Join-Path $Root 'usb_agent_config.json'

if (-not (Test-Path $ConfigFile)) {
    Write-Error "缺少配置文件 $ConfigFile（请先以管理员运行 install_usb_agent.ps1）"
    exit 1
}
try {
    $cfg = Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Write-Error "配置文件解析失败: $($_.Exception.Message)"
    exit 1
}
if (-not $Port) { $Port = [int]$cfg.port }
$Token = [string]$cfg.token
$ResetScript = Join-Path $Root 'reset_orbbec_usb.ps1'
# 以无 BOM 的 UTF-8 写状态文件（避免 BOM 干扰退出码的 [int] 解析）
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------- 任务状态（监听循环内单线程串行读写，无并发竞争） ----------
# 方案 A：常驻代理保留 SYSTEM 上下文（HttpListener + usbipd 都需要管理员/SYSTEM），
# 但真正执行透传的进程切到「登录用户」上下文——WSL 发行版绑定登录用户，SYSTEM 下
# wsl 感知不到发行版会导致 reset 脚本误报「未找到可用 WSL 发行版」。
# 实现：/reset 不再直接 spawn 子进程，而是 schtasks /Run 触发辅助计划任务
# DataMaUsbReset（install_usb_agent.ps1 注册，以登录用户身份运行 usb_reset_runner.ps1）。
# 因代理拿不到辅助任务的进程句柄，任务状态（running/exit_code/日志尾部）改由
# usb_reset_runner.ps1 写出的状态文件 + 日志文件驱动（Get-JobState 统一读取）。
$script:TaskName = 'DataMaUsbReset'   # 辅助计划任务名（登录用户上下文）
$script:JobLogFile = ''
$script:JobExit = $null
$script:JobStartedAt = $null
$script:JobFinishedAt = $null

# 状态/日志文件（与 usb_reset_runner.ps1 约定一致）
$script:JobExitFile = Join-Path $Root 'usb_agent_job_exit.txt'

function Update-JobState {
    # 从「退出码状态文件 + 日志文件」推断任务是否结束。runnner 写退出码数字表示已结束。
    if ($script:JobStartedAt -and $null -eq $script:JobExit) {
        $ec = $null
        try { $ec = ([System.IO.File]::ReadAllText($script:JobExitFile)).Trim() } catch { }
        if ($ec -and $ec -ne 'running') {
            $script:JobExit = try { [int]$ec } catch { -1 }
            $script:JobFinishedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        }
    }
}

function Get-JobTail {
    # 任务日志尾部（最近 80 行），供前端进度展示
    if ($script:JobLogFile -and (Test-Path $script:JobLogFile)) {
        try {
            $lines = @(Get-Content $script:JobLogFile -Encoding UTF8 -ErrorAction SilentlyContinue |
                Select-Object -Last 80)
            return ($lines -join "`n")
        } catch { return '' }
    }
    return ''
}

function Send-Json {
    param($ctx, [int]$code, $obj)
    try {
        $json = $obj | ConvertTo-Json -Depth 5 -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $ctx.Response.StatusCode = $code
        $ctx.Response.ContentType = 'application/json; charset=utf-8'
        $ctx.Response.ContentLength64 = $bytes.Length
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    } catch { } finally {
        try { $ctx.Response.OutputStream.Close() } catch { }
    }
}

function Test-Token {
    param($ctx)
    $t = $ctx.Request.QueryString['token']
    if (-not $t) { $t = $ctx.Request.Headers['X-Agent-Token'] }
    return ($t -and ($t -eq $Token))
}

# ---------- 启动监听 ----------
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:$Port/")
try {
    $listener.Start()
} catch {
    Write-Error "HTTP 监听启动失败（端口 $Port）: $($_.Exception.Message)"
    Write-Host '  排查：端口被占用 / 未以管理员运行 / 缺少 URL ACL（重装: install_usb_agent.ps1）' -ForegroundColor Yellow
    exit 1
}

# ---------- 请求处理循环 ----------
while ($true) {
    $ctx = $null
    try {
        $ctx = $listener.GetContext()
    } catch {
        # 监听器异常（极少见）：短暂等待后继续，保证代理常驻
        Start-Sleep -Seconds 2
        continue
    }
    try {
        $path = $ctx.Request.Url.AbsolutePath.TrimEnd('/')
        if ($path -eq '') { $path = '/' }
        $method = $ctx.Request.HttpMethod

        if ($path -eq '/ping' -and $method -eq 'GET') {
            Send-Json $ctx 200 @{ ok = $true; service = 'datama-usb-agent' }
            continue
        }

        if (-not (Test-Token $ctx)) {
            Send-Json $ctx 403 @{ ok = $false; error = 'unauthorized' }
            continue
        }

        if ($path -eq '/status' -and $method -eq 'GET') {
            Update-JobState
            Send-Json $ctx 200 @{
                ok          = $true
                running     = ($null -eq $script:JobExit -and $null -ne $script:JobStartedAt)
                exit_code   = $script:JobExit
                started_at  = $script:JobStartedAt
                finished_at = $script:JobFinishedAt
                tail        = (Get-JobTail)
            }
            continue
        }

        if ($path -eq '/reset' -and $method -eq 'POST') {
            if (-not (Test-Path $ResetScript)) {
                Send-Json $ctx 404 @{ ok = $false; error = "未找到 reset_orbbec_usb.ps1（$ResetScript）" }
                continue
            }
            Update-JobState
            if ($null -eq $script:JobExit -and $null -ne $script:JobStartedAt) {
                Send-Json $ctx 409 @{ ok = $false; error = '已有透传任务在执行中' }
                continue
            }
            # 校验辅助计划任务已注册（方案 A 依赖它：以登录用户上下文执行透传脚本）
            $task = Get-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue
            if (-not $task) {
                Send-Json $ctx 500 @{ ok = $false; error = "辅助计划任务 $($script:TaskName) 未注册——请在宿主机以管理员运行 install_usb_agent.ps1（会注册登录用户上下文的透传执行器）" }
                continue
            }
            # 清空退出码状态文件，标记进行中，然后触发辅助任务
            try { [System.IO.File]::WriteAllText($script:JobExitFile, 'running', $utf8NoBom) } catch { }
            $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
            $script:JobLogFile = Join-Path $Root "usb_agent_job_$ts.log"
            $script:JobExit = $null
            $script:JobStartedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            $script:JobFinishedAt = $null
            # 触发辅助计划任务（schtasks /Run 会让任务以它注册的用户身份运行）
            $null = & schtasks.exe /Run /TN $script:TaskName 2>&1
            if ($LASTEXITCODE -ne 0) {
                $script:JobExit = -1
                $script:JobFinishedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
                Send-Json $ctx 500 @{ ok = $false; error = "触发透传任务失败（schtasks /Run 退出码 $LASTEXITCODE），请检查计划任务 $($script:TaskName) 是否正常" }
                continue
            }
            Send-Json $ctx 200 @{ ok = $true; started = $true; started_at = $script:JobStartedAt }
            continue
        }

        Send-Json $ctx 404 @{ ok = $false; error = "not found: $method $path" }
    } catch {
        try { Send-Json $ctx 500 @{ ok = $false; error = $_.Exception.Message } } catch { }
    }
}
