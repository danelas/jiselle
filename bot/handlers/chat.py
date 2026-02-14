"""
Telegram handler for AI chat powered by OpenAI.
Catches all text messages that aren't commands or handled by other handlers.
When AI detects purchase intent, creates a PayPal payment link and sends it.
"""

import logging
import datetime
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CommandHandler, filters

from bot.services.openai_chat import (
    chat, clear_history, ContentRequest, get_post_offer_reply, get_last_tool_call_id,
)
from bot.config import OPENAI_API_KEY
from bot.models.database import SessionLocal
from bot.models.schemas import User, Image, Order, OrderStatus, ContentType
from bot.services import paypal

logger = logging.getLogger(__name__)


async def _find_image_for_user(telegram_id: int):
    """Pick a random private image the user hasn't purchased yet."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            logger.warning(f"No User record for telegram_id={telegram_id}")
            return None, None, db

        # Get IDs of images user already owns
        owned_ids = [
            o.image_id for o in
            db.query(Order)
            .filter(Order.user_id == user.id, Order.status == OrderStatus.COMPLETED.value)
            .all()
        ]

        # Find private images not yet owned
        q = db.query(Image).filter(
            Image.content_type == ContentType.PRIVATE.value,
            Image.is_active == True,
        )
        if owned_ids:
            q = q.filter(~Image.id.in_(owned_ids))

        available = q.all()
        logger.info(f"Private images for user {telegram_id}: {len(available)} available, {len(owned_ids)} owned")

        if not available:
            # Fallback: try any active image not owned
            fallback_q = db.query(Image).filter(Image.is_active == True)
            if owned_ids:
                fallback_q = fallback_q.filter(~Image.id.in_(owned_ids))
            available = fallback_q.all()
            logger.info(f"Fallback (any active image): {len(available)} available")

        if not available:
            logger.warning(f"No images at all for user {telegram_id}")
            return None, user, db

        image = random.choice(available)
        return image, user, db
    except Exception:
        db.close()
        raise


async def _create_payment_for_chat(user: User, image: Image, db) -> dict:
    """Create an Order + PayPal payment link for an image."""
    # Calculate price (apply VIP discount)
    from bot.handlers.purchase import _get_user_discount
    from bot.handlers.flash_sales import get_flash_price

    sale_price, _, _ = get_flash_price(image, db)
    discount = _get_user_discount(user)
    final_price = round(sale_price * discount)

    # Create internal order
    order = Order(
        user_id=user.id,
        image_id=image.id,
        amount=final_price,
        status=OrderStatus.PENDING.value,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # Create PayPal order
    pp_result = await paypal.create_order(
        amount=final_price,
        description=f"Unlock: {image.title}",
        custom_id=str(order.id),
    )

    order.paypal_order_id = pp_result["order_id"]
    db.commit()

    return {
        "approve_url": pp_result["approve_url"],
        "price": final_price,
        "order_id": order.id,
    }


async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any text message as an AI chat message."""
    if not update.message or not update.message.text:
        return

    if not OPENAI_API_KEY:
        await update.message.reply_text(
            "Chat isn't set up yet. Check back soon 💋"
        )
        return

    tg_user = update.effective_user
    user_name = tg_user.first_name or tg_user.username or ""

    try:
        # Show typing indicator
        await update.message.chat.send_action("typing")

        result = await chat(
            user_id=tg_user.id,
            user_message=update.message.text,
            user_name=user_name,
        )

        # Normal text reply
        if isinstance(result, str):
            await update.message.reply_text(result)
            return

        # AI triggered purchase intent
        if isinstance(result, ContentRequest):
            db = None
            try:
                image, user, db = await _find_image_for_user(tg_user.id)

                if not user:
                    await update.message.reply_text("Send /start first so I know who you are 💋")
                    return

                if not image:
                    logger.info(f"No images available for user {tg_user.id}")
                    # No content to sell — clear the dangling tool call from history
                    from bot.services.openai_chat import _histories
                    hist = _histories.get(tg_user.id, [])
                    if hist and hist[-1].get("tool_calls"):
                        hist.pop()
                    hist.append({"role": "assistant", "content": "I'm working on something new just for you… not quite ready yet, but soon 💋"})
                    await update.message.reply_text(
                        "I'm working on something new just for you… not quite ready yet, but soon 💋"
                    )
                    return

                # Create payment
                payment = await _create_payment_for_chat(user, image, db)

                # Get AI's natural response about the offer
                tool_call_id = get_last_tool_call_id(tg_user.id)
                ai_reply = await get_post_offer_reply(
                    tg_user.id, tool_call_id, image.title, int(payment["price"]), user_name
                )

                # Send AI message + payment button
                keyboard = [
                    [InlineKeyboardButton(
                        f"💳 Unlock for ${payment['price']:.0f}",
                        url=payment["approve_url"]
                    )],
                ]

                await update.message.reply_text(
                    ai_reply,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

            finally:
                if db:
                    db.close()

    except Exception as e:
        logger.error(f"Chat handler error for user {tg_user.id}: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "I got a little distracted… send that again? 💭"
            )
        except Exception:
            pass


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
