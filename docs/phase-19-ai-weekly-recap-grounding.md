# Phase 19 — AI weekly recap grounded on all-play, luck & waivers (contract)

Feed the weekly-recap AI report the facts its output schema already anticipates. The
`WeeklyRecap` schema has `luck_notes` and `waiver_highlights` fields that were previously
starved of inputs; Phase 19 grounds them on persisted all-play/luck and transactions.
**Facts + prompt wording only** — no output-schema, metric, DB, provider, or frontend change.

## Scope (v1)
`weekly_recap_input` now returns (all from persisted rows, no invented numbers):
- **`matchups`** — home/away/points/is_playoff (existing), plus `winner`, `loser`, `margin`,
  `tie` computed from the two scores when both are present.
- **`week_all_play`** — per-team all-play for **this week** via the existing pure
  `metrics.compute_all_play` fed only the week's `{team_id: score}`: `all_play_wins/losses/
  ties`, `all_play_win_pct`, `week_score`. (Only the week ranking is exposed; the helper's
  season-based `luck_delta` isn't meaningful for a single week.)
- **`season_all_play`** — from `metrics.read_all_play`: per-team `all_play_win_pct` +
  `luck_delta` (season luck context).
- **`transactions`** — that week's `Transaction` rows only, with `player_in`/`player_out`
  resolved to names via `Player` (falling back to `#id`); `type` and `bid` included. An empty
  or absent feed (SPEC §2.4) → `[]`.

The `weekly_recap` prompt (`ai.py`) writes `luck_notes` strictly from `week_all_play` /
`season_all_play`, and `waiver_highlights` strictly from `transactions`; on an empty
transaction list it calls it a **quiet transaction week** and invents nothing.

`SCHEMA_VERSION` bumps `v2`→`v3` so cached weekly recaps invalidate (the prompt text is not
part of the `input_hash`; fact changes already bust weekly_recap on their own).

## Invariants (unchanged)
- No metric computation change (`metrics.py`, `edge_config.py`, `models.py` untouched); the
  in-season `edge_score` `82.5` fixtures stay byte-identical.
- Output schema `ai_schemas.WeeklyRecap` unchanged. No DB/schema change → no `make db-reset`.
- The **production path is unchanged**: with `ANTHROPIC_API_KEY` set, the endpoint uses the
  real `AiService` → `AnthropicLlmClient`. **Automated tests are fully offline** (fake LLM;
  no key required). No secrets logged or returned; no live ESPN/Anthropic calls in tests.

## Manual real-AI smoke (optional, costs API usage)
This is the **only** place that calls the real model, run by hand — never in tests/CI.

1. Set your key **in the shell or `.env`** (never paste it into a commit, log, screenshot, or
   this repo). Cookies/SWID/espn_s2 must likewise never be pasted or logged.
   ```bash
   export ANTHROPIC_API_KEY=...    # or put it in .env; do not echo it
   make dev                        # api on :8000
   ```
2. Sync a real league so it has matchups/transactions (see `docs/live-smoke.md`).
3. Generate a real weekly recap for a completed week (`force=true` bypasses the cache):
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/leagues/{league_id}/ai/weekly-recap?week={week}&force=true"
   ```
   The response is an `AiReportEnvelope` with the recap content. **A real Anthropic call may
   incur API usage charges.** With no key set the same call returns `enabled:false` (no error).

## Acceptance criteria
- `weekly_recap_input` returns `matchups` (with `winner`/`margin`), `week_all_play`,
  `season_all_play`, and `transactions` (names resolved), all from persisted data.
- A week with transactions → resolved names; a week with none → `transactions == []`.
- `_TASK["weekly_recap"]` references all-play/luck and transactions and the quiet-week rule.
- `SCHEMA_VERSION == "v3"` and identical facts hash differently than under `v2`.
- No-key path returns `enabled:false` (no 500); no secrets in responses/logs.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- A weekly-recap UI panel (currently generate-only backend) with a GET + history.
- Ground `draft_recap` on the team's draft-surplus component; ground `trade_finder` on
  roster projections.
