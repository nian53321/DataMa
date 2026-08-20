"""宿主机 USB 透传代理客户端（usb_agent.ps1）

视频采集弹窗"透传深度相机"按钮：后端容器无法直接执行宿主机 PowerShell，
经 host.docker.internal 调用宿主机 usb_agent.ps1（install_usb_agent.ps1
注册的计划任务，令牌鉴权），由代理以 SYSTEM 子进程执行 reset_orbbec_usb.ps1
（无人值守 -Yes -SkipContainerRestart），进度经 /passthrough/status 轮询。

任务状态保存在宿主机代理进程内——透传脚本复位 USB 总线导致后端容器/网络
短暂中断时任务本体不受影响，恢复后继续查询即可。

配置（back/.env，由 install_usb_agent.ps1 自动写入）：
- HOST_AGENT_URL   代理地址，默认 http://host.docker.internal:8765
- HOST_AGENT_TOKEN 访问令牌；缺失时代理未部署，透传按钮不可用
"""
import json
import os
import urllib.error
import urllib.request

_AGENT_URL = (os.environ.get("HOST_AGENT_URL") or "http://host.docker.internal:8765").rstrip("/")
_AGENT_TOKEN = os.environ.get("HOST_AGENT_TOKEN") or ""


def agent_configured():
    """宿主机透传代理是否已配置（back/.env 含 HOST_AGENT_TOKEN）"""
    return bool(_AGENT_TOKEN)


def agent_call(path, method="GET", timeout=5):
    """调用宿主机 USB 透传代理；返回 (data_dict, err)"""
    if not _AGENT_TOKEN:
        return None, "宿主机透传代理未配置（back/.env 缺少 HOST_AGENT_TOKEN），请在宿主机以管理员运行 install_usb_agent.ps1"
    try:
        req = urllib.request.Request(
            _AGENT_URL + path, method=method,
            data=b"" if method == "POST" else None)
        req.add_header("X-Agent-Token", _AGENT_TOKEN)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        # 代理业务错误（409 任务进行中 / 403 令牌不符等）：透出代理给出的错误信息
        try:
            body = json.loads(e.read().decode("utf-8"))
            return None, body.get("error") or f"宿主机代理返回 {e.code}"
        except Exception:
            return None, f"宿主机代理返回 {e.code}"
    except Exception:
        return None, "无法连接宿主机透传代理——请确认宿主机已运行 install_usb_agent.ps1 且计划任务 DataMaUsbAgent 在运行"
