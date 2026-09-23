from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError
from app.db.session import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def liveness() -> dict:
    settings = get_settings()
    return {"status": "ok", "version": settings.version, "env": settings.env}


@router.get("/ready")
def readiness(db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ServiceUnavailableError("Database unavailable", code="database_unavailable") from exc
    return {"status": "ready", "database": "ok"}
