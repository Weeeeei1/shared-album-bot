import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from database import db
from .core import (
    validate_album_name,
    get_user_info,
    is_admin,
    get_main_menu_keyboard,
)

# Basic logger for this module
logger = logging.getLogger(__name__)


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
        except Exception as e:
            logger.warning(f"编辑空相册列表消息失败: {e}")
            # 如果编辑失败（当前是媒体消息），删除后发送新消息
            try:
                await query.message.delete()
            except Exception as e2:
                logger.warning(f"删除消息失败: {e2}")
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
    except Exception as e:
        logger.warning(f"编辑相册列表消息失败: {e}")
        # 如果编辑失败（当前是媒体消息），删除后发送新消息
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
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
        except Exception as e:
            logger.warning(f"删除原消息失败: {e}")

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
            logger.error(f"发送预览失败: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=user.id,
                text=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    else:
        # 相册为空
        keyboard = [
            [InlineKeyboardButton("➕ 创建新相册", callback_data="menu_create")],
            [InlineKeyboardButton("« 返回相册列表", callback_data="menu_albums")],
        ]

        await query.edit_message_text(
            f"📂 {album['name']}\n\n📭 此相册为空\n\n👥 访问人数: {viewer_count}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


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
        logger.warning(f"编辑分享消息失败: {e}")
        # 删除原消息并发送新消息
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
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
        except Exception as e:
            logger.warning(f"编辑无权访问消息失败: {e}")
            try:
                await query.message.delete()
            except Exception as e2:
                logger.warning(f"删除消息失败: {e2}")
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
    except Exception as e:
        logger.warning(f"编辑访问日志消息失败: {e}")
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
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
    except Exception as e:
        logger.warning(f"编辑相册设置消息失败: {e}")
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
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


# -------------- 新增的相册操作函数 --------------


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
