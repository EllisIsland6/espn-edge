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

from ..models import DraftPick, League, Matchup, Player, Team
from .metrics import team_edge
from .parse import classify_scoring


def _scoring(league: League) -> str | None:
    return classify_scoring(league.scoring_json) if league.scoring_json else None


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
            Player.espn_adp,
        )
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(DraftPick.league_id == league_id, DraftPick.team_id == team_id)
        .order_by(DraftPick.overall)
    ).all()
    picks: list[dict] = []
    for overall, rnd, pid, auto, name, pos, adp in rows:
        # Positive delta = drafted later than ADP (captured value); negative = a reach.
        delta = round(adp - overall, 1) if (adp is not None and overall is not None) else None
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
    team_facts = []
    for t in teams:
        edge = team_edge(session, league.id, t.id)
        fp = _fingerprint(_picks_for_team(session, league.id, t.id))
        team_facts.append(
            {
                "name": t.name,
                "is_me": t.is_me,
                "record": [t.wins, t.losses, t.ties],
                "points_for": round(t.points_for, 1),
                "points_against": round(t.points_against, 1),
                "standing": t.standing,
                "edge_score": edge.edge_score,
                "autodrafted": t.autodrafted,
                "first6_by_pos": fp["first6_by_pos"],
            }
        )
    return {"league": _league_facts(league), "lifecycle": league.lifecycle, "teams": team_facts}


def advantage_verdict_input(session: Session, league: League, team: Team) -> dict:
    edge = team_edge(session, league.id, team.id)
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
            "edge_score": edge.edge_score,
            "grade": edge.grade,
            "verdict": edge.verdict,
            "playoff_odds": edge.playoff_odds,
        },
        "league_context": {"team_count": len(teams), "my_points_for_rank": pf_rank},
    }


def weekly_recap_input(session: Session, league: League, week: int) -> dict:
    names = {t.id: t.name for t in session.scalars(select(Team).where(Team.league_id == league.id))}
    matchups = session.scalars(
        select(Matchup).where(Matchup.league_id == league.id, Matchup.week == week)
    )
    games = []
    for m in matchups:
        games.append(
            {
                "home": names.get(m.home_team_id, "?"),
                "away": names.get(m.away_team_id, "?"),
                "home_points": m.home_points,
                "away_points": m.away_points,
                "is_playoff": m.is_playoff,
            }
        )
    return {"league": _league_facts(league), "week": week, "matchups": games}


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
