# -*- coding: utf-8 -*-
"""创建/修复深度摄像头设备节点（usbfs + v4l2）

背景（两类节点，失效机制不同，都要修）：

1) usbfs —— /dev/bus/usb/<bus>/<dev>
   Docker Desktop（WSL2 后端）的 docker-desktop 发行版没有 udev 守护进程，
   usbipd 透传的 USB 设备不会自动生成 /dev/bus/usb 节点。容器与 docker-desktop
   共享内核，因此在容器内按 usbfs 设备号规则手动 mknod 即可访问设备。
   设备重连/重新 attach 后 devnum 会变化（如 2-1-15 -> 2-1-16），旧节点指向失效
   设备号，因此每次执行时先清除非 root hub 幽灵节点，再按当前 sysfs 重建。

2) v4l2 —— /dev/video<N>  ★ 透传后相机不可用的真正原因 ★
   Intel RealSense D400 系在 Linux 由 uvcvideo 驱动绑定为 video4linux 设备，
   librealsense 默认走 **V4L2 后端**取彩色/深度流（实测 physical_port 为
   .../2-1:1.0/video4linux/video0）。容器内 /dev 是 tmpfs，/dev/video* 是
   「容器创建时刻」由 runc 按当时宿主机设备 mknod 出来的**静态快照**：
     - 容器启动时相机尚未透传 → 容器内根本没有 /dev/video*，此后 attach 也不会补
     - usbip 重新 attach / 设备重新枚举 → 内核重建 video4linux 设备，旧快照或
       编号漂移、或指向已释放的 minor
   两种情况下 librealsense 打开 pipeline 都会失败（实测报
   "Couldn't resolve requests"），而设备在 sysfs 里**依然可见**（`probe` 枚举靠
   libusb 的 sysfs 兜底，能"看到"设备却打不开）→ 前端表现为"未检测到深度摄像头"。
   此时点「透传深度相机」也救不回来（旧实现只修 usbfs），只能重启/重建容器让
   runc 重新 mknod 快照 —— 本模块在检测/开会话前按 sysfs 重建这些节点即可消除
   该重启需求。

支持设备（按厂商 VID 白名单）：
  - Orbbec Femto Bolt（VID 0x2bc5，pyk4a / K4A Wrapper）
  - Intel RealSense D455f 等 D400 系（VID 0x8086，pyrealsense2 / librealsense）

设备号规则：
  usbfs: major = 189（USBDEVFS 固定），minor = (busnum - 1) * 128 + (devnum - 1)
  v4l2 : major = 81（固定），minor 由 /sys/class/video4linux/<node>/dev 给出

安全约定（避免打断正在进行的预览/录制）：
- v4l2 节点**只在"不存在"或"打开失败且错误码明确表示设备已消失"
  （ENODEV/ENXIO/EIO/ENOENT）时**才删除重建；EBUSY/EACCES/EPERM 等一律视为
  "在用/不可动"并跳过（重建会 unlink 旧节点，inode 变化）。
- 只处理属于白名单 VID 的节点，绝不触碰其他摄像头/USB 设备。
- 每个节点独立 try，单点失败不影响其余节点。
"""
import errno
import glob
import os

# 深度摄像头厂商 VID 白名单（Orbbec / Intel RealSense）
CAMERA_VENDOR_IDS = {"2bc5", "8086"}
USBDEVFS_MAJOR = 189
V4L2_MAJOR = 81

# 路径常量（测试可替换）
SYSFS_USB_DEVICES = "/sys/bus/usb/devices"
SYSFS_V4L2 = "/sys/class/video4linux"
DEV_BUS_USB = "/dev/bus/usb"
DEV_DIR = "/dev"

# 字符设备节点权限：S_IFCHR | 0666（实际受 umask 削位，022 下为 0644；
# 容器内进程以 root 运行，0644 足够）
_CHARDEV_MODE = 0o20666

# 打开失败时判定"设备已消失、节点需重建"的 errno 集合
_STALE_ERRNOS = frozenset(
    e for e in (getattr(errno, name, None) for name in
                ("ENODEV", "ENXIO", "EIO", "ENOENT"))
    if e is not None
)

# 沿 sysfs 父链向上查找 USB 设备节点的深度上限（防御异常深/环状路径）
_VENDOR_LOOKUP_DEPTH = 12


def _read_text(path):
    """读小文件并 strip；失败返回 None"""
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _usb_vendor_of(sysfs_path):
    """沿 sysfs 父链找到最近的 USB 设备目录，返回其 idVendor（小写）

    v4l2 设备节点的父链形如:
      /sys/devices/platform/vhci_hcd.0/usb2/2-1/2-1:1.0/video4linux/video0
    向上第三级 2-1 即 USB 设备目录，其 idVendor 为 8086。找不到返回 None。
    """
    path = os.path.realpath(sysfs_path)
    for _ in range(_VENDOR_LOOKUP_DEPTH):
        path = os.path.dirname(path)
        if not path or path == os.sep:
            return None
        vendor = _read_text(os.path.join(path, "idVendor"))
        if vendor:
            return vendor.lower()
    return None


def _usable_or_busy(path):
    """节点能否被打开；True 表示"可用或正被占用（不可动）"，False 表示"需重建"

    EBUSY（正被录制/预览占用）、EACCES/EPERM（权限）等都归入 True —— 宁可不修，
    也不要在会话进行中 unlink 掉节点。
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        return exc.errno not in _STALE_ERRNOS
    except Exception:
        return True
    try:
        os.close(fd)
    except OSError:
        pass
    return True


def _create_node(path, major, minor, created):
    """删除失效节点并按 (major, minor) 重新 mknod；成功则追加到 created"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    os.mknod(path, _CHARDEV_MODE, os.makedev(major, minor))
    created.append(f"{path} (maj={major}, min={minor})")


def _ensure_usbfs_nodes():
    """按 sysfs 重建深度摄像头的 /dev/bus/usb 节点，返回创建的节点列表

    失效表现是"路径名漂移"（devnum 变化 → 节点名变化），因此判据是
    "期望节点是否存在"，无需探活；额外清理 sysfs 里已不存在的非 root hub 幽灵节点。
    """
    created = []
    expected = {}  # node_path -> (major, minor)

    # 1) 期望节点：sysfs 中属于白名单 VID 的 USB 设备
    for dev in glob.glob(os.path.join(SYSFS_USB_DEVICES, "*", "")):
        vendor = _read_text(os.path.join(dev, "idVendor"))
        if not vendor or vendor.lower() not in CAMERA_VENDOR_IDS:
            continue
        try:
            busnum = int(_read_text(os.path.join(dev, "busnum")) or "")
            devnum = int(_read_text(os.path.join(dev, "devnum")) or "")
        except ValueError:
            continue
        minor = (busnum - 1) * 128 + (devnum - 1)
        expected[os.path.join(DEV_BUS_USB, f"{busnum:03d}", f"{devnum:03d}")] = (
            USBDEVFS_MAJOR, minor)

    # 2) 逐个补齐缺失节点
    for node, (major, minor) in sorted(expected.items()):
        if os.path.exists(node):
            continue
        _create_node(node, major, minor, created)

    # 3) 幽灵清理：非 root hub 且 sysfs 里已无对应设备的旧节点
    for bus_dir in glob.glob(os.path.join(DEV_BUS_USB, "*", "")):
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
            if devnum == 1 or node in expected:
                continue
            try:
                os.remove(node)
            except OSError:
                pass
    return created


def ensure_video_nodes():
    """按 sysfs 重建深度摄像头的 /dev/video<N> 节点（v4l2），返回创建的节点列表

    与 usbfs 不同，v4l2 的节点名固定（video0..N）而语义是 (major=81, minor)，
    编号漂移后同一路径会指向已释放/他人的设备，因此必须探活后再决定是否重建。
    """
    created = []
    expected = set()

    # 1) 期望节点：sysfs 中属于白名单 VID 的 v4l2 设备
    for sysfs_node in sorted(glob.glob(os.path.join(SYSFS_V4L2, "video*"))):
        name = os.path.basename(sysfs_node)
        if not name[len("video"):].isdigit():
            continue
        if _usb_vendor_of(sysfs_node) not in CAMERA_VENDOR_IDS:
            continue
        dev = _read_text(os.path.join(sysfs_node, "dev"))
        try:
            major, minor = (int(x) for x in (dev or "").split(":"))
        except ValueError:
            continue
        if major != V4L2_MAJOR:
            continue
        node = os.path.join(DEV_DIR, name)
        expected.add(node)
        if os.path.exists(node) and _usable_or_busy(node):
            continue  # 可用或正被占用：不动
        _create_node(node, major, minor, created)

    # 2) 幽灵清理：内核已无该 v4l2 设备，残留节点必然无效
    for node in glob.glob(os.path.join(DEV_DIR, "video*")):
        base = os.path.basename(node)
        if not base[len("video"):].isdigit() or node in expected:
            continue
        if os.path.exists(os.path.join(SYSFS_V4L2, base)):
            continue
        try:
            os.remove(node)
        except OSError:
            pass
    return created


def ensure_usb_nodes():
    """按当前 sysfs 重建深度相机的全部设备节点（usbfs + v4l2），返回创建列表

    函数名沿用历史（既有两个调用点 from ensure_usb_nodes import ensure_usb_nodes:
    app/api/realsense.py 与 app/api/orbbec.py），现在同时覆盖 v4l2 ——
    透传后无需重启容器即可恢复相机。
    """
    created = list(_ensure_usbfs_nodes())
    created.extend(ensure_video_nodes())
    return created


if __name__ == "__main__":
    try:
        created = ensure_usb_nodes()
        if created:
            print(f"[ensure_usb_nodes] 已创建设备节点: {created}")
        else:
            print("[ensure_usb_nodes] 未发现深度摄像头或节点均已可用")
    except Exception as e:
        print(f"[ensure_usb_nodes] 失败: {e}")
