"""
共享相册 Telegram 机器人主程序 - 全按钮交互版
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

import config
from database import db

# 设置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, config.LOG_LEVEL),
    handlers=[logging.FileHandler(config.LOG_FILE), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# 会话状态
WAITING_ALBUM_NAME = 1
WAITING_MAX_VIEWERS = 2
WAITING_EXPIRY = 3
WAITING_RENAME = 4


def get_user_info(user) -> str:
    """获取用户信息显示"""
    parts = []
    if user.username:
        parts.append(f"@{user.username}")
    if user.first_name:
        parts.append(user.first_name)
    if user.last_name:
        parts.append(user.last_name)
    return " | ".join(parts) if parts else f"User_{user.id}"


async def notify_album_owner(
    context: ContextTypes.DEFAULT_TYPE, owner_id: int, visitor, album_name: str
):
    """通知相册创建者有人访问了相册"""
    try:
        # 构建访问者信息
        visitor_info = []
        if visitor.username:
            visitor_info.append(f"@{visitor.username}")
        if visitor.first_name:
            visitor_info.append(visitor.first_name)
        if visitor.last_name:
            visitor_info.append(visitor.last_name)

        visitor_display = (
            " ".join(visitor_info) if visitor_info else f"用户 {visitor.id}"
        )

        # 构建通知消息
        notification_text = (
            f"🔔 访问通知\n\n"
            f"👤 {visitor_display} 访问了你的相册「{album_name}」\n"
            f"🆔 用户ID: {visitor.id}\n"
            f"⏰ 访问时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # 发送通知给相册创建者
        await context.bot.send_message(chat_id=owner_id, text=notification_text)

        logger.info(
            f"已发送访问通知给相册创建者 {owner_id}: {visitor_display} 访问了 {album_name}"
        )

    except Exception as e:
        error_msg = str(e)
        logger.error(f"发送访问通知失败: {error_msg}")

        # 如果是权限问题（用户未启动机器人），记录特殊日志
        if (
            "bot can't initiate conversation" in error_msg.lower()
            or "chat not found" in error_msg.lower()
        ):
            logger.warning(
                f"无法通知用户 {owner_id}: 该用户需要先给机器人发送 /start 启动对话"
            )
        elif "user is deactivated" in error_msg.lower():
            logger.warning(f"无法通知用户 {owner_id}: 该用户已停用 Telegram")
        elif "blocked" in error_msg.lower():
            logger.warning(f"无法通知用户 {owner_id}: 该用户已屏蔽机器人")


def is_admin(user_id: int) -> bool:
    """检查是否为管理员"""
    return user_id == config.ADMIN_USER_ID


async def check_channel_membership(
    user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """检查用户是否订阅了公开频道"""
    try:
        member = await context.bot.get_chat_member(config.PUBLIC_CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"检查频道成员状态失败: {e}")
        return False


async def require_channel_membership(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """要求用户必须订阅频道才能使用，返回是否通过检查"""
    user = update.effective_user

    # 管理员豁免
    if is_admin(user.id):
        return True

    # 检查是否已订阅
    is_member = await check_channel_membership(user.id, context)

    if not is_member:
        try:
            chat = await context.bot.get_chat(config.PUBLIC_CHANNEL_ID)
            channel_title = chat.title
            channel_link = (
                chat.invite_link
                or f"https://t.me/c/{str(config.PUBLIC_CHANNEL_ID)[4:]}"
            )
        except:
            channel_title = "公开频道"
            channel_link = f"https://t.me/c/{str(config.PUBLIC_CHANNEL_ID)[4:]}"

        keyboard = [
            [InlineKeyboardButton(f"👉 订阅 {channel_title}", url=channel_link)]
        ]

        message = f"""❌ 使用机器人前请先订阅我们的频道！

📢 频道: {channel_title}

点击下方按钮订阅，然后再回来使用机器人 👇"""

        if update.callback_query:
            await update.callback_query.answer("请先订阅频道！", show_alert=True)
            await update.callback_query.edit_message_text(
                message, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                message, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return False

    return True


def get_main_menu_keyboard():
    """获取主菜单键盘"""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 上传媒体", callback_data="menu_upload")],
            [InlineKeyboardButton("📁 我的相册", callback_data="menu_albums")],
            [InlineKeyboardButton("➕ 创建相册", callback_data="menu_create")],
            [InlineKeyboardButton("📊 系统统计", callback_data="menu_stats")],
            [InlineKeyboardButton("❓ 使用帮助", callback_data="menu_help")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令 - 主入口"""
    user = update.effective_user

    # 保存用户信息
    db.add_user(user.id, user.username, user.first_name, user.last_name)

    # 检查是否是通过分享链接访问
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("album_"):
            # 新格式: album_{id}_{token}
            parts = arg.split("_")
            if len(parts) >= 3:
                album_id = int(parts[1])
                token = parts[2]
                return await view_shared_album(update, context, album_id, token)
            else:
                # 旧格式兼容（没有令牌）
                album_id = int(parts[1])
                return await view_shared_album(update, context, album_id, None)

    # 检查频道订阅
    if not await require_channel_membership(update, context):
        return

    welcome_text = f"""👋 你好 {user.first_name}！

欢迎使用共享相册机器人 🤖

📸 直接发送图片、视频或文件即可保存

📁 创建相册来组织你的内容

🌐 分享相册给好友，设置访问权限

👇 点击下方按钮开始使用"""

    await update.message.reply_text(welcome_text, reply_markup=get_main_menu_keyboard())


# ========== 媒体上传处理 ==========


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户上传的媒体 - 支持批量上传"""
    # 只在私聊中响应
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    message = update.message

    # 保存用户信息
    db.add_user(user.id, user.username, user.first_name, user.last_name)

    # 检查频道订阅（管理员豁免）
    if not is_admin(user.id):
        if not await check_channel_membership(user.id, context):
            try:
                chat = await context.bot.get_chat(config.PUBLIC_CHANNEL_ID)
                channel_title = chat.title
                channel_link = (
                    chat.invite_link
                    or f"https://t.me/c/{str(config.PUBLIC_CHANNEL_ID)[4:]}"
                )
            except:
                channel_title = "公开频道"
                channel_link = f"https://t.me/c/{str(config.PUBLIC_CHANNEL_ID)[4:]}"

            keyboard = [
                [InlineKeyboardButton(f"👉 订阅 {channel_title}", url=channel_link)]
            ]

            await message.reply_text(
                f"""❌ 请先订阅频道才能上传媒体！

📢 频道: {channel_title}

点击下方按钮订阅 👇""",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

    # 确定媒体类型和文件ID
    file_id = None
    file_type = None
    caption = message.caption or ""

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    elif message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
    elif message.voice:
        file_id = message.voice.file_id
        file_type = "voice"
    else:
        await message.reply_text("❌ 不支持的媒体类型")
        return

    # 检查是否重复上传（同一文件ID在默认相册中已存在）
    album_id = db.get_default_album(user.id)
    if db.is_file_exists(album_id, file_id):
        await message.reply_text(
            "⚠️ 此媒体文件已存在于你的相册中，无需重复上传。",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    # 转发到私有群组备份
    try:
        user_info = get_user_info(user)
        topic_title = f"👤 用户 {user.id}"
        if user.username:
            topic_title += f" (@{user.username})"
        elif user.first_name:
            topic_title += f" - {user.first_name}"

        full_caption = caption if caption else ""
        full_caption += f"\n\n{topic_title}" if full_caption else topic_title

        forwarded = await message.copy(
            chat_id=config.PRIVATE_GROUP_ID, caption=full_caption
        )

        private_message_id = forwarded.message_id

        # 添加到待处理媒体列表（批量处理）
        if "pending_media_list" not in context.user_data:
            context.user_data["pending_media_list"] = []

        context.user_data["pending_media_list"].append(
            {
                "file_id": file_id,
                "file_type": file_type,
                "caption": caption,
                "private_message_id": private_message_id,
            }
        )

        # 取消之前的定时器（如果有）
        if "batch_timer" in context.user_data:
            try:
                context.user_data["batch_timer"].cancel()
            except:
                pass

        # 设置新的定时器，2秒后处理批量上传
        async def process_batch():
            await asyncio.sleep(2)
            await process_batch_upload(update, context)

        context.user_data["batch_timer"] = asyncio.create_task(process_batch())

    except Exception as e:
        logger.error(f"处理媒体时出错: {e}")
        await message.reply_text("❌ 处理失败，请重试")


async def process_batch_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理批量上传的媒体"""
    user = update.effective_user
    media_list = context.user_data.get("pending_media_list", [])

    if not media_list:
        return

    count = len(media_list)

    # 显示相册选择（批量）
    await show_batch_album_selection(update, context, count)


async def show_batch_album_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, count: int
):
    """显示批量相册选择列表"""
    user = update.effective_user

    albums = db.get_user_albums(user.id)

    keyboard = []

    # 显示所有相册
    for album in albums:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 {album['name']}",
                    callback_data=f"batch_select_album_{album['album_id']}",
                )
            ]
        )

    # 新增相册选项
    keyboard.append(
        [InlineKeyboardButton("➕ 创建新相册", callback_data="batch_create_album")]
    )

    # 跳过选择（存默认相册）
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔘 跳过（存默认相册）", callback_data="batch_select_default"
            )
        ]
    )

    text = f"✅ 已收到 {count} 个媒体文件！\n\n请选择要保存到的相册："

    await context.bot.send_message(
        chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ========== 回调处理 ==========


async def show_album_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示相册选择列表"""
    user = update.effective_user
    message = update.message

    albums = db.get_user_albums(user.id)

    keyboard = []

    # 显示所有相册
    for album in albums:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 {album['name']}",
                    callback_data=f"select_album_save_{album['album_id']}",
                )
            ]
        )

    # 新增相册选项
    keyboard.append(
        [InlineKeyboardButton("➕ 创建新相册", callback_data="select_album_new")]
    )

    # 跳过选择（存默认相册）
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔘 跳过（存默认相册）", callback_data="select_album_default"
            )
        ]
    )

    text = "✅ 媒体已上传到服务器！\n\n请选择要保存到的相册："

    if message:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理所有内联键盘回调"""
    query = update.callback_query
    await query.answer()

    data = query.data
    user = update.effective_user

    # 保存用户信息
    db.add_user(user.id, user.username, user.first_name, user.last_name)

    # 检查频道订阅（管理员豁免）- 每次交互都检查
    if not is_admin(user.id):
        if not await check_channel_membership(user.id, context):
            try:
                chat = await context.bot.get_chat(config.PUBLIC_CHANNEL_ID)
                channel_title = chat.title
                channel_link = (
                    chat.invite_link
                    or f"https://t.me/c/{str(config.PUBLIC_CHANNEL_ID)[4:]}"
                )
            except:
                channel_title = "公开频道"
                channel_link = f"https://t.me/c/{str(config.PUBLIC_CHANNEL_ID)[4:]}"

            keyboard = [
                [InlineKeyboardButton(f"👉 订阅 {channel_title}", url=channel_link)]
            ]

            message = f"""❌ 请先订阅频道才能使用机器人！

📢 频道: {channel_title}

点击下方按钮订阅，然后再回来使用机器人 👇"""

            try:
                await query.edit_message_text(
                    message, reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except:
                await context.bot.send_message(
                    chat_id=user.id,
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            return

    # ========== 菜单导航 ==========
    if data == "menu_upload":
        await query.edit_message_text(
            "📤 请直接发送图片、视频或文件\n\n我会帮你保存并管理它们",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]
            ),
        )

    elif data == "menu_main":
        try:
            await query.edit_message_text(
                f"👋 你好 {user.first_name}！\n\n欢迎使用共享相册机器人 🤖\n\n👇 选择功能",
                reply_markup=get_main_menu_keyboard(),
            )
        except:
            # 如果编辑失败（当前是媒体消息），删除后发送新消息
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(
                chat_id=user.id,
                text=f"👋 你好 {user.first_name}！\n\n欢迎使用共享相册机器人 🤖\n\n👇 选择功能",
                reply_markup=get_main_menu_keyboard(),
            )

    elif data == "menu_albums":
        await show_albums_list(update, context)

    elif data == "menu_create":
        await start_create_album(update, context)

    elif data == "menu_stats":
        await show_stats(update, context)

    elif data == "menu_help":
        await show_help(update, context)

    # ========== 批量相册选择 ==========
    elif data.startswith("batch_select_album_"):
        album_id = int(data.split("_")[3])
        await process_batch_save(update, context, album_id)

    elif data == "batch_select_default":
        album_id = db.get_default_album(user.id)
        await process_batch_save(update, context, album_id)

    elif data == "batch_create_album":
        await query.edit_message_text(
            "➕ 为批量媒体创建新相册\n\n请发送相册名称：",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 取消", callback_data="cancel_batch_create")]]
            ),
        )
        context.user_data["waiting_for"] = "batch_new_album"

    elif data == "cancel_batch_create":
        context.user_data.pop("waiting_for", None)
        media_list = context.user_data.get("pending_media_list", [])
        await show_batch_album_selection(update, context, len(media_list))

    # ========== 批量公开/保存选择 ==========
    elif data == "batch_publish_public":
        await publish_batch_media(update, context, is_public=True)

    elif data == "batch_publish_private":
        await publish_batch_media(update, context, is_public=False)

    # ========== 相册选择 ==========
    elif data.startswith("select_album_save_"):
        album_id = int(data.split("_")[3])
        context.user_data["selected_album_id"] = album_id
        await ask_public_or_private(update, context)

    elif data == "select_album_new":
        await query.edit_message_text(
            "➕ 创建新相册\n\n请发送相册名称：",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 取消", callback_data="cancel_create")]]
            ),
        )
        context.user_data["waiting_for"] = "new_album_for_media"

    elif data == "select_album_default":
        album_id = db.get_default_album(user.id)
        context.user_data["selected_album_id"] = album_id
        await ask_public_or_private(update, context)

    elif data == "cancel_create":
        context.user_data.pop("waiting_for", None)
        await show_album_selection(update, context)

    # ========== 媒体发布 ==========
    elif data == "publish_public":
        await publish_media(update, context, is_public=True)
    elif data == "publish_private":
        await publish_media(update, context, is_public=False)

    # ========== 预览翻页 ==========
    elif data.startswith("preview_next_"):
        parts = data.split("_")
        album_id = int(parts[2])
        idx = int(parts[3])
        await show_preview(update, context, album_id, idx, is_owner=True)

    elif data.startswith("preview_prev_"):
        parts = data.split("_")
        album_id = int(parts[2])
        idx = int(parts[3])
        await show_preview(update, context, album_id, idx, is_owner=True)

    elif data.startswith("shared_next_"):
        parts = data.split("_")
        album_id = int(parts[2])
        idx = int(parts[3])
        await show_preview(update, context, album_id, idx, is_owner=False)

    elif data.startswith("shared_prev_"):
        parts = data.split("_")
        album_id = int(parts[2])
        idx = int(parts[3])
        await show_preview(update, context, album_id, idx, is_owner=False)

    # ========== 相册操作 ==========
    elif data.startswith("view_album_"):
        album_id = int(data.split("_")[2])
        await show_album_details(update, context, album_id)

    elif data.startswith("share_album_"):
        album_id = int(data.split("_")[2])
        await show_share_options(update, context, album_id)

    elif data.startswith("access_album_"):
        album_id = int(data.split("_")[2])
        await show_access_logs(update, context, album_id)

    elif data.startswith("settings_album_"):
        album_id = int(data.split("_")[2])
        await show_album_settings(update, context, album_id)

    elif data.startswith("rename_album_"):
        album_id = int(data.split("_")[2])
        await start_rename_album(update, context, album_id)

    elif data.startswith("delete_album_"):
        album_id = int(data.split("_")[2])
        await confirm_delete_album(update, context, album_id)

    elif data.startswith("confirm_delete_"):
        album_id = int(data.split("_")[2])
        await execute_delete_album(update, context, album_id)

    elif data.startswith("delete_media_"):
        parts = data.split("_")
        album_id = int(parts[2])
        media_id = int(parts[3])
        idx = int(parts[4])
        await confirm_delete_media(update, context, album_id, media_id, idx)

    elif data.startswith("confirm_del_media_"):
        parts = data.split("_")
        album_id = int(parts[3])
        media_id = int(parts[4])
        await execute_delete_media(update, context, album_id, media_id)

    elif data == "back_to_albums":
        await show_albums_list(update, context)

    # ========== 权限设置 ==========
    elif data.startswith("set_limit_"):
        parts = data.split("_")
        album_id = int(parts[2])
        limit_type = parts[3]
        await start_set_limit(update, context, album_id, limit_type)

    elif data.startswith("set_auto_delete_"):
        album_id = int(data.split("_")[3])
        context.user_data["waiting_for"] = f"set_auto_delete_{album_id}"
        album = db.get_album(album_id)
        current = album.get("auto_delete_seconds", 600)
        current_text = f"{current}秒" if current > 0 else "不自动删除"
        await query.edit_message_text(
            f"🕐 设置自动删除 - {album['name']}\n\n当前: {current_text}\n\n请输入自动删除秒数（0=不删除，600=10分钟，3600=1小时）：",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "« 取消", callback_data=f"settings_album_{album_id}"
                        )
                    ]
                ]
            ),
        )

    elif data.startswith("toggle_protect_"):
        album_id = int(data.split("_")[2])
        album = db.get_album(album_id)
        current = album.get("protect_content", 1)
        new_value = 0 if current else 1
        db.update_album_settings(album_id, protect_content=new_value)
        status = "开启" if new_value else "关闭"
        await query.answer(f"✅ 内容保护已{status}", show_alert=True)
        await show_album_settings(update, context, album_id)

    elif data.startswith("toggle_download_"):
        album_id = int(data.split("_")[2])
        album = db.get_album(album_id)
        current = album.get("allow_download", 0)
        new_value = 1 if current == 0 else 0
        db.update_album_settings(album_id, allow_download=new_value)
        status = "允许" if new_value else "禁止"
        await query.answer(f"✅ 下载权限已设为{status}", show_alert=True)
        await show_album_settings(update, context, album_id)

    elif data.startswith("toggle_download_"):
        album_id = int(data.split("_")[2])
        album = db.get_album(album_id)
        current = album.get("allow_download", 0)
        new_value = 1 if current == 0 else 0
        db.update_album_settings(album_id, allow_download=new_value)
        status = "允许" if new_value else "禁止"
        await query.answer(f"✅ 下载权限已{status}", show_alert=True)
        await show_album_settings(update, context, album_id)

    # ========== 黑名单 ==========
    elif data.startswith("block_user_"):
        parts = data.split("_")
        album_id = int(parts[2])
        blocked_user_id = int(parts[3])
        await block_user_from_album(update, context, album_id, blocked_user_id)

    # ========== 审核 ==========
    elif data.startswith("review_approve_"):
        review_id = int(data.split("_")[2])
        await approve_review(update, context, review_id)

    elif data.startswith("review_reject_"):
        review_id = int(data.split("_")[2])
        await reject_review(update, context, review_id)

    # ========== 管理员 ==========
    elif data == "admin_menu":
        await show_admin_menu(update, context)

    elif data == "admin_stats":
        await show_admin_stats(update, context)

    elif data == "admin_users":
        await show_admin_users(update, context)

    elif data == "admin_pending":
        await show_admin_pending(update, context)

    elif data == "admin_settings":
        await show_admin_settings(update, context)

    elif data == "admin_broadcast":
        await start_broadcast(update, context)

    elif data == "admin_maintenance":
        await show_admin_maintenance(update, context)

    elif data.startswith("set_public_channel_"):
        await set_public_channel(update, context)

    elif data.startswith("set_private_group_"):
        await set_private_group(update, context)

    elif data.startswith("approve_review_"):
        review_id = int(data.split("_")[2])
        await approve_review(update, context, review_id)

    elif data.startswith("reject_review_"):
        review_id = int(data.split("_")[2])
        await reject_review(update, context, review_id)


async def process_batch_save(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """批量保存媒体到相册"""
    query = update.callback_query
    user = update.effective_user

    media_list = context.user_data.get("pending_media_list", [])
    if not media_list:
        await query.answer("❌ 没有待处理的媒体", show_alert=True)
        return

    album = db.get_album(album_id)

    # 显示批量公开/保存选择
    keyboard = [
        [
            InlineKeyboardButton(
                "📢 全部公开到频道（需审核）", callback_data="batch_publish_public"
            )
        ],
        [InlineKeyboardButton("🔒 全部仅保存", callback_data="batch_publish_private")],
    ]

    await query.edit_message_text(
        f"✅ 将保存 {len(media_list)} 个文件到相册: {album['name']}\n\n"
        f"是否公开到频道？\n\n"
        f"注意：公开内容需要管理员审核后才会显示。",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # 保存选择的相册ID
    context.user_data["batch_album_id"] = album_id


async def publish_batch_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, is_public: bool
):
    """批量发布媒体"""
    query = update.callback_query
    user = update.effective_user

    media_list = context.user_data.get("pending_media_list", [])
    album_id = context.user_data.get("batch_album_id")

    if not media_list or not album_id:
        await query.edit_message_text(
            "❌ 会话已过期，请重新上传",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]
            ),
        )
        return

    # 批量保存
    saved_count = 0
    public_count = 0

    for pending in media_list:
        # 保存到数据库
        media_id = db.add_media(
            album_id=album_id,
            user_id=user.id,
            file_id=pending["file_id"],
            file_type=pending["file_type"],
            caption=pending["caption"],
            private_message_id=pending["private_message_id"],
        )
        saved_count += 1

        # 如果用户选择公开，发送到私密群组等待审核
        if is_public:
            try:
                # 创建审核记录
                review_id = db.add_pending_review(
                    media_id=media_id,
                    user_id=user.id,
                    album_id=album_id,
                    file_id=pending["file_id"],
                    file_type=pending["file_type"],
                    caption=pending["caption"],
                    private_message_id=pending["private_message_id"],
                )

                # 发送审核请求到私密群组
                user_info = get_user_info(user)
                caption_text = pending["caption"] or ""

                review_caption = f"""📝 审核请求 #{review_id}

👤 用户: {user_info}
🆔 用户ID: {user.id}
📁 相册: {db.get_album(album_id)["name"]}
💬 描述: {caption_text if caption_text else "无"}

⏳ 等待管理员审核..."""

                # 发送媒体到私密群组
                if pending["file_type"] == "photo":
                    review_msg = await context.bot.send_photo(
                        chat_id=config.PRIVATE_GROUP_ID,
                        photo=pending["file_id"],
                        caption=review_caption,
                    )
                elif pending["file_type"] == "video":
                    review_msg = await context.bot.send_video(
                        chat_id=config.PRIVATE_GROUP_ID,
                        video=pending["file_id"],
                        caption=review_caption,
                    )
                else:
                    review_msg = await context.bot.send_document(
                        chat_id=config.PRIVATE_GROUP_ID,
                        document=pending["file_id"],
                        caption=review_caption,
                    )

                # 添加审核按钮
                review_keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ 同意", callback_data=f"review_approve_{review_id}"
                            ),
                            InlineKeyboardButton(
                                "❌ 拒绝", callback_data=f"review_reject_{review_id}"
                            ),
                        ]
                    ]
                )

                await context.bot.send_message(
                    chat_id=config.PRIVATE_GROUP_ID,
                    text=f"⚡ 请审核上述媒体",
                    reply_markup=review_keyboard,
                    reply_to_message_id=review_msg.message_id,
                )

                # 保存审核消息ID
                db.update_review_message_id(review_id, review_msg.message_id)
                public_count += 1

            except Exception as e:
                logger.error(f"批量提交审核时出错: {e}")

    # 清理临时数据
    context.user_data.pop("pending_media_list", None)
    context.user_data.pop("batch_album_id", None)

    # 显示结果
    if is_public:
        text = f"✅ 已保存 {saved_count} 个文件到相册！\n⏳ 其中 {public_count} 个已提交审核。"
    else:
        text = f"✅ 已保存 {saved_count} 个文件到私有相册！"

    await query.edit_message_text(text, reply_markup=get_main_menu_keyboard())


async def ask_public_or_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """询问用户是否公开到频道"""
    query = update.callback_query
    album_id = context.user_data.get("selected_album_id")
    album = db.get_album(album_id)

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 公开到频道（需审核）", callback_data="publish_public"
            )
        ],
        [InlineKeyboardButton("🔒 仅保存", callback_data="publish_private")],
    ]

    await query.edit_message_text(
        f"✅ 将保存到相册: {album['name']}\n\n是否公开到频道？\n\n注意：公开内容需要管理员审核后才会显示。",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def publish_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, is_public: bool
):
    """发布媒体"""
    query = update.callback_query
    user = update.effective_user

    pending = context.user_data.get("pending_media")
    if not pending:
        await query.edit_message_text(
            "❌ 会话已过期，请重新上传",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]
            ),
        )
        return

    # 获取选中的相册（如果没有选择则使用默认相册）
    album_id = context.user_data.get("selected_album_id") or db.get_default_album(
        user.id
    )

    # 保存到数据库
    media_id = db.add_media(
        album_id=album_id,
        user_id=user.id,
        file_id=pending["file_id"],
        file_type=pending["file_type"],
        caption=pending["caption"],
        private_message_id=pending["private_message_id"],
    )

    # 如果用户选择公开，发送到私密群组等待审核
    if is_public:
        try:
            # 创建审核记录
            review_id = db.add_pending_review(
                media_id=media_id,
                user_id=user.id,
                album_id=album_id,
                file_id=pending["file_id"],
                file_type=pending["file_type"],
                caption=pending["caption"],
                private_message_id=pending["private_message_id"],
            )

            # 发送审核请求到私密群组
            user_info = get_user_info(user)
            caption_text = pending["caption"] or ""

            review_caption = f"""📝 审核请求 #{review_id}

👤 用户: {user_info}
🆔 用户ID: {user.id}
📁 相册: {db.get_album(album_id)["name"]}
💬 描述: {caption_text if caption_text else "无"}

⏳ 等待管理员审核..."""

            # 发送媒体到私密群组（供管理员预览）
            if pending["file_type"] == "photo":
                review_msg = await context.bot.send_photo(
                    chat_id=config.PRIVATE_GROUP_ID,
                    photo=pending["file_id"],
                    caption=review_caption,
                )
            elif pending["file_type"] == "video":
                review_msg = await context.bot.send_video(
                    chat_id=config.PRIVATE_GROUP_ID,
                    video=pending["file_id"],
                    caption=review_caption,
                )
            else:
                review_msg = await context.bot.send_document(
                    chat_id=config.PRIVATE_GROUP_ID,
                    document=pending["file_id"],
                    caption=review_caption,
                )

            # 添加审核按钮
            review_keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ 同意", callback_data=f"review_approve_{review_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ 拒绝", callback_data=f"review_reject_{review_id}"
                        ),
                    ]
                ]
            )

            await context.bot.send_message(
                chat_id=config.PRIVATE_GROUP_ID,
                text=f"⚡ 请审核上述媒体",
                reply_markup=review_keyboard,
                reply_to_message_id=review_msg.message_id,
            )

            # 保存审核消息ID
            db.update_review_message_id(review_id, review_msg.message_id)

            await query.edit_message_text(
                "⏳ 已提交审核！\n\n管理员审核通过后，内容将显示在公开频道。",
                reply_markup=get_main_menu_keyboard(),
            )

        except Exception as e:
            logger.error(f"提交审核时出错: {e}")
            await query.edit_message_text(
                "✅ 已保存到私有相册！\n❌ 提交审核失败",
                reply_markup=get_main_menu_keyboard(),
            )
    else:
        await query.edit_message_text(
            "✅ 已保存到私有相册！", reply_markup=get_main_menu_keyboard()
        )

    # 清理临时数据
    context.user_data.pop("pending_media", None)
    context.user_data.pop("selected_album_id", None)


# ========== 相册管理 ==========


async def show_albums_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示相册列表"""
    query = update.callback_query
    user = update.effective_user

    albums = db.get_user_albums(user.id)

    if not albums:
        keyboard = [
            [InlineKeyboardButton("➕ 创建相册", callback_data="menu_create")],
            [InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")],
        ]
        try:
            await query.edit_message_text(
                "📭 你还没有相册\n\n创建一个来开始整理你的媒体文件吧！",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except:
            # 如果编辑失败（当前是媒体消息），删除后发送新消息
            try:
                await query.message.delete()
            except:
                pass
            await context.bot.send_message(
                chat_id=user.id,
                text="📭 你还没有相册\n\n创建一个来开始整理你的媒体文件吧！",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        return

    keyboard = []
    for album in albums:
        media_count = len(db.get_album_media(album["album_id"]))
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📂 {album['name']} ({media_count})",
                    callback_data=f"view_album_{album['album_id']}",
                )
            ]
        )

    keyboard.append(
        [InlineKeyboardButton("➕ 创建新相册", callback_data="menu_create")]
    )
    keyboard.append([InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")])

    try:
        await query.edit_message_text(
            f"📁 我的相册 (共{len(albums)}个)\n\n点击相册查看详情",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except:
        # 如果编辑失败（当前是媒体消息），删除后发送新消息
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=user.id,
            text=f"📁 我的相册 (共{len(albums)}个)\n\n点击相册查看详情",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_album_details(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """显示相册详情 - 带媒体预览"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)
    if not album:
        await query.edit_message_text(
            "❌ 相册不存在",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回", callback_data="menu_albums")]]
            ),
        )
        return

    if album["owner_id"] != user.id and not is_admin(user.id):
        await query.edit_message_text(
            "❌ 无权访问此相册",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回", callback_data="menu_albums")]]
            ),
        )
        return

    media = db.get_album_media(album_id)
    viewer_count = db.get_unique_viewers_count(album_id)

    # 显示第一个媒体作为预览
    if media:
        current_idx = 0
        current_media = media[0]
        total = len(media)

        caption = f"""📂 {album["name"]} ({current_idx + 1}/{total})

👥 访问人数: {viewer_count}
⚙️ 人数限制: {album["max_viewers"] if album["max_viewers"] > 0 else "无限制"}
⏰ 有效期: {f"{album['expiry_hours']}小时" if album["expiry_hours"] > 0 else "永久"}"""

        # 构建导航按钮
        keyboard = []
        nav_buttons = []

        if total > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    "▶️ 下一张", callback_data=f"preview_next_{album_id}_1"
                )
            )

        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        "🗑️ 删除此媒体",
                        callback_data=f"delete_media_{album_id}_{current_media['media_id']}_{current_idx}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔗 分享相册", callback_data=f"share_album_{album_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📋 访问日志", callback_data=f"access_album_{album_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⚙️ 权限设置", callback_data=f"settings_album_{album_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✏️ 重命名", callback_data=f"rename_album_{album_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🗑️ 删除相册", callback_data=f"delete_album_{album_id}"
                    )
                ],
                [InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")],
            ]
        )

        # 删除原消息，发送媒体
        try:
            await query.message.delete()
        except:
            pass

        try:
            if current_media["file_type"] == "photo":
                await context.bot.send_photo(
                    chat_id=user.id,
                    photo=current_media["file_id"],
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            elif current_media["file_type"] == "video":
                await context.bot.send_video(
                    chat_id=user.id,
                    video=current_media["file_id"],
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=current_media["file_id"],
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception as e:
            logger.error(f"发送预览失败: {e}")
            await context.bot.send_message(
                chat_id=user.id,
                text=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    else:
        # 相册为空
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔗 分享相册", callback_data=f"share_album_{album_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "⚙️ 权限设置", callback_data=f"settings_album_{album_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑️ 删除相册", callback_data=f"delete_album_{album_id}"
                )
            ],
            [InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")],
        ]

        await query.edit_message_text(
            f"📂 {album['name']}\n\n📭 此相册为空\n\n👥 访问人数: {viewer_count}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_preview(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    album_id: int,
    idx: int,
    is_owner: bool = False,
):
    """显示相册预览（带翻页）- 使用发送新消息方式确保稳定"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)
    media = db.get_album_media(album_id)

    if not media or idx < 0 or idx >= len(media):
        await query.answer("无效的索引", show_alert=True)
        return

    current_media = media[idx]
    total = len(media)
    viewer_count = db.get_unique_viewers_count(album_id)

    if is_owner:
        caption = f"""📂 {album["name"]} ({idx + 1}/{total})

👥 访问人数: {viewer_count}
⚙️ 人数限制: {album["max_viewers"] if album["max_viewers"] > 0 else "无限制"}
⏰ 有效期: {f"{album['expiry_hours']}小时" if album["expiry_hours"] > 0 else "永久"}"""
    else:
        allow_download = album.get("allow_download", 0)
        protect_text = (
            "🔒 此内容受保护，无法转发或保存"
            if allow_download == 0
            else "💾 允许下载和转发"
        )
        caption = f"""📂 {album["name"]} ({idx + 1}/{total})

💬 {current_media["caption"] if current_media["caption"] else "无描述"}

{protect_text}"""

    # 构建导航按钮
    keyboard = []
    nav_buttons = []

    if idx > 0:
        if is_owner:
            nav_buttons.append(
                InlineKeyboardButton(
                    "◀️ 上一张", callback_data=f"preview_prev_{album_id}_{idx - 1}"
                )
            )
        else:
            nav_buttons.append(
                InlineKeyboardButton(
                    "◀️ 上一张", callback_data=f"shared_prev_{album_id}_{idx - 1}"
                )
            )

    if idx < total - 1:
        if is_owner:
            nav_buttons.append(
                InlineKeyboardButton(
                    "▶️ 下一张", callback_data=f"preview_next_{album_id}_{idx + 1}"
                )
            )
        else:
            nav_buttons.append(
                InlineKeyboardButton(
                    "▶️ 下一张", callback_data=f"shared_next_{album_id}_{idx + 1}"
                )
            )

    if nav_buttons:
        keyboard.append(nav_buttons)

    # 所有者显示管理按钮
    if is_owner:
        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        "🗑️ 删除此媒体",
                        callback_data=f"delete_media_{album_id}_{current_media['media_id']}_{idx}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔗 分享相册", callback_data=f"share_album_{album_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📋 访问日志", callback_data=f"access_album_{album_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⚙️ 权限设置", callback_data=f"settings_album_{album_id}"
                    )
                ],
                [InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")],
            ]
        )
    else:
        # 访客显示创建自己相册的按钮
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🤖 使用机器人创建相册", url=f"https://t.me/{context.bot.username}"
                )
            ]
        )

    try:
        # 先回答回调，避免加载状态
        await query.answer()

        # 删除原消息
        try:
            await query.message.delete()
        except:
            pass

        # 获取自动删除设置（仅对访客有效）
        auto_delete_seconds = (
            album.get("auto_delete_seconds", 600) if not is_owner else 0
        )

        # 发送新媒体消息（访客浏览时根据 allow_download 设置决定是否保护）
        allow_download = album.get("allow_download", 0) if not is_owner else 1
        protect = allow_download == 0  # 禁止下载时启用保护

        sent_message = None

        if current_media["file_type"] == "photo":
            sent_message = await context.bot.send_photo(
                chat_id=user.id,
                photo=current_media["file_id"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                protect_content=protect,
            )
        elif current_media["file_type"] == "video":
            sent_message = await context.bot.send_video(
                chat_id=user.id,
                video=current_media["file_id"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                protect_content=protect,
            )
        else:
            sent_message = await context.bot.send_document(
                chat_id=user.id,
                document=current_media["file_id"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                protect_content=protect,
            )

        # 如果设置了自动删除且是访客，安排定时删除
        if auto_delete_seconds > 0 and sent_message and not is_owner:

            async def delete_message_after_delay():
                await asyncio.sleep(auto_delete_seconds)
                try:
                    await sent_message.delete()
                except Exception as e:
                    logger.warning(f"自动删除消息失败: {e}")

            # 启动后台任务删除消息
            asyncio.create_task(delete_message_after_delay())

    except Exception as e:
        logger.error(f"发送预览失败: {e}")
        await query.answer("加载失败，请重试", show_alert=True)


async def handle_waiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理等待中的用户输入"""
    # 只在私聊中响应
    if update.effective_chat.type != "private":
        return

    user = update.effective_user
    text = update.message.text

    waiting_for = context.user_data.get("waiting_for")

    if not waiting_for:
        return  # 不在等待输入状态

    if waiting_for == "album_name":
        # 创建相册
        album_id = db.create_album(user.id, text)
        context.user_data.pop("waiting_for", None)

        keyboard = [
            [
                InlineKeyboardButton(
                    "📂 查看相册", callback_data=f"view_album_{album_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 分享相册", callback_data=f"share_album_{album_id}"
                )
            ],
            [InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")],
        ]

        await update.message.reply_text(
            f"✅ 相册创建成功！\n\n📁 名称: {text}\n🆔 ID: {album_id}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif waiting_for == "new_album_for_media":
        # 创建新相册用于保存当前媒体
        album_id = db.create_album(user.id, text)
        context.user_data["selected_album_id"] = album_id
        context.user_data.pop("waiting_for", None)

        await ask_public_or_private(update, context)

    elif waiting_for == "album_rename":
        # 重命名相册
        album_id = context.user_data.get("rename_album_id")
        if album_id:
            db.rename_album(album_id, text)
            context.user_data.pop("waiting_for", None)
            context.user_data.pop("rename_album_id", None)

            keyboard = [
                [
                    InlineKeyboardButton(
                        "📂 查看相册", callback_data=f"view_album_{album_id}"
                    )
                ],
                [InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")],
            ]

            await update.message.reply_text(
                f"✅ 相册已重命名为: {text}",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    # ========== 批量创建相册 ==========
    elif waiting_for == "batch_new_album":
        # 批量创建新相册
        album_id = db.create_album(user.id, text)
        context.user_data.pop("waiting_for", None)

        # 批量保存到新建的相册
        await process_batch_save(update, context, album_id)

    # ========== 设置自动删除时间 ==========
    elif waiting_for and waiting_for.startswith("set_auto_delete_"):
        album_id = int(waiting_for.split("_")[3])
        try:
            seconds = int(text)
            if seconds < 0:
                seconds = 0
            db.update_album_settings(album_id, auto_delete_seconds=seconds)
            context.user_data.pop("waiting_for", None)
            await update.message.reply_text(
                f"✅ 自动删除时间已设置为: {seconds}秒"
                if seconds > 0
                else "✅ 已关闭自动删除",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "« 返回设置", callback_data=f"settings_album_{album_id}"
                            )
                        ]
                    ]
                ),
            )
        except ValueError:
            await update.message.reply_text("❌ 请输入有效的数字（秒数）")

    # ========== 管理员功能 ==========
    elif waiting_for == "set_public_channel":
        # 设置公开频道ID
        if not is_admin(user.id):
            return

        try:
            new_channel_id = int(text)
            # 更新配置文件
            import re

            with open("config.py", "r") as f:
                content = f.read()
            content = re.sub(
                r"PUBLIC_CHANNEL_ID = -?\d+",
                f"PUBLIC_CHANNEL_ID = {new_channel_id}",
                content,
            )
            with open("config.py", "w") as f:
                f.write(content)

            # 更新内存中的配置
            config.PUBLIC_CHANNEL_ID = new_channel_id

            context.user_data.pop("waiting_for", None)

            keyboard = [
                [InlineKeyboardButton("« 返回系统设置", callback_data="admin_settings")]
            ]
            await update.message.reply_text(
                f"✅ 公开频道ID已更新为: {new_channel_id}\n\n注意：此更改在内存中已生效，重启后将永久保存。",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except ValueError:
            await update.message.reply_text(
                "❌ 无效的频道ID，请输入数字格式（如: -1001234567890）"
            )

    elif waiting_for == "set_private_group":
        # 设置私密群组ID
        if not is_admin(user.id):
            return

        try:
            new_group_id = int(text)
            # 更新配置文件
            import re

            with open("config.py", "r") as f:
                content = f.read()
            content = re.sub(
                r"PRIVATE_GROUP_ID = -?\d+",
                f"PRIVATE_GROUP_ID = {new_group_id}",
                content,
            )
            with open("config.py", "w") as f:
                f.write(content)

            # 更新内存中的配置
            config.PRIVATE_GROUP_ID = new_group_id

            context.user_data.pop("waiting_for", None)

            keyboard = [
                [InlineKeyboardButton("« 返回系统设置", callback_data="admin_settings")]
            ]
            await update.message.reply_text(
                f"✅ 私密群组ID已更新为: {new_group_id}\n\n注意：此更改在内存中已生效，重启后将永久保存。",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except ValueError:
            await update.message.reply_text(
                "❌ 无效的群组ID，请输入数字格式（如: -1001234567890）"
            )

    elif waiting_for == "broadcast_message":
        # 广播消息
        if not is_admin(user.id):
            return

        context.user_data.pop("waiting_for", None)

        # 发送确认
        keyboard = [
            [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]
        ]
        await update.message.reply_text(
            "📢 开始广播...\n\n正在向所有用户发送消息...",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        # 获取所有用户并广播
        users = db.get_all_users()
        success_count = 0
        fail_count = 0

        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u["user_id"], text=f"📢 系统公告:\n\n{text}"
                )
                success_count += 1
            except Exception as e:
                logger.error(f"广播给用户 {u['user_id']} 失败: {e}")
                fail_count += 1

        # 发送结果
        await update.message.reply_text(
            f"✅ 广播完成\n\n成功: {success_count}\n失败: {fail_count}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def start_create_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始创建相册流程"""
    query = update.callback_query

    context.user_data["waiting_for"] = "album_name"

    await query.edit_message_text(
        "➕ 创建新相册\n\n请发送相册名称：",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 取消", callback_data="menu_albums")]]
        ),
    )


async def start_rename_album(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """开始重命名相册"""
    query = update.callback_query

    context.user_data["waiting_for"] = "album_rename"
    context.user_data["rename_album_id"] = album_id

    album = db.get_album(album_id)

    await query.edit_message_text(
        f"✏️ 重命名相册\n\n当前名称: {album['name']}\n\n请发送新名称：",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 取消", callback_data=f"view_album_{album_id}")]]
        ),
    )


async def confirm_delete_album(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """确认删除相册"""
    query = update.callback_query

    album = db.get_album(album_id)

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ 确认删除", callback_data=f"confirm_delete_{album_id}"
            ),
            InlineKeyboardButton("❌ 取消", callback_data=f"view_album_{album_id}"),
        ]
    ]

    await query.edit_message_text(
        f'⚠️ 确定要删除相册 "{album["name"]}" 吗？\n\n'
        f"注意：这只会删除数据库记录，不会删除Telegram上的媒体文件。",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def execute_delete_album(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """执行删除相册"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)
    if not album or album["owner_id"] != user.id:
        await query.edit_message_text(
            "❌ 删除失败",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回", callback_data="menu_albums")]]
            ),
        )
        return

    db.delete_album(album_id)
    await query.edit_message_text(
        "✅ 相册已删除",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")]]
        ),
    )


async def confirm_delete_media(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    album_id: int,
    media_id: int,
    idx: int,
):
    """确认删除单个媒体"""
    query = update.callback_query
    user = update.effective_user

    media = db.get_media_by_id(media_id)
    album = db.get_album(album_id)

    if not media or not album or album["owner_id"] != user.id:
        await query.answer("❌ 无权操作", show_alert=True)
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ 确认删除",
                callback_data=f"confirm_del_media_{album_id}_{media_id}_{idx}",
            ),
            InlineKeyboardButton(
                "❌ 取消", callback_data=f"preview_next_{album_id}_{idx}"
            ),
        ]
    ]

    try:
        await query.edit_message_caption(
            caption=f"⚠️ 确定要删除这张媒体吗？\n\n此操作不可恢复。",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except:
        # 如果编辑失败，发送新消息
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=user.id,
            text="⚠️ 确定要删除这张媒体吗？\n\n此操作不可恢复。",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def execute_delete_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int, media_id: int
):
    """执行删除单个媒体"""
    query = update.callback_query
    user = update.effective_user

    media = db.get_media_by_id(media_id)
    album = db.get_album(album_id)

    if not media or not album or album["owner_id"] != user.id:
        await query.answer("❌ 无权操作", show_alert=True)
        return

    # 删除媒体记录
    db.delete_media(media_id)

    await query.answer("✅ 已删除", show_alert=True)

    # 重新显示相册（从第0张开始）
    await show_album_details(update, context, album_id)


# ========== 分享与访问控制 ==========


async def show_share_options(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """显示分享选项"""
    query = update.callback_query
    user = update.effective_user

    # 获取或生成分享令牌
    album = db.get_album(album_id)
    if not album:
        await query.answer("❌ 相册不存在", show_alert=True)
        return

    token = album.get("share_token")
    if not token:
        token = db.generate_share_token(album_id)

    share_link = f"https://t.me/{context.bot.username}?start=album_{album_id}_{token}"

    keyboard = [
        [InlineKeyboardButton("📋 访问日志", callback_data=f"access_album_{album_id}")],
        [
            InlineKeyboardButton(
                "⚙️ 权限设置", callback_data=f"settings_album_{album_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "« 返回相册详情", callback_data=f"view_album_{album_id}"
            )
        ],
    ]

    text = f"""🔗 分享链接:

<code>{share_link}</code>

将此链接发送给好友，他们可以通过点击链接查看你的相册。

提示：长按链接可复制"""

    try:
        # 尝试编辑消息，如果失败（媒体消息）则删除后发送新消息
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        # 删除原消息并发送新消息
        try:
            await query.message.delete()
        except:
            pass

        await context.bot.send_message(
            chat_id=user.id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def show_access_logs(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """显示访问日志"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)
    if not album or (album["owner_id"] != user.id and not is_admin(user.id)):
        try:
            await query.edit_message_text("❌ 无权访问")
        except:
            await query.message.delete()
            await context.bot.send_message(chat_id=user.id, text="❌ 无权访问")
        return

    logs = db.get_access_logs(album_id)
    blacklist = db.get_blacklist(album_id)

    text = f"""📋 {album["name"]} - 访问统计

👥 总访问次数: {len(logs)}
🚫 黑名单人数: {len(blacklist)}\n"""

    keyboard = []

    if logs:
        text += "\n最近访问者:\n"
        for log in logs[:10]:
            user_info = f"@{log['username']}" if log["username"] else log["first_name"]
            text += f"• {user_info}\n"

            # 添加拉黑按钮（前5个）
            if len(keyboard) < 5:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"🚫 拉黑 {user_info[:15]}",
                            callback_data=f"block_user_{album_id}_{log['viewer_id']}",
                        )
                    ]
                )
    else:
        text += "\n📭 暂无访问记录"

    keyboard.append(
        [InlineKeyboardButton("« 返回相册详情", callback_data=f"view_album_{album_id}")]
    )

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_album_settings(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """显示相册权限设置"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)

    auto_delete = album.get("auto_delete_seconds", 600)
    protect = album.get("protect_content", 1)
    allow_download = album.get("allow_download", 0)

    text = f"""⚙️ {album["name"]} - 权限设置

当前设置:
👥 人数限制: {album["max_viewers"] if album["max_viewers"] > 0 else "无限制"}
⏰ 有效期: {f"{album['expiry_hours']}小时" if album["expiry_hours"] > 0 else "永久"}
🕐 自动删除: {f"{auto_delete}秒后" if auto_delete > 0 else "不自动删除"}
🔒 内容保护: {"✅ 已开启" if protect else "❌ 已关闭"}
💾 允许下载: {"✅ 允许" if allow_download else "❌ 禁止"}

点击下方按钮修改设置:"""

    keyboard = [
        [
            InlineKeyboardButton(
                "👥 设置人数限制", callback_data=f"set_limit_{album_id}_viewers"
            )
        ],
        [
            InlineKeyboardButton(
                "⏰ 设置有效期", callback_data=f"set_limit_{album_id}_expiry"
            )
        ],
        [
            InlineKeyboardButton(
                "🕐 设置自动删除", callback_data=f"set_auto_delete_{album_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🔒 内容保护", callback_data=f"toggle_protect_{album_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "💾 下载权限", callback_data=f"toggle_download_{album_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 解除所有限制", callback_data=f"set_limit_{album_id}_none"
            )
        ],
        [
            InlineKeyboardButton(
                "« 返回相册详情", callback_data=f"view_album_{album_id}"
            )
        ],
    ]

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def start_set_limit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int, limit_type: str
):
    """开始设置限制"""
    query = update.callback_query

    if limit_type == "none":
        # 解除限制
        db.update_album_settings(album_id, max_viewers=0, expiry_hours=0)
        await query.answer("✅ 已解除所有限制", show_alert=True)
        await show_album_settings(update, context, album_id)
        return

    album = db.get_album(album_id)

    if limit_type == "viewers":
        current = album["max_viewers"] if album["max_viewers"] > 0 else "无限制"
        text = f"👥 设置人数限制 - {album['name']}\n\n当前: {current}\n\n请输入最大查看人数（0=无限制）："
    elif limit_type == "expiry":
        current = (
            f"{album['expiry_hours']}小时" if album["expiry_hours"] > 0 else "永久"
        )
        text = f"⏰ 设置有效期 - {album['name']}\n\n当前: {current}\n\n请输入有效期小时数（0=永久）："
    elif limit_type == "auto_delete":
        current = album.get("auto_delete_seconds", 600)
        current_text = f"{current}秒" if current > 0 else "不自动删除"
        text = f"🕐 设置自动删除 - {album['name']}\n\n当前: {current_text}\n\n请输入自动删除秒数（0=不删除，600=10分钟，3600=1小时）："

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "« 取消", callback_data=f"settings_album_{album_id}"
                    )
                ]
            ]
        ),
    )


async def block_user_from_album(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    album_id: int,
    blocked_user_id: int,
):
    """将用户加入黑名单"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)
    if not album or (album["owner_id"] != user.id and not is_admin(user.id)):
        await query.answer("无权操作", show_alert=True)
        return

    db.add_to_blacklist(album_id, blocked_user_id, "手动拉黑")
    await query.answer("✅ 已加入黑名单", show_alert=True)

    # 刷新访问日志
    await show_access_logs(update, context, album_id)


# ========== 分享相册查看 ==========


async def view_shared_album(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    album_id: int,
    token: Optional[str] = None,
):
    """通过分享链接查看相册"""
    user = update.effective_user

    # 保存用户信息（无论是否订阅都先保存，这样后续可以发送通知）
    db.add_user(user.id, user.username, user.first_name, user.last_name)

    # 检查频道订阅（所有用户都需要订阅，包括通过分享链接访问的）
    if not await require_channel_membership(update, context):
        return

    album = db.get_album(album_id)
    if not album:
        await update.message.reply_text("❌ 相册不存在或已被删除")
        return

    # 验证分享令牌（如果相册有设置令牌）
    share_token = album.get("share_token")
    if share_token:
        if token != share_token:
            await update.message.reply_text("❌ 无效的分享链接")
            return
    elif token is None:
        # 旧相册没有令牌，但新链接尝试访问 - 拒绝
        await update.message.reply_text("❌ 分享链接已失效，请重新获取")
        return

    # 检查黑名单
    if db.is_blacklisted(album_id, user.id):
        await update.message.reply_text("❌ 你已被禁止访问此相册")
        return

    # 检查是否过期
    if album["expiry_hours"] > 0:
        created = datetime.fromisoformat(album["created_at"])
        expiry = created + timedelta(hours=album["expiry_hours"])
        if datetime.now() > expiry:
            await update.message.reply_text("❌ 此相册分享链接已过期")
            return

    # 检查人数限制
    if album["max_viewers"] > 0:
        viewer_count = db.get_unique_viewers_count(album_id)
        if viewer_count >= album["max_viewers"] and not db.has_user_viewed(
            album_id, user.id
        ):
            await update.message.reply_text("❌ 此相册已达到最大访问人数限制")
            return

    # 记录访问（如果不是所有者）
    if album["owner_id"] != user.id:
        db.log_access(album_id, user.id)

        # 发送通知给相册创建者
        await notify_album_owner(context, album["owner_id"], user, album["name"])

    # 显示相册预览（带翻页）
    media = db.get_album_media(album_id)

    if not media:
        await update.message.reply_text("📭 此相册为空")
        return

    # 显示第一个媒体作为预览（带内容保护，防止转发和保存）
    current_idx = 0
    current_media = media[0]
    total = len(media)

    caption = f"""📂 {album["name"]} ({current_idx + 1}/{total})

💬 {current_media["caption"] if current_media["caption"] else "无描述"}

🔒 此内容受保护，无法转发或保存"""

    # 构建导航按钮
    keyboard = []
    nav_buttons = []

    if total > 1:
        nav_buttons.append(
            InlineKeyboardButton("▶️ 下一张", callback_data=f"shared_next_{album_id}_1")
        )

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append(
        [
            InlineKeyboardButton(
                "🤖 使用机器人创建相册", url=f"https://t.me/{context.bot.username}"
            )
        ]
    )

    # 获取内容保护和自动删除设置
    # allow_download: 0=禁止(保护内容), 1=允许(不保护)
    allow_download = album.get("allow_download", 0)
    protect = allow_download == 0  # 禁止下载时启用保护
    auto_delete_seconds = album.get("auto_delete_seconds", 600)

    sent_message = None

    try:
        if current_media["file_type"] == "photo":
            sent_message = await update.message.reply_photo(
                photo=current_media["file_id"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                protect_content=protect,
            )
        elif current_media["file_type"] == "video":
            sent_message = await update.message.reply_video(
                video=current_media["file_id"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                protect_content=protect,
            )
        else:
            sent_message = await update.message.reply_document(
                document=current_media["file_id"],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                protect_content=protect,
            )

        # 如果设置了自动删除，安排定时删除
        if auto_delete_seconds > 0 and sent_message:

            async def delete_message_after_delay():
                await asyncio.sleep(auto_delete_seconds)
                try:
                    await sent_message.delete()
                except Exception as e:
                    logger.warning(f"自动删除消息失败: {e}")

            # 启动后台任务删除消息
            asyncio.create_task(delete_message_after_delay())

    except Exception as e:
        logger.error(f"发送预览失败: {e}")
        await update.message.reply_text(
            f"📂 {album['name']}\n\n📭 加载失败",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ========== 审核功能 ==========


async def approve_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    """管理员审核通过"""
    query = update.callback_query
    admin_user = update.effective_user

    # 检查是否为管理员
    if not is_admin(admin_user.id):
        await query.answer("❌ 无权操作", show_alert=True)
        return

    review = db.get_pending_review(review_id)
    if not review:
        await query.answer("❌ 审核记录不存在", show_alert=True)
        return

    if review["status"] != "pending":
        await query.answer("❌ 该审核已处理", show_alert=True)
        return

    try:
        # 发布到公开频道
        user_info = (
            f"@{review['username']}" if review["username"] else review["first_name"]
        )
        caption_text = review["caption"] or ""

        public_caption = f"""{caption_text}

👤 {user_info}

🤖 <a href="https://t.me/{context.bot.username}">使用机器人创建</a>"""

        if review["file_type"] == "photo":
            public_msg = await context.bot.send_photo(
                chat_id=config.PUBLIC_CHANNEL_ID,
                photo=review["file_id"],
                caption=public_caption,
                parse_mode=ParseMode.HTML,
            )
        elif review["file_type"] == "video":
            public_msg = await context.bot.send_video(
                chat_id=config.PUBLIC_CHANNEL_ID,
                video=review["file_id"],
                caption=public_caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            public_msg = await context.bot.send_document(
                chat_id=config.PUBLIC_CHANNEL_ID,
                document=review["file_id"],
                caption=public_caption,
                parse_mode=ParseMode.HTML,
            )

        # 更新媒体记录
        db.update_public_message_id(review["media_id"], public_msg.message_id)

        # 更新审核状态
        db.update_review_status(review_id, "approved", admin_user.id)

        # 通知用户
        try:
            await context.bot.send_message(
                chat_id=review["user_id"],
                text="✅ 你的媒体已通过审核并发布到公开频道！",
            )
        except:
            pass

        # 更新审核消息
        try:
            await query.edit_message_text(
                f"✅ 审核通过\n\n管理员: {get_user_info(admin_user)}\n用户: {user_info}",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "« 返回待审核列表", callback_data="admin_pending"
                            )
                        ]
                    ]
                ),
            )
        except:
            await query.answer("✅ 已通过", show_alert=True)

    except Exception as e:
        logger.error(f"审核通过时出错: {e}")
        await query.answer(f"❌ 发布失败: {e}", show_alert=True)


async def reject_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    """管理员审核拒绝"""
    query = update.callback_query
    admin_user = update.effective_user

    # 检查是否为管理员
    if not is_admin(admin_user.id):
        await query.answer("❌ 无权操作", show_alert=True)
        return

    review = db.get_pending_review(review_id)
    if not review:
        await query.answer("❌ 审核记录不存在", show_alert=True)
        return

    if review["status"] != "pending":
        await query.answer("❌ 该审核已处理", show_alert=True)
        return

    try:
        # 更新审核状态
        db.update_review_status(review_id, "rejected", admin_user.id)

        # 通知用户
        user_info = (
            f"@{review['username']}" if review["username"] else review["first_name"]
        )
        try:
            await context.bot.send_message(
                chat_id=review["user_id"],
                text="❌ 你的媒体未通过审核，未发布到公开频道。",
            )
        except:
            pass

        # 更新审核消息
        try:
            await query.edit_message_text(
                f"❌ 审核拒绝\n\n管理员: {get_user_info(admin_user)}\n用户: {user_info}",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "« 返回待审核列表", callback_data="admin_pending"
                            )
                        ]
                    ]
                ),
            )
        except:
            await query.answer("❌ 已拒绝", show_alert=True)

    except Exception as e:
        logger.error(f"审核拒绝时出错: {e}")
        await query.answer(f"❌ 操作失败: {e}", show_alert=True)


# ========== 统计与帮助 ==========


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    query = update.callback_query
    user = update.effective_user
    stats_data = db.get_stats()

    # 获取待审核数量（仅管理员）
    pending_count = db.get_pending_reviews_count() if is_admin(user.id) else 0

    text = f"""📊 系统统计

👥 总用户数: {stats_data["total_users"]}
📁 总相册数: {stats_data["total_albums"]}
🖼️ 总媒体数: {stats_data["total_media"]}
👀 总访问次数: {stats_data["total_accesses"]}"""

    if is_admin(user.id) and pending_count > 0:
        text += f"\n⏳ 待审核: {pending_count}"

    keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]

    # 管理员额外显示按钮
    if is_admin(user.id):
        keyboard.insert(
            0, [InlineKeyboardButton("👑 管理员菜单", callback_data="admin_menu")]
        )

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        # 如果编辑失败（当前是媒体消息），删除后发送新消息
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示帮助信息"""
    query = update.callback_query
    user = update.effective_user

    help_text = """📖 使用帮助

📝 如何上传媒体：
直接发送图片、视频或文件即可

📁 如何创建相册：
点击"➕ 创建相册"按钮，输入相册名称

🔗 如何分享相册：
1. 进入"我的相册"
2. 选择要分享的相册
3. 点击"🔗 分享相册"
4. 复制链接发送给好友

⚙️ 权限设置：
• 人数限制：设置最大查看人数
• 有效期：设置链接过期时间
• 黑名单：在访问日志中拉黑用户

👀 查看访问记录：
在相册详情中点击查看日志按钮"""

    keyboard = [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]

    try:
        await query.edit_message_text(
            help_text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except:
        # 如果编辑失败（当前是媒体消息），删除后发送新消息
        try:
            await query.message.delete()
        except:
            pass
        await context.bot.send_message(
            chat_id=user.id, text=help_text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ========== 管理员功能 ==========


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示管理员菜单"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    pending_count = db.get_pending_reviews_count()

    keyboard = [
        [
            InlineKeyboardButton(
                f"📋 待审核 ({pending_count})", callback_data="admin_pending"
            )
        ],
        [InlineKeyboardButton("📊 全局统计", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 用户管理", callback_data="admin_users")],
        [InlineKeyboardButton("⚙️ 系统设置", callback_data="admin_settings")],
        [InlineKeyboardButton("📢 广播消息", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔧 系统维护", callback_data="admin_maintenance")],
        [InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")],
    ]

    await query.edit_message_text(
        "👑 管理员菜单\n\n选择功能：", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示管理员统计"""
    query = update.callback_query
    user = update.effective_user
    stats_data = db.get_stats()
    pending_count = db.get_pending_reviews_count()

    text = f"""👑 管理员 - 全局统计

👥 总用户数: {stats_data["total_users"]}
📁 总相册数: {stats_data["total_albums"]}
🖼️ 总媒体数: {stats_data["total_media"]}
👀 总访问次数: {stats_data["total_accesses"]}
⏳ 待审核: {pending_count}

📢 公开频道: {config.PUBLIC_CHANNEL_ID}
🔒 私密群组: {config.PRIVATE_GROUP_ID}"""

    keyboard = [[InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示用户管理"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    users = db.get_all_users()

    text = f"""👥 用户管理

总用户数: {len(users)}

最近注册用户:"""

    for u in users[:10]:
        user_info = f"@{u['username']}" if u["username"] else f"{u['first_name']}"
        text += f"\n• {user_info} (ID: {u['user_id']})"

    keyboard = [[InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示待审核列表"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    reviews = db.get_all_pending_reviews()

    if not reviews:
        await query.edit_message_text(
            "📭 没有待审核的媒体",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]]
            ),
        )
        return

    text = f"""📋 待审核列表

共 {len(reviews)} 条待审核"""

    keyboard = []

    for review in reviews[:10]:
        user_info = (
            f"@{review['username']}" if review["username"] else review["first_name"]
        )
        text += f"\n\n📝 #{review['review_id']}\n"
        text += f"👤 {user_info}\n"
        text += f"📁 {review['album_name']}\n"
        text += f"🕐 {review['created_at'][:16]}"

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"✅ 通过 #{review['review_id']}",
                    callback_data=f"approve_review_{review['review_id']}",
                ),
                InlineKeyboardButton(
                    f"❌ 拒绝 #{review['review_id']}",
                    callback_data=f"reject_review_{review['review_id']}",
                ),
            ]
        )

    keyboard.append(
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示系统设置"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    text = f"""⚙️ 系统设置

当前配置:
📢 公开频道ID: {config.PUBLIC_CHANNEL_ID}
🔒 私密群组ID: {config.PRIVATE_GROUP_ID}

注意：修改频道/群组ID后需要重启机器人才能生效。"""

    keyboard = [
        [InlineKeyboardButton("📢 修改公开频道", callback_data="set_public_channel")],
        [InlineKeyboardButton("🔒 修改私密群组", callback_data="set_private_group")],
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def set_public_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """设置公开频道ID"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    context.user_data["waiting_for"] = "set_public_channel"

    await query.edit_message_text(
        "📢 修改公开频道ID\n\n"
        f"当前ID: {config.PUBLIC_CHANNEL_ID}\n\n"
        "请输入新的频道ID（格式如: -1001234567890）：\n\n"
        "注意：机器人需要是新频道的管理员，且有发送消息权限。",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 取消", callback_data="admin_settings")]]
        ),
    )


async def set_private_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """设置私密群组ID"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    context.user_data["waiting_for"] = "set_private_group"

    await query.edit_message_text(
        "🔒 修改私密群组ID\n\n"
        f"当前ID: {config.PRIVATE_GROUP_ID}\n\n"
        "请输入新的群组ID（格式如: -1001234567890）：\n\n"
        "注意：机器人需要是新群组的管理员，且有发送消息权限。",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 取消", callback_data="admin_settings")]]
        ),
    )


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始广播消息"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    context.user_data["waiting_for"] = "broadcast_message"

    await query.edit_message_text(
        "📢 广播消息\n\n"
        "请输入要广播的消息内容：\n\n"
        "⚠️ 警告：此操作会向所有用户发送消息，请谨慎使用！",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 取消", callback_data="admin_menu")]]
        ),
    )


async def show_admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示系统维护选项"""
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    text = """🔧 系统维护

请选择维护操作：

⚠️ 注意：以下操作不可恢复，请谨慎使用！"""

    keyboard = [
        [
            InlineKeyboardButton(
                "🧹 清理已删除相册的媒体记录", callback_data="cleanup_orphan_media"
            )
        ],
        [InlineKeyboardButton("📊 数据库状态", callback_data="db_status")],
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ========== 错误处理 ==========


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理"""
    logger.error(f"更新 {update} 导致错误: {context.error}")


# ========== 主程序 ==========


def main():
    """启动机器人"""
    # 验证配置
    errors = config.validate_config()
    if errors:
        print("❌ 配置错误:")
        for e in errors:
            print(f"  - {e}")
        print("\n请设置以下环境变量:")
        print("  BOT_TOKEN, PUBLIC_CHANNEL_ID, PRIVATE_GROUP_ID, ADMIN_USER_ID")
        return

    application = Application.builder().token(config.BOT_TOKEN).build()

    # 命令处理器（只保留start）
    application.add_handler(CommandHandler("start", start))

    # 回调处理器（所有按钮交互）
    application.add_handler(CallbackQueryHandler(button_callback))

    # 媒体处理器
    application.add_handler(
        MessageHandler(
            filters.PHOTO
            | filters.VIDEO
            | filters.Document.ALL
            | filters.AUDIO
            | filters.VOICE,
            handle_media,
        )
    )

    # 等待输入处理器（创建相册、重命名等）
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_waiting_input)
    )

    # 错误处理器
    application.add_error_handler(error_handler)

    logger.info("机器人启动中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
