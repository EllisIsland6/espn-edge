"""Session endpoints: who am I, and sign me out.

There is deliberately **no endpoint that mints a session**. Minting requires
proof of identity, that proof is an OIDC ID token, and verifying one needs a
JWT library this environment cannot install. Hand-rolling the verification is
not an option -- `alg: none` and key-confusion bugs live exactly there.

So `mint_session` stays internal until the identity provider lands, and these
two routes exercise everything around it. The shape of the callback is not
guessed at here; it is absent, which is the honest state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..auth import COOKIE_NAME, revoke_session
from ..db import get_session
from ..schemas import SessionIdentity
from ..tenancy import current_tenant_id, current_user_id

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax")
