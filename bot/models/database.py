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
        Subscription, DripSchedule, FlashSale, CustomRequest, LoyaltyRedemption,
        ScheduledPost,
    )
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """Add new columns to existing tables if they don't exist yet."""
    from sqlalchemy import text, inspect
    insp = inspect(engine)
    if "images" in insp.get_table_names():
        columns = [c["name"] for c in insp.get_columns("images")]
        with engine.begin() as conn:
            if "file_data" not in columns:
                conn.execute(text("ALTER TABLE images ADD COLUMN file_data BYTEA"))
            if "file_mimetype" not in columns:
                conn.execute(text("ALTER TABLE images ADD COLUMN file_mimetype VARCHAR(50)"))
            # Make cloudinary_url nullable if it wasn't already
            conn.execute(text("ALTER TABLE images ALTER COLUMN cloudinary_url DROP NOT NULL"))
