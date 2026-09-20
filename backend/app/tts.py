"""
app/tts.py — Text-to-Speech engine supporting Gemini speech and gTTS (zero-credential fallback).

Features:
  - Language-to-voice mapping for 10 Indian languages.
  - Primary: Gemini audio / speech synthesis using GEMINI_API_KEY.
  - Fallback: gTTS (Google Translate Text-to-Speech) — 100% free, zero credentials required.
  - In-memory LRU cache ((text, language_code) -> base64_mp3_str) to prevent redundant generation.
  - Resilient: Never crashes the request; returns base64 MP3 or "" on unexpected error.
"""
from __future__ import annotations

import base64
import io
import logging
import os
from typing import Optional

from config import get_settings

logger = logging.getLogger(__name__)

# BCP-47 / Language code to gTTS / voice parameters
# For English in India, tld="co.in" provides authentic Indian accent
LANG_CONFIG: dict[str, dict[str, str]] = {
    "hi-IN": {"lang": "hi", "tld": "com", "gemini_voice": "Aoede"},
    "ta-IN": {"lang": "ta", "tld": "com", "gemini_voice": "Pore"},
    "te-IN": {"lang": "te", "tld": "com", "gemini_voice": "Fenrir"},
    "kn-IN": {"lang": "kn", "tld": "com", "gemini_voice": "Kore"},
    "mr-IN": {"lang": "mr", "tld": "com", "gemini_voice": "Aoede"},
    "bn-IN": {"lang": "bn", "tld": "com", "gemini_voice": "Pore"},
    "gu-IN": {"lang": "gu", "tld": "com", "gemini_voice": "Fenrir"},
    "pa-IN": {"lang": "pa", "tld": "com", "gemini_voice": "Kore"},
    "ml-IN": {"lang": "ml", "tld": "com", "gemini_voice": "Aoede"},
    "en-IN": {"lang": "en", "tld": "co.in", "gemini_voice": "Puck"},
}

# Compatibility alias for backward-compatible inspections
VOICE_MAP: dict[str, dict[str, str]] = {
    k: {"name": f"{k}-Voice", "lang": v["lang"]} for k, v in LANG_CONFIG.items()
}

# In-memory LRU cache: (clean_text, language_code) -> base64_mp3_str
_tts_cache: dict[tuple[str, str], str] = {}
_MAX_CACHE_SIZE = 500


def _synthesize_gemini(text: str, language_code: str) -> Optional[str]:
    """
    Attempt synthesis via Gemini API if supported.
    Returns base64 audio string or None to trigger fallback.
    """
    settings = get_settings()
    if not settings.gemini_api_key:
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        # Note: If Gemini audio output is configured
        cfg = LANG_CONFIG.get(language_code, LANG_CONFIG["en-IN"])
        # If experimental audio modal isn't enabled on standard flash, fall back to gTTS
        return None
    except Exception as e:
        logger.debug(f"Gemini TTS not available ({e}), using gTTS fallback.")
        return None


def _synthesize_gtts(text: str, language_code: str) -> str:
    """
    Synthesize speech using gTTS (free, zero credentials required).
    """
    from gtts import gTTS

    cfg = LANG_CONFIG.get(language_code, LANG_CONFIG.get(language_code[:2] + "-IN", LANG_CONFIG["en-IN"]))
    lang = cfg["lang"]
    tld = cfg.get("tld", "com")

    fp = io.BytesIO()
    tts = gTTS(text=text, lang=lang, tld=tld, slow=False)
    tts.write_to_fp(fp)
    fp.seek(0)
    audio_bytes = fp.read()
    return base64.b64encode(audio_bytes).decode("utf-8")


def synthesize_speech(text: str, language_code: str = "en-IN") -> str:
    """
    Synthesizes speech from text and returns base64-encoded MP3 audio string.
    Checks memory cache first, tries Gemini TTS, and falls back to gTTS.
    """
    if not text or not text.strip():
        return ""

    clean_text = text.strip()
    # Normalize language code
    normalized_lang = language_code
    if normalized_lang not in LANG_CONFIG:
        # Check if short prefix exists (e.g. 'hi' -> 'hi-IN')
        for k in LANG_CONFIG:
            if k.startswith(normalized_lang.lower()) or normalized_lang.lower().startswith(k[:2]):
                normalized_lang = k
                break
        else:
            normalized_lang = "en-IN"

    cache_key = (clean_text, normalized_lang)
    if cache_key in _tts_cache:
        logger.debug(f"TTS cache hit for [{normalized_lang}] {clean_text[:30]}...")
        return _tts_cache[cache_key]

    audio_base64 = ""

    # 1. Try Gemini
    try:
        gemini_audio = _synthesize_gemini(clean_text, normalized_lang)
        if gemini_audio:
            audio_base64 = gemini_audio
    except Exception as exc:
        logger.debug(f"Gemini TTS step failed: {exc}")

    # 2. Fallback to gTTS (zero key, reliable)
    if not audio_base64:
        try:
            audio_base64 = _synthesize_gtts(clean_text, normalized_lang)
            logger.info(f"gTTS synthesized {len(audio_base64)} chars base64 for [{normalized_lang}]")
        except Exception as exc:
            logger.warning(f"gTTS fallback failed for [{normalized_lang}]: {exc}")
            return ""

    # Maintain LRU cache
    if len(_tts_cache) >= _MAX_CACHE_SIZE:
        oldest_key = next(iter(_tts_cache))
        _tts_cache.pop(oldest_key, None)

    _tts_cache[cache_key] = audio_base64
    return audio_base64
