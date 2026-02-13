import logging
import datetime
from sqlalchemy.orm import Session
from bot.models.schemas import Order, Image, User, OrderStatus
from bot.models.database import SessionLocal

logger = logging.getLogger(__name__)


async def deliver_image(bot, order_id: int):
    """Deliver the purchased image to the user via Telegram."""
    db = SessionLocal()
    try:
        order = db.query(Order).get(order_id)
        if not order:
            logger.error(f"Order {order_id} not found for delivery")
            return False

        if order.status != OrderStatus.COMPLETED.value:
            logger.warning(f"Order {order_id} is not completed (status: {order.status})")
            return False

        user = db.query(User).get(order.user_id)
        image = db.query(Image).get(order.image_id)

        if not user or not image:
            logger.error(f"User or image not found for order {order_id}")
            return False

        # Send the full image (from DB bytes or legacy cloudinary URL)
        photo_source = image.file_data if image.file_data else image.cloudinary_url
        if not photo_source:
            logger.error(f"No image data for image {image.id}")
            return False

        await bot.send_photo(
            chat_id=user.telegram_id,
            photo=photo_source,
            caption=(
                f"✅ **Payment received!**\n\n"
                f"🖼 **{image.title}**\n"
                f"{image.description or ''}\n\n"
                f"Enjoy! 💋"
            ),
            parse_mode="Markdown"
        )

        # Upsell — suggest related content
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
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = [
                [InlineKeyboardButton(
                    f"🔥 {r.title} — ${r.price:.2f}",
                    callback_data=f"img_{r.id}"
                )]
                for r in related
            ]
            keyboard.append([
                InlineKeyboardButton("🖼 Browse More", callback_data="browse_categories")
            ])

            await bot.send_message(
                chat_id=user.telegram_id,
                text="💡 **You might also like:**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        logger.info(f"Delivered image {image.id} to user {user.telegram_id} (order {order_id})")
        return True

    except Exception as e:
        logger.error(f"Delivery failed for order {order_id}: {e}")
        return False
    finally:
        db.close()


def complete_order(paypal_order_id: str, capture_id: str = None) -> int:
    """
    Mark an order as completed after PayPal payment.
    Returns the order ID or 0 if not found.
    """
    db = SessionLocal()
    try:
        order = (
            db.query(Order)
            .filter(Order.paypal_order_id == paypal_order_id)
            .first()
        )

        if not order:
            logger.error(f"No order found for PayPal order {paypal_order_id}")
            return 0

        if order.status == OrderStatus.COMPLETED.value:
            logger.info(f"Order {order.id} already completed")
            return order.id

        order.status = OrderStatus.COMPLETED.value
        order.completed_at = datetime.datetime.utcnow()

        # Update image sales count
        image = db.query(Image).get(order.image_id)
        if image:
            image.total_sales += 1

        # Update user stats
        user = db.query(User).get(order.user_id)
        if user:
            user.total_spent += order.amount
            user.loyalty_points += int(order.amount * 10)  # 10 points per dollar

            # Auto-upgrade VIP tier
            if user.total_spent >= 150:
                user.vip_tier = "gold"
            elif user.total_spent >= 75:
                user.vip_tier = "silver"
            elif user.total_spent >= 25:
                user.vip_tier = "bronze"

        db.commit()
        logger.info(f"Order {order.id} completed for PayPal order {paypal_order_id}")
        return order.id

    except Exception as e:
        logger.error(f"Failed to complete order for {paypal_order_id}: {e}")
        db.rollback()
        return 0
    finally:
        db.close()
