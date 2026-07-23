"""Leagues router — discovery, manual add, sync (SPEC 2.6, 8.2)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..models import Account, League
from ..schemas import (
    DiscoveredLeagueOut,
    LeagueAdd,
    LeagueOut,
    SyncSummary,
)
from ..services.discovery import DiscoveryAuthError, discover_leagues, parse_league_id
from ..services.espn import cookies_for_account
from ..services.read_models import build_league_out
from ..services.sync import SyncService

router = APIRouter(prefix="/api/leagues", tags=["leagues"])


@router.get("", response_model=list[LeagueOut])
def list_leagues(session: Session = Depends(get_session)) -> list[LeagueOut]:
    leagues = session.scalars(select(League).order_by(League.id))
    return [build_league_out(session, league) for league in leagues]


@router.get("/discover/{account_id}", response_model=list[DiscoveredLeagueOut])
def discover(account_id: int, session: Session = Depends(get_session)):
    """Attempt fan-profile auto-discovery for one account (SPEC 2.6 path 1)."""
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "account not found")
    cookies = cookies_for_account(account)
    if cookies is None:
        raise HTTPException(400, "account has no cookies")
    try:
        found = discover_leagues(cookies, season=get_settings().season)
    except DiscoveryAuthError as exc:
        account.status = "needs_reauth"
        session.commit()
        raise HTTPException(401, "ESPN session expired; re-authenticate this account") from exc
    return [DiscoveredLeagueOut(**vars(d)) for d in found]


@router.post("", response_model=LeagueOut, status_code=201)
def add_league(payload: LeagueAdd, session: Session = Depends(get_session)) -> LeagueOut:
    """Manual add (SPEC 2.6 path 2, the guaranteed fallback)."""
    settings = get_settings()
    season = payload.season or settings.season
    try:
        espn_league_id = parse_league_id(payload.league_ref)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if payload.account_id is not None and session.get(Account, payload.account_id) is None:
        raise HTTPException(404, "account not found")

    existing = session.scalar(
        select(League).where(League.espn_league_id == espn_league_id, League.season == season)
    )
    if existing is not None:
        # Allow re-pointing to an account, but don't duplicate the row.
        if payload.account_id is not None:
            existing.account_id = payload.account_id
            existing.is_public = False
        session.commit()
        session.refresh(existing)
        return build_league_out(session, existing)

    league = League(
        espn_league_id=espn_league_id,
        season=season,
        account_id=payload.account_id,
        is_public=payload.account_id is None,
        lifecycle="pre_draft",
    )
    session.add(league)
    session.commit()
    session.refresh(league)
    return build_league_out(session, league)


@router.post("/{league_id}/sync", response_model=SyncSummary)
def sync_league(league_id: int, session: Session = Depends(get_session)) -> SyncSummary:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(404, "league not found")
    with SyncService(session) as svc:
        result = svc.sync_league(league)
    session.commit()
    return SyncSummary(**result)
