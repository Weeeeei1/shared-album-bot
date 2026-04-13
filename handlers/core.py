"""Core utility functions and constants."""

import config
from database import db

# Session states
WAITING_ALBUM_NAME = 1
WAITING_MAX_VIEWERS = 2
WAITING_EXPIRY = 3
WAITING_RENAME = 4

# Constants
MAX_ALBUM_NAME_LENGTH = 50
MIN_ALBUM_NAME_LENGTH = 1


def validate_album_name(name: str) -> tuple:
    """验证相册名称，返回 (是否有效, 错误消息)"""
    if not name or not name.strip():
        return False, "相册名称不能为空"
    name = name.strip()
    if len(name) > MAX_ALBUM_NAME_LENGTH:
        return False, f"相册名称不能超过{MAX_ALBUM_NAME_LENGTH}个字符"
    if len(name) < MIN_ALBUM_NAME_LENGTH:
        return False, f"相册名称至少需要{MIN_ALBUM_NAME_LENGTH}个字符"
    return True, name


def get_user_info(user) -> str:
    """获取用户显示名称"""
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        return user.first_name
    else:
        return str(user.id)


def notify_album_owner(context, owner_id: int, viewer, album_name: str):
    """通知相册创建者有新访客"""
    try:
        viewer_name = get_user_info(viewer)
        message = f"👤 {viewer_name} 查看了你的相册「{album_name}」"
        context.bot.send_message(chat_id=owner_id, text=message)
    except Exception as e:
        pass  # 忽略通知失败


def is_admin(user_id: int) -> bool:
    """检查用户是否是管理员"""
    return user_id == config.ADMIN_USER_ID


def get_main_menu_keyboard():
    """获取主菜单键盘"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 上传媒体", callback_data="menu_upload")],
            [InlineKeyboardButton("📁 我的相册", callback_data="menu_albums")],
            [InlineKeyboardButton("👥 我的粉丝", callback_data="my_fans_menu")],
            [InlineKeyboardButton("➕ 创建相册", callback_data="menu_create")],
            [InlineKeyboardButton("📊 系统统计", callback_data="menu_stats")],
            [InlineKeyboardButton("❓ 使用帮助", callback_data="menu_help")],
        ]
    )
