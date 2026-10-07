"""FastAPI application entrypoint (SPEC 3)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

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
def root():
    """The JSON banner when this process is API-only; the app when it is not.

    A single-container deployment serves the frontend from here, and a visitor
    landing on `/` wants the application, not a JSON blob naming where the
    application might be. The banner is kept for the API-only shape, which is
    what every test and the development server use.
    """
    directory = get_settings().static_dir
    if directory:
        index = Path(directory) / "index.html"
        if index.is_file():
            return FileResponse(index)
    return {"app": "espn-edge", "docs": "/docs", "health": "/api/health"}


def _mount_frontend(application: FastAPI, directory: Path) -> None:
    """Serve the built frontend from this process, with an SPA fallback.

    Registered LAST and deliberately: a catch-all route added before the API
    routers would shadow every one of them. `/api/` is refused here rather
    than falling through to `index.html`, because an unknown API path that
    answers 200 with a page of HTML is a far worse error message than a 404 --
    the caller's JSON parse fails somewhere else entirely.

    The traversal guard is the part that matters. `full_path` comes from the
    URL, so `../../etc/passwd` is a request this function would otherwise
    honour; every candidate is resolved and checked to be inside the served
    directory before it is opened. `tests/test_static_frontend.py` plants that
    exact request.
    """
    root_dir = directory.resolve()

    @application.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="not found")
        index = root_dir / "index.html"
        if full_path:
            candidate = (root_dir / full_path).resolve()
            inside = candidate == root_dir or root_dir in candidate.parents
            if inside and candidate.is_file():
                return FileResponse(candidate)
        # Any other path is a client-side route: hand back the shell and let
        # the router in the browser decide, which is what an SPA needs.
        return FileResponse(index)


_static_dir = get_settings().static_dir
if _static_dir:
    _resolved_static = Path(_static_dir)
    if not (_resolved_static / "index.html").is_file():
        # Loud rather than silent: a container built without the frontend
        # would otherwise serve 404s for every page and look like a routing
        # bug, days after the build that caused it.
        raise RuntimeError(
            f"STATIC_DIR={_static_dir!r} has no index.html. Either the "
            "frontend was not built into the image or the path is wrong; "
            "serving the API with no frontend is not a state worth starting in."
        )
    _mount_frontend(app, _resolved_static)
