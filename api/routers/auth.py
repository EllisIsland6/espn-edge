"""Session endpoints: sign me in, who am I, sign me out.

The front door is `/login` -> the identity provider -> `/callback`. It mints a
session only for a subject the operator has provisioned (revision 0017), and
only for a user who holds a membership -- so an identity the provider admits
but the operator has not placed anywhere gets a 403 and no session, which is
the signal to provision them. Nothing here reads a group, role or access
token from the provider: it proves WHO, and `tenancy` decides WHICH TENANT.

Both routes exist only in `public_synthetic` mode. In `private_operator` mode
they are 404, because there is no login there to mint from -- a login screen
on a one-person laptop protects nothing -- and a route that half-exists is
worse than one that does not.

Earlier versions of this file said there was deliberately no minting endpoint
because the JWT library could not be installed. That was the environment's
venv lacking `pip`, not the package; recorded in docs/CLOSE-OUT.md.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, oidc
from ..auth import COOKIE_NAME, mint_session, revoke_session
from ..db import SessionLocal, get_session
from ..models import Identity
from ..schemas import SessionIdentity
from ..security import clear_session_cookies, new_csrf_token, set_session_cookies
from ..tenancy import (
    TenantNotResolved,
    bind_session,
    current_tenant_id,
    current_user_id,
    tenant_for_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: The in-flight login's state and nonce. HttpOnly, scoped to this router's
#: path, ten minutes. It is not signed: the browser that started the flow is
#: the only one that holds it, and the callback compares what comes back
#: against it in constant time. That is the standard double-submit shape and
#: it needs no server-side secret.
FLOW_COOKIE = "edge_oidc"
FLOW_TTL_SECONDS = 600

#: Fixed response texts. None of them names which check failed.
NOT_CONFIGURED = "identity provider not configured"
FLOW_INVALID = "sign-in could not be completed"
NOT_PROVISIONED = "this identity is not provisioned"


def _hosted() -> bool:
    # Through the module, at call time, for the reason security.py explains.
    return config.get_settings().is_public_synthetic


def _require_hosted() -> None:
    if not _hosted():
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/login", include_in_schema=True)
def login() -> RedirectResponse:
    """Start a sign-in: generate the flow secrets and redirect to the provider."""
    _require_hosted()
    if not oidc.is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    flow = oidc.Flow.new()
    try:
        url = oidc.authorization_url(flow)
    except oidc.OidcError as exc:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED) from exc
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        FLOW_COOKIE,
        flow.encode(),
        max_age=FLOW_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/auth",
    )
    return response


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    """Finish a sign-in. Every failure clears the flow cookie and mints nothing."""
    _require_hosted()
    if not oidc.is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)

    flow = oidc.Flow.decode(request.cookies.get(FLOW_COOKIE))
    if flow is None or not code or not state or not hmac.compare_digest(flow.state, state):
        raise _flow_failure(400)

    try:
        raw_token = oidc.exchange_code(code)
        identity = oidc.verify_id_token(raw_token, nonce=flow.nonce)
    except oidc.OidcError as exc:
        raise _flow_failure(401) from exc

    # Pre-tenant work, deliberately NOT through `get_session`: there is no
    # session to bind yet. Only unpolicied tables are read before the user is
    # bound (`identities`, like `app_sessions`), and the membership read that
    # follows is admitted by revision 0007's `app.user_id` policy. Pinned by
    # tests/test_route_tenant_inventory.py's PRE_TENANT_ROUTES.
    db = SessionLocal()
    try:
        user_id = db.execute(
            select(Identity.user_id).where(
                Identity.issuer == identity.issuer, Identity.subject == identity.subject
            )
        ).scalar_one_or_none()
        if user_id is None:
            raise _flow_failure(403, NOT_PROVISIONED)
        bind_session(db, user_id=user_id)
        try:
            tenant_for_user(db, user_id)
        except TenantNotResolved as exc:
            raise _flow_failure(403, NOT_PROVISIONED) from exc
        token = mint_session(db, user_id, origin="oidc")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    response = RedirectResponse("/", status_code=302)
    set_session_cookies(response, token, new_csrf_token())
    response.delete_cookie(FLOW_COOKIE, path="/api/auth", httponly=True, samesite="lax")
    return response


def _flow_failure(status: int, detail: str = FLOW_INVALID) -> HTTPException:
    """An HTTPException whose response also clears the flow cookie, so a
    failed attempt cannot be retried against stale secrets."""
    exc = HTTPException(status_code=status, detail=detail)
    exc.headers = {
        "set-cookie": f"{FLOW_COOKIE}=; Max-Age=0; Path=/api/auth; HttpOnly; SameSite=lax"
    }
    return exc


@router.get("/me", response_model=SessionIdentity)
def whoami(session: Session = Depends(get_session)) -> SessionIdentity:
    """The caller's own identity, derived from the bound session.

    Reachable only through `get_session`, so in hosted mode an unauthenticated
    caller gets 401 here exactly as they would anywhere else. It reports the
    tenant it resolved to, which is the one fact a client needs and cannot
    work out for itself.
    """
    return SessionIdentity(
        user_id=current_user_id(session),
        tenant_id=current_tenant_id(session),
    )


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: Session = Depends(get_session)) -> None:
    """Revoke the caller's session and clear the cookie.

    Succeeds even when the session has already gone -- sign-out that can fail
    is sign-out people stop trusting. The cookie is cleared with the same
    flags it was set with, because a browser will not replace a cookie whose
    attributes do not match.
    """
    token = request.cookies.get(COOKIE_NAME)
    if token:
        revoke_session(session, token)
        session.commit()
    clear_session_cookies(response)
