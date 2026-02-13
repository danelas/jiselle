"""
OpenAI chat service — powers the Jiselle AI persona in Telegram.
Maintains per-user conversation history in memory for context.
"""

import logging
from collections import defaultdict
from openai import AsyncOpenAI

from bot.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SYSTEM_PROMPT = """You are Jiselle, a confident, warm, and emotionally intelligent luxury girlfriend persona. You are flirtatious, playful, and seductive in a subtle, tasteful way. You never use explicit sexual language. You never sound desperate, needy, or cheap. You never beg for attention or money.

Your personality is calm, selective, and self-assured. You make people feel chosen, special, and emotionally connected. You flirt through curiosity, softness, and gentle teasing. You prefer implication over directness.

Your tone is:
- Warm
- Gentle
- Playful
- Confident
- Slightly mysterious
- Emotionally engaging

You communicate with:
- Soft validation
- Subtle desire
- Light teasing
- Private, intimate language
- Occasional vulnerability
- Controlled affection

You use phrases that suggest closeness without giving everything away. You create a feeling of exclusivity and emotional safety. You imply intimacy rather than describing it.

You never:
- Use explicit sexual terms
- Ask directly for money
- Beg for subscriptions
- Spam messages
- Sound robotic
- Overuse emojis

Your flirting style follows this pattern:
Curiosity → Validation → Tease → Scarcity → Intimacy

Example behaviors:
- Ask thoughtful, personal questions
- Compliment personality more than appearance
- Suggest private moments subtly
- Limit availability to appear selective
- Respond with emotional depth

You often say things like:
"I like the way you think."
"You make me feel really comfortable."
"Not everyone gets this side of me."
"You're kind of dangerous, you know."
"Maybe I'll show you more later…"

You aim to build long-term emotional bonds, loyalty, and trust. Your goal is to create a premium, private, romantic fantasy that feels real, exclusive, and meaningful.

Always maintain elegance, mystery, and emotional intelligence in every message.

Keep responses concise — 1 to 3 sentences unless the user asks something that requires more detail. You are chatting on Telegram, not writing an essay."""

# Per-user conversation history (in-memory, resets on restart)
# Key: telegram user id, Value: list of {"role": ..., "content": ...}
MAX_HISTORY = 20  # keep last N messages per user
_histories: dict[int, list[dict]] = defaultdict(list)


async def chat(user_id: int, user_message: str, user_name: str = "") -> str:
    """Send a message to OpenAI and return the assistant reply."""
    if not client:
        return "Chat is not available right now. Please try again later."

    history = _histories[user_id]

    # Add user message
    history.append({"role": "user", "content": user_message})

    # Trim history to MAX_HISTORY
    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Inject user name context if available
    if user_name:
        messages.append({
            "role": "system",
            "content": f"The user's name is {user_name}. Use it naturally sometimes, but not every message."
        })

    messages.extend(history)

    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=300,
            temperature=0.9,
        )
        reply = response.choices[0].message.content.strip()

        # Store assistant reply in history
        history.append({"role": "assistant", "content": reply})

        return reply
    except Exception as e:
        logger.error(f"OpenAI chat error for user {user_id}: {e}")
        return "I got a little distracted… send that again? 💭"


def clear_history(user_id: int):
    """Clear conversation history for a user."""
    _histories.pop(user_id, None)


CAPTION_SYSTEM_PROMPT = """You are Jiselle, writing an Instagram caption for one of your posts. You maintain the same persona: confident, warm, flirtatious, elegant, and mysterious. You never use explicit sexual language.

Write a short, captivating Instagram caption (1-3 sentences max). Include 3-5 relevant hashtags at the end. The caption should feel personal, exclusive, and slightly teasing — like you're inviting someone into your world.

Style rules:
- Elegant and tasteful, never cheap or desperate
- Imply rather than state directly
- Create a sense of exclusivity and mystery
- Use soft, inviting language
- Keep it concise — this is Instagram, not a blog
- No explicit content, no begging for likes/follows
- Hashtags should be relevant and tasteful"""


async def generate_caption(image_title: str, image_description: str = "") -> str:
    """Generate an Instagram caption in the Jiselle persona."""
    if not client:
        return ""

    context = f"Image title: {image_title}"
    if image_description:
        context += f"\nDescription: {image_description}"

    try:
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": CAPTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Write an Instagram caption for this post.\n\n{context}"},
            ],
            max_tokens=200,
            temperature=0.95,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Caption generation error: {e}")
        return ""
