"""
app/stt.py — Groq Whisper STT with Indian shop domain vocabulary prompt hints.

Flow:
  audio bytes / file -> Groq Whisper (whisper-large-v3) -> (transcript, detected_language)
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from config import get_settings

logger = logging.getLogger(__name__)

# Indian kirana & commerce domain hints for Whisper
DOMAIN_HINTS = (
    "Paytm, kirana, chawal, atta, udhaar, dues, stock, dukan, namkeen, "
    "biscuit, tel, dal, cheeni, chai, rupee, rupaye, bikri, kamai, munafa, "
    "bikri kaisa raha, kiska udhaar baaki hai, stockout, Basmati, UPI, QR code."
)


@dataclass
class STTResult:
    transcript: str
    language: str
    duration_seconds: Optional[float] = None


def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    content_type: str = "audio/webm",
) -> STTResult:
    """
    Transcribes audio bytes using Groq Whisper large-v3.
    Returns STTResult(transcript, language).
    """
    settings = get_settings()
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is not configured in environment/.env.")

    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)

    # Groq accepts a tuple of (filename, file_content, content_type)
    file_tuple = (filename, audio_bytes, content_type)

    logger.info(f"Sending {len(audio_bytes)} bytes of audio to Groq Whisper...")
    response = client.audio.transcriptions.create(
        file=file_tuple,
        model="whisper-large-v3",
        prompt=DOMAIN_HINTS,
        response_format="verbose_json",
        temperature=0.0,
    )

    transcript = (getattr(response, "text", "") or "").strip()
    language = getattr(response, "language", "english") or "english"
    duration = getattr(response, "duration", None)

    logger.info(f"Groq Whisper transcription success: '{transcript}' [language: {language}]")
    return STTResult(transcript=transcript, language=language.lower(), duration_seconds=duration)
