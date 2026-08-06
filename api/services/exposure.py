"""Portfolio draft exposure analytics (Phase 23).

Aggregates drafted rosters across the portfolio. This is read-only service code:
no React math, no ESPN/FFC calls, and no writes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import DraftPick, League, Player, Team
from .espn_constants import nfl_team_name
from .metrics import pick_value
from .portfolio_filters import PortfolioFilters, filtered_league_ids

ExposureScope = Literal["me", "opponents"]
ExposureView = Literal["rostered", "field_owned", "all"]

_POSITIONS = ("QB", "RB", "WR", "TE", "D/ST", "K")


@dataclass(frozen=True)
class _PickRow:
    league_id: int
    league_name: str | None
    league_size: int | None
    draft_type: str | None
    team_id: int
    team_name: str | None
    espn_player_id: int
    player_name: str | None
    position: str | None
    nfl_team: str | None
    overall: int | None
    round: int | None
    round_pick: int | None
    keeper: bool
    adp_at_draft: float | None
    ffc_adp: float | None


@dataclass(frozen=True)
class _ScopeData:
    scope: ExposureScope
    teams: list[Team]
    teams_in_scope: int
    leagues_in_scope: int
    auction_teams: int
    rows: list[_PickRow]
    snake_rows: list[_PickRow]
    players: list[dict]
    players_by_id: dict[int, dict]
    positional_spend: list[dict]
    round_fingerprint: list[dict]
    nfl_team_concentration: list[dict]
    core_dart: dict
    keeper_picks: int
    auction_picks: int


def _pct(numer: float, denom: float) -> float:
    return round(numer / denom * 100.0, 1) if denom else 0.0


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _mean_raw(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _nfl_team_label(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return nfl_team_name(int(value))
    except ValueError:
        return value


def _drafted_league_ids(session: Session, league_ids: list[int]) -> set[int]:
    if not league_ids:
        return set()
    rows = session.execute(
        select(DraftPick.league_id, func.count())
        .where(DraftPick.league_id.in_(league_ids), DraftPick.espn_player_id.is_not(None))
        .group_by(DraftPick.league_id)
    ).all()
    return {league_id for league_id, count in rows if count > 0}


def _teams_in_scope(session: Session, league_ids: set[int], scope: ExposureScope) -> list[Team]:
    if not league_ids:
        return []
    stmt = select(Team).where(Team.league_id.in_(league_ids))
    if scope == "me":
        stmt = stmt.where(Team.is_me.is_(True))
    else:
        stmt = stmt.where(Team.is_me.is_(False))
    teams = list(session.scalars(stmt))
    # A drafted team denominator needs at least one pick row.
    with_picks = {
        tid
        for (tid,) in session.execute(
            select(DraftPick.team_id)
            .where(DraftPick.league_id.in_(league_ids), DraftPick.team_id.is_not(None))
            .group_by(DraftPick.team_id)
        ).all()
    }
    return [team for team in teams if team.id in with_picks]


def _pick_rows(session: Session, team_ids: list[int]) -> list[_PickRow]:
    if not team_ids:
        return []
    rows = session.execute(
        select(
            League.id,
            League.name,
            League.size,
            League.draft_type,
            Team.id,
            Team.name,
            Player.espn_player_id,
            Player.name,
            Player.position,
            Player.nfl_team,
            DraftPick.overall,
            DraftPick.round,
            DraftPick.round_pick,
            DraftPick.keeper,
            DraftPick.adp_at_draft,
            Player.ffc_adp,
        )
        .join(Team, Team.id == DraftPick.team_id)
        .join(League, League.id == DraftPick.league_id)
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id)
        .where(DraftPick.team_id.in_(team_ids))
    ).all()
    return [_PickRow(*row) for row in rows]


def _team_count_with_auctions(session: Session, teams: list[Team]) -> tuple[int, int]:
    if not teams:
        return 0, 0
    leagues = {
        league.id: league
        for league in session.scalars(
            select(League).where(League.id.in_({team.league_id for team in teams}))
        )
    }
    auctions = sum(
        1
        for team in teams
        if (leagues.get(team.league_id).draft_type or "").upper() == "AUCTION"
    )
    return len(teams), auctions


def _player_rows(
    rows: list[_PickRow],
    teams_in_scope: int,
    leagues_in_scope: int,
) -> list[dict]:
    by_player: dict[int, list[_PickRow]] = defaultdict(list)
    for row in rows:
        by_player[row.espn_player_id].append(row)

    players = []
    for player_id, picks in by_player.items():
        team_count = len({row.team_id for row in picks})
        league_count = len({row.league_id for row in picks})
        overalls = [float(row.overall) for row in picks if row.overall is not None]
        snake_values = [
            pick_value(row.overall)
            for row in picks
            if row.overall is not None and (row.draft_type or "").upper() != "AUCTION"
        ]
        first = picks[0]
        leagues_for_player = []
        for row in sorted(
            picks,
            key=lambda p: (p.league_name or "", p.team_name or "", p.overall or 9999),
        ):
            leagues_for_player.append(
                {
                    "league_id": row.league_id,
                    "league_name": row.league_name,
                    "team_id": row.team_id,
                    "team_name": row.team_name,
                    "overall": row.overall,
                    "round": row.round,
                    "round_pick": row.round_pick,
                    "draft_type": row.draft_type,
                    "keeper": row.keeper,
                }
            )
        players.append(
            {
                "espn_player_id": player_id,
                "player_name": first.player_name,
                "position": first.position,
                "nfl_team": _nfl_team_label(first.nfl_team),
                "rostered_teams": team_count,
                "teams_in_scope": teams_in_scope,
                "exposure_pct": _pct(team_count, teams_in_scope),
                "share": f"{team_count} / {teams_in_scope}",
                "rostered_leagues": league_count,
                "leagues_in_scope": leagues_in_scope,
                "league_exposure_pct": _pct(league_count, leagues_in_scope),
                "league_share": f"{league_count} / {leagues_in_scope}",
                "avg_overall": _mean(overalls),
                "min_overall": int(min(overalls)) if overalls else None,
                "max_overall": int(max(overalls)) if overalls else None,
                "avg_pick_value": _mean(snake_values),
                "auction_rosters": sum(
                    1 for row in picks if (row.draft_type or "").upper() == "AUCTION"
                ),
                "leagues": leagues_for_player,
            }
        )
    players.sort(
        key=lambda row: (
            -row["rostered_teams"],
            row["avg_overall"] or 9999,
            row["player_name"] or "",
        )
    )
    return players


def _positional_spend(rows: list[_PickRow]) -> list[dict]:
    total_pick_value = sum(
        pick_value(row.overall) for row in rows if row.overall is not None
    )
    by_pos_value: Counter[str] = Counter()
    by_pos_count: Counter[str] = Counter()
    for row in rows:
        pos = row.position or "?"
        by_pos_count[pos] += 1
        if row.overall is not None:
            by_pos_value[pos] += pick_value(row.overall)
    return [
        {
            "position": pos,
            "pick_count": by_pos_count.get(pos, 0),
            "pick_value": round(by_pos_value.get(pos, 0.0), 2),
            "pick_value_pct": _pct(by_pos_value.get(pos, 0.0), total_pick_value),
            "total_pick_value": round(total_pick_value, 2),
        }
        for pos in sorted(
            set(_POSITIONS) | set(by_pos_count),
            key=lambda p: (_POSITIONS.index(p) if p in _POSITIONS else 99, p),
        )
        if by_pos_count.get(pos, 0)
    ]


def _round_fingerprint(rows: list[_PickRow]) -> list[dict]:
    bucket_pos: dict[tuple[str, str], int] = Counter()
    bucket_totals: Counter[str] = Counter()
    teams_in_scope = len({row.team_id for row in rows})
    for row in rows:
        round_number = row.round
        if round_number is None and row.overall is not None and row.league_size:
            round_number = (row.overall - 1) // row.league_size + 1
        if round_number is None:
            continue
        bucket = str(round_number)
        pos = row.position or "?"
        bucket_pos[(bucket, pos)] += 1
        bucket_totals[bucket] += 1
    return [
        {
            "bucket": bucket,
            "position": pos,
            "pick_count": count,
            "picks_per_team": round(count / teams_in_scope, 2) if teams_in_scope else 0.0,
            "pick_pct": _pct(count, bucket_totals[bucket]),
            "bucket_picks": bucket_totals[bucket],
        }
        for (bucket, pos), count in sorted(bucket_pos.items())
    ]


def _nfl_team_concentration(rows: list[_PickRow], teams_in_scope: int) -> list[dict]:
    instances: Counter[str] = Counter()
    teams_with_player: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        team = _nfl_team_label(row.nfl_team) or "FA"
        instances[team] += 1
        teams_with_player[team].add(row.team_id)

    out = []
    for team in sorted(instances, key=lambda t: (-len(teams_with_player[t]), -instances[t], t)):
        team_count = len(teams_with_player[team])
        instance_count = instances[team]
        out.append(
            {
                "nfl_team": team,
                # Backward-compatible names now mean team penetration, not player instances.
                "rostered_teams": team_count,
                "teams_in_scope": teams_in_scope,
                "exposure_pct": _pct(team_count, teams_in_scope),
                "share": f"{team_count} / {teams_in_scope}",
                "teams_with_player": team_count,
                "player_team_instances": instance_count,
                "penetration_pct": _pct(team_count, teams_in_scope),
                "penetration_share": f"{team_count} / {teams_in_scope}",
                "players_per_team": (
                    round(instance_count / teams_in_scope, 2) if teams_in_scope else 0.0
                ),
                "players_per_team_share": f"{instance_count} / {teams_in_scope}",
            }
        )
    return out


def _merge_positional_spend(primary: list[dict], field: list[dict]) -> list[dict]:
    primary_by_pos = {row["position"]: row for row in primary}
    field_by_pos = {row["position"]: row for row in field}
    positions = sorted(
        set(primary_by_pos) | set(field_by_pos),
        key=lambda p: (_POSITIONS.index(p) if p in _POSITIONS else 99, p),
    )
    out = []
    for pos in positions:
        base = dict(
            primary_by_pos.get(
                pos,
                {
                    "position": pos,
                    "pick_count": 0,
                    "pick_value": 0.0,
                    "pick_value_pct": 0.0,
                    "total_pick_value": 0.0,
                },
            )
        )
        field_row = field_by_pos.get(pos, {})
        base.update(
            {
                "field_pick_count": int(field_row.get("pick_count", 0)),
                "field_pick_value": float(field_row.get("pick_value", 0.0)),
                "field_pick_value_pct": float(field_row.get("pick_value_pct", 0.0)),
                "field_total_pick_value": float(field_row.get("total_pick_value", 0.0)),
                "leverage_pp": round(
                    float(base.get("pick_value_pct", 0.0))
                    - float(field_row.get("pick_value_pct", 0.0)),
                    1,
                ),
            }
        )
        out.append(base)
    return out


def _merge_round_fingerprint(primary: list[dict], field: list[dict]) -> list[dict]:
    primary_by_key = {(row["bucket"], row["position"]): row for row in primary}
    field_by_key = {(row["bucket"], row["position"]): row for row in field}
    positions = (
        set(_POSITIONS)
        | {key[1] for key in primary_by_key}
        | {key[1] for key in field_by_key}
    )
    rounds = {
        int(row["bucket"])
        for row in primary + field
        if str(row.get("bucket", "")).isdigit()
    }
    fixed_domain_keys = {
        (str(round_number), position)
        for position in positions
        for round_number in range(1, max(rounds, default=0) + 1)
    }
    keys = sorted(
        set(primary_by_key) | set(field_by_key) | fixed_domain_keys,
        key=lambda key: (
            int(key[0].split("-", 1)[0]),
            _POSITIONS.index(key[1]) if key[1] in _POSITIONS else 99,
            key[1],
        ),
    )
    out = []
    for bucket, pos in keys:
        base = dict(
            primary_by_key.get(
                (bucket, pos),
                {
                    "bucket": bucket,
                    "position": pos,
                    "pick_count": 0,
                    "picks_per_team": 0.0,
                    "pick_pct": 0.0,
                    "bucket_picks": 0,
                },
            )
        )
        field_row = field_by_key.get((bucket, pos), {})
        base.update(
            {
                "field_pick_count": int(field_row.get("pick_count", 0)),
                "field_picks_per_team": float(field_row.get("picks_per_team", 0.0)),
                "field_pick_pct": float(field_row.get("pick_pct", 0.0)),
                "field_bucket_picks": int(field_row.get("bucket_picks", 0)),
                "leverage_pp": round(
                    float(base.get("pick_pct", 0.0)) - float(field_row.get("pick_pct", 0.0)),
                    1,
                ),
            }
        )
        out.append(base)
    return out


def _blank_player_from_field(
    field_row: dict,
    *,
    teams_in_scope: int,
    leagues_in_scope: int,
) -> dict:
    return {
        "espn_player_id": field_row["espn_player_id"],
        "player_name": field_row.get("player_name"),
        "position": field_row.get("position"),
        "nfl_team": field_row.get("nfl_team"),
        "rostered_teams": 0,
        "teams_in_scope": teams_in_scope,
        "exposure_pct": 0.0,
        "share": f"0 / {teams_in_scope}",
        "rostered_leagues": 0,
        "leagues_in_scope": leagues_in_scope,
        "league_exposure_pct": 0.0,
        "league_share": f"0 / {leagues_in_scope}",
        "avg_overall": None,
        "min_overall": None,
        "max_overall": None,
        "avg_pick_value": None,
        "auction_rosters": 0,
        "leagues": [],
    }


def _merge_player_leverage(me: _ScopeData, field: _ScopeData, scope: ExposureScope) -> list[dict]:
    primary = me if scope == "me" else field
    players_by_id = {row["espn_player_id"]: dict(row) for row in primary.players}
    if scope == "me":
        for player_id, field_row in field.players_by_id.items():
            players_by_id.setdefault(
                player_id,
                _blank_player_from_field(
                    field_row,
                    teams_in_scope=me.teams_in_scope,
                    leagues_in_scope=me.leagues_in_scope,
                ),
            )
    for player_id, row in list(players_by_id.items()):
        me_row = me.players_by_id.get(player_id)
        field_row = field.players_by_id.get(player_id)
        my_leagues = int(me_row.get("rostered_leagues", 0)) if me_row else 0
        field_leagues = int(field_row.get("rostered_leagues", 0)) if field_row else 0
        my_pct = _pct(my_leagues, me.leagues_in_scope)
        field_pct = _pct(field_leagues, field.leagues_in_scope)
        field_slot_pct = float(field_row["exposure_pct"]) if field_row else 0.0
        field_slot_share = field_row["share"] if field_row else f"0 / {field.teams_in_scope}"
        row.update(
            {
                "my_rostered_teams": int(me_row["rostered_teams"]) if me_row else 0,
                "my_teams_in_scope": me.teams_in_scope,
                "my_rostered_leagues": my_leagues,
                "my_leagues_in_scope": me.leagues_in_scope,
                "my_exposure_pct": my_pct,
                "my_share": (
                    me_row.get("league_share", f"{my_leagues} / {me.leagues_in_scope}")
                    if me_row
                    else f"0 / {me.leagues_in_scope}"
                ),
                "field_rostered_teams": (
                    int(field_row["rostered_teams"]) if field_row else 0
                ),
                "field_teams_in_scope": field.teams_in_scope,
                "field_rostered_leagues": field_leagues,
                "field_leagues_in_scope": field.leagues_in_scope,
                "field_exposure_pct": field_pct,
                "field_share": (
                    field_row.get("league_share", f"{field_leagues} / {field.leagues_in_scope}")
                    if field_row
                    else f"0 / {field.leagues_in_scope}"
                ),
                "field_slot_pct": field_slot_pct,
                "field_slot_share": field_slot_share,
                "leverage_pp": round(my_pct - field_pct, 1),
            }
        )
        if scope == "me":
            row["exposure_pct"] = my_pct
            row["share"] = row["my_share"]
    players = list(players_by_id.values())
    players.sort(
        key=lambda row: (
            -float(row.get("leverage_pp") or 0.0),
            -float(row.get("my_exposure_pct") or row.get("exposure_pct") or 0.0),
            row.get("player_name") or "",
        )
    )
    return players


def players_for_view(players: list[dict], view: ExposureView) -> list[dict]:
    if view == "rostered":
        return [
            row
            for row in players
            if float(row.get("my_exposure_pct") or row.get("exposure_pct") or 0.0) > 0.0
        ]
    if view == "field_owned":
        rows = [
            row
            for row in players
            if float(row.get("my_exposure_pct") or 0.0) == 0.0
            and float(row.get("field_exposure_pct") or 0.0) > 0.0
        ]
        return sorted(
            rows,
            key=lambda row: (
                -float(row.get("field_exposure_pct") or 0.0),
                -float(row.get("field_slot_pct") or 0.0),
                row.get("player_name") or "",
            ),
        )
    return list(players)


def _view_summaries(players: list[dict]) -> dict:
    return {
        "rostered": {
            "row_count": len(players_for_view(players, "rostered")),
            "default_sort": "leverage_desc",
        },
        "field_owned": {
            "row_count": len(players_for_view(players, "field_owned")),
            "default_sort": "field_exposure_desc",
        },
        "all": {
            "row_count": len(players),
            "default_sort": "leverage_desc",
        },
    }


def _largest_market_move(rows: list[_PickRow], players: list[dict]) -> dict | None:
    min_exposure_pct = 5.0
    exposed = {
        row["espn_player_id"]: row
        for row in players
        if row.get("rostered_teams", 0) > 0
        and float(row.get("exposure_pct") or 0.0) >= min_exposure_pct
    }
    draft_adp_by_player: dict[int, list[float]] = defaultdict(list)
    ffc_by_player: dict[int, float] = {}
    for row in rows:
        if row.espn_player_id not in exposed:
            continue
        if row.adp_at_draft is not None:
            draft_adp_by_player[row.espn_player_id].append(float(row.adp_at_draft))
        if row.ffc_adp is not None:
            ffc_by_player[row.espn_player_id] = float(row.ffc_adp)

    candidates = []
    for player_id, draft_adps in draft_adp_by_player.items():
        if player_id not in ffc_by_player:
            continue
        draft_adp = _mean_raw(draft_adps)
        current_adp = ffc_by_player[player_id]
        if draft_adp is None:
            continue
        row = exposed[player_id]
        move = current_adp - draft_adp
        if move < 0:
            label = f"rose {abs(move):.1f} picks since draft"
        elif move > 0:
            label = f"fell {move:.1f} picks since draft"
        else:
            label = "unchanged since draft"
        candidates.append(
            {
                "espn_player_id": player_id,
                "player_name": row.get("player_name"),
                "position": row.get("position"),
                "nfl_team": row.get("nfl_team"),
                "draft_time_adp": round(draft_adp, 1),
                "current_ffc_adp": round(current_adp, 1),
                "market_move": round(move, 1),
                "market_move_abs": round(abs(move), 1),
                "market_move_label": label,
                "exposure_pct": row.get("exposure_pct", 0.0),
                "share": row.get("share", ""),
            }
        )
    return max(
        candidates,
        key=lambda row: (row["market_move_abs"], row["player_name"] or ""),
        default=None,
    )


def _leverage_headline(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        "espn_player_id": row["espn_player_id"],
        "player_name": row.get("player_name"),
        "position": row.get("position"),
        "nfl_team": row.get("nfl_team"),
        "exposure_pct": row.get("my_exposure_pct", 0.0),
        "field_exposure_pct": row.get("field_exposure_pct", 0.0),
        "leverage_pp": row.get("leverage_pp", 0.0),
        "share": row.get("my_share", ""),
        "field_share": row.get("field_share", ""),
        "field_slot_pct": row.get("field_slot_pct", 0.0),
        "field_slot_share": row.get("field_slot_share", ""),
    }


def _headline_block(me: _ScopeData, field: _ScopeData, players: list[dict]) -> dict:
    highest_leverage = next((row for row in players if row.get("leverage_pp") is not None), None)
    negative_tail = [
        row for row in players if row.get("leverage_pp") is not None and row["leverage_pp"] < 0
    ]
    most_underowned = min(
        negative_tail,
        key=lambda row: (float(row.get("leverage_pp") or 0.0), row.get("player_name") or ""),
        default=None,
    )
    rb_row = next((row for row in me.positional_spend if row["position"] == "RB"), None)
    field_rb = next((row for row in field.positional_spend if row["position"] == "RB"), None)
    concentrated = me.nfl_team_concentration[0] if me.nfl_team_concentration else None
    return {
        "highest_leverage": _leverage_headline(highest_leverage),
        "most_underowned": _leverage_headline(most_underowned),
        "positional_capital_vs_field": (
            {
                "position": "RB",
                "pick_value_pct": rb_row["pick_value_pct"] if rb_row else 0.0,
                "field_pick_value_pct": field_rb["pick_value_pct"] if field_rb else 0.0,
                "leverage_pp": round(
                    (rb_row["pick_value_pct"] if rb_row else 0.0)
                    - (field_rb["pick_value_pct"] if field_rb else 0.0),
                    1,
                ),
                "pick_count": rb_row["pick_count"] if rb_row else 0,
                "field_pick_count": field_rb["pick_count"] if field_rb else 0,
            }
            if rb_row or field_rb
            else None
        ),
        "most_concentrated_nfl_team": concentrated,
        "largest_market_move": _largest_market_move(me.rows, me.players),
    }


def _scope_data(
    session: Session,
    drafted_league_ids: set[int],
    scope: ExposureScope,
) -> _ScopeData:
    teams = _teams_in_scope(session, drafted_league_ids, scope)
    team_ids = [team.id for team in teams]
    teams_in_scope, auction_teams = _team_count_with_auctions(session, teams)
    rows = _pick_rows(session, team_ids)
    snake_rows = [row for row in rows if (row.draft_type or "").upper() != "AUCTION"]
    players = _player_rows(rows, teams_in_scope, len(drafted_league_ids))
    return _ScopeData(
        scope=scope,
        teams=teams,
        teams_in_scope=teams_in_scope,
        leagues_in_scope=len(drafted_league_ids),
        auction_teams=auction_teams,
        rows=rows,
        snake_rows=snake_rows,
        players=players,
        players_by_id={row["espn_player_id"]: row for row in players},
        positional_spend=_positional_spend(snake_rows),
        round_fingerprint=_round_fingerprint(snake_rows),
        nfl_team_concentration=_nfl_team_concentration(rows, teams_in_scope),
        core_dart={
            "core_players": sum(
                1 for player in players if player["rostered_teams"] > teams_in_scope / 2
            ),
            "dart_players": sum(1 for player in players if player["rostered_teams"] == 1),
            "core_definition": "rostered on a majority of teams in scope",
            "dart_definition": "rostered on exactly one team in scope",
        },
        keeper_picks=sum(1 for row in rows if row.keeper),
        auction_picks=sum(1 for row in rows if (row.draft_type or "").upper() == "AUCTION"),
    )


def build_exposure(
    session: Session,
    *,
    scope: ExposureScope,
    filters: PortfolioFilters,
) -> dict:
    season = filters.season or get_settings().season
    league_ids = filtered_league_ids(session, filters)
    leagues = {
        league.id: league
        for league in session.scalars(select(League).where(League.id.in_(league_ids)))
    } if league_ids else {}
    drafted_league_ids = {
        league_id
        for league_id in _drafted_league_ids(session, league_ids)
        if (leagues[league_id].lifecycle != "pre_draft")
    }
    me = _scope_data(session, drafted_league_ids, "me")
    field = _scope_data(session, drafted_league_ids, "opponents")
    current = me if scope == "me" else field
    players = _merge_player_leverage(me, field, scope)
    positional_spend = (
        _merge_positional_spend(me.positional_spend, field.positional_spend)
        if scope == "me"
        else _merge_positional_spend(field.positional_spend, me.positional_spend)
    )
    round_fingerprint = (
        _merge_round_fingerprint(me.round_fingerprint, field.round_fingerprint)
        if scope == "me"
        else _merge_round_fingerprint(field.round_fingerprint, me.round_fingerprint)
    )
    return {
        "scope": scope,
        "season": season,
        "teams_in_scope": current.teams_in_scope,
        "coverage": {
            "league_count": len(drafted_league_ids),
            "teams_in_scope": current.teams_in_scope,
            "my_teams_in_scope": me.teams_in_scope,
            "field_teams_in_scope": field.teams_in_scope,
            "auction_teams": current.auction_teams,
            "auction_picks": current.auction_picks,
            "keeper_picks": current.keeper_picks,
            "pick_value_picks": len(
                [row for row in current.snake_rows if row.overall is not None]
            ),
            "pre_draft_leagues_excluded": len(set(league_ids) - drafted_league_ids),
            "notes": [
                "Auction teams are included in exposure counts; pick-value rollups "
                "exclude auction pick-number math.",
                "Pre-draft leagues are excluded from denominators.",
                "Field exposure counts drafted leagues where at least one opponent "
                "rostered the player; field slots retain opponent-team intensity.",
            ],
        },
        "headlines": _headline_block(me, field, players),
        "views": _view_summaries(players),
        "players": players,
        "positional_spend": positional_spend,
        "round_fingerprint": round_fingerprint,
        "nfl_team_concentration": current.nfl_team_concentration,
        "core_dart": current.core_dart,
    }
