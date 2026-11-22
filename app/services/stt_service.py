"""Speech-to-text service using OpenRouter's audio transcription API.

Telegram sends voice messages as .ogg (Opus codec), which Whisper handles natively.
"""

import asyncio
import logging
import os
import tempfile

from openai import AsyncOpenAI

from app.core.config import settings

log = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _get_client() -> AsyncOpenAI:
    """OpenRouter client for audio transcription (same as LLM client)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client_loop = loop
        _client = AsyncOpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            timeout=settings.OPENROUTER_TIMEOUT,
            max_retries=settings.OPENROUTER_MAX_RETRIES,
            default_headers={"X-Title": settings.APP_NAME},
        )
    return _client


# Whisper model on OpenRouter (cheap and accurate for Persian)
WHISPER_MODEL = "openai/whisper-1"

# Max voice duration Telegram allows: ~20 min. We cap at 5 min to control cost.
MAX_VOICE_SECONDS = 300


async def transcribe_voice(file_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    """Transcribe voice audio bytes to text using Whisper via OpenRouter.

    Returns the transcribed text, or None if transcription failed.
    """
    if not file_bytes:
        return None

    # Write to temp file (Whisper API needs a file, not bytes)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1] or ".ogg", delete=False) as f:
            f.write(file_bytes)
            tmp_path = f.name

        with open(tmp_path, "rb") as audio_file:
            resp = await _get_client().audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_file,
                language="fa",  # Persian - improves accuracy
                response_format="text",
            )

        text = (resp or "").strip()
        if not text:
            return None
        log.info("Voice transcribed (%d chars): %s", len(text), text[:100])
        return text

    except Exception:
        log.exception("Voice transcription failed")
        return None

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
