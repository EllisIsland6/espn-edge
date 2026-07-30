"""Pure parsers: raw ESPN JSON → plain dataclasses/dicts.

No DB, no network — so unit tests replay recorded fixtures offline (SPEC 12).
Each parser logs a single schema-drift warning (view + missing path) instead of
crashing when ESPN's shape changes (SPEC 12 "schema drift alarm").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from .espn_constants import (
    STAT_ID_RECEPTIONS,
    STAT_SOURCE_ACTUAL,
    STAT_SOURCE_PROJECTED,
    is_starter_slot,
    nfl_team_name,
    position_name,
    slot_name,
)

log = logging.getLogger("espn.parse")


def _warn_missing(view: str, path: str) -> None:
    log.warning("schema-drift: view=%s missing=%s", view, path)


def normalize_swid(swid: str | None) -> str:
    """Strip braces + uppercase so account SWIDs match team-owner SWIDs (SPEC 2.7)."""
    if not swid:
        return ""
    return swid.strip().strip("{}").upper()


# --------------------------------------------------------------------------- #
# mSettings
# --------------------------------------------------------------------------- #
@dataclass
class LeagueSettings:
    name: str | None
    size: int | None
    scoring_json: dict
    lineup_slots: dict
    draft_type: str | None
    scoring_label: str
    playoff_team_count: int | None
    current_week: int | None
    current_scoring_period: int | None
    current_matchup_period: int | None
    drafted: bool | None


def parse_settings(data: dict) -> LeagueSettings:
    settings = data.get("settings") or {}
    if not settings:
        _warn_missing("mSettings", "settings")
    scoring = settings.get("scoringSettings") or {}
    roster = settings.get("rosterSettings") or {}
    draft = settings.get("draftSettings") or {}
    schedule = settings.get("scheduleSettings") or {}
    status = data.get("status") or {}

    lineup_slots = roster.get("lineupSlotCounts") or {}
    current_matchup_period = status.get("currentMatchupPeriod")
    current_scoring_period = data.get("scoringPeriodId")
    if not isinstance(current_scoring_period, int) or current_scoring_period <= 0:
        current_scoring_period = current_matchup_period
    current_week = current_matchup_period or status.get("latestScoringPeriod")

    return LeagueSettings(
        name=settings.get("name"),
        size=settings.get("size"),
        scoring_json=scoring,
        lineup_slots=lineup_slots,
        draft_type=str(draft.get("type")) if draft.get("type") is not None else None,
        scoring_label=classify_scoring(scoring),
        playoff_team_count=schedule.get("playoffTeamCount"),
        current_week=current_week,
        current_scoring_period=current_scoring_period,
        current_matchup_period=current_matchup_period,
        drafted=(data.get("draftDetail") or {}).get("drafted"),
    )


def classify_scoring(scoring: dict) -> str:
    """PPR / Half-PPR / Standard from the reception scoring item (SPEC 2.4)."""
    items = scoring.get("scoringItems") or []
    for it in items:
        if it.get("statId") == STAT_ID_RECEPTIONS:
            pts = it.get("points", it.get("pointsOverrides", {}).get("16"))
            try:
                pts = float(pts)
            except (TypeError, ValueError):
                continue
            if pts >= 1.0:
                return "PPR"
            if pts >= 0.5:
                return "Half-PPR"
            return "Standard"
    # Fall back to the coarse scoringType if reception item absent.
    return str(scoring.get("scoringType") or "Unknown")


# --------------------------------------------------------------------------- #
# mTeam
# --------------------------------------------------------------------------- #
@dataclass
class ParsedTeam:
    espn_team_id: int
    name: str
    abbrev: str | None
    owner_swids: list[str]
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    standing: int | None
    logo_url: str | None


def _team_name(team: dict) -> str:
    # Newer API: "name"; older: location + nickname.
    name = team.get("name")
    if name:
        return name
    loc = (team.get("location") or "").strip()
    nick = (team.get("nickname") or "").strip()
    combined = f"{loc} {nick}".strip()
    return combined or f"Team {team.get('id')}"


def parse_teams(data: dict) -> list[ParsedTeam]:
    teams = data.get("teams")
    if teams is None:
        _warn_missing("mTeam", "teams")
        return []
    out: list[ParsedTeam] = []
    for t in teams:
        record = ((t.get("record") or {}).get("overall")) or {}
        owners = [normalize_swid(o) for o in (t.get("owners") or [])]
        out.append(
            ParsedTeam(
                espn_team_id=t.get("id"),
                name=_team_name(t),
                abbrev=t.get("abbrev"),
                owner_swids=owners,
                wins=int(record.get("wins", 0) or 0),
                losses=int(record.get("losses", 0) or 0),
                ties=int(record.get("ties", 0) or 0),
                points_for=float(record.get("pointsFor", t.get("points", 0)) or 0),
                points_against=float(record.get("pointsAgainst", t.get("pointsAdjusted", 0)) or 0),
                standing=t.get("playoffSeed") or t.get("rankCalculatedFinal"),
                logo_url=t.get("logo"),
            )
        )
    return out


def parse_members(data: dict) -> list[str]:
    """Return normalized SWIDs of league members (data['members'])."""
    members = data.get("members") or []
    return [normalize_swid(m.get("id")) for m in members if m.get("id")]


def detect_my_team(teams: list[ParsedTeam], account_swid: str | None) -> int | None:
    """Match account SWID against team owners → espn_team_id (SPEC 2.7)."""
    target = normalize_swid(account_swid)
    if not target:
        return None
    for t in teams:
        if target in t.owner_swids:
            return t.espn_team_id
    return None


# --------------------------------------------------------------------------- #
# mDraftDetail
# --------------------------------------------------------------------------- #
@dataclass
class ParsedPick:
    overall: int | None
    round: int | None
    round_pick: int | None
    espn_team_id: int | None
    espn_player_id: int | None
    keeper: bool
    autodraft: bool
    bid_amount: int | None


def parse_draft(data: dict) -> tuple[bool, list[ParsedPick]]:
    detail = data.get("draftDetail") or {}
    drafted = bool(detail.get("drafted"))
    picks_raw = detail.get("picks") or []
    picks: list[ParsedPick] = []
    for p in picks_raw:
        # Pre-draft leagues pre-populate every draft SLOT (rounds × teams) with
        # the empty-slot sentinel playerId == -1 (verified live 2026-07-07). Skip
        # only that sentinel — NOT all negatives: D/ST picks use large negative
        # ids (e.g. -16033, lineupSlotId 16), which are real picks ESPN/espn-api
        # count (SPEC §5: skip if undrafted; §2.4 verified note).
        pid = p.get("playerId")
        if pid is None or pid == -1:
            continue
        picks.append(
            ParsedPick(
                overall=p.get("overallPickNumber"),
                round=p.get("roundId"),
                round_pick=p.get("roundPickNumber"),
                espn_team_id=p.get("teamId"),
                espn_player_id=p.get("playerId"),
                keeper=bool(p.get("keeper")),
                # autoDraftTypeId 0 = manual; >0 = some auto mode.
                autodraft=bool(p.get("autoDraftTypeId")),
                bid_amount=p.get("bidAmount"),
            )
        )
    return drafted, picks


# --------------------------------------------------------------------------- #
# mMatchup / mMatchupScore
# --------------------------------------------------------------------------- #
@dataclass
class ParsedMatchup:
    week: int | None
    home_espn_team_id: int | None
    away_espn_team_id: int | None
    home_points: float | None
    away_points: float | None
    home_projected_points: float | None
    away_projected_points: float | None
    is_playoff: bool


def _side_total(side: dict, live_key: str, settled_key: str) -> float | None:
    value = side.get(live_key) if live_key in side else side.get(settled_key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_schedule(data: dict) -> list[ParsedMatchup]:
    schedule = data.get("schedule")
    if schedule is None:
        _warn_missing("mMatchup", "schedule")
        return []
    out: list[ParsedMatchup] = []
    for m in schedule:
        home = m.get("home") or {}
        away = m.get("away") or {}
        tier = m.get("playoffTierType") or "NONE"
        out.append(
            ParsedMatchup(
                week=m.get("matchupPeriodId"),
                home_espn_team_id=home.get("teamId"),
                away_espn_team_id=away.get("teamId"),
                home_points=_side_total(home, "totalPointsLive", "totalPoints"),
                away_points=_side_total(away, "totalPointsLive", "totalPoints"),
                home_projected_points=_side_total(
                    home, "totalProjectedPointsLive", "totalProjectedPoints"
                ),
                away_projected_points=_side_total(
                    away, "totalProjectedPointsLive", "totalProjectedPoints"
                ),
                is_playoff=tier != "NONE",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# mBoxscore
# --------------------------------------------------------------------------- #
@dataclass
class ParsedLineupEntry:
    espn_team_id: int | None
    slot: str
    espn_player_id: int | None
    points: float | None
    is_starter: bool


def _roster_entries(side: dict) -> list[dict]:
    roster = side.get("rosterForCurrentScoringPeriod") or side.get("rosterForMatchupPeriod") or {}
    return roster.get("entries") or []


def parse_boxscore_week(data: dict, week: int) -> list[ParsedLineupEntry]:
    """Per-player started/bench points for a single week (SPEC 2.4 mBoxscore)."""
    schedule = data.get("schedule") or []
    out: list[ParsedLineupEntry] = []
    for m in schedule:
        if m.get("matchupPeriodId") != week:
            continue
        for key in ("home", "away"):
            side = m.get(key) or {}
            team_id = side.get("teamId")
            for e in _roster_entries(side):
                slot_id = e.get("lineupSlotId")
                pool = e.get("playerPoolEntry") or {}
                applied = pool.get("appliedStatTotal")
                if applied is None:
                    applied = e.get("appliedStatTotal")
                out.append(
                    ParsedLineupEntry(
                        espn_team_id=team_id,
                        slot=slot_name(slot_id),
                        espn_player_id=e.get("playerId") or pool.get("id"),
                        points=applied,
                        is_starter=is_starter_slot(slot_id),
                    )
                )
    return out


# --------------------------------------------------------------------------- #
# Current scoring-period roster + professional schedule
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParsedProGame:
    opponent: str | None
    kickoff_at: datetime | None
    game_status: str | None


@dataclass
class ParsedCurrentRosterEntry:
    espn_team_id: int
    slot_id: int
    slot_index: int
    espn_player_id: int
    player_name: str | None
    player_position: str | None
    nfl_team: str | None
    opponent: str | None
    kickoff_at: datetime | None
    game_status: str | None
    injury_status: str | None
    actual_points: float | None
    projected_points: float | None


def _game_status(game: dict) -> str | None:
    status = str(game.get("status") or game.get("gameStatus") or "").upper()
    if game.get("completed") is True or status in {"FINAL", "POST", "COMPLETE", "COMPLETED"}:
        return "final"
    if status in {"IN", "LIVE", "IN_PROGRESS", "INPROGRESS"}:
        return "in_progress"
    if status in {"PRE", "PREGAME", "SCHEDULED"}:
        return "pregame"
    return None


def parse_pro_schedule(data: dict, scoring_period: int) -> dict[int, ParsedProGame]:
    teams = (data.get("settings") or {}).get("proTeams")
    if teams is None:
        _warn_missing("proTeamSchedules_wl", "settings.proTeams")
        return {}
    out: dict[int, ParsedProGame] = {}
    for team in teams:
        team_id = team.get("id")
        if not isinstance(team_id, int) or team_id == 0:
            continue
        games_by_period = team.get("proGamesByScoringPeriod") or {}
        if str(scoring_period) not in games_by_period:
            continue
        games = games_by_period.get(str(scoring_period)) or []
        if not games:
            out[team_id] = ParsedProGame(
                opponent=None,
                kickoff_at=None,
                game_status="bye",
            )
            continue
        game = games[0]
        home_id = game.get("homeProTeamId")
        away_id = game.get("awayProTeamId")
        opponent_id = away_id if home_id == team_id else home_id
        out[team_id] = ParsedProGame(
            opponent=nfl_team_name(opponent_id),
            kickoff_at=_epoch_ms_to_dt(game.get("date")),
            game_status=_game_status(game),
        )
    return out


def _weekly_points(player: dict, scoring_period: int, source: int) -> float | None:
    for stats in player.get("stats") or []:
        if stats.get("scoringPeriodId") != scoring_period:
            continue
        if stats.get("statSourceId") != source or stats.get("statSplitTypeId") == 2:
            continue
        value = stats.get("appliedTotal")
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _entry_points(
    entry: dict,
    player: dict,
    scoring_period: int,
) -> tuple[float | None, float | None]:
    pool = entry.get("playerPoolEntry") or {}
    actual = pool.get("appliedStatTotal")
    if actual is None:
        actual = entry.get("appliedStatTotal")
    try:
        actual_value = float(actual) if actual is not None else None
    except (TypeError, ValueError):
        actual_value = None
    if actual_value is None:
        actual_value = _weekly_points(player, scoring_period, STAT_SOURCE_ACTUAL)
    return actual_value, _weekly_points(player, scoring_period, STAT_SOURCE_PROJECTED)


def _current_roster_sources(data: dict, matchup_period: int | None) -> dict[int, list[dict]]:
    by_team: dict[int, list[dict]] = {}
    for team in data.get("teams") or []:
        team_id = team.get("id")
        if isinstance(team_id, int):
            by_team[team_id] = ((team.get("roster") or {}).get("entries") or [])
    for matchup in data.get("schedule") or []:
        if matchup_period is not None and matchup.get("matchupPeriodId") != matchup_period:
            continue
        for side_name in ("home", "away"):
            side = matchup.get(side_name) or {}
            team_id = side.get("teamId")
            entries = _roster_entries(side)
            if isinstance(team_id, int) and entries:
                by_team[team_id] = entries
    return by_team


def parse_current_rosters(
    data: dict,
    scoring_period: int,
    matchup_period: int | None,
    pro_schedule: dict[int, ParsedProGame],
) -> list[ParsedCurrentRosterEntry]:
    out: list[ParsedCurrentRosterEntry] = []
    for team_id, entries in _current_roster_sources(data, matchup_period).items():
        slot_counts: dict[int, int] = {}
        for entry in entries:
            slot_id = entry.get("lineupSlotId")
            pool = entry.get("playerPoolEntry") or {}
            player = pool.get("player") or entry.get("player") or {}
            player_id = entry.get("playerId") or pool.get("id") or player.get("id")
            if not isinstance(slot_id, int) or not isinstance(player_id, int):
                continue
            slot_index = slot_counts.get(slot_id, 0)
            slot_counts[slot_id] = slot_index + 1
            pro_team_id = player.get("proTeamId")
            game = pro_schedule.get(pro_team_id)
            actual, projected = _entry_points(entry, player, scoring_period)
            out.append(
                ParsedCurrentRosterEntry(
                    espn_team_id=team_id,
                    slot_id=slot_id,
                    slot_index=slot_index,
                    espn_player_id=player_id,
                    player_name=player.get("fullName"),
                    player_position=position_name(player.get("defaultPositionId")),
                    nfl_team=nfl_team_name(pro_team_id),
                    opponent=game.opponent if game else None,
                    kickoff_at=game.kickoff_at if game else None,
                    game_status=game.game_status if game else None,
                    injury_status=player.get("injuryStatus"),
                    actual_points=actual,
                    projected_points=projected,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# mTransactions2
# --------------------------------------------------------------------------- #
@dataclass
class ParsedTransaction:
    espn_team_id: int | None
    type: str | None
    week: int | None
    player_in: int | None
    player_out: int | None
    bid: int | None
    executed_at: datetime | None = None


def _epoch_ms_to_dt(ms: int | float | None) -> datetime | None:
    """ESPN dates are epoch milliseconds (UTC). Return None on missing/garbage."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


_TXN_TYPE_MAP = {
    "WAIVER": "waiver",
    "FREEAGENT": "fa_add",
    "TRADE_ACCEPT": "trade",
    "TRADE": "trade",
    "DRAFT": "draft",
    "ROSTER": "roster",
}


def parse_transactions(data: dict) -> list[ParsedTransaction]:
    txns = data.get("transactions")
    if txns is None:
        return []
    out: list[ParsedTransaction] = []
    for tx in txns:
        raw_type = tx.get("type") or ""
        mapped = _TXN_TYPE_MAP.get(raw_type, raw_type.lower() or None)
        player_in = player_out = None
        for item in tx.get("items") or []:
            if item.get("type") == "ADD":
                player_in = item.get("playerId")
            elif item.get("type") == "DROP":
                player_out = item.get("playerId")
        # Completed transactions carry processDate; pending ones only proposedDate.
        executed_at = _epoch_ms_to_dt(tx.get("processDate") or tx.get("proposedDate"))
        out.append(
            ParsedTransaction(
                espn_team_id=tx.get("teamId"),
                type=mapped,
                week=tx.get("scoringPeriodId"),
                player_in=player_in,
                player_out=player_out,
                bid=tx.get("bidAmount"),
                executed_at=executed_at,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# kona_player_info
# --------------------------------------------------------------------------- #
@dataclass
class ParsedPlayer:
    espn_player_id: int
    name: str | None
    position: str
    nfl_team: str | None
    espn_adp: float | None
    espn_pct_owned: float | None
    espn_rank_ppr: float | None
    proj_ros: float | None = None


def _season_projection(player: dict) -> float | None:
    """Full-season projected points = the season-split projection row (SPEC 2.9).

    ESPN `stats[]` rows carry statSourceId (0=actual, 1=projection) and
    statSplitTypeId (0=season, 1=single game). The season projection's
    appliedTotal is our preseason rest-of-season proxy (refined in-season later).
    """
    best: float | None = None
    for st in player.get("stats") or []:
        if st.get("statSourceId") != STAT_SOURCE_PROJECTED:
            continue
        if st.get("statSplitTypeId") not in (0, None):
            continue
        applied = st.get("appliedTotal")
        if applied is None:
            continue
        try:
            best = float(applied)
        except (TypeError, ValueError):
            continue
    return best


def parse_player_pool(data: dict) -> list[ParsedPlayer]:
    players = data.get("players")
    if players is None:
        _warn_missing("kona_player_info", "players")
        return []
    out: list[ParsedPlayer] = []
    for wrapper in players:
        p = wrapper.get("player") or {}
        pid = p.get("id") or wrapper.get("id")
        if pid is None:
            continue
        ownership = p.get("ownership") or {}
        ranks = p.get("draftRanksByRankType") or {}
        ppr = ranks.get("PPR") or ranks.get("STANDARD") or {}
        out.append(
            ParsedPlayer(
                espn_player_id=pid,
                name=p.get("fullName"),
                position=position_name(p.get("defaultPositionId")),
                nfl_team=str(p.get("proTeamId")) if p.get("proTeamId") is not None else None,
                espn_adp=ownership.get("averageDraftPosition"),
                espn_pct_owned=ownership.get("percentOwned"),
                espn_rank_ppr=ppr.get("rank"),
                proj_ros=_season_projection(p),
            )
        )
    return out
