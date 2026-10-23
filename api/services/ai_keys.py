"""A tenant's own AI provider key: stored, read, used, erased (ADR-012, G7).

The operator's decision, 2026-10-23: *they pay for their own AI API, and we
store what's needed and practical, per best practice.* This module is that
sentence as code, and it leans on two things that already exist rather than
inventing a third custody path:

- `api/crypto.py` encrypts and decrypts. It is the chokepoint that refuses
  custody on the synthetic site, and its key is the one the custody CMK
  protects, so an AI key is kept exactly as an ESPN session is kept.
- Row security scopes the row (revision 0018), so the tenant that wrote a key
  is the tenant that can read it; the predicate here is for plan quality and
  readability, not isolation (Phase 36's contract).

What never happens here: the plaintext in a log line, in an exception
message, in a response, or anywhere but the one `Anthropic(...)` client built
for the one call that needs it. `last_error` records an exception CLASS, never
its message -- a message is the one place a provider could echo a value back.

## Verification before storage

A key is checked against the provider's model listing (`GET /v1/models`),
which needs a valid key and costs no tokens, so a mistyped key is refused on
the spot and nothing is stored. The check is a module-level callable so the
offline suite replaces it; the real one is never reached in tests (the suite
forbids outbound sockets) and never logs the key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..models import AiCredential
from ..schemas import AiKeyOut
from ..tenancy import current_tenant_id

log = logging.getLogger(__name__)

PROVIDER = "anthropic"
MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"
VERIFY_TIMEOUT_SECONDS = 10.0


class AiKeyRefused(ValueError):
    """The provider refused the key. The message is fixed and names nothing."""

    def __init__(self) -> None:
        super().__init__("the key was refused by Anthropic")


class AiKeyUnverifiable(RuntimeError):
    """The provider could not be reached to check the key, so nothing was stored."""

    def __init__(self) -> None:
        super().__init__("Anthropic could not be reached to verify the key; nothing was stored")


def hint_for(api_key: str) -> str:
    """The last four characters. A key shorter than that is its own hint."""
    return api_key[-4:]


def verify_with_anthropic(api_key: str) -> None:
    """Ask the provider whether the key is good, without spending tokens.

    401 and 403 are a refusal. Anything else that is not a success -- a
    network failure, a 5xx, a 429 -- is "could not verify", which is a
    different answer: the key may well be good, and the person should try
    again rather than be told it is wrong. Nothing of the key or the response
    body is logged; the one log line carries the status code.
    """
    try:
        response = httpx.get(
            MODELS_URL,
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        log.warning("ai key verification could not reach the provider: %s", type(exc).__name__)
        raise AiKeyUnverifiable() from exc
    if response.status_code in (401, 403):
        raise AiKeyRefused()
    if response.status_code != 200:
        log.warning("ai key verification got status %s", response.status_code)
        raise AiKeyUnverifiable()


#: Replaced by the offline suite; production uses the provider.
verify: Callable[[str], None] = verify_with_anthropic


def _row(session: Session) -> AiCredential | None:
    return session.scalar(
        select(AiCredential).where(AiCredential.tenant_id == current_tenant_id(session))
    )


def _out(row: AiCredential) -> AiKeyOut:
    return AiKeyOut(
        provider=row.provider,
        hint=row.key_hint,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_used_at=row.last_used_at,
        last_error=row.last_error,
    )


def read_key(session: Session) -> AiKeyOut | None:
    row = _row(session)
    return None if row is None else _out(row)


def store_key(session: Session, api_key: str) -> AiKeyOut:
    """Verify, encrypt, upsert. The caller commits.

    `crypto.encrypt` runs the hosted-mode chokepoint first, so on a synthetic
    instance this raises `HostedModeForbidden` before anything is verified or
    written -- the same refusal, the same 403, as pasting an ESPN session.
    """
    api_key = api_key.strip()
    ciphertext = crypto.encrypt(api_key)
    verify(api_key)
    now = datetime.now(UTC)
    row = _row(session)
    if row is None:
        row = AiCredential(
            tenant_id=current_tenant_id(session),
            provider=PROVIDER,
            api_key_encrypted=ciphertext,
            key_hint=hint_for(api_key),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.api_key_encrypted = ciphertext
        row.key_hint = hint_for(api_key)
        row.updated_at = now
        row.last_used_at = None
        row.last_error = None
    session.flush()
    return _out(row)


def delete_key(session: Session) -> bool:
    """Erase the row. The caller commits. True when there was one."""
    row = _row(session)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def tenant_key(session: Session) -> str | None:
    """The plaintext, for the one call that needs it, or None when the tenant
    holds no key. Decrypts through the chokepoint; the caller must not keep
    the value past the call."""
    row = _row(session)
    if row is None:
        return None
    return crypto.decrypt(row.api_key_encrypted)


def note_use(session: Session, *, error: BaseException | None) -> None:
    """Record that the key was used: the time on success, the exception class
    on failure. The caller commits. Never the message."""
    row = _row(session)
    if row is None:
        return
    if error is None:
        row.last_used_at = datetime.now(UTC)
        row.last_error = None
    else:
        row.last_error = type(error).__name__[:64]
    session.flush()
