import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from bot.models.database import SessionLocal
from bot.models.schemas import User, Image, Order, OrderStatus, LoyaltyRedemption

logger = logging.getLogger(__name__)

# Loyalty rewards catalog
REWARDS = {
    "unlock_basic": {
        "name": "Unlock Any Basic Image",
        "emoji": "🖼",
        "points": 500,
        "type": "image_unlock",
        "tier_limit": "basic",
    },
    "unlock_premium": {
        "name": "Unlock Any Premium Image",
        "emoji": "💎",
        "points": 1200,
        "type": "image_unlock",
        "tier_limit": "premium",
    },
    "discount_10": {
        "name": "10% Off Next Purchase",
        "emoji": "🏷",
        "points": 300,
        "type": "discount_10",
        "tier_limit": None,
    },
    "discount_25": {
        "name": "25% Off Next Purchase",
        "emoji": "🏷",
        "points": 700,
        "type": "discount_25",
        "tier_limit": None,
    },
    "free_unlock_token": {
        "name": "+1 Free Unlock Token",
        "emoji": "🎁",
        "points": 400,
        "type": "free_unlock",
        "tier_limit": None,
    },
}


async def loyalty_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show loyalty points balance and rewards catalog."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await update.message.reply_text("Please /start the bot first.")
            return

        # Count total redeemed
        total_redeemed = (
            db.query(LoyaltyRedemption)
            .filter(LoyaltyRedemption.user_id == user.id)
            .count()
        )

        text = (
            f"⭐ **Loyalty Points**\n\n"
            f"Your balance: **{user.loyalty_points:,} pts**\n"
            f"Rewards redeemed: **{total_redeemed}**\n\n"
            f"💡 Earn points:\n"
            f"  • 10 pts per $1 spent on images\n"
            f"  • 15 pts per $1 spent on subscriptions\n"
            f"  • 50 pts for each referral\n\n"
            f"🎁 **Rewards Catalog:**\n\n"
        )

        keyboard = []
        for reward_key, reward in REWARDS.items():
            can_afford = user.loyalty_points >= reward["points"]
            status = "✅" if can_afford else "🔒"
            text += (
                f"{reward['emoji']} **{reward['name']}**\n"
                f"  {status} {reward['points']:,} pts\n\n"
            )
            if can_afford:
                keyboard.append([
                    InlineKeyboardButton(
                        f"{reward['emoji']} Redeem: {reward['name']}",
                        callback_data=f"redeem_{reward_key}"
                    )
                ])

        if not keyboard:
            text += "_Keep shopping to earn more points!_ 🛍"

        keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")])

        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def loyalty_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback version for inline button."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            return

        text = (
            f"⭐ **Loyalty Points**\n\n"
            f"Balance: **{user.loyalty_points:,} pts**\n\n"
            f"🎁 **Available Rewards:**\n\n"
        )

        keyboard = []
        for reward_key, reward in REWARDS.items():
            can_afford = user.loyalty_points >= reward["points"]
            if can_afford:
                text += f"{reward['emoji']} {reward['name']} — {reward['points']:,} pts\n"
                keyboard.append([
                    InlineKeyboardButton(
                        f"{reward['emoji']} {reward['name']} ({reward['points']:,} pts)",
                        callback_data=f"redeem_{reward_key}"
                    )
                ])

        if not keyboard:
            text += f"_You need more points. Keep shopping!_ 🛍\n\n"
            text += "Cheapest reward: 300 pts (10% discount)"

        keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")])

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def redeem_reward_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem a loyalty reward."""
    query = update.callback_query
    reward_key = query.data.replace("redeem_", "")

    if reward_key not in REWARDS:
        await query.answer("Invalid reward.", show_alert=True)
        return

    reward = REWARDS[reward_key]
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await query.answer("Error. Try /start first.", show_alert=True)
            return

        if user.loyalty_points < reward["points"]:
            await query.answer(
                f"Not enough points! Need {reward['points']:,}, have {user.loyalty_points:,}.",
                show_alert=True
            )
            return

        # Process redemption
        reward_type = reward["type"]

        if reward_type == "free_unlock":
            user.loyalty_points -= reward["points"]
            user.free_unlocks += 1

            redemption = LoyaltyRedemption(
                user_id=user.id,
                points_spent=reward["points"],
                reward_type=reward_type,
            )
            db.add(redemption)
            db.commit()

            await query.answer()
            await query.message.reply_text(
                f"🎁 **Reward Redeemed!**\n\n"
                f"You got **+1 Free Unlock Token**!\n"
                f"Free unlocks: **{user.free_unlocks}**\n"
                f"Points remaining: **{user.loyalty_points:,}**\n\n"
                f"Use it in /browse 🖼",
                parse_mode="Markdown"
            )

        elif reward_type in ("discount_10", "discount_25"):
            pct = 10 if reward_type == "discount_10" else 25
            user.loyalty_points -= reward["points"]

            # Store discount in user_data for next purchase
            context.user_data["loyalty_discount"] = pct
            context.user_data["loyalty_discount_used"] = False

            redemption = LoyaltyRedemption(
                user_id=user.id,
                points_spent=reward["points"],
                reward_type=reward_type,
            )
            db.add(redemption)
            db.commit()

            await query.answer()
            await query.message.reply_text(
                f"🏷 **{pct}% Discount Activated!**\n\n"
                f"Your next purchase will be **{pct}% off**!\n"
                f"Points remaining: **{user.loyalty_points:,}**\n\n"
                f"Go grab something from /browse 🛍",
                parse_mode="Markdown"
            )

        elif reward_type == "image_unlock":
            # Store the unlock info for the user to pick an image
            tier_limit = reward.get("tier_limit", "basic")
            context.user_data["loyalty_unlock_tier"] = tier_limit
            user.loyalty_points -= reward["points"]

            redemption = LoyaltyRedemption(
                user_id=user.id,
                points_spent=reward["points"],
                reward_type=reward_type,
            )
            db.add(redemption)
            db.commit()

            # Show eligible images
            tier_filter = [tier_limit]
            if tier_limit == "premium":
                tier_filter = ["basic", "premium"]

            images = (
                db.query(Image)
                .filter(Image.is_active == True, Image.tier.in_(tier_filter))
                .order_by(Image.total_sales.desc())
                .limit(10)
                .all()
            )

            if not images:
                await query.message.reply_text(
                    "🎁 Reward redeemed! But no eligible images found right now.\n"
                    "A free unlock token has been added to your account instead."
                )
                user.free_unlocks += 1
                db.commit()
                return

            keyboard = [
                [InlineKeyboardButton(
                    f"🖼 {img.title}",
                    callback_data=f"loyalty_pick_{img.id}"
                )]
                for img in images
            ]

            await query.answer()
            await query.message.reply_text(
                f"🎁 **Pick an image to unlock for FREE!**\n\n"
                f"Tier: {tier_limit.title()} or below\n"
                f"Points remaining: **{user.loyalty_points:,}**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

    finally:
        db.close()


async def loyalty_pick_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picks a specific image to unlock with loyalty reward."""
    query = update.callback_query
    await query.answer()

    img_id = int(query.data.split("_")[2])
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        image = db.query(Image).get(img_id)

        if not user or not image:
            await query.message.reply_text("Something went wrong.")
            return

        # Check if already owned
        existing = (
            db.query(Order)
            .filter(
                Order.user_id == user.id,
                Order.image_id == image.id,
                Order.status == OrderStatus.COMPLETED.value,
            )
            .first()
        )
        if existing:
            await query.message.reply_text(
                "You already own this image! Pick a different one."
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
        image.total_sales += 1
        db.commit()

        # Send the image
        photo_source = bytes(image.file_data) if image.file_data else image.cloudinary_url
        await query.message.reply_photo(
            photo=photo_source,
            caption=(
                f"🎁 **{image.title}** — Unlocked with Loyalty Points!\n\n"
                f"Enjoy! 💋"
            ),
            parse_mode="Markdown"
        )

        # Upsell
        related = (
            db.query(Image)
            .filter(
                Image.category_id == image.category_id,
                Image.id != image.id,
                Image.is_active == True,
            )
            .limit(3)
            .all()
        )
        if related:
            upsell_kb = [
                [InlineKeyboardButton(
                    f"🔥 {r.title} — ${r.price:.0f}",
                    callback_data=f"img_{r.id}"
                )]
                for r in related
            ]
            await query.message.reply_text(
                "💡 **You might also like:**",
                reply_markup=InlineKeyboardMarkup(upsell_kb),
                parse_mode="Markdown"
            )

    finally:
        db.close()


def get_loyalty_handlers():
    return [
        CommandHandler("loyalty", loyalty_command),
        CommandHandler("points", loyalty_command),
        CallbackQueryHandler(loyalty_callback, pattern="^view_loyalty$"),
        CallbackQueryHandler(redeem_reward_callback, pattern=r"^redeem_"),
        CallbackQueryHandler(loyalty_pick_image_callback, pattern=r"^loyalty_pick_\d+$"),
    ]
