# ESPN EDGE — Multi-Account, Multi-League Fantasy Advantage Tracker
### Build specification for Claude Code · v1.0 · July 2026

---

## 0. How to use this document

Drop this file into an empty repo as `SPEC.md`, then start Claude Code with:

> Read SPEC.md in full before writing any code. Section 1 (ESPN data access) is the verified technical foundation — do not substitute a different auth method, base URL, or data source without asking me first. Execute Phase 0 and Phase 1 from Section 10, then stop for my review.

Work phase-by-phase (Section 10). Each phase has acceptance criteria; don't move on until they pass. Copy the "CLAUDE.md seed" from Appendix A into the repo so the project rules persist across sessions.

---

## 1. What we're building

A locally-run web app that connects to **all of my ESPN fantasy football leagues across multiple ESPN accounts**, pulls every league's full state (settings, standings, rosters, matchups, draft results, transactions), and answers one question with data: **"Am I an advantaged player in each of these leagues — and in my portfolio overall?"**

Context: NFL redraft, PPR, league sizes ranging from 8 to 32 teams, 2026 season. Tracked per league: W-L-T record, points for/against, current standing, draft slot and roster strength (ADP-based), and trades/waiver moves. On top of the raw data, the app computes an **Edge Index** per league (Section 6) and layers **AI analysis** (Section 7): draft recaps per team, strategy classification, league-difficulty narratives, and weekly recaps. Feature inspiration comes from FantasyPros My Playbook / League Sync (multi-league assistant, league analyzer, start/sit, waiver assistant, trade assistant) — but self-hosted, ESPN-only, and built around the advantage question instead of generic advice.

The visual target is a dark, dense, draft-board aesthetic (Section 9) — near-black navy, red primary actions, green mono value chips, colored position pills, gold tier dividers.

---

## 2. TECHNICAL FOUNDATION — ESPN data access (read first, do not improvise)

### 2.1 Reality check

ESPN has **no official public fantasy API**. There is, however, a stable, well-mapped **unofficial v3 JSON API** — the same one that powers fantasy.espn.com and the ESPN Fantasy app. The community has documented it for years (espn-api Python lib, ffscrapr, mkreiser's JS client). Everything below is verified against sources current as of mid-2026. Treat it as unofficial: no SLA, subject to change, personal use, be polite with request volume.

### 2.2 Base endpoints (current)

ESPN moved the read API off `fantasy.espn.com` to a dedicated host in **April 2024**. Use:

```
# 2018 season and later (this is the one we use):
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{leagueId}

# 2017 and earlier (historical archive; returns a JSON array, take [0]):
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{leagueId}?seasonId={season}

# League-independent player universe (ADP, ownership, projections):
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/players?view=...
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3?view=kona_player_info
# (leaguedefaults/3 = ESPN's PPR default scoring universe — matches our PPR leagues)
```

`segments/0` = the full regular season. Weekly slices come from the `scoringPeriodId` query param, not different segments. Data is requested by stacking `view=` params (Section 2.4); multiple views can be combined in one request: `?view=mSettings&view=mTeam&view=mRoster`.

Legacy note: `fantasy.espn.com/apis/v3/...` may still redirect but is deprecated. Hardcode the `lm-api-reads` host in one config constant so a future move is a one-line fix.

### 2.3 Authentication: espn_s2 + SWID cookies (per account)

- **Public leagues** need no auth at all.
- **Private leagues** authenticate by sending two cookies from a logged-in browser session: `SWID` and `espn_s2`. This is the only viable method. **Do not build username/password login automation** — ESPN's Disney login has bot protection (reCAPTCHA) and programmatic login has been broken for years. Cookie auth is the industry-standard approach (espn-api, ffscrapr, FantasyPros' ESPN flow all rely on session credentials).
- The cookies are **long-lived** (persist across sessions, typically valid for many months). Handle expiry gracefully: on 401/403, mark the account "needs re-auth" in the UI instead of crashing.
- **Multi-account model**: each ESPN account = one (SWID, espn_s2) pair. The app stores multiple account entries, each with a user-supplied label ("Main", "Work email", etc.). Every league is fetched with its owning account's cookie pair.

**Cookie acquisition UX** (build this as a first-class onboarding screen with these instructions rendered in-app):
1. Log in at fantasy.espn.com in Chrome.
2. DevTools → Application → Cookies → `https://fantasy.espn.com`.
3. Copy `SWID` (keep the curly braces, e.g. `{ABC123-...}`) and `espn_s2` (a very long string).
4. Paste both into the app's "Add ESPN account" form.
(Alternative: the open-source "ESPN Cookie Finder" Chrome extension pulls both values in one click — link it in the UI.)

**Gotchas**: some browsers copy `espn_s2` URL-encoded (`%2F`, `%3D`...). Store exactly what the user pasted; if auth fails, automatically retry once with the URL-decoded value and persist whichever variant worked. Send both cookies on every private-league request, plus a browser-like `User-Agent` and `Accept: application/json`.

**Access boundary to know**: for private leagues, an account can only read seasons it was actually a member of. Cross-account leagues must be fetched with the correct account's cookies — another reason every league row stores its `account_id`.

### 2.4 Views reference (what to request for each feature)

| View | Returns | Used for |
|---|---|---|
| `mSettings` | League name, size, scoring rules, roster/lineup slots, playoff format, draft settings | League config, PPR verification, lineup-slot map |
| `mTeam` | Teams, owners (SWID list per team), records, standings, logos | Standings, "my team" detection |
| `mRoster` | Current rosters per team | Roster strength |
| `mMatchup` / `mMatchupScore` | Matchup schedule + weekly scores (add `scoringPeriodId=N` for a week) | Records, PF/PA, all-play |
| `mBoxscore` | Per-player started/bench points for a week (with `scoringPeriodId`) | Lineup efficiency, points-left-on-bench |
| `mDraftDetail` | Every draft pick: overall #, round, team, player, keeper flag, autodraft flag, auction bid | Draft analysis, Edge Index |
| `mStandings` | Computed standings | Cross-check |
| `mTransactions2` + `mPendingTransactions` | Adds/drops/waivers/trades | Activity metrics |
| `kona_player_info` | Player pool with ESPN ADP, % owned, projections (respects league scoring when called on a league; PPR defaults via `leaguedefaults/3`) | ADP source, projections |
| `mLiveScoring`, `mPositionalRatings`, `mNav` | Live scores, positional ratings, nav metadata | Optional/nice-to-have |

Also available: `GET {league}/communication/?view=kona_league_communication` style topics feed — this is what espn-api's `recent_activity()` uses for the human-readable activity log. Prefer `mTransactions2` for structured data and fall back to the communication feed if a transaction type isn't covered. **Verify exact field names against a live response before modeling** (e.g., draft picks are expected to carry `overallPickNumber`, `roundId`, `roundPickNumber`, `teamId`, `playerId`, `keeper`, `autoDraftTypeId`, `bidAmount` — confirm on first pull and adjust).

> **VERIFIED (2026-07-07, league 17739342):** all draft-pick field names above are correct as-is. Additional gotcha: a **pre-draft** league still returns a full `draftDetail.picks` array of empty SLOTS (rounds × teams, e.g. 128 for 8×16) with `playerId: -1` and `draftDetail.drafted: false`. Filter picks to `playerId > 0` so undrafted leagues report 0 picks (matches ESPN/espn-api). Boxscore path `home/away.rosterForCurrentScoringPeriod.entries[]` and `mMatchupScore` shape also confirmed.
>
> **OPEN (transactions):** `view=mTransactions2` returned **no `transactions` key at all** for league 17739342 in 2025/2026 (plain and with an `X-Fantasy-Filter`), and `/communication/?view=kona_league_communication` 404'd on the filter shape tried. Our parser handles the absence gracefully (0 transactions, no crash). Whether this league genuinely had zero waiver activity or the history needs the activity/communication feed is **unconfirmed** (ESPN rate-limited further probing). Deferred to Phase 3, where the §6.1 opponent-inactivity metric requires the activity feed — resolve the correct endpoint/filter there.

### 2.5 The X-Fantasy-Filter header

Player-pool endpoints cap results (~50) unless you pass a JSON filter in the `X-Fantasy-Filter` **header**. Known-good example for a full draftable pool sorted by ownership:

```json
{"players": {
  "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
  "limit": 1500,
  "sortPercOwned": {"sortPriority": 1, "sortAsc": false},
  "sortDraftRanks": {"sortPriority": 100, "sortAsc": true, "value": "STANDARD"}
}}
```

Use it for `kona_player_info` pulls. `{"games":{"limit":2000}}` style filters also lift limits on other list endpoints.

### 2.6 League discovery per account

Two paths, in order:

1. **Attempted auto-discovery (verify at build time)**: ESPN exposes a "fan profile" API that returns an account's fantasy entries (league IDs, team IDs, league names) when called with the account's SWID + cookies — the candidate endpoint is `https://fan.api.espn.com/apis/v2/fans/{SWID}` (SWID with braces, URL-encoded). This endpoint is used by community dashboard projects but is less documented than the league API, so: implement it behind a `discover_leagues(account)` function, test with a real account on first run, and if the shape differs, capture the actual request the fantasy.espn.com dashboard makes (DevTools → Network) and adapt. Do not let this block Phase 1.
2. **Guaranteed fallback — manual add**: user pastes a league ID (or the league URL; parse `leagueId=` out of it) and picks which account owns it. This always works and ships first.

### 2.7 Identifying "my team" in each league

`mTeam` includes each league `members[]` entry with an `id` equal to that member's SWID, and each team's `owners[]` lists member SWIDs. **Match the account's SWID against team owners to auto-flag `is_me`** — no manual team selection needed (but allow a manual override in the UI for co-managed teams).

### 2.8 Client strategy: espn-api library + raw escape hatch

Use **`espn-api` (PyPI, cwendt94)** as the primary client — it's MIT-licensed, actively maintained (v0.46.x, released March 2026), already handles the current base URL, endpoint switching for historical seasons, cookies, box scores, draft, free agents, and activity. Wrap it in our own `EspnService` layer so the app never imports it directly, and add a thin `raw_view(league_id, season, views, scoring_period, x_fantasy_filter)` httpx client alongside it for anything the library doesn't expose (e.g., `mTransactions2`, kona pulls with custom filters). If the library breaks on an ESPN change, the raw client + this spec is the fallback; enable `debug=True` mode when diagnosing.

> **AMENDMENT (2026-07-07):** Inverted — the raw httpx client is the workhorse (own parser → fixture-replayable offline tests, custom views, kona filters); same auth/host/data sources unchanged. `espn-api` stays installed as an independent cross-check: `python -m api.verify --cross-check` loads the league through it and diffs headline numbers against our parser.

### 2.9 ADP + projections sources (for "ADP-based roster strength" and draft grading)

Primary and secondary, both free:

1. **ESPN's own ADP/ownership/projections** via `kona_player_info` (league-scoped for league-scoring-adjusted values; `leaguedefaults/3` for the PPR universe). Fields include `ownership.averageDraftPosition`, `ownership.percentOwned`, `draftRanksByRankType` (PPR/STANDARD), and per-period projected vs actual stats (projections carry `statSourceId: 1`, actuals `0`). This is the **default source** — it's platform-consistent with where the drafts actually happened.
2. **Fantasy Football Calculator ADP REST API** — free for personal/commercial use with attribution, no key. Pattern: `https://fantasyfootballcalculator.com/api/v1/adp/ppr?teams=12&year=2026` (formats: standard/ppr/half-ppr/2qb/dynasty; verify params against their help article "ADP REST API"). Use as a cross-platform sanity check and for pre-draft value boards. Add the attribution link in the app footer.

**Player ID mapping**: ESPN player IDs are ESPN-native; FFC has its own. Match on normalized `(name, position, NFL team)` — lowercase, strip punctuation/suffixes (Jr., III), map D/ST naming — and keep a small manual-override table for stragglers. Store both IDs on the player record. Snapshot ADP pulls into `adp_snapshots` (dated) so drift over the summer is queryable.

### 2.10 Request hygiene (non-negotiable)

- Cache every raw JSON response to disk/DB keyed by `(league, season, view-set, scoringPeriodId)`; default TTL 6h in-season, 24h preseason; manual "Sync now" busts cache.
- Throttle to ~1 request/second per account; exponential backoff on 429/5xx.
- Derive the current season from config/env (`SEASON=2026`), never hardcode inline.
- Never log cookie values; never commit them; encrypt at rest (Section 3).

---

## 3. Architecture & stack

Local-first, single-user, Docker-compose. Optimize for inspectability (the user runs a personal Excel/CSV/JSON data pipeline and will want to query the DB directly).

- **Backend**: Python 3.12, **FastAPI**, httpx, SQLAlchemy 2 + **SQLite** (file at `./data/edge.db`), APScheduler for background sync jobs, `cryptography` (Fernet) for cookie encryption with key from env, `pandas` + `openpyxl` for exports.
- **Frontend**: **React + Vite + TypeScript**, Tailwind, **TanStack Table** (dense sortable tables are the core UI), Recharts for sparklines/charts. Dark mode only.
- **AI**: official `anthropic` Python SDK, called from the backend only (Section 7).
- **Layout**: monorepo — `/api` (FastAPI), `/web` (Vite), `/data` (SQLite + raw JSON cache), `docker-compose.yml` at root. `make dev` runs both with hot reload.

No auth layer on the app itself (localhost, single user), but bind to 127.0.0.1 by default since the DB holds ESPN session cookies.

---

## 4. Data model (SQLite)

```
accounts        id, label, swid, espn_s2_encrypted, status(active|needs_reauth), created_at
leagues         id (pk), espn_league_id, season, account_id → accounts,
                name, size, scoring_json, lineup_slots_json, draft_type,
                lifecycle(pre_draft|drafted|in_season|complete),
                my_team_id, is_public, last_synced_at
                UNIQUE(espn_league_id, season)
teams           id, league_id →, espn_team_id, name, abbrev, owner_swids_json,
                is_me, autodrafted(bool), wins, losses, ties, points_for, points_against,
                standing, logo_url
draft_picks     id, league_id →, overall, round, round_pick, team_id →, espn_player_id,
                keeper(bool), autodraft(bool), bid_amount, adp_at_draft, value_delta
matchups        id, league_id →, week, home_team_id, away_team_id,
                home_points, away_points, is_playoff
lineup_slots    id, league_id →, team_id →, week, slot, espn_player_id,
                points, is_starter
transactions    id, league_id →, team_id →, type(waiver|fa_add|drop|trade|...),
                week, player_in, player_out, bid, executed_at
players         espn_player_id (pk), name, position, nfl_team,
                espn_adp, espn_pct_owned, espn_rank_ppr, proj_ros, ffc_id, ffc_adp, updated_at
adp_snapshots   id, source(espn|ffc), pulled_at, format, teams, payload_json
metrics         id, league_id →, team_id →, key, week(nullable), value_float, computed_at
ai_reports      id, league_id →, scope(league|team|portfolio), kind, input_hash,
                model, content_json, created_at
raw_cache       key, fetched_at, payload_json   -- raw ESPN responses for debugging/replay
```

Every derived number in the UI must trace back to a `metrics` row or a raw table — no math in React components beyond formatting.

---

## 5. Sync engine & league lifecycle

`SyncService.sync_league(league)` runs this pipeline (idempotent, upsert everything):

1. `mSettings` + `mTeam` → league config, teams, standings, my-team detection (SWID match).
2. `mDraftDetail` → draft picks (skip cleanly if `drafted == false`; set lifecycle `pre_draft`).
3. For each completed week: `mMatchupScore` + `mBoxscore` with `scoringPeriodId=w` → matchups + per-player lineup rows (starters and bench, with points).
4. `mTransactions2` (fallback: communication feed) → transactions.
5. `kona_player_info` (league-scoped) → refresh player ADP/ownership/projections touched by this league.
6. Recompute metrics for the league (Section 6), then bump `last_synced_at`.

Scheduler defaults: full portfolio sync nightly; in-season, matchup/boxscore sync every 6h on Sun/Mon/Thu; everything manual-triggerable per league and per account. **Lifecycle states drive the UI**: `pre_draft` shows settings + league softness proxies only; `drafted` unlocks draft analysis + preseason Edge; `in_season` unlocks the full Edge Index; `complete` freezes final results.

Right now (July 2026) every league will be `pre_draft` or freshly drafted — the app must be fully useful in those states, not just mid-season.

---

## 6. Analytics engine — the Edge Index

The centerpiece. Per league: **Edge Score (0–100)** = `0.5 × MyEdge + 0.5 × LeagueSoftness`, with a letter grade (A+…F) and a verdict: **Advantaged (≥65) / Neutral (45–65) / Disadvantaged (<45)**. Portfolio view rolls up: "Advantaged in X of Y leagues," mean/median edge, and best/worst leagues.

Normalization rule: every component below is converted to a **percentile (0–100) against the population of all teams across all synced leagues** (a real advantage of the multi-league dataset — with 8–32 team leagues there will be plenty of comparison teams). Winsorize at p5/p95 before averaging. All weights live in one `edge_config.py` file so they're tunable without hunting through code.

### 6.1 LeagueSoftness (how exploitable are the opponents?) — equal-weighted mean of:

1. **Opponent lineup inefficiency** — for each opposing team, weekly `started_points / optimal_points` (optimal = best legal lineup from their roster given the league's slot map, computed from `lineup_slots`). Softness uses `1 − median opponent efficiency`.
2. **Opponent inactivity** — inverse of median opponent transactions per week (adds + claims; trades count double).
3. **Abandoned teams** — fraction of teams flagged abandoned: started ≥2 zero-point/BYE/OUT slots in ≥2 consecutive weeks, OR zero transactions over the trailing 4 in-season weeks. Preseason proxy: `autodrafted == true` on every pick.
4. **Opponent draft indiscipline** — median opponent of mean `(pick_overall − espn_adp_at_draft)` clipped at 0 (only reaches count). Big habitual reaches = soft league.
5. **Exploitable weakness share** — fraction of teams whose points-for per week is below `league median − 1 SD`.

### 6.2 MyEdge (how strong is my position?) — weighted mean of:

1. **Roster strength (0.35)** — sum of rest-of-season ESPN projections for my optimal starting lineup ÷ the league-median team's same figure. Pre-season fallback: ADP-implied value of my drafted roster vs league median (see value curve below).
2. **Draft surplus (0.25)** — `Σ over my picks of [v(adp) − v(overall_pick)]` where `v(p) = 100 × e^(−p/34)` is a smooth pick-value curve (steep early, flat late; calibrate `34` so pick 1 ≈ 100 and pick 100 ≈ 5, and document any recalibration). Positive = I captured value vs the market.
3. **My lineup efficiency (0.20)** — my own `started/optimal` percentile (in-season only; weight redistributes to 1 and 2 pre-season).
4. **Waiver capture (0.10)** — points scored by players while on my roster that I acquired in-season, per week, percentile-ranked.
5. **Luck-adjusted record (0.10)** — all-play win% (my score vs every other team each week) minus actual win%; reported as a *luck delta* and folded in so a good team with an unlucky record still grades as advantaged.

### 6.3 Also compute and display (not in the composite)

Points-for/against percentiles, strength of remaining schedule (opponents' mean PF), **playoff odds via Monte Carlo** (10k sims of the remaining schedule; each team's weekly score ~ Normal(μ, σ) from its season-to-date scoring, respecting the league's playoff-seed settings from `mSettings`), and per-team **draft strategy fingerprints** (positional spend by round: share of first 6 picks on RB/WR, QB/TE round, etc.) which feed the AI layer.

---

## 7. AI analysis layer (Anthropic API)

Backend-only integration via the official `anthropic` Python SDK. Model choice: default to a current mid-tier model (e.g. `claude-sonnet-4-6`) for one-off analyses and a small/fast model (e.g. `claude-haiku-4-5`) for bulk weekly recaps — **verify current model names and options at https://docs.claude.com/en/docs/about-claude/models before wiring in**, and read https://docs.claude.com/en/api/overview for request format. `ANTHROPIC_API_KEY` comes from env; if unset, the app runs fine with AI panels showing a "connect a key" empty state (AI is additive, never load-bearing).

Rules of the layer:

- **Structured output**: every report kind has a JSON schema; request JSON-only responses (or tool-use structured output) and validate with pydantic before storing.
- **Grounding**: prompts contain only computed facts from the DB (picks, ADP deltas, metrics) — the model narrates and classifies, it never invents stats. Include the input data hash in `ai_reports.input_hash` and reuse cached reports when inputs haven't changed; add a "Regenerate" button that busts the cache.
- **Report kinds (v1)**:
  1. **Draft recap per team** — input: that team's picks with round/overall/position/ADP delta + positional fingerprint. Output schema: `{strategy_label, secondary_label, grade, confidence, summary (≤120 words), key_values[], key_reaches[]}`. Strategy labels from a fixed enum: Zero RB, Hero RB, Robust RB, Anchor WR, Elite TE, Late-Round QB, Balanced/BPA, Autodraft/Absent.
  2. **League difficulty brief** — input: all softness components + team fingerprints. Output: `{difficulty_tier, narrative, exploit_plan[]}` — a 3-bullet "how to exploit this league" plan for my team.
  3. **My advantage verdict** — turns the Edge Index components into a plain-English paragraph: why I am/am not advantaged here and the single highest-leverage next move.
  4. **Weekly recap (in-season)** — league newsletter: results, luck notes, waiver highlights. Bulk-generated with the small model.
  5. **Trade finder rationale** — given my positional surpluses/deficits vs another team's inverse, propose 1–2 trades with reasoning (advisory only; the app never executes anything on ESPN).

---

## 8. Feature set (screens)

1. **Onboarding / Accounts** — add ESPN account (label + SWID + espn_s2, with the in-app cookie instructions from 2.3), account health badges, re-auth flow.
2. **Add leagues** — auto-discovery attempt per account with checkboxes, plus manual league-ID/URL entry. Show detected settings (size, PPR, slots) on add.
3. **Portfolio Board (home)** — the signature screen. Every one of my teams as a row on a tiered board (Tier 1 Advantaged / Tier 2 Neutral / Tier 3 Disadvantaged, gold tier dividers), columns: league name + size pill, account label, record W-L-T, PF/PA, standing, Edge Score as a green value chip, grade pill, playoff odds, last synced. Right rail: portfolio summary (X of Y advantaged, aggregate record, best/worst edge, sync-all button) — styled like the reference screenshot's "My Team" panel.
4. **League detail** — tabs: Overview (standings + Edge breakdown with per-component bars), Draft Board (full grid, my picks highlighted, value deltas color-coded, per-team AI recap cards), Teams (rosters + strength), Matchups (weekly results, all-play table, luck chart), Activity (transactions feed with per-team activity counts), AI Brief (difficulty narrative + exploit plan + advantage verdict).
5. **Multi-league player exposure** — which players I roster across leagues and total exposure share (FantasyPros-style multi-league assistant); flag players I face most often.
6. **Exports** — one-click CSV per table, a master `portfolio.json`, and an `.xlsx` workbook (one sheet per league + a portfolio summary sheet). These must match the DB exactly — this feeds a personal Excel/CSV/JSON pipeline downstream.
7. **Settings** — season, sync schedule, edge weights (advanced), Anthropic key status, FFC attribution notice.

---

## 9. Design system — match the reference screenshot

Dark draft-board aesthetic: near-black navy surfaces, one saturated red for primary actions, luminous green mono numerals for value, colored position pills, gold tier breaks. Dense, information-forward, zero decoration that isn't data.

### 9.1 Tokens (CSS variables)

```css
--bg-page:#070B14;      --bg-header:#04070D;   --bg-panel:#0C1120;
--bg-row:#0A0F1B;       --bg-row-hover:#111828;
--border:#1B2233;       --border-dashed:#232C42;
--text-primary:#E7ECF4; --text-secondary:#8B93A7; --text-muted:#5B6478;

--red:#E5484D; --red-hover:#F2555A; --red-outline:rgba(229,72,77,.35);
--green-text:#86EFAC; --green-chip-bg:#12241A; --green-bar:#26331F;

--tier-gold:#D9A62E;
--grade-a:#4ADE80; --grade-b:#38BDF8; --grade-c:#FBBF24; --grade-df:#F87171;

--pos-qb:#F472B6; --pos-rb:#2DD4BF; --pos-wr:#60A5FA;
--pos-te:#FBBF24; --pos-dst:#A78BFA; --pos-k:#94A3B8;
```

Typography: **Inter** for UI text; **JetBrains Mono** (or `font-variant-numeric: tabular-nums`) for every number in a table, chip, or stat. Numbers are the product — they must align.

### 9.2 Component anatomy (mirror the screenshot)

- **Data rows**: ~64px tall, 40px circular avatar/logo, bold name, position pill (colored bg at 15% opacity + colored text) beside a muted team/league abbrev, thin 3px left-edge accent strip in the position/verdict color, hairline `--border` row separators, `--bg-row-hover` on hover.
- **Dual-value cells**: primary mono number stacked over a smaller secondary (e.g., Edge Score over last-week delta; rank over ADP) inside a `--green-chip-bg` rounded chip with `--green-text`.
- **Value bars**: horizontal fill bars in `--green-bar` behind mono numerals (the 680/662/656 pattern in the reference).
- **Grade pills**: rounded-md, mono, colored per `--grade-*` scale on 12% opacity backgrounds.
- **Tier dividers**: 1px `--tier-gold` rule with an uppercase, letter-spaced `TIER n — ADVANTAGED` label.
- **Buttons**: primary = solid `--red` (hover `--red-hover`); secondary = ghost `--bg-panel` with `--border`; destructive-ish = red outline (the "Reset Draft" pattern).
- **Control bar**: sticky under the header — pill toggle with icon (like "Dynamic ⚡"), tab filters (ALL / QB / RB / WR / TE / DST / K equivalent → ALL / ADVANTAGED / NEUTRAL / DISADVANTAGED / BY ACCOUNT), rounded search input, accent checkbox ("Hide synced-errors" style), Settings + CSV buttons top-right.
- **Right rail**: fixed ~320px panel, section header with count badge, dashed-border "Empty" slot rows, red count pill ("15 left" pattern → "3 of 8 advantaged").

Quality floor: responsive to laptop widths, visible keyboard focus rings (red outline token), `prefers-reduced-motion` respected, no animation beyond 150ms hover/opacity transitions.

---

## 10. Build phases & acceptance criteria

**Phase 0 — Scaffold.** Monorepo, docker-compose (api + web), env handling, CLAUDE.md from Appendix A, lint/format/test wiring, empty FastAPI + Vite apps talking to each other. ✅ AC: `make dev` serves both; `GET /api/health` returns season + db path.

**Phase 1 — ESPN client, accounts, sync.** Cookie vault (Fernet), add-account + add-league APIs, discovery attempt + manual fallback, full `sync_league` pipeline into SQLite, raw-response cache, and a CLI: `python -m api.verify --league <id>`. ✅ AC: verify CLI prints league name, size, scoring type, standings, my team, draft-pick count for a real league and the numbers match the ESPN UI; unit tests run against recorded JSON fixtures (record one public league's responses into `tests/fixtures/`); a private league syncs with cookies; a bad cookie flips the account to `needs_reauth` without crashing.

**Phase 2 — Portfolio Board + League detail UI.** Design tokens implemented, portfolio board with tiering + right rail, league detail tabs fed entirely from the DB. ✅ AC: zero hardcoded data; screenshot-comparison against Section 9 tokens; table sorting/filtering works; pre-draft leagues render sensible empty states.

**Phase 3 — Analytics engine.** Optimal-lineup solver (respecting each league's slot map incl. FLEX), lineup efficiency, all-play, luck delta, activity + abandoned detection, draft surplus with the value curve, LeagueSoftness + MyEdge + Edge Score with preseason fallbacks, metrics persisted. ✅ AC: every formula in Section 6 has a unit test with a hand-computed fixture; recomputing metrics is idempotent; edge weights configurable in one file.

**Phase 4 — AI layer.** Anthropic SDK wiring, all five report kinds with schemas + pydantic validation, caching by input hash, regenerate flow, UI cards. ✅ AC: reports validate against schemas; app fully functional with no API key; a full-league draft recap run (all teams) completes and caches.

**Phase 5 — Playoff odds, exports, polish.** Monte Carlo sim, CSV/XLSX/master-JSON exports, empty/error/loading states, README with cookie walkthrough. ✅ AC: exports open clean in Excel; sim respects league playoff settings; fresh-clone-to-running takes ≤3 commands.

---

## 11. Guardrails — do NOT

- Do **not** attempt ESPN username/password login, headless-browser login, or captcha workarounds. Cookies only.
- Do **not** scrape ESPN HTML pages. JSON API only.
- Do **not** call ESPN from the React frontend (CORS + cookie exposure). All ESPN traffic goes through the FastAPI backend.
- Do **not** hardcode the season, the base host in multiple places, or any league ID.
- Do **not** log, print, or commit `espn_s2`/`SWID` values; redact them in error messages.
- Do **not** build write operations against ESPN (lineup changes, waivers). Read-only, always.
- Do **not** invent stats in AI prompts or let AI output feed back into metrics.
- Do **not** substitute paid data APIs; the two free sources in 2.9 are sufficient.

---

## 12. Verification & testing strategy

- **Fixtures first**: record real JSON (one public league + sanitized private league) into `tests/fixtures/`; all parsing/metric tests run offline against them.
- **Live smoke test**: `make verify LEAGUE=<id>` hits ESPN once and diffs headline numbers (record, PF, standing) against expectations you confirm by eye in the ESPN UI.
- **Metric truth tables**: for a 4-team toy fixture, hand-compute optimal lineups, all-play records, and draft surplus in comments next to the asserted values.
- **Schema drift alarm**: the sync pipeline validates required fields per view and logs a single warning line (view name + missing path) if ESPN's shape changes, rather than crashing mid-sync.

---

## Appendix A — CLAUDE.md seed, env, layout

**CLAUDE.md** (copy into repo root):

```md
# ESPN Edge — project rules
- SPEC.md is the source of truth; Section 1/2 (ESPN access) may not be changed without asking.
- ESPN traffic: backend only, lm-api-reads host, cookies from the accounts table, 1 rps throttle, cache raw JSON.
- Never print/log/commit SWID or espn_s2. Fernet-encrypt at rest.
- Read-only against ESPN. No login automation. No HTML scraping.
- Every UI number comes from the DB (`metrics` or raw tables). No math in components.
- Tests run offline on fixtures in tests/fixtures/. Run `make test` before declaring a phase done.
- Design tokens in Section 9 are canonical — no ad-hoc colors.
```

**.env.example**:

```
SEASON=2026
FERNET_KEY=            # generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ANTHROPIC_API_KEY=     # optional — AI panels disabled if empty
DB_PATH=./data/edge.db
SYNC_CRON=0 5 * * *
```

**Layout**:

```
/api        FastAPI app: routers/, services/ (espn, sync, metrics, ai, export), models/, verify.py
/web        Vite + React + TS: src/components, src/pages, src/tokens.css
/data       edge.db, raw_cache/
/tests      fixtures/, unit tests
docker-compose.yml  Makefile  SPEC.md  CLAUDE.md  README.md
```

## Appendix B — Reference links (for Claude Code to consult, not to copy from)

- espn-api (Python, primary client): https://github.com/cwendt94/espn-api — wiki has per-sport docs
- ffscrapr ESPN endpoint guide (views + X-Fantasy-Filter): https://ffscrapr.ffverse.com/articles/espn_getendpoint.html
- Cookie how-to discussion (incl. Chrome extension): https://github.com/cwendt94/espn-api/discussions/150
- Community endpoint gist (views, players endpoints, stat IDs): https://gist.github.com/nntrn/ee26cb2a0716de0947a0a4e9a157bc1c
- FFC ADP REST API help article: https://help.fantasyfootballcalculator.com/article/42-adp-rest-api
- Anthropic API docs (verify models/format): https://docs.claude.com/en/api/overview

*Unofficial-API disclaimer: ESPN's fantasy API is undocumented and unsupported; this tool is for personal use with your own accounts' data. Expect occasional breakage and design for it (raw cache, schema-drift warnings, single-constant base URL).*
