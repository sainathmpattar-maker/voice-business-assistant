"""
routers/chat.py — POST /api/chat   (text-input testing endpoint)

This endpoint lets you test the full Gemini assistant pipeline with plain text
(no audio required), perfect for development and CI.

Request body:
  {
    "text":      "Aaj kitni sale hui?",
    "language":  "hindi",          // optional — auto-detected from text if omitted
    "merchant_id": 1,              // optional — defaults to settings.MERCHANT_ID
    "session_id": "test-session-1" // optional — for conversation continuity
  }

Response:
  {
    "display_text":  "...",
    "speech_text":   "...",
    "language_code": "hi-IN",
    "data_used":     ["get_sales_summary"],
    "actions":       [{"label": "...", "action_type": "..."}],
    "latency_ms":    1230
  }
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.assistant import run_assistant, clear_session
from config import get_settings
from database import get_db
from repository.postgres import PostgresTransactionRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["assistant"])
settings = get_settings()


# ── Request / Response schemas ────────────────────────────────────────────────

class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="Merchant's text query")
    language: str = Field(
        default="english",
        description=(
            "Detected language name or code. "
            "E.g. 'hindi', 'tamil', 'telugu', 'english', 'hi', 'ta', 'te'. "
            "When omitted, defaults to 'english'."
        ),
    )
    merchant_id: int = Field(
        default=0,
        description="Merchant ID. 0 means use the default from settings.",
    )
    session_id: str = Field(
        default="",
        description=(
            "Conversation session ID for memory continuity. "
            "Pass the same value across follow-up questions. "
            "Leave empty to generate a new session."
        ),
    )
    is_premium: bool = Field(
        default=False,
        description="Whether the merchant has Polaris Premium enabled (demo toggle).",
    )


class ActionSuggestion(BaseModel):
    label: str
    action_type: str


class ChatResponse(BaseModel):
    display_text: str
    speech_text: str
    language_code: str
    data_used: list[str]
    actions: list[ActionSuggestion]
    latency_ms: int
    session_id: str


class ClearSessionRequest(BaseModel):
    session_id: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Text-based assistant query (no audio needed)",
    description=(
        "Send a merchant question as plain text and get a structured assistant response. "
        "Useful for development, testing, and the web frontend chat widget."
    ),
)
def chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
) -> ChatResponse:
    current_settings = get_settings()
    merchant_id = req.merchant_id or current_settings.merchant_id
    session_id  = req.session_id  or str(uuid.uuid4())

    if not current_settings.gemini_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GEMINI_API_KEY is not configured. Please add it to .env.",
        )

    repo = PostgresTransactionRepository(db)

    try:
        result = run_assistant(
            transcript        = req.text,
            detected_language = req.language,
            merchant_id       = merchant_id,
            repo              = repo,
            session_id        = session_id,
            is_premium        = req.is_premium,
        )
    except Exception as exc:
        logger.exception("Assistant pipeline failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Assistant error: {exc}",
        ) from exc

    return ChatResponse(
        display_text  = result.display_text,
        speech_text   = result.speech_text,
        language_code = result.language_code,
        data_used     = result.data_used,
        actions       = [ActionSuggestion(**a) for a in result.actions],
        latency_ms    = result.latency_ms,
        session_id    = session_id,
    )


@router.delete(
    "/chat/session",
    summary="Clear conversation memory for a session",
)
def clear_chat_session(req: ClearSessionRequest) -> dict:
    clear_session(req.session_id)
    return {"cleared": True, "session_id": req.session_id}
