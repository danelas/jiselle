import logging
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from sqlalchemy.orm import Session
from bot.models.database import SessionLocal
from bot.models.schemas import User

logger = logging.getLogger(__name__)


def _get_or_create_user(db: Session, telegram_id: int, username: str = None, first_name: str = None) -> User:
    """Get existing user or create a new one."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        referral_code = uuid.uuid4().hex[:8].upper()
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            referral_code=referral_code,
            free_unlocks=1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        import datetime
        user.last_active = datetime.datetime.utcnow()
        if username:
            user.username = username
        if first_name:
            user.first_name = first_name
        db.commit()
    return user


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command — welcome funnel."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = _get_or_create_user(
            db, tg_user.id, tg_user.username, tg_user.first_name
        )
        is_new = user.free_unlocks > 0

        welcome_text = (
            f"Hey {tg_user.first_name or 'babe'} 💋\n\n"
            f"Welcome to my exclusive collection.\n"
            f"I have special content just for you...\n\n"
        )

        if is_new:
            welcome_text += (
                "🎁 **You have 1 FREE unlock** waiting for you!\n"
                "Browse my collection and pick your favorite.\n\n"
            )

        welcome_text += "What would you like to do?"

        keyboard = []
        if is_new:
            keyboard.append([
                InlineKeyboardButton("🎁 Claim Free Image", callback_data="claim_free")
            ])
        keyboard.extend([
            [InlineKeyboardButton("🖼 Browse Collection", callback_data="browse_categories")],
            [InlineKeyboardButton("🔥 What's Hot", callback_data="browse_popular")],
            [
                InlineKeyboardButton("⚡ Deals", callback_data="view_deals"),
                InlineKeyboardButton("⭐ Loyalty", callback_data="view_loyalty"),
            ],
            [InlineKeyboardButton("💎 VIP Subscribe", callback_data="vip_info")],
            [InlineKeyboardButton("✨ Custom Request", callback_data="start_custom_request")],
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            welcome_text, reply_markup=reply_markup, parse_mode="Markdown"
        )
    finally:
        db.close()


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "💡 **How it works:**\n\n"
        "1️⃣ Browse my collection by category\n"
        "2️⃣ See a preview of what you like\n"
        "3️⃣ Tap 'Unlock' to get the payment link\n"
        "4️⃣ Pay securely via PayPal\n"
        "5️⃣ Image is sent to you instantly!\n\n"
        "**Commands:**\n"
        "/start — Main menu\n"
        "/browse — Browse collection\n"
        "/popular — Most popular content\n"
        "/deals — Flash sales & deals\n"
        "/subscribe — VIP subscriptions\n"
        "/loyalty — Points & rewards\n"
        "/request — Custom content request\n"
        "/mypurchases — Your unlocked content\n"
        "/myrequests — Your custom requests\n"
        "/referral — Share & earn free unlocks\n"
        "/newchat — Start a fresh conversation with me\n"
        "/help — This message\n"
        "\n💬 **Just type anything** to chat with me!"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def vip_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show VIP tier info."""
    query = update.callback_query
    await query.answer()

    vip_text = (
        "💎 **VIP Tiers**\n\n"
        "🥉 **Bronze** — 5% off all purchases\n"
        "  Spend $25+ total to unlock\n\n"
        "🥈 **Silver** — 10% off + early access to new drops\n"
        "  Spend $75+ total to unlock\n\n"
        "🥇 **Gold** — 20% off + exclusive content + priority requests\n"
        "  Spend $150+ total to unlock\n\n"
        "Your tier upgrades automatically as you shop! 🛍"
    )

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    await query.edit_message_text(
        vip_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, tg_user.id)
        has_free = user.free_unlocks > 0
    finally:
        db.close()

    text = f"Hey {tg_user.first_name or 'babe'} 💋\nWhat would you like to do?"

    keyboard = []
    if has_free:
        keyboard.append([
            InlineKeyboardButton("🎁 Claim Free Image", callback_data="claim_free")
        ])
    keyboard.extend([
        [InlineKeyboardButton("🖼 Browse Collection", callback_data="browse_categories")],
        [InlineKeyboardButton("🔥 What's Hot", callback_data="browse_popular")],
        [
            InlineKeyboardButton("⚡ Deals", callback_data="view_deals"),
            InlineKeyboardButton("⭐ Loyalty", callback_data="view_loyalty"),
        ],
        [InlineKeyboardButton("💎 VIP Subscribe", callback_data="vip_info")],
        [InlineKeyboardButton("✨ Custom Request", callback_data="start_custom_request")],
    ])

    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )


async def claim_free_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redirect to browse so user can pick their free image."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = _get_or_create_user(db, tg_user.id)
        free_count = user.free_unlocks or 0
    finally:
        db.close()

    if free_count <= 0:
        await query.edit_message_text(
            "You've already used your free unlock! \n\n"
            "💡 Refer friends to earn more: /referral\n"
            "Or browse my collection to find something you love 💋",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🖼 Browse Collection", callback_data="browse_categories")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")],
            ]),
            parse_mode="Markdown"
        )
        return

    await query.edit_message_text(
        f"🎁 **You have {free_count} free unlock{'s' if free_count > 1 else ''}!**\n\n"
        f"Browse my categories and tap **🎁 Use Free Unlock** on any basic image.\n"
        f"Pick your favorite — it's on me 💋",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🖼 Browse & Choose", callback_data="browse_categories")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")],
        ]),
        parse_mode="Markdown"
    )


async def start_custom_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redirect to /request flow via inline button."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "✨ **Custom Content Request**\n\n"
        "Describe what you'd like created. Be as specific as possible!\n"
        "The more detail you give, the better the result.\n\n"
        "💰 Pricing is set per request based on complexity.\n"
        "Typical range: **$10 — $50**\n\n"
        "Use /request to submit your description.",
        parse_mode="Markdown"
    )


def get_start_handlers():
    return [
        CommandHandler("start", start_command),
        CommandHandler("help", help_command),
        CallbackQueryHandler(vip_info_callback, pattern="^vip_info$"),
        CallbackQueryHandler(back_to_menu_callback, pattern="^back_to_menu$"),
        CallbackQueryHandler(start_custom_request_callback, pattern="^start_custom_request$"),
        CallbackQueryHandler(claim_free_callback, pattern="^claim_free$"),
    ]
