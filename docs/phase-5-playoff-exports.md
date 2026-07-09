# Phase 5 — Monte Carlo playoff odds + exports (contract)

Two features: a real (deterministic, seeded) Monte Carlo playoff-odds simulation that
replaces the Phase 3 heuristic, and CSV/JSON/XLSX portfolio exports. All computation is
backend-only; React never computes analytics or calls ESPN. See also
[phase-3-analytics.md](phase-3-analytics.md) (heuristic → this upgrade).

## Playoff odds

### Meaning
`playoff_odds` (0.0–1.0, nullable) = the probability my team makes the playoffs,
estimated by simulating the remaining regular-season schedule many times. Still stored
in `metrics` under key `"playoff_odds"` (per team, `week=NULL`), recomputed on every
sync, idempotent.

### Exact inputs (DB only; nothing fabricated)
- `leagues.playoff_team_count` (playoff spots), `leagues.lifecycle`.
- `teams`: `wins, losses, ties, points_for, standing`.
- `matchups`: `week, home_team_id, away_team_id, home_points, away_points, is_playoff`.
  A matchup is **played** if either side scored > 0. **Remaining** games = not played,
  not `is_playoff`, both teams present. Each team's per-week scores (from played games)
  seed its scoring distribution.

### Computation (`api/services/playoff_sim.py`, knobs in `api/edge_config.py`)
Per-team score model: `μ` = mean of that team's played scores; `σ` = population stdev of
those scores, or a fallback `SIM_SIGMA_FALLBACK_FRAC · μ` (floored at `SIM_SIGMA_FLOOR`)
when fewer than 2 games are played. Each simulation draws every remaining game's scores
`~ Normal(μ, σ)` for both teams, tallies wins (ties = 0.5) and points-for on top of the
current record, ranks all teams by `(wins desc, points_for desc)`, and marks the top
`playoff_team_count` as making it. `playoff_odds = made / SIM_COUNT`. A stable seed
(`SIM_SEED`) makes it **reproducible** (tests are deterministic).

### Case behavior
- **complete** → deterministic: `1.0` if `standing ≤ playoff_team_count`, else `0.0`
  (final standings are ground truth — no simulation).
- **in_season** with a known `playoff_team_count` and ≥1 played game →
  Monte Carlo as above. If there are no remaining games, every sim is identical and the
  result is a deterministic 1.0/0.0 by current standings.
- **in_season but inputs missing** (`playoff_team_count` unknown, or no played games to
  estimate `μ`) → **pending** (`None`) — never fabricated.
- **pre_draft / drafted** (no meaningful results) → **pending** (`None`).

### Persistence / exposure
Unchanged from Phase 3: upserted into `metrics` (cleared to pending when inputs vanish),
surfaced through `/api/portfolio`, `/api/portfolio/summary`, and
`/api/leagues/{id}/overview` as before. No API shape change — only the underlying number
gets better.

## Exports (`api/services/exports.py`, `api/routers/exports.py`)

All exports reflect DB/view-layer data **exactly** (no frontend recomputation), built
from the same `services/portfolio.py` row/summary builders the board uses.

| endpoint | type | content |
|---|---|---|
| `GET /api/exports/portfolio.csv` | `text/csv` | one row per league (the Portfolio Board columns) |
| `GET /api/exports/portfolio.json` | `application/json` | `{generated_at, summary, rows, leagues:[{league, teams, draft, matchups, activity}]}` |
| `GET /api/exports/portfolio.xlsx` | `…spreadsheetml.sheet` | **Portfolio** summary sheet + one sheet per league (standings + edge) |

All send `Content-Disposition: attachment; filename=…`. XLSX uses the existing `openpyxl`
dependency. Frontend: typed download helpers in `api.ts`; the Portfolio Board's control
bar exposes CSV / JSON / XLSX download buttons (replacing the placeholder CSV button).

## Tests & manual smoke

Backend is covered offline: pure seeded-Monte-Carlo tests (deterministic, ordering,
pending cases), a recompute regression that playoff odds stay pending without a
schedule/`playoff_team_count`, and export API tests (content types, CSV/JSON shape, and
an XLSX opened with `openpyxl` to check sheets/headers).

No frontend test framework yet, so the export UI has a **manual smoke checklist**:
1. Board control bar shows the `Export · CSV · JSON · XLSX` group.
2. Clicking each downloads `portfolio.{csv,json,xlsx}` (browser uses the
   `Content-Disposition` filename).
3. The CSV/XLSX numbers match the board (records, edge, playoff %); the XLSX has a
   `Portfolio` sheet + one sheet per league.

## Intentionally future
- No long-term-premium / advanced score models (still Normal from season-to-date); no
  strength-of-schedule weighting or opponent-adjusted variance; playoff-seed tiebreakers
  beyond points-for; no bracket/championship-odds sim. Per-league export selection and
  an `.xlsx` per-league draft/roster detail beyond standings are future.
