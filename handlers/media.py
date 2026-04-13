import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import config
from database import db
from utils import task_manager

from .core import validate_album_name, get_user_info, is_admin
from .channel import check_channel_membership, require_channel_membership
from .followers import notify_followers

logger = logging.getLogger(__name__)


# NOTE:
# The following functions were extracted from bot.py to this module.
# They preserve original signatures where possible. Some inner helpers
# (like nested functions) are kept verbatim to maintain exact behavior.


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
            except Exception as e:
                logger.warning(f"获取频道信息失败: {e}")
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

    # 如果没有留言，先让用户选择留言方式
    if not caption:
        # 保存媒体信息到user_data
        context.user_data["pending_media_for_caption"] = {
            "file_id": file_id,
            "file_type": file_type,
            "original_message_id": message.message_id,
            "original_chat_id": user.id,
        }
        context.user_data["waiting_for"] = "media_caption"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💬 使用默认留言", callback_data="use_default_caption"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✏️ 自己输入留言", callback_data="input_custom_caption"
                    )
                ],
            ]
        )

        await message.reply_text(
            "📝 请为您的媒体添加留言\n\n"
            "点击下方按钮选择：\n"
            "• 默认留言：'好s'\n"
            "• 自己输入：手动输入留言内容",
            reply_markup=keyboard,
        )
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
            except Exception as e:
                logger.warning(f"取消定时器失败: {e}")

        # 设置新的定时器，2秒后处理批量上传
        async def process_batch():
            await asyncio.sleep(2)
            await process_batch_upload(update, context)

        context.user_data["batch_timer"] = task_manager.spawn(
            process_batch(), name=f"batch_{user.id}"
        )

    except Exception as e:
        logger.error(f"处理媒体时出错: {e}", exc_info=True)
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
        # Notify followers about new content
        try:
            task_manager.spawn(
                notify_followers(context, album_id, user.id),
                name=f"notify_followers_{album_id}",
            )
        except Exception:
            pass
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
\n"""

                review_caption = f"""📝 审核请求 #{review_id}
\n
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
                logger.error(f"批量提交审核时出错: {e}", exc_info=True)

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

    if not album:
        await query.answer("相册不存在，请重新选择", show_alert=True)
        await show_album_selection(update, context)
        return

    await query.answer()

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
    # Notify followers about new content
    try:
        task_manager.spawn(
            notify_followers(context, album_id, user.id),
            name=f"notify_followers_{album_id}",
        )
    except Exception:
        pass

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
\n
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
            logger.error(f"提交审核时出错: {e}", exc_info=True)
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


async def process_caption_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, album_id: int
):
    """处理留言后选择相册的媒体"""
    logger.info(f"[CAPTION] process_caption_media called, album_id={album_id}")

    query = update.callback_query
    user = update.effective_user

    pending = context.user_data.get("pending_media_for_caption")
    caption = context.user_data.get("pending_caption", "好s")

    logger.info(f"[CAPTION] pending={pending is not None}, caption={caption}")

    if not pending:
        logger.warning(f"[CAPTION] No pending media, returning")
        await query.answer("超时，请重新上传媒体", show_alert=True)
        return

    album = db.get_album(album_id)
    logger.info(f"[CAPTION] album found: {album}")

    if not album:
        logger.warning(f"[CAPTION] Album {album_id} not found")
        await query.answer("相册不存在", show_alert=True)
        return

    await query.answer()
    logger.info(f"[CAPTION] Answered callback, now editing message")

    # 显示公开/保存选择
    keyboard = [
        [
            InlineKeyboardButton(
                "📢 公开到频道（需审核）", callback_data="caption_publish_public"
            )
        ],
        [InlineKeyboardButton("🔒 仅保存", callback_data="caption_publish_private")],
    ]

    try:
        await query.edit_message_text(
            f"✅ 将保存到相册: {album['name']}\n📝 留言: {caption}\n\n是否公开到频道？",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        logger.info(f"[CAPTION] Message edited successfully")
    except Exception as e:
        logger.error(f"[CAPTION] Failed to edit message: {e}")
        raise

    # 保存选择
    context.user_data["caption_album_id"] = album_id
    logger.info(f"[CAPTION] Done, caption_album_id set to {album_id}")


async def publish_caption_media(
    update: Update, context: ContextTypes.DEFAULT_TYPE, is_public: bool
):
    """发布留言后的单条媒体"""
    query = update.callback_query
    user = update.effective_user

    pending = context.user_data.get("pending_media_for_caption")
    caption = context.user_data.get("pending_caption", "好s")
    album_id = context.user_data.get("caption_album_id")

    if not pending or not album_id:
        await query.answer("超时，请重新上传媒体", show_alert=True)
        return

    album = db.get_album(album_id)

    # 转发到私有群组备份（使用新caption）
    try:
        user_info = get_user_info(user)
        topic_title = f"👤 用户 {user.id}"
        if user.username:
            topic_title += f" (@{user.username})"
        elif user.first_name:
            topic_title += f" - {user.first_name}"

        full_caption = caption
        full_caption += f"\n\n{topic_title}" if full_caption else topic_title

        forwarded = await context.bot.copy_message(
            chat_id=config.PRIVATE_GROUP_ID,
            from_chat_id=user.id,
            message_id=pending.get("original_message_id", 0),
            caption=full_caption,
        )
        private_message_id = forwarded.message_id
    except Exception as e:
        logger.warning(f"转发媒体失败: {e}")
        private_message_id = 0

    # 保存到数据库
    media_id = db.add_media(
        album_id=album_id,
        user_id=user.id,
        file_id=pending["file_id"],
        file_type=pending["file_type"],
        caption=caption,
        private_message_id=private_message_id,
    )

    # 清理user_data
    context.user_data.pop("pending_media_for_caption", None)
    context.user_data.pop("pending_caption", None)
    context.user_data.pop("caption_album_id", None)

    # 如果选择公开，发送到审核
    if is_public:
        try:
            review_id = db.add_pending_review(
                media_id=media_id,
                user_id=user.id,
                album_id=album_id,
                file_id=pending["file_id"],
                file_type=pending["file_type"],
                caption=caption,
                private_message_id=private_message_id,
            )

            await query.answer("✅ 已保存并提交审核")
            await query.edit_message_text(
                f"✅ 已保存到相册「{album['name']}」\n"
                f"📝 留言: {caption}\n\n"
                f"已提交公开审核，等待管理员批准后会在频道展示。",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]
                ),
            )
        except Exception as e:
            logger.error(f"提交审核失败: {e}")
            await query.answer("❌ 审核提交失败", show_alert=True)
    else:
        await query.answer("✅ 已保存")
        await query.edit_message_text(
            f"✅ 已保存到相册「{album['name']}」\n📝 留言: {caption}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« 返回主菜单", callback_data="menu_main")]]
            ),
        )

    # 通知粉丝
    try:
        task_manager.spawn(
            notify_followers(context, album_id, user.id),
            name=f"notify_followers_{album_id}",
        )
    except Exception:
        pass


async def use_default_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """使用默认留言"""
    query = update.callback_query
    user = update.effective_user

    pending = context.user_data.get("pending_media_for_caption")
    if not pending:
        await query.answer("超时，请重新上传媒体", show_alert=True)
        return

    # 使用默认留言
    caption = "好s"
    context.user_data["pending_caption"] = caption
    # 注意：不要删除 pending_media_for_caption，因为它在 process_caption_media 中还需要使用

    # 让用户选择相册
    await query.answer()
    await select_album_for_caption(update, context)


async def start_custom_caption_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """让用户输入自定义留言"""
    query = update.callback_query
    user = update.effective_user

    pending = context.user_data.get("pending_media_for_caption")
    if not pending:
        await query.answer("超时，请重新上传媒体", show_alert=True)
        return

    context.user_data["waiting_for"] = "custom_caption"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("« 取消", callback_data="cancel_caption_input")]]
    )

    await query.answer()
    await query.edit_message_text("✏️ 请输入您的留言内容：", reply_markup=keyboard)


async def select_album_for_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """选择相册并继续处理媒体"""
    user = update.effective_user
    query = update.callback_query if hasattr(update, "callback_query") else None

    albums = db.get_user_albums(user.id)
    caption = context.user_data.get("pending_caption", "好s")

    keyboard = []
    for album in albums:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📁 {album['name']}",
                    callback_data=f"caption_select_album_{album['album_id']}",
                )
            ]
        )

    keyboard.append(
        [InlineKeyboardButton("➕ 创建新相册", callback_data="caption_create_album")]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "🔘 跳过（存默认相册）", callback_data="caption_select_default"
            )
        ]
    )

    text = f"📝 留言: {caption}\n\n请选择要保存到的相册："

    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await context.bot.send_message(
            chat_id=user.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
