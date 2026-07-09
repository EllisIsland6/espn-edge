"""Portfolio exports (Phase 5) — CSV, master JSON, and XLSX.

Every export reflects DB/view-layer data exactly (built from the same portfolio and
view models the API serves — no recomputation). No ESPN traffic; no secrets.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DraftPick, League, Matchup, Team, Transaction
from ..schemas import DraftPickOut, LeagueOut, MatchupOut, TeamOut, TransactionOut
from .metrics import team_edge
from .portfolio import build_portfolio_rows, build_summary

# Column order shared by CSV + XLSX so the two never drift.
_ROW_FIELDS = [
    "league_id", "season", "league_name", "size", "account_label", "lifecycle",
    "my_team_name", "wins", "losses", "ties", "points_for", "points_against",
    "standing", "edge_score", "grade", "verdict", "playoff_odds", "last_synced_at",
]


def _teams_sorted(session: Session, league_id: int) -> list[Team]:
    teams = session.scalars(select(Team).where(Team.league_id == league_id))
    return sorted(teams, key=lambda t: (t.standing is None, t.standing or 0))


# --------------------------------------------------------------------------- #
# CSV — one row per league (the Portfolio Board columns)
# --------------------------------------------------------------------------- #
def portfolio_csv(session: Session) -> str:
    rows = build_portfolio_rows(session)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_ROW_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: getattr(r, k) for k in _ROW_FIELDS})
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Master JSON — summary + rows + per-league detail (mirrors the API models)
# --------------------------------------------------------------------------- #
def portfolio_json(session: Session) -> dict:
    rows = build_portfolio_rows(session)
    summary = build_summary(rows)
    leagues_out = []
    for lg in session.scalars(select(League).order_by(League.season.desc(), League.id)):
        teams = _teams_sorted(session, lg.id)
        draft = session.scalars(
            select(DraftPick).where(DraftPick.league_id == lg.id).order_by(DraftPick.overall)
        )
        matchups = session.scalars(
            select(Matchup).where(Matchup.league_id == lg.id).order_by(Matchup.week)
        )
        activity = session.scalars(select(Transaction).where(Transaction.league_id == lg.id))
        leagues_out.append(
            {
                "league": LeagueOut.model_validate(lg).model_dump(mode="json"),
                "teams": [TeamOut.model_validate(t).model_dump(mode="json") for t in teams],
                "draft": [DraftPickOut.model_validate(p).model_dump(mode="json") for p in draft],
                "matchups": [
                    MatchupOut.model_validate(m).model_dump(mode="json") for m in matchups
                ],
                "activity": [
                    TransactionOut.model_validate(x).model_dump(mode="json") for x in activity
                ],
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary.model_dump(mode="json"),
        "rows": [r.model_dump(mode="json") for r in rows],
        "leagues": leagues_out,
    }


# --------------------------------------------------------------------------- #
# XLSX — Portfolio summary sheet + one sheet per league
# --------------------------------------------------------------------------- #
_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")


def _sheet_title(name: str, used: set[str]) -> str:
    title = _INVALID_SHEET.sub("-", name or "League")[:28].strip() or "League"
    candidate = title
    i = 2
    while candidate.lower() in used:
        candidate = f"{title[:26]}-{i}"
        i += 1
    used.add(candidate.lower())
    return candidate


def portfolio_xlsx(session: Session) -> bytes:
    wb = Workbook()
    used: set[str] = set()

    ws = wb.active
    ws.title = _sheet_title("Portfolio", used)
    ws.append([f.replace("_", " ").title() for f in _ROW_FIELDS])
    for r in build_portfolio_rows(session):
        ws.append([getattr(r, k) for k in _ROW_FIELDS])

    header = ["Standing", "Team", "Abbrev", "W", "L", "T", "PF", "PA", "Edge", "Playoff %", "Me"]
    for lg in session.scalars(select(League).order_by(League.season.desc(), League.id)):
        sheet = wb.create_sheet(_sheet_title(f"{lg.name or lg.espn_league_id} {lg.season}", used))
        sheet.append(header)
        for t in _teams_sorted(session, lg.id):
            edge = team_edge(session, lg.id, t.id)
            sheet.append(
                [
                    t.standing,
                    t.name,
                    t.abbrev,
                    t.wins,
                    t.losses,
                    t.ties,
                    round(t.points_for, 1),
                    round(t.points_against, 1),
                    edge.edge_score,
                    None if edge.playoff_odds is None else round(edge.playoff_odds * 100, 1),
                    "yes" if t.is_me else "",
                ]
            )

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
