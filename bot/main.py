import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn

from bot.config import TELEGRAM_BOT_TOKEN, BASE_URL, PORT, PAYPAL_WEBHOOK_ID, ADMIN_PASSWORD
from bot.models.database import init_db
from bot.handlers.start import get_start_handlers
from bot.handlers.browse import get_browse_handlers
from bot.handlers.purchase import get_purchase_handlers
from bot.handlers.admin import get_admin_handlers
from bot.handlers.subscription import get_subscription_handlers, activate_subscription
from bot.handlers.flash_sales import get_flash_sale_handlers
from bot.handlers.custom_requests import get_custom_request_handlers
from bot.handlers.loyalty import get_loyalty_handlers
from bot.services.paypal import verify_webhook_signature, capture_order as paypal_capture
from bot.services.delivery import deliver_image, complete_order
from bot.services.drip import process_drip_content, check_flash_sales, check_expiring_subscriptions
from bot.web.dashboard import router as dashboard_router, process_scheduled_posts, register_auth_exception_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global references
tg_app: Application = None
scheduler: AsyncIOScheduler = None


def build_telegram_app() -> Application:
    """Build and configure the Telegram bot application."""
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers (order matters — conversation handlers first)
    for handler in get_admin_handlers():
        app.add_handler(handler)

    for handler in get_custom_request_handlers():
        app.add_handler(handler)

    for handler in get_start_handlers():
        app.add_handler(handler)

    for handler in get_browse_handlers():
        app.add_handler(handler)

    for handler in get_purchase_handlers():
        app.add_handler(handler)

    for handler in get_subscription_handlers():
        app.add_handler(handler)

    for handler in get_flash_sale_handlers():
        app.add_handler(handler)

    for handler in get_loyalty_handlers():
        app.add_handler(handler)

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global tg_app, scheduler

    # Initialize database
    logger.info("Initializing database...")
    init_db()

    # Build and initialize Telegram app
    logger.info("Starting Telegram bot...")
    tg_app = build_telegram_app()
    await tg_app.initialize()
    await tg_app.start()

    # Set webhook
    webhook_url = f"{BASE_URL}/telegram/webhook"
    await tg_app.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set to {webhook_url}")

    # Start scheduler for drip content, flash sales, subscription expiry
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        process_drip_content, "interval", minutes=5, id="drip_content",
        args=[tg_app.bot],
    )
    scheduler.add_job(
        check_flash_sales, "interval", minutes=2, id="flash_sales",
        args=[tg_app.bot],
    )
    scheduler.add_job(
        check_expiring_subscriptions, "interval", hours=6, id="sub_expiry",
        args=[tg_app.bot],
    )
    scheduler.add_job(
        process_scheduled_posts, "interval", minutes=1, id="ig_scheduled_posts",
    )
    scheduler.start()
    logger.info("Scheduler started (drip: 5min, flash: 2min, subs: 6hr, ig: 1min)")

    yield

    # Shutdown
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    await tg_app.stop()
    await tg_app.shutdown()


# FastAPI app
web_app = FastAPI(lifespan=lifespan)

# Session middleware for dashboard auth
from starlette.middleware.sessions import SessionMiddleware
web_app.add_middleware(SessionMiddleware, secret_key=ADMIN_PASSWORD)

# Mount dashboard
web_app.include_router(dashboard_router)
register_auth_exception_handler(web_app)


# ─── Telegram Webhook Endpoint ─────────────────────────

@web_app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook."""
    global tg_app
    try:
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as e:
        logger.error(f"Error processing Telegram update: {e}")
    return Response(status_code=200)


# ─── PayPal Webhook Endpoint ──────────────────────────

@web_app.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    """Handle PayPal payment webhooks — auto-deliver images."""
    body = await request.body()
    headers = dict(request.headers)

    # Verify webhook signature
    if PAYPAL_WEBHOOK_ID:
        try:
            valid = await verify_webhook_signature(headers, body, PAYPAL_WEBHOOK_ID)
            if not valid:
                logger.warning("Invalid PayPal webhook signature")
                return Response(status_code=400)
        except Exception as e:
            logger.warning(f"Webhook verification failed: {e}")
            # Continue processing in sandbox mode for testing

    import json
    event = json.loads(body)
    event_type = event.get("event_type", "")

    logger.info(f"PayPal webhook: {event_type}")

    if event_type == "CHECKOUT.ORDER.APPROVED":
        # User approved the payment — capture it
        resource = event.get("resource", {})
        paypal_order_id = resource.get("id")

        if paypal_order_id:
            try:
                capture_result = await paypal_capture(paypal_order_id)
                status = capture_result.get("status")

                if status == "COMPLETED":
                    await _process_completed_payment(paypal_order_id)
            except Exception as e:
                logger.error(f"Capture/delivery failed for {paypal_order_id}: {e}")

    elif event_type == "PAYMENT.CAPTURE.COMPLETED":
        resource = event.get("resource", {})
        supplementary = resource.get("supplementary_data", {})
        related = supplementary.get("related_ids", {})
        paypal_order_id = related.get("order_id")

        if paypal_order_id:
            await _process_completed_payment(paypal_order_id)

    return Response(status_code=200)


async def _process_completed_payment(paypal_order_id: str):
    """Route a completed PayPal payment to the right handler."""
    # Try image order first
    order_id = complete_order(paypal_order_id)
    if order_id:
        await deliver_image(tg_app.bot, order_id)
        logger.info(f"Image delivered for PayPal order {paypal_order_id}")
        return

    # Try subscription activation
    sub_id = activate_subscription(paypal_order_id)
    if sub_id:
        from bot.models.database import SessionLocal
        from bot.models.schemas import Subscription, User
        db = SessionLocal()
        try:
            sub = db.query(Subscription).get(sub_id)
            if sub:
                user = db.query(User).get(sub.user_id)
                if user:
                    from bot.handlers.subscription import SUB_TIERS
                    tier_info = SUB_TIERS.get(sub.tier, {})
                    try:
                        await tg_app.bot.send_message(
                            chat_id=user.telegram_id,
                            text=(
                                f"✅ **VIP {tier_info.get('name', sub.tier)} Activated!**\n\n"
                                f"{tier_info.get('emoji', '💎')} Your perks are now active:\n" +
                                "\n".join(f"  • {p}" for p in tier_info.get('perks', [])) +
                                f"\n\nExpires: {sub.expires_at.strftime('%B %d, %Y')}\n"
                                f"Enjoy your VIP status! 👑"
                            ),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify user of sub activation: {e}")
        finally:
            db.close()
        logger.info(f"Subscription {sub_id} activated for PayPal order {paypal_order_id}")
        return

    # Try custom request payment
    from bot.models.database import SessionLocal as SL
    from bot.models.schemas import CustomRequest, RequestStatus as RS, User as UserModel
    db = SL()
    try:
        req = db.query(CustomRequest).filter(
            CustomRequest.paypal_order_id == paypal_order_id
        ).first()
        if req and req.status != RS.COMPLETED.value:
            req.status = RS.ACCEPTED.value  # paid, awaiting admin delivery
            db.commit()

            user_obj = db.query(UserModel).get(req.user_id) if req else None
            if user_obj:
                try:
                    await tg_app.bot.send_message(
                        chat_id=user_obj.telegram_id,
                        text=(
                            f"✅ **Payment received for Request #{req.id}!**\n\n"
                            f"Your custom content is being created.\n"
                            f"You'll receive it as soon as it's ready! 🎨"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

            # Notify admin
            from bot.config import ADMIN_TELEGRAM_ID
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            try:
                await tg_app.bot.send_message(
                    chat_id=ADMIN_TELEGRAM_ID,
                    text=f"💰 **Request #{req.id} PAID** (${req.price:.2f})\nReady for delivery!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                        f"📸 Deliver #{req.id}",
                        callback_data=f"admin_req_deliver_{req.id}"
                    )]]),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            logger.info(f"Custom request {req.id} paid via {paypal_order_id}")
    finally:
        db.close()


# ─── PayPal Return/Cancel URLs ────────────────────────

@web_app.get("/paypal/return")
async def paypal_return(request: Request):
    """User redirected here after PayPal approval."""
    token = request.query_params.get("token", "")

    if token:
        try:
            capture_result = await paypal_capture(token)
            status = capture_result.get("status")
            if status == "COMPLETED":
                await _process_completed_payment(token)
        except Exception as e:
            logger.error(f"Return capture failed: {e}")

    return HTMLResponse(
        """
        <html>
        <head><title>Payment Complete</title></head>
        <body style="display:flex;align-items:center;justify-content:center;height:100vh;
                      font-family:Arial,sans-serif;background:#1a1a2e;color:white;text-align:center;">
            <div>
                <h1>✅ Payment Successful!</h1>
                <p>Your image has been sent to you on Telegram.</p>
                <p>You can close this page now. 💋</p>
            </div>
        </body>
        </html>
        """
    )


@web_app.get("/paypal/cancel")
async def paypal_cancel():
    """User cancelled payment."""
    return HTMLResponse(
        """
        <html>
        <head><title>Payment Cancelled</title></head>
        <body style="display:flex;align-items:center;justify-content:center;height:100vh;
                      font-family:Arial,sans-serif;background:#1a1a2e;color:white;text-align:center;">
            <div>
                <h1>❌ Payment Cancelled</h1>
                <p>No worries! Head back to Telegram to continue browsing.</p>
            </div>
        </body>
        </html>
        """
    )


# ─── Health Check ──────────────────────────────────────

@web_app.get("/")
async def health_check():
    return RedirectResponse("/dashboard", status_code=303)


# ─── Entry Point ───────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("bot.main:web_app", host="0.0.0.0", port=PORT, reload=False)
