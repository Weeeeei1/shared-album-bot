"""
共享相册机器人配置
支持环境变量读取，生产环境应设置以下变量：
- BOT_TOKEN: Telegram Bot Token
- PUBLIC_CHANNEL_ID: 公开频道ID
- PRIVATE_GROUP_ID: 私有群组ID
- ADMIN_USER_ID: 管理员用户ID
- DATABASE_FILE: 数据库文件路径
- LOG_FILE: 日志文件路径
- LOG_LEVEL: 日志级别
"""

import os


def _get_env(key: str, default, cast_type=None):
    """从环境变量获取配置，支持类型转换"""
    value = os.environ.get(key)
    if value is None:
        return default
    if cast_type is not None:
        try:
            return cast_type(value)
        except (ValueError, TypeError):
            return default
    return value


# Bot Token - 必须设置
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# 频道/群组配置（必须设置，无默认值）
PUBLIC_CHANNEL_ID = _get_env("PUBLIC_CHANNEL_ID", None, int)
PRIVATE_GROUP_ID = _get_env("PRIVATE_GROUP_ID", None, int)

# 管理员配置（必须设置，无默认值）
ADMIN_USER_ID = _get_env("ADMIN_USER_ID", None, int)

# 数据库配置
DATABASE_FILE = os.environ.get("DATABASE_FILE", "shared_album_bot.db")

# 默认相册名称
DEFAULT_ALBUM_NAME = "未分类"

# 日志配置
LOG_FILE = os.environ.get("LOG_FILE", "bot.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# 版本号
BOT_VERSION = "1.0.4"


def validate_config():
    """验证配置是否完整"""
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN 环境变量未设置")
    if not PUBLIC_CHANNEL_ID:
        errors.append("PUBLIC_CHANNEL_ID 环境变量未设置")
    if not PRIVATE_GROUP_ID:
        errors.append("PRIVATE_GROUP_ID 环境变量未设置")
    if not ADMIN_USER_ID:
        errors.append("ADMIN_USER_ID 环境变量未设置")
    return errors
