import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from bot.models.database import SessionLocal
from bot.models.schemas import User, Image, Order, OrderStatus
from bot.services import paypal
from bot.handlers.flash_sales import get_flash_price

logger = logging.getLogger(__name__)


def _get_user_discount(user: User) -> float:
    """Return discount multiplier based on VIP tier."""
    if not user:
        return 1.0
    tier_discounts = {
        "bronze": 0.95,
        "silver": 0.90,
        "gold": 0.80,
    }
    return tier_discounts.get(user.vip_tier, 1.0)


def _update_vip_tier(user: User, db):
    """Auto-upgrade VIP tier based on total spending."""
    if user.total_spent >= 150:
        user.vip_tier = "gold"
    elif user.total_spent >= 75:
        user.vip_tier = "silver"
    elif user.total_spent >= 25:
        user.vip_tier = "bronze"
    db.commit()


async def buy_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate PayPal purchase for an image."""
    query = update.callback_query
    await query.answer("Creating payment link... 💳")

    img_id = int(query.data.split("_")[1])
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        image = db.query(Image).get(img_id)

        if not image:
            await query.edit_message_text("Image not found.")
            return

        if not user:
            await query.edit_message_text("Please /start the bot first.")
            return

        # Check if already owned
        existing = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.image_id == image.id,
                Order.status == OrderStatus.COMPLETED.value
            )
            .first()
        )
        if existing:
            await query.message.reply_photo(
                photo=image.cloudinary_url,
                caption=f"✅ You already own **{image.title}**! Here it is:",
                parse_mode="Markdown"
            )
            return

        # Calculate price with flash sale + VIP discount
        sale_price, _, _ = get_flash_price(image, db)
        discount = _get_user_discount(user)
        final_price = round(sale_price * discount, 2)

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
        try:
            pp_result = await paypal.create_order(
                amount=final_price,
                description=f"Unlock: {image.title}",
                custom_id=str(order.id),
            )
        except Exception as e:
            logger.error(f"PayPal order creation failed: {e}")
            order.status = OrderStatus.FAILED.value
            db.commit()
            await query.message.reply_text(
                "❌ Payment system error. Please try again later."
            )
            return

        # Store PayPal order ID
        order.paypal_order_id = pp_result["order_id"]
        db.commit()

        approve_url = pp_result["approve_url"]

        keyboard = [
            [InlineKeyboardButton("💳 Pay Now with PayPal", url=approve_url)],
            [InlineKeyboardButton("🔙 Back", callback_data=f"img_{img_id}")],
        ]

        await query.message.reply_text(
            f"💳 **Payment Ready!**\n\n"
            f"🖼 {image.title}\n"
            f"💰 ${final_price:.2f}\n\n"
            f"Click the button below to pay securely via PayPal.\n"
            f"Your image will be sent **instantly** after payment! ⚡",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    finally:
        db.close()


async def free_unlock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Use a free unlock on an image."""
    query = update.callback_query
    await query.answer()

    img_id = int(query.data.split("_")[1])
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        image = db.query(Image).get(img_id)

        if not user or not image:
            await query.edit_message_text("Something went wrong. Try /start again.")
            return

        if user.free_unlocks <= 0:
            await query.message.reply_text(
                "❌ No free unlocks remaining.\n"
                "💡 Refer friends to earn more! Use /referral"
            )
            return

        # Only allow free unlock on basic tier
        if image.tier not in ("free", "basic"):
            await query.message.reply_text(
                "🎁 Free unlocks can only be used on Basic tier content.\n"
                "This is Premium/VIP content — unlock it with 💳"
            )
            return

        # Create completed order
        order = Order(
            user_id=user.id,
            image_id=image.id,
            amount=0.0,
            status=OrderStatus.COMPLETED.value,
            completed_at=datetime.datetime.utcnow(),
        )
        db.add(order)
        user.free_unlocks -= 1
        image.total_sales += 1
        db.commit()

        # Send the full image
        await query.message.reply_photo(
            photo=image.cloudinary_url,
            caption=(
                f"🎁 **{image.title}** — Unlocked for FREE!\n\n"
                f"Enjoy! Want more? Browse my collection 💋"
            ),
            parse_mode="Markdown"
        )

        # Upsell: suggest related content
        related = (
            db.query(Image)
            .filter(
                Image.category_id == image.category_id,
                Image.id != image.id,
                Image.is_active == True
            )
            .limit(3)
            .all()
        )

        if related:
            keyboard = [
                [InlineKeyboardButton(
                    f"🔥 {r.title} — ${r.price:.2f}",
                    callback_data=f"img_{r.id}"
                )]
                for r in related
            ]
            keyboard.append([InlineKeyboardButton("🖼 Browse All", callback_data="browse_categories")])

            await query.message.reply_text(
                "💡 **You might also like:**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

    finally:
        db.close()


async def my_purchases_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's purchased images."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await update.message.reply_text("Please /start the bot first.")
            return

        orders = (
            db.query(Order)
            .filter(Order.user_id == user.id, Order.status == OrderStatus.COMPLETED.value)
            .order_by(Order.completed_at.desc())
            .limit(20)
            .all()
        )

        if not orders:
            await update.message.reply_text(
                "You haven't unlocked any content yet!\n\n"
                "🖼 /browse to see my collection"
            )
            return

        text = f"📦 **Your Unlocked Content** ({len(orders)} items)\n\n"
        keyboard = []
        for order in orders:
            image = db.query(Image).get(order.image_id)
            if image:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📸 {image.title}",
                        callback_data=f"resend_{image.id}"
                    )
                ])

        text += "Tap any item to get it re-sent:"

        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def resend_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-send a previously purchased image."""
    query = update.callback_query
    await query.answer()

    img_id = int(query.data.split("_")[1])
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        image = db.query(Image).get(img_id)

        if not user or not image:
            return

        # Verify ownership
        owned = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.image_id == image.id,
                Order.status == OrderStatus.COMPLETED.value
            )
            .first()
        )

        if not owned:
            await query.message.reply_text("❌ You don't own this image.")
            return

        await query.message.reply_photo(
            photo=image.cloudinary_url,
            caption=f"📸 **{image.title}**",
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral info and code."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await update.message.reply_text("Please /start the bot first.")
            return

        # Count successful referrals
        referral_count = db.query(User).filter(User.referred_by == user.id).count()

        bot_info = await context.bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start=ref_{user.referral_code}"

        text = (
            f"🎁 **Your Referral Program**\n\n"
            f"Share your link and earn **1 free unlock** for each friend who joins!\n\n"
            f"🔗 Your link:\n`{referral_link}`\n\n"
            f"👥 Referrals so far: **{referral_count}**\n"
            f"🎁 Free unlocks remaining: **{user.free_unlocks}**"
        )

        await update.message.reply_text(text, parse_mode="Markdown")
    finally:
        db.close()


def get_purchase_handlers():
    return [
        CommandHandler("mypurchases", my_purchases_command),
        CommandHandler("referral", referral_command),
        CallbackQueryHandler(buy_image_callback, pattern=r"^buy_\d+$"),
        CallbackQueryHandler(free_unlock_callback, pattern=r"^free_\d+$"),
        CallbackQueryHandler(resend_image_callback, pattern=r"^resend_\d+$"),
    ]
