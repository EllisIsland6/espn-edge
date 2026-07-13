# Phase 18 — AI grounding uses the Edge Index model (contract)

Reground the optional AI layer so the two edge-driven report kinds treat the Phase 16/17
**Edge Index** as the primary advantage signal, keeping the legacy Phase 3 `edge_score` only
as clearly-named secondary context. **Facts + prompt wording only** — no metric computation,
schema, or provider changes. AI stays optional (no key → `enabled:false`, never a 500).

## Scope (v1)
1. **`advantage_verdict_input` facts (`me`)** now include, from the persisted metrics:
   - `edge_index_score`, `edge_index_grade`, `edge_index_verdict`, `edge_index_components`
     (the two 0–100 halves)
   - `my_edge_score` + `my_edge_components` (within-league percentiles)
   - `league_softness_score` + `league_softness_components`
   - `playoff_odds`
   - `legacy_edge_score`, `legacy_grade`, `legacy_verdict` — the old Phase 3 within-league
     proxy, **renamed** so the model can't confuse it with the composite.
   - **No bare `edge_score`/`grade`/`verdict` key remains.**
2. **`league_brief_input` team facts** now carry `edge_index_score` + `edge_index_verdict`
   (scalar) and `legacy_edge_score`, and drop the bare `edge_score`. Kept **lean** — no
   per-team component arrays (component breakdown lives only in the single "me" verdict).
3. **Prompts** (`ai.py` `_TASK`): `league_brief` and `advantage_verdict` lead with the Edge
   Index (and its components for the "why"); `legacy_edge_score` is described as a deprecated
   secondary signal. `draft_recap` / `weekly_recap` / `trade_finder` are unchanged.
4. **Cache**: `SCHEMA_VERSION` bumped `v1`→`v2`. Fact changes already bust `input_hash`, but
   the prompt text is **not** part of the hash, so the version bump is what invalidates cached
   reports whose facts happen to be unchanged.

## Invariants (unchanged)
- No metric computation change (`metrics.py`, `edge_config.py`, `models.py` untouched); the
  in-season `edge_score` `82.5` fixtures stay byte-identical. Legacy `edge_score` is **kept**
  (renamed in facts only, still computed and shown on the board). No provider SDK / model-id /
  pricing changes. No live ESPN or Anthropic calls; no secrets logged or returned.

## Acceptance criteria
- `advantage_verdict_input.me` contains the Edge Index / MyEdge / LeagueSoftness facts +
  `playoff_odds` + `legacy_edge_score`, and no bare `edge_score`.
- `league_brief_input` team facts contain `edge_index_score` + `legacy_edge_score`, no bare
  `edge_score`.
- `_TASK["league_brief"]` and `_TASK["advantage_verdict"]` lead with the Edge Index and
  mention `legacy_edge_score` as secondary; no longer lead with `edge_score`.
- `SCHEMA_VERSION == "v2"` and identical facts hash differently than under `v1`.
- With no key, all AI endpoints still return `enabled:false` / empty (no 500).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Once Edge Index is the sole board score, drop `legacy_edge_score` from AI facts entirely.
- Consider grounding `weekly_recap` on all-play/luck and `draft_recap` on the team's Edge
  Index draft-surplus component.
