"""FastAPI application entrypoint (SPEC 3)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .routers import (
    accounts,
    ai,
    auth,
    exports,
    health,
    leagues,
    opportunity,
    player_images,
    recovery,
    views,
)
from .security import csrf_middleware, security_headers_middleware
from .services.recovery import RecoveryAdmissionError, secret_free_error


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ESPN Edge", version="1.0.0", lifespan=lifespan)

# Vite dev server talks to the API cross-origin in dev only.
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{_settings.web_port}",
        f"http://127.0.0.1:{_settings.web_port}",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    # `allow_credentials` is deliberately left off. Phase 40 serves the SPA and
    # the API as ONE origin, so the session cookie never needs to travel
    # cross-origin -- and a wildcard origin combined with credentials is the
    # classic way to hand an attacker's page an authenticated read. Pinned by
    # tests/test_cors_and_runtime_config.py.
)


@app.get("/config.json", tags=["config"])
def runtime_config() -> dict[str, object]:
    """Configuration the SPA reads at runtime rather than at build time.

    Phase 40's guarantee is "no build-time `VITE_API_BASE` promotion
    dependency": an image baked with one environment's API base cannot be
    promoted to another, so the same artifact must ask the server where it is.

    `api_base` is the empty string because the SPA and the API share an origin.
    That is the answer, not a placeholder -- a relative base is what makes the
    single-origin cookie work.

    Sessionless on purpose: the SPA needs this before anyone has logged in.
    It therefore returns nothing that is not already public.
    """
    settings = get_settings()
    return {
        "api_base": "",
        "mode": settings.app_mode,
        "season": settings.season,
    }


def _is_account_credential_endpoint(request: Request) -> bool:
    """True for the two POST endpoints that carry SWID/espn_s2 in the request body.

    FastAPI's default 422 reflects the submitted ``input`` object, which would echo
    the ESPN cookies back on a malformed request rejected before the router runs.
    """
    if request.method != "POST":
        return False
    path = request.url.path.rstrip("/")
    return path == "/api/accounts" or (
        path.startswith("/api/accounts/") and path.endswith("/reauth")
    )


@app.exception_handler(RequestValidationError)
async def _redact_account_validation_errors(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Sanitize validation failures for the credential-bearing account endpoints.

    Returns a generic, credential-neutral 422 with no ``input`` object, no submitted
    values, and no sensitive field names. Deliberately logs nothing: ``exc``, the
    request body, and headers may all contain SWID/espn_s2. Every other endpoint
    keeps FastAPI's standard validation response.
    """
    if _is_account_credential_endpoint(request):
        return JSONResponse(status_code=422, content={"detail": "invalid account request"})
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(RecoveryAdmissionError)
async def _recovery_admission_error(_request: Request, exc: RecoveryAdmissionError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": secret_free_error(exc)})


# Order matters, and it is the reverse of what it looks like. Starlette
# prepends each registration, so the LAST one added ends up OUTERMOST. The
# headers wrapper must be outermost to stamp every response -- including the
# 403 the CSRF check short-circuits with, which is a response an attacker's
# page can see. Registered the other way round first, and
# `test_the_headers_reach_a_refusal_too` caught the bare 403.
app.middleware("http")(csrf_middleware)
app.middleware("http")(security_headers_middleware)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(leagues.router)
app.include_router(views.router)
app.include_router(player_images.router)
app.include_router(ai.router)
app.include_router(exports.router)
app.include_router(opportunity.router)
app.include_router(recovery.router)


@app.get("/")
def root() -> dict:
    return {"app": "espn-edge", "docs": "/docs", "health": "/api/health"}
