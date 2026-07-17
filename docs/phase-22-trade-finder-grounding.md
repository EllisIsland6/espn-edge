# Phase 22 — Trade Finder grounding on roster snapshots (contract)

Replace Trade Finder's draft-time position-count grounding with the best **truthful** roster
facts already in the DB, plus strict player-name validation. **No new ESPN view, roster
table, or DB migration** — the data already exists. Automated tests stay offline (fake LLM);
the real model path is used only in production when `ANTHROPIC_API_KEY` is set. Advisory only —
the app never executes trades on ESPN.

## Data reality (why this is the honest maximum)
`lineup_slots` holds **per-completed-week** full rosters (starters + bench) from `mBoxscore`;
there is **no `mRoster` fetch**, so no live current roster. Facts therefore label a **"Week N
roster snapshot,"** never "current roster." Preseason has no `lineup_slots` → the drafted
roster is the only signal.

## Roster facts
- **Source selection:** the **latest week both teams share** a `lineup_slots` snapshot
  (`grounding_source="lineup_snapshot"`). Both rosters are read from that same week — **never
  mixed across weeks** (which would show traded/dropped players incorrectly). If there is no
  shared week → **`drafted_roster`** fallback (DraftPick⨝Player) with a `fallback_reason`; if
  neither exists → `grounding_source="none"` with empty rosters (no invented players).
- **Per player:** `espn_player_id`, canonical `name`, `position`, `slot`, `is_starter`,
  `proj_ros` **or null** (nulls are never treated as 0). Deduped by `espn_player_id` (a starter
  row wins); a lineup row missing its `Player` join is **kept** (name null), never dropped.
- **Provenance/freshness (deterministic):** `grounding_source`, `snapshot_week` (or null),
  `fallback_reason`, `snapshot_stale` (the shared week is older than the newest league lineup
  week), `projections_stale` (**conservative: true whenever `last_sync_ok` is not True**),
  `unsupported_slots` (superflex/OP, IDP … present in the league but not modeled), and per-team
  `projection_coverage`.

## Positional surplus/deficit (deterministic, no LLM math)
Requirements come from the league's persisted slot map via the **reused** metrics helper
`_starting_slot_counts` (imported read-only; `metrics.py` unchanged, so metric behavior stays
byte-identical). Per position (QB/RB/WR/TE/K/D/ST): `count`, `starting_need` (dedicated slots),
`flex_share`, `proj_total`, `surplus_count`, `surplus_proj` (projection-weighted depth beyond
starters), `deficit`. **FLEX rule:** after each position fills its dedicated slots, the ordinary
FLEX demand is assigned to the highest projected RB/WR/TE remaining (nulls sort last; ties broken
by `espn_player_id`). **Superflex/OP and IDP are unsupported** — reported in `unsupported_slots`,
never folded into FLEX. When projection coverage is weak, callers lean on labeled count-based
depth (never interpreting missing projections as weak players).

## Player-name validation (prevent invented trades)
The `TradeFinder` output schema is **unchanged**; validation is **post-generation**. Every
`i_give` name must be one exact player from `me.players` and every `i_get` from
`opponent.players` (normalized for case/whitespace, then emitted as the canonical DB name — **no
substring/fuzzy/combined-string matching**; one player per list item). Invalid proposals are
**dropped before persistence**; valid ones are kept (canonicalized); if none survive, an empty
`proposals` list with an advisory note is stored/served. Wired via a new optional
`post_validate` hook on `AiService.generate` used **only** by trade_finder — the other four
report kinds are unaffected.

## Cache / freshness
- `SCHEMA_VERSION` bumped **v3 → v4** (grounding + prompt changed) — v3 reports never serve as
  fresh v4 reports.
- New facts (roster membership, snapshot week, projections, freshness flags, fallback source)
  all feed `input_hash`, so any change marks the prior per-opponent report stale.
- Phase 21 per-opponent retrieval + legacy-cache protection are preserved; provenance is
  persisted in `content_json` via `extra`.

## Frontend
The Phase 21 layout/opponent-picker is unchanged; the previous opponent's report is not shown
while a new one loads. A provenance chip shows **"Week N roster snapshot"** (with an "older
snapshot" flag when stale) or **"Drafted-roster fallback"**, plus "projections unconfirmed" /
"low projection coverage" when relevant. The blanket draft-time caveat is replaced by a
snapshot-grounded line only for `lineup_snapshot` reports; advisory-only / never-executes wording
stays.

## Invariants
- No DB/schema change, no sync/ESPN-view change, no metric/Edge-Index formula change, no provider
  SDK/model-id change. `edge_score` `82.5` fixtures byte-identical. AI stays optional (no key →
  `enabled:false`). No secrets logged/returned; no live ESPN/Anthropic calls in tests/CI.

## Manual real-AI smoke (production only — separate explicit approval required; may cost money)
```bash
export ANTHROPIC_API_KEY=...     # never echo/commit/log it
make dev                          # sync ONE real in-season league (docs/live-smoke.md)
# App: League detail → AI Brief → Trade finder → pick ONE opponent → Generate; or one call:
curl -X POST "http://127.0.0.1:8000/api/leagues/{league_id}/ai/trade-finder?opponent_team_id={team_id}&force=true"
```
Verify (a) every named player is on the real rosters, (b) provenance matches the snapshot
week/source, (c) freshness flags behave. **Do not run without explicit approval.**

## Gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- A true current-roster snapshot (an `mRoster` fetch + persistence) would remove the
  "as-of-latest-week" limitation — a larger phase (new ESPN view + storage).
