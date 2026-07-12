# Phase 10 — draft value foundation (contract)

Populate the persisted draft ADP/value data that the schema and UI already reserve fields
for (`DraftPick.adp_at_draft`, `DraftPick.value_delta`), and lay a **pure** draft-surplus
foundation for future MyEdge work. This is a data/plumbing slice — it does **not** change
`edge_score`, `grade`, `verdict`, `playoff_odds`, or the Phase 9 component semantics.

## Scope (v1)
1. **Stamp draft ADP/value during sync.** After a successful `kona_player_info` player-pool
   refresh, for each `DraftPick`:
   - `adp_at_draft = Player.espn_adp` (the freshly-refreshed pool value)
   - `value_delta = round(adp_at_draft - overall, 1)`
     (matches the existing AI convention: positive delta = drafted later than ADP)
   - Leave **both null** when the player, its ADP, or `overall` is missing.
   - **Never use stale player data.** Picks are fully replaced every sync and inserted with
     null ADP/value; when the player-pool fetch fails (`projections_fresh == False`) the
     stamping step is skipped, so newly-inserted picks keep null ADP/value.
2. **Pure draft-surplus helper (future MyEdge; not in edge_score yet).**
   - `pick_value(overall) = 100 · e^(−overall / 34)` (steep early, flat late; SPEC §6.2)
   - per-pick surplus = `pick_value(adp_at_draft) − pick_value(overall)`
   - team draft surplus = sum of known per-pick surpluses
   - Persisted as a team metric `draft_surplus` (`week = NULL`); cleared when a team has no
     valid pick values. **Not** part of `edge_score`/components.
3. **AI inputs** read persisted `DraftPick.adp_at_draft` / `value_delta` instead of
   recomputing the delta from a joined `Player.espn_adp`.
4. **Draft Board** shows player name + position + ADP + value delta (from a `Player` join on
   the read endpoint) instead of mostly raw player IDs. React formats only — no ADP math.

## Non-goals
- No change to `edge_score`, `grade`, `verdict`, `playoff_odds`, or Phase 9 components.
- `draft_surplus` is computed/persisted but **not** folded into the Edge Index yet.
- No new ESPN calls (ADP comes from the existing `kona_player_info` pull), no DB schema
  change (`adp_at_draft`/`value_delta` columns and the generic `metrics` table already
  exist) → **no `make db-reset`**.
- Not the full SPEC §6 draft-surplus MyEdge component — this is the data foundation for it.

## Persistence / ordering
- Stamping runs in the sync pipeline **after** Step 5 (player pool), only when
  `projections_fresh`, and **before** Step 6 (metrics recompute), so `recompute_league`
  reads the just-stamped `adp_at_draft` when computing `draft_surplus`.

## Acceptance criteria
- A successful sync stamps `adp_at_draft` + `value_delta` for picks whose player has an ADP;
  a failed player-pool fetch leaves them null (no stale values).
- `pick_value`, per-pick surplus, and team surplus are pure and hand-computable; team
  `draft_surplus` persists and clears with no valid picks.
- AI facts reflect persisted ADP/value delta.
- `/api/leagues/{id}/draft` returns player name/position; the Draft Board renders them.
- Existing exact `edge_score` values remain byte-identical.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Fold `draft_surplus` into a normalized MyEdge draft-surplus component (SPEC §6.2), with
  calibration of the value-curve constant and population normalization.
- FFC ADP cross-source and pre-draft value boards (SPEC §2.9).
