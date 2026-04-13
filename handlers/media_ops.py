"""Media operations - preview, delete, and related functions."""

import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import db
from utils import task_manager

logger = logging.getLogger(__name__)


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
        except Exception as e:
            logger.warning(f"删除原消息失败: {e}")

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

            task_manager.spawn(
                delete_message_after_delay(),
                name=f"delete_msg_{sent_message.message_id}",
            )

    except Exception as e:
        logger.error(f"发送预览失败: {e}", exc_info=True)
        await query.answer("加载失败，请重试", show_alert=True)


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
    except Exception as e:
        logger.warning(f"编辑删除确认消息失败: {e}")
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
        await context.bot.send_message(
            chat_id=user.id,
            text="⚠️ 确定要删除这张媒体吗？\n\n此操作不可恢复。",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def execute_delete_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int, media_id: int
):
    """执行删除单个媒体"""
    from .albums import show_album_details

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


async def delete_message_after_delay(sent_message, auto_delete_seconds: int):
    """定时删除消息（独立函数版本）"""
    await asyncio.sleep(auto_delete_seconds)
    try:
        await sent_message.delete()
    except Exception as e:
        logger.warning(f"自动删除消息失败: {e}")
