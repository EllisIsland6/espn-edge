import { expect, test, type Page, type Route } from "@playwright/test";

// Deterministic mock data — the app's read models. No backend/ESPN involved.
const NOW = new Date("2026-07-09T12:00:00Z").toISOString();

const PORTFOLIO = [
  {
    league_id: 1, espn_league_id: "111", season: 2026, league_name: "Alpha League",
    size: 8, account_label: "Main", lifecycle: "in_season", last_synced_at: NOW,
    last_sync_ok: true, last_sync_error: null,
    my_team_id: 1, my_team_name: "My Team", my_team_logo_url: "/mock-team-logo/my.svg",
    wins: 5, losses: 2, ties: 0,
    points_for: 900.5, points_against: 820.1, standing: 2,
    edge_score: 72.0, grade: "B", playoff_odds: 0.81, verdict: "advantaged",
    edge_index_score: 68.0, edge_index_grade: "B", edge_index_verdict: "advantaged",
    edge_index_momentum: {
      status: "up", delta: 4.5, streak_direction: "up", streak_count: 2, history_count: 3,
    },
    achievements: [
      { key: "sync_healthy", label: "Sync healthy", detail: "Most recent sync completed" },
      { key: "all_play_edge", label: "All-play 60%+", detail: "All-play win rate is 66.7%" },
    ],
  },
  {
    league_id: 2, espn_league_id: "222", season: 2026, league_name: "Beta League",
    size: 10, account_label: "Main", lifecycle: "pre_draft", last_synced_at: null,
    last_sync_ok: null, last_sync_error: null,
    my_team_id: null, my_team_name: null, my_team_logo_url: null,
    wins: null, losses: null, ties: null,
    points_for: null, points_against: null, standing: null,
    edge_score: null, grade: null, playoff_odds: null, verdict: null,
    edge_index_score: null, edge_index_grade: null, edge_index_verdict: null,
    edge_index_momentum: {
      status: "pending", delta: null, streak_direction: null, streak_count: 0, history_count: 0,
    },
    achievements: [],
  },
];

const SUMMARY = {
  total_leagues: 2, aggregate_wins: 5, aggregate_losses: 2, aggregate_ties: 0,
  edge_index_scored_count: 1, edge_index_advantaged_count: 1,
  best_edge_index_score: 68.0, worst_edge_index_score: 68.0,
  advantaged_count: 1, scored_count: 1, best_edge_score: 72.0, worst_edge_score: 72.0,
};

const LEAGUE_1 = {
  id: 1, espn_league_id: "111", season: 2026, account_id: 1, name: "Alpha League",
  size: 8, draft_type: "SNAKE", lifecycle: "in_season", my_team_id: 1,
  my_team_name: "My Team", my_team_logo_url: "/mock-team-logo/my.svg",
  is_public: false, last_synced_at: NOW, last_sync_ok: true, last_sync_error: null,
};

const OVERVIEW = {
  league: LEAGUE_1,
  account_label: "Main",
  scoring: "PPR",
  teams: [
    {
      id: 2, espn_team_id: 2, name: "Rival", abbrev: "RIV", is_me: false,
      autodrafted: false, wins: 6, losses: 1, ties: 0, points_for: 950.0,
      points_against: 800.0, standing: 1, logo_url: "/mock-team-logo/rival.svg",
    },
    {
      id: 1, espn_team_id: 1, name: "My Team", abbrev: "MINE", is_me: true,
      autodrafted: false, wins: 5, losses: 2, ties: 0, points_for: 900.5,
      points_against: 820.1, standing: 2, logo_url: "/mock-team-logo/my.svg",
    },
  ],
  edge_score: 72.0, grade: "B", verdict: "advantaged", playoff_odds: 0.81,
  momentum: {
    status: "up", delta: 3.0, streak_direction: "up", streak_count: 2, history_count: 3,
  },
  achievements: [
    { key: "sync_healthy", label: "Sync healthy", detail: "Most recent sync completed" },
  ],
  components: [
    { key: "win_pct", label: "Win %", weight: 0.4, percentile: 75.0 },
    { key: "points_for", label: "Points for", weight: 0.3, percentile: 87.5 },
    { key: "point_diff", label: "Point differential", weight: 0.3, percentile: 62.5 },
  ],
};

const rosterSlot = (
  slotId: number,
  slotLabel: string,
  slotIndex: number,
  section: "starters" | "bench" | "ir",
  player: {
    id: number;
    name: string;
    position: string;
    nflTeam: string;
    opponent?: string | null;
    actual?: number | null;
    projected?: number | null;
    injury?: string | null;
    status?: "pregame" | "in_progress" | "final" | "bye" | null;
  } | null,
) => ({
  slot_id: slotId,
  slot_label: slotLabel,
  slot_index: slotIndex,
  section,
  espn_player_id: player?.id ?? null,
  player_name: player?.name ?? null,
  player_position: player?.position ?? null,
  nfl_team: player?.nflTeam ?? null,
  opponent: player?.opponent ?? null,
  kickoff_at: player?.status === "bye" ? null : "2026-09-10T20:20:00Z",
  game_status: player?.status ?? null,
  injury_status: player?.injury ?? null,
  actual_points: player?.actual ?? null,
  projected_points: player?.projected ?? null,
});

function teamDetail(teamId: number) {
  const team = OVERVIEW.teams.find((candidate) => candidate.id === teamId) ?? OVERVIEW.teams[1];
  return {
    league: LEAGUE_1,
    account_label: "Main",
    scoring: "PPR",
    team,
    current_scoring_period: 1,
    current_matchup_period: 1,
    roster_status: "current",
    roster_synced_at: NOW,
    starters: [
      rosterSlot(0, "QB", 0, "starters", {
        id: 1001, name: "Star Quarterback", position: "QB", nflTeam: "ATL",
        opponent: "BUF", actual: 0, projected: 18.5, injury: "QUESTIONABLE",
        status: "pregame",
      }),
      rosterSlot(2, "RB", 0, "starters", {
        id: 1002, name: "Star Runningback", position: "RB", nflTeam: "BUF",
        opponent: "ATL", projected: 15.2, status: "pregame",
      }),
      rosterSlot(2, "RB", 1, "starters", null),
      rosterSlot(4, "WR", 0, "starters", {
        id: 1003, name: "Ace Receiver", position: "WR", nflTeam: "CHI",
        status: "bye",
      }),
      rosterSlot(6, "TE", 0, "starters", null),
      rosterSlot(23, "FLEX", 0, "starters", null),
      rosterSlot(16, "D/ST", 0, "starters", null),
      rosterSlot(17, "K", 0, "starters", null),
    ],
    bench: [
      rosterSlot(20, "BE", 0, "bench", {
        id: 1004, name: "Bench Receiver", position: "WR", nflTeam: "CIN",
      }),
      rosterSlot(20, "BE", 1, "bench", null),
    ],
    ir: [
      rosterSlot(21, "IR", 0, "ir", {
        id: 1005, name: "Injured Tight End", position: "TE", nflTeam: "CLE",
        injury: "INJURY_RESERVE",
      }),
    ],
    matchup: {
      matchup_period: 1,
      scoring_period: 1,
      is_playoff: false,
      home: { team: OVERVIEW.teams[1], points: 0, projected_points: 108.4 },
      away: { team: OVERVIEW.teams[0], points: 0, projected_points: 111.2 },
      next_kickoff_at: "2026-09-10T20:20:00Z",
    },
  };
}

const AI_DISABLED = (kind: string) => ({
  enabled: false, kind, model: null, created_at: null, stale: false,
  content: null, error: null,
});

// Matchups + all-play/luck (Phase 12).
const MATCHUPS = [
  { week: 1, home_team_id: 1, away_team_id: 2, home_points: 120.5, away_points: 100.0, is_playoff: false },
  { week: 1, home_team_id: 3, away_team_id: 4, home_points: 90.0, away_points: 80.0, is_playoff: false },
];
const ALL_PLAY = [
  {
    team_id: 1, team_name: "My Team", wins: 1, losses: 0, ties: 0, win_pct: 1.0,
    all_play_wins: 3, all_play_losses: 0, all_play_ties: 0, all_play_win_pct: 1.0, luck_delta: 0.0,
  },
  {
    team_id: 2, team_name: "Rival", wins: 0, losses: 1, ties: 0, win_pct: 0.0,
    all_play_wins: 1, all_play_losses: 2, all_play_ties: 0, all_play_win_pct: 0.3333, luck_delta: 0.3333,
  },
];
// Full Edge Index v1 (Phase 16): my team + a rival, composite of the two halves.
const EDGE_INDEX = [
  {
    team_id: 1, team_name: "My Team", is_me: true, edge_index_score: 68.0, grade: "B", verdict: "advantaged",
    components: [
      { key: "my_edge", label: "MyEdge", weight: 0.5, value: 71.0 },
      { key: "league_softness", label: "LeagueSoftness", weight: 0.5, value: 65.0 },
    ],
  },
  {
    team_id: 2, team_name: "Rival", is_me: false, edge_index_score: 45.0, grade: "C", verdict: "neutral",
    components: [{ key: "my_edge", label: "MyEdge", weight: 1.0, value: 45.0 }],
  },
];

// LeagueSoftness v1 (Phase 15): my team + a rival, with component percentiles.
const LEAGUE_SOFTNESS = [
  {
    team_id: 1, team_name: "My Team", is_me: true, league_softness_score: 66.0,
    components: [
      { key: "exploitable_weakness_share", label: "Exploitable weakness share", weight: 0.5, percentile: 75.0 },
      { key: "abandoned_proxy", label: "Abandoned teams (proxy)", weight: 0.5, percentile: 57.0 },
    ],
  },
  {
    team_id: 2, team_name: "Rival", is_me: false, league_softness_score: 40.0,
    components: [
      { key: "exploitable_weakness_share", label: "Exploitable weakness share", weight: 1.0, percentile: 40.0 },
    ],
  },
];

// MyEdge v1 (Phase 14): my team + a rival, with component percentiles.
const MY_EDGE = [
  {
    team_id: 1, team_name: "My Team", is_me: true, my_edge_score: 71.0,
    components: [
      { key: "roster_strength", label: "Roster strength", weight: 0.5833, percentile: 80.0 },
      { key: "luck_adjusted_record", label: "Luck-adjusted record", weight: 0.4167, percentile: 58.0 },
    ],
  },
  {
    team_id: 2, team_name: "Rival", is_me: false, my_edge_score: 40.0,
    components: [
      { key: "roster_strength", label: "Roster strength", weight: 1.0, percentile: 40.0 },
    ],
  },
];

const LINEUP_EFFICIENCY = [
  {
    team_id: 2, team_name: "Rival", lineup_efficiency: 1.0,
    started_points_avg: 130.0, optimal_points_avg: 130.0, points_left_on_bench_avg: 0.0,
  },
  {
    team_id: 1, team_name: "My Team", lineup_efficiency: 0.8333,
    started_points_avg: 100.0, optimal_points_avg: 120.0, points_left_on_bench_avg: 20.0,
  },
];

// Draft board with a resolved player name/position/ADP (Phase 10).
const DRAFT = [
  {
    overall: 1, round: 1, round_pick: 1, team_id: 1, espn_player_id: 1001,
    keeper: false, autodraft: false, bid_amount: null,
    adp_at_draft: 3.4, value_delta: 2.4, player_name: "Star Runningback", player_position: "RB",
  },
  {
    overall: 2, round: 1, round_pick: 2, team_id: 2, espn_player_id: 1002,
    keeper: false, autodraft: false, bid_amount: null,
    adp_at_draft: 1.1, value_delta: -0.9, player_name: "Ace Receiver", player_position: "WR",
  },
];

const ACTIVITY = [
  {
    team_id: 1, type: "waiver", week: 1,
    player_in: 1001, player_in_name: "Star Runningback", player_in_position: "RB",
    player_out: 1002, player_out_name: "Ace Receiver", player_out_position: "WR",
    bid: 17, executed_at: NOW,
  },
];

const EXPOSURE = {
  scope: "me",
  season: 2026,
  teams_in_scope: 4,
  coverage: {
    league_count: 4, teams_in_scope: 4, auction_teams: 1, auction_picks: 12,
    my_teams_in_scope: 4, field_teams_in_scope: 12,
    keeper_picks: 1, pick_value_picks: 48, pre_draft_leagues_excluded: 1,
    notes: [],
  },
  headlines: {
    highest_leverage: {
      espn_player_id: 104, player_name: "Leverage Sleeper", position: "TE", nfl_team: "LV",
      exposure_pct: 25.0, field_exposure_pct: 0.0, leverage_pp: 25.0,
      share: "1 / 4", field_share: "0 / 4",
      field_slot_pct: 0.0, field_slot_share: "0 / 12",
    },
    most_underowned: {
      espn_player_id: 103, player_name: "Field Favorite", position: "WR", nfl_team: "DET",
      exposure_pct: 0.0, field_exposure_pct: 100.0, leverage_pp: -100.0,
      share: "0 / 4", field_share: "4 / 4",
      field_slot_pct: 50.0, field_slot_share: "6 / 12",
    },
    positional_capital_vs_field: {
      position: "RB", pick_value_pct: 52.2, field_pick_value_pct: 39.0,
      leverage_pp: 13.2, pick_count: 20, field_pick_count: 44,
    },
    most_concentrated_nfl_team: {
      nfl_team: "ATL", rostered_teams: 2, teams_in_scope: 4, exposure_pct: 50.0,
      share: "2 / 4", teams_with_player: 2, player_team_instances: 3,
      penetration_pct: 50.0, penetration_share: "2 / 4",
      players_per_team: 0.75, players_per_team_share: "3 / 4",
    },
    largest_market_move: {
      espn_player_id: 102, player_name: "Puka Nacua", position: "WR", nfl_team: "LAR",
      draft_time_adp: 6.0, current_ffc_adp: 2.0, market_move: -4.0,
      market_move_abs: 4.0, market_move_label: "rose 4.0 picks since draft",
      exposure_pct: 25.0, share: "1 / 4",
    },
  },
  views: {
    rostered: { row_count: 3, default_sort: "leverage_desc" },
    field_owned: { row_count: 1, default_sort: "field_exposure_desc" },
    all: { row_count: 4, default_sort: "leverage_desc" },
  },
  players: [
    {
      espn_player_id: 101, player_name: "Bijan Robinson", position: "RB", nfl_team: "ATL",
      rostered_teams: 2, teams_in_scope: 4, exposure_pct: 50.0, share: "2 / 4",
      rostered_leagues: 2, leagues_in_scope: 4, league_exposure_pct: 50.0,
      league_share: "2 / 4",
      my_rostered_teams: 2, my_teams_in_scope: 4, my_rostered_leagues: 2,
      my_leagues_in_scope: 4, my_exposure_pct: 50.0, my_share: "2 / 4",
      field_rostered_teams: 2, field_teams_in_scope: 12, field_rostered_leagues: 2,
      field_leagues_in_scope: 4, field_exposure_pct: 50.0,
      field_share: "2 / 4", field_slot_pct: 16.7, field_slot_share: "2 / 12",
      leverage_pp: 0.0,
      avg_overall: 3.5, min_overall: 2, max_overall: 5, avg_pick_value: 90.2,
      auction_rosters: 0,
      leagues: [
        {
          league_id: 1, league_name: "Alpha League", team_id: 1, team_name: "My Team",
          overall: 2, round: 1, round_pick: 2, draft_type: "SNAKE", keeper: false,
        },
        {
          league_id: 2, league_name: "Beta League", team_id: 2, team_name: "My Team 2",
          overall: 5, round: 1, round_pick: 5, draft_type: "SNAKE", keeper: false,
        },
      ],
    },
    {
      espn_player_id: 102, player_name: "Puka Nacua", position: "WR", nfl_team: "LAR",
      rostered_teams: 1, teams_in_scope: 4, exposure_pct: 25.0, share: "1 / 4",
      rostered_leagues: 1, leagues_in_scope: 4, league_exposure_pct: 25.0,
      league_share: "1 / 4",
      my_rostered_teams: 1, my_teams_in_scope: 4, my_rostered_leagues: 1,
      my_leagues_in_scope: 4, my_exposure_pct: 25.0, my_share: "1 / 4",
      field_rostered_teams: 5, field_teams_in_scope: 12, field_rostered_leagues: 3,
      field_leagues_in_scope: 4, field_exposure_pct: 75.0,
      field_share: "3 / 4", field_slot_pct: 41.7, field_slot_share: "5 / 12",
      leverage_pp: -50.0,
      avg_overall: 4.0, min_overall: 4, max_overall: 4, avg_pick_value: 86.9,
      auction_rosters: 0,
      leagues: [
        {
          league_id: 3, league_name: "Gamma League", team_id: 3, team_name: "My Team 3",
          overall: 4, round: 1, round_pick: 4, draft_type: "SNAKE", keeper: false,
        },
      ],
    },
    {
      espn_player_id: 103, player_name: "Field Favorite", position: "WR", nfl_team: "DET",
      rostered_teams: 0, teams_in_scope: 4, exposure_pct: 0.0, share: "0 / 4",
      rostered_leagues: 0, leagues_in_scope: 4, league_exposure_pct: 0.0,
      league_share: "0 / 4",
      my_rostered_teams: 0, my_teams_in_scope: 4, my_rostered_leagues: 0,
      my_leagues_in_scope: 4, my_exposure_pct: 0.0, my_share: "0 / 4",
      field_rostered_teams: 6, field_teams_in_scope: 12, field_rostered_leagues: 4,
      field_leagues_in_scope: 4, field_exposure_pct: 100.0,
      field_share: "4 / 4", field_slot_pct: 50.0, field_slot_share: "6 / 12",
      leverage_pp: -100.0,
      avg_overall: null, min_overall: null, max_overall: null, avg_pick_value: null,
      auction_rosters: 0, leagues: [],
    },
    {
      espn_player_id: 104, player_name: "Leverage Sleeper", position: "TE", nfl_team: "LV",
      rostered_teams: 1, teams_in_scope: 4, exposure_pct: 25.0, share: "1 / 4",
      rostered_leagues: 1, leagues_in_scope: 4, league_exposure_pct: 25.0,
      league_share: "1 / 4",
      my_rostered_teams: 1, my_teams_in_scope: 4, my_rostered_leagues: 1,
      my_leagues_in_scope: 4, my_exposure_pct: 25.0, my_share: "1 / 4",
      field_rostered_teams: 0, field_teams_in_scope: 12, field_rostered_leagues: 0,
      field_leagues_in_scope: 4, field_exposure_pct: 0.0,
      field_share: "0 / 4", field_slot_pct: 0.0, field_slot_share: "0 / 12",
      leverage_pp: 25.0,
      avg_overall: 72.0, min_overall: 72, max_overall: 72, avg_pick_value: 8.7,
      auction_rosters: 0,
      leagues: [
        {
          league_id: 4, league_name: "Delta League", team_id: 4, team_name: "My Team 4",
          overall: 72, round: 8, round_pick: 2, draft_type: "SNAKE", keeper: false,
        },
      ],
    },
  ],
  positional_spend: [
    { position: "RB", pick_count: 20, pick_value: 600, pick_value_pct: 52.2, total_pick_value: 1149.4, field_pick_count: 44, field_pick_value: 890, field_pick_value_pct: 39.0, field_total_pick_value: 2282.0, leverage_pp: 13.2 },
    { position: "WR", pick_count: 18, pick_value: 430, pick_value_pct: 37.4, total_pick_value: 1149.4, field_pick_count: 54, field_pick_value: 980, field_pick_value_pct: 42.9, field_total_pick_value: 2282.0, leverage_pp: -5.5 },
    { position: "QB", pick_count: 5, pick_value: 70, pick_value_pct: 6.1, total_pick_value: 1149.4, field_pick_count: 12, field_pick_value: 180, field_pick_value_pct: 7.9, field_total_pick_value: 2282.0, leverage_pp: -1.8 },
    { position: "TE", pick_count: 5, pick_value: 49.4, pick_value_pct: 4.3, total_pick_value: 1149.4, field_pick_count: 10, field_pick_value: 132, field_pick_value_pct: 5.8, field_total_pick_value: 2282.0, leverage_pp: -1.5 },
  ],
  round_fingerprint: [
    { bucket: "1", position: "RB", pick_count: 3, picks_per_team: 0.75, pick_pct: 60.0, bucket_picks: 5, field_pick_count: 4, field_picks_per_team: 0.33, field_pick_pct: 40.0, field_bucket_picks: 10, leverage_pp: 20.0 },
    { bucket: "1", position: "WR", pick_count: 2, picks_per_team: 0.5, pick_pct: 40.0, bucket_picks: 5, field_pick_count: 4, field_picks_per_team: 0.33, field_pick_pct: 40.0, field_bucket_picks: 10, leverage_pp: 0.0 },
    { bucket: "2", position: "RB", pick_count: 2, picks_per_team: 0.5, pick_pct: 40.0, bucket_picks: 5, field_pick_count: 3, field_picks_per_team: 0.25, field_pick_pct: 30.0, field_bucket_picks: 10, leverage_pp: 10.0 },
    { bucket: "2", position: "WR", pick_count: 2, picks_per_team: 0.5, pick_pct: 40.0, bucket_picks: 5, field_pick_count: 5, field_picks_per_team: 0.42, field_pick_pct: 50.0, field_bucket_picks: 10, leverage_pp: -10.0 },
    { bucket: "2", position: "TE", pick_count: 1, picks_per_team: 0.25, pick_pct: 20.0, bucket_picks: 5, field_pick_count: 2, field_picks_per_team: 0.17, field_pick_pct: 20.0, field_bucket_picks: 10, leverage_pp: 0.0 },
  ],
  nfl_team_concentration: [
    { nfl_team: "ATL", rostered_teams: 2, teams_in_scope: 4, exposure_pct: 50.0, share: "2 / 4", teams_with_player: 2, player_team_instances: 3, penetration_pct: 50.0, penetration_share: "2 / 4", players_per_team: 0.75, players_per_team_share: "3 / 4" },
    { nfl_team: "LAR", rostered_teams: 1, teams_in_scope: 4, exposure_pct: 25.0, share: "1 / 4", teams_with_player: 1, player_team_instances: 1, penetration_pct: 25.0, penetration_share: "1 / 4", players_per_team: 0.25, players_per_team_share: "1 / 4" },
  ],
  core_dart: {
    core_players: 0, dart_players: 1,
    core_definition: "rostered on a majority of teams in scope",
    dart_definition: "rostered on exactly one team in scope",
  },
};

const DRAFT_ADP_ANALYTICS = {
  season: 2026,
  teams_in_scope: 4,
  coverage: {
    teams_in_scope: 4, auction_teams: 1, keeper_picks: 1, eligible_picks: 48,
    picks_with_espn_adp: 47, picks_with_ffc_adp: 45,
    picks_without_espn_adp: 1, picks_without_ffc_adp: 3,
    ffc_matched_players: 39, ffc_unmatched_players: 2,
    ffc_snapshot_excluded_players: 2, ffc_resolution_failures: 0, notes: [],
  },
  source_sets: [
    {
      requested_format: "ppr", requested_teams: 10, used_format: "ppr", used_teams: 10,
      year: 2026, exact_match: true, pulled_at: NOW, stale: false,
    },
  ],
  teams: [
    {
      league_id: 1, league_name: "Alpha League", team_id: 1, team_name: "My Team",
      draft_type: "SNAKE", draft_value_capture_espn: 3.2,
      draft_value_capture_ffc: 1.4, draft_adp_source_disagreement: 8.1,
      draft_value_capture_espn_portfolio_median: 2.0,
      draft_value_capture_espn_vs_portfolio_median: 1.2,
      draft_value_capture_ffc_portfolio_median: 0.5,
      draft_value_capture_ffc_vs_portfolio_median: 0.9,
      draft_adp_source_disagreement_portfolio_median: 6.4,
      draft_adp_source_disagreement_vs_portfolio_median: 1.7,
    },
  ],
  by_round: [],
  by_position: [
    { source: "espn", source_label: "vs. draft-time ADP", bucket: "RB", avg_delta: 3.2, picks_with_adp: 20, eligible_picks: 20, portfolio_median_delta: 2.4, portfolio_p25_delta: 0.8, portfolio_p75_delta: 4.1, mean_percentile: 72.5 },
    { source: "espn", source_label: "vs. draft-time ADP", bucket: "WR", avg_delta: -1.1, picks_with_adp: 18, eligible_picks: 18, portfolio_median_delta: -0.6, portfolio_p25_delta: -2.0, portfolio_p75_delta: 1.2, mean_percentile: 42.5 },
    { source: "ffc", source_label: "vs. current market ADP", bucket: "RB", avg_delta: 1.4, picks_with_adp: 19, eligible_picks: 20, portfolio_median_delta: 1.0, portfolio_p25_delta: -0.4, portfolio_p75_delta: 2.8, mean_percentile: 64.0 },
    { source: "ffc", source_label: "vs. current market ADP", bucket: "WR", avg_delta: -2.0, picks_with_adp: 17, eligible_picks: 18, portfolio_median_delta: -1.5, portfolio_p25_delta: -3.2, portfolio_p75_delta: 0.7, mean_percentile: 37.0 },
  ],
  biggest_values: [
    {
      source: "espn", source_label: "vs. draft-time ADP", league_id: 1,
      league_name: "Washington Pro H2H Points PPR League", team_id: 1, team_name: "My Team",
      espn_player_id: 101, player_name: "Bijan Robinson", position: "RB", nfl_team: "ATL",
      overall: 2, round: 1, adp: 7.0, delta: 5.0, draft_type: "SNAKE",
    },
  ],
  biggest_reaches: [
    {
      source: "ffc", source_label: "vs. current market ADP", league_id: 1,
      league_name: "New York Pro H2H Points PPR League", team_id: 1, team_name: "My Team",
      espn_player_id: 102, player_name: "Puka Nacua", position: "WR", nfl_team: "LAR",
      overall: 4, round: 1, adp: 2.0, delta: -2.0, draft_type: "SNAKE",
    },
  ],
  unmatched_players: [
    { espn_player_id: -16027, player_name: "Buccaneers D/ST", position: "D/ST", nfl_team: "27", reason: "not_in_snapshot" },
    { espn_player_id: 4259619, player_name: "Blake Grupe", position: "K", nfl_team: "11", reason: "not_in_snapshot" },
  ],
};

const STRATEGIES_ANALYTICS = {
  season: 2026,
  teams_in_scope: 5,
  coverage: {
    teams_in_scope: 5, qualifying_teams: 4, auction_teams: 1,
    keeper_picks: 1, missing_strategy_teams: 1, notes: [],
  },
  primary_distribution: [
    { label: "Zero RB", count: 0, pct: 0.0 },
    { label: "Hero RB", count: 1, pct: 25.0 },
    { label: "Robust RB", count: 2, pct: 50.0 },
    { label: "Balanced/BPA", count: 1, pct: 25.0 },
    { label: "Autodraft/Absent", count: 0, pct: 0.0 },
  ],
  secondary_distribution: [
    { label: "Anchor WR", count: 1, pct: 25.0 },
    { label: "Elite TE", count: 0, pct: 0.0 },
    { label: "Late-Round QB", count: 2, pct: 50.0 },
  ],
  mean_edge_index_by_primary: [
    { label: "Zero RB", mean_edge_index_score: null, mean_edge_index_score_unrounded: null, stddev_edge_index_score: null, ci95_low: null, ci95_high: null, teams_with_edge_index: 0 },
    { label: "Hero RB", mean_edge_index_score: 66.0, mean_edge_index_score_unrounded: 66.0, stddev_edge_index_score: 0.0, ci95_low: null, ci95_high: null, teams_with_edge_index: 1 },
    { label: "Robust RB", mean_edge_index_score: 61.5, mean_edge_index_score_unrounded: 61.5, stddev_edge_index_score: 4.95, ci95_low: 54.64, ci95_high: 68.36, teams_with_edge_index: 2 },
    { label: "Balanced/BPA", mean_edge_index_score: 64.0, mean_edge_index_score_unrounded: 64.0, stddev_edge_index_score: 0.0, ci95_low: null, ci95_high: null, teams_with_edge_index: 1 },
    { label: "Autodraft/Absent", mean_edge_index_score: null, mean_edge_index_score_unrounded: null, stddev_edge_index_score: null, ci95_low: null, ci95_high: null, teams_with_edge_index: 0 },
  ],
  comparison_note: "Strategy/Edge Index comparisons are descriptive, not causal; strategy is confounded with draft slot, league, and opponent quality.",
  uncertainty_note: "Differences are not distinguishable at this sample: at least two 95% confidence intervals overlap.",
  teams: [
    {
      league_id: 1, league_name: "Alpha League", team_id: 1, team_name: "My Team",
      draft_type: "SNAKE", primary_label: "Robust RB", primary_confidence: 0.82,
      secondary_label: "Late-Round QB", secondary_confidence: 0.76, edge_index_score: 68.0,
      triggering_picks: {
        "Robust RB": [{ overall: 2, player_name: "Bijan Robinson", position: "RB", nfl_team: "ATL" }],
        "Late-Round QB": [{ overall: 90, player_name: "Late QB", position: "QB", nfl_team: "BUF" }],
      },
    },
  ],
};

function json(route: Route, data: unknown) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
}

async function mockApi(page: Page) {
  // Fallback first (lowest priority) so no request ever escapes to a real backend.
  await page.route("**/api/**", (r) => json(r, {}));
  // Player portraits are same-origin and backend-proxied in production. Fulfill the local
  // route here so the browser suite remains fully offline and deterministic.
  await page.route("**/api/players/*/portrait", (r) =>
    r.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#d9dde5"/></svg>',
    }));
  await page.route("**/mock-team-logo/*", (r) =>
    r.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56"><path d="M4 4h48v48H4z" fill="#49d98a"/></svg>',
    }));
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: false, standard_model: "claude-sonnet-5", bulk_model: "claude-haiku-4-5" }));
  await page.route("**/api/portfolio", (r) => json(r, PORTFOLIO));
  await page.route("**/api/portfolio/summary", (r) => json(r, SUMMARY));
  await page.route("**/api/portfolio/exposure**", (r) => json(r, EXPOSURE));
  await page.route("**/api/portfolio/draft-adp**", (r) => json(r, DRAFT_ADP_ANALYTICS));
  await page.route("**/api/portfolio/strategies**", (r) => json(r, STRATEGIES_ANALYTICS));
  await page.route("**/api/leagues", (r) => json(r, [LEAGUE_1]));
  await page.route("**/api/accounts", (r) => json(r, []));
  await page.route("**/api/leagues/1/overview", (r) => json(r, OVERVIEW));
  await page.route("**/api/leagues/1/teams", (r) => json(r, OVERVIEW.teams));
  await page.route("**/api/leagues/1/teams/*", (r) => {
    const teamId = Number(new URL(r.request().url()).pathname.split("/").at(-1));
    return json(r, teamDetail(teamId));
  });
  await page.route("**/api/leagues/1/ai/league-brief", (r) => json(r, AI_DISABLED("league_brief")));
  await page.route("**/api/leagues/1/ai/advantage-verdict", (r) =>
    json(r, AI_DISABLED("advantage_verdict")));
  await page.route("**/api/leagues/1/ai/draft-recaps", (r) =>
    json(r, { enabled: false, kind: "draft_recap", reports: [] }));
  await page.route("**/api/leagues/1/draft", (r) => json(r, DRAFT));
  await page.route("**/api/leagues/1/activity", (r) => json(r, ACTIVITY));
  await page.route("**/api/leagues/1/matchups", (r) => json(r, MATCHUPS));
  await page.route("**/api/leagues/1/all-play", (r) => json(r, ALL_PLAY));
  await page.route("**/api/leagues/1/lineup-efficiency", (r) => json(r, LINEUP_EFFICIENCY));
  await page.route("**/api/leagues/1/my-edge", (r) => json(r, MY_EDGE));
  await page.route("**/api/leagues/1/league-softness", (r) => json(r, LEAGUE_SOFTNESS));
  await page.route("**/api/leagues/1/edge-index", (r) => json(r, EDGE_INDEX));
  // CSV export responds like the backend (attachment) so clicking it triggers a download.
  await page.route("**/api/exports/portfolio.csv", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/csv",
      headers: { "content-disposition": 'attachment; filename="portfolio.csv"' },
      body: "league_id,league_name\n1,Alpha League\n",
    }));
  await page.route("**/api/exports/exposure.csv**", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/csv",
      headers: { "content-disposition": 'attachment; filename="exposure-me.csv"' },
      body: "player_name,exposure_pct\nBijan Robinson,50.0\n",
    }));
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("portfolio board loads with rows, summary, and export controls", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: /ESPN\s*Edge/ })).toBeVisible();
  await expect(page.getByText("Alpha League")).toBeVisible();
  await expect(page.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(page.getByTitle("My Team").first()).not.toContainText("MT");
  const teamBox = await page.getByTitle("My Team").boundingBox();
  const leagueBox = await page.getByText("Alpha League", { exact: true }).boundingBox();
  expect(teamBox).not.toBeNull();
  expect(leagueBox).not.toBeNull();
  expect(teamBox!.y).toBeLessThan(leagueBox!.y);
  await expect(page.getByText("Leagues tracked")).toBeVisible();
  const portfolioSync = page.getByRole("button", { name: "Sync all" });
  await expect(portfolioSync).toHaveAttribute("data-variant", "sync");
  await expect(portfolioSync).toHaveCSS("background-image", /linear-gradient/);
  for (const label of ["CSV", "JSON", "XLSX"]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }
});

test("header theme toggle switches modes, stays top-right, and persists", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const root = page.locator("html");
  const lightToggle = page.getByRole("button", { name: "Switch to light mode" });
  await expect(root).toHaveAttribute("data-theme", "dark");
  await expect(lightToggle).toHaveAttribute("aria-pressed", "true");
  await expect.poll(
    () => root.evaluate((element) => getComputedStyle(element).getPropertyValue("--color-page").trim()),
  ).toBe("#070b14");

  const toggleBox = await lightToggle.boundingBox();
  expect(toggleBox).not.toBeNull();
  expect(toggleBox!.x + toggleBox!.width).toBeGreaterThan(370);

  await lightToggle.click();
  await expect(root).toHaveAttribute("data-theme", "light");
  const darkToggle = page.getByRole("button", { name: "Switch to dark mode" });
  await expect(darkToggle).toHaveAttribute("aria-pressed", "false");
  await expect.poll(
    () => root.evaluate((element) => getComputedStyle(element).getPropertyValue("--color-page").trim()),
  ).toBe("#edf2f4");

  await page.reload();
  await expect(root).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible();
  await expect.poll(
    () => root.evaluate((element) => getComputedStyle(element).getPropertyValue("--color-page").trim()),
  ).toBe("#edf2f4");
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("analytics renders exposure, distinct ADP sources, full strategy enum, and coverage", async ({ page }) => {
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  await expect(page.getByText("Bijan Robinson").first()).toBeVisible();
  await expect(page.getByRole("img", { name: "Bijan Robinson ESPN portrait" }).first()).toBeVisible();
  await expect(page.getByRole("columnheader", { name: /Leverage/ })).toBeVisible();
  await expect(page.getByText("+25.0 pp").first()).toBeVisible();
  await expect(page.getByText("rose 4.0 picks since draft")).toBeVisible();
  const exposureTable = page.getByRole("table", { name: "Portfolio player exposure" });
  await expect(page.getByRole("button", { name: /Rostered\s*3/ })).toBeVisible();
  await expect(exposureTable.getByText("Field Favorite")).toHaveCount(0);
  await page.getByRole("button", { name: /Field owns\s*1/ }).click();
  await expect(exposureTable.getByText("Field Favorite")).toBeVisible();
  await page.getByRole("button", { name: /Rostered\s*3/ }).click();
  const firstTablePlayer = async () =>
    (await exposureTable.locator("tbody tr").first().locator("td").first().innerText())
      .split("\n")[0]
      .trim();
  const leverageHeader = page.getByRole("columnheader", { name: /Leverage/ });
  if ((await leverageHeader.getAttribute("aria-sort")) !== "descending") {
    await leverageHeader.getByRole("button").click();
  }
  const leverageFirst = await firstTablePlayer();
  const exposureHeader = page.getByRole("columnheader", { name: /^Exposure/ });
  await exposureHeader.getByRole("button").click();
  if ((await exposureHeader.getAttribute("aria-sort")) !== "descending") {
    await exposureHeader.getByRole("button").click();
  }
  const exposureFirst = await firstTablePlayer();
  expect(leverageFirst).not.toBe(exposureFirst);
  await expect(page.getByText("0.75x · 3 / 4 players/team")).toBeVisible();
  await expect(page.getByTestId("round-fingerprint-chart").locator("svg").first()).toBeVisible();
  await expect(page.getByTestId("round-fingerprint-chart")).toContainText("Draft round");
  await expect(page.getByTestId("round-fingerprint-chart")).toContainText("Picks / team");
  await expect(page.getByRole("heading", { name: "vs. draft-time ADP" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "vs. current market ADP" })).toBeVisible();
  await expect(page.getByText("Jul 9, 2026")).toBeVisible();
  await expect(page.getByTestId("strategy-distribution-chart").locator("svg")).toBeVisible();
  await expect(page.getByText("Zero RB", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("0.0% · 0 / 4").first()).toBeVisible();
  await expect(page.getByText("descriptive, not causal", { exact: false })).toBeVisible();
  await expect(page.getByText("not listed in FFC snapshot")).toBeVisible();
  await expect(page.getByRole("link", { name: "Fantasy Football Calculator" })).toBeVisible();

  const leverageInfo = page.getByRole("button", { name: "About Highest leverage" });
  await leverageInfo.hover();
  await expect(page.getByRole("tooltip")).toContainText("biggest difference from the field");
  await leverageInfo.click();
  await page.mouse.move(0, 0);
  await expect(page.getByRole("tooltip")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
});

test("analytics controls and tables remain usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/analytics");
  await expect(page.getByRole("link", { name: "Analytics" })).toBeVisible();
  await page.getByRole("button", { name: "Opponents" }).click();
  await expect(page.getByRole("heading", { name: "Opponent roster census" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("portfolio board shows Edge Index as the primary score (Phase 17)", async ({ page }) => {
  await page.addInitScript(() => {
    const callbacks = new Map<number, FrameRequestCallback>();
    let nextId = 0;
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      nextId += 1;
      callbacks.set(nextId, callback);
      return nextId;
    };
    window.cancelAnimationFrame = (id: number) => callbacks.delete(id);
    (window as typeof window & { __flushScoreRingFrame: () => void }).__flushScoreRingFrame = () => {
      const frame = [...callbacks.values()];
      callbacks.clear();
      frame.forEach((callback) => callback(performance.now()));
    };
  });
  await page.goto("/");
  // Alpha League's ring shows the Edge Index composite (68), with the legacy score noted.
  const ring = page.getByTestId("score-ring-1");
  await expect(ring).toHaveAttribute("data-state", "scored");
  await expect(ring).toHaveAttribute("data-motion", "animated");
  await expect(ring).toHaveAttribute("data-value", String(PORTFOLIO[0].edge_index_score));
  const progress = page.getByTestId("score-ring-1-progress");
  await expect(progress).toHaveCSS("transition-duration", "0.65s");
  const circumference = Number(await progress.getAttribute("stroke-dasharray"));
  const emptyOffset = Number(await progress.getAttribute("stroke-dashoffset"));
  expect(emptyOffset).toBeCloseTo(circumference, 3);

  await page.evaluate(() =>
    (window as typeof window & { __flushScoreRingFrame: () => void }).__flushScoreRingFrame());
  await page.evaluate(() =>
    (window as typeof window & { __flushScoreRingFrame: () => void }).__flushScoreRingFrame());
  await expect.poll(async () =>
    Number(await progress.getAttribute("stroke-dashoffset"))).toBeCloseTo(
      circumference * (1 - Number(PORTFOLIO[0].edge_index_score) / 100),
      3,
    );
  expect(Number(await progress.getAttribute("stroke-dashoffset"))).toBeLessThan(emptyOffset);
  await expect(ring.getByText("68", { exact: true })).toBeVisible();
  await expect(page.getByText("Legacy Edge Score 72", { exact: true })).toBeVisible();
  await expect(page.getByTestId("achievements-1")).toHaveAttribute("aria-label", "Achievements");
  await expect(page.getByTestId("achievements-1").getByLabel("Sync healthy")).toBeVisible();
  // Right rail is now driven by Edge Index, with legacy kept clearly labeled.
  await expect(page.getByText("Best / worst Edge Index")).toBeVisible();
  await expect(page.getByText("Legacy Edge Score", { exact: true })).toBeVisible();
});

test("score rings distinguish a real zero, pending, and first sync", async ({ page }) => {
  await page.route("**/api/portfolio", (r) =>
    json(r, [
      {
        ...PORTFOLIO[0],
        edge_index_score: 0,
        edge_index_grade: "F",
        edge_index_momentum: {
          status: "first_sync", delta: null, streak_direction: null, streak_count: 0, history_count: 1,
        },
        achievements: [],
      },
      PORTFOLIO[1],
    ]));

  await page.goto("/");
  const zero = page.getByTestId("score-ring-1");
  const pending = page.getByTestId("score-ring-2");
  await expect(zero).toHaveAttribute("data-state", "scored");
  await expect(zero.getByText("0", { exact: true })).toBeVisible();
  await expect(page.getByTestId("momentum-1")).toContainText("first sync");
  await expect(page.getByTestId("momentum-1")).not.toContainText("up");
  await expect(pending).toHaveAttribute("data-state", "pending");
  await expect(pending.getByText("Pending", { exact: true })).toBeVisible();
  await expect(page.getByTestId("momentum-2")).toContainText("pending");
  await expect(page.getByTestId("achievements-1")).toHaveCount(0);
  await expect(page.getByTestId("achievements-2")).toHaveCount(0);
  await expect(page.getByText("Team not detected", { exact: true })).toBeVisible();
});

test("momentum chips render up, down, flat, and meaningful streak states", async ({ page }) => {
  const momentumRows = [
    {
      ...PORTFOLIO[0], league_id: 11, league_name: "Up League",
      edge_index_momentum: {
        status: "up", delta: 4.5, streak_direction: "up", streak_count: 2, history_count: 3,
      },
    },
    {
      ...PORTFOLIO[0], league_id: 12, league_name: "Down League",
      edge_index_momentum: {
        status: "down", delta: -2.0, streak_direction: "down", streak_count: 1, history_count: 2,
      },
    },
    {
      ...PORTFOLIO[0], league_id: 13, league_name: "Flat League",
      edge_index_momentum: {
        status: "flat", delta: 0, streak_direction: null, streak_count: 0, history_count: 2,
      },
    },
  ];
  await page.route("**/api/portfolio", (r) => json(r, momentumRows));

  await page.goto("/");
  await expect(page.getByTestId("momentum-11")).toContainText("▲ +4.5");
  await expect(page.getByTestId("momentum-11")).toContainText("2 up");
  await expect(page.getByTestId("momentum-12")).toContainText("▼ -2.0");
  await expect(page.getByTestId("momentum-12")).not.toContainText("1 down");
  await expect(page.getByTestId("momentum-13")).toContainText("no change");
});

test("grade tiering handles one league, stable ties, and zero leagues", async ({ page }) => {
  let rows = [{ ...PORTFOLIO[0], league_name: "Only League" }];
  await page.route("**/api/portfolio", (r) => json(r, rows));

  await page.goto("/");
  await expect(page.getByTestId("grade-tier-b")).toContainText("B tier");
  await expect(page.locator('[data-testid^="grade-tier-"]')).toHaveCount(1);

  rows = [
    { ...PORTFOLIO[0], league_id: 21, league_name: "Zulu League" },
    { ...PORTFOLIO[0], league_id: 22, league_name: "Alpha League" },
  ];
  await page.reload();
  await expect(page.locator('a[href^="/league/"]')).toHaveCount(2);
  await expect(page.locator('a[href^="/league/"]').nth(0)).toContainText("Alpha League");
  await expect(page.locator('a[href^="/league/"]').nth(1)).toContainText("Zulu League");

  rows = [];
  await page.reload();
  await expect(page.getByText("No leagues yet")).toBeVisible();
  await expect(page.locator('[data-testid^="grade-tier-"]')).toHaveCount(0);
});

test("reduced motion skips ring and AI content animations", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: true, standard_model: "offline", bulk_model: "offline" }));
  await page.route("**/api/leagues/1/ai/league-brief", (r) =>
    json(r, {
      enabled: true, kind: "league_brief", model: "offline", created_at: NOW, stale: false,
      content: { difficulty_tier: "Tough", narrative: "Offline brief.", exploit_plan: ["Stay active"] },
      error: null,
    }));

  await page.goto("/");
  const ring = page.getByTestId("score-ring-1");
  await expect(ring).toHaveAttribute("data-motion", "reduced");
  await expect(page.getByTestId("score-ring-1-progress")).toHaveCSS("transition-duration", "0s");

  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  const reveal = page.getByTestId("ai-content-reveal");
  await expect(reveal).toHaveAttribute("data-motion", "reduced");
  await expect(reveal).toHaveCSS("animation-name", "none");
});

test("CSV export button points at the backend export endpoint", async ({ page }) => {
  await page.goto("/");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "CSV" }).click(),
  ]);
  expect(download.url()).toContain("/api/exports/portfolio.csv");
  expect(download.suggestedFilename()).toBe("portfolio.csv");
});

test("manage tab renders the account and league forms", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Top navigation").getByRole("link", { name: "Manage" }).click();
  await expect(page.getByRole("heading", { name: "Add ESPN account" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Add league" })).toBeVisible();
  await expect(page.getByTestId("league-achievements-1").getByLabel("Sync healthy")).toBeVisible();
  await expect(page.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  const teamBox = await page.getByTitle("My Team").boundingBox();
  const leagueBox = await page.getByText("Alpha League", { exact: true }).boundingBox();
  expect(teamBox).not.toBeNull();
  expect(leagueBox).not.toBeNull();
  expect(teamBox!.y).toBeLessThan(leagueBox!.y);
});

test("manage individual sync preserves completed-with-issues feedback after reload", async ({ page }) => {
  await page.route("**/api/accounts", (route) =>
    json(route, [{ id: 1, label: "Main", status: "active", created_at: NOW }]));
  await page.route("**/api/leagues", (route) => json(route, [LEAGUE_1]));
  await page.route("**/api/leagues/1/sync", (route) =>
    json(route, {
      league_id: "111",
      season: 2026,
      name: "Alpha League",
      lifecycle: "in_season",
      teams: 8,
      draft_picks: 120,
      matchups: 8,
      transactions: 4,
      metric_snapshots: 1,
      my_team_espn_id: 1,
      needs_reauth: false,
      errors: ["boxscore fixture unavailable"],
    }));

  await page.goto("/manage");
  const alphaRow = page.getByRole("link", { name: /Alpha League/ }).locator("..");
  const rowSync = alphaRow.getByRole("button", { name: "Sync" });
  await expect(rowSync).toHaveAttribute("data-variant", "sync");
  await rowSync.click();

  await expect(
    page.getByText("Alpha League: Completed with issues: boxscore fixture unavailable"),
  ).toBeVisible();
});

test("manage discovers, selects, imports, and syncs account leagues", async ({ page }) => {
  const account = {
    id: 1, label: "Main", status: "active", created_at: NOW,
  };
  const existing = { ...LEAGUE_1, espn_league_id: "111", name: "Alpha League" };
  const imported = {
    ...LEAGUE_1,
    id: 2,
    espn_league_id: "333",
    name: "Gamma League",
    lifecycle: "pre_draft",
    my_team_id: null,
    last_synced_at: null,
    last_sync_ok: null,
  };
  let addPayload: unknown = null;
  let discoveryCalls = 0;
  let syncCalls = 0;

  await page.route("**/api/accounts", (r) => json(r, [account]));
  await page.route("**/api/leagues/discover/1", (r) => {
    discoveryCalls += 1;
    return json(r, [
      { espn_league_id: "111", name: "Alpha League", season: 2026, team_id: 1 },
      { espn_league_id: "333", name: "Gamma League", season: 2026, team_id: 3 },
      { espn_league_id: "333", name: "Gamma League", season: 2026, team_id: 3 },
    ]);
  });
  await page.route("**/api/leagues", (r) => {
    if (r.request().method() === "POST") {
      addPayload = r.request().postDataJSON();
      return json(r, imported);
    }
    return json(r, [existing]);
  });
  await page.route("**/api/leagues/2/sync", (r) => {
    syncCalls += 1;
    return json(r, {
      league_id: "333", season: 2026, name: "Gamma League", lifecycle: "pre_draft",
      teams: 10, draft_picks: 0, matchups: 0, transactions: 0,
      my_team_espn_id: 3, needs_reauth: false, errors: [],
    });
  });

  await page.goto("/manage");
  await page.getByRole("button", { name: "Discover leagues" }).click();
  await expect.poll(() => discoveryCalls).toBe(1);
  const discovery = page.getByTestId("league-discovery-1");
  await expect(discovery.getByText("Alpha League")).toBeVisible();
  await expect(discovery.getByText("Gamma League")).toBeVisible();
  await expect(discovery.getByText("Added", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Select Alpha League" })).toBeDisabled();
  const gamma = page.getByRole("checkbox", { name: "Select Gamma League" });
  await expect(gamma).toBeChecked();
  await gamma.uncheck();
  await expect(page.getByRole("button", { name: "Import & sync 0" })).toBeDisabled();
  await gamma.check();
  await discovery.getByRole("button", { name: "Refresh" }).click();
  await expect.poll(() => discoveryCalls).toBe(2);
  await expect(discovery.getByText("Gamma League")).toHaveCount(1);
  await expect(discovery.getByText("Added", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Select Alpha League" })).toBeDisabled();
  await expect(gamma).toBeChecked();
  await discovery.getByRole("button", { name: "Refresh" }).click();
  await expect.poll(() => discoveryCalls).toBe(3);
  await expect(discovery.getByText("Gamma League")).toHaveCount(1);
  await expect(discovery.getByText("Added", { exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Select Alpha League" })).toBeDisabled();
  await expect(gamma).toBeChecked();
  const importSync = page.getByRole("button", { name: "Import & sync 1" });
  await expect(importSync).toHaveAttribute("data-variant", "sync");
  await importSync.click();

  await expect(page.getByText("Processed 1 league.")).toBeVisible();
  const results = page.getByTestId("league-import-results-1");
  await expect(results.getByText("Success", { exact: true })).toBeVisible();
  await expect(results.getByText("Gamma League", { exact: true })).toBeVisible();
  expect(addPayload).toEqual({ league_ref: "333", account_id: 1, season: 2026 });
  expect(syncCalls).toBe(1);
});

test("manage continues a batch after failure and reports every league result", async ({ page }) => {
  const account = { id: 1, label: "Main", status: "active", created_at: NOW };
  const importedIds: Record<string, number> = { "201": 2, "202": 3, "203": 4 };
  const syncOrder: number[] = [];

  await page.route("**/api/accounts", (r) => json(r, [account]));
  await page.route("**/api/leagues/discover/1", (r) =>
    json(r, [
      { espn_league_id: "201", name: "Success League", season: 2026, team_id: 1 },
      { espn_league_id: "202", name: "Interrupted League", season: 2026, team_id: 2 },
      { espn_league_id: "203", name: "Ambiguous League", season: 2026, team_id: 3 },
    ]));
  await page.route("**/api/leagues", (route) => {
    if (route.request().method() !== "POST") return json(route, []);
    const payload = route.request().postDataJSON() as { league_ref: string };
    return json(route, {
      ...LEAGUE_1,
      id: importedIds[payload.league_ref],
      espn_league_id: payload.league_ref,
      name: null,
      my_team_id: null,
    });
  });
  await page.route("**/api/leagues/*/sync", (route) => {
    const match = new URL(route.request().url()).pathname.match(/\/leagues\/(\d+)\/sync$/);
    const id = Number(match?.[1]);
    syncOrder.push(id);
    if (id === 3) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "fixture interruption" }),
      });
    }
    return json(route, {
      league_id: id === 2 ? "201" : "203",
      season: 2026,
      name: id === 2 ? "Success League" : "Ambiguous League",
      lifecycle: "pre_draft",
      teams: 10,
      draft_picks: 0,
      matchups: 0,
      transactions: 0,
      my_team_espn_id: id === 2 ? 1 : null,
      needs_reauth: false,
      errors: [],
    });
  });

  await page.goto("/manage");
  await page.getByRole("button", { name: "Discover leagues" }).click();
  await page.getByRole("button", { name: "Import & sync 3" }).click();

  const results = page.getByTestId("league-import-results-1");
  await expect(results.getByText("Success", { exact: true })).toHaveCount(1);
  await expect(results.getByText("Failed", { exact: true })).toHaveCount(1);
  await expect(results.getByText("Team not identified", { exact: true })).toHaveCount(1);
  await expect(results.getByText("Success League", { exact: true })).toBeVisible();
  await expect(results.getByText("Interrupted League", { exact: true })).toBeVisible();
  await expect(results.getByText("Ambiguous League", { exact: true })).toBeVisible();
  expect(syncOrder).toEqual([2, 3, 4]);
});

test("manage bulk sync runs a stable sequential queue with one final reload", async ({ page }) => {
  const account = { id: 1, label: "Main", status: "active", created_at: NOW };
  const leagues = [
    LEAGUE_1,
    { ...LEAGUE_1, id: 2, espn_league_id: "222", name: "Beta League" },
    { ...LEAGUE_1, id: 3, espn_league_id: "333", name: "Gamma League" },
  ];
  const syncOrder: number[] = [];
  const releases = new Map<number, () => void>();
  let inFlight = 0;
  let maxInFlight = 0;
  let accountReads = 0;
  let leagueReads = 0;

  await page.route("**/api/accounts", (route) => {
    accountReads += 1;
    return json(route, [account]);
  });
  await page.route("**/api/leagues", (route) => {
    leagueReads += 1;
    return json(route, leagues);
  });
  await page.route("**/api/leagues/*/sync", async (route) => {
    const id = Number(new URL(route.request().url()).pathname.match(/\/leagues\/(\d+)\/sync$/)?.[1]);
    syncOrder.push(id);
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    try {
      await new Promise<void>((resolve) => releases.set(id, resolve));
      await json(route, {
        league_id: String(id),
        season: 2026,
        name: leagues.find((league) => league.id === id)?.name,
        lifecycle: "in_season",
        teams: 10,
        draft_picks: 150,
        matchups: 10,
        transactions: 5,
        metric_snapshots: 1,
        my_team_espn_id: 1,
        needs_reauth: false,
        errors: [],
      });
    } finally {
      inFlight -= 1;
    }
  });

  await page.goto("/manage");
  const bulk = page.getByRole("button", { name: "Sync all 3 leagues" });
  await expect(bulk).toHaveAttribute("data-variant", "sync");
  await expect(bulk).toBeVisible();
  const initialAccountReads = accountReads;
  const initialLeagueReads = leagueReads;

  await bulk.click();
  await expect.poll(() => [...syncOrder]).toEqual([1]);
  await expect(page.getByRole("button", { name: "Syncing 1 of 3..." })).toBeVisible();
  releases.get(1)?.();

  await expect.poll(() => [...syncOrder]).toEqual([1, 2]);
  await expect(page.getByTestId("bulk-sync-result-1")).toContainText("Success");
  await expect(page.getByRole("button", { name: "Syncing 2 of 3..." })).toBeVisible();
  releases.get(2)?.();

  await expect.poll(() => [...syncOrder]).toEqual([1, 2, 3]);
  await expect(page.getByTestId("bulk-sync-result-2")).toContainText("Success");
  await expect(page.getByRole("button", { name: "Syncing 3 of 3..." })).toBeVisible();
  releases.get(3)?.();

  await expect(
    page.getByText("Processed 3 leagues: 3 synced.", { exact: true }),
  ).toBeVisible();
  expect(syncOrder).toEqual([1, 2, 3]);
  expect(maxInFlight).toBe(1);
  expect(accountReads).toBe(initialAccountReads + 1);
  expect(leagueReads).toBe(initialLeagueReads + 1);
});

test("manage bulk sync records mixed results and preserves them when reload fails", async ({ page }) => {
  const account = { id: 1, label: "Main", status: "active", created_at: NOW };
  const leagues = [
    LEAGUE_1,
    { ...LEAGUE_1, id: 2, espn_league_id: "222", name: "Issues League" },
    { ...LEAGUE_1, id: 3, espn_league_id: "333", name: "Failed League" },
    { ...LEAGUE_1, id: 4, espn_league_id: "444", name: "Missing Team League" },
  ];
  const syncOrder: number[] = [];
  let leagueReads = 0;

  await page.route("**/api/accounts", (route) => json(route, [account]));
  await page.route("**/api/leagues", (route) => {
    leagueReads += 1;
    if (leagueReads > 1) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "reload unavailable" }),
      });
    }
    return json(route, leagues);
  });
  await page.route("**/api/leagues/*/sync", (route) => {
    const id = Number(new URL(route.request().url()).pathname.match(/\/leagues\/(\d+)\/sync$/)?.[1]);
    syncOrder.push(id);
    if (id === 3) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "fixture interruption" }),
      });
    }
    return json(route, {
      league_id: String(id),
      season: 2026,
      name: leagues.find((league) => league.id === id)?.name,
      lifecycle: "in_season",
      teams: 10,
      draft_picks: 150,
      matchups: 10,
      transactions: 5,
      metric_snapshots: 1,
      my_team_espn_id: id === 4 ? null : 1,
      needs_reauth: false,
      errors: id === 2 ? ["boxscore fixture unavailable"] : [],
    });
  });

  await page.goto("/manage");
  await page.getByRole("button", { name: "Sync all 4 leagues" }).click();

  const results = page.getByTestId("bulk-sync-results");
  await expect(results.getByText("Success", { exact: true })).toHaveCount(1);
  await expect(results.getByText("Completed with issues", { exact: true })).toHaveCount(1);
  await expect(results.getByText("Failed", { exact: true })).toHaveCount(1);
  await expect(results.getByText("Team not identified", { exact: true })).toHaveCount(1);
  await expect(
    page.getByText(
      "Processed 4 leagues: 1 synced, 1 with issues, 1 team not identified, 1 failed.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(page.getByText(/Manage data could not be refreshed: reload unavailable/)).toBeVisible();
  expect(syncOrder).toEqual([1, 2, 3, 4]);
});

test("manage bulk sync skips accounts needing re-auth and continues other leagues", async ({ page }) => {
  const accounts = [
    { id: 1, label: "Main", status: "active", created_at: NOW },
    { id: 2, label: "Expired", status: "needs_reauth", created_at: NOW },
    { id: 3, label: "Other", status: "active", created_at: NOW },
  ];
  const leagues = [
    LEAGUE_1,
    { ...LEAGUE_1, id: 2, espn_league_id: "222", name: "Same Account League" },
    { ...LEAGUE_1, id: 3, espn_league_id: "333", name: "Already Expired", account_id: 2 },
    {
      ...LEAGUE_1,
      id: 4,
      espn_league_id: "444",
      name: "Public League",
      account_id: null,
      is_public: true,
      my_team_id: null,
      my_team_name: null,
      my_team_logo_url: null,
    },
    { ...LEAGUE_1, id: 5, espn_league_id: "555", name: "Other Account League", account_id: 3 },
  ];
  const syncOrder: number[] = [];

  await page.route("**/api/accounts", (route) => json(route, accounts));
  await page.route("**/api/leagues", (route) => json(route, leagues));
  await page.route("**/api/leagues/*/sync", (route) => {
    const id = Number(new URL(route.request().url()).pathname.match(/\/leagues\/(\d+)\/sync$/)?.[1]);
    syncOrder.push(id);
    return json(route, {
      league_id: String(id),
      season: 2026,
      name: leagues.find((league) => league.id === id)?.name,
      lifecycle: "in_season",
      teams: 10,
      draft_picks: 150,
      matchups: 10,
      transactions: 5,
      metric_snapshots: 1,
      my_team_espn_id: id === 4 ? null : 1,
      needs_reauth: id === 1,
      errors: [],
    });
  });

  await page.goto("/manage");
  await page.getByRole("button", { name: "Sync all 5 leagues" }).click();

  await expect(
    page.getByText("Processed 5 leagues: 2 synced, 1 need re-auth, 2 skipped.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("bulk-sync-result-1")).toContainText("Needs re-auth");
  await expect(page.getByTestId("bulk-sync-result-2")).toContainText("Skipped");
  await expect(page.getByTestId("bulk-sync-result-3")).toContainText("Skipped");
  await expect(page.getByTestId("bulk-sync-result-4")).toContainText("Success");
  await expect(page.getByTestId("bulk-sync-result-5")).toContainText("Success");
  expect(syncOrder).toEqual([1, 4, 5]);
});

test("manage prevents overlapping bulk, individual, and import sync operations", async ({ page }) => {
  const account = { id: 1, label: "Main", status: "active", created_at: NOW };
  const leagues = [
    LEAGUE_1,
    { ...LEAGUE_1, id: 2, espn_league_id: "222", name: "Beta League" },
  ];
  const syncOrder: number[] = [];
  let phase: "bulk" | "individual" | "import" = "bulk";
  let releaseBulk: (() => void) | null = null;
  let releaseIndividual: (() => void) | null = null;
  let releaseImport: (() => void) | null = null;

  await page.route("**/api/accounts", (route) => json(route, [account]));
  await page.route("**/api/leagues/discover/1", (route) =>
    json(route, [{ espn_league_id: "333", name: "Gamma League", season: 2026, team_id: 3 }]));
  await page.route("**/api/leagues", (route) => {
    if (route.request().method() === "POST") {
      return json(route, { ...LEAGUE_1, id: 3, espn_league_id: "333", name: "Gamma League" });
    }
    return json(route, leagues);
  });
  await page.route("**/api/leagues/*/sync", async (route) => {
    const id = Number(new URL(route.request().url()).pathname.match(/\/leagues\/(\d+)\/sync$/)?.[1]);
    syncOrder.push(id);
    if (phase === "bulk" && id === 1) {
      await new Promise<void>((resolve) => {
        releaseBulk = resolve;
      });
    } else if (phase === "individual") {
      await new Promise<void>((resolve) => {
        releaseIndividual = resolve;
      });
    } else if (phase === "import") {
      await new Promise<void>((resolve) => {
        releaseImport = resolve;
      });
    }
    return json(route, {
      league_id: String(id),
      season: 2026,
      name: id === 1 ? "Alpha League" : id === 2 ? "Beta League" : "Gamma League",
      lifecycle: "in_season",
      teams: 10,
      draft_picks: 150,
      matchups: 10,
      transactions: 5,
      metric_snapshots: 1,
      my_team_espn_id: 1,
      needs_reauth: false,
      errors: [],
    });
  });

  await page.goto("/manage");
  await page.getByRole("button", { name: "Discover leagues" }).click();
  const importButton = page.getByRole("button", { name: "Import & sync 1" });
  const bulkButton = page.getByRole("button", { name: "Sync all 2 leagues" });

  await bulkButton.evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect.poll(() => [...syncOrder]).toEqual([1]);
  await expect(importButton).toBeDisabled();
  for (const button of await page.getByRole("button", { name: /^(Sync|Syncing…)$/ }).all()) {
    await expect(button).toBeDisabled();
  }
  releaseBulk?.();
  await expect(page.getByText("Processed 2 leagues: 2 synced.", { exact: true })).toBeVisible();
  expect(syncOrder).toEqual([1, 2]);

  phase = "individual";
  const alphaRow = page.getByRole("link", { name: /Alpha League/ }).locator("..");
  await alphaRow.getByRole("button", { name: "Sync" }).click();
  await expect.poll(() => [...syncOrder]).toEqual([1, 2, 1]);
  await expect(page.getByRole("button", { name: "Sync all 2 leagues" })).toBeDisabled();
  await expect(importButton).toBeDisabled();
  releaseIndividual?.();
  await expect(page.getByRole("button", { name: "Sync all 2 leagues" })).toBeEnabled();

  phase = "import";
  await importButton.click();
  await expect.poll(() => [...syncOrder]).toEqual([1, 2, 1, 3]);
  await expect(page.getByRole("button", { name: "Sync all 2 leagues" })).toBeDisabled();
  releaseImport?.();
  await expect(page.getByText("Processed 1 league.", { exact: true })).toBeVisible();
  expect(syncOrder).toEqual([1, 2, 1, 3]);
});

test("manage hides bulk sync for an empty list", async ({ page }) => {
  let syncCalls = 0;
  await page.route("**/api/accounts", (route) => json(route, []));
  await page.route("**/api/leagues", (route) => json(route, []));
  await page.route("**/api/leagues/*/sync", (route) => {
    syncCalls += 1;
    return json(route, {});
  });

  await page.goto("/manage");
  await expect(page.getByText("No leagues yet")).toBeVisible();
  await expect(page.getByRole("button", { name: /Sync all/ })).toHaveCount(0);
  expect(syncCalls).toBe(0);
});

test("manage bulk sync is keyboard accessible and usable on mobile", async ({ page }) => {
  const account = { id: 1, label: "Main", status: "active", created_at: NOW };
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/accounts", (route) => json(route, [account]));
  await page.route("**/api/leagues", (route) => json(route, [LEAGUE_1]));
  await page.route("**/api/leagues/1/sync", (route) =>
    json(route, {
      league_id: "111",
      season: 2026,
      name: "Alpha League",
      lifecycle: "in_season",
      teams: 8,
      draft_picks: 120,
      matchups: 8,
      transactions: 4,
      metric_snapshots: 1,
      my_team_espn_id: 1,
      needs_reauth: false,
      errors: [],
    }));

  await page.goto("/manage");
  const bulk = page.getByRole("button", { name: "Sync all 1 league" });
  await bulk.focus();
  await page.keyboard.press("Enter");

  await expect(page.getByText("Processed 1 league: 1 synced.", { exact: true })).toBeVisible();
  await expect(page.getByTestId("bulk-sync-status")).toHaveAttribute("aria-live", "polite");
  await expect(page.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("manage keeps manual entry available when discovery returns no leagues", async ({ page }) => {
  await page.route("**/api/accounts", (r) =>
    json(r, [{ id: 1, label: "Main", status: "active", created_at: NOW }]));
  await page.route("**/api/leagues", (r) => json(r, []));
  await page.route("**/api/leagues/discover/1", (r) => json(r, []));

  await page.goto("/manage");
  await page.getByRole("button", { name: "Discover leagues" }).click();
  await expect(page.getByText(/ESPN returned no discoverable leagues/)).toBeVisible();
  await expect(page.getByPlaceholder("League ID or URL")).toBeVisible();
});

test("manage reports a discovery request failure without losing manual entry", async ({ page }) => {
  let expired = false;
  await page.route("**/api/accounts", (r) => {
    return json(r, [{
      id: 1,
      label: "Main",
      status: expired ? "needs_reauth" : "active",
      created_at: NOW,
    }]);
  });
  await page.route("**/api/leagues", (r) => json(r, []));
  await page.route("**/api/leagues/discover/1", (r) => {
    expired = true;
    return r.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "ESPN session expired; re-authenticate this account" }),
    });
  });

  await page.goto("/manage");
  await page.getByRole("button", { name: "Discover leagues" }).click();
  await expect(
    page.getByText("Error: ESPN session expired; re-authenticate this account"),
  ).toBeVisible();
  await expect(page.getByTestId("reauth-form-1")).toBeVisible();
  await expect(page.getByPlaceholder("League ID or URL")).toBeVisible();
});

test("league detail renders standings from mocked API data", async ({ page }) => {
  await page.goto("/league/1");
  const heading = page.getByRole("heading", { name: /My Team/ });
  const standings = page.getByRole("table", { name: "League standings" });
  await expect(heading).toBeVisible();
  await expect(page.getByText("Alpha League", { exact: true })).toBeVisible();
  await expect(page.getByText("PPR")).toBeVisible();
  await expect(heading.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(standings.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(standings.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sync now" })).toHaveAttribute("data-variant", "sync");
});

test("team links open a deep-linked configured roster and return to the source tab", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto("/league/1?tab=overview");
  const standings = page.getByRole("table", { name: "League standings" });
  const rivalLink = standings.getByRole("link", { name: "Open Rival roster" });
  await expect(rivalLink).toHaveAttribute("href", "/league/1/teams/2");
  await rivalLink.click();

  await expect(page).toHaveURL(/\/league\/1\/teams\/2$/);
  await expect(page.getByRole("heading", { name: /Rival/ })).toBeVisible();
  await expect(page.getByText("Week 1 matchup")).toBeVisible();
  await expect(page.getByText("108.4 proj")).toBeVisible();
  await expect(page.getByText("111.2 proj")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sync now" })).toHaveAttribute("data-variant", "sync");

  const starters = page.getByRole("table", { name: "Starting lineup roster" });
  await expect(starters).toBeVisible();
  await expect(starters.locator("tbody td:first-child")).toHaveText([
    "QB", "RB", "RB", "WR", "TE", "FLEX", "D/ST", "K",
  ]);
  await expect(starters.getByText("Star Quarterback")).toBeVisible();
  await expect(starters.getByText("0.0", { exact: true })).toBeVisible();
  await expect(starters.getByText("18.5", { exact: true })).toBeVisible();
  await expect(starters.getByText("Q", { exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "Bench roster" })).toBeVisible();
  await expect(page.getByRole("table", { name: "IR / Reserve roster" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText(/undefined|NaN/);

  await page.getByRole("link", { name: "Back to league" }).click();
  await expect(page).toHaveURL(/\/league\/1\?tab=overview$/);
  await expect(standings).toBeVisible();
  expect(consoleErrors).toEqual([]);
});

test("team detail supports hard refresh, keyboard entry, Escape, and retry", async ({ page }) => {
  let detailCalls = 0;
  await page.route("**/api/leagues/1/teams/1", (route) => {
    detailCalls += 1;
    if (detailCalls === 1) {
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Roster temporarily unavailable" }),
      });
    }
    return json(route, teamDetail(1));
  });

  await page.goto("/league/1/teams/1");
  await expect(page.getByText("Roster temporarily unavailable")).toBeVisible();
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: /My Team/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("table", { name: "Starting lineup roster" })).toBeVisible();

  await page.goto("/league/1?tab=teams");
  const teamsTable = page.getByRole("table", { name: "League teams" });
  const link = teamsTable.getByRole("link", { name: "Open Rival roster" });
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/league\/1\/teams\/2$/);
  await expect(page.getByRole("heading", { name: /Rival/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/league\/1\?tab=teams$/);
});

test("team detail uses stacked player rows on mobile without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/league/1/teams/1");
  await expect(page.getByRole("table", { name: "Starting lineup roster" })).toBeHidden();
  await expect(page.getByText("Star Quarterback").last()).toBeVisible();
  await expect(page.getByText("Actual 0.0")).toBeVisible();
  await expect(page.getByText("Proj 18.5")).toBeVisible();
  await expect(page.getByText("Bye", { exact: true }).last()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("team detail holds its skeleton shape and labels a retained roster stale", async ({ page }) => {
  await page.route("**/api/leagues/1/teams/1", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    return json(route, {
      ...teamDetail(1),
      roster_status: "stale",
      roster_synced_at: "2026-09-01T12:00:00Z",
    });
  });
  await page.goto("/league/1/teams/1");
  await expect(page.getByLabel("Loading team roster")).toBeVisible();
  await expect(page.getByTestId("roster-stale-note")).toContainText(
    "Roster snapshot may be stale",
  );
  await expect(page.getByRole("table", { name: "Starting lineup roster" })).toBeVisible();
});

test("team-first league identity remains readable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(page.getByText("Alpha League", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.goto("/league/1");
  await expect(page.getByRole("heading", { name: /My Team/ })).toBeVisible();
  await expect(page.getByText("Alpha League", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("league detail Overview renders edge component breakdown bars", async ({ page }) => {
  await page.goto("/league/1");
  const ring = page.getByTestId("overview-score-ring");
  await expect(ring).toHaveAttribute("data-state", "scored");
  await expect(ring).toHaveAttribute("data-value", String(OVERVIEW.edge_score));
  await expect(ring).toHaveAttribute(
    "aria-label",
    `Edge Score ${Math.round(OVERVIEW.edge_score)} grade ${OVERVIEW.grade}`,
  );
  await expect(ring.getByText(String(Math.round(OVERVIEW.edge_score)), { exact: true })).toBeVisible();
  await expect(page.getByTestId("overview-momentum")).toContainText("▲ +3.0");
  await expect(page.getByTestId("overview-achievements").getByLabel("Sync healthy")).toBeVisible();
  // Component labels + bar percentiles from the mocked overview payload (Phase 9).
  await expect(page.getByText("Components (within-league percentile)")).toBeVisible();
  await expect(page.getByText("Win %", { exact: true })).toBeVisible();
  await expect(page.getByText("Points for", { exact: true })).toBeVisible();
  await expect(page.getByText("Point differential", { exact: true })).toBeVisible();
  await expect(page.getByText("weight 40%")).toBeVisible();
  await expect(page.getByText("88 pct")).toBeVisible(); // 87.5 → 88 rounded for display
});

test("overview renders the MyEdge v1 panel for my team (Phase 14)", async ({ page }) => {
  // Keep Edge Index empty so its "MyEdge" component label doesn't collide with the ValueChip.
  await page.route("**/api/leagues/1/edge-index", (r) => json(r, []));
  await page.goto("/league/1");
  await expect(page.getByRole("heading", { name: /MyEdge/ })).toBeVisible();
  // My team's MyEdge components (distinct from the Edge Score component labels).
  await expect(page.getByText("Roster strength", { exact: true })).toBeVisible();
  await expect(page.getByText("Luck-adjusted record", { exact: true })).toBeVisible();
  await expect(page.getByText("MyEdge", { exact: true })).toBeVisible(); // ValueChip label
});

test("overview renders the full Edge Index v1 panel for my team (Phase 16)", async ({ page }) => {
  // Keep MyEdge + LeagueSoftness panels empty so their labels don't collide with the
  // Edge Index composite component labels.
  await page.route("**/api/leagues/1/my-edge", (r) => json(r, []));
  await page.route("**/api/leagues/1/league-softness", (r) => json(r, []));
  await page.goto("/league/1");
  await expect(page.getByRole("heading", { name: /Edge Index/ })).toBeVisible();
  await expect(page.getByText("MyEdge", { exact: true })).toBeVisible(); // composite half label
  await expect(page.getByText("LeagueSoftness", { exact: true })).toBeVisible();
  await expect(page.getByText("Edge Index", { exact: true })).toBeVisible(); // ValueChip label
});

test("overview renders the LeagueSoftness v1 panel for my team (Phase 15)", async ({ page }) => {
  await page.goto("/league/1");
  await expect(page.getByRole("heading", { name: /LeagueSoftness/ })).toBeVisible();
  await expect(page.getByText("Exploitable weakness share", { exact: true })).toBeVisible();
  await expect(page.getByText("Abandoned teams (proxy)", { exact: true })).toBeVisible();
  await expect(page.getByText("Softness", { exact: true })).toBeVisible(); // ValueChip label
});

test("overview renders the preseason Draft surplus component (Phase 11)", async ({ page }) => {
  // Override with a preseason-style overview blending roster projection + draft surplus.
  await page.route("**/api/leagues/1/overview", (r) =>
    json(r, {
      ...OVERVIEW,
      edge_score: 63.0, grade: "C",
      components: [
        { key: "roster_proj", label: "Roster projection", weight: 0.5833, percentile: 83.3 },
        { key: "draft_surplus", label: "Draft surplus", weight: 0.4167, percentile: 37.5 },
      ],
    }));
  // This test targets the Edge breakdown panel; keep the other Overview panels empty.
  await page.route("**/api/leagues/1/my-edge", (r) => json(r, []));
  await page.route("**/api/leagues/1/league-softness", (r) => json(r, []));
  await page.route("**/api/leagues/1/edge-index", (r) => json(r, []));
  await page.goto("/league/1");
  await expect(page.getByText("Roster projection", { exact: true })).toBeVisible();
  await expect(page.getByText("Draft surplus", { exact: true })).toBeVisible();
  await expect(page.getByText("weight 42%")).toBeVisible(); // 0.4167 → 42%
});

test("draft board shows player name, position, and ADP (not raw IDs)", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "Draft Board" }).click();
  const draftBoard = page.getByRole("table", { name: "Draft board" });
  // Resolved player name + position + ADP from the mocked draft payload (Phase 10).
  await expect(page.getByText("Star Runningback")).toBeVisible();
  await expect(page.getByText("Ace Receiver")).toBeVisible();
  await expect(page.getByText("RB", { exact: true })).toBeVisible(); // position pill
  await expect(page.getByText("3.4")).toBeVisible(); // ADP column
  await expect(page.getByRole("img", { name: "Star Runningback ESPN portrait" })).toHaveAttribute(
    "src",
    "/api/players/1001/portrait",
  );
  await expect(page.getByTitle("Star Runningback")).not.toContainText("SR");
  await expect(draftBoard.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(page.getByTitle("My Team").last()).not.toContainText("MT");
  await expect(draftBoard.getByRole("img", { name: "Rival team logo" })).toBeVisible();
});

test("team logo failure keeps the team initials visible", async ({ page }) => {
  await page.route("**/mock-team-logo/my.svg", (route) =>
    route.fulfill({ status: 502, contentType: "application/json", body: "{}" }));

  await page.goto("/league/1");
  const heading = page.getByRole("heading", { name: /My Team/ });
  const avatar = heading.getByTitle("My Team");
  const image = avatar.locator('img[alt="My Team team logo"]');
  await expect(image).toBeAttached();
  await expect(image).toHaveCSS("display", "none");
  await expect(avatar).toContainText("MT");
});

test("draft board shows a compact per-team grade strip", async ({ page }) => {
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: true, standard_model: "offline", bulk_model: "offline" }));
  await page.route("**/api/leagues/1/ai/draft-recaps", (r) =>
    json(r, {
      enabled: true,
      kind: "draft_recap",
      reports: [
        {
          espn_team_id: 1, team_name: "My Team", strategy_label: "Balanced",
          secondary_label: null, grade: "A-", confidence: "high", summary: "Strong value.",
          key_values: [], key_reaches: [],
        },
        {
          espn_team_id: 2, team_name: "Rival", strategy_label: "Zero RB",
          secondary_label: null, grade: "B+", confidence: "medium", summary: "Solid start.",
          key_values: [], key_reaches: [],
        },
      ],
    }));

  await page.goto("/league/1");
  await page.getByRole("button", { name: "Draft Board" }).click();
  const strip = page.getByTestId("draft-grade-strip");
  await expect(strip).toBeVisible();
  await expect(strip).toContainText("My Team");
  await expect(strip).toContainText("A-");
  await expect(strip).toContainText("Rival");
  await expect(strip).toContainText("B+");
  for (const [teamId, grade] of [[1, "A-"], [2, "B+"]] as const) {
    await expect(page.getByTestId(`draft-grade-strip-${teamId}`).getByText(grade, { exact: true })).toBeVisible();
    await expect(page.getByTestId(`draft-recap-card-${teamId}`).getByText(grade, { exact: true })).toBeVisible();
  }
  await expect(page.getByTestId("draft-grade-strip-1").getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(page.getByTestId("draft-grade-strip-2").getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(page.getByTestId("draft-recap-card-1").getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(page.getByTestId("draft-recap-card-2").getByRole("img", { name: "Rival team logo" })).toBeVisible();
});

test("activity renders ESPN portraits with resolved player names", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "Activity" }).click();
  const activity = page.getByRole("table", { name: "League activity" });
  await expect(page.getByText("Star Runningback")).toBeVisible();
  await expect(page.getByText("Ace Receiver")).toBeVisible();
  await expect(page.getByRole("img", { name: "Star Runningback ESPN portrait" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Ace Receiver ESPN portrait" })).toBeVisible();
  await expect(activity.getByRole("img", { name: "My Team team logo" }).first()).toBeVisible();
});

test("portrait failure keeps the player initials visible", async ({ page }) => {
  let portraitRequests = 0;
  await page.route("**/api/players/*/portrait", (route) => {
    portraitRequests += 1;
    return route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ detail: "portrait unavailable" }),
    });
  });

  await page.goto("/league/1");
  await page.getByRole("button", { name: "Draft Board" }).click();

  const avatar = page.getByTitle("Star Runningback");
  const image = avatar.locator('img[alt="Star Runningback ESPN portrait"]');
  await expect(image).toBeAttached();
  await expect(image).toHaveCSS("display", "none");
  await expect(avatar).toBeVisible();
  await expect(avatar).toContainText("SR");
  expect(portraitRequests).toBeGreaterThan(0);
});

test("matchups tab renders all-play + luck table (Phase 12)", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "Matchups" }).click();
  await expect(page.getByText("All-play & luck")).toBeVisible();
  await expect(page.getByText("Luck", { exact: true })).toBeVisible(); // column header
  // Mocked all-play row values: My Team is 3-0 all-play, Rival has a +33.3 luck delta.
  await expect(page.getByText("+33.3")).toBeVisible();
  await expect(page.getByText("Matchup schedule")).toBeVisible();
  const allPlay = page.getByRole("table", { name: "All-play standings" });
  const schedule = page.getByRole("table", { name: "Matchup schedule" });
  await expect(allPlay.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(allPlay.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(schedule.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(schedule.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(schedule.getByRole("link", { name: "Open Rival roster" }).first()).toHaveAttribute(
    "href",
    "/league/1/teams/2",
  );
});

test("teams tab renders lineup efficiency table (Phase 13)", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "Teams" }).click();
  await expect(page.getByText("Lineup efficiency")).toBeVisible();
  await expect(page.getByText("83.3%")).toBeVisible(); // My Team efficiency (0.8333)
  await expect(page.getByText("Left on bench/wk")).toBeVisible(); // column header
  const leagueTeams = page.getByRole("table", { name: "League teams" });
  const efficiency = page.getByRole("table", { name: "Lineup efficiency" });
  await expect(leagueTeams.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(leagueTeams.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(efficiency.getByRole("img", { name: "My Team team logo" })).toBeVisible();
  await expect(efficiency.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(leagueTeams.getByRole("link", { name: "Open Rival roster" })).toHaveAttribute(
    "href",
    "/league/1/teams/2",
  );
});

test("AI-disabled state renders the connect-a-key panel without crashing", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByText("AI analysis is off")).toBeVisible();
});

test("AI brief reveal fires once for each genuine generation", async ({ page }) => {
  let generation = 0;
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: true, standard_model: "offline", bulk_model: "offline" }));
  await page.route("**/api/leagues/1/ai/league-brief**", (route) => {
    if (route.request().method() === "GET") {
      return json(route, AI_DISABLED("league_brief"));
    }
    generation += 1;
    return json(route, {
      enabled: true,
      kind: "league_brief",
      model: "offline",
      created_at: `2026-07-09T12:00:0${generation}Z`,
      stale: false,
      content: {
        difficulty_tier: generation === 1 ? "Tough" : "Balanced",
        narrative: `Offline generation ${generation}`,
        exploit_plan: ["Use persisted facts"],
      },
      error: null,
    });
  });

  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  await page.evaluate(() => {
    const state = window as Window & { __aiRevealCount?: number };
    state.__aiRevealCount = 0;
    document.addEventListener("animationstart", (event) => {
      if ((event as AnimationEvent).animationName === "ai-reveal") {
        state.__aiRevealCount = (state.__aiRevealCount ?? 0) + 1;
      }
    });
  });
  const card = page.getByRole("heading", { name: "League difficulty brief" }).locator("..").locator("..");

  await card.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(card.getByText("Offline generation 1")).toBeVisible();
  await expect.poll(() => page.evaluate(() =>
    (window as Window & { __aiRevealCount?: number }).__aiRevealCount)).toBe(1);
  await page.waitForTimeout(350);
  expect(await page.evaluate(() =>
    (window as Window & { __aiRevealCount?: number }).__aiRevealCount)).toBe(1);

  await card.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(card.getByText("Offline generation 2")).toBeVisible();
  await expect.poll(() => page.evaluate(() =>
    (window as Window & { __aiRevealCount?: number }).__aiRevealCount)).toBe(2);
  expect(generation).toBe(2);
});

test("AI cached content does not reveal again after a tab round-trip", async ({ page }) => {
  const cached = {
    enabled: true,
    kind: "league_brief",
    model: "offline",
    created_at: NOW,
    stale: false,
    content: {
      difficulty_tier: "Tough",
      narrative: "One cached offline brief.",
      exploit_plan: ["Use persisted facts"],
    },
    error: null,
  };
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: true, standard_model: "offline", bulk_model: "offline" }));
  await page.route("**/api/leagues/1/ai/league-brief", async (r) => {
    // Let status and the sibling verdict settle first so a replay cannot be
    // hidden by a later unrelated render removing the animation class.
    await new Promise((resolve) => setTimeout(resolve, 75));
    return json(r, cached);
  });

  await page.goto("/league/1");
  await page.evaluate(() => {
    const state = window as Window & { __aiRevealCount?: number };
    state.__aiRevealCount = 0;
    document.addEventListener("animationstart", (event) => {
      if ((event as AnimationEvent).animationName === "ai-reveal") {
        state.__aiRevealCount = (state.__aiRevealCount ?? 0) + 1;
      }
    });
  });

  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByText("One cached offline brief.")).toBeVisible();
  await expect.poll(() => page.evaluate(() =>
    (window as Window & { __aiRevealCount?: number }).__aiRevealCount)).toBe(1);

  await page.getByRole("button", { name: "Overview" }).click();
  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByText("One cached offline brief.")).toBeVisible();
  await expect(page.getByTestId("ai-content-reveal")).toHaveAttribute("data-reveal", "seen");
  await page.waitForTimeout(350);
  expect(await page.evaluate(() =>
    (window as Window & { __aiRevealCount?: number }).__aiRevealCount)).toBe(1);
});

test("AI Brief weekly recap card renders a recap for the picked week (Phase 20)", async ({ page }) => {
  // Enable AI and mock the weekly-recap GET; matchups (mocked) drive the completed-week picker.
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: true, standard_model: "claude-sonnet-5", bulk_model: "claude-haiku-4-5" }));
  await page.route("**/api/leagues/1/ai/weekly-recap**", (r) =>
    json(r, {
      enabled: true, kind: "weekly_recap", model: "claude-haiku-4-5", created_at: NOW, stale: false,
      content: {
        week: 1, headline: "Alpha rolls in Week 1", body: "Top scorer took the crown.",
        luck_notes: ["Rival lost despite a top-3 all-play week"],
        waiver_highlights: ["Main added Star Runningback ($17)"],
      },
      error: null,
    }));
  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByRole("heading", { name: "Weekly recap" })).toBeVisible();
  await expect(page.getByText("Alpha rolls in Week 1")).toBeVisible();
  await expect(page.getByText("Luck notes")).toBeVisible();
  await expect(page.getByText("Rival lost despite a top-3 all-play week")).toBeVisible();
  await expect(page.getByText("Waiver highlights")).toBeVisible();
  const waiverHighlight = page.getByRole("listitem").filter({ hasText: "Main added" });
  await expect(waiverHighlight.getByText("Star Runningback", { exact: true })).toBeVisible();
  await expect(waiverHighlight).toContainText("($17)");
  await expect(page.getByRole("img", { name: "Star Runningback ESPN portrait" })).toBeVisible();
});

test("AI Brief trade finder renders proposals for the picked opponent (Phase 21)", async ({ page }) => {
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: true, standard_model: "claude-sonnet-5", bulk_model: "claude-haiku-4-5" }));
  await page.route("**/api/leagues/1/teams", (r) =>
    json(r, [
      ...OVERVIEW.teams,
      {
        id: 3, espn_team_id: 3, name: "Challenger", abbrev: "CHL", is_me: false,
        autodrafted: false, wins: 4, losses: 3, ties: 0, points_for: 875.0,
        points_against: 860.0, standing: 3, logo_url: "/mock-team-logo/challenger.svg",
      },
    ]));
  // Opponent options come from the team list; trade-finder GET/POST return the proposal.
  await page.route("**/api/leagues/1/ai/trade-finder**", (r) =>
    json(r, {
      enabled: true, kind: "trade_finder", model: "claude-sonnet-5", created_at: NOW, stale: false,
      content: {
        opponent_team_id: 2, opponent_name: "Rival",
        grounding_source: "lineup_snapshot", snapshot_week: 4, snapshot_stale: false,
        projections_stale: false, my_projection_coverage: 0.9,
        proposals: [
          {
            i_give: ["My RB2"],
            i_get: ["Their WR1"],
            i_give_players: [{ espn_player_id: 2001, name: "My RB2", position: "RB" }],
            i_get_players: [{ espn_player_id: 2002, name: "Their WR1", position: "WR" }],
            rationale: "My RB2 gives you depth; Their WR1 fills the need.",
          },
        ],
        note: "Advisory only — the app never executes trades on ESPN.",
      },
      error: null,
    }));
  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByRole("heading", { name: "Trade finder" })).toBeVisible();
  const picker = page.getByRole("button", { name: "Trade opponent: Rival" });
  await expect(picker.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await picker.focus();
  await picker.press("ArrowDown");
  const rival = page.getByRole("menuitemradio", { name: /Rival/ });
  const challenger = page.getByRole("menuitemradio", { name: /Challenger/ });
  await expect(rival).toBeFocused();
  await rival.press("ArrowDown");
  await expect(challenger).toBeFocused();
  await challenger.press("Escape");
  await expect(picker).toBeFocused();
  await expect(page.getByRole("menu", { name: "Trade opponent" })).toHaveCount(0);
  await picker.click();
  await expect(challenger.getByRole("img", { name: "Challenger team logo" })).toBeVisible();
  await challenger.click();
  await expect(page.getByRole("button", { name: "Trade opponent: Challenger" })).toBeVisible();
  await page.getByRole("button", { name: "Trade opponent: Challenger" }).click();
  await page.getByRole("menuitemradio", { name: /Rival/ }).click();
  await expect(page.getByRole("button", { name: "Trade opponent: Rival" })).toBeVisible();
  const resultOpponent = page.getByText("vs", { exact: true }).locator("..");
  await expect(resultOpponent).toBeVisible();
  await expect(resultOpponent.getByRole("img", { name: "Rival team logo" })).toBeVisible();
  await expect(page.getByText("Week 4 roster snapshot")).toBeVisible(); // Phase 22 provenance
  await expect(page.getByText("My RB2", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Their WR1", { exact: true })).toHaveCount(2);
  await expect(page.getByText(/My RB2 gives you depth/)).toBeVisible();
  await expect(page.getByRole("img", { name: "My RB2 ESPN portrait" })).toHaveCount(2);
  await expect(page.getByRole("img", { name: "Their WR1 ESPN portrait" })).toHaveCount(2);
  // exact: the model note is a prefix of the static advisory copy below it.
  await expect(
    page.getByText("Advisory only — the app never executes trades on ESPN.", { exact: true }),
  ).toBeVisible();
  // Exercise the POST path too (mock returns the same proposal).
  await page.getByRole("button", { name: "Regenerate" }).click();
  await expect(page.getByText(/My RB2 gives you depth/)).toBeVisible();
});

test("status page renders health/AI/account/league counts without leaking secrets", async ({ page }) => {
  const FAKE_SWID = "{FAKE-SWID-DO-NOT-RENDER}";
  const FAKE_S2 = "fake-espn-s2-should-never-render";
  await page.route("**/api/health", (r) => json(r, { status: "ok", season: 2026, db_path: "/data/edge.db" }));
  // Accounts include (as if a buggy API leaked them) cookie fields the UI must never show.
  await page.route("**/api/accounts", (r) =>
    json(r, [
      { id: 1, label: "Main", status: "active", created_at: NOW, swid: FAKE_SWID, espn_s2: FAKE_S2 },
      { id: 2, label: "Work", status: "needs_reauth", created_at: NOW, swid: FAKE_SWID, espn_s2: FAKE_S2 },
    ]));
  // Two leagues, one with a failed last sync.
  await page.route("**/api/portfolio", (r) =>
    json(r, [
      { ...PORTFOLIO[0], last_sync_ok: true },
      { ...PORTFOLIO[1], last_sync_ok: false, last_sync_error: "auth_failed" },
    ]));

  await page.goto("/");
  await page.getByLabel("Top navigation").getByRole("link", { name: "Status" }).click();

  await expect(page.getByRole("heading", { name: "System status" })).toBeVisible();
  // Health + season + DB path.
  await expect(page.getByText("/data/edge.db")).toBeVisible();
  await expect(page.getByText("2026")).toBeVisible();
  // AI model names from the mocked ai/status (enabled:false).
  await expect(page.getByText("claude-sonnet-5")).toBeVisible();
  await expect(page.getByText("claude-haiku-4-5")).toBeVisible();
  // Counts: 2 accounts, 1 needs re-auth, 2 leagues, 1 failed sync (exact to hit the <dt> labels).
  await expect(page.getByText("Need re-auth", { exact: true })).toBeVisible();
  await expect(page.getByText("Last sync failed", { exact: true })).toBeVisible();
  await expect(page.getByText("Tracked", { exact: true })).toBeVisible();
  // No cookie/secret ever reaches the DOM.
  await expect(page.locator("body")).not.toContainText(FAKE_SWID);
  await expect(page.locator("body")).not.toContainText(FAKE_S2);
});

test("re-auth form posts new cookies and clears the needs_reauth badge", async ({ page }) => {
  let reauthed = false;
  let posted: unknown = null;
  // Stateful accounts endpoint: needs_reauth until the reauth POST succeeds.
  await page.route("**/api/accounts", (r) =>
    json(r, [{ id: 7, label: "Expired", status: reauthed ? "active" : "needs_reauth", created_at: NOW }]));
  await page.route("**/api/accounts/7/reauth", (r) => {
    posted = r.request().postDataJSON();
    reauthed = true;
    return json(r, { id: 7, label: "Expired", status: "active", created_at: NOW });
  });

  await page.goto("/");
  await page.getByLabel("Top navigation").getByRole("link", { name: "Manage" }).click();

  // needs_reauth is shown and the re-auth form is auto-expanded.
  await expect(page.getByTestId("account-status-7")).toHaveText("needs_reauth");
  const form = page.getByTestId("reauth-form-7");
  await expect(form).toBeVisible();
  await form.getByPlaceholder("SWID {…}").fill("NEW-9");
  await form.getByPlaceholder("espn_s2").fill("fresh-cookie-value");
  await form.getByRole("button", { name: "Save cookies" }).click();

  // The POST fired with exactly the entered cookies (secrets only in the body).
  await expect.poll(() => posted).toEqual({ swid: "NEW-9", espn_s2: "fresh-cookie-value" });
  // UI reloaded → badge cleared, form collapsed, no secret left in the DOM.
  await expect(page.getByTestId("account-status-7")).toHaveText("active");
  await expect(page.getByTestId("reauth-form-7")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("fresh-cookie-value");
});
