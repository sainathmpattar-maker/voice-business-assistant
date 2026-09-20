"""
test_assistant.py — Integration smoke test for the Gemini assistant pipeline.

Fires 10 questions across Hindi, Tamil, Telugu, and English.
For each question, prints:
  - The question text and language
  - Which tools Gemini called
  - The display_text response
  - Latency in ms

Usage (requires GEMINI_API_KEY and a running database with seeded data):
  cd backend
  python test_assistant.py

Tip: Set DATABASE_URL in .env to point to your local PostgreSQL instance,
     OR set DATABASE_URL=sqlite:///./local_test.db and run seed.py first.
"""
from __future__ import annotations

import os
import sys
import textwrap

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

# Load .env before importing project modules
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database as db_module
from database import Base, init_engine
import models  # noqa — register ORM models
from app.assistant import run_assistant
from repository.postgres import PostgresTransactionRepository
from config import get_settings

settings = get_settings()

# ── DB setup ──────────────────────────────────────────────────────────────────
def _setup_db():
    eng = init_engine()
    Base.metadata.create_all(bind=eng)
    # Try to seed if empty
    from models import Merchant
    Session = sessionmaker(bind=eng)
    s = Session()
    if not s.query(Merchant).first():
        s.close()
        print("[seed] No data found — running seed.py ...")
        import seed
        seed.seed()
    else:
        s.close()
    return sessionmaker(bind=eng)


# ── Test questions ─────────────────────────────────────────────────────────────
TEST_CASES = [
    # (question_text, language, session_id, description)
    (
        "Aaj ki sale kitni rahi?",
        "hindi",
        "session-hi",
        "Q1 [Hindi] Today's sales",
    ),
    (
        "Is hafte ka sale pichle hafte se zyada hua ya kam?",
        "hindi",
        "session-hi",
        "Q2 [Hindi] This week vs last week comparison",
    ),
    (
        "Aur kal ka?",           # follow-up — needs memory to understand "sales yesterday"
        "hindi",
        "session-hi",
        "Q3 [Hindi] Follow-up: 'what about yesterday?' (memory test)",
    ),
    (
        "Kitna udhaar baaki hai aur kaun sabse zyada dena hai?",
        "hindi",
        "session-dues",
        "Q4 [Hindi] Total dues + who owes most",
    ),
    (
        "Intha vaaram enge nalla vilkiradu?",   # Tamil: "which items sold well this week?"
        "tamil",
        "session-ta",
        "Q5 [Tamil] Top items this week",
    ),
    (
        "Stock teerangunthundaa?",    # Telugu: "Is stock running out?"
        "telugu",
        "session-te",
        "Q6 [Telugu] Low stock check",
    ),
    (
        "Rice out of stock hone se kitna nuksan hua?",
        "hindi",
        "session-stockout",
        "Q7 [Hindi] Stockout impact for rice",
    ),
    (
        "What were my top 3 products this month?",
        "english",
        "session-en",
        "Q8 [English] Top 3 products last 30 days",
    ),
    (
        "How does this month compare to last month in revenue?",
        "english",
        "session-en",
        "Q9 [English] Month-over-month comparison",
    ),
    (
        "Which customers have overdue payments?",
        "english",
        "session-en",
        "Q10 [English] Overdue customers",
    ),
]

# ── Formatting helpers ────────────────────────────────────────────────────────
SEP = "─" * 70

def print_result(desc: str, q: str, lang: str, result) -> None:
    print(f"\n{SEP}")
    print(f"  {desc}")
    print(f"  Q : {q}")
    print(f"  Lang: {lang}  →  BCP-47: {result.language_code}")
    print(f"  Tools called : {result.data_used or ['(none)']}")
    print(f"  Latency      : {result.latency_ms} ms")
    print()
    print("  DISPLAY:")
    for line in textwrap.wrap(result.display_text, width=64):
        print(f"    {line}")
    print()
    print("  SPEECH (TTS):")
    for line in textwrap.wrap(result.speech_text, width=64):
        print(f"    {line}")
    if result.actions:
        print()
        print("  ACTIONS:")
        for a in result.actions:
            print(f"    [{a.get('action_type','?')}]  {a.get('label','')}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY is not set in .env")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("  Polaris Assistant — Integration Test")
    print("  10 questions across Hindi, Tamil, Telugu, English")
    print("=" * 70)

    SessionFactory = _setup_db()

    passed = 0
    failed = 0

    for q_text, lang, session_id, desc in TEST_CASES:
        db = SessionFactory()
        try:
            repo   = PostgresTransactionRepository(db)
            result = run_assistant(
                transcript        = q_text,
                detected_language = lang,
                merchant_id       = settings.merchant_id,
                repo              = repo,
                session_id        = session_id,
            )
            print_result(desc, q_text, lang, result)

            # Basic sanity checks
            ok = (
                bool(result.display_text)
                and bool(result.speech_text)
                and bool(result.language_code)
                and len(result.data_used) > 0  # at least one tool must be called
            )
            if ok:
                passed += 1
                print(f"  STATUS: PASS")
            else:
                failed += 1
                print(f"  STATUS: FAIL — missing fields or no tools called")

        except Exception as exc:
            failed += 1
            print(f"\n{SEP}")
            print(f"  {desc}")
            print(f"  STATUS: ERROR — {exc}")
        finally:
            db.close()

    print(f"\n{'=' * 70}")
    print(f"  Results: {passed} passed, {failed} failed / {len(TEST_CASES)} total")
    print("=" * 70 + "\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
