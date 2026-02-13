import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from bot.models.database import SessionLocal
from bot.models.schemas import FlashSale, Image, Category, User

logger = logging.getLogger(__name__)


def get_active_flash_sale(db) -> FlashSale:
    """Get the current active flash sale, if any."""
    now = datetime.datetime.utcnow()
    return (
        db.query(FlashSale)
        .filter(
            FlashSale.is_active == True,
            FlashSale.starts_at <= now,
            FlashSale.ends_at > now,
        )
        .first()
    )


def get_flash_price(image: Image, db) -> tuple:
    """
    Get the effective price for an image considering active flash sales.
    Returns (price, discount_percent, is_on_sale).
    """
    sale = get_active_flash_sale(db)
    if not sale:
        return image.price, 0, False

    # Check if sale applies to this image's category
    if sale.category_id and sale.category_id != image.category_id:
        return image.price, 0, False

    discounted = round(image.price * (1 - sale.discount_percent / 100), 2)
    return discounted, sale.discount_percent, True


async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active flash sales and deals."""
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        sale = get_active_flash_sale(db)

        if not sale:
            # Show upcoming sales if any
            upcoming = (
                db.query(FlashSale)
                .filter(FlashSale.is_active == True, FlashSale.starts_at > now)
                .order_by(FlashSale.starts_at)
                .first()
            )

            if upcoming:
                time_until = upcoming.starts_at - now
                hours = int(time_until.total_seconds() / 3600)
                mins = int((time_until.total_seconds() % 3600) / 60)

                await update.message.reply_text(
                    f"⏳ **No active sales right now**\n\n"
                    f"But something's coming...\n"
                    f"🔥 **{upcoming.title}** starts in **{hours}h {mins}m**!\n\n"
                    f"Stay tuned! 👀",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    "No active deals right now.\n\n"
                    "Follow me for flash sale notifications! 🔔"
                )
            return

        # Active sale
        remaining = sale.ends_at - now
        hours_left = int(remaining.total_seconds() / 3600)
        mins_left = int((remaining.total_seconds() % 3600) / 60)

        cat_name = "Everything"
        if sale.category_id:
            cat = db.query(Category).get(sale.category_id)
            if cat:
                cat_name = f"{cat.emoji or ''} {cat.name}"

        text = (
            f"⚡ **FLASH SALE — LIVE NOW!** ⚡\n\n"
            f"🏷 **{sale.title}**\n"
            f"💥 **{sale.discount_percent}% OFF** {cat_name}\n"
            f"⏰ Ends in: **{hours_left}h {mins_left}m**\n\n"
        )

        # Show sale items
        query = db.query(Image).filter(Image.is_active == True)
        if sale.category_id:
            query = query.filter(Image.category_id == sale.category_id)

        images = query.order_by(Image.total_sales.desc()).limit(10).all()

        keyboard = []
        for img in images:
            original = img.price
            discounted = round(original * (1 - sale.discount_percent / 100), 2)
            keyboard.append([
                InlineKeyboardButton(
                    f"🔥 {img.title} — ~${original:.0f}~ ${discounted:.0f}",
                    callback_data=f"img_{img.id}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🖼 Browse All", callback_data="browse_categories")])
        keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")])

        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def deals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback version of deals for inline buttons."""
    query = update.callback_query
    await query.answer()

    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        sale = get_active_flash_sale(db)

        if not sale:
            await query.edit_message_text(
                "No active deals right now. Check back soon! 🔔",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")]
                ])
            )
            return

        remaining = sale.ends_at - now
        hours_left = int(remaining.total_seconds() / 3600)
        mins_left = int((remaining.total_seconds() % 3600) / 60)

        cat_name = "Everything"
        if sale.category_id:
            cat = db.query(Category).get(sale.category_id)
            if cat:
                cat_name = f"{cat.emoji or ''} {cat.name}"

        text = (
            f"⚡ **FLASH SALE!** ⚡\n\n"
            f"🏷 **{sale.title}**\n"
            f"💥 **{sale.discount_percent}% OFF** {cat_name}\n"
            f"⏰ **{hours_left}h {mins_left}m** left!\n\n"
        )

        img_query = db.query(Image).filter(Image.is_active == True)
        if sale.category_id:
            img_query = img_query.filter(Image.category_id == sale.category_id)

        images = img_query.order_by(Image.total_sales.desc()).limit(8).all()

        keyboard = []
        for img in images:
            original = img.price
            discounted = round(original * (1 - sale.discount_percent / 100), 2)
            keyboard.append([
                InlineKeyboardButton(
                    f"🔥 {img.title} — ${discounted:.0f} (was ${original:.0f})",
                    callback_data=f"img_{img.id}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 Menu", callback_data="back_to_menu")])

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


def get_flash_sale_handlers():
    return [
        CommandHandler("deals", deals_command),
        CallbackQueryHandler(deals_callback, pattern="^view_deals$"),
    ]
