"""Health check — season, db path (Phase 0 AC) and the real backend."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.engine import make_url

from ..config import get_settings
from ..schemas import HealthOut

router = APIRouter(tags=["health"])


def describe_backend(sqlalchemy_url: str) -> str:
    """The datastore in one line, with no credentials in it.

    A load balancer polls this endpoint without authenticating, so whatever it
    returns is public. The user and password are therefore dropped and the
    host and database kept: enough to tell a staging database from a
    production one at a glance, and nothing that helps anybody reach either.
    """
    url = make_url(sqlalchemy_url)
    if url.get_backend_name() == "sqlite":
        return "sqlite"
    host = url.host or "?"
    port = f":{url.port}" if url.port else ""
    return f"{url.get_backend_name()}://{host}{port}/{url.database or '?'}"


@router.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = get_settings()
    return HealthOut(
        status="ok",
        season=settings.season,
        db_path=str(settings.db_file),
        backend=describe_backend(settings.sqlalchemy_url),
    )
