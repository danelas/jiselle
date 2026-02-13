import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from sqlalchemy.orm import Session
from bot.models.database import SessionLocal
from bot.models.schemas import Category, Image, User, Order, OrderStatus, ContentType
from bot.handlers.flash_sales import get_flash_price

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 6


async def browse_categories_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available categories."""
    query = update.callback_query
    await query.answer()

    db = SessionLocal()
    try:
        categories = (
            db.query(Category)
            .filter(Category.is_active == True)
            .order_by(Category.sort_order)
            .all()
        )

        # Only show categories that have private (purchasable) images
        visible = []
        for cat in categories:
            img_count = db.query(Image).filter(
                Image.category_id == cat.id,
                Image.is_active == True,
                Image.content_type == ContentType.PRIVATE.value,
            ).count()
            if img_count > 0:
                visible.append((cat, img_count))

        if not visible:
            await query.edit_message_text(
                "No categories available yet. Check back soon! \uD83D\uDCAB",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("\uD83D\uDD19 Back", callback_data="back_to_menu")]
                ])
            )
            return

        keyboard = []
        for cat, img_count in visible:
            emoji = cat.emoji or "\uD83D\uDCC1"
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {cat.name} ({img_count})",
                    callback_data=f"cat_{cat.id}_0"
                )
            ])

        keyboard.append([InlineKeyboardButton("\uD83D\uDD19 Back", callback_data="back_to_menu")])

        await query.edit_message_text(
            "\uD83D\uDCC1 **Choose a category:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def browse_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /browse command."""
    db = SessionLocal()
    try:
        categories = (
            db.query(Category)
            .filter(Category.is_active == True)
            .order_by(Category.sort_order)
            .all()
        )

        visible = []
        for cat in categories:
            img_count = db.query(Image).filter(
                Image.category_id == cat.id,
                Image.is_active == True,
                Image.content_type == ContentType.PRIVATE.value,
            ).count()
            if img_count > 0:
                visible.append((cat, img_count))

        if not visible:
            await update.message.reply_text("No categories available yet. Check back soon! \uD83D\uDCAB")
            return

        keyboard = []
        for cat, img_count in visible:
            emoji = cat.emoji or "\uD83D\uDCC1"
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {cat.name} ({img_count})",
                    callback_data=f"cat_{cat.id}_0"
                )
            ])

        await update.message.reply_text(
            "📂 **Choose a category:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def category_images_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show images in a category with pagination."""
    query = update.callback_query
    await query.answer()

    data = query.data  # cat_{id}_{page}
    parts = data.split("_")
    cat_id = int(parts[1])
    page = int(parts[2])

    db = SessionLocal()
    try:
        category = db.query(Category).get(cat_id)
        if not category:
            await query.edit_message_text("Category not found.")
            return

        images = (
            db.query(Image)
            .filter(Image.category_id == cat_id, Image.is_active == True)
            .order_by(Image.created_at.desc())
            .offset(page * ITEMS_PER_PAGE)
            .limit(ITEMS_PER_PAGE)
            .all()
        )

        total_count = db.query(Image).filter(
            Image.category_id == cat_id, Image.is_active == True
        ).count()

        if not images:
            await query.edit_message_text(
                f"No images in {category.name} yet!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Categories", callback_data="browse_categories")]
                ])
            )
            return

        # Check what user already owns
        tg_user = update.effective_user
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()
        owned_ids = set()
        if user:
            owned_orders = (
                db.query(Order.image_id)
                .filter(Order.user_id == user.id, Order.status == OrderStatus.COMPLETED.value)
                .all()
            )
            owned_ids = {o[0] for o in owned_orders}

        text = f"{category.emoji or '📁'} **{category.name}**\n\n"

        keyboard = []
        for img in images:
            owned = img.id in owned_ids
            if owned:
                status = "✅"
            else:
                sale_price, discount_pct, on_sale = get_flash_price(img, db)
                if on_sale:
                    status = f"~${img.price:.0f}~ ${sale_price:.0f} 🔥"
                else:
                    status = f"${img.price:.0f}"
            keyboard.append([
                InlineKeyboardButton(
                    f"{'✅ ' if owned else ''}{img.title} — {status}",
                    callback_data=f"img_{img.id}"
                )
            ])

        # Pagination
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cat_{cat_id}_{page - 1}"))
        if (page + 1) * ITEMS_PER_PAGE < total_count:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cat_{cat_id}_{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([InlineKeyboardButton("🔙 Categories", callback_data="browse_categories")])

        text += f"Page {page + 1}/{(total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE}"

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def image_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show image preview and purchase option."""
    query = update.callback_query
    await query.answer()

    img_id = int(query.data.split("_")[1])

    db = SessionLocal()
    try:
        image = db.query(Image).get(img_id)
        if not image:
            await query.edit_message_text("Image not found.")
            return

        tg_user = update.effective_user
        user = db.query(User).filter(User.telegram_id == tg_user.id).first()

        # Check if already owned
        already_owned = False
        if user:
            existing = (
                db.query(Order)
                .filter(
                    Order.user_id == user.id,
                    Order.image_id == image.id,
                    Order.status == OrderStatus.COMPLETED.value
                )
                .first()
            )
            already_owned = existing is not None

        cat_id = image.category_id or 0

        if already_owned:
            # Send the full image directly
            text = f"✅ **{image.title}**\n\nYou already own this! Here it is:"
            photo_source = image.file_data if image.file_data else image.cloudinary_url
            await query.message.reply_photo(
                photo=photo_source,
                caption=text,
                parse_mode="Markdown"
            )
            return

        # Calculate discounted price — flash sale + VIP tier
        sale_price, discount_pct, on_sale = get_flash_price(image, db)
        price = sale_price  # start with flash sale price (or original)
        discount_label = ""
        if on_sale:
            discount_label = f" ({discount_pct}% FLASH SALE 🔥)"

        # Apply VIP discount on top
        if user and user.vip_tier == "bronze":
            price = round(price * 0.95, 2)
            discount_label += " +5% VIP"
        elif user and user.vip_tier == "silver":
            price = round(price * 0.90, 2)
            discount_label += " +10% VIP"
        elif user and user.vip_tier == "gold":
            price = round(price * 0.80, 2)
            discount_label += " +20% VIP"

        # Show preview
        original_str = f"~~${image.price:.0f}~~ " if on_sale else ""
        text = (
            f"🖼 **{image.title}**\n"
            f"{image.description or ''}\n\n"
            f"💰 {original_str}**${price:.0f}**{discount_label}\n"
        )

        has_free = user and (user.free_unlocks or 0) > 0

        keyboard = []
        if has_free and not image.is_explicit:
            keyboard.append([
                InlineKeyboardButton("🎁 Use Free Unlock", callback_data=f"free_{image.id}")
            ])
        keyboard.append([
            InlineKeyboardButton(f"💳 Unlock for ${price:.0f}", callback_data=f"buy_{image.id}")
        ])
        keyboard.append([
            InlineKeyboardButton("🔙 Back", callback_data=f"cat_{cat_id}_0")
        ])

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def browse_popular_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show most popular/best-selling images."""
    query = update.callback_query
    await query.answer()

    db = SessionLocal()
    try:
        images = (
            db.query(Image)
            .filter(Image.is_active == True)
            .order_by(Image.total_sales.desc())
            .limit(10)
            .all()
        )

        if not images:
            await query.edit_message_text(
                "No content yet. Check back soon! 🔥",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return

        text = "🔥 **Most Popular**\n\n"
        keyboard = []
        for i, img in enumerate(images, 1):
            keyboard.append([
                InlineKeyboardButton(
                    f"#{i} {img.title} — ${img.price:.0f}",
                    callback_data=f"img_{img.id}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")])

        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def popular_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /popular command."""
    db = SessionLocal()
    try:
        images = (
            db.query(Image)
            .filter(Image.is_active == True)
            .order_by(Image.total_sales.desc())
            .limit(10)
            .all()
        )

        if not images:
            await update.message.reply_text("No content yet. Check back soon! 🔥")
            return

        text = "🔥 **Most Popular**\n\n"
        keyboard = []
        for i, img in enumerate(images, 1):
            keyboard.append([
                InlineKeyboardButton(
                    f"#{i} {img.title} — ${img.price:.0f}",
                    callback_data=f"img_{img.id}"
                )
            ])

        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


def get_browse_handlers():
    return [
        CommandHandler("browse", browse_categories_command),
        CommandHandler("popular", popular_command),
        CallbackQueryHandler(browse_categories_callback, pattern="^browse_categories$"),
        CallbackQueryHandler(category_images_callback, pattern=r"^cat_\d+_\d+$"),
        CallbackQueryHandler(image_detail_callback, pattern=r"^img_\d+$"),
        CallbackQueryHandler(browse_popular_callback, pattern="^browse_popular$"),
    ]
