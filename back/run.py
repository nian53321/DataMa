# -*- coding: utf-8 -*-
"""Flask 启动入口"""
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    env = os.getenv("FLASK_ENV", "development")
    # 开发环境使用 Flask 内置服务器，生产环境应使用 gunicorn
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=(env == "development"),
        threaded=True,
    )
