"""Phase 30 logical recovery and write-admission tests; no live repository."""

from __future__ import annotations

import ast
import contextlib
import copy
import fcntl
import hashlib
import inspect
import json
import os
import plistlib
import pty
import re
import select as select_module
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import warnings
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# `api.recovery` holds `ROOT` and `_launchd_plist`; `api.services.recovery`,
# aliased `recovery_module` below, is a different module. Both are imported,
# under names that cannot be confused at a call site.
from api import recovery as app_recovery
from api.config import Settings
from api.crypto import encrypt
from api.models import Account, League, RawCache, Team, Tenant
from api.services import recovery as recovery_module
from api.services.espn import EspnReauthRequired, cookies_for_account
from api.services.recovery import (
    BoundedSubprocessRunner,
    MacOSTopologyResolver,
    ProcessResult,
    RecoveryAdmissionError,
    RecoveryError,
    RecoveryState,
    RepositoryPin,
    ResticRepository,
    StorageIdentity,
    apply_retention,
    assert_recovery_write_allowed,
    build_logical_bundle,
    catalog_spec,
    load_state,
    recovery_lock,
    recovery_status,
    reserve_api_trigger,
    restore_bundle_to_scratch,
    run_backup,
    save_state,
    scavenge_scratch,
    secret_free_error,
    validate_catalog,
)
from api.services.sync import SyncService
from api.tenancy import current_tenant_id

# ---------------------------------------------------------------------------
# The lexical venv, and why seventeen tests below can be skipped without that
# being a weakening.
#
# `api/recovery.py` puts `<repo>/.venv/bin/python` into the launchd plist and
# refuses to build one if that path is not an executable file. The path is
# LEXICAL on purpose: resolving the symlink selects the base framework
# interpreter and loses the venv's package search path under launchd. On the
# operator's Mac the symlink resolves; in a Linux container, or a CI checkout
# where `pip install -e .` goes into the runner's own environment, it does not
# exist at all.
#
# So seventeen tests here assert a property of an INSTALLED DEPLOYMENT rather
# than of this code, and in an environment with no such deployment they fail
# before reaching anything they are about. Skipping them there is honest;
# skipping them silently would not be, which is why:
#
#   * the predicate is the same three-part check `_launchd_plist` makes, and
#     `test_the_lexical_venv_skip_condition_matches_the_production_check`
#     asserts the two agree rather than trusting that they do;
#   * the two tests that run the interpreter and import the application carry a
#     STRONGER condition, because a venv that exists but cannot import `api`
#     would skip them for the wrong reason;
#   * the skip reason names the path, so a reader of a CI log is told what is
#     missing rather than that something was skipped.
# ---------------------------------------------------------------------------

LEXICAL_VENV_PYTHON = app_recovery.ROOT / ".venv/bin/python"


def _lexical_venv_is_usable() -> bool:
    """Exactly `_launchd_plist`'s check, so the skip cannot drift from it."""
    return (
        LEXICAL_VENV_PYTHON.is_absolute()
        and LEXICAL_VENV_PYTHON.is_file()
        and os.access(LEXICAL_VENV_PYTHON, os.X_OK)
    )


def _lexical_venv_imports_the_app() -> bool:
    """And can it actually run the application?

    A venv that exists but has no dependencies installed is a different
    condition from one that is absent, and only two tests care about the
    difference -- the ones that execute the interpreter and import `api`.
    """
    if not _lexical_venv_is_usable():
        return False
    try:
        completed = subprocess.run(
            [str(LEXICAL_VENV_PYTHON), "-c", "import api"],
            cwd=app_recovery.ROOT,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


requires_lexical_venv = pytest.mark.skipif(
    not _lexical_venv_is_usable(),
    reason=(
        f"{LEXICAL_VENV_PYTHON} is not an executable file, so no launchd plist "
        "can be built. This asserts a property of an installed deployment, not "
        "of the code."
    ),
)

requires_lexical_venv_with_dependencies = pytest.mark.skipif(
    not _lexical_venv_imports_the_app(),
    reason=(
        f"{LEXICAL_VENV_PYTHON} cannot import the application, so the "
        "interpreter it names cannot run the backup job."
    ),
)


def test_the_lexical_venv_skip_condition_matches_the_production_check():
    """Guards the skip. Never skipped itself.

    A skip whose condition disagreed with the code it stands in for would hide
    real failures in exactly the environments where it fires. So the predicate
    and `_launchd_plist`'s own refusal are compared, in whichever state this
    machine happens to be: either the venv is usable and the plist builds, or
    it is not and the plist refuses with `recovery_tool_invalid`. There is no
    third outcome, and asserting the equivalence is what makes the skips above
    safe to read as "not applicable here" rather than as "not checked".
    """
    usable = _lexical_venv_is_usable()
    refused = False
    try:
        app_recovery._launchd_plist()
    except RecoveryError as exc:
        refused = exc.code == "recovery_tool_invalid"
    except Exception:  # noqa: BLE001 - any other failure is not this condition
        refused = False
    # Which direction this can catch depends on the machine, and that is worth
    # saying rather than leaving as an apparent gap. Here the venv is genuinely
    # unusable, so a predicate hardcoded to `True` fails this test AND makes
    # the seventeen skipped tests run and fail -- the direction that matters,
    # because it is the one where a skip would hide a real failure. A predicate
    # hardcoded to `False` is simply the correct answer on this machine and
    # cannot be detected here; on a machine with a working venv the two
    # directions swap. The assertion holds in both.
    assert usable == (not refused), (
        f"the skip predicate says usable={usable} while _launchd_plist "
        f"{'refused' if refused else 'did not refuse'} for a missing runtime; "
        "the skip condition has drifted from the check it stands in for"
    )


def test_the_stronger_condition_implies_the_weaker_one():
    """A venv that can import the application is necessarily usable.

    Stated because the two predicates are separate functions and the ordering
    between them is the thing that makes the stronger skip narrower rather than
    merely different.
    """
    if _lexical_venv_imports_the_app():
        assert _lexical_venv_is_usable()


def _settings(tmp_path: Path, *, required: bool = True) -> Settings:
    repository = tmp_path / "external" / "repo"
    repository.mkdir(parents=True)
    binary = tmp_path / "restic"
    binary.write_bytes(b"fake-restic")
    command = tmp_path / "credential-command"
    command.write_text("#!/bin/sh\nprintf 'synthetic-password\\n'\n")
    binary.chmod(0o700)
    command.chmod(0o700)
    return Settings(
        db_path=str(tmp_path / "source.db"),
        recovery_required=required,
        recovery_repository=str(repository),
        recovery_restic_path=str(binary),
        recovery_restic_sha256="0" * 64,
        recovery_credential_command=str(command),
        recovery_state_path=str(tmp_path / "state.json"),
        recovery_lock_path=str(tmp_path / "state.lock"),
        recovery_scratch_path=str(tmp_path / "scratch"),
    )


def _prompt_in_pty(
    payload: bytes | None,
    *,
    fake_timeout: bool = False,
    fake_eof: bool = False,
    fail_restore: bool = False,
    fail_restore_always: bool = False,
    terminal_signal: int | None = None,
    cleanup_root: Path | None = None,
) -> bytes:
    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - assertions are made against the parent transcript
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
        if fake_timeout or fake_eof:
            snapshot_fd = os.dup(0)
            try:
                independent_state = recovery_module._capture_independent_tty_state(
                    snapshot_fd
                )
            finally:
                os.close(snapshot_fd)
            assert independent_state is not None
            recovery_module._capture_independent_tty_state = (
                lambda _tty_fd: independent_state
            )
        if fake_timeout:
            ticks = iter((0.0, 301.0, 301.0))
            recovery_module.time.monotonic = lambda: next(ticks, 301.0)
        if fake_eof:
            recovery_module.os.readv = lambda _fd, _buffers: 0
            real_selector = recovery_module.selectors.SelectSelector

            class ReadableEofSelector:
                def __init__(self):
                    self.delegate = real_selector()

                def register(self, *args, **kwargs):
                    return self.delegate.register(*args, **kwargs)

                def select(self, _timeout=None):
                    key = next(iter(self.delegate.get_map().values()))
                    return [(key, recovery_module.selectors.EVENT_READ)]

                def close(self):
                    self.delegate.close()

            recovery_module.selectors.SelectSelector = ReadableEofSelector
        if fail_restore or fail_restore_always:
            original_tcsetattr = recovery_module.termios.tcsetattr
            calls = 0

            def fail_second_tcsetattr(*args):
                nonlocal calls
                calls += 1
                if calls >= 2 and (fail_restore_always or calls == 2):
                    raise recovery_module.termios.error("synthetic restore failure")
                return original_tcsetattr(*args)

            recovery_module.termios.tcsetattr = fail_second_tcsetattr
        def invoke_prompt():
            if cleanup_root is None:
                return recovery_module._read_break_glass_credential()
            lock_path = cleanup_root / "prompt.lock"
            scratch_path = cleanup_root / "scratch"
            with recovery_module.recovery_lock(lock_path):
                root, root_token = recovery_module._safe_scratch_root(scratch_path)
                run = recovery_module._new_scratch_run(root, root_token)
                try:
                    return recovery_module._read_break_glass_credential()
                finally:
                    recovery_module._quarantine_scratch_run(
                        root, run.name, root_token
                    )

        try:
            value = invoke_prompt()
            outcome = f"PROMPT_OK:{len(value)}"
            recovery_module._zeroize(value)
        except BaseException as exc:
            envelope = recovery_module.secret_free_error(exc)
            outcome = "PROMPT_ERROR:" + json.dumps(envelope, sort_keys=True)
        attributes = recovery_module.termios.tcgetattr(0)
        terminal = (
            f":ECHO_{'ON' if attributes[3] & recovery_module.termios.ECHO else 'OFF'}"
            f":ISIG_{'ON' if attributes[3] & recovery_module.termios.ISIG else 'OFF'}"
        )
        control_indices = tuple(
            index
            for name in ("VINTR", "VQUIT", "VSUSP", "VDSUSP")
            if isinstance(
                (index := getattr(recovery_module.termios, name, None)), int
            )
            and index < len(original_terminal[6])
        )
        controls_restored = all(
            attributes[6][index] == original_terminal[6][index]
            for index in control_indices
        )
        handlers_restored = all(
            signal.getsignal(terminal_signal) == original_handler
            for terminal_signal, original_handler in original_handlers.items()
        )
        os.set_blocking(0, False)
        try:
            queued = os.read(0, 4096)
        except (BlockingIOError, OSError):
            queued = b""
        cleanup = ""
        if cleanup_root is not None:
            lock_path = cleanup_root / "prompt.lock"
            scratch_path = cleanup_root / "scratch"
            try:
                with recovery_module.recovery_lock(lock_path, blocking=False):
                    pass
                lock_released = True
            except BaseException:
                lock_released = False
            try:
                root, root_token = recovery_module._safe_scratch_root(scratch_path)
                retry = recovery_module._new_scratch_run(root, root_token)
                recovery_module._quarantine_scratch_run(
                    root, retry.name, root_token
                )
                scratch_retryable = not retry.exists()
            except BaseException:
                scratch_retryable = False
            cleanup = (
                f":LOCK_{'RELEASED' if lock_released else 'HELD'}"
                f":SCRATCH_{'RETRYABLE' if scratch_retryable else 'BLOCKED'}"
            )
        os.write(
            1,
            (
                outcome
                + terminal
                + f":QUEUED:{len(queued)}"
                + f":CC_{'RESTORED' if controls_restored else 'CHANGED'}"
                + f":SIGNALS_{'RESTORED' if handlers_restored else 'CHANGED'}"
                + cleanup
            ).encode("ascii"),
        )
        os._exit(0)

    transcript = bytearray()
    sent = False
    deadline = time.monotonic() + 5.0
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    transcript.extend(chunk)
                if (
                    (payload is not None or terminal_signal is not None)
                    and not sent
                    and recovery_module._BREAK_GLASS_PROMPT in transcript
                ):
                    if terminal_signal is None:
                        assert payload is not None
                        os.write(master_fd, payload)
                    else:
                        os.kill(pid, terminal_signal)
                    sent = True
            waited, _status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            pytest.fail("synthetic credential prompt did not terminate")
        while True:
            readable, _, _ = select_module.select([master_fd], [], [], 0)
            if not readable:
                break
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            transcript.extend(chunk)
    finally:
        os.close(master_fd)
    return bytes(transcript)


class FakeRepository:
    def __init__(self) -> None:
        self.bundle: bytes | None = None
        self.backups = 0
        self.checks = 0
        self.fail_backup = False
        self.snapshot_id = "a" * 64

    def validate_tool(self) -> None:
        return None

    def validate_topology(self, _source: Path):
        return StorageIdentity("source-disk", "internal"), StorageIdentity(
            "target-disk", "external"
        )

    @contextmanager
    def pin_repository(self, source: Path, *, lock_fd: int):
        yield RepositoryPin(
            fd=-1,
            repository_path="fake-pinned-repository",
            topology=self.validate_topology(source),
            repository_id="f" * 64,
            device=2,
            inode=2,
            lock_fd=lock_fd,
        )

    def latest_bundle(self, *, expect_snapshot: bool = False, pin=None) -> bytes | None:
        del pin
        if expect_snapshot and self.bundle is None:
            raise RecoveryError("recovery_repository_error", "missing")
        return self.bundle

    def backup(self, bundle: bytes, *, pin=None) -> str:
        del pin
        if self.fail_backup:
            raise RecoveryError("recovery_repository_error", "Repository failed.")
        self.backups += 1
        self.bundle = bundle
        return self.snapshot_id

    def check(self, *, pin=None) -> None:
        del pin
        self.checks += 1

    def snapshots(self, *, pin=None):
        del pin
        return (
            [{"id": self.snapshot_id, "tags": ["espn-edge-private-v1"]}]
            if self.bundle
            else []
        )


def _diskutil_payload(
    *,
    stores: list[str | dict[str, str]] | None = None,
    parent: str | None = "synthetic-physical-store",
    protocol: str = "USB",
    internal: bool = False,
    virtual: str | None = None,
    device: str = "__QUERY_SUBJECT__",
    **extra,
) -> bytes:
    payload = {
        "APFSPhysicalStores": (
            [{"APFSPhysicalStore": "synthetic-physical-store"}]
            if stores is None
            else stores
        ),
        "DeviceIdentifier": device,
        "BusProtocol": protocol,
        "Internal": internal,
        **extra,
    }
    if parent is not None:
        payload["ParentWholeDisk"] = parent
    if virtual is not None:
        payload["VirtualOrPhysical"] = virtual
    return plistlib.dumps(payload)


class FakeDiskutilRunner:
    def __init__(self, responses: dict[str, ProcessResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        assert argv[:3] == ["/usr/sbin/diskutil", "info", "-plist"]
        assert kwargs["max_output"] == 16 * 1024
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        assert 0 < kwargs["timeout_seconds"] <= 5.0
        assert re.fullmatch(r"disk[0-9]+(?:s[0-9]+)*", argv[-1])
        result = self.responses.get(
            argv[-1],
            self.responses.get(
                "*",
                ProcessResult(
                    1,
                    plistlib.dumps(
                        {"Error": -1, "ErrorMessage": "synthetic device is unavailable"}
                    ),
                    b"",
                ),
            ),
        )
        if result.returncode == 0:
            with contextlib.suppress(plistlib.InvalidFileException):
                payload = plistlib.loads(result.stdout)
                if (
                    isinstance(payload, dict)
                    and payload.get("DeviceIdentifier") == "__QUERY_SUBJECT__"
                ):
                    payload["DeviceIdentifier"] = argv[-1]
                    return ProcessResult(0, plistlib.dumps(payload), result.stderr)
        return result


def _resolver(runner, **kwargs) -> MacOSTopologyResolver:
    return MacOSTopologyResolver(
        runner,
        device_subject=lambda device: f"disk{device}",
        **kwargs,
    )


def test_macos_device_subject_uses_held_descriptor_block_identity(tmp_path, monkeypatch):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    calls: list[tuple[int, int]] = []

    class FakeDevname:
        argtypes = None
        restype = None

        def __call__(self, device, mode):
            calls.append((device, mode))
            return b"disk42s1"

    class FakeLibc:
        devname = FakeDevname()

    monkeypatch.setattr(recovery_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        recovery_module.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc()
    )
    device = source.stat().st_dev
    assert recovery_module._macos_device_subject(device) == "disk42s1"
    assert calls == [(device, recovery_module.stat.S_IFBLK)]


def test_macos_device_subject_fails_closed_without_block_identity(tmp_path, monkeypatch):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")

    class MissingDevname:
        argtypes = None
        restype = None

        def __call__(self, _device, _mode):
            return None

    class FakeLibc:
        devname = MissingDevname()

    monkeypatch.setattr(recovery_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        recovery_module.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc()
    )
    with pytest.raises(RecoveryError) as caught:
        recovery_module._macos_device_subject(source.stat().st_dev)
    assert caught.value.code == "recovery_target_unavailable"
    assert caught.value.safe_message == "Storage device identity is unavailable."


@pytest.mark.parametrize(
    "device",
    [-1, recovery_module._MAX_NATIVE_DEVICE + 1, True],
)
def test_macos_device_subject_rejects_narrowing_unsafe_device_before_ctypes(
    monkeypatch, device
):
    monkeypatch.setattr(recovery_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        recovery_module.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: pytest.fail("ctypes must not receive unsafe device"),
    )
    with pytest.raises(RecoveryError) as caught:
        recovery_module._macos_device_subject(device)
    assert caught.value.code == "recovery_target_unavailable"
    assert caught.value.safe_message == "Storage device identity is unavailable."


@pytest.mark.parametrize(
    "subject",
    [None, "", "/dev/disk1", "rdisk1", "disk", "disk1;synthetic"],
)
def test_topology_resolver_rejects_unqualified_descriptor_subject(
    tmp_path, subject
):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner()
    resolver = MacOSTopologyResolver(runner, device_subject=lambda _fd: subject)
    with pytest.raises(RecoveryError) as caught:
        resolver.resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert runner.calls == []


def test_topology_resolver_bounds_child_blocked_in_path_open(tmp_path, monkeypatch):
    sentinel = "PRIVATE-BLOCKED-VOLUME-3b762e14"
    blocked = tmp_path / sentinel
    os.mkfifo(blocked, 0o600)
    sessions = []
    original_start = recovery_module._PathOpenSession.start.__func__

    def tracking_start(cls, *args, **kwargs):
        session = original_start(cls, *args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(
        recovery_module._PathOpenSession, "start", classmethod(tracking_start)
    )
    runner = FakeDiskutilRunner()
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner, path_open_timeout_seconds=0.1).resolve(blocked)
    elapsed = time.monotonic() - started
    envelope = json.dumps(secret_free_error(caught.value), sort_keys=True)
    assert caught.value.code == "recovery_target_unavailable"
    assert caught.value.safe_message == "Recovery path open timed out."
    assert sentinel not in str(caught.value)
    assert sentinel not in envelope
    assert runner.calls == []
    assert len(sessions) == 1
    assert sentinel not in " ".join(str(item) for item in sessions[0].process.args)
    assert sessions[0].closed is True
    assert sessions[0].process.poll() is not None
    assert elapsed < 2


def test_blocked_path_open_preserves_state_releases_lock_and_restarts(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    settings.db_file.write_bytes(b"synthetic source descriptor")
    blocked = tmp_path / "synthetic-blocked-volume"
    os.mkfifo(blocked, 0o600)
    prior = RecoveryState(
        last_coverage_at="2026-08-16T19:00:00+00:00",
        last_snapshot_at="2026-08-16T19:00:00+00:00",
        last_result_code="recovery_ok",
        artifact_bytes=1234,
        schema_fingerprint_short="a" * 12,
    )
    save_state(settings.recovery_state_file, prior)
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    source_fd = os.open(settings.db_file, os.O_RDONLY)
    repository_fd = os.open(settings.recovery_repository, os.O_RDONLY | os.O_DIRECTORY)
    os.set_inheritable(source_fd, True)
    os.set_inheritable(repository_fd, True)
    real_popen = recovery_module.subprocess.Popen
    inheritance_records = []

    def recording_popen(*args, **kwargs):
        lock_fds = []
        if settings.recovery_lock_file.exists():
            lock_stat = settings.recovery_lock_file.stat()
            for candidate in range(256):
                try:
                    current = os.fstat(candidate)
                except OSError:
                    continue
                if (current.st_dev, current.st_ino) == (
                    lock_stat.st_dev,
                    lock_stat.st_ino,
                ):
                    lock_fds.append(candidate)
        inheritance_records.append(
            {
                "close_fds": kwargs.get("close_fds"),
                "pass_fds": tuple(kwargs.get("pass_fds", ())),
                "lock_fds": tuple(lock_fds),
            }
        )
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(recovery_module.subprocess, "Popen", recording_popen)

    class BlockedTopologyRepository:
        def validate_tool(self):
            return None

        def validate_topology(self, _source):
            return _resolver(
                FakeDiskutilRunner(), path_open_timeout_seconds=0.1
            ).resolve(blocked)

        @contextmanager
        def pin_repository(self, source, *, lock_fd):
            del lock_fd
            self.validate_topology(source)
            yield  # pragma: no cover - topology always fails

    try:
        started = time.monotonic()
        with pytest.raises(RecoveryError) as caught:
            run_backup(
                "hourly", settings, repository=BlockedTopologyRepository()
            )
        elapsed = time.monotonic() - started
        failed = load_state(settings.recovery_state_file)
        assert caught.value.code == "recovery_target_unavailable"
        assert failed.last_coverage_at == prior.last_coverage_at
        assert failed.last_snapshot_at == prior.last_snapshot_at
        assert failed.artifact_bytes == prior.artifact_bytes
        assert failed.last_result_code == "recovery_target_unavailable"
        assert elapsed < 2

        healthy = tmp_path / "synthetic-healthy-volume"
        healthy.write_bytes(b"synthetic")
        runner = FakeDiskutilRunner(
            {"*": ProcessResult(0, _diskutil_payload(), b"")}
        )
        with recovery_lock(settings.recovery_lock_file):
            identity = _resolver(
                runner, path_open_timeout_seconds=0.5
            ).resolve(healthy)
        assert identity.object_token is not None
        assert len(runner.calls) == 1
    finally:
        os.close(repository_fd)
        os.close(source_fd)

    assert len(inheritance_records) == 2
    for record in inheritance_records:
        assert record["close_fds"] is True
        assert len(record["pass_fds"]) == 1
        assert record["lock_fds"]
        assert source_fd not in record["pass_fds"]
        assert repository_fd not in record["pass_fds"]
        assert set(record["lock_fds"]).isdisjoint(record["pass_fds"])


@pytest.mark.parametrize(
    ("workflow", "blocked_authority_call"),
    [
        ("backup-precheck", 1),
        ("retention-pin", 2),
        ("retention-revalidation", 3),
    ],
)
def test_production_repository_path_authority_is_bounded_and_releases_lock(
    tmp_path, monkeypatch, workflow, blocked_authority_call
):
    sentinel = "PRIVATE-REPOSITORY-PATH-7d910e4b"
    settings = _settings(tmp_path / "settings")
    repository_path = tmp_path / sentinel
    repository_path.mkdir()
    settings = settings.model_copy(
        update={"recovery_repository": str(repository_path)}
    )
    settings.db_file.write_bytes(b"synthetic source")
    repository_stat = repository_path.stat()
    source_stat = settings.db_file.stat()
    prior = RecoveryState(
        last_coverage_at="2026-08-16T19:00:00+00:00",
        last_snapshot_at="2026-08-16T19:00:00+00:00",
        last_result_code="recovery_ok",
        artifact_bytes=1234,
        schema_fingerprint_short="a" * 12,
        retention_configured=True,
        retention_enforced=True,
    )
    save_state(settings.recovery_state_file, prior)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)

    class StaticTopology:
        def resolve(self, path):
            if Path(path) == repository_path:
                return StorageIdentity(
                    "synthetic-target-disk",
                    "external",
                    f"{repository_stat.st_dev}:{repository_stat.st_ino}",
                )
            return StorageIdentity(
                "synthetic-source-disk",
                "internal",
                f"{source_stat.st_dev}:{source_stat.st_ino}",
            )

    class NoCallRunner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(argv)
            raise AssertionError("Restic must not run after a blocked repository path open")

    runner = NoCallRunner()
    repository = ResticRepository(
        settings,
        runner=runner,
        topology=StaticTopology(),
        path_open_timeout_seconds=0.1,
    )
    monkeypatch.setattr(repository, "validate_tool", lambda: None)
    real_start = recovery_module._PathOpenSession.start.__func__
    authority_calls = 0
    sessions = []
    held_path = repository_path.with_name(f".{repository_path.name}.held")

    def blocking_start(cls, absolute, *args, **kwargs):
        nonlocal authority_calls
        if absolute == repository_path:
            authority_calls += 1
            if authority_calls == blocked_authority_call:
                repository_path.rename(held_path)
                os.mkfifo(repository_path, 0o600)
        session = real_start(cls, absolute, *args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(
        recovery_module._PathOpenSession,
        "start",
        classmethod(blocking_start),
    )
    try:
        started = time.monotonic()
        with pytest.raises(RecoveryError) as caught:
            if workflow == "backup-precheck":
                run_backup("hourly", settings, repository=repository)
            else:
                apply_retention(
                    settings,
                    repository=repository,
                    apply=True,
                    drill_snapshot="latest",
                )
        elapsed = time.monotonic() - started
        failed = load_state(settings.recovery_state_file)
        assert caught.value.code == "recovery_target_unavailable"
        assert caught.value.safe_message == "Recovery path open timed out."
        assert sentinel not in str(caught.value)
        assert sentinel not in json.dumps(secret_free_error(caught.value))
        assert failed.last_coverage_at == prior.last_coverage_at
        assert failed.last_snapshot_at == prior.last_snapshot_at
        assert failed.artifact_bytes == prior.artifact_bytes
        assert failed.last_result_code == "recovery_target_unavailable"
        assert runner.calls == []
        assert authority_calls == blocked_authority_call
        assert all(sentinel not in " ".join(map(str, item.process.args)) for item in sessions)
        assert all(item.closed and item.process.poll() is not None for item in sessions)
        assert elapsed < 2
        with recovery_lock(settings.recovery_lock_file, blocking=False):
            pass
    finally:
        if repository_path.is_fifo():
            repository_path.unlink()
        if held_path.exists():
            held_path.rename(repository_path)


@pytest.mark.parametrize(
    ("payload_kind", "flags", "descriptor_count", "invalid_control"),
    [
        ("malformed", 0, 2, False),
        ("wrong-magic", 0, 2, False),
        ("wrong-count", 0, 2, False),
        ("truncated-metadata", 0, 2, False),
        ("wrong-mode", 0, 2, False),
        ("valid", getattr(recovery_module.socket, "MSG_CTRUNC", 0), 2, False),
        ("valid", getattr(recovery_module.socket, "MSG_TRUNC", 0), 2, False),
        ("valid", 0, 1, False),
        ("valid", 0, 2, True),
    ],
    ids=(
        "malformed-payload",
        "wrong-metadata-magic",
        "wrong-metadata-count",
        "truncated-metadata",
        "invalid-metadata-mode",
        "truncated-ancillary",
        "truncated-payload",
        "wrong-fd-count",
        "unexpected-control-before-rights",
    ),
)
def test_path_open_session_closes_all_fds_from_invalid_response(
    payload_kind, flags, descriptor_count, invalid_control
):
    descriptors = [os.open("/dev/null", os.O_RDONLY) for _ in range(descriptor_count)]
    packed = recovery_module.array.array("i", descriptors).tobytes()
    valid_payload = recovery_module._PATH_METADATA_HEADER.pack(
        recovery_module._PATH_METADATA_MAGIC, 2
    ) + b"".join(
        recovery_module._PATH_METADATA_ENTRY.pack(1, index + 1, recovery_module.stat.S_IFREG)
        for index in range(2)
    )
    payload = valid_payload
    if payload_kind == "malformed":
        payload = b"E"
    elif payload_kind == "wrong-magic":
        payload = b"XX" + valid_payload[2:]
    elif payload_kind == "wrong-count":
        payload = recovery_module._PATH_METADATA_HEADER.pack(
            recovery_module._PATH_METADATA_MAGIC, 1
        ) + valid_payload[recovery_module._PATH_METADATA_HEADER.size :]
    elif payload_kind == "truncated-metadata":
        payload = valid_payload[:-1]
    elif payload_kind == "wrong-mode":
        payload = recovery_module._PATH_METADATA_HEADER.pack(
            recovery_module._PATH_METADATA_MAGIC, 2
        ) + b"".join(
            recovery_module._PATH_METADATA_ENTRY.pack(1, index + 1, 0)
            for index in range(2)
        )

    class FakeSocket:
        def settimeout(self, _timeout):
            return None

        def send(self, _value):
            return 1

        def recvmsg(self, _size, _ancillary_size):
            ancillary = []
            if invalid_control:
                ancillary.append((0, 0, b"synthetic-invalid-control"))
            ancillary.append(
                (
                    recovery_module.socket.SOL_SOCKET,
                    recovery_module.socket.SCM_RIGHTS,
                    packed,
                )
            )
            return (
                payload,
                ancillary,
                flags,
                None,
            )

        def close(self):
            return None

    class FinishedProcess:
        pid = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            return 0

    session = recovery_module._PathOpenSession(
        FinishedProcess(), FakeSocket(), 2, clock=time.monotonic
    )
    with pytest.raises(RecoveryError) as caught:
        session.open_path(deadline=time.monotonic() + 1, max_wait=1)
    session.close()
    assert caught.value.code == "recovery_target_unavailable"
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_path_open_session_real_socket_rejects_overlength_payload_and_closes_rights(
    monkeypatch,
):
    parent_socket, worker_socket = recovery_module.socket.socketpair(
        recovery_module.socket.AF_UNIX, recovery_module.socket.SOCK_DGRAM
    )
    sender_fd = os.open("/dev/null", os.O_RDONLY)
    rights = recovery_module.array.array("i", [sender_fd]).tobytes()
    worker_socket.sendmsg(
        [
            recovery_module._PATH_METADATA_HEADER.pack(
                recovery_module._PATH_METADATA_MAGIC, 1
            )
            + recovery_module._PATH_METADATA_ENTRY.pack(
                1, 1, recovery_module.stat.S_IFREG
            )
            + b"x"
        ],
        [
            (
                recovery_module.socket.SOL_SOCKET,
                recovery_module.socket.SCM_RIGHTS,
                rights,
            )
        ],
    )
    real_close = os.close
    closed_descriptors = []

    def recording_close(descriptor):
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(recovery_module.os, "close", recording_close)

    class FinishedProcess:
        pid = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            return 0

    session = recovery_module._PathOpenSession(
        FinishedProcess(), parent_socket, 1, clock=time.monotonic
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(RecoveryError) as caught:
                session.open_path(deadline=time.monotonic() + 1, max_wait=1)
        assert caught.value.code == "recovery_target_unavailable"
        received = [item for item in closed_descriptors if item != sender_fd]
        assert len(received) == 1
        with pytest.raises(OSError):
            os.fstat(received[0])
        os.fstat(sender_fd)
    finally:
        session.close()
        worker_socket.close()
        real_close(sender_fd)


@pytest.mark.parametrize(
    ("device", "mode"),
    [
        (1, 0x80000000),
        (recovery_module._MAX_NATIVE_DEVICE + 1, recovery_module.stat.S_IFREG),
    ],
    ids=("high-bit-mode", "native-device-overflow"),
)
def test_path_open_session_real_socket_rejects_narrowing_unsafe_metadata(
    monkeypatch, device, mode
):
    sentinel = "PRIVATE-METADATA-SENTINEL-4f2c"
    parent_socket, worker_socket = recovery_module.socket.socketpair(
        recovery_module.socket.AF_UNIX, recovery_module.socket.SOCK_DGRAM
    )
    sender_fd = os.open("/dev/null", os.O_RDONLY)
    rights = recovery_module.array.array("i", [sender_fd]).tobytes()
    payload = recovery_module._PATH_METADATA_HEADER.pack(
        recovery_module._PATH_METADATA_MAGIC, 1
    ) + recovery_module._PATH_METADATA_ENTRY.pack(device, 1, mode)
    worker_socket.sendmsg(
        [payload],
        [(recovery_module.socket.SOL_SOCKET, recovery_module.socket.SCM_RIGHTS, rights)],
    )
    real_close = os.close
    closed_descriptors = []

    def recording_close(descriptor):
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(recovery_module.os, "close", recording_close)

    class FinishedProcess:
        pid = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            return 0

    session = recovery_module._PathOpenSession(
        FinishedProcess(), parent_socket, 1, clock=time.monotonic
    )
    try:
        with pytest.raises(RecoveryError) as caught:
            session.open_path(deadline=time.monotonic() + 1, max_wait=1)
        envelope = json.dumps(secret_free_error(caught.value), sort_keys=True)
        assert caught.value.code == "recovery_target_unavailable"
        assert sentinel not in str(caught.value)
        assert sentinel not in envelope
        received = [item for item in closed_descriptors if item != sender_fd]
        assert len(received) == 1
        with pytest.raises(OSError):
            os.fstat(received[0])
        os.fstat(sender_fd)
    finally:
        session.close()
        worker_socket.close()
        real_close(sender_fd)


def test_path_open_session_real_socket_rejects_extra_rights_and_closes_received_fds(
    monkeypatch,
):
    parent_socket, worker_socket = recovery_module.socket.socketpair(
        recovery_module.socket.AF_UNIX, recovery_module.socket.SOCK_DGRAM
    )
    sender_fds = [os.open("/dev/null", os.O_RDONLY) for _ in range(2)]
    payload = recovery_module._PATH_METADATA_HEADER.pack(
        recovery_module._PATH_METADATA_MAGIC, 1
    ) + recovery_module._PATH_METADATA_ENTRY.pack(
        1, 1, recovery_module.stat.S_IFREG
    )
    worker_socket.sendmsg(
        [payload],
        [
            (
                recovery_module.socket.SOL_SOCKET,
                recovery_module.socket.SCM_RIGHTS,
                recovery_module.array.array("i", sender_fds).tobytes(),
            )
        ],
    )
    real_close = os.close
    closed_descriptors = []

    def recording_close(descriptor):
        closed_descriptors.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(recovery_module.os, "close", recording_close)

    class FinishedProcess:
        pid = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            return 0

    session = recovery_module._PathOpenSession(
        FinishedProcess(), parent_socket, 1, clock=time.monotonic
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(RecoveryError) as caught:
                session.open_path(deadline=time.monotonic() + 1, max_wait=1)
        assert caught.value.code == "recovery_target_unavailable"
        received = [item for item in closed_descriptors if item not in sender_fds]
        assert received
        for descriptor in received:
            with pytest.raises(OSError):
                os.fstat(descriptor)
        for descriptor in sender_fds:
            os.fstat(descriptor)
    finally:
        session.close()
        worker_socket.close()
        for descriptor in sender_fds:
            real_close(descriptor)


@pytest.mark.parametrize("kind", ["empty", "multiple"])
def test_path_open_session_rejects_empty_or_multiple_rights_records(kind):
    descriptors = [os.open("/dev/null", os.O_RDONLY) for _ in range(2)]
    payload = recovery_module._PATH_METADATA_HEADER.pack(
        recovery_module._PATH_METADATA_MAGIC, 2
    ) + b"".join(
        recovery_module._PATH_METADATA_ENTRY.pack(
            1, index + 1, recovery_module.stat.S_IFREG
        )
        for index in range(2)
    )

    class FakeSocket:
        def settimeout(self, _timeout):
            return None

        def send(self, _value):
            return 1

        def recvmsg(self, _size, _ancillary_size):
            if kind == "empty":
                ancillary = [
                    (
                        recovery_module.socket.SOL_SOCKET,
                        recovery_module.socket.SCM_RIGHTS,
                        b"",
                    )
                ]
            else:
                ancillary = [
                    (
                        recovery_module.socket.SOL_SOCKET,
                        recovery_module.socket.SCM_RIGHTS,
                        recovery_module.array.array("i", [descriptor]).tobytes(),
                    )
                    for descriptor in descriptors
                ]
            return payload, ancillary, 0, None

        def close(self):
            return None

    class FinishedProcess:
        pid = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            return 0

    session = recovery_module._PathOpenSession(
        FinishedProcess(), FakeSocket(), 2, clock=time.monotonic
    )
    with pytest.raises(RecoveryError) as caught:
        session.open_path(deadline=time.monotonic() + 1, max_wait=1)
    session.close()
    assert caught.value.code == "recovery_target_unavailable"
    if kind == "empty":
        for descriptor in descriptors:
            os.close(descriptor)
    else:
        for descriptor in descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)


def test_parent_path_authority_consumes_worker_metadata_without_fstat(
    tmp_path, monkeypatch
):
    target = tmp_path / "synthetic-repository"
    target.mkdir()

    def forbidden_parent_fstat(_fd):
        raise AssertionError("parent must not read external descriptor metadata")

    monkeypatch.setattr(recovery_module.os, "fstat", forbidden_parent_fstat)
    with recovery_module._bounded_path_authority(
        target, require_directory=True
    ) as authority:
        assert authority.object_token
        assert authority.fd >= 0


def _mark_mount_devices(monkeypatch, mounts: dict[Path, int]) -> None:
    device_by_object: dict[tuple[int, int], int] = {}
    for mount, synthetic_device in mounts.items():
        for candidate in (mount, *mount.rglob("*")):
            if candidate.is_symlink():
                continue
            current = candidate.stat()
            device_by_object[(current.st_dev, current.st_ino)] = synthetic_device

    def synthetic_metadata(descriptor):
        synthetic_device = device_by_object.get(
            (descriptor.device, descriptor.inode)
        )
        if synthetic_device is None:
            return descriptor
        return recovery_module._PathDescriptor(
            descriptor.fd,
            synthetic_device,
            descriptor.inode,
            descriptor.mode,
        )

    monkeypatch.setattr(recovery_module, "_topology_metadata", synthetic_metadata)


def test_topology_resolver_accepts_direct_local_physical_mount(tmp_path, monkeypatch):
    mount = tmp_path / "mounted-volume"
    mount.mkdir()
    _mark_mount_devices(monkeypatch, {mount: 99101})
    runner = FakeDiskutilRunner(
        {"disk99101": ProcessResult(0, _diskutil_payload(stores=[]), b"")}
    )
    identity = _resolver(runner).resolve(mount)
    st = mount.stat()
    assert identity.physical_parents == ("synthetic-physical-store",)
    assert identity.media_class == "external"
    assert identity.object_token == f"99101:{st.st_ino}"
    assert [call[0][-1] for call in runner.calls] == ["disk99101"]


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_topology_resolver_queries_descriptor_discovered_mount_only(
    tmp_path, monkeypatch, kind
):
    mount = tmp_path / "mounted-volume"
    nested = mount / "private" / "repository"
    nested.mkdir(parents=True)
    original = nested / "edge.db" if kind == "file" else nested
    if kind == "file":
        original.write_bytes(b"synthetic")
    _mark_mount_devices(monkeypatch, {mount: 99102})
    runner = FakeDiskutilRunner(
        {"disk99102": ProcessResult(0, _diskutil_payload(), b"")}
    )
    identity = _resolver(runner).resolve(original)
    original_st = original.stat()
    mount_st = mount.stat()
    assert identity.object_token == f"99102:{original_st.st_ino}"
    assert identity.object_token != f"99102:{mount_st.st_ino}"
    assert [call[0][-1] for call in runner.calls] == ["disk99102"]


def test_topology_resolver_stops_at_root_when_no_mount_is_recognized(tmp_path):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert len(runner.calls) == 1
    assert runner.calls[0][0][-1].startswith("disk")


@pytest.mark.parametrize(
    "result",
    [
        ProcessResult(1, b"", b"synthetic failure"),
        ProcessResult(
            0,
            plistlib.dumps({"Error": -1, "ErrorMessage": "synthetic failure"}),
            b"",
        ),
    ],
    ids=("nonzero", "error-plist"),
)
def test_topology_resolver_does_not_climb_after_mount_query_failure(
    tmp_path, monkeypatch, result
):
    mount = tmp_path / "network-or-image-volume"
    nested = mount / "repository"
    nested.mkdir(parents=True)
    _mark_mount_devices(monkeypatch, {mount: 99103})
    runner = FakeDiskutilRunner({"disk99103": result})
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(nested)
    assert caught.value.code == "recovery_target_unavailable"
    assert [call[0][-1] for call in runner.calls] == ["disk99103"]


@pytest.mark.parametrize(
    "payload",
    [b"not-a-plist", plistlib.dumps(["not", "a", "mapping"])],
)
def test_topology_resolver_rejects_malformed_diskutil_plist(tmp_path, payload):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner({"*": ProcessResult(0, payload, b"")})
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert len(runner.calls) == 1


def test_topology_resolver_rejects_symlink_ancestor_before_diskutil(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "edge.db").write_bytes(b"synthetic")
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    runner = FakeDiskutilRunner()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(linked / "edge.db")
    assert caught.value.code == "recovery_target_unavailable"
    assert runner.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"virtual": "Virtual"},
        {"protocol": "Disk Image"},
        {"protocol": "Network"},
        {"protocol": "USB", "VolumeKind": "smbfs"},
    ],
    ids=(
        "explicit-virtual",
        "disk-image-without-virtual-field",
        "network-protocol",
        "network-filesystem-class",
    ),
)
def test_topology_resolver_rejects_virtual_image_and_network_mounts(tmp_path, overrides):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner(
        {"*": ProcessResult(0, _diskutil_payload(**overrides), b"")}
    )
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("payload", "parents", "media"),
    [
        (
            _diskutil_payload(
                stores=[{"APFSPhysicalStore": "synthetic-store-a"}],
                parent="synthetic-whole-disk",
                protocol="Apple Fabric",
                internal=True,
            ),
            ("synthetic-store-a",),
            "internal",
        ),
        (
            _diskutil_payload(
                stores=["synthetic-store-b", {"APFSPhysicalStore": "synthetic-store-a"}],
                parent="synthetic-whole-disk",
            ),
            ("synthetic-store-a", "synthetic-store-b"),
            "external",
        ),
        (
            _diskutil_payload(
                stores=[],
                parent="synthetic-external-disk",
                protocol="USB",
                internal=False,
                Ejectable=True,
                Removable=True,
            ),
            ("synthetic-external-disk",),
            "external",
        ),
    ],
    ids=("native-apfs-dict", "native-apfs-string-and-dict", "native-external-fat-shape"),
)
def test_topology_resolver_accepts_measured_native_shapes(tmp_path, payload, parents, media):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner({"*": ProcessResult(0, payload, b"")})
    identity = _resolver(runner).resolve(source)
    assert identity.physical_parents == parents
    assert identity.media_class == media


def test_topology_resolver_rejects_parentless_non_apfs_partition(tmp_path):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner(
        {"*": ProcessResult(0, _diskutil_payload(stores=[], parent=None), b"")}
    )
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"


def test_topology_resolver_rejects_unknown_physical_evidence(tmp_path):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner(
        {
            "*": ProcessResult(
                0, _diskutil_payload(protocol="Synthetic Unknown Bus"), b""
            )
        }
    )
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"


def test_topology_resolver_detects_ancestor_symlink_replacement_during_diskutil(
    tmp_path, monkeypatch
):
    mount = tmp_path / "mounted-volume"
    mount.mkdir()
    ancestor = mount / "private"
    ancestor.mkdir()
    source = ancestor / "edge.db"
    source.write_bytes(b"synthetic")
    displaced = mount / "displaced"
    _mark_mount_devices(monkeypatch, {mount: 99104})

    class AncestorSwapRunner(FakeDiskutilRunner):
        def run(self, argv, **kwargs):
            result = super().run(argv, **kwargs)
            os.rename(ancestor, displaced)
            ancestor.symlink_to(displaced, target_is_directory=True)
            return result

    runner = AncestorSwapRunner(
        {"disk99104": ProcessResult(0, _diskutil_payload(), b"")}
    )
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert len(runner.calls) == 1


def test_topology_resolver_detects_mount_swap_during_diskutil(tmp_path, monkeypatch):
    mount = tmp_path / "mounted-volume"
    mount.mkdir()
    displaced = tmp_path / "displaced"
    _mark_mount_devices(monkeypatch, {mount: 99105})

    class SwappingRunner(FakeDiskutilRunner):
        def run(self, argv, **kwargs):
            result = super().run(argv, **kwargs)
            os.rename(mount, displaced)
            mount.mkdir()
            return result

    runner = SwappingRunner(
        {"disk99105": ProcessResult(0, _diskutil_payload(), b"")}
    )
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(mount)
    assert caught.value.code == "recovery_target_unavailable"


def test_topology_resolver_rejects_swap_query_restore_aba(tmp_path, monkeypatch):
    mount = tmp_path / "mounted-volume"
    mount.mkdir()
    source = mount / "edge.db"
    source.write_bytes(b"synthetic")
    displaced = tmp_path / "held-original"
    attacker_saved = tmp_path / "attacker-volume"
    _mark_mount_devices(monkeypatch, {mount: 99108})

    class AbaRunner(FakeDiskutilRunner):
        def run(self, argv, **kwargs):
            os.rename(mount, displaced)
            mount.mkdir()
            try:
                return super().run(argv, **kwargs)
            finally:
                os.rename(mount, attacker_saved)
                os.rename(displaced, mount)

    runner = AbaRunner(
        {
            "disk99108": ProcessResult(
                0,
                _diskutil_payload(
                    device="disk99109",
                    stores=[{"APFSPhysicalStore": "attacker-store"}],
                ),
                b"",
            )
        }
    )
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert [call[0][-1] for call in runner.calls] == ["disk99108"]
    assert source.read_bytes() == b"synthetic"
    assert attacker_saved.is_dir()


def test_topology_resolver_missing_path_error_is_secret_free(tmp_path):
    sentinel = "PRIVATE-PATH-SENTINEL-a19f55ec70b34b1b"
    runner = FakeDiskutilRunner()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(tmp_path / sentinel / "edge.db")
    serialized = json.dumps(secret_free_error(caught.value), sort_keys=True)
    assert caught.value.code == "recovery_target_unavailable"
    assert sentinel not in str(caught.value)
    assert sentinel not in serialized
    assert runner.calls == []


def test_topology_resolver_bounds_component_walk(tmp_path):
    source = tmp_path.joinpath(*(f"part-{index}" for index in range(65)))
    runner = FakeDiskutilRunner()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert runner.calls == []


def test_topology_resolver_preserves_original_object_token(tmp_path):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")
    runner = FakeDiskutilRunner(
        {
            "*": ProcessResult(
                0,
                _diskutil_payload(protocol="Apple Fabric", internal=True),
                b"",
            )
        }
    )
    identity = _resolver(runner).resolve(source)
    source_stat = source.stat()
    root_stat = Path("/").stat()
    assert identity.object_token == f"{source_stat.st_dev}:{source_stat.st_ino}"
    assert identity.object_token != f"{root_stat.st_dev}:{root_stat.st_ino}"


def test_bounded_runner_kills_and_drains_hung_process():
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        BoundedSubprocessRunner().run(
            [
                sys.executable,
                "-c",
                "import time; print('started', flush=True); time.sleep(60)",
            ],
            env={"PATH": "/usr/bin:/bin"},
            max_output=1024,
            timeout_seconds=0.05,
        )
    assert caught.value.code == "recovery_tool_invalid"
    assert "timed out" in caught.value.safe_message.lower()
    assert time.monotonic() - started < 2


def test_bounded_runner_parent_interrupt_kills_noninteractive_process_group(
    tmp_path, monkeypatch
):
    descendant_pid = tmp_path / "synthetic-descendant.pid"
    child = (
        "import os,time; "
        f"open({str(descendant_pid)!r},'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "time.sleep(60)"
    )
    real_selector = recovery_module.selectors.DefaultSelector

    class InterruptingSelector:
        def __init__(self):
            self.delegate = real_selector()

        def register(self, *args, **kwargs):
            return self.delegate.register(*args, **kwargs)

        def unregister(self, *args, **kwargs):
            return self.delegate.unregister(*args, **kwargs)

        def get_map(self):
            return self.delegate.get_map()

        def close(self):
            return self.delegate.close()

        def select(self, timeout=None):
            if descendant_pid.exists():
                raise KeyboardInterrupt
            return self.delegate.select(min(timeout or 0.01, 0.01))

    monkeypatch.setattr(
        recovery_module.selectors, "DefaultSelector", InterruptingSelector
    )
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        BoundedSubprocessRunner().run(
            [sys.executable, "-c", parent],
            env={"PATH": "/usr/bin:/bin"},
            timeout_seconds=5,
        )
    elapsed = time.monotonic() - started
    assert descendant_pid.exists()
    pid = int(descendant_pid.read_text(encoding="ascii"))
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        pytest.fail("noninteractive descendant survived parent interruption")
    assert elapsed < 2


def test_bounded_runner_selector_setup_interrupt_kills_child_authority_copies(
    tmp_path, monkeypatch
):
    repository = tmp_path / "synthetic-repository"
    repository.mkdir()
    repository_fd = os.open(repository, os.O_RDONLY | os.O_DIRECTORY)
    lock_path = tmp_path / "synthetic.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    credential_read, credential_write = os.pipe()
    os.close(credential_write)
    started_processes = []
    real_popen = recovery_module.subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started_processes.append((process, kwargs["pass_fds"]))
        return process

    def interrupted_selector():
        raise KeyboardInterrupt

    monkeypatch.setattr(recovery_module.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(
        recovery_module.selectors, "DefaultSelector", interrupted_selector
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            BoundedSubprocessRunner().run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                env={"PATH": "/usr/bin:/bin"},
                pass_fds=(repository_fd, lock_fd, credential_read),
                timeout_seconds=5,
            )
        assert len(started_processes) == 1
        process, inherited = started_processes[0]
        assert inherited == (repository_fd, lock_fd, credential_read)
        assert process.wait(timeout=1) < 0
    finally:
        os.close(credential_read)
        os.close(repository_fd)
        os.close(lock_fd)

    contender = os.open(lock_path, os.O_RDWR)
    try:
        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(contender)


def test_descriptor_cwd_runner_requires_authority_and_preserves_timeout_redaction(
    tmp_path, capsys
):
    repository = tmp_path / "synthetic-repository"
    repository.mkdir()
    descriptor = os.open(repository, os.O_RDONLY | os.O_DIRECTORY)
    sentinel = "DESCRIPTOR-CWD-TIMEOUT-MUST-NOT-LEAK"
    try:
        with pytest.raises(RecoveryError) as invalid:
            BoundedSubprocessRunner().run(
                [sys.executable, "-c", "raise SystemExit(0)"],
                cwd_fd=descriptor,
                timeout_seconds=1,
            )
        assert invalid.value.code == "recovery_tool_invalid"

        started = time.monotonic()
        with pytest.raises(RecoveryError) as timed_out:
            BoundedSubprocessRunner().run(
                [
                    sys.executable,
                    "-c",
                    f"import sys,time; print({sentinel!r}); sys.stdout.flush(); time.sleep(60)",
                ],
                env={"PATH": "/usr/bin:/bin"},
                max_output=1024,
                pass_fds=(descriptor,),
                cwd_fd=descriptor,
                timeout_seconds=0.05,
            )
    finally:
        os.close(descriptor)

    captured = capsys.readouterr()
    assert timed_out.value.code == "recovery_tool_invalid"
    assert timed_out.value.safe_message == "Recovery tool timed out."
    assert sentinel not in str(timed_out.value)
    assert sentinel not in json.dumps(secret_free_error(timed_out.value))
    assert captured.out == ""
    assert captured.err == ""
    assert time.monotonic() - started < 2


def test_bounded_runner_timeout_with_descendant_holding_both_pipes(capsys):
    sentinel = "DESCENDANT-TIMEOUT-OUTPUT-MUST-NOT-LEAK"
    child = (
        "import sys,time; time.sleep(60); "
        f"sys.stdout.write({sentinel!r}); sys.stderr.write({sentinel!r})"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        BoundedSubprocessRunner().run(
            [sys.executable, "-c", parent],
            env={"PATH": "/usr/bin:/bin"},
            max_output=1024,
            timeout_seconds=0.1,
        )
    elapsed = time.monotonic() - started
    time.sleep(0.1)
    captured = capsys.readouterr()
    assert caught.value.code == "recovery_tool_invalid"
    assert caught.value.safe_message == "Recovery tool timed out."
    assert sentinel not in str(caught.value)
    assert sentinel not in json.dumps(secret_free_error(caught.value))
    assert captured.out == ""
    assert captured.err == ""
    assert elapsed < 2


def test_bounded_runner_overflow_with_descendant_holding_both_pipes(capsys):
    sentinel = "DESCENDANT-OVERFLOW-OUTPUT-MUST-NOT-LEAK"
    child = (
        "import sys,time; "
        f"sys.stdout.write({sentinel!r}); sys.stdout.flush(); "
        f"sys.stderr.write({sentinel!r}); sys.stderr.flush(); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "time.sleep(0.05); sys.stdout.write('X'*4096); sys.stdout.flush(); time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        BoundedSubprocessRunner().run(
            [sys.executable, "-c", parent],
            env={"PATH": "/usr/bin:/bin"},
            max_output=64,
        )
    elapsed = time.monotonic() - started
    time.sleep(0.1)
    captured = capsys.readouterr()
    assert caught.value.code == "recovery_tool_invalid"
    assert caught.value.safe_message == "Recovery tool output exceeded its bound."
    assert sentinel not in str(caught.value)
    assert sentinel not in json.dumps(secret_free_error(caught.value))
    assert captured.out == ""
    assert captured.err == ""
    assert elapsed < 2


def test_bounded_runner_bounds_drain_when_descendant_escapes_group(capsys):
    sentinel = "ESCAPED-DESCENDANT-OUTPUT-MUST-NOT-LEAK"
    child = (
        "import os,sys,time; os.setsid(); time.sleep(1.2); "
        f"sys.stdout.write({sentinel!r}); sys.stderr.write({sentinel!r})"
    )
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        BoundedSubprocessRunner().run(
            [sys.executable, "-c", parent],
            env={"PATH": "/usr/bin:/bin"},
            max_output=1024,
        )
    elapsed = time.monotonic() - started
    time.sleep(0.3)
    captured = capsys.readouterr()
    assert caught.value.code == "recovery_tool_invalid"
    assert caught.value.safe_message == "Recovery tool output did not close."
    assert sentinel not in json.dumps(secret_free_error(caught.value))
    assert captured.out == ""
    assert captured.err == ""
    assert elapsed < 2


def test_topology_resolver_translates_runner_timeout_to_safe_error(tmp_path):
    source = tmp_path / "edge.db"
    source.write_bytes(b"synthetic")

    class HungRunner(FakeDiskutilRunner):
        def run(self, argv, **kwargs):
            super().run(argv, **kwargs)
            raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")

    runner = HungRunner()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner).resolve(source)
    assert caught.value.code == "recovery_target_unavailable"
    assert caught.value.safe_message == "Storage topology is unavailable."


def test_topology_resolver_bounds_total_resolution_before_command():
    times = iter((0.0, 6.0))
    runner = FakeDiskutilRunner()
    with pytest.raises(RecoveryError) as caught:
        _resolver(runner, monotonic=lambda: next(times)).resolve(Path("/"))
    assert caught.value.code == "recovery_target_unavailable"
    assert "timed out" in caught.value.safe_message.lower()
    assert runner.calls == []


def test_repository_rejects_same_non_apfs_parent(tmp_path):
    settings = _settings(tmp_path)

    class SameParentTopology:
        def resolve(self, path):
            media = "external" if path == settings.recovery_repository else "internal"
            return StorageIdentity("same-whole-disk", media)

    repository_adapter = ResticRepository(settings, topology=SameParentTopology())
    with pytest.raises(RecoveryError) as caught:
        repository_adapter.validate_topology(settings.db_file)
    assert caught.value.code == "recovery_target_unavailable"


@pytest.mark.parametrize(
    ("source_stores", "target_stores"),
    [
        ([{"APFSPhysicalStore": "shared-store"}], ["shared-store"]),
        (
            ["shared-store", {"APFSPhysicalStore": "source-only-store"}],
            [{"APFSPhysicalStore": "target-only-store"}, "shared-store"],
        ),
    ],
    ids=("single-store", "reversed-multi-store-overlap"),
)
def test_repository_rejects_any_overlapping_apfs_physical_store(
    tmp_path, monkeypatch, source_stores, target_stores
):
    source_mount = tmp_path / "source-volume"
    target_mount = tmp_path / "target-volume"
    source_mount.mkdir()
    target_mount.mkdir()
    source = source_mount / "edge.db"
    source.write_bytes(b"synthetic")
    repository = target_mount / "repository"
    repository.mkdir()
    _mark_mount_devices(monkeypatch, {source_mount: 99106, target_mount: 99107})
    settings = _settings(tmp_path / "settings")
    settings = settings.model_copy(
        update={"db_path": str(source), "recovery_repository": str(repository)}
    )
    runner = FakeDiskutilRunner(
        {
            "disk99106": ProcessResult(
                0,
                _diskutil_payload(
                    stores=source_stores,
                    parent="synthetic-source-whole-disk",
                    protocol="Apple Fabric",
                    internal=True,
                ),
                b"",
            ),
            "disk99107": ProcessResult(
                0,
                _diskutil_payload(
                    stores=target_stores,
                    parent="synthetic-target-whole-disk",
                ),
                b"",
            ),
        }
    )
    repository_adapter = ResticRepository(
        settings, topology=_resolver(runner)
    )
    with pytest.raises(RecoveryError) as caught:
        repository_adapter.validate_topology(source)
    assert caught.value.code == "recovery_target_unavailable"


def test_bundle_excludes_cache_credentials_owner_ids_and_restores_reauth(tmp_path, db_session):
    account = Account(
        tenant_id=current_tenant_id(db_session),
        label="operator",
        swid="{PRIVATE-OWNER-CANARY}",
        espn_s2_encrypted=encrypt("PRIVATE-S2-CANARY"),
        status="active",
    )
    db_session.add(account)
    db_session.flush()
    league = League(tenant_id=current_tenant_id(db_session), 
        espn_league_id="8001",
        season=2026,
        account_id=account.id,
        lifecycle="pre_draft",
        is_public=False,
    )
    db_session.add(league)
    db_session.flush()
    db_session.add(
        Team(
            league_id=league.id,
            espn_team_id=1,
            name="Synthetic Team",
            owner_swids_json=["OWNER-ID-CANARY"],
            is_me=True,
        )
    )
    db_session.add(
        RawCache(
            key="cache-canary",
            # `tenant_id` is part of the primary key as of migration 0012 and
            # is NOT NULL, so a construction without it no longer inserts.
            tenant_id=db_session.query(Tenant).one().id,
            fetched_at=datetime.now(UTC),
            payload_json={"private": "RAW-PAYLOAD-CANARY"},
        )
    )
    db_session.commit()

    # The db_session fixture owns a different path than tmp_path; use the active
    # engine's real SQLite file without exposing any production data.
    source = Path(str(db_session.get_bind().url.database))
    bundle, manifest = build_logical_bundle(source)
    assert "raw_cache" not in manifest["tables"]
    for canary in (
        b"PRIVATE-OWNER-CANARY",
        b"PRIVATE-S2-CANARY",
        b"OWNER-ID-CANARY",
        b"RAW-PAYLOAD-CANARY",
    ):
        assert canary not in bundle

    restored = tmp_path / "restored.db"
    assert restore_bundle_to_scratch(bundle, restored)["integrity"] == "ok"
    connection = sqlite3.connect(restored)
    try:
        row = connection.execute("SELECT swid,espn_s2_encrypted,status FROM accounts").fetchone()
        assert row == ("{REAUTH-REQUIRED}", "not-a-fernet-token", "needs_reauth")
        assert connection.execute("SELECT count(*) FROM raw_cache").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM teams WHERE owner_swids_json IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_unknown_schema_aborts_before_repository(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    connection = sqlite3.connect(settings.db_file)
    connection.execute("CREATE TABLE unexpected (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    repo = FakeRepository()
    with pytest.raises(RecoveryError, match="schema") as caught:
        run_backup("manual", settings, repository=repo)
    assert caught.value.code == "recovery_schema_drift"
    assert repo.backups == 0


def test_backup_changed_noop_and_failure_preserves_coverage(tmp_path, monkeypatch):
    from api import models  # noqa: F401
    from api.db import Base

    settings = _settings(tmp_path)
    engine = __import__("sqlalchemy").create_engine(f"sqlite:///{settings.db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    repo = FakeRepository()
    now = datetime(2026, 8, 13, tzinfo=UTC)

    first = run_backup("manual", settings, repository=repo, clock=lambda: now)
    assert first["snapshot_created"] is True
    first_state = json.loads(settings.recovery_state_file.read_text())
    # A cache-only mutation is deliberately absent from the safe-content digest.
    connection = sqlite3.connect(settings.db_file)
    # `tenant_id` is part of the primary key and NOT NULL as of revision 0012,
    # so a raw INSERT has to name it. The tenant is read back rather than
    # hardcoded to 1: this database is built by `create_all`, whose seeding
    # listener decides the id, and a literal would be right until it wasn't.
    tenant_id = connection.execute("SELECT id FROM tenants LIMIT 1").fetchone()[0]
    connection.execute(
        "INSERT INTO raw_cache(key,fetched_at,payload_json,tenant_id) VALUES(?,?,?,?)",
        (
            "cache-only",
            "2026-08-13 00:01:00",
            '{"private":"not-in-bundle"}',
            tenant_id,
        ),
    )
    connection.commit()
    connection.close()
    second = run_backup(
        "hourly", settings, repository=repo, clock=lambda: now + timedelta(minutes=30)
    )
    assert second["snapshot_created"] is False
    second_state = json.loads(settings.recovery_state_file.read_text())
    assert second_state["last_coverage_at"] != first_state["last_coverage_at"]
    assert second_state["last_snapshot_at"] == first_state["last_snapshot_at"]
    assert repo.backups == 1

    # Change a protected table, then force the fake repository failure.
    connection = sqlite3.connect(settings.db_file)
    connection.execute(
        "INSERT INTO accounts(label,swid,espn_s2_encrypted,status,created_at,tenant_id) "
        "VALUES('x','secret-a','secret-b','active','2026-08-13 00:00:00',(SELECT id FROM tenants LIMIT 1))"
    )
    connection.commit()
    connection.close()
    repo.fail_backup = True
    with pytest.raises(RecoveryError):
        run_backup("hourly", settings, repository=repo, clock=lambda: now + timedelta(minutes=45))
    failed_state = json.loads(settings.recovery_state_file.read_text())
    assert failed_state["last_coverage_at"] == second_state["last_coverage_at"]
    assert failed_state["last_snapshot_at"] == first_state["last_snapshot_at"]


@pytest.mark.parametrize(
    ("seconds", "allowed"),
    [(86_399.999, True), (86_400, False)],
)
def test_write_admission_exact_stale_boundary(tmp_path, monkeypatch, seconds, allowed):
    settings = _settings(tmp_path)
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    monkeypatch.setattr("api.services.recovery._target_available", lambda _settings: True)
    now = datetime(2026, 8, 13, tzinfo=UTC)
    save_state(
        settings.recovery_state_file,
        RecoveryState(
            last_coverage_at=(now - timedelta(seconds=seconds)).isoformat(),
            last_snapshot_at=(now - timedelta(seconds=seconds)).isoformat(),
            retention_configured=True,
            retention_enforced=True,
            last_result_code="recovery_ok",
        ),
    )
    if allowed:
        assert_recovery_write_allowed(settings, clock=lambda: now)
    else:
        with pytest.raises(RecoveryAdmissionError) as caught:
            assert_recovery_write_allowed(settings, clock=lambda: now)
        assert caught.value.code == "recovery_point_stale"


def test_status_rejects_naive_and_future_clock(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    save_state(settings.recovery_state_file, RecoveryState(last_coverage_at="2026-08-13T00:00:00"))
    assert recovery_status(settings).state == "corrupt"
    save_state(
        settings.recovery_state_file,
        RecoveryState(last_coverage_at="2026-08-14T00:00:00+00:00"),
    )
    assert (
        recovery_status(settings, clock=lambda: datetime(2026, 8, 13, tzinfo=UTC)).state
        == "corrupt"
    )


def test_api_trigger_rate_bound_persists_and_lock_is_cross_process(tmp_path):
    settings = _settings(tmp_path)
    now = datetime(2026, 8, 13, tzinfo=UTC)
    reserve_api_trigger(settings, clock=lambda: now)
    with pytest.raises(RecoveryError) as caught:
        reserve_api_trigger(settings, clock=lambda: now + timedelta(seconds=59, milliseconds=999))
    assert caught.value.code == "recovery_rate_limited"
    assert caught.value.retry_after == 1
    reserve_api_trigger(settings, clock=lambda: now + timedelta(seconds=60))
    with recovery_lock(settings.recovery_lock_file):
        with pytest.raises(RecoveryError) as busy:
            with recovery_lock(settings.recovery_lock_file):
                pass
        assert busy.value.code == "recovery_busy"

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys; "
                "fd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600); "
                "fcntl.flock(fd,fcntl.LOCK_EX); print('locked',flush=True); "
                "sys.stdin.read(1)"
            ),
            str(settings.recovery_lock_file),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(RecoveryError) as process_busy:
            with recovery_lock(settings.recovery_lock_file):
                pass
        assert process_busy.value.code == "recovery_busy"
    finally:
        holder.stdin.write("x")
        holder.stdin.flush()
        holder.wait(timeout=5)


def test_uncheckpointed_wal_and_concurrent_writer_produce_one_consistent_view(
    tmp_path, monkeypatch
):
    from api import models  # noqa: F401
    from api.db import Base

    settings = _settings(tmp_path)
    engine = __import__("sqlalchemy").create_engine(f"sqlite:///{settings.db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()
    connection = sqlite3.connect(settings.db_file)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "INSERT INTO accounts(id,label,swid,espn_s2_encrypted,status,created_at,tenant_id) "
        "VALUES(1,'before','a','b','active','2026-08-13 00:00:00',(SELECT id FROM tenants LIMIT 1))"
    )
    connection.execute(
        "INSERT INTO leagues(id,espn_league_id,season,account_id,lifecycle,is_public,"
        "tenant_id) VALUES(1,'before',2026,1,'pre_draft',0,"
        "(SELECT id FROM tenants LIMIT 1))"
    )
    connection.commit()  # committed but intentionally not checkpointed
    connection.close()

    started = threading.Event()
    writer_done = threading.Event()
    original = __import__("api.services.recovery", fromlist=["_tag_value"])._tag_value

    def pause_once(value, declared_type):
        if not started.is_set():
            started.set()
            assert writer_done.wait(timeout=5)
        return original(value, declared_type)

    monkeypatch.setattr("api.services.recovery._tag_value", pause_once)

    def writer():
        assert started.wait(timeout=5)
        conn = sqlite3.connect(settings.db_file)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO accounts(id,label,swid,espn_s2_encrypted,status,created_at,tenant_id) "
            "VALUES(2,'after','c','d','active','2026-08-13 00:01:00',(SELECT id FROM tenants LIMIT 1))"
        )
        conn.execute(
            "INSERT INTO leagues(id,espn_league_id,season,account_id,lifecycle,is_public,"
            "tenant_id) VALUES(2,'after',2026,2,'pre_draft',0,"
            "(SELECT id FROM tenants LIMIT 1))"
        )
        conn.commit()
        conn.close()
        writer_done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    bundle, manifest = build_logical_bundle(settings.db_file)
    thread.join(timeout=5)
    assert not thread.is_alive()
    # The read transaction was established by catalog inspection before the
    # writer committed, so both parent and child remain on the before view.
    assert manifest["tables"]["accounts"]["rows"] == 1
    assert manifest["tables"]["leagues"]["rows"] == 1
    assert b'"after"' not in bundle


def test_account_reauth_gate_precedes_decrypt_and_sync_provider(db_session, monkeypatch):
    account = Account(
        tenant_id=current_tenant_id(db_session),
        label="restored",
        swid="{REAUTH-REQUIRED}",
        espn_s2_encrypted="not-a-fernet-token",
        status="needs_reauth",
    )
    db_session.add(account)
    db_session.flush()
    league = League(tenant_id=current_tenant_id(db_session), 
        espn_league_id="9001",
        season=2026,
        account_id=account.id,
        lifecycle="pre_draft",
        is_public=False,
    )
    db_session.add(league)
    db_session.flush()
    with pytest.raises(EspnReauthRequired):
        cookies_for_account(account)

    class ProviderSpy:
        calls = 0

        def fetch_views(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    monkeypatch.setattr("api.services.sync.assert_recovery_write_allowed", lambda: None)
    provider = ProviderSpy()
    result = SyncService(db_session, espn=provider).sync_league(league)
    assert result["needs_reauth"] is True
    assert provider.calls == 0


def test_sync_stale_gate_precedes_production_provider_construction(db_session, monkeypatch):
    league = League(tenant_id=current_tenant_id(db_session), espn_league_id="9002", season=2026, lifecycle="pre_draft", is_public=True)
    db_session.add(league)
    db_session.flush()
    constructors = []

    def blocked():
        raise RecoveryAdmissionError(
            "recovery_point_stale", "A current verified recovery point is required."
        )

    monkeypatch.setattr("api.services.sync.assert_recovery_write_allowed", blocked)
    monkeypatch.setattr(
        "api.services.sync.EspnService", lambda **_kwargs: constructors.append("provider")
    )
    with pytest.raises(RecoveryAdmissionError):
        SyncService(db_session).sync_league(league)
    assert constructors == []


def test_recovery_settings_reject_secret_fields():
    assert "recovery_password" not in Settings.model_fields
    assert "recovery_secret" not in Settings.model_fields


@pytest.mark.parametrize("size", [1, 1023, 1024])
def test_trusted_prompt_is_visible_once_hidden_and_accepts_exact_bounds(size):
    synthetic = b"Z" * size
    transcript = _prompt_in_pty(synthetic + b"\n")
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert synthetic not in transcript
    assert f"PROMPT_OK:{len(synthetic)}".encode() in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in transcript


def test_trusted_prompt_accepts_multibyte_utf8_without_echo():
    synthetic = "synthetic-Δ-break-glass".encode()
    transcript = _prompt_in_pty(synthetic + b"\n")
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert synthetic not in transcript
    assert f"PROMPT_OK:{len(synthetic)}".encode() in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in transcript


def test_trusted_prompt_select_readiness_waits_for_delayed_real_pty_input():
    synthetic = b"synthetic-delayed-value"
    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - parent asserts the bounded child report
        original_terminal = recovery_module.termios.tcgetattr(0)
        original_open = recovery_module.os.open
        terminal_stat = os.fstat(0)
        terminal_identity = (terminal_stat.st_dev, terminal_stat.st_ino)

        def controlling_tty_open(path, flags, *args, **kwargs):
            if path == "/dev/tty":
                return os.dup(0)
            return original_open(path, flags, *args, **kwargs)

        class KqueueLikeSelector(recovery_module.selectors.SelectSelector):
            def register(self, fileobj, events, data=None):
                descriptor = fileobj if isinstance(fileobj, int) else fileobj.fileno()
                current = os.fstat(descriptor)
                if (current.st_dev, current.st_ino) == terminal_identity:
                    raise OSError(22, "synthetic kqueue TTY rejection")
                return super().register(fileobj, events, data)

        recovery_module.os.open = controlling_tty_open
        recovery_module.selectors.DefaultSelector = KqueueLikeSelector
        value = bytearray()
        try:
            value = recovery_module._read_break_glass_credential()
            accepted_bytes = len(value)
            recovery_module._zeroize(value)
            report = {
                "accepted_bytes": accepted_bytes,
                "terminal_restored": recovery_module._tty_state_restored(
                    recovery_module.termios.tcgetattr(0), original_terminal
                ),
                "zeroized": value == bytearray(accepted_bytes),
            }
        except BaseException as exc:
            report = recovery_module.secret_free_error(exc)
        os.write(1, b"PTY_RESULT:" + json.dumps(report, sort_keys=True).encode("ascii"))
        os._exit(0)

    transcript = bytearray()
    child_status = None
    deadline = time.monotonic() + 5.0
    try:
        while recovery_module._BREAK_GLASS_PROMPT not in transcript:
            assert time.monotonic() < deadline
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                transcript.extend(os.read(master_fd, 4096))
        waited, _ = os.waitpid(pid, os.WNOHANG)
        assert waited == 0
        wait_started = time.monotonic()
        readable, _, _ = select_module.select([master_fd], [], [], 0.2)
        waited_seconds = time.monotonic() - wait_started
        assert readable == []
        assert waited_seconds >= 0.19
        waited, _ = os.waitpid(pid, os.WNOHANG)
        assert waited == 0
        os.write(master_fd, synthetic + b"\n")
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    transcript.extend(os.read(master_fd, 4096))
                except OSError:
                    pass
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            pytest.fail("delayed synthetic prompt did not terminate")
    finally:
        if child_status is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)
        os.close(master_fd)

    assert child_status is not None and os.waitstatus_to_exitcode(child_status) == 0
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert synthetic not in transcript
    marker = b"PTY_RESULT:"
    assert marker in transcript
    report = json.loads(bytes(transcript).split(marker, 1)[1])
    assert report == {
        "accepted_bytes": len(synthetic),
        "terminal_restored": True,
        "zeroized": True,
    }


@pytest.mark.parametrize(
    "payload",
    [b"\n", b"\xff\n", b"synthetic\x00value\n", b"\x03"],
    ids=("empty", "invalid-utf8", "nul", "ctrl-c"),
)
def test_trusted_prompt_failures_are_stable_hidden_and_single_shot(payload):
    transcript = _prompt_in_pty(payload)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    visible_payload = payload.rstrip(b"\n\x03\x04")
    if visible_payload:
        assert visible_payload not in transcript
    assert b'"code": "recovery_repository_error"' in transcript
    assert b'"message": "Recovery credential is unavailable."' in transcript
    assert b"Traceback" not in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in transcript


def test_cleanup_signal_after_handler_restoration_still_zeroizes_the_read_buffer():
    """A guarded signal in the post-restoration cleanup tail must not skip zeroization."""

    synthetic = b"synthetic-cleanup-signal"
    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - parent asserts the bounded child report
        original_terminal = recovery_module.termios.tcgetattr(0)
        original_handlers = {
            terminal_signal: signal.getsignal(terminal_signal)
            for terminal_signal in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
        }
        original_open = recovery_module.os.open

        def controlling_tty_open(path, flags, *args, **kwargs):
            if path == "/dev/tty":
                return os.dup(0)
            return original_open(path, flags, *args, **kwargs)

        recovery_module.os.open = controlling_tty_open

        zeroized_lengths = []
        real_zeroize = recovery_module._zeroize

        def recording_zeroize(buffer):
            zeroized_lengths.append(len(buffer))
            return real_zeroize(buffer)

        recovery_module._zeroize = recording_zeroize

        real_write_all = recovery_module._write_all
        fired = []

        def signalling_write_all(tty_fd, payload):
            # The cleanup newline is written immediately after the original signal
            # handlers are restored and before the buffers are zeroized. That is
            # exactly the window under test, so deliver a guarded signal here.
            if payload == b"\n" and not fired:
                fired.append(True)
                os.kill(os.getpid(), signal.SIGTERM)
            return real_write_all(tty_fd, payload)

        recovery_module._write_all = signalling_write_all

        report = {}
        try:
            with recovery_module._break_glass_lifetime_signal_guard():
                recovery_module._read_break_glass_credential()
            report["envelope"] = "no-error"
        except BaseException as exc:
            report["envelope"] = recovery_module.secret_free_error(exc)
        report["signal_delivered"] = bool(fired)
        report["read_buffer_zeroized"] = (
            recovery_module._MAX_CREDENTIAL_BYTES + 2
        ) in zeroized_lengths
        report["terminal_restored"] = recovery_module._tty_state_restored(
            recovery_module.termios.tcgetattr(0), original_terminal
        )
        report["handlers_restored"] = all(
            signal.getsignal(terminal_signal) == original_handler
            for terminal_signal, original_handler in original_handlers.items()
        )
        os.write(1, b"PTY_RESULT:" + json.dumps(report, sort_keys=True).encode("ascii"))
        os._exit(0)

    transcript = bytearray()
    child_status = None
    deadline = time.monotonic() + 10.0
    try:
        while recovery_module._BREAK_GLASS_PROMPT not in transcript:
            assert time.monotonic() < deadline
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                transcript.extend(os.read(master_fd, 4096))
        os.write(master_fd, synthetic + b"\n")
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    transcript.extend(os.read(master_fd, 4096))
                except OSError:
                    pass
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            pytest.fail("cleanup-signal prompt did not terminate")
        while True:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if not readable:
                break
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            transcript.extend(chunk)
    finally:
        if child_status is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)
        os.close(master_fd)

    assert child_status is not None and os.waitstatus_to_exitcode(child_status) == 0
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert synthetic not in transcript
    marker = b"PTY_RESULT:"
    assert marker in transcript
    report = json.loads(bytes(transcript).split(marker, 1)[1])
    assert report["signal_delivered"] is True
    # The defect this guards: without a cleanup boundary the restored handler
    # raises here and both zeroization calls are skipped entirely.
    assert report["read_buffer_zeroized"] is True
    assert report["terminal_restored"] is True
    assert report["handlers_restored"] is True
    assert report["envelope"] == {
        "code": "recovery_repository_error",
        "message": "Recovery credential is unavailable.",
    }


def test_cleanup_signal_on_failure_path_zeroizes_the_secret_inside_one_boundary():
    """Both credential buffers must be zeroized without the cleanup boundary reopening.

    The read buffer and the secret are zeroized at two different points. If the
    cleanup boundary closes between them, a guarded signal delivered in that gap
    reaches the already-restored outer handler at ``cleanup_depth == 0``, raises,
    and orphans the populated secret. Asserting that no boundary exit occurs
    between the two zeroizations regresses that gap deterministically, which a
    timing-dependent signal injection cannot.
    """

    oversize = b"a" * 1025
    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - parent asserts the bounded child report
        original_open = recovery_module.os.open

        def controlling_tty_open(path, flags, *args, **kwargs):
            if path == "/dev/tty":
                return os.dup(0)
            return original_open(path, flags, *args, **kwargs)

        recovery_module.os.open = controlling_tty_open

        events = []
        real_boundary = recovery_module._break_glass_cleanup_boundary

        @contextlib.contextmanager
        def recording_boundary():
            events.append("enter")
            try:
                with real_boundary():
                    yield
            finally:
                events.append("exit")

        recovery_module._break_glass_cleanup_boundary = recording_boundary

        real_zeroize = recovery_module._zeroize

        def recording_zeroize(buffer):
            events.append("zeroize:" + str(len(buffer)))
            return real_zeroize(buffer)

        recovery_module._zeroize = recording_zeroize

        real_write_all = recovery_module._write_all
        fired = []

        def signalling_write_all(tty_fd, payload):
            if payload == b"\n" and not fired:
                fired.append(True)
                os.kill(os.getpid(), signal.SIGTERM)
            return real_write_all(tty_fd, payload)

        recovery_module._write_all = signalling_write_all

        report = {}
        try:
            with recovery_module._break_glass_lifetime_signal_guard():
                recovery_module._read_break_glass_credential()
            report["envelope"] = "no-error"
        except BaseException as exc:
            report["envelope"] = recovery_module.secret_free_error(exc)

        read_buffer_event = "zeroize:" + str(recovery_module._MAX_CREDENTIAL_BYTES + 2)
        secret_event = "zeroize:" + str(recovery_module._MAX_CREDENTIAL_BYTES)
        report["signal_delivered"] = bool(fired)
        report["read_buffer_zeroized"] = read_buffer_event in events
        report["secret_zeroized"] = secret_event in events
        if report["read_buffer_zeroized"] and report["secret_zeroized"]:
            start = events.index(read_buffer_event)
            finish = events.index(secret_event, start)
            report["boundary_reopened_between"] = "exit" in events[start:finish]
        else:
            report["boundary_reopened_between"] = None
        os.write(1, b"PTY_RESULT:" + json.dumps(report, sort_keys=True).encode("ascii"))
        os._exit(0)

    transcript = bytearray()
    child_status = None
    deadline = time.monotonic() + 10.0
    try:
        while recovery_module._BREAK_GLASS_PROMPT not in transcript:
            assert time.monotonic() < deadline
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                transcript.extend(os.read(master_fd, 4096))
        os.write(master_fd, oversize + b"\n")
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    transcript.extend(os.read(master_fd, 4096))
                except OSError:
                    pass
            waited, child_status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            pytest.fail("oversize cleanup-signal prompt did not terminate")
        while True:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if not readable:
                break
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            transcript.extend(chunk)
    finally:
        if child_status is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(pid, 0)
        os.close(master_fd)

    assert child_status is not None and os.waitstatus_to_exitcode(child_status) == 0
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert oversize not in transcript
    marker = b"PTY_RESULT:"
    assert marker in transcript
    report = json.loads(bytes(transcript).split(marker, 1)[1])
    assert report["signal_delivered"] is True
    assert report["read_buffer_zeroized"] is True
    assert report["secret_zeroized"] is True
    # The load-bearing assertion: the boundary must not close between them.
    assert report["boundary_reopened_between"] is False
    assert report["envelope"] == {
        "code": "recovery_repository_error",
        "message": "Recovery credential is unavailable.",
    }


def _restored_bundle(tmp_path, *, leagues=(), picks=0):
    """A schema-valid scratch database for read-closure tests."""

    import sqlalchemy

    from api import models  # noqa: F401
    from api.db import Base

    db = tmp_path / "restored.db"
    engine = sqlalchemy.create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    if leagues or picks:
        connection = sqlite3.connect(db)
        for index, season in enumerate(leagues, start=1):
            connection.execute(
                "INSERT INTO leagues(id,espn_league_id,season,name,lifecycle,is_public,"
                "tenant_id) VALUES(?,?,?,?,?,1,(SELECT id FROM tenants LIMIT 1))",
                (index, f"L{index}", season, f"League {index}", "drafted"),
            )
        for overall in range(1, picks + 1):
            connection.execute(
                "INSERT INTO draft_picks(league_id,overall,round,round_pick,keeper,autodraft)"
                " VALUES(?,?,?,?,0,0)",
                (1, overall, 1, overall),
            )
        connection.commit()
        connection.close()
    return db


def test_read_closure_rejects_a_rowless_bundle(tmp_path):
    """A schema-valid but empty bundle must fail, not report zeroes as success.

    The previous implementation hardcoded ``PortfolioFilters(season=2025)`` and
    returned raw counts, so an empty restore reported four low/zero counts
    alongside ``integrity: ok`` and read as a verified recovery point.
    """

    db = _restored_bundle(tmp_path)
    with pytest.raises(recovery_module.RecoveryError) as caught:
        recovery_module.verify_restored_read_closure(db)
    assert caught.value.code == "recovery_bundle_invalid"
    assert "synthetic" not in caught.value.safe_message.lower()


def test_read_closure_derives_the_season_from_the_restored_bundle(tmp_path):
    """The filter season comes from the bundle, not a hardcoded literal."""

    db = _restored_bundle(tmp_path, leagues=(2026,))
    closure = recovery_module.verify_restored_read_closure(db)
    assert closure["restored_season"] == 2026
    assert closure["configured_season"] == 2026
    assert closure["portfolio_rows"] > 0


def test_read_closure_season_is_not_hardcoded(tmp_path):
    """A bundle from an older season is still read on its own terms."""

    db = _restored_bundle(tmp_path, leagues=(2024,))
    closure = recovery_module.verify_restored_read_closure(db)
    assert closure["restored_season"] == 2024
    assert closure["portfolio_rows"] > 0


def test_trusted_prompt_eof_is_stable_hidden_and_restores_terminal():
    transcript = _prompt_in_pty(None, fake_eof=True)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert b'"code": "recovery_repository_error"' in transcript
    assert b'"message": "Recovery credential is unavailable."' in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in transcript


@pytest.mark.parametrize(
    "payload",
    [
        b"a" * 1025 + b"\n",
        b"synthetic\r\n",
        b"synthetic\nextra\n",
        b"synthetic\rqueued-command\n",
    ],
    ids=("1025", "crlf", "multiline", "trailing"),
)
def test_trusted_prompt_rejects_and_drains_oversize_or_trailing_input(payload):
    transcript = _prompt_in_pty(payload)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert payload.rstrip(b"\r\n") not in transcript
    assert b'"code": "recovery_repository_error"' in transcript
    assert b'"message": "Recovery credential is unavailable."' in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in transcript


def test_trusted_prompt_fake_clock_timeout_is_stable_and_restores_terminal():
    timeout = _prompt_in_pty(None, fake_timeout=True)
    assert timeout.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert b'"code": "recovery_repository_error"' in timeout
    assert b'"message": "Recovery credential is unavailable."' in timeout
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in timeout


def test_trusted_prompt_retries_and_verifies_transient_terminal_restore_failure():
    transcript = _prompt_in_pty(b"synthetic\n", fail_restore=True)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert b"synthetic" not in transcript
    assert b"PROMPT_OK:9" in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0" in transcript


def test_trusted_prompt_persistent_primary_restore_uses_independent_fallback():
    transcript = _prompt_in_pty(b"synthetic\n", fail_restore_always=True)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert b"synthetic" not in transcript
    assert b"PROMPT_OK:9" in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0:CC_RESTORED:SIGNALS_RESTORED" in transcript


def test_independent_tty_state_helper_retains_real_pty_session_and_restores():
    pid, master_fd = pty.fork()
    if pid == 0:  # pragma: no cover - parent asserts the bounded child report
        tty_fd = os.dup(0)
        original = recovery_module.termios.tcgetattr(tty_fd)
        argv = [
            sys.executable,
            "-I",
            "-S",
            "-c",
            recovery_module._TTY_STATE_EXEC_CODE,
            str(tty_fd),
            recovery_module._STTY_PATH,
            "-g",
        ]
        detached = BoundedSubprocessRunner().run(
            argv,
            env=recovery_module._minimal_env(),
            max_output=recovery_module._TTY_STATE_MAX_BYTES,
            pass_fds=(tty_fd,),
            timeout_seconds=recovery_module._TTY_STATE_TIMEOUT_SECONDS,
        )
        session_modes = []

        class SessionRecordingRunner:
            def run(self, argv, **kwargs):
                session_modes.append(kwargs.get("interactive"))
                return BoundedSubprocessRunner().run(argv, **kwargs)

        recovery_module.BoundedSubprocessRunner = SessionRecordingRunner
        state = recovery_module._capture_independent_tty_state(tty_fd)
        mutated = list(original)
        mutated[6] = list(original[6])
        mutated[3] &= ~(
            recovery_module.termios.ECHO
            | recovery_module.termios.ECHONL
            | recovery_module.termios.ICANON
        )
        recovery_module.termios.tcsetattr(
            tty_fd, recovery_module.termios.TCSANOW, mutated
        )
        restored = state is not None and recovery_module._restore_independent_tty_state(
            tty_fd, state, original
        )
        current = recovery_module.termios.tcgetattr(tty_fd)
        report = {
            "detached_failed": detached.returncode != 0,
            "detached_stdout_bytes": len(detached.stdout),
            "detached_stderr_bytes": len(detached.stderr),
            "session_modes": session_modes,
            "state_captured": state is not None,
            "restored": restored,
            "terminal_restored": recovery_module._tty_state_restored(current, original),
        }
        os.close(tty_fd)
        os.write(1, json.dumps(report, sort_keys=True).encode("ascii"))
        os._exit(0)

    transcript = bytearray()
    deadline = time.monotonic() + 5.0
    status = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select_module.select([master_fd], [], [], 0.05)
            if readable:
                try:
                    transcript.extend(os.read(master_fd, 4096))
                except OSError:
                    pass
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                break
        else:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            pytest.fail("synthetic terminal-state helper did not terminate")
    finally:
        os.close(master_fd)

    assert status is not None and os.waitstatus_to_exitcode(status) == 0
    assert recovery_module._BREAK_GLASS_PROMPT not in transcript
    report = json.loads(bytes(transcript))
    assert report["session_modes"] == [True, True]
    assert report["state_captured"] is True
    assert report["restored"] is True
    assert report["terminal_restored"] is True
    assert report["detached_stdout_bytes"] <= recovery_module._TTY_STATE_MAX_BYTES
    assert report["detached_stderr_bytes"] <= recovery_module._TTY_STATE_MAX_BYTES
    if report["detached_failed"]:
        assert report["detached_stderr_bytes"] > 0


@pytest.mark.parametrize(
    "payload",
    [b"\x1c", b"\x1a"],
    ids=("ctrl-backslash", "ctrl-z"),
)
def test_trusted_prompt_job_control_bytes_restore_and_release_resources(
    tmp_path, payload
):
    transcript = _prompt_in_pty(payload, cleanup_root=tmp_path)
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert payload not in transcript
    assert b'"code": "recovery_repository_error"' in transcript
    assert b'"message": "Recovery credential is unavailable."' in transcript
    assert b"Traceback" not in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0:CC_RESTORED:SIGNALS_RESTORED" in transcript
    assert b":LOCK_RELEASED:SCRATCH_RETRYABLE" in transcript


def test_trusted_prompt_hangup_is_caught_until_cleanup_completes(tmp_path):
    transcript = _prompt_in_pty(
        None, terminal_signal=signal.SIGHUP, cleanup_root=tmp_path
    )
    assert transcript.count(recovery_module._BREAK_GLASS_PROMPT) == 1
    assert b'"code": "recovery_repository_error"' in transcript
    assert b'"message": "Recovery credential is unavailable."' in transcript
    assert b"Traceback" not in transcript
    assert b":ECHO_ON:ISIG_ON:QUEUED:0:CC_RESTORED:SIGNALS_RESTORED" in transcript
    assert b":LOCK_RELEASED:SCRATCH_RETRYABLE" in transcript


def test_trusted_prompt_requires_controlling_tty():
    script = (
        "import json; from api.services.recovery import "
        "_read_break_glass_credential,secret_free_error; "
        "\ntry: _read_break_glass_credential()"
        "\nexcept BaseException as exc: print(json.dumps(secret_free_error(exc),sort_keys=True))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parent.parent,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        start_new_session=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "code": "recovery_repository_error",
        "message": "Recovery credential is unavailable.",
    }
    assert result.stderr == b""


@pytest.mark.parametrize(
    "secret",
    [
        bytearray(b"a" * 1025),
        bytearray(b"synthetic\nvalue"),
        bytearray(b"synthetic\rvalue"),
        bytearray(b"\xff"),
    ],
    ids=("oversize", "lf", "cr", "invalid-utf8"),
)
def test_break_glass_validation_rejects_invalid_bytes(secret):
    with pytest.raises(ValueError):
        recovery_module._validate_break_glass_credential(secret)


@pytest.mark.parametrize(
    "secret",
    [
        bytearray(b"a"),
        bytearray("Δ".encode()),
        bytearray("𐍈".encode()),
        bytearray(b"a" * 1024),
    ],
)
def test_break_glass_byte_only_utf8_validation_accepts_valid_sequences(secret):
    recovery_module._validate_break_glass_credential(secret)


@pytest.mark.parametrize(
    "secret",
    [
        bytearray(b"\xc0\x80"),
        bytearray(b"\xe0\x80\x80"),
        bytearray(b"\xed\xa0\x80"),
        bytearray(b"\xf4\x90\x80\x80"),
        bytearray(b"\xf0\x90\x80"),
    ],
)
def test_break_glass_byte_only_utf8_validation_rejects_invalid_sequences(secret):
    with pytest.raises(ValueError):
        recovery_module._validate_break_glass_credential(secret)


def test_break_glass_utf8_validator_creates_no_decoded_text_or_bytes_copy():
    source = inspect.getsource(recovery_module._validate_break_glass_credential)
    assert ".decode(" not in source
    assert "bytes(" not in source
    assert "codecs" not in source


def test_break_glass_context_best_effort_zeroizes_on_failure(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    repository = ResticRepository(settings, runner=object(), topology=object())
    synthetic = bytearray(b"synthetic-break-glass")
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    monkeypatch.setattr(
        repository, "validate_break_glass_tool", lambda _expected=None: authority
    )
    monkeypatch.setattr(
        recovery_module, "_read_break_glass_credential", lambda: synthetic
    )
    with pytest.raises(RuntimeError):
        with repository.break_glass_credential() as credential:
            secret, observed_authority = credential
            assert secret is synthetic
            assert observed_authority is authority
            raise RuntimeError("synthetic failure")
    assert synthetic == bytearray(len(synthetic))


@pytest.mark.parametrize("terminal_signal", recovery_module._BREAK_GLASS_LIFETIME_SIGNALS)
def test_break_glass_lifetime_guard_suppresses_second_signal_and_restores_handler(
    monkeypatch, terminal_signal
):
    installed = {}
    original = object()

    monkeypatch.setattr(recovery_module.signal, "getsignal", lambda _signal: original)

    def capture_handler(signum, handler):
        installed[signum] = handler

    monkeypatch.setattr(recovery_module.signal, "signal", capture_handler)
    with pytest.raises(RecoveryError) as caught:
        with recovery_module._break_glass_lifetime_signal_guard():
            handler = installed[terminal_signal]
            with pytest.raises(KeyboardInterrupt):
                handler(terminal_signal, None)
            handler(terminal_signal, None)
    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Recovery credential is unavailable."
    assert all(handler is original for handler in installed.values())


def test_break_glass_lifetime_guard_normal_exit_restores_handlers(monkeypatch):
    installed = {}
    originals = {
        terminal_signal: object()
        for terminal_signal in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
    }
    monkeypatch.setattr(
        recovery_module.signal,
        "getsignal",
        lambda terminal_signal: originals[terminal_signal],
    )
    monkeypatch.setattr(
        recovery_module.signal,
        "signal",
        lambda terminal_signal, handler: installed.__setitem__(terminal_signal, handler),
    )
    with recovery_module._break_glass_lifetime_signal_guard():
        assert all(installed[item] is not originals[item] for item in originals)
    assert installed == originals


@pytest.mark.parametrize(
    "injection_point",
    (
        "after-handler-1",
        "after-handler-2",
        "after-handler-3",
        "before-context-reset",
        "after-context-reset",
    ),
)
def test_break_glass_handler_restoration_is_atomic_and_consumes_pending_signal(
    monkeypatch, injection_point
):
    class RestorationEscape(BaseException):
        pass

    def escaped_original_handler(_signum, _frame):
        raise RestorationEscape

    initial_handlers = {
        guarded: signal.getsignal(guarded)
        for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
    }
    initial_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    original_signal = recovery_module.signal.signal
    original_reset = recovery_module._reset_break_glass_signal_context
    restored_handlers = 0

    for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS:
        original_signal(guarded, escaped_original_handler)

    def instrumented_signal(signum, handler):
        nonlocal restored_handlers
        result = original_signal(signum, handler)
        if handler is escaped_original_handler:
            restored_handlers += 1
            if injection_point == f"after-handler-{restored_handlers}":
                os.kill(os.getpid(), signal.SIGHUP)
        return result

    def instrumented_reset(token):
        if injection_point == "before-context-reset":
            os.kill(os.getpid(), signal.SIGHUP)
        original_reset(token)
        if injection_point == "after-context-reset":
            os.kill(os.getpid(), signal.SIGHUP)

    monkeypatch.setattr(recovery_module.signal, "signal", instrumented_signal)
    monkeypatch.setattr(
        recovery_module, "_reset_break_glass_signal_context", instrumented_reset
    )
    outer_cleanup = False
    try:
        with pytest.raises(RecoveryError) as caught:
            try:
                with recovery_module._break_glass_lifetime_signal_guard():
                    pass
            finally:
                outer_cleanup = True
        assert caught.value.code == "recovery_repository_error"
        assert caught.value.safe_message == "Recovery credential is unavailable."
        assert outer_cleanup is True
        assert restored_handlers == len(recovery_module._BREAK_GLASS_LIFETIME_SIGNALS)
        assert all(
            signal.getsignal(guarded) is escaped_original_handler
            for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
        )
        assert recovery_module._ACTIVE_BREAK_GLASS_SIGNAL_STATE.get() is None
        assert signal.SIGHUP not in signal.sigpending()
        assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == initial_mask
    finally:
        for guarded, initial_handler in initial_handlers.items():
            original_signal(guarded, initial_handler)


@pytest.mark.parametrize("terminal_signal", [signal.SIGTERM, signal.SIGQUIT])
def test_break_glass_first_signal_at_zeroize_entry_is_deferred_until_erased(
    tmp_path, monkeypatch, terminal_signal
):
    settings = _settings(tmp_path)
    repository = ResticRepository(settings, runner=object(), topology=object())
    synthetic = bytearray(b"synthetic-zeroize-boundary")
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    original_handlers = {
        guarded: signal.getsignal(guarded)
        for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
    }
    monkeypatch.setattr(
        repository, "validate_break_glass_tool", lambda _expected=None: authority
    )
    monkeypatch.setattr(
        recovery_module, "_read_break_glass_credential", lambda: synthetic
    )
    original_zeroize = recovery_module._zeroize
    injected = False

    def signal_then_zeroize(buffer):
        nonlocal injected
        if buffer is synthetic and not injected:
            injected = True
            os.kill(os.getpid(), terminal_signal)
        original_zeroize(buffer)

    monkeypatch.setattr(recovery_module, "_zeroize", signal_then_zeroize)
    with pytest.raises(RecoveryError) as caught:
        with repository.break_glass_credential():
            pass
    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Recovery credential is unavailable."
    assert injected is True
    assert synthetic == bytearray(len(synthetic))
    assert all(
        signal.getsignal(guarded) == original_handler
        for guarded, original_handler in original_handlers.items()
    )


def test_break_glass_first_signal_before_password_read_close_finishes_cleanup(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    repository = ResticRepository(settings, runner=object(), topology=object())
    synthetic = bytearray(b"synthetic-descriptor-boundary")
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    topology = (
        StorageIdentity("synthetic-source", "internal", "source"),
        StorageIdentity("synthetic-target", "external", "target"),
    )
    pin = RepositoryPin(-1, ".", topology, "", 1, 2, -1)
    original_handlers = {
        guarded: signal.getsignal(guarded)
        for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
    }
    monkeypatch.setattr(
        repository, "validate_break_glass_tool", lambda _expected=None: authority
    )
    monkeypatch.setattr(repository, "_assert_pin", lambda _pin: None)
    monkeypatch.setattr(
        recovery_module, "_read_break_glass_credential", lambda: synthetic
    )
    original_close = recovery_module.os.close
    password_read = -1
    injected = False

    def signal_before_password_close(descriptor):
        nonlocal injected
        if descriptor == password_read and not injected:
            injected = True
            os.kill(os.getpid(), signal.SIGTERM)
        original_close(descriptor)

    monkeypatch.setattr(recovery_module.os, "close", signal_before_password_close)
    with pytest.raises(RecoveryError) as caught:
        with repository.break_glass_credential() as credential:
            secret, observed_authority = credential
            with repository._break_glass_password_descriptor(
                secret,
                binary_authority=observed_authority,
                pin=pin,
            ) as descriptor:
                password_read = descriptor
    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Recovery credential is unavailable."
    assert injected is True
    with pytest.raises(OSError):
        os.fstat(password_read)
    assert synthetic == bytearray(len(synthetic))
    assert all(
        signal.getsignal(guarded) == original_handler
        for guarded, original_handler in original_handlers.items()
    )


def test_break_glass_first_signal_at_nonsignal_child_cleanup_is_deferred(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    repository = ResticRepository(settings, runner=object(), topology=object())
    synthetic = bytearray(b"synthetic-child-cleanup-boundary")
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    original_handlers = {
        guarded: signal.getsignal(guarded)
        for guarded in recovery_module._BREAK_GLASS_LIFETIME_SIGNALS
    }
    monkeypatch.setattr(
        repository, "validate_break_glass_tool", lambda _expected=None: authority
    )
    monkeypatch.setattr(
        recovery_module, "_read_break_glass_credential", lambda: synthetic
    )
    original_popen = recovery_module.subprocess.Popen
    children = []

    def recording_popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(recovery_module.subprocess, "Popen", recording_popen)
    original_killpg = recovery_module.os.killpg
    injected = False

    def signal_when_cleanup_starts(process_group, child_signal):
        nonlocal injected
        if not injected:
            injected = True
            os.kill(os.getpid(), signal.SIGTERM)
        original_killpg(process_group, child_signal)

    monkeypatch.setattr(recovery_module.os, "killpg", signal_when_cleanup_starts)
    with pytest.raises(RecoveryError) as caught:
        with repository.break_glass_credential():
            BoundedSubprocessRunner().run(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
                timeout_seconds=0.01,
            )
    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Recovery credential is unavailable."
    assert injected is True
    assert len(children) == 1
    assert children[0].poll() is not None
    assert synthetic == bytearray(len(synthetic))
    assert all(
        signal.getsignal(guarded) == original_handler
        for guarded, original_handler in original_handlers.items()
    )


def _empty_source(settings: Settings) -> None:
    from api import models  # noqa: F401
    from api.db import Base

    engine = __import__("sqlalchemy").create_engine(f"sqlite:///{settings.db_file}")
    Base.metadata.create_all(engine)
    engine.dispose()


class _PinnedBackupRunner:
    def __init__(
        self,
        repository_path: Path,
        *,
        swap_at: int | None = None,
        restore_during_call: bool = False,
    ) -> None:
        self.repository_path = repository_path
        self.swap_at = swap_at
        self.restore_during_call = restore_during_call
        self.held_path = repository_path.with_name(f".{repository_path.name}.held")
        self.original_inode = repository_path.stat().st_ino
        self.aba_observed = False
        self.calls: list[tuple[list[str], dict]] = []
        self.bundle: bytes | None = None
        self.snapshot_id = "d" * 64
        self.dump_calls = 0
        self.snapshot_calls = 0

    def run(self, argv, **kwargs):
        call_number = len(self.calls) + 1
        copied = (list(argv), dict(kwargs))
        self.calls.append(copied)
        assert kwargs["timeout_seconds"] is not None
        assert kwargs["timeout_seconds"] > 0
        assert argv[1:3] == ["-r", kwargs.get("expected_repository", argv[2])]
        assert argv[2] == "."
        assert str(self.repository_path) not in argv
        assert len(kwargs["pass_fds"]) == 3
        assert kwargs["cwd_fd"] == kwargs["pass_fds"][0]
        for descriptor in kwargs["pass_fds"]:
            os.fstat(descriptor)
        assert "--password-command" not in argv
        password_index = argv.index("--password-file")
        assert argv[password_index + 1] == f"/dev/fd/{kwargs['pass_fds'][2]}"

        if self.swap_at == call_number:
            self.repository_path.rename(self.held_path)
            self.repository_path.mkdir()
            assert os.fstat(kwargs["pass_fds"][0]).st_ino == self.original_inode
            assert self.repository_path.stat().st_ino != self.original_inode
            self.aba_observed = True

        if "cat" in argv and "config" in argv:
            result = ProcessResult(0, json.dumps({"id": "c" * 64}).encode(), b"")
        elif "dump" in argv:
            self.dump_calls += 1
            if self.dump_calls == 1:
                result = ProcessResult(1, b"", b"synthetic empty repository")
            else:
                assert self.bundle is not None
                result = ProcessResult(0, self.bundle, b"")
        elif "snapshots" in argv:
            self.snapshot_calls += 1
            rows = []
            if self.snapshot_calls > 1:
                rows = [{"id": self.snapshot_id, "tags": [recovery_module.RECOVERY_TAG]}]
            result = ProcessResult(0, json.dumps(rows).encode(), b"")
        elif "backup" in argv:
            self.bundle = kwargs["input_bytes"]
            summary = {
                "message_type": "summary",
                "snapshot_id": self.snapshot_id,
            }
            result = ProcessResult(0, (json.dumps(summary) + "\n").encode(), b"")
        elif "check" in argv:
            result = ProcessResult(0, b"", b"")
        else:
            raise AssertionError(f"unexpected synthetic restic argv: {argv!r}")
        if self.held_path.exists() and self.restore_during_call:
            self.restore_path()
        return result

    def restore_path(self) -> None:
        if self.held_path.exists():
            self.repository_path.rmdir()
            self.held_path.rename(self.repository_path)


def _production_backup_repository(settings: Settings, runner) -> ResticRepository:
    source_stat = settings.db_file.stat()
    repository_stat = Path(settings.recovery_repository).stat()

    class StaticTopology:
        def resolve(self, path):
            current = Path(path).stat()
            if Path(path) == Path(settings.recovery_repository):
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

    repository = ResticRepository(settings, runner=runner, topology=StaticTopology())
    repository.validate_tool = lambda: None
    assert repository_stat.st_ino == Path(settings.recovery_repository).stat().st_ino
    return repository


def test_break_glass_calls_share_pin_and_never_name_automation_credential(
    tmp_path
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _manifest = build_logical_bundle(settings.db_file)

    class Runner:
        def __init__(self):
            self.calls = []
            self.password_fds = []

        def run(self, argv, **kwargs):
            self.calls.append((list(argv), dict(kwargs)))
            assert kwargs["interactive"] is False
            assert kwargs["timeout_seconds"] == recovery_module._RESTIC_BREAK_GLASS_TIMEOUT_SECONDS
            assert len(kwargs["pass_fds"]) == 3
            assert argv[1:3] == ["-r", "."]
            assert kwargs["cwd_fd"] == kwargs["pass_fds"][0]
            assert "--password-command" not in argv
            assert str(settings.recovery_credential_command) not in argv
            password_fd = kwargs["pass_fds"][2]
            os.fstat(password_fd)
            self.password_fds.append(password_fd)
            assert os.read(password_fd, 4096) == b"synthetic-break-glass"
            assert os.read(password_fd, 1) == b""
            password_index = argv.index("--password-file")
            assert argv[password_index + 1] == f"/dev/fd/{password_fd}"
            if "snapshots" in argv:
                return ProcessResult(
                    0,
                    json.dumps(
                        [
                            {
                                "id": "a" * 64,
                                "tags": [recovery_module.RECOVERY_TAG],
                            }
                        ]
                    ).encode(),
                    b"",
                )
            if "dump" in argv:
                return ProcessResult(0, bundle, b"")
            if "check" in argv:
                return ProcessResult(0, b"", b"")
            raise AssertionError(f"unexpected break-glass argv: {argv!r}")

    runner = Runner()
    repository = _production_backup_repository(settings, runner)
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    repository.validate_break_glass_tool = lambda _expected=None: authority
    secret = bytearray(b"synthetic-break-glass")
    with recovery_lock(settings.recovery_lock_file) as lock_fd:
        with repository.pin_break_glass_repository(
            settings.db_file, lock_fd=lock_fd
        ) as pin:
            repository.break_glass_check(
                secret=secret, binary_authority=authority, pin=pin
            )
            with pytest.raises(OSError):
                os.fstat(runner.password_fds[-1])
            snapshots = repository.break_glass_snapshots(
                secret=secret, binary_authority=authority, pin=pin
            )
            with pytest.raises(OSError):
                os.fstat(runner.password_fds[-1])
            restored = repository.break_glass_bundle(
                "latest", secret=secret, binary_authority=authority, pin=pin
            )
            with pytest.raises(OSError):
                os.fstat(runner.password_fds[-1])

    assert snapshots[0]["id"] == "a" * 64
    assert restored == bundle
    assert len(runner.calls) == 3
    assert len({call[0][2] for call in runner.calls}) == 1
    assert len({call[1]["cwd_fd"] for call in runner.calls}) == 1
    assert len(runner.password_fds) == 3


@pytest.mark.parametrize(
    ("replace_after", "mutation"),
    [
        ("prompt", "identical-replacement"),
        ("check", "identical-replacement"),
        ("snapshots", "identical-replacement"),
        ("check", "in-place"),
    ],
)
def test_break_glass_revalidates_binary_before_every_secret_handoff(
    tmp_path, monkeypatch, replace_after, mutation
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    binary = Path(settings.recovery_restic_path)
    approved = binary.read_bytes()
    settings = settings.model_copy(
        update={"recovery_restic_sha256": hashlib.sha256(approved).hexdigest()}
    )
    operations = []
    prompted = []

    def mutate_binary():
        if mutation == "identical-replacement":
            held = binary.with_name("synthetic-old-restic")
            binary.rename(held)
            binary.write_bytes(approved)
            binary.chmod(0o700)
            assert binary.stat().st_ino != held.stat().st_ino
            assert binary.read_bytes() == held.read_bytes()
            return
        changed = bytearray(approved)
        changed[0] ^= 1
        with binary.open("r+b") as handle:
            handle.write(changed)
            handle.flush()
        assert binary.stat().st_size == len(approved)

    class Runner:
        def run(self, argv, **kwargs):
            if argv == [str(binary), "version"]:
                return ProcessResult(
                    0,
                    f"restic {settings.recovery_restic_version}\n".encode(),
                    b"",
                )
            operation = "check" if "check" in argv else "snapshots" if "snapshots" in argv else "dump"
            operations.append(operation)
            password_fd = kwargs["pass_fds"][-1]
            assert os.read(password_fd, 4096) == b"synthetic-break-glass"
            assert os.read(password_fd, 1) == b""
            if operation == replace_after:
                mutate_binary()
            if operation == "snapshots":
                return ProcessResult(
                    0,
                    json.dumps(
                        [{"id": "a" * 64, "tags": [recovery_module.RECOVERY_TAG]}]
                    ).encode(),
                    b"",
                )
            return ProcessResult(0, b"", b"")

    runner = Runner()
    repository = _production_backup_repository(settings, runner)

    def synthetic_prompt():
        prompted.append(True)
        if replace_after == "prompt":
            mutate_binary()
        return bytearray(b"synthetic-break-glass")

    monkeypatch.setattr(
        recovery_module, "_read_break_glass_credential", synthetic_prompt
    )
    with recovery_lock(settings.recovery_lock_file) as lock_fd:
        with repository.pin_break_glass_repository(
            settings.db_file, lock_fd=lock_fd
        ) as pin:
            with pytest.raises(RecoveryError) as caught:
                with repository.break_glass_credential() as credential:
                    secret, authority = credential
                    repository.break_glass_check(
                        secret=secret, binary_authority=authority, pin=pin
                    )
                    repository.break_glass_snapshots(
                        secret=secret, binary_authority=authority, pin=pin
                    )
                    repository.break_glass_bundle(
                        "latest",
                        secret=secret,
                        binary_authority=authority,
                        pin=pin,
                    )
    assert caught.value.code == "recovery_tool_invalid"
    expected_message = (
        "Recovery tool identity changed."
        if mutation == "identical-replacement"
        else "Recovery tool digest is invalid."
    )
    assert caught.value.safe_message == expected_message
    assert prompted == [True]
    expected = {
        "prompt": [],
        "check": ["check"],
        "snapshots": ["check", "snapshots"],
    }
    assert operations == expected[replace_after]


def test_break_glass_wrong_secret_and_malicious_output_are_not_forwarded(
    tmp_path, capsys
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    canary = b"synthetic-secret-path-id-\x1b[31m"

    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **kwargs):
            self.calls.append((list(argv), dict(kwargs)))
            password_fd = kwargs["pass_fds"][-1]
            assert os.read(password_fd, 4096) == b"synthetic-wrong-secret"
            assert os.read(password_fd, 1) == b""
            assert b"synthetic-wrong-secret" not in "\0".join(argv).encode()
            assert b"synthetic-wrong-secret" not in json.dumps(kwargs["env"]).encode()
            return ProcessResult(1, canary, canary)

    runner = Runner()
    repository = _production_backup_repository(settings, runner)
    authority = recovery_module._BinaryAuthority(1, 2, 3, "0" * 64)
    repository.validate_break_glass_tool = lambda _expected=None: authority
    secret = bytearray(b"synthetic-wrong-secret")
    with recovery_lock(settings.recovery_lock_file) as lock_fd:
        with repository.pin_break_glass_repository(
            settings.db_file, lock_fd=lock_fd
        ) as pin:
            with pytest.raises(RecoveryError) as caught:
                repository.break_glass_check(
                    secret=secret, binary_authority=authority, pin=pin
                )
    envelope = json.dumps(secret_free_error(caught.value), sort_keys=True)
    captured = capsys.readouterr()
    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Encrypted repository operation failed."
    assert len(runner.calls) == 1
    assert "synthetic-secret" not in envelope
    assert "\x1b" not in envelope
    assert captured.out == captured.err == ""


def test_diskutil_adapter_receives_no_recovery_or_repository_authority(
    tmp_path, monkeypatch
):
    source = tmp_path / "synthetic-source"
    source.write_bytes(b"synthetic")
    repository = tmp_path / "synthetic-repository"
    repository.mkdir()
    _mark_mount_devices(monkeypatch, {tmp_path: 99108})
    runner = FakeDiskutilRunner(
        {"disk99108": ProcessResult(0, _diskutil_payload(), b"")}
    )
    lock_path = tmp_path / "synthetic.lock"
    with recovery_lock(lock_path) as lock_fd:
        with recovery_module._bounded_path_authority(
            repository, require_directory=True
        ) as authority:
            identity = _resolver(runner).resolve(source)
            kwargs = runner.calls[0][1]
            assert tuple(kwargs.get("pass_fds", ())) == ()
            assert lock_fd not in tuple(kwargs.get("pass_fds", ()))
            assert authority.fd not in tuple(kwargs.get("pass_fds", ()))
    assert identity.object_token is not None


def test_credential_broker_relay_is_anonymous_and_authority_free(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    repository_path = Path(settings.recovery_repository)
    settings.recovery_lock_file.touch(mode=0o600)
    repository_stat = repository_path.stat()
    lock_stat = settings.recovery_lock_file.stat()
    credential_marker = tmp_path / "credential-authority-marker"
    command = Path(settings.recovery_credential_command)
    command.write_text(
        f"""#!{sys.executable}
import os
from pathlib import Path

expected = {((repository_stat.st_dev, repository_stat.st_ino), (lock_stat.st_dev, lock_stat.st_ino))!r}
marker = Path({str(credential_marker)!r})
def authority_present():
    identities = set()
    for descriptor in range(128):
        try:
            current = os.fstat(descriptor)
        except OSError:
            continue
        identities.add((current.st_dev, current.st_ino))
    return any(item in identities for item in expected)
child = os.fork()
if child == 0:
    with marker.open("ab") as handle:
        handle.write(b"child-bad\\n" if authority_present() else b"child-clean\\n")
    os._exit(0)
os.waitpid(child, 0)
with marker.open("ab") as handle:
    handle.write(b"parent-bad\\n" if authority_present() else b"parent-clean\\n")
os.write(1, os.urandom(32))
""",
        encoding="utf-8",
    )
    command.chmod(0o700)
    binary = Path(settings.recovery_restic_path)
    binary.write_text(
        f"""#!{sys.executable}
import json
import os
import sys

args = sys.argv[1:]
if "--password-command" in args:
    raise SystemExit(80)
password_name = args[args.index("--password-file") + 1]
with open(password_name, "rb", buffering=0) as handle:
    password = handle.read()
if len(password) != 32:
    raise SystemExit(81)
if "cat" in args and "config" in args:
    print(json.dumps({{"id": {'c' * 64!r}}}))
elif "check" in args:
    pass
else:
    raise SystemExit(82)
""",
        encoding="utf-8",
    )
    binary.chmod(0o700)

    source_stat = settings.db_file.stat()

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
    real_popen = recovery_module.subprocess.Popen
    records = []

    def authority_fds():
        found = set()
        expected = {
            (repository_stat.st_dev, repository_stat.st_ino),
            (lock_stat.st_dev, lock_stat.st_ino),
        }
        for descriptor in range(128):
            try:
                current = os.fstat(descriptor)
            except OSError:
                continue
            if (current.st_dev, current.st_ino) in expected:
                found.add(descriptor)
        return found

    def recording_popen(*args, **kwargs):
        argv = tuple(map(str, args[0]))
        passed = set(kwargs.get("pass_fds", ()))
        authority = authority_fds()
        if _CREDENTIAL_BROKER_CODE_FOR_TEST in " ".join(argv):
            kind = "broker"
        elif (
            len(argv) > 6
            and argv[0] == sys.executable
            and argv[1:5]
            == ("-I", "-S", "-c", recovery_module._RESTIC_DIRECTORY_EXEC_CODE)
            and argv[6] == str(binary)
        ):
            kind = "restic"
        elif "_path_open_worker_entry" in " ".join(argv):
            kind = "path-helper"
        else:
            kind = "other"
        records.append((kind, argv, passed, authority))
        return real_popen(*args, **kwargs)

    _CREDENTIAL_BROKER_CODE_FOR_TEST = "_credential_broker_entry"
    monkeypatch.setattr(recovery_module.subprocess, "Popen", recording_popen)
    with recovery_lock(settings.recovery_lock_file) as lock_fd:
        with repository.pin_repository(settings.db_file, lock_fd=lock_fd) as pin:
            repository.check(pin=pin)

    assert credential_marker.read_text(encoding="ascii").splitlines() == [
        "child-clean",
        "parent-clean",
        "child-clean",
        "parent-clean",
    ]
    broker_records = [item for item in records if item[0] == "broker"]
    assert len(broker_records) == 2
    assert all(len(passed) == 2 for _, _argv, passed, _authority in broker_records)
    assert all(not (passed & authority) for _, _argv, passed, authority in broker_records)
    restic_records = [item for item in records if item[0] == "restic"]
    assert len(restic_records) == 2
    for _kind, argv, passed, authority in restic_records:
        assert len(passed) == 3
        assert authority < passed
        assert argv[7:9] == ("-r", ".")
        assert "--password-command" not in argv
        assert str(command) not in argv
        password_index = argv.index("--password-file")
        assert argv[password_index + 1].startswith("/dev/fd/")
    helper_records = [item for item in records if item[0] == "path-helper"]
    assert helper_records
    assert all(not (passed & authority) for _, _argv, passed, authority in helper_records)


@pytest.mark.parametrize("behavior", ["empty", "failure", "overflow", "timeout"])
def test_credential_broker_failure_is_bounded_secret_free_and_preserves_state(
    tmp_path, monkeypatch, capsys, behavior
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    monkeypatch.setattr(recovery_module, "_CREDENTIAL_COMMAND_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(recovery_module, "_CREDENTIAL_BROKER_TIMEOUT_SECONDS", 1.5)
    prior = RecoveryState(
        last_coverage_at="2026-08-16T19:00:00+00:00",
        last_snapshot_at="2026-08-16T19:00:00+00:00",
        last_result_code="recovery_ok",
        artifact_bytes=1234,
        schema_fingerprint_short="a" * 12,
    )
    save_state(settings.recovery_state_file, prior)
    command = Path(settings.recovery_credential_command)
    synthetic_secret = "SYNTHETIC-CREDENTIAL-DO-NOT-EMIT-9f03b0"
    if behavior == "empty":
        body = "pass"
    elif behavior == "failure":
        body = (
            "import os; "
            f"os.write(1, {synthetic_secret.encode()!r}); "
            f"os.write(2, {synthetic_secret.encode()!r}); "
            "raise SystemExit(7)"
        )
    elif behavior == "overflow":
        body = f"import os; os.write(1, os.urandom({recovery_module._MAX_CREDENTIAL_BYTES + 1}))"
    else:
        body = "import time; time.sleep(60)"
    command.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    command.chmod(0o700)

    class NoResticCall:
        def run(self, _argv, **_kwargs):
            raise AssertionError("Restic must not run without a credential")

    repository = _production_backup_repository(settings, NoResticCall())
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        run_backup("manual", settings, repository=repository)
    elapsed = time.monotonic() - started
    failed = load_state(settings.recovery_state_file)
    captured = capsys.readouterr()

    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Recovery credential is unavailable."
    assert secret_free_error(caught.value) == {
        "code": "recovery_repository_error",
        "message": "Recovery credential is unavailable.",
    }
    assert captured.out == ""
    assert captured.err == ""
    assert synthetic_secret not in str(caught.value)
    assert synthetic_secret not in json.dumps(secret_free_error(caught.value))
    assert failed.last_coverage_at == prior.last_coverage_at
    assert failed.last_snapshot_at == prior.last_snapshot_at
    assert failed.artifact_bytes == prior.artifact_bytes
    assert failed.last_result_code == "recovery_repository_error"
    assert elapsed < 3
    with recovery_lock(settings.recovery_lock_file, blocking=False):
        pass


def test_the_descriptor_identity_scan_can_actually_report_authority(tmp_path):
    """An instrument check for the test below, and it needed one.

    That test asserts a forked descendant's open descriptors do NOT include the
    recovery repository or the lock file -- it reads "clean". Removing the
    `close_fds=True` that holds that property does make it fail, but it fails
    with the marker file MISSING rather than reading "bad", because the extra
    descriptors break the broker before the credential command runs at all. So
    the removal proves the spawn is fragile without proving the scan can ever
    say "bad".

    A probe that cannot report the defect is not a probe. This one proves the
    scan both ways, with no recovery code involved: an inherited descriptor is
    detected, and a closed one is not. Three previous phases recorded
    instruments that could not have reported the thing they were watching for;
    this is the cheap version of not repeating that.
    """
    watched = tmp_path / "watched"
    watched.write_text("x", encoding="ascii")
    identity = (watched.stat().st_dev, watched.stat().st_ino)
    seen = tmp_path / "seen"
    unseen = tmp_path / "unseen"

    def scan_in_child(result: Path, *, keep_open: bool) -> None:
        handle = os.open(watched, os.O_RDONLY)
        child = os.fork()
        if child == 0:  # pragma: no cover - the child never returns
            try:
                if not keep_open:
                    os.close(handle)
                identities = set()
                for descriptor in range(128):
                    try:
                        current = os.fstat(descriptor)
                    except OSError:
                        continue
                    identities.add((current.st_dev, current.st_ino))
                result.write_text(
                    "bad" if identity in identities else "clean", encoding="ascii"
                )
            finally:
                os._exit(0)
        os.close(handle)
        os.waitpid(child, 0)

    scan_in_child(seen, keep_open=True)
    scan_in_child(unseen, keep_open=False)

    assert seen.read_text(encoding="ascii") == "bad", (
        "the scan did not notice an inherited descriptor on a watched file, so "
        'a "clean" reading from it establishes nothing'
    )
    assert unseen.read_text(encoding="ascii") == "clean"


def test_escaped_credential_descendant_is_authority_free_and_does_not_hold_lock(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    monkeypatch.setattr(recovery_module, "_CREDENTIAL_COMMAND_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(recovery_module, "_CREDENTIAL_BROKER_TIMEOUT_SECONDS", 1.5)
    settings.recovery_lock_file.touch(mode=0o600)
    repository_stat = Path(settings.recovery_repository).stat()
    lock_stat = settings.recovery_lock_file.stat()
    marker = tmp_path / "credential-orphan-authority"
    pid_file = tmp_path / "credential-orphan-pid"
    exit_marker = tmp_path / "credential-orphan-exited"
    release = tmp_path / "credential-orphan-release"
    command = Path(settings.recovery_credential_command)
    # The escaped descendant waits for `release` rather than sleeping.
    #
    # It used to `time.sleep(1.2)` and the test asserted, further down, that it
    # had not yet exited -- a liveness PRECONDITION for the lock check that
    # follows, dressed as a race. The race was unwinnable by construction: the
    # test sets the broker timeout to 1.5s, so `run_backup` cannot return in
    # less than that, and 1.5 > 1.2. Measured here at 2.085s against a 1.2s
    # sleep, with the child already gone by 0.885s. It could only ever have
    # passed where `run_backup` returned early for some other reason.
    #
    # So the ordering is now controlled instead of hoped for. The deadline is a
    # backstop against leaving a process behind if the test dies before
    # releasing it, and it writes a DIFFERENT word, so an exit on the deadline
    # fails the final assertion loudly rather than passing as a timely one.
    command.write_text(
        f"""#!{sys.executable}
import os
from pathlib import Path
import time

expected = {((repository_stat.st_dev, repository_stat.st_ino), (lock_stat.st_dev, lock_stat.st_ino))!r}
marker = Path({str(marker)!r})
pid_file = Path({str(pid_file)!r})
exit_marker = Path({str(exit_marker)!r})
release = Path({str(release)!r})
child = os.fork()
if child == 0:
    os.setsid()
    identities = set()
    for descriptor in range(128):
        try:
            current = os.fstat(descriptor)
        except OSError:
            continue
        identities.add((current.st_dev, current.st_ino))
    marker.write_text("bad" if any(item in identities for item in expected) else "clean", encoding="ascii")
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    deadline = time.monotonic() + 30
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    exit_marker.write_text(
        "exited" if release.exists() else "deadline", encoding="ascii"
    )
    os._exit(0)
time.sleep(60)
""",
        encoding="utf-8",
    )
    command.chmod(0o700)

    class NoResticCall:
        def run(self, _argv, **_kwargs):
            raise AssertionError("Restic must not run after broker timeout")

    repository = _production_backup_repository(settings, NoResticCall())
    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        run_backup("manual", settings, repository=repository)
    elapsed = time.monotonic() - started

    assert caught.value.code == "recovery_repository_error"
    assert elapsed < 3
    deadline = time.monotonic() + 1
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.read_text(encoding="ascii") == "clean"
    assert pid_file.exists()

    # The orphan is alive, asserted directly rather than inferred from the
    # absence of a file. This is the precondition that makes the lock check
    # below mean anything at all: a dead process holds no locks, so acquiring
    # the lock after the orphan had exited would prove nothing.
    orphan_pid = int(pid_file.read_text(encoding="ascii"))
    os.kill(orphan_pid, 0)
    assert not exit_marker.exists()

    # The claim: the escaped descendant does not hold the recovery lock. Taken
    # while it is provably still running and waiting.
    with recovery_lock(settings.recovery_lock_file, blocking=False):
        pass

    # And it exits when told to, which is the definitive liveness proof -- a
    # zombie would satisfy `os.kill(pid, 0)` but cannot answer a handshake.
    release.write_text("go", encoding="ascii")
    deadline = time.monotonic() + 10
    while not exit_marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert exit_marker.read_text(encoding="ascii") == "exited"


def test_backup_repository_calls_are_pinned_and_have_operation_deadlines(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    runner = _PinnedBackupRunner(Path(settings.recovery_repository))
    repository = _production_backup_repository(settings, runner)

    result = run_backup("manual", settings, repository=repository)

    assert result["snapshot_created"] is True
    assert len(runner.calls) == 7
    assert all(call[0][2] == "." for call in runner.calls)
    assert all(call[1]["cwd_fd"] == call[1]["pass_fds"][0] for call in runner.calls)
    assert all(call[1]["timeout_seconds"] is not None for call in runner.calls)
    assert [call[1]["timeout_seconds"] for call in runner.calls] == [
        recovery_module._RESTIC_METADATA_TIMEOUT_SECONDS,
        recovery_module._RESTIC_DUMP_TIMEOUT_SECONDS,
        recovery_module._RESTIC_METADATA_TIMEOUT_SECONDS,
        recovery_module._RESTIC_BACKUP_TIMEOUT_SECONDS,
        recovery_module._RESTIC_METADATA_TIMEOUT_SECONDS,
        recovery_module._RESTIC_DUMP_TIMEOUT_SECONDS,
        recovery_module._RESTIC_CHECK_TIMEOUT_SECONDS,
    ]


def test_every_restic_subprocess_call_has_an_explicit_deadline():
    tree = ast.parse(inspect.getsource(ResticRepository))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
        and node.func.value.attr == "runner"
    ]
    assert len(calls) == 4
    assert all(
        any(keyword.arg == "timeout_seconds" for keyword in call.keywords)
        for call in calls
    )
    policy = {
        recovery_module._RESTIC_VERSION_TIMEOUT_SECONDS,
        recovery_module._RESTIC_INIT_TIMEOUT_SECONDS,
        recovery_module._RESTIC_METADATA_TIMEOUT_SECONDS,
        recovery_module._RESTIC_DUMP_TIMEOUT_SECONDS,
        recovery_module._RESTIC_BACKUP_TIMEOUT_SECONDS,
        recovery_module._RESTIC_CHECK_TIMEOUT_SECONDS,
        recovery_module._RESTIC_RETENTION_DRY_RUN_TIMEOUT_SECONDS,
        recovery_module._RESTIC_RETENTION_APPLY_TIMEOUT_SECONDS,
        recovery_module._RESTIC_BREAK_GLASS_TIMEOUT_SECONDS,
    }
    assert min(policy) > 0


@pytest.mark.parametrize("swap_at", range(1, 8))
def test_backup_rejects_persistent_repository_replacement_at_every_restic_call(
    tmp_path, monkeypatch, swap_at
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    prior = RecoveryState(
        last_result_code="recovery_state_missing",
    )
    save_state(settings.recovery_state_file, prior)
    runner = _PinnedBackupRunner(
        Path(settings.recovery_repository), swap_at=swap_at
    )
    repository = _production_backup_repository(settings, runner)
    try:
        with pytest.raises(RecoveryError) as caught:
            run_backup("manual", settings, repository=repository)
        failed = load_state(settings.recovery_state_file)
        assert caught.value.code == "recovery_target_unavailable"
        assert failed.last_coverage_at == prior.last_coverage_at
        assert failed.last_snapshot_at == prior.last_snapshot_at
        assert failed.artifact_bytes is None
        assert len(runner.calls) == swap_at
        assert all(call[0][2] == "." for call in runner.calls)
    finally:
        runner.restore_path()


@pytest.mark.parametrize("swap_at", range(1, 8))
def test_backup_aba_swap_restore_cannot_redirect_any_restic_call(
    tmp_path, monkeypatch, swap_at
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    runner = _PinnedBackupRunner(
        Path(settings.recovery_repository),
        swap_at=swap_at,
        restore_during_call=True,
    )
    repository = _production_backup_repository(settings, runner)

    result = run_backup("manual", settings, repository=repository)

    assert result["snapshot_created"] is True
    assert runner.aba_observed is True
    assert len(runner.calls) == 7
    assert all(call[0][2] == "." for call in runner.calls)


def test_escaped_restic_descendant_retains_admission_lock_until_exit_then_retry_succeeds(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    monkeypatch.setattr(recovery_module, "native_supported", lambda: True)
    monkeypatch.setattr(recovery_module, "_RESTIC_DUMP_TIMEOUT_SECONDS", 0.1)
    binary = Path(settings.recovery_restic_path)
    hang_once = tmp_path / "synthetic-hang-once"
    orphan_pid_file = tmp_path / "synthetic-orphan-pid"
    stored_bundle = tmp_path / "synthetic-repository-bundle"
    script = f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
hang_once = Path({str(hang_once)!r})
orphan_pid_file = Path({str(orphan_pid_file)!r})
stored_bundle = Path({str(stored_bundle)!r})
snapshot_id = {'e' * 64!r}
if "cat" in args and "config" in args:
    print(json.dumps({{"id": {'c' * 64!r}}}))
elif "dump" in args:
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
            time.sleep(1.2)
            os._exit(0)
        time.sleep(60)
    if not stored_bundle.exists():
        raise SystemExit(1)
    sys.stdout.buffer.write(stored_bundle.read_bytes())
elif "snapshots" in args:
    rows = []
    if stored_bundle.exists():
        rows = [{{"id": snapshot_id, "tags": [{recovery_module.RECOVERY_TAG!r}]}}]
    print(json.dumps(rows))
elif "backup" in args:
    stored_bundle.write_bytes(sys.stdin.buffer.read())
    print(json.dumps({{"message_type": "summary", "snapshot_id": snapshot_id}}))
elif "check" in args:
    pass
else:
    raise SystemExit(2)
"""
    binary.write_text(script, encoding="utf-8")
    binary.chmod(0o700)
    repository = _production_backup_repository(
        settings, BoundedSubprocessRunner()
    )

    started = time.monotonic()
    with pytest.raises(RecoveryError) as caught:
        run_backup("manual", settings, repository=repository)
    elapsed = time.monotonic() - started
    failed = load_state(settings.recovery_state_file)
    assert caught.value.code == "recovery_tool_invalid"
    assert caught.value.safe_message == "Recovery tool timed out."
    # Includes bounded helper startup for topology/pin assertions before the
    # 100 ms Restic deadline; the operation still returns well before the
    # synthetic 60-second hung parent.
    assert elapsed < 6
    assert failed.last_coverage_at is None
    assert failed.last_snapshot_at is None
    assert failed.artifact_bytes is None

    deadline = time.monotonic() + 1
    while not orphan_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert orphan_pid_file.exists()

    def attempt_direct_lock():
        with recovery_lock(settings.recovery_lock_file):
            pass

    for attempt in (
        attempt_direct_lock,
        lambda: run_backup("manual", settings, repository=FakeRepository()),
        lambda: apply_retention(
            settings,
            repository=FakeRepository(),
            apply=False,
            drill_snapshot="latest",
        ),
        lambda: reserve_api_trigger(settings),
    ):
        with pytest.raises(RecoveryError) as busy:
            attempt()
        assert busy.value.code == "recovery_busy"

    lock_reusable = False
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            with recovery_lock(settings.recovery_lock_file):
                lock_reusable = True
            break
        except RecoveryError as exc:
            assert exc.code == "recovery_busy"
            time.sleep(0.05)
    assert lock_reusable

    retried = run_backup("manual", settings, repository=repository)
    assert retried["snapshot_created"] is True
    recovered = load_state(settings.recovery_state_file)
    assert recovered.last_coverage_at is not None
    assert recovered.last_snapshot_at is not None


def _retention_plan(snapshot_id: str) -> bytes:
    snapshot = {
        "gid": 20,
        "hostname": "synthetic-host",
        "id": snapshot_id,
        "paths": ["synthetic-data"],
        "program_version": "restic 0.19.1",
        "short_id": "synthetic-short-id",
        "summary": {"total_files_processed": 1},
        "tags": ["espn-edge-private-v1"],
        "time": "2026-08-13T00:00:00+00:00",
        "tree": "b" * 64,
        "uid": 501,
        "username": "synthetic-user",
    }
    return json.dumps(
        [
            {
                "host": "synthetic-host",
                "paths": ["synthetic-data"],
                "tags": None,
                "keep": [snapshot],
                "remove": None,
                "reasons": [{"matches": ["hourly"], "snapshot": snapshot}],
            }
        ]
    ).encode()


def test_retention_parser_accepts_restic_0191_null_empty_disposition():
    snapshot_id = "a" * 64
    assert recovery_module._parse_retention_dry_run(
        _retention_plan(snapshot_id), inventory={snapshot_id}
    ) == {snapshot_id}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda group: group.update({"unknown": True}),
        lambda group: group.pop("keep"),
        lambda group: group.pop("remove"),
        lambda group: group.update({"keep": {}}),
        lambda group: group.update({"remove": "invalid"}),
    ],
    ids=(
        "unknown-group-field",
        "omitted-keep",
        "omitted-remove",
        "malformed-keep",
        "malformed-remove",
    ),
)
def test_retention_parser_rejects_unknown_omitted_or_malformed_shapes(mutation):
    snapshot_id = "a" * 64
    plan = json.loads(_retention_plan(snapshot_id))
    mutation(plan[0])
    with pytest.raises(RecoveryError) as caught:
        recovery_module._parse_retention_dry_run(
            json.dumps(plan).encode(), inventory={snapshot_id}
        )
    assert caught.value.code == "recovery_retention_invalid"


@pytest.mark.parametrize(
    "plan,inventory",
    [
        (
            [{"keep": None, "remove": None}],
            {"a" * 64},
        ),
        (
            [
                {
                    "keep": [
                        {"id": "a" * 64, "tags": ["espn-edge-private-v1"]},
                        {"id": "a" * 64, "tags": ["espn-edge-private-v1"]},
                    ],
                    "remove": None,
                }
            ],
            {"a" * 64},
        ),
        (
            [
                {
                    "keep": [{"id": "a" * 64, "tags": ["wrong"]}],
                    "remove": None,
                }
            ],
            {"a" * 64},
        ),
        (
            [
                {
                    "keep": [
                        {"id": "a" * 64, "tags": ["espn-edge-private-v1"]}
                    ],
                    "remove": None,
                }
            ],
            {"a" * 64, "b" * 64},
        ),
    ],
    ids=("empty-survivors", "duplicate", "wrong-tag", "incomplete-partition"),
)
def test_retention_parser_preserves_survivor_partition_invariants(plan, inventory):
    with pytest.raises(RecoveryError) as caught:
        recovery_module._parse_retention_dry_run(
            json.dumps(plan).encode(), inventory=inventory
        )
    assert caught.value.code == "recovery_retention_invalid"


@pytest.mark.parametrize(
    "raw",
    [
        b"\xffprivate-input",
        b'{"value":' + (b"9" * 5_000) + b"}",
        (b"[" * 1_100) + b"0" + (b"]" * 1_100),
    ],
    ids=("invalid-utf8", "oversized-integer", "excessive-nesting"),
)
def test_retention_parser_translates_hostile_json_failures_without_reflection(raw):
    with pytest.raises(RecoveryError) as caught:
        recovery_module._parse_retention_dry_run(raw, inventory={"a" * 64})
    envelope = secret_free_error(caught.value)
    assert caught.value.code == "recovery_retention_invalid"
    assert envelope == {
        "code": "recovery_retention_invalid",
        "message": "Retention plan is invalid.",
    }
    assert "private-input" not in str(caught.value)
    assert len(json.dumps(envelope)) < 256


class RetentionRepository(FakeRepository):
    def __init__(self, bundle: bytes) -> None:
        super().__init__()
        self.bundle = bundle
        self.retention_calls: list[bool] = []
        self.plan = _retention_plan(self.snapshot_id)
        self.after_ids = {self.snapshot_id}
        self.repo_id = "c" * 64
        self.topology = (
            StorageIdentity("source-disk", "internal", "1:1"),
            StorageIdentity("target-disk", "external", "2:2"),
        )

    def validate_topology(self, _source: Path):
        return self.topology

    @contextmanager
    def pin_repository(self, _source: Path, *, lock_fd: int):
        yield RepositoryPin(
            fd=-1,
            repository_path="fake-pinned-repository",
            topology=self.topology,
            repository_id=self.repo_id,
            device=2,
            inode=2,
            lock_fd=lock_fd,
        )

    def repository_id(self, *, pin=None) -> str:
        del pin
        return self.repo_id

    def retention(self, *, apply: bool, pin=None, expected_repository_id=None) -> bytes:
        del pin
        if apply and expected_repository_id != self.repo_id:
            raise RecoveryError("recovery_retention_invalid", "repository changed")
        self.retention_calls.append(apply)
        return self.plan

    def snapshots(self, *, pin=None):
        del pin
        ids = self.after_ids if self.retention_calls[-1:] == [True] else {self.snapshot_id}
        return [
            {
                "id": item,
                "tags": ["espn-edge-private-v1"],
                "time": "2026-08-13T00:00:00+00:00",
            }
            for item in sorted(ids)
        ]


def _partition_plan(*, keep: list[str], remove: list[str]) -> bytes:
    def row(snapshot_id: str) -> dict[str, object]:
        return {"id": snapshot_id, "tags": ["espn-edge-private-v1"]}

    return json.dumps(
        [
            {
                "keep": [row(snapshot_id) for snapshot_id in keep],
                "remove": [row(snapshot_id) for snapshot_id in remove],
            }
        ]
    ).encode()


class DrillRetentionRepository(RetentionRepository):
    older_snapshot = "a" * 64
    current_snapshot = "b" * 64

    def __init__(self, bundle: bytes) -> None:
        super().__init__(bundle)
        self.snapshot_id = self.current_snapshot
        self.plan = _partition_plan(
            keep=[self.older_snapshot, self.current_snapshot],
            remove=[],
        )
        self.after_ids = {self.older_snapshot, self.current_snapshot}
        self.ambiguous_latest = False

    def snapshots(self, *, pin=None):
        del pin
        ids = (
            self.after_ids
            if self.retention_calls[-1:] == [True]
            else {self.older_snapshot, self.current_snapshot}
        )
        return [
            {
                "id": snapshot_id,
                "tags": ["espn-edge-private-v1"],
                "time": (
                    "2026-08-13T01:00:00+00:00"
                    if self.ambiguous_latest or snapshot_id == self.current_snapshot
                    else "2026-08-13T00:00:00+00:00"
                ),
            }
            for snapshot_id in sorted(ids)
        ]


@pytest.mark.parametrize(
    "selector",
    ["latest", DrillRetentionRepository.older_snapshot.upper()],
    ids=("latest", "exact-older"),
)
def test_retention_keeps_operator_selected_drill_snapshot(tmp_path, selector):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    repo = DrillRetentionRepository(bundle)
    state = apply_retention(
        settings,
        repository=repo,
        apply=True,
        drill_snapshot=selector,
    )
    assert repo.retention_calls == [False, False, True]
    assert state.retention_applied_pending_drill is True


@pytest.mark.parametrize(
    "selector",
    [None, "", "newest", "a" * 63, "g" * 64],
    ids=("absent", "empty", "unsupported-alias", "short", "non-hex"),
)
def test_manual_retention_rejects_absent_or_invalid_drill_selector(
    tmp_path, selector
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    repo = DrillRetentionRepository(bundle)
    with pytest.raises(RecoveryError) as caught:
        apply_retention(
            settings,
            repository=repo,
            apply=True,
            drill_snapshot=selector,
        )
    assert caught.value.code == "recovery_retention_invalid"
    assert repo.retention_calls == []


def test_retention_rejects_missing_or_ambiguous_selected_snapshot(tmp_path):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    missing = DrillRetentionRepository(bundle)
    with pytest.raises(RecoveryError) as not_found:
        apply_retention(
            settings,
            repository=missing,
            apply=True,
            drill_snapshot="c" * 64,
        )
    assert not_found.value.code == "recovery_retention_invalid"
    assert missing.retention_calls == []

    ambiguous = DrillRetentionRepository(bundle)
    ambiguous.ambiguous_latest = True
    with pytest.raises(RecoveryError) as tied:
        apply_retention(
            settings,
            repository=ambiguous,
            apply=True,
            drill_snapshot="latest",
        )
    assert tied.value.code == "recovery_retention_invalid"
    assert ambiguous.retention_calls == []


def test_retention_blocks_apply_when_selected_older_point_would_be_removed(tmp_path):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    repo = DrillRetentionRepository(bundle)
    repo.plan = _partition_plan(
        keep=[repo.current_snapshot],
        remove=[repo.older_snapshot],
    )
    with pytest.raises(RecoveryError) as caught:
        apply_retention(
            settings,
            repository=repo,
            apply=True,
            drill_snapshot=repo.older_snapshot,
        )
    assert caught.value.code == "recovery_retention_invalid"
    assert repo.retention_calls == [False]
    assert True not in repo.retention_calls


def test_retention_rebinds_same_selected_point_and_rejects_plan_drift(tmp_path):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)

    class DriftingPlanRepository(DrillRetentionRepository):
        def retention(self, *, apply, pin=None, expected_repository_id=None):
            result = super().retention(
                apply=apply,
                pin=pin,
                expected_repository_id=expected_repository_id,
            )
            if not apply and self.retention_calls == [False, False]:
                return _partition_plan(
                    keep=[self.current_snapshot],
                    remove=[self.older_snapshot],
                )
            return result

    repo = DriftingPlanRepository(bundle)
    with pytest.raises(RecoveryError) as caught:
        apply_retention(
            settings,
            repository=repo,
            apply=True,
            drill_snapshot=repo.older_snapshot,
        )
    assert caught.value.code == "recovery_retention_invalid"
    assert repo.retention_calls == [False, False]
    assert True not in repo.retention_calls


def test_retention_plan_digest_binds_selected_drill_snapshot():
    topology = (
        StorageIdentity("source-disk", "internal", "1:1"),
        StorageIdentity("target-disk", "external", "2:2"),
    )
    common = {
        "repository_id": "c" * 64,
        "topology": topology,
        "inventory": {"a" * 64, "b" * 64},
        "survivors": {"a" * 64, "b" * 64},
    }
    older = recovery_module._retention_plan_digest(
        **common,
        drill_snapshot="a" * 64,
    )
    current = recovery_module._retention_plan_digest(
        **common,
        drill_snapshot="b" * 64,
    )
    assert older != current


def test_retention_is_fail_closed_until_post_apply_drill_and_preserves_timestamps(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    repo = RetentionRepository(bundle)
    original = RecoveryState(
        last_coverage_at="2026-08-13T00:00:00+00:00",
        last_snapshot_at="2026-08-12T23:00:00+00:00",
        retention_configured=True,
        retention_enforced=True,
        last_result_code="recovery_ok",
    )
    save_state(settings.recovery_state_file, original)
    state = apply_retention(
        settings,
        repository=repo,
        apply=True,
        drill_snapshot="latest",
    )
    assert repo.retention_calls == [False, False, True]
    assert state.retention_enforced is False
    assert state.retention_applied_pending_drill is True
    assert state.last_coverage_at == original.last_coverage_at
    assert state.last_snapshot_at == original.last_snapshot_at


@pytest.mark.parametrize("failure", ["wrong-tag", "ambiguous", "zero-survivor", "post-mismatch"])
def test_retention_rejects_untrusted_or_mismatched_survivor_sets(tmp_path, failure):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    repo = RetentionRepository(bundle)
    if failure == "wrong-tag":
        repo.plan = json.dumps([{"tags": ["wrong"], "keep": []}]).encode()
    elif failure == "ambiguous":
        kept = {"id": repo.snapshot_id, "tags": ["espn-edge-private-v1"]}
        repo.plan = json.dumps([{"keep": [kept, kept], "remove": []}]).encode()
    elif failure == "zero-survivor":
        removed = {"id": repo.snapshot_id, "tags": ["espn-edge-private-v1"]}
        repo.plan = json.dumps([{"keep": [], "remove": [removed]}]).encode()
    else:
        repo.after_ids = {"b" * 64}
    with pytest.raises(RecoveryError) as caught:
        apply_retention(
            settings,
            repository=repo,
            apply=True,
            drill_snapshot="latest",
        )
    assert caught.value.code == "recovery_retention_invalid"
    state = load_state(settings.recovery_state_file)
    assert state.retention_enforced is False


def test_retention_cli_requires_exact_approval_transition(monkeypatch):
    from api.recovery import _parser, main

    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["retention"])
    with pytest.raises(SystemExit):
        parser.parse_args(["retention", "--apply"])
    calls: list[str] = []
    monkeypatch.setattr(
        "api.recovery.apply_retention",
        lambda **_kwargs: calls.append("dry-run") or RecoveryState(),
    )
    monkeypatch.setattr(
        "api.recovery._enable_retention",
        lambda _selector: calls.append("enable") or RecoveryState(),
    )
    assert main(["retention", "--enable"]) == 1
    assert main(["retention", "--dry-run", "--apply"]) == 1
    assert calls == []
    assert main(
        ["retention", "--dry-run", "--drill-snapshot", "latest"]
    ) == 0
    assert calls == ["dry-run"]
    assert main(
        [
            "retention",
            "--enable",
            "--apply",
            "--drill-snapshot",
            "a" * 64,
        ]
    ) == 0
    assert calls == ["dry-run", "enable"]
    assert parser.parse_args(
        ["retention", "--dry-run", "--drill-snapshot", "latest"]
    ).dry_run is True
    exact = parser.parse_args(
        [
            "retention",
            "--enable",
            "--apply",
            "--drill-snapshot",
            "a" * 64,
        ]
    )
    assert exact.enable is True and exact.apply is True


@pytest.mark.parametrize(
    "argv",
    [
        ["retention", "--dry-run", "--json"],
        ["retention", "--enable", "--apply", "--json"],
        [
            "retention",
            "--dry-run",
            "--drill-snapshot",
            "/private/operator-selected-snapshot",
            "--json",
        ],
        [
            "retention",
            "--scheduled-apply",
            "--drill-snapshot",
            "latest",
            "--json",
        ],
    ],
    ids=("dry-run-absent", "enable-absent", "invalid", "scheduled-selector"),
)
def test_retention_cli_rejects_missing_or_invalid_selector_without_reflection(
    argv, monkeypatch, capsys
):
    from api.recovery import main

    calls = []
    monkeypatch.setattr(
        "api.recovery.apply_retention",
        lambda **kwargs: calls.append(kwargs) or RecoveryState(),
    )
    monkeypatch.setattr(
        "api.recovery._enable_retention",
        lambda selector: calls.append(selector) or RecoveryState(),
    )
    assert main(argv) == 1
    output = capsys.readouterr().out
    envelope = json.loads(output)
    assert set(envelope) == {"code", "message"}
    assert envelope["code"] == "recovery_retention_invalid"
    assert envelope["message"] in {
        "Retention requires a valid drill snapshot selector.",
        "Scheduled retention does not accept manual approval arguments.",
    }
    assert "operator-selected-snapshot" not in output
    assert calls == []


@pytest.mark.parametrize(
    "raw",
    [
        b"\xffprivate-cli-input",
        b'{"value":' + (b"9" * 5_000) + b"}",
        (b"[" * 1_100) + b"0" + (b"]" * 1_100),
    ],
    ids=("invalid-utf8", "oversized-integer", "excessive-nesting"),
)
def test_retention_cli_bounds_and_redacts_hostile_parser_failures(
    raw, monkeypatch, capsys
):
    from api.recovery import main

    def parse_hostile_plan(**_kwargs):
        return recovery_module._parse_retention_dry_run(
            raw,
            inventory={"a" * 64},
        )

    monkeypatch.setattr("api.recovery.apply_retention", parse_hostile_plan)
    assert main(
        [
            "retention",
            "--dry-run",
            "--drill-snapshot",
            "latest",
            "--json",
        ]
    ) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "code": "recovery_retention_invalid",
        "message": "Retention plan is invalid.",
    }
    assert "private-cli-input" not in output
    assert len(output) < 256


@requires_lexical_venv
def test_runner_install_loads_hourly_backup_only(tmp_path, monkeypatch):
    from api import recovery

    class Runner:
        def __init__(self):
            self.calls = []
            self.loaded = set()

        def run(self, argv, **kwargs):
            self.calls.append((argv, kwargs))
            if argv[1] == "print":
                label = argv[-1].rsplit("/", 1)[-1]
                if label in self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if argv[1] == "bootstrap":
                self.loaded.add("com.espn-edge.private-recovery")
                return ProcessResult(0, b"", b"")
            raise AssertionError(argv[1])

    runner = Runner()
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)
    recovery._install_runner(load=True)
    agents = tmp_path / "Library/LaunchAgents"
    assert (agents / "com.espn-edge.private-recovery.plist").is_file()
    assert not (agents / "com.espn-edge.private-recovery-retention.plist").exists()
    assert [call[0][1] for call in runner.calls] == [
        "print",
        "print",
        "bootstrap",
        "print",
    ]
    bootstrap = runner.calls[2]
    assert bootstrap[0][1:3] == ["bootstrap", f"gui/{os.getuid()}"]
    assert bootstrap[0][-1].startswith("/dev/fd/")
    assert bootstrap[1]["pass_fds"]
    assert "private-recovery-retention" not in " ".join(bootstrap[0])


def test_hourly_runner_refuses_stale_retention_install(tmp_path, monkeypatch):
    from api import recovery

    class Runner:
        def run(self, _argv, **_kwargs):
            return ProcessResult(113, b"", b"could not find service")

    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    retention = agents / "com.espn-edge.private-recovery-retention.plist"
    retention.write_text("stale")
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", Runner)
    with pytest.raises(RecoveryError):
        recovery._install_runner(load=False)
    assert retention.read_text() == "stale"
    assert not (agents / "com.espn-edge.private-recovery.plist").exists()


@requires_lexical_venv
def test_recurring_plist_uses_only_armed_scheduled_mode():
    import plistlib

    from api.recovery import _launchd_plist

    payload = plistlib.loads(_launchd_plist(retention=True))
    argv = payload["ProgramArguments"]
    assert argv[-2:] == ["retention", "--scheduled-apply"]
    assert "--apply" not in argv
    assert "--enable" not in argv


@pytest.mark.parametrize("retention", [False, True], ids=("hourly", "retention"))
@requires_lexical_venv
def test_launchd_plist_preserves_lexical_venv_interpreter(retention):
    from api import recovery

    content = recovery._launchd_plist(retention=retention)
    payload = plistlib.loads(content)
    lexical = recovery.ROOT / ".venv/bin/python"
    resolved_base = lexical.resolve(strict=True)
    assert lexical.is_absolute()
    assert lexical != resolved_base
    assert payload["ProgramArguments"][0] == str(lexical)
    assert str(resolved_base) not in content.decode("utf-8")
    assert payload["WorkingDirectory"] == str(recovery.ROOT)
    assert payload["EnvironmentVariables"] == {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"
    }


@pytest.mark.parametrize("retention", [False, True], ids=("hourly", "retention"))
@requires_lexical_venv_with_dependencies
def test_launchd_generated_interpreter_imports_application_dependencies(retention):
    from api.recovery import _launchd_plist

    payload = plistlib.loads(_launchd_plist(retention=retention))
    runtime = payload["ProgramArguments"][0]
    result = BoundedSubprocessRunner().run(
        [
            runtime,
            "-c",
            "import api.recovery, fastapi, pydantic_settings, sqlalchemy",
        ],
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        max_output=1024,
        timeout_seconds=5.0,
    )
    assert result.returncode == 0
    assert result.stdout == b""
    assert result.stderr == b""


@pytest.mark.parametrize("runtime_state", ["missing", "non-executable"])
def test_launchd_plist_rejects_unavailable_runtime_without_path_leak(
    tmp_path, monkeypatch, runtime_state
):
    from api import recovery

    sentinel = "PRIVATE-RUNTIME-PATH-7f4e09d4bb1f"
    root = tmp_path / sentinel
    if runtime_state == "non-executable":
        runtime = root / ".venv/bin/python"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"synthetic runtime")
        runtime.chmod(0o600)
    monkeypatch.setattr(recovery, "ROOT", root)
    with pytest.raises(RecoveryError) as caught:
        recovery._launchd_plist()
    envelope = json.dumps(secret_free_error(caught.value), sort_keys=True)
    assert caught.value.code == "recovery_tool_invalid"
    assert caught.value.safe_message == "Configured Python runtime is unavailable."
    assert sentinel not in str(caught.value)
    assert sentinel not in envelope


@requires_lexical_venv
def test_retention_runner_install_is_rolled_back_when_load_fails(tmp_path, monkeypatch):
    from api import recovery

    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **_kwargs):
            self.calls.append(argv)
            if argv[1] == "print":
                return ProcessResult(113, b"", b"could not find service")
            if argv[1] == "bootstrap":
                return ProcessResult(1, b"", b"")
            if argv[1] == "bootout":
                return ProcessResult(113, b"", b"could not find service")
            raise AssertionError(argv[1])

    runner = Runner()
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    with pytest.raises(RecoveryError):
        recovery._install_retention_runner(runner)
    target = (
        tmp_path
        / "Library/LaunchAgents/com.espn-edge.private-recovery-retention.plist"
    )
    assert not target.exists()
    assert [call[1] for call in runner.calls] == [
        "print",
        "bootstrap",
        "bootout",
        "print",
        "bootout",
        "print",
    ]


@requires_lexical_venv
def test_retention_runner_is_rolled_back_when_state_arm_fails(tmp_path, monkeypatch):
    from api import recovery

    class Runner:
        def __init__(self):
            self.calls = []
            self.loaded = False

        def run(self, argv, **_kwargs):
            self.calls.append(argv)
            if argv[1] == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if argv[1] == "bootstrap":
                self.loaded = True
            elif argv[1] == "bootout":
                self.loaded = False
            return ProcessResult(0, b"", b"")

    runner = Runner()
    apply_calls = []
    arm_error = RecoveryError("recovery_retention_invalid", "synthetic arm failure")
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )
    monkeypatch.setattr(
        recovery,
        "arm_retention_schedule",
        lambda: (_ for _ in ()).throw(arm_error),
    )
    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")
    assert caught.value is arm_error
    target = (
        tmp_path
        / "Library/LaunchAgents/com.espn-edge.private-recovery-retention.plist"
    )
    assert apply_calls == [{"apply": True, "drill_snapshot": "latest"}]
    assert not target.exists()
    assert [call[1] for call in runner.calls] == [
        "print",
        "print",
        "bootstrap",
        "print",
        "bootout",
        "print",
        "bootout",
        "print",
    ]


@requires_lexical_venv
def test_retention_state_arm_oserror_is_redacted_and_rolled_back(
    tmp_path, monkeypatch
):
    from api import recovery
    from api.services import recovery as service_recovery

    sentinel = "PRIVATE-ARM-STATE-PATH-2f614"

    class Runner:
        def __init__(self):
            self.loaded = False

        def run(self, argv, **_kwargs):
            operation = argv[1]
            if operation == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if operation == "bootstrap":
                self.loaded = True
            elif operation == "bootout":
                self.loaded = False
            return ProcessResult(0, b"", b"")

    settings = _settings(tmp_path)
    prior = RecoveryState(
        last_result_code="recovery_retention_applied",
        retention_configured=True,
        retention_applied_pending_drill=True,
        retention_schedule_armed=False,
    )
    save_state(settings.recovery_state_file, prior)
    runner = Runner()
    installed = []
    real_install = recovery._install_retention_runner

    def tracked_install(active_runner):
        authority = real_install(active_runner)
        installed.append(authority)
        return authority

    def fail_save(*_args, **_kwargs):
        raise OSError(f"{sentinel}/state.json")

    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)
    monkeypatch.setattr(recovery, "apply_retention", lambda **_kwargs: prior)
    monkeypatch.setattr(recovery, "_install_retention_runner", tracked_install)
    monkeypatch.setattr(service_recovery, "save_state", fail_save)
    monkeypatch.setattr(
        recovery,
        "arm_retention_schedule",
        lambda: service_recovery.arm_retention_schedule(settings),
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")

    assert secret_free_error(caught.value) == {
        "code": "recovery_repository_error",
        "message": "Launchd service control failed.",
    }
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None
    assert runner.loaded is False
    assert not recovery._retention_runner_target().exists()
    assert load_state(settings.recovery_state_file) == prior
    assert len(installed) == 1
    assert installed[0].closed is True
    with pytest.raises(OSError):
        os.fstat(installed[0].plist_fd)
    with pytest.raises(OSError):
        os.fstat(installed[0].directory_fd)


@requires_lexical_venv
def test_retention_cli_redacts_state_arm_oserror(tmp_path, monkeypatch, capsys):
    from api import recovery

    sentinel = "PRIVATE-CLI-ARM-STATE-PATH-8af32"

    class Runner:
        def __init__(self):
            self.loaded = False

        def run(self, argv, **_kwargs):
            operation = argv[1]
            if operation == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if operation == "bootstrap":
                self.loaded = True
            elif operation == "bootout":
                self.loaded = False
            return ProcessResult(0, b"", b"")

    runner = Runner()
    apply_calls = []
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )

    def fail_arm():
        arm_calls.append(True)
        raise OSError(f"{sentinel}/state.json")

    monkeypatch.setattr(recovery, "arm_retention_schedule", fail_arm)

    assert (
        recovery.main(
            [
                "retention",
                "--enable",
                "--apply",
                "--drill-snapshot",
                "latest",
                "--json",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "code": "recovery_repository_error",
        "message": "Launchd service control failed.",
    }
    assert sentinel not in output
    assert "Traceback" not in output
    assert apply_calls == [{"apply": True, "drill_snapshot": "latest"}]
    assert arm_calls == [True]
    assert runner.loaded is False
    assert not recovery._retention_runner_target().exists()


def test_retention_preflight_timeout_blocks_destructive_apply_and_arm(
    tmp_path, monkeypatch
):
    from api import recovery

    class TimeoutRunner:
        def run(self, argv, **kwargs):
            assert argv[1] == "print"
            assert kwargs["timeout_seconds"] == recovery._LAUNCHCTL_TIMEOUT_SECONDS
            raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")

    apply_calls = []
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", TimeoutRunner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")

    assert caught.value.code == "recovery_repository_error"
    assert apply_calls == []
    assert arm_calls == []
    assert not recovery._retention_runner_target().exists()


@requires_lexical_venv
def test_retention_bootstrap_timeout_late_load_is_bounded_and_rolled_back(
    tmp_path, monkeypatch
):
    from api import recovery

    class LateLoadRunner:
        def __init__(self):
            self.calls = []
            self.loaded = False

        def run(self, argv, **kwargs):
            self.calls.append((argv, kwargs))
            assert kwargs["timeout_seconds"] == recovery._LAUNCHCTL_TIMEOUT_SECONDS
            if argv[1] == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if argv[1] == "bootstrap":
                self.loaded = True
                raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")
            if argv[1] == "bootout":
                self.loaded = False
                return ProcessResult(0, b"", b"")
            raise AssertionError("unexpected service-control call")

    runner = LateLoadRunner()
    apply_calls = []
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")

    assert caught.value.code == "recovery_repository_error"
    assert apply_calls == [{"apply": True, "drill_snapshot": "latest"}]
    assert arm_calls == []
    assert runner.loaded is False
    assert not recovery._retention_runner_target().exists()
    assert [call[0][1] for call in runner.calls] == [
        "print",
        "print",
        "bootstrap",
        "bootout",
        "print",
        "bootout",
        "print",
    ]


@requires_lexical_venv
def test_retention_bootout_timeout_still_verifies_and_cleans_exact_inode(
    tmp_path, monkeypatch
):
    from api import recovery

    class BootoutTimeoutRunner:
        def __init__(self):
            self.calls = []
            self.loaded = False
            self.bootout_calls = 0

        def run(self, argv, **kwargs):
            self.calls.append((argv, kwargs))
            assert kwargs["timeout_seconds"] == recovery._LAUNCHCTL_TIMEOUT_SECONDS
            if argv[1] == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if argv[1] == "bootstrap":
                self.loaded = True
                return ProcessResult(1, b"", b"")
            if argv[1] == "bootout":
                self.bootout_calls += 1
                if self.bootout_calls == 1:
                    raise RecoveryError(
                        "recovery_tool_invalid", "Recovery tool timed out."
                    )
                self.loaded = False
                return ProcessResult(0, b"", b"")
            raise AssertionError("unexpected service-control call")

    runner = BootoutTimeoutRunner()
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError):
        recovery._install_retention_runner(runner)

    assert arm_calls == []
    assert runner.loaded is False
    assert not recovery._retention_runner_target().exists()
    assert [call[0][1] for call in runner.calls] == [
        "print",
        "bootstrap",
        "bootout",
        "print",
        "bootout",
        "print",
        "bootout",
        "print",
    ]


@requires_lexical_venv
def test_retention_timeout_cleanup_preserves_replacement_inode(tmp_path, monkeypatch):
    from api import recovery

    replacement = b"synthetic-unrelated-replacement"

    class ReplacementRunner:
        def __init__(self):
            self.loaded = False

        def run(self, argv, **kwargs):
            if argv[1] == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if argv[1] == "bootstrap":
                target = recovery._retention_runner_target()
                target.unlink()
                target.write_bytes(replacement)
                fd = kwargs["pass_fds"][0]
                assert os.pread(fd, len(replacement), 0) != replacement
                self.loaded = True
                raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")
            if argv[1] == "bootout":
                self.loaded = False
                return ProcessResult(0, b"", b"")
            raise AssertionError("unexpected service-control call")

    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    with pytest.raises(RecoveryError):
        recovery._install_retention_runner(ReplacementRunner())

    target = recovery._retention_runner_target()
    assert target.read_bytes() == replacement


def test_launchctl_service_control_has_static_finite_bounds():
    from api import recovery

    module_tree = ast.parse(inspect.getsource(recovery))
    launchctl_literals = [
        node
        for node in ast.walk(module_tree)
        if isinstance(node, ast.Constant) and node.value == "/bin/launchctl"
    ]
    assert len(launchctl_literals) == 1

    helper_tree = ast.parse(inspect.getsource(recovery._run_launchctl))
    run_calls = [
        node
        for node in ast.walk(helper_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert len(run_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in run_calls[0].keywords}
    assert isinstance(keywords["timeout_seconds"], ast.Name)
    assert keywords["timeout_seconds"].id == "_LAUNCHCTL_TIMEOUT_SECONDS"
    assert isinstance(keywords["max_output"], ast.Name)
    assert keywords["max_output"].id == "_LAUNCHCTL_MAX_OUTPUT_BYTES"
    assert 0 < recovery._LAUNCHCTL_TIMEOUT_SECONDS <= 30
    assert 0 < recovery._LAUNCHCTL_MAX_OUTPUT_BYTES <= 64 * 1024
    hourly = inspect.getsource(recovery._install_runner)
    retention = inspect.getsource(recovery._install_retention_runner)
    assert "_install_launchd_runner(" in hourly
    assert "_install_launchd_runner(" in retention


@pytest.mark.parametrize("returncode", [5, 77, 113])
def test_retention_ambiguous_service_status_blocks_apply(returncode, tmp_path, monkeypatch):
    from api import recovery

    sentinel = "PRIVATE-LAUNCHD-ERROR-PATH-6d498"

    class AmbiguousRunner:
        def run(self, argv, **_kwargs):
            assert argv[1] == "print"
            return ProcessResult(returncode, b"", sentinel.encode())

    apply_calls = []
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", AmbiguousRunner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")

    assert caught.value.code == "recovery_repository_error"
    assert caught.value.safe_message == "Launchd service state is unavailable."
    assert sentinel not in str(caught.value)
    assert apply_calls == []
    assert arm_calls == []


@pytest.mark.parametrize(
    "failure",
    ["timeout", "overflow", "permission", "malformed"],
)
def test_retention_service_control_failures_are_stable_before_apply(
    failure, tmp_path, monkeypatch
):
    from api import recovery

    sentinel = "PRIVATE-SERVICE-CONTROL-FAILURE-27bc"

    class FailingRunner:
        def run(self, _argv, **_kwargs):
            if failure == "timeout":
                raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")
            if failure == "overflow":
                raise RecoveryError(
                    "recovery_tool_invalid", "Recovery tool output exceeded its bound."
                )
            if failure == "permission":
                raise PermissionError(sentinel)
            return object()

    apply_calls = []
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", FailingRunner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")

    envelope = secret_free_error(caught.value)
    assert envelope == {
        "code": "recovery_repository_error",
        "message": "Launchd service control failed.",
    }
    assert sentinel not in json.dumps(envelope)
    assert apply_calls == []
    assert arm_calls == []


def test_retention_malformed_launchd_domain_blocks_apply(tmp_path, monkeypatch):
    from api import recovery

    class NoCallRunner:
        def run(self, *_args, **_kwargs):
            raise AssertionError("malformed domain must fail before service control")

    apply_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery.os, "getuid", lambda: -1)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", NoCallRunner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._enable_retention("latest")

    assert secret_free_error(caught.value) == {
        "code": "recovery_repository_error",
        "message": "Launchd service control failed.",
    }
    assert apply_calls == []


@requires_lexical_venv
def test_retention_reconciles_registration_after_first_absent(tmp_path, monkeypatch):
    from api import recovery

    class DelayedRegistrationRunner:
        def __init__(self):
            self.pending = False
            self.loaded = False
            self.print_calls = 0
            self.bootout_calls = 0

        def run(self, argv, **_kwargs):
            operation = argv[1]
            if operation == "print":
                self.print_calls += 1
                if self.pending:
                    self.pending = False
                    self.loaded = True
                    return ProcessResult(113, b"", b"could not find service")
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if operation == "bootstrap":
                self.pending = True
                raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")
            if operation == "bootout":
                self.bootout_calls += 1
                self.loaded = False
                return ProcessResult(0, b"", b"")
            raise AssertionError(operation)

    runner = DelayedRegistrationRunner()
    apply_calls = []
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError):
        recovery._enable_retention("latest")

    assert apply_calls == [{"apply": True, "drill_snapshot": "latest"}]
    assert arm_calls == []
    assert runner.bootout_calls >= 2
    assert runner.loaded is False
    assert runner.pending is False
    assert not recovery._retention_runner_target().exists()


def test_hourly_bootstrap_timeout_rolls_back_service_and_plist(tmp_path, monkeypatch):
    from api import recovery

    class HourlyTimeoutRunner:
        def __init__(self):
            self.loaded = set()

        def run(self, argv, **kwargs):
            operation = argv[1]
            if operation == "print":
                label = argv[-1].rsplit("/", 1)[-1]
                if label in self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if operation == "bootstrap":
                assert kwargs["pass_fds"]
                self.loaded.add(recovery._LAUNCHD_LABEL)
                raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")
            if operation == "bootout":
                self.loaded.discard(argv[-1].rsplit("/", 1)[-1])
                return ProcessResult(0, b"", b"")
            raise AssertionError(operation)

    runner = HourlyTimeoutRunner()
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", lambda: runner)

    with pytest.raises(RecoveryError):
        recovery._install_runner(load=True)

    assert runner.loaded == set()
    assert not recovery._launchd_target(recovery._LAUNCHD_LABEL).exists()
    assert not recovery._retention_runner_target().exists()


@requires_lexical_venv
def test_repeated_bootout_ambiguity_returns_cleanup_required_without_arm(
    tmp_path, monkeypatch
):
    from api import recovery

    class AmbiguousBootoutRunner:
        def __init__(self):
            self.loaded = False
            self.bootout_calls = 0

        def run(self, argv, **_kwargs):
            operation = argv[1]
            if operation == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if operation == "bootstrap":
                self.loaded = True
                return ProcessResult(5, b"", b"ambiguous bootstrap")
            if operation == "bootout":
                self.bootout_calls += 1
                return ProcessResult(77, b"", b"ambiguous bootout")
            raise AssertionError(operation)

    runner = AmbiguousBootoutRunner()
    arm_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        recovery, "arm_retention_schedule", lambda: arm_calls.append(True)
    )

    with pytest.raises(RecoveryError) as caught:
        recovery._install_retention_runner(runner)

    assert secret_free_error(caught.value) == {
        "code": "recovery_repository_error",
        "message": "Launchd runner cleanup is required.",
    }
    assert runner.bootout_calls == recovery._LAUNCHCTL_RECONCILE_ATTEMPTS
    assert runner.loaded is True
    assert arm_calls == []
    assert not recovery._retention_runner_target().exists()


@pytest.mark.parametrize("replacement_kind", ["directory", "symlink"])
@requires_lexical_venv
def test_descriptor_bound_plist_survives_parent_replacement_and_cleans_original(
    replacement_kind, tmp_path, monkeypatch
):
    from api import recovery

    replacement = b"synthetic unrelated launch agent"

    class ParentReplacementRunner:
        def __init__(self):
            self.loaded = False
            self.submitted = b""
            self.moved_parent = tmp_path / "moved-launch-agents"
            self.replacement_file = None

        def run(self, argv, **kwargs):
            operation = argv[1]
            if operation == "print":
                if self.loaded:
                    return ProcessResult(0, b"", b"")
                return ProcessResult(113, b"", b"could not find service")
            if operation == "bootstrap":
                target = recovery._retention_runner_target()
                target.parent.rename(self.moved_parent)
                if replacement_kind == "directory":
                    target.parent.mkdir(parents=True)
                    self.replacement_file = target
                else:
                    attacker = tmp_path / "attacker-launch-agents"
                    attacker.mkdir()
                    target.parent.symlink_to(attacker, target_is_directory=True)
                    self.replacement_file = attacker / target.name
                self.replacement_file.write_bytes(replacement)
                fd = kwargs["pass_fds"][0]
                self.submitted = os.pread(fd, 1_000_000, 0)
                self.loaded = True
                return ProcessResult(0, b"", b"")
            if operation == "bootout":
                self.loaded = False
                return ProcessResult(0, b"", b"")
            raise AssertionError(operation)

    runner = ParentReplacementRunner()
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)

    with pytest.raises(RecoveryError) as caught:
        recovery._install_retention_runner(runner)

    assert caught.value.code == "recovery_repository_error"
    assert runner.submitted == recovery._launchd_plist(retention=True)
    assert runner.submitted != replacement
    assert runner.loaded is False
    assert runner.replacement_file.read_bytes() == replacement
    assert not (runner.moved_parent / runner.replacement_file.name).exists()


def test_retention_cli_redacts_service_control_failure(tmp_path, monkeypatch, capsys):
    from api import recovery

    sentinel = "PRIVATE-CLI-LAUNCHD-PATH-33e5"

    class AmbiguousRunner:
        def run(self, _argv, **_kwargs):
            return ProcessResult(77, b"", sentinel.encode())

    apply_calls = []
    monkeypatch.setattr(recovery.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(recovery, "BoundedSubprocessRunner", AmbiguousRunner)
    monkeypatch.setattr(
        recovery, "apply_retention", lambda **kwargs: apply_calls.append(kwargs)
    )

    assert (
        recovery.main(
            [
                "retention",
                "--enable",
                "--apply",
                "--drill-snapshot",
                "latest",
                "--json",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "code": "recovery_repository_error",
        "message": "Launchd service state is unavailable.",
    }
    assert sentinel not in output
    assert "Traceback" not in output
    assert apply_calls == []


def test_scheduled_retention_requires_atomic_arm_state(tmp_path):
    from api.services.recovery import arm_retention_schedule

    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    repo = RetentionRepository(bundle)
    with pytest.raises(RecoveryError):
        apply_retention(settings, repository=repo, apply=True, scheduled=True)
    with pytest.raises(RecoveryError) as manual_selector:
        apply_retention(
            settings,
            repository=repo,
            apply=True,
            scheduled=True,
            drill_snapshot="latest",
        )
    assert manual_selector.value.code == "recovery_retention_invalid"
    assert repo.retention_calls == []
    applied = apply_retention(
        settings,
        repository=repo,
        apply=True,
        drill_snapshot="latest",
    )
    assert applied.retention_schedule_armed is False
    armed = arm_retention_schedule(settings)
    assert armed.retention_schedule_armed is True
    scheduled = apply_retention(
        settings,
        repository=RetentionRepository(bundle),
        apply=True,
        scheduled=True,
    )
    assert scheduled.retention_schedule_armed is True
    assert "drill_snapshot" not in scheduled.__dict__


@pytest.mark.parametrize(
    "mutator",
    [
        lambda c: c["accounts"]["columns"][0].update(type="TEXT"),
        lambda c: c["accounts"]["columns"][0].update(notnull=0),
        lambda c: c["accounts"]["columns"][0].update(default="1"),
        lambda c: c["accounts"]["columns"][0].update(pk=0),
        lambda c: c["leagues"]["foreign_keys"].clear(),
        lambda c: c["metrics"]["indexes"][0].update(unique=0),
        lambda c: c["metrics"]["indexes"][0].update(sql="wrong predicate"),
        lambda c: c["accounts"].update(sql="create table accounts (id integer)"),
    ],
    ids=("type", "nullability", "default", "pk", "fk", "unique", "predicate", "table-sql"),
)
def test_format_v1_catalog_rejects_each_schema_drift_category(tmp_path, mutator):
    settings = _settings(tmp_path)
    _empty_source(settings)
    connection = sqlite3.connect(settings.db_file)
    try:
        catalog = catalog_spec(connection)
    finally:
        connection.close()
    mutated = copy.deepcopy(catalog)
    mutator(mutated)
    with pytest.raises(RecoveryError) as caught:
        validate_catalog(mutated)
    assert caught.value.code == "recovery_schema_drift"


def test_bundle_bound_and_safe_digest_tamper_fail_before_restore(tmp_path):
    settings = _settings(tmp_path)
    _empty_source(settings)
    with pytest.raises(RecoveryError, match="bound"):
        build_logical_bundle(settings.db_file, max_bundle_bytes=1)
    bundle, manifest = build_logical_bundle(settings.db_file)
    value = json.loads(bundle)
    value["manifest"]["safe_content_digest"] = "0" * 64
    with pytest.raises(RecoveryError) as caught:
        restore_bundle_to_scratch(json.dumps(value).encode(), tmp_path / "restored.db")
    assert caught.value.code == "recovery_bundle_invalid"
    assert manifest["account_statuses"] == []


def test_bundle_preserves_exact_datetime_text_and_retained_utf8_text(tmp_path):
    settings = _settings(tmp_path)
    _empty_source(settings)
    exact_datetime = "2026-08-13 01:02:03.456789+00:00"
    exact_text = "operator-\u03a9-\U0001f3c8-line\\ntext"
    connection = sqlite3.connect(settings.db_file)
    connection.execute(
        "INSERT INTO accounts(id,label,swid,espn_s2_encrypted,status,created_at,tenant_id) "
        "VALUES(1,?,?,?,?,?,(SELECT id FROM tenants LIMIT 1))",
        (exact_text, "source-secret", "ciphertext", "paused", exact_datetime),
    )
    connection.commit()
    connection.close()
    bundle, manifest = build_logical_bundle(settings.db_file)
    assert manifest["account_statuses"] == [{"id": 1, "status": "paused"}]
    restored = tmp_path / "restored.db"
    restore_bundle_to_scratch(bundle, restored)
    connection = sqlite3.connect(restored)
    try:
        assert connection.execute(
            "SELECT label,created_at,status FROM accounts WHERE id=1"
        ).fetchone() == (exact_text, exact_datetime, "needs_reauth")
    finally:
        connection.close()


@pytest.mark.parametrize(
    "patch",
    [
        {"format_version": True},
        {"artifact_bytes": False},
        {"retention_enforced": True},
        {"last_snapshot_at": "2026-08-13T00:00:00+00:00"},
        {"evidence_run_id": "not-an-id"},
    ],
)
def test_recovery_state_rejects_wrong_types_and_cross_field_invariants(tmp_path, patch):
    path = tmp_path / "state.json"
    value = {**RecoveryState().__dict__, **patch}
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    with pytest.raises(RecoveryError) as caught:
        load_state(path)
    assert caught.value.code == "recovery_state_corrupt"


def test_recovery_state_cannot_claim_enforcement_without_verified_point(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    save_state(
        settings.recovery_state_file,
        RecoveryState(retention_configured=True, retention_enforced=True),
    )
    with pytest.raises(RecoveryError) as caught:
        load_state(settings.recovery_state_file)
    assert caught.value.code == "recovery_state_corrupt"
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    monkeypatch.setattr("api.services.recovery._target_available", lambda _settings: True)
    assert recovery_status(settings).state == "corrupt"
    with pytest.raises(RecoveryAdmissionError) as admission:
        assert_recovery_write_allowed(settings)
    assert admission.value.code == "recovery_state_corrupt"


@pytest.mark.parametrize(
    "override",
    [
        {"recovery_stale_after_seconds": 86_399},
        {"recovery_stale_after_seconds": 86_401},
        {"recovery_api_min_interval_seconds": 59},
        {"recovery_required": True, "api_host": "0.0.0.0"},
    ],
)
def test_recovery_contract_limits_cannot_be_weakened(override):
    with pytest.raises(ValueError):
        Settings(**override)


def test_repository_distinguishes_empty_from_unreadable_latest(tmp_path):
    settings = _settings(tmp_path)

    class Runner:
        snapshots: list[dict] = []

        def run(self, argv, **_kwargs):
            if "dump" in argv:
                return ProcessResult(1, b"", b"")
            return ProcessResult(0, json.dumps(self.snapshots).encode(), b"")

    runner = Runner()
    repo = ResticRepository(settings, runner=runner)
    assert repo.latest_bundle() is None
    runner.snapshots = [{"id": "a" * 64, "tags": ["espn-edge-private-v1"]}]
    with pytest.raises(RecoveryError) as caught:
        repo.latest_bundle()
    assert caught.value.code == "recovery_repository_error"


def test_scratch_scavenging_removes_only_old_owned_run_directories(tmp_path):
    from api.services.recovery import _new_scratch_run, _safe_scratch_root

    root = tmp_path / "scratch"
    canonical, token = _safe_scratch_root(root)
    old = _new_scratch_run(canonical, token)
    fresh = _new_scratch_run(canonical, token)
    unrelated = root / "keep-me"
    unrelated.mkdir()
    fake_run = root / "run-not-owned"
    fake_run.mkdir(mode=0o700)
    os.utime(old, (0, 0))
    assert scavenge_scratch(root, older_than_seconds=1) == 1
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()
    assert fake_run.exists()


def test_scratch_rejects_symlink_mode_owner_and_marker_device_attacks(tmp_path, monkeypatch):
    from api.services.recovery import _new_scratch_run, _safe_scratch_root

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "scratch-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RecoveryError):
        _safe_scratch_root(link)

    root = tmp_path / "scratch"
    canonical, token = _safe_scratch_root(root)
    run = _new_scratch_run(canonical, token)
    run.chmod(0o755)
    os.utime(run, (0, 0))
    assert scavenge_scratch(root, older_than_seconds=1) == 0
    assert run.exists()
    run.chmod(0o700)
    marker_path = run / ".espn-edge-recovery-run-v1"
    marker = json.loads(marker_path.read_text())
    marker["device"] += 1
    marker_path.write_text(json.dumps(marker))
    marker_path.chmod(0o600)
    assert scavenge_scratch(root, older_than_seconds=1) == 0
    assert run.exists()

    real_uid = os.getuid()
    monkeypatch.setattr("api.services.recovery.os.getuid", lambda: real_uid + 1)
    with pytest.raises(RecoveryError):
        _safe_scratch_root(root)


def test_scratch_cleanup_failure_is_a_hard_failure(tmp_path, monkeypatch):
    from api.services.recovery import _new_scratch_run, _safe_scratch_root

    root, token = _safe_scratch_root(tmp_path / "scratch")
    run = _new_scratch_run(root, token)
    os.utime(run, (0, 0))
    real_rmdir = os.rmdir

    def fail_quarantine_rmdir(path, *, dir_fd=None):
        if str(path).startswith(".quarantine-"):
            return None
        return real_rmdir(path, dir_fd=dir_fd)

    monkeypatch.setattr("api.services.recovery.os.rmdir", fail_quarantine_rmdir)
    with pytest.raises(RecoveryError) as caught:
        scavenge_scratch(root, older_than_seconds=0)
    assert caught.value.code == "recovery_scratch_unsafe"
    assert not run.exists()
    assert len(list(root.glob(".quarantine-*"))) == 1


def test_scratch_rename_after_validation_never_deletes_replacement(tmp_path, monkeypatch):
    from api.services.recovery import _new_scratch_run, _safe_scratch_root

    root, token = _safe_scratch_root(tmp_path / "scratch")
    run = _new_scratch_run(root, token)
    os.utime(run, (0, 0))
    replacement = root / run.name
    displaced = root / "attacker-displaced-original"

    def swap(_root, name):
        os.rename(root / name, displaced)
        replacement.mkdir(mode=0o700)
        (replacement / "must-survive").write_text("replacement")

    monkeypatch.setattr("api.services.recovery._before_scratch_quarantine", swap)
    with pytest.raises(RecoveryError) as caught:
        scavenge_scratch(root, older_than_seconds=0)
    assert caught.value.code == "recovery_scratch_unsafe"
    assert (replacement / "must-survive").read_text() == "replacement"
    assert displaced.exists()
    assert not list(root.glob(".quarantine-*"))


def test_scratch_post_validation_path_replacement_survives(tmp_path, monkeypatch):
    from api.services.recovery import _new_scratch_run, _safe_scratch_root

    root, token = _safe_scratch_root(tmp_path / "scratch")
    run = _new_scratch_run(root, token)
    (run / "private-scratch").write_text("synthetic")
    os.utime(run, (0, 0))
    displaced = root / "verified-original"

    def replace_after_validation(_root, quarantine_name):
        os.rename(root / quarantine_name, displaced)
        replacement = root / quarantine_name
        replacement.mkdir(mode=0o700)
        (replacement / "must-survive").write_text("replacement")

    monkeypatch.setattr(
        "api.services.recovery._before_scratch_descriptor_delete",
        replace_after_validation,
    )
    with pytest.raises(RecoveryError) as caught:
        scavenge_scratch(root, older_than_seconds=0)
    assert caught.value.code == "recovery_scratch_unsafe"
    replacements = list(root.glob(".quarantine-*/must-survive"))
    assert len(replacements) == 1
    assert replacements[0].read_text() == "replacement"
    assert displaced.exists()


def test_scratch_rejects_cross_device_insertion_after_validation(tmp_path, monkeypatch):
    from api.services.recovery import _new_scratch_run, _safe_scratch_root

    root, token = _safe_scratch_root(tmp_path / "scratch")
    run = _new_scratch_run(root, token)
    os.utime(run, (0, 0))
    real_stat = os.stat

    def insert_after_validation(_root, quarantine_name):
        nested = root / quarantine_name / "nested-mount"
        nested.mkdir()
        (nested / "must-survive").write_text("cross-device")

    def cross_device(path, *, dir_fd=None, follow_symlinks=True):
        result = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == "nested-mount" and dir_fd is not None:
            values = list(result)
            values[2] = result.st_dev + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(
        "api.services.recovery._before_scratch_descriptor_delete",
        insert_after_validation,
    )
    monkeypatch.setattr("api.services.recovery.os.stat", cross_device)
    with pytest.raises(RecoveryError) as caught:
        scavenge_scratch(root, older_than_seconds=0)
    assert caught.value.code == "recovery_scratch_unsafe"
    quarantines = list(root.glob(".quarantine-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "nested-mount" / "must-survive").exists()


@pytest.mark.parametrize(
    "stage",
    ["run-created", "bundle-written", "db-created", "reads-complete", "verified"],
)
def test_sigkill_restore_stage_residue_is_scavenged_on_restart(tmp_path, stage):
    root = tmp_path / stage
    script = """
import os,sys
from pathlib import Path
from api.services.recovery import _safe_scratch_root,_new_scratch_run
root,token=_safe_scratch_root(Path(sys.argv[1]))
run=_new_scratch_run(root,token)
(run/sys.argv[2]).write_text('synthetic-stage')
os.utime(run,(0,0))
os.kill(os.getpid(),9)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), stage],
        check=False,
        env={**os.environ, "RECOVERY_REQUIRED": "false"},
    )
    assert result.returncode == -9
    assert scavenge_scratch(root, older_than_seconds=0) == 1
    assert not list(root.glob("run-*"))


def test_retention_identity_or_plan_swap_aborts_before_apply(tmp_path):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)

    class SwappingRepository(RetentionRepository):
        def __init__(self, payload, field):
            super().__init__(payload)
            self.field = field
            self.identity_calls = 0

        def repository_id(self, *, pin=None):
            del pin
            self.identity_calls += 1
            if self.field == "repository" and self.identity_calls > 1:
                return "d" * 64
            return super().repository_id()

        def validate_topology(self, source):
            if self.field == "topology" and self.retention_calls:
                return (
                    self.topology[0],
                    StorageIdentity("swapped-disk", "external", "3:3"),
                )
            return super().validate_topology(source)

        def retention(self, *, apply, pin=None, expected_repository_id=None):
            if self.field == "inside-apply" and apply:
                self.repo_id = "e" * 64
            result = super().retention(
                apply=apply,
                pin=pin,
                expected_repository_id=expected_repository_id,
            )
            if self.field == "plan" and len(self.retention_calls) == 2:
                removed = {"id": self.snapshot_id, "tags": ["espn-edge-private-v1"]}
                return json.dumps([{"keep": [], "remove": [removed]}]).encode()
            return result

    for field in ("repository", "topology", "plan", "inside-apply"):
        repo = SwappingRepository(bundle, field)
        with pytest.raises(RecoveryError):
            apply_retention(
                settings,
                repository=repo,
                apply=True,
                drill_snapshot="latest",
            )
        assert True not in repo.retention_calls


@pytest.mark.parametrize(
    "stage",
    ["tool", "pin", "repository", "inventory", "manifest", "plan"],
)
def test_retention_apply_clears_enforcement_before_every_preflight_failure(
    tmp_path, monkeypatch, stage
):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    now = datetime(2026, 8, 13, tzinfo=UTC)
    save_state(
        settings.recovery_state_file,
        RecoveryState(
            last_coverage_at=now.isoformat(),
            last_snapshot_at=now.isoformat(),
            retention_configured=True,
            retention_enforced=True,
            last_result_code="recovery_ok",
        ),
    )

    class PreflightFailure(RetentionRepository):
        def validate_tool(self):
            if stage == "tool":
                raise RecoveryError("recovery_tool_invalid", "synthetic")

        @contextmanager
        def pin_repository(self, source, *, lock_fd):
            if stage == "pin":
                raise RecoveryError("recovery_target_unavailable", "synthetic")
            with super().pin_repository(source, lock_fd=lock_fd) as pin:
                yield pin

        def repository_id(self, *, pin=None):
            if stage == "repository":
                raise RecoveryError("recovery_repository_error", "synthetic")
            return super().repository_id(pin=pin)

        def snapshots(self, *, pin=None):
            if stage == "inventory":
                raise RecoveryError("recovery_repository_error", "synthetic")
            return super().snapshots(pin=pin)

        def latest_bundle(self, *, expect_snapshot=False, pin=None):
            if stage == "manifest":
                raise RecoveryError("recovery_repository_error", "synthetic")
            return super().latest_bundle(expect_snapshot=expect_snapshot, pin=pin)

        def retention(self, *, apply, pin=None, expected_repository_id=None):
            if stage == "plan" and not apply:
                raise RecoveryError("recovery_retention_invalid", "synthetic")
            return super().retention(
                apply=apply,
                pin=pin,
                expected_repository_id=expected_repository_id,
            )

    with pytest.raises(RecoveryError):
        apply_retention(
            settings,
            repository=PreflightFailure(bundle),
            apply=True,
            drill_snapshot="latest",
        )
    state = load_state(settings.recovery_state_file)
    assert state.retention_enforced is False
    assert state.last_coverage_at == now.isoformat()
    assert state.last_snapshot_at == now.isoformat()
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    monkeypatch.setattr("api.services.recovery._target_available", lambda _settings: True)
    with pytest.raises(RecoveryAdmissionError) as blocked:
        assert_recovery_write_allowed(settings, clock=lambda: now)
    assert blocked.value.code == "recovery_retention_not_enforced"


def test_retention_transition_serializes_backup_and_api_reservation(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _empty_source(settings)
    bundle, _ = build_logical_bundle(settings.db_file)
    now = datetime(2026, 8, 13, tzinfo=UTC)
    save_state(
        settings.recovery_state_file,
        RecoveryState(
            last_coverage_at=now.isoformat(),
            last_snapshot_at=now.isoformat(),
            retention_configured=True,
            retention_enforced=True,
            last_result_code="recovery_ok",
        ),
    )
    entered = threading.Event()
    release = threading.Event()

    class BarrierRepository(RetentionRepository):
        def validate_tool(self):
            entered.set()
            assert release.wait(timeout=5)

    errors = []

    def retain():
        try:
            apply_retention(
                settings,
                repository=BarrierRepository(bundle),
                apply=True,
                drill_snapshot="latest",
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=retain)
    thread.start()
    assert entered.wait(timeout=5)
    monkeypatch.setattr("api.services.recovery.native_supported", lambda: True)
    with pytest.raises(RecoveryError) as backup_busy:
        run_backup("manual", settings, repository=FakeRepository(), clock=lambda: now)
    assert backup_busy.value.code == "recovery_busy"
    with pytest.raises(RecoveryError) as api_busy:
        reserve_api_trigger(settings, clock=lambda: now)
    assert api_busy.value.code == "recovery_busy"
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
