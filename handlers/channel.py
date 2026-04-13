"""Channel membership verification."""

import config
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def check_channel_membership(user_id: int, context) -> bool:
    """检查用户是否已订阅频道"""
    try:
        member = await context.bot.get_chat_member(
            chat_id=config.PUBLIC_CHANNEL_ID, user_id=user_id
        )
        # status in ('member', 'administrator', 'creator')
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"检查频道成员失败: {e}")
        return False


async def require_channel_membership(update, context) -> bool:
    """检查频道订阅，不满足则发送订阅提示并返回True表示已处理"""
    user = update.effective_user

    if user.id == config.ADMIN_USER_ID:
        return False

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

        if update.message:
            await update.message.reply_text(
                f"❌ 请先订阅频道才能使用机器人！\n\n📢 频道: {channel_title}\n\n点击下方按钮订阅 👇",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif update.callback_query:
            await update.callback_query.answer("请先订阅频道", show_alert=True)

        return True  # 表示已处理，需要阻止进一步执行

    return False  # 表示检查通过
