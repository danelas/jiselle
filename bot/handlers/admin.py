import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler
)
from bot.config import ADMIN_TELEGRAM_ID
from bot.models.database import SessionLocal
from bot.models.schemas import (
    Category, Image, Order, User, OrderStatus, ContentType,
    FlashSale, DripSchedule, CustomRequest, RequestStatus, Subscription, SubscriptionStatus
)
import mimetypes

logger = logging.getLogger(__name__)

# Conversation states for admin flows
(
    AWAITING_CATEGORY_NAME,
    AWAITING_CATEGORY_EMOJI,
    AWAITING_IMAGE_CATEGORY,
    AWAITING_IMAGE_CONTENT_TYPE,
    AWAITING_IMAGE_TITLE,
    AWAITING_IMAGE_PRICE,
    AWAITING_IMAGE_TIER,
    AWAITING_IMAGE_DESCRIPTION,
    AWAITING_IMAGE_FILE,
) = range(9)

# Flash sale conversation states
(
    AWAITING_SALE_TITLE,
    AWAITING_SALE_DISCOUNT,
    AWAITING_SALE_DURATION,
    AWAITING_SALE_CATEGORY,
) = range(10, 14)

# Drip conversation states
(
    AWAITING_DRIP_IMAGE,
    AWAITING_DRIP_TIER,
    AWAITING_DRIP_DELAY,
    AWAITING_DRIP_MESSAGE,
) = range(20, 24)

# Custom request admin states
AWAITING_REQ_PRICE = 30
AWAITING_REQ_DELIVERY_IMAGE = 31

# Instagram posting states
AWAITING_IG_IMAGE_SELECT = 40
AWAITING_IG_CAPTION = 41


def admin_only(func):
    """Decorator to restrict handler to admin only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_TELEGRAM_ID:
            await update.message.reply_text("⛔ Admin only.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


def admin_only_callback(func):
    """Decorator for callback query handlers."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_TELEGRAM_ID:
            await update.callback_query.answer("⛔ Admin only.", show_alert=True)
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


# ─── Admin Dashboard ───────────────────────────────────

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin dashboard."""
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return

    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        total_orders = db.query(Order).filter(Order.status == OrderStatus.COMPLETED.value).count()
        completed = db.query(Order).filter(Order.status == OrderStatus.COMPLETED.value).all()
        revenue = sum(o.amount for o in completed)
        total_images = db.query(Image).count()
        total_categories = db.query(Category).count()

        text = (
            "🛠 **Admin Dashboard**\n\n"
            f"👥 Users: **{total_users}**\n"
            f"📦 Orders: **{total_orders}**\n"
            f"💰 Revenue: **${revenue:.0f}**\n"
            f"🖼 Images: **{total_images}**\n"
            f"📁 Categories: **{total_categories}**\n"
        )

        # Phase 2 stats
        active_subs = db.query(Subscription).filter(
            Subscription.status == SubscriptionStatus.ACTIVE.value
        ).count()
        pending_requests = db.query(CustomRequest).filter(
            CustomRequest.status == RequestStatus.PENDING.value
        ).count()
        active_sales = db.query(FlashSale).filter(FlashSale.is_active == True).count()

        text += (
            f"\n💎 Active Subs: **{active_subs}**\n"
            f"⚡ Flash Sales: **{active_sales}**\n"
            f"📬 Pending Requests: **{pending_requests}**\n"
        )

        keyboard = [
            [InlineKeyboardButton("➕ Add Category", callback_data="admin_add_cat")],
            [InlineKeyboardButton("📸 Upload Image", callback_data="admin_upload_img")],
            [InlineKeyboardButton("📋 List Categories", callback_data="admin_list_cats")],
            [InlineKeyboardButton("📊 Recent Orders", callback_data="admin_recent_orders")],
            [InlineKeyboardButton("⚡ Create Flash Sale", callback_data="admin_flash_sale")],
            [InlineKeyboardButton("📅 Schedule Drip", callback_data="admin_drip")],
            [InlineKeyboardButton(f"📬 Requests ({pending_requests})", callback_data="admin_requests")],
            [InlineKeyboardButton("� Post to Instagram", callback_data="admin_ig_post")],
            [InlineKeyboardButton("�� Broadcast Message", callback_data="admin_broadcast")],
        ]

        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


# ─── Add Category Flow ─────────────────────────────────

async def add_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add category conversation."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text("📁 Enter the category name:")
    return AWAITING_CATEGORY_NAME


async def add_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive category name."""
    context.user_data["new_cat_name"] = update.message.text.strip()
    await update.message.reply_text(
        f"Category: **{update.message.text.strip()}**\n\n"
        "Now send an emoji for this category (or /skip):",
        parse_mode="Markdown"
    )
    return AWAITING_CATEGORY_EMOJI


async def add_category_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive category emoji and save."""
    emoji = update.message.text.strip() if update.message.text != "/skip" else "📁"
    name = context.user_data.get("new_cat_name", "Unnamed")

    db = SessionLocal()
    try:
        cat = Category(name=name, emoji=emoji)
        db.add(cat)
        db.commit()
        db.refresh(cat)
        await update.message.reply_text(
            f"✅ Category **{emoji} {name}** created! (ID: {cat.id})\n\n"
            f"Now upload images to it with /admin → Upload Image",
            parse_mode="Markdown"
        )
    finally:
        db.close()

    return ConversationHandler.END


# ─── Upload Image Flow ─────────────────────────────────

async def upload_image_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start image upload conversation."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()

    db = SessionLocal()
    try:
        categories = db.query(Category).filter(Category.is_active == True).all()
        if not categories:
            await query.message.reply_text(
                "❌ No categories exist. Create one first with /admin → Add Category"
            )
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton(
                f"{c.emoji or '📁'} {c.name}",
                callback_data=f"admcat_{c.id}"
            )]
            for c in categories
        ]

        await query.message.reply_text(
            "📸 **Upload Image**\n\nSelect a category:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return AWAITING_IMAGE_CATEGORY
    finally:
        db.close()


async def upload_image_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive category selection for image."""
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[1])
    context.user_data["img_cat_id"] = cat_id

    keyboard = [
        [InlineKeyboardButton(
            "📸 Instagram (SFW) — safe for public posting",
            callback_data="ctype_instagram"
        )],
        [InlineKeyboardButton(
            "🔒 Private (NSFW) — Telegram only, NEVER posted publicly",
            callback_data="ctype_private"
        )],
    ]
    await query.message.reply_text(
        "⚠️ **Content Type**\n\n"
        "Choose carefully — this determines where this image can appear:\n\n"
        "📸 **Instagram** = SFW only, may be posted to Instagram\n"
        "🔒 **Private** = NSFW / Telegram-only, will **NEVER** be posted publicly",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return AWAITING_IMAGE_CONTENT_TYPE


async def upload_image_content_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive content type (instagram vs private)."""
    query = update.callback_query
    await query.answer()
    ctype = query.data.split("_")[1]  # "instagram" or "private"
    context.user_data["img_content_type"] = ctype

    label = "📸 Instagram (SFW)" if ctype == "instagram" else "🔒 Private (NSFW)"
    await query.message.reply_text(f"Content type: **{label}**\n\nEnter a title for this image:", parse_mode="Markdown")
    return AWAITING_IMAGE_TITLE


async def upload_image_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive image title."""
    context.user_data["img_title"] = update.message.text.strip()
    await update.message.reply_text("Enter a short description (or /skip):")
    return AWAITING_IMAGE_DESCRIPTION


async def upload_image_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive image description."""
    text = update.message.text.strip()
    context.user_data["img_desc"] = "" if text == "/skip" else text
    await update.message.reply_text("Enter the price in USD (e.g., 5.00):")
    return AWAITING_IMAGE_PRICE


async def upload_image_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive image price."""
    try:
        price = float(update.message.text.strip().replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Enter a number (e.g., 5.00):")
        return AWAITING_IMAGE_PRICE

    context.user_data["img_price"] = price

    keyboard = [
        [InlineKeyboardButton("Free", callback_data="tier_free")],
        [InlineKeyboardButton("Basic", callback_data="tier_basic")],
        [InlineKeyboardButton("Premium", callback_data="tier_premium")],
        [InlineKeyboardButton("VIP", callback_data="tier_vip")],
    ]

    await update.message.reply_text(
        "Select the content tier:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return AWAITING_IMAGE_TIER


async def upload_image_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive tier selection."""
    query = update.callback_query
    await query.answer()
    tier = query.data.split("_")[1]
    context.user_data["img_tier"] = tier
    await query.message.reply_text("Now send me the image file (as a photo or document):")
    return AWAITING_IMAGE_FILE


async def upload_image_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and process the image file."""
    await update.message.reply_text("⏳ Uploading to cloud storage...")

    try:
        if update.message.photo:
            # Get highest resolution photo
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            file_bytes = await file.download_as_bytearray()
            filename = f"img_{photo.file_unique_id}"
        elif update.message.document:
            doc = update.message.document
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            filename = doc.file_name or f"doc_{doc.file_unique_id}"
        else:
            await update.message.reply_text("❌ Please send a photo or document.")
            return AWAITING_IMAGE_FILE

        content_type = context.user_data.get("img_content_type", "private")
        mimetype = mimetypes.guess_type(filename)[0] or "image/jpeg"

        # Save to database
        db = SessionLocal()
        try:
            image = Image(
                title=context.user_data.get("img_title", "Untitled"),
                description=context.user_data.get("img_desc", ""),
                category_id=context.user_data.get("img_cat_id"),
                tier=context.user_data.get("img_tier", "basic"),
                price=context.user_data.get("img_price", 5.0),
                file_data=bytes(file_bytes),
                file_mimetype=mimetype,
                content_type=content_type,
            )
            db.add(image)
            db.commit()
            db.refresh(image)

            ctype_label = "📸 Instagram (SFW)" if content_type == "instagram" else "🔒 Private (NSFW)"
            await update.message.reply_text(
                f"✅ **Image uploaded successfully!**\n\n"
                f"🆔 ID: {image.id}\n"
                f"📝 Title: {image.title}\n"
                f"💰 Price: ${image.price:.0f}\n"
                f"🏷 Tier: {image.tier}\n"
                f"📂 Type: {ctype_label}\n\n"
                f"Upload more with /admin",
                parse_mode="Markdown"
            )
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Image upload failed: {e}")
        await update.message.reply_text(f"❌ Upload failed: {str(e)}")

    return ConversationHandler.END


async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel upload conversation."""
    await update.message.reply_text("❌ Upload cancelled.")
    return ConversationHandler.END


# ─── List Categories ───────────────────────────────────

async def list_categories_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all categories with image counts."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    await query.answer()

    db = SessionLocal()
    try:
        categories = db.query(Category).all()
        if not categories:
            await query.message.reply_text("No categories yet.")
            return

        text = "📁 **All Categories**\n\n"
        for cat in categories:
            count = db.query(Image).filter(Image.category_id == cat.id).count()
            status = "✅" if cat.is_active else "❌"
            text += f"{status} {cat.emoji or ''} **{cat.name}** — {count} images (ID: {cat.id})\n"

        await query.message.reply_text(text, parse_mode="Markdown")
    finally:
        db.close()


# ─── Recent Orders ─────────────────────────────────────

async def recent_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent orders."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    await query.answer()

    db = SessionLocal()
    try:
        orders = (
            db.query(Order)
            .order_by(Order.created_at.desc())
            .limit(15)
            .all()
        )

        if not orders:
            await query.message.reply_text("No orders yet.")
            return

        text = "📊 **Recent Orders**\n\n"
        for o in orders:
            user = db.query(User).get(o.user_id)
            image = db.query(Image).get(o.image_id)
            username = user.username or user.first_name or str(user.telegram_id) if user else "Unknown"
            img_name = image.title if image else "Unknown"
            status_emoji = {
                "completed": "✅",
                "pending": "⏳",
                "failed": "❌",
                "refunded": "↩️",
            }.get(o.status, "❓")

            text += (
                f"{status_emoji} **${o.amount:.0f}** — {img_name}\n"
                f"   👤 @{username} | {o.created_at.strftime('%m/%d %H:%M')}\n"
            )

        await query.message.reply_text(text, parse_mode="Markdown")
    finally:
        db.close()


# ─── Broadcast ─────────────────────────────────────────

AWAITING_BROADCAST = 100


# ─── Flash Sale Creation ──────────────────────────────

async def flash_sale_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start flash sale creation."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(
        "⚡ **Create Flash Sale**\n\nEnter a title for the sale (e.g., 'Weekend Blowout'):",
        parse_mode="Markdown"
    )
    return AWAITING_SALE_TITLE


async def flash_sale_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive sale title."""
    context.user_data["sale_title"] = update.message.text.strip()
    await update.message.reply_text(
        "Enter the discount percentage (e.g., 30 for 30% off):"
    )
    return AWAITING_SALE_DISCOUNT


async def flash_sale_discount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive discount percentage."""
    try:
        discount = int(update.message.text.strip().replace("%", ""))
        if discount < 1 or discount > 90:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a number between 1 and 90:")
        return AWAITING_SALE_DISCOUNT

    context.user_data["sale_discount"] = discount
    await update.message.reply_text(
        "How long should the sale last? Enter hours (e.g., 24 for 1 day):"
    )
    return AWAITING_SALE_DURATION


async def flash_sale_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive sale duration."""
    try:
        hours = int(update.message.text.strip())
        if hours < 1 or hours > 168:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter hours between 1 and 168 (1 week max):")
        return AWAITING_SALE_DURATION

    context.user_data["sale_hours"] = hours

    db = SessionLocal()
    try:
        categories = db.query(Category).filter(Category.is_active == True).all()
        keyboard = [
            [InlineKeyboardButton("🌐 All Categories", callback_data="salecat_all")]
        ]
        for c in categories:
            keyboard.append([
                InlineKeyboardButton(
                    f"{c.emoji or '📁'} {c.name}",
                    callback_data=f"salecat_{c.id}"
                )
            ])
        await update.message.reply_text(
            "Apply sale to which category?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return AWAITING_SALE_CATEGORY
    finally:
        db.close()


async def flash_sale_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive category and create the flash sale."""
    query = update.callback_query
    await query.answer()

    cat_part = query.data.split("_")[1]
    cat_id = None if cat_part == "all" else int(cat_part)

    import datetime
    now = datetime.datetime.utcnow()
    hours = context.user_data.get("sale_hours", 24)

    db = SessionLocal()
    try:
        sale = FlashSale(
            title=context.user_data.get("sale_title", "Flash Sale"),
            discount_percent=context.user_data.get("sale_discount", 20),
            starts_at=now,
            ends_at=now + datetime.timedelta(hours=hours),
            is_active=True,
            category_id=cat_id,
        )
        db.add(sale)
        db.commit()
        db.refresh(sale)

        cat_name = "All Categories"
        if cat_id:
            cat = db.query(Category).get(cat_id)
            if cat:
                cat_name = f"{cat.emoji or ''} {cat.name}"

        await query.message.reply_text(
            f"⚡ **Flash Sale Created!**\n\n"
            f"🏷 {sale.title}\n"
            f"💥 {sale.discount_percent}% off — {cat_name}\n"
            f"⏰ Runs for {hours} hours\n"
            f"📢 Users will be notified automatically!\n\n"
            f"Sale ID: {sale.id}",
            parse_mode="Markdown"
        )
    finally:
        db.close()

    return ConversationHandler.END


# ─── Drip Content Scheduling ──────────────────────────

async def drip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start drip content scheduling."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()

    db = SessionLocal()
    try:
        images = db.query(Image).filter(Image.is_active == True).order_by(Image.created_at.desc()).limit(20).all()
        if not images:
            await query.message.reply_text("❌ No images to schedule. Upload some first.")
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton(
                f"{img.title} (${img.price:.0f})",
                callback_data=f"drpimg_{img.id}"
            )]
            for img in images
        ]
        await query.message.reply_text(
            "📅 **Schedule Drip Content**\n\nSelect an image to drip:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return AWAITING_DRIP_IMAGE
    finally:
        db.close()


async def drip_image_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive image selection for drip."""
    query = update.callback_query
    await query.answer()
    img_id = int(query.data.split("_")[1])
    context.user_data["drip_img_id"] = img_id

    keyboard = [
        [InlineKeyboardButton("🌐 Free (all users)", callback_data="drptier_free")],
        [InlineKeyboardButton("🥉 Bronze+", callback_data="drptier_bronze")],
        [InlineKeyboardButton("🥈 Silver+", callback_data="drptier_silver")],
        [InlineKeyboardButton("🥇 Gold only", callback_data="drptier_gold")],
    ]
    await query.message.reply_text(
        "Who should receive this drip?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return AWAITING_DRIP_TIER


async def drip_tier_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive tier for drip."""
    query = update.callback_query
    await query.answer()
    tier = query.data.split("_")[1]
    context.user_data["drip_tier"] = tier

    await query.message.reply_text(
        "When should this be sent? Enter delay in hours from now (e.g., 1, 12, 24, 48):"
    )
    return AWAITING_DRIP_DELAY


async def drip_delay_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive delay and schedule."""
    try:
        hours = float(update.message.text.strip())
        if hours < 0 or hours > 720:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter hours between 0 and 720:")
        return AWAITING_DRIP_DELAY

    context.user_data["drip_hours"] = hours
    await update.message.reply_text(
        "Enter a teaser message to accompany the drip (or /skip):"
    )
    return AWAITING_DRIP_MESSAGE


async def drip_message_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive message and create drip schedule."""
    text = update.message.text.strip()
    message = "" if text == "/skip" else text

    import datetime
    now = datetime.datetime.utcnow()
    hours = context.user_data.get("drip_hours", 24)
    send_at = now + datetime.timedelta(hours=hours)

    db = SessionLocal()
    try:
        drip = DripSchedule(
            image_id=context.user_data.get("drip_img_id"),
            tier_required=context.user_data.get("drip_tier", "free"),
            send_at=send_at,
            message_text=message,
        )
        db.add(drip)
        db.commit()
        db.refresh(drip)

        img = db.query(Image).get(drip.image_id)
        img_name = img.title if img else "Unknown"

        await update.message.reply_text(
            f"📅 **Drip Scheduled!**\n\n"
            f"🖼 Image: {img_name}\n"
            f"👥 Audience: {drip.tier_required}+\n"
            f"⏰ Sends at: {send_at.strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"💬 Message: {message or '(default)'}\n\n"
            f"Drip ID: {drip.id}",
            parse_mode="Markdown"
        )
    finally:
        db.close()

    return ConversationHandler.END


# ─── Custom Request Management ────────────────────────

async def admin_requests_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending custom requests."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    await query.answer()

    db = SessionLocal()
    try:
        requests = (
            db.query(CustomRequest)
            .filter(CustomRequest.status.in_([
                RequestStatus.PENDING.value,
                RequestStatus.ACCEPTED.value
            ]))
            .order_by(CustomRequest.created_at.desc())
            .limit(15)
            .all()
        )

        if not requests:
            await query.message.reply_text("No pending requests.")
            return

        text = "📬 **Custom Requests**\n\n"
        keyboard = []
        for req in requests:
            user = db.query(User).get(req.user_id)
            username = user.username or user.first_name or str(user.telegram_id) if user else "Unknown"
            status_emoji = {"pending": "⏳", "accepted": "💰"}.get(req.status, "❓")
            desc_short = req.description[:50] + "..." if len(req.description) > 50 else req.description
            price_str = f" — ${req.price:.0f}" if req.price else ""

            text += f"{status_emoji} **#{req.id}**{price_str} @{username}\n  _{desc_short}_\n\n"

            if req.status == RequestStatus.PENDING.value:
                keyboard.append([
                    InlineKeyboardButton(
                        f"💰 Price #{req.id}",
                        callback_data=f"admin_req_accept_{req.id}"
                    ),
                    InlineKeyboardButton(
                        f"❌ Reject #{req.id}",
                        callback_data=f"admin_req_reject_{req.id}"
                    ),
                ])
            elif req.status == RequestStatus.ACCEPTED.value:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📸 Deliver #{req.id}",
                        callback_data=f"admin_req_deliver_{req.id}"
                    )
                ])

        await query.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def admin_accept_request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start accepting a custom request — ask for price."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()

    req_id = int(query.data.split("_")[3])
    context.user_data["admin_req_id"] = req_id

    db = SessionLocal()
    try:
        req = db.query(CustomRequest).get(req_id)
        if not req:
            await query.message.reply_text("Request not found.")
            return ConversationHandler.END

        await query.message.reply_text(
            f"📬 **Request #{req_id}**\n\n"
            f"_{req.description}_\n\n"
            f"Enter the price in USD for this request:",
            parse_mode="Markdown"
        )
        return AWAITING_REQ_PRICE
    finally:
        db.close()


async def admin_set_request_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set price and accept the request."""
    try:
        price = float(update.message.text.strip().replace("$", ""))
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a valid price (e.g., 25.00):")
        return AWAITING_REQ_PRICE

    req_id = context.user_data.get("admin_req_id")
    db = SessionLocal()
    try:
        req = db.query(CustomRequest).get(req_id)
        if not req:
            await update.message.reply_text("Request not found.")
            return ConversationHandler.END

        req.price = price
        req.status = RequestStatus.ACCEPTED.value
        db.commit()

        # Notify user
        user = db.query(User).get(req.user_id)
        if user:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = [[InlineKeyboardButton(
                f"💳 Pay ${price:.0f}",
                callback_data=f"pay_request_{req.id}"
            )]]
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        f"✨ **Custom Request #{req.id} Accepted!**\n\n"
                        f"💰 Price: **${price:.0f}**\n\n"
                        f"Tap below to pay. Once paid, your custom content will be created! 🎨"
                    ),
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Failed to notify user of request acceptance: {e}")

        await update.message.reply_text(
            f"✅ Request #{req_id} accepted at ${price:.0f}. User notified."
        )
    finally:
        db.close()

    return ConversationHandler.END


async def admin_reject_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a custom request."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return
    await query.answer()

    req_id = int(query.data.split("_")[3])
    db = SessionLocal()
    try:
        req = db.query(CustomRequest).get(req_id)
        if not req:
            await query.message.reply_text("Request not found.")
            return

        req.status = RequestStatus.REJECTED.value
        db.commit()

        user = db.query(User).get(req.user_id)
        if user:
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=(
                        f"❌ **Custom Request #{req.id}** was not accepted.\n\n"
                        f"Sorry about that! Feel free to submit a new one with /request"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        await query.message.reply_text(f"❌ Request #{req_id} rejected. User notified.")
    finally:
        db.close()


async def admin_deliver_request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start delivering a custom request — ask for image."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()

    req_id = int(query.data.split("_")[3])
    context.user_data["deliver_req_id"] = req_id

    await query.message.reply_text(
        f"📸 Send the image for Request #{req_id} (as photo or document):"
    )
    return AWAITING_REQ_DELIVERY_IMAGE


async def admin_deliver_request_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive image and deliver to user."""
    await update.message.reply_text("⏳ Uploading and delivering...")

    req_id = context.user_data.get("deliver_req_id")
    db = SessionLocal()
    try:
        req = db.query(CustomRequest).get(req_id)
        if not req:
            await update.message.reply_text("Request not found.")
            return ConversationHandler.END

        # Download the file
        if update.message.photo:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            file_bytes = await file.download_as_bytearray()
            filename = f"custom_{req_id}_{photo.file_unique_id}"
        elif update.message.document:
            doc = update.message.document
            file = await context.bot.get_file(doc.file_id)
            file_bytes = await file.download_as_bytearray()
            filename = doc.file_name or f"custom_{req_id}"
        else:
            await update.message.reply_text("❌ Please send a photo or document.")
            return AWAITING_REQ_DELIVERY_IMAGE

        # Save as image in DB
        import datetime
        mimetype_cr = mimetypes.guess_type(filename)[0] or "image/jpeg"
        image = Image(
            title=f"Custom #{req_id}",
            description=req.description[:200],
            tier="vip",
            price=req.price or 0,
            file_data=bytes(file_bytes),
            file_mimetype=mimetype_cr,
            is_active=False,  # custom images are private
        )
        db.add(image)
        db.commit()
        db.refresh(image)

        req.status = RequestStatus.COMPLETED.value
        req.result_image_id = image.id
        req.completed_at = datetime.datetime.utcnow()
        db.commit()

        # Deliver to user
        user = db.query(User).get(req.user_id)
        if user:
            try:
                await context.bot.send_photo(
                    chat_id=user.telegram_id,
                    photo=result["full_url"],
                    caption=(
                        f"✨ **Custom Request #{req_id} — Delivered!**\n\n"
                        f"Here's your custom content. Enjoy! 💋"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to deliver custom request: {e}")

        await update.message.reply_text(
            f"✅ Request #{req_id} delivered to user!"
        )
    finally:
        db.close()

    return ConversationHandler.END


# ─── Instagram Posting Flow ───────────────────────────

async def ig_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Instagram-safe images to post."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()

    db = SessionLocal()
    try:
        ig_images = (
            db.query(Image)
            .filter(
                Image.content_type == ContentType.INSTAGRAM.value,
                Image.is_active == True,
            )
            .order_by(Image.created_at.desc())
            .limit(15)
            .all()
        )

        if not ig_images:
            await query.message.reply_text(
                "📸 No Instagram-safe images found.\n\n"
                "Upload images with content type **📸 Instagram (SFW)** first.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END

        keyboard = [
            [InlineKeyboardButton(
                f"📸 {img.title} (${img.price:.0f})",
                callback_data=f"igpick_{img.id}"
            )]
            for img in ig_images
        ]

        # Count private images for awareness
        private_count = db.query(Image).filter(
            Image.content_type == ContentType.PRIVATE.value
        ).count()

        await query.message.reply_text(
            f"📸 **Post to Instagram**\n\n"
            f"Showing **{len(ig_images)}** Instagram-safe images.\n"
            f"({private_count} private images are hidden — they can NEVER be posted here)\n\n"
            f"Select an image to post:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return AWAITING_IG_IMAGE_SELECT
    finally:
        db.close()


async def ig_image_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive image selection for Instagram post."""
    query = update.callback_query
    await query.answer()

    img_id = int(query.data.split("_")[1])
    context.user_data["ig_post_img_id"] = img_id

    db = SessionLocal()
    try:
        image = db.query(Image).get(img_id)
        if not image or image.content_type != ContentType.INSTAGRAM.value:
            await query.message.reply_text(
                "⛔ **BLOCKED** — This image is NOT marked as Instagram-safe.\n"
                "Only images with content_type='instagram' can be posted publicly.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END

        # Generate AI caption suggestion
        ai_caption = ""
        try:
            from bot.services.openai_chat import generate_caption
            ai_caption = await generate_caption(image.title, image.description or "")
        except Exception:
            pass

        caption_msg = f"📸 **{image.title}**\n\n"
        if ai_caption:
            caption_msg += f"✨ **AI-generated caption:**\n_{ai_caption}_\n\n"
            caption_msg += "Send /use to use this caption, type your own, or /skip for no caption:"
            context.user_data["ig_ai_caption"] = ai_caption
        else:
            caption_msg += "Enter a caption for the Instagram post (or /skip):"

        photo_source = image.file_data if image.file_data else image.cloudinary_url
        await query.message.reply_photo(
            photo=photo_source,
            caption=caption_msg,
            parse_mode="Markdown"
        )
        return AWAITING_IG_CAPTION
    finally:
        db.close()


async def ig_caption_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive caption and post to Instagram."""
    text = update.message.text.strip()
    if text == "/skip":
        caption = ""
    elif text == "/use":
        caption = context.user_data.get("ig_ai_caption", "")
    else:
        caption = text
    img_id = context.user_data.get("ig_post_img_id")

    if not img_id:
        await update.message.reply_text("❌ No image selected. Try again from /admin.")
        return ConversationHandler.END

    await update.message.reply_text("⏳ Posting to Instagram...")

    from bot.services.instagram import post_image_by_id, InstagramSafetyError
    from bot.config import INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN

    if not INSTAGRAM_USER_ID or not INSTAGRAM_ACCESS_TOKEN:
        await update.message.reply_text(
            "❌ Instagram credentials not configured.\n"
            "Set `INSTAGRAM_USER_ID` and `INSTAGRAM_ACCESS_TOKEN` in your environment.",
        )
        return ConversationHandler.END

    try:
        result = await post_image_by_id(
            image_id=img_id,
            ig_user_id=INSTAGRAM_USER_ID,
            ig_access_token=INSTAGRAM_ACCESS_TOKEN,
            caption=caption,
        )

        if result.get("success"):
            await update.message.reply_text(
                f"✅ **Posted to Instagram!**\n\n"
                f"Media ID: {result['media_id']}\n"
                f"Caption: {caption or '(none)'}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ Instagram post failed: {result.get('error', 'Unknown error')}"
            )

    except InstagramSafetyError as e:
        logger.critical(f"SAFETY BLOCK: {e}")
        await update.message.reply_text(
            f"⛔ **SAFETY BLOCK** — Posting prevented!\n\n{str(e)}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Instagram posting failed: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

    return ConversationHandler.END


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast flow."""
    query = update.callback_query
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text(
        "📢 **Broadcast**\n\nSend the message you want to broadcast to all users.\n"
        "Send /cancel to abort.",
        parse_mode="Markdown"
    )
    return AWAITING_BROADCAST


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send broadcast to all users."""
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        return ConversationHandler.END

    message_text = update.message.text
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_banned == False).all()
        success = 0
        failed = 0

        await update.message.reply_text(f"📤 Sending to {len(users)} users...")

        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=message_text,
                    parse_mode="Markdown"
                )
                success += 1
            except Exception:
                failed += 1

        await update.message.reply_text(
            f"📢 Broadcast complete!\n✅ Sent: {success}\n❌ Failed: {failed}"
        )
    finally:
        db.close()

    return ConversationHandler.END


def get_admin_handlers():
    """Return all admin handlers including conversation handlers."""

    add_cat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_category_start, pattern="^admin_add_cat$")],
        states={
            AWAITING_CATEGORY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_name)],
            AWAITING_CATEGORY_EMOJI: [MessageHandler(filters.TEXT, add_category_emoji)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    upload_img_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_image_start, pattern="^admin_upload_img$")],
        states={
            AWAITING_IMAGE_CATEGORY: [CallbackQueryHandler(upload_image_category, pattern=r"^admcat_\d+$")],
            AWAITING_IMAGE_CONTENT_TYPE: [CallbackQueryHandler(upload_image_content_type, pattern=r"^ctype_")],
            AWAITING_IMAGE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_image_title)],
            AWAITING_IMAGE_DESCRIPTION: [MessageHandler(filters.TEXT, upload_image_description)],
            AWAITING_IMAGE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_image_price)],
            AWAITING_IMAGE_TIER: [CallbackQueryHandler(upload_image_tier, pattern=r"^tier_")],
            AWAITING_IMAGE_FILE: [MessageHandler(filters.PHOTO | filters.Document.ALL, upload_image_file)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_start, pattern="^admin_broadcast$")],
        states={
            AWAITING_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    flash_sale_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(flash_sale_start, pattern="^admin_flash_sale$")],
        states={
            AWAITING_SALE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, flash_sale_title)],
            AWAITING_SALE_DISCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, flash_sale_discount)],
            AWAITING_SALE_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, flash_sale_duration)],
            AWAITING_SALE_CATEGORY: [CallbackQueryHandler(flash_sale_category, pattern=r"^salecat_")],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    drip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(drip_start, pattern="^admin_drip$")],
        states={
            AWAITING_DRIP_IMAGE: [CallbackQueryHandler(drip_image_selected, pattern=r"^drpimg_\d+$")],
            AWAITING_DRIP_TIER: [CallbackQueryHandler(drip_tier_selected, pattern=r"^drptier_")],
            AWAITING_DRIP_DELAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, drip_delay_received)],
            AWAITING_DRIP_MESSAGE: [MessageHandler(filters.TEXT, drip_message_received)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    accept_req_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_accept_request_start, pattern=r"^admin_req_accept_\d+$")],
        states={
            AWAITING_REQ_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_request_price)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    deliver_req_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_deliver_request_start, pattern=r"^admin_req_deliver_\d+$")],
        states={
            AWAITING_REQ_DELIVERY_IMAGE: [MessageHandler(filters.PHOTO | filters.Document.ALL, admin_deliver_request_image)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    ig_post_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ig_post_start, pattern="^admin_ig_post$")],
        states={
            AWAITING_IG_IMAGE_SELECT: [CallbackQueryHandler(ig_image_selected, pattern=r"^igpick_\d+$")],
            AWAITING_IG_CAPTION: [MessageHandler(filters.TEXT, ig_caption_received)],
        },
        fallbacks=[CommandHandler("cancel", upload_cancel)],
        per_message=False,
    )

    return [
        CommandHandler("admin", admin_command),
        add_cat_conv,
        upload_img_conv,
        broadcast_conv,
        flash_sale_conv,
        drip_conv,
        accept_req_conv,
        deliver_req_conv,
        ig_post_conv,
        CallbackQueryHandler(list_categories_callback, pattern="^admin_list_cats$"),
        CallbackQueryHandler(recent_orders_callback, pattern="^admin_recent_orders$"),
        CallbackQueryHandler(admin_requests_callback, pattern="^admin_requests$"),
        CallbackQueryHandler(admin_reject_request, pattern=r"^admin_req_reject_\d+$"),
    ]
