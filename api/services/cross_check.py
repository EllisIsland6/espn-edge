"""Cross-check headline numbers against the `espn-api` library (SPEC §2.8 amendment).

Our raw httpx client + parser is the workhorse; this module loads the same league
through cwendt94's `espn-api` as an *independent* implementation and diffs the
headline numbers (team count, records, PF/PA, standings order, draft pick count).
A mismatch means our parser and a second implementation disagree — worth a look.

espn-api makes its own network call, so this only runs in the verify CLI, never in
the app request path, and never in offline tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DraftPick, League, Team


@dataclass
class HeadlineNumbers:
    source: str
    team_count: int
    records: dict[int, tuple[int, int, int]]  # espn_team_id -> (w, l, t)
    points_for: dict[int, float]
    points_against: dict[int, float]
    standings_order: list[int]  # espn_team_ids ordered by standing
    draft_pick_count: int


def from_db(session: Session, league: League) -> HeadlineNumbers:
    teams = list(session.scalars(select(Team).where(Team.league_id == league.id)))
    records = {t.espn_team_id: (t.wins, t.losses, t.ties) for t in teams}
    pf = {t.espn_team_id: round(float(t.points_for or 0), 2) for t in teams}
    pa = {t.espn_team_id: round(float(t.points_against or 0), 2) for t in teams}
    order = [
        t.espn_team_id
        for t in sorted(teams, key=lambda t: (t.standing is None, t.standing or 0))
    ]
    pick_count = session.query(DraftPick).filter(DraftPick.league_id == league.id).count()
    return HeadlineNumbers("ours", len(teams), records, pf, pa, order, pick_count)


def from_espn_api(
    league_id: str | int,
    season: int,
    swid: str | None = None,
    espn_s2: str | None = None,
) -> HeadlineNumbers:
    """Load via espn-api. Raises on no-access/not-found — caller decides what to do."""
    from espn_api.football import League as EspnApiLeague

    kwargs: dict = {"league_id": int(league_id), "year": int(season)}
    if swid and espn_s2:
        kwargs["swid"] = swid
        kwargs["espn_s2"] = espn_s2
    lg = EspnApiLeague(**kwargs)

    teams = list(lg.teams)
    records = {
        t.team_id: (int(t.wins), int(t.losses), int(getattr(t, "ties", 0) or 0))
        for t in teams
    }
    pf = {t.team_id: round(float(t.points_for or 0), 2) for t in teams}
    pa = {t.team_id: round(float(t.points_against or 0), 2) for t in teams}

    def _standing(t) -> int:
        return int(getattr(t, "standing", None) or getattr(t, "final_standing", None) or 999)

    order = [t.team_id for t in sorted(teams, key=_standing)]
    draft = getattr(lg, "draft", None) or []
    return HeadlineNumbers("espn-api", len(teams), records, pf, pa, order, len(draft))


def diff(ours: HeadlineNumbers, other: HeadlineNumbers, pf_tol: float = 0.1) -> list[str]:
    """Return human-readable mismatch lines; empty list = agreement."""
    out: list[str] = []
    if ours.team_count != other.team_count:
        out.append(f"team_count: ours={ours.team_count} espn-api={other.team_count}")

    for tid in sorted(set(ours.records) | set(other.records)):
        a, b = ours.records.get(tid), other.records.get(tid)
        if a != b:
            out.append(f"record team {tid}: ours={a} espn-api={b}")

    for tid in sorted(set(ours.points_for) | set(other.points_for)):
        a = ours.points_for.get(tid, 0.0)
        b = other.points_for.get(tid, 0.0)
        if abs(a - b) > pf_tol:
            out.append(f"PF team {tid}: ours={a} espn-api={b}")
    for tid in sorted(set(ours.points_against) | set(other.points_against)):
        a = ours.points_against.get(tid, 0.0)
        b = other.points_against.get(tid, 0.0)
        if abs(a - b) > pf_tol:
            out.append(f"PA team {tid}: ours={a} espn-api={b}")

    if ours.standings_order != other.standings_order:
        out.append(
            f"standings order: ours={ours.standings_order} espn-api={other.standings_order}"
        )
    if ours.draft_pick_count != other.draft_pick_count:
        out.append(
            f"draft_pick_count: ours={ours.draft_pick_count} espn-api={other.draft_pick_count}"
        )
    return out
