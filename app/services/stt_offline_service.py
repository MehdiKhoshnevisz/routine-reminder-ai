"""Offline speech-to-text using Vosk (no API key needed).

Downloads a small Persian Vosk model on first use and caches it in /tmp.
Telegram voice messages (.ogg/Opus) are decoded with soundfile and fed to Vosk.

Accuracy improvements over the naive version:
- Proper anti-aliased resampling via scipy.signal.resample_poly (not linear interp)
- Peak-normalization of the audio before recognition
- Chunked feeding (10s windows) so the recognizer sees manageable segments
- Word-level confidence is extracted and logged; low-confidence results are flagged
"""

import asyncio
import io
import json
import logging
import os
import urllib.request
import zipfile

from app.core.config import settings

log = logging.getLogger(__name__)

# Model selection via env var: "small" (default, ~130MB) or "large" (~1.1GB, better accuracy)
VOSK_MODEL_SIZE = os.getenv("VOSK_MODEL_SIZE", "small").lower()

if VOSK_MODEL_SIZE == "large":
    # Large Persian model (~1.1GB extracted) - significantly better accuracy
    VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-fa-0.5.zip"
    MODEL_DIR = "/tmp/vosk-model-fa-large"
    MODEL_ZIP = "/tmp/vosk-model-fa-large.zip"
    log.info("Using LARGE Vosk Persian model (VOSK_MODEL_SIZE=large)")
else:
    # Small Persian model (~53MB zip, ~130MB extracted) - good enough for short voice commands
    VOSK_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-fa-0.5.zip"
    MODEL_DIR = "/tmp/vosk-model-fa"
    MODEL_ZIP = "/tmp/vosk-model-fa.zip"
    log.info("Using SMALL Vosk Persian model (VOSK_MODEL_SIZE=small)")

# Vosk works best at 16kHz; Telegram voice is typically 48kHz Opus
TARGET_SAMPLE_RATE = 16000

# Max voice duration to process (Telegram allows up to ~20 min; we cap at 5 min)
MAX_VOICE_SECONDS = 300

# Feed audio to the recognizer in chunks of this many samples (10s @ 16kHz)
CHUNK_SAMPLES = TARGET_SAMPLE_RATE * 10

_model = None
_model_lock = asyncio.Lock()


def _download_and_extract_model():
    """Download and extract the Vosk model if not already present. Blocking I/O, run in executor.

    Space-efficient: streams download, extracts directly to target dir, deletes zip immediately.
    """
    if os.path.isdir(MODEL_DIR) and os.path.exists(os.path.join(MODEL_DIR, "am", "final.mdl")):
        return

    log.info("Downloading Vosk Persian model (streaming)...")

    # Stream download to avoid loading entire zip in memory
    import shutil
    with urllib.request.urlopen(VOSK_MODEL_URL) as resp, open(MODEL_ZIP, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        while True:
            chunk = resp.read(1024 * 1024)  # 1MB chunks
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if total:
                log.debug("Downloaded %d/%d bytes (%.1f%%)", downloaded, total, 100 * downloaded / total)

    log.info("Extracting Vosk model to %s...", MODEL_DIR)

    # Extract directly to target directory (avoids duplicate space in /tmp)
    with zipfile.ZipFile(MODEL_ZIP, "r") as zf:
        # Create target dir
        os.makedirs(MODEL_DIR, exist_ok=True)
        for member in zf.infolist():
            # Skip directories, extract files directly to MODEL_DIR preserving structure
            target_path = os.path.join(MODEL_DIR, member.filename)
            if member.is_dir():
                os.makedirs(target_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    # Clean up zip immediately
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
    """Decode .ogg (Opus) audio to 16kHz mono PCM bytes.

    Uses soundfile (libsndfile >= 1.1 supports Opus in OGG) for decoding,
    then scipy.signal.resample_poly for proper anti-aliased resampling,
    then peak-normalizes the signal.
    """
    import soundfile as sf
    import numpy as np

    # Read the ogg file as float32 for better processing headroom
    data, sr = sf.read(io.BytesIO(ogg_bytes), dtype="float32")

    # Convert to mono if stereo
    if data.ndim > 1:
        data = data.mean(axis=1)

    # Peak-normalize to 0.9 to avoid clipping and help the recognizer
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1e-6:
        data = data * (0.9 / peak)

    # Resample to 16kHz if needed, using proper anti-aliased polyphase filtering
    if sr != TARGET_SAMPLE_RATE:
        try:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(sr), TARGET_SAMPLE_RATE)
            up = TARGET_SAMPLE_RATE // g
            down = int(sr) // g
            data = resample_poly(data, up, down)
        except ImportError:
            # Fallback: linear interpolation (less ideal but functional)
            duration = len(data) / sr
            new_len = int(duration * TARGET_SAMPLE_RATE)
            indices = np.linspace(0, len(data) - 1, new_len)
            data = np.interp(indices, np.arange(len(data)), data)

    # Convert to int16 PCM
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    return pcm.tobytes()


# Common Persian command phrases for the routine bot. Used as a Vosk grammar
# in pass 1 — constraining recognition to likely phrases massively improves
# accuracy for short command-style voice input. Pass 2 falls back to free-form.
# Only include phrases with words that exist in the Persian model vocabulary.
COMMAND_PHRASES = [
    # Core commands
    "یادم بنداز",
    "یادم بینداز",
    "یادآوری",
    "تنظیم کن",
    "یادت باشه",
    # Time/frequency
    "هر روز",
    "هر روز ساعت",
    "روزانه",
    "فقط امروز",
    "فقط فردا",
    "فردا",
    "امروز",
    "فردا ساعت",
    "امروز ساعت",
    "صبح",
    "شام",
    "بعدازظهر",
    # Listing
    "لیست کارها",
    "لیست کارهایم",
    "کارها",
    "کارهام",
    "نمایش بده",
    "نشان بده",
    "چی کارها دارم",
    # Deletion
    "حذف کن",
    "پاک کن",
    "نمیخوام",
    "نیاز ندارم",
    "لغو کن",
    "حذفش کن",
    "پاکش کن",
    # Time
    "ساعت",
    # Common activities
    "تئاتر",
    "بلیط",
    "بخرم",
    "بازی",
    "ورزش",
    "دارو",
    "غذا",
    "جلسه",
    "ملاقات",
    "خرید",
    "دکتر",
    "دندون",
    "پیوست",
    # Relative dates
    "دو هفته",
    "هفته بعد",
    "ماه بعد",
    "هفته دیگه",
    "ماه دیگه",
    "روز دیگه",
    "پریروز",
    "파روز",
    # Numbers (spoken)
    "یک",
    "دو",
    "سه",
    "چهار",
    "پنج",
    "شش",
    "هفت",
    "هشت",
    "نه",
    "ده",
    "یازده",
    "دوازده",
    "سی",
    "چهل",
    "پنجاه",
]


def _build_grammar_json() -> str:
    """Build a Vosk grammar JSON from the command phrase list."""
    phrases = [p for p in COMMAND_PHRASES if p.strip()]
    return json.dumps(phrases, ensure_ascii=False)


def _recognize_pcm(model, pcm_data: bytes, grammar: str | None = None) -> tuple[str, float]:
    """Run Vosk recognition on PCM data, feeding it in chunks.

    Returns (text, avg_confidence). Confidence is the mean of word-level
    confidences reported by Vosk (0..1); 0.0 if none reported.
    If `grammar` is given, the recognizer is constrained to those phrases.
    """
    import vosk

    if grammar:
        rec = vosk.KaldiRecognizer(model, TARGET_SAMPLE_RATE, grammar)
    else:
        rec = vosk.KaldiRecognizer(model, TARGET_SAMPLE_RATE)
    rec.SetWords(True)

    # Feed in chunks so the recognizer processes manageable segments
    chunk_bytes = CHUNK_SAMPLES * 2  # int16 = 2 bytes/sample
    offset = 0
    total = len(pcm_data)
    while offset < total:
        end = min(offset + chunk_bytes, total)
        rec.AcceptWaveform(pcm_data[offset:end])
        offset = end

    result = json.loads(rec.Result())
    text = (result.get("text") or "").strip()

    # Compute average word confidence
    confs = [w.get("conf", 0.0) for w in result.get("result", [])]
    avg_conf = sum(confs) / len(confs) if confs else 0.0

    if not text:
        partial = json.loads(rec.PartialResult())
        text = (partial.get("partial") or "").strip()

    return text, avg_conf


async def transcribe_voice_offline(file_bytes: bytes) -> str | None:
    """Transcribe voice audio bytes to text using local Vosk model.

    Two-pass strategy:
    1. Grammar-constrained pass (COMMAND_PHRASES) — best for short commands
    2. Free-form pass if pass 1 confidence is low — catches unexpected phrases

    Returns the transcribed text, or None if transcription failed.
    """
    if not file_bytes:
        return None

    try:
        model = await _get_model()

        # Decode audio (blocking I/O in executor)
        loop = asyncio.get_running_loop()
        pcm_data = await loop.run_in_executor(None, _decode_ogg_to_pcm, file_bytes)

        if not pcm_data:
            return None

        # Duration guard (after decode, based on actual PCM length)
        duration_s = len(pcm_data) / 2 / TARGET_SAMPLE_RATE
        if duration_s > MAX_VOICE_SECONDS:
            log.warning("Voice too long (%.1fs), truncating to %ds", duration_s, MAX_VOICE_SECONDS)
            pcm_data = pcm_data[: int(MAX_VOICE_SECONDS * TARGET_SAMPLE_RATE * 2)]

        # --- Pass 1: Grammar-constrained (fast, high accuracy for known commands) ---
        grammar = _build_grammar_json()
        text1, conf1 = await loop.run_in_executor(None, _recognize_pcm, model, pcm_data, grammar)

        # If pass 1 gave good confidence, use it
        if text1 and conf1 >= 0.35:
            log.info("Pass 1 (grammar) OK (conf=%.2f): %s", conf1, text1[:100])
            return text1

        # --- Pass 2: Free-form fallback (catches unexpected phrases) ---
        log.info("Pass 1 low confidence (conf=%.2f), trying free-form...", conf1)
        text2, conf2 = await loop.run_in_executor(None, _recognize_pcm, model, pcm_data, None)

        # Pick the better result (prefer pass 1 if it has text and pass 2 is very low)
        if text2 and conf2 > conf1:
            log.info("Pass 2 (free-form) better (conf=%.2f): %s", conf2, text2[:100])
            return text2
        elif text1:
            log.info("Falling back to Pass 1 (conf=%.2f): %s", conf1, text1[:100])
            return text1
        elif text2:
            log.info("Using Pass 2 only (conf=%.2f): %s", conf2, text2[:100])
            return text2

        return None

    except Exception:
        log.exception("Offline voice transcription failed")
        return None
