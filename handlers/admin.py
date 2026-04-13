from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import config
from database import db
from .core import is_admin, get_main_menu_keyboard

"""
Admin handlers proxy module.
This module exposes admin-related function signatures by delegating
to the original implementations in bot.py. This avoids duplicating
logic and keeps signatures intact as requested.
Note: Some admin actions in bot.py are implemented as top-level
functions; this module provides thin wrappers so you can import
admin handlers from a single place.
"""


async def view_shared_album(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    album_id: int,
    token: Optional[str] = None,
):
    # Lazy import to avoid circular imports at module load time
    from ..bot import view_shared_album as _view_shared_album

    return await _view_shared_album(update, context, album_id, token)


async def delete_all_messages_after_delay():
    """Backward-compatibility placeholder.
    The original implementation is defined as a nested function inside
    view_shared_album in bot.py. Exposing a top-level alias would require
    refactoring; keep this as a lightweight placeholder to preserve
    import-time compatibility for callers that expect this symbol.
    The actual logic remains inside bot.py.
    """
    try:
        from ..bot import delete_all_messages_after_delay as _del  # type: ignore

        return await _del()
    except Exception:
        # If the symbol is not available due to refactoring, degrade gracefully
        return None


async def approve_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    from ..bot import approve_review as _approve_review

    return await _approve_review(update, context, review_id)


async def reject_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    from ..bot import reject_review as _reject_review

    return await _reject_review(update, context, review_id)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_stats as _show_stats

    return await _show_stats(update, context)


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_help as _show_help

    return await _show_help(update, context)


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_admin_menu as _show_admin_menu

    return await _show_admin_menu(update, context)


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_admin_stats as _show_admin_stats

    return await _show_admin_stats(update, context)


async def show_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_admin_users as _show_admin_users

    return await _show_admin_users(update, context)


async def show_admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_admin_pending as _show_admin_pending

    return await _show_admin_pending(update, context)


async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_admin_settings as _show_admin_settings

    return await _show_admin_settings(update, context)


async def show_admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import show_admin_maintenance as _show_admin_maintenance

    return await _show_admin_maintenance(update, context)


async def set_public_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import set_public_channel as _set_public_channel

    return await _set_public_channel(update, context)


async def set_private_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Note: In bot.py, the function is named set_private_group. This alias
    # preserves the requested name while delegating to the actual implementation.
    from ..bot import set_private_group as _set_private_group

    return await _set_private_group(update, context)


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import start_broadcast as _start_broadcast

    return await _start_broadcast(update, context)


async def batch_approve_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import batch_approve_all as _batch_approve_all

    return await _batch_approve_all(update, context)


async def batch_reject_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..bot import batch_reject_all as _batch_reject_all

    return await _batch_reject_all(update, context)


async def preview_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE, review_id: int
):
    from ..bot import preview_review as _preview_review

    return await _preview_review(update, context, review_id)
