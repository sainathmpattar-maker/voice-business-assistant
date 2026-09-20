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
from app.stt import transcribe_audio
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
    audio: UploadFile = File(..., description="Audio recording blob (webm, wav, m4a, mp3)"),
    merchant_id: Optional[int] = Form(None),
    session_id: Optional[str] = Form(None),
    is_premium: Optional[bool] = Form(False),
    db: Session = Depends(get_db),
) -> VoiceAskResponse:
    settings = get_settings()
    actual_merchant_id = merchant_id or settings.merchant_id
    actual_session_id = session_id or str(uuid.uuid4())

    total_start_time = time.perf_counter()

    # Read audio bytes
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty audio file provided.",
        )

    # 1. STT: Groq Whisper
    stt_start = time.perf_counter()
    try:
        stt_result = transcribe_audio(
            audio_bytes=audio_bytes,
            filename=audio.filename or "audio.webm",
            content_type=audio.content_type or "audio/webm",
        )
    except Exception as exc:
        logger.exception(f"STT failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Speech recognition error: {exc}",
        ) from exc
    stt_duration_ms = int((time.perf_counter() - stt_start) * 1000)

    if not stt_result.transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No speech detected in the audio. Please try speaking again.",
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
        logger.exception(f"Assistant error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Assistant processing error: {exc}",
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
        logger.warning(f"TTS synthesis error (gracefully omitted): {exc}")
    tts_duration_ms = int((time.perf_counter() - tts_start) * 1000)

    total_duration_ms = int((time.perf_counter() - total_start_time) * 1000)

    logger.info(
        f"Voice ask complete in {total_duration_ms}ms "
        f"[STT: {stt_duration_ms}ms | LLM: {llm_duration_ms}ms | TTS: {tts_duration_ms}ms]"
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
