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


class AccountReauth(BaseModel):
    """New cookies for an account whose session expired (Phase 7)."""

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
    last_sync_ok: bool | None = None
    last_sync_error: str | None = None

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


# ---- Phase 2 read-only views ----------------------------------------------
# Metric fields (edge_score/grade/playoff_odds/verdict) are intentionally null
# until Phase 3 computes them — the UI must render null/empty, not compute (SPEC 4).
class TeamOut(BaseModel):
    id: int
    espn_team_id: int
    name: str | None
    abbrev: str | None
    is_me: bool
    autodrafted: bool
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    standing: int | None
    logo_url: str | None

    model_config = ConfigDict(from_attributes=True)


class DraftPickOut(BaseModel):
    overall: int | None
    round: int | None
    round_pick: int | None
    team_id: int | None
    espn_player_id: int | None
    keeper: bool
    autodraft: bool
    bid_amount: int | None
    adp_at_draft: float | None
    value_delta: float | None
    # Phase 10: resolved from the players table on the read endpoint (null if unmapped).
    player_name: str | None = None
    player_position: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MatchupOut(BaseModel):
    week: int
    home_team_id: int | None
    away_team_id: int | None
    home_points: float | None
    away_points: float | None
    is_playoff: bool

    model_config = ConfigDict(from_attributes=True)


class TransactionOut(BaseModel):
    team_id: int | None
    type: str | None
    week: int | None
    player_in: int | None
    player_out: int | None
    bid: int | None
    executed_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class EdgeComponent(BaseModel):
    """One within-league percentile that feeds my team's edge_score (Phase 9)."""

    key: str
    label: str
    weight: float
    percentile: float

    model_config = ConfigDict(from_attributes=True)


class AllPlayOut(BaseModel):
    """A team's actual vs all-play record + luck delta (Phase 12)."""

    team_id: int
    team_name: str | None
    wins: int
    losses: int
    ties: int
    win_pct: float
    all_play_wins: int
    all_play_losses: int
    all_play_ties: int
    all_play_win_pct: float
    luck_delta: float


class LeagueOverview(BaseModel):
    league: LeagueOut
    account_label: str | None
    scoring: str | None
    teams: list[TeamOut]  # ordered by standing
    # Phase 3 metrics for my team (null when pending):
    edge_score: float | None = None
    grade: str | None = None
    verdict: str | None = None
    playoff_odds: float | None = None
    # Phase 9: the weighted components behind edge_score (empty when pending).
    components: list[EdgeComponent] = []


class PortfolioRow(BaseModel):
    league_id: int
    espn_league_id: str
    season: int
    league_name: str | None
    size: int | None
    account_label: str | None
    lifecycle: str
    last_synced_at: datetime | None
    last_sync_ok: bool | None = None
    last_sync_error: str | None = None
    # my team (null if not detected / public):
    my_team_id: int | None
    my_team_name: str | None
    wins: int | None
    losses: int | None
    ties: int | None
    points_for: float | None
    points_against: float | None
    standing: int | None
    # Phase 3 placeholders (null until analytics land):
    edge_score: float | None = None
    grade: str | None = None
    playoff_odds: float | None = None
    verdict: str | None = None


class AiStatus(BaseModel):
    enabled: bool
    standard_model: str
    bulk_model: str


class AiReportEnvelope(BaseModel):
    """A single league/team-scoped AI report (null content = not generated)."""

    enabled: bool
    kind: str
    model: str | None = None
    created_at: datetime | None = None
    stale: bool = False  # stored inputs differ from current DB facts
    content: dict | None = None
    error: str | None = None


class AiReportList(BaseModel):
    """Multiple reports of one kind (e.g. per-team draft recaps)."""

    enabled: bool
    kind: str
    reports: list[dict] = []
    error: str | None = None


class PortfolioSummary(BaseModel):
    """Aggregate portfolio numbers, computed in the view layer (never in React)."""

    total_leagues: int
    advantaged_count: int
    scored_count: int
    aggregate_wins: int
    aggregate_losses: int
    aggregate_ties: int
    # Phase 3 placeholders (null until any league has an edge score):
    best_edge_score: float | None = None
    worst_edge_score: float | None = None
