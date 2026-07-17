# Phase 21 — Trade Finder panel in the AI Brief (contract)

Wire the last backend-only AI report kind, `trade_finder`, into League Detail → AI Brief with
**correct per-opponent retrieval and caching** and **no DB schema change**. Automated tests
stay fully offline (fake LLM); the real model path is used only in production when
`ANTHROPIC_API_KEY` is set. **Advisory only — the app never executes trades on ESPN.**

## Scope (v1)
1. **Backend — per-opponent GET/POST:**
   - `GET /api/leagues/{id}/ai/trade-finder?opponent_team_id=N` returns the stored proposal for
     opponent N (or `content:null`), with `stale` measured against that opponent's current facts.
   - `POST /api/leagues/{id}/ai/trade-finder?opponent_team_id=N&force=...` generates and stores,
     passing `extra={"opponent_team_id": N, "opponent_name": ...}` so the opponent is persisted
     in `content_json`.
   - Validation (`_valid_opponent`): the opponent must belong to this league and must **not** be
     the detected my-team. Unknown ids, cross-league ids, and the user's own team all return an
     error envelope (never content, never generation). Missing my-team → error envelope.
   - `AiService.latest_for_opponent(league_id, kind, opponent_team_id)` (a sibling of Phase 20's
     `latest_for_week`, both on a shared `_latest_where_content`) filters by
     `content_json["opponent_team_id"]`. GET/POST use it — **never** `latest(kind)`, which
     ignores the opponent. Reports lacking `opponent_team_id` are legacy/unscoped and are never
     returned for an opponent.
   - **Legacy-cache edge case:** POST reuses a report **only** when it is already tagged for this
     opponent AND its `input_hash` matches the fresh facts; otherwise it calls
     `generate(..., force=True, extra=...)`, which skips the hash-only cache (so a legacy
     untagged report sharing the `input_hash` can't be served) and stores a correctly-tagged
     report. `force=true` always regenerates.
   - Disabled-without-key → `enabled:false` (no 500).
2. **Frontend — Trade Finder card** in the AI Brief tab:
   - Opponent `<select>` from `getLeagueTeams` filtered to `!is_me` (default: first opponent).
     On change, the previous opponent's result is cleared while the new one loads (no stale
     flash), then `getTradeFinder(id, opponentId)`.
   - Generate / Regenerate for the selected opponent; renders opponent identity, each proposal's
     `i_give` / `i_get` / `rationale`, and the advisory `note`; "inputs changed" hint when
     `stale`; honest states for loading, no-my-team, no-opponents, no-report, and errors. Copy
     states the recommendations are advisory only, the app doesn't execute trades, and this v1
     grounds on draft-time positional counts that may not reflect current in-season rosters.

## Invariants (unchanged)
- No `SCHEMA_VERSION` bump; no change to `trade_finder` grounding/facts/prompt/output schema
  (`ai_inputs.py`, `ai_schemas.py` untouched). No metric/DB/schema change; `edge_score` `82.5`
  fixtures byte-identical.
- The **production Anthropic path is intact**: with `ANTHROPIC_API_KEY` set, POST runs the real
  `AiService` → `AnthropicLlmClient`. **All automated tests are offline** (fake LLM; no key
  required). No secrets logged/returned; no live ESPN/Anthropic calls in tests/CI.

## Manual real-AI smoke (production only, may cost API usage)
Run by hand, never in tests/CI. Don't echo/commit/log the key or cookies.
```bash
export ANTHROPIC_API_KEY=...     # or in .env
make dev                          # sync a real league first (docs/live-smoke.md)
# in the app: League detail → AI Brief → Trade finder → pick an opponent → Generate
# or directly (opponent_team_id is the internal Team.id):
curl -X POST "http://127.0.0.1:8000/api/leagues/{league_id}/ai/trade-finder?opponent_team_id={team_id}&force=true"
```
A real call may incur Anthropic API usage charges. With no key, the same call returns
`enabled:false` (no error).

## Acceptance criteria
- `GET ?opponent_team_id=N` returns opponent N even when other opponents are stored (never the
  wrong opponent); legacy/unscoped reports are ignored.
- POST does not reuse a legacy unscoped cache entry with the same `input_hash`; it generates a
  correctly-tagged report. Cache hits work per opponent; `force=true` regenerates.
- `stale` is per opponent.
- Self / cross-league / unknown opponent ids are rejected (error envelope, no generation).
- No-key GET+POST return `enabled:false` (no 500).
- The AI Brief tab shows the Trade Finder card with an opponent picker and renders proposals.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- ✅ **Done in Phase 22** — grounding now uses the latest shared `lineup_slots` snapshot +
  `proj_ros` and deterministic positional surplus/deficit, with player-name validation. See
  **[docs/phase-22-trade-finder-grounding.md](docs/phase-22-trade-finder-grounding.md)**.
