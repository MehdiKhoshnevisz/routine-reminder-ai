"""API-based speech-to-text using Groq/Cloudflare (no local model needed).

Replaces the offline Vosk service. Uses OpenAI-compatible API format.
"""

import logging
from typing import Literal

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

STTProvider = Literal["groq", "cloudflare", "offline"]


async def transcribe_voice(file_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Transcribe voice audio using configured STT provider.

    Args:
        file_bytes: Raw audio bytes (OGG/Opus from Telegram)
        filename: Original filename for content-type detection

    Returns:
        Transcribed text or None if failed
    """
    provider: STTProvider = settings.STT_PROVIDER.lower()  # type: ignore[assignment]

    if provider == "groq":
        return await _transcribe_groq(file_bytes, filename)
    elif provider == "cloudflare":
        return await _transcribe_cloudflare(file_bytes, filename)
    elif provider == "offline":
        # Fallback to offline Vosk (kept for backward compat)
        from app.services.stt_offline_service import transcribe_voice_offline
        return await transcribe_voice_offline(file_bytes)

    log.warning("Unknown STT_PROVIDER: %s", provider)
    return None


# Default Persian context prompt for Whisper to improve routine command recognition
DEFAULT_GROQ_PROMPT = (
    "یادم بنداز، یادم بینداز، یادآوری، تنظیم کن، یادت باشه، "
    "هر روز، روزانه، فردا، امروز، پریروز، هفته بعد، ماه بعد، دو هفته دیگه، "
    "ساعت، صبح، شام، بعدازظهر، "
    "لیست کارها، کارها، کارهام، نمایش بده، نشون بده، "
    "حذف کن، پاک کن، نمی‌خوام، نیاز ندارم، لغو کن، حذفش کن، پاکش کن، "
    "تئاتر، بلیط، بخرم، بازی، ورزش، دارو، غذا، جلسه، ملاقات، خرید، دکتر، دندون، "
    "یک، دو، سه، چهار، پنج، شش، هفت، هشت، نه، ده، یازده، دوازده، سی، چهل، پنجاه"
)


async def _transcribe_groq(file_bytes: bytes, filename: str) -> str | None:
    """Transcribe using Groq's Whisper API (OpenAI-compatible).

    Uses configurable model (default whisper-large-v3 for accuracy) + Persian prompt.
    """
    if not settings.GROQ_API_KEY:
        log.error("GROQ_API_KEY not configured")
        return None

    # Use custom prompt from env, or default
    prompt = settings.GROQ_WHISPER_PROMPT or DEFAULT_GROQ_PROMPT
    model = settings.GROQ_WHISPER_MODEL or "whisper-large-v3"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"file": (filename, file_bytes, "audio/ogg")}
            data = {
                "model": model,
                "language": "fa",
                "prompt": prompt,
                "response_format": "json",
                "temperature": 0.0,  # Deterministic
            }
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                files=files,
                data=data,
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()

            if text:
                log.info("Groq STT transcribed (%d chars, model=%s): %s", len(text), model, text[:100])
            return text or None

    except httpx.HTTPStatusError as e:
        log.error("Groq STT HTTP error: %s - %s", e.response.status_code, e.response.text)
        return None
    except Exception:
        log.exception("Groq STT failed")
        return None


async def _transcribe_cloudflare(file_bytes: bytes, filename: str) -> str | None:
    """Transcribe using Cloudflare Workers AI Whisper."""
    if not settings.CF_ACCOUNT_ID or not settings.CF_API_TOKEN:
        log.error("CF_ACCOUNT_ID or CF_API_TOKEN not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"file": (filename, file_bytes, "audio/ogg")}
            resp = await client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{settings.CF_ACCOUNT_ID}/ai/run/@cf/openai/whisper-large-v3-turbo",
                headers={"Authorization": f"Bearer {settings.CF_API_TOKEN}"},
                files=files,
            )
            resp.raise_for_status()
            result = resp.json()

            # Cloudflare returns {result: {text: "..."}, success: true, errors: [], messages: []}
            if result.get("success") and result.get("result"):
                text = result["result"].get("text", "").strip()
                if text:
                    log.info("Cloudflare STT transcribed (%d chars): %s", len(text), text[:100])
                return text or None

            log.error("Cloudflare STT error: %s", result.get("errors"))
            return None

    except httpx.HTTPStatusError as e:
        log.error("Cloudflare STT HTTP error: %s - %s", e.response.status_code, e.response.text)
        return None
    except Exception:
        log.exception("Cloudflare STT failed")
        return None