"""Phase 30 offline application-level restore closure."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pty
import select as select_module
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from api.config import Settings
from api.crypto import encrypt
from api.models import (
    Account,
    AdpSnapshot,
    AiReport,
    League,
    MetricSnapshot,
    OpportunityImport,
)
from api.recovery import (
    _canonical_digest,
    _read_application_payloads,
    _stable_restore_read_projection,
)
from api.routers import leagues as league_router
from api.services import recovery as recovery_module
from api.services.espn import EspnReauthRequired, cookies_for_account
from api.services.recovery import (
    RECOVERY_TAG,
    BoundedSubprocessRunner,
    ProcessResult,
    RecoveryError,
    RepositoryPin,
    ResticRepository,
    StorageIdentity,
    apply_retention,
    build_logical_bundle,
    recovery_lock,
    reserve_api_trigger,
    restore_bundle_to_scratch,
    restore_drill,
    run_backup,
)
from api.services.sync import SyncService
from api.tenancy import current_tenant_id

from .conftest import FakeEspn


class _BreakGlassRepository:
    def __init__(self, bundle: bytes):
        self.bundle = bundle

    def validate_tool(self):
        return None

    def validate_topology(self, _source):
        return (
            StorageIdentity("source", "internal", "1:1"),
            StorageIdentity("target", "external", "2:2"),
        )

    @contextmanager
    def pin_break_glass_repository(self, _source, *, lock_fd):
        assert lock_fd >= 0
        yield object()

    def break_glass_check(self, *, pin):
        assert pin is not None
        return None

    def break_glass_snapshots(self, *, pin):
        assert pin is not None
        return [
            {
                "id": "a" * 64,
                "tags": [RECOVERY_TAG],
                "time": "2026-08-13T00:00:00+00:00",
            }
        ]

    def break_glass_bundle(self, _snapshot, *, pin):
        assert pin is not None
        return self.bundle


def test_bounded_runner_descriptor_submission_ignores_lexical_replacement(tmp_path):
    original = b"synthetic descriptor-bound plist"
    replacement = b"synthetic replacement plist"
    target = tmp_path / "runner.plist"
    target.write_bytes(original)
    descriptor = os.open(target, os.O_RDONLY)
    try:
        target.unlink()
        target.write_bytes(replacement)
        result = BoundedSubprocessRunner().run(
            ["/bin/cat", f"/dev/fd/{descriptor}"],
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            pass_fds=(descriptor,),
            max_output=1_024,
            timeout_seconds=5.0,
        )
    finally:
        os.close(descriptor)

    assert result.returncode == 0
    assert result.stdout == original
    assert result.stderr == b""
    assert target.read_bytes() == replacement


def _restore_read_payload(
    *,
    age_hours: float,
    stale: bool,
    fetched_at: str = "2026-08-16T00:00:00Z",
    stored_rows: int = 3,
) -> list:
    return [
        [{"portfolio": "stable"}],
        {"exposure": "stable"},
        {"strategies": "stable"},
        {
            "source": {
                "fetched_at": fetched_at,
                "age_hours": age_hours,
                "stale": stale,
                "stored_rows": stored_rows,
            },
            "charts": [{"id": "synthetic-chart"}],
        },
    ]


def test_restore_read_projection_ignores_only_clock_rounding_and_ttl_boundary():
    before_rounding = _restore_read_payload(age_hours=23.94, stale=False)
    after_rounding = _restore_read_payload(age_hours=24.04, stale=False)
    before_ttl = _restore_read_payload(age_hours=24.0, stale=False)
    after_ttl = _restore_read_payload(age_hours=24.0, stale=True)
    assert _stable_restore_read_projection(
        before_rounding
    ) == _stable_restore_read_projection(after_rounding)
    assert _stable_restore_read_projection(before_ttl) == _stable_restore_read_projection(
        after_ttl
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"fetched_at": "2026-08-16T00:00:01Z"},
        {"stored_rows": 4},
    ],
    ids=("persisted-fetched-at", "nonvolatile-field"),
)
def test_restore_read_projection_detects_persisted_or_nonvolatile_mismatch(changed):
    expected = _restore_read_payload(age_hours=24.0, stale=False)
    actual = _restore_read_payload(age_hours=24.1, stale=True, **changed)
    assert not hmac.compare_digest(
        _canonical_digest(_stable_restore_read_projection(expected)),
        _canonical_digest(_stable_restore_read_projection(actual)),
    )


def test_restore_read_projection_requires_persisted_clock_input():
    payload = _restore_read_payload(age_hours=24.0, stale=False)
    payload[3]["source"].pop("fetched_at")
    with pytest.raises(RecoveryError) as caught:
        _stable_restore_read_projection(payload)
    assert getattr(caught.value, "code", None) == "recovery_bundle_invalid"


def _restore_settings(tmp_path: Path, source: Path) -> Settings:
    return Settings(
        db_path=str(source),
        recovery_required=False,
        recovery_repository=str(tmp_path / "fake-repository"),
        recovery_restic_path=str(tmp_path / "fake-restic"),
        recovery_restic_sha256="0" * 64,
        recovery_credential_command=str(tmp_path / "fake-key-command"),
        recovery_state_path=str(tmp_path / "state.json"),
        recovery_lock_path=str(tmp_path / "state.lock"),
        recovery_scratch_path=str(tmp_path / "scratch"),
    )


@pytest.mark.parametrize("swap_mode", ["persistent", "aba"])
def test_real_restic_uses_descriptor_bound_repository_cwd(
    tmp_path, monkeypatch, swap_mode
):
    binary = Path.home() / ".local" / "bin" / "restic-0.19.1"
    approved_digest = "06582569ff2f10e1935a6f12187c76db02f0bae99e6e54098f2cdc374766d768"
    if not binary.is_file() or binary.is_symlink():
        pytest.skip("approved pinned Restic is not installed")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != approved_digest:
        pytest.skip("installed Restic does not match the approved digest")

    source = tmp_path / "synthetic-source"
    source.write_bytes(b"synthetic")
    repository_path = tmp_path / "synthetic-repository"
    repository_path.mkdir()
    held_path = tmp_path / "synthetic-repository-held"
    credential_command = tmp_path / "synthetic-credential-command"
    credential_command.write_text(
        "#!/bin/sh\nprintf 'synthetic-password\\n'\n", encoding="utf-8"
    )
    credential_command.chmod(0o700)
    cache_path = tmp_path / "synthetic-restic-cache"
    cache_path.mkdir()
    minimal_env = recovery_module._minimal_env()
    monkeypatch.setattr(
        recovery_module,
        "_minimal_env",
        lambda: {**minimal_env, "RESTIC_CACHE_DIR": str(cache_path)},
    )
    settings = Settings(
        db_path=str(source),
        recovery_required=False,
        recovery_repository=str(repository_path),
        recovery_restic_path=str(binary),
        recovery_restic_sha256=approved_digest,
        recovery_restic_version="0.19.1",
        recovery_credential_command=str(credential_command),
        recovery_state_path=str(tmp_path / "state.json"),
        recovery_lock_path=str(tmp_path / "state.lock"),
        recovery_scratch_path=str(tmp_path / "scratch"),
    )

    class DescriptorSwapRunner(BoundedSubprocessRunner):
        def __init__(self):
            self.swapped = False
            self.restic_fds = []

        def run(self, argv, **kwargs):
            cwd_fd = kwargs.get("cwd_fd")
            if cwd_fd is not None:
                passed = tuple(kwargs["pass_fds"])
                assert passed[0] == cwd_fd
                assert len(passed) == 3
                self.restic_fds.append(passed)
            if "snapshots" not in argv:
                return super().run(argv, **kwargs)
            repository_path.rename(held_path)
            repository_path.mkdir()
            if swap_mode == "aba":
                repository_path.rmdir()
                held_path.rename(repository_path)
            try:
                result = super().run(argv, **kwargs)
                if swap_mode == "persistent":
                    assert not any(repository_path.iterdir())
                self.swapped = True
                return result
            finally:
                if held_path.exists():
                    repository_path.rmdir()
                    held_path.rename(repository_path)

    runner = DescriptorSwapRunner()
    repository = ResticRepository(settings, runner=runner)
    repository.validate_tool()
    repository_fd = os.open(repository_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        repository_stat = os.fstat(repository_fd)
        with recovery_lock(settings.recovery_lock_file) as lock_fd:
            pin = RepositoryPin(
                fd=repository_fd,
                repository_path=".",
                topology=(
                    StorageIdentity("synthetic-source", "internal"),
                    StorageIdentity("synthetic-target", "external"),
                ),
                repository_id="",
                device=repository_stat.st_dev,
                inode=repository_stat.st_ino,
                lock_fd=lock_fd,
            )
            repository._checked(
                "init",
                pin=pin,
                timeout_seconds=recovery_module._RESTIC_INIT_TIMEOUT_SECONDS,
            )
            repository_id = repository.repository_id(pin=pin)
            assert len(repository_id) == 64
            assert repository.snapshots(pin=pin) == []
            repository.check(pin=pin)
    finally:
        os.close(repository_fd)

    assert runner.swapped is True
    assert len(runner.restic_fds) == 4
    assert all(len(set(descriptors)) == 3 for descriptors in runner.restic_fds)


_APP_READ_SCRIPT = r"""
import json,sys
from fastapi.testclient import TestClient
from api.main import app
paths=[
  "/api/portfolio",
  "/api/portfolio/exposure?scope=me&season=2025",
  "/api/portfolio/strategies?season=2025",
  "/api/portfolio/opportunity/charts?view=all&position=WR&season=2025",
]
with TestClient(app, client=("127.0.0.1", 50000)) as client:
  values=[]
  for path in paths:
    response=client.get(path)
    assert response.status_code == 200, (path,response.status_code,response.text[:200])
    values.append(response.json())
print(json.dumps(values,sort_keys=True,separators=(",",":")))
"""


def _app_reads(database: Path) -> list:
    result = subprocess.run(
        [sys.executable, "-c", _APP_READ_SCRIPT],
        cwd=Path(__file__).parent.parent,
        env={
            **os.environ,
            "DB_PATH": str(database),
            "SEASON": "2025",
            "RECOVERY_REQUIRED": "false",
            "ANTHROPIC_API_KEY": "",
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _seed_source(db_session, league_fixture, players_fixture) -> tuple[Path, int]:
    account = Account(
        label="synthetic-operator",
        swid="{AAAA-1111}",
        espn_s2_encrypted=encrypt("synthetic-cookie"),
        status="active",
    )
    db_session.add(account)
    db_session.flush()
    league = League(tenant_id=current_tenant_id(db_session), 
        espn_league_id="phase30-synthetic",
        season=2025,
        account_id=account.id,
        lifecycle="pre_draft",
        is_public=False,
    )
    db_session.add(league)
    db_session.flush()
    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(league)
    db_session.add_all(
        [
            AiReport(
                league_id=league.id,
                scope="league",
                kind="synthetic",
                input_hash="oracle-input",
                model="offline-fake",
                content_json={"summary": "synthetic-only"},
            ),
            OpportunityImport(
                id="phase30-synthetic-import",
                season=2025,
                state="ready",
                started_at=datetime(2026, 8, 13, tzinfo=UTC),
                completed_at=datetime(2026, 8, 13, 0, 1, tzinfo=UTC),
                input_rows=3,
                stored_rows=3,
                matched_players=2,
                unmatched_players=1,
                details_json={"diagnostic": "synthetic"},
            ),
            AdpSnapshot(
                source="ffc",
                pulled_at=datetime(2026, 8, 13, tzinfo=UTC),
                format="ppr",
                teams=12,
                payload_json={"players": [{"name": "Synthetic Player"}]},
            ),
        ]
    )
    db_session.commit()
    return Path(str(db_session.get_bind().url.database)), account.id


def test_restore_read_projection_detects_persisted_fetched_at_drift(
    tmp_path, db_session, league_fixture, players_fixture
):
    source, _account_id = _seed_source(db_session, league_fixture, players_fixture)
    bundle, _manifest = build_logical_bundle(source)
    restored = tmp_path / "persisted-drift.db"
    restore_bundle_to_scratch(bundle, restored)
    restored_engine = create_engine(f"sqlite:///{restored}")
    try:
        with Session(restored_engine) as session:
            persisted = session.scalar(
                select(OpportunityImport).where(
                    OpportunityImport.id == "phase30-synthetic-import"
                )
            )
            assert persisted is not None and persisted.completed_at is not None
            persisted.completed_at += timedelta(seconds=1)
            session.commit()
    finally:
        restored_engine.dispose()
    assert not hmac.compare_digest(
        _canonical_digest(_read_application_payloads(source)),
        _canonical_digest(_read_application_payloads(restored)),
    )


def test_offline_restore_starts_app_matches_reads_and_enforces_reauth(
    tmp_path, db_session, league_fixture, players_fixture, monkeypatch
):
    source, account_id = _seed_source(db_session, league_fixture, players_fixture)
    source_counts = {
        model.__tablename__: db_session.scalar(select(func.count()).select_from(model))
        for model in (AiReport, MetricSnapshot, OpportunityImport, AdpSnapshot)
    }
    assert source_counts["ai_reports"] == 1
    assert source_counts["metric_snapshots"] > 0
    assert source_counts["opportunity_imports"] == 1
    assert source_counts["adp_snapshots"] == 1
    source_reads = _app_reads(source)
    bundle, _ = build_logical_bundle(source)
    monkeypatch.setattr("api.services.recovery.filevault_enabled", lambda _runner: True)
    operational = restore_drill(
        "latest",
        _restore_settings(tmp_path / "operational", source),
        repository=_BreakGlassRepository(bundle),
    )
    assert all(operational["application_evidence"].values())
    restored = tmp_path / "restored.db"
    restore_bundle_to_scratch(bundle, restored)
    assert _stable_restore_read_projection(
        _app_reads(restored)
    ) == _stable_restore_read_projection(source_reads)

    restored_engine = create_engine(f"sqlite:///{restored}")
    with Session(restored_engine) as session:
        account = session.get(Account, account_id)
        league = session.scalar(select(League).where(League.account_id == account_id))
        assert account is not None and league is not None
        assert account.label == "synthetic-operator"
        assert account.status == "needs_reauth"

        monkeypatch.setattr(
            "api.crypto.decrypt", lambda _value: pytest.fail("decrypt must not run")
        )
        with pytest.raises(EspnReauthRequired):
            cookies_for_account(account)

        provider_calls = []

        class ProviderSpy:
            def fetch_views(self, *_args, **_kwargs):
                provider_calls.append("fetch")
                raise AssertionError("provider must not run")

        result = SyncService(session, espn=ProviderSpy()).sync_league(league)
        assert result["needs_reauth"] is True
        assert provider_calls == []

        monkeypatch.setattr(
            league_router,
            "discover_leagues",
            lambda *_args, **_kwargs: pytest.fail("discovery provider must not run"),
        )
        with pytest.raises(Exception) as discovery_error:
            league_router.discover(account_id, session)
        assert getattr(discovery_error.value, "status_code", None) == 401

        monkeypatch.setattr(
            "api.services.sync.EspnService",
            lambda **_kwargs: pytest.fail("production provider must not construct"),
        )
        summary_error = league_router.sync_league(league.id, session)
        assert summary_error.needs_reauth is True

        from api import verify

        @contextmanager
        def restored_scope():
            yield session

        monkeypatch.setattr(verify, "init_db", lambda: None)
        monkeypatch.setattr(verify, "session_scope", restored_scope)
        monkeypatch.setattr(verify, "_load_cookies", lambda: None)
        assert verify.main(["--league", league.espn_league_id, "--season", "2025"]) == 3

        restored_counts = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (AiReport, MetricSnapshot, OpportunityImport, AdpSnapshot)
        }
        assert restored_counts == source_counts

        # Explicit synthetic reauthentication replaces both sentinels; injected
        # FakeEspn can then run without constructing a production client.
        monkeypatch.undo()
        account.swid = "{AAAA-1111}"
        account.espn_s2_encrypted = encrypt("synthetic-cookie-reauthed")
        account.status = "active"
        session.flush()
        post = SyncService(
            session, espn=FakeEspn(league_fixture, players_fixture)
        ).sync_league(league)
        assert post.get("needs_reauth") is not True

        assert session.scalar(select(func.count()).select_from(MetricSnapshot)) > source_counts[
            "metric_snapshots"
        ]
    restored_engine.dispose()


@pytest.mark.parametrize(
    "interrupt_payload",
    [None, b"\x1c", b"\x1a"],
    ids=("success", "ctrl-backslash", "ctrl-z"),
)
def test_pty_restore_prompt_releases_lock_and_scratch_after_terminal_input(
    tmp_path,
    db_session,
    league_fixture,
    players_fixture,
    monkeypatch,
    interrupt_payload,
):
    source, _account_id = _seed_source(db_session, league_fixture, players_fixture)
    bundle, _manifest = build_logical_bundle(source)
    settings = _restore_settings(tmp_path / "pty-restore", source)
    repository_path = Path(settings.recovery_repository)
    repository_path.mkdir(parents=True)
    binary = Path(settings.recovery_restic_path)
    binary.write_bytes(b"synthetic-restic-authority")
    binary.chmod(0o700)
    settings = settings.model_copy(
        update={
            "recovery_restic_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()
        }
    )
    synthetic_secret = b"synthetic-pty-break-glass"

    class Runner:
        def __init__(self):
            self.calls = 0

        def run(self, argv, **kwargs):
            if argv == [str(binary), "version"]:
                return ProcessResult(
                    0,
                    f"restic {settings.recovery_restic_version}\n".encode(),
                    b"",
                )
            self.calls += 1
            assert kwargs["interactive"] is False
            assert "--password-command" not in argv
            assert str(settings.recovery_credential_command) not in argv
            password_fd = kwargs["pass_fds"][-1]
            assert os.read(password_fd, 4096) == synthetic_secret
            assert os.read(password_fd, 1) == b""
            if "snapshots" in argv:
                return ProcessResult(
                    0,
                    json.dumps(
                        [
                            {
                                "id": "a" * 64,
                                "tags": [RECOVERY_TAG],
                                "time": "2026-08-13T00:00:00+00:00",
                            }
                        ]
                    ).encode(),
                    b"",
                )
            if "dump" in argv:
                return ProcessResult(0, bundle, b"")
            if "check" in argv:
                return ProcessResult(0, b"", b"")
            raise AssertionError(f"unexpected synthetic restic call: {argv!r}")

    source_stat = source.stat()
    repository_stat = repository_path.stat()

    class StaticTopology:
        def resolve(self, path):
            current = Path(path).stat()
            if Path(path) == repository_path:
                assert (current.st_dev, current.st_ino) == (
                    repository_stat.st_dev,
                    repository_stat.st_ino,
                )
                return StorageIdentity(
                    "synthetic-target", "external", f"{current.st_dev}:{current.st_ino}"
                )
            assert (current.st_dev, current.st_ino) == (
                source_stat.st_dev,
                source_stat.st_ino,
            )
            return StorageIdentity(
                "synthetic-source", "internal", f"{current.st_dev}:{current.st_ino}"
            )

    runner = Runner()
    repository = ResticRepository(settings, runner=runner, topology=StaticTopology())
    monkeypatch.setattr(recovery_module, "filevault_enabled", lambda _runner: True)

    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - parent validates the complete PTY transcript
        original_terminal = recovery_module.termios.tcgetattr(0)
        original_handlers = {
            terminal_signal: signal.getsignal(terminal_signal)
            for terminal_signal in (signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP)
        }
        original_open = recovery_module.os.open

        def controlling_tty_open(path, flags, *args, **kwargs):
            if path == "/dev/tty":
                return os.dup(0)
            return original_open(path, flags, *args, **kwargs)

        recovery_module.os.open = controlling_tty_open
        try:
            result = restore_drill("latest", settings, repository=repository)
            payload = {"restic_calls": runner.calls, "result": result}
        except BaseException as exc:
            payload = {"error": recovery_module.secret_free_error(exc)}
        current_terminal = recovery_module.termios.tcgetattr(0)
        payload["terminal_restored"] = recovery_module._tty_state_restored(
            current_terminal, original_terminal
        )
        payload["handlers_restored"] = all(
            signal.getsignal(terminal_signal) == original_handler
            for terminal_signal, original_handler in original_handlers.items()
        )
        os.set_blocking(0, False)
        try:
            payload["queued_bytes"] = len(os.read(0, 4096))
        except (BlockingIOError, OSError):
            payload["queued_bytes"] = 0
        try:
            with recovery_module.recovery_lock(
                settings.recovery_lock_file, blocking=False
            ):
                pass
            payload["lock_released"] = True
        except BaseException:
            payload["lock_released"] = False
        try:
            root, root_token = recovery_module._safe_scratch_root(
                settings.recovery_scratch_dir
            )
            retry = recovery_module._new_scratch_run(root, root_token)
            recovery_module._quarantine_scratch_run(root, retry.name, root_token)
            payload["scratch_retryable"] = not retry.exists()
        except BaseException:
            payload["scratch_retryable"] = False
        os.write(1, (json.dumps(payload, sort_keys=True) + "\n").encode())
        os._exit(0)

    transcript = bytearray()
    sent = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(master_fd, 64 * 1024)
                except OSError:
                    chunk = b""
                transcript.extend(chunk)
                if (
                    not sent
                    and recovery_module._BREAK_GLASS_PROMPT in transcript
                ):
                    os.write(
                        master_fd,
                        (
                            synthetic_secret + b"\n"
                            if interrupt_payload is None
                            else interrupt_payload
                        ),
                    )
                    sent = True
            waited, _status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            pytest.fail("synthetic PTY restore did not terminate")
    finally:
        os.close(master_fd)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert synthetic_secret not in transcript
    if interrupt_payload is not None:
        assert interrupt_payload not in transcript
    lines = [line.strip(b"\r") for line in transcript.splitlines() if line.startswith(b"{")]
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["terminal_restored"] is True
    assert result["handlers_restored"] is True
    assert result["queued_bytes"] == 0
    assert result["lock_released"] is True
    assert result["scratch_retryable"] is True
    if interrupt_payload is None:
        assert "error" not in result, result
        assert result["restic_calls"] == 3
        assert all(result["result"]["application_evidence"].values())
    else:
        assert result["error"] == {
            "code": "recovery_repository_error",
            "message": "Recovery credential is unavailable.",
        }
    assert {path.name for path in settings.recovery_scratch_dir.iterdir()} == {
        recovery_module._SCRATCH_ROOT_MARKER
    }


@pytest.mark.parametrize("operation", ["check", "snapshots", "dump"])
@pytest.mark.parametrize(
    "terminal_signal", recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
)
def test_pty_restore_signal_during_each_child_cleans_complete_credential_lifetime(
    tmp_path,
    db_session,
    league_fixture,
    players_fixture,
    monkeypatch,
    operation,
    terminal_signal,
):
    source, _account_id = _seed_source(db_session, league_fixture, players_fixture)
    bundle, _manifest = build_logical_bundle(source)
    work = tmp_path / f"signal-{operation}-{terminal_signal}"
    settings = _restore_settings(work, source)
    repository_path = Path(settings.recovery_repository)
    repository_path.mkdir(parents=True)
    bundle_path = work / "synthetic-bundle"
    bundle_path.write_bytes(bundle)
    binary = Path(settings.recovery_restic_path)
    binary.write_text(
        f"""#!{sys.executable}
import json
import os
import signal
import sys
import time

args = sys.argv[1:]
current = "check" if "check" in args else "snapshots" if "snapshots" in args else "dump"
if current == {operation!r}:
    parent = os.getppid()
    os.kill(parent, {int(terminal_signal)})
    os.kill(parent, {int(terminal_signal)})
    while True:
        time.sleep(1)
if current == "snapshots":
    print(json.dumps([{{
        "id": "a" * 64,
        "tags": [{RECOVERY_TAG!r}],
        "time": "2026-08-13T00:00:00+00:00",
    }}]))
elif current == "dump":
    with open({str(bundle_path)!r}, "rb") as handle:
        sys.stdout.buffer.write(handle.read())
""",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    settings = settings.model_copy(
        update={
            "recovery_restic_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()
        }
    )
    source_stat = source.stat()
    repository_stat = repository_path.stat()

    class StaticTopology:
        def resolve(self, path):
            current = Path(path).stat()
            if Path(path) == repository_path:
                assert (current.st_dev, current.st_ino) == (
                    repository_stat.st_dev,
                    repository_stat.st_ino,
                )
                return StorageIdentity(
                    "synthetic-target", "external", f"{current.st_dev}:{current.st_ino}"
                )
            assert (current.st_dev, current.st_ino) == (
                source_stat.st_dev,
                source_stat.st_ino,
            )
            return StorageIdentity(
                "synthetic-source", "internal", f"{current.st_dev}:{current.st_ino}"
            )

    repository = ResticRepository(
        settings,
        runner=BoundedSubprocessRunner(),
        topology=StaticTopology(),
    )
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    repository.validate_break_glass_tool = lambda _expected=None: authority
    monkeypatch.setattr(recovery_module, "filevault_enabled", lambda _runner: True)
    synthetic_secret = b"synthetic-lifetime-credential"

    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - parent validates the complete PTY transcript
        original_terminal = recovery_module.termios.tcgetattr(0)
        original_handlers = {
            guarded: signal.getsignal(guarded)
            for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
        }
        original_open = recovery_module.os.open

        def controlling_tty_open(path, flags, *args, **kwargs):
            if path == "/dev/tty":
                return os.dup(0)
            return original_open(path, flags, *args, **kwargs)

        recovery_module.os.open = controlling_tty_open
        captured_secrets = []
        original_prompt = recovery_module._read_break_glass_credential

        def captured_prompt():
            secret = original_prompt()
            captured_secrets.append(secret)
            return secret

        recovery_module._read_break_glass_credential = captured_prompt
        real_popen = recovery_module.subprocess.Popen
        restic_processes = []
        password_fds = []

        def recording_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            argv = list(args[0])
            if "--password-file" in argv:
                restic_processes.append(process)
                password_fds.append(kwargs["pass_fds"][-1])
            return process

        recovery_module.subprocess.Popen = recording_popen
        try:
            restore_drill("latest", settings, repository=repository)
            result = {"unexpected_success": True}
        except BaseException as exc:
            result = {"error": recovery_module.secret_free_error(exc)}
        current_terminal = recovery_module.termios.tcgetattr(0)
        result["terminal_restored"] = recovery_module._tty_state_restored(
            current_terminal, original_terminal
        )
        result["handlers_restored"] = all(
            signal.getsignal(guarded) == original_handler
            for guarded, original_handler in original_handlers.items()
        )
        result["secret_zeroized"] = bool(captured_secrets) and all(
            not value for value in captured_secrets[0]
        )
        result["password_fds_closed"] = True
        for descriptor in password_fds:
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            result["password_fds_closed"] = False
        result["children_reaped"] = bool(restic_processes) and all(
            child.poll() is not None for child in restic_processes
        )
        result["process_groups_gone"] = bool(restic_processes)
        for child in restic_processes:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            result["process_groups_gone"] = False
        try:
            with recovery_module.recovery_lock(
                settings.recovery_lock_file, blocking=False
            ):
                pass
            result["lock_released"] = True
        except BaseException:
            result["lock_released"] = False
        try:
            root, root_token = recovery_module._safe_scratch_root(
                settings.recovery_scratch_dir
            )
            retry = recovery_module._new_scratch_run(root, root_token)
            recovery_module._quarantine_scratch_run(root, retry.name, root_token)
            result["scratch_retryable"] = not retry.exists()
        except BaseException:
            result["scratch_retryable"] = False
        os.write(1, (json.dumps(result, sort_keys=True) + "\n").encode())
        os._exit(0)

    transcript = bytearray()
    sent = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(master_fd, 64 * 1024)
                except OSError:
                    chunk = b""
                transcript.extend(chunk)
                if not sent and recovery_module._BREAK_GLASS_PROMPT in transcript:
                    os.write(master_fd, synthetic_secret + b"\n")
                    sent = True
            waited, _status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            pytest.fail("signal-interrupted synthetic restore did not terminate")
    finally:
        os.close(master_fd)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert synthetic_secret not in transcript
    lines = [
        line.strip(b"\r")
        for line in transcript.splitlines()
        if line.startswith(b"{")
    ]
    assert len(lines) == 1, transcript
    result = json.loads(lines[0])
    assert result["error"] == {
        "code": "recovery_repository_error",
        "message": "Recovery credential is unavailable.",
    }
    for field in (
        "terminal_restored",
        "handlers_restored",
        "secret_zeroized",
        "password_fds_closed",
        "children_reaped",
        "process_groups_gone",
        "lock_released",
        "scratch_retryable",
    ):
        assert result[field] is True, result


def test_break_glass_orphan_retains_admission_until_exit_and_retry_completes(
    tmp_path, db_session, league_fixture, players_fixture, monkeypatch
):
    source, _account_id = _seed_source(db_session, league_fixture, players_fixture)
    bundle, _manifest = build_logical_bundle(source)
    work = tmp_path / "orphan-restore"
    work.mkdir()
    settings = _restore_settings(work, source)
    repository_path = Path(settings.recovery_repository)
    repository_path.mkdir()
    bundle_path = work / "synthetic-bundle"
    bundle_path.write_bytes(bundle)
    hang_once = work / "synthetic-hang-once"
    orphan_pid_file = work / "synthetic-orphan-pid"
    binary = Path(settings.recovery_restic_path)
    script = f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
hang_once = Path({str(hang_once)!r})
orphan_pid_file = Path({str(orphan_pid_file)!r})
bundle_path = Path({str(bundle_path)!r})
snapshot_id = {'a' * 64!r}
if "--password-command" in args or "cat" in args:
    raise SystemExit(91)
if "check" in args:
    if not hang_once.exists():
        hang_once.write_text("1", encoding="ascii")
        child = os.fork()
        if child == 0:
            os.setsid()
            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, 0)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            orphan_pid_file.write_text(str(os.getpid()), encoding="ascii")
            time.sleep(4.0)
            os._exit(0)
        time.sleep(60)
elif "snapshots" in args:
    print(json.dumps([{{
        "id": snapshot_id,
        "tags": [{RECOVERY_TAG!r}],
        "time": "2026-08-13T00:00:00+00:00",
    }}]))
elif "dump" in args:
    sys.stdout.buffer.write(bundle_path.read_bytes())
else:
    raise SystemExit(92)
"""
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o700)
    source_stat = source.stat()

    class StaticTopology:
        def resolve(self, path):
            current = Path(path).stat()
            if Path(path) == repository_path:
                return StorageIdentity(
                    "synthetic-target-disk",
                    "external",
                    f"{current.st_dev}:{current.st_ino}",
                )
            assert (current.st_dev, current.st_ino) == (
                source_stat.st_dev,
                source_stat.st_ino,
            )
            return StorageIdentity(
                "synthetic-source-disk",
                "internal",
                f"{current.st_dev}:{current.st_ino}",
            )

    repository = ResticRepository(
        settings,
        runner=BoundedSubprocessRunner(),
        topology=StaticTopology(),
    )
    repository.validate_tool = lambda: None
    repository.validate_break_glass_tool = lambda _expected=None: None
    monkeypatch.setattr(recovery_module, "filevault_enabled", lambda _runner: True)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    monkeypatch.setattr(
        recovery_module,
        "_read_break_glass_credential",
        lambda: bytearray(b"synthetic-break-glass"),
    )
    monkeypatch.setattr(recovery_module, "_RESTIC_BREAK_GLASS_TIMEOUT_SECONDS", 2.0)

    real_popen = recovery_module.subprocess.Popen
    inheritance_records = []

    def matching_open_fds(path):
        if not path.exists():
            return set()
        expected = path.stat()
        matches = set()
        for candidate in range(128):
            try:
                current = os.fstat(candidate)
            except OSError:
                continue
            if (current.st_dev, current.st_ino) == (
                expected.st_dev,
                expected.st_ino,
            ):
                matches.add(candidate)
        return matches

    def recording_popen(*args, **kwargs):
        argv = list(args[0])
        passed = set(kwargs.get("pass_fds", ()))
        authority = matching_open_fds(settings.recovery_lock_file) | matching_open_fds(
            repository_path
        )
        if (
            len(argv) > 6
            and argv[0] == sys.executable
            and argv[1:5]
            == ["-I", "-S", "-c", recovery_module._RESTIC_DIRECTORY_EXEC_CODE]
            and argv[6] == str(binary)
        ):
            kind = "restic"
        elif "_path_open_worker_entry" in " ".join(map(str, argv)):
            kind = "path-helper"
        elif argv[-3:] == ["-m", "api.recovery", "internal-verify"]:
            kind = "app-verifier"
        else:
            kind = "other"
        inheritance_records.append((kind, tuple(argv), passed, authority))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(recovery_module.subprocess, "Popen", recording_popen)

    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        restore_drill("latest", settings, repository=repository)
    elapsed = time.monotonic() - started
    assert caught.value.code == "recovery_tool_invalid"
    assert caught.value.safe_message == "Recovery tool timed out."
    assert elapsed < 10
    deadline = time.monotonic() + 1
    while not orphan_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert orphan_pid_file.exists()

    def attempt_direct_lock():
        with recovery_lock(settings.recovery_lock_file):
            pass

    for attempt in (
        attempt_direct_lock,
        lambda: run_backup("manual", settings, repository=object()),
        lambda: apply_retention(
            settings,
            repository=object(),
            apply=False,
            drill_snapshot="latest",
        ),
        lambda: reserve_api_trigger(settings),
    ):
        with pytest.raises(RecoveryError) as busy:
            attempt()
        assert busy.value.code == "recovery_busy"

    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        try:
            with recovery_lock(settings.recovery_lock_file):
                break
        except RecoveryError as exc:
            assert exc.code == "recovery_busy"
            time.sleep(0.05)
    else:  # pragma: no cover - deterministic child lifetime is bounded
        pytest.fail("escaped synthetic restore descendant retained the lock")

    result = restore_drill("latest", settings, repository=repository)
    assert all(result["application_evidence"].values())
    restic_records = [item for item in inheritance_records if item[0] == "restic"]
    assert len(restic_records) == 4
    for _kind, argv, passed, authority in restic_records:
        assert len(passed) == 3
        assert authority.issubset(passed)
        assert len(passed - authority) == 1
        assert argv[7:9] == ("-r", ".")
        assert "--password-file" in argv
        assert "--password-command" not in argv
        assert str(settings.recovery_credential_command) not in argv
    helper_records = [
        item for item in inheritance_records if item[0] == "path-helper"
    ]
    assert helper_records
    assert all(not (passed & authority) for _, _argv, passed, authority in helper_records)
    verifier_records = [
        item for item in inheritance_records if item[0] == "app-verifier"
    ]
    assert len(verifier_records) == 1
    assert verifier_records[0][2] == set()
    assert not (verifier_records[0][2] & verifier_records[0][3])


@pytest.mark.parametrize(
    "stage",
    [
        "scratch-created",
        "repository-verified",
        "bundle-retrieved",
        "database-restored",
        "application-verified",
        "cleanup-ready",
    ],
)
def test_actual_restore_drill_sigkill_stage_is_scavenged_by_next_run(
    tmp_path, db_session, league_fixture, players_fixture, monkeypatch, stage
):
    source, _ = _seed_source(db_session, league_fixture, players_fixture)
    bundle, _ = build_logical_bundle(source)
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_bytes(bundle)
    settings = _restore_settings(tmp_path, source)
    script = r"""
import os,sys
from contextlib import contextmanager
from pathlib import Path
from api.config import Settings
import api.services.recovery as recovery
from api.services.recovery import RECOVERY_TAG,StorageIdentity
class Repo:
  def __init__(self,bundle): self.bundle=Path(bundle).read_bytes()
  def validate_tool(self): pass
  def validate_topology(self,_source):
    return StorageIdentity('source','internal','1:1'),StorageIdentity('target','external','2:2')
  @contextmanager
  def pin_break_glass_repository(self,_source,*,lock_fd):
    assert lock_fd >= 0
    yield object()
  def break_glass_check(self,*,pin): assert pin is not None
  def break_glass_snapshots(self,*,pin):
    assert pin is not None
    return [{'id':'a'*64,'tags':[RECOVERY_TAG],'time':'2026-08-13T00:00:00+00:00'}]
  def break_glass_bundle(self,_snapshot,*,pin):
    assert pin is not None
    return self.bundle
recovery.filevault_enabled=lambda _runner: True
settings=Settings(
 db_path=sys.argv[1],recovery_required=False,recovery_repository=sys.argv[2],
 recovery_restic_path=sys.argv[3],recovery_restic_sha256='0'*64,
 recovery_credential_command=sys.argv[4],recovery_state_path=sys.argv[5],
 recovery_lock_path=sys.argv[6],recovery_scratch_path=sys.argv[7])
def kill(actual,_run):
  if actual == sys.argv[9]: os.kill(os.getpid(),9)
recovery.restore_drill('latest',settings,repository=Repo(sys.argv[8]),stage_hook=kill)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            settings.recovery_repository,
            settings.recovery_restic_path,
            settings.recovery_credential_command,
            str(settings.recovery_state_file),
            str(settings.recovery_lock_file),
            str(settings.recovery_scratch_dir),
            str(bundle_path),
            stage,
        ],
        cwd=Path(__file__).parent.parent,
        env={**os.environ, "RECOVERY_REQUIRED": "false"},
        check=False,
        timeout=30,
    )
    assert result.returncode == -9
    monkeypatch.setattr("api.services.recovery.filevault_enabled", lambda _runner: True)
    recovered = restore_drill(
        "latest", settings, repository=_BreakGlassRepository(bundle)
    )
    assert all(recovered["application_evidence"].values())
    assert not list(settings.recovery_scratch_dir.glob("run-*"))
    assert not list(settings.recovery_scratch_dir.glob(".quarantine-*"))
