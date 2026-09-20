"""
routers/voice.py — POST /api/voice/ask (full audio pipeline endpoint)

Flow:
  audio upload -> Groq Whisper STT (text + detected language)
               -> assistant (Gemini with tools & memory)
               -> Google TTS (speech_text -> base64 mp3)
               -> response with per-stage latency breakdown
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.assistant import run_assistant
from app.stt import transcribe_audio, MIN_AUDIO_BYTES
from app.tts import synthesize_speech
from config import get_settings
from database import get_db
from repository.postgres import PostgresTransactionRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


class ActionSuggestion(BaseModel):
    label: str
    action_type: str


class TimingsMs(BaseModel):
    stt: int
    llm: int
    tts: int
    total: int


class VoiceAskResponse(BaseModel):
    transcript: str
    language: str
    display_text: str
    speech_text: str
    audio_base64: str
    data_used: list[str]
    actions: list[ActionSuggestion]
    timings_ms: TimingsMs
    session_id: str


@router.post(
    "/ask",
    response_model=VoiceAskResponse,
    summary="End-to-end voice query pipeline: Audio -> STT -> LLM+Tools -> TTS -> Audio Response",
)
async def voice_ask(
    audio: UploadFile = File(..., description="Audio recording blob (webm, wav, m4a, mp3, ogg)"),
    merchant_id: Optional[int] = Form(None),
    session_id: Optional[str] = Form(None),
    is_premium: Optional[bool] = Form(False),
    language_hint: Optional[str] = Form(None, description="ISO-639-1 language code hint (e.g. 'te', 'hi', 'ta'). 'auto' or empty = auto-detect."),
    db: Session = Depends(get_db),
) -> VoiceAskResponse:
    settings = get_settings()
    actual_merchant_id = merchant_id or settings.merchant_id
    actual_session_id = session_id or str(uuid.uuid4())

    # Normalise language_hint: treat "auto" / "" as None
    lang_hint: Optional[str] = None
    if language_hint and language_hint.strip().lower() not in ("", "auto"):
        lang_hint = language_hint.strip().lower()

    total_start_time = time.perf_counter()

    # Read audio bytes
    audio_bytes = await audio.read()
    if not audio_bytes or len(audio_bytes) < MIN_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Audio is too short or empty ({len(audio_bytes)} bytes). "
                "Please hold the mic and speak for at least 1 second."
            ),
        )

    # 1. STT: Groq Whisper
    stt_start = time.perf_counter()
    try:
        stt_result = transcribe_audio(
            audio_bytes=audio_bytes,
            filename=audio.filename or "audio.webm",
            content_type=audio.content_type or "audio/webm",
            language_hint=lang_hint,
        )
    except ValueError as exc:
        # Size or config error — clean 400
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        # Groq failed even after WAV fallback
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("STT unexpected error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Speech recognition is temporarily unavailable. Please try again.",
        ) from exc
    stt_duration_ms = int((time.perf_counter() - stt_start) * 1000)

    if not stt_result.transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No speech detected. Please speak clearly and try again.",
        )

    # 2. LLM: Gemini Assistant with function calling
    llm_start = time.perf_counter()
    repo = PostgresTransactionRepository(db)
    try:
        assistant_result = run_assistant(
            transcript=stt_result.transcript,
            detected_language=stt_result.language,
            merchant_id=actual_merchant_id,
            repo=repo,
            session_id=actual_session_id,
            is_premium=is_premium or False,
        )
    except Exception as exc:
        logger.exception("Assistant error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not generate a response. Please try again.",
        ) from exc
    llm_duration_ms = int((time.perf_counter() - llm_start) * 1000)

    # 3. TTS: Google Cloud Text-to-Speech
    tts_start = time.perf_counter()
    audio_base64 = ""
    try:
        audio_base64 = synthesize_speech(
            text=assistant_result.speech_text,
            language_code=assistant_result.language_code,
        )
    except Exception as exc:
        logger.warning("TTS synthesis error (gracefully omitted): %s", exc)
    tts_duration_ms = int((time.perf_counter() - tts_start) * 1000)

    total_duration_ms = int((time.perf_counter() - total_start_time) * 1000)

    logger.info(
        "Voice ask complete in %dms [STT: %dms | LLM: %dms | TTS: %dms]",
        total_duration_ms,
        stt_duration_ms,
        llm_duration_ms,
        tts_duration_ms,
    )

    return VoiceAskResponse(
        transcript=stt_result.transcript,
        language=stt_result.language,
        display_text=assistant_result.display_text,
        speech_text=assistant_result.speech_text,
        audio_base64=audio_base64,
        data_used=assistant_result.data_used,
        actions=[ActionSuggestion(**a) for a in assistant_result.actions],
        timings_ms=TimingsMs(
            stt=stt_duration_ms,
            llm=llm_duration_ms,
            tts=tts_duration_ms,
            total=total_duration_ms,
        ),
        session_id=actual_session_id,
    )
