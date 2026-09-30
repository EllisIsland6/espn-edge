"""Application sessions: what they accept, what they refuse, and what is on disk.

The security property that matters here is not "a valid token works" -- that is
the easy half. It is that the database holds nothing a thief could use, and
that every way a session can be dead produces the same answer to the caller.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from api.auth import (
    DEFAULT_TTL,
    SessionRejected,
    _as_utc,
    mint_session,
    revoke_all_for_user,
    revoke_session,
    verify_session,
)
from api.db import SessionLocal
from api.models import AppSession, User


@pytest.fixture
def user(db_session):
    row = User(email="someone@example.test")
    db_session.add(row)
    db_session.flush()
    return row


def test_a_minted_token_verifies(db_session, user):
    token = mint_session(db_session, user.id, origin="test")
    resolved = verify_session(db_session, token)
    assert resolved.user_id == user.id
    assert resolved.origin == "test"


def test_the_raw_token_is_nowhere_in_the_database(db_session, user):
    """The property a stolen database turns on. Searched across every column of
    every table rather than only the one we expect it in -- a token that leaked
    into an audit row or an origin field would still be a usable credential,
    and checking only `app_sessions.token_hash` would not see it."""
    token = mint_session(db_session, user.id)
    db_session.commit()

    found: list[str] = []
    probe = SessionLocal()
    try:
        tables = [
            r[0]
            for r in probe.execute(
                text(
                    "SELECT name FROM sqlite_schema WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            )
        ]
        assert len(tables) >= 20, "no tables found; this search proves nothing"
        for table in tables:
            for row in probe.execute(text(f"SELECT * FROM {table}")):
                for value in row:
                    if isinstance(value, str) and token in value:
                        found.append(table)
    finally:
        probe.close()

    assert not found, f"the raw session token is stored in: {sorted(set(found))}"


def test_an_expired_session_is_refused(db_session, user):
    token = mint_session(db_session, user.id, ttl=timedelta(seconds=-1))
    with pytest.raises(SessionRejected) as caught:
        verify_session(db_session, token)
    assert caught.value.reason == "expired"


def test_expiry_survives_a_fresh_read_from_disk(db_session, user):
    """The Phase 35 trap, aimed at deliberately.

    SQLite has no timestamp type that carries an offset, so a row read back
    from the file is naive even though the column is `DateTime(timezone=True)`.
    Comparing it against `datetime.now(UTC)` raises `TypeError`. It never shows
    up while the instance is still live in the identity map -- so this test
    commits, opens a SEPARATE session, and forces the value off disk.

    It is not only a cross-process problem, which is what makes it nasty: a
    flush inside the same session expires the instance, and the next SELECT
    reloads it naive. Measured, one test below.

    Remove `_as_utc` from `api/auth.py` and this fails with
    `TypeError: can't compare offset-naive and offset-aware datetimes`, while
    every other test in this file stays green.
    """
    token = mint_session(db_session, user.id, ttl=timedelta(hours=1))
    db_session.commit()

    fresh = SessionLocal()
    try:
        row = fresh.execute(
            select(AppSession).where(AppSession.user_id == user.id)
        ).scalar_one()
        assert row.expires_at.tzinfo is None, (
            "the stored expiry came back timezone-aware, so this test is no "
            "longer exercising the naive-datetime path it exists for"
        )
        assert verify_session(fresh, token).user_id == user.id
    finally:
        fresh.close()


def test_a_revoked_session_is_refused(db_session, user):
    token = mint_session(db_session, user.id)
    assert revoke_session(db_session, token) is True
    with pytest.raises(SessionRejected) as caught:
        verify_session(db_session, token)
    assert caught.value.reason == "revoked"


def test_revoking_twice_is_not_an_error(db_session, user):
    """Sign-out must succeed even when the session has already gone."""
    token = mint_session(db_session, user.id)
    assert revoke_session(db_session, token) is True
    assert revoke_session(db_session, token) is False


def test_unknown_and_absent_tokens_are_refused(db_session):
    for value, reason in ((None, "absent"), ("", "absent"), ("not-a-token", "unknown")):
        with pytest.raises(SessionRejected) as caught:
            verify_session(db_session, value)
        assert caught.value.reason == reason


def test_every_refusal_tells_the_caller_the_same_thing(db_session, user):
    """`reason` is for logs. The message is what a caller sees, and it must not
    distinguish "expired" from "revoked" from "never existed" -- each of those
    confirms something about a token someone is holding."""
    expired = mint_session(db_session, user.id, ttl=timedelta(seconds=-1))
    revoked = mint_session(db_session, user.id)
    revoke_session(db_session, revoked)

    messages = set()
    for token in (None, "not-a-token", expired, revoked):
        with pytest.raises(SessionRejected) as caught:
            verify_session(db_session, token)
        messages.add(str(caught.value))
    assert messages == {"invalid session"}, f"refusals are distinguishable: {messages}"


def test_revoke_all_ends_every_live_session_and_counts_them(db_session, user):
    tokens = [mint_session(db_session, user.id) for _ in range(3)]
    revoke_session(db_session, tokens[0])

    assert revoke_all_for_user(db_session, user.id) == 2, "already-revoked was re-counted"
    for token in tokens:
        with pytest.raises(SessionRejected):
            verify_session(db_session, token)


def test_revocation_preserves_when_the_session_was_created(db_session, user):
    """The reason `revoked_at` is a column rather than a DELETE: afterwards you
    can still answer when a session started and when it was cut off."""
    before = datetime.now(UTC)
    token = mint_session(db_session, user.id)
    revoke_session(db_session, token)

    row = db_session.execute(select(AppSession)).scalar_one()
    assert row.created_at is not None
    assert row.revoked_at is not None
    # Normalised, because the raw attribute is NAIVE here -- in the same
    # session that wrote it, moments earlier. The first version of this test
    # compared it directly and raised `TypeError: can't compare offset-naive
    # and offset-aware datetimes`, which also disproved a claim in the
    # docstring above it: the identity map does NOT reliably hand back the
    # aware object, because the flush in `revoke_session` expires the instance
    # and the next SELECT reloads it from SQLite. So the trap is wider than a
    # fresh-process read -- any flush is enough.
    assert _as_utc(row.expires_at) > before
    assert _as_utc(row.expires_at) <= before + DEFAULT_TTL + timedelta(seconds=5)
