"""Health check — returns season + db path (Phase 0 AC)."""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(status="ok", season=settings.season, db_path=str(settings.db_file))
