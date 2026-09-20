"""
tests/conftest.py — Shared fixtures for all repository tests.

Uses an in-memory SQLite database so tests run without PostgreSQL.
The seed() function is called once per test session.
"""
from __future__ import annotations

import os
import sys

# ── 1. Put backend/ on sys.path ───────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# ── 2. Patch env BEFORE any project import so config picks up SQLite ──────────
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

# Now safe to import project modules
import database as db_module
from database import Base, init_engine
import models  # noqa: F401 — registers all ORM classes with Base


from sqlalchemy.pool import StaticPool

# ── Session-scoped SQLite engine ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def sqlite_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(eng, "connect")
    def enable_fk(dbapi_con, _):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="session")
def seeded_session(sqlite_engine):
    """
    Injects our SQLite engine into database module, seeds once, returns session.
    """
    SessionTest = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    # Patch the module singletons so seed.py / repository use our engine
    db_module.engine = sqlite_engine
    db_module.SessionLocal = SessionTest

    import seed as seed_module
    # Also patch seed's references (it imports engine/SessionLocal at call time via db_module)
    seed_module.seed()

    session = SessionTest()
    yield session
    session.close()


@pytest.fixture(scope="session", autouse=True)
def setup_app_db_override(seeded_session, sqlite_engine):
    """
    Override get_db in FastAPI app to use the seeded SQLite session.
    """
    from main import app
    from database import get_db

    SessionTest = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def _override_get_db():
        db = SessionTest()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="session")
def repo(seeded_session):
    from repository.postgres import PostgresTransactionRepository
    return PostgresTransactionRepository(seeded_session)
