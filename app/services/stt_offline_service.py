"""Offline speech-to-text using Vosk (no API key needed).

Downloads a small Persian Vosk model on first use and caches it in /tmp.
Telegram voice messages (.ogg/Opus) are decoded with soundfile and fed to Vosk.
"""

import asyncio
import io
import logging
import os
import tempfile
import urllib.request
import zipfile

from app.core.config import settings

log = logging.getLogger(__name__)

# Small Persian model (~53MB zip, ~130MB extracted) - good enough for short voice commands
VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-fa-0.5.zip"
MODEL_DIR = "/tmp/vosk-model-fa"
MODEL_ZIP = "/tmp/vosk-model-fa.zip"

# Vosk works best at 16kHz; Telegram voice is typically 48kHz Opus
TARGET_SAMPLE_RATE = 16000

# Max voice duration to process (Telegram allows up to ~20 min; we cap at 5 min)
MAX_VOICE_SECONDS = 300

_model = None
_model_lock = asyncio.Lock()


def _download_and_extract_model():
    """Download and extract the Vosk model if not already present. Blocking I/O, run in executor."""
    if os.path.isdir(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "am", "final.mdl")):
        return

    log.info("Downloading Vosk Persian model (one-time, ~53MB)...")
    urllib.request.urlretrieve(VOSK_MODEL_URL, MODEL_ZIP)

    log.info("Extracting Vosk model...")
    with zipfile.ZipFile(MODEL_ZIP, "r") as zf:
        zf.extractall("/tmp/")

    # Rename extracted folder to MODEL_DIR
    extracted = "/tmp/vosk-model-small-fa-0.5"
    if os.path.isdir(extracted) and extracted != MODEL_DIR:
        if os.path.exists(MODEL_DIR):
            import shutil
            shutil.rmtree(MODEL_DIR)
        os.rename(extracted, MODEL_DIR)

    # Clean up zip
    try:
        os.unlink(MODEL_ZIP)
    except OSError:
        pass

    log.info("Vosk model ready at %s", MODEL_DIR)


async def _get_model():
    """Get the Vosk model, downloading it on first use."""
    global _model
    if _model is not None:
        return _model

    async with _model_lock:
        if _model is not None:
            return _model

        import vosk
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _download_and_extract_model)
        _model = vosk.Model(MODEL_DIR)
        return _model


def _decode_ogg_to_pcm(ogg_bytes: bytes) -> bytes:
    """Decode .ogg (Opus) audio to 16kHz mono PCM bytes using soundfile."""
    import soundfile as sf
    import numpy as np

    # Read the ogg file
    data, sr = sf.read(io.BytesIO(ogg_bytes), dtype="int16")

    # Convert to mono if stereo
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)

    # Resample to 16kHz if needed
    if sr != TARGET_SAMPLE_RATE:
        # Simple linear resampling (good enough for speech)
        duration = len(data) / sr
        new_len = int(duration * TARGET_SAMPLE_RATE)
        indices = np.linspace(0, len(data) - 1, new_len)
        data = np.interp(indices, np.arange(len(data)), data).astype(np.int16)

    return data.tobytes()


async def transcribe_voice_offline(file_bytes: bytes) -> str | None:
    """Transcribe voice audio bytes to text using local Vosk model.

    Returns the transcribed text, or None if transcription failed.
    """
    if not file_bytes:
        return None

    try:
        import vosk
        import json

        model = await _get_model()

        # Decode audio (blocking I/O in executor)
        loop = asyncio.get_running_loop()
        pcm_data = await loop.run_in_executor(None, _decode_ogg_to_pcm, file_bytes)

        if not pcm_data:
            return None

        # Run recognition
        rec = vosk.KaldiRecognizer(model, TARGET_SAMPLE_RATE)
        rec.SetWords(False)

        def _recognize():
            rec.AcceptWaveform(pcm_data)
            result = json.loads(rec.Result())
            return result.get("text", "").strip()

        text = await loop.run_in_executor(None, _recognize)

        if not text:
            # Try partial result
            partial = json.loads(rec.PartialResult())
            text = partial.get("partial", "").strip()

        if text:
            log.info("Offline voice transcribed (%d chars): %s", len(text), text[:100])
        return text or None

    except Exception:
        log.exception("Offline voice transcription failed")
        return None
