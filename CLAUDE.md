# ESPN Edge — project rules
- SPEC.md is the source of truth; Section 1/2 (ESPN access) may not be changed without asking.
- ESPN traffic: backend only, lm-api-reads host, cookies from the accounts table, 1 rps throttle, cache raw JSON.
- Never print/log/commit SWID or espn_s2. Fernet-encrypt at rest.
- Read-only against ESPN. No login automation. No HTML scraping.
- Every UI number comes from the DB (`metrics` or raw tables). No math in components.
- Tests run offline on fixtures in tests/fixtures/. Run `make test` before declaring a phase done.
- Quality gates (also enforced by CI, .github/workflows/ci.yml): backend `pytest` + `ruff check api tests`; frontend `npm run lint` + `npm run build` + `npm run e2e` (Playwright smoke, mocks /api — no ESPN). Browser: `npm run e2e:install` once.
- Design tokens in Section 9 are canonical — no ad-hoc colors.
- SPEC §2.8 amended 2026-07-07: raw httpx client is the workhorse (own parser); espn-api stays installed as a `--cross-check` diff in the verify CLI, not the primary read path.
- `SyncService` owns the `EspnService`/httpx.Client it creates: always use `with SyncService(session) as svc:` (or call `svc.close()`) in scheduler jobs, routes, and CLI so clients don't leak. Injected services (test fakes / shared clients) are never closed by it.
- UI metric fields (edge_score/grade/playoff_odds/verdict) stay null until Phase 3; the Phase 2 UI renders placeholders, never computes.

## Build status
- Phase 0 (scaffold), Phase 1 (ESPN client, accounts, sync), Phase 1.5 (hardening),
  Phase 2 (Portfolio Board + League detail UI), Phase 3 (deterministic Edge analytics),
  Phase 4 v1 (AI analysis layer), Phase 5 v1 (Monte Carlo playoff odds + exports),
  Phase 6 v1 (beta hardening: CI + Playwright smoke suite),
  Phase 7 v1 (private-beta reliability: account re-auth + persistent sync diagnostics) implemented.
- Phase 7: `POST /api/accounts/{id}/reauth` (body {swid, espn_s2}) normalizes SWID + re-encrypts
  espn_s2 + sets status=active; returns AccountOut, never echoes/logs the cookies (404 missing,
  400 empty SWID). `League.last_sync_ok`/`last_sync_error` (nullable) persist the last-sync
  outcome, written on every sync_league exit path (auth_failed / fetch_failed / partial / clean)
  via SyncService._record_diagnostics; SyncService._safe_error sanitizes+truncates so a
  diagnostic never carries cookies. Exposed in LeagueOut/PortfolioRow/LeagueOverview + TS types.
  The two new leagues columns need `make db-reset`. Contract: docs/phase-7-beta-reliability.md.
- Phase 5: playoff_odds is now a seeded Monte Carlo sim (api/services/playoff_sim.py;
  knobs in edge_config.py) replacing the Phase 3 heuristic — complete=1/0, in_season=sim,
  else pending. Exports: api/services/exports.py + routers/exports.py (CSV/JSON/XLSX,
  built from services/portfolio.py so board/summary/exports never drift). Contract:
  docs/phase-5-playoff-exports.md.
- Phase 3: api/services/metrics.py + api/edge_config.py; edge_score/grade/verdict/
  playoff_odds recompute on sync, persisted in metrics. Contract: docs/phase-3-analytics.md.
- Phase 4: backend-only Anthropic layer (api/services/ai.py, ai_inputs.py, ai_schemas.py,
  ai_config.py, routers/ai.py). Works with NO ANTHROPIC_API_KEY (endpoints return
  enabled:false, never 500). Structured output validated with pydantic, cached by
  input_hash in ai_reports, grounded on DB facts only. Models: standard=claude-sonnet-5,
  bulk=claude-haiku-4-5 (ai_config.py). Contract: docs/phase-4-ai.md. Never log/return the key.
- Web stack: React+Vite+TS, Tailwind v4 (tokens in src/tokens.css @theme), TanStack
  Table (src/components/DataTable.tsx), react-router. Pages: PortfolioBoard, LeagueDetail
  (tabs), Manage. All data from the read-only /api view endpoints; no math in components.
- `make dev` runs api (uvicorn) + web (vite) natively with hot reload; docker-compose is provided for parity but not required.
- Verify CLI: `python -m api.verify --league <id> [--season 2026] [--cross-check] [--label main]`. Public-first, falls back to cookies from ESPN_SWID/ESPN_S2 (.env) or hidden prompt — never argv.
- Layout: /api (FastAPI), /web (Vite+React+TS), /data (SQLite + raw_cache), /tests (fixtures + unit tests).
