"""Operator CLI for Phase 30 private-local recovery.

Commands never accept or print a repository secret. The normal automation
credential is obtained by restic through the configured absolute credential
command; restore-drill uses one application-owned hidden controlling-TTY prompt
and fresh anonymous password descriptors for its non-interactive restic calls.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import io
import json
import os
import plistlib
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ROOT, get_settings
from .services.recovery import (
    BoundedSubprocessRunner,
    ProcessResult,
    RecoveryError,
    ResticRepository,
    _normalize_retention_drill_selector,
    apply_retention,
    arm_retention_schedule,
    filevault_enabled,
    recovery_status,
    restore_drill,
    run_backup,
    secret_free_error,
)

_LAUNCHD_LABEL = "com.espn-edge.private-recovery"
_RETENTION_LABEL = "com.espn-edge.private-recovery-retention"
_LAUNCHCTL_TIMEOUT_SECONDS = 5.0
_LAUNCHCTL_MAX_OUTPUT_BYTES = 4_096
_LAUNCHCTL_RECONCILE_ATTEMPTS = 4
_LAUNCHCTL_RECONCILE_INTERVAL_SECONDS = 0.05
_LAUNCHCTL_MISSING_SERVICE_PHRASE = b"could not find service"
_LAUNCHD_LABELS = frozenset({_LAUNCHD_LABEL, _RETENTION_LABEL})


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _stable_restore_read_projection(payloads: list[Any]) -> list[Any]:
    """Remove only clock-derived opportunity fields from restore equality.

    `source.age_hours` is rounded from the current clock and `source.stale` is
    derived from that age and the configured TTL. Their persisted input,
    `source.fetched_at`, and every other field remain in the projection.
    """

    if len(payloads) != 4 or not isinstance(payloads[3], dict):
        raise RecoveryError(
            "recovery_bundle_invalid", "Restore read projection is invalid."
        )
    charts = payloads[3]
    source = charts.get("source")
    required = {"age_hours", "stale", "fetched_at"}
    if not isinstance(source, dict) or not required.issubset(source):
        raise RecoveryError(
            "recovery_bundle_invalid", "Restore read projection is invalid."
        )
    projected = list(payloads)
    projected[3] = {
        **charts,
        "source": {
            key: value
            for key, value in source.items()
            if key not in {"age_hours", "stale"}
        },
    }
    return projected


def _read_application_payloads(database: Path) -> list[Any]:
    from fastapi.encoders import jsonable_encoder
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from .services.draft_analytics import build_strategies
    from .services.exposure import build_exposure
    from .services.opportunity import build_opportunity_charts
    from .services.portfolio import build_portfolio_rows
    from .services.portfolio_filters import PortfolioFilters

    engine = create_engine(f"sqlite:///{database}")
    filters = PortfolioFilters(season=2025)
    try:
        with Session(engine) as session:
            return _stable_restore_read_projection(
                jsonable_encoder(
                    [
                        build_portfolio_rows(session),
                        build_exposure(session, scope="me", filters=filters),
                        build_strategies(session, filters),
                        build_opportunity_charts(
                            session, filters, view="all", position="WR"
                        ),
                    ]
                )
            )
    finally:
        engine.dispose()


class _SyntheticRecoveryEspn:
    def __init__(self) -> None:
        self.calls = 0
        self.payload = {
            "settings": {
                "name": "Recovery Synthetic League",
                "size": 1,
                "scoringSettings": {"scoringItems": []},
                "rosterSettings": {"lineupSlotCounts": {}},
                "draftSettings": {"type": "SNAKE"},
                "scheduleSettings": {},
            },
            "status": {},
            "teams": [
                {
                    "id": 1,
                    "name": "Recovery Synthetic Team",
                    "owners": ["{AAAA-1111}"],
                    "record": {"overall": {}},
                }
            ],
            "draftDetail": {"drafted": False, "picks": []},
            "schedule": [],
            "transactions": [],
        }

    def fetch_views(self, _league_id, _season, views, **_kwargs):
        self.calls += 1
        return {"players": []} if "kona_player_info" in views else self.payload

    def fetch_pro_schedule(self, _season):
        self.calls += 1
        return {"settings": {"proTeams": []}}


def _internal_application_verify() -> dict[str, bool]:
    """Child-process verifier used only by the operational restore drill."""
    source_raw = os.environ.get("RECOVERY_VERIFY_SOURCE", "")
    source = Path(source_raw)
    restored = get_settings().db_file
    if (
        not source.is_absolute()
        or not source.is_file()
        or source.is_symlink()
        or not restored.is_file()
        or restored.is_symlink()
    ):
        raise RecoveryError("recovery_bundle_invalid", "Restore verifier input is unsafe.")

    source_payloads = _read_application_payloads(source)
    restored_payloads = _read_application_payloads(restored)
    four_reads_match = hmac.compare_digest(
        _canonical_digest(source_payloads), _canonical_digest(restored_payloads)
    )

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from . import verify
    from .crypto import encrypt
    from .db import get_session
    from .main import app
    from .models import Account, AdpSnapshot, AiReport, League, MetricSnapshot, OpportunityImport
    from .services.espn import EspnReauthRequired, cookies_for_account
    from .services.sync import SyncService

    paths = [
        "/api/portfolio",
        "/api/portfolio/exposure?scope=me&season=2025",
        "/api/portfolio/strategies?season=2025",
        "/api/portfolio/opportunity/charts?view=all&position=WR&season=2025",
    ]
    source_engine = create_engine(f"sqlite:///{source}")

    def source_session():
        with Session(source_engine) as source_route_session:
            yield source_route_session

    app.dependency_overrides[get_session] = source_session
    try:
        with TestClient(app, client=("127.0.0.1", 50_000)) as source_client:
            source_route_responses = [source_client.get(path) for path in paths]
            source_routes_ok = all(
                response.status_code == 200 for response in source_route_responses
            )
            source_route_payloads = [
                response.json() for response in source_route_responses
            ]
    finally:
        app.dependency_overrides.pop(get_session, None)
        source_engine.dispose()

    with TestClient(app, client=("127.0.0.1", 50_000)) as client:
        responses = [client.get(path) for path in paths]
        app_routes_ok = all(response.status_code == 200 for response in responses)
        app_routes_match = source_routes_ok and app_routes_ok and hmac.compare_digest(
            _canonical_digest(
                _stable_restore_read_projection(
                    [response.json() for response in responses]
                )
            ),
            _canonical_digest(_stable_restore_read_projection(source_route_payloads)),
        )

        engine = create_engine(f"sqlite:///{restored}")
        source_engine = create_engine(f"sqlite:///{source}")
        try:
            with Session(engine) as session, Session(source_engine) as source_session:
                account = session.scalar(select(Account).order_by(Account.id))
                league = session.scalar(
                    select(League)
                    .where(League.account_id.is_not(None))
                    .order_by(League.id)
                )
                if account is None or league is None:
                    raise RecoveryError(
                        "recovery_bundle_invalid", "Restored reauthentication probe is unavailable."
                    )
                try:
                    cookies_for_account(account)
                except EspnReauthRequired:
                    direct_cookie_reauth = True
                else:
                    direct_cookie_reauth = False

                class ProviderSpy:
                    calls = 0

                    def fetch_views(self, *_args, **_kwargs):
                        self.calls += 1
                        raise AssertionError("provider must not run before reauthentication")

                provider = ProviderSpy()
                direct_result = SyncService(session, espn=provider).sync_league(league)
                direct_reauth = direct_result.get("needs_reauth") is True
                pre_reauth_zero_provider = provider.calls == 0

                discovery = client.get(f"/api/leagues/discover/{account.id}")
                discovery_reauth = discovery.status_code == 401
                router = client.post(f"/api/leagues/{league.id}/sync")
                router_reauth = (
                    router.status_code == 200 and router.json().get("needs_reauth") is True
                )

                output = io.StringIO()
                original_cookie_loader = verify._load_cookies
                verify._load_cookies = lambda: None
                try:
                    with contextlib.redirect_stdout(output):
                        verify_code = verify.main(
                            [
                                "--league",
                                league.espn_league_id,
                                "--season",
                                str(league.season),
                            ]
                        )
                finally:
                    verify._load_cookies = original_cookie_loader
                verify_reauth = verify_code == 3

                families = (AiReport, MetricSnapshot, OpportunityImport, AdpSnapshot)
                retained_families_match = all(
                    session.scalar(select(func.count()).select_from(model))
                    == source_session.scalar(select(func.count()).select_from(model))
                    for model in families
                )

                account.swid = "{AAAA-1111}"
                account.espn_s2_encrypted = encrypt("synthetic-reauth-cookie")
                account.status = "active"
                session.flush()
                fake = _SyntheticRecoveryEspn()
                sync_result = SyncService(session, espn=fake).sync_league(league)
                synthetic_reauth_sync = (
                    fake.calls > 0 and sync_result.get("needs_reauth") is not True
                )
        finally:
            engine.dispose()
            source_engine.dispose()

    return {
        "four_reads_match": four_reads_match,
        "app_routes_ok": app_routes_ok and app_routes_match,
        "pre_reauth_zero_provider": pre_reauth_zero_provider,
        "discovery_reauth": discovery_reauth,
        "router_reauth": router_reauth,
        "verify_reauth": verify_reauth,
        "direct_reauth": direct_cookie_reauth and direct_reauth,
        "synthetic_reauth_sync": synthetic_reauth_sync,
        "retained_families_match": retained_families_match,
    }


def _print(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def _doctor() -> dict[str, Any]:
    settings = get_settings()
    status = recovery_status(settings)
    result = status.public_dict()
    result["filevault"] = "on" if filevault_enabled() else "off"
    if status.configured and status.target_available:
        repository = ResticRepository(settings)
        try:
            repository.validate_tool()
            source, target = repository.validate_topology(settings.db_file)
            result["tool"] = "verified"
            result["physical_separation"] = source.physical_parent != target.physical_parent
            result["source_media_class"] = source.media_class
            result["target_media_class"] = target.media_class
        except RecoveryError as exc:
            result["tool"] = "invalid"
            result["physical_separation"] = False
            result["last_result_code"] = exc.code
    return result


def _init_repository() -> None:
    settings = get_settings()
    repository = ResticRepository(settings)
    repository.validate_topology(settings.db_file)
    repository.init()


def _launchd_plist(*, retention: bool = False) -> bytes:
    # Keep the lexical venv entry point: resolving its symlink selects the base
    # framework interpreter and loses the venv package search path under launchd.
    python = ROOT / ".venv/bin/python"
    if (
        not python.is_absolute()
        or not python.is_file()
        or not os.access(python, os.X_OK)
    ):
        raise RecoveryError("recovery_tool_invalid", "Configured Python runtime is unavailable.")
    payload: dict[str, Any] = {
        "Label": _RETENTION_LABEL if retention else _LAUNCHD_LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "api.recovery",
            *(
                ["retention", "--scheduled-apply"]
                if retention
                else ["backup", "--reason", "hourly"]
            ),
        ],
        "WorkingDirectory": str(ROOT),
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    if retention:
        payload["StartCalendarInterval"] = {"Day": 1, "Hour": 4, "Minute": 15}
    else:
        payload["StartInterval"] = 3600
        payload["RunAtLoad"] = True
    # Repository/key configuration is read from the operator-owned .env; no
    # secret or private path is embedded in the plist itself.
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


@dataclass
class _InstalledLaunchdRunner:
    label: str
    target_name: str
    directory_fd: int
    plist_fd: int
    identity: tuple[int, int]
    closed: bool = False


def _launchd_domain() -> str:
    try:
        uid = os.getuid()
    except Exception as exc:
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        ) from exc
    if type(uid) is not int or uid < 0:
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        )
    return f"gui/{uid}"


def _launchd_target(label: str) -> Path:
    if label not in _LAUNCHD_LABELS:
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        )
    try:
        return Path.home() / "Library/LaunchAgents" / f"{label}.plist"
    except (OSError, RuntimeError) as exc:
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        ) from exc


def _retention_runner_target() -> Path:
    return _launchd_target(_RETENTION_LABEL)


def _launchctl_env() -> dict[str, str]:
    return {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def _run_launchctl(
    runner: BoundedSubprocessRunner,
    *arguments: str,
    pass_fds: tuple[int, ...] = (),
) -> ProcessResult:
    """Run one service-control operation with finite bounds and a stable error."""
    if not arguments or any(not isinstance(item, str) or not item for item in arguments):
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        )
    try:
        result = runner.run(
            ["/bin/launchctl", *arguments],
            env=_launchctl_env(),
            max_output=_LAUNCHCTL_MAX_OUTPUT_BYTES,
            timeout_seconds=_LAUNCHCTL_TIMEOUT_SECONDS,
            pass_fds=pass_fds,
        )
    except Exception as exc:
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        ) from exc
    if (
        not isinstance(result, ProcessResult)
        or type(result.returncode) is not int
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or len(result.stdout) > _LAUNCHCTL_MAX_OUTPUT_BYTES
        or len(result.stderr) > _LAUNCHCTL_MAX_OUTPUT_BYTES
    ):
        raise RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        )
    return result


def _launchctl_result_is_missing(result: ProcessResult) -> bool:
    output = (result.stdout + b"\n" + result.stderr).lower()
    return (
        result.returncode == 113
        and _LAUNCHCTL_MISSING_SERVICE_PHRASE in output
    )


def _launchd_runner_is_loaded(
    runner: BoundedSubprocessRunner, label: str
) -> bool:
    target = f"{_launchd_domain()}/{label}"
    result = _run_launchctl(runner, "print", target)
    if result.returncode == 0:
        return True
    if _launchctl_result_is_missing(result):
        return False
    raise RecoveryError(
        "recovery_repository_error", "Launchd service state is unavailable."
    )


def _retention_runner_is_loaded(runner: BoundedSubprocessRunner) -> bool:
    return _launchd_runner_is_loaded(runner, _RETENTION_LABEL)


def _assert_launchd_runner_absent(
    runner: BoundedSubprocessRunner,
    label: str,
    *,
    conflict_code: str,
    conflict_message: str,
) -> None:
    target = _launchd_target(label)
    try:
        target.lstat()
        path_present = True
    except FileNotFoundError:
        path_present = False
    except OSError as exc:
        raise RecoveryError(
            "recovery_repository_error", "Launchd service state is unavailable."
        ) from exc
    if path_present or _launchd_runner_is_loaded(runner, label):
        raise RecoveryError(conflict_code, conflict_message)


def _assert_retention_runner_absent(runner: BoundedSubprocessRunner) -> None:
    _assert_launchd_runner_absent(
        runner,
        _RETENTION_LABEL,
        conflict_code="recovery_retention_invalid",
        conflict_message="Recurring retention is already installed or loaded.",
    )


def _close_installed_runner(installed: _InstalledLaunchdRunner) -> None:
    if installed.closed:
        return
    installed.closed = True
    with contextlib.suppress(OSError):
        os.close(installed.plist_fd)
    with contextlib.suppress(OSError):
        os.close(installed.directory_fd)


def _unlink_installed_runner(installed: _InstalledLaunchdRunner) -> None:
    try:
        try:
            current = os.stat(
                installed.target_name,
                dir_fd=installed.directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) != installed.identity:
            return
        os.unlink(installed.target_name, dir_fd=installed.directory_fd)
        os.fsync(installed.directory_fd)
    except OSError as exc:
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner cleanup is required."
        ) from exc


def _bootout_launchd_runner(
    runner: BoundedSubprocessRunner, label: str
) -> None:
    result = _run_launchctl(runner, "bootout", f"{_launchd_domain()}/{label}")
    if result.returncode == 0 or _launchctl_result_is_missing(result):
        return
    raise RecoveryError(
        "recovery_repository_error", "Launchd runner cleanup is required."
    )


def _rollback_launchd_runner(
    runner: BoundedSubprocessRunner,
    installed: _InstalledLaunchdRunner,
) -> None:
    """Boundedly quiesce a possibly late load, then remove only our inode."""
    consecutive_absent = 0
    service_absent = False
    last_error: RecoveryError | None = None
    cleanup_error: RecoveryError | None = None
    try:
        for attempt in range(_LAUNCHCTL_RECONCILE_ATTEMPTS):
            try:
                _bootout_launchd_runner(runner, installed.label)
            except RecoveryError as exc:
                last_error = exc
            try:
                loaded = _launchd_runner_is_loaded(runner, installed.label)
            except RecoveryError as exc:
                last_error = exc
                consecutive_absent = 0
            else:
                if loaded:
                    consecutive_absent = 0
                else:
                    consecutive_absent += 1
                    if consecutive_absent >= 2:
                        service_absent = True
                        break
            if attempt + 1 < _LAUNCHCTL_RECONCILE_ATTEMPTS:
                time.sleep(_LAUNCHCTL_RECONCILE_INTERVAL_SECONDS)
    finally:
        try:
            _unlink_installed_runner(installed)
        except RecoveryError as exc:
            cleanup_error = exc
        _close_installed_runner(installed)
    if cleanup_error is not None:
        raise cleanup_error
    if not service_absent:
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner cleanup is required."
        ) from last_error


def _parent_and_target_still_bound(
    parent: Path, installed: _InstalledLaunchdRunner
) -> bool:
    try:
        parent_now = parent.stat(follow_symlinks=False)
        parent_open = os.fstat(installed.directory_fd)
        target_now = os.stat(
            installed.target_name,
            dir_fd=installed.directory_fd,
            follow_symlinks=False,
        )
    except OSError:
        return False
    return (
        stat.S_ISDIR(parent_now.st_mode)
        and (parent_now.st_dev, parent_now.st_ino)
        == (parent_open.st_dev, parent_open.st_ino)
        and (target_now.st_dev, target_now.st_ino) == installed.identity
    )


def _install_launchd_runner(
    runner: BoundedSubprocessRunner,
    *,
    label: str,
    content: bytes,
    load: bool,
    conflict_code: str,
    conflict_message: str,
) -> _InstalledLaunchdRunner | None:
    """Install and optionally load one descriptor-bound plist transactionally."""
    _assert_launchd_runner_absent(
        runner,
        label,
        conflict_code=conflict_code,
        conflict_message=conflict_message,
    )
    target = _launchd_target(label)
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        directory_fd = os.open(target.parent, flags)
    except OSError as exc:
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner install failed."
        ) from exc

    installed: _InstalledLaunchdRunner | None = None
    linked_identity: tuple[int, int] | None = None
    plist_fd: int | None = None
    tmp_name = f".{target.name}.{os.getpid()}.tmp"
    try:
        write_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(write_fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(
            tmp_name,
            target.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        linked = os.stat(
            target.name, dir_fd=directory_fd, follow_symlinks=False
        )
        linked_identity = (linked.st_dev, linked.st_ino)
        read_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        plist_fd = os.open(target.name, read_flags, dir_fd=directory_fd)
        current = os.fstat(plist_fd)
        installed = _InstalledLaunchdRunner(
            label=label,
            target_name=target.name,
            directory_fd=directory_fd,
            plist_fd=plist_fd,
            identity=(current.st_dev, current.st_ino),
        )
        if (
            installed.identity != linked_identity
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
            or os.pread(plist_fd, len(content) + 1, 0) != content
        ):
            raise OSError("installed plist validation failed")
        os.fsync(directory_fd)
    except OSError as exc:
        if installed is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=installed.directory_fd)
            with contextlib.suppress(RecoveryError):
                _unlink_installed_runner(installed)
            _close_installed_runner(installed)
        else:
            if plist_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(plist_fd)
            if linked_identity is not None:
                with contextlib.suppress(OSError):
                    current = os.stat(
                        target.name, dir_fd=directory_fd, follow_symlinks=False
                    )
                    if (current.st_dev, current.st_ino) == linked_identity:
                        os.unlink(target.name, dir_fd=directory_fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=directory_fd)
            with contextlib.suppress(OSError):
                os.close(directory_fd)
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner install failed."
        ) from exc
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name, dir_fd=directory_fd)

    if installed is None:  # pragma: no cover - defensive
        with contextlib.suppress(OSError):
            os.close(directory_fd)
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner install failed."
        )
    if not _parent_and_target_still_bound(target.parent, installed):
        try:
            _unlink_installed_runner(installed)
        finally:
            _close_installed_runner(installed)
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner install failed."
        )
    if not load:
        _close_installed_runner(installed)
        return None

    try:
        result = _run_launchctl(
            runner,
            "bootstrap",
            _launchd_domain(),
            f"/dev/fd/{installed.plist_fd}",
            pass_fds=(installed.plist_fd,),
        )
        if (
            result.returncode != 0
            or not _parent_and_target_still_bound(target.parent, installed)
            or not _launchd_runner_is_loaded(runner, label)
        ):
            raise RecoveryError(
                "recovery_repository_error", "Launchd runner could not be loaded."
            )
    except (OSError, RecoveryError, TypeError, ValueError) as exc:
        try:
            _rollback_launchd_runner(runner, installed)
        except RecoveryError:
            raise
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError(
            "recovery_repository_error", "Launchd runner could not be loaded."
        ) from exc
    return installed


def _install_runner(load: bool) -> None:
    # The ordinary runner command installs only the non-destructive hourly
    # backup. Recurring forget/prune has its own explicit approval transition.
    runner = BoundedSubprocessRunner()
    _assert_retention_runner_absent(runner)
    installed = _install_launchd_runner(
        runner,
        label=_LAUNCHD_LABEL,
        content=_launchd_plist(),
        load=load,
        conflict_code="recovery_repository_error",
        conflict_message="Launchd runner is already installed or loaded.",
    )
    if installed is not None:
        _close_installed_runner(installed)


def _install_retention_runner(
    runner: BoundedSubprocessRunner,
) -> _InstalledLaunchdRunner:
    """Atomically install/load the approved recurring runner, or roll it back."""
    installed = _install_launchd_runner(
        runner,
        label=_RETENTION_LABEL,
        content=_launchd_plist(retention=True),
        load=True,
        conflict_code="recovery_retention_invalid",
        conflict_message="Recurring retention is already installed or loaded.",
    )
    if installed is None:  # pragma: no cover - load=True always retains authority
        raise RecoveryError(
            "recovery_repository_error", "Retention runner could not be loaded."
        )
    return installed


def _enable_retention(drill_snapshot: str) -> Any:
    runner = BoundedSubprocessRunner()
    # Reject a stale/loaded recurring runner before the first destructive call.
    _assert_retention_runner_absent(runner)
    apply_retention(apply=True, drill_snapshot=drill_snapshot)
    installed = _install_retention_runner(runner)
    try:
        result = arm_retention_schedule()
    except RecoveryError as exc:
        arm_error = exc
    except Exception:
        arm_error = RecoveryError(
            "recovery_repository_error", "Launchd service control failed."
        )
    else:
        _close_installed_runner(installed)
        return result
    _rollback_launchd_runner(runner, installed)
    raise arm_error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="api.recovery", description="Private recovery control")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("internal-verify", help=argparse.SUPPRESS)
    sub.add_parser("doctor").add_argument("--json", action="store_true")
    init = sub.add_parser("init")
    init.add_argument("--confirm-empty-repository", action="store_true")
    backup = sub.add_parser("backup")
    backup.add_argument(
        "--reason",
        choices=("hourly", "post-clean-portfolio-sync", "manual"),
        required=True,
    )
    runner = sub.add_parser("runner")
    runner_sub = runner.add_subparsers(dest="runner_command", required=True)
    install = runner_sub.add_parser("install")
    install.add_argument("--load", action="store_true")
    retention = sub.add_parser("retention")
    retention_mode = retention.add_mutually_exclusive_group(required=True)
    retention_mode.add_argument("--dry-run", action="store_true")
    retention_mode.add_argument("--enable", action="store_true")
    retention_mode.add_argument("--scheduled-apply", action="store_true", help=argparse.SUPPRESS)
    retention.add_argument("--apply", action="store_true")
    retention.add_argument("--drill-snapshot")
    retention.add_argument("--json", action="store_true")
    restore = sub.add_parser("restore-drill")
    restore.add_argument("--snapshot", default="latest")
    restore.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "internal-verify":
            result = _internal_application_verify()
        elif args.command == "doctor":
            result = _doctor()
        elif args.command == "init":
            if not args.confirm_empty_repository:
                raise RecoveryError(
                    "recovery_repository_error",
                    "Repository initialization needs explicit confirmation.",
                )
            _init_repository()
            result = {"result_code": "recovery_ok"}
        elif args.command == "backup":
            result = run_backup(args.reason)
        elif args.command == "runner":
            _install_runner(args.load)
            result = {"result_code": "recovery_ok", "loaded": bool(args.load)}
        elif args.command == "retention":
            if args.dry_run:
                if args.apply:
                    raise RecoveryError(
                        "recovery_retention_invalid",
                        "Dry-run retention cannot apply changes.",
                    )
                drill_snapshot = _normalize_retention_drill_selector(
                    args.drill_snapshot
                )
                state = apply_retention(
                    apply=False,
                    drill_snapshot=drill_snapshot,
                )
            elif args.enable:
                if not args.apply:
                    raise RecoveryError(
                        "recovery_retention_invalid",
                        "Enabling retention requires the exact --enable --apply transition.",
                    )
                drill_snapshot = _normalize_retention_drill_selector(
                    args.drill_snapshot
                )
                state = _enable_retention(drill_snapshot)
            else:
                if args.apply or args.drill_snapshot is not None:
                    raise RecoveryError(
                        "recovery_retention_invalid",
                        "Scheduled retention does not accept manual approval arguments.",
                    )
                state = apply_retention(apply=True, scheduled=True)
            result = {
                "result_code": state.last_result_code,
                "dry_run": bool(args.dry_run),
                "applied": not bool(args.dry_run),
                # Application does not become enforced until the subsequent
                # independent break-glass restore succeeds.
                "enforced": state.retention_enforced,
            }
        else:
            result = restore_drill(args.snapshot)
        _print(result)
        return 0
    except RecoveryError as exc:
        _print(secret_free_error(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
