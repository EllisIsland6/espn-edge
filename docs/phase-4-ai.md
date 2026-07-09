# Phase 4 — AI analysis layer (contract)

Backend-only Anthropic integration (SPEC §7). The AI layer is **additive, never
load-bearing**: the entire app works with no API key, showing a "connect a key"
empty state. Nothing here runs in React; the frontend only fetches stored reports.

## Model & SDK

- Official `anthropic` Python SDK, called from the backend only.
- Structured output via `client.messages.parse(output_format=<pydantic model>)` — the
  response is constrained to the schema and validated; we re-validate with pydantic
  before storing (belt-and-suspenders, SPEC §7 "validate before storing").
- Models (SPEC §7 tiers; ids current as of the claude-api skill, tunable in
  `api/ai_config.py`): **standard = `claude-sonnet-5`** for one-off analyses,
  **bulk = `claude-haiku-4-5`** for weekly recaps. Swap to `claude-opus-4-8` in
  `ai_config.py` if you want maximum quality over cost.
- `ANTHROPIC_API_KEY` comes from env/`.env`. If unset, `AiService.enabled` is False;
  every AI endpoint returns a disabled/empty result (never a 500), and the SDK is
  never constructed or called.

## The five report kinds (v1)

Each kind has a pydantic output schema in `api/ai_schemas.py` and a grounded input
builder in `api/services/ai_inputs.py`.

| kind | scope | model | inputs (DB facts only) | output schema |
|---|---|---|---|---|
| `draft_recap` | team | standard | that team's picks (round/overall/position/ADP delta) + positional fingerprint | `DraftRecap` |
| `league_brief` | league | standard | league size/scoring, per-team edge_score + records + autodraft flags + draft fingerprints | `LeagueBrief` |
| `advantage_verdict` | team (mine) | standard | my edge_score/grade/verdict/playoff_odds, record, standing, PF/PA | `AdvantageVerdict` |
| `weekly_recap` | league | bulk | one week's matchups + scores | `WeeklyRecap` |
| `trade_finder` | team (mine) | standard | my positional surplus/deficit vs one opponent's inverse (from drafted rosters) | `TradeFinder` |

Strategy labels for `draft_recap` come from a fixed enum (SPEC §7.1): Zero RB, Hero RB,
Robust RB, Anchor WR, Elite TE, Late-Round QB, Balanced/BPA, Autodraft/Absent.

## Grounding (SPEC §7)

Prompts contain **only computed facts from the DB** — picks, ADP deltas, records,
metrics, fingerprints. The model narrates and classifies; it never invents stats. The
system prompt states this explicitly and forbids new numbers. Input builders are pure
functions over DB rows (unit-testable offline) and emit compact JSON-ish fact blocks.

## Caching by input hash (SPEC §7)

`ai_reports.input_hash` = SHA-256 over `{kind, model, schema_version, input_facts}`
(canonical JSON). On generate:

- If a row exists for `(league_id, kind, input_hash)` and `force` is false → return it
  (cache hit; no API call).
- Otherwise call the model, validate, upsert a row, return it.

A **Regenerate** action passes `force=true`, which recomputes even on a hash match
(and replaces the stored row). `GET` endpoints return the most recent stored report for
a kind plus a `stale` flag (stored `input_hash` ≠ freshly computed hash → inputs
changed since generation).

Per-team `draft_recap` rows are distinguished by `input_hash` (each team's facts differ)
and carry `espn_team_id` inside `content_json`; the list endpoint returns all of them.

## Persistence

Existing `ai_reports` table (SPEC §4): `league_id, scope, kind, input_hash, model,
content_json, created_at`. No schema change — team identity for team-scoped kinds lives
inside `content_json` (`espn_team_id`), keyed by the team-specific `input_hash`.

## API (all read/generate; no secrets in responses)

- `GET /api/ai/status` → `{enabled, standard_model, bulk_model}` (key never returned).
- `GET  /api/leagues/{id}/ai/draft-recaps` → stored recaps (list, one per team) + stale.
- `POST /api/leagues/{id}/ai/draft-recaps?force=…` → generate/refresh all teams; caches.
- `GET|POST /api/leagues/{id}/ai/league-brief`
- `GET|POST /api/leagues/{id}/ai/advantage-verdict`
- `POST /api/leagues/{id}/ai/weekly-recap?week=N` (POST-only for now; GET added when the UI lands)
- `POST /api/leagues/{id}/ai/trade-finder?opponent_team_id=…`

When disabled, GET returns `{enabled:false, reports:…}` empties and POST returns
`{enabled:false}` — never an error.

## UI

The League detail **AI Brief** tab renders league-brief + advantage-verdict cards with
Generate/Regenerate; the **Draft Board** tab renders a per-team recap card grid. All
show the "connect a key" empty state when `enabled:false`. Weekly-recap and
trade-finder are backend-complete (schemas, validation, caching, endpoints) with UI
wiring deferred to the remainder of Phase 4.

## v1 vs future

v1 grounds `league_brief` on available metrics/records/fingerprints (the full SPEC §6.1
LeagueSoftness components are Phase 3 future); `trade_finder` uses drafted-roster
positional balance (in-season roster/needs is future). AI output never feeds back into
metrics (SPEC guardrail 11).
