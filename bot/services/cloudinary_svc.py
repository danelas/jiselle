import cloudinary
import cloudinary.uploader
import cloudinary.api
from bot.config import CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True
)

# ── Strict folder separation: NSFW content must NEVER reach Instagram folder ──
FOLDER_INSTAGRAM = "companion_bot/instagram"   # SFW only — safe for public posting
FOLDER_PRIVATE = "companion_bot/private"        # NSFW / Telegram-only — NEVER publish


def folder_for_content_type(content_type: str) -> str:
    """Return the correct Cloudinary folder based on content type.
    Defaults to PRIVATE to prevent accidental public exposure."""
    if content_type == "instagram":
        return FOLDER_INSTAGRAM
    return FOLDER_PRIVATE


def upload_image(file_path: str, folder: str = "companion_bot/private") -> dict:
    """Upload an image to Cloudinary and return URLs."""
    result = cloudinary.uploader.upload(
        file_path,
        folder=folder,
        resource_type="image"
    )

    return {
        "public_id": result["public_id"],
        "full_url": result["secure_url"],
    }


def upload_image_from_bytes(file_bytes: bytes, filename: str, folder: str = "companion_bot/private") -> dict:
    """Upload image from bytes (e.g., from Telegram file download)."""
    import io
    result = cloudinary.uploader.upload(
        io.BytesIO(file_bytes),
        folder=folder,
        resource_type="image",
        public_id=filename.split(".")[0] if "." in filename else filename
    )

    return {
        "public_id": result["public_id"],
        "full_url": result["secure_url"],
    }


def delete_image(public_id: str) -> bool:
    """Delete an image from Cloudinary."""
    result = cloudinary.uploader.destroy(public_id)
    return result.get("result") == "ok"


def get_full_url(public_id: str) -> str:
    """Get the full-resolution URL for a Cloudinary image."""
    return cloudinary.CloudinaryImage(public_id).build_url()


