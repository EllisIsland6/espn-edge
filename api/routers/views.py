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
    EdgeComponent,
    LeagueOut,
    LeagueOverview,
    MatchupOut,
    PortfolioRow,
    PortfolioSummary,
    TeamOut,
    TransactionOut,
)
from ..services.metrics import team_components, team_edge
from ..services.parse import classify_scoring
from ..services.portfolio import build_portfolio_rows, build_summary

router = APIRouter(tags=["views"])


def _scoring_label(league: League) -> str | None:
    if not league.scoring_json:
        return None
    return classify_scoring(league.scoring_json)


def _standing_key(t: Team):
    # None standings sort last; otherwise ascending seed.
    return (t.standing is None, t.standing or 0)


@router.get("/api/portfolio", response_model=list[PortfolioRow])
def portfolio(session: Session = Depends(get_session)) -> list[PortfolioRow]:
    return build_portfolio_rows(session)


@router.get("/api/portfolio/summary", response_model=PortfolioSummary)
def portfolio_summary(session: Session = Depends(get_session)) -> PortfolioSummary:
    """Aggregate portfolio numbers derived from the same rows the board renders."""
    return build_summary(build_portfolio_rows(session))


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
    components = team_components(session, league.id, league.my_team_id)
    return LeagueOverview(
        league=LeagueOut.model_validate(league),
        account_label=account.label if account else None,
        scoring=_scoring_label(league),
        teams=[TeamOut.model_validate(t) for t in teams],
        edge_score=edge.edge_score,
        grade=edge.grade,
        verdict=edge.verdict,
        playoff_odds=edge.playoff_odds,
        components=[EdgeComponent.model_validate(c) for c in components],
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
