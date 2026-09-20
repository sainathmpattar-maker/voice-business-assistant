"""
tests/test_chat_router.py — Tests for POST /api/chat and session clearing endpoints.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from main import app
from app.assistant import AssistantResponse, clear_session, _memory

client = TestClient(app)


def test_chat_endpoint_missing_api_key(monkeypatch):
    """When GEMINI_API_KEY is not set or empty, should return 503 or clear error."""
    with patch("routers.chat.get_settings") as mock_settings:
        mock_settings.return_value.gemini_api_key = ""
        mock_settings.return_value.merchant_id = 1
        
        response = client.post(
            "/api/chat",
            json={"text": "Aaj ki sale kitni rahi?", "language": "hindi", "session_id": "test-s1"}
        )
        assert response.status_code == 503
        data = response.json()
        assert "GEMINI_API_KEY" in data["detail"]


def test_chat_endpoint_success():
    """Mock assistant execution to verify FastAPI serialization and contract."""
    mock_resp = AssistantResponse(
        display_text="आज कुल ₹18,400 की बिक्री हुई।",
        speech_text="Aaj kul atharah hazaar char sau rupaye ki bikri hui.",
        language_code="hi-IN",
        data_used=["sales_summary"],
        actions=[{"label": "Top items dekhein", "action_type": "view_top_items"}],
        latency_ms=120,
    )
    
    with patch("routers.chat.get_settings") as mock_settings, \
         patch("routers.chat.run_assistant", return_value=mock_resp):
        mock_settings.return_value.gemini_api_key = "dummy_key"
        mock_settings.return_value.merchant_id = 1
        
        response = client.post(
            "/api/chat",
            json={
                "text": "Aaj ki sale kitni rahi?",
                "language": "hindi",
                "session_id": "test-sess-123"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["display_text"] == "आज कुल ₹18,400 की बिक्री हुई।"
        assert data["speech_text"] == "Aaj kul atharah hazaar char sau rupaye ki bikri hui."
        assert data["language_code"] == "hi-IN"
        assert data["data_used"] == ["sales_summary"]
        assert len(data["actions"]) == 1
        assert data["actions"][0]["label"] == "Top items dekhein"
        assert data["session_id"] == "test-sess-123"


def test_clear_session_endpoint():
    """Test DELETE /api/chat/session to clear conversation memory."""
    _memory["sess-to-clear"] = [{"role": "user", "parts": ["hi"]}]
    assert "sess-to-clear" in _memory
    
    response = client.request("DELETE", "/api/chat/session", json={"session_id": "sess-to-clear"})
    assert response.status_code == 200
    assert response.json()["cleared"] is True
    assert "sess-to-clear" not in _memory
