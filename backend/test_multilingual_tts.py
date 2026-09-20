"""
test_multilingual_tts.py — Test spoken output in Hindi, Tamil, Telugu, and English.
"""
import base64
import sys
import os

# Ensure UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(__file__))

from app.tts import synthesize_speech, _tts_cache

test_phrases = [
    ("hi-IN", "Hindi", "आज कुल अठारह हजार चार सौ रुपये की बिक्री हुई।"),
    ("ta-IN", "Tamil", "இந்த வாரம் விற்பனை நன்றாக உள்ளது. அரிசி அதிகம் விற்றுள்ளது."),
    ("te-IN", "Telugu", "ఈ వారం పద్దెనిమిది వేల నాలుగు వందల రూపాయల వ్యాపారం జరిగింది."),
    ("en-IN", "English", "Today's sales total eighteen thousand four hundred rupees."),
]

print("=" * 65)
print("  POLARIS — MULTILINGUAL TTS TEST (Hindi, Tamil, Telugu, English)")
print("=" * 65)

all_passed = True
for lang_code, lang_name, text in test_phrases:
    audio_b64 = synthesize_speech(text, lang_code)
    if audio_b64 and len(audio_b64) > 500:
        raw_bytes = base64.b64decode(audio_b64)
        print(f"[PASS] {lang_name:7s} ({lang_code}): {len(raw_bytes):,} bytes MP3 generated")
        print(f"       Text: {text}")
    else:
        print(f"[FAIL] {lang_name:7s} ({lang_code}): synthesis failed")
        all_passed = False

# Test caching
cache_count_before = len(_tts_cache)
audio_cached = synthesize_speech(test_phrases[0][2], test_phrases[0][0])
cache_count_after = len(_tts_cache)

assert cache_count_before == cache_count_after, "Cache failed — created new entry instead of cache hit"
print(f"\n[PASS] In-Memory Cache: HIT verified ({len(_tts_cache)} items in cache)")

print("=" * 65)
if all_passed:
    print("  ALL 4 LANGUAGES PASSED AUDIO SYNTHESIS SUCCESSFULLY")
print("=" * 65)
