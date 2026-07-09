# Phase 3 analytics — contract (v1)

This documents the first real analytics layer for the four metrics the UI has been
showing as placeholders: **edge_score, grade, verdict, playoff_odds**.

Design stance: **deterministic and explainable over clever**. v1 uses only data
already persisted from ESPN syncs and simple, auditable formulas. The richer
population-normalized model in SPEC §6 (opponent lineup efficiency, all-play, draft
surplus curves, Monte Carlo playoff sim) is intentionally **future work** — see
"v1 vs future" at the end. Nothing here calls ESPN or runs in React.

All weights and thresholds live in one file: **`api/edge_config.py`** (SPEC §6).

---

## 1. What each metric means

- **edge_score** (`0–100`, higher = better positioned): how advantaged *my* team is
  within *this* league, as a single composite. It is a **relative, within-league**
  measure — a proxy for SPEC's Edge Index, not the full Index yet.
- **grade** (`A`/`B`/`C`/`D`/`F`): a human-readable band of `edge_score`.
- **verdict** (`advantaged` / `neutral` / `disadvantaged` / *pending*): the portfolio
  bucket, from `edge_score` thresholds. *pending* when inputs are insufficient.
- **playoff_odds** (`0.0–1.0`, nullable): estimate of making the playoffs.
  > **Superseded in Phase 5:** this was a coarse standings/record heuristic in Phase 3
  > and has been **replaced by a real seeded Monte Carlo simulation** — see
  > [phase-5-playoff-exports.md](phase-5-playoff-exports.md). The Phase-3 description
  > below is retained for history; the API/persistence contract is unchanged.

## 2. Exact input data (all from the local DB, never live ESPN)

Per team in the league (`teams` table): `wins, losses, ties, points_for,
points_against, standing`. Per league (`leagues`): `size, lifecycle,
playoff_team_count`. For preseason roster strength: each team's drafted players
(`draft_picks.espn_player_id`) joined to `players.proj_ros` (ESPN season projection),
summed per team. No cookies/SWID/espn_s2 are read or emitted.

## 3. How each metric is computed

Normalization is **within-league percentile** (`_percentile`): a value's rank among
the league's teams, scaled to 0–100 (ties get mid-rank; a 1-team league → 50).

**edge_score**, by lifecycle:
- `pre_draft` → **pending** (`None`): no draft, no games.
- `drafted` (picks exist, no games) → percentile of the team's **summed roster
  projection** (`proj_ros`) within the league. Pending if fewer than 2 teams have any
  projection data.
- `in_season` / `complete` (≥1 game played) → weighted blend of within-league
  percentiles (weights in `edge_config.INSEASON_WEIGHTS`, sum = 1.0):
  - `win_pct` = `(wins + 0.5·ties) / games_played` — **0.40**
  - `points_for` — **0.30**
  - `point_diff` = `points_for − points_against` — **0.30**
  - (If an in_season league somehow has 0 games, falls back to the `drafted` rule.)

**grade** — `edge_config.grade_for(score)`: bands `A ≥ 80, B ≥ 65, C ≥ 50, D ≥ 40,
else F`. `None → None`.

**verdict** — `edge_config.verdict_for(score)`: `advantaged ≥ 65`, `neutral ≥ 45`,
else `disadvantaged`. `None → None` (rendered as *pending*). Thresholds are SPEC §6.

**playoff_odds** — *(Phase 3 heuristic, now superseded — kept for history)* needed
`standing`, `size`, `playoff_team_count`; `complete` → `1.0`/`0.0` by final standing,
`in_season` → `clamp(0.5·win_pct + 0.5·seed, 0.02, 0.98)`, else pending. **Phase 5
replaces the in-season case with a seeded Monte Carlo of the remaining schedule**
(`metrics.compute_playoff_odds_for_league` → `services/playoff_sim.py`); `complete` stays
deterministic 1.0/0.0 and missing schedule/settings stay pending. See
[phase-5-playoff-exports.md](phase-5-playoff-exports.md).

## 4. Persistence

Computed values are stored in the existing **`metrics`** table, one row per
`(league_id, team_id, key, week=NULL)` using keys `"edge_score"` and
`"playoff_odds"` (`value_float`). This matches the `uq_metric_team` partial unique
index, so recompute upserts in place.

`grade` and `verdict` are **not** stored — they are pure, centralized functions of
the persisted `edge_score` (`edge_config.grade_for` / `verdict_for`), derived in the
read layer. Storing only the numeric source keeps the float-only `metrics` schema and
guarantees grade/verdict can never drift from the score.

One small schema addition: `leagues.playoff_team_count` (already parsed from
`mSettings`, previously discarded) — needed for `playoff_odds` and future Phase 5 sim.

## 5. Invalidation / recompute

Metrics are **recomputed on every sync**. `SyncService.sync_league` calls
`metrics.recompute_league(session, league, projections_fresh=...)` at step 6, after
all ESPN data for the league is upserted. Recompute reads current DB state for every
team and **upserts or clears** each metric: if a metric becomes pending (e.g. a league
reverts to `pre_draft`, or projection data disappears), the stale row is **deleted**,
so the API never serves a value that no longer holds. There is no separate cache to
invalidate.

**Stale-projection guard:** the projection-based edge branch (drafted / no-games
leagues) must not be recomputed from possibly-stale `players.proj_ros`. If
`kona_player_info` fails during a sync (`players_failed`), the sync passes
`projections_fresh=False`, and `recompute_league` treats projection-based edge scores
as **pending and clears them** — so a drafted league can't serve a freshly-stamped
score derived from old projections. The record/points branch (in_season / complete
with ≥1 game) does **not** depend on projections and still computes normally in this
case. `playoff_odds` depends only on standings/size/`playoff_team_count`, so it is
unaffected. The `players_failed` warning still surfaces in the sync result/UI.

## 6. Missing / incomplete data

Represented as **absence**: a pending metric has **no `metrics` row**, and the read
layer returns `null`. `edge_score = null` implies `grade = null`, `verdict = null`
(*pending*). `playoff_odds` is independently `null` when its inputs are missing
(e.g. preseason, or unknown `playoff_team_count`). The UI already renders `null` as a
muted placeholder / "Not yet scored" tier.

## 7. API exposure (pending vs computed)

The read endpoints return the persisted/derived values; `null` means pending:
- `GET /api/portfolio` — each row's `edge_score, grade, verdict, playoff_odds` for the
  detected "my team".
- `GET /api/portfolio/summary` — `advantaged_count` (verdict == advantaged),
  `scored_count` (edge_score not null), `best/worst_edge_score`, aggregated in the
  view layer from the same rows.
- `GET /api/leagues/{id}/overview` — `edge_score, grade, verdict, playoff_odds` for my team.

React only formats these; it never computes them.

## 8. v1 vs future (intentional placeholders)

**Deterministic v1 (this phase):** within-league percentile edge_score from
record/points/roster-projection, grade/verdict bands. (playoff_odds was a heuristic
here; **Phase 5 upgraded it to a Monte Carlo simulation** — see phase-5 doc.)

**Future (SPEC §6, not built):** cross-league population normalization + p5/p95
winsorization; LeagueSoftness components (opponent lineup inefficiency, inactivity,
abandoned-team detection, draft indiscipline, exploitable-weakness share); MyEdge
components (draft surplus value curve, lineup efficiency, waiver capture,
luck-adjusted record); per-component breakdown bars. The UI must not present more
certainty than these v1 numbers justify.

---

## Resetting / recomputing locally

Metrics recompute automatically on sync, so **re-syncing a league recomputes it**
(`POST /api/leagues/{id}/sync`, "Sync"/"Sync all" in the UI, or `make verify`). To
rebuild everything from scratch (also required after the `playoff_team_count` column
addition): `make db-reset`, then re-add accounts/leagues and sync. Source data lives
on ESPN; nothing analytics-related is authoritative only in the DB.
