// Thin API client. All ESPN traffic goes through the backend (SPEC guardrail 11);
// the frontend only ever talks to our own FastAPI.
const BASE = import.meta.env.VITE_API_BASE ?? "";

export interface Health {
  status: string;
  season: number;
  db_path: string;
}

export async function getHealth(): Promise<Health> {
  return get<Health>("/api/health");
}

// --- Phase 2 read-only view types (mirror api/schemas.py) ------------------
// Metric fields are null until Phase 3 computes them; the UI must not compute.
export interface MetricMomentum {
  status: "pending" | "first_sync" | "up" | "down" | "flat";
  delta: number | null;
  streak_direction: "up" | "down" | null;
  streak_count: number;
  history_count: number;
}

export interface Achievement {
  key: string;
  label: string;
  detail: string;
}

export interface PortfolioRow {
  league_id: number;
  espn_league_id: string;
  season: number;
  league_name: string | null;
  size: number | null;
  account_label: string | null;
  lifecycle: string;
  last_synced_at: string | null;
  last_sync_ok: boolean | null;
  last_sync_error: string | null;
  my_team_id: number | null;
  my_team_name: string | null;
  my_team_logo_url: string | null;
  wins: number | null;
  losses: number | null;
  ties: number | null;
  points_for: number | null;
  points_against: number | null;
  standing: number | null;
  edge_score: number | null;
  grade: string | null;
  playoff_odds: number | null;
  verdict: string | null;
  // Phase 16: full Edge Index composite, carried alongside legacy edge_score and used
  // as the Portfolio Board's primary ranking.
  edge_index_score: number | null;
  edge_index_grade: string | null;
  edge_index_verdict: string | null;
  edge_index_momentum: MetricMomentum;
  achievements: Achievement[];
}

export interface TeamOut {
  id: number;
  espn_team_id: number;
  name: string | null;
  abbrev: string | null;
  is_me: boolean;
  autodrafted: boolean;
  wins: number;
  losses: number;
  ties: number;
  points_for: number;
  points_against: number;
  standing: number | null;
  logo_url: string | null;
}

export interface LeagueOut {
  id: number;
  espn_league_id: string;
  season: number;
  account_id: number | null;
  name: string | null;
  size: number | null;
  draft_type: string | null;
  lifecycle: string;
  my_team_id: number | null;
  my_team_name: string | null;
  my_team_logo_url: string | null;
  is_public: boolean;
  last_synced_at: string | null;
  last_sync_ok: boolean | null;
  last_sync_error: string | null;
}

// A within-league percentile that feeds edge_score (Phase 9). Rendered as a bar;
// React only formats these — the backend computes and persists them.
export interface EdgeComponent {
  key: string;
  label: string;
  weight: number;
  percentile: number;
}

export interface LeagueOverview {
  league: LeagueOut;
  account_label: string | null;
  scoring: string | null;
  teams: TeamOut[];
  edge_score: number | null;
  grade: string | null;
  verdict: string | null;
  playoff_odds: number | null;
  components: EdgeComponent[];
  momentum: MetricMomentum;
  achievements: Achievement[];
}

export interface DraftPickOut {
  overall: number | null;
  round: number | null;
  round_pick: number | null;
  team_id: number | null;
  espn_player_id: number | null;
  keeper: boolean;
  autodraft: boolean;
  bid_amount: number | null;
  adp_at_draft: number | null;
  value_delta: number | null;
  player_name: string | null;
  player_position: string | null;
}

export interface MatchupOut {
  week: number;
  home_team_id: number | null;
  away_team_id: number | null;
  home_points: number | null;
  away_points: number | null;
  home_projected_points: number | null;
  away_projected_points: number | null;
  is_playoff: boolean;
}

export type RosterSection = "starters" | "bench" | "ir";

export interface RosterSlotOut {
  slot_id: number;
  slot_label: string;
  slot_index: number;
  section: RosterSection;
  espn_player_id: number | null;
  player_name: string | null;
  player_position: string | null;
  nfl_team: string | null;
  opponent: string | null;
  kickoff_at: string | null;
  game_status: "pregame" | "in_progress" | "final" | "bye" | null;
  injury_status: string | null;
  actual_points: number | null;
  projected_points: number | null;
}

export interface TeamMatchupSideOut {
  team: TeamOut | null;
  points: number | null;
  projected_points: number | null;
}

export interface TeamMatchupOut {
  matchup_period: number;
  scoring_period: number | null;
  is_playoff: boolean;
  home: TeamMatchupSideOut;
  away: TeamMatchupSideOut;
  next_kickoff_at: string | null;
}

export interface TeamDetailOut {
  league: LeagueOut;
  account_label: string | null;
  scoring: string | null;
  team: TeamOut;
  current_scoring_period: number | null;
  current_matchup_period: number | null;
  roster_status: "current" | "stale" | "unavailable";
  roster_synced_at: string | null;
  starters: RosterSlotOut[];
  bench: RosterSlotOut[];
  ir: RosterSlotOut[];
  matchup: TeamMatchupOut | null;
}

// Full Edge Index v1 (Phase 16): 0.5·MyEdge + 0.5·LeagueSoftness. Backend-computed.
export interface EdgeIndexComponentOut {
  key: string;
  label: string;
  weight: number;
  value: number; // the 0–100 sub-score (not a percentile)
}
export interface EdgeIndexOut {
  team_id: number;
  team_name: string | null;
  is_me: boolean;
  edge_index_score: number;
  grade: string | null;
  verdict: string | null;
  components: EdgeIndexComponentOut[];
}

// LeagueSoftness v1 (Phase 15): how exploitable a team's opponents are. Backend-computed.
export interface LeagueSoftnessComponentOut {
  key: string;
  label: string;
  weight: number;
  percentile: number;
}
export interface LeagueSoftnessOut {
  team_id: number;
  team_name: string | null;
  is_me: boolean;
  league_softness_score: number;
  components: LeagueSoftnessComponentOut[];
}

// MyEdge v1 (Phase 14): a separate blended score + component breakdown. Backend-computed.
export interface MyEdgeComponentOut {
  key: string;
  label: string;
  weight: number;
  percentile: number;
}
export interface MyEdgeOut {
  team_id: number;
  team_name: string | null;
  is_me: boolean;
  my_edge_score: number;
  components: MyEdgeComponentOut[];
}

// Started-vs-optimal lineup efficiency (Phase 13). Backend-computed; React formats only.
export interface LineupEfficiencyOut {
  team_id: number;
  team_name: string | null;
  lineup_efficiency: number;
  started_points_avg: number;
  optimal_points_avg: number;
  points_left_on_bench_avg: number;
}

// Actual vs all-play record + luck delta (Phase 12). Backend-computed; React formats only.
export interface AllPlayOut {
  team_id: number;
  team_name: string | null;
  wins: number;
  losses: number;
  ties: number;
  win_pct: number;
  all_play_wins: number;
  all_play_losses: number;
  all_play_ties: number;
  all_play_win_pct: number;
  luck_delta: number;
}

export interface TransactionOut {
  team_id: number | null;
  type: string | null;
  week: number | null;
  player_in: number | null;
  player_out: number | null;
  player_in_name: string | null;
  player_in_position: string | null;
  player_out_name: string | null;
  player_out_position: string | null;
  bid: number | null;
  executed_at: string | null;
}

// --- Phase 23/24 portfolio draft analytics --------------------------------
export type ExposureScope = "me" | "opponents";
export type ExposureView = "rostered" | "field_owned" | "all";

export interface ExposureLeagueBreakdown {
  league_id: number;
  league_name: string | null;
  team_id: number;
  team_name: string | null;
  overall: number | null;
  round: number | null;
  round_pick: number | null;
  draft_type: string | null;
  keeper: boolean;
}

export interface PlayerExposure {
  espn_player_id: number;
  player_name: string | null;
  position: string | null;
  nfl_team: string | null;
  rostered_teams: number;
  teams_in_scope: number;
  exposure_pct: number;
  share: string;
  rostered_leagues: number;
  leagues_in_scope: number;
  league_exposure_pct: number;
  league_share: string;
  my_rostered_teams: number;
  my_teams_in_scope: number;
  my_rostered_leagues: number;
  my_leagues_in_scope: number;
  my_exposure_pct: number;
  my_share: string;
  field_rostered_teams: number;
  field_teams_in_scope: number;
  field_rostered_leagues: number;
  field_leagues_in_scope: number;
  field_exposure_pct: number;
  field_share: string;
  field_slot_pct: number;
  field_slot_share: string;
  leverage_pp: number;
  avg_overall: number | null;
  min_overall: number | null;
  max_overall: number | null;
  avg_pick_value: number | null;
  auction_rosters: number;
  leagues: ExposureLeagueBreakdown[];
}

export interface NflTeamConcentration {
  nfl_team: string;
  rostered_teams: number;
  teams_in_scope: number;
  exposure_pct: number;
  share: string;
  teams_with_player: number;
  player_team_instances: number;
  penetration_pct: number;
  penetration_share: string;
  players_per_team: number;
  players_per_team_share: string;
}

export interface PortfolioExposure {
  scope: ExposureScope;
  season: number;
  teams_in_scope: number;
  coverage: {
    league_count: number;
    teams_in_scope: number;
    my_teams_in_scope: number;
    field_teams_in_scope: number;
    auction_teams: number;
    auction_picks: number;
    keeper_picks: number;
    pick_value_picks: number;
    pre_draft_leagues_excluded: number;
    notes: string[];
  };
  headlines: {
    highest_leverage: {
      espn_player_id: number;
      player_name: string | null;
      position: string | null;
      nfl_team: string | null;
      exposure_pct: number;
      field_exposure_pct: number;
      leverage_pp: number;
      share: string;
      field_share: string;
      field_slot_pct: number;
      field_slot_share: string;
    } | null;
    most_underowned: {
      espn_player_id: number;
      player_name: string | null;
      position: string | null;
      nfl_team: string | null;
      exposure_pct: number;
      field_exposure_pct: number;
      leverage_pp: number;
      share: string;
      field_share: string;
      field_slot_pct: number;
      field_slot_share: string;
    } | null;
    positional_capital_vs_field: {
      position: string;
      pick_value_pct: number;
      field_pick_value_pct: number;
      leverage_pp: number;
      pick_count: number;
      field_pick_count: number;
    } | null;
    most_concentrated_nfl_team: NflTeamConcentration | null;
    largest_market_move: {
      espn_player_id: number;
      player_name: string | null;
      position: string | null;
      nfl_team: string | null;
      draft_time_adp: number;
      current_ffc_adp: number;
      market_move: number;
      market_move_abs: number;
      market_move_label: string;
      exposure_pct: number;
      share: string;
    } | null;
  };
  views: Record<ExposureView, {
    row_count: number;
    default_sort: string;
  }>;
  players: PlayerExposure[];
  positional_spend: Array<{
    position: string;
    pick_count: number;
    pick_value: number;
    pick_value_pct: number;
    total_pick_value: number;
    field_pick_count: number;
    field_pick_value: number;
    field_pick_value_pct: number;
    field_total_pick_value: number;
    leverage_pp: number;
  }>;
  round_fingerprint: Array<{
    bucket: string;
    position: string;
    pick_count: number;
    picks_per_team: number;
    pick_pct: number;
    bucket_picks: number;
    field_pick_count: number;
    field_picks_per_team: number;
    field_pick_pct: number;
    field_bucket_picks: number;
    leverage_pp: number;
  }>;
  nfl_team_concentration: NflTeamConcentration[];
  core_dart: {
    core_players: number;
    dart_players: number;
    core_definition: string;
    dart_definition: string;
  };
}

export interface DraftAdpTeam {
  league_id: number;
  league_name: string | null;
  team_id: number;
  team_name: string | null;
  draft_type: string | null;
  draft_value_capture_espn: number | null;
  draft_value_capture_ffc: number | null;
  draft_adp_source_disagreement: number | null;
  draft_value_capture_espn_portfolio_median: number | null;
  draft_value_capture_espn_vs_portfolio_median: number | null;
  draft_value_capture_ffc_portfolio_median: number | null;
  draft_value_capture_ffc_vs_portfolio_median: number | null;
  draft_adp_source_disagreement_portfolio_median: number | null;
  draft_adp_source_disagreement_vs_portfolio_median: number | null;
}

export interface DraftAdpPick {
  source: "espn" | "ffc";
  source_label: string;
  league_id: number;
  league_name: string | null;
  team_id: number;
  team_name: string | null;
  espn_player_id: number | null;
  player_name: string | null;
  position: string | null;
  nfl_team: string | null;
  overall: number | null;
  round: number | null;
  adp: number | null;
  delta: number;
  draft_type: string | null;
}

export interface PortfolioDraftAdp {
  season: number;
  teams_in_scope: number;
  coverage: {
    teams_in_scope: number;
    auction_teams: number;
    keeper_picks: number;
    eligible_picks: number;
    picks_with_espn_adp: number;
    picks_with_ffc_adp: number;
    picks_without_espn_adp: number;
    picks_without_ffc_adp: number;
    ffc_matched_players: number;
    ffc_unmatched_players: number;
    ffc_snapshot_excluded_players: number;
    ffc_resolution_failures: number;
    notes: string[];
  };
  source_sets: Array<{
    requested_format: string;
    requested_teams: number;
    used_format: string;
    used_teams: number;
    year: number;
    exact_match: boolean;
    pulled_at: string | null;
    stale: boolean;
  }>;
  teams: DraftAdpTeam[];
  by_round: Array<{
    source: "espn" | "ffc";
    source_label: string;
    bucket: string;
    avg_delta: number | null;
    picks_with_adp: number;
    eligible_picks: number;
    portfolio_median_delta: number | null;
    portfolio_p25_delta: number | null;
    portfolio_p75_delta: number | null;
    mean_percentile: number | null;
  }>;
  by_position: Array<{
    source: "espn" | "ffc";
    source_label: string;
    bucket: string;
    avg_delta: number | null;
    picks_with_adp: number;
    eligible_picks: number;
    portfolio_median_delta: number | null;
    portfolio_p25_delta: number | null;
    portfolio_p75_delta: number | null;
    mean_percentile: number | null;
  }>;
  biggest_values: DraftAdpPick[];
  biggest_reaches: DraftAdpPick[];
  unmatched_players: Array<{
    espn_player_id: number | null;
    player_name: string | null;
    position: string | null;
    nfl_team: string | null;
    reason: "not_in_snapshot" | "missing_adp" | "resolution_failed";
  }>;
}

export interface PortfolioStrategies {
  season: number;
  teams_in_scope: number;
  coverage: {
    teams_in_scope: number;
    qualifying_teams: number;
    auction_teams: number;
    keeper_picks: number;
    missing_strategy_teams: number;
    notes: string[];
  };
  primary_distribution: Array<{ label: string; count: number; pct: number }>;
  secondary_distribution: Array<{ label: string; count: number; pct: number }>;
  mean_edge_index_by_primary: Array<{
    label: string;
    mean_edge_index_score: number | null;
    mean_edge_index_score_unrounded: number | null;
    stddev_edge_index_score: number | null;
    ci95_low: number | null;
    ci95_high: number | null;
    teams_with_edge_index: number;
  }>;
  comparison_note: string;
  uncertainty_note: string;
  teams: Array<{
    league_id: number;
    league_name: string | null;
    team_id: number;
    team_name: string | null;
    draft_type: string | null;
    primary_label: string;
    primary_confidence: number;
    secondary_label: string | null;
    secondary_confidence: number | null;
    edge_index_score: number | null;
    triggering_picks: Record<
      string,
      Array<{
        overall: number;
        player_name: string | null;
        position: string | null;
        nfl_team: string | null;
      }>
    >;
  }>;
}

export interface AccountOut {
  id: number;
  label: string;
  status: string;
  created_at: string;
}

export interface PortfolioSummary {
  total_leagues: number;
  aggregate_wins: number;
  aggregate_losses: number;
  aggregate_ties: number;
  // Phase 16 Edge Index — primary advantage aggregates (Phase 17):
  edge_index_scored_count: number;
  edge_index_advantaged_count: number;
  best_edge_index_score: number | null;
  worst_edge_index_score: number | null;
  // Legacy Phase 3 edge_score aggregates:
  advantaged_count: number;
  scored_count: number;
  best_edge_score: number | null;
  worst_edge_score: number | null;
}

export interface DiscoveredLeague {
  espn_league_id: string;
  name: string | null;
  season: number | null;
  team_id: number | null;
}

export interface SyncSummary {
  league_id: string;
  season: number;
  name: string | null;
  lifecycle: string | null;
  teams: number | null;
  draft_picks: number | null;
  matchups: number | null;
  transactions: number | null;
  current_roster_entries: number | null;
  metric_snapshots: number | null;
  my_team_espn_id: number | null;
  needs_reauth: boolean | null;
  errors: string[];
}

// --- AI layer (Phase 4) ----------------------------------------------------
export interface AiStatus {
  enabled: boolean;
  standard_model: string;
  bulk_model: string;
}

// Report content dicts are loosely typed (backend validates against pydantic).
export interface DraftRecapContent {
  espn_team_id: number;
  team_name: string | null;
  strategy_label: string;
  secondary_label: string | null;
  grade: string;
  confidence: string;
  summary: string;
  key_values: string[];
  key_reaches: string[];
}

export interface LeagueBriefContent {
  difficulty_tier: string;
  narrative: string;
  exploit_plan: string[];
}

export interface AdvantageVerdictContent {
  verdict_label: string;
  paragraph: string;
  highest_leverage_move: string;
}

export interface WeeklyRecapContent {
  headline: string;
  body: string;
  luck_notes: string[];
  waiver_highlights: string[];
  week?: number;
}

export interface TradeProposal {
  i_give: string[];
  i_get: string[];
  i_give_players?: PlayerReference[];
  i_get_players?: PlayerReference[];
  rationale: string;
}
export interface PlayerReference {
  espn_player_id: number | null;
  name: string;
  position: string | null;
}
export interface TradeFinderContent {
  proposals: TradeProposal[];
  note: string;
  opponent_team_id?: number;
  opponent_name?: string;
  // Provenance (Phase 22): where the roster facts came from + freshness.
  grounding_source?: "lineup_snapshot" | "drafted_roster" | "none";
  snapshot_week?: number | null;
  fallback_reason?: string | null;
  snapshot_stale?: boolean;
  projections_stale?: boolean;
  my_projection_coverage?: number | null;
}

export interface AiReportEnvelope<T = Record<string, unknown>> {
  enabled: boolean;
  kind: string;
  model: string | null;
  created_at: string | null;
  stale: boolean;
  content: T | null;
  error: string | null;
}

export interface AiReportList<T = Record<string, unknown>> {
  enabled: boolean;
  kind: string;
  reports: T[];
  error: string | null;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(await errorText(res, path));
  return res.json() as Promise<T>;
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(await errorText(res, path));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

async function errorText(res: Response, path: string): Promise<string> {
  try {
    const j = await res.json();
    if (j?.detail) return typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
  } catch {
    /* fall through */
  }
  return `${path} ${res.status}`;
}

// Reads (view endpoints — every number comes from the DB; no ESPN, no math here).
export const getPortfolio = () => get<PortfolioRow[]>("/api/portfolio");
export const getPortfolioSummary = () => get<PortfolioSummary>("/api/portfolio/summary");
export const getPortfolioExposure = (scope: ExposureScope = "me") =>
  get<PortfolioExposure>(`/api/portfolio/exposure?scope=${scope}`);
export const getPortfolioDraftAdp = () =>
  get<PortfolioDraftAdp>("/api/portfolio/draft-adp");
export const getPortfolioStrategies = () =>
  get<PortfolioStrategies>("/api/portfolio/strategies");
export const getLeagues = () => get<LeagueOut[]>("/api/leagues");
export const getLeagueOverview = (id: number) =>
  get<LeagueOverview>(`/api/leagues/${id}/overview`);
export const getLeagueTeams = (id: number) => get<TeamOut[]>(`/api/leagues/${id}/teams`);
export const getLeagueTeamDetail = (leagueId: number, teamId: number) =>
  get<TeamDetailOut>(`/api/leagues/${leagueId}/teams/${teamId}`);
export const getLeagueDraft = (id: number) => get<DraftPickOut[]>(`/api/leagues/${id}/draft`);
export const getLeagueMatchups = (id: number) =>
  get<MatchupOut[]>(`/api/leagues/${id}/matchups`);
export const getLeagueAllPlay = (id: number) =>
  get<AllPlayOut[]>(`/api/leagues/${id}/all-play`);
export const getLeagueLineupEfficiency = (id: number) =>
  get<LineupEfficiencyOut[]>(`/api/leagues/${id}/lineup-efficiency`);
export const getLeagueMyEdge = (id: number) =>
  get<MyEdgeOut[]>(`/api/leagues/${id}/my-edge`);
export const getLeagueSoftness = (id: number) =>
  get<LeagueSoftnessOut[]>(`/api/leagues/${id}/league-softness`);
export const getLeagueEdgeIndex = (id: number) =>
  get<EdgeIndexOut[]>(`/api/leagues/${id}/edge-index`);
export const getLeagueActivity = (id: number) =>
  get<TransactionOut[]>(`/api/leagues/${id}/activity`);

// Accounts + leagues management.
export const getAccounts = () => get<AccountOut[]>("/api/accounts");
export const addAccount = (label: string, swid: string, espn_s2: string) =>
  send<AccountOut>("POST", "/api/accounts", { label, swid, espn_s2 });
export const deleteAccount = (id: number) => send<void>("DELETE", `/api/accounts/${id}`);
// Re-auth: replace an account's cookies after they expire (Phase 7). swid/espn_s2
// go only in the request body; the response (AccountOut) never echoes them back.
export const reauthAccount = (id: number, swid: string, espn_s2: string) =>
  send<AccountOut>("POST", `/api/accounts/${id}/reauth`, { swid, espn_s2 });
export const discoverLeagues = (accountId: number) =>
  get<DiscoveredLeague[]>(`/api/leagues/discover/${accountId}`);
export const addLeague = (leagueRef: string, accountId: number | null, season?: number) =>
  send<LeagueOut>("POST", "/api/leagues", {
    league_ref: leagueRef,
    account_id: accountId,
    season: season ?? null,
  });
export const syncLeague = (id: number) => send<SyncSummary>("POST", `/api/leagues/${id}/sync`);

// Exports (Phase 5) — file downloads; the backend sets the filename via
// Content-Disposition and the response reflects DB data exactly (no frontend recompute).
export const EXPORT_ENDPOINTS = {
  csv: "/api/exports/portfolio.csv",
  json: "/api/exports/portfolio.json",
  xlsx: "/api/exports/portfolio.xlsx",
} as const;
export type ExportKind = keyof typeof EXPORT_ENDPOINTS;

export function downloadExport(kind: ExportKind): void {
  downloadPath(EXPORT_ENDPOINTS[kind]);
}

export type AnalyticsCsvKind = "exposure" | "draft-adp" | "strategies";

export function downloadAnalyticsCsv(
  kind: AnalyticsCsvKind,
  scope: ExposureScope = "me",
  view: ExposureView = "all",
): void {
  const path =
    kind === "exposure"
      ? `/api/exports/exposure.csv?scope=${scope}&view=${view}`
      : `/api/exports/${kind}.csv`;
  downloadPath(path);
}

function downloadPath(path: string): void {
  const a = document.createElement("a");
  a.href = `${BASE}${path}`;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// AI reads + generate (POST triggers a model call unless cached; ?force=true regenerates).
export const getAiStatus = () => get<AiStatus>("/api/ai/status");
export const getDraftRecaps = (id: number) =>
  get<AiReportList<DraftRecapContent>>(`/api/leagues/${id}/ai/draft-recaps`);
export const generateDraftRecaps = (id: number, force = false) =>
  send<AiReportList<DraftRecapContent>>("POST", `/api/leagues/${id}/ai/draft-recaps?force=${force}`);
export const getLeagueBrief = (id: number) =>
  get<AiReportEnvelope<LeagueBriefContent>>(`/api/leagues/${id}/ai/league-brief`);
export const generateLeagueBrief = (id: number, force = false) =>
  send<AiReportEnvelope<LeagueBriefContent>>("POST", `/api/leagues/${id}/ai/league-brief?force=${force}`);
export const getAdvantageVerdict = (id: number) =>
  get<AiReportEnvelope<AdvantageVerdictContent>>(`/api/leagues/${id}/ai/advantage-verdict`);
export const generateAdvantageVerdict = (id: number, force = false) =>
  send<AiReportEnvelope<AdvantageVerdictContent>>(
    "POST",
    `/api/leagues/${id}/ai/advantage-verdict?force=${force}`,
  );
export const getWeeklyRecap = (id: number, week: number) =>
  get<AiReportEnvelope<WeeklyRecapContent>>(`/api/leagues/${id}/ai/weekly-recap?week=${week}`);
export const generateWeeklyRecap = (id: number, week: number, force = false) =>
  send<AiReportEnvelope<WeeklyRecapContent>>(
    "POST",
    `/api/leagues/${id}/ai/weekly-recap?week=${week}&force=${force}`,
  );
export const getTradeFinder = (id: number, opponentTeamId: number) =>
  get<AiReportEnvelope<TradeFinderContent>>(
    `/api/leagues/${id}/ai/trade-finder?opponent_team_id=${opponentTeamId}`,
  );
export const generateTradeFinder = (id: number, opponentTeamId: number, force = false) =>
  send<AiReportEnvelope<TradeFinderContent>>(
    "POST",
    `/api/leagues/${id}/ai/trade-finder?opponent_team_id=${opponentTeamId}&force=${force}`,
  );
