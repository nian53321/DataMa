# -*- coding: utf-8 -*-
"""创建/修复深度摄像头 USB 设备节点

背景：Docker Desktop（WSL2 后端）的 docker-desktop 发行版没有 udev 守护进程，
usbipd 透传的 USB 设备不会自动生成 /dev/bus/usb 节点。容器与 docker-desktop
共享内核，因此在容器内按 usbfs 设备号规则手动 mknod 即可访问设备。

支持设备（按厂商 VID）：
  - Orbbec Femto Bolt（VID 0x2bc5，pyk4a / K4A Wrapper）
  - Intel RealSense D455f 等 D400 系（VID 0x8086，pyrealsense2 / librealsense）

设备重连/重新 attach 后 devnum 会变化（如 2-1-15 -> 2-1-16），旧节点指向失效
设备号。因此每次执行时先清除非 root hub 旧节点，再按当前 sysfs 重建。

usbfs 设备号规则：
  major = 189（USBDEVFS 主设备号固定）
  minor = (busnum - 1) * 128 + (devnum - 1)
"""
import glob
import os

# 深度摄像头厂商 VID 白名单（Orbbec / Intel RealSense）
CAMERA_VENDOR_IDS = {"2bc5", "8086"}
USBDEVFS_MAJOR = 189


def ensure_usb_nodes():
    """清除旧节点并按当前 sysfs 重建深度摄像头设备节点，返回创建的节点列表"""
    # 1) 删除所有非 root hub 设备节点（devnum != 1），避免旧设备号残留
    for bus_dir in glob.glob("/dev/bus/usb/*/"):
        try:
            busnum = int(os.path.basename(bus_dir.rstrip("/\\")))
        except ValueError:
            continue
        if busnum == 1:
            continue  # 保留 bus1（root hub 区）
        for node in glob.glob(os.path.join(bus_dir, "*")):
            try:
                devnum = int(os.path.basename(node))
            except ValueError:
                continue
            if devnum == 1:  # root hub 自身，保留
                continue
            try:
                os.remove(node)
            except OSError:
                pass
    # 2) 按当前 sysfs 创建深度摄像头设备节点
    created = []
    for dev in glob.glob("/sys/bus/usb/devices/*/"):
        try:
            vid = open(os.path.join(dev, "idVendor")).read().strip()
        except OSError:
            continue  # 非 USB 设备目录（如 usb1/usb2 根）
        if vid.lower() not in CAMERA_VENDOR_IDS:
            continue
        busnum = int(open(os.path.join(dev, "busnum")).read().strip())
        devnum = int(open(os.path.join(dev, "devnum")).read().strip())
        bus_dir = f"/dev/bus/usb/{busnum:03d}"
        node = os.path.join(bus_dir, f"{devnum:03d}")
        if os.path.exists(node):
            continue
        os.makedirs(bus_dir, exist_ok=True)
        minor = (busnum - 1) * 128 + (devnum - 1)
        os.mknod(node, 0o20666, os.makedev(USBDEVFS_MAJOR, minor))  # char dev + rw-rw-rw-
        created.append(f"{node} (vid={vid}, maj={USBDEVFS_MAJOR}, min={minor})")
    return created


if __name__ == "__main__":
    try:
        created = ensure_usb_nodes()
        if created:
            print(f"[ensure_usb_nodes] 已创建设备节点: {created}")
        else:
            print("[ensure_usb_nodes] 未发现深度摄像头或节点已存在")
    except Exception as e:
        print(f"[ensure_usb_nodes] 失败: {e}")
