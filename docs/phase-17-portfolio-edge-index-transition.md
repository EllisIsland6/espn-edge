# Phase 17 — Portfolio Board Edge Index transition (contract)

Promote the Phase 16 full Edge Index (`edge_index_score`) to the **primary** advantage score
on the Portfolio Board, summary rail, and exports — while keeping the older Phase 3
`edge_score` as a **legacy/secondary** value. **No metric computation changes**: this is a
presentation/aggregation transition only.

## Scope (v1)
1. **Summary** — `PortfolioSummary` gains Edge Index aggregates, computed in the view layer:
   `edge_index_scored_count`, `edge_index_advantaged_count`, `best_edge_index_score`,
   `worst_edge_index_score`. The legacy `scored_count`, `advantaged_count`,
   `best_edge_score`, `worst_edge_score` (Phase 3 `edge_score`) are kept unchanged.
2. **Portfolio Board** — tier / filter / row accent and the row chip use
   `edge_index_score` / `edge_index_grade` / `edge_index_verdict` as primary. The legacy
   `edge_score` is shown as a small secondary value. When `edge_index_score` is null the row
   is **pending** (honest) — React never computes or fabricates a fallback.
3. **Right rail** — Edge Index counts / best-worst are primary; the legacy `edge_score`
   count is shown clearly labeled as legacy. The stale "fuller SPEC §6 model is future" copy
   is removed (SPEC §6 now lands as Edge Index).
4. **Exports** — CSV/XLSX gain `edge_index_score`, `edge_index_grade`, `edge_index_verdict`
   columns; the master JSON already carries them via the portfolio row model. Existing
   `edge_score`, `grade`, `verdict` columns are kept for backward compatibility.

## Invariants (unchanged)
- `edge_score` and `edge_index_score` **computation**, `grade_for` / `verdict_for`, sync
  behavior, and the DB schema are all untouched. The in-season `82.5` fixtures stay
  byte-identical. Both legacy and Edge Index fields remain present on portfolio rows.

## Acceptance criteria
- Summary exposes the four new Edge Index aggregates plus the unchanged legacy ones.
- `/api/portfolio` rows still carry both `edge_score`/`grade`/`verdict` and
  `edge_index_score`/`edge_index_grade`/`edge_index_verdict`.
- CSV/XLSX include the Edge Index columns alongside the legacy ones.
- The board renders Edge Index as the primary score/label (mocked Playwright assertion).

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Future work
- Once Edge Index is validated on real leagues, retire the legacy `edge_score` entirely
  (board, summary, exports), and add cross-league population normalization + p5/p95
  winsorization (full SPEC §6 normalization rule).
