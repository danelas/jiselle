from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from bot.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from bot.models.schemas import (
        User, Image, Order, Category,
        Subscription, DripSchedule, FlashSale, CustomRequest, LoyaltyRedemption
    )
    Base.metadata.create_all(bind=engine)
