"""Read-only view endpoints feeding the Phase 2 UI (SPEC 8).

Every number comes straight from the DB — no analytics computed here (that's Phase 3);
metric fields are returned null until then. No ESPN traffic in this router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Account, DraftPick, League, Matchup, Team, Transaction
from ..schemas import (
    DraftPickOut,
    LeagueOut,
    LeagueOverview,
    MatchupOut,
    PortfolioRow,
    PortfolioSummary,
    TeamOut,
    TransactionOut,
)
from ..services.metrics import team_edge
from ..services.parse import classify_scoring

router = APIRouter(tags=["views"])


def _scoring_label(league: League) -> str | None:
    if not league.scoring_json:
        return None
    return classify_scoring(league.scoring_json)


def _standing_key(t: Team):
    # None standings sort last; otherwise ascending seed.
    return (t.standing is None, t.standing or 0)


def _build_portfolio_rows(session: Session) -> list[PortfolioRow]:
    """One row per league; my-team fields populated when detected (SPEC 8.3).

    Single source of truth for both /api/portfolio and /api/portfolio/summary so
    the summary aggregates exactly what the board shows (no divergent math).
    """
    rows: list[PortfolioRow] = []
    leagues = list(session.scalars(select(League).order_by(League.season.desc(), League.id)))
    for lg in leagues:
        account = session.get(Account, lg.account_id) if lg.account_id else None
        me = session.scalar(select(Team).where(Team.league_id == lg.id, Team.is_me.is_(True)))
        # Persisted Phase 3 metrics for my team (null/pending when not computed).
        edge = team_edge(session, lg.id, me.id if me else None)
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
            )
        )
    return rows


@router.get("/api/portfolio", response_model=list[PortfolioRow])
def portfolio(session: Session = Depends(get_session)) -> list[PortfolioRow]:
    return _build_portfolio_rows(session)


@router.get("/api/portfolio/summary", response_model=PortfolioSummary)
def portfolio_summary(session: Session = Depends(get_session)) -> PortfolioSummary:
    """Aggregate portfolio numbers derived from the same rows the board renders.

    edge_score/verdict are null until Phase 3, so advantaged/scored counts are 0
    and best/worst edge stay null — but the aggregation is DB-sourced and will fill
    in automatically once metrics land, with no React math."""
    rows = _build_portfolio_rows(session)
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


def _get_league(session: Session, league_id: int) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(404, "league not found")
    return league


@router.get("/api/leagues/{league_id}/overview", response_model=LeagueOverview)
def league_overview(league_id: int, session: Session = Depends(get_session)) -> LeagueOverview:
    league = _get_league(session, league_id)
    account = session.get(Account, league.account_id) if league.account_id else None
    teams = sorted(
        session.scalars(select(Team).where(Team.league_id == league.id)),
        key=_standing_key,
    )
    edge = team_edge(session, league.id, league.my_team_id)
    return LeagueOverview(
        league=LeagueOut.model_validate(league),
        account_label=account.label if account else None,
        scoring=_scoring_label(league),
        teams=[TeamOut.model_validate(t) for t in teams],
        edge_score=edge.edge_score,
        grade=edge.grade,
        verdict=edge.verdict,
    )


@router.get("/api/leagues/{league_id}/teams", response_model=list[TeamOut])
def league_teams(league_id: int, session: Session = Depends(get_session)) -> list[Team]:
    _get_league(session, league_id)
    teams = session.scalars(select(Team).where(Team.league_id == league_id))
    return sorted(teams, key=_standing_key)


@router.get("/api/leagues/{league_id}/draft", response_model=list[DraftPickOut])
def league_draft(league_id: int, session: Session = Depends(get_session)) -> list[DraftPick]:
    _get_league(session, league_id)
    return list(
        session.scalars(
            select(DraftPick).where(DraftPick.league_id == league_id).order_by(DraftPick.overall)
        )
    )


@router.get("/api/leagues/{league_id}/matchups", response_model=list[MatchupOut])
def league_matchups(league_id: int, session: Session = Depends(get_session)) -> list[Matchup]:
    _get_league(session, league_id)
    return list(
        session.scalars(
            select(Matchup).where(Matchup.league_id == league_id).order_by(Matchup.week)
        )
    )


@router.get("/api/leagues/{league_id}/activity", response_model=list[TransactionOut])
def league_activity(league_id: int, session: Session = Depends(get_session)) -> list[Transaction]:
    _get_league(session, league_id)
    return list(
        session.scalars(
            select(Transaction)
            .where(Transaction.league_id == league_id)
            .order_by(Transaction.executed_at.desc().nullslast(), Transaction.id)
        )
    )
