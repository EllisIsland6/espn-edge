"""Three modes, two questions -- and every place the code asks one of them.

ADR-012 adds `private_invited`: hosted like `public_synthetic`, real like
`private_operator`. Before it, the code asked one question, `is_public_synthetic`,
in two senses: "is this hosted?" (sessions, CSRF, the front door, default deny)
and "is this synthetic?" (no provider, no custody, no raw-cache write). The new
mode is exactly the case where the two answers differ, so the question is split
into `is_hosted` and `is_synthetic`, the old name is gone, and this file keeps a
register of every reader with the reason it asks the question it asks.

A reader that lands unregistered fails `test_every_reader_is_registered`; a
reader registered under the wrong question fails the behaviour matrix.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from api.config import Settings

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"

MODES = ("private_operator", "public_synthetic", "private_invited")

#: (file relative to api/, property) -> why that property, not the other.
REGISTER: dict[tuple[str, str], str] = {
    (
        "db.py",
        "is_hosted",
    ): "default deny is about where requests come from, not what the deployment holds",
    (
        "security.py",
        "is_hosted",
    ): "secure cookies and CSRF apply to every internet-facing deployment",
    (
        "routers/auth.py",
        "is_hosted",
    ): "the OIDC front door exists wherever requests come from the internet",
    (
        "crypto.py",
        "is_synthetic",
    ): "credential custody is forbidden only where no real data may exist",
    (
        "services/espn.py",
        "is_synthetic",
    ): "the real provider is unconstructible only on the synthetic site",
    (
        "services/ai.py",
        "is_hosted",
    ): "a hosted deployment with a host key is ledger-bounded whether or not a price is set",
}

RETIRED = "is_public_synthetic"


def _readers() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(API.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                "is_hosted",
                "is_synthetic",
                RETIRED,
            ):
                found.add((str(path.relative_to(API)), node.attr))
    return found


def test_every_reader_is_registered():
    readers = _readers()
    assert readers, "premise: the walk sees the readers at all"
    unregistered = readers - set(REGISTER)
    stale = set(REGISTER) - readers
    assert not unregistered, f"readers with no reason recorded: {sorted(unregistered)}"
    assert not stale, f"register names readers that no longer exist: {sorted(stale)}"


def test_the_retired_name_is_gone_from_the_application():
    assert not {r for r in _readers() if r[1] == RETIRED}
    assert not hasattr(Settings(app_mode="private_operator"), RETIRED)


@pytest.mark.parametrize(
    ("mode", "hosted", "synthetic"),
    [
        ("private_operator", False, False),
        ("public_synthetic", True, True),
        ("private_invited", True, False),
    ],
)
def test_the_truth_table(mode, hosted, synthetic):
    s = Settings(app_mode=mode)
    assert s.is_hosted is hosted
    assert s.is_synthetic is synthetic


def _patch_all(monkeypatch, mode: str) -> None:
    """Every module reads settings through its own binding; patch each one."""
    factory = lambda: Settings(app_mode=mode)  # noqa: E731
    for target in (
        "api.config.get_settings",
        "api.services.espn.get_settings",
        "api.crypto.get_settings",
        "api.db.get_settings",
        "api.routers.health.get_settings",  # bound at import, like espn.py -- the trap test_hosted_mode names
    ):
        monkeypatch.setattr(target, factory, raising=True)


@pytest.mark.parametrize("mode", MODES)
def test_the_behaviour_matrix(monkeypatch, mode):
    """The guards, asked directly, in every mode. Synthetic refuses the three
    dangerous operations; the two real modes admit them; the two hosted modes
    answer `_hosted()` true in both places that ask."""
    _patch_all(monkeypatch, mode)
    from api import security
    from api.crypto import _forbid_credential_custody_in_hosted_mode
    from api.routers import auth
    from api.services.espn import HostedModeForbidden, _forbid_in_hosted_mode

    s = Settings(app_mode=mode)
    assert security._hosted() is s.is_hosted
    assert auth._hosted() is s.is_hosted

    for guard, operation in (
        (_forbid_in_hosted_mode, "real provider construction"),
        (_forbid_in_hosted_mode, "raw cache write"),
    ):
        if s.is_synthetic:
            with pytest.raises(HostedModeForbidden):
                guard(operation)
        else:
            guard(operation)

    if s.is_synthetic:
        with pytest.raises(HostedModeForbidden):
            _forbid_credential_custody_in_hosted_mode()
    else:
        _forbid_credential_custody_in_hosted_mode()


def test_private_invited_refuses_a_request_without_a_session(monkeypatch):
    """The acceptance criterion that matters most: the real hosted mode keeps
    the synthetic site's door. A data route and the identity route both 401
    with no cookie, exactly as `test_hosted_default_deny.py` proves for
    `public_synthetic`."""
    from fastapi.testclient import TestClient

    from api.main import app

    _patch_all(monkeypatch, "private_invited")
    with TestClient(app) as client:
        assert client.get("/api/accounts").status_code == 401
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/health").json()["mode"] == "private_invited"
