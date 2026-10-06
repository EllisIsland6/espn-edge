"""API smoke tests (Phase 0 AC: /api/health returns season + db path)."""

import re

import pytest
from fastapi.testclient import TestClient

from api.db import init_db
from api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _ensure_schema():
    # Other tests drop/recreate tables; guarantee the schema exists for API tests.
    init_db()


def test_health_returns_season_and_db_path():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["season"] == 2026
    assert body["db_path"].endswith(".db")


def test_add_account_never_leaks_cookies():
    r = client.post(
        "/api/accounts",
        json={"label": "Main", "swid": "AAAA-1111", "espn_s2": "super-secret-value"},
    )
    assert r.status_code == 201
    body = r.json()
    assert "swid" not in body and "espn_s2" not in body
    assert "super-secret-value" not in r.text
    assert body["label"] == "Main"
    assert body["status"] == "active"


def test_reauth_updates_cookie_swid_and_status_without_leaking():
    from api.crypto import decrypt
    from api.db import SessionLocal
    from api.models import Account

    aid = client.post(
        "/api/accounts", json={"label": "Expiring", "swid": "OLD-1", "espn_s2": "old-s2"}
    ).json()["id"]
    # Force the account into needs_reauth so we can prove reauth clears it.
    with SessionLocal() as s:
        s.get(Account, aid).status = "needs_reauth"
        s.commit()

    r = client.post(
        f"/api/accounts/{aid}/reauth",
        json={"swid": "NEW-2", "espn_s2": "brand-new-secret"},
    )
    assert r.status_code == 200
    body = r.json()
    # Response never echoes the secrets back.
    assert "swid" not in body and "espn_s2" not in body
    assert "brand-new-secret" not in r.text and "NEW-2" not in r.text
    assert body["status"] == "active"

    with SessionLocal() as s:
        acct = s.get(Account, aid)
        assert acct.status == "active"
        assert acct.swid == "{NEW-2}"  # normalized to braced form
        assert decrypt(acct.espn_s2_encrypted) == "brand-new-secret"


def test_reauth_missing_account_404():
    r = client.post(
        "/api/accounts/999999/reauth", json={"swid": "X-1", "espn_s2": "s2"}
    )
    assert r.status_code == 404


def test_reauth_empty_swid_400():
    aid = client.post(
        "/api/accounts", json={"label": "T", "swid": "{Y-1}", "espn_s2": "s2"}
    ).json()["id"]
    r = client.post(f"/api/accounts/{aid}/reauth", json={"swid": "{}", "espn_s2": "s2"})
    assert r.status_code == 400


def test_manual_add_league_parses_url():
    r = client.post(
        "/api/leagues",
        json={"league_ref": "https://fantasy.espn.com/football/team?leagueId=778899&seasonId=2026"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["espn_league_id"] == "778899"
    assert body["my_team_id"] is None
    assert body["my_team_name"] is None
    assert body["my_team_logo_url"] is None


def test_manual_add_league_is_idempotent_for_repeat_import():
    payload = {"league_ref": "90007771", "season": 2026}
    first = client.post("/api/leagues", json=payload)
    second = client.post("/api/leagues", json=payload)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    matching = [
        league
        for league in client.get("/api/leagues").json()
        if league["espn_league_id"] == "90007771" and league["season"] == 2026
    ]
    assert len(matching) == 1


def test_repeat_import_returns_existing_owned_team_identity():
    from api.db import SessionLocal
    from api.models import League, Team

    payload = {"league_ref": "90007772", "season": 2026}
    first = client.post("/api/leagues", json=payload)
    assert first.status_code == 201
    league_id = first.json()["id"]

    with SessionLocal() as session:
        team = Team(
            league_id=league_id,
            espn_team_id=8,
            name="Existing Eight",
            logo_url="https://example.test/eight.png",
            is_me=True,
        )
        session.add(team)
        session.flush()
        session.get(League, league_id).my_team_id = team.id
        session.commit()

    repeated = client.post("/api/leagues", json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == league_id
    assert repeated.json()["my_team_name"] == "Existing Eight"
    assert repeated.json()["my_team_logo_url"] == "https://example.test/eight.png"


def test_delete_unlinked_account_ok():
    aid = client.post(
        "/api/accounts", json={"label": "Disposable", "swid": "{X-1}", "espn_s2": "s2"}
    ).json()["id"]
    assert client.delete(f"/api/accounts/{aid}").status_code == 204
    labels = [a["label"] for a in client.get("/api/accounts").json()]
    assert "Disposable" not in labels


def test_delete_account_blocked_when_leagues_linked():
    aid = client.post(
        "/api/accounts", json={"label": "Linked", "swid": "{X-2}", "espn_s2": "s2"}
    ).json()["id"]
    add = client.post("/api/leagues", json={"league_ref": "445566", "account_id": aid})
    assert add.status_code == 201

    r = client.delete(f"/api/accounts/{aid}")
    assert r.status_code == 409
    assert "linked league" in r.json()["detail"]
    # account still present (not deleted)
    assert aid in [a["id"] for a in client.get("/api/accounts").json()]


# ---- credential redaction (Security: redact account validation errors) -------
# Unique canaries so any accidental reflection is unmistakable in the raw body.
#
# The swid canary is PER TEST, via the fixture below, and that is not tidiness.
# These tests share one database and one tenant, and revision 0015 put
# `UNIQUE (tenant_id, swid)` on `accounts` -- so one module-level swid constant
# meant the second test to post it got a 409 instead of the account it needed.
# Five tests failed that way the first time the suite ran against the new
# constraint. A per-test canary also says WHICH test leaked, if one ever does.
_S2_CANARY = "S2-CANARY-b41c88d0"
_S2_CANARY_OLD = "S2-CANARY-OLD-1a2b3c"
_S2_CANARY_NEW = "S2-CANARY-NEW-9z8y7x"


@pytest.fixture
def swid_canary(request) -> str:
    """A swid canary unique to the test that asks for it.

    Derived from the test name so a leak names its own source, and distinct per
    test so `UNIQUE (tenant_id, swid)` admits every one of them.
    """
    tag = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name).strip("-")[:48]
    return f"SWID-CANARY-{tag}-7f3a9e21"


def _assert_no_canaries(text: str, *canaries: str) -> None:
    """Inspect the complete raw response/log text, not just parsed top-level keys."""
    for canary in canaries:
        assert canary not in text, f"credential canary {canary!r} leaked into: {text!r}"


def test_discovery_response_and_logs_never_expose_account_credentials(swid_canary, monkeypatch, caplog):
    import logging

    from api.routers import leagues
    from api.services.discovery import DiscoveredLeague

    aid = client.post(
        "/api/accounts",
        json={"label": "DiscoveryCanary", "swid": swid_canary, "espn_s2": _S2_CANARY},
    ).json()["id"]
    stored_swid = f"{{{swid_canary.upper()}}}"

    def fake_discover(cookies, season):
        assert cookies.swid == stored_swid
        assert cookies.espn_s2 == _S2_CANARY
        assert season == 2026
        return [DiscoveredLeague("701", "Safe League", 2026, 4)]

    monkeypatch.setattr(leagues, "discover_leagues", fake_discover)
    with caplog.at_level(logging.DEBUG):
        response = client.get(f"/api/leagues/discover/{aid}")

    assert response.status_code == 200
    assert response.json() == [
        {"espn_league_id": "701", "name": "Safe League", "season": 2026, "team_id": 4}
    ]
    _assert_no_canaries(response.text, swid_canary, stored_swid, _S2_CANARY)
    _assert_no_canaries(caplog.text, swid_canary, stored_swid, _S2_CANARY)


def test_discovery_expired_session_sets_reauth_without_leaking(swid_canary, monkeypatch):
    from api.db import SessionLocal
    from api.models import Account
    from api.routers import leagues
    from api.services.discovery import DiscoveryAuthError

    aid = client.post(
        "/api/accounts",
        json={"label": "DiscoveryExpired", "swid": swid_canary, "espn_s2": _S2_CANARY},
    ).json()["id"]
    stored_swid = f"{{{swid_canary.upper()}}}"

    def fake_discover(cookies, season):
        raise DiscoveryAuthError("ESPN session expired")

    monkeypatch.setattr(leagues, "discover_leagues", fake_discover)
    response = client.get(f"/api/leagues/discover/{aid}")

    assert response.status_code == 401
    assert response.json() == {
        "detail": "ESPN session expired; re-authenticate this account"
    }
    _assert_no_canaries(response.text, swid_canary, stored_swid, _S2_CANARY)
    with SessionLocal() as session:
        assert session.get(Account, aid).status == "needs_reauth"


def test_add_account_success_has_no_credential_keys_or_values(swid_canary):
    r = client.post(
        "/api/accounts",
        json={"label": "Canary1", "swid": swid_canary, "espn_s2": _S2_CANARY},
    )
    assert r.status_code == 201
    body = r.json()
    assert "swid" not in body and "espn_s2" not in body
    assert set(body) == {"id", "label", "status", "created_at"}
    _assert_no_canaries(r.text, swid_canary, _S2_CANARY)


def test_list_accounts_never_exposes_stored_or_encrypted_credentials(swid_canary):
    from api.db import SessionLocal
    from api.models import Account

    aid = client.post(
        "/api/accounts",
        json={"label": "Canary2", "swid": swid_canary, "espn_s2": _S2_CANARY},
    ).json()["id"]
    with SessionLocal() as s:
        acct = s.get(Account, aid)
        stored_swid = acct.swid  # braced/normalized form persisted server-side
        encrypted_s2 = acct.espn_s2_encrypted  # Fernet ciphertext at rest

    r = client.get("/api/accounts")
    assert r.status_code == 200
    for row in r.json():
        assert "swid" not in row and "espn_s2" not in row
    # No plaintext canary, no stored SWID, and no encrypted blob in the raw text.
    _assert_no_canaries(r.text, swid_canary, _S2_CANARY, stored_swid, encrypted_s2)


def test_reauth_success_has_no_old_or_new_credentials(swid_canary):
    aid = client.post(
        "/api/accounts",
        json={"label": "Canary3", "swid": swid_canary, "espn_s2": _S2_CANARY_OLD},
    ).json()["id"]
    r = client.post(
        f"/api/accounts/{aid}/reauth",
        json={"swid": swid_canary, "espn_s2": _S2_CANARY_NEW},
    )
    assert r.status_code == 200
    body = r.json()
    assert "swid" not in body and "espn_s2" not in body
    _assert_no_canaries(r.text, swid_canary, _S2_CANARY_OLD, _S2_CANARY_NEW)


def test_add_account_missing_label_does_not_echo_credentials(swid_canary):
    r = client.post(
        "/api/accounts", json={"swid": swid_canary, "espn_s2": _S2_CANARY}
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid account request"}
    # No submitted values and no sensitive field names in the sanitized body.
    _assert_no_canaries(r.text, swid_canary, _S2_CANARY)
    assert "swid" not in r.text and "espn_s2" not in r.text


def test_add_account_invalid_empty_credential_fields_does_not_echo():
    # Empty swid violates min_length=1 → RequestValidationError before the router.
    r = client.post(
        "/api/accounts", json={"label": "L", "swid": "", "espn_s2": _S2_CANARY}
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid account request"}
    _assert_no_canaries(r.text, _S2_CANARY)


def test_reauth_missing_fields_does_not_echo_credentials(swid_canary):
    aid = client.post(
        "/api/accounts", json={"label": "Canary4", "swid": "{Z-1}", "espn_s2": "s2"}
    ).json()["id"]
    r = client.post(f"/api/accounts/{aid}/reauth", json={"swid": swid_canary})
    assert r.status_code == 422
    assert r.json() == {"detail": "invalid account request"}
    _assert_no_canaries(r.text, swid_canary)
    assert "espn_s2" not in r.text


def test_empty_normalized_swid_returns_safe_wording_no_value():
    # Router-generated 400: neutral wording, never names SWID or echoes the value.
    r = client.post(
        "/api/accounts", json={"label": "L", "swid": "{}", "espn_s2": _S2_CANARY}
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "account identifier looks empty after normalization"}
    assert "SWID" not in r.text and "swid" not in r.text
    _assert_no_canaries(r.text, _S2_CANARY)


def test_reauth_empty_normalized_swid_returns_safe_wording_no_value():
    aid = client.post(
        "/api/accounts", json={"label": "Canary5", "swid": "{Z-2}", "espn_s2": "s2"}
    ).json()["id"]
    r = client.post(
        f"/api/accounts/{aid}/reauth", json={"swid": "{}", "espn_s2": _S2_CANARY}
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "account identifier looks empty after normalization"}
    assert "SWID" not in r.text and "swid" not in r.text
    _assert_no_canaries(r.text, _S2_CANARY)


def test_reauth_unknown_account_is_safe_404(swid_canary):
    r = client.post(
        "/api/accounts/999999/reauth",
        json={"swid": swid_canary, "espn_s2": _S2_CANARY},
    )
    assert r.status_code == 404
    _assert_no_canaries(r.text, swid_canary, _S2_CANARY)


def test_account_requests_never_emit_credentials_to_logs(swid_canary, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/accounts",
            json={"label": "LogCanary", "swid": swid_canary, "espn_s2": _S2_CANARY},
        )
        client.post(  # malformed: triggers the sanitizing validation handler
            "/api/accounts", json={"swid": swid_canary, "espn_s2": _S2_CANARY}
        )
        client.post(
            "/api/accounts/999999/reauth",
            json={"swid": swid_canary, "espn_s2": _S2_CANARY},
        )
    _assert_no_canaries(caplog.text, swid_canary, _S2_CANARY)


def test_settings_defaults_api_host_to_localhost(monkeypatch):
    """A fresh Settings with no .env / API_HOST override binds localhost only.

    Isolated from the developer's real environment and .env so the default —
    not a local override — is what is asserted.
    """
    from api.config import Settings

    monkeypatch.delenv("API_HOST", raising=False)
    settings = Settings(_env_file=None)  # ignore repo .env entirely
    assert settings.api_host == "127.0.0.1"

# ---- duplicate credentials (revision 0015: UNIQUE (tenant_id, swid)) --------


def test_a_duplicate_swid_is_refused_with_409_and_leaks_nothing(swid_canary, caplog):
    """What the constraint made the API responsible for answering.

    Before this, a duplicate reached `session.commit()` unguarded and raised
    `IntegrityError` out of the handler -- a 500 whose exception string carries
    the bound parameters, i.e. the swid and the encrypted espn_s2, and which an
    ASGI server logs with its traceback. Measured before the fix: the swid was
    in the exception text.
    """
    import logging

    first = client.post(
        "/api/accounts",
        json={"label": "Original", "swid": swid_canary, "espn_s2": _S2_CANARY},
    )
    assert first.status_code == 201

    with caplog.at_level(logging.DEBUG):
        second = client.post(
            "/api/accounts",
            json={"label": "Duplicate", "swid": swid_canary, "espn_s2": _S2_CANARY},
        )
    assert second.status_code == 409
    assert second.json()["detail"] == "this account is already linked"
    stored = f"{{{swid_canary.upper()}}}"
    _assert_no_canaries(second.text, swid_canary, stored, _S2_CANARY)
    _assert_no_canaries(caplog.text, swid_canary, stored, _S2_CANARY)


def test_the_integrity_branch_answers_409_without_the_pre_check(swid_canary, caplog, monkeypatch):
    """The pre-check is racy; the constraint is not. So the `IntegrityError`
    branch has to answer on its own, and this reaches it by disabling the
    check -- which is what two simultaneous requests do by losing the race.
    """
    import logging

    from api.routers import accounts as accounts_router

    assert (
        client.post(
            "/api/accounts",
            json={"label": "First", "swid": swid_canary, "espn_s2": _S2_CANARY},
        ).status_code
        == 201
    )
    monkeypatch.setattr(
        accounts_router, "_refuse_duplicate_swid", lambda *a, **k: None
    )
    with caplog.at_level(logging.DEBUG):
        racing = client.post(
            "/api/accounts",
            json={"label": "Racing", "swid": swid_canary, "espn_s2": _S2_CANARY},
        )
    assert racing.status_code == 409, "the constraint must answer even unchecked"
    stored = f"{{{swid_canary.upper()}}}"
    _assert_no_canaries(racing.text, swid_canary, stored, _S2_CANARY)
    _assert_no_canaries(caplog.text, swid_canary, stored, _S2_CANARY)


def test_reauth_to_a_swid_another_account_holds_is_refused(swid_canary):
    taken = client.post(
        "/api/accounts",
        json={"label": "Taken", "swid": swid_canary, "espn_s2": _S2_CANARY},
    )
    assert taken.status_code == 201
    other = client.post(
        "/api/accounts",
        json={"label": "Other", "swid": swid_canary + "-OTHER", "espn_s2": _S2_CANARY},
    ).json()["id"]

    collide = client.post(
        f"/api/accounts/{other}/reauth",
        json={"swid": swid_canary, "espn_s2": _S2_CANARY},
    )
    assert collide.status_code == 409
    _assert_no_canaries(collide.text, swid_canary, _S2_CANARY)


def test_reauth_with_the_accounts_own_swid_is_still_allowed(swid_canary):
    """The ordinary case the duplicate guard must not break: a user whose
    session expired pastes the same swid with a fresh espn_s2. The row already
    holds that swid, so a guard that did not exclude the row being updated
    would refuse every re-auth -- which is the whole feature."""
    account_id = client.post(
        "/api/accounts",
        json={"label": "Expired", "swid": swid_canary, "espn_s2": _S2_CANARY},
    ).json()["id"]
    again = client.post(
        f"/api/accounts/{account_id}/reauth",
        json={"swid": swid_canary, "espn_s2": "fresh-secret"},
    )
    assert again.status_code == 200
    assert again.json()["status"] == "active"
