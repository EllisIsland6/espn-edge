# Phase 8 — beta readiness polish (contract)

Make the app easy and trustworthy to run as a private beta against a **real** ESPN
league, without touching the analytics core. Continues the Phase 6 (CI + smoke) → Phase 7
(re-auth + sync diagnostics) reliability arc with an operator-facing "is everything
healthy?" surface, clearer fresh-start guidance, and a repeatable manual live-smoke loop.

## Scope (v1)
1. **Read-only System Status page** — a single `/status` screen that composes data the API
   already exposes (`/api/health`, `/api/ai/status`, `/api/accounts`, `/api/portfolio`) into
   one health-at-a-glance view. No new math, no ESPN calls, no secrets.
2. **Fresh-start UX copy** — zero-state guidance on the Portfolio Board and Manage tab that
   walks a first-time user through add account → add league → sync. Copy-only.
3. **Live-smoke runbook** — `docs/live-smoke.md`: an end-to-end checklist for validating one
   real ESPN league by eye against the ESPN UI, including the existing `api.verify` CLI, the
   re-auth path, and export sanity — with a hard "never paste real cookies anywhere" reminder.

## Non-goals
- **Analytics depth** — no changes to `metrics.py`, `edge_config.py`, or `playoff_sim.py`;
  the fuller SPEC §6 Edge Index (optimal-lineup solver, all-play, luck, softness/MyEdge
  components) stays future work.
- **Sync/auth/ESPN behavior** — no changes to the sync pipeline, cookie handling, or the
  ESPN client; the Status page only *reads* existing endpoints.
- **DB / schema changes** — no new columns, no migration, no `make db-reset` required.
- **New ESPN network calls** from the status surface.
- Desktop packaging / release tooling, multi-league player-exposure screen, scheduler wiring.

## Acceptance criteria
- `GET /status` renders (client-composed) from `/api/health`, `/api/ai/status`,
  `/api/accounts`, `/api/portfolio` and shows: API health status, season, DB path, AI
  enabled/disabled + model names, account count + `needs_reauth` count, league count,
  last-sync-failed count (portfolio rows with `last_sync_ok === false`), export endpoint
  availability (CSV/JSON/XLSX), a short beta/fresh-start checklist, and the FFC/ESPN ADP
  attribution notice (SPEC §2.9). **No SWID/espn_s2/cookies/secrets are ever rendered.**
- A `/status` route and a nav entry exist; the page degrades gracefully while loading and
  on fetch error.
- Portfolio and Manage empty states clearly guide the add-account → add-league → sync path
  and surface the in-app cookie steps. No behavior changes.
- `docs/live-smoke.md` exists and is linked from the README; it never instructs pasting real
  cookies into logs/commits/screenshots.
- All gates pass, including a new mocked Playwright test asserting the Status page renders
  the health/AI/account/league/failed-sync counts and that known fake secret strings do not
  appear in the DOM.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Analytics depth / Edge component breakdown (SPEC §6) — the next high-value phase.
- Scheduler wiring (APScheduler nightly + in-season cadence, SPEC §5).
- Multi-league player-exposure screen (SPEC §8.5).
- Full Settings screen with editable edge weights + sync schedule (SPEC §8.7).
- Desktop packaging / release workflow.
