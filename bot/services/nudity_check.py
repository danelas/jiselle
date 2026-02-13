"""
OpenAI Vision-based image classifier.
Detects nudity AND apparel/category in a single API call.
Used during bulk upload to auto-flag explicit images and auto-assign categories.
"""

import json
import base64
import logging
from dataclasses import dataclass
from openai import AsyncOpenAI
from bot.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

CLASSIFY_PROMPT = (
    "You are an image content classifier. Analyze this image and respond with JSON only.\n"
    "{\n"
    '  "explicit": true/false,\n'
    '  "category": "one of the categories below"\n'
    "}\n\n"
    "Rules for 'explicit': true if the image contains nudity, exposed private parts, "
    "or sexually explicit content. false otherwise. Lingerie, swimwear, or suggestive-but-clothed is NOT explicit.\n\n"
    "Rules for 'category' — pick the BEST match:\n"
    '- "lingerie" — wearing lingerie, bra/panties, corsets, stockings, garter belts\n'
    '- "lifestyle" — casual/everyday photos, selfies, outfits, travel, food, gym, day-to-day\n'
    '- "exclusive" — artistic nudes, boudoir, bathtub, bed, or premium private content\n'
    '- "instagram" — safe-for-work, fully clothed, landscape, promotional\n\n'
    "Respond with valid JSON only, no other text."
)


@dataclass
class ImageClassification:
    is_explicit: bool
    category_key: str  # "lingerie", "lifestyle", "exclusive", "instagram"


async def classify_image(image_bytes: bytes, mimetype: str = "image/jpeg") -> ImageClassification:
    """Classify an image for nudity and category in one API call."""
    if not client:
        logger.warning("OpenAI client not configured — skipping classification")
        return ImageClassification(is_explicit=False, category_key="exclusive")

    try:
        media_type = mimetype if mimetype.startswith("image/") else "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CLASSIFY_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{b64}",
                                "detail": "low",
                            },
                        },
                    ],
                }
            ],
            max_tokens=50,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(raw)
        result = ImageClassification(
            is_explicit=bool(data.get("explicit", False)),
            category_key=data.get("category", "exclusive"),
        )
        logger.info(f"Image classified: explicit={result.is_explicit}, category={result.category_key}")
        return result

    except Exception as e:
        logger.error(f"Image classification failed: {e}")
        return ImageClassification(is_explicit=False, category_key="exclusive")


# Keep backward-compatible alias
async def check_explicit(image_bytes: bytes, mimetype: str = "image/jpeg") -> bool:
    result = await classify_image(image_bytes, mimetype)
    return result.is_explicit
