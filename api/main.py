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
from .routers import accounts, ai, exports, health, leagues, views


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ESPN Edge", version="0.1.0", lifespan=lifespan)

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
)

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


app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(leagues.router)
app.include_router(views.router)
app.include_router(ai.router)
app.include_router(exports.router)


@app.get("/")
def root() -> dict:
    return {"app": "espn-edge", "docs": "/docs", "health": "/api/health"}
