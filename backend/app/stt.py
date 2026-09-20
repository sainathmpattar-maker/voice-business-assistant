"""
app/stt.py — Groq Whisper STT with:
  - Runtime MIME-type / extension derivation (fixes mobile audio/mp4 recordings)
  - WAV fallback via pydub+imageio-ffmpeg if Groq returns invalid_media_file
  - Whisper repetition de-duplication
  - Optional language hint (avoids e.g. Telugu being detected as Malayalam)
  - Minimum-size guard (< 2 KB → ValueError before any API call)
  - temperature=0 for deterministic output
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from typing import Optional

from config import get_settings

logger = logging.getLogger(__name__)

# ── Domain vocabulary hints for Whisper ───────────────────────────────────────
DOMAIN_HINTS = (
    "Paytm, kirana, chawal, atta, udhaar, dues, stock, dukan, namkeen, "
    "biscuit, tel, dal, cheeni, chai, rupee, rupaye, bikri, kamai, munafa, "
    "bikri kaisa raha, kiska udhaar baaki hai, stockout, Basmati, UPI, QR code."
)

# Minimum blob size we'll even send to Groq (saves API quota on garbage)
MIN_AUDIO_BYTES = 2_048  # 2 KB

# ── MIME → (extension, canonical_content_type) map ────────────────────────────
_MIME_MAP: dict[str, tuple[str, str]] = {
    "audio/webm": ("webm", "audio/webm"),
    "audio/webm;codecs=opus": ("webm", "audio/webm"),
    "audio/ogg": ("ogg", "audio/ogg"),
    "audio/ogg;codecs=opus": ("ogg", "audio/ogg"),
    "audio/mp4": ("mp4", "audio/mp4"),
    "audio/x-m4a": ("m4a", "audio/mp4"),
    "audio/m4a": ("m4a", "audio/mp4"),
    "audio/mpeg": ("mp3", "audio/mpeg"),
    "audio/mp3": ("mp3", "audio/mpeg"),
    "audio/wav": ("wav", "audio/wav"),
    "audio/wave": ("wav", "audio/wav"),
    "audio/x-wav": ("wav", "audio/wav"),
    "audio/flac": ("flac", "audio/flac"),
}

# Extension → canonical content-type (for deriving from filename extension)
_EXT_TO_CT: dict[str, str] = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
}


def _derive_ext_and_ct(filename: str, content_type: str) -> tuple[str, str]:
    """
    Return (extension, canonical_content_type) from the provided filename + content_type.
    Uses content_type as primary signal; falls back to filename extension.
    Defaults to ("webm", "audio/webm") when nothing is recognisable.
    """
    # Normalise content_type (strip extra params except codec)
    ct_base = content_type.split(";")[0].strip().lower() if content_type else ""
    # Try content_type first
    for mime, (ext, canon) in _MIME_MAP.items():
        if ct_base == mime.split(";")[0].strip():
            return ext, canon

    # Fall back to filename extension
    if filename and "." in filename:
        file_ext = filename.rsplit(".", 1)[-1].lower()
        if file_ext in _EXT_TO_CT:
            return file_ext, _EXT_TO_CT[file_ext]

    logger.warning(
        "Could not derive audio format from content_type=%r filename=%r; defaulting to webm",
        content_type,
        filename,
    )
    return "webm", "audio/webm"


def _dedup_transcript(text: str) -> str:
    """
    Remove Whisper repetition artifacts such as "Aaj ki sale? Aaj ki sale?"
    Strategy: collapse runs of identical sentences (case/punctuation-normalised).
    """
    if not text:
        return text

    # Split on sentence-ending punctuation keeping the delimiter
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    if len(parts) <= 1:
        # Try word-level repetition: "foo foo foo" → "foo"
        words = text.split()
        deduped: list[str] = []
        i = 0
        while i < len(words):
            # Find longest run of the current word
            j = i + 1
            while j < len(words) and words[j].lower() == words[i].lower():
                j += 1
            deduped.append(words[i])
            i = j
        result = " ".join(deduped)
        return result

    seen: list[str] = []
    for part in parts:
        norm = re.sub(r"[^a-z0-9\u0900-\u097f\u0b80-\u0bff\u0c00-\u0c7f\u0c80-\u0cff]", "", part.lower())
        if not seen or norm != re.sub(r"[^a-z0-9\u0900-\u097f\u0b80-\u0bff\u0c00-\u0c7f\u0c80-\u0cff]", "", seen[-1].lower()):
            seen.append(part)

    return " ".join(seen)


def _convert_to_wav(audio_bytes: bytes) -> bytes:
    """
    Convert arbitrary audio bytes to 16kHz mono WAV using pydub + imageio-ffmpeg.
    imageio-ffmpeg ships a static ffmpeg binary that works on Render free tier.
    """
    try:
        import imageio_ffmpeg  # noqa: F401 — registers ffmpeg path for pydub
        from pydub import AudioSegment

        # Let pydub auto-detect format
        seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
        seg = seg.set_channels(1).set_frame_rate(16_000).set_sample_width(2)
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        buf.seek(0)
        wav_bytes = buf.read()
        logger.info("Converted audio to 16kHz mono WAV (%d bytes)", len(wav_bytes))
        return wav_bytes
    except Exception as exc:
        raise RuntimeError(f"WAV conversion failed: {exc}") from exc


@dataclass
class STTResult:
    transcript: str
    language: str
    duration_seconds: Optional[float] = None


def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "audio/webm",
    language_hint: Optional[str] = None,
) -> STTResult:
    """
    Transcribes audio bytes using Groq Whisper large-v3.

    Args:
        audio_bytes:    Raw audio bytes from the client upload.
        filename:       Original filename (used to derive extension when content_type is vague).
        content_type:   MIME type reported by the client (may be wrong on mobile).
        language_hint:  Optional ISO-639-1 code (e.g. "te", "hi", "ta").
                        When provided, passed to Whisper to force language detection.
                        When None, Whisper auto-detects.

    Returns:
        STTResult(transcript, language, duration_seconds)

    Raises:
        ValueError:  If the audio is too small to bother sending.
        RuntimeError: If Groq fails even after WAV fallback conversion.
    """
    if len(audio_bytes) < MIN_AUDIO_BYTES:
        raise ValueError(
            f"Audio too small ({len(audio_bytes)} bytes < {MIN_AUDIO_BYTES} B). "
            "Please speak for at least 1 second."
        )

    settings = get_settings()
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is not configured in environment/.env.")

    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)

    ext, canon_ct = _derive_ext_and_ct(filename, content_type)
    send_filename = f"audio.{ext}"
    send_bytes = audio_bytes
    send_ct = canon_ct

    logger.info(
        "Sending %d bytes to Groq Whisper [ext=%s, ct=%s, lang_hint=%s]",
        len(send_bytes),
        ext,
        send_ct,
        language_hint or "auto",
    )

    def _call_groq(fbytes: bytes, fname: str, fct: str) -> STTResult:
        extra_kwargs: dict = {}
        if language_hint:
            extra_kwargs["language"] = language_hint

        response = client.audio.transcriptions.create(
            file=(fname, fbytes, fct),
            model="whisper-large-v3",
            prompt=DOMAIN_HINTS,
            response_format="verbose_json",
            temperature=0.0,
            **extra_kwargs,
        )
        transcript = _dedup_transcript((getattr(response, "text", "") or "").strip())
        language = (getattr(response, "language", "") or "english").lower()
        duration = getattr(response, "duration", None)
        logger.info(
            "Groq Whisper OK: %r [language=%s, duration=%ss]",
            transcript[:80],
            language,
            duration,
        )
        return STTResult(transcript=transcript, language=language, duration_seconds=duration)

    # ── First attempt ──────────────────────────────────────────────────────────
    try:
        return _call_groq(send_bytes, send_filename, send_ct)
    except Exception as first_exc:
        err_str = str(first_exc).lower()
        is_media_err = (
            "invalid_media_file" in err_str
            or "could not process file" in err_str
            or "400" in err_str
        )
        if not is_media_err:
            logger.exception("Groq Whisper non-media error: %s", first_exc)
            raise

        logger.warning(
            "Groq returned invalid_media_file for ext=%s — converting to WAV and retrying. Error: %s",
            ext,
            first_exc,
        )

    # ── WAV fallback ───────────────────────────────────────────────────────────
    try:
        wav_bytes = _convert_to_wav(audio_bytes)
        return _call_groq(wav_bytes, "audio.wav", "audio/wav")
    except Exception as second_exc:
        logger.exception("Groq Whisper failed even after WAV conversion: %s", second_exc)
        raise RuntimeError(
            "Speech recognition failed. Please try speaking more clearly or check your microphone."
        ) from second_exc
