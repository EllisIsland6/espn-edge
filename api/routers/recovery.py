"""Local-only status and bounded manual recovery trigger (Phase 30)."""

from __future__ import annotations

import ipaddress
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request

from ..config import get_settings
from ..schemas import RecoveryBackupOut, RecoveryStatusOut
from ..services.recovery import (
    RecoveryBusyError,
    RecoveryError,
    recovery_status,
    reserve_api_trigger,
    run_backup,
    secret_free_error,
)

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


def _reject_unsafe_request(request: Request, action: str | None) -> None:
    settings = get_settings()
    allowed_hosts = {
        f"localhost:{settings.api_port}",
        f"127.0.0.1:{settings.api_port}",
    }
    allowed_origins = {
        f"http://localhost:{settings.web_port}",
        f"http://127.0.0.1:{settings.web_port}",
    }
    try:
        peer_is_loopback = bool(
            request.client and ipaddress.ip_address(request.client.host).is_loopback
        )
    except ValueError:
        peer_is_loopback = False
    if (
        not peer_is_loopback
        or settings.api_host not in {"127.0.0.1", "::1", "localhost"}
        or request.headers.get("host") not in allowed_hosts
        or request.headers.get("origin") not in allowed_origins
        or request.headers.get("x-forwarded-host") is not None
        or request.headers.get("forwarded") is not None
        or action != "backup"
    ):
        raise HTTPException(403, "local operator action required")


@router.get("/status", response_model=RecoveryStatusOut)
def status() -> dict:
    return recovery_status().public_dict()


@router.post("/backup", response_model=RecoveryBackupOut)
def backup(
    request: Request,
    reason: Literal["post-clean-portfolio-sync", "manual"] = "manual",
    action: str | None = Header(None, alias="X-ESPN-Edge-Action"),
) -> dict:
    _reject_unsafe_request(request, action)
    try:
        reserve_api_trigger()
        return run_backup(reason)
    except RecoveryBusyError as exc:
        raise HTTPException(409, secret_free_error(exc)) from exc
    except RecoveryError as exc:
        status_code = 429 if exc.code == "recovery_rate_limited" else 503
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code, secret_free_error(exc), headers=headers) from exc
