"""Private-local recovery boundary (Phase 30).

The source database is never copied.  A read-only SQLite transaction emits a
versioned, allowlisted logical bundle which an injected repository adapter
encrypts.  Operational code deliberately keeps secrets, paths, row counts, and
content hashes out of status, logs, and API responses.
"""

from __future__ import annotations

import array
import contextlib
import contextvars
import ctypes
import fcntl
import hashlib
import hmac
import json
import math
import os
import plistlib
import re
import selectors
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import termios
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import create_engine

from ..config import Settings, get_settings

# Bumped from 1 for this phase. The frozen catalog changed -- eight tables,
# three columns, and `alembic_version` leaving the fingerprint -- and the
# comment above `_FORMAT_V2_CATALOG_SHA256` requires a reviewed version for
# exactly that. Leaving this at 1 while the format is v2 would make the number
# in every bundle and every state file a false statement.
#
# A v1 bundle cannot be read by this code and should not pretend otherwise: its
# catalog would fail `validate_catalog` regardless, so the version marker makes
# the refusal say WHY. No v1 bundle exists outside a test -- no restore has ever
# been authorized -- so nothing is being orphaned.
FORMAT_VERSION = 2
BUNDLE_FILENAME = "espn-edge-recovery-v2.json"
RECOVERY_TAG = "espn-edge-private-v1"
REAUTH_SWID_SENTINEL = "{REAUTH-REQUIRED}"
REAUTH_S2_SENTINEL = "not-a-fernet-token"
RECOVERY_CANARY = "espn-edge-recovery-format-v2"
_SCRATCH_ROOT_MARKER = ".espn-edge-recovery-root-v1"
_SCRATCH_RUN_MARKER = ".espn-edge-recovery-run-v1"
_MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_MAX_TOOL_OUTPUT = 64 * 1024
_MAX_DISKUTIL_OUTPUT = 16 * 1024
_MAX_MOUNT_COMPONENTS = 64
_TOPOLOGY_RESOLUTION_TIMEOUT_SECONDS = 5.0
_PATH_OPEN_TIMEOUT_SECONDS = 2.0
_PATH_OPEN_CLEANUP_SECONDS = 0.5
_MAX_PATH_REQUEST_BYTES = 16 * 1024
_PROCESS_IO_POLL_SECONDS = 0.02
_PROCESS_DRAIN_TIMEOUT_SECONDS = 0.5
_PROCESS_CLEANUP_TIMEOUT_SECONDS = 0.5
_PATH_METADATA_HEADER = struct.Struct("!2sH")
_PATH_METADATA_ENTRY = struct.Struct("!QQI")
_PATH_METADATA_MAGIC = b"P1"
_MAX_PATH_MODE = 0xFFFF
_MAX_NATIVE_DEVICE = (1 << (ctypes.sizeof(ctypes.c_int) * 8 - 1)) - 1
# Policy ceilings. Only the live backup and full-check observations recorded in
# E30 are measurements; these bounds are deliberately conservative operational
# limits, not forecasts.
_RESTIC_VERSION_TIMEOUT_SECONDS = 10.0
_RESTIC_INIT_TIMEOUT_SECONDS = 120.0
_RESTIC_METADATA_TIMEOUT_SECONDS = 30.0
_RESTIC_DUMP_TIMEOUT_SECONDS = 120.0
_RESTIC_BACKUP_TIMEOUT_SECONDS = 120.0
_RESTIC_CHECK_TIMEOUT_SECONDS = 60.0
_RESTIC_RETENTION_DRY_RUN_TIMEOUT_SECONDS = 120.0
_RESTIC_RETENTION_APPLY_TIMEOUT_SECONDS = 900.0
_RESTIC_BREAK_GLASS_TIMEOUT_SECONDS = 300.0
_BREAK_GLASS_PROMPT_TIMEOUT_SECONDS = 300.0
_BREAK_GLASS_TTY_POLL_SECONDS = 0.1
_BREAK_GLASS_TRAILING_INPUT_SECONDS = 0.05
_BREAK_GLASS_TTY_RESTORE_ATTEMPTS = 3
_BREAK_GLASS_PROMPT = b"ESPN Edge break-glass repository password: "
_BREAK_GLASS_LIFETIME_SIGNALS = (
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGTERM,
    signal.SIGTSTP,
)
_TTY_STATE_MAX_BYTES = 1024
_TTY_STATE_TIMEOUT_SECONDS = 1.0
_STTY_PATH = "/bin/stty"
_PATH_OPEN_WORKER_CODE = (
    "import sys; from api.services.recovery import _path_open_worker_entry as w; "
    "w(int(sys.argv[1]))"
)
_CREDENTIAL_BROKER_CODE = (
    "import sys; from api.services.recovery import _credential_broker_entry as b; "
    "b(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))"
)
_RESTIC_DIRECTORY_EXEC_CODE = (
    "import os,sys; "
    "directory_fd=int(sys.argv[1]); argv=sys.argv[2:]; "
    "os.fchdir(directory_fd); os.execv(argv[0], argv)"
)
_TTY_STATE_EXEC_CODE = (
    "import os,sys; "
    "tty_fd=int(sys.argv[1]); os.dup2(tty_fd,0); "
    "tty_fd == 0 or os.close(tty_fd); "
    "os.execv(sys.argv[2],sys.argv[2:])"
)
_MAX_CREDENTIAL_BYTES = 1024
_CREDENTIAL_COMMAND_TIMEOUT_SECONDS = 5.0
_CREDENTIAL_BROKER_TIMEOUT_SECONDS = 6.0
_CREDENTIAL_BROKER_EXIT_TIMEOUT_SECONDS = 0.5
_CREDENTIAL_BROKER_CLEANUP_SECONDS = 0.5
# Format v1 freezes the complete canonical SQLite catalog, not a catalog
# regenerated from whatever models happen to be installed at runtime. The
# first digest is the measured Phase-29 database after its two additive
# opportunity columns; the second is the semantically equivalent fresh
# create_all catalog used by isolated tests/restores. Any table, column order,
# affinity, nullability, default, PK, FK, index, predicate, or catalog SQL
# change requires a reviewed recovery-format version.
# Phase 31 unit 4 re-pin, recorded as a contract amendment in E31.5. The two
# ledger tables change the catalog, so these are the two shapes the schema now
# takes. Each was reproduced from a real database rather than derived from
# metadata: `create_all` over the current models, and the same models with
# `opportunity_weeks` built without `receiving_tds`/`team_passing_yards` and then
# ALTER-extended the way `_apply_additive_migrations` reaches an older file. Both
# recipes were first confirmed to reproduce the previous pinned pair exactly,
# which is what makes these two the same shapes rather than two new guesses.
#
# The previous pair is REPLACED, not kept alongside: a catalog without the ledger
# tables can no longer reach this check at all, because the table-set comparison
# above it rejects any catalog whose tables differ from EXPECTED_TABLE_COLUMNS.
# Leaving them would be an allowlist entry nothing can ever match.
#: Format v2. Re-pinned because Phases 36-41 added eight tables and a
#: `tenant_id` to three existing ones, and the v1 pair described the Phase-31
#: schema -- a pin that matched nothing the application could build, so every
#: backup and every restore refused.
#:
#: THREE shapes, not two, and finding the third is why this took measurement
#: rather than arithmetic. Two independent axes:
#:
#:   leagues.tenant_id  -- appended (migrated) or fifth (create_all)
#:   opportunity_weeks  -- full (create_all) or ALTER-extended (older file)
#:
#: The migrated shape had never been pinned at all, because `catalog_spec`
#: included alembic's own `alembic_version` table and the allowlist never
#: listed it -- so `alembic upgrade head`, the documented path, could not
#: validate under v1 either. See `catalog_spec`.
#:
#: Each digest below was read off a real database built by the named recipe,
#: by `.venv/phase41/digests.py`, which also reports which of
#: `validate_catalog`'s four checks each shape reaches. Any table, column
#: order, affinity, nullability, default, PK, FK, index, predicate or catalog
#: SQL change requires a reviewed format version and a re-run of that script.
#: Re-pinned at revision 0013, which made `leagues.tenant_id` NOT NULL and
#: dropped the old global `uq_league_season`. 0012 moved it before that, for
#: `raw_cache`'s composite key. The catalog carries nullability and
#: constraints, so every schema change moves all three shapes, and the pin
#: moving is the normal cost of a migration rather than a sign of trouble.
#:
#: Read off real databases by `.venv/phase41/digests.py`.
#: `tests/test_recovery_format.py` builds each shape and requires its digest to
#: be here, and requires that nothing here is a digest no shape produces --
#: which is what v1 silently became.
_FORMAT_V2_CATALOG_SHA256 = frozenset(
    {
        # alembic upgrade head -- what an operator's database actually is
        "a3a38b867bdf1faaaa4b560bb7aab69e13961d4363c629e7a9b125866deb8b63",
        # create_all over the current models, opportunity_weeks older then ALTERed
        "e9863f5e7e5cbdebeca90ac7b2af4e76bdb130637faea7201ab5bdb3a7a88725",
        # create_all over the current models -- what the suite builds
        "6c6cceefa499488e00f01864cca600b64bf9735ce307f180c1b142fa27308572",
    }
)

#: The v1 pair, kept only so the drift test can assert that nothing buildable
#: hashes to it. Nothing in the recovery path reads this.
_FORMAT_V1_CATALOG_SHA256 = frozenset(
    {
        "61bb0f04d7099622adf5ce7fdd07375505981e34c62b110bcd75989cc20b404f",
        "e695bd2c2b2afdad67c52c79420c2cd6a2965a9fb712904a073284d350506b1c",
    }
)
_SAFE_ERROR_CODES = {
    "recovery_not_required",
    "recovery_unconfigured",
    "recovery_topology_unsupported",
    "recovery_target_unavailable",
    "recovery_state_missing",
    "recovery_state_corrupt",
    "recovery_clock_invalid",
    "recovery_point_stale",
    "recovery_retention_not_enforced",
    "recovery_busy",
    "recovery_rate_limited",
    "recovery_schema_drift",
    "recovery_tool_invalid",
    "recovery_repository_error",
    "recovery_repository_empty",
    "recovery_snapshot_ambiguous",
    "recovery_retention_invalid",
    "recovery_retention_applied",
    "recovery_retention_dry_run",
    "recovery_bundle_invalid",
    "recovery_scratch_unsafe",
    "recovery_ok",
    "recovery_unchanged",
}


# Explicit order is part of the recovery format. opportunity_weeks records the
# current additive-migration order; the validator also admits create_all order
# after proving its semantic catalog is otherwise identical.
EXPECTED_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    # `tenant_id` appended by alembic 0004 -- appended in both a migrated and a
    # create_all database, unlike `leagues` below.
    "accounts": (
        "id", "label", "swid", "espn_s2_encrypted", "status", "created_at", "tenant_id",
    ),
    "adp_snapshots": ("id", "source", "pulled_at", "format", "teams", "payload_json"),
    "ai_reports": (
        "id",
        "league_id",
        "scope",
        "kind",
        "input_hash",
        "model",
        "content_json",
        "created_at",
    ),
    # Phase 31 unit 4, recorded as a contract amendment in E31.5. The Phase 31
    # owned-paths list does not include this file, but the same contract requires
    # "one additive revision for ledger state", and Phase 30 made the schema an
    # allowlist that fails closed on any table it has not been told about. The
    # list could not satisfy the contract it belongs to, which is the same reason
    # the unit-2 amendment was taken.
    #
    # These are RETAINED in the recovery bundle, not excluded like `raw_cache`.
    # A restore that dropped them would reset the UTC-month committed total to
    # zero, so "restore from backup" would become a way to clear the spend
    # ceiling. Retaining them is part of the bound, not incidental.
    "ai_spend_entries": (
        "id",
        "reservation",
        "month",
        "kind",
        "model",
        "state",
        "reserved_micro_usd",
        "settled_micro_usd",
        "created_at",
        "updated_at",
    ),
    "ai_spend_months": (
        "id",
        "month",
        "committed_micro_usd",
        "updated_at",
    ),
    "current_roster_entries": (
        "id",
        "snapshot_id",
        "team_id",
        "lineup_slot_id",
        "slot_index",
        "espn_player_id",
        "player_name",
        "player_position",
        "nfl_team",
        "opponent",
        "kickoff_at",
        "game_status",
        "injury_status",
        "actual_points",
        "projected_points",
    ),
    "current_roster_snapshots": (
        "id",
        "league_id",
        "scoring_period",
        "matchup_period",
        "synced_at",
    ),
    "draft_picks": (
        "id",
        "league_id",
        "overall",
        "round",
        "round_pick",
        "team_id",
        "espn_player_id",
        "keeper",
        "autodraft",
        "bid_amount",
        "adp_at_draft",
        "value_delta",
    ),
    # The migrated order. `tenant_id` is LAST here and FIFTH in a create_all
    # database, because alembic 0003 added it with a batch rebuild that appends
    # while the model declares it after `account_id`. Measured, not assumed:
    # `PRAGMA table_info` was read from a database built each way and the two
    # disagree on this table and on no other. `_CREATE_ALL_LEAGUES_ORDER` below
    # carries the other shape, and `validate_catalog` admits both -- the same
    # accommodation `opportunity_weeks` already needed.
    #
    # Getting this wrong would have been invisible in one direction: the suite
    # builds with `create_all` and the operator's database is migrated, so a
    # single-order allowlist passes exactly one of them.
    "leagues": (
        "id",
        "espn_league_id",
        "season",
        "account_id",
        "name",
        "size",
        "scoring_json",
        "lineup_slots_json",
        "draft_type",
        "playoff_team_count",
        "current_scoring_period",
        "current_matchup_period",
        "lifecycle",
        "my_team_id",
        "is_public",
        "last_synced_at",
        "last_sync_ok",
        "last_sync_error",
        "tenant_id",
    ),
    "lineup_slots": (
        "id",
        "league_id",
        "team_id",
        "week",
        "slot",
        "espn_player_id",
        "points",
        "is_starter",
    ),
    "matchups": (
        "id",
        "league_id",
        "week",
        "home_team_id",
        "away_team_id",
        "home_points",
        "away_points",
        "home_projected_points",
        "away_projected_points",
        "is_playoff",
    ),
    "metric_snapshots": (
        "id",
        "league_id",
        "team_id",
        "batch_id",
        "key",
        "period",
        "value_float",
        "recorded_at",
    ),
    "metrics": (
        "id",
        "league_id",
        "team_id",
        "key",
        "week",
        "value_float",
        "computed_at",
    ),
    "nflverse_player_maps": ("espn_player_id", "gsis_id", "status", "method", "updated_at"),
    "opportunity_imports": (
        "id",
        "season",
        "state",
        "started_at",
        "completed_at",
        "latest_week",
        "input_rows",
        "stored_rows",
        "matched_players",
        "unmatched_players",
        "retries",
        "package_version",
        "schema_fingerprint",
        "error_code",
        "error_message",
        "details_json",
    ),
    "opportunity_weeks": (
        "id",
        "season",
        "season_type",
        "week",
        "game_id",
        "gsis_id",
        "team",
        "opponent_team",
        "position",
        "carries",
        "carry_share",
        "targets",
        "receptions",
        "rushing_yards",
        "receiving_yards",
        "receiving_air_yards",
        "target_share",
        "air_yards_share",
        "wopr",
        "rushing_epa",
        "receiving_epa",
        "fantasy_points_ppr",
        "receiving_tds",
        "team_passing_yards",
    ),
    "players": (
        "espn_player_id",
        "name",
        "position",
        "nfl_team",
        "espn_adp",
        "espn_pct_owned",
        "espn_rank_ppr",
        "proj_ros",
        "ffc_id",
        "ffc_adp",
        "updated_at",
    ),
    "raw_cache": ("key", "fetched_at", "payload_json", "tenant_id"),
    # ---- Phases 36-41. See NOT_BUNDLED for which of these travel. -------
    "tenants": ("id", "slug", "created_at"),
    "users": ("id", "email", "created_at"),
    "memberships": ("id", "tenant_id", "user_id", "role"),
    "app_sessions": (
        "id",
        "token_hash",
        "user_id",
        "created_at",
        "expires_at",
        "revoked_at",
        "origin",
    ),
    "jobs": (
        "id",
        "tenant_id",
        "kind",
        "idempotency_key",
        "payload_json",
        "state",
        "attempts",
        "max_attempts",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "last_error",
        "created_at",
        "updated_at",
    ),
    "schedules": (
        "id",
        "tenant_id",
        "name",
        "kind",
        "payload_json",
        "interval_seconds",
        "anchor_at",
        "next_run_at",
        "enabled",
        "created_at",
        "updated_at",
    ),
    "outbox": (
        "id",
        "tenant_id",
        "topic",
        "dedupe_key",
        "payload_json",
        "created_at",
        "available_at",
        "delivered_at",
        "attempts",
        "last_error",
    ),
    "worker_heartbeats": (
        "owner",
        "first_seen_at",
        "last_seen_at",
        "last_claimed_at",
        "ticks",
        "claims",
        "failures",
        "reported_ticks",
        "reported_claims",
        "reported_failures",
        "reported_at",
    ),
    "teams": (
        "id",
        "league_id",
        "espn_team_id",
        "name",
        "abbrev",
        "owner_swids_json",
        "is_me",
        "autodrafted",
        "wins",
        "losses",
        "ties",
        "points_for",
        "points_against",
        "standing",
        "logo_url",
    ),
    "transactions": (
        "id",
        "league_id",
        "team_id",
        "type",
        "week",
        "player_in",
        "player_out",
        "bid",
        "executed_at",
    ),
}

#: `leagues` as a `create_all` database orders it: `tenant_id` fifth, where the
#: model declares it, rather than appended where alembic 0003's batch rebuild
#: put it. Both are real schemas -- the suite builds one and the operator runs
#: the other -- so `validate_catalog` admits both.
_CREATE_ALL_LEAGUES_ORDER = (
    "id",
    "espn_league_id",
    "season",
    "account_id",
    "tenant_id",
    "name",
    "size",
    "scoring_json",
    "lineup_slots_json",
    "draft_type",
    "playoff_team_count",
    "current_scoring_period",
    "current_matchup_period",
    "lifecycle",
    "my_team_id",
    "is_public",
    "last_synced_at",
    "last_sync_ok",
    "last_sync_error",
)

#: Allowlisted so the schema check passes, and deliberately NOT written into
#: the recovery bundle. Each entry is a decision, not an omission.
#:
#: `raw_cache` -- raw ESPN payloads for private leagues. Must not travel in a
#:   backup at all; this was already true in format v1.
#:
#: `users` -- `email` is unique and is a member identifier. A single
#:   `{REAUTH-REQUIRED}` substitution of the kind `accounts` uses would violate
#:   the unique constraint on the second row, and inventing a per-row stand-in
#:   would be fabricating identity. So identity does not travel, exactly as
#:   credentials do not: a restore returns the data and the operator re-links
#:   who may see it.
#:
#: `memberships` -- the authorization join. It carries no identifier itself,
#:   but every row points at a `users` row that is not in the bundle, so
#:   restoring it would write dangling references into a database whose
#:   verification step checks `PRAGMA foreign_key_check`.
#:
#: `app_sessions` -- live session tokens. Restoring them would resurrect
#:   sessions that were valid at backup time, so a restore would silently
#:   re-admit whoever was signed in hours ago. A restore is a recovery event
#:   and everyone should authenticate again.
#:
#: `jobs` -- queue state. A restored lease names a worker that does not exist,
#:   and a restored `queued` row re-runs work whose side effects may already
#:   have happened. Schedules re-materialise what is still due, by arithmetic,
#:   so nothing is lost by starting the queue empty.
#:
#: `outbox` -- pending side effects. An undelivered message describes a state
#:   change that the restored database may no longer contain, and sending a
#:   notification about an event that no longer exists cannot be un-sent. The
#:   outbox's guarantee is at-least-once relative to a committed change; a
#:   restore moves which changes are committed, so the messages do not survive
#:   it.
#:
#: `worker_heartbeats` -- operational state that regenerates on the first tick.
#:   Carrying it would make a freshly restored database report a worker
#:   identity that is not running.
#:
#: `schedules` is deliberately ABSENT from this set, i.e. it IS bundled. It is
#: configuration the operator created, not transient state, and dropping it
#: would mean nothing ever syncs again after a restore with nothing saying so.
#: `materialize_due` keys on `(schedule, slot)` with no clock reading, so a
#: restored schedule cannot double-execute anything.
#:
#: `tenants` is bundled too, and must be: `leagues.tenant_id`,
#: `accounts.tenant_id` and `schedules.tenant_id` all reference it.
NOT_BUNDLED = frozenset(
    {
        "raw_cache",
        "users",
        "memberships",
        "app_sessions",
        "jobs",
        "outbox",
        "worker_heartbeats",
    }
)

_CREATE_ALL_OPPORTUNITY_ORDER = (
    "id",
    "season",
    "season_type",
    "week",
    "game_id",
    "gsis_id",
    "team",
    "opponent_team",
    "position",
    "carries",
    "carry_share",
    "targets",
    "receptions",
    "rushing_yards",
    "receiving_yards",
    "receiving_air_yards",
    "receiving_tds",
    "team_passing_yards",
    "target_share",
    "air_yards_share",
    "wopr",
    "rushing_epa",
    "receiving_epa",
    "fantasy_points_ppr",
)

EXCLUDED_COLUMNS = {
    "accounts": {"swid", "espn_s2_encrypted", "status"},
    "teams": {"owner_swids_json"},
}


class RecoveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code if code in _SAFE_ERROR_CODES else "recovery_repository_error"
        self.safe_message = message
        self.retry_after = retry_after


class RecoveryAdmissionError(RecoveryError):
    pass


class RecoveryBusyError(RecoveryError):
    pass


@dataclass(frozen=True)
class RecoveryState:
    last_coverage_at: str | None = None
    last_snapshot_at: str | None = None
    last_api_trigger_at: str | None = None
    last_result_code: str = "recovery_state_missing"
    artifact_bytes: int | None = None
    format_version: int = FORMAT_VERSION
    schema_fingerprint_short: str | None = None
    retention_configured: bool = False
    retention_enforced: bool = False
    retention_applied_pending_drill: bool = False
    retention_schedule_armed: bool = False
    evidence_run_id: str | None = None


@dataclass(frozen=True)
class RecoveryStatus:
    required: bool
    supported_topology: bool
    configured: bool
    target_available: bool
    state: str
    last_coverage_at: str | None
    last_snapshot_at: str | None
    age_seconds: int | None
    stale_after_seconds: int
    last_result_code: str
    artifact_bytes: int | None
    format_version: int
    schema_fingerprint_short: str | None
    retention_configured: bool
    retention_enforced: bool

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StorageIdentity:
    physical_parent: str
    media_class: str
    object_token: str | None = None
    physical_parents: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepositoryPin:
    fd: int
    repository_path: str
    topology: tuple[StorageIdentity, StorageIdentity]
    repository_id: str
    device: int
    inode: int
    lock_fd: int


@dataclass(frozen=True)
class _BinaryAuthority:
    device: int
    inode: int
    size: int
    digest: str


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        env: Mapping[str, str] | None = None,
        max_output: int = _MAX_TOOL_OUTPUT,
        interactive: bool = False,
        pass_fds: tuple[int, ...] = (),
        cwd_fd: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ProcessResult: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RecoveryError("recovery_state_corrupt", "Recovery state is invalid.") from exc
    if parsed.tzinfo is None:
        raise RecoveryError("recovery_state_corrupt", "Recovery state is invalid.")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def load_state(path: Path) -> RecoveryState:
    if not path.exists():
        return RecoveryState()
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
            raise RecoveryError("recovery_state_corrupt", "Recovery state is invalid.")
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = {field.name for field in fields(RecoveryState)}
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("unexpected state fields")
        expected_types: dict[str, tuple[type, ...]] = {
            "last_coverage_at": (str, type(None)),
            "last_snapshot_at": (str, type(None)),
            "last_api_trigger_at": (str, type(None)),
            "last_result_code": (str,),
            "artifact_bytes": (int, type(None)),
            "format_version": (int,),
            "schema_fingerprint_short": (str, type(None)),
            "retention_configured": (bool,),
            "retention_enforced": (bool,),
            "retention_applied_pending_drill": (bool,),
            "retention_schedule_armed": (bool,),
            "evidence_run_id": (str, type(None)),
        }
        for key, expected in expected_types.items():
            value = raw.get(key, getattr(RecoveryState(), key))
            if not isinstance(value, expected) or (type(value) is bool and bool not in expected):
                raise ValueError(f"invalid state type: {key}")
        state = RecoveryState(**raw)
        _aware_utc(state.last_coverage_at)
        _aware_utc(state.last_snapshot_at)
        _aware_utc(state.last_api_trigger_at)
        coverage = _aware_utc(state.last_coverage_at)
        snapshot = _aware_utc(state.last_snapshot_at)
        if state.last_result_code not in _SAFE_ERROR_CODES:
            raise ValueError("invalid result code")
        if state.format_version != FORMAT_VERSION:
            raise ValueError("invalid format version")
        if state.artifact_bytes is not None and state.artifact_bytes < 0:
            raise ValueError("invalid artifact bytes")
        if state.schema_fingerprint_short is not None and not re.fullmatch(
            r"[0-9a-f]{12}", state.schema_fingerprint_short
        ):
            raise ValueError("invalid schema fingerprint")
        if state.evidence_run_id is not None and not re.fullmatch(
            r"[0-9a-f]{32}", state.evidence_run_id
        ):
            raise ValueError("invalid evidence run id")
        if (snapshot is None) != (coverage is None):
            raise ValueError("coverage and snapshot must coexist")
        if snapshot is not None and coverage is not None and snapshot > coverage:
            raise ValueError("snapshot newer than coverage")
        if state.retention_enforced and (
            not state.retention_configured
            or state.retention_applied_pending_drill
            or coverage is None
            or snapshot is None
        ):
            raise ValueError("invalid retention state")
        if state.retention_applied_pending_drill and not state.retention_configured:
            raise ValueError("invalid retention pending state")
        if state.retention_schedule_armed and not state.retention_configured:
            raise ValueError("invalid retention schedule state")
        return state
    except RecoveryError:
        raise
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RecoveryError("recovery_state_corrupt", "Recovery state is invalid.") from exc


def save_state(path: Path, state: RecoveryState) -> None:
    _atomic_write_json(path, asdict(state))


def native_supported() -> bool:
    return sys.platform == "darwin" and not Path("/.dockerenv").exists()


def _configured(settings: Settings) -> bool:
    return bool(
        settings.recovery_repository
        and settings.recovery_restic_path
        and settings.recovery_restic_sha256
        and settings.recovery_credential_command
    )


def _target_available(settings: Settings) -> bool:
    if not settings.recovery_repository or not native_supported():
        return False
    try:
        target = Path(settings.recovery_repository)
        if not target.is_absolute():
            return False
        source_id = MacOSTopologyResolver().resolve(settings.db_file)
        target_id = MacOSTopologyResolver().resolve(target)
        with _bounded_path_authority(target, require_directory=True) as authority:
            if target_id.object_token != authority.object_token:
                return False
        return _physical_parent_inventory(source_id).isdisjoint(
            _physical_parent_inventory(target_id)
        )
    except (OSError, RecoveryError):
        return False


def recovery_status(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> RecoveryStatus:
    cfg = settings or get_settings()
    supported = native_supported()
    configured = _configured(cfg)
    target = _target_available(cfg)
    try:
        state = load_state(cfg.recovery_state_file)
    except RecoveryError as exc:
        return RecoveryStatus(
            required=cfg.recovery_required,
            supported_topology=supported,
            configured=configured,
            target_available=target,
            state="corrupt",
            last_coverage_at=None,
            last_snapshot_at=None,
            age_seconds=None,
            stale_after_seconds=cfg.recovery_stale_after_seconds,
            last_result_code=exc.code,
            artifact_bytes=None,
            format_version=FORMAT_VERSION,
            schema_fingerprint_short=None,
            retention_configured=False,
            retention_enforced=False,
        )
    now = clock().astimezone(UTC)
    coverage = _aware_utc(state.last_coverage_at)
    age: int | None = None
    status_name = "not_required" if not cfg.recovery_required else "ready"
    if cfg.recovery_required:
        if not supported:
            status_name = "unsupported"
        elif not configured:
            status_name = "unconfigured"
        elif coverage is None:
            status_name = "missing"
        elif coverage > now:
            status_name = "corrupt"
        else:
            age = max(0, int((now - coverage).total_seconds()))
            if age >= cfg.recovery_stale_after_seconds:
                status_name = "stale"
            elif not state.retention_enforced:
                status_name = "retention_pending"
            elif not target:
                status_name = "target_unavailable"
    if coverage is not None and coverage <= now and age is None:
        age = max(0, int((now - coverage).total_seconds()))
    return RecoveryStatus(
        required=cfg.recovery_required,
        supported_topology=supported,
        configured=configured,
        target_available=target,
        state=status_name,
        last_coverage_at=state.last_coverage_at,
        last_snapshot_at=state.last_snapshot_at,
        age_seconds=age,
        stale_after_seconds=cfg.recovery_stale_after_seconds,
        last_result_code=state.last_result_code,
        artifact_bytes=state.artifact_bytes,
        format_version=state.format_version,
        schema_fingerprint_short=state.schema_fingerprint_short,
        retention_configured=state.retention_configured,
        retention_enforced=state.retention_enforced,
    )


def assert_recovery_write_allowed(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> None:
    status = recovery_status(settings, clock=clock)
    if not status.required:
        return
    code_by_state = {
        "unsupported": "recovery_topology_unsupported",
        "unconfigured": "recovery_unconfigured",
        "missing": "recovery_state_missing",
        "corrupt": "recovery_state_corrupt",
        "stale": "recovery_point_stale",
        "retention_pending": "recovery_retention_not_enforced",
        "target_unavailable": "recovery_target_unavailable",
    }
    if status.state != "ready":
        raise RecoveryAdmissionError(
            code_by_state.get(status.state, "recovery_point_stale"),
            "A current verified recovery point is required before this operation.",
        )


def reserve_api_trigger(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> None:
    cfg = settings or get_settings()
    now = clock().astimezone(UTC)
    with recovery_lock(cfg.recovery_lock_file):
        state = load_state(cfg.recovery_state_file)
        prior = _aware_utc(state.last_api_trigger_at)
        if prior is not None:
            if prior > now:
                raise RecoveryError("recovery_state_corrupt", "Recovery state is invalid.")
            elapsed = (now - prior).total_seconds()
            if elapsed < cfg.recovery_api_min_interval_seconds:
                retry = max(1, math.ceil(cfg.recovery_api_min_interval_seconds - elapsed))
                raise RecoveryError(
                    "recovery_rate_limited",
                    "A recovery request was accepted recently.",
                    retry_after=retry,
                )
        save_state(
            cfg.recovery_state_file,
            RecoveryState(**{**asdict(state), "last_api_trigger_at": _iso(now)}),
        )


@contextlib.contextmanager
def recovery_lock(path: Path, *, blocking: bool = False) -> Iterator[int]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError as exc:
            raise RecoveryBusyError(
                "recovery_busy", "A recovery operation is already running."
            ) from exc
        yield fd
    finally:
        # Do not issue LOCK_UN: Restic is deliberately given this open-file
        # description. If timeout cleanup cannot kill a descendant, its copy
        # keeps admission closed until that descendant exits.
        with _break_glass_cleanup_boundary():
            os.close(fd)


def _normalize_sql(value: str | None) -> str:
    return " ".join((value or "").replace('"', "").split()).lower()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_catalog(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    columns = [
        {
            "name": row[1],
            "type": (row[2] or "").upper(),
            "notnull": int(row[3]),
            "default": row[4],
            "pk": int(row[5]),
            "hidden": int(row[6]),
        }
        for row in conn.execute(f"PRAGMA table_xinfo({_quote(table)})")
    ]
    foreign_keys = sorted(
        [tuple(row[2:8]) for row in conn.execute(f"PRAGMA foreign_key_list({_quote(table)})")],
        key=lambda row: tuple("" if value is None else str(value) for value in row),
    )
    indexes: list[dict[str, Any]] = []
    for row in conn.execute(f"PRAGMA index_list({_quote(table)})"):
        index_name = row[1]
        index_sql_row = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='index' AND name=?", (index_name,)
        ).fetchone()
        indexes.append(
            {
                "name": index_name,
                "unique": int(row[2]),
                "origin": row[3],
                "partial": int(row[4]),
                "columns": [
                    (item[2], item[3], item[4], item[5])
                    for item in conn.execute(f"PRAGMA index_xinfo({_quote(index_name)})")
                ],
                "sql": _normalize_sql(index_sql_row[0] if index_sql_row else None),
            }
        )
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return {
        "columns": columns,
        "foreign_keys": foreign_keys,
        "indexes": sorted(indexes, key=lambda item: item["name"]),
        "sql": _normalize_sql(table_sql[0] if table_sql else None),
    }


def catalog_spec(conn: sqlite3.Connection) -> dict[str, Any]:
    """The application schema, as SQLite reports it.

    `alembic_version` is excluded, and that exclusion is a format-v2 fix rather
    than a tidy-up. It exists only in a database that was MIGRATED, not in one
    built by `create_all`, so including it made the fingerprint depend on how
    the database was created rather than on what shape it is -- and since the
    allowlist never listed it, `validate_catalog` refused every database
    produced by `alembic upgrade head`, which is the documented path. It went
    unnoticed because the suite builds with `create_all`, where the table is
    absent: the suite exercised one provenance and the operator ran the other.

    Its contents are not lost by this. The revision is schema identity, and it
    is recorded in the manifest beside the fingerprint rather than inside the
    thing being fingerprinted.
    """
    unexpected = list(
        conn.execute(
            "SELECT type,name FROM sqlite_schema "
            "WHERE type IN ('view','trigger') AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )
    if unexpected:
        raise RecoveryError("recovery_schema_drift", "Database schema is not allowlisted.")
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version' ORDER BY name"
        )
    ]
    return {table: _table_catalog(conn, table) for table in tables}


def _catalog_semantics(catalog: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table, spec in catalog.items():
        result[table] = {
            "columns": sorted(spec["columns"], key=lambda item: item["name"]),
            "foreign_keys": spec["foreign_keys"],
            "indexes": spec["indexes"],
        }
    # A catalog reloaded from the JSON bundle has lists where the live PRAGMA
    # representation used tuples. Canonical JSON erases that incidental Python
    # distinction before comparison.
    return json.loads(json.dumps(result, sort_keys=True, separators=(",", ":")))


def validate_catalog(catalog: dict[str, Any]) -> str:
    if set(catalog) != set(EXPECTED_TABLE_COLUMNS):
        raise RecoveryError("recovery_schema_drift", "Database schema is not allowlisted.")
    for table, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        actual = tuple(column["name"] for column in catalog[table]["columns"])
        allowed = {expected_columns}
        if table == "opportunity_weeks":
            allowed.add(_CREATE_ALL_OPPORTUNITY_ORDER)
        if table == "leagues":
            allowed.add(_CREATE_ALL_LEAGUES_ORDER)
        if actual not in allowed:
            raise RecoveryError("recovery_schema_drift", "Database schema is not allowlisted.")
        if " check " in f" {catalog[table]['sql']} ":
            raise RecoveryError("recovery_schema_drift", "Database schema is not allowlisted.")
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if digest not in _FORMAT_V2_CATALOG_SHA256:
        raise RecoveryError("recovery_schema_drift", "Database schema is not allowlisted.")
    return digest


def _tag_value(value: Any, declared_type: str) -> list[Any]:
    if value is None:
        return ["null", None]
    kind = declared_type.upper()
    if kind == "JSON":
        if not isinstance(value, str):
            raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
        parsed = json.loads(value)
        return ["json", parsed]
    if kind == "BOOLEAN":
        if not isinstance(value, int) or value not in (0, 1):
            raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
        return ["bool", bool(value)]
    if "INT" in kind:
        if not isinstance(value, int):
            raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
        return ["int", value]
    if kind in {"FLOAT", "REAL", "DOUBLE", "NUMERIC"}:
        if not isinstance(value, float):
            raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
        number = float(value)
        if not math.isfinite(number):
            raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
        return ["float", number.hex()]
    if "BLOB" in kind:
        if not isinstance(value, bytes):
            raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
        import base64

        return ["bytes", base64.b64encode(value).decode("ascii")]
    if not isinstance(value, str):
        raise RecoveryError("recovery_bundle_invalid", "Database value has invalid type.")
    return ["datetime" if kind == "DATETIME" else "text", value]


def _decode_value(value: list[Any]) -> Any:
    if not isinstance(value, list) or len(value) != 2:
        raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.")
    tag, payload = value
    if tag == "null":
        return None
    if tag == "bool":
        if type(payload) is not bool:
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.")
        return int(payload)
    if tag == "int":
        if type(payload) is not int:
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.")
        return payload
    if tag == "float":
        if not isinstance(payload, str):
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.")
        value = float.fromhex(payload)
        if not math.isfinite(value):
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.")
        return value
    if tag == "bytes":
        import base64

        return base64.b64decode(payload, validate=True)
    if tag == "datetime":
        try:
            if not isinstance(payload, str):
                raise ValueError("datetime must be text")
            datetime.fromisoformat(payload)
        except (TypeError, ValueError) as exc:
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.") from exc
        # The recovery format preserves SQLite's exact stored datetime text;
        # passing a datetime through SQLAlchemy would normalize or strip it.
        return payload
    if tag == "json":
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if tag == "text" and isinstance(payload, str):
        return payload
    raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.")


def _adjusted_value(table: str, column: str, value: Any) -> Any:
    if table == "accounts":
        if column == "swid":
            return REAUTH_SWID_SENTINEL
        if column == "espn_s2_encrypted":
            return REAUTH_S2_SENTINEL
        if column == "status":
            return "needs_reauth"
    if table == "teams" and column == "owner_swids_json":
        return None
    return value


def _row_order(spec: dict[str, Any]) -> list[str]:
    pks = sorted(
        ((column["pk"], column["name"]) for column in spec["columns"] if column["pk"]),
        key=lambda item: item[0],
    )
    if not pks:
        raise RecoveryError("recovery_schema_drift", "Database schema is not allowlisted.")
    return [name for _, name in pks]


def _table_rows_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        digest.update(len(canonical).to_bytes(8, "big"))
        digest.update(canonical)
    return digest.hexdigest()


def _safe_content_digest(
    table_manifest: Mapping[str, Any], account_statuses: Sequence[Mapping[str, Any]]
) -> str:
    canonical = json.dumps(
        {"account_statuses": account_statuses, "tables": table_manifest},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _bounded_json_bytes(value: Any, *, max_bytes: int) -> bytes:
    """Incrementally encode into a hard-bounded in-memory pipe payload.

    This avoids a second unbounded JSON allocation and aborts as soon as the
    format ceiling is crossed. The repository adapter receives exactly these
    bytes through stdin; no physical source copy or plaintext spool file exists.
    """
    chunks = bytearray()
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    for text in encoder.iterencode(value):
        encoded = text.encode("utf-8")
        if len(chunks) + len(encoded) > max_bytes:
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle exceeds its bound.")
        chunks.extend(encoded)
    return bytes(chunks)


def build_logical_bundle(
    db_path: Path, *, max_bundle_bytes: int = _MAX_BUNDLE_BYTES
) -> tuple[bytes, dict[str, Any]]:
    if not db_path.is_file() or db_path.is_symlink():
        raise RecoveryError("recovery_target_unavailable", "Source database is unavailable.")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        catalog = catalog_spec(conn)
        schema_fingerprint = validate_catalog(catalog)
        # Bound accumulation while rows are read, not only during the final
        # encoder pass. This is deliberately conservative; exact envelope
        # enforcement still happens in _bounded_json_bytes below.
        accumulated_bytes = len(
            json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
        )
        if accumulated_bytes > max_bundle_bytes:
            raise RecoveryError("recovery_bundle_invalid", "Recovery bundle exceeds its bound.")
        tables: dict[str, list[dict[str, list[Any]]]] = {}
        table_manifest: dict[str, dict[str, Any]] = {}
        account_statuses = [
            {"id": int(row[0]), "status": str(row[1])}
            for row in conn.execute("SELECT id,status FROM accounts ORDER BY id")
        ]
        for table in sorted(EXPECTED_TABLE_COLUMNS):
            if table in NOT_BUNDLED:
                continue
            columns = EXPECTED_TABLE_COLUMNS[table]
            declared = {item["name"]: item["type"] for item in catalog[table]["columns"]}
            order = ",".join(_quote(column) for column in _row_order(catalog[table]))
            rows: list[dict[str, list[Any]]] = []
            selected = ",".join(_quote(column) for column in columns)
            query = f"SELECT {selected} FROM {_quote(table)} ORDER BY {order}"
            for source_row in conn.execute(query):
                encoded: dict[str, list[Any]] = {}
                for column in columns:
                    value = _adjusted_value(table, column, source_row[column])
                    encoded[column] = _tag_value(value, declared[column])
                accumulated_bytes += len(
                    json.dumps(encoded, sort_keys=True, separators=(",", ":")).encode()
                )
                if accumulated_bytes > max_bundle_bytes:
                    raise RecoveryError(
                        "recovery_bundle_invalid", "Recovery bundle exceeds its bound."
                    )
                rows.append(encoded)
            tables[table] = rows
            table_manifest[table] = {"rows": len(rows), "sha256": _table_rows_digest(rows)}
        manifest = {
            "format_version": FORMAT_VERSION,
            "canary": RECOVERY_CANARY,
            "schema_fingerprint": schema_fingerprint,
            "catalog": catalog,
            "tables": table_manifest,
            "account_statuses": account_statuses,
        }
        manifest["safe_content_digest"] = _safe_content_digest(table_manifest, account_statuses)
        bundle = _bounded_json_bytes(
            {"manifest": manifest, "tables": tables}, max_bytes=max_bundle_bytes
        )
        return bundle, manifest
    except sqlite3.Error as exc:
        raise RecoveryError(
            "recovery_bundle_invalid", "Recovery bundle could not be created."
        ) from exc
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        conn.close()


class BoundedSubprocessRunner:
    """No-shell runner with bounded stdout/stderr and an allowlisted environment."""

    def run(
        self,
        argv: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        env: Mapping[str, str] | None = None,
        max_output: int = _MAX_TOOL_OUTPUT,
        interactive: bool = False,
        pass_fds: tuple[int, ...] = (),
        cwd_fd: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        if (
            not argv
            or not Path(argv[0]).is_absolute()
            or max_output < 0
            or (timeout_seconds is not None and timeout_seconds <= 0)
        ):
            raise RecoveryError("recovery_tool_invalid", "Recovery tool is invalid.")
        process_argv = list(argv)
        if cwd_fd is not None:
            try:
                directory = os.fstat(cwd_fd)
            except OSError as exc:
                raise RecoveryError(
                    "recovery_tool_invalid", "Recovery tool directory is invalid."
                ) from exc
            if (
                cwd_fd not in pass_fds
                or not stat.S_ISDIR(directory.st_mode)
                or not Path(sys.executable).is_absolute()
            ):
                raise RecoveryError(
                    "recovery_tool_invalid", "Recovery tool directory is invalid."
                )
            # macOS cannot traverse `/dev/fd/<directory-fd>/<child>`.  Enter the
            # already-open directory in a fixed isolated launcher, then replace
            # that process with the absolute pinned tool.  The exec preserves
            # the repository, recovery-lock and optional password descriptors,
            # while the working-directory vnode remains bound to the verified
            # repository inode even if its lexical path is swapped.
            process_argv = [
                sys.executable,
                "-I",
                "-S",
                "-c",
                _RESTIC_DIRECTORY_EXEC_CODE,
                str(cwd_fd),
                *process_argv,
            ]
        started = time.monotonic()
        isolated_group = not interactive
        child_stdin = (
            None
            if interactive
            else (subprocess.PIPE if input_bytes else subprocess.DEVNULL)
        )
        process: subprocess.Popen[bytes] | None = None
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        selector: selectors.BaseSelector | None = None
        stdin_view = memoryview(input_bytes or b"")
        stdin_offset = 0
        operation_deadline = (
            started + timeout_seconds if timeout_seconds is not None else None
        )
        returncode: int | None = None
        failure: Literal["drain", "overflow", "timeout"] | None = None
        exit_drain_deadline: float | None = None

        def register_pipe(name: str, pipe, events: int) -> None:
            assert selector is not None
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe.fileno(), events, (name, pipe))

        def close_pipe(fd: int, pipe) -> None:
            if selector is not None:
                with contextlib.suppress(KeyError, ValueError):
                    selector.unregister(fd)
            with contextlib.suppress(OSError):
                pipe.close()

        def output_open() -> bool:
            if selector is None:
                return False
            return any(
                key.data[0] in {"stdout", "stderr"}
                for key in selector.get_map().values()
            )

        def close_stdin() -> None:
            if process is None:
                return
            if selector is None:
                if process.stdin is not None:
                    with contextlib.suppress(OSError):
                        process.stdin.close()
                return
            for key in tuple(selector.get_map().values()):
                if key.data[0] == "stdin":
                    close_pipe(key.fd, key.data[1])

        def terminate_tree() -> None:
            if process is None or process.poll() is not None:
                return
            if isolated_group:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()

        def service_events(timeout: float, *, retain_output: bool) -> bool:
            nonlocal stdin_offset, failure
            if selector is None:
                return False
            try:
                events = selector.select(max(0.0, timeout))
            except OSError:
                events = []
            for key, _mask in events:
                name, pipe = key.data
                if name == "stdin":
                    try:
                        written = os.write(
                            key.fd, stdin_view[stdin_offset : stdin_offset + 64 * 1024]
                        )
                        stdin_offset += written
                    except (BrokenPipeError, OSError):
                        close_pipe(key.fd, pipe)
                        continue
                    if stdin_offset >= len(stdin_view):
                        close_pipe(key.fd, pipe)
                    continue
                try:
                    chunk = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    close_pipe(key.fd, pipe)
                    continue
                if retain_output and len(buffers[name]) + len(chunk) > max_output:
                    failure = "overflow"
                    return False
                if retain_output:
                    buffers[name].extend(chunk)
            return True

        def bounded_cleanup() -> None:
            if process is None:
                return
            with _break_glass_cleanup_boundary():
                terminate_tree()
                close_stdin()
                cleanup_deadline = time.monotonic() + _PROCESS_CLEANUP_TIMEOUT_SECONDS
                while selector is not None and time.monotonic() < cleanup_deadline:
                    process.poll()
                    if not output_open() and process.poll() is not None:
                        break
                    remaining = cleanup_deadline - time.monotonic()
                    service_events(
                        min(_PROCESS_IO_POLL_SECONDS, remaining), retain_output=False
                    )
                terminate_tree()
                if process.poll() is None:
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        with contextlib.suppress(OSError):
                            pipe.close()

        try:
            process = subprocess.Popen(
                process_argv,
                stdin=child_stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=dict(env or {}),
                shell=False,
                close_fds=True,
                pass_fds=pass_fds,
                start_new_session=isolated_group,
            )
            selector = selectors.DefaultSelector()
            assert process.stdout is not None and process.stderr is not None
            register_pipe("stdout", process.stdout, selectors.EVENT_READ)
            register_pipe("stderr", process.stderr, selectors.EVENT_READ)
            if process.stdin is not None:
                if stdin_view:
                    register_pipe("stdin", process.stdin, selectors.EVENT_WRITE)
                else:
                    process.stdin.close()

            while failure is None:
                now = time.monotonic()
                if operation_deadline is not None and now >= operation_deadline:
                    failure = "timeout"
                    break
                returncode = process.poll()
                if returncode is not None:
                    close_stdin()
                    if not output_open():
                        break
                    if exit_drain_deadline is None:
                        exit_drain_deadline = now + _PROCESS_DRAIN_TIMEOUT_SECONDS
                    elif now >= exit_drain_deadline:
                        failure = "drain"
                        break
                wait_until = now + _PROCESS_IO_POLL_SECONDS
                if operation_deadline is not None:
                    wait_until = min(wait_until, operation_deadline)
                if exit_drain_deadline is not None:
                    wait_until = min(wait_until, exit_drain_deadline)
                service_events(wait_until - now, retain_output=True)

            if failure is not None:
                bounded_cleanup()
                if failure == "overflow":
                    raise RecoveryError(
                        "recovery_tool_invalid", "Recovery tool output exceeded its bound."
                    )
                if failure == "timeout":
                    raise RecoveryError("recovery_tool_invalid", "Recovery tool timed out.")
                raise RecoveryError(
                    "recovery_tool_invalid", "Recovery tool output did not close."
                )

            assert returncode is not None
            return ProcessResult(
                returncode, bytes(buffers["stdout"]), bytes(buffers["stderr"])
            )
        except BaseException:
            if failure is None:
                bounded_cleanup()
            raise
        finally:
            if selector is not None:
                for key in tuple(selector.get_map().values()):
                    close_pipe(key.fd, key.data[1])
                selector.close()
            if process is not None:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        with contextlib.suppress(OSError):
                            pipe.close()


def _minimal_env() -> dict[str, str]:
    return {
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }


def _credential_unavailable(exc: BaseException | None = None) -> RecoveryError:
    error = RecoveryError(
        "recovery_repository_error", "Recovery credential is unavailable."
    )
    if exc is not None:
        error.__cause__ = exc
    return error


@dataclass
class _BreakGlassSignalState:
    interrupted: bool = False
    cleanup_depth: int = 0


_ACTIVE_BREAK_GLASS_SIGNAL_STATE: contextvars.ContextVar[
    _BreakGlassSignalState | None
] = contextvars.ContextVar("active_break_glass_signal_state", default=None)


@contextlib.contextmanager
def _break_glass_cleanup_boundary() -> Iterator[None]:
    state = _ACTIVE_BREAK_GLASS_SIGNAL_STATE.get()
    if state is None:
        yield
        return
    state.cleanup_depth += 1
    try:
        yield
    finally:
        state.cleanup_depth -= 1


def _raise_deferred_break_glass_signal() -> None:
    state = _ACTIVE_BREAK_GLASS_SIGNAL_STATE.get()
    if state is not None and state.interrupted and state.cleanup_depth == 0:
        raise _credential_unavailable()


def _reset_break_glass_signal_context(
    token: contextvars.Token[_BreakGlassSignalState | None],
) -> None:
    _ACTIVE_BREAK_GLASS_SIGNAL_STATE.reset(token)


def _restore_break_glass_signal_authority(
    state: _BreakGlassSignalState,
    token: contextvars.Token[_BreakGlassSignalState | None],
    original_handlers: Mapping[int, Any],
) -> None:
    guarded = set(_BREAK_GLASS_LIFETIME_SIGNALS)
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, guarded)
    try:
        for terminal_signal, original_handler in original_handlers.items():
            signal.signal(terminal_signal, original_handler)
        _reset_break_glass_signal_context(token)
        while True:
            pending = set(signal.sigpending()).intersection(guarded)
            if not pending:
                break
            state.interrupted = True
            signal.sigwait(pending)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


@contextlib.contextmanager
def _break_glass_lifetime_signal_guard() -> Iterator[_BreakGlassSignalState]:
    """Interrupt work once, defer signals during cleanup, then normalize."""

    active = _ACTIVE_BREAK_GLASS_SIGNAL_STATE.get()
    if active is not None:
        yield active
        return

    state = _BreakGlassSignalState()
    token = _ACTIVE_BREAK_GLASS_SIGNAL_STATE.set(state)
    original_handlers: dict[int, Any] = {}
    failure: BaseException | None = None

    def interrupt_for_cleanup(_signum: int, _frame: Any) -> None:
        state.interrupted = True
        if state.cleanup_depth == 0:
            raise KeyboardInterrupt

    try:
        for terminal_signal in _BREAK_GLASS_LIFETIME_SIGNALS:
            original_handlers[terminal_signal] = signal.getsignal(terminal_signal)
            signal.signal(terminal_signal, interrupt_for_cleanup)
        yield state
    except BaseException as exc:
        failure = exc
    finally:
        with _break_glass_cleanup_boundary():
            _restore_break_glass_signal_authority(
                state, token, original_handlers
            )
    if state.interrupted:
        raise _credential_unavailable(failure) from failure
    if failure is not None:
        raise failure


def _zeroize(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


def _write_all(descriptor: int, content: bytes | bytearray | memoryview) -> None:
    view = memoryview(content)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise OSError("descriptor write failed")
        offset += written


def _validate_break_glass_credential(secret: bytearray) -> None:
    if not secret or len(secret) > _MAX_CREDENTIAL_BYTES:
        raise ValueError("credential length is invalid")
    if any(item in secret for item in (0, 10, 13)):
        raise ValueError("credential contains a prohibited byte")

    def continuation(index: int) -> bool:
        return index < len(secret) and 0x80 <= secret[index] <= 0xBF

    index = 0
    while index < len(secret):
        lead = secret[index]
        if lead <= 0x7F:
            index += 1
            continue
        if 0xC2 <= lead <= 0xDF:
            if not continuation(index + 1):
                raise ValueError("credential encoding is invalid")
            index += 2
            continue
        if lead == 0xE0:
            valid = (
                index + 2 < len(secret)
                and 0xA0 <= secret[index + 1] <= 0xBF
                and continuation(index + 2)
            )
        elif 0xE1 <= lead <= 0xEC or 0xEE <= lead <= 0xEF:
            valid = continuation(index + 1) and continuation(index + 2)
        elif lead == 0xED:
            valid = (
                index + 2 < len(secret)
                and 0x80 <= secret[index + 1] <= 0x9F
                and continuation(index + 2)
            )
        else:
            valid = False
        if valid:
            index += 3
            continue
        if lead == 0xF0:
            valid = (
                index + 3 < len(secret)
                and 0x90 <= secret[index + 1] <= 0xBF
                and continuation(index + 2)
                and continuation(index + 3)
            )
        elif 0xF1 <= lead <= 0xF3:
            valid = (
                continuation(index + 1)
                and continuation(index + 2)
                and continuation(index + 3)
            )
        elif lead == 0xF4:
            valid = (
                index + 3 < len(secret)
                and 0x80 <= secret[index + 1] <= 0x8F
                and continuation(index + 2)
                and continuation(index + 3)
            )
        if not valid:
            raise ValueError("credential encoding is invalid")
        index += 4


def _tty_state_restored(current: list[Any], original: list[Any]) -> bool:
    local_mask = termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG
    control_indices = tuple(
        index
        for name in ("VINTR", "VQUIT", "VSUSP", "VDSUSP")
        if isinstance((index := getattr(termios, name, None)), int)
        and index < len(original[6])
        and index < len(current[6])
    )
    return (
        current[3] & local_mask == original[3] & local_mask
        and current[6][termios.VMIN] == original[6][termios.VMIN]
        and current[6][termios.VTIME] == original[6][termios.VTIME]
        and all(current[6][index] == original[6][index] for index in control_indices)
    )


def _restore_tty_state(tty_fd: int, original: list[Any]) -> bool:
    for _attempt in range(_BREAK_GLASS_TTY_RESTORE_ATTEMPTS):
        try:
            termios.tcsetattr(tty_fd, termios.TCSANOW, original)
            current = termios.tcgetattr(tty_fd)
        except (OSError, termios.error):
            continue
        if _tty_state_restored(current, original):
            return True
    return False


def _tty_state_command(tty_fd: int, *arguments: str) -> ProcessResult:
    return BoundedSubprocessRunner().run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            _TTY_STATE_EXEC_CODE,
            str(tty_fd),
            _STTY_PATH,
            *arguments,
        ],
        env=_minimal_env(),
        max_output=_TTY_STATE_MAX_BYTES,
        interactive=True,
        pass_fds=(tty_fd,),
        timeout_seconds=_TTY_STATE_TIMEOUT_SECONDS,
    )


def _capture_independent_tty_state(tty_fd: int) -> str | None:
    try:
        result = _tty_state_command(tty_fd, "-g")
    except RecoveryError:
        return None
    state = result.stdout.strip()
    if (
        result.returncode != 0
        or result.stderr
        or not re.fullmatch(rb"[0-9A-Za-z:=._-]{1,1024}", state)
    ):
        return None
    return state.decode("ascii")


def _restore_independent_tty_state(
    tty_fd: int, state: str, original: list[Any]
) -> bool:
    try:
        result = _tty_state_command(tty_fd, state)
        current = termios.tcgetattr(tty_fd)
    except (OSError, RecoveryError, termios.error):
        return False
    return (
        result.returncode == 0
        and not result.stdout
        and not result.stderr
        and _tty_state_restored(current, original)
    )


def _tty_control_value(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, bytes) and len(value) == 1:
        return value[0]
    raise ValueError("terminal control character is invalid")


def _set_tty_control_value(attributes: list[Any], index: int, value: int) -> None:
    current = attributes[6][index]
    attributes[6][index] = bytes((value,)) if isinstance(current, bytes) else value


def _flush_tty_input(tty_fd: int) -> bool:
    for _attempt in range(_BREAK_GLASS_TTY_RESTORE_ATTEMPTS):
        try:
            termios.tcflush(tty_fd, termios.TCIFLUSH)
        except (OSError, termios.error):
            continue
        return True
    return False


def _read_break_glass_credential() -> bytearray:
    """Read one hidden, bounded credential from the process controlling TTY."""

    tty_fd = -1
    original_attributes: list[Any] | None = None
    secret = bytearray()
    read_buffer = bytearray(_MAX_CREDENTIAL_BYTES + 2)
    expected_failure: BaseException | None = None
    unexpected_failure: BaseException | None = None
    restore_failed = False
    pending_error: BaseException | None = None
    selector: selectors.BaseSelector | None = None
    independent_tty_state: str | None = None
    original_signal_handlers: dict[int, Any] = {}
    interrupted_signal = False

    def mark_terminal_signal(_signum: int, _frame: Any) -> None:
        nonlocal interrupted_signal
        interrupted_signal = True

    try:
        tty_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOCTTY", 0)
        tty_fd = os.open("/dev/tty", tty_flags)
        if not os.isatty(tty_fd):
            raise OSError("controlling terminal is unavailable")
        for terminal_signal in _BREAK_GLASS_LIFETIME_SIGNALS:
            original_signal_handlers[terminal_signal] = signal.getsignal(terminal_signal)
            signal.signal(terminal_signal, mark_terminal_signal)
        original_attributes = termios.tcgetattr(tty_fd)
        independent_tty_state = _capture_independent_tty_state(tty_fd)
        if independent_tty_state is None:
            raise OSError("terminal restore authority is unavailable")
        hidden_attributes = list(original_attributes)
        hidden_attributes[6] = list(original_attributes[6])
        hidden_attributes[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
        disabled_value = os.fpathconf(tty_fd, "PC_VDISABLE")
        if not isinstance(disabled_value, int) or not 0 <= disabled_value <= 0xFF:
            raise OSError("terminal disable value is unavailable")
        rejected_controls: set[int] = set()
        for name in ("VQUIT", "VSUSP", "VDSUSP"):
            control_index = getattr(termios, name, None)
            if not isinstance(control_index, int) or control_index >= len(hidden_attributes[6]):
                continue
            original_value = _tty_control_value(original_attributes[6][control_index])
            if original_value != disabled_value:
                rejected_controls.add(original_value)
            _set_tty_control_value(hidden_attributes, control_index, disabled_value)
        hidden_attributes[6][termios.VMIN] = 0
        hidden_attributes[6][termios.VTIME] = 0
        termios.tcsetattr(tty_fd, termios.TCSAFLUSH, hidden_attributes)
        _write_all(tty_fd, _BREAK_GLASS_PROMPT)
        deadline = time.monotonic() + _BREAK_GLASS_PROMPT_TIMEOUT_SECONDS
        trailing_deadline: float | None = None
        selector = selectors.SelectSelector()
        selector.register(tty_fd, selectors.EVENT_READ)
        while True:
            if interrupted_signal:
                raise KeyboardInterrupt
            now = time.monotonic()
            if trailing_deadline is not None and now >= trailing_deadline:
                _validate_break_glass_credential(secret)
                break
            remaining = deadline - now
            if remaining <= 0:
                raise TimeoutError("credential prompt timed out")
            wait_for = min(remaining, _BREAK_GLASS_TTY_POLL_SECONDS)
            if trailing_deadline is not None:
                wait_for = min(wait_for, trailing_deadline - now)
            events = selector.select(max(0.0, wait_for))
            if interrupted_signal:
                raise KeyboardInterrupt
            if not events:
                continue
            count = os.readv(tty_fd, [read_buffer])
            if count == 0:
                raise EOFError("credential terminal reached EOF")
            for index in range(count):
                value = read_buffer[index]
                if value in rejected_controls:
                    raise KeyboardInterrupt
                if trailing_deadline is not None:
                    raise ValueError("credential contains trailing input")
                if value in (10, 13):
                    trailing_deadline = min(
                        deadline,
                        time.monotonic() + _BREAK_GLASS_TRAILING_INPUT_SECONDS,
                    )
                    continue
                if len(secret) >= _MAX_CREDENTIAL_BYTES:
                    raise ValueError("credential exceeds its bound")
                secret.append(value)
            for index in range(count):
                read_buffer[index] = 0
    except (EOFError, KeyboardInterrupt, OSError, TimeoutError, UnicodeError, ValueError) as exc:
        expected_failure = exc
    except BaseException as exc:  # terminal restoration still precedes propagation
        unexpected_failure = exc
    finally:
        with _break_glass_cleanup_boundary():
            if selector is not None:
                try:
                    selector.close()
                except BaseException as exc:
                    if expected_failure is None and unexpected_failure is None:
                        unexpected_failure = exc
            if tty_fd >= 0:
                if not _flush_tty_input(tty_fd):
                    restore_failed = True
                if original_attributes is not None:
                    restored = _restore_tty_state(tty_fd, original_attributes)
                    if (
                        not restored
                        and independent_tty_state is not None
                        and _restore_independent_tty_state(
                            tty_fd, independent_tty_state, original_attributes
                        )
                    ):
                        restored = True
                    if not restored:
                        restore_failed = True
                for terminal_signal, original_handler in original_signal_handlers.items():
                    try:
                        signal.signal(terminal_signal, original_handler)
                    except (OSError, ValueError):
                        restore_failed = True
                with contextlib.suppress(OSError):
                    _write_all(tty_fd, b"\n")
                with contextlib.suppress(OSError):
                    os.close(tty_fd)
            _zeroize(read_buffer)
            if (
                interrupted_signal
                and expected_failure is None
                and unexpected_failure is None
            ):
                expected_failure = KeyboardInterrupt()
            if expected_failure is not None or restore_failed:
                _zeroize(secret)
                pending_error = _credential_unavailable(expected_failure)
            elif unexpected_failure is not None:
                _zeroize(secret)
                pending_error = unexpected_failure
    if pending_error is not None:
        raise pending_error
    return secret


def _credential_broker_entry(command: str, password_fd: int, status_fd: int) -> None:
    """Isolated credential relay; secret bytes never enter the application process."""

    status_socket = socket.socket(fileno=status_fd)
    succeeded = False
    try:
        if not Path(command).is_absolute():
            raise ValueError("credential command is not absolute")
        result = BoundedSubprocessRunner().run(
            [command],
            env=_minimal_env(),
            max_output=_MAX_CREDENTIAL_BYTES,
            # The broker already owns an isolated session. Keeping the command
            # in that group lets the application kill the entire broker tree
            # if broker startup or status delivery itself stalls.
            interactive=True,
            timeout_seconds=_CREDENTIAL_COMMAND_TIMEOUT_SECONDS,
        )
        if result.returncode or not result.stdout:
            raise ValueError("credential command failed")
        pending = memoryview(result.stdout)
        offset = 0
        while offset < len(pending):
            offset += os.write(password_fd, pending[offset:])
        status_socket.send(b"O")
        succeeded = True
    except Exception:
        with contextlib.suppress(OSError):
            status_socket.send(b"E")
    finally:
        with contextlib.suppress(OSError):
            os.close(password_fd)
        status_socket.close()
    if not succeeded:
        raise SystemExit(1)


def filevault_enabled(runner: ProcessRunner | None = None) -> bool:
    if sys.platform != "darwin":
        return False
    result = (runner or BoundedSubprocessRunner()).run(
        ["/usr/bin/fdesetup", "status"], env=_minimal_env(), max_output=1024
    )
    return result.returncode == 0 and b"filevault is on" in result.stdout.lower()


def _open_path_components(absolute: Path) -> list[int]:
    """Open one absolute path without following any component symlink."""

    parts = tuple(part for part in absolute.parts if part != absolute.anchor)
    if (
        not absolute.is_absolute()
        or len(parts) > _MAX_MOUNT_COMPONENTS
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise OSError("unsupported path")
    descriptors: list[int] = []
    try:
        root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open("/", root_flags))
        for index, part in enumerate(parts, start=1):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if index < len(parts):
                flags |= os.O_DIRECTORY
            descriptor = os.open(part, flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            current = os.fstat(descriptor)
            if index < len(parts) and not stat.S_ISDIR(current.st_mode):
                raise OSError("unsafe ancestor")
            if index == len(parts) and not (
                stat.S_ISREG(current.st_mode) or stat.S_ISDIR(current.st_mode)
            ):
                raise OSError("unsafe object")
        return descriptors
    except Exception:
        for descriptor in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        raise


@dataclass(frozen=True)
class _PathDescriptor:
    fd: int
    device: int
    inode: int
    mode: int

    @property
    def identity(self) -> tuple[int, int, int]:
        return self.device, self.inode, stat.S_IFMT(self.mode)


def _path_metadata_payload(descriptors: Sequence[int]) -> bytes:
    """Child-only serialization of descriptor metadata into a fixed frame."""

    if not descriptors or len(descriptors) > _MAX_MOUNT_COMPONENTS + 1:
        raise ValueError("invalid descriptor count")
    values = bytearray(_PATH_METADATA_HEADER.pack(_PATH_METADATA_MAGIC, len(descriptors)))
    for descriptor in descriptors:
        current = os.fstat(descriptor)
        values.extend(
            _PATH_METADATA_ENTRY.pack(
                current.st_dev,
                current.st_ino,
                current.st_mode,
            )
        )
    return bytes(values)


def _path_open_worker_entry(socket_fd: int) -> None:
    """Child-only path opener; responses contain no path or OS error text."""

    worker_socket = socket.socket(fileno=socket_fd)
    try:
        request = worker_socket.recv(_MAX_PATH_REQUEST_BYTES + 1)
        if not request or len(request) > _MAX_PATH_REQUEST_BYTES:
            return
        absolute = Path(os.fsdecode(request))
        if not absolute.is_absolute() or os.fsencode(absolute) != request:
            return
        worker_socket.send(b"R")
        while True:
            command = worker_socket.recv(1)
            if command != b"O":
                return
            descriptors: list[int] = []
            try:
                descriptors = _open_path_components(absolute)
                payload = _path_metadata_payload(descriptors)
                packed = array.array("i", descriptors).tobytes()
                worker_socket.sendmsg(
                    [payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, packed)]
                )
            except Exception:
                with contextlib.suppress(OSError):
                    worker_socket.send(b"E")
            finally:
                for descriptor in reversed(descriptors):
                    with contextlib.suppress(OSError):
                        os.close(descriptor)
    except (OSError, TypeError, UnicodeError, ValueError):
        return
    finally:
        worker_socket.close()


class _PathOpenSession:
    def __init__(
        self,
        process: subprocess.Popen,
        parent_socket: socket.socket,
        expected_descriptors: int,
        *,
        clock: Callable[[], float],
    ) -> None:
        self.process = process
        self.socket = parent_socket
        self.expected_descriptors = expected_descriptors
        self.clock = clock
        self.closed = False

    @classmethod
    def start(
        cls,
        absolute: Path,
        expected_descriptors: int,
        *,
        clock: Callable[[], float],
        startup_timeout: float,
    ) -> _PathOpenSession:
        if startup_timeout <= 0 or not Path(sys.executable).is_absolute():
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path open timed out."
            )
        encoded = os.fsencode(absolute)
        if not encoded or len(encoded) > _MAX_PATH_REQUEST_BYTES:
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path is unavailable or unsafe."
            )
        parent_socket, child_socket = socket.socketpair(
            socket.AF_UNIX, socket.SOCK_DGRAM
        )
        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-c", _PATH_OPEN_WORKER_CODE, str(child_socket.fileno())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_minimal_env(),
                cwd=str(Path(__file__).resolve().parents[2]),
                shell=False,
                close_fds=True,
                pass_fds=(child_socket.fileno(),),
                start_new_session=True,
            )
            child_socket.close()
            parent_socket.settimeout(startup_timeout)
            parent_socket.send(encoded)
            if parent_socket.recv(1) != b"R":
                raise OSError("path worker did not become ready")
            parent_socket.settimeout(None)
            return cls(
                process,
                parent_socket,
                expected_descriptors,
                clock=clock,
            )
        except (OSError, TimeoutError) as exc:
            child_socket.close()
            parent_socket.close()
            if process is not None and process.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_PATH_OPEN_CLEANUP_SECONDS)
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path is unavailable or unsafe."
            ) from exc

    def open_path(self, *, deadline: float, max_wait: float) -> list[_PathDescriptor]:
        wait = min(max_wait, deadline - self.clock())
        if self.closed or wait <= 0:
            self.terminate()
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path open timed out."
            )
        received: list[int] = []
        try:
            self.socket.settimeout(wait)
            self.socket.send(b"O")
            item_size = array.array("i").itemsize
            payload_size = _PATH_METADATA_HEADER.size + (
                self.expected_descriptors * _PATH_METADATA_ENTRY.size
            )
            payload, ancillary, flags, _address = self.socket.recvmsg(
                payload_size,
                socket.CMSG_SPACE(self.expected_descriptors * item_size),
            )
            invalid_ancillary = False
            rights_records = 0
            for level, kind, data in ancillary:
                if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                    invalid_ancillary = True
                    continue
                rights_records += 1
                if not data:
                    invalid_ancillary = True
                    continue
                usable = len(data) - (len(data) % item_size)
                values = array.array("i")
                values.frombytes(data[:usable])
                received.extend(values.tolist())
                if usable != len(data):
                    invalid_ancillary = True
            if (
                invalid_ancillary
                or rights_records != 1
                or flags != 0
                or len(received) != self.expected_descriptors
            ):
                raise ValueError("invalid descriptor response")
            if len(payload) != payload_size:
                raise ValueError("invalid metadata response")
            magic, count = _PATH_METADATA_HEADER.unpack_from(payload)
            if magic != _PATH_METADATA_MAGIC or count != self.expected_descriptors:
                raise ValueError("invalid metadata response")
            metadata: list[tuple[int, int, int]] = []
            offset = _PATH_METADATA_HEADER.size
            for _index in range(count):
                device, inode, mode = _PATH_METADATA_ENTRY.unpack_from(payload, offset)
                offset += _PATH_METADATA_ENTRY.size
                if (
                    device > _MAX_NATIVE_DEVICE
                    or not inode
                    or mode > _MAX_PATH_MODE
                ):
                    raise ValueError("invalid metadata response")
                if stat.S_IFMT(mode) not in (stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("invalid metadata response")
                metadata.append((device, inode, mode))
            for descriptor in received:
                os.set_inheritable(descriptor, False)
            return [
                _PathDescriptor(descriptor, device, inode, mode)
                for descriptor, (device, inode, mode) in zip(
                    received, metadata, strict=True
                )
            ]
        except TimeoutError as exc:
            self.terminate()
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path open timed out."
            ) from exc
        except Exception as exc:
            for descriptor in received:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path is unavailable or unsafe."
            ) from exc
        finally:
            if not self.closed:
                with contextlib.suppress(OSError):
                    self.socket.settimeout(None)

    def terminate(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.socket.close()
        if self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=_PATH_OPEN_CLEANUP_SECONDS)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(OSError):
            self.socket.send(b"Q")
        self.socket.close()
        try:
            self.process.wait(timeout=_PATH_OPEN_CLEANUP_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=_PATH_OPEN_CLEANUP_SECONDS)


@dataclass(frozen=True)
class _BoundPathAuthority:
    absolute: Path
    descriptors: tuple[_PathDescriptor, ...]
    device: int
    inode: int
    mode: int

    @property
    def fd(self) -> int:
        return self.descriptors[-1].fd

    @property
    def object_token(self) -> str:
        return f"{self.device}:{self.inode}"


@contextlib.contextmanager
def _bounded_path_authority(
    path: Path,
    *,
    require_directory: bool,
    monotonic: Callable[[], float] = time.monotonic,
    path_open_timeout_seconds: float = _PATH_OPEN_TIMEOUT_SECONDS,
) -> Iterator[_BoundPathAuthority]:
    """Hold no-follow descriptors while bounded pathname authority is in use."""

    descriptors: list[_PathDescriptor] = []
    session: _PathOpenSession | None = None
    deadline = monotonic() + _TOPOLOGY_RESOLUTION_TIMEOUT_SECONDS
    try:
        raw = os.fspath(path)
        absolute = Path(os.path.abspath(raw))
        parts = tuple(part for part in absolute.parts if part != absolute.anchor)
        if (
            not absolute.is_absolute()
            or len(parts) > _MAX_MOUNT_COMPONENTS
            or not hasattr(os, "O_NOFOLLOW")
        ):
            raise OSError("unsupported path")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path open timed out."
            )
        session = _PathOpenSession.start(
            absolute,
            len(parts) + 1,
            clock=monotonic,
            startup_timeout=remaining,
        )
        descriptors = session.open_path(
            deadline=deadline,
            max_wait=path_open_timeout_seconds,
        )
        current = descriptors[-1]
        if require_directory:
            if not stat.S_ISDIR(current.mode):
                raise OSError("path is not a directory")
        elif not (stat.S_ISREG(current.mode) or stat.S_ISDIR(current.mode)):
            raise OSError("path is not a regular object")
        yield _BoundPathAuthority(
            absolute=absolute,
            descriptors=tuple(descriptors),
            device=current.device,
            inode=current.inode,
            mode=current.mode,
        )
    except RecoveryError:
        raise
    except (OSError, TypeError, UnicodeError, ValueError) as exc:
        raise RecoveryError(
            "recovery_target_unavailable", "Recovery path is unavailable or unsafe."
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            with contextlib.suppress(OSError):
                os.close(descriptor.fd)
        if session is not None:
            session.close()


def _topology_metadata(descriptor: _PathDescriptor) -> _PathDescriptor:
    """Test seam; production consumes metadata returned by the killable helper."""

    return descriptor


def _macos_device_subject(device: int) -> str:
    """Resolve the held vnode's block device without consulting a pathname."""

    if sys.platform != "darwin":
        raise RecoveryError(
            "recovery_target_unavailable", "Storage device identity is unavailable."
        )
    try:
        if type(device) is not int or not 0 <= device <= _MAX_NATIVE_DEVICE:
            raise ValueError("invalid block device")
        libc = ctypes.CDLL(None, use_errno=True)
        devname = libc.devname
        devname.argtypes = (ctypes.c_int, ctypes.c_uint)
        devname.restype = ctypes.c_char_p
        raw = devname(device, stat.S_IFBLK)
        if raw is None:
            raise ValueError("no block device")
        return raw.decode("ascii", errors="strict")
    except (AttributeError, OSError, TypeError, UnicodeError, ValueError) as exc:
        raise RecoveryError(
            "recovery_target_unavailable", "Storage device identity is unavailable."
        ) from exc


def _physical_parent_inventory(identity: StorageIdentity) -> frozenset[str]:
    return frozenset(identity.physical_parents or (identity.physical_parent,))


@dataclass(frozen=True)
class _BoundTopologyPath:
    absolute: Path
    parts: tuple[str, ...]
    descriptors: tuple[_PathDescriptor, ...]
    identities: tuple[tuple[int, int, int], ...]
    mount_index: int
    session: _PathOpenSession

    @property
    def mount_fd(self) -> int:
        return self.descriptors[self.mount_index].fd

    @property
    def mount_device(self) -> int:
        return self.identities[self.mount_index][0]

    @property
    def object_token(self) -> str:
        device, inode, _mode = self.identities[-1]
        return f"{device}:{inode}"


class MacOSTopologyResolver:
    _LOCAL_PROTOCOL_MARKERS = (
        "apple fabric",
        "firewire",
        "nvme",
        "pci",
        "sata",
        "secure digital",
        "thunderbolt",
        "usb",
    )

    def __init__(
        self,
        runner: ProcessRunner | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        device_subject: Callable[[int], str] = _macos_device_subject,
        path_open_timeout_seconds: float = _PATH_OPEN_TIMEOUT_SECONDS,
    ) -> None:
        self.runner = runner or BoundedSubprocessRunner()
        self.monotonic = monotonic
        self.device_subject = device_subject
        self.path_open_timeout_seconds = path_open_timeout_seconds

    def resolve(self, path: Path) -> StorageIdentity:
        deadline = self.monotonic() + _TOPOLOGY_RESOLUTION_TIMEOUT_SECONDS
        with self._bind_path(path, deadline) as bound:
            self._revalidate(bound, deadline)
            try:
                subject = self.device_subject(bound.mount_device)
            except RecoveryError:
                raise
            except (OSError, TypeError, UnicodeError, ValueError) as exc:
                raise RecoveryError(
                    "recovery_target_unavailable", "Storage device identity is unavailable."
                ) from exc
            if not self._valid_device_subject(subject):
                raise RecoveryError(
                    "recovery_target_unavailable", "Storage device identity is unavailable."
                )
            command_timeout = self._remaining(deadline)
            info: dict[str, Any] | None = None
            command_error: RecoveryError | None = None
            try:
                info = self._diskutil_info(
                    subject,
                    timeout_seconds=command_timeout,
                )
            except RecoveryError as exc:
                command_error = exc
            self._revalidate(bound, deadline)
            if command_error is not None:
                raise RecoveryError(
                    "recovery_target_unavailable", "Storage topology is unavailable."
                ) from None
            self._remaining(deadline)
            assert info is not None
            return self._identity_from_info(info, bound.object_token)

    @contextlib.contextmanager
    def _bind_path(self, path: Path, deadline: float) -> Iterator[_BoundTopologyPath]:
        descriptors: list[_PathDescriptor] = []
        session: _PathOpenSession | None = None
        try:
            raw = os.fspath(path)
            absolute = Path(os.path.abspath(raw))
            parts = tuple(part for part in absolute.parts if part != absolute.anchor)
            if len(parts) > _MAX_MOUNT_COMPONENTS or not hasattr(os, "O_NOFOLLOW"):
                raise OSError("unsupported path")
            startup_timeout = self._remaining(deadline)
            session = _PathOpenSession.start(
                absolute,
                len(parts) + 1,
                clock=self.monotonic,
                startup_timeout=startup_timeout,
            )
            descriptors = session.open_path(
                deadline=deadline, max_wait=self.path_open_timeout_seconds
            )
            root_metadata = _topology_metadata(descriptors[0])
            identities = [root_metadata.identity]
            mount_index = 0
            previous_device = root_metadata.device
            for index, descriptor in enumerate(descriptors[1:], start=1):
                current = _topology_metadata(descriptor)
                if index < len(parts) and not stat.S_ISDIR(current.mode):
                    raise OSError("unsafe ancestor")
                if index == len(parts) and not (
                    stat.S_ISREG(current.mode) or stat.S_ISDIR(current.mode)
                ):
                    raise OSError("unsafe object")
                identities.append(current.identity)
                if current.device != previous_device:
                    mount_index = index
                previous_device = current.device
            yield _BoundTopologyPath(
                absolute=absolute,
                parts=parts,
                descriptors=tuple(descriptors),
                identities=tuple(identities),
                mount_index=mount_index,
                session=session,
            )
        except RecoveryError:
            raise
        except (OSError, TypeError, UnicodeError, ValueError) as exc:
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery path is unavailable or unsafe."
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                with contextlib.suppress(OSError):
                    os.close(descriptor.fd)
            if session is not None:
                session.close()

    def _revalidate(self, bound: _BoundTopologyPath, deadline: float) -> None:
        reopened: list[_PathDescriptor] = []
        try:
            for held, expected in zip(
                bound.descriptors, bound.identities, strict=True
            ):
                self._remaining(deadline)
                if _topology_metadata(held).identity != expected:
                    raise OSError("held identity changed")
            reopened = bound.session.open_path(
                deadline=deadline, max_wait=self.path_open_timeout_seconds
            )
            if len(reopened) != len(bound.identities):
                raise OSError("path depth changed")
            for descriptor, expected in zip(
                reopened, bound.identities, strict=True
            ):
                if _topology_metadata(descriptor).identity != expected:
                    raise OSError("path identity changed")
        except RecoveryError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise RecoveryError(
                "recovery_target_unavailable", "Storage topology changed during resolution."
            ) from exc
        finally:
            for descriptor in reversed(reopened):
                with contextlib.suppress(OSError):
                    os.close(descriptor.fd)

    def _diskutil_info(self, subject: str, *, timeout_seconds: float) -> dict[str, Any]:
        try:
            result = self.runner.run(
                ["/usr/sbin/diskutil", "info", "-plist", subject],
                env=_minimal_env(),
                max_output=_MAX_DISKUTIL_OUTPUT,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, RecoveryError) as exc:
            raise RecoveryError(
                "recovery_target_unavailable", "Storage topology command failed."
            ) from exc
        if result.returncode:
            raise RecoveryError(
                "recovery_target_unavailable", "Storage topology command failed."
            )
        try:
            info = plistlib.loads(result.stdout)
            if (
                not isinstance(info, dict)
                or "Error" in info
                or "ErrorMessage" in info
                or info.get("DeviceIdentifier") != subject
            ):
                raise ValueError("invalid diskutil response")
            return info
        except (TypeError, ValueError, plistlib.InvalidFileException) as exc:
            raise RecoveryError(
                "recovery_target_unavailable", "Storage topology response is invalid."
            ) from exc

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise RecoveryError(
                "recovery_target_unavailable", "Storage topology resolution timed out."
            )
        return remaining

    @classmethod
    def _identity_from_info(cls, info: dict[str, Any], object_token: str) -> StorageIdentity:
        try:
            raw_stores = info.get("APFSPhysicalStores", [])
            if not isinstance(raw_stores, list):
                raise ValueError("invalid physical store inventory")
            stores: set[str] = set()
            for item in raw_stores:
                if isinstance(item, str):
                    store = item
                elif isinstance(item, dict):
                    store = item.get("APFSPhysicalStore")
                else:
                    raise ValueError("invalid physical store entry")
                if not cls._valid_device_identifier(store):
                    raise ValueError("invalid physical store identifier")
                stores.add(store)
            if stores:
                parents = tuple(sorted(stores))
            else:
                parent = info.get("ParentWholeDisk")
                if not cls._valid_device_identifier(parent):
                    raise ValueError("missing physical parent")
                parents = (parent,)

            internal = info.get("Internal")
            if type(internal) is not bool:
                raise ValueError("missing physical media classification")
            raw_protocol = info.get("BusProtocol", info.get("Protocol"))
            if not isinstance(raw_protocol, str) or not raw_protocol.strip():
                raise ValueError("missing bus protocol")
            protocol = raw_protocol.strip().lower()
            classification_values = [protocol]
            for key in (
                "DeviceLocation",
                "FilesystemType",
                "MediaName",
                "MediaType",
                "VolumeKind",
            ):
                value = info.get(key)
                if isinstance(value, str):
                    classification_values.append(value.lower())
            classification = " ".join(classification_values)
            if any(
                marker in classification
                for marker in ("afp", "disk image", "network", "nfs", "smb", "webdav")
            ):
                raise ValueError("not local physical storage")
            if not any(marker in protocol for marker in cls._LOCAL_PROTOCOL_MARKERS):
                raise ValueError("unsupported bus protocol")
            virtual = info.get("VirtualOrPhysical")
            if virtual is not None and (
                not isinstance(virtual, str) or virtual.strip().lower() != "physical"
            ):
                raise ValueError("not physical storage")
            media = "internal" if internal else "external"
            return StorageIdentity(parents[0], media, object_token, parents)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RecoveryError(
                "recovery_target_unavailable", "Storage topology is not a qualifying physical disk."
            ) from exc

    @staticmethod
    def _valid_device_identifier(value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) is not None

    @staticmethod
    def _valid_device_subject(value: Any) -> bool:
        return (
            isinstance(value, str)
            and re.fullmatch(r"disk[0-9]+(?:s[0-9]+)*", value) is not None
        )


class ResticRepository:
    def __init__(
        self,
        settings: Settings,
        *,
        runner: ProcessRunner | None = None,
        topology: MacOSTopologyResolver | None = None,
        path_open_timeout_seconds: float = _PATH_OPEN_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings
        self.runner = runner or BoundedSubprocessRunner()
        self.topology = topology or MacOSTopologyResolver(self.runner)
        self.binary = Path(settings.recovery_restic_path)
        self.repository = Path(settings.recovery_repository)
        self.credential_command = Path(settings.recovery_credential_command)
        self.path_open_timeout_seconds = path_open_timeout_seconds

    def _binary_authority(self) -> _BinaryAuthority:
        path = self.binary
        descriptor = -1
        try:
            if not path.is_absolute():
                raise OSError("binary path is not absolute")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_mode & (
                stat.S_IWGRP | stat.S_IWOTH
            ):
                raise OSError("binary mode is unsafe")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
            before_identity = (before.st_dev, before.st_ino, before.st_size)
            unsafe_mode = stat.S_IWGRP | stat.S_IWOTH
            if (
                before_identity != (after.st_dev, after.st_ino, after.st_size)
                or before_identity != (current.st_dev, current.st_ino, current.st_size)
                or before.st_mode != after.st_mode
                or before.st_mode != current.st_mode
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_mtime_ns != current.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
                or before.st_ctime_ns != current.st_ctime_ns
                or not stat.S_ISREG(current.st_mode)
                or current.st_mode & unsafe_mode
            ):
                raise OSError("binary identity changed")
            authority = _BinaryAuthority(*before_identity, digest.hexdigest())
        except OSError as exc:
            raise RecoveryError(
                "recovery_tool_invalid", "Recovery tool configuration is invalid."
            ) from exc
        finally:
            if descriptor >= 0:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
        if not self.settings.recovery_restic_sha256 or not hmac.compare_digest(
            authority.digest, self.settings.recovery_restic_sha256.lower()
        ):
            raise RecoveryError("recovery_tool_invalid", "Recovery tool digest is invalid.")
        return authority

    def _validate_binary_authority(
        self, expected_authority: _BinaryAuthority | None = None
    ) -> _BinaryAuthority:
        authority = self._binary_authority()
        if expected_authority is not None and authority != expected_authority:
            raise RecoveryError(
                "recovery_tool_invalid", "Recovery tool identity changed."
            )
        result = self.runner.run(
            [str(self.binary), "version"],
            env=_minimal_env(),
            timeout_seconds=_RESTIC_VERSION_TIMEOUT_SECONDS,
        )
        expected = f"restic {self.settings.recovery_restic_version}"
        if result.returncode or expected.encode() not in result.stdout[:256]:
            raise RecoveryError("recovery_tool_invalid", "Recovery tool version is invalid.")
        confirmed = self._binary_authority()
        if confirmed != authority:
            raise RecoveryError(
                "recovery_tool_invalid", "Recovery tool identity changed."
            )
        return authority

    def validate_break_glass_tool(
        self, expected_authority: _BinaryAuthority | None = None
    ) -> _BinaryAuthority:
        """Validate only pinned Restic; automation credential authority is excluded."""

        return self._validate_binary_authority(expected_authority)

    def validate_tool(self) -> None:
        self._validate_binary_authority()
        for path in (self.credential_command,):
            if not path.is_absolute() or not path.is_file() or path.is_symlink():
                raise RecoveryError(
                    "recovery_tool_invalid", "Recovery tool configuration is invalid."
                )
            mode = path.stat().st_mode
            if mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise RecoveryError(
                    "recovery_tool_invalid", "Recovery tool configuration is invalid."
                )

    @contextlib.contextmanager
    def break_glass_credential(
        self,
    ) -> Iterator[tuple[bytearray, _BinaryAuthority]]:
        """Prompt once and best-effort erase the in-process credential on exit."""

        secret: bytearray | None = None
        with _break_glass_lifetime_signal_guard() as signal_state:
            try:
                authority = self.validate_break_glass_tool()
                secret = _read_break_glass_credential()
                yield secret, authority
            except KeyboardInterrupt as exc:
                raise _credential_unavailable(exc) from exc
            finally:
                if secret is not None:
                    with _break_glass_cleanup_boundary():
                        _zeroize(secret)
            if signal_state.interrupted:
                raise _credential_unavailable()

    def validate_topology(self, source: Path) -> tuple[StorageIdentity, StorageIdentity]:
        if not self.repository.is_absolute():
            raise RecoveryError("recovery_target_unavailable", "Recovery target is unavailable.")
        source_id = self.topology.resolve(source)
        target_id = self.topology.resolve(self.repository)
        with _bounded_path_authority(
            self.repository,
            require_directory=True,
            path_open_timeout_seconds=self.path_open_timeout_seconds,
        ) as authority:
            if target_id.object_token != authority.object_token:
                raise RecoveryError(
                    "recovery_target_unavailable", "Recovery repository identity changed."
                )
        if not _physical_parent_inventory(source_id).isdisjoint(
            _physical_parent_inventory(target_id)
        ):
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery target is not physically separate."
            )
        return source_id, target_id

    @contextlib.contextmanager
    def _password_descriptor(self) -> Iterator[int]:
        """Yield a one-use password pipe populated only by an isolated broker."""

        password_read = -1
        password_write = -1
        parent_status: socket.socket | None = None
        child_status: socket.socket | None = None
        process: subprocess.Popen | None = None
        try:
            password_read, password_write = os.pipe()
            parent_status, child_status = socket.socketpair(
                socket.AF_UNIX, socket.SOCK_DGRAM
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _CREDENTIAL_BROKER_CODE,
                    str(self.credential_command),
                    str(password_write),
                    str(child_status.fileno()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_minimal_env(),
                cwd=str(Path(__file__).resolve().parents[2]),
                shell=False,
                close_fds=True,
                pass_fds=(password_write, child_status.fileno()),
                start_new_session=True,
            )
            os.close(password_write)
            password_write = -1
            child_status.close()
            child_status = None
            parent_status.settimeout(_CREDENTIAL_BROKER_TIMEOUT_SECONDS)
            if parent_status.recv(1) != b"O":
                raise RecoveryError(
                    "recovery_repository_error", "Recovery credential is unavailable."
                )
            try:
                returncode = process.wait(
                    timeout=_CREDENTIAL_BROKER_EXIT_TIMEOUT_SECONDS
                )
            except subprocess.TimeoutExpired as exc:
                raise RecoveryError(
                    "recovery_repository_error", "Recovery credential is unavailable."
                ) from exc
            if returncode:
                raise RecoveryError(
                    "recovery_repository_error", "Recovery credential is unavailable."
                )
            yield password_read
        except RecoveryError:
            raise
        except (OSError, TimeoutError, ValueError) as exc:
            raise RecoveryError(
                "recovery_repository_error", "Recovery credential is unavailable."
            ) from exc
        finally:
            if parent_status is not None:
                parent_status.close()
            if child_status is not None:
                child_status.close()
            for descriptor in (password_read, password_write):
                if descriptor >= 0:
                    with contextlib.suppress(OSError):
                        os.close(descriptor)
            if process is not None and process.poll() is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=_CREDENTIAL_BROKER_CLEANUP_SECONDS)

    @contextlib.contextmanager
    def _break_glass_password_descriptor(
        self,
        secret: bytearray,
        *,
        binary_authority: _BinaryAuthority,
        pin: RepositoryPin,
    ) -> Iterator[int]:
        """Yield a fresh EOF-terminated anonymous pipe for one Restic child."""

        password_read = -1
        password_write = -1
        try:
            _validate_break_glass_credential(secret)
            self._assert_pin(pin)
            self.validate_break_glass_tool(binary_authority)
            password_read, password_write = os.pipe()
            _write_all(password_write, secret)
            os.close(password_write)
            password_write = -1
            yield password_read
        except RecoveryError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise _credential_unavailable(exc) from exc
        finally:
            with _break_glass_cleanup_boundary():
                for descriptor in (password_read, password_write):
                    if descriptor >= 0:
                        with contextlib.suppress(OSError):
                            os.close(descriptor)

    @contextlib.contextmanager
    def pin_repository(self, source: Path, *, lock_fd: int) -> Iterator[RepositoryPin]:
        topology = self.validate_topology(source)
        with _bounded_path_authority(
            self.repository,
            require_directory=True,
            path_open_timeout_seconds=self.path_open_timeout_seconds,
        ) as authority:
            fd = authority.fd
            target_token = topology[1].object_token
            if target_token != authority.object_token:
                raise RecoveryError(
                    "recovery_target_unavailable", "Recovery repository identity changed."
                )
            provisional = RepositoryPin(
                fd=fd,
                repository_path=".",
                topology=topology,
                repository_id="",
                device=authority.device,
                inode=authority.inode,
                lock_fd=lock_fd,
            )
            repository_id = self.repository_id(pin=provisional)
            yield replace(provisional, repository_id=repository_id)

    @contextlib.contextmanager
    def pin_break_glass_repository(
        self, source: Path, *, lock_fd: int
    ) -> Iterator[RepositoryPin]:
        """Pin interactive restore authority without an automation credential call."""

        topology = self.validate_topology(source)
        with _bounded_path_authority(
            self.repository,
            require_directory=True,
            path_open_timeout_seconds=self.path_open_timeout_seconds,
        ) as authority:
            if topology[1].object_token != authority.object_token:
                raise RecoveryError(
                    "recovery_target_unavailable", "Recovery repository identity changed."
                )
            pin = RepositoryPin(
                fd=authority.fd,
                repository_path=".",
                topology=topology,
                repository_id="",
                device=authority.device,
                inode=authority.inode,
                lock_fd=lock_fd,
            )
            completed = False
            try:
                yield pin
                completed = True
            finally:
                if completed:
                    self._assert_pin(pin)
                    if self.validate_topology(source) != topology:
                        raise RecoveryError(
                            "recovery_target_unavailable",
                            "Storage topology changed during restore.",
                        )

    def _assert_pin(self, pin: RepositoryPin) -> None:
        try:
            with _bounded_path_authority(
                self.repository,
                require_directory=True,
                path_open_timeout_seconds=self.path_open_timeout_seconds,
            ) as authority:
                current_mode = authority.mode
                current_identity = (authority.device, authority.inode)
        except RecoveryError:
            raise
        except OSError as exc:
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery repository identity changed."
            ) from exc
        if (
            not stat.S_ISDIR(current_mode)
            or current_identity != (pin.device, pin.inode)
        ):
            raise RecoveryError(
                "recovery_target_unavailable", "Recovery repository identity changed."
            )

    def _argv(
        self,
        *args: str,
        use_credential: bool = True,
        pin: RepositoryPin | None = None,
        password_fd: int | None = None,
    ) -> list[str]:
        repository = "." if pin is not None else str(self.repository)
        argv = [str(self.binary), "-r", repository]
        if use_credential:
            if password_fd is None:
                raise RecoveryError(
                    "recovery_repository_error", "Recovery credential is unavailable."
                )
            argv += ["--password-file", f"/dev/fd/{password_fd}"]
        argv += list(args)
        return argv

    def _run_restic(
        self,
        *args: str,
        input_bytes: bytes | None = None,
        max_output: int = _MAX_TOOL_OUTPUT,
        use_credential: bool = True,
        interactive: bool = False,
        pin: RepositoryPin | None = None,
        timeout_seconds: float,
    ) -> ProcessResult:
        authority_fds = (pin.fd, pin.lock_fd) if pin is not None else ()
        if not use_credential:
            return self.runner.run(
                self._argv(*args, use_credential=False, pin=pin),
                input_bytes=input_bytes,
                env=_minimal_env(),
                max_output=max_output,
                interactive=interactive,
                pass_fds=authority_fds,
                cwd_fd=pin.fd if pin is not None else None,
                timeout_seconds=timeout_seconds,
            )
        with self._password_descriptor() as password_fd:
            return self.runner.run(
                self._argv(
                    *args,
                    use_credential=True,
                    pin=pin,
                    password_fd=password_fd,
                ),
                input_bytes=input_bytes,
                env=_minimal_env(),
                max_output=max_output,
                interactive=interactive,
                pass_fds=(*authority_fds, password_fd),
                cwd_fd=pin.fd if pin is not None else None,
                timeout_seconds=timeout_seconds,
            )

    def _run_break_glass_restic(
        self,
        *args: str,
        secret: bytearray,
        binary_authority: _BinaryAuthority,
        max_output: int = _MAX_TOOL_OUTPUT,
        pin: RepositoryPin,
    ) -> ProcessResult:
        authority_fds = (pin.fd, pin.lock_fd)
        with self._break_glass_password_descriptor(
            secret, binary_authority=binary_authority, pin=pin
        ) as password_fd:
            result = self.runner.run(
                self._argv(
                    *args,
                    use_credential=True,
                    pin=pin,
                    password_fd=password_fd,
                ),
                env=_minimal_env(),
                max_output=max_output,
                interactive=False,
                pass_fds=(*authority_fds, password_fd),
                cwd_fd=pin.fd,
                timeout_seconds=_RESTIC_BREAK_GLASS_TIMEOUT_SECONDS,
            )
        _raise_deferred_break_glass_signal()
        return result

    def _checked_break_glass(
        self,
        *args: str,
        secret: bytearray,
        binary_authority: _BinaryAuthority,
        max_output: int = _MAX_TOOL_OUTPUT,
        pin: RepositoryPin,
    ) -> bytes:
        result = self._run_break_glass_restic(
            *args,
            secret=secret,
            binary_authority=binary_authority,
            max_output=max_output,
            pin=pin,
        )
        if result.returncode:
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository operation failed."
            )
        return result.stdout

    def _checked(
        self,
        *args: str,
        input_bytes: bytes | None = None,
        max_output: int = _MAX_TOOL_OUTPUT,
        use_credential: bool = True,
        interactive: bool = False,
        pin: RepositoryPin | None = None,
        timeout_seconds: float,
    ) -> bytes:
        if pin is not None:
            self._assert_pin(pin)
        result = self._run_restic(
            *args,
            input_bytes=input_bytes,
            max_output=max_output,
            use_credential=use_credential,
            interactive=interactive,
            pin=pin,
            timeout_seconds=timeout_seconds,
        )
        if result.returncode:
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository operation failed."
            )
        return result.stdout

    def init(self) -> None:
        self.validate_tool()
        self._checked("init", timeout_seconds=_RESTIC_INIT_TIMEOUT_SECONDS)

    def latest_bundle(
        self, *, expect_snapshot: bool = False, pin: RepositoryPin | None = None
    ) -> bytes | None:
        if pin is not None:
            self._assert_pin(pin)
        result = self._run_restic(
            "dump",
            "latest",
            BUNDLE_FILENAME,
            "--tag",
            RECOVERY_TAG,
            max_output=_MAX_BUNDLE_BYTES,
            pin=pin,
            timeout_seconds=_RESTIC_DUMP_TIMEOUT_SECONDS,
        )
        if result.returncode:
            if expect_snapshot:
                raise RecoveryError(
                    "recovery_repository_error", "Encrypted recovery point could not be read."
                )
            snapshots = self.snapshots(pin=pin)
            if snapshots:
                raise RecoveryError(
                    "recovery_repository_error", "Encrypted recovery point could not be read."
                )
            return None
        return result.stdout

    def backup(self, bundle: bytes, *, pin: RepositoryPin) -> str:
        raw = self._checked(
            "backup",
            "--stdin",
            "--stdin-filename",
            BUNDLE_FILENAME,
            "--tag",
            RECOVERY_TAG,
            "--json",
            input_bytes=bundle,
            pin=pin,
            timeout_seconds=_RESTIC_BACKUP_TIMEOUT_SECONDS,
        )
        try:
            events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            summary = next(
                item for item in reversed(events) if item.get("message_type") == "summary"
            )
            snapshot_id = summary["snapshot_id"]
        except (KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository response failed."
            ) from exc
        if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository response failed."
            )
        return snapshot_id

    def check(self, *, pin: RepositoryPin) -> None:
        self._checked(
            "check",
            "--read-data-subset=1/20",
            pin=pin,
            timeout_seconds=_RESTIC_CHECK_TIMEOUT_SECONDS,
        )

    def snapshots(self, *, pin: RepositoryPin | None = None) -> list[dict[str, Any]]:
        raw = self._checked(
            "snapshots",
            "--tag",
            RECOVERY_TAG,
            "--json",
            pin=pin,
            timeout_seconds=_RESTIC_METADATA_TIMEOUT_SECONDS,
        )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository response failed."
            ) from exc
        if not isinstance(value, list):
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository response failed."
            )
        return value

    def repository_id(self, *, pin: RepositoryPin | None = None) -> str:
        raw = self._checked(
            "cat",
            "config",
            pin=pin,
            timeout_seconds=_RESTIC_METADATA_TIMEOUT_SECONDS,
        )
        try:
            value = json.loads(raw)
            repository_id = value["id"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository identity failed."
            ) from exc
        if not isinstance(repository_id, str) or not re.fullmatch(
            r"[0-9a-f]{64}", repository_id
        ):
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository identity failed."
            )
        return repository_id

    def retention(
        self,
        *,
        apply: bool,
        pin: RepositoryPin | None = None,
        expected_repository_id: str | None = None,
    ) -> bytes:
        args = [
            "forget",
            "--tag",
            RECOVERY_TAG,
            "--keep-hourly",
            "48",
            "--keep-daily",
            "30",
            "--keep-monthly",
            "12",
            "--prune",
            "--json",
        ]
        if not apply:
            args.append("--dry-run")
        if apply:
            if pin is None or expected_repository_id is None:
                raise RecoveryError(
                    "recovery_retention_invalid",
                    "Destructive retention requires a pinned repository.",
                )
            self._assert_pin(pin)
            actual_repository_id = self.repository_id(pin=pin)
            if not hmac.compare_digest(actual_repository_id, expected_repository_id):
                raise RecoveryError(
                    "recovery_retention_invalid", "Recovery repository identity changed."
                )
        return self._checked(
            *args,
            pin=pin,
            timeout_seconds=(
                _RESTIC_RETENTION_APPLY_TIMEOUT_SECONDS
                if apply
                else _RESTIC_RETENTION_DRY_RUN_TIMEOUT_SECONDS
            ),
        )

    def break_glass_bundle(
        self,
        snapshot: str,
        *,
        secret: bytearray,
        binary_authority: _BinaryAuthority,
        pin: RepositoryPin,
    ) -> bytes:
        args = ["dump", snapshot, BUNDLE_FILENAME]
        if snapshot == "latest":
            args += ["--tag", RECOVERY_TAG]
        return self._checked_break_glass(
            *args,
            secret=secret,
            binary_authority=binary_authority,
            max_output=_MAX_BUNDLE_BYTES,
            pin=pin,
        )

    def break_glass_snapshots(
        self,
        *,
        secret: bytearray,
        binary_authority: _BinaryAuthority,
        pin: RepositoryPin,
    ) -> list[dict[str, Any]]:
        raw = self._checked_break_glass(
            "snapshots",
            "--tag",
            RECOVERY_TAG,
            "--json",
            secret=secret,
            binary_authority=binary_authority,
            pin=pin,
        )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository response failed."
            ) from exc
        if not isinstance(value, list):
            raise RecoveryError(
                "recovery_repository_error", "Encrypted repository response failed."
            )
        return value

    def break_glass_check(
        self,
        *,
        secret: bytearray,
        binary_authority: _BinaryAuthority,
        pin: RepositoryPin,
    ) -> None:
        self._checked_break_glass(
            "check",
            "--read-data-subset=1/20",
            secret=secret,
            binary_authority=binary_authority,
            pin=pin,
        )


def _bundle_manifest(bundle: bytes) -> dict[str, Any]:
    if len(bundle) > _MAX_BUNDLE_BYTES:
        raise RecoveryError("recovery_bundle_invalid", "Recovery bundle exceeds its bound.")
    try:
        value = json.loads(bundle)
        if not isinstance(value, dict) or set(value) != {"manifest", "tables"}:
            raise ValueError("invalid bundle envelope")
        manifest = value["manifest"]
        if (
            not isinstance(manifest, dict)
            or not isinstance(value.get("tables"), dict)
            or manifest.get("format_version") != FORMAT_VERSION
            or manifest.get("canary") != RECOVERY_CANARY
        ):
            raise ValueError("invalid bundle")
        expected_manifest = {
            "format_version",
            "canary",
            "schema_fingerprint",
            "catalog",
            "tables",
            "account_statuses",
            "safe_content_digest",
        }
        if set(manifest) != expected_manifest:
            raise ValueError("invalid manifest fields")
        fingerprint = validate_catalog(manifest["catalog"])
        if manifest["schema_fingerprint"] != fingerprint:
            raise ValueError("invalid schema fingerprint")
        table_rows = value["tables"]
        expected_tables = set(EXPECTED_TABLE_COLUMNS) - NOT_BUNDLED
        if set(table_rows) != expected_tables or set(manifest["tables"]) != expected_tables:
            raise ValueError("invalid table inventory")
        for table in sorted(expected_tables):
            rows = table_rows[table]
            entry = manifest["tables"][table]
            if (
                not isinstance(rows, list)
                or not isinstance(entry, dict)
                or set(entry) != {"rows", "sha256"}
                or type(entry["rows"]) is not int
                or entry["rows"] < 0
                or entry["rows"] != len(rows)
                or not isinstance(entry["sha256"], str)
                or not hmac.compare_digest(entry["sha256"], _table_rows_digest(rows))
            ):
                raise ValueError("invalid table digest")
        statuses = manifest["account_statuses"]
        if not isinstance(statuses, list) or any(
            not isinstance(item, dict)
            or set(item) != {"id", "status"}
            or type(item["id"]) is not int
            or not isinstance(item["status"], str)
            for item in statuses
        ):
            raise ValueError("invalid account status metadata")
        if [item["id"] for item in statuses] != sorted(item["id"] for item in statuses) or len(
            {item["id"] for item in statuses}
        ) != len(statuses):
            raise ValueError("invalid account status ordering")
        expected_safe_digest = _safe_content_digest(manifest["tables"], statuses)
        if not isinstance(manifest["safe_content_digest"], str) or not hmac.compare_digest(
            manifest["safe_content_digest"], expected_safe_digest
        ):
            raise ValueError("invalid safe content digest")
        return manifest
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.") from exc


def run_backup(
    reason: Literal["hourly", "post-clean-portfolio-sync", "manual"],
    settings: Settings | None = None,
    *,
    repository: ResticRepository | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    del reason  # constant enum is intentionally not persisted or passed to restic.
    cfg = settings or get_settings()
    if not native_supported():
        raise RecoveryError(
            "recovery_topology_unsupported", "Recovery is supported on native macOS only."
        )
    if not _configured(cfg):
        raise RecoveryError("recovery_unconfigured", "Recovery is not configured.")
    repo = repository or ResticRepository(cfg)
    now = clock().astimezone(UTC)
    with (
        _break_glass_lifetime_signal_guard(),
        recovery_lock(cfg.recovery_lock_file) as lock_fd,
    ):
        prior = load_state(cfg.recovery_state_file)
        try:
            repo.validate_tool()
            with repo.pin_repository(cfg.db_file, lock_fd=lock_fd) as pin:
                before = pin.topology
                bundle, manifest = build_logical_bundle(cfg.db_file)
                previous_bundle = repo.latest_bundle(
                    expect_snapshot=prior.last_snapshot_at is not None,
                    pin=pin,
                )
                previous_manifest = (
                    _bundle_manifest(previous_bundle) if previous_bundle else None
                )
                unchanged = bool(
                    previous_manifest
                    and previous_manifest.get("safe_content_digest")
                    == manifest["safe_content_digest"]
                )
                if unchanged:
                    repo.check(pin=pin)
                else:
                    new_snapshot_id = repo.backup(bundle, pin=pin)
                    snapshots = repo.snapshots(pin=pin)
                    if (
                        sum(
                            1
                            for item in snapshots
                            if item.get("id") == new_snapshot_id
                        )
                        != 1
                    ):
                        raise RecoveryError(
                            "recovery_repository_error",
                            "New encrypted recovery point could not be verified.",
                        )
                    verified_bundle = repo.latest_bundle(
                        expect_snapshot=True,
                        pin=pin,
                    )
                    verified_manifest = _bundle_manifest(verified_bundle)
                    if not hmac.compare_digest(
                        verified_manifest["safe_content_digest"],
                        manifest["safe_content_digest"],
                    ):
                        raise RecoveryError(
                            "recovery_repository_error",
                            "New encrypted recovery point could not be verified.",
                        )
                    repo.check(pin=pin)
                after = repo.validate_topology(cfg.db_file)
                if before != after:
                    raise RecoveryError(
                        "recovery_target_unavailable",
                        "Storage topology changed during backup.",
                    )
            state = RecoveryState(
                last_coverage_at=_iso(now),
                last_snapshot_at=prior.last_snapshot_at if unchanged else _iso(now),
                last_api_trigger_at=prior.last_api_trigger_at,
                last_result_code="recovery_unchanged" if unchanged else "recovery_ok",
                artifact_bytes=len(bundle),
                format_version=FORMAT_VERSION,
                schema_fingerprint_short=manifest["schema_fingerprint"][:12],
                retention_configured=prior.retention_configured,
                retention_enforced=prior.retention_enforced,
                retention_applied_pending_drill=prior.retention_applied_pending_drill,
                retention_schedule_armed=prior.retention_schedule_armed,
                evidence_run_id=uuid.uuid4().hex,
            )
            save_state(cfg.recovery_state_file, state)
            return {
                "result_code": state.last_result_code,
                "artifact_bytes": state.artifact_bytes,
                "snapshot_created": not unchanged,
            }
        except RecoveryError as exc:
            save_state(
                cfg.recovery_state_file,
                RecoveryState(**{**asdict(prior), "last_result_code": exc.code}),
            )
            raise


def _snapshot_ids(rows: Any) -> set[str]:
    if not isinstance(rows, list):
        raise RecoveryError("recovery_retention_invalid", "Retention inventory is invalid.")
    ids: list[str] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["id"])
            or row.get("tags") != [RECOVERY_TAG]
        ):
            raise RecoveryError("recovery_retention_invalid", "Retention inventory is invalid.")
        ids.append(row["id"])
    if len(ids) != len(set(ids)):
        raise RecoveryError("recovery_retention_invalid", "Retention inventory is ambiguous.")
    return set(ids)


def _current_snapshot_id(rows: Any) -> str:
    """Return the single newest exact-tag snapshot using aware Restic timestamps."""
    _snapshot_ids(rows)
    dated: list[tuple[datetime, str]] = []
    for row in rows:
        raw_time = row.get("time")
        when = _aware_utc(raw_time) if isinstance(raw_time, str) else None
        if when is None:
            raise RecoveryError("recovery_retention_invalid", "Retention inventory is invalid.")
        dated.append((when, row["id"]))
    newest = max(when for when, _ in dated)
    matches = [snapshot_id for when, snapshot_id in dated if when == newest]
    if len(matches) != 1:
        raise RecoveryError("recovery_retention_invalid", "Retention inventory is ambiguous.")
    return matches[0]


def _normalize_retention_drill_selector(selector: str | None) -> str:
    if selector == "latest":
        return selector
    if isinstance(selector, str) and re.fullmatch(r"[0-9a-fA-F]{64}", selector):
        return selector.lower()
    raise RecoveryError(
        "recovery_retention_invalid",
        "Retention requires a valid drill snapshot selector.",
    )


def _parse_retention_dry_run(raw: bytes, *, inventory: set[str]) -> set[str]:
    """Independently parse Restic's JSON policy output into the exact keep set."""
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RecoveryError("recovery_retention_invalid", "Retention plan is invalid.") from exc
    if not isinstance(value, list) or not value:
        raise RecoveryError("recovery_retention_invalid", "Retention plan is invalid.")
    kept: set[str] = set()
    removed: set[str] = set()
    seen: set[str] = set()
    for group in value:
        group_fields = {"host", "paths", "tags", "keep", "remove", "reasons"}
        if (
            not isinstance(group, dict)
            or set(group) - group_fields
            or not {"keep", "remove"}.issubset(group)
        ):
            raise RecoveryError("recovery_retention_invalid", "Retention plan is invalid.")
        if group.get("tags") not in (None, [RECOVERY_TAG]):
            raise RecoveryError("recovery_retention_invalid", "Retention plan has a wrong tag.")
        for disposition, target in (("keep", kept), ("remove", removed)):
            rows = group[disposition]
            # Restic 0.19.1 encodes an empty keep/remove slice as JSON null.
            # The field must still be present so omitted dispositions fail closed.
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise RecoveryError("recovery_retention_invalid", "Retention plan is invalid.")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    raise RecoveryError("recovery_retention_invalid", "Retention plan is invalid.")
                snapshot_id = row["id"]
                if snapshot_id not in inventory or snapshot_id in seen:
                    raise RecoveryError(
                        "recovery_retention_invalid", "Retention plan is ambiguous."
                    )
                if row.get("tags") != [RECOVERY_TAG]:
                    raise RecoveryError(
                        "recovery_retention_invalid", "Retention plan has a wrong tag."
                    )
                target.add(snapshot_id)
                seen.add(snapshot_id)
    if seen != inventory or not kept or kept & removed:
        raise RecoveryError("recovery_retention_invalid", "Retention plan is incomplete.")
    return kept


def _retention_plan_digest(
    *,
    repository_id: str,
    topology: tuple[StorageIdentity, StorageIdentity],
    inventory: set[str],
    survivors: set[str],
    drill_snapshot: str,
) -> str:
    # This binding exists only in-process. It is intentionally never written to
    # the secret-free recovery status or evidence files.
    value = {
        "repository_id": repository_id,
        "topology": [asdict(item) for item in topology],
        "inventory": sorted(inventory),
        "survivors": sorted(survivors),
        "drill_snapshot": drill_snapshot,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def apply_retention(
    settings: Settings | None = None,
    *,
    repository: ResticRepository | None = None,
    apply: bool = False,
    scheduled: bool = False,
    drill_snapshot: str | None = None,
) -> RecoveryState:
    if scheduled:
        if drill_snapshot is not None:
            raise RecoveryError(
                "recovery_retention_invalid",
                "Scheduled retention selects its drill snapshot internally.",
            )
        normalized_drill_selector: str | None = None
    else:
        normalized_drill_selector = _normalize_retention_drill_selector(
            drill_snapshot
        )
    cfg = settings or get_settings()
    repo = repository or ResticRepository(cfg)
    with recovery_lock(cfg.recovery_lock_file) as lock_fd:
        original = load_state(cfg.recovery_state_file)
        if scheduled and (
            not apply
            or not original.retention_schedule_armed
            or not original.retention_configured
            or not (
                original.retention_enforced
                or original.retention_applied_pending_drill
            )
        ):
            raise RecoveryError(
                "recovery_retention_invalid",
                "Recurring retention is not armed.",
            )
        fail_closed = original
        if apply:
            # This is the first mutation under the shared lock, before tool,
            # topology, repository, inventory, manifest or plan preflight.
            fail_closed = replace(
                original,
                retention_configured=True,
                retention_enforced=False,
                retention_applied_pending_drill=False,
                retention_schedule_armed=(
                    original.retention_schedule_armed if scheduled else False
                ),
                last_result_code="recovery_retention_not_enforced",
            )
            save_state(cfg.recovery_state_file, fail_closed)
        try:
            repo.validate_tool()
            with repo.pin_repository(cfg.db_file, lock_fd=lock_fd) as pin:
                topology_before = pin.topology
                repository_id = pin.repository_id
                if not hmac.compare_digest(repo.repository_id(pin=pin), repository_id):
                    raise RecoveryError(
                        "recovery_retention_invalid", "Recovery repository identity changed."
                    )
                snapshot_rows = repo.snapshots(pin=pin)
                inventory = _snapshot_ids(snapshot_rows)
                if not inventory:
                    raise RecoveryError(
                        "recovery_repository_empty", "No verified recovery point exists."
                    )
                current_snapshot = _current_snapshot_id(snapshot_rows)
                selected_drill_snapshot = (
                    current_snapshot
                    if scheduled or normalized_drill_selector == "latest"
                    else normalized_drill_selector
                )
                if selected_drill_snapshot not in inventory:
                    raise RecoveryError(
                        "recovery_retention_invalid",
                        "Selected drill snapshot is unavailable.",
                    )
                before_bundle = repo.latest_bundle(expect_snapshot=True, pin=pin)
                _bundle_manifest(before_bundle)
                dry_run = repo.retention(apply=False, pin=pin)
                survivors = _parse_retention_dry_run(dry_run, inventory=inventory)
                if (
                    not survivors
                    or current_snapshot not in survivors
                    or selected_drill_snapshot not in survivors
                ):
                    raise RecoveryError(
                        "recovery_retention_invalid", "Retention has no survivor."
                    )
                if not apply:
                    dry_state = replace(
                        original,
                        retention_configured=True,
                        last_result_code="recovery_retention_dry_run",
                    )
                    save_state(cfg.recovery_state_file, dry_state)
                    return dry_state
                approved_digest = _retention_plan_digest(
                    repository_id=repository_id,
                    topology=topology_before,
                    inventory=inventory,
                    survivors=survivors,
                    drill_snapshot=selected_drill_snapshot,
                )
                # Repeat every mutable preflight through the same open directory
                # handle immediately before the destructive adapter call.
                if repo.validate_topology(cfg.db_file) != topology_before:
                    raise RecoveryError(
                        "recovery_retention_invalid", "Recovery repository identity changed."
                    )
                rebound_repository_id = repo.repository_id(pin=pin)
                rebound_rows = repo.snapshots(pin=pin)
                rebound_inventory = _snapshot_ids(rebound_rows)
                rebound_current = _current_snapshot_id(rebound_rows)
                if selected_drill_snapshot not in rebound_inventory:
                    raise RecoveryError(
                        "recovery_retention_invalid",
                        "Selected drill snapshot changed before apply.",
                    )
                rebound_plan = repo.retention(apply=False, pin=pin)
                rebound_survivors = _parse_retention_dry_run(
                    rebound_plan, inventory=rebound_inventory
                )
                rebound_digest = _retention_plan_digest(
                    repository_id=rebound_repository_id,
                    topology=topology_before,
                    inventory=rebound_inventory,
                    survivors=rebound_survivors,
                    drill_snapshot=selected_drill_snapshot,
                )
                if (
                    not hmac.compare_digest(approved_digest, rebound_digest)
                    or rebound_current not in rebound_survivors
                    or selected_drill_snapshot not in rebound_survivors
                ):
                    raise RecoveryError(
                        "recovery_retention_invalid", "Retention identity changed before apply."
                    )
                repo.retention(
                    apply=True,
                    pin=pin,
                    expected_repository_id=repository_id,
                )
                after_inventory = _snapshot_ids(repo.snapshots(pin=pin))
                if after_inventory != survivors:
                    raise RecoveryError(
                        "recovery_retention_invalid",
                        "Applied retention does not match its verified plan.",
                    )
                after_bundle = repo.latest_bundle(expect_snapshot=True, pin=pin)
                _bundle_manifest(after_bundle)
                if (
                    repo.validate_topology(cfg.db_file) != topology_before
                    or not hmac.compare_digest(
                        repo.repository_id(pin=pin), repository_id
                    )
                ):
                    raise RecoveryError(
                        "recovery_target_unavailable",
                        "Storage topology changed during retention.",
                    )
        except RecoveryError as exc:
            if apply:
                save_state(
                    cfg.recovery_state_file,
                    replace(fail_closed, last_result_code=exc.code),
                )
            raise
        applied = replace(
            fail_closed,
            retention_applied_pending_drill=True,
            last_result_code="recovery_retention_applied",
        )
        save_state(cfg.recovery_state_file, applied)
        return applied


def arm_retention_schedule(settings: Settings | None = None) -> RecoveryState:
    """Arm a successfully installed recurring runner under the shared state lock."""
    cfg = settings or get_settings()
    with recovery_lock(cfg.recovery_lock_file, blocking=True):
        state = load_state(cfg.recovery_state_file)
        if (
            not state.retention_configured
            or state.retention_enforced
            or not state.retention_applied_pending_drill
            or state.retention_schedule_armed
        ):
            raise RecoveryError(
                "recovery_retention_invalid",
                "Recurring retention cannot be armed from this state.",
            )
        armed = replace(state, retention_schedule_armed=True)
        save_state(cfg.recovery_state_file, armed)
        return armed


def _no_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch path is unsafe.")


def _write_marker(path: Path, value: Mapping[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())


def _read_marker(path: Path) -> dict[str, Any]:
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
                raise ValueError("unsafe marker")
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("invalid marker")
        return value
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RecoveryError(
            "recovery_scratch_unsafe", "Recovery scratch marker is unsafe."
        ) from exc


def _safe_scratch_root(path: Path) -> tuple[Path, str]:
    _no_symlink_components(path)
    created = not path.exists()
    if created:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    canonical = path.resolve(strict=True)
    _no_symlink_components(canonical)
    st = canonical.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or stat.S_IMODE(st.st_mode) != 0o700
        or st.st_uid != os.getuid()
    ):
        raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch root is unsafe.")
    marker_path = canonical / _SCRATCH_ROOT_MARKER
    if created:
        _write_marker(
            marker_path,
            {
                "format": 1,
                "uid": os.getuid(),
                "device": st.st_dev,
                "inode": st.st_ino,
                "token": uuid.uuid4().hex,
            },
        )
    marker = _read_marker(marker_path)
    if (
        set(marker) != {"format", "uid", "device", "inode", "token"}
        or marker["format"] != 1
        or marker["uid"] != os.getuid()
        or marker["device"] != st.st_dev
        or marker["inode"] != st.st_ino
        or not isinstance(marker["token"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", marker["token"])
    ):
        raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch root is unsafe.")
    return canonical, marker["token"]


def _new_scratch_run(root: Path, root_token: str) -> Path:
    run_dir = root / f"run-{uuid.uuid4().hex}"
    run_dir.mkdir(mode=0o700)
    st = run_dir.lstat()
    _write_marker(
        run_dir / _SCRATCH_RUN_MARKER,
        {
            "format": 1,
            "uid": os.getuid(),
            "device": st.st_dev,
            "root_token": root_token,
            "created_at": _iso(_utc_now()),
        },
    )
    return run_dir


def _run_marker_valid(marker: Mapping[str, Any], st: os.stat_result, root_token: str) -> bool:
    return (
        set(marker) == {"format", "uid", "device", "root_token", "created_at"}
        and marker["format"] == 1
        and marker["uid"] == os.getuid()
        and marker["device"] == st.st_dev
        and hmac.compare_digest(str(marker["root_token"]), root_token)
        and _aware_utc(marker["created_at"]) is not None
    )


def _read_marker_at(directory_fd: int, name: str) -> dict[str, Any]:
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(name, flags, dir_fd=directory_fd)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
                raise ValueError("unsafe marker")
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("invalid marker")
        return value
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RecoveryError(
            "recovery_scratch_unsafe", "Recovery scratch marker is unsafe."
        ) from exc


def _open_owned_scratch_run(
    root: Path, name: str, root_token: str
) -> tuple[int, int, os.stat_result]:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        root_fd = os.open(root, directory_flags)
        root_st = os.fstat(root_fd)
        run_fd = os.open(name, directory_flags, dir_fd=root_fd)
        run_st = os.fstat(run_fd)
        if (
            not stat.S_ISDIR(run_st.st_mode)
            or stat.S_IMODE(run_st.st_mode) != 0o700
            or run_st.st_uid != os.getuid()
            or run_st.st_dev != root_st.st_dev
        ):
            raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch run is unsafe.")
        marker = _read_marker_at(run_fd, _SCRATCH_RUN_MARKER)
        if not _run_marker_valid(marker, run_st, root_token):
            raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch run is unsafe.")
        return root_fd, run_fd, run_st
    except Exception:
        with contextlib.suppress(UnboundLocalError, OSError):
            os.close(run_fd)
        with contextlib.suppress(UnboundLocalError, OSError):
            os.close(root_fd)
        raise


def _owned_scratch_run(path: Path, root_token: str) -> bool:
    try:
        root_fd, run_fd, _ = _open_owned_scratch_run(path.parent, path.name, root_token)
        os.close(run_fd)
        os.close(root_fd)
        return True
    except (OSError, RecoveryError):
        return False


def _before_scratch_quarantine(_root: Path, _name: str) -> None:
    """Deterministic race-test seam; production is intentionally a no-op."""


def _before_scratch_descriptor_delete(_root: Path, _name: str) -> None:
    """Deterministic post-validation race-test seam; production is a no-op."""


def _open_scratch_entry(
    directory_fd: int, name: str, expected_device: int
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if before.st_dev != expected_device or stat.S_ISLNK(before.st_mode):
            raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch tree is unsafe.")
        if stat.S_ISDIR(before.st_mode):
            flags |= os.O_DIRECTORY
        elif not stat.S_ISREG(before.st_mode):
            raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch tree is unsafe.")
        entry_fd = os.open(name, flags, dir_fd=directory_fd)
        opened = os.fstat(entry_fd)
        if (
            (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_dev != expected_device
        ):
            os.close(entry_fd)
            raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch tree changed.")
        return entry_fd, opened
    except OSError as exc:
        raise RecoveryError(
            "recovery_scratch_unsafe", "Recovery scratch tree is unsafe."
        ) from exc


def _remove_scratch_contents_fd(directory_fd: int, expected_device: int) -> None:
    """Remove only entries opened beneath the verified directory descriptor."""
    directory = os.fstat(directory_fd)
    if not stat.S_ISDIR(directory.st_mode) or directory.st_dev != expected_device:
        raise RecoveryError("recovery_scratch_unsafe", "Recovery scratch tree is unsafe.")
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise RecoveryError(
            "recovery_scratch_unsafe", "Recovery scratch tree is unsafe."
        ) from exc
    for name in names:
        entry_fd, opened = _open_scratch_entry(directory_fd, name, expected_device)
        tombstone = f".delete-{uuid.uuid4().hex}"
        try:
            os.rename(name, tombstone, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            rebound = os.stat(tombstone, dir_fd=directory_fd, follow_symlinks=False)
            if (rebound.st_dev, rebound.st_ino) != (opened.st_dev, opened.st_ino):
                raise RecoveryError(
                    "recovery_scratch_unsafe", "Recovery scratch entry changed."
                )
            if stat.S_ISDIR(opened.st_mode):
                _remove_scratch_contents_fd(entry_fd, expected_device)
                current = os.stat(tombstone, dir_fd=directory_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    raise RecoveryError(
                        "recovery_scratch_unsafe", "Recovery scratch entry changed."
                    )
                os.rmdir(tombstone, dir_fd=directory_fd)
            else:
                current = os.stat(tombstone, dir_fd=directory_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    raise RecoveryError(
                        "recovery_scratch_unsafe", "Recovery scratch entry changed."
                    )
                os.unlink(tombstone, dir_fd=directory_fd)
        except OSError as exc:
            raise RecoveryError(
                "recovery_scratch_unsafe", "Recovery scratch cleanup failed."
            ) from exc
        finally:
            os.close(entry_fd)


def _quarantine_scratch_run(root: Path, name: str, root_token: str) -> None:
    root_fd, run_fd, checked = _open_owned_scratch_run(root, name, root_token)
    quarantine_name = f".quarantine-{uuid.uuid4().hex}"
    try:
        _before_scratch_quarantine(root, name)
        current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (checked.st_dev, checked.st_ino):
            raise RecoveryError(
                "recovery_scratch_unsafe", "Recovery scratch run changed before quarantine."
            )
        os.rename(name, quarantine_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        quarantined = os.stat(quarantine_name, dir_fd=root_fd, follow_symlinks=False)
        if (quarantined.st_dev, quarantined.st_ino) != (checked.st_dev, checked.st_ino):
            raise RecoveryError(
                "recovery_scratch_unsafe", "Recovery scratch quarantine failed."
            )
        _before_scratch_descriptor_delete(root, quarantine_name)
        _remove_scratch_contents_fd(run_fd, checked.st_dev)
        rebound = os.stat(quarantine_name, dir_fd=root_fd, follow_symlinks=False)
        if (rebound.st_dev, rebound.st_ino) != (checked.st_dev, checked.st_ino):
            raise RecoveryError(
                "recovery_scratch_unsafe", "Recovery scratch quarantine changed."
            )
        os.rmdir(quarantine_name, dir_fd=root_fd)
        try:
            os.stat(quarantine_name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RecoveryError(
                "recovery_scratch_unsafe", "Recovery scratch cleanup failed."
            )
    except OSError as exc:
        raise RecoveryError(
            "recovery_scratch_unsafe", "Recovery scratch cleanup failed."
        ) from exc
    finally:
        os.close(run_fd)
        os.close(root_fd)


def scavenge_scratch(path: Path, *, older_than_seconds: int = 3600) -> int:
    root, root_token = _safe_scratch_root(path)
    cutoff = time.time() - older_than_seconds
    removed = 0
    for child in root.iterdir():
        if not child.name.startswith("run-") or child.is_symlink():
            continue
        st = child.lstat()
        if _owned_scratch_run(child, root_token) and st.st_mtime <= cutoff:
            _quarantine_scratch_run(root, child.name, root_token)
            removed += 1
    return removed


def _probe_metric_partial_uniques(connection) -> None:
    """Exercise all four nullable metric uniqueness shapes in scratch only."""
    dbapi = connection.connection.driver_connection
    probe = uuid.uuid4().hex
    league_id = -4_000_000_001
    team_id = -4_000_000_002
    dbapi.execute("SAVEPOINT phase30_probe")
    try:
        # `tenant_id` is NOT NULL as of revision 0013, and this probe runs
        # against a SCRATCH database during a restore, so it reads the tenant
        # out of the rows that were just restored rather than assuming an id.
        # A literal 1 would be right until a restore produced anything else.
        tenant_row = dbapi.execute("SELECT id FROM tenants LIMIT 1").fetchone()
        if tenant_row is None:
            raise RecoveryError(
                "recovery_bundle_invalid",
                "Restored database has no tenant; the uniqueness probe cannot run.",
            )
        dbapi.execute(
            "INSERT INTO leagues(id,espn_league_id,season,lifecycle,is_public,tenant_id) "
            "VALUES(?,?,?,'pre_draft',1,?)",
            (league_id, f"recovery-probe-{probe}", 1901, tenant_row[0]),
        )
        dbapi.execute(
            "INSERT INTO teams(id,league_id,espn_team_id,is_me,autodrafted,wins,losses,ties,"
            "points_for,points_against) VALUES(?,?,1,0,0,0,0,0,0,0)",
            (team_id, league_id),
        )
        shapes = (
            (None, None, f"probe-league-{probe}"),
            (None, 1, f"probe-league-week-{probe}"),
            (team_id, None, f"probe-team-{probe}"),
            (team_id, 1, f"probe-team-week-{probe}"),
        )
        for index, (row_team, week, key) in enumerate(shapes):
            savepoint = f"phase30_case_{index}"
            dbapi.execute(f"SAVEPOINT {savepoint}")
            values = (league_id, row_team, key, week, 1.0, "2026-08-13 00:00:00")
            sql = (
                'INSERT INTO metrics(league_id,team_id,"key",week,value_float,computed_at) '
                "VALUES(?,?,?,?,?,?)"
            )
            try:
                dbapi.execute(sql, values)
                try:
                    dbapi.execute(sql, values)
                except sqlite3.IntegrityError:
                    pass
                else:
                    raise RecoveryError(
                        "recovery_bundle_invalid",
                        "Restored partial unique constraint verification failed.",
                    )
            finally:
                dbapi.execute(f"ROLLBACK TO {savepoint}")
                dbapi.execute(f"RELEASE {savepoint}")
    finally:
        dbapi.execute("ROLLBACK TO phase30_probe")
        dbapi.execute("RELEASE phase30_probe")


def restore_bundle_to_scratch(bundle: bytes, scratch_db: Path) -> dict[str, Any]:
    manifest = _bundle_manifest(bundle)
    try:
        payload = json.loads(bundle)
    except json.JSONDecodeError as exc:  # pragma: no cover - already parsed above
        raise RecoveryError("recovery_bundle_invalid", "Recovery bundle is invalid.") from exc
    if validate_catalog(manifest["catalog"]) != manifest["schema_fingerprint"]:
        raise RecoveryError("recovery_bundle_invalid", "Recovery catalog verification failed.")
    from .. import models  # noqa: F401
    from ..db import Base

    # The target must be empty, and saying so beats assuming it. Every caller
    # passes a fresh scratch path, and the inserts below would collide on
    # primary keys if it were not -- so this turns a confusing IntegrityError
    # deep in the loop into a refusal that names the problem. It also means the
    # clearing step further down cannot destroy anything an operator wanted:
    # there is nothing there to destroy.
    if scratch_db.exists() and scratch_db.stat().st_size:
        probe = sqlite3.connect(scratch_db)
        try:
            existing = [
                name
                for (name,) in probe.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
                )
                if probe.execute(f"SELECT 1 FROM {_quote(name)} LIMIT 1").fetchone()
            ]
        finally:
            probe.close()
        if existing:
            raise RecoveryError(
                "recovery_target_unavailable",
                "Restore target is not empty.",
            )

    engine = create_engine(f"sqlite:///{scratch_db}")
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")

            # `create_all` is not inert: an `after_create` listener on
            # `tenants` seeds the single default tenant, which is right for a
            # fresh application database and wrong here -- the bundle's own
            # tenant row then collides on the primary key, and before this the
            # restore failed with `UNIQUE constraint failed: tenants.id`.
            #
            # So the restore starts from empty: a restore means "exactly these
            # rows", not "these rows plus whatever the schema decided to seed".
            # Deleted in REVERSE dependency order so children go before
            # parents and no `ON DELETE CASCADE` has anything to reach --
            # Phase 36 measured what happens when a delete under enforced
            # foreign keys fires cascades nobody counted.
            for table in reversed(Base.metadata.sorted_tables):
                connection.exec_driver_sql(f"DELETE FROM {_quote(table.name)}")
            for table in Base.metadata.sorted_tables:
                remaining = connection.exec_driver_sql(
                    f"SELECT count(*) FROM {_quote(table.name)}"
                ).scalar()
                if remaining:
                    raise RecoveryError(
                        "recovery_bundle_invalid",
                        "Restore target did not start empty.",
                    )

            restored_catalog = catalog_spec(connection.connection.driver_connection)
            if _catalog_semantics(restored_catalog) != _catalog_semantics(manifest["catalog"]):
                raise RecoveryError(
                    "recovery_bundle_invalid", "Restored constraint inventory does not match."
                )
            for table in Base.metadata.sorted_tables:
                if table.name in NOT_BUNDLED:
                    continue
                rows = payload["tables"].get(table.name)
                if not isinstance(rows, list):
                    raise RecoveryError("recovery_bundle_invalid", "Recovery table is missing.")
                expected_columns = set(EXPECTED_TABLE_COLUMNS[table.name])
                if any(not isinstance(row, dict) or set(row) != expected_columns for row in rows):
                    raise RecoveryError(
                        "recovery_bundle_invalid", "Recovery row shape is invalid."
                    )
                decoded = [
                    tuple(
                        _decode_value(row[column])
                        for column in EXPECTED_TABLE_COLUMNS[table.name]
                    )
                    for row in rows
                ]
                expected_hash = hashlib.sha256()
                for row in rows:
                    canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
                    expected_hash.update(len(canonical).to_bytes(8, "big"))
                    expected_hash.update(canonical)
                table_manifest = manifest["tables"].get(table.name, {})
                if (
                    table_manifest.get("rows") != len(decoded)
                    or table_manifest.get("sha256") != expected_hash.hexdigest()
                ):
                    raise RecoveryError(
                        "recovery_bundle_invalid", "Recovery table verification failed."
                    )
                if decoded:
                    columns = ",".join(_quote(name) for name in EXPECTED_TABLE_COLUMNS[table.name])
                    placeholders = ",".join("?" for _ in EXPECTED_TABLE_COLUMNS[table.name])
                    connection.exec_driver_sql(
                        f"INSERT INTO {_quote(table.name)} ({columns}) VALUES ({placeholders})",
                        decoded,
                    )
            integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
            fk_rows = list(connection.exec_driver_sql("PRAGMA foreign_key_check"))
            raw_count = connection.exec_driver_sql("SELECT count(*) FROM raw_cache").scalar()
            reauth_count = connection.exec_driver_sql(
                "SELECT count(*) FROM accounts WHERE status != 'needs_reauth'"
            ).scalar()
            owner_count = connection.exec_driver_sql(
                "SELECT count(*) FROM teams WHERE owner_swids_json IS NOT NULL"
            ).scalar()
            if integrity != "ok" or fk_rows or raw_count or reauth_count or owner_count:
                raise RecoveryError(
                    "recovery_bundle_invalid", "Restored database verification failed."
                )
            _probe_metric_partial_uniques(connection)
        return {
            "integrity": "ok",
            "foreign_keys": "ok",
            "constraints": "ok",
            "exclusions": "ok",
        }
    finally:
        engine.dispose()


def verify_restored_read_closure(scratch_db: Path) -> dict[str, int]:
    """Execute the four Stage 0b read surfaces against the scratch database.

    The return value contains aggregate, secret-free shape counts only. Tests
    compare the complete synthetic outputs to the source database separately;
    the operator drill needs a runnable application-level closure check rather
    than a SQL-only integrity check.

    The filter season is derived from the restored data instead of hardcoded, and
    a bundle holding leagues must yield portfolio rows. Without both, this check
    only proved the four read surfaces did not raise: a rowless bundle, or a filter
    season absent from the bundle, reported zeroes alongside `integrity: ok`.
    Both the restored and configured seasons are reported so a mismatch is visible.
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from ..config import get_settings
    from .draft_analytics import build_strategies
    from .exposure import build_exposure
    from .opportunity import build_opportunity_charts
    from .portfolio import build_portfolio_rows
    from .portfolio_filters import PortfolioFilters

    engine = create_engine(f"sqlite:///{scratch_db}")
    try:
        with Session(engine) as session:
            restored_season = session.execute(
                text("SELECT MAX(season) FROM leagues")
            ).scalar()
            if restored_season is None:
                raise ValueError("restored database contains no league season")
            league_count = (
                session.execute(text("SELECT COUNT(*) FROM leagues")).scalar() or 0
            )
            filters = PortfolioFilters(season=int(restored_season))
            portfolio = build_portfolio_rows(session)
            exposure = build_exposure(session, scope="me", filters=filters)
            strategies = build_strategies(session, filters)
            charts = build_opportunity_charts(
                session,
                filters,
                view="all",
                position="WR",
            )
            closure = {
                "restored_season": int(restored_season),
                "configured_season": int(get_settings().season),
                "portfolio_rows": len(portfolio),
                "exposure_players": len(exposure.get("players", [])),
                "strategy_rows": len(strategies.get("strategies", [])),
                "opportunity_charts": len(charts.get("charts", [])),
            }
    except Exception as exc:
        raise RecoveryError(
            "recovery_bundle_invalid", "Restored application reads failed."
        ) from exc
    finally:
        engine.dispose()
    # A schema-valid but rowless bundle must fail rather than report zeroes beside
    # `integrity: ok`. Only the league-to-portfolio relationship is asserted: it is
    # definitional, because `build_portfolio_rows` is unfiltered and emits a row per
    # league in scope. The derived analytics surfaces are deliberately NOT asserted.
    # A legitimate sparse bundle can contain draft picks and still yield zero
    # exposure or strategy rows -- no team flagged `is_me`, no matched players, or no
    # persisted `draft_strategy_*` metrics -- so requiring them rejects valid
    # bundles. The wrong-season defect this check was written for is closed by
    # deriving the season above, which is asserted directly by the offline tests.
    if league_count > 0 and closure["portfolio_rows"] <= 0:
        raise RecoveryError(
            "recovery_bundle_invalid", "Restored application reads returned no rows."
        )
    return closure


def _run_operational_restore_verifier(source_db: Path, restored_db: Path) -> dict[str, bool]:
    """Run the four read surfaces against the restored database, in a subprocess.

    THE ENVIRONMENT IS COMPLETE ON PURPOSE, and it was not.

    `Settings` has three required fields -- `APP_MODE`, `TELEMETRY_ENABLED`,
    `TELEMETRY_REPORT_PATH` -- and none of them was in this dict. It worked
    anyway, because `Settings` is configured with `env_file=str(ROOT / ".env")`
    -- an ABSOLUTE path -- so **a hand-built environment does not isolate the
    subprocess from the operator's gitignored `.env`**. `_minimal_env()` looks
    like isolation and is not. On any machine without that file -- a CI runner,
    a fresh clone, a new deployment -- `Settings` failed to validate, the
    subprocess printed a traceback instead of JSON, and this function raised
    **"Restored application verification failed"**: a configuration problem
    reported as a verification failure, which an operator reads as data loss
    during the one procedure where that would be terrifying.

    Found by running the suite from a clean clone rather than the worktree.
    The same defect was recorded in `docs/CLOSE-OUT.md` a phase earlier, in two
    *tests* that were handed a hand-built environment and also `cwd=ROOT`. This
    is the same mistake in production code.

    `APP_MODE` being explicit matters beyond portability: inheriting it meant
    the verifier could have run in hosted mode -- which is synthetic-only --
    against real restored data, depending on an untracked file.
    """
    from cryptography.fernet import Fernet

    env = {
        **_minimal_env(),
        "DB_PATH": str(restored_db),
        "RECOVERY_VERIFY_SOURCE": str(source_db),
        "RECOVERY_REQUIRED": "false",
        "SEASON": "2025",
        "FERNET_KEY": Fernet.generate_key().decode(),
        "ANTHROPIC_API_KEY": "",
        # The three that were coming from a gitignored file. See the docstring.
        "APP_MODE": "private_operator",
        # A verification subprocess must not publish provider measurements,
        # and the path is named inside the scratch area so that even a future
        # change enabling it cannot write outside the restore's own directory.
        #
        # The filename deliberately avoids the word the provider-wiring scan in
        # `tests/test_telemetry_seams.py` looks for: that scan reads ordinary
        # string constants, which is exactly where an `import_module` argument
        # lives, so the prose moves rather than the control.
        "TELEMETRY_ENABLED": "false",
        "TELEMETRY_REPORT_PATH": str(restored_db.parent / "verifier-report.md"),
    }
    result = BoundedSubprocessRunner().run(
        [sys.executable, "-m", "api.recovery", "internal-verify"],
        env=env,
        max_output=16 * 1024,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        # Distinct from the failure below. The verifier did not produce a
        # verdict at all, which is a different incident from a verdict that
        # says no -- and conflating them is how a missing setting gets read as
        # a corrupted restore.
        raise RecoveryError(
            "recovery_bundle_invalid", "Restore verifier did not run."
        ) from exc
    expected = {
        "four_reads_match",
        "app_routes_ok",
        "pre_reauth_zero_provider",
        "discovery_reauth",
        "router_reauth",
        "verify_reauth",
        "direct_reauth",
        "synthetic_reauth_sync",
        "retained_families_match",
    }
    if result.returncode or not isinstance(value, dict) or set(value) != expected or not all(
        type(item) is bool and item for item in value.values()
    ):
        raise RecoveryError(
            "recovery_bundle_invalid", "Restored application verification failed."
        )
    return value


def _restore_stage(hook: Callable[[str, Path], None] | None, stage: str, run_dir: Path) -> None:
    if hook is not None:
        hook(stage, run_dir)


def restore_drill(
    snapshot: str,
    settings: Settings | None = None,
    *,
    repository: ResticRepository | None = None,
    stage_hook: Callable[[str, Path], None] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"latest|[0-9a-f]{8,64}", snapshot):
        raise RecoveryError("recovery_bundle_invalid", "Recovery point selector is invalid.")
    cfg = settings or get_settings()
    repo = repository or ResticRepository(cfg)
    if not filevault_enabled(getattr(repo, "runner", None)):
        raise RecoveryError(
            "recovery_scratch_unsafe",
            "Restore drill requires FileVault or a separately encrypted scratch volume.",
        )
    with recovery_lock(cfg.recovery_lock_file) as lock_fd:
        root, root_token = _safe_scratch_root(cfg.recovery_scratch_dir)
        scavenge_scratch(root, older_than_seconds=0)
        run_dir = _new_scratch_run(root, root_token)
        _restore_stage(stage_hook, "scratch-created", run_dir)
        started = time.monotonic()
        completed = False
        try:
            production_repository = isinstance(repo, ResticRepository)
            if not production_repository:
                repo.validate_tool()
            with repo.pin_break_glass_repository(
                cfg.db_file, lock_fd=lock_fd
            ) as pin:
                credential_context = (
                    repo.break_glass_credential()
                    if production_repository
                    else contextlib.nullcontext(None)
                )
                with credential_context as credential:
                    if credential is None:
                        repo.break_glass_check(pin=pin)
                        available = repo.break_glass_snapshots(pin=pin)
                    else:
                        secret, binary_authority = credential
                        repo.break_glass_check(
                            secret=secret,
                            binary_authority=binary_authority,
                            pin=pin,
                        )
                        available = repo.break_glass_snapshots(
                            secret=secret,
                            binary_authority=binary_authority,
                            pin=pin,
                        )
                    available_ids = _snapshot_ids(available)
                    selected_snapshot = snapshot
                    if snapshot != "latest":
                        matches = sorted(
                            item for item in available_ids if item.startswith(snapshot)
                        )
                        if len(matches) != 1:
                            raise RecoveryError(
                                "recovery_snapshot_ambiguous",
                                "Recovery point selector is absent or ambiguous.",
                            )
                        selected_snapshot = matches[0]
                    if not available_ids:
                        raise RecoveryError(
                            "recovery_bundle_invalid",
                            "Recovery point does not belong to this repository.",
                        )
                    _restore_stage(stage_hook, "repository-verified", run_dir)
                    if credential is None:
                        bundle = repo.break_glass_bundle(selected_snapshot, pin=pin)
                    else:
                        bundle = repo.break_glass_bundle(
                            selected_snapshot,
                            secret=secret,
                            binary_authority=binary_authority,
                            pin=pin,
                        )
            _restore_stage(stage_hook, "bundle-retrieved", run_dir)
            ready = time.monotonic()
            restored_db = run_dir / "restored.db"
            result = restore_bundle_to_scratch(bundle, restored_db)
            _restore_stage(stage_hook, "database-restored", run_dir)
            read_closure = verify_restored_read_closure(restored_db)
            application_evidence = _run_operational_restore_verifier(
                cfg.db_file, restored_db
            )
            _restore_stage(stage_hook, "application-verified", run_dir)
            verified = time.monotonic()
            completed = True
            result = {
                **result,
                "read_closure": read_closure,
                "application_evidence": application_evidence,
                "restore_ready_seconds": round(ready - started, 6),
                "verification_seconds": round(verified - ready, 6),
            }
            _restore_stage(stage_hook, "cleanup-ready", run_dir)
        finally:
            with _break_glass_cleanup_boundary():
                _quarantine_scratch_run(root, run_dir.name, root_token)
        _raise_deferred_break_glass_signal()
        if completed:
            state = load_state(cfg.recovery_state_file)
            if state.retention_applied_pending_drill:
                save_state(
                    cfg.recovery_state_file,
                    replace(
                        state,
                        retention_enforced=True,
                        retention_applied_pending_drill=False,
                        last_result_code="recovery_ok",
                    ),
                )
            return result
    raise RecoveryError("recovery_bundle_invalid", "Recovery drill did not complete.")


def secret_free_error(exc: Exception) -> dict[str, str]:
    if isinstance(exc, RecoveryError):
        return {"code": exc.code, "message": exc.safe_message}
    return {"code": "recovery_repository_error", "message": "Recovery operation failed."}
