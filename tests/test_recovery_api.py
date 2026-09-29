"""Phase 30 local API controls and service-boundary admission tests."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services.recovery import RecoveryAdmissionError

client = TestClient(app, client=("127.0.0.1", 50_000))

_ALLOWED = {
    "Host": "127.0.0.1:8000",
    "Origin": "http://127.0.0.1:5173",
    "X-ESPN-Edge-Action": "backup",
}


def test_recovery_status_is_db_free_and_has_exact_public_shape(monkeypatch):
    from api.routers import recovery
    from api.services.recovery import RecoveryStatus

    monkeypatch.setattr(
        recovery,
        "recovery_status",
        lambda: RecoveryStatus(
            required=True,
            supported_topology=True,
            configured=True,
            target_available=False,
            state="target_unavailable",
            last_coverage_at="2026-08-13T00:00:00+00:00",
            last_snapshot_at="2026-08-13T00:00:00+00:00",
            age_seconds=60,
            stale_after_seconds=86400,
            last_result_code="recovery_target_unavailable",
            artifact_bytes=1234,
            format_version=1,
            schema_fingerprint_short="0123456789ab",
            retention_configured=True,
            retention_enforced=True,
        ),
    )
    response = client.get("/api/recovery/status")
    assert response.status_code == 200
    assert set(response.json()) == {
        "required",
        "supported_topology",
        "configured",
        "target_available",
        "state",
        "last_coverage_at",
        "last_snapshot_at",
        "age_seconds",
        "stale_after_seconds",
        "last_result_code",
        "artifact_bytes",
        "format_version",
        "schema_fingerprint_short",
        "retention_configured",
        "retention_enforced",
    }
    text = response.text.lower()
    assert "repository" not in text and "password" not in text and "path" not in text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {**_ALLOWED, "Host": "attacker.example"},
        {**_ALLOWED, "Origin": "https://attacker.example"},
        {**_ALLOWED, "X-Forwarded-Host": "attacker.example"},
        {**_ALLOWED, "Forwarded": "host=attacker.example"},
        {key: value for key, value in _ALLOWED.items() if key != "X-ESPN-Edge-Action"},
    ],
)
def test_backup_rejects_cross_origin_rebinding_and_simple_posts_before_work(monkeypatch, headers):
    from api.routers import recovery

    calls = []
    monkeypatch.setattr(recovery, "reserve_api_trigger", lambda: calls.append("reserve"))
    monkeypatch.setattr(recovery, "run_backup", lambda _reason: calls.append("backup"))
    response = client.post("/api/recovery/backup?reason=manual", headers=headers)
    assert response.status_code == 403
    assert calls == []


def test_backup_accepts_exact_local_action(monkeypatch):
    from api.routers import recovery

    calls = []
    monkeypatch.setattr(recovery, "reserve_api_trigger", lambda: calls.append("reserve"))
    monkeypatch.setattr(
        recovery,
        "run_backup",
        lambda reason: {
            "result_code": "recovery_ok",
            "artifact_bytes": 42,
            "snapshot_created": reason == "manual",
        },
    )
    response = client.post("/api/recovery/backup?reason=manual", headers=_ALLOWED)
    assert response.status_code == 200
    assert calls == ["reserve"]
    assert response.json() == {
        "result_code": "recovery_ok",
        "artifact_bytes": 42,
        "snapshot_created": True,
    }


def test_backup_rejects_non_loopback_socket_peer_before_work(monkeypatch):
    from api.routers import recovery

    calls = []
    monkeypatch.setattr(recovery, "reserve_api_trigger", lambda: calls.append("reserve"))
    remote = TestClient(app, client=("192.0.2.10", 50_000))
    response = remote.post("/api/recovery/backup?reason=manual", headers=_ALLOWED)
    assert response.status_code == 403
    assert calls == []


def test_api_and_cli_share_lock_and_sigkill_releases_it(tmp_path, monkeypatch):
    from api.routers import recovery
    from api.services.recovery import recovery_lock

    lock = tmp_path / "shared.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys; "
                "fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600); "
                "fcntl.flock(fd,fcntl.LOCK_EX); print('locked',flush=True); "
                "sys.stdin.read()"
            ),
            str(lock),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "locked"

    def reserve():
        with recovery_lock(lock):
            pass

    monkeypatch.setattr(recovery, "reserve_api_trigger", reserve)
    monkeypatch.setattr(recovery, "run_backup", lambda _reason: pytest.fail("must not start"))
    response = client.post("/api/recovery/backup?reason=manual", headers=_ALLOWED)
    assert response.status_code == 409
    holder.kill()
    holder.wait(timeout=5)
    with recovery_lock(lock):
        pass


def test_uncached_ai_is_gated_before_client_call(db_session, monkeypatch):
    from api.services.ai import AiService

    class ClientSpy:
        calls = 0

        def complete_json(self, **_kwargs):
            self.calls += 1
            return {}

    def blocked():
        raise RecoveryAdmissionError(
            "recovery_point_stale", "A current verified recovery point is required."
        )

    spy = ClientSpy()
    monkeypatch.setattr("api.services.ai.assert_recovery_write_allowed", blocked)
    with pytest.raises(RecoveryAdmissionError):
        AiService(db_session, client=spy).generate(
            kind="league_brief",
            scope="league",
            league_id=1,
            model="offline-fake",
            facts={"at": datetime.now(UTC)},
        )
    assert spy.calls == 0


def test_opportunity_and_ffc_gate_before_client_construction(db_session, monkeypatch):
    from api.services.ffc_adp import refresh_ffc_adp
    from api.services.opportunity import refresh_opportunity

    calls = []

    def blocked():
        calls.append("gate")
        raise RecoveryAdmissionError(
            "recovery_point_stale", "A current verified recovery point is required."
        )

    monkeypatch.setattr("api.services.opportunity.assert_recovery_write_allowed", blocked)
    monkeypatch.setattr("api.services.ffc_adp.assert_recovery_write_allowed", blocked)
    with pytest.raises(RecoveryAdmissionError):
        refresh_opportunity(db_session, 2026)
    with pytest.raises(RecoveryAdmissionError):
        refresh_ffc_adp(db_session, [])
    assert calls == ["gate", "gate"]
