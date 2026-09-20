"""
main.py — FastAPI application entry point for Polaris Voice Assistant.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import Base, init_engine
from models import Base  # noqa: F811 — ensures all models registered
from routers.health import router as health_router
from routers.chat import router as chat_router
from routers.voice import router as voice_router
from routers.insights import router as insights_router

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB engine and create tables on startup (idempotent)."""
    logger.info("Starting Polaris — initialising database …")
    eng = init_engine()
    Base.metadata.create_all(bind=eng)
    try:
        from seed import seed
        seed()
    except Exception as exc:
        logger.warning("Database seed check: %s", exc)
    logger.info("DB tables and seed data ready.")
    yield
    logger.info("Polaris shutting down.")



app = FastAPI(
    title="Polaris Voice Assistant API",
    description="Multilingual voice-first business intelligence for Indian merchants.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(voice_router)
app.include_router(insights_router)


@app.get("/", include_in_schema=False)
def root():
    return {"message": "Polaris Voice Assistant — see /docs for API reference"}
