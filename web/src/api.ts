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
export interface PortfolioRow {
  league_id: number;
  espn_league_id: string;
  season: number;
  league_name: string | null;
  size: number | null;
  account_label: string | null;
  lifecycle: string;
  last_synced_at: string | null;
  my_team_id: number | null;
  my_team_name: string | null;
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
  is_public: boolean;
  last_synced_at: string | null;
}

export interface LeagueOverview {
  league: LeagueOut;
  account_label: string | null;
  scoring: string | null;
  teams: TeamOut[];
  edge_score: number | null;
  grade: string | null;
  verdict: string | null;
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
}

export interface MatchupOut {
  week: number;
  home_team_id: number | null;
  away_team_id: number | null;
  home_points: number | null;
  away_points: number | null;
  is_playoff: boolean;
}

export interface TransactionOut {
  team_id: number | null;
  type: string | null;
  week: number | null;
  player_in: number | null;
  player_out: number | null;
  bid: number | null;
  executed_at: string | null;
}

export interface AccountOut {
  id: number;
  label: string;
  status: string;
  created_at: string;
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
  needs_reauth: boolean | null;
  errors: string[];
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
export const getLeagues = () => get<LeagueOut[]>("/api/leagues");
export const getLeagueOverview = (id: number) =>
  get<LeagueOverview>(`/api/leagues/${id}/overview`);
export const getLeagueTeams = (id: number) => get<TeamOut[]>(`/api/leagues/${id}/teams`);
export const getLeagueDraft = (id: number) => get<DraftPickOut[]>(`/api/leagues/${id}/draft`);
export const getLeagueMatchups = (id: number) =>
  get<MatchupOut[]>(`/api/leagues/${id}/matchups`);
export const getLeagueActivity = (id: number) =>
  get<TransactionOut[]>(`/api/leagues/${id}/activity`);

// Accounts + leagues management.
export const getAccounts = () => get<AccountOut[]>("/api/accounts");
export const addAccount = (label: string, swid: string, espn_s2: string) =>
  send<AccountOut>("POST", "/api/accounts", { label, swid, espn_s2 });
export const deleteAccount = (id: number) => send<void>("DELETE", `/api/accounts/${id}`);
export const discoverLeagues = (accountId: number) =>
  get<DiscoveredLeague[]>(`/api/leagues/discover/${accountId}`);
export const addLeague = (leagueRef: string, accountId: number | null, season?: number) =>
  send<LeagueOut>("POST", "/api/leagues", {
    league_ref: leagueRef,
    account_id: accountId,
    season: season ?? null,
  });
export const syncLeague = (id: number) => send<SyncSummary>("POST", `/api/leagues/${id}/sync`);
