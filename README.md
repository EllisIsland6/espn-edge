# ESPN Edge

A locally-run web app that connects to all your ESPN fantasy football leagues across
multiple ESPN accounts and answers one question with data: **"Am I an advantaged
player in each league — and across my portfolio?"** See [SPEC.md](./SPEC.md) for the
full design; ESPN data access is Section 2 (the verified technical foundation).

> Status: **Phases 0–9 v1 complete** — scaffold, ESPN sync pipeline, Portfolio Board +
> League detail UI, deterministic Edge analytics, the optional backend-only AI layer,
> Monte Carlo playoff odds + exports, CI + Playwright smoke suite, Phase 7 private-beta
> reliability (re-auth + sync diagnostics), Phase 8 beta-readiness polish (read-only
> System Status page + a [live-smoke runbook](docs/live-smoke.md)), and **Phase 9 Edge
> Score [component breakdown](docs/phase-9-edge-components.md) in League detail, and
> Phase 10 [draft value foundation](docs/phase-10-draft-value.md) (persisted ADP /
> value delta + a draft-surplus metric; Draft Board shows player names), and **Phase 11
> [preseason Edge](docs/phase-11-preseason-edge.md) blending roster projection + draft
> surplus for drafted/no-games leagues, Phase 12 [all-play + luck
> metrics](docs/phase-12-all-play-luck.md) in the Matchups tab, and **Phase 13 [lineup
> efficiency](docs/phase-13-lineup-efficiency.md) (started vs optimal) in the Teams tab.**

## Quick start

Fresh clone to running in **two commands** (`setup` installs api + web deps and writes
`.env` with a generated `FERNET_KEY`):

```bash
make setup   # venv + Python deps, npm install, bootstrap .env (once)
make dev     # api on :8000, web on :5173, both hot-reload
```

Prefer the explicit steps? `make install` + `make install-web`, then
`cp .env.example .env` and set `FERNET_KEY` (the cookie-encryption key):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`GET http://127.0.0.1:8000/api/health` returns the season + DB path. The web app at
`http://127.0.0.1:5173` is the **Portfolio Board** (your teams tiered by Edge Index,
with a portfolio summary rail) and per-league **detail pages** (Overview, Draft Board,
Teams, Matchups, Activity, AI Brief). Add accounts/leagues on the **Manage** tab. The
**Status** tab is a read-only health check (API/AI status, account + league counts,
re-auth / failed-sync counts, export links) — no ESPN calls, no secrets shown.

Docker parity is available (`docker compose up`) but not required — `make dev` runs the
same two services natively.

## Connecting a private league (cookies)

ESPN has no official public API. Private leagues authenticate with two cookies from a
logged-in browser session — **cookies only; no username/password automation** (SPEC 2.3).

1. Log in at fantasy.espn.com in Chrome.
2. DevTools → Application → Cookies → `https://fantasy.espn.com`.
3. Copy `SWID` (keep the curly braces, e.g. `{ABC123-...}`) and `espn_s2` (a long string).
4. Add the account:

```bash
curl -X POST http://127.0.0.1:8000/api/accounts \
  -H 'Content-Type: application/json' \
  -d '{"label":"Main","swid":"{...}","espn_s2":"..."}'
```

`espn_s2` is Fernet-encrypted at rest; `SWID`/`espn_s2` are never logged or returned by
the API. On a bad/expired cookie the owning account flips to `needs_reauth` rather than
crashing. Public leagues need no account.

### Re-authenticating an expired account (Phase 7)

When cookies expire, the account shows `needs_reauth`. Paste fresh cookies to resume
syncing — no need to delete and re-add the account (which is blocked while leagues are
linked). In the UI, the Manage tab auto-opens a **Re-auth** form on any `needs_reauth`
account. From the API:

```bash
curl -X POST http://127.0.0.1:8000/api/accounts/{id}/reauth \
  -H 'Content-Type: application/json' \
  -d '{"swid":"{...}","espn_s2":"..."}'
```

The SWID is normalized, `espn_s2` re-encrypted, and the account returns to `active`. The
response is a plain `AccountOut` — it never echoes the cookies back.

Each league also stores its **last sync outcome** (`last_sync_ok` / a redacted
`last_sync_error`), so a failed sync stays visible in Manage and on the League detail page
after the transient toast fades. Diagnostics are sanitized and never contain cookies.

## Adding & syncing a league

```bash
# Manual add (always works): a bare league id or a full league URL.
curl -X POST http://127.0.0.1:8000/api/leagues \
  -H 'Content-Type: application/json' \
  -d '{"league_ref":"https://fantasy.espn.com/football/team?leagueId=123456","account_id":1}'

# Auto-discover leagues on an account (best-effort fan-profile API):
curl http://127.0.0.1:8000/api/leagues/discover/1

# Sync a league (id from the add/list response):
curl -X POST http://127.0.0.1:8000/api/leagues/1/sync
```

## Verify against a real league (live smoke test)

Prints league name, size, scoring type, standings, my team, and draft-pick count so you
can eyeball them against the ESPN UI (SPEC Phase 1 AC):

It tries **public** access first; for a private league it falls back to cookies
from `ESPN_SWID`/`ESPN_S2` in `.env` (or a hidden prompt) — **never** passed as
command-line args. `--cross-check` also loads the league via `espn-api` and diffs
headline numbers.

```bash
make verify LEAGUE=123456                          # public, or private via .env cookies
make verify LEAGUE=123456 SEASON=2025 CROSSCHECK=1 # diff against espn-api
# or directly:
python -m api.verify --league 123456 --season 2026 --cross-check --label main
```

`--label` names the account the cookies are stored under (default `main`).

For a full end-to-end beta walkthrough (add account → sync → diff vs ESPN → re-auth →
exports), follow the **[live-smoke runbook](docs/live-smoke.md)**. It includes the hard
rule: never paste real cookies into logs, commits, screenshots, or bug reports.

## Tests & quality gates

All backend tests run **offline** against recorded fixtures in `tests/fixtures/` (no
network, no cookies). The frontend has a small **Playwright** smoke suite that mocks the
`/api/*` layer (no backend, no ESPN). The full gate set (enforced by CI on every push/PR
— `.github/workflows/ci.yml`):

```bash
# backend
make test    # pytest (or: .venv/bin/python -m pytest -p no:cacheprovider)
make lint    # ruff check api tests
# frontend (from web/)
npm run lint          # tsc
npm run build         # vite build
npm run e2e:install   # one-time: fetch the Playwright Chromium browser
npm run e2e           # Playwright smoke suite
```

Beta-hardening scope (CI + smoke tests + robustness audit):
**[docs/phase-6-beta-hardening.md](docs/phase-6-beta-hardening.md)**. Private-beta
reliability (re-auth + sync diagnostics):
**[docs/phase-7-beta-reliability.md](docs/phase-7-beta-reliability.md)**. Beta-readiness
polish (Status page + live-smoke runbook):
**[docs/phase-8-beta-readiness.md](docs/phase-8-beta-readiness.md)**. Edge Score
component breakdown: **[docs/phase-9-edge-components.md](docs/phase-9-edge-components.md)**.

## Schema changes & reset

This is a local, single-user, fully re-syncable app, so there is **no migration
tool** (no Alembic). The SQLite schema — including the uniqueness constraints and
foreign keys in `api/models.py` — is created by `create_all` on startup. When the
schema changes, drop and rebuild rather than migrate:

```bash
make db-reset     # deletes data/edge.db (+ WAL/SHM)
# then re-add accounts/leagues and re-sync — all source data lives on ESPN.
```

> Phase 7 added two nullable `leagues` columns (`last_sync_ok`, `last_sync_error`).
> Upgrading an existing DB requires a `make db-reset` before the first run.

Foreign keys are enforced (`PRAGMA foreign_keys=ON` per connection) and child rows
cascade on delete, so deleting a league removes its teams/picks/matchups/etc. The
raw JSON cache in `data/raw_cache/`/`raw_cache` table makes re-syncing cheap.

## Analytics (Edge metrics)

`edge_score`, `grade`, `verdict`, and `playoff_odds` are computed by the deterministic
Phase 3 v1 engine (`api/services/metrics.py`, tunables in `api/edge_config.py`) and
persisted in the `metrics` table. They **recompute automatically on every sync**, so to
refresh them just re-sync a league ("Sync"/"Sync all" in the UI, `POST
/api/leagues/{id}/sync`, or `make verify`). Pending metrics (e.g. a pre-draft league)
are represented as absence and surface as `null`. Full contract, formulas, and the
v1-vs-future split: **[docs/phase-3-analytics.md](docs/phase-3-analytics.md)**.

League detail → Overview shows the **component breakdown** behind `edge_score` — the
weighted within-league percentiles (win %, points for, point differential, or roster
projection) that the score is the mean of, rendered as bars. See
**[docs/phase-9-edge-components.md](docs/phase-9-edge-components.md)**.

The **Draft Board** persists each pick's `adp_at_draft` and `value_delta` (ADP − overall)
at sync from the ESPN player pool, and stores a per-team `draft_surplus` metric from the
pick-value curve `v(p)=100·e^(−p/34)`. See
**[docs/phase-10-draft-value.md](docs/phase-10-draft-value.md)**.

For **drafted / no-games (preseason) leagues**, `edge_score` now blends the within-league
percentiles of **roster projection** (weight ≈0.58) and **draft surplus** (≈0.42), shown as
components in the Overview. Roster-only leagues are unchanged, and in-season/complete
scoring is byte-identical. Still a within-league v1, not the cross-league SPEC §6 Index.
See **[docs/phase-11-preseason-edge.md](docs/phase-11-preseason-edge.md)**.

The **Matchups** tab shows each team's **all-play** record (scored against every other team
every completed week) and **luck delta** (`all-play win% − actual win%`), served by
`GET /api/leagues/{id}/all-play`. It's the foundation for SPEC §6.2 luck-adjusted record and
is **not** folded into `edge_score` yet. See
**[docs/phase-12-all-play-luck.md](docs/phase-12-all-play-luck.md)**.

The **Teams** tab shows **lineup efficiency** — started points ÷ the best legal lineup from
that week's roster (an optimal-lineup solver over the ESPN slot map), with points left on
bench — served by `GET /api/leagues/{id}/lineup-efficiency`. Foundation for SPEC §6.1/§6.2
lineup metrics; **not** in `edge_score` yet. See
**[docs/phase-13-lineup-efficiency.md](docs/phase-13-lineup-efficiency.md)**.

## AI analysis (optional)

The AI layer (draft recaps, league difficulty brief, advantage verdict, weekly recap,
trade finder) is **backend-only and additive** — the app is fully functional without it.
Set `ANTHROPIC_API_KEY` in `.env` to enable it; with no key, AI panels show a "connect a
key" empty state and the endpoints return `enabled:false` (never an error). Reports are
grounded on DB facts only, validated against pydantic schemas, and cached by input hash
(with a Regenerate action). Models are configured in `api/ai_config.py` (standard =
`claude-sonnet-5`, bulk = `claude-haiku-4-5`). Full contract:
**[docs/phase-4-ai.md](docs/phase-4-ai.md)**.

## Exports

One-click portfolio exports from the board's **Export** control (CSV · JSON · XLSX), or
directly:

```bash
curl -OJ http://127.0.0.1:8000/api/exports/portfolio.csv    # one row per league
curl -OJ http://127.0.0.1:8000/api/exports/portfolio.json   # summary + rows + per-league detail
curl -OJ http://127.0.0.1:8000/api/exports/portfolio.xlsx   # Portfolio sheet + one sheet per league
```

Exports reflect DB/API data exactly (no frontend recomputation) and feed a downstream
Excel/CSV/JSON pipeline. Playoff odds in the exports come from the Phase 5 Monte Carlo
simulation. Contract: **[docs/phase-5-playoff-exports.md](docs/phase-5-playoff-exports.md)**.

## Layout

```
/api    FastAPI: config, db, models, crypto, edge_config, ai_config, ai_schemas,
        routers/, services/ (espn, sync, metrics, ai, exports, …), verify.py
/web    Vite + React + TS + Tailwind: src/pages (PortfolioBoard, LeagueDetail, Manage),
        src/components, src/api.ts, src/tokens.css
/data   edge.db + raw_cache/   (gitignored — holds session cookies)
/docs   phase-3-analytics, phase-4-ai, phase-5-playoff-exports (contracts)
/tests  fixtures/ + offline unit tests
```

*Unofficial-API disclaimer: ESPN's fantasy API is undocumented and unsupported; this tool
is for personal use with your own accounts' data. Expect occasional breakage.*
