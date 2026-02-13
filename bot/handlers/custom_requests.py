import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler
)
from bot.models.database import SessionLocal
from bot.models.schemas import User, CustomRequest, RequestStatus, Image
from bot.services import paypal

logger = logging.getLogger(__name__)

AWAITING_REQUEST_DESCRIPTION = 200
AWAITING_REQUEST_CONFIRM = 201


async def custom_request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a custom content request."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await update.message.reply_text("Please /start the bot first.")
            return ConversationHandler.END

        # Check for pending requests
        pending = (
            db.query(CustomRequest)
            .filter(
                CustomRequest.user_id == user.id,
                CustomRequest.status.in_([
                    RequestStatus.PENDING.value,
                    RequestStatus.ACCEPTED.value
                ])
            )
            .count()
        )

        if pending >= 3:
            await update.message.reply_text(
                "❌ You already have 3 pending requests.\n"
                "Please wait for them to be completed before submitting more."
            )
            return ConversationHandler.END

        await update.message.reply_text(
            "✨ **Custom Content Request**\n\n"
            "Describe what you'd like created. Be as specific as possible!\n"
            "The more detail you give, the better the result.\n\n"
            "💰 Pricing is set per request based on complexity.\n"
            "Typical range: **$10 — $50**\n\n"
            "Type your description below (or /cancel to abort):",
            parse_mode="Markdown"
        )
        return AWAITING_REQUEST_DESCRIPTION
    finally:
        db.close()


async def receive_request_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive the custom request description."""
    description = update.message.text.strip()
    if len(description) < 10:
        await update.message.reply_text(
            "❌ Please provide a more detailed description (at least 10 characters)."
        )
        return AWAITING_REQUEST_DESCRIPTION

    context.user_data["custom_request_desc"] = description

    keyboard = [
        [InlineKeyboardButton("✅ Submit Request", callback_data="confirm_request")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_request")],
    ]

    await update.message.reply_text(
        f"📝 **Your Request:**\n\n"
        f"_{description}_\n\n"
        f"Submit this request? You'll be notified when it's reviewed and priced.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return AWAITING_REQUEST_CONFIRM


async def confirm_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and submit the custom request."""
    query = update.callback_query
    await query.answer()

    description = context.user_data.get("custom_request_desc", "")
    if not description:
        await query.edit_message_text("❌ Request expired. Please try /request again.")
        return ConversationHandler.END

    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            return ConversationHandler.END

        req = CustomRequest(
            user_id=user.id,
            description=description,
            status=RequestStatus.PENDING.value,
        )
        db.add(req)
        db.commit()
        db.refresh(req)

        await query.edit_message_text(
            f"✅ **Request #{req.id} Submitted!**\n\n"
            f"I'll review it and get back to you with a price.\n"
            f"You'll receive a notification when it's ready.\n\n"
            f"Check status anytime with /myrequests",
            parse_mode="Markdown"
        )

        # Notify admin
        from bot.config import ADMIN_TELEGRAM_ID
        try:
            admin_keyboard = [
                [InlineKeyboardButton(
                    "💰 Set Price & Accept",
                    callback_data=f"admin_req_accept_{req.id}"
                )],
                [InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"admin_req_reject_{req.id}"
                )],
            ]
            await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=(
                    f"📬 **New Custom Request #{req.id}**\n\n"
                    f"👤 @{user.username or user.first_name or user.telegram_id}\n"
                    f"📝 {description}\n\n"
                    f"VIP Tier: {user.vip_tier}\n"
                    f"Total Spent: ${user.total_spent:.0f}"
                ),
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to notify admin of new request: {e}")

    finally:
        db.close()

    return ConversationHandler.END


async def cancel_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the custom request."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Request cancelled.")
    context.user_data.pop("custom_request_desc", None)
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation via /cancel."""
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def my_requests_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's custom requests and their status."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await update.message.reply_text("Please /start the bot first.")
            return

        requests = (
            db.query(CustomRequest)
            .filter(CustomRequest.user_id == user.id)
            .order_by(CustomRequest.created_at.desc())
            .limit(10)
            .all()
        )

        if not requests:
            await update.message.reply_text(
                "No custom requests yet.\n\n"
                "Submit one with /request ✨"
            )
            return

        status_emoji = {
            "pending": "⏳",
            "accepted": "💰",
            "completed": "✅",
            "rejected": "❌",
        }

        text = "📋 **Your Custom Requests**\n\n"
        keyboard = []
        for req in requests:
            emoji = status_emoji.get(req.status, "❓")
            desc_short = req.description[:40] + "..." if len(req.description) > 40 else req.description
            price_str = f" — ${req.price:.0f}" if req.price else ""

            text += f"{emoji} **#{req.id}**{price_str}\n  _{desc_short}_\n"

            if req.status == RequestStatus.ACCEPTED.value and req.price:
                keyboard.append([
                    InlineKeyboardButton(
                        f"💳 Pay #{req.id} — ${req.price:.0f}",
                        callback_data=f"pay_request_{req.id}"
                    )
                ])

        if keyboard:
            text += "\n💡 Pay accepted requests to get your custom content:"

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def pay_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pay for an accepted custom request."""
    query = update.callback_query
    await query.answer("Creating payment... 💳")

    req_id = int(query.data.split("_")[2])
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        req = db.query(CustomRequest).get(req_id)

        if not req or not user or req.user_id != user.id:
            await query.message.reply_text("Request not found.")
            return

        if req.status != RequestStatus.ACCEPTED.value:
            await query.message.reply_text("This request is not ready for payment.")
            return

        if not req.price:
            await query.message.reply_text("Price not set yet. Please wait.")
            return

        try:
            pp_result = await paypal.create_order(
                amount=req.price,
                description=f"Custom Request #{req.id}",
                custom_id=f"req_{req.id}",
            )
        except Exception as e:
            logger.error(f"PayPal order for request failed: {e}")
            await query.message.reply_text("❌ Payment error. Try again later.")
            return

        req.paypal_order_id = pp_result["order_id"]
        db.commit()

        keyboard = [
            [InlineKeyboardButton("💳 Pay Now", url=pp_result["approve_url"])],
        ]

        await query.message.reply_text(
            f"💳 **Pay for Custom Request #{req.id}**\n\n"
            f"💰 ${req.price:.0f}\n\n"
            f"Once paid, your custom content will be created and delivered! ⚡",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    finally:
        db.close()


def get_custom_request_handlers():
    request_conv = ConversationHandler(
        entry_points=[CommandHandler("request", custom_request_command)],
        states={
            AWAITING_REQUEST_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_request_description)
            ],
            AWAITING_REQUEST_CONFIRM: [
                CallbackQueryHandler(confirm_request_callback, pattern="^confirm_request$"),
                CallbackQueryHandler(cancel_request_callback, pattern="^cancel_request$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_message=False,
    )

    return [
        request_conv,
        CommandHandler("myrequests", my_requests_command),
        CallbackQueryHandler(pay_request_callback, pattern=r"^pay_request_\d+$"),
    ]
