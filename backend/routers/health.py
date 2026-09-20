"""
routers/health.py — /api/health endpoint.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import get_db

router = APIRouter(prefix="/api", tags=["infra"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict:
    """
    Returns 200 with DB connectivity status.
    Frontend polls this on load to confirm the stack is up.
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        db_status = f"error: {exc}"

    return {
        "status": "ok",
        "service": "Polaris Voice Assistant",
        "version": "0.1.0",
        "database": db_status,
    }
