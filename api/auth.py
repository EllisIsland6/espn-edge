"""Application sessions: mint, verify, revoke.

This module answers "who is this request". It does not answer "what may they
do" -- that is `tenancy`, which reads the membership. Keeping the two apart is
Phase 40's explicit guarantee: a Cognito `sub` proves identity only, and an
identity provider's groups or access tokens never grant tenant authorization.

Nothing here knows about Cognito, OIDC or passwords. Whatever front door
authenticates a person calls `mint_session` and gets an opaque token. Swapping
the front door later touches nothing in this file, which is the point of
having it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AppSession

#: Bytes of randomness in a session token. 32 bytes is 256 bits; guessing one
#: is not a threat model, which is why the lookup below can be a plain indexed
#: equality rather than a search.
TOKEN_BYTES = 32

#: Cookie name. Deliberately not "session" -- that collides with the SQLAlchemy
#: sense of the word everywhere else in this codebase.
COOKIE_NAME = "edge_session"

DEFAULT_TTL = timedelta(hours=12)


class SessionRejected(Exception):
    """A token did not yield a usable session.

    One exception for every cause, and `reason` is for logs, never for the
    response. Telling a caller "expired" rather than "revoked" or "unknown"
    confirms that a token was once real, which is a free hint for anyone
    holding a stolen one.
    """

    def __init__(self, reason: str) -> None:
        super().__init__("invalid session")
        self.reason = reason


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalise a stored timestamp to an aware UTC datetime.

    Phase 35 measured that every datetime column in this schema was naive:
    aware going in, naive coming out, `TypeError` on arithmetic -- and it never
    surfaced offline because the identity map hands back the same aware object
    that was written, so only a fresh read from disk shows it. The columns are
    `DateTime(timezone=True)` now, but SQLite has no timestamp type to carry an
    offset, so a value read back from a SQLite file is still naive.

    Comparing an expiry against `datetime.now(UTC)` therefore raises on exactly
    the path that matters -- a session loaded from disk in a fresh process --
    and never in a test that wrote the row moments earlier. Hence this.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def mint_session(
    session: Session,
    user_id: int,
    *,
    ttl: timedelta = DEFAULT_TTL,
    origin: str = "unknown",
) -> str:
    """Create a session and return its raw token.

    The return value is the only time the raw token exists outside the
    caller's cookie. It is deliberately not stored, not logged and not
    recoverable: losing it means minting another, which is the correct
    behaviour and the reason a stolen database yields nothing usable.
    """
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    now = datetime.now(UTC)
    session.add(
        AppSession(
            token_hash=_hash(raw_token),
            user_id=user_id,
            created_at=now,
            expires_at=now + ttl,
            origin=origin,
        )
    )
    session.flush()
    return raw_token


def verify_session(session: Session, raw_token: str | None) -> AppSession:
    """Resolve a raw token to a live session, or refuse.

    Refuses on: no token, unknown token, expired, revoked. The caller gets one
    answer for all four.
    """
    if not raw_token:
        raise SessionRejected("absent")

    candidate = _hash(raw_token)
    row = session.execute(
        select(AppSession).where(AppSession.token_hash == candidate)
    ).scalar_one_or_none()
    if row is None:
        raise SessionRejected("unknown")

    # Belt and braces. The SQL lookup already matched on the hash; this makes
    # the final comparison constant-time so the claim in the model's docstring
    # is true of the code rather than only of the intent.
    if not hmac.compare_digest(row.token_hash, candidate):
        raise SessionRejected("mismatch")

    if row.revoked_at is not None:
        raise SessionRejected("revoked")
    if _as_utc(row.expires_at) <= datetime.now(UTC):
        raise SessionRejected("expired")
    return row


def revoke_session(session: Session, raw_token: str) -> bool:
    """Revoke by token. Returns whether a live session was actually ended.

    Idempotent: revoking an already-revoked or unknown token is not an error,
    because sign-out must succeed even when the session has already gone. It
    reports `False` so a caller that cares can tell the difference.
    """
    row = session.execute(
        select(AppSession).where(AppSession.token_hash == _hash(raw_token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(UTC)
    session.flush()
    return True


def revoke_all_for_user(session: Session, user_id: int) -> int:
    """Sign a user out everywhere. Returns how many sessions were ended.

    The operation an incident needs, and the reason `revoked_at` is a column
    rather than a deletion: afterwards you can still answer when each session
    was cut off and when it had been created.
    """
    now = datetime.now(UTC)
    rows = session.execute(
        select(AppSession).where(
            AppSession.user_id == user_id, AppSession.revoked_at.is_(None)
        )
    ).scalars().all()
    for row in rows:
        row.revoked_at = now
    session.flush()
    return len(rows)
