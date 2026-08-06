"""Shared portfolio-scope filters for Phase 23 analytics endpoints.

The Portfolio Board, exports, and analytics should agree on which leagues are in
scope. This helper reuses the board row builder for account/verdict semantics
instead of reimplementing them endpoint-by-endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import League
from ..schemas import PortfolioRow
from .portfolio import build_portfolio_rows


@dataclass(frozen=True)
class PortfolioFilters:
    season: int | None = None
    account_id: int | None = None
    verdict: str | None = None


def _verdict_matches(row: PortfolioRow, verdict: str | None) -> bool:
    if verdict is None or verdict.lower() in {"all", ""}:
        return True
    return (row.edge_index_verdict or "").lower() == verdict.lower()


def filtered_portfolio_rows(session: Session, filters: PortfolioFilters) -> list[PortfolioRow]:
    """Rows matching the Portfolio Board's filter vocabulary.

    `season=None` defaults to the configured season, which is how the board's
    portfolio-scope analytics read current-year data without hardcoding 2026.
    """
    season = filters.season or get_settings().season
    rows = [row for row in build_portfolio_rows(session) if row.season == season]
    if filters.account_id is not None:
        leagues = {
            league.id: league
            for league in session.scalars(
                select(League).where(League.id.in_([row.league_id for row in rows]))
            )
        }
        rows = [
            row
            for row in rows
            if (league := leagues.get(row.league_id)) is not None
            and league.account_id == filters.account_id
        ]
    return [row for row in rows if _verdict_matches(row, filters.verdict)]


def filtered_league_ids(session: Session, filters: PortfolioFilters) -> list[int]:
    return [row.league_id for row in filtered_portfolio_rows(session, filters)]
