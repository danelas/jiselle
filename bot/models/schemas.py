import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean,
    DateTime, ForeignKey, Text, Enum as SAEnum, LargeBinary
)
from sqlalchemy.orm import relationship
from bot.models.database import Base
import enum


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PENDING = "pending"


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    REJECTED = "rejected"


class ContentType(str, enum.Enum):
    INSTAGRAM = "instagram"   # SFW — safe for Instagram posting
    PRIVATE = "private"       # NSFW — Telegram-only, NEVER post to Instagram


class ContentTier(str, enum.Enum):
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"
    VIP = "vip"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    vip_tier = Column(String(50), default=ContentTier.FREE.value)
    total_spent = Column(Float, default=0.0)
    loyalty_points = Column(Integer, default=0)
    referral_code = Column(String(20), unique=True, nullable=True)
    referred_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    free_unlocks = Column(Integer, default=1)  # welcome funnel: 1 free image
    is_banned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_active = Column(DateTime, default=datetime.datetime.utcnow)

    orders = relationship("Order", back_populates="user")
    subscriptions = relationship("Subscription", back_populates="user")
    custom_requests = relationship("CustomRequest", back_populates="user")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    emoji = Column(String(10), nullable=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    images = relationship("Image", back_populates="category")


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    tier = Column(String(50), default=ContentTier.BASIC.value)
    price = Column(Float, nullable=False, default=5.0)
    cloudinary_url = Column(Text, nullable=True)  # legacy — kept for backward compat
    cloudinary_public_id = Column(String(255), nullable=True)
    file_data = Column(LargeBinary, nullable=True)  # image bytes stored in DB
    file_mimetype = Column(String(50), nullable=True)  # e.g. image/jpeg
    content_type = Column(String(20), nullable=False, default=ContentType.PRIVATE.value)  # instagram or private
    is_explicit = Column(Boolean, default=False)  # nude/explicit — blocked from free unlocks
    is_bundle = Column(Boolean, default=False)
    bundle_size = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    total_sales = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Flash sale fields
    flash_sale_price = Column(Float, nullable=True)
    flash_sale_start = Column(DateTime, nullable=True)
    flash_sale_end = Column(DateTime, nullable=True)
    flash_sale_label = Column(String(100), nullable=True)

    # Drip content fields
    is_drip = Column(Boolean, default=False)
    drip_tier_required = Column(String(50), nullable=True)

    category = relationship("Category", back_populates="images")
    orders = relationship("Order", back_populates="image")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default="USD")
    paypal_order_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), default=OrderStatus.PENDING.value)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")
    image = relationship("Image", back_populates="orders")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tier = Column(String(50), nullable=False)  # bronze, silver, gold
    price_monthly = Column(Float, nullable=False)
    status = Column(String(50), default=SubscriptionStatus.PENDING.value)
    paypal_subscription_id = Column(String(255), nullable=True, index=True)
    paypal_order_id = Column(String(255), nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="subscriptions")


class DripSchedule(Base):
    __tablename__ = "drip_schedule"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    tier_required = Column(String(50), default="free")  # min sub tier to receive
    send_at = Column(DateTime, nullable=False)
    sent = Column(Boolean, default=False)
    message_text = Column(Text, nullable=True)  # optional teaser text
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    image = relationship("Image")


class FlashSale(Base):
    __tablename__ = "flash_sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    discount_percent = Column(Integer, nullable=False)  # e.g. 50 = 50% off
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    announcement_sent = Column(Boolean, default=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)  # null = all categories
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class CustomRequest(Base):
    __tablename__ = "custom_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(Text, nullable=False)
    price = Column(Float, nullable=True)  # set by admin after review
    status = Column(String(50), default=RequestStatus.PENDING.value)
    admin_notes = Column(Text, nullable=True)
    paypal_order_id = Column(String(255), nullable=True)
    result_image_id = Column(Integer, ForeignKey("images.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="custom_requests")
    result_image = relationship("Image")


class LoyaltyRedemption(Base):
    __tablename__ = "loyalty_redemptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    points_spent = Column(Integer, nullable=False)
    reward_type = Column(String(50), nullable=False)  # "image_unlock", "discount_10", "discount_25"
    image_id = Column(Integer, ForeignKey("images.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    caption = Column(Text, nullable=True)
    scheduled_at = Column(DateTime, nullable=False)
    status = Column(String(20), default="pending")  # pending, posted, failed
    ig_media_id = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    posted_at = Column(DateTime, nullable=True)

    image = relationship("Image")
