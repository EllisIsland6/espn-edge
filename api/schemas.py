"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthOut(BaseModel):
    status: str
    season: int
    db_path: str


# ---- accounts --------------------------------------------------------------
class AccountCreate(BaseModel):
    label: str = Field(..., min_length=1)
    swid: str = Field(..., min_length=1)
    espn_s2: str = Field(..., min_length=1)


class AccountOut(BaseModel):
    id: int
    label: str
    status: str
    created_at: datetime
    # NB: swid/espn_s2 are intentionally never serialized out (SPEC guardrail 11).

    model_config = ConfigDict(from_attributes=True)


# ---- leagues ---------------------------------------------------------------
class LeagueAdd(BaseModel):
    league_ref: str = Field(..., description="league id or a league URL containing leagueId=")
    account_id: int | None = None
    season: int | None = None


class LeagueOut(BaseModel):
    id: int
    espn_league_id: str
    season: int
    account_id: int | None
    name: str | None
    size: int | None
    draft_type: str | None
    lifecycle: str
    my_team_id: int | None
    is_public: bool
    last_synced_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class DiscoveredLeagueOut(BaseModel):
    espn_league_id: str
    name: str | None
    season: int | None
    team_id: int | None


class SyncSummary(BaseModel):
    league_id: str
    season: int
    name: str | None = None
    size: int | None = None
    scoring: str | None = None
    teams: int | None = None
    draft_picks: int | None = None
    drafted: bool | None = None
    lifecycle: str | None = None
    matchups: int | None = None
    transactions: int | None = None
    players: int | None = None
    completed_weeks: list[int] | None = None
    needs_reauth: bool | None = None
    errors: list[str] = []
