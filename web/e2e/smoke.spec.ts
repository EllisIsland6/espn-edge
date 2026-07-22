import { expect, test, type Page, type Route } from "@playwright/test";

// Deterministic mock data — the app's read models. No backend/ESPN involved.
const NOW = new Date("2026-07-09T12:00:00Z").toISOString();

const PORTFOLIO = [
  {
    league_id: 1, espn_league_id: "111", season: 2026, league_name: "Alpha League",
    size: 8, account_label: "Main", lifecycle: "in_season", last_synced_at: NOW,
    last_sync_ok: true, last_sync_error: null,
    my_team_id: 1, my_team_name: "My Team", wins: 5, losses: 2, ties: 0,
    points_for: 900.5, points_against: 820.1, standing: 2,
    edge_score: 72.0, grade: "B", playoff_odds: 0.81, verdict: "advantaged",
    edge_index_score: 68.0, edge_index_grade: "B", edge_index_verdict: "advantaged",
  },
  {
    league_id: 2, espn_league_id: "222", season: 2026, league_name: "Beta League",
    size: 10, account_label: "Main", lifecycle: "pre_draft", last_synced_at: null,
    last_sync_ok: null, last_sync_error: null,
    my_team_id: null, my_team_name: null, wins: null, losses: null, ties: null,
    points_for: null, points_against: null, standing: null,
    edge_score: null, grade: null, playoff_odds: null, verdict: null,
    edge_index_score: null, edge_index_grade: null, edge_index_verdict: null,
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
      points_against: 800.0, standing: 1, logo_url: null,
    },
    {
      id: 1, espn_team_id: 1, name: "My Team", abbrev: "MINE", is_me: true,
      autodrafted: false, wins: 5, losses: 2, ties: 0, points_for: 900.5,
      points_against: 820.1, standing: 2, logo_url: null,
    },
  ],
  edge_score: 72.0, grade: "B", verdict: "advantaged", playoff_odds: 0.81,
  components: [
    { key: "win_pct", label: "Win %", weight: 0.4, percentile: 75.0 },
    { key: "points_for", label: "Points for", weight: 0.3, percentile: 87.5 },
    { key: "point_diff", label: "Point differential", weight: 0.3, percentile: 62.5 },
  ],
};

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
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: false, standard_model: "claude-sonnet-5", bulk_model: "claude-haiku-4-5" }));
  await page.route("**/api/portfolio", (r) => json(r, PORTFOLIO));
  await page.route("**/api/portfolio/summary", (r) => json(r, SUMMARY));
  await page.route("**/api/leagues", (r) => json(r, [LEAGUE_1]));
  await page.route("**/api/accounts", (r) => json(r, []));
  await page.route("**/api/leagues/1/overview", (r) => json(r, OVERVIEW));
  await page.route("**/api/leagues/1/teams", (r) => json(r, OVERVIEW.teams));
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
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("portfolio board loads with rows, summary, and export controls", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: /ESPN\s*Edge/ })).toBeVisible();
  await expect(page.getByText("Alpha League")).toBeVisible();
  await expect(page.getByText("Leagues tracked")).toBeVisible();
  for (const label of ["CSV", "JSON", "XLSX"]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible();
  }
});

test("portfolio board shows Edge Index as the primary score (Phase 17)", async ({ page }) => {
  await page.goto("/");
  // Alpha League's row chip shows the Edge Index composite (68), with the legacy score noted.
  await expect(page.getByText("68", { exact: true })).toBeVisible(); // Edge Index chip value
  await expect(page.getByText(/Edge Index · legacy 72/)).toBeVisible(); // row secondary label
  // Right rail is now driven by Edge Index, with legacy kept clearly labeled.
  await expect(page.getByText("Best / worst Edge Index")).toBeVisible();
  await expect(page.getByText("Legacy Edge Score")).toBeVisible();
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
  await page.getByRole("link", { name: "Manage" }).click();
  await expect(page.getByRole("heading", { name: "Add ESPN account" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Add league" })).toBeVisible();
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
  await page.getByRole("button", { name: "Import & sync 1" }).click();

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
  await expect(page.getByRole("heading", { name: "Alpha League" })).toBeVisible();
  await expect(page.getByText("PPR")).toBeVisible();
  await expect(page.getByText("Rival")).toBeVisible();
  await expect(page.getByText("My Team")).toBeVisible();
});

test("league detail Overview renders edge component breakdown bars", async ({ page }) => {
  await page.goto("/league/1");
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
  // Resolved player name + position + ADP from the mocked draft payload (Phase 10).
  await expect(page.getByText("Star Runningback")).toBeVisible();
  await expect(page.getByText("Ace Receiver")).toBeVisible();
  await expect(page.getByText("RB", { exact: true })).toBeVisible(); // position pill
  await expect(page.getByText("3.4")).toBeVisible(); // ADP column
  await expect(page.getByRole("img", { name: "Star Runningback ESPN portrait" })).toHaveAttribute(
    "src",
    "/api/players/1001/portrait",
  );
});

test("activity renders ESPN portraits with resolved player names", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "Activity" }).click();
  await expect(page.getByText("Star Runningback")).toBeVisible();
  await expect(page.getByText("Ace Receiver")).toBeVisible();
  await expect(page.getByRole("img", { name: "Star Runningback ESPN portrait" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Ace Receiver ESPN portrait" })).toBeVisible();
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
});

test("teams tab renders lineup efficiency table (Phase 13)", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "Teams" }).click();
  await expect(page.getByText("Lineup efficiency")).toBeVisible();
  await expect(page.getByText("83.3%")).toBeVisible(); // My Team efficiency (0.8333)
  await expect(page.getByText("Left on bench/wk")).toBeVisible(); // column header
});

test("AI-disabled state renders the connect-a-key panel without crashing", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByText("AI analysis is off")).toBeVisible();
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
  await expect(page.getByText("vs Rival")).toBeVisible();
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
  await page.getByRole("link", { name: "Status" }).click();

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
  await page.getByRole("link", { name: "Manage" }).click();

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
