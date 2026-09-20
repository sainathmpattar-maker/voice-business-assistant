"""
tests/test_voice_and_insights.py — Tests for Voice pipeline, Insights, and New Tools.
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from main import app
from app.assistant import AssistantResponse
from app.stt import STTResult
from app.tts import synthesize_speech, VOICE_MAP

client = TestClient(app)


def test_tts_voice_map_and_cache():
    """Verify voice map exists for major Indian languages and caching works."""
    assert "hi-IN" in VOICE_MAP
    assert "ta-IN" in VOICE_MAP
    assert "te-IN" in VOICE_MAP
    assert "kn-IN" in VOICE_MAP
    assert "en-IN" in VOICE_MAP

    # Empty text returns empty string
    assert synthesize_speech("") == ""
    assert synthesize_speech("   ") == ""


def test_insights_endpoint():
    """Verify GET /api/insights returns 3 proactive spoken insights."""
    response = client.get("/api/insights?merchant_id=1&language=hi-IN")
    assert response.status_code == 200
    data = response.json()
    assert data["merchant_id"] == 1
    assert "insights" in data
    assert len(data["insights"]) == 3
    
    categories = [i["category"] for i in data["insights"]]
    assert "stock" in categories
    assert "sales" in categories
    assert "dues" in categories
    for item in data["insights"]:
        assert bool(item["display_text"])
        assert bool(item["speech_text"])


def test_merchant_today_sales_endpoint():
    """Verify GET /api/merchant/today returns header card metrics."""
    response = client.get("/api/merchant/today?merchant_id=1")
    assert response.status_code == 200
    data = response.json()
    assert data["merchant_id"] == 1
    assert data["shop_name"] == "Ramesh Kirana Store"
    assert "today_revenue" in data
    assert "today_orders" in data
    assert "period_7d_revenue" in data


def test_voice_ask_pipeline():
    """Verify POST /api/voice/ask complete flow with mock STT, LLM, TTS."""
    fake_stt = STTResult(transcript="Aaj ki sale kitni rahi?", language="hindi", duration_seconds=1.5)
    fake_llm = AssistantResponse(
        display_text="आज की कुल बिक्री ₹2,600 है।",
        speech_text="Aaj ki kul bikri do hazaar chhah sau rupaye hai.",
        language_code="hi-IN",
        data_used=["sales_summary"],
        actions=[{"label": "Top items dekhein", "action_type": "view_top_items"}],
        latency_ms=300,
    )

    with patch("routers.voice.transcribe_audio", return_value=fake_stt), \
         patch("routers.voice.run_assistant", return_value=fake_llm), \
         patch("routers.voice.synthesize_speech", return_value="dummy_mp3_base64"):

        # Build a >2 KB audio file so it passes the MIN_AUDIO_BYTES size guard
        import wave
        _buf = io.BytesIO()
        with wave.open(_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000)  # 1 second of silence = 32 KB
        _buf.seek(0)
        fake_audio_file = _buf
        response = client.post(
            "/api/voice/ask",
            files={"audio": ("test.wav", fake_audio_file, "audio/wav")},
            data={"merchant_id": 1, "session_id": "voice-test-sess", "is_premium": "false"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["transcript"] == "Aaj ki sale kitni rahi?"
        assert data["language"] == "hindi"
        assert data["display_text"] == "आज की कुल बिक्री ₹2,600 है।"
        assert data["audio_base64"] == "dummy_mp3_base64"
        assert "timings_ms" in data
        assert data["timings_ms"]["total"] >= 0
        assert data["session_id"] == "voice-test-sess"


def test_peer_benchmarking_repo(repo):
    """Test repository peer_benchmarking method."""
    bm_sales = repo.peer_benchmarking(1, "daily_sales")
    assert bm_sales.metric == "daily_sales"
    assert bm_sales.merchant_value > 0
    assert bm_sales.peer_avg_value > 0
    assert bool(bm_sales.insight)

    bm_basket = repo.peer_benchmarking(1, "avg_order_value")
    assert bm_basket.metric == "avg_order_value"
    assert bm_basket.merchant_value > 0
    assert bm_basket.peer_avg_value > 0


def test_financial_guidance_repo(repo):
    """Test repository financial_guidance method."""
    fin = repo.financial_guidance(1, "loan")
    assert fin.is_eligible is True
    assert fin.estimated_credit_limit > 0
    assert "indicative" in fin.disclaimer.lower() or "approval" in fin.disclaimer.lower()
