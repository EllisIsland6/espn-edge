"""Phase 27 nflverse ingestion and deterministic Opportunity Analytics read models."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import platform
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from statistics import mean, median
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    CurrentRosterEntry,
    CurrentRosterSnapshot,
    League,
    NflversePlayerMap,
    OpportunityImport,
    OpportunityWeek,
    Player,
    Team,
)
from .ffc_adp import normalize_position, normalize_team
from .portfolio_filters import PortfolioFilters, filtered_league_ids
from .recovery import assert_recovery_write_allowed

log = logging.getLogger("espn.opportunity")

ELIGIBLE_POSITIONS = {"RB", "WR", "TE"}
CHART_ESPN_RANK_LIMIT = 250
NFLVERSE_ID_OVERRIDES: dict[int, str] = {}
SOURCE_HOST = "https://github.com/nflverse/nflverse-data/releases/download"
MAX_DETAILS_BYTES = 2048
SOURCE_UNAVAILABLE_MESSAGE = (
    "NFL opportunity data is temporarily unavailable; showing the last good import."
)
DATA_CONFLICT_MESSAGE = (
    "NFL opportunity data contained conflicting player-game rows; previous data was preserved."
)
SAVE_FAILED_MESSAGE = (
    "Opportunity data downloaded but could not be saved; previous data was preserved."
)

_PLAYER_COLUMNS = {"gsis_id", "espn_id"}
_STAT_COLUMNS = {
    "season",
    "season_type",
    "week",
    "game_id",
    "player_id",
    "position",
    "team",
    "opponent_team",
    "carries",
    "targets",
    "receptions",
    "rushing_yards",
    "receiving_yards",
    "receiving_air_yards",
    "receiving_tds",
    "passing_yards",
    "target_share",
    "air_yards_share",
    "wopr",
    "rushing_epa",
    "receiving_epa",
    "fantasy_points_ppr",
}
_STORED_FLOATS = (
    "carries",
    "targets",
    "receptions",
    "rushing_yards",
    "receiving_yards",
    "receiving_air_yards",
    "receiving_tds",
    "target_share",
    "air_yards_share",
    "wopr",
    "rushing_epa",
    "receiving_epa",
    "fantasy_points_ppr",
)
_INPUT_FLOATS = (*_STORED_FLOATS, "passing_yards")


class OpportunityDataClient(Protocol):
    package_version: str
    retries: int

    def load_players(self) -> list[dict[str, Any]]: ...

    def load_player_stats(self, season: int) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class OpportunityError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retries: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retries = retries


class OpportunityBusyError(OpportunityError):
    pass


class NflverseClient:
    """Small adapter around beta nflreadpy, with bounded retries and CSV fallback."""

    def __init__(self, http: httpx.Client | None = None) -> None:
        try:
            self.package_version = version("nflreadpy")
        except PackageNotFoundError:
            self.package_version = "not-installed"
        self.retries = 0
        self._owns_http = http is None
        self.http = http or httpx.Client(timeout=30.0, follow_redirects=True)

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    @staticmethod
    def _rows(frame: Any) -> list[dict[str, Any]]:
        to_dicts = getattr(frame, "to_dicts", None)
        if not callable(to_dicts):
            raise OpportunityError(
                "OPP-SOURCE-FORMAT",
                "NFL opportunity data changed format; previous data was preserved.",
                details={"reason": "loader did not return a Polars DataFrame"},
            )
        return list(to_dicts())

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "429",
                "500",
                "502",
                "503",
                "504",
                "timed out",
                "timeout",
                "connection reset",
                "connection refused",
                "temporary failure",
            )
        )

    def _csv_fallback(self, path: str) -> list[dict[str, Any]]:
        url = f"{SOURCE_HOST}/{path}.csv"
        try:
            response = self.http.get(url)
            response.raise_for_status()
            import polars as pl

            return list(
                pl.read_csv(io.BytesIO(response.content), null_values=["NA", "NULL", ""]).to_dicts()
            )
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise OpportunityError(
                "OPP-NOT-PUBLISHED" if code == 404 else "OPP-SOURCE-UNAVAILABLE",
                "Opportunity data begins after regular-season games."
                if code == 404
                else SOURCE_UNAVAILABLE_MESSAGE,
                details={"status_code": code, "asset": path},
                retries=self.retries,
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OpportunityError(
                "OPP-SOURCE-UNAVAILABLE",
                SOURCE_UNAVAILABLE_MESSAGE,
                details={"exception": type(exc).__name__, "asset": path},
                retries=self.retries,
            ) from exc

    def _load(self, loader: Any, path: str) -> list[dict[str, Any]]:
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._rows(loader())
            except OpportunityError:
                raise
            except Exception as exc:  # nflreadpy wraps requests/polars exceptions.
                last = exc
                text = str(exc).lower()
                if "404" in text:
                    return self._csv_fallback(path)
                if not self._is_retryable(exc) or attempt == 2:
                    break
                self.retries += 1
                time.sleep(0.25 * (attempt + 1))
        raise OpportunityError(
            "OPP-SOURCE-UNAVAILABLE",
            "NFL opportunity data is temporarily unavailable; showing the last good import.",
            details={"exception": type(last).__name__ if last else "unknown", "asset": path},
            retries=self.retries,
        ) from last

    def _package(self):
        if self.package_version == "not-installed":
            raise OpportunityError(
                "OPP-SOURCE-UNAVAILABLE",
                "NFL opportunity support is not installed; showing the last good import.",
                details={"dependency": "nflreadpy"},
            )
        import nflreadpy as nfl
        from nflreadpy.config import update_config

        update_config(cache_mode="off", verbose=False, timeout=30)
        return nfl

    def load_players(self) -> list[dict[str, Any]]:
        nfl = self._package()
        return self._load(nfl.load_players, "players/players")

    def load_player_stats(self, season: int) -> list[dict[str, Any]]:
        nfl = self._package()
        return self._load(
            lambda: nfl.load_player_stats(season, summary_level="week"),
            f"stats_player/stats_player_week_{season}",
        )


@dataclass(frozen=True)
class PreparedImport:
    weeks: list[dict[str, Any]]
    maps: list[dict[str, Any]]
    input_rows: int
    matched_players: int
    unmatched_players: int
    latest_week: int | None
    schema_fingerprint: str
    details: dict[str, Any]


def _prepare_empty_import(
    session: Session,
    registry_rows: list[dict[str, Any]],
    details: dict[str, Any] | None = None,
) -> PreparedImport:
    columns = _validate_columns(registry_rows, _PLAYER_COLUMNS, "players/players")
    maps, matched, unmatched, examples = _registry_maps(session, registry_rows)
    return PreparedImport(
        weeks=[],
        maps=maps,
        input_rows=0,
        matched_players=matched,
        unmatched_players=unmatched,
        latest_week=None,
        schema_fingerprint=_schema_fingerprint(columns),
        details=_bounded_details({**(details or {}), "unmatched_examples": examples}),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _schema_fingerprint(columns: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(columns)).encode()).hexdigest()[:16]


def _bounded_details(details: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(details, default=str))
    for key, value in list(out.items()):
        if isinstance(value, list):
            out[key] = value[:20]
    while len(json.dumps(out, separators=(",", ":")).encode()) > MAX_DETAILS_BYTES:
        lists = [value for value in out.values() if isinstance(value, list) and value]
        if not lists:
            return {"truncated": True}
        max(lists, key=len).pop()
    return out


def _validate_columns(rows: list[dict[str, Any]], required: set[str], asset: str) -> set[str]:
    columns = set().union(*(row.keys() for row in rows)) if rows else set()
    missing = sorted(required - columns)
    if missing:
        raise OpportunityError(
            "OPP-SOURCE-FORMAT",
            "NFL opportunity data changed format; previous data was preserved.",
            details={"asset": asset, "missing_columns": missing, "actual_columns": sorted(columns)},
        )
    return columns


def _registry_maps(
    session: Session, registry_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int, list[dict[str, Any]]]:
    by_espn: dict[int, set[str]] = defaultdict(set)
    for row in registry_rows:
        espn_id = _int(row.get("espn_id"))
        gsis_id = str(row.get("gsis_id") or "").strip()
        if espn_id is not None and gsis_id:
            by_espn[espn_id].add(gsis_id)

    players = list(
        session.scalars(select(Player).where(Player.position.in_(sorted(ELIGIBLE_POSITIONS))))
    )
    candidates_by_espn: dict[int, set[str]] = {}
    claimed_by_gsis: dict[str, set[int]] = defaultdict(set)
    for player in players:
        override = NFLVERSE_ID_OVERRIDES.get(player.espn_player_id)
        candidates = {override} if override else set(by_espn.get(player.espn_player_id, set()))
        candidates.discard(None)
        candidates_by_espn[player.espn_player_id] = candidates
        if len(candidates) == 1:
            claimed_by_gsis[next(iter(candidates))].add(player.espn_player_id)

    maps: list[dict[str, Any]] = []
    matched = 0
    unmatched = 0
    examples: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for player in players:
        override = NFLVERSE_ID_OVERRIDES.get(player.espn_player_id)
        candidates = candidates_by_espn[player.espn_player_id]
        if len(candidates) == 1 and len(claimed_by_gsis[next(iter(candidates))]) == 1:
            gsis_id = next(iter(candidates))
            status = "matched"
            method = "manual" if override else "registry"
            matched += 1
        elif candidates:
            gsis_id = None
            status = "ambiguous"
            method = None
            unmatched += 1
        else:
            gsis_id = None
            status = "unmatched"
            method = None
            unmatched += 1
        maps.append(
            {
                "espn_player_id": player.espn_player_id,
                "gsis_id": gsis_id,
                "status": status,
                "method": method,
                "updated_at": now,
            }
        )
        if status != "matched" and len(examples) < 20:
            examples.append(
                {
                    "espn_player_id": player.espn_player_id,
                    "name": player.name,
                    "position": player.position,
                    "status": status,
                }
            )
    return maps, matched, unmatched, examples


def prepare_opportunity_import(
    session: Session,
    season: int,
    registry_rows: list[dict[str, Any]],
    stat_rows: list[dict[str, Any]],
) -> PreparedImport:
    """Validate and reduce nflverse rows without mutating the DB."""
    player_columns = _validate_columns(registry_rows, _PLAYER_COLUMNS, "players/players")
    stat_columns = _validate_columns(
        stat_rows, _STAT_COLUMNS, f"stats_player/stats_player_week_{season}"
    )
    maps, matched, unmatched, unmatched_examples = _registry_maps(session, registry_rows)

    regular_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    raw_conflicts: list[dict[str, Any]] = []
    invalid_values: list[dict[str, Any]] = []
    for row_index, raw in enumerate(stat_rows):
        for field in _INPUT_FLOATS:
            value = raw.get(field)
            if value not in (None, "") and _float(value) is None:
                invalid_values.append(
                    {"row": row_index, "column": field, "type": type(value).__name__}
                )
        if _int(raw.get("season")) != season or str(raw.get("season_type") or "").upper() != "REG":
            continue
        game_id = str(raw.get("game_id") or "").strip()
        team = normalize_team(raw.get("team"))
        if not game_id or not team:
            continue
        row = dict(raw)
        row["game_id"] = game_id
        row["team"] = team
        row["opponent_team"] = normalize_team(raw.get("opponent_team")) or None
        row["position"] = normalize_position(raw.get("position"))
        row["player_id"] = str(raw.get("player_id") or "").strip()
        row["week"] = _int(raw.get("week"))
        if not row["player_id"] or row["week"] is None:
            continue
        for field in _INPUT_FLOATS:
            row[field] = _float(raw.get(field))
        normalized = {
            "game_id": row["game_id"],
            "team": row["team"],
            "opponent_team": row["opponent_team"],
            "position": row["position"],
            "player_id": row["player_id"],
            "week": row["week"],
            **{field: row[field] for field in _INPUT_FLOATS},
        }
        key = (game_id, row["player_id"])
        prior = regular_by_key.get(key)
        if prior is None:
            regular_by_key[key] = normalized
        elif prior != normalized:
            raw_conflicts.append({"game_id": game_id, "gsis_id": row["player_id"]})

    if invalid_values:
        raise OpportunityError(
            "OPP-SOURCE-FORMAT",
            "NFL opportunity data changed format; previous data was preserved.",
            details={
                "asset": f"stats_player/stats_player_week_{season}",
                "invalid_values": invalid_values[:20],
                "invalid_count": len(invalid_values),
            },
        )

    if raw_conflicts:
        raise OpportunityError(
            "OPP-DATA-CONFLICT",
            DATA_CONFLICT_MESSAGE,
            details={"conflicts": raw_conflicts[:20], "conflict_count": len(raw_conflicts)},
        )

    regular = list(regular_by_key.values())
    team_carries: dict[tuple[str, str], float] = defaultdict(float)
    team_passing_yards: dict[tuple[str, str], float] = defaultdict(float)
    teams_with_passing_yards: set[tuple[str, str]] = set()
    for row in regular:
        team_key = (row["game_id"], row["team"])
        team_carries[team_key] += row["carries"] or 0.0
        if row["passing_yards"] is not None:
            team_passing_yards[team_key] += row["passing_yards"]
            teams_with_passing_yards.add(team_key)

    selected: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for row in regular:
        if row["position"] not in ELIGIBLE_POSITIONS:
            continue
        team_key = (row["game_id"], row["team"])
        denominator = team_carries[team_key]
        carry_share = (row["carries"] or 0.0) / denominator if denominator > 0 else None
        item = {
            "season": season,
            "season_type": "REG",
            "week": row["week"],
            "game_id": row["game_id"],
            "gsis_id": row["player_id"],
            "team": row["team"],
            "opponent_team": row["opponent_team"],
            "position": row["position"],
            "carry_share": carry_share,
            "team_passing_yards": (
                team_passing_yards[team_key] if team_key in teams_with_passing_yards else None
            ),
            **{field: row[field] for field in _STORED_FLOATS},
        }
        key = (season, "REG", item["game_id"], item["gsis_id"])
        prior = selected.get(key)
        if prior is None:
            selected[key] = item
        elif prior != item:
            conflicts.append({"game_id": item["game_id"], "gsis_id": item["gsis_id"]})
    if conflicts:
        raise OpportunityError(
            "OPP-DATA-CONFLICT",
            DATA_CONFLICT_MESSAGE,
            details={"conflicts": conflicts[:20], "conflict_count": len(conflicts)},
        )

    weeks = list(selected.values())
    latest_week = max((row["week"] for row in weeks), default=None)
    columns = player_columns | stat_columns
    return PreparedImport(
        weeks=weeks,
        maps=maps,
        input_rows=len(stat_rows),
        matched_players=matched,
        unmatched_players=unmatched,
        latest_week=latest_week,
        schema_fingerprint=_schema_fingerprint(columns),
        details=_bounded_details({"unmatched_examples": unmatched_examples}),
    )


_refresh_lock = threading.Lock()
_active_run_id: str | None = None


def _latest_good(session: Session, season: int) -> OpportunityImport | None:
    return session.scalar(
        select(OpportunityImport)
        .where(
            OpportunityImport.season == season,
            OpportunityImport.state.in_(("ready", "partial")),
            OpportunityImport.stored_rows > 0,
        )
        .order_by(OpportunityImport.completed_at.desc(), OpportunityImport.started_at.desc())
    )


def _expected_empty(session: Session, season: int) -> bool:
    leagues = list(session.scalars(select(League).where(League.season == season)))
    return not leagues or all(league.lifecycle in {"pre_draft", "drafted"} for league in leagues)


def _run_dict(run: OpportunityImport, last_good: OpportunityImport | None = None) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "season": run.season,
        "state": run.state,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "latest_week": run.latest_week,
        "input_rows": run.input_rows,
        "stored_rows": run.stored_rows,
        "matched_players": run.matched_players,
        "unmatched_players": run.unmatched_players,
        "retries": run.retries,
        "package_version": run.package_version,
        "schema_fingerprint": run.schema_fingerprint,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "details": run.details_json or {},
        "last_good_at": last_good.completed_at if last_good else None,
    }


def _record_run(session: Session, **values: Any) -> OpportunityImport:
    run = OpportunityImport(**values)
    session.add(run)
    session.flush()
    stale_ids = list(
        session.scalars(
            select(OpportunityImport.id)
            .where(OpportunityImport.season == run.season)
            .order_by(OpportunityImport.started_at.desc())
            .offset(50)
        )
    )
    if stale_ids:
        session.execute(delete(OpportunityImport).where(OpportunityImport.id.in_(stale_ids)))
    return run


def refresh_opportunity(
    session: Session,
    season: int,
    *,
    force: bool = False,
    dry_run: bool = False,
    client: OpportunityDataClient | None = None,
) -> dict[str, Any]:
    """Fetch, validate, and atomically replace a season; known failures are envelopes."""
    assert_recovery_write_allowed()
    global _active_run_id
    run_id = uuid.uuid4().hex[:12]
    if not _refresh_lock.acquire(blocking=False):
        raise OpportunityBusyError(
            "OPP-REFRESH-BUSY",
            "Opportunity refresh is already running.",
            details={"run_id": _active_run_id},
        )
    _active_run_id = run_id
    started = datetime.now(UTC)
    owns_client = client is None
    source = client or NflverseClient()
    last_good = _latest_good(session, season)
    try:
        ttl = timedelta(hours=get_settings().opportunity_ttl_hours)
        if (
            not force
            and last_good is not None
            and last_good.completed_at is not None
            and datetime.now(UTC) - _aware(last_good.completed_at) < ttl
        ):
            run = OpportunityImport(
                id=run_id,
                season=season,
                state="skipped",
                started_at=started,
                completed_at=datetime.now(UTC),
                latest_week=last_good.latest_week,
                input_rows=last_good.input_rows,
                stored_rows=last_good.stored_rows,
                matched_players=last_good.matched_players,
                unmatched_players=last_good.unmatched_players,
                retries=0,
                package_version=source.package_version,
                schema_fingerprint=last_good.schema_fingerprint,
                details_json={"reason": "fresh"},
            )
            if not dry_run:
                session.add(run)
                session.flush()
            return _run_dict(run, last_good)

        registry: list[dict[str, Any]] | None = None
        try:
            registry = source.load_players()
            stats = source.load_player_stats(season)
            if not stats:
                if not _expected_empty(session, season):
                    raise OpportunityError(
                        "OPP-SOURCE-UNAVAILABLE",
                        SOURCE_UNAVAILABLE_MESSAGE,
                        details={"reason": "empty in-season dataset"},
                    )
                prepared = _prepare_empty_import(session, registry)
                state = "empty"
                error_code = "OPP-NOT-PUBLISHED"
                error_message = "Opportunity data begins after regular-season games."
            else:
                prepared = prepare_opportunity_import(session, season, registry, stats)
                state = (
                    "partial"
                    if prepared.matched_players + prepared.unmatched_players > 0
                    and prepared.matched_players
                    / (prepared.matched_players + prepared.unmatched_players)
                    < 0.95
                    else "ready"
                )
                error_code = "OPP-ID-COVERAGE" if prepared.unmatched_players else None
                error_message = (
                    "Some ESPN players could not be linked to nflverse and were excluded."
                    if prepared.unmatched_players
                    else None
                )
        except OpportunityError as exc:
            if (
                exc.code == "OPP-NOT-PUBLISHED"
                and registry is not None
                and _expected_empty(session, season)
            ):
                state = "empty"
                prepared = _prepare_empty_import(session, registry, exc.details)
                error_code = exc.code
                error_message = exc.message
            else:
                run = OpportunityImport(
                    id=run_id,
                    season=season,
                    state="failed",
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    retries=max(source.retries, exc.retries),
                    package_version=source.package_version,
                    error_code=exc.code,
                    error_message=exc.message,
                    details_json=_bounded_details(exc.details),
                )
                if not dry_run:
                    session.add(run)
                    session.flush()
                log.warning(
                    "opportunity refresh failed run_id=%s season=%s code=%s",
                    run_id,
                    season,
                    exc.code,
                )
                return _run_dict(run, last_good)
        except Exception as exc:
            run = OpportunityImport(
                id=run_id,
                season=season,
                state="failed",
                started_at=started,
                completed_at=datetime.now(UTC),
                retries=source.retries,
                package_version=source.package_version,
                error_code="OPP-UNEXPECTED",
                error_message=(
                    "Opportunity refresh failed unexpectedly; previous data was preserved."
                ),
                details_json={
                    "exception": type(exc).__name__,
                    "sqlite_errorcode": getattr(
                        getattr(exc, "orig", None), "sqlite_errorcode", None
                    ),
                    "db_path": str(get_settings().db_file),
                },
            )
            if not dry_run:
                session.add(run)
                session.flush()
            log.exception(
                "opportunity refresh unexpected failure run_id=%s season=%s",
                run_id,
                season,
            )
            return _run_dict(run, last_good)

        completed = datetime.now(UTC)
        run = OpportunityImport(
            id=run_id,
            season=season,
            state=state,
            started_at=started,
            completed_at=completed,
            latest_week=prepared.latest_week,
            input_rows=prepared.input_rows,
            stored_rows=len(prepared.weeks),
            matched_players=prepared.matched_players,
            unmatched_players=prepared.unmatched_players,
            retries=source.retries,
            package_version=source.package_version,
            schema_fingerprint=prepared.schema_fingerprint,
            error_code=error_code,
            error_message=error_message,
            details_json=prepared.details,
        )
        if dry_run:
            return _run_dict(run, last_good)

        try:
            session.execute(delete(NflversePlayerMap))
            session.execute(delete(OpportunityWeek).where(OpportunityWeek.season == season))
            session.add_all(NflversePlayerMap(**row) for row in prepared.maps)
            session.add_all(OpportunityWeek(**row) for row in prepared.weeks)
            session.add(run)
            session.flush()
            stale_ids = list(
                session.scalars(
                    select(OpportunityImport.id)
                    .where(OpportunityImport.season == season)
                    .order_by(OpportunityImport.started_at.desc())
                    .offset(50)
                )
            )
            if stale_ids:
                session.execute(
                    delete(OpportunityImport).where(OpportunityImport.id.in_(stale_ids))
                )
        except SQLAlchemyError as exc:
            session.rollback()
            failed = _record_run(
                session,
                id=run_id,
                season=season,
                state="failed",
                started_at=started,
                completed_at=datetime.now(UTC),
                retries=source.retries,
                package_version=source.package_version,
                schema_fingerprint=prepared.schema_fingerprint,
                error_code="OPP-SAVE-FAILED",
                error_message=SAVE_FAILED_MESSAGE,
                details_json={"exception": type(exc).__name__},
            )
            log.exception("opportunity save failed run_id=%s season=%s", run_id, season)
            return _run_dict(failed, _latest_good(session, season))

        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        log.info(
            "opportunity refresh run_id=%s season=%s state=%s latest_week=%s "
            "input=%s stored=%s mapped=%s unmatched=%s retries=%s schema=%s "
            "elapsed_ms=%s last_good_at=%s",
            run_id,
            season,
            state,
            prepared.latest_week,
            prepared.input_rows,
            len(prepared.weeks),
            prepared.matched_players,
            prepared.unmatched_players,
            source.retries,
            prepared.schema_fingerprint,
            elapsed_ms,
            (
                run.completed_at
                if run.stored_rows
                else last_good.completed_at
                if last_good
                else None
            ),
        )
        return _run_dict(
            run, run if state in {"ready", "partial"} and run.stored_rows else last_good
        )
    finally:
        if owns_client:
            source.close()
        _active_run_id = None
        _refresh_lock.release()


def _pct_rank(value: float, population: list[float]) -> float:
    less = sum(sample < value for sample in population)
    equal = sum(sample == value for sample in population)
    return (less + 0.5 * equal) / len(population) * 100.0


def _mean_present(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return mean(values) if values else None


def _volume(
    position: str, carry: float | None, target: float | None, air: float | None
) -> float | None:
    if position == "RB":
        return (
            100.0 * (0.60 * carry + 0.40 * target)
            if carry is not None and target is not None
            else None
        )
    return 100.0 * (0.60 * target + 0.40 * air) if target is not None and air is not None else None


def compute_opportunity_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure last-three-game opportunity scoring; inputs are stored-week-shaped dicts."""
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("position") in ELIGIBLE_POSITIONS:
            by_player[str(row["gsis_id"])].append(row)

    out: list[dict[str, Any]] = []
    for gsis_id, games in by_player.items():
        games.sort(key=lambda row: (int(row["week"]), str(row["game_id"])))
        position = str(games[-1]["position"])
        same_position = [row for row in games if row.get("position") == position]
        sample = same_position[-3:]
        carry = _mean_present(sample, "carry_share")
        target = _mean_present(sample, "target_share")
        air = _mean_present(sample, "air_yards_share")
        component_a = carry if position == "RB" else target
        component_b = target if position == "RB" else air
        production = _mean_present(sample, "fantasy_points_ppr")
        sample_targets = _mean_present(sample, "targets")
        sample_receptions = _mean_present(sample, "receptions")
        sample_receiving_yards = _mean_present(sample, "receiving_yards")
        sample_receiving_tds = _mean_present(sample, "receiving_tds")
        sample_team_passing_yards = _mean_present(sample, "team_passing_yards")
        target_rows = [
            row
            for row in sample
            if row.get("targets") is not None and row.get("receiving_air_yards") is not None
        ]
        target_total = sum(float(row["targets"]) for row in target_rows)
        adot = (
            sum(float(row["receiving_air_yards"]) for row in target_rows) / target_total
            if target_rows and target_total > 0
            else None
        )
        trend = None
        trend_delta = None
        if len(same_position) >= 4:
            prior = same_position[-4:-2]
            recent = same_position[-2:]
            prior_volume = _volume(
                position,
                _mean_present(prior, "carry_share"),
                _mean_present(prior, "target_share"),
                _mean_present(prior, "air_yards_share"),
            )
            recent_volume = _volume(
                position,
                _mean_present(recent, "carry_share"),
                _mean_present(recent, "target_share"),
                _mean_present(recent, "air_yards_share"),
            )
            if prior_volume is not None and recent_volume is not None:
                trend_delta = recent_volume - prior_volume
                trend = (
                    "rising" if trend_delta >= 5 else "falling" if trend_delta <= -5 else "steady"
                )
        out.append(
            {
                "gsis_id": gsis_id,
                "position": position,
                "sample_games": len(sample),
                "through_week": max(int(row["week"]) for row in sample),
                "team": sample[-1].get("team"),
                "avg_carry_share": carry,
                "avg_target_share": target,
                "avg_air_yards_share": air,
                "avg_wopr": _mean_present(sample, "wopr"),
                "avg_rushing_epa": _mean_present(sample, "rushing_epa"),
                "avg_receiving_epa": _mean_present(sample, "receiving_epa"),
                "ppr_points_per_game": production,
                "targets_per_game": sample_targets,
                "receptions_per_game": sample_receptions,
                "receiving_yards_per_game": sample_receiving_yards,
                "receiving_tds_per_game": sample_receiving_tds,
                "average_depth_of_target": adot,
                "team_passing_yards_per_game": sample_team_passing_yards,
                "volume_index": _volume(position, carry, target, air),
                "component_a": component_a,
                "component_b": component_b,
                "opportunity_score": None,
                "production_percentile": None,
                "opportunity_gap": None,
                "signal": "pending",
                "trend": trend,
                "trend_delta_pp": trend_delta,
                "position_changed": len({row.get("position") for row in games}) > 1,
            }
        )

    for position in sorted(ELIGIBLE_POSITIONS):
        eligible = [
            row
            for row in out
            if row["position"] == position
            and row["sample_games"] >= 2
            and row["component_a"] is not None
            and row["component_b"] is not None
        ]
        if len(eligible) < 10:
            continue
        pop_a = [row["component_a"] for row in eligible]
        pop_b = [row["component_b"] for row in eligible]
        pop_production = [
            row["ppr_points_per_game"] for row in eligible if row["ppr_points_per_game"] is not None
        ]
        for row in eligible:
            row["opportunity_score"] = 0.60 * _pct_rank(
                row["component_a"], pop_a
            ) + 0.40 * _pct_rank(row["component_b"], pop_b)
            if row["ppr_points_per_game"] is not None and len(pop_production) >= 10:
                row["production_percentile"] = _pct_rank(row["ppr_points_per_game"], pop_production)
                row["opportunity_gap"] = row["opportunity_score"] - row["production_percentile"]
                if row["sample_games"] >= 3:
                    gap = row["opportunity_gap"]
                    row["signal"] = (
                        "opportunity_ahead"
                        if gap >= 15
                        else "production_ahead"
                        if gap <= -15
                        else "aligned"
                    )
    return out


def opportunity_status(session: Session, season: int) -> dict[str, Any]:
    latest = session.scalar(
        select(OpportunityImport)
        .where(OpportunityImport.season == season)
        .order_by(OpportunityImport.started_at.desc())
    )
    good = _latest_good(session, season)
    fetched_at = good.completed_at if good else None
    age_hours = (
        (datetime.now(UTC) - _aware(fetched_at)).total_seconds() / 3600 if fetched_at else None
    )
    stale = bool(age_hours is not None and age_hours > get_settings().opportunity_ttl_hours)
    return {
        "season": season,
        "state": latest.state if latest else "empty",
        "run_id": latest.id if latest else None,
        "fetched_at": fetched_at,
        "age_hours": round(age_hours, 1) if age_hours is not None else None,
        "stale": stale,
        "latest_week": good.latest_week if good else None,
        "stored_rows": good.stored_rows if good else 0,
        "matched_players": good.matched_players if good else 0,
        "unmatched_players": good.unmatched_players if good else 0,
        "package_version": (latest or good).package_version if (latest or good) else None,
        "schema_fingerprint": good.schema_fingerprint if good else None,
        "error_code": latest.error_code if latest else None,
        "error_message": latest.error_message if latest else None,
        "last_good_at": fetched_at,
    }


def _roster_context(
    session: Session, leagues: list[League]
) -> tuple[dict[int, dict[int, str]], list[dict[str, Any]]]:
    """Return espn player -> league status plus league diagnostic rows."""
    if not leagues:
        return {}, []
    league_ids = [league.id for league in leagues]
    snapshots = {
        snap.league_id: snap
        for snap in session.scalars(
            select(CurrentRosterSnapshot).where(CurrentRosterSnapshot.league_id.in_(league_ids))
        )
    }
    team_me = {
        team.id: team.is_me
        for team in session.scalars(select(Team).where(Team.league_id.in_(league_ids)))
    }
    entries_by_league: dict[int, dict[int, int]] = defaultdict(dict)
    snapshot_ids = [snap.id for snap in snapshots.values()]
    if snapshot_ids:
        rows = session.execute(
            select(
                CurrentRosterSnapshot.league_id,
                CurrentRosterEntry.espn_player_id,
                CurrentRosterEntry.team_id,
            )
            .join(CurrentRosterEntry, CurrentRosterEntry.snapshot_id == CurrentRosterSnapshot.id)
            .where(CurrentRosterSnapshot.id.in_(snapshot_ids))
        )
        for league_id, player_id, team_id in rows:
            entries_by_league[league_id].setdefault(player_id, team_id)

    statuses: dict[int, dict[int, str]] = defaultdict(dict)
    league_rows: list[dict[str, Any]] = []
    for league in leagues:
        snapshot = snapshots.get(league.id)
        current = bool(
            snapshot is not None
            and snapshot.scoring_period == league.current_scoring_period
            and league.last_sync_ok is True
        )
        league_rows.append(
            {
                "league_id": league.id,
                "league_name": league.name,
                "state": "current" if current else "unknown",
            }
        )
        if current:
            for player_id, team_id in entries_by_league.get(league.id, {}).items():
                statuses[player_id][league.id] = "mine" if team_me.get(team_id) else "field"
            # Availability for absent players is filled by the caller's player universe.
    return statuses, league_rows


def _round_or_none(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


@dataclass(frozen=True)
class OpportunityUniverse:
    season: int
    leagues: list[League]
    stored_player_games: int
    current_roster_league_ids: set[int]
    rows: list[dict[str, Any]]
    source: dict[str, Any]


def _build_opportunity_universe(session: Session, filters: PortfolioFilters) -> OpportunityUniverse:
    """One unrounded DB-only player universe shared by the table and chart read models."""
    season = filters.season or get_settings().season
    league_ids = filtered_league_ids(session, filters)
    leagues = (
        list(session.scalars(select(League).where(League.id.in_(league_ids)))) if league_ids else []
    )
    # A Core select yielding mappings, not an ORM query. Measured on Linux at
    # 99,990 player-game rows, one case per process, baseline 67.2 MiB:
    #
    #   ORM objects only .................. 308.1 MiB
    #   ORM objects -> dicts (was here) ... 388.1 MiB   <- both alive at once
    #   whole universe (was) .............. 396.1 MiB
    #   Core mappings -> dicts (this) ..... 232.4 MiB
    #
    # The ORM builds a persistent object per row and registers it in the
    # identity map, and the dict comprehension then holds a second copy of
    # every row while the first is still reachable. Core rows are tuples the
    # session never tracks, so `dict(m)` is the only copy that exists.
    #
    # Equivalence was checked before the swap, not assumed: same row count,
    # same key order, identical values, and `compute_opportunity_scores`
    # returns an identical result. A faster different answer is not the same
    # answer.
    #
    # This is the dominant cost of every opportunity route, not of the CSV
    # export: `build_portfolio_opportunity` measured the same 396.1 MiB as the
    # universe alone, so the whole read model adds nothing on top of this.
    _stored_columns = [
        column for column in OpportunityWeek.__table__.columns if column.name != "id"
    ]
    stored = [
        dict(mapping)
        for mapping in session.execute(
            select(*_stored_columns)
            .where(OpportunityWeek.season == season)
            .order_by(OpportunityWeek.week, OpportunityWeek.game_id)
            .execution_options(yield_per=2000)
        ).mappings()
    ]
    scored = compute_opportunity_scores(stored)
    maps = {
        row.gsis_id: row.espn_player_id
        for row in session.scalars(
            select(NflversePlayerMap).where(
                NflversePlayerMap.status == "matched",
                NflversePlayerMap.gsis_id.is_not(None),
            )
        )
        if row.gsis_id is not None
    }
    espn_ids = list(maps.values())
    players = (
        {
            player.espn_player_id: player
            for player in session.scalars(select(Player).where(Player.espn_player_id.in_(espn_ids)))
        }
        if espn_ids
        else {}
    )
    owned, league_rows = _roster_context(session, leagues)
    current_ids = {row["league_id"] for row in league_rows if row["state"] == "current"}
    rows: list[dict[str, Any]] = []
    for item in scored:
        espn_id = maps.get(item["gsis_id"])
        player = players.get(espn_id) if espn_id is not None else None
        if player is None:
            continue
        counts = {"mine": 0, "field": 0, "available": 0, "unknown": 0}
        for league in leagues:
            roster_state = owned.get(espn_id, {}).get(league.id)
            if roster_state is None:
                roster_state = "available" if league.id in current_ids else "unknown"
            counts[roster_state] += 1
        rows.append(
            {
                "espn_player_id": espn_id,
                "player_name": player.name,
                "position": item["position"],
                # ESPN stores proTeamId as a numeric string; nflverse supplies the
                # readable team abbreviation used by the chart filter and labels.
                "nfl_team": item["team"] or player.nfl_team,
                "espn_rank_ppr": player.espn_rank_ppr,
                "sample_games": item["sample_games"],
                "through_week": item["through_week"],
                "avg_carry_share": (
                    item["avg_carry_share"] * 100 if item["avg_carry_share"] is not None else None
                ),
                "avg_target_share": (
                    item["avg_target_share"] * 100 if item["avg_target_share"] is not None else None
                ),
                "avg_air_yards_share": (
                    item["avg_air_yards_share"] * 100
                    if item["avg_air_yards_share"] is not None
                    else None
                ),
                "target_share_pct": (
                    item["avg_target_share"] * 100 if item["avg_target_share"] is not None else None
                ),
                "air_yards_share_pct": (
                    item["avg_air_yards_share"] * 100
                    if item["avg_air_yards_share"] is not None
                    else None
                ),
                "avg_wopr": item["avg_wopr"],
                "avg_rushing_epa": item["avg_rushing_epa"],
                "avg_receiving_epa": item["avg_receiving_epa"],
                "ppr_points_per_game": item["ppr_points_per_game"],
                "targets_per_game": item["targets_per_game"],
                "receptions_per_game": item["receptions_per_game"],
                "receiving_yards_per_game": item["receiving_yards_per_game"],
                "receiving_tds_per_game": item["receiving_tds_per_game"],
                "average_depth_of_target": item["average_depth_of_target"],
                "team_passing_yards_per_game": item["team_passing_yards_per_game"],
                "opportunity_score": item["opportunity_score"],
                "production_percentile": item["production_percentile"],
                "opportunity_gap": item["opportunity_gap"],
                "signal": item["signal"],
                "trend": item["trend"],
                "trend_delta_pp": item["trend_delta_pp"],
                "mine_leagues": counts["mine"],
                "field_leagues": counts["field"],
                "available_leagues": counts["available"],
                "unknown_leagues": counts["unknown"],
                "position_changed": item["position_changed"],
            }
        )
    rows.sort(
        key=lambda row: (
            row["position"],
            str(row["player_name"] or ""),
            row["espn_player_id"],
        )
    )
    return OpportunityUniverse(
        season=season,
        leagues=leagues,
        stored_player_games=len(stored),
        current_roster_league_ids=current_ids,
        rows=rows,
        source=opportunity_status(session, season),
    )


def _opportunity_view(
    universe: OpportunityUniverse,
    requested: Literal["rostered", "available", "all"] | None,
) -> Literal["rostered", "available", "all"]:
    if requested is not None:
        return requested
    return (
        "available"
        if any(league.lifecycle == "in_season" for league in universe.leagues)
        else "rostered"
    )


def _rows_for_view(
    rows: list[dict[str, Any]], view: Literal["rostered", "available", "all"]
) -> list[dict[str, Any]]:
    if view == "rostered":
        return [row for row in rows if row["mine_leagues"] > 0]
    if view == "available":
        return [row for row in rows if row["available_leagues"] > 0]
    return list(rows)


def _opportunity_warnings(
    universe: OpportunityUniverse, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    status = universe.source
    warnings: list[dict[str, Any]] = []
    if status["error_code"]:
        warnings.append({"code": status["error_code"], "message": status["error_message"]})
    if status["stale"]:
        warnings.append(
            {
                "code": "OPP-SOURCE-STALE",
                "message": "Opportunity data is older than 24 hours; refresh before acting on it.",
            }
        )
    stale_leagues = len(universe.leagues) - len(universe.current_roster_league_ids)
    if stale_leagues:
        warnings.append(
            {
                "code": "OPP-ROSTERS-STALE",
                "message": "Availability is unknown until the affected ESPN leagues are synced.",
                "count": stale_leagues,
            }
        )
    if not any(row["opportunity_score"] is not None for row in rows):
        warnings.append(
            {
                "code": "OPP-NO-SAMPLE",
                "message": "Not enough games to calculate opportunity signals yet.",
            }
        )
    position_changes = sum(bool(row["position_changed"]) for row in universe.rows)
    if position_changes:
        warnings.append(
            {
                "code": "OPP-POSITION-CHANGE",
                "message": (
                    "Some players changed positions; only games at the latest position were scored."
                ),
                "count": position_changes,
            }
        )
    return warnings


def _table_player(row: dict[str, Any]) -> dict[str, Any]:
    rounded = dict(row)
    rounded.pop("position_changed", None)
    rounded.pop("target_share_pct", None)
    rounded.pop("air_yards_share_pct", None)
    rounded.pop("espn_rank_ppr", None)
    for key in (
        "avg_carry_share",
        "avg_target_share",
        "avg_air_yards_share",
        "avg_wopr",
        "avg_rushing_epa",
        "avg_receiving_epa",
        "ppr_points_per_game",
        "targets_per_game",
        "receptions_per_game",
        "receiving_yards_per_game",
        "receiving_tds_per_game",
        "average_depth_of_target",
        "team_passing_yards_per_game",
        "opportunity_score",
        "production_percentile",
        "opportunity_gap",
        "trend_delta_pp",
    ):
        rounded[key] = _round_or_none(rounded[key])
    return rounded


def build_portfolio_opportunity(
    session: Session,
    filters: PortfolioFilters,
    *,
    view: Literal["rostered", "available", "all"] | None = None,
) -> dict[str, Any]:
    universe = _build_opportunity_universe(session, filters)
    actual_view = _opportunity_view(universe, view)
    raw_rows = _rows_for_view(universe.rows, actual_view)
    rows = [_table_player(row) for row in raw_rows]
    rows.sort(
        key=lambda row: (
            row["opportunity_score"] is None,
            -(row["opportunity_score"] or 0),
            row["player_name"] or "",
        )
    )
    stale_leagues = len(universe.leagues) - len(universe.current_roster_league_ids)
    return {
        "season": universe.season,
        "view": actual_view,
        "source": universe.source,
        "coverage": {
            "leagues_in_scope": len(universe.leagues),
            "current_roster_leagues": len(universe.current_roster_league_ids),
            "unknown_roster_leagues": stale_leagues,
            "stored_player_games": universe.stored_player_games,
            "mapped_players": universe.source["matched_players"],
            "unmatched_players": universe.source["unmatched_players"],
            "players_returned": len(rows),
        },
        "warnings": _opportunity_warnings(universe, raw_rows),
        "players": rows,
    }


_CHART_SPECS: dict[str, dict[str, Any]] = {
    "target_air": {
        "title": "Target share vs air-yards share",
        "x_key": "target_share_pct",
        "y_key": "air_yards_share_pct",
        "x_label": "Target share",
        "y_label": "Air-yards share",
        "positions": ("WR",),
        "minimum_span": (10.0, 15.0),
        "quadrants": (
            ("featured", "Featured downfield", "high", "high"),
            ("target_volume", "Target volume", "high", "low"),
            ("air_heavy", "Air-yards heavy", "low", "high"),
            ("lower_share", "Lower recent share", "low", "low"),
        ),
    },
    "yards_tds": {
        "title": "Receiving yards vs touchdowns",
        "x_key": "receiving_yards_per_game",
        "y_key": "receiving_tds_per_game",
        "x_label": "Receiving yards/game",
        "y_label": "Receiving TDs/game",
        "positions": ("WR",),
        "minimum_span": (20.0, 0.25),
        "quadrants": (
            ("yards_tds", "Yards + TDs", "high", "high"),
            ("yards", "High yards / fewer TDs", "high", "low"),
            ("tds", "TD-heavy production", "low", "high"),
            ("lower_output", "Lower recent output", "low", "low"),
        ),
    },
    "adot_targets": {
        "title": "Target depth vs volume",
        "x_key": "average_depth_of_target",
        "y_key": "targets_per_game",
        "x_label": "Target-weighted aDOT",
        "y_label": "Targets/game",
        "positions": ("WR",),
        "minimum_span": (3.0, 2.0),
        "quadrants": (
            ("deep_volume", "Deep + volume", "high", "high"),
            ("deep", "Deep targets", "high", "low"),
            ("short_volume", "Short + volume", "low", "high"),
            ("lower_volume", "Lower volume", "low", "low"),
        ),
    },
    "opportunity_production": {
        "title": "Opportunity vs production",
        "x_key": "opportunity_score",
        "y_key": "production_percentile",
        "x_label": "Opportunity percentile",
        "y_label": "Production percentile",
        "positions": ("RB", "WR", "TE"),
        "minimum_span": (100.0, 100.0),
        "fixed_domain": True,
        "quadrants": (),
    },
    "passing_environment": {
        "title": "Passing environment vs target share",
        "x_key": "team_passing_yards_per_game",
        "y_key": "target_share_pct",
        "x_label": "Team QB passing yards/game",
        "y_label": "Target share",
        "positions": ("WR",),
        "minimum_span": (50.0, 10.0),
        "quadrants": (
            ("strong_feature", "Strong environment + share", "high", "high"),
            ("strong_competition", "Strong environment / lower share", "high", "low"),
            ("share", "High share / lower environment", "low", "high"),
            ("lower_both", "Lower environment + share", "low", "low"),
        ),
    },
}


def _chart_point(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "espn_player_id": row["espn_player_id"],
        "player_name": row["player_name"],
        "nfl_team": row["nfl_team"],
        "position": row["position"],
        "espn_rank_ppr": row["espn_rank_ppr"],
        "sample_games": row["sample_games"],
        "through_week": row["through_week"],
        "target_share_pct": row["target_share_pct"],
        "air_yards_share_pct": row["air_yards_share_pct"],
        "targets_per_game": row["targets_per_game"],
        "receptions_per_game": row["receptions_per_game"],
        "receiving_yards_per_game": row["receiving_yards_per_game"],
        "receiving_tds_per_game": row["receiving_tds_per_game"],
        "average_depth_of_target": row["average_depth_of_target"],
        "team_passing_yards_per_game": row["team_passing_yards_per_game"],
        "opportunity_score": row["opportunity_score"],
        "production_percentile": row["production_percentile"],
        "opportunity_gap": row["opportunity_gap"],
        "signal": row["signal"],
        "trend": row["trend"],
        "mine_leagues": row["mine_leagues"],
        "field_leagues": row["field_leagues"],
        "available_leagues": row["available_leagues"],
        "unknown_leagues": row["unknown_leagues"],
    }


def _finite_metric(row: dict[str, Any], key: str) -> bool:
    value = row.get(key)
    return value is not None and math.isfinite(float(value))


def _has_top_espn_rank(row: dict[str, Any]) -> bool:
    rank = row.get("espn_rank_ppr")
    return (
        rank is not None
        and math.isfinite(float(rank))
        and 1 <= float(rank) <= CHART_ESPN_RANK_LIMIT
    )


def _metric_domain(values: list[float], minimum_span: float) -> tuple[float, float]:
    if not values:
        return (0.0, minimum_span)
    low = min(values)
    high = max(values)
    span = high - low
    if span < minimum_span:
        center = (low + high) / 2.0
        low = center - minimum_span / 2.0
        high = center + minimum_span / 2.0
        span = minimum_span
    padding = span * 0.05
    return (low - padding, high + padding)


def _omitted_reasons(rows: list[dict[str, Any]], x_key: str, y_key: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["sample_games"] < 2:
            counts["fewer_than_two_games"] += 1
        elif not _finite_metric(row, x_key):
            counts[f"missing_{x_key}"] += 1
        elif not _finite_metric(row, y_key):
            counts[f"missing_{y_key}"] += 1
    return [{"reason": reason, "count": counts[reason]} for reason in sorted(counts)]


def _chart_definition(
    chart_id: str,
    spec: dict[str, Any],
    population_rows: list[dict[str, Any]],
    returned_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    x_key = str(spec["x_key"])
    y_key = str(spec["y_key"])
    eligible_population = [
        row
        for row in population_rows
        if row["sample_games"] >= 2 and _finite_metric(row, x_key) and _finite_metric(row, y_key)
    ]
    eligible_returned = [
        row
        for row in returned_rows
        if row["sample_games"] >= 2 and _finite_metric(row, x_key) and _finite_metric(row, y_key)
    ]
    if spec.get("fixed_domain"):
        x_domain = (0.0, 100.0)
        y_domain = (0.0, 100.0)
        references = [
            {
                "kind": "line",
                "x1": 0.0,
                "y1": 0.0,
                "x2": 100.0,
                "y2": 100.0,
                "label": "Opportunity = production",
            },
            {
                "kind": "line",
                "x1": 15.0,
                "y1": 0.0,
                "x2": 100.0,
                "y2": 85.0,
                "label": "Opportunity +15",
            },
            {
                "kind": "line",
                "x1": 0.0,
                "y1": 15.0,
                "x2": 85.0,
                "y2": 100.0,
                "label": "Production +15",
            },
        ]
    else:
        x_values = [float(row[x_key]) for row in eligible_population]
        y_values = [float(row[y_key]) for row in eligible_population]
        x_domain = _metric_domain(x_values, float(spec["minimum_span"][0]))
        y_domain = _metric_domain(y_values, float(spec["minimum_span"][1]))
        references = []
        if x_values:
            references.append(
                {
                    "kind": "x",
                    "value": median(x_values),
                    "label": "Position median",
                }
            )
        if y_values:
            references.append(
                {
                    "kind": "y",
                    "value": median(y_values),
                    "label": "Position median",
                }
            )
    omitted = _omitted_reasons(returned_rows, x_key, y_key)
    return {
        "id": chart_id,
        "title": spec["title"],
        "x_key": x_key,
        "y_key": y_key,
        "x_label": spec["x_label"],
        "y_label": spec["y_label"],
        "supported_positions": list(spec["positions"]),
        "domain": {
            "x_min": x_domain[0],
            "x_max": x_domain[1],
            "y_min": y_domain[0],
            "y_max": y_domain[1],
        },
        "references": references,
        "quadrants": [
            {"key": key, "label": label, "x_side": x_side, "y_side": y_side}
            for key, label, x_side, y_side in spec["quadrants"]
        ],
        "point_count": len(eligible_returned),
        "population_point_count": len(eligible_population),
        "omitted_count": sum(item["count"] for item in omitted),
        "omitted_reasons": omitted,
    }


def build_opportunity_charts(
    session: Session,
    filters: PortfolioFilters,
    *,
    view: Literal["rostered", "available", "all"] | None = None,
    position: Literal["RB", "WR", "TE"] = "WR",
    chart_id: str | None = None,
) -> dict[str, Any]:
    """Build chart-ready values, references, and domains without fetching upstream data."""
    universe = _build_opportunity_universe(session, filters)
    actual_view = _opportunity_view(universe, view)
    position_population = [row for row in universe.rows if row["position"] == position]
    population = [row for row in position_population if _has_top_espn_rank(row)]
    returned = [
        row
        for row in _rows_for_view(universe.rows, actual_view)
        if row["position"] == position and _has_top_espn_rank(row)
    ]
    supported = [
        (name, spec) for name, spec in _CHART_SPECS.items() if position in spec["positions"]
    ]
    if chart_id is not None:
        spec = _CHART_SPECS.get(chart_id)
        if spec is None or position not in spec["positions"]:
            raise OpportunityError(
                "OPP-CHART-UNSUPPORTED",
                "This chart is not available for the selected position.",
                details={"chart_id": chart_id, "position": position},
            )
        supported = [(chart_id, spec)]
    charts = [_chart_definition(name, spec, population, returned) for name, spec in supported]
    warnings = _opportunity_warnings(universe, returned)
    missing_rank_count = sum(row.get("espn_rank_ppr") is None for row in position_population)
    if missing_rank_count:
        warnings.append(
            {
                "code": "OPP-CHART-RANK-MISSING",
                "message": (
                    "Some players were excluded because their ESPN PPR rank is unavailable."
                ),
                "count": missing_rank_count,
            }
        )
    for chart in charts:
        if chart["population_point_count"] < 3 or chart["point_count"] < 2:
            warnings.append(
                {
                    "code": "OPP-CHART-NO-SAMPLE",
                    "message": "Not enough comparable players to draw this chart yet.",
                    "count": chart["point_count"],
                    "chart_id": chart["id"],
                }
            )
        elif chart["omitted_count"]:
            warnings.append(
                {
                    "code": "OPP-CHART-INCOMPLETE",
                    "message": "Some players are missing a metric required by this chart.",
                    "count": chart["omitted_count"],
                    "chart_id": chart["id"],
                }
            )
    stale_leagues = len(universe.leagues) - len(universe.current_roster_league_ids)
    return {
        "season": universe.season,
        "view": actual_view,
        "position": position,
        "window_games": 3,
        "through_week": universe.source["latest_week"],
        "source": universe.source,
        "coverage": {
            "rank_limit": CHART_ESPN_RANK_LIMIT,
            "population_players": len(population),
            "returned_players": len(returned),
            "current_roster_leagues": len(universe.current_roster_league_ids),
            "unknown_roster_leagues": stale_leagues,
        },
        "charts": charts,
        "points": [_chart_point(row) for row in returned],
        "warnings": warnings,
    }


def build_player_opportunity(
    session: Session,
    espn_player_id: int,
    filters: PortfolioFilters,
) -> dict[str, Any] | None:
    player = session.get(Player, espn_player_id)
    if player is None:
        return None
    mapping = session.get(NflversePlayerMap, espn_player_id)
    season = filters.season or get_settings().season
    all_data = build_portfolio_opportunity(session, filters, view="all")
    summary = next(
        (row for row in all_data["players"] if row["espn_player_id"] == espn_player_id),
        None,
    )
    weeks = []
    if mapping and mapping.gsis_id:
        weeks = [
            {
                "week": row.week,
                "game_id": row.game_id,
                "team": row.team,
                "opponent_team": row.opponent_team,
                "position": row.position,
                "carries": row.carries,
                "carry_share": _round_or_none(
                    row.carry_share * 100 if row.carry_share is not None else None
                ),
                "targets": row.targets,
                "target_share": _round_or_none(
                    row.target_share * 100 if row.target_share is not None else None
                ),
                "air_yards_share": _round_or_none(
                    row.air_yards_share * 100 if row.air_yards_share is not None else None
                ),
                "wopr": row.wopr,
                "fantasy_points_ppr": row.fantasy_points_ppr,
                "receptions": row.receptions,
                "receiving_yards": row.receiving_yards,
                "receiving_tds": row.receiving_tds,
                "average_depth_of_target": _round_or_none(
                    row.receiving_air_yards / row.targets
                    if row.receiving_air_yards is not None
                    and row.targets is not None
                    and row.targets > 0
                    else None
                ),
                "team_passing_yards": row.team_passing_yards,
            }
            for row in session.scalars(
                select(OpportunityWeek)
                .where(
                    OpportunityWeek.season == season,
                    OpportunityWeek.gsis_id == mapping.gsis_id,
                )
                .order_by(OpportunityWeek.week, OpportunityWeek.game_id)
            )
        ]
    league_ids = filtered_league_ids(session, filters)
    leagues = (
        list(session.scalars(select(League).where(League.id.in_(league_ids)))) if league_ids else []
    )
    owned, league_rows = _roster_context(session, leagues)
    status_by_league = owned.get(espn_player_id, {})
    for row in league_rows:
        if row["state"] == "current":
            row["state"] = status_by_league.get(row["league_id"], "available")
    return {
        "season": season,
        "player": {
            "espn_player_id": player.espn_player_id,
            "player_name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
            "gsis_id": mapping.gsis_id if mapping else None,
            "mapping_status": mapping.status if mapping else "unmatched",
        },
        "summary": summary,
        "weeks": weeks,
        "leagues": league_rows,
        "source": all_data["source"],
    }


def opportunity_doctor(session: Session, season: int) -> dict[str, Any]:
    status = opportunity_status(session, season)
    universe = _build_opportunity_universe(session, PortfolioFilters(season=season))
    runs = list(
        session.scalars(
            select(OpportunityImport)
            .where(OpportunityImport.season == season)
            .order_by(OpportunityImport.started_at.desc())
            .limit(5)
        )
    )
    stored_rows = (
        session.scalar(
            select(func.count())
            .select_from(OpportunityWeek)
            .where(OpportunityWeek.season == season)
        )
        or 0
    )
    duplicate_groups = (
        select(
            OpportunityWeek.season,
            OpportunityWeek.season_type,
            OpportunityWeek.game_id,
            OpportunityWeek.gsis_id,
        )
        .where(OpportunityWeek.season == season)
        .group_by(
            OpportunityWeek.season,
            OpportunityWeek.season_type,
            OpportunityWeek.game_id,
            OpportunityWeek.gsis_id,
        )
        .having(func.count() > 1)
        .subquery()
    )
    duplicate_keys = session.scalar(select(func.count()).select_from(duplicate_groups)) or 0
    mapping_status = {
        state: count
        for state, count in session.execute(
            select(NflversePlayerMap.status, func.count()).group_by(NflversePlayerMap.status)
        )
    }
    try:
        package_version = version("nflreadpy")
    except PackageNotFoundError:
        package_version = "not-installed"
    chart_diagnostics: dict[str, dict[str, Any]] = {}
    for position in sorted(ELIGIBLE_POSITIONS):
        all_position_rows = [row for row in universe.rows if row["position"] == position]
        position_rows = [row for row in all_position_rows if _has_top_espn_rank(row)]
        position_charts: dict[str, Any] = {}
        for chart_id, spec in _CHART_SPECS.items():
            if position not in spec["positions"]:
                continue
            definition = _chart_definition(chart_id, spec, position_rows, position_rows)
            coordinates: dict[tuple[float, float], int] = defaultdict(int)
            for row in position_rows:
                if (
                    row["sample_games"] >= 2
                    and _finite_metric(row, spec["x_key"])
                    and _finite_metric(row, spec["y_key"])
                ):
                    coordinates[(float(row[spec["x_key"]]), float(row[spec["y_key"]]))] += 1
            position_charts[chart_id] = {
                "rank_limit": CHART_ESPN_RANK_LIMIT,
                "eligible_players": definition["population_point_count"],
                "omitted_players": definition["omitted_count"],
                "omitted_reasons": definition["omitted_reasons"],
                "domain": definition["domain"],
                "references": definition["references"],
                "largest_identical_coordinate_cluster": max(coordinates.values(), default=0),
            }
        position_charts["ranking_coverage"] = {
            "rank_limit": CHART_ESPN_RANK_LIMIT,
            "ranked_players": len(position_rows),
            "excluded_players": len(all_position_rows) - len(position_rows),
        }
        chart_diagnostics[position] = position_charts
    chart_fields = sorted(
        {str(spec[key]) for spec in _CHART_SPECS.values() for key in ("x_key", "y_key")}
    )
    return {
        "status": status,
        "environment": {
            "python": platform.python_version(),
            "nflreadpy": package_version,
            "db_path": str(get_settings().db_file),
            "source_host": SOURCE_HOST,
        },
        "source": {
            "reachability": "not_probed",
            "next_check": "refresh --dry-run fetches and validates without DB mutation",
        },
        "stored_rows": stored_rows,
        "duplicate_keys": duplicate_keys,
        "mapping_status": mapping_status,
        "charts": {
            "source_run_id": status["run_id"],
            "schema_fingerprint": status["schema_fingerprint"],
            "null_or_non_finite_by_field": {
                field: sum(not _finite_metric(row, field) for row in universe.rows)
                for field in chart_fields
            },
            "positions": chart_diagnostics,
        },
        "required_player_columns": sorted(_PLAYER_COLUMNS),
        "required_stat_columns": sorted(_STAT_COLUMNS),
        "runs": [_run_dict(run, _latest_good(session, season)) for run in runs],
    }
