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
  },
  {
    league_id: 2, espn_league_id: "222", season: 2026, league_name: "Beta League",
    size: 10, account_label: "Main", lifecycle: "pre_draft", last_synced_at: null,
    last_sync_ok: null, last_sync_error: null,
    my_team_id: null, my_team_name: null, wins: null, losses: null, ties: null,
    points_for: null, points_against: null, standing: null,
    edge_score: null, grade: null, playoff_odds: null, verdict: null,
  },
];

const SUMMARY = {
  total_leagues: 2, advantaged_count: 1, scored_count: 1,
  aggregate_wins: 5, aggregate_losses: 2, aggregate_ties: 0,
  best_edge_score: 72.0, worst_edge_score: 72.0,
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

function json(route: Route, data: unknown) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
}

async function mockApi(page: Page) {
  // Fallback first (lowest priority) so no request ever escapes to a real backend.
  await page.route("**/api/**", (r) => json(r, {}));
  await page.route("**/api/ai/status", (r) =>
    json(r, { enabled: false, standard_model: "claude-sonnet-5", bulk_model: "claude-haiku-4-5" }));
  await page.route("**/api/portfolio", (r) => json(r, PORTFOLIO));
  await page.route("**/api/portfolio/summary", (r) => json(r, SUMMARY));
  await page.route("**/api/leagues", (r) => json(r, [LEAGUE_1]));
  await page.route("**/api/accounts", (r) => json(r, []));
  await page.route("**/api/leagues/1/overview", (r) => json(r, OVERVIEW));
  await page.route("**/api/leagues/1/ai/league-brief", (r) => json(r, AI_DISABLED("league_brief")));
  await page.route("**/api/leagues/1/ai/advantage-verdict", (r) =>
    json(r, AI_DISABLED("advantage_verdict")));
  await page.route("**/api/leagues/1/ai/draft-recaps", (r) =>
    json(r, { enabled: false, kind: "draft_recap", reports: [] }));
  await page.route("**/api/leagues/1/draft", (r) => json(r, DRAFT));
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
