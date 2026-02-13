"""
Telegram handler for AI chat powered by OpenAI.
Catches all text messages that aren't commands or handled by other handlers.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, CommandHandler, filters

from bot.services.openai_chat import chat, clear_history
from bot.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)


async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any text message as an AI chat message."""
    if not update.message or not update.message.text:
        return

    if not OPENAI_API_KEY:
        await update.message.reply_text(
            "Chat isn't set up yet. Check back soon 💋"
        )
        return

    user = update.effective_user
    user_name = user.first_name or user.username or ""

    # Show typing indicator
    await update.message.chat.send_action("typing")

    reply = await chat(
        user_id=user.id,
        user_message=update.message.text,
        user_name=user_name,
    )

    await update.message.reply_text(reply)


async def reset_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /newchat — clear conversation history."""
    user = update.effective_user
    clear_history(user.id)
    await update.message.reply_text(
        "Fresh start ✨ What's on your mind?"
    )


def get_chat_handlers():
    return [
        CommandHandler("newchat", reset_chat_command),
        # MessageHandler must be added LAST — it catches all remaining text
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message),
    ]
