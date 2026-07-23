"""Shared builders for DB-backed API read models."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import League, Team
from ..schemas import LeagueOut


def build_league_out(session: Session, league: League) -> LeagueOut:
    """Serialize a league with its detected owner-team identity when available."""
    me = (
        session.scalar(
            select(Team).where(
                Team.id == league.my_team_id,
                Team.league_id == league.id,
            )
        )
        if league.my_team_id is not None
        else None
    )
    return LeagueOut.model_validate(league).model_copy(
        update={
            "my_team_name": me.name if me else None,
            "my_team_logo_url": me.logo_url if me else None,
        }
    )
