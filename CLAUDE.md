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
  Phase 7 v1 (private-beta reliability: account re-auth + persistent sync diagnostics),
  Phase 8 v1 (beta-readiness polish: read-only Status page + live-smoke runbook),
  Phase 9 v1 (Edge Score component breakdown in League detail),
  Phase 10 v1 (draft value foundation),
  Phase 11 v1 (preseason edge blends roster projection + draft surplus) implemented.
- Phase 11: the preseason branch of compute_edge_components (drafted/no-games) now blends
  within-league percentiles of roster_proj and draft_surplus. TeamStat gains draft_surplus
  (fed from _draft_surplus_by_team in recompute). Base weights from SPEC §6.2 (0.35/0.25)
  renormalized to sum to 1.0 (DRAFTED_WEIGHTS in edge_config); per-team weights renormalize
  across the components PRESENT (a component needs ≥2 teams with values). Roster-only →
  weight 1.0 → byte-identical to Phase 9/10; both present → preseason edge_score changes.
  projections_fresh=False → pending + cleared. New component row edge_component_draft_surplus
  (percentile, not raw surplus); the raw draft_surplus metric is unchanged. In-season/
  complete scoring byte-identical (82.5 fixtures pass). No schema change, no db-reset.
  Overview exposes the Draft surplus component automatically. Contract:
  docs/phase-11-preseason-edge.md.
- Phase 10: sync stamps DraftPick.adp_at_draft (from the fresh kona_player_info pool) +
  value_delta=round(adp-overall,1) in a Step 5b (_stamp_draft_values), only when
  projections_fresh — a failed player fetch leaves the freshly-replaced picks null (never
  stale). Pure draft-surplus foundation in metrics.py: pick_value(p)=100·e^(-p/34)
  (DRAFT_VALUE_DECAY in edge_config), pick_surplus, compute_draft_surplus; persisted as a
  team metric key=draft_surplus (week NULL), cleared when no valid picks. NOT part of
  edge_score/grade/verdict/components (unchanged, byte-identical). AI draft facts read the
  persisted adp_at_draft/value_delta. /api/leagues/{id}/draft joins players → DraftPickOut
  gains player_name/player_position; Draft Board shows name/pos/ADP/Δ. No schema change, no
  db-reset. Contract: docs/phase-10-draft-value.md.
- Phase 9: edge_score is now derived from its within-league percentile components
  (compute_edge_components in metrics.py): in_season/complete → win_pct/points_for/
  point_diff (INSEASON_WEIGHTS); drafted/no-games → roster_proj (weight 1.0, fresh
  projections only). edge_score = round(Σ weight·percentile, 1) — byte-identical to
  Phase 3. Components persist as metrics rows key=edge_component_<name> (week NULL),
  upserted/cleared every recompute so no stale key survives a branch/pending change.
  Exposed as LeagueOverview.components (my team only) + rendered as bars in League
  detail Overview. No sync/parse/schema change, no db-reset. Contract:
  docs/phase-9-edge-components.md.
- Phase 8: read-only `/status` page (web/src/pages/Status.tsx, nav in Layout.tsx) composes
  existing endpoints (health, ai/status, accounts, portfolio) into a health view — no ESPN
  calls, no math, no secrets, no new backend endpoint or schema change. Fresh-start empty
  states on Portfolio/Manage guide add-account → add-league → sync. Live-smoke checklist:
  docs/live-smoke.md (referenced from README). Contract: docs/phase-8-beta-readiness.md.
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
