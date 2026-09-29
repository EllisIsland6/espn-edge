"""Phase 31 unit 2 — the hosted boundary is structural, not procedural.

These tests assert that `public_synthetic` mode cannot *construct* the real
provider or *reach* the credential path, rather than that some call site
remembered to check a flag. That distinction is the whole point of the
selection recorded in the contract: a future call site inherits the guarantee.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.config import Settings, get_settings
from api.services.cache import DBRawCache
from api.services.espn import (
    Cookies,
    EspnService,
    HostedModeForbidden,
    cookies_for_account,
)


def _mode(monkeypatch, mode: str) -> None:
    """Patch the one binding the guards actually read.

    Unit-2 review found the original helper patching three targets, two of them
    inert: `api/services/cache.py` imports `_forbid_in_hosted_mode`, not
    `get_settings`, and `api/services/espn.py` binds `get_settings` at import, so
    patching `api.config.get_settings` never reaches the guard. `raising=False`
    concealed both. One load-bearing patch, asserted to exist.
    """
    monkeypatch.setattr(
        "api.services.espn.get_settings",
        lambda: Settings(app_mode=mode),
        raising=True,
    )


class _Account:
    swid = "{00000000-0000-0000-0000-000000000001}"
    status = "active"
    espn_s2_encrypted = b""


def test_default_mode_is_private_operator():
    assert Settings().app_mode == "private_operator"
    assert Settings().is_public_synthetic is False


def test_configured_mode_is_explicit_and_validated():
    assert Settings(app_mode="public_synthetic").is_public_synthetic is True
    with pytest.raises(ValidationError):
        Settings(app_mode="somewhere_else")


def test_hosted_mode_cannot_construct_the_real_provider(monkeypatch):
    _mode(monkeypatch, "public_synthetic")
    with pytest.raises(HostedModeForbidden) as caught:
        EspnService()
    assert caught.value.operation == "real provider construction"
    # The error carries no host, key, or configuration detail.
    assert "http" not in str(caught.value)


def test_hosted_mode_cannot_reach_the_credential_path(monkeypatch):
    _mode(monkeypatch, "public_synthetic")
    with pytest.raises(HostedModeForbidden) as caught:
        cookies_for_account(_Account())
    assert caught.value.operation == "credential decrypt"


def test_hosted_mode_guard_precedes_account_state_checks(monkeypatch):
    """The guard must fire before any account inspection.

    Otherwise a hosted process could learn account state (for example that a
    row needs reauthentication) by probing the credential path.
    """

    _mode(monkeypatch, "public_synthetic")

    class Exploding:
        @property
        def swid(self):  # pragma: no cover - must never be reached
            raise AssertionError("hosted mode inspected the account row")

    with pytest.raises(HostedModeForbidden):
        cookies_for_account(Exploding())


def test_hosted_mode_cannot_write_the_raw_cache(db_session, monkeypatch):
    _mode(monkeypatch, "public_synthetic")
    cache = DBRawCache(db_session)
    with pytest.raises(HostedModeForbidden) as caught:
        cache.set("synthetic-key", {"synthetic": True})
    assert caught.value.operation == "raw cache write"


def test_hosted_mode_may_still_read_the_raw_cache(db_session, monkeypatch):
    """Read stays available; only the write direction is closed."""

    _mode(monkeypatch, "private_operator")
    DBRawCache(db_session).set("synthetic-key", {"synthetic": True})
    _mode(monkeypatch, "public_synthetic")
    assert DBRawCache(db_session).get("synthetic-key") == {"synthetic": True}


def test_private_operator_mode_is_unchanged(monkeypatch):
    _mode(monkeypatch, "private_operator")
    service = EspnService()
    try:
        assert service.host.startswith("https://")
        assert cookies_for_account(None) is None
    finally:
        service.close() if hasattr(service, "close") else None


def test_real_settings_default_does_not_block_anything():
    """A default deployment must not accidentally be in hosted mode."""

    get_settings.cache_clear()
    assert get_settings().is_public_synthetic is False


def test_mode_resolves_from_the_real_environment(monkeypatch):
    """Exercise production resolution, not an injected Settings object.

    Every other hosted assertion here substitutes a Settings instance. Without
    this test a future env_prefix, field rename or model_config change could make
    the kill switch unreachable in a real deployment with the suite still green.
    """

    monkeypatch.setenv("APP_MODE", "public_synthetic")
    get_settings.cache_clear()
    try:
        assert get_settings().is_public_synthetic is True
        with pytest.raises(HostedModeForbidden):
            EspnService()
    finally:
        get_settings.cache_clear()


def test_an_invalid_mode_refuses_to_start():
    """An ambiguous mode must fail loudly at construction, not default quietly."""

    with pytest.raises(ValidationError):
        Settings(app_mode="PUBLIC_SYNTHETIC")


def test_the_mode_cannot_be_flipped_at_runtime():
    """The boundary is a process-lifetime invariant, not a mutable field.

    `get_settings()` returns a cached singleton, so a single unvalidated
    attribute write would otherwise disable every guard process-wide.
    """

    settings = Settings(app_mode="public_synthetic")
    with pytest.raises(ValidationError):
        settings.app_mode = "private_operator"
    assert settings.is_public_synthetic is True


def test_hosted_mode_cannot_store_a_credential(monkeypatch):
    """The ingest direction, not just decrypt.

    Unit-2 review found `POST /api/accounts` still encrypting and persisting a
    real espn_s2 in hosted mode, leaving acceptance criterion 2 half-met.
    """

    monkeypatch.setattr(
        "api.config.get_settings", lambda: Settings(app_mode="public_synthetic")
    )
    from api.crypto import encrypt

    with pytest.raises(HostedModeForbidden) as caught:
        encrypt("synthetic-value")
    assert caught.value.operation == "credential custody"


def test_hosted_mode_blocks_call_sites_reachable_without_construction(monkeypatch):
    """Defence in depth for the paths that never build EspnService."""

    _mode(monkeypatch, "public_synthetic")
    from api.services.cross_check import from_espn_api
    from api.services.discovery import discover_leagues

    with pytest.raises(HostedModeForbidden) as caught:
        discover_leagues(Cookies(swid="x", espn_s2="y"))
    assert caught.value.operation == "league discovery"

    with pytest.raises(HostedModeForbidden) as caught:
        from_espn_api(1, 2026, swid="x", espn_s2="y")
    assert caught.value.operation == "cross-check provider call"


def test_the_mode_must_be_declared_before_the_process_exists(monkeypatch):
    """`app_mode` is required, with no default.

    A default meant the dangerous configuration was reached by OMITTING a
    setting while the safe one had to be expressed: review measured a container
    with only an API key set running 500 generations with no ledger row and no
    ceiling, because `private_operator` was the fallback and no prices were
    configured. A process that has not said what it is now does not start.
    """
    import pydantic
    import pytest

    from api.config import Settings

    monkeypatch.delenv("APP_MODE", raising=False)
    with pytest.raises(pydantic.ValidationError) as caught:
        Settings(_env_file=None)
    assert "app_mode" in str(caught.value)

    # Both declared values still construct.
    for mode in ("private_operator", "public_synthetic"):
        monkeypatch.setenv("APP_MODE", mode)
        assert Settings(_env_file=None).app_mode == mode


def test_the_shipped_container_does_not_decide_the_mode_for_the_deployer():
    """Baking a value into the image puts the decision back in the image rather
    than in the deployment that knows the answer -- and a hosted image that
    defaulted to `private_operator` would have the provider, the credential
    decrypt path and the raw cache all enabled."""
    from pathlib import Path

    dockerfile = (Path(__file__).resolve().parents[1] / "api" / "Dockerfile").read_text()
    assert "ENV APP_MODE" not in dockerfile
    assert "APP_MODE=public_synthetic" in dockerfile, "it must at least say how to set it"
