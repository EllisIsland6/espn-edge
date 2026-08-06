# Phase 24 - Portfolio Analytics UI and exports

Phase 24 renders the Phase 23 read models at `/analytics` and adds exact CSV, JSON,
and XLSX outputs for the same data. It is additive: there is no schema reset, ESPN
access change, live FFC call from React, or change to any Edge metric.

## UI contract

- `Analytics` is a top-level route and nav item beside Portfolio, Manage, and Status.
- The exposure table uses the shared TanStack `DataTable`, supports my-team and opponent
  scopes, and expands each player into the API-provided league breakdown.
- Recharts renders the normalized draft fingerprint and primary-strategy distribution.
  The custom strategy legend always renders the complete primary enum, including
  `Zero RB` at `0 / N`.
- Coverage chips show teams in scope, auction handling, keeper handling, picks without
  ESPN/FFC ADP, FFC snapshot exclusions, and resolver failures. Percentages retain raw
  denominators in tables, legends, or tooltips.
- ESPN is labeled `vs. draft-time ADP`. FFC is labeled `vs. current market ADP` with
  the snapshot `pulled_at` date. The two figures are never blended.
- Mean Edge Index by strategy carries the backend's descriptive-not-causal note.
- Missing/stale FFC snapshots, no drafted teams, no classified teams, errors, and fewer
  than three teams in scope all have explicit states.
- Fantasy Football Calculator attribution now lives in the global app footer.

## Export contract

Shared row-field lists in `api/services/exports.py` drive these CSV tables and the
matching workbook sheets:

- `GET /api/exports/exposure.csv?scope=me|opponents`
- `GET /api/exports/draft-adp.csv`
- `GET /api/exports/strategies.csv`

The master `portfolio.json` adds `exposure.me`, `exposure.opponents`, `draft_adp`, and
`strategies`, each built by the same read-model service as its API endpoint. The master
workbook adds `Exposure`, `Draft ADP`, and `Strategies` sheets before its per-league
sheets.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check api tests
cd web && npm run lint
cd web && npm run build
cd web && npm run e2e
```

The mocked Playwright suite includes desktop and 390px mobile Analytics smoke coverage;
all `/api` calls remain offline.
