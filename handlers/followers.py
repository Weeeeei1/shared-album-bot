import asyncio
import logging

logger = logging.getLogger(__name__)
from telegram.ext import ContextTypes
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import config
from database import db
from utils import task_manager
from .core import get_user_info


async def follow_publisher(update, context, album_id):
    """关注相册发布者"""
    query = update.callback_query
    user = update.effective_user
    album = db.get_album(album_id)
    publisher_id = album["owner_id"]
    if publisher_id == user.id:
        await query.answer("不能关注自己", show_alert=True)
        return
    if db.is_following(user.id, album_id):
        await query.answer("已经关注过了", show_alert=True)
        return
    db.add_follower(user.id, publisher_id, album_id)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔔 已关注", callback_data=f"unfollow_{album_id}")],
            [
                InlineKeyboardButton(
                    "📂 查看完整相册", callback_data=f"full_album_{album_id}"
                )
            ],
        ]
    )
    try:
        await query.answer("✅ 关注成功！有新内容时会收到通知", show_alert=True)
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


async def unfollow_publisher(update, context, album_id):
    """取消关注"""
    query = update.callback_query
    user = update.effective_user
    db.remove_follower(user.id, album_id)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ 关注发布者", callback_data=f"follow_{album_id}")],
            [
                InlineKeyboardButton(
                    "📂 查看完整相册", callback_data=f"full_album_{album_id}"
                )
            ],
        ]
    )
    await query.answer("已取消关注")
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


async def view_new_content(update, context, album_id):
    """查看新内容（自上次查看后新增的）"""
    query = update.callback_query
    user = update.effective_user
    last_viewed = db.get_last_viewed(user.id, album_id)
    last_media_id = last_viewed["last_viewed_media_id"] if last_viewed else None
    new_media = db.get_new_media_since(album_id, last_media_id)
    if not new_media:
        await query.answer("暂无新内容", show_alert=True)
        return
    await query.answer()
    display_media = new_media[:10]
    has_more = len(new_media) > 10
    album = db.get_album(album_id)
    total = len(display_media)
    protect = album.get("protect_content", 0) == 1
    await context.bot.send_message(
        chat_id=user.id,
        text=f"🆕 新内容 ({total} 条)"
        + (f" 还有{len(new_media) - 10}条..." if has_more else ""),
    )
    for idx, media_item in enumerate(display_media):
        caption = f"🆕 {idx + 1}/{total}\n\n💬 {media_item['caption'] or '无描述'}"
        try:
            if media_item["file_type"] == "photo":
                await context.bot.send_photo(
                    chat_id=user.id,
                    photo=media_item["file_id"],
                    caption=caption,
                    protect_content=protect,
                )
            elif media_item["file_type"] == "video":
                await context.bot.send_video(
                    chat_id=user.id,
                    video=media_item["file_id"],
                    caption=caption,
                    protect_content=protect,
                )
            else:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=media_item["file_id"],
                    caption=caption,
                    protect_content=protect,
                )
        except Exception as e:
            # 发送失败继续并记录日志
            logger.warning(f"发送新内容失败: {e}")
        await asyncio.sleep(0.3)
    if display_media:
        last_item = display_media[-1]
        db.update_last_viewed(user.id, album_id, last_item["media_id"])
    keyboard_buttons = []
    if has_more:
        keyboard_buttons.append(
            [
                InlineKeyboardButton(
                    f"查看完整相册还有{len(new_media) - 10}条",
                    callback_data=f"full_album_{album_id}",
                )
            ]
        )
    keyboard_buttons.append(
        [InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]
    )
    await context.bot.send_message(
        chat_id=user.id,
        text="👆 以上是自上次查看后的新内容",
        reply_markup=InlineKeyboardMarkup(keyboard_buttons),
    )


async def view_full_album(update, context, album_id):
    """查看完整相册"""
    query = update.callback_query
    user = update.effective_user
    album = db.get_album(album_id)
    media = db.get_album_media(album_id)
    if not media:
        await query.answer("相册为空", show_alert=True)
        return
    await query.answer()
    db.update_last_viewed(user.id, album_id, media[-1]["media_id"])
    total = len(media)
    protect = album.get("protect_content", 0) == 1
    for idx, media_item in enumerate(media):
        caption = f"📎 {idx + 1}/{total}\n\n💬 {media_item['caption'] or '无描述'}"
        try:
            if media_item["file_type"] == "photo":
                await context.bot.send_photo(
                    chat_id=user.id,
                    photo=media_item["file_id"],
                    caption=caption,
                    protect_content=protect,
                )
            elif media_item["file_type"] == "video":
                await context.bot.send_video(
                    chat_id=user.id,
                    video=media_item["file_id"],
                    caption=caption,
                    protect_content=protect,
                )
            else:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=media_item["file_id"],
                    caption=caption,
                    protect_content=protect,
                )
        except Exception as e:
            logger = None
            try:
                import logging

                logging.getLogger(__name__).warning(f"发送媒体失败: {e}")
            except Exception:
                pass
        await asyncio.sleep(0.3)
    await context.bot.send_message(
        chat_id=user.id,
        text=f"✅ 相册「{album['name']}」已全部加载完成！",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("« 返回主菜单", callback_data=f"menu_main")]]
        ),
    )


async def notify_followers(context, album_id, publisher_id):
    """通知粉丝有新内容"""
    try:
        album = db.get_album(album_id)
        followers = db.get_followers(publisher_id, album_id)
        if not followers:
            return
        for follower_id in followers:
            try:
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🆕 更新啦！！更新啦！！",
                                callback_data=f"new_content_{album_id}",
                            )
                        ]
                    ]
                )
                await context.bot.send_message(
                    chat_id=follower_id,
                    text=f"📢 相册「{album['name']}」有更新啦！",
                    reply_markup=keyboard,
                )
                await asyncio.sleep(0.2)
            except Exception as e:
                logging = __import__("logging")
                logging.getLogger(__name__).warning(f"通知粉丝 {follower_id} 失败: {e}")
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).error(f"通知粉丝失败: {e}")


async def show_fans_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示粉丝管理入口"""
    query = update.callback_query
    user = update.effective_user

    albums = db.get_user_albums(user.id)

    if not albums:
        await query.answer("你没有相册", show_alert=True)
        return

    keyboard = []
    for album in albums:
        follower_count = db.get_follower_count(user.id, album["album_id"])
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 {album['name']} ({follower_count}粉丝)",
                    callback_data=f"my_fans_{album['album_id']}",
                )
            ]
        )

    keyboard.append([InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")])

    await query.edit_message_text(
        "📊 我的粉丝\n\n选择相册查看粉丝统计:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_my_fans(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """显示指定相册的粉丝统计"""
    query = update.callback_query
    user = update.effective_user

    album = db.get_album(album_id)

    if album["owner_id"] != user.id:
        await query.answer("无权访问", show_alert=True)
        return

    follower_count = db.get_follower_count(user.id, album_id)
    audience_count = db.get_audience_count(user.id, album_id)

    text = f"""📊 相册「{album["name"]}」统计

👥 粉丝数: {follower_count} (关注了你的人数)
👀 用户数: {audience_count} (访问过链接的人数)

💡 粉丝会收到新内容通知，用户不会"""

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 广播通知", callback_data="broadcast_start")],
            [
                InlineKeyboardButton(
                    "« 返回相册", callback_data=f"view_album_{album_id}"
                )
            ],
        ]
    )

    await query.answer()
    await query.edit_message_text(text, reply_markup=keyboard)


async def start_broadcast_publisher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始广播（向粉丝发送内容）"""
    query = update.callback_query
    user = update.effective_user

    albums = db.get_user_albums(user.id)
    if not albums:
        await query.answer("你没有相册", show_alert=True)
        return

    # 创建或获取广播相册
    broadcast_album_id = db.get_or_create_broadcast_album(user.id)

    context.user_data["broadcast_album_id"] = broadcast_album_id
    context.user_data["waiting_for"] = "user_broadcast"
    context.user_data["broadcast_start_time"] = asyncio.get_event_loop().time()
    context.user_data["broadcast_type"] = "user"  # 标记为用户广播

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("« 取消广播", callback_data="cancel_broadcast")]]
    )

    await query.edit_message_text(
        "📢 广播功能\n\n"
        "请发送要广播的内容：\n\n"
        "📝 支持：文字、图片、视频、文件\n"
        "💬 建议添加描述文字\n\n"
        "内容将直接发送给所有粉丝。\n\n"
        "⏱️ 60秒内无操作将自动取消",
        reply_markup=keyboard,
    )


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """确认广播"""
    query = update.callback_query
    user = update.effective_user

    album_id = context.user_data.get("broadcast_album_id")
    message_text = context.user_data.get("broadcast_text", "")

    if not album_id:
        await query.answer("广播已取消", show_alert=True)
        return

    album = db.get_album(album_id)
    audience = db.get_audience(user.id, album_id)

    if not audience:
        await query.answer("没有观众可以通知", show_alert=True)
        return

    sent = 0
    for viewer_id in audience:
        try:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🆕 查看新内容", callback_data=f"new_content_{album_id}"
                        )
                    ]
                ]
            )
            await context.bot.send_message(
                chat_id=viewer_id,
                text=f"📢 {album['name']} 有新更新！\n\n{message_text}",
                reply_markup=keyboard,
            )
            sent += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logging = __import__("logging")
            logging.getLogger(__name__).warning(f"发送广播给 {viewer_id} 失败: {e}")

    await query.answer(f"✅ 已发送给 {sent} 位观众")

    context.user_data.pop("broadcast_album_id", None)
    context.user_data.pop("waiting_for", None)
    context.user_data.pop("broadcast_text", None)

    await query.edit_message_text(
        f"✅ 广播已发送!\n\n已发送给 {sent} 位观众。",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "« 返回相册", callback_data=f"view_album_{album_id}"
                    )
                ]
            ]
        ),
    )


async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消广播"""
    query = update.callback_query

    # 清理所有广播相关状态
    context.user_data.pop("broadcast_album_id", None)
    context.user_data.pop("waiting_for", None)
    context.user_data.pop("broadcast_text", None)
    context.user_data.pop("broadcast_media_file_id", None)
    context.user_data.pop("broadcast_media_type", None)
    context.user_data.pop("broadcast_start_time", None)
    context.user_data.pop("broadcast_type", None)

    await query.answer("已取消广播")
    await query.edit_message_text("广播已取消")
