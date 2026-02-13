import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from bot.models.database import SessionLocal
from bot.models.schemas import User, Subscription, SubscriptionStatus, Image, Order, OrderStatus
from bot.services import paypal

logger = logging.getLogger(__name__)

# Subscription tier config
SUB_TIERS = {
    "bronze": {
        "name": "Bronze",
        "emoji": "🥉",
        "price": 9.99,
        "discount": 5,
        "perks": ["5% off all purchases", "Monthly exclusive image"],
    },
    "silver": {
        "name": "Silver",
        "emoji": "🥈",
        "price": 19.99,
        "discount": 10,
        "perks": ["10% off all purchases", "3 exclusive images/month", "Early access to new drops"],
    },
    "gold": {
        "name": "Gold",
        "emoji": "🥇",
        "price": 39.99,
        "discount": 20,
        "perks": [
            "20% off all purchases",
            "5 exclusive images/month",
            "Early access to new drops",
            "Priority custom requests",
            "Exclusive VIP-only content",
        ],
    },
}


def _get_active_sub(db, user_id: int) -> Subscription:
    """Get user's active subscription if any."""
    return (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.expires_at > datetime.datetime.utcnow(),
        )
        .first()
    )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show subscription tiers."""
    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await update.message.reply_text("Please /start the bot first.")
            return

        active_sub = _get_active_sub(db, user.id)

        text = "💎 **VIP Subscriptions**\n\n"

        if active_sub:
            tier_info = SUB_TIERS.get(active_sub.tier, {})
            text += (
                f"✅ You're currently on **{tier_info.get('emoji', '')} "
                f"{tier_info.get('name', active_sub.tier)}**\n"
                f"Expires: {active_sub.expires_at.strftime('%B %d, %Y')}\n\n"
                f"─────────────────\n\n"
            )

        for tier_key, tier in SUB_TIERS.items():
            text += (
                f"{tier['emoji']} **{tier['name']}** — ${tier['price']:.0f}/mo\n"
            )
            for perk in tier["perks"]:
                text += f"  • {perk}\n"
            text += "\n"

        keyboard = []
        for tier_key, tier in SUB_TIERS.items():
            if active_sub and active_sub.tier == tier_key:
                keyboard.append([
                    InlineKeyboardButton(
                        f"✅ {tier['emoji']} {tier['name']} (Current)",
                        callback_data=f"sub_current"
                    )
                ])
            else:
                label = "Upgrade" if active_sub else "Subscribe"
                keyboard.append([
                    InlineKeyboardButton(
                        f"{tier['emoji']} {label} {tier['name']} — ${tier['price']:.0f}/mo",
                        callback_data=f"sub_{tier_key}"
                    )
                ])

        if active_sub:
            keyboard.append([
                InlineKeyboardButton("❌ Cancel Subscription", callback_data="sub_cancel")
            ])

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")])

        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def subscribe_tier_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription purchase for a specific tier."""
    query = update.callback_query
    await query.answer("Processing... 💳")

    tier_key = query.data.split("_")[1]
    if tier_key not in SUB_TIERS:
        await query.message.reply_text("Invalid tier.")
        return

    tier = SUB_TIERS[tier_key]
    tg_user = update.effective_user

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            await query.message.reply_text("Please /start the bot first.")
            return

        # Create subscription record
        sub = Subscription(
            user_id=user.id,
            tier=tier_key,
            price_monthly=tier["price"],
            status=SubscriptionStatus.PENDING.value,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)

        # Create PayPal order
        try:
            pp_result = await paypal.create_order(
                amount=tier["price"],
                description=f"VIP {tier['name']} Subscription (1 month)",
                custom_id=f"sub_{sub.id}",
            )
        except Exception as e:
            logger.error(f"PayPal subscription order failed: {e}")
            sub.status = SubscriptionStatus.EXPIRED.value
            db.commit()
            await query.message.reply_text("❌ Payment system error. Try again later.")
            return

        sub.paypal_order_id = pp_result["order_id"]
        db.commit()

        keyboard = [
            [InlineKeyboardButton("💳 Pay Now with PayPal", url=pp_result["approve_url"])],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")],
        ]

        await query.message.reply_text(
            f"💎 **{tier['emoji']} {tier['name']} VIP Subscription**\n\n"
            f"💰 ${tier['price']:.0f} for 1 month\n\n"
            f"**Perks:**\n" +
            "\n".join(f"  • {p}" for p in tier["perks"]) +
            f"\n\nClick below to pay. Your VIP access activates **instantly**! ⚡",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def cancel_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel active subscription."""
    query = update.callback_query
    await query.answer()

    tg_user = update.effective_user
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        if not user:
            return

        active_sub = _get_active_sub(db, user.id)
        if not active_sub:
            await query.message.reply_text("You don't have an active subscription.")
            return

        active_sub.status = SubscriptionStatus.CANCELLED.value
        active_sub.cancelled_at = datetime.datetime.utcnow()
        db.commit()

        await query.message.reply_text(
            f"❌ **Subscription Cancelled**\n\n"
            f"Your VIP access remains active until "
            f"**{active_sub.expires_at.strftime('%B %d, %Y')}**.\n\n"
            f"We'll miss you! You can re-subscribe anytime with /subscribe 💋",
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def sub_current_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle click on current tier."""
    query = update.callback_query
    await query.answer("This is your current plan! ✅", show_alert=True)


def activate_subscription(paypal_order_id: str) -> int:
    """Activate a subscription after payment. Returns sub ID or 0."""
    db = SessionLocal()
    try:
        sub = (
            db.query(Subscription)
            .filter(Subscription.paypal_order_id == paypal_order_id)
            .first()
        )
        if not sub:
            return 0

        if sub.status == SubscriptionStatus.ACTIVE.value:
            return sub.id

        now = datetime.datetime.utcnow()
        sub.status = SubscriptionStatus.ACTIVE.value
        sub.started_at = now
        sub.expires_at = now + datetime.timedelta(days=30)

        # Update user's VIP tier
        user = db.query(User).get(sub.user_id)
        if user:
            tier_rank = {"bronze": 1, "silver": 2, "gold": 3}
            current_rank = tier_rank.get(user.vip_tier, 0)
            new_rank = tier_rank.get(sub.tier, 0)
            if new_rank >= current_rank:
                user.vip_tier = sub.tier
            user.total_spent += sub.price_monthly
            user.loyalty_points += int(sub.price_monthly * 15)  # 15 pts/$ for subs (bonus)

        db.commit()
        logger.info(f"Subscription {sub.id} activated for user {sub.user_id}, tier={sub.tier}")
        return sub.id
    except Exception as e:
        logger.error(f"Subscription activation failed: {e}")
        db.rollback()
        return 0
    finally:
        db.close()


def get_subscription_handlers():
    return [
        CommandHandler("subscribe", subscribe_command),
        CallbackQueryHandler(subscribe_tier_callback, pattern=r"^sub_(bronze|silver|gold)$"),
        CallbackQueryHandler(cancel_subscription_callback, pattern="^sub_cancel$"),
        CallbackQueryHandler(sub_current_callback, pattern="^sub_current$"),
    ]
