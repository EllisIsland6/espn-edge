"""CSRF and response security headers.

Both are hosted-mode concerns. In `private_operator` there is no browser
session to ride on and no cross-origin attacker, so enforcing either would add
failure modes without removing any.
"""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from . import config
from .auth import COOKIE_NAME


def _hosted() -> bool:
    """Read the mode through the module, not through a name bound at import.

    `from .config import get_settings` binds the function object at import
    time, so patching `api.config.get_settings` never reaches it -- the guard
    stays live in every test that thinks it disabled or enabled it. That is
    exactly the defect recorded in `tests/test_hosted_mode.py`'s `_mode`
    docstring, and it happened here: the CSRF middleware below was inert in
    the hosted-mode tests and they passed anyway, for the wrong reason.
    Measured, then fixed.
    """
    return config.get_settings().is_public_synthetic

#: The double-submit pair. The session cookie is HttpOnly so JavaScript cannot
#: read it; this one deliberately is NOT, because the page must read it to put
#: it in the header. That asymmetry is the mechanism: an attacker's page can
#: cause the browser to SEND our cookies, but cannot READ them to forge the
#: matching header, because it is on another origin.
CSRF_COOKIE = "edge_csrf"
CSRF_HEADER = "X-CSRF-Token"

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths exempt from CSRF. Only the docs UI, which is GET-only anyway and is
#: listed so the exemption is a decision rather than an accident of routing.
_EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    """Set the session and CSRF cookies with the attributes they must have.

    Centralised so the front door -- whenever it lands -- cannot get the flags
    wrong. `httponly` on the session cookie is Phase 40's explicit guarantee
    that JavaScript cannot read it. `secure` is conditional only because a
    local HTTP development origin would otherwise drop the cookie silently and
    the whole flow would fail with no visible cause.
    """
    secure = _hosted()
    response.set_cookie(
        COOKIE_NAME,
        session_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,  # deliberate: the page must read this one
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    """Clear both, with the flags they were set with.

    A browser will not replace a cookie whose attributes do not match, so a
    delete that forgets `path` or `httponly` leaves the cookie in place and
    sign-out silently does nothing.
    """
    for name, httponly in ((COOKIE_NAME, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", httponly=httponly, samesite="lax")


async def csrf_middleware(request: Request, call_next):
    """Double-submit CSRF, hosted mode only.

    Checked before the route runs and before any session is resolved, so a
    forged request never reaches a handler. Safe methods pass untouched --
    they are not state-changing, and requiring a header on them would break
    every plain navigation.

    `compare_digest` rather than `==`: the token is short-lived and guessing it
    is not the threat, but a timing-distinguishable comparison on a secret is
    a habit worth not having.
    """
    if (
        _hosted()
        and request.method in UNSAFE_METHODS
        and not request.url.path.startswith(_EXEMPT_PREFIXES)
    ):
        cookie = request.cookies.get(CSRF_COOKIE)
        header = request.headers.get(CSRF_HEADER)
        if not cookie or not header or not secrets.compare_digest(cookie, header):
            return JSONResponse(status_code=403, content={"detail": "csrf check failed"})
    return await call_next(request)


async def security_headers_middleware(request: Request, call_next):
    """Headers that constrain what a compromised page can do.

    `default-src 'self'` with no `unsafe-inline` is the load-bearing one: it is
    what stops an injected script reaching an attacker's origin with whatever
    it managed to read. HSTS is hosted-only, because sending it from a local
    HTTP origin would pin a developer's browser to HTTPS for a host that does
    not serve it.
    """
    response = await call_next(request)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: https:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if _hosted():
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response
