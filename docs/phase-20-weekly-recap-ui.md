# Phase 20 — Weekly recap panel in the AI Brief (contract)

Wire the Phase 19 grounded weekly recap into the app UI, with **correct per-week retrieval
and caching** and **no DB schema change**. Automated tests stay fully offline (fake LLM); the
real model path is used only in production when `ANTHROPIC_API_KEY` is set.

## Scope (v1)
1. **Backend — per-week GET/POST:**
   - `GET /api/leagues/{id}/ai/weekly-recap?week=N` returns the stored recap for **week N**
     (or `content:null`), with `stale` measured against **that week's** current facts.
   - `POST /api/leagues/{id}/ai/weekly-recap?week=N&force=...` generates and stores, passing
     `extra={"week": week}` so the week is persisted in `content_json`.
   - New `AiService.latest_for_week(league_id, kind, week)` scans `all_reports` (newest-first)
     for the first whose `content_json["week"] == week`. GET/POST use it — **never** the
     generic `latest(kind)`, which ignores week and would return another week's recap.
   - Per-week caching already works via the facts `input_hash` (facts carry the week);
     disabled-without-key returns `enabled:false` (no 500). No schema/`SCHEMA_VERSION` change.
2. **Frontend — Weekly recap card** in League Detail → AI Brief:
   - Completed-week options are derived from `GET .../matchups` (weeks with both final scores,
     non-playoff); a `<select>` picks the week (defaults to the latest completed week).
   - Generate / Regenerate for the selected week; renders `headline`, `body`, `luck_notes`,
     `waiver_highlights`, and an "inputs changed" hint when `stale`. No completed weeks → an
     honest empty state. No key → the existing AI-off panel. React formats content only.

## Invariants (unchanged)
- No `SCHEMA_VERSION` bump; no facts/prompt/output-schema change (`ai_inputs.py`,
  `ai_schemas.py`, `ai_config.py` untouched). No metric/DB/schema change; the in-season
  `edge_score` `82.5` fixtures stay byte-identical.
- The **production Anthropic path is intact**: with `ANTHROPIC_API_KEY` set, POST runs the
  real `AiService` → `AnthropicLlmClient`. **All automated tests are offline** (fake LLM; no
  key required). No secrets logged or returned; no live ESPN/Anthropic calls in tests/CI.

## Real weekly-recap generation (production only, costs API usage)
Generating a real recap uses the production model path — run by hand, never in tests/CI:
```bash
export ANTHROPIC_API_KEY=...     # or in .env; never echo/commit/log it
make dev
# in the app: League detail → AI Brief → Weekly recap → pick a week → Generate
# or directly:
curl -X POST "http://127.0.0.1:8000/api/leagues/{league_id}/ai/weekly-recap?week={week}&force=true"
```
A real Anthropic call may incur API usage charges. With no key, the same call returns
`enabled:false` (no error).

## Acceptance criteria
- `GET ?week=N` returns week N even when other weeks are stored (never the wrong week).
- `POST ?week=N` persists `week` in `content_json` and caches per week (repeat → no new call;
  different week → new call; `force=true` regenerates).
- `stale` is per week.
- GET + POST are safe with no key (`enabled:false`, no 500).
- The AI Brief tab shows the weekly card with a completed-week picker and renders the recap.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- A recap history list per week; a "generate all completed weeks" action; surface luck/waiver
  chips inline on the Matchups tab.
