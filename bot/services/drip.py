import logging
import datetime
from sqlalchemy.orm import Session
from bot.models.database import SessionLocal
from bot.models.schemas import (
    DripSchedule, Image, User, Subscription, SubscriptionStatus
)

logger = logging.getLogger(__name__)

TIER_RANK = {"free": 0, "bronze": 1, "silver": 2, "gold": 3}


def _user_meets_tier(user: User, required_tier: str, db: Session) -> bool:
    """Check if user's VIP tier meets the required tier for drip content."""
    user_rank = TIER_RANK.get(user.vip_tier, 0)
    required_rank = TIER_RANK.get(required_tier, 0)

    if user_rank >= required_rank:
        return True

    # Also check active subscription
    active_sub = (
        db.query(Subscription)
        .filter(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.expires_at > datetime.datetime.utcnow(),
        )
        .first()
    )
    if active_sub:
        sub_rank = TIER_RANK.get(active_sub.tier, 0)
        if sub_rank >= required_rank:
            return True

    return False


async def process_drip_content(bot):
    """
    Check for drip content that needs to be sent.
    Called periodically by the scheduler.
    """
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()

        # Get all unsent drip items that are due
        due_drips = (
            db.query(DripSchedule)
            .filter(
                DripSchedule.sent == False,
                DripSchedule.send_at <= now,
            )
            .all()
        )

        if not due_drips:
            return

        logger.info(f"Processing {len(due_drips)} drip content items")

        for drip in due_drips:
            image = db.query(Image).get(drip.image_id)
            if not image:
                drip.sent = True
                db.commit()
                continue

            required_tier = drip.tier_required or "free"

            # Get all eligible users
            users = db.query(User).filter(User.is_banned == False).all()

            sent_count = 0
            for user in users:
                if not _user_meets_tier(user, required_tier, db):
                    continue

                try:
                    teaser = drip.message_text or f"Here's something special for you... 💋"

                    if required_tier == "free":
                        # Free drip: send preview + CTA to buy
                        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                        keyboard = [[
                            InlineKeyboardButton(
                                f"🔓 Unlock Full Image — ${image.price:.2f}",
                                callback_data=f"img_{image.id}"
                            )
                        ]]
                        photo_src = image.file_data if image.file_data else (image.preview_url or image.cloudinary_url)
                        await bot.send_photo(
                            chat_id=user.telegram_id,
                            photo=photo_src,
                            caption=f"🔥 **New Drop!**\n\n{teaser}\n\n{image.title}",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="Markdown"
                        )
                    else:
                        # Paid tier drip: send full image as perk
                        photo_src = image.file_data if image.file_data else image.cloudinary_url
                        await bot.send_photo(
                            chat_id=user.telegram_id,
                            photo=photo_src,
                            caption=(
                                f"💎 **VIP Exclusive Drop!**\n\n"
                                f"{teaser}\n\n"
                                f"🖼 {image.title}\n"
                                f"This is a perk of your VIP membership 👑"
                            ),
                            parse_mode="Markdown"
                        )

                    sent_count += 1
                except Exception as e:
                    logger.warning(f"Failed to send drip to {user.telegram_id}: {e}")

            drip.sent = True
            db.commit()
            logger.info(f"Drip {drip.id} sent to {sent_count} users")

    except Exception as e:
        logger.error(f"Drip processing error: {e}")
    finally:
        db.close()


async def check_flash_sales(bot):
    """
    Check for flash sales that need announcements.
    Called periodically by the scheduler.
    """
    from bot.models.schemas import FlashSale, Category
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()

        # Find active flash sales that haven't been announced
        sales = (
            db.query(FlashSale)
            .filter(
                FlashSale.is_active == True,
                FlashSale.announcement_sent == False,
                FlashSale.starts_at <= now,
                FlashSale.ends_at > now,
            )
            .all()
        )

        for sale in sales:
            # Calculate time remaining
            remaining = sale.ends_at - now
            hours_left = int(remaining.total_seconds() / 3600)
            mins_left = int((remaining.total_seconds() % 3600) / 60)

            cat_name = "All Categories"
            if sale.category_id:
                cat = db.query(Category).get(sale.category_id)
                if cat:
                    cat_name = f"{cat.emoji or ''} {cat.name}"

            text = (
                f"⚡ **FLASH SALE!** ⚡\n\n"
                f"🏷 **{sale.title}**\n"
                f"💥 **{sale.discount_percent}% OFF** {cat_name}\n\n"
                f"⏰ Ends in: **{hours_left}h {mins_left}m**\n\n"
                f"Don't miss out! 🔥"
            )

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [[
                InlineKeyboardButton("🛒 Shop Now", callback_data="browse_categories")
            ]]

            users = db.query(User).filter(User.is_banned == False).all()
            for user in users:
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            sale.announcement_sent = True
            db.commit()
            logger.info(f"Flash sale {sale.id} announced to {len(users)} users")

        # Auto-deactivate expired flash sales
        expired = (
            db.query(FlashSale)
            .filter(
                FlashSale.is_active == True,
                FlashSale.ends_at <= now,
            )
            .all()
        )
        for sale in expired:
            sale.is_active = False
        if expired:
            db.commit()
            logger.info(f"Deactivated {len(expired)} expired flash sales")

    except Exception as e:
        logger.error(f"Flash sale check error: {e}")
    finally:
        db.close()


async def check_expiring_subscriptions(bot):
    """Notify users whose subscriptions are expiring soon."""
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        tomorrow = now + datetime.timedelta(days=1)

        # Find subscriptions expiring in the next 24 hours
        expiring = (
            db.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.expires_at <= tomorrow,
                Subscription.expires_at > now,
            )
            .all()
        )

        for sub in expiring:
            user = db.query(User).get(sub.user_id)
            if not user:
                continue

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [[
                InlineKeyboardButton("🔄 Renew Now", callback_data=f"sub_{sub.tier}")
            ]]

            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        f"⚠️ **Your VIP subscription expires tomorrow!**\n\n"
                        f"Don't lose your {sub.tier.title()} perks!\n"
                        f"Renew now to keep your discounts and exclusive access.\n\n"
                        f"💡 Tap below to renew instantly."
                    ),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to notify expiring sub for user {user.telegram_id}: {e}")

        # Expire overdue subscriptions
        overdue = (
            db.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.expires_at <= now,
            )
            .all()
        )
        for sub in overdue:
            sub.status = SubscriptionStatus.EXPIRED.value
            # Reset user tier if no other active sub
            user = db.query(User).get(sub.user_id)
            if user:
                other_active = (
                    db.query(Subscription)
                    .filter(
                        Subscription.user_id == user.id,
                        Subscription.id != sub.id,
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.expires_at > now,
                    )
                    .first()
                )
                if not other_active:
                    # Revert to spending-based tier
                    if user.total_spent >= 150:
                        user.vip_tier = "gold"
                    elif user.total_spent >= 75:
                        user.vip_tier = "silver"
                    elif user.total_spent >= 25:
                        user.vip_tier = "bronze"
                    else:
                        user.vip_tier = "free"

        if overdue:
            db.commit()
            logger.info(f"Expired {len(overdue)} subscriptions")

    except Exception as e:
        logger.error(f"Subscription expiry check error: {e}")
    finally:
        db.close()
