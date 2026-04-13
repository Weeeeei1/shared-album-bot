"""Admin handlers - real implementations."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import config
from database import db

logger = logging.getLogger(__name__)


async def view_shared_album(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    album_id: int,
    token: Optional[str] = None,
):
    """通过分享链接查看相册"""
    from handlers.core import notify_album_owner, get_user_info
    from handlers.channel import require_channel_membership

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
    is_first_visit = False
    if album["owner_id"] != user.id:
        if not db.has_user_viewed(album_id, user.id):
            db.log_access(album_id, user.id)
            is_first_visit = True
            # 发送通知给相册创建者
            await notify_album_owner(context, album["owner_id"], user, album["name"])

            # 关注人群统计/记录
            try:
                db.add_audience(user.id, album["owner_id"], album_id)
            except Exception:
                pass
            # 更新观看位置为相册中的最后一条媒体
            try:
                media_list = db.get_album_media(album_id)
                last_media_id = media_list[-1]["media_id"] if media_list else None
                if last_media_id is not None:
                    db.update_last_viewed(user.id, album_id, last_media_id)
            except Exception:
                pass

    # 获取所有媒体
    media = db.get_album_media(album_id)

    if not media:
        await update.message.reply_text("📭 此相册为空")
        return

    total = len(media)

    # 获取内容保护和自动删除设置
    # allow_download: 0=禁止(保护内容), 1=允许(不保护)
    allow_download = album.get("allow_download", 0)
    protect = allow_download == 0  # 禁止下载时启用保护
    auto_delete_seconds = album.get("auto_delete_seconds", 600)

    # 收集发送的消息ID，用于批量删除
    sent_message_ids = []

    try:
        # 发送相册标题消息
        title_message = await update.message.reply_text(
            f"📂 {album['name']} (共 {total} 个媒体)\n\n🔒 此内容受保护，无法转发或保存"
        )
        sent_message_ids.append(title_message.message_id)

        # 依次发送所有媒体，添加延迟避免触发限流
        for idx, media_item in enumerate(media):
            caption = f"📎 {idx + 1}/{total}\n\n💬 {media_item['caption'] if media_item['caption'] else '无描述'}"

            try:
                if media_item["file_type"] == "photo":
                    msg = await update.message.reply_photo(
                        photo=media_item["file_id"],
                        caption=caption,
                        protect_content=protect,
                    )
                elif media_item["file_type"] == "video":
                    msg = await update.message.reply_video(
                        video=media_item["file_id"],
                        caption=caption,
                        protect_content=protect,
                    )
                else:
                    msg = await update.message.reply_document(
                        document=media_item["file_id"],
                        caption=caption,
                        protect_content=protect,
                    )
                sent_message_ids.append(msg.message_id)

                # 减少延迟以加快发送速度，但仍需避免触发Telegram限流
                if idx < total - 1:
                    await asyncio.sleep(0.05)

            except Exception as e:
                logger.warning(f"发送媒体 {idx + 1} 失败: {e}")
                continue

        # 获取当前是第几位观众
        viewer_count = db.get_unique_viewers_count(album_id)

        # 发送完成提示
        finish_text = f"✅ 相册「{album['name']}」已全部加载完成！\\n\\n👀 你是第 {viewer_count} 位观众\\n\\n⏱️ 内容将在 {auto_delete_seconds // 60} 分钟后自动消失"

        # 构建完成页按钮：先关注按钮再机器人入口
        keyboard_rows = []
        if album["owner_id"] != user.id:
            if db.is_following(user.id, album_id):
                keyboard_rows.append(
                    [
                        InlineKeyboardButton(
                            "🔔 已关注", callback_data=f"unfollow_{album_id}"
                        )
                    ]
                )
            else:
                keyboard_rows.append(
                    [
                        InlineKeyboardButton(
                            "✅ 关注发布者", callback_data=f"follow_{album_id}"
                        )
                    ]
                )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    "🤖 使用机器人创建相册", url=f"https://t.me/{context.bot.username}"
                )
            ]
        )
        finish_keyboard = InlineKeyboardMarkup(keyboard_rows)

        finish_message = await update.message.reply_text(
            finish_text, reply_markup=finish_keyboard
        )
        sent_message_ids.append(finish_message.message_id)

        # 如果设置了自动删除，安排定时批量删除所有消息
        if auto_delete_seconds > 0 and sent_message_ids:

            async def delete_all_messages_after_delay():
                await asyncio.sleep(auto_delete_seconds)
                for msg_id in sent_message_ids:
                    try:
                        await context.bot.delete_message(
                            chat_id=user.id, message_id=msg_id
                        )
                    except Exception as e:
                        logger.warning(f"删除消息 {msg_id} 失败: {e}")

            # 启动后台任务删除所有消息
            from utils import task_manager

            task_manager.spawn(
                delete_all_messages_after_delay(), name=f"delete_batch_{user.id}"
            )

    except Exception as e:
        logger.error(f"发送相册失败: {e}", exc_info=True)
        await update.message.reply_text(
            f"📂 {album['name']}\n\n📭 加载失败",
        )


async def approve_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    """管理员审核通过"""
    from handlers.core import is_admin, get_user_info

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
        # 先应答回调，避免Telegram显示持续加载状态
        await query.answer()

        # 发布到公开频道
        user_info = (
            f"@{review['username']}" if review["username"] else review["first_name"]
        )
        caption_text = review["caption"] or ""

        public_caption = f"""{caption_text}

👤 {user_info}
📁 {review["album_name"]}

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
        except Exception as e:
            logger.warning(f"通知用户审核通过失败: {e}")

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
        except Exception as e:
            logger.warning(f"编辑审核通过消息失败: {e}")
            await query.answer("✅ 已通过", show_alert=True)

    except Exception as e:
        logger.error(f"审核通过时出错: {e}", exc_info=True)
        await query.answer(f"❌ 发布失败: {e}", show_alert=True)


async def reject_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    """管理员审核拒绝"""
    from handlers.core import is_admin, get_user_info

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
        # 先应答回调，避免Telegram显示持续加载状态
        await query.answer()

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
        except Exception as e:
            logger.warning(f"通知用户审核拒绝失败: {e}")

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
        except Exception as e:
            logger.warning(f"编辑审核拒绝消息失败: {e}")
            await query.answer("❌ 已拒绝", show_alert=True)

    except Exception as e:
        logger.error(f"审核拒绝时出错: {e}", exc_info=True)
        await query.answer(f"❌ 操作失败: {e}", show_alert=True)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    from handlers.core import is_admin, get_main_menu_keyboard

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
    except Exception as e:
        logger.warning(f"编辑统计消息失败: {e}")
        # 如果编辑失败（当前是媒体消息），删除后发送新消息
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示帮助信息"""
    from handlers.core import get_main_menu_keyboard

    query = update.callback_query

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
    except Exception as e:
        logger.warning(f"编辑帮助消息失败: {e}")
        # 如果编辑失败（当前是媒体消息），删除后发送新消息
        try:
            await query.message.delete()
        except Exception as e2:
            logger.warning(f"删除消息失败: {e2}")
        await context.bot.send_message(
            chat_id=user.id, text=help_text, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示管理员菜单"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    pending_count = db.get_pending_reviews_count()
    expired_count = len(db.get_expired_albums())

    keyboard = [
        [
            InlineKeyboardButton(
                f"📋 待审核 ({pending_count})", callback_data="admin_pending"
            )
        ],
        [
            InlineKeyboardButton("📁 相册管理", callback_data="admin_albums_0"),
            InlineKeyboardButton(
                "🖼️ 内容管理", callback_data="admin_content_approved_0"
            ),
        ],
        [
            InlineKeyboardButton("👥 用户管理", callback_data="admin_users"),
            InlineKeyboardButton("🏥 系统健康", callback_data="admin_health"),
        ],
        [InlineKeyboardButton("📊 全局统计", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙️ 系统设置", callback_data="admin_settings")],
        [InlineKeyboardButton("📢 广播消息", callback_data="admin_broadcast")],
        [
            InlineKeyboardButton("🔧 系统维护", callback_data="admin_maintenance"),
            InlineKeyboardButton(
                f"⚠️ 过期相册({expired_count})", callback_data="admin_albums_0"
            ),
        ],
        [InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")],
    ]

    await query.edit_message_text(
        "👑 管理员菜单\n\n选择功能：", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示管理员统计"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

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
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    users = db.get_all_users()

    text = f"""👥 用户管理

总用户数: {len(users)}

👆 点击用户查看详情"""

    keyboard = []
    for u in users[:15]:
        user_info = f"@{u['username']}" if u["username"] else f"{u['first_name']}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"👤 {user_info}", callback_data=f"admin_user_detail_{u['user_id']}"
                )
            ]
        )

    keyboard.append(
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示待审核列表"""
    from handlers.core import is_admin

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

    # 发送待审核列表，包含预览功能
    text = f"""📋 待审核列表

共 {len(reviews)} 条待审核
👇 点击「查看」查看媒体详情"""

    keyboard = []

    for review in reviews[:10]:
        user_info = (
            f"@{review['username']}" if review["username"] else review["first_name"]
        )
        text += f"\n\n📝 #{review['review_id']}"
        text += f"\n👤 {user_info}"
        text += f"\n📁 {review['album_name']}"
        text += f"\n💬 {review['caption'][:30] if review['caption'] else '无留言'}"
        text += f"\n🕐 {review['created_at'][:16]}"

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"👁 查看 #{review['review_id']}",
                    callback_data=f"preview_review_{review['review_id']}",
                ),
                InlineKeyboardButton(
                    f"✅ 通过",
                    callback_data=f"approve_review_{review['review_id']}",
                ),
                InlineKeyboardButton(
                    f"❌ 拒绝",
                    callback_data=f"reject_review_{review['review_id']}",
                ),
            ]
        )

    # 添加批量操作按钮
    keyboard.insert(
        0,
        [
            InlineKeyboardButton("✅ 批量通过全部", callback_data="batch_approve_all"),
            InlineKeyboardButton("❌ 批量拒绝全部", callback_data="batch_reject_all"),
        ],
    )

    keyboard.append(
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示系统设置"""
    from handlers.core import is_admin

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
    from handlers.core import is_admin

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
    from handlers.core import is_admin

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
    from handlers.core import is_admin

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
    from handlers.core import is_admin

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


async def preview_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    """预览待审核媒体"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    review = db.get_pending_review(review_id)
    if not review:
        await query.answer("审核记录不存在", show_alert=True)
        return

    user_info = f"@{review['username']}" if review["username"] else review["first_name"]
    caption_text = review["caption"] or ""

    review_caption = f"""📝 审核预览 #{review_id}

👤 用户: {user_info}
🆔 用户ID: {review["user_id"]}
📁 相册: {review["album_name"]}
💬 描述: {caption_text if caption_text else "无"}

状态: {review["status"]}"""

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ 通过", callback_data=f"approve_review_{review_id}"
            ),
            InlineKeyboardButton("❌ 拒绝", callback_data=f"reject_review_{review_id}"),
        ],
        [InlineKeyboardButton("« 返回列表", callback_data="admin_pending")],
    ]

    try:
        if review["file_type"] == "photo":
            await query.edit_message_media(
                media=review["file_id"],
                caption=review_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif review["file_type"] == "video":
            await query.edit_message_media(
                media=review["file_id"],
                caption=review_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await query.edit_message_media(
                media=review["file_id"],
                caption=review_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    except Exception:
        # 如果编辑媒体失败，尝试发送新消息
        try:
            await query.message.delete()
        except Exception:
            pass

        if review["file_type"] == "photo":
            await context.bot.send_photo(
                chat_id=user.id,
                photo=review["file_id"],
                caption=review_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif review["file_type"] == "video":
            await context.bot.send_video(
                chat_id=user.id,
                video=review["file_id"],
                caption=review_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await context.bot.send_document(
                chat_id=user.id,
                document=review["file_id"],
                caption=review_caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


async def batch_approve_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """批量审核通过"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    reviews = db.get_all_pending_reviews()
    if not reviews:
        await query.answer("没有待审核的媒体", show_alert=True)
        return

    approved_count = 0
    for review in reviews:
        try:
            # 发布到公开频道
            review_obj = db.get_pending_review(review["review_id"])
            if not review_obj or review_obj["status"] != "pending":
                continue

            user_info = (
                f"@{review['username']}" if review["username"] else review["first_name"]
            )
            caption_text = review["caption"] or ""

            public_caption = f"""{caption_text}

👤 {user_info}
📁 {review["album_name"]}

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

            # 更新记录
            db.update_public_message_id(review["media_id"], public_msg.message_id)
            db.update_review_status(review["review_id"], "approved", user.id)

            # 通知用户
            try:
                await context.bot.send_message(
                    chat_id=review["user_id"],
                    text="✅ 你的媒体已通过审核并发布到公开频道！",
                )
            except Exception:
                pass

            approved_count += 1
            await asyncio.sleep(0.3)

        except Exception as e:
            logger.error(f"批量审核第 {review['review_id']} 条时出错: {e}")
            continue

    await query.answer(f"✅ 已批量通过 {approved_count} 条")
    await show_admin_pending(update, context)


async def batch_reject_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """批量审核拒绝"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    reviews = db.get_all_pending_reviews()
    if not reviews:
        await query.answer("没有待审核的媒体", show_alert=True)
        return

    rejected_count = 0
    for review in reviews:
        try:
            review_obj = db.get_pending_review(review["review_id"])
            if not review_obj or review_obj["status"] != "pending":
                continue

            # 更新审核状态
            db.update_review_status(review["review_id"], "rejected", user.id)

            # 通知用户
            try:
                await context.bot.send_message(
                    chat_id=review["user_id"],
                    text="❌ 你的媒体未通过审核，未发布到公开频道。",
                )
            except Exception:
                pass

            rejected_count += 1
            await asyncio.sleep(0.3)

        except Exception as e:
            logger.error(f"批量拒绝第 {review['review_id']} 条时出错: {e}")
            continue

    await query.answer(f"❌ 已批量拒绝 {rejected_count} 条")
    await show_admin_pending(update, context)


# ========== Enhanced Admin Features ==========


async def show_admin_user_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int
):
    """显示用户详情"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    user_info = db.get_user(user_id)
    if not user_info:
        await query.answer("用户不存在", show_alert=True)
        return

    albums_count = db.get_user_albums_count(user_id)
    media_count = db.get_user_media_count(user_id)
    total_views = db.get_user_total_views(user_id)

    text = f"""👤 用户详情

🆔 用户ID: {user_info["user_id"]}
📛 名称: {user_info["first_name"]} {user_info.get("last_name", "") or ""}
👤 用户名: @{user_info["username"] if user_info["username"] else "无"}
📅 注册时间: {user_info["created_at"][:16]}
🕐 最后活跃: {user_info["last_active"][:16]}

📊 统计:
📁 相册数: {albums_count}
🖼️ 媒体数: {media_count}
👀 总访问: {total_views}"""

    keyboard = [
        [
            InlineKeyboardButton(
                "🗑️ 删除用户所有内容", callback_data=f"delete_user_content_{user_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 查看用户相册", callback_data=f"view_user_albums_{user_id}"
            ),
        ],
        [
            InlineKeyboardButton("« 返回用户管理", callback_data="admin_users"),
        ],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def start_delete_user_content(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int
):
    """确认删除用户内容"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ 确认删除", callback_data=f"confirm_delete_user_{user_id}"
            ),
            InlineKeyboardButton(
                "❌ 取消", callback_data=f"admin_user_detail_{user_id}"
            ),
        ]
    ]

    await query.edit_message_text(
        "⚠️ 确认删除该用户的所有内容？\n\n"
        "这将删除该用户的所有相册和媒体，此操作不可恢复！",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def confirm_delete_user_content(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int
):
    """执行删除用户内容"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    albums_deleted = db.delete_user_albums(user_id)
    media_deleted = db.delete_user_media(user_id)

    await query.answer(
        f"✅ 已删除 {albums_deleted} 个相册和 {media_deleted} 个媒体", show_alert=True
    )
    await show_admin_users(update, context)


async def show_all_albums(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
):
    """显示所有相册"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    page_size = 10
    offset = page * page_size
    albums = db.get_all_albums(limit=page_size, offset=offset)
    total = db.get_all_albums_count()
    total_pages = (total + page_size - 1) // page_size

    if not albums and page > 0:
        await query.answer("没有更多相册", show_alert=True)
        return

    text = f"""📁 所有相册 (第 {page + 1}/{max(1, total_pages)} 页)

共 {total} 个相册\n"""

    keyboard = []
    for album in albums:
        owner = f"@{album['username']}" if album["username"] else album["owner_name"]
        text += f"\n📂 {album['name']}\n"
        text += f"   👤 {owner} | 🖼️ {album['media_count']} | 👀 {album['view_count']}\n"

        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑️ 删除'{album['name'][:15]}'",
                    callback_data=f"force_delete_album_{album['album_id']}",
                ),
            ]
        )

    # 分页按钮
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("◀️ 上一页", callback_data=f"admin_albums_{page - 1}")
        )
    if (page + 1) < total_pages:
        nav_buttons.append(
            InlineKeyboardButton("▶️ 下一页", callback_data=f"admin_albums_{page + 1}")
        )
    if nav_buttons:
        keyboard.insert(0, nav_buttons)

    keyboard.append(
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def force_delete_album_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """强制删除相册"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    album = db.get_album(album_id)
    if not album:
        await query.answer("相册不存在", show_alert=True)
        return

    db.force_delete_album(album_id)
    await query.answer(f"✅ 已删除相册: {album['name']}", show_alert=True)
    await show_all_albums(update, context, page=0)


async def show_system_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示系统健康状态"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    stats = db.get_stats()
    db_size = db.get_database_size()
    db_size_mb = db_size / (1024 * 1024)

    # 计算待审核积压警告
    pending_count = db.get_pending_reviews_count()
    pending_warning = "⚠️ 积压较多!" if pending_count > 20 else ""

    # 获取过期相册数量
    expired = db.get_expired_albums()

    # 获取最近7天统计
    daily_stats = db.get_daily_stats(7)
    media_stats = db.get_media_daily_stats(7)

    total_new_users_7d = sum(d.get("new_users", 0) for d in daily_stats)
    total_new_media_7d = sum(d.get("new_media", 0) for d in media_stats)

    text = f"""🏥 系统健康状态

📊 基本统计:
👥 总用户: {stats["total_users"]}
📁 总相册: {stats["total_albums"]}
🖼️ 总媒体: {stats["total_media"]}
👀 总访问: {stats["total_accesses"]}

📈 7天趋势:
👤 新增用户: {total_new_users_7d}
🖼️ 新增媒体: {total_new_media_7d}

💾 数据库:
📦 大小: {db_size_mb:.2f} MB

⏳ 审核队列:
📋 待审核: {pending_count} {pending_warning}

🕐 过期相册: {len(expired)} 个"""

    keyboard = [
        [
            InlineKeyboardButton(
                "🧹 清理过期相册", callback_data="cleanup_expired_albums"
            )
        ],
        [InlineKeyboardButton("📊 详细统计", callback_data="show_detailed_stats")],
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")],
    ]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def cleanup_expired_albums_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """清理过期相册"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    count = db.cleanup_expired_albums()
    await query.answer(f"✅ 已清理 {count} 个过期相册", show_alert=True)
    await show_system_health(update, context)


async def show_all_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status: str = "approved",
    page: int = 0,
):
    """显示所有内容"""
    from handlers.core import is_admin

    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("无权访问", show_alert=True)
        return

    page_size = 10
    offset = page * page_size
    contents = db.get_media_by_status(status, limit=page_size, offset=offset)
    total = db.get_media_by_status_count(status)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    status_text = {"approved": "已发布", "rejected": "已拒绝", "pending": "待审核"}
    current_status = status_text.get(status, status)

    if not contents and page > 0:
        await query.answer("没有更多内容", show_alert=True)
        return

    text = f"""📋 内容管理 - {current_status} (第 {page + 1}/{max(1, total_pages)} 页)

共 {total} 条内容\n"""

    keyboard = []

    # 状态切换按钮
    status_buttons = []
    for s, sname in status_text.items():
        if s != status:
            status_buttons.append(
                InlineKeyboardButton(sname, callback_data=f"admin_content_{s}_0")
            )
    if status_buttons:
        keyboard.append(status_buttons)

    for content in contents:
        user_info = (
            f"@{content['username']}" if content["username"] else content["first_name"]
        )
        caption = content["caption"][:20] if content["caption"] else "无描述"
        text += f"\n🖼️ #{content['media_id']} - {content['file_type']}\n"
        text += f"   👤 {user_info} | 📁 {content['album_name']}\n"
        text += f"   💬 {caption}\n"
        text += f"   🕐 {content['created_at'][:16]}\n"

    # 分页按钮
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "◀️ 上一页", callback_data=f"admin_content_{status}_{page - 1}"
            )
        )
    if (page + 1) < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                "▶️ 下一页", callback_data=f"admin_content_{status}_{page + 1}"
            )
        )
    if nav_buttons:
        keyboard.insert(0, nav_buttons)

    keyboard.append(
        [InlineKeyboardButton("« 返回管理员菜单", callback_data="admin_menu")]
    )

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
