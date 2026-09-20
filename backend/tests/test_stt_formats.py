"""
tests/test_stt_formats.py — Tests for STT audio format handling.

Covers:
  - webm, mp4/m4a, ogg, wav uploads to /api/voice/ask (Groq mocked)
  - empty file → 400
  - tiny file (<2 KB) → 400
  - _dedup_transcript utility
  - _derive_ext_and_ct utility
"""
from __future__ import annotations

import io
import struct
import wave
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.stt import STTResult, _dedup_transcript, _derive_ext_and_ct, MIN_AUDIO_BYTES
from main import app

client = TestClient(app)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_wav_bytes(duration_ms: int = 1000, sample_rate: int = 16000) -> bytes:
    """Generate a minimal valid WAV file (silence) of the given duration."""
    num_samples = int(sample_rate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)  # silence
    return buf.getvalue()


def _fake_stt(**kwargs) -> STTResult:
    """Return a deterministic STTResult regardless of what bytes were sent."""
    return STTResult(transcript="Aaj ki sale kitni rahi?", language="hindi", duration_seconds=1.0)


FAKE_ASSISTANT = MagicMock()
FAKE_ASSISTANT.display_text = "₹2,600"
FAKE_ASSISTANT.speech_text = "Do hazaar chhah sau"
FAKE_ASSISTANT.language_code = "hi-IN"
FAKE_ASSISTANT.data_used = ["get_sales_summary"]
FAKE_ASSISTANT.actions = []
FAKE_ASSISTANT.latency_ms = 100


# ── Dedup utility tests ────────────────────────────────────────────────────────

class TestDedupTranscript:
    def test_no_repetition(self):
        assert _dedup_transcript("Aaj ki sale kitni rahi?") == "Aaj ki sale kitni rahi?"

    def test_sentence_repetition(self):
        result = _dedup_transcript("Aaj ki sale kitni rahi? Aaj ki sale kitni rahi?")
        # Should collapse to a single sentence
        assert result.count("Aaj ki sale kitni rahi?") == 1

    def test_word_repetition(self):
        result = _dedup_transcript("hello hello hello")
        assert result == "hello"

    def test_empty(self):
        assert _dedup_transcript("") == ""

    def test_single_word(self):
        assert _dedup_transcript("namaste") == "namaste"

    def test_hindi_repetition(self):
        text = "कितना बिक्री हुआ? कितना बिक्री हुआ?"
        result = _dedup_transcript(text)
        assert result.count("कितना बिक्री हुआ?") == 1


# ── MIME derivation tests ──────────────────────────────────────────────────────

class TestDeriveExtAndCt:
    def test_webm(self):
        ext, ct = _derive_ext_and_ct("audio.webm", "audio/webm")
        assert ext == "webm" and "webm" in ct

    def test_webm_opus(self):
        ext, ct = _derive_ext_and_ct("recording.webm", "audio/webm;codecs=opus")
        assert ext == "webm"

    def test_mp4_from_content_type(self):
        ext, ct = _derive_ext_and_ct("recording.webm", "audio/mp4")
        assert ext == "mp4"

    def test_ogg_from_content_type(self):
        ext, ct = _derive_ext_and_ct("audio.ogg", "audio/ogg;codecs=opus")
        assert ext == "ogg"

    def test_wav(self):
        ext, ct = _derive_ext_and_ct("audio.wav", "audio/wav")
        assert ext == "wav"

    def test_fallback_to_filename_ext(self):
        ext, ct = _derive_ext_and_ct("recording.mp4", "")
        assert ext == "mp4"

    def test_unknown_defaults_webm(self):
        ext, ct = _derive_ext_and_ct("blob", "application/octet-stream")
        assert ext == "webm"


# ── /api/voice/ask format tests ───────────────────────────────────────────────

def _post_audio(audio_bytes: bytes, filename: str, content_type: str):
    """Helper: POST audio to /api/voice/ask with mocked STT, LLM, TTS."""
    with (
        patch("routers.voice.transcribe_audio", side_effect=_fake_stt),
        patch("routers.voice.run_assistant", return_value=FAKE_ASSISTANT),
        patch("routers.voice.synthesize_speech", return_value="base64audio"),
    ):
        return client.post(
            "/api/voice/ask",
            files={"audio": (filename, io.BytesIO(audio_bytes), content_type)},
            data={"merchant_id": 1, "session_id": "fmt-test", "is_premium": "false"},
        )


class TestVoiceAskFormats:
    """Verify that each audio format is accepted (with mocked Groq)."""

    def _large_enough_wav(self) -> bytes:
        """1s WAV always > 2 KB."""
        return _make_wav_bytes(1000)

    def test_wav_format(self):
        audio = self._large_enough_wav()
        assert len(audio) >= MIN_AUDIO_BYTES
        resp = _post_audio(audio, "audio.wav", "audio/wav")
        assert resp.status_code == 200
        assert resp.json()["transcript"] == "Aaj ki sale kitni rahi?"

    def test_webm_format(self):
        # Use WAV bytes but label as webm — Groq is mocked so format doesn't matter
        audio = self._large_enough_wav()
        resp = _post_audio(audio, "recording.webm", "audio/webm")
        assert resp.status_code == 200

    def test_mp4_format(self):
        audio = self._large_enough_wav()
        resp = _post_audio(audio, "recording.mp4", "audio/mp4")
        assert resp.status_code == 200

    def test_ogg_format(self):
        audio = self._large_enough_wav()
        resp = _post_audio(audio, "recording.ogg", "audio/ogg")
        assert resp.status_code == 200

    def test_m4a_format(self):
        audio = self._large_enough_wav()
        resp = _post_audio(audio, "recording.m4a", "audio/x-m4a")
        assert resp.status_code == 200

    def test_mp4_content_type_webm_filename(self):
        """Simulate Android Chrome: sends audio/mp4 bytes but filename says .webm"""
        audio = self._large_enough_wav()
        resp = _post_audio(audio, "recording.webm", "audio/mp4")
        assert resp.status_code == 200


class TestVoiceAskSizeGuards:
    def test_empty_file_returns_400(self):
        resp = _post_audio(b"", "audio.wav", "audio/wav")
        assert resp.status_code == 400
        assert "short" in resp.json()["detail"].lower() or "empty" in resp.json()["detail"].lower()

    def test_tiny_file_returns_400(self):
        tiny = b"RIFF" + b"\x00" * 20  # 24 bytes — way below 2 KB
        resp = _post_audio(tiny, "audio.wav", "audio/wav")
        assert resp.status_code == 400

    def test_just_under_limit_returns_400(self):
        almost = b"\x00" * (MIN_AUDIO_BYTES - 1)
        resp = _post_audio(almost, "audio.wav", "audio/wav")
        assert resp.status_code == 400


class TestVoiceAskLanguageHint:
    def test_language_hint_passed_through(self):
        """Verify endpoint accepts language_hint param without error."""
        wav = _make_wav_bytes(1000)
        with (
            patch("routers.voice.transcribe_audio", side_effect=_fake_stt) as mock_stt,
            patch("routers.voice.run_assistant", return_value=FAKE_ASSISTANT),
            patch("routers.voice.synthesize_speech", return_value=""),
        ):
            resp = client.post(
                "/api/voice/ask",
                files={"audio": ("audio.wav", io.BytesIO(wav), "audio/wav")},
                data={"merchant_id": 1, "language_hint": "te"},
            )
            assert resp.status_code == 200
            # Confirm transcribe_audio was called with the hint
            call_kwargs = mock_stt.call_args.kwargs
            assert call_kwargs.get("language_hint") == "te"

    def test_auto_language_hint_normalised_to_none(self):
        wav = _make_wav_bytes(1000)
        with (
            patch("routers.voice.transcribe_audio", side_effect=_fake_stt) as mock_stt,
            patch("routers.voice.run_assistant", return_value=FAKE_ASSISTANT),
            patch("routers.voice.synthesize_speech", return_value=""),
        ):
            resp = client.post(
                "/api/voice/ask",
                files={"audio": ("audio.wav", io.BytesIO(wav), "audio/wav")},
                data={"merchant_id": 1, "language_hint": "auto"},
            )
            assert resp.status_code == 200
            call_kwargs = mock_stt.call_args.kwargs
            assert call_kwargs.get("language_hint") is None
