# Telegram Companion Bot

A Telegram bot for selling exclusive image content with automated PayPal payments and instant delivery.

## Features

### Core (Phase 1)
- **Browse & Purchase** — Categories, pagination, instant unlock
- **Instant Payment** — PayPal Checkout API with webhooks for auto-delivery
- **VIP Tiers** — Bronze/Silver/Gold with auto-upgrade based on spending
- **Free Welcome Unlock** — New users get 1 free image to hook them
- **Referral Program** — Users share codes, earn free unlocks
- **Upsell Engine** — Related content suggestions after every purchase
- **Admin Dashboard** — Upload images, manage categories, broadcast, view stats

### Advanced (Phase 2)
- **VIP Subscriptions** — Bronze ($9.99/mo), Silver ($19.99/mo), Gold ($39.99/mo) with auto-activate/expire
- **Flash Sales** — Time-limited discounts, auto-announce to all users, countdown, auto-expire
- **Drip Content** — Scheduled image drops to keep users engaged, tier-gated delivery
- **Custom Requests** — Users submit paid custom content requests, admin prices/accepts/delivers
- **Loyalty Points** — 10 pts/$1 images, 15 pts/$1 subs, redeem for free unlocks, discounts, images
- **Background Scheduler** — APScheduler runs drip delivery (5min), flash sale checks (2min), sub expiry (6hr)

### Content Safety — Instagram / Private Separation
- **Two content types**: Every image is classified as `instagram` (SFW) or `private` (NSFW) at upload time
- **Separate Cloudinary folders**: `companion_bot/instagram/` vs `companion_bot/private/` — physically isolated storage
- **3-layer NSFW guard** for Instagram posting:
  1. DB query filter — only `content_type='instagram'` images are shown in the Instagram posting UI
  2. Explicit guard check (`_assert_safe_for_instagram`) — runs before every post attempt, raises `InstagramSafetyError` if content is private
  3. Cloudinary folder separation — private images are never even in the same folder as Instagram content
- **Default is PRIVATE** — if content type is somehow missing, the system defaults to `private` to prevent accidental exposure
- **Admin must explicitly choose** `📸 Instagram (SFW)` or `🔒 Private (NSFW)` for every upload

## Setup

### 1. Prerequisites

- Python 3.10+
- [Telegram Bot Token](https://t.me/BotFather) — create a bot
- [Cloudinary Account](https://cloudinary.com/) — free tier (25GB)
- [PayPal Developer Account](https://developer.paypal.com/) — create REST API app
- [Render Account](https://render.com/) — for hosting + PostgreSQL

### 2. Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Where to get it |
|----------|----------------|
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram |
| `ADMIN_TELEGRAM_ID` | Send `/start` to @userinfobot |
| `DATABASE_URL` | Render PostgreSQL dashboard |
| `CLOUDINARY_*` | Cloudinary dashboard → Settings → API Keys |
| `PAYPAL_CLIENT_ID` | PayPal Developer → My Apps → REST API app |
| `PAYPAL_CLIENT_SECRET` | Same as above |
| `PAYPAL_WEBHOOK_ID` | PayPal Developer → Webhooks (set URL to `{BASE_URL}/paypal/webhook`) |
| `BASE_URL` | Your Render app URL (e.g., `https://your-bot.onrender.com`) |

### 3. PayPal Webhook Setup

1. Go to [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/applications)
2. Select your app → Webhooks → Add Webhook
3. URL: `https://your-bot.onrender.com/paypal/webhook`
4. Events to subscribe:
   - `CHECKOUT.ORDER.APPROVED`
   - `PAYMENT.CAPTURE.COMPLETED`
5. Copy the Webhook ID into `PAYPAL_WEBHOOK_ID`

### 4. Deploy to Render

**Option A: Blueprint (recommended)**
1. Push code to GitHub
2. Go to Render → New → Blueprint
3. Connect repo → Render reads `render.yaml` and sets up everything
4. Fill in environment variables in Render dashboard

**Option B: Manual**
1. Create a PostgreSQL database on Render (free tier)
2. Create a Web Service, connect your repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `python -m bot.main`
5. Set all environment variables

### 5. Local Development

```bash
pip install -r requirements.txt
# Set up .env with your credentials
# For local dev, you need ngrok for webhooks:
# ngrok http 10000
# Set BASE_URL to your ngrok URL
python -m bot.main
```

## Admin Commands

Once deployed, send these from your admin Telegram account:

- `/admin` — Full dashboard with stats + all management buttons:
  - **Add Category** / **Upload Image** — content management
  - **Create Flash Sale** — set discount %, duration, category scope
  - **Schedule Drip** — pick image, audience tier, delay, teaser message
  - **Manage Requests** — price/accept/reject/deliver custom requests
  - **Broadcast** — send message to all users
  - **Recent Orders** — view order history

## User Commands

| Command | Description |
|---------|-------------|
| `/start` | Main menu with all options |
| `/browse` | Browse by category |
| `/popular` | Top selling content |
| `/deals` | Active flash sales |
| `/subscribe` | VIP subscription tiers |
| `/loyalty` / `/points` | Points balance & rewards |
| `/request` | Submit custom content request |
| `/mypurchases` | Re-download owned content |
| `/myrequests` | Custom request status |
| `/referral` | Referral link & stats |
| `/help` | Command list |

## Bot Flow

```
User /start → Welcome + 1 free unlock
  → Browse categories → See blurred preview (flash sale prices shown)
  → "Unlock" → PayPal payment link
  → User pays → Webhook fires → Image auto-sent → Upsell suggestions
  → Drip content arrives on schedule → User comes back
  → Flash sale notification → Urgency purchase
  → Custom request → Admin prices → User pays → Admin delivers
  → Loyalty points accumulate → Redeem for free content
```

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Health check |
| `/telegram/webhook` | POST | Telegram bot updates |
| `/paypal/webhook` | POST | PayPal payment notifications |
| `/paypal/return` | GET | Post-payment redirect |
| `/paypal/cancel` | GET | Payment cancelled redirect |
