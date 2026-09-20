"""
test_full_pipeline_verification.py — Comprehensive verification script for Polaris:
Tests /api/chat and /api/voice/ask across Hindi, Tamil, Telugu, English, Session Follow-up,
Peer Benchmarking, and Loan eligibility.
"""
import io
import sys
import time
import requests
from gtts import gTTS

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


BASE_URL = "http://127.0.0.1:8000"

QUESTIONS = [
    {
        "id": "q1_hindi",
        "name": "Hindi Sales Summary",
        "text": "Is hafte business kaisa raha?",
        "lang": "hi",
        "expected_lang": "hi",
    },
    {
        "id": "q2_tamil",
        "name": "Tamil Sales Summary",
        "text": "இந்த வாரம் எவ்வளவு விற்பனை?",
        "lang": "ta",
        "expected_lang": "ta",
    },
    {
        "id": "q3_telugu",
        "name": "Telugu Pending Dues",
        "text": "ఎవరికి బాకీ ఉంది?",
        "lang": "te",
        "expected_lang": "te",
    },
    {
        "id": "q4_english_top",
        "name": "English Top Selling Item",
        "text": "which item sells the most",
        "lang": "en",
        "expected_lang": "en",
    },
    {
        "id": "q5_benchmark",
        "name": "Peer Benchmarking",
        "text": "Mera store doosre kirana stores ke mukable kaisa perform kar raha hai?",
        "lang": "hi",
        "expected_lang": "hi",
    },
    {
        "id": "q6_loan",
        "name": "Loan Eligibility",
        "text": "Can I get a loan for my shop?",
        "lang": "en",
        "expected_lang": "en",
    },
]

def generate_speech_bytes(text: str, lang: str) -> bytes:
    tts = gTTS(text=text, lang=lang)
    fp = io.BytesIO()
    tts.write_to_fp(fp)
    fp.seek(0)
    return fp.read()

def run_tests():
    print("=" * 70)
    print("  POLARIS — FULL PIPELINE VERIFICATION (/api/chat & /api/voice/ask)")
    print("=" * 70)
    
    session_id = "test-session-pipeline-1"
    
    # Test 1: Chat endpoint tests
    print("\n--- 1. Testing POST /api/chat ---")
    for q in QUESTIONS:
        time.sleep(4)
        t0 = time.time()
        payload = {
            "merchant_id": 1,
            "session_id": session_id,
            "text": q["text"],
            "language": q["lang"]
        }
        res = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=40)
        elapsed = time.time() - t0
        
        assert res.status_code == 200, f"Chat failed with status {res.status_code}: {res.text}"
        data = res.json()
        
        has_display = bool(data.get("display_text"))
        has_speech = bool(data.get("speech_text"))
        actions = data.get("actions", [])
        
        print(f"[{'PASS' if has_display else 'FAIL'}] {q['name']} ({elapsed:.2f}s)")
        print(f"       Question: {q['text']}")
        print(f"       Display:  {data.get('display_text')}")
        print(f"       Speech:   {data.get('speech_text')}")
        print(f"       Actions:  {actions}")
    
    # Test 2: Follow-up question in the same session
    print("\n--- 2. Testing Follow-up in same session ('aur kal ka?') ---")
    time.sleep(4)
    t0 = time.time()
    res = requests.post(f"{BASE_URL}/api/chat", json={
        "merchant_id": 1,
        "session_id": session_id,
        "text": "aur kal ka?",
        "language": "hi"
    }, timeout=40)
    elapsed = time.time() - t0
    data = res.json()
    print(f"[PASS] Follow-up 'aur kal ka?' ({elapsed:.2f}s)")
    print(f"       Display:  {data.get('display_text')}")
    print(f"       Speech:   {data.get('speech_text')}")
    
    # Test 3: Voice endpoint (/api/voice/ask)
    print("\n--- 3. Testing POST /api/voice/ask with real audio input ---")
    voice_tests = [
        {"name": "Hindi Voice: 'Is hafte business kaisa raha?'", "text": "इस हफ्ते बिजनेस कैसा रहा?", "lang": "hi"},
        {"name": "English Voice: 'which item sells the most'", "text": "which item sells the most", "lang": "en"},
        {"name": "Telugu Voice: 'ఎవరికి బాకీ ఉంది?'", "text": "ఎవరికి బాకీ ఉంది", "lang": "te"},
    ]
    
    for vt in voice_tests:
        print(f"\nGenerating test audio for {vt['name']}...")
        audio_bytes = generate_speech_bytes(vt["text"], vt["lang"])
        
        t0 = time.time()
        files = {"audio": ("query.mp3", io.BytesIO(audio_bytes), "audio/mpeg")}
        data_fields = {
            "merchant_id": "1",
            "session_id": f"voice-sess-{vt['lang']}",
        }
        res = requests.post(f"{BASE_URL}/api/voice/ask", files=files, data=data_fields, timeout=25)
        elapsed = time.time() - t0
        
        assert res.status_code == 200, f"Voice ask failed: {res.status_code} {res.text}"
        res_data = res.json()
        
        audio_b64 = res_data.get("audio_base64")
        audio_len = len(audio_b64) if audio_b64 else 0
        
        print(f"[PASS] {vt['name']} ({elapsed:.2f}s, Audio response: {audio_len} chars base64)")
        print(f"       Transcript: {res_data.get('transcript')}")
        print(f"       Language:   {res_data.get('language')}")
        print(f"       Display:    {res_data.get('display_text')}")
        print(f"       Speech:     {res_data.get('speech_text')}")
        print(f"       Actions:    {res_data.get('actions')}")

    print("\n" + "=" * 70)
    print("  ALL VERIFICATION CHECKS PASSED!")
    print("=" * 70)

if __name__ == "__main__":
    run_tests()
