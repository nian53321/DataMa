# -*- coding: utf-8 -*-
"""存量文件加密迁移脚本

将 data_lake 目录下已存在的明文文件原地加密为信封加密格式。
- 跳过 .transcodes 转码缓存目录（派生临时文件，不加密）
- 跳过已加密文件（魔数 DMEC 开头）
- 使用临时文件中转：先写到 .tmp 再替换，避免加密失败时损坏原文件

用法：
    cd back
    python scripts/migrate_encrypt.py            # 预览（dry-run）
    python scripts/migrate_encrypt.py --apply    # 实际执行
"""
import os
import sys
import tempfile

# 将 back/ 加入 sys.path，便于直接导入 app 模块
BACK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACK_DIR)

from app import create_app
from app.utils.crypto import (
    MAGIC, encrypt_bytes, is_encrypted_file, get_master_key,
)


def migrate(dry_run=True):
    """遍历 data_lake 目录，加密所有明文文件"""
    app = create_app()
    with app.app_context():
        # 确保主密钥就绪
        get_master_key()
        data_lake = app.config["DATA_LAKE_DIR"]
        print(f"数据湖目录: {data_lake}")
        print(f"主密钥: {app.config['MASTER_KEY_PATH']}")
        print(f"模式: {'预览（dry-run）' if dry_run else '实际执行'}")
        print("-" * 60)

        encrypted_count = 0
        skipped_count = 0
        failed_count = 0

        for root, dirs, files in os.walk(data_lake):
            # 跳过转码缓存目录
            if ".transcodes" in dirs:
                dirs.remove(".transcodes")
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, data_lake)

                # 已加密则跳过
                if is_encrypted_file(fpath):
                    print(f"  [跳过] 已加密: {rel}")
                    skipped_count += 1
                    continue

                if dry_run:
                    size = os.path.getsize(fpath)
                    print(f"  [待加密] {rel} ({size} 字节)")
                    encrypted_count += 1
                    continue

                # 实际加密：写临时文件 -> 原子替换
                try:
                    with open(fpath, "rb") as f:
                        plaintext = f.read()
                    encrypted = encrypt_bytes(plaintext)
                    fd, tmp = tempfile.mkstemp(
                        dir=os.path.dirname(fpath), prefix=".enc_", suffix=".tmp")
                    try:
                        os.write(fd, encrypted)
                    finally:
                        os.close(fd)
                    os.replace(tmp, fpath)
                    print(f"  [已加密] {rel}")
                    encrypted_count += 1
                except Exception as e:
                    print(f"  [失败] {rel}: {e}")
                    failed_count += 1

        print("-" * 60)
        print(f"总计: 加密 {encrypted_count}，跳过 {skipped_count}，失败 {failed_count}")
        if dry_run and encrypted_count > 0:
            print("\n这是预览模式。加 --apply 参数实际执行加密。")


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    migrate(dry_run=dry)
