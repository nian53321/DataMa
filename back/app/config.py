# -*- coding: utf-8 -*-
"""应用配置"""
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BaseConfig:
    """基础配置"""
    # 项目根目录（back/），用于定位密钥文件等
    BASE_DIR = BASE_DIR

    # MySQL 数据库连接
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "123456")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_NAME = os.getenv("DB_NAME", "data_management")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        "?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }

    # JWT 配置
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production")
    JWT_ACCESS_TOKEN_EXPIRES = 86400  # 24 小时
    # 同时支持 Header 与 Query 参数传递 token：
    #   - Header: Authorization: Bearer <token>  (常规 AJAX)
    #   - Query : ?access_token=<token>          (浏览器 <video>/<audio>/<a download> 标签)
    JWT_TOKEN_LOCATION = ["headers", "query_string"]
    JWT_QUERY_STRING_NAME = "access_token"

    # Celery 配置
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    # 文件存储路径（轻量级数据湖本地存储目录）
    DATA_LAKE_DIR = os.path.join(BASE_DIR, "data_lake")
    UPLOAD_DIR = os.path.join(DATA_LAKE_DIR, "raw")
    CLEANED_DIR = os.path.join(DATA_LAKE_DIR, "cleaned")
    FEATURE_DIR = os.path.join(DATA_LAKE_DIR, "feature")
    ANNOTATION_DIR = os.path.join(DATA_LAKE_DIR, "annotation")

    # 文件加密配置（信封加密：主密钥保护每文件 DEK）
    ENCRYPT_DATA_LAKE = True                     # 是否启用数据湖文件加密
    MASTER_KEY_PATH = os.path.join(BASE_DIR, "master.key")  # 主密钥文件路径

    # 分页默认值
    DEFAULT_PAGE_SIZE = 20
    MAX_PAGE_SIZE = 100

    # 文件上传上限（512MB）
    MAX_CONTENT_LENGTH = 512 * 1024 * 1024

    # refresh token 30 天有效
    JWT_REFRESH_TOKEN_EXPIRES = 30 * 24 * 3600

    # CORS 白名单（逗号分隔，生产环境应限定为前端域名）
    # 默认 "*" 以支持前后端跨服务器部署；__init__.py 会在启动时记录警告日志
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

    # 登录失败限制参数（供 auth.py 读取，便于环境覆盖）
    LOGIN_MAX_FAILS = int(os.getenv("LOGIN_MAX_FAILS", "5"))
    LOGIN_LOCK_SECONDS = int(os.getenv("LOGIN_LOCK_SECONDS", "900"))  # 15 分钟

    # 脱敏 HMAC 密钥（未配置时回退到无盐 SHA256，仅记录警告日志）
    DESENS_HMAC_KEY = os.getenv("DESENS_HMAC_KEY", "")

    # 日志配置
    LOG_DIR = os.path.join(BASE_DIR, "logs")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


class DevelopmentConfig(BaseConfig):
    """开发环境"""
    DEBUG = True
    SQLALCHEMY_ECHO = False


class ProductionConfig(BaseConfig):
    """生产环境"""
    DEBUG = False

    @classmethod
    def validate(cls):
        """生产环境配置校验：禁止使用默认密钥与弱密码"""
        # JWT 密钥不能是默认值
        if cls.JWT_SECRET_KEY == "change-me-in-production":
            raise RuntimeError(
                "生产环境 JWT_SECRET_KEY 不能使用默认值，请配置随机强密钥"
            )
        # 数据库密码不能是弱密码 123456
        if cls.DB_PASSWORD == "123456":
            raise RuntimeError(
                "生产环境 DB_PASSWORD 不能使用弱密码 123456，请更换为强密码"
            )


class TestingConfig(BaseConfig):
    """测试环境"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(env=None):
    """根据环境名获取配置"""
    if env is None:
        env = os.getenv("FLASK_ENV", "development")
    # 生产环境启动前强制校验关键配置
    if env == "production":
        ProductionConfig.validate()
    return config_map.get(env, DevelopmentConfig)
