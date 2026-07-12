# Phase 12 — all-play record + luck delta (contract)

Compute and display each team's **all-play** record and **luck delta** from completed
matchup scores, replacing the Matchups-tab placeholder. This is the foundation for the SPEC
§6.2 luck-adjusted record — but it is **not** folded into `edge_score` yet.

## Scope (v1)
- Pure helpers in `api/services/metrics.py`:
  - For each completed **regular-season** week, collect each team's score.
  - Score every team against every **other scored team** that week (win / loss / tie).
  - Aggregate `all_play_wins` / `all_play_losses` / `all_play_ties` and
    `all_play_win_pct = (wins + 0.5·ties) / games`.
  - `luck_delta = all_play_win_pct − actual_win_pct`. Positive = the team's scoring quality
    outran its actual record.
  - **Ignore playoff matchups.** **Skip weeks with fewer than 2 scored teams.**
- Completed weeks come from the sync's `completed_weeks` when available so current-week
  partial scores aren't counted; with `completed_weeks=None` (manual/test recompute) fall
  back to the conservative "either side scored > 0" heuristic (mirrors `_split_matchups`).

## Persistence
- Team metrics with `week = NULL`: `all_play_wins`, `all_play_losses`, `all_play_ties`,
  `all_play_win_pct`, `luck_delta`.
- Cleared (all five) for a team with **no completed all-play sample**. No schema change
  (generic `metrics` rows) → **no `make db-reset`**.

## API
- `GET /api/leagues/{id}/all-play` → rows ordered by `all_play_win_pct` descending, one per
  team **with a completed sample**. Each row: `team_id`, `team_name`, actual
  `wins/losses/ties/win_pct`, `all_play_wins/losses/ties/win_pct`, `luck_delta`.

## UI
- League detail → Matchups tab shows an **All-play & luck** table above the matchup
  schedule; the stale "arrive in Phase 3" placeholder is removed. React formats the
  backend values only (percent, signed luck) — no metric math in components.

## Invariants (unchanged)
- `edge_score`, `grade`, `verdict`, `playoff_odds`, and the Phase 9/11 component behavior
  are untouched; the in-season `82.5` fixtures stay byte-identical.

## Acceptance criteria
- Hand-computable all-play/luck on a small 4-team, 2-week fixture.
- All-play metrics persist and clear when there's no completed sample.
- `/api/leagues/{id}/all-play` returns rows ordered by all-play win% desc.
- Matchups tab renders the all-play/luck table (mocked Playwright assertion).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Fold luck-adjusted record into the SPEC §6.2 MyEdge composite (weight 0.10), and add
  strength-of-schedule + all-play trend charts. Population-normalize across leagues.
