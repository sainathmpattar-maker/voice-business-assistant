"""
database.py — SQLAlchemy engine, session factory, and Base declarative class.

Engine creation is lazy so tests can patch DATABASE_URL before any
import triggers a real connection attempt.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def _make_db_url(url: str) -> str:
    """
    Normalise the DATABASE_URL for pg8000 / psycopg drivers.
    """
    clean_url = url
    # Clean query parameters incompatible with pg8000
    for param in ["channel_binding", "sslmode"]:
        import re
        clean_url = re.sub(rf"[?&]{param}=[^&]+", "", clean_url)
    if "?" not in clean_url and "&" in clean_url:
        clean_url = clean_url.replace("&", "?", 1)

    if clean_url.startswith("postgresql://") or clean_url.startswith("postgres://"):
        return clean_url.replace("postgresql://", "postgresql+pg8000://", 1).replace(
            "postgres://", "postgresql+pg8000://", 1
        )
    if "postgresql+psycopg" in clean_url:
        return clean_url.replace("postgresql+psycopg2", "postgresql+pg8000").replace("postgresql+psycopg", "postgresql+pg8000")
    return clean_url


def _build_engine(url: str):
    """Create an engine with driver-appropriate kwargs."""
    normalised = _make_db_url(url)
    if normalised.startswith("sqlite"):
        return create_engine(
            normalised,
            connect_args={"check_same_thread": False},
        )
    
    connect_args = {}
    if "neon.tech" in normalised or "ssl" in url.lower():
        import ssl
        ssl_ctx = ssl.create_default_context()
        connect_args["ssl_context"] = ssl_ctx

    return create_engine(
        normalised,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


def _get_engine_and_session():
    """Lazy initialisation — reads DATABASE_URL at call time, not import time."""
    import os
    from config import get_settings
    url = os.environ.get("DATABASE_URL") or get_settings().database_url
    eng = _build_engine(url)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    return eng, Session


# Module-level singletons — replaced by tests via monkeypatching
engine = None          # type: ignore[assignment]
SessionLocal = None    # type: ignore[assignment]


def _ensure_init():
    global engine, SessionLocal
    if engine is None:
        engine, SessionLocal = _get_engine_and_session()


def get_db():
    """FastAPI dependency — yields a DB session and ensures cleanup."""
    _ensure_init()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_engine(url: str | None = None):
    """
    Explicitly initialise (or re-initialise) the engine.
    Called by main.py lifespan and by tests that need a custom URL.
    """
    global engine, SessionLocal
    if url:
        engine = _build_engine(url)
    else:
        engine, SessionLocal = _get_engine_and_session()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine
