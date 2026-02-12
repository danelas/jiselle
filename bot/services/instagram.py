"""
Instagram posting service with strict NSFW content guard.

SAFETY RULE: Only images with content_type='instagram' can EVER be posted.
Private/NSFW content is blocked at multiple levels:
  1. Database query filter (only content_type='instagram')
  2. Explicit guard check before every post
  3. Cloudinary folder separation (instagram/ vs private/)
"""

import logging
import httpx
import datetime
from bot.models.database import SessionLocal
from bot.models.schemas import Image, ContentType

logger = logging.getLogger(__name__)


class InstagramSafetyError(Exception):
    """Raised when attempting to post non-Instagram content."""
    pass


def _assert_safe_for_instagram(image: Image) -> None:
    """Hard guard: raises if image is not explicitly marked as Instagram-safe.
    This is the final safety check before any public posting."""
    if image.content_type != ContentType.INSTAGRAM.value:
        raise InstagramSafetyError(
            f"BLOCKED: Image #{image.id} ('{image.title}') has content_type='{image.content_type}'. "
            f"Only content_type='instagram' can be posted publicly. "
            f"This image is in the PRIVATE folder and must NEVER be published."
        )


def get_instagram_ready_images(limit: int = 20) -> list:
    """Get images that are safe for Instagram posting.
    Only returns images with content_type='instagram'."""
    db = SessionLocal()
    try:
        return (
            db.query(Image)
            .filter(
                Image.content_type == ContentType.INSTAGRAM.value,
                Image.is_active == True,
            )
            .order_by(Image.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def get_unposted_instagram_images(limit: int = 10) -> list:
    """Get Instagram-safe images that haven't been posted yet.
    Uses the instagram_posted flag (must be added to Image model if scheduling is needed)."""
    db = SessionLocal()
    try:
        return (
            db.query(Image)
            .filter(
                Image.content_type == ContentType.INSTAGRAM.value,
                Image.is_active == True,
            )
            .order_by(Image.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


async def post_to_instagram(
    image: Image,
    ig_user_id: str,
    ig_access_token: str,
    caption: str = "",
) -> dict:
    """Post an image to Instagram via the Graph API.

    SAFETY: Will raise InstagramSafetyError if the image is not content_type='instagram'.

    Args:
        image: The Image ORM object to post
        ig_user_id: Instagram Business Account user ID
        ig_access_token: Long-lived Instagram Graph API access token
        caption: Post caption text

    Returns:
        dict with 'creation_id' and 'media_id' on success
    """
    # ── SAFETY GUARD ── This MUST run before any posting logic
    _assert_safe_for_instagram(image)

    api_base = "https://graph.facebook.com/v18.0"

    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Create media container
        container_resp = await client.post(
            f"{api_base}/{ig_user_id}/media",
            params={
                "image_url": image.cloudinary_url,
                "caption": caption,
                "access_token": ig_access_token,
            }
        )
        container_data = container_resp.json()

        if "id" not in container_data:
            error = container_data.get("error", {}).get("message", "Unknown error")
            logger.error(f"Instagram container creation failed: {error}")
            return {"error": error}

        creation_id = container_data["id"]
        logger.info(f"Instagram container created: {creation_id} for image #{image.id}")

        # Step 2: Publish the container
        publish_resp = await client.post(
            f"{api_base}/{ig_user_id}/media_publish",
            params={
                "creation_id": creation_id,
                "access_token": ig_access_token,
            }
        )
        publish_data = publish_resp.json()

        if "id" not in publish_data:
            error = publish_data.get("error", {}).get("message", "Unknown error")
            logger.error(f"Instagram publish failed: {error}")
            return {"error": error}

        media_id = publish_data["id"]
        logger.info(f"Instagram post published: {media_id} for image #{image.id}")

        return {
            "creation_id": creation_id,
            "media_id": media_id,
            "success": True,
        }


async def post_image_by_id(
    image_id: int,
    ig_user_id: str,
    ig_access_token: str,
    caption: str = "",
) -> dict:
    """Post a specific image by ID — with full safety checks."""
    db = SessionLocal()
    try:
        image = db.query(Image).get(image_id)
        if not image:
            return {"error": "Image not found"}

        # Safety guard runs inside post_to_instagram
        return await post_to_instagram(image, ig_user_id, ig_access_token, caption)
    finally:
        db.close()
