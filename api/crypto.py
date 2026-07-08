"""Cookie vault — Fernet encryption for espn_s2 at rest (SPEC 2.10, 3, guardrail 11).

Only espn_s2 is treated as a secret to encrypt; SWID is an opaque account id that
we must match against team owners, so it is stored plainly but is still never logged.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class VaultError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise VaultError(
            "FERNET_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:  # malformed key
        raise VaultError("FERNET_KEY is malformed; expected a urlsafe base64 32-byte key") from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise VaultError("could not decrypt espn_s2 — wrong FERNET_KEY?") from exc


def redact(value: str | None) -> str:
    """Safe-for-logs representation of a secret (never the value itself)."""
    if not value:
        return "<empty>"
    return f"<redacted len={len(value)}>"
