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
  Phase 11 v1 (preseason edge blends roster projection + draft surplus),
  Phase 12 v1 (all-play record + luck delta),
  Phase 13 v1 (lineup efficiency foundation),
  Phase 14 v1 (MyEdge component foundation),
  Phase 15 v1 (LeagueSoftness component foundation),
  Phase 16 v1 (full Edge Index composite),
  Phase 17 v1 (Portfolio Board Edge Index transition),
  Phase 18 v1 (AI grounding uses the Edge Index model),
  Phase 19 v1 (AI weekly recap grounded on all-play, luck & waivers),
  Phase 20 v1 (weekly recap panel in the AI Brief),
  Phase 21 v1 (Trade Finder panel in the AI Brief) implemented.
- Phase 21: trade_finder is now per-opponent + in the UI. routers/ai.py adds GET
  /api/leagues/{id}/ai/trade-finder?opponent_team_id=N and POST stores
  extra={"opponent_team_id","opponent_name"} in content_json; AiService.latest_for_opponent
  (shares _latest_where_content with latest_for_week) filters by content_json.opponent_team_id
  so GET/POST never return another opponent's report and never a legacy/unscoped one.
  _valid_opponent rejects self/cross-league/unknown ids (error envelope, no generation).
  Legacy-cache edge case handled: POST reuses only a report already tagged for this opponent
  with a matching input_hash, else generate(force=True, extra=...) (skips the hash-only cache
  so a legacy row sharing the hash can't be served; stores a tagged report). Frontend AI Brief
  adds a TradeFinderCard (opponent picker from getLeagueTeams excluding is_me; clears prior
  opponent's result while loading; renders give/get/rationale + advisory note; v1 draft-time
  grounding caveat in copy). No DB/schema/SCHEMA_VERSION change, no grounding/facts/prompt/
  ai_schemas change; production AnthropicLlmClient path intact; tests offline (fake LLM; new
  Playwright AI-enabled trade-finder). Advisory only — never executes trades. Contract:
  docs/phase-21-trade-finder-ui.md.
- Phase 20: weekly recap is now per-week + in the UI. routers/ai.py adds GET
  /api/leagues/{id}/ai/weekly-recap?week=N and POST passes extra={"week":week} so the week is
  stored in content_json; AiService.latest_for_week filters by content_json.week so GET/POST
  never return another week's recap (plain latest(kind) ignored week). Per-week caching already
  works via the facts input_hash; per-week stale checks that week's fresh facts. Frontend AI
  Brief adds a WeeklyRecapCard (completed weeks derived from matchups; Generate/Regenerate;
  renders headline/body/luck_notes/waiver_highlights). No DB/schema/SCHEMA_VERSION change, no
  ai_inputs/ai_schemas/metrics change; production AnthropicLlmClient path intact (tests offline,
  fake LLM; new Playwright test mocks AI-enabled + weekly-recap). Contract:
  docs/phase-20-weekly-recap-ui.md.
- Phase 19: ai_inputs.weekly_recap_input now grounds the recap — matchups gain winner/loser/
  margin/tie; week_all_play (reuses pure metrics.compute_all_play on the week's scores);
  season_all_play + luck_delta (read_all_play); transactions for that week with player_in/out
  names resolved from Player (empty feed → [], never invented). ai.py _TASK["weekly_recap"]
  writes luck_notes only from all-play/luck facts and waiver_highlights only from transactions,
  calling a quiet week when empty. ai_config SCHEMA_VERSION v2→v3 busts cached weekly recaps.
  WeeklyRecap OUTPUT schema unchanged; no metric/DB/provider change; production still uses real
  AnthropicLlmClient when the key is set (tests offline, fake LLM). Manual real-AI smoke:
  POST /api/leagues/{id}/ai/weekly-recap?week={w}&force=true (docs/phase-19-...md; may cost API
  usage). Contract: docs/phase-19-ai-weekly-recap-grounding.md.
- Phase 18: ai_inputs.advantage_verdict_input + league_brief_input regrounded on Edge Index —
  verdict facts carry edge_index_score/grade/verdict + components, my_edge_score + components,
  league_softness_score + components, playoff_odds, and legacy_edge_score/grade/verdict
  (renamed from edge_score; no bare edge_score key). Brief carries per-team edge_index_score/
  verdict + legacy_edge_score (lean; no per-team component arrays). ai.py _TASK prompts for
  league_brief + advantage_verdict lead with Edge Index, legacy is secondary. ai_config
  SCHEMA_VERSION v1→v2 busts cached ai_reports (prompt text isn't in input_hash; facts changes
  already bust it). No metric/schema/provider/model-id change; AI still optional (no key →
  enabled:false); tests offline (fake LLM). Contract: docs/phase-18-ai-edge-index-grounding.md.
- Phase 17: Portfolio Board + summary + exports now treat Phase 16 edge_index_score as the
  PRIMARY advantage score; legacy Phase 3 edge_score kept alongside. PortfolioSummary gains
  edge_index_scored_count/edge_index_advantaged_count/best_edge_index_score/
  worst_edge_index_score (build_summary), legacy scored_count/advantaged_count/best/worst_
  edge_score unchanged. Board tiers/filters/accent/chip use edge_index_verdict/score/grade
  (null → pending, no React fallback); legacy edge_score shown as a small secondary; right
  rail driven by Edge Index with legacy clearly labeled. exports _ROW_FIELDS gains
  edge_index_score/grade/verdict (CSV+XLSX; JSON already had them via the row model). NO
  metric computation change (edge_score/edge_index_score/grade_for/verdict_for/sync/schema
  untouched; 82.5 byte-identical). Contract: docs/phase-17-portfolio-edge-index-transition.md.
- Phase 16: metrics.py compute_edge_index_row = 0.5*my_edge_score + 0.5*league_softness_score
  (SPEC §6), a SEPARATE composite from the persisted Phase 14/15 sub-scores. Component values
  are the already-0–100 sub-scores (not percentiles); only present halves count, weights
  renormalize (one half → 1.0), neither → pending. grade/verdict via existing grade_for/
  verdict_for. Persisted team metrics (week NULL): edge_index_score + edge_index_component_
  my_edge/league_softness, cleared when unavailable. read_edge_index + team_edge_index; GET
  /api/leagues/{id}/edge-index (ordered by score desc, grade/verdict, is_me). PortfolioRow
  gains edge_index_score/grade/verdict (board still shows edge_score; summary unchanged).
  Overview shows an Edge Index v1 panel above MyEdge/LeagueSoftness. Does NOT change
  edge_score/grade/verdict/playoff_odds or Phase 9–15 semantics (82.5 byte-identical). No
  schema change, no db-reset. Contract: docs/phase-16-edge-index-composite.md.
- Phase 15: metrics.py compute_league_softness is a SEPARATE per-team score (how exploitable
  a team's opponents are, SPEC §6.1): within-league percentiles of opponent_lineup_inefficiency
  (1-median opp lineup_efficiency), opponent_draft_indiscipline (-median opp draft_surplus),
  exploitable_weakness_share (frac opponents PF<median-1SD, played only), abandoned_proxy
  (frac opponents autodrafted), opponent_inactivity (-median opp txn count; ONLY when the
  league has transaction rows — an empty feed is unknown, not zero, via
  _transaction_counts_by_team). Equal base weights (edge_config LEAGUE_SOFTNESS_WEIGHTS);
  a component needs >=2 teams with distinct raw values; weights renormalize across present.
  Persisted team metrics (week NULL): league_softness_score + league_softness_component_<name>
  (percentiles), cleared when unavailable. read_league_softness + GET
  /api/leagues/{id}/league-softness (ordered by score desc, includes is_me). Overview shows a
  LeagueSoftness v1 panel beside MyEdge. Does NOT change edge_score or Phase 9/11/12/13/14
  semantics (82.5 byte-identical). No schema change, no db-reset. Contract:
  docs/phase-15-league-softness-foundation.md.
- Phase 14: metrics.py compute_my_edge blends within-league percentiles of roster_strength
  (roster proj, gated on projections_fresh), draft_surplus, lineup_efficiency, and
  luck_adjusted_record (all_play_win_pct) into a SEPARATE my_edge_score (SPEC §6.2 weights in
  edge_config MY_EDGE_WEIGHTS; waiver_capture 0.10 pending → weights renormalize across present
  components, ≥2-team availability). Persisted team metrics (week NULL): my_edge_score +
  my_edge_component_<name> (percentiles), cleared when inputs unavailable. read_my_edge + GET
  /api/leagues/{id}/my-edge (ordered by score desc, includes is_me). Overview shows a MyEdge v1
  panel for my team. Does NOT change edge_score/grade/verdict/playoff_odds or Phase 9/11/12/13
  semantics (82.5 byte-identical). No schema change, no db-reset. Contract:
  docs/phase-14-my-edge-foundation.md.
- Phase 13: metrics.py optimal_lineup_points (pure solver: QB/RB/WR/TE/FLEX(RB-WR-TE)/K/DST,
  respects leagues.lineup_slots_json via _starting_slot_counts, ignores bench/IR/IDP, never
  places unknown positions); compute_lineup_week (started/optimal/bench/efficiency, pending
  if optimal<=0); compute_lineup_efficiency aggregates per team season (efficiency is
  points-weighted Σstarted/Σoptimal; *_avg are per-week means). Reads LineupSlot joined to
  Player.position for completed weeks. Persisted team metrics (week NULL): lineup_efficiency,
  started_points_avg, optimal_points_avg, points_left_on_bench_avg; cleared when no sample.
  read_lineup_efficiency + GET /api/leagues/{id}/lineup-efficiency (ordered by efficiency
  desc). Teams tab shows a Lineup efficiency table. NOT in edge_score (unchanged, 82.5
  byte-identical). No schema change, no db-reset. Contract: docs/phase-13-lineup-efficiency.md.
- Phase 12: metrics.py compute_all_play (pure) scores each team vs every other scored team
  each completed regular-season week (playoffs ignored, weeks with <2 scored teams skipped);
  luck_delta = all_play_win_pct - actual_win_pct. Completed weeks from sync's completed_weeks;
  completed_weeks=None falls back to the "either side scored >0" heuristic. Persisted as team
  metrics (week NULL): all_play_wins/losses/ties/all_play_win_pct/luck_delta, cleared when no
  completed sample. read_all_play + GET /api/leagues/{id}/all-play (ordered by AP win% desc,
  AllPlayOut). Matchups tab shows an All-play & luck table (placeholder removed). NOT in
  edge_score (unchanged, 82.5 byte-identical). No schema change, no db-reset. Contract:
  docs/phase-12-all-play-luck.md.
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
