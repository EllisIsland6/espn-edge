"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    my_team_name: str | None = None
    my_team_logo_url: str | None = None
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
    current_roster_entries: int | None = None
    metric_snapshots: int | None = None
    my_team_espn_id: int | None = None
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
    home_projected_points: float | None = None
    away_projected_points: float | None = None
    is_playoff: bool

    model_config = ConfigDict(from_attributes=True)


class RosterSlotOut(BaseModel):
    slot_id: int
    slot_label: str
    slot_index: int
    section: Literal["starters", "bench", "ir"]
    espn_player_id: int | None = None
    player_name: str | None = None
    player_position: str | None = None
    nfl_team: str | None = None
    opponent: str | None = None
    kickoff_at: datetime | None = None
    game_status: Literal["pregame", "in_progress", "final", "bye"] | None = None
    injury_status: str | None = None
    actual_points: float | None = None
    projected_points: float | None = None


class TeamMatchupSideOut(BaseModel):
    team: TeamOut | None
    points: float | None = None
    projected_points: float | None = None


class TeamMatchupOut(BaseModel):
    matchup_period: int
    scoring_period: int | None
    is_playoff: bool
    home: TeamMatchupSideOut
    away: TeamMatchupSideOut
    next_kickoff_at: datetime | None = None


class TeamDetailOut(BaseModel):
    league: LeagueOut
    account_label: str | None
    scoring: str | None
    team: TeamOut
    current_scoring_period: int | None
    current_matchup_period: int | None
    roster_status: Literal["current", "stale", "unavailable"]
    roster_synced_at: datetime | None
    starters: list[RosterSlotOut]
    bench: list[RosterSlotOut]
    ir: list[RosterSlotOut]
    matchup: TeamMatchupOut | None


class TransactionOut(BaseModel):
    team_id: int | None
    type: str | None
    week: int | None
    player_in: int | None
    player_out: int | None
    player_in_name: str | None = None
    player_in_position: str | None = None
    player_out_name: str | None = None
    player_out_position: str | None = None
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


class MyEdgeComponentOut(BaseModel):
    """One within-league percentile that feeds MyEdge (Phase 14)."""

    key: str
    label: str
    weight: float
    percentile: float


class EdgeIndexComponentOut(BaseModel):
    """One half (0–100 sub-score) that feeds the Edge Index composite (Phase 16)."""

    key: str
    label: str
    weight: float
    value: float


class EdgeIndexOut(BaseModel):
    """A team's full Edge Index v1 composite (Phase 16)."""

    team_id: int
    team_name: str | None
    is_me: bool
    edge_index_score: float
    grade: str | None
    verdict: str | None
    components: list[EdgeIndexComponentOut]


class LeagueSoftnessComponentOut(BaseModel):
    """One within-league percentile that feeds LeagueSoftness (Phase 15)."""

    key: str
    label: str
    weight: float
    percentile: float


class LeagueSoftnessOut(BaseModel):
    """A team's LeagueSoftness v1 score + component breakdown (Phase 15)."""

    team_id: int
    team_name: str | None
    is_me: bool
    league_softness_score: float
    components: list[LeagueSoftnessComponentOut]


class MyEdgeOut(BaseModel):
    """A team's MyEdge v1 score + component breakdown (Phase 14)."""

    team_id: int
    team_name: str | None
    is_me: bool
    my_edge_score: float
    components: list[MyEdgeComponentOut]


class LineupEfficiencyOut(BaseModel):
    """A team's started-vs-optimal lineup efficiency (Phase 13)."""

    team_id: int
    team_name: str | None
    lineup_efficiency: float
    started_points_avg: float
    optimal_points_avg: float
    points_left_on_bench_avg: float


class MetricMomentumOut(BaseModel):
    status: Literal["pending", "first_sync", "up", "down", "flat"]
    delta: float | None
    streak_direction: Literal["up", "down"] | None
    streak_count: int
    history_count: int


class AchievementOut(BaseModel):
    key: str
    label: str
    detail: str


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
    momentum: MetricMomentumOut
    achievements: list[AchievementOut] = Field(default_factory=list)
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
    my_team_logo_url: str | None
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
    # Phase 16: full Edge Index composite, carried alongside edge_score (board still shows
    # edge_score for now). Null until both/either half is available.
    edge_index_score: float | None = None
    edge_index_grade: str | None = None
    edge_index_verdict: str | None = None
    edge_index_momentum: MetricMomentumOut
    achievements: list[AchievementOut] = Field(default_factory=list)


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
    aggregate_wins: int
    aggregate_losses: int
    aggregate_ties: int
    # Phase 16 Edge Index — the primary "advantage" aggregates (Phase 17):
    edge_index_scored_count: int = 0
    edge_index_advantaged_count: int = 0
    best_edge_index_score: float | None = None
    worst_edge_index_score: float | None = None
    # Legacy Phase 3 edge_score aggregates (kept for backward compatibility):
    advantaged_count: int
    scored_count: int
    best_edge_score: float | None = None
    worst_edge_score: float | None = None


# ---- Phase 23 portfolio draft analytics -----------------------------------
class ExposureLeagueBreakdownOut(BaseModel):
    league_id: int
    league_name: str | None
    team_id: int
    team_name: str | None
    overall: int | None
    round: int | None
    round_pick: int | None
    draft_type: str | None
    keeper: bool


class PlayerExposureOut(BaseModel):
    espn_player_id: int
    player_name: str | None
    position: str | None
    nfl_team: str | None
    rostered_teams: int
    teams_in_scope: int
    exposure_pct: float
    share: str
    rostered_leagues: int
    leagues_in_scope: int
    league_exposure_pct: float
    league_share: str
    my_rostered_teams: int
    my_teams_in_scope: int
    my_rostered_leagues: int
    my_leagues_in_scope: int
    my_exposure_pct: float
    my_share: str
    field_rostered_teams: int
    field_teams_in_scope: int
    field_rostered_leagues: int
    field_leagues_in_scope: int
    field_exposure_pct: float
    field_share: str
    field_slot_pct: float
    field_slot_share: str
    leverage_pp: float
    avg_overall: float | None
    min_overall: int | None
    max_overall: int | None
    avg_pick_value: float | None
    auction_rosters: int
    leagues: list[ExposureLeagueBreakdownOut]


class ExposureCoverageOut(BaseModel):
    league_count: int
    teams_in_scope: int
    my_teams_in_scope: int
    field_teams_in_scope: int
    auction_teams: int
    auction_picks: int
    keeper_picks: int
    pick_value_picks: int
    pre_draft_leagues_excluded: int
    notes: list[str] = Field(default_factory=list)


class PositionSpendOut(BaseModel):
    position: str
    pick_count: int
    pick_value: float
    pick_value_pct: float
    total_pick_value: float
    field_pick_count: int
    field_pick_value: float
    field_pick_value_pct: float
    field_total_pick_value: float
    leverage_pp: float


class RoundFingerprintOut(BaseModel):
    bucket: str
    position: str
    pick_count: int
    picks_per_team: float
    pick_pct: float
    bucket_picks: int
    field_pick_count: int
    field_picks_per_team: float
    field_pick_pct: float
    field_bucket_picks: int
    leverage_pp: float


class NflTeamConcentrationOut(BaseModel):
    nfl_team: str
    rostered_teams: int
    teams_in_scope: int
    exposure_pct: float
    share: str
    teams_with_player: int
    player_team_instances: int
    penetration_pct: float
    penetration_share: str
    players_per_team: float
    players_per_team_share: str


class CoreDartOut(BaseModel):
    core_players: int
    dart_players: int
    core_definition: str
    dart_definition: str


class ExposureLeverageHeadlineOut(BaseModel):
    espn_player_id: int
    player_name: str | None
    position: str | None
    nfl_team: str | None
    exposure_pct: float
    field_exposure_pct: float
    leverage_pp: float
    share: str
    field_share: str
    field_slot_pct: float
    field_slot_share: str


class PositionalCapitalHeadlineOut(BaseModel):
    position: str
    pick_value_pct: float
    field_pick_value_pct: float
    leverage_pp: float
    pick_count: int
    field_pick_count: int


class MarketMoveHeadlineOut(BaseModel):
    espn_player_id: int
    player_name: str | None
    position: str | None
    nfl_team: str | None
    draft_time_adp: float
    current_ffc_adp: float
    market_move: float
    market_move_abs: float
    market_move_label: str
    exposure_pct: float
    share: str


class ExposureViewOut(BaseModel):
    row_count: int
    default_sort: str


class ExposureViewsOut(BaseModel):
    rostered: ExposureViewOut
    field_owned: ExposureViewOut
    all: ExposureViewOut


class ExposureHeadlinesOut(BaseModel):
    highest_leverage: ExposureLeverageHeadlineOut | None
    most_underowned: ExposureLeverageHeadlineOut | None
    positional_capital_vs_field: PositionalCapitalHeadlineOut | None
    most_concentrated_nfl_team: NflTeamConcentrationOut | None
    largest_market_move: MarketMoveHeadlineOut | None


class PortfolioExposureOut(BaseModel):
    scope: Literal["me", "opponents"]
    season: int
    teams_in_scope: int
    coverage: ExposureCoverageOut
    headlines: ExposureHeadlinesOut
    views: ExposureViewsOut
    players: list[PlayerExposureOut]
    positional_spend: list[PositionSpendOut]
    round_fingerprint: list[RoundFingerprintOut]
    nfl_team_concentration: list[NflTeamConcentrationOut]
    core_dart: CoreDartOut


class DraftAdpCoverageOut(BaseModel):
    teams_in_scope: int
    auction_teams: int
    keeper_picks: int
    eligible_picks: int
    picks_with_espn_adp: int
    picks_with_ffc_adp: int
    picks_without_espn_adp: int
    picks_without_ffc_adp: int
    ffc_matched_players: int
    ffc_unmatched_players: int
    ffc_snapshot_excluded_players: int
    ffc_resolution_failures: int
    notes: list[str] = Field(default_factory=list)


class FfcSourceSetOut(BaseModel):
    requested_format: str
    requested_teams: int
    used_format: str
    used_teams: int
    year: int
    exact_match: bool
    pulled_at: datetime | None
    stale: bool


class DraftAdpTeamOut(BaseModel):
    league_id: int
    league_name: str | None
    team_id: int
    team_name: str | None
    draft_type: str | None
    draft_value_capture_espn: float | None
    draft_value_capture_ffc: float | None
    draft_adp_source_disagreement: float | None
    draft_value_capture_espn_portfolio_median: float | None
    draft_value_capture_espn_vs_portfolio_median: float | None
    draft_value_capture_ffc_portfolio_median: float | None
    draft_value_capture_ffc_vs_portfolio_median: float | None
    draft_adp_source_disagreement_portfolio_median: float | None
    draft_adp_source_disagreement_vs_portfolio_median: float | None


class DraftAdpBucketOut(BaseModel):
    source: Literal["espn", "ffc"]
    source_label: str
    bucket: str
    avg_delta: float | None
    picks_with_adp: int
    eligible_picks: int
    portfolio_median_delta: float | None
    portfolio_p25_delta: float | None
    portfolio_p75_delta: float | None
    mean_percentile: float | None


class DraftAdpPickOut(BaseModel):
    source: Literal["espn", "ffc"]
    source_label: str
    league_id: int
    league_name: str | None
    team_id: int
    team_name: str | None
    espn_player_id: int | None
    player_name: str | None
    position: str | None
    nfl_team: str | None
    overall: int | None
    round: int | None
    adp: float | None
    delta: float
    draft_type: str | None


class DraftAdpUnmatchedPlayerOut(BaseModel):
    espn_player_id: int | None
    player_name: str | None
    position: str | None
    nfl_team: str | None
    reason: Literal["not_in_snapshot", "missing_adp", "resolution_failed"]


class PortfolioDraftAdpOut(BaseModel):
    season: int
    teams_in_scope: int
    coverage: DraftAdpCoverageOut
    source_sets: list[FfcSourceSetOut]
    teams: list[DraftAdpTeamOut]
    by_round: list[DraftAdpBucketOut]
    by_position: list[DraftAdpBucketOut]
    biggest_values: list[DraftAdpPickOut]
    biggest_reaches: list[DraftAdpPickOut]
    unmatched_players: list[DraftAdpUnmatchedPlayerOut]


class StrategyCoverageOut(BaseModel):
    teams_in_scope: int
    qualifying_teams: int
    auction_teams: int
    keeper_picks: int
    missing_strategy_teams: int
    notes: list[str] = Field(default_factory=list)


class StrategyDistributionOut(BaseModel):
    label: str
    count: int
    pct: float


class StrategyEdgeSummaryOut(BaseModel):
    label: str
    mean_edge_index_score: float | None
    mean_edge_index_score_unrounded: float | None
    stddev_edge_index_score: float | None
    ci95_low: float | None
    ci95_high: float | None
    teams_with_edge_index: int


class StrategyTriggerPickOut(BaseModel):
    overall: int
    player_name: str | None
    position: str | None
    nfl_team: str | None


class StrategyTeamOut(BaseModel):
    league_id: int
    league_name: str | None
    team_id: int
    team_name: str | None
    draft_type: str | None
    primary_label: str
    primary_confidence: float
    secondary_label: str | None
    secondary_confidence: float | None
    edge_index_score: float | None
    triggering_picks: dict[str, list[StrategyTriggerPickOut]]


class PortfolioStrategiesOut(BaseModel):
    season: int
    teams_in_scope: int
    coverage: StrategyCoverageOut
    primary_distribution: list[StrategyDistributionOut]
    secondary_distribution: list[StrategyDistributionOut]
    mean_edge_index_by_primary: list[StrategyEdgeSummaryOut]
    comparison_note: str
    uncertainty_note: str
    teams: list[StrategyTeamOut]
