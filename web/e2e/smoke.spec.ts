import { expect, test, type Page, type Route } from "@playwright/test";

// Deterministic mock data — the app's read models. No backend/ESPN involved.
const NOW = new Date("2026-07-09T12:00:00Z").toISOString();

const PORTFOLIO = [
  {
    league_id: 1, espn_league_id: "111", season: 2026, league_name: "Alpha League",
    size: 8, account_label: "Main", lifecycle: "in_season", last_synced_at: NOW,
    my_team_id: 1, my_team_name: "My Team", wins: 5, losses: 2, ties: 0,
    points_for: 900.5, points_against: 820.1, standing: 2,
    edge_score: 72.0, grade: "B", playoff_odds: 0.81, verdict: "advantaged",
  },
  {
    league_id: 2, espn_league_id: "222", season: 2026, league_name: "Beta League",
    size: 10, account_label: "Main", lifecycle: "pre_draft", last_synced_at: null,
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
  is_public: false, last_synced_at: NOW,
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
};

const AI_DISABLED = (kind: string) => ({
  enabled: false, kind, model: null, created_at: null, stale: false,
  content: null, error: null,
});

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

test("AI-disabled state renders the connect-a-key panel without crashing", async ({ page }) => {
  await page.goto("/league/1");
  await page.getByRole("button", { name: "AI Brief" }).click();
  await expect(page.getByText("AI analysis is off")).toBeVisible();
});
