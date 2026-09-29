"""Phase 27 Opportunity Analytics API; GETs are DB-only and refresh is explicit."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..schemas import (
    OpportunityRefreshOut,
    OpportunityStatusOut,
    PlayerOpportunityOut,
    PortfolioOpportunityChartsOut,
    PortfolioOpportunityOut,
)
from ..services.opportunity import (
    OpportunityBusyError,
    OpportunityError,
    build_opportunity_charts,
    build_player_opportunity,
    build_portfolio_opportunity,
    opportunity_status,
    refresh_opportunity,
)
from ..services.portfolio_filters import PortfolioFilters

router = APIRouter(tags=["opportunity"])


@router.get("/api/opportunity/status", response_model=OpportunityStatusOut)
def status(
    season: int | None = None,
    session: Session = Depends(get_session),
) -> dict:
    return opportunity_status(session, season or get_settings().season)


@router.post("/api/opportunity/refresh", response_model=OpportunityRefreshOut)
def refresh(
    season: int | None = None,
    force: bool = False,
    session: Session = Depends(get_session),
) -> dict:
    try:
        result = refresh_opportunity(
            session,
            season or get_settings().season,
            force=force,
        )
    except OpportunityBusyError as exc:
        raise HTTPException(
            409,
            {"code": exc.code, "message": exc.message, **exc.details},
        ) from exc
    session.commit()
    return result


@router.get("/api/portfolio/opportunity", response_model=PortfolioOpportunityOut)
def portfolio_opportunity(
    view: Literal["rostered", "available", "all"] | None = None,
    season: int | None = None,
    account_id: int | None = None,
    verdict: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    return build_portfolio_opportunity(
        session,
        PortfolioFilters(season=season, account_id=account_id, verdict=verdict),
        view=view,
    )


@router.get(
    "/api/portfolio/opportunity/charts",
    response_model=PortfolioOpportunityChartsOut,
)
def portfolio_opportunity_charts(
    view: Literal["rostered", "available", "all"] | None = None,
    position: Literal["RB", "WR", "TE"] = "WR",
    chart_id: str | None = None,
    season: int | None = None,
    account_id: int | None = None,
    verdict: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return build_opportunity_charts(
            session,
            PortfolioFilters(season=season, account_id=account_id, verdict=verdict),
            view=view,
            position=position,
            chart_id=chart_id,
        )
    except OpportunityError as exc:
        raise HTTPException(
            422,
            {"code": exc.code, "message": exc.message, **exc.details},
        ) from exc


@router.get(
    "/api/players/{espn_player_id}/opportunity",
    response_model=PlayerOpportunityOut,
)
def player_opportunity(
    espn_player_id: int,
    season: int | None = None,
    account_id: int | None = None,
    verdict: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    result = build_player_opportunity(
        session,
        espn_player_id,
        PortfolioFilters(season=season, account_id=account_id, verdict=verdict),
    )
    if result is None:
        raise HTTPException(404, "player not found")
    return result
