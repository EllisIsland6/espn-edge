"""Grounded input builders for the AI layer (SPEC §7 grounding rule).

Each function reads DB rows and returns a compact dict of **facts only** — picks,
ADP deltas, records, metrics, fingerprints. These dicts are (a) rendered into the
prompt and (b) hashed for the cache key. No prose, no invented numbers. Pure over the
session, so unit-testable offline.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DraftPick, League, LineupSlot, Matchup, Player, Team, Transaction
from .espn_constants import slot_name
from .metrics import (
    _DEDICATED_SLOTS,
    _FLEX_ELIGIBLE,
    _KNOWN_POSITIONS,
    _SUPPORTED_SLOTS,
    TeamStat,
    _starting_slot_counts,
    compute_all_play,
    read_all_play,
    read_edge_index,
    read_league_softness,
    read_my_edge,
    team_edge,
)
from .parse import classify_scoring

# Bench / reserve slot names — not "starting" positions, and not unsupported starters either.
_BENCH_SLOT_NAMES: frozenset[str] = frozenset({"BE", "IR", "RES", "ER"})


def _scoring(league: League) -> str | None:
    return classify_scoring(league.scoring_json) if league.scoring_json else None


# --- Phase 18: Edge Index grounding helpers --------------------------------
def _find(rows: list, team_id: int):
    """First row whose team_id matches, or None (metric readers omit pending teams)."""
    return next((r for r in rows if r.team_id == team_id), None)


def _value_components(comps) -> list[dict]:
    """Serialize 0–100 sub-score components (Edge Index halves)."""
    return [
        {"key": c.key, "label": c.label, "weight": round(c.weight, 4), "value": c.value}
        for c in comps
    ]


def _pct_components(comps) -> list[dict]:
    """Serialize within-league percentile components (MyEdge / LeagueSoftness)."""
    return [
        {"key": c.key, "label": c.label, "weight": round(c.weight, 4), "percentile": c.percentile}
        for c in comps
    ]


def _league_facts(league: League) -> dict:
    return {
        "name": league.name,
        "size": league.size,
        "scoring": _scoring(league),
        "season": league.season,
    }


def _picks_for_team(session: Session, league_id: int, team_id: int) -> list[dict]:
    rows = session.execute(
        select(
            DraftPick.overall,
            DraftPick.round,
            DraftPick.espn_player_id,
            DraftPick.autodraft,
            Player.name,
            Player.position,
            DraftPick.adp_at_draft,
            DraftPick.value_delta,
        )
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(DraftPick.league_id == league_id, DraftPick.team_id == team_id)
        .order_by(DraftPick.overall)
    ).all()
    picks: list[dict] = []
    for overall, rnd, pid, auto, name, pos, adp, delta in rows:
        # ADP + value delta are persisted at sync (Phase 10); positive delta = drafted
        # later than ADP (captured value), negative = a reach.
        picks.append(
            {
                "overall": overall,
                "round": rnd,
                "pos": pos or "?",
                "player": name or (f"#{pid}" if pid is not None else "?"),
                "adp": adp,
                "value_delta": delta,
                "auto": bool(auto),
            }
        )
    return picks


def _fingerprint(picks: list[dict]) -> dict:
    first6 = picks[:6]
    return {
        "first6_by_pos": dict(Counter(p["pos"] for p in first6)),
        "total_picks": len(picks),
        "all_autodrafted": bool(picks) and all(p["auto"] for p in picks),
    }


def draft_recap_input(session: Session, league: League, team: Team) -> dict:
    picks = _picks_for_team(session, league.id, team.id)
    return {
        "league": _league_facts(league),
        "team": {"espn_team_id": team.espn_team_id, "name": team.name, "is_me": team.is_me},
        "picks": picks,
        "fingerprint": _fingerprint(picks),
    }


def league_brief_input(session: Session, league: League) -> dict:
    teams = list(
        session.scalars(
            select(Team).where(Team.league_id == league.id).order_by(Team.standing)
        )
    )
    # Edge Index is the primary advantage signal (Phase 16/17); read once for the league.
    # Keep the brief lean: scalar scores per team, not full component arrays.
    ei_by_team = {r.team_id: r for r in read_edge_index(session, league.id)}
    team_facts = []
    for t in teams:
        edge = team_edge(session, league.id, t.id)  # legacy edge_score only
        ei = ei_by_team.get(t.id)
        fp = _fingerprint(_picks_for_team(session, league.id, t.id))
        team_facts.append(
            {
                "name": t.name,
                "is_me": t.is_me,
                "record": [t.wins, t.losses, t.ties],
                "points_for": round(t.points_for, 1),
                "points_against": round(t.points_against, 1),
                "standing": t.standing,
                "edge_index_score": ei.edge_index_score if ei else None,
                "edge_index_verdict": ei.verdict if ei else None,
                "legacy_edge_score": edge.edge_score,  # deprecated within-league proxy
                "autodrafted": t.autodrafted,
                "first6_by_pos": fp["first6_by_pos"],
            }
        )
    return {"league": _league_facts(league), "lifecycle": league.lifecycle, "teams": team_facts}


def advantage_verdict_input(session: Session, league: League, team: Team) -> dict:
    edge = team_edge(session, league.id, team.id)  # legacy edge_score + playoff_odds
    ei = _find(read_edge_index(session, league.id), team.id)
    mye = _find(read_my_edge(session, league.id), team.id)
    sof = _find(read_league_softness(session, league.id), team.id)
    teams = list(session.scalars(select(Team).where(Team.league_id == league.id)))
    pf_rank = 1 + sum(1 for t in teams if t.points_for > team.points_for)
    return {
        "league": _league_facts(league),
        "lifecycle": league.lifecycle,
        "me": {
            "name": team.name,
            "record": [team.wins, team.losses, team.ties],
            "standing": team.standing,
            "points_for": round(team.points_for, 1),
            "points_against": round(team.points_against, 1),
            # Edge Index (Phase 16) — the PRIMARY advantage signal:
            "edge_index_score": ei.edge_index_score if ei else None,
            "edge_index_grade": ei.grade if ei else None,
            "edge_index_verdict": ei.verdict if ei else None,
            "edge_index_components": _value_components(ei.components) if ei else [],
            "my_edge_score": mye.my_edge_score if mye else None,
            "my_edge_components": _pct_components(mye.components) if mye else [],
            "league_softness_score": sof.league_softness_score if sof else None,
            "league_softness_components": _pct_components(sof.components) if sof else [],
            "playoff_odds": edge.playoff_odds,
            # Legacy Phase 3 within-league proxy — secondary context only:
            "legacy_edge_score": edge.edge_score,
            "legacy_grade": edge.grade,
            "legacy_verdict": edge.verdict,
        },
        "league_context": {"team_count": len(teams), "my_points_for_rank": pf_rank},
    }


def _team_stat(team: Team) -> TeamStat:
    """Minimal TeamStat for the all-play helper (roster_proj unused here)."""
    return TeamStat(
        team_id=team.id,
        wins=team.wins,
        losses=team.losses,
        ties=team.ties,
        points_for=team.points_for,
        points_against=team.points_against,
        standing=team.standing,
        roster_proj=None,
    )


def _week_matchups(
    names: dict[int, str], matchups: list[Matchup]
) -> tuple[list[dict], dict[int, float]]:
    """Per-game facts (with winner/loser/margin/tie when both scores exist) + this week's
    {team_id: score} map for the all-play computation. All values come from the DB rows."""
    games: list[dict] = []
    week_scores: dict[int, float] = {}
    for m in matchups:
        hp, ap = m.home_points, m.away_points
        game = {
            "home": names.get(m.home_team_id, "?"),
            "away": names.get(m.away_team_id, "?"),
            "home_points": hp,
            "away_points": ap,
            "is_playoff": m.is_playoff,
        }
        if hp is not None and ap is not None:
            if hp > ap:
                game.update(
                    winner=game["home"], loser=game["away"], margin=round(hp - ap, 1), tie=False
                )
            elif ap > hp:
                game.update(
                    winner=game["away"], loser=game["home"], margin=round(ap - hp, 1), tie=False
                )
            else:
                game.update(winner=None, loser=None, margin=0.0, tie=True)
            if m.home_team_id is not None:
                week_scores[m.home_team_id] = hp
            if m.away_team_id is not None:
                week_scores[m.away_team_id] = ap
        games.append(game)
    return games, week_scores


def _week_all_play(teams: list[Team], week: int, week_scores: dict[int, float]) -> list[dict]:
    """Per-team all-play for THIS week, via the existing pure compute_all_play on the week's
    scores (grounded; no invention). Only the week ranking is exposed — the helper's season
    luck_delta is not meaningful for a single week, so season luck comes from read_all_play."""
    if not week_scores:
        return []
    stats = [_team_stat(t) for t in teams if t.id in week_scores]
    rows = compute_all_play(stats, {week: week_scores})
    out: list[dict] = []
    for t in teams:
        r = rows.get(t.id)
        if r is not None and r.all_play_win_pct is not None:
            out.append(
                {
                    "team": t.name,
                    "week_score": week_scores.get(t.id),
                    "all_play_wins": r.all_play_wins,
                    "all_play_losses": r.all_play_losses,
                    "all_play_ties": r.all_play_ties,
                    "all_play_win_pct": r.all_play_win_pct,
                }
            )
    return out


def _week_transactions(
    session: Session, league_id: int, week: int, team_names: dict[int, str]
) -> list[dict]:
    """This week's transactions from persisted Transaction rows only (SPEC §2.4: an empty
    feed yields []). player_in/out ids are resolved to names, falling back to #id."""
    rows = list(
        session.scalars(
            select(Transaction).where(
                Transaction.league_id == league_id, Transaction.week == week
            )
        )
    )
    pids = {p for r in rows for p in (r.player_in, r.player_out) if p is not None}
    pname: dict[int, str | None] = {}
    if pids:
        pname = dict(
            session.execute(
                select(Player.espn_player_id, Player.name).where(Player.espn_player_id.in_(pids))
            ).all()
        )

    def _player(pid: int | None) -> str | None:
        if pid is None:
            return None
        return pname.get(pid) or f"#{pid}"

    return [
        {
            "team": team_names.get(r.team_id, "?"),
            "type": r.type,
            "player_in": _player(r.player_in),
            "player_out": _player(r.player_out),
            "bid": r.bid,
        }
        for r in rows
    ]


def weekly_recap_input(session: Session, league: League, week: int) -> dict:
    teams = list(session.scalars(select(Team).where(Team.league_id == league.id)))
    names = {t.id: t.name for t in teams}
    matchups = list(
        session.scalars(
            select(Matchup).where(Matchup.league_id == league.id, Matchup.week == week)
        )
    )
    games, week_scores = _week_matchups(names, matchups)
    # Season all-play + luck context, from persisted metrics (Phase 12).
    season_all_play = [
        {"team": names.get(r.team_id, "?"), "all_play_win_pct": r.all_play_win_pct,
         "luck_delta": r.luck_delta}
        for r in read_all_play(session, league.id)
    ]
    return {
        "league": _league_facts(league),
        "week": week,
        "matchups": games,
        "week_all_play": _week_all_play(teams, week, week_scores),
        "season_all_play": season_all_play,
        "transactions": _week_transactions(session, league.id, week, names),
    }


# --- Phase 22: Trade Finder grounding on roster snapshots -------------------
def _newest_lineup_week(session: Session, league_id: int) -> int | None:
    return session.scalar(
        select(func.max(LineupSlot.week)).where(LineupSlot.league_id == league_id)
    )


def _latest_common_lineup_week(session: Session, league_id: int, team_ids: list[int]) -> int | None:
    """The latest week for which EVERY given team has a lineup snapshot. None if no shared week.
    Using the common week guarantees both rosters are read from the same date (never mixed)."""
    weeks: dict[int, set[int]] = {tid: set() for tid in team_ids}
    rows = session.execute(
        select(LineupSlot.team_id, LineupSlot.week)
        .where(LineupSlot.league_id == league_id, LineupSlot.team_id.in_(team_ids))
        .distinct()
    ).all()
    for tid, wk in rows:
        if tid in weeks:
            weeks[tid].add(wk)
    if not all(weeks.values()):
        return None
    common = set.intersection(*weeks.values())
    return max(common) if common else None


def _dedup_by_player(entries: list[dict]) -> list[dict]:
    """Dedup by espn_player_id (prefer a starter row); entries without an id are kept as-is
    so a lineup row missing a Player join is never silently dropped."""
    by_pid: dict[int, dict] = {}
    no_id: list[dict] = []
    for e in entries:
        pid = e["espn_player_id"]
        if pid is None:
            no_id.append(e)
        elif pid not in by_pid or (e["is_starter"] and not by_pid[pid]["is_starter"]):
            by_pid[pid] = e
    return list(by_pid.values()) + no_id


def _roster_from_lineup(session: Session, league_id: int, team_id: int, week: int) -> list[dict]:
    rows = session.execute(
        select(
            LineupSlot.espn_player_id, LineupSlot.slot, LineupSlot.is_starter,
            Player.name, Player.position, Player.proj_ros,
        )
        .join(Player, Player.espn_player_id == LineupSlot.espn_player_id, isouter=True)
        .where(
            LineupSlot.league_id == league_id, LineupSlot.team_id == team_id,
            LineupSlot.week == week,
        )
    ).all()
    entries = [
        {"espn_player_id": pid, "name": name, "position": pos, "slot": slot,
         "is_starter": bool(starter), "proj_ros": proj}
        for pid, slot, starter, name, pos, proj in rows
    ]
    return _dedup_by_player(entries)


def _roster_from_draft(session: Session, league_id: int, team_id: int) -> list[dict]:
    rows = session.execute(
        select(DraftPick.espn_player_id, Player.name, Player.position, Player.proj_ros)
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(
            DraftPick.league_id == league_id, DraftPick.team_id == team_id,
            DraftPick.espn_player_id.is_not(None),
        )
        .order_by(DraftPick.overall)
    ).all()
    entries = [
        {"espn_player_id": pid, "name": name, "position": pos, "slot": None,
         "is_starter": False, "proj_ros": proj}
        for pid, name, pos, proj in rows
    ]
    return _dedup_by_player(entries)


def _unsupported_starting_slots(lineup_slots_json: dict | None) -> list[str]:
    """Starting-slot names present in the league that this v1 doesn't model (superflex/OP,
    IDP, RB/WR, WR/TE ...). Bench/IR/reserve are excluded — they aren't starting slots."""
    out: list[str] = []
    if not lineup_slots_json:
        return out
    for sid_str, count in lineup_slots_json.items():
        try:
            name = slot_name(int(sid_str))
        except (ValueError, TypeError):
            continue
        if count and name not in _SUPPORTED_SLOTS and name not in _BENCH_SLOT_NAMES:
            out.append(name)
    return sorted(set(out))


def _projection_coverage(players: list[dict]) -> float | None:
    if not players:
        return None
    covered = sum(1 for p in players if p["proj_ros"] is not None)
    return round(covered / len(players), 3)


def _positional_facts(players: list[dict], req: dict[str, int]) -> dict[str, dict]:
    """Deterministic per-position depth vs the league's starting requirements. FLEX demand is
    allocated to the highest projected RB/WR/TE remaining after each position's dedicated slots
    (nulls sort last; ties broken by espn_player_id) — no LLM math."""
    def _key(p: dict) -> tuple:
        return (p["proj_ros"] is None, -(p["proj_ros"] or 0.0), p["espn_player_id"] or 0)

    by_pos: dict[str, list[dict]] = {}
    for p in players:
        if p["position"] in _KNOWN_POSITIONS:
            by_pos.setdefault(p["position"], []).append(p)
    for lst in by_pos.values():
        lst.sort(key=_key)

    dedicated = {pos: req.get(pos, 0) for pos in _DEDICATED_SLOTS}
    remaining: list[tuple[str, dict]] = []
    for pos in _FLEX_ELIGIBLE:
        for p in by_pos.get(pos, [])[dedicated.get(pos, 0):]:
            remaining.append((pos, p))
    remaining.sort(key=lambda pp: _key(pp[1]))
    flex_share = dict.fromkeys(_FLEX_ELIGIBLE, 0)
    for pos, _p in remaining[: req.get("FLEX", 0)]:
        flex_share[pos] += 1

    out: dict[str, dict] = {}
    for pos in sorted(_KNOWN_POSITIONS):
        lst = by_pos.get(pos, [])
        need = dedicated.get(pos, 0)
        fshare = flex_share.get(pos, 0)
        starters = need + fshare
        projs = [p["proj_ros"] for p in lst if p["proj_ros"] is not None]
        depth_projs = [p["proj_ros"] for p in lst[starters:] if p["proj_ros"] is not None]
        out[pos] = {
            "count": len(lst),
            "starting_need": need,
            "flex_share": fshare,
            "proj_total": round(sum(projs), 1) if projs else None,
            "surplus_count": len(lst) - starters,
            "surplus_proj": round(sum(depth_projs), 1) if depth_projs else None,
            "deficit": max(0, need - len(lst)),
        }
    return out


def _team_roster_facts(team: Team, players: list[dict], req: dict[str, int]) -> dict:
    return {
        "name": team.name,
        "projection_coverage": _projection_coverage(players),
        "players": players,
        "by_position": _positional_facts(players, req),
    }


def trade_finder_input(session: Session, league: League, me: Team, opponent: Team) -> dict:
    """Grounded facts (Phase 22): the latest lineup-slots week both teams share, or a labeled
    drafted-roster fallback. Rosters are never mixed across weeks; projections are never
    invented; provenance/freshness is explicit."""
    team_ids = [me.id, opponent.id]
    req = _starting_slot_counts(league.lineup_slots_json)
    week = _latest_common_lineup_week(session, league.id, team_ids)

    if week is not None:
        source, fallback = "lineup_snapshot", None
        me_players = _roster_from_lineup(session, league.id, me.id, week)
        opp_players = _roster_from_lineup(session, league.id, opponent.id, week)
        newest = _newest_lineup_week(session, league.id)
        snapshot_stale = newest is not None and week < newest
    else:
        me_players = _roster_from_draft(session, league.id, me.id)
        opp_players = _roster_from_draft(session, league.id, opponent.id)
        snapshot_stale = False
        if me_players or opp_players:
            source = "drafted_roster"
            fallback = "no shared lineup snapshot; drafted rosters may not reflect current teams"
        else:
            source = "none"
            fallback = "no lineup snapshot and no drafted roster — insufficient data"

    # Conservative freshness: only trust projections when the latest sync completed cleanly.
    projections_stale = league.last_sync_ok is not True

    return {
        "league": _league_facts(league),
        "lifecycle": league.lifecycle,
        "roster_snapshot": {
            "grounding_source": source,
            "snapshot_week": week,
            "fallback_reason": fallback,
            "snapshot_stale": snapshot_stale,
            "projections_stale": projections_stale,
            "unsupported_slots": _unsupported_starting_slots(league.lineup_slots_json),
        },
        "slot_requirements": req,
        "me": _team_roster_facts(me, me_players, req),
        "opponent": _team_roster_facts(opponent, opp_players, req),
    }
