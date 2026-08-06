"""Fantasy Football Calculator ADP ingestion (Phase 23).

FFC is a secondary, current-market ADP source. It is never blended with ESPN's
draft-time ADP, and page reads never make live network calls.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AdpSnapshot, League, Player
from .espn_constants import NFL_TEAM_ABBREVIATIONS
from .parse import classify_scoring

log = logging.getLogger("espn.ffc_adp")

FFC_SOURCE = "ffc"
FFC_SUPPORTED_FORMATS = {"standard", "ppr", "half-ppr", "2qb", "dynasty"}
FFC_SUPPORTED_TEAMS = (8, 10, 12, 14)

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_SPACE_RE = re.compile(r"\s+")

_TEAM_ALIASES = {
    "ARIZONA": "ARI",
    "CARDINALS": "ARI",
    "ATLANTA": "ATL",
    "FALCONS": "ATL",
    "BALTIMORE": "BAL",
    "RAVENS": "BAL",
    "BUFFALO": "BUF",
    "BILLS": "BUF",
    "CAROLINA": "CAR",
    "PANTHERS": "CAR",
    "CHICAGO": "CHI",
    "BEARS": "CHI",
    "CINCINNATI": "CIN",
    "BENGALS": "CIN",
    "CLEVELAND": "CLE",
    "BROWNS": "CLE",
    "DALLAS": "DAL",
    "COWBOYS": "DAL",
    "DENVER": "DEN",
    "BRONCOS": "DEN",
    "DETROIT": "DET",
    "LIONS": "DET",
    "GREEN BAY": "GB",
    "PACKERS": "GB",
    "HOUSTON": "HOU",
    "TEXANS": "HOU",
    "INDIANAPOLIS": "IND",
    "COLTS": "IND",
    "JACKSONVILLE": "JAX",
    "JAGUARS": "JAX",
    "KANSAS CITY": "KC",
    "CHIEFS": "KC",
    "LAS VEGAS": "LV",
    "RAIDERS": "LV",
    "LOS ANGELES RAMS": "LAR",
    "RAMS": "LAR",
    "LOS ANGELES CHARGERS": "LAC",
    "CHARGERS": "LAC",
    "MIAMI": "MIA",
    "DOLPHINS": "MIA",
    "MINNESOTA": "MIN",
    "VIKINGS": "MIN",
    "NEW ENGLAND": "NE",
    "PATRIOTS": "NE",
    "NEW ORLEANS": "NO",
    "SAINTS": "NO",
    "NEW YORK GIANTS": "NYG",
    "GIANTS": "NYG",
    "NEW YORK JETS": "NYJ",
    "JETS": "NYJ",
    "PHILADELPHIA": "PHI",
    "EAGLES": "PHI",
    "PITTSBURGH": "PIT",
    "STEELERS": "PIT",
    "SAN FRANCISCO": "SF",
    "49ERS": "SF",
    "SEAHAWKS": "SEA",
    "SEATTLE": "SEA",
    "TAMPA BAY": "TB",
    "BUCCANEERS": "TB",
    "TENNESSEE": "TEN",
    "TITANS": "TEN",
    "WASHINGTON": "WSH",
    "COMMANDERS": "WSH",
    "WAS": "WSH",
    "JAC": "JAX",
    "LA": "LAR",
}
_ABBR_BY_ID = {str(k): v for k, v in NFL_TEAM_ABBREVIATIONS.items()}

# Manual overrides are deliberately tiny and explicit. Key = normalized
# (name, position, nfl_team), value = FFC player id.
FFC_MANUAL_OVERRIDES: dict[tuple[str, str, str], int] = {}


@dataclass(frozen=True)
class FfcRequest:
    requested_format: str
    requested_teams: int
    used_format: str
    used_teams: int
    year: int
    exact_match: bool


@dataclass(frozen=True)
class FfcPlayer:
    ffc_id: int | None
    name: str
    position: str
    nfl_team: str | None
    adp: float | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class FfcMatchReport:
    matched_players: int
    unmatched_players: list[dict[str, Any]]
    snapshot_ids: list[int]
    requests: list[FfcRequest]


def ffc_format_for_league(league: League) -> str:
    label = classify_scoring(league.scoring_json or {}).lower()
    if "half" in label:
        return "half-ppr"
    if "ppr" in label:
        return "ppr"
    if "standard" in label:
        return "standard"
    return "ppr"


def _nearest_supported_teams(size: int | None) -> int:
    if size is None:
        return 12
    return min(FFC_SUPPORTED_TEAMS, key=lambda v: (abs(v - size), v))


def ffc_request_for_league(league: League, year: int) -> FfcRequest:
    requested_format = ffc_format_for_league(league)
    requested_teams = int(league.size or 12)
    used_format = requested_format if requested_format in FFC_SUPPORTED_FORMATS else "ppr"
    used_teams = (
        requested_teams
        if requested_teams in FFC_SUPPORTED_TEAMS
        else _nearest_supported_teams(league.size)
    )
    return FfcRequest(
        requested_format=requested_format,
        requested_teams=requested_teams,
        used_format=used_format,
        used_teams=used_teams,
        year=year,
        exact_match=(requested_format == used_format and requested_teams == used_teams),
    )


def dedupe_ffc_requests(leagues: list[League], year: int) -> list[FfcRequest]:
    by_key: dict[tuple[str, int, int], FfcRequest] = {}
    for league in leagues:
        req = ffc_request_for_league(league, year)
        by_key.setdefault((req.used_format, req.used_teams, req.year), req)
    return list(by_key.values())


def _snapshot_year(snapshot: AdpSnapshot) -> int | None:
    payload = snapshot.payload_json or {}
    meta = payload.get("_edge_meta") if isinstance(payload, dict) else None
    year = (meta or {}).get("year") if isinstance(meta, dict) else None
    try:
        return int(year)
    except (TypeError, ValueError):
        return None


def latest_ffc_snapshot(
    session: Session, fmt: str, teams: int, year: int
) -> AdpSnapshot | None:
    rows = list(
        session.scalars(
            select(AdpSnapshot)
            .where(
                AdpSnapshot.source == FFC_SOURCE,
                AdpSnapshot.format == fmt,
                AdpSnapshot.teams == teams,
            )
            .order_by(AdpSnapshot.pulled_at.desc(), AdpSnapshot.id.desc())
        )
    )
    for row in rows:
        if _snapshot_year(row) == year:
            return row
    return None


def _fresh(snapshot: AdpSnapshot, ttl: timedelta) -> bool:
    pulled_at = snapshot.pulled_at
    if pulled_at.tzinfo is None:
        pulled_at = pulled_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - pulled_at < ttl


def fetch_ffc_payload(req: FfcRequest, client: httpx.Client | None = None) -> dict:
    settings = get_settings()
    owns_client = client is None
    http = client or httpx.Client(timeout=30.0)
    try:
        url = f"{settings.ffc_api_host.rstrip('/')}/api/v1/adp/{req.used_format}"
        resp = http.get(url, params={"teams": req.used_teams, "year": req.year})
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("FFC ADP response was not a JSON object")
        return payload
    finally:
        if owns_client:
            http.close()


def _persist_snapshot(session: Session, req: FfcRequest, payload: dict) -> AdpSnapshot:
    payload = dict(payload)
    payload["_edge_meta"] = {
        "year": req.year,
        "requested_format": req.requested_format,
        "requested_teams": req.requested_teams,
        "used_format": req.used_format,
        "used_teams": req.used_teams,
        "exact_match": req.exact_match,
    }
    snap = AdpSnapshot(
        source=FFC_SOURCE,
        pulled_at=datetime.now(UTC),
        format=req.used_format,
        teams=req.used_teams,
        payload_json=payload,
    )
    session.add(snap)
    session.flush()
    return snap


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_position(position: str | None) -> str:
    raw = (position or "").strip().upper().replace("DEF", "D/ST").replace("DST", "D/ST")
    if raw in {"PK", "KICKER"}:
        return "K"
    return raw


def normalize_team(team: str | int | None) -> str:
    if team is None:
        return ""
    raw = str(team).strip().upper()
    if raw in _ABBR_BY_ID:
        return _ABBR_BY_ID[raw]
    raw = raw.replace(".", "").replace("-", " ")
    return _TEAM_ALIASES.get(raw, raw)


def normalize_player_name(
    name: str | None, position: str | None = None, team: str | None = None
) -> str:
    text = unicodedata.normalize("NFKD", (name or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    if normalize_position(position) == "D/ST":
        team_norm = normalize_team(team)
        if team_norm:
            return team_norm.lower()
        text = text.replace("d/st", "").replace("defense", "").replace("special teams", "")
    text = _SUFFIX_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def player_identity_key(
    name: str | None, position: str | None, team: str | int | None
) -> tuple[str, str, str]:
    pos = normalize_position(position)
    team_norm = normalize_team(team)
    return (normalize_player_name(name, pos, team_norm), pos, team_norm)


def parse_ffc_players(payload: dict) -> list[FfcPlayer]:
    raw_players = payload.get("players") or []
    out: list[FfcPlayer] = []
    for row in raw_players:
        if not isinstance(row, dict):
            continue
        name = row.get("name") or row.get("player_name")
        if not name:
            continue
        ffc_id = row.get("player_id", row.get("id", row.get("ffc_id")))
        try:
            ffc_id_int = int(ffc_id) if ffc_id is not None else None
        except (TypeError, ValueError):
            ffc_id_int = None
        out.append(
            FfcPlayer(
                ffc_id=ffc_id_int,
                name=str(name),
                position=normalize_position(row.get("position") or row.get("pos")),
                nfl_team=normalize_team(
                    row.get("team") or row.get("nfl_team") or row.get("team_abbrev")
                ),
                adp=_to_float(row.get("adp")),
                raw=row,
            )
        )
    return out


def apply_ffc_snapshots_to_players(
    session: Session, snapshots: list[AdpSnapshot]
) -> FfcMatchReport:
    ffc_players: list[FfcPlayer] = []
    requests: list[FfcRequest] = []
    for snap in snapshots:
        payload = snap.payload_json or {}
        ffc_players.extend(parse_ffc_players(payload))
        meta = payload.get("_edge_meta") if isinstance(payload, dict) else {}
        if isinstance(meta, dict):
            requests.append(
                FfcRequest(
                    requested_format=str(meta.get("requested_format") or snap.format or ""),
                    requested_teams=int(meta.get("requested_teams") or snap.teams or 0),
                    used_format=str(meta.get("used_format") or snap.format or ""),
                    used_teams=int(meta.get("used_teams") or snap.teams or 0),
                    year=int(meta.get("year") or 0),
                    exact_match=bool(meta.get("exact_match", True)),
                )
            )

    by_key: dict[tuple[str, str, str], FfcPlayer] = {}
    by_id = {p.ffc_id: p for p in ffc_players if p.ffc_id is not None}
    for player in ffc_players:
        by_key.setdefault(
            player_identity_key(player.name, player.position, player.nfl_team), player
        )

    matched = 0
    unmatched: list[dict[str, Any]] = []
    for player in session.scalars(select(Player)):
        key = player_identity_key(player.name, player.position, player.nfl_team)
        match = None
        override = FFC_MANUAL_OVERRIDES.get(key)
        if override is not None:
            match = by_id.get(override)
        if match is None:
            match = by_key.get(key)
        if match is None or match.adp is None:
            player.ffc_id = None
            player.ffc_adp = None
            if player.name and player.position in {"QB", "RB", "WR", "TE", "K", "D/ST"}:
                unmatched.append(
                    {
                        "espn_player_id": player.espn_player_id,
                        "name": player.name,
                        "position": player.position,
                        "nfl_team": normalize_team(player.nfl_team),
                    }
                )
            continue
        matched += 1
        player.ffc_id = match.ffc_id
        player.ffc_adp = match.adp
    session.flush()
    if unmatched:
        log.info("FFC ADP unmatched players: %s", len(unmatched))
    return FfcMatchReport(
        matched_players=matched,
        unmatched_players=unmatched,
        snapshot_ids=[snap.id for snap in snapshots],
        requests=requests,
    )


def refresh_ffc_adp(
    session: Session,
    leagues: list[League],
    *,
    year: int | None = None,
    force: bool = False,
    client: httpx.Client | None = None,
) -> FfcMatchReport:
    """Fetch the deduped FFC ADP sets for leagues and apply them to Player rows.

    A fresh snapshot (default TTL 24h) is reused unless `force=True`. This function
    is intentionally not called by read endpoints.
    """
    settings = get_settings()
    season = year or settings.season
    ttl = timedelta(hours=settings.ffc_adp_ttl_hours)
    snapshots: list[AdpSnapshot] = []
    for req in dedupe_ffc_requests(leagues, season):
        snap = latest_ffc_snapshot(session, req.used_format, req.used_teams, req.year)
        if snap is None or force or not _fresh(snap, ttl):
            payload = fetch_ffc_payload(req, client=client)
            snap = _persist_snapshot(session, req, payload)
        snapshots.append(snap)
    return apply_ffc_snapshots_to_players(session, snapshots)
