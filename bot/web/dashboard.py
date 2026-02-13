"""
Web dashboard for managing images and scheduling Instagram posts.
Protected by simple password auth via cookie session.
"""

import logging
import datetime
from pathlib import Path
from typing import List
from fastapi import APIRouter, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from bot.config import ADMIN_PASSWORD, INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN
from bot.models.database import SessionLocal
from bot.models.schemas import (
    Image, Category, ContentType, ScheduledPost, User, Order, OrderStatus,
)
from bot.services.cloudinary_svc import upload_image_from_bytes, folder_for_content_type
from bot.services.openai_chat import generate_caption

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard")

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ── Auth helpers ──────────────────────────────────────

class NotAuthenticatedException(Exception):
    pass


def register_auth_exception_handler(app):
    """Must be called from main.py after mounting the router."""
    @app.exception_handler(NotAuthenticatedException)
    async def _redirect_to_login(request: Request, exc: NotAuthenticatedException):
        return RedirectResponse("/dashboard/login", status_code=303)


def require_login(request: Request):
    """Dependency: redirect to login if not authenticated."""
    if not request.session.get("authenticated"):
        raise NotAuthenticatedException()


# ── Login / Logout ────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Wrong password"})


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/dashboard/login", status_code=303)


# ── Dashboard home ────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def dashboard_home(request: Request, _=Depends(require_login)):
    db = SessionLocal()
    try:
        total_images = db.query(Image).filter(Image.is_active == True).count()
        ig_images = db.query(Image).filter(
            Image.content_type == ContentType.INSTAGRAM.value, Image.is_active == True
        ).count()
        private_images = total_images - ig_images
        total_users = db.query(User).count()
        total_orders = db.query(Order).filter(Order.status == OrderStatus.COMPLETED.value).count()
        pending_posts = db.query(ScheduledPost).filter(ScheduledPost.status == "pending").count()
        recent_posts = (
            db.query(ScheduledPost)
            .order_by(ScheduledPost.created_at.desc())
            .limit(5)
            .all()
        )

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "total_images": total_images,
            "ig_images": ig_images,
            "private_images": private_images,
            "total_users": total_users,
            "total_orders": total_orders,
            "pending_posts": pending_posts,
            "recent_posts": recent_posts,
        })
    finally:
        db.close()


# ── Images gallery ────────────────────────────────────

@router.get("/images", response_class=HTMLResponse)
async def images_page(request: Request, content_type: str = "all", _=Depends(require_login)):
    db = SessionLocal()
    try:
        q = db.query(Image).filter(Image.is_active == True)
        if content_type == "instagram":
            q = q.filter(Image.content_type == ContentType.INSTAGRAM.value)
        elif content_type == "private":
            q = q.filter(Image.content_type == ContentType.PRIVATE.value)

        images = q.order_by(Image.created_at.desc()).all()
        categories = db.query(Category).filter(Category.is_active == True).all()

        return templates.TemplateResponse("images.html", {
            "request": request,
            "images": images,
            "categories": categories,
            "current_filter": content_type,
        })
    finally:
        db.close()


# ── Upload ────────────────────────────────────────────

def _ensure_default_categories(db):
    """Create default categories if none exist."""
    existing = db.query(Category).count()
    if existing == 0:
        defaults = [
            Category(name="Instagram Posts", emoji="📸", sort_order=1, description="SFW content for Instagram"),
            Category(name="Exclusive Private", emoji="🔒", sort_order=2, description="Premium private content"),
            Category(name="Lingerie", emoji="🌹", sort_order=3, description="Tasteful lingerie content"),
            Category(name="Lifestyle", emoji="✨", sort_order=4, description="Day-to-day lifestyle content"),
        ]
        for cat in defaults:
            db.add(cat)
        db.commit()
        logger.info("Created default categories")


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, _=Depends(require_login)):
    db = SessionLocal()
    try:
        _ensure_default_categories(db)
        categories = db.query(Category).filter(Category.is_active == True).order_by(Category.sort_order).all()
        return templates.TemplateResponse("upload.html", {
            "request": request,
            "categories": categories,
        })
    finally:
        db.close()


@router.post("/upload")
async def upload_submit(
    request: Request,
    content_type: str = Form(...),
    category_id: int = Form(...),
    tier: str = Form("basic"),
    price: float = Form(5.0),
    image_files: List[UploadFile] = File(...),
    _=Depends(require_login),
):
    db = SessionLocal()
    uploaded = 0
    errors = []

    try:
        for image_file in image_files:
            file_bytes = await image_file.read()
            if not file_bytes:
                continue

            cloud_folder = folder_for_content_type(content_type)
            filename = image_file.filename or "upload"
            # Auto-title from filename (strip extension)
            title = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()

            try:
                result = upload_image_from_bytes(bytes(file_bytes), filename, folder=cloud_folder)
            except Exception as e:
                logger.error(f"Cloudinary upload failed for {filename}: {e}")
                errors.append(filename)
                continue

            image = Image(
                title=title,
                description="",
                category_id=category_id,
                tier=tier,
                price=price,
                cloudinary_url=result["full_url"],
                cloudinary_public_id=result["public_id"],
                content_type=content_type,
            )
            db.add(image)
            uploaded += 1

        db.commit()
        logger.info(f"Uploaded {uploaded} images ({len(errors)} failed)")
        return RedirectResponse("/dashboard/images", status_code=303)
    finally:
        db.close()


# ── Schedule Instagram post ───────────────────────────

@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, _=Depends(require_login)):
    db = SessionLocal()
    try:
        ig_images = (
            db.query(Image)
            .filter(Image.content_type == ContentType.INSTAGRAM.value, Image.is_active == True)
            .order_by(Image.created_at.desc())
            .all()
        )
        scheduled = (
            db.query(ScheduledPost)
            .order_by(ScheduledPost.scheduled_at.desc())
            .limit(30)
            .all()
        )

        has_ig_creds = bool(INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN)

        return templates.TemplateResponse("schedule.html", {
            "request": request,
            "ig_images": ig_images,
            "scheduled": scheduled,
            "has_ig_creds": has_ig_creds,
        })
    finally:
        db.close()


@router.post("/schedule")
async def schedule_submit(
    request: Request,
    image_id: int = Form(...),
    caption: str = Form(""),
    scheduled_date: str = Form(...),
    scheduled_time: str = Form(...),
    _=Depends(require_login),
):
    # Validate image is Instagram-safe
    db = SessionLocal()
    try:
        image = db.query(Image).get(image_id)
        if not image or image.content_type != ContentType.INSTAGRAM.value:
            raise HTTPException(status_code=400, detail="Image is not Instagram-safe")

        # Auto-generate AI caption if left blank
        if not caption.strip():
            try:
                caption = await generate_caption(image.title, image.description or "")
            except Exception as e:
                logger.warning(f"AI caption generation failed: {e}")
                caption = ""

        scheduled_at = datetime.datetime.fromisoformat(f"{scheduled_date}T{scheduled_time}")

        post = ScheduledPost(
            image_id=image_id,
            caption=caption,
            scheduled_at=scheduled_at,
        )
        db.add(post)
        db.commit()
        return RedirectResponse("/dashboard/schedule", status_code=303)
    finally:
        db.close()


@router.post("/schedule/{post_id}/delete")
async def schedule_delete(post_id: int, request: Request, _=Depends(require_login)):
    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).get(post_id)
        if post and post.status == "pending":
            db.delete(post)
            db.commit()
        return RedirectResponse("/dashboard/schedule", status_code=303)
    finally:
        db.close()


@router.post("/schedule/{post_id}/post-now")
async def post_now(post_id: int, request: Request, _=Depends(require_login)):
    """Immediately post a scheduled post."""
    from bot.services.instagram import post_to_instagram, InstagramSafetyError

    if not INSTAGRAM_USER_ID or not INSTAGRAM_ACCESS_TOKEN:
        raise HTTPException(status_code=400, detail="Instagram credentials not configured")

    db = SessionLocal()
    try:
        post = db.query(ScheduledPost).get(post_id)
        if not post or post.status != "pending":
            raise HTTPException(status_code=404, detail="Post not found or already processed")

        image = db.query(Image).get(post.image_id)
        if not image:
            raise HTTPException(status_code=404, detail="Image not found")

        try:
            result = await post_to_instagram(image, INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN, post.caption or "")
            if result.get("success"):
                post.status = "posted"
                post.ig_media_id = result.get("media_id")
                post.posted_at = datetime.datetime.utcnow()
            else:
                post.status = "failed"
                post.error_message = result.get("error", "Unknown error")
        except InstagramSafetyError as e:
            post.status = "failed"
            post.error_message = str(e)
        except Exception as e:
            post.status = "failed"
            post.error_message = str(e)

        db.commit()
        return RedirectResponse("/dashboard/schedule", status_code=303)
    finally:
        db.close()


# ── Scheduler job: auto-post due scheduled posts ─────

async def process_scheduled_posts():
    """Called by APScheduler — posts any due scheduled posts."""
    from bot.services.instagram import post_to_instagram, InstagramSafetyError

    if not INSTAGRAM_USER_ID or not INSTAGRAM_ACCESS_TOKEN:
        return

    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        due_posts = (
            db.query(ScheduledPost)
            .filter(ScheduledPost.status == "pending", ScheduledPost.scheduled_at <= now)
            .all()
        )

        for post in due_posts:
            image = db.query(Image).get(post.image_id)
            if not image:
                post.status = "failed"
                post.error_message = "Image not found"
                db.commit()
                continue

            try:
                result = await post_to_instagram(
                    image, INSTAGRAM_USER_ID, INSTAGRAM_ACCESS_TOKEN, post.caption or ""
                )
                if result.get("success"):
                    post.status = "posted"
                    post.ig_media_id = result.get("media_id")
                    post.posted_at = datetime.datetime.utcnow()
                    logger.info(f"Scheduled post #{post.id} published to Instagram")
                else:
                    post.status = "failed"
                    post.error_message = result.get("error", "Unknown error")
                    logger.error(f"Scheduled post #{post.id} failed: {post.error_message}")
            except InstagramSafetyError as e:
                post.status = "failed"
                post.error_message = str(e)
                logger.critical(f"SAFETY BLOCK on scheduled post #{post.id}: {e}")
            except Exception as e:
                post.status = "failed"
                post.error_message = str(e)
                logger.error(f"Scheduled post #{post.id} error: {e}")

            db.commit()
    finally:
        db.close()
