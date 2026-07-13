"""Portfolio row + summary builders — single source of truth for the board, the
summary endpoint, and exports (so all three show identical DB-sourced numbers)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, League, Team
from ..schemas import PortfolioRow, PortfolioSummary
from .metrics import team_edge, team_edge_index


def build_portfolio_rows(session: Session) -> list[PortfolioRow]:
    """One row per league; my-team fields populated when detected (SPEC 8.3)."""
    rows: list[PortfolioRow] = []
    leagues = list(session.scalars(select(League).order_by(League.season.desc(), League.id)))
    for lg in leagues:
        account = session.get(Account, lg.account_id) if lg.account_id else None
        me = session.scalar(select(Team).where(Team.league_id == lg.id, Team.is_me.is_(True)))
        edge = team_edge(session, lg.id, me.id if me else None)
        ei = team_edge_index(session, lg.id, me.id if me else None)
        rows.append(
            PortfolioRow(
                league_id=lg.id,
                espn_league_id=lg.espn_league_id,
                season=lg.season,
                league_name=lg.name,
                size=lg.size,
                account_label=account.label if account else None,
                lifecycle=lg.lifecycle,
                last_synced_at=lg.last_synced_at,
                last_sync_ok=lg.last_sync_ok,
                last_sync_error=lg.last_sync_error,
                my_team_id=me.id if me else None,
                my_team_name=me.name if me else None,
                wins=me.wins if me else None,
                losses=me.losses if me else None,
                ties=me.ties if me else None,
                points_for=me.points_for if me else None,
                points_against=me.points_against if me else None,
                standing=me.standing if me else None,
                edge_score=edge.edge_score,
                grade=edge.grade,
                playoff_odds=edge.playoff_odds,
                verdict=edge.verdict,
                edge_index_score=ei.edge_index_score,
                edge_index_grade=ei.grade,
                edge_index_verdict=ei.verdict,
            )
        )
    return rows


def build_summary(rows: list[PortfolioRow]) -> PortfolioSummary:
    scored = [r.edge_score for r in rows if r.edge_score is not None]
    return PortfolioSummary(
        total_leagues=len(rows),
        advantaged_count=sum(1 for r in rows if r.verdict == "advantaged"),
        scored_count=len(scored),
        aggregate_wins=sum(r.wins or 0 for r in rows),
        aggregate_losses=sum(r.losses or 0 for r in rows),
        aggregate_ties=sum(r.ties or 0 for r in rows),
        best_edge_score=max(scored) if scored else None,
        worst_edge_score=min(scored) if scored else None,
    )
