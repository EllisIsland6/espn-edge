"""Grounded input builders for the AI layer (SPEC §7 grounding rule).

Each function reads DB rows and returns a compact dict of **facts only** — picks,
ADP deltas, records, metrics, fingerprints. These dicts are (a) rendered into the
prompt and (b) hashed for the cache key. No prose, no invented numbers. Pure over the
session, so unit-testable offline.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DraftPick, League, Matchup, Player, Team, Transaction
from .metrics import (
    TeamStat,
    compute_all_play,
    read_all_play,
    read_edge_index,
    read_league_softness,
    read_my_edge,
    team_edge,
)
from .parse import classify_scoring


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


def _pos_counts(picks: list[dict]) -> dict:
    return dict(Counter(p["pos"] for p in picks))


def trade_finder_input(session: Session, league: League, me: Team, opponent: Team) -> dict:
    return {
        "league": _league_facts(league),
        "me": {
            "name": me.name,
            "pos_counts": _pos_counts(_picks_for_team(session, league.id, me.id)),
        },
        "opponent": {
            "name": opponent.name,
            "pos_counts": _pos_counts(_picks_for_team(session, league.id, opponent.id)),
        },
    }
