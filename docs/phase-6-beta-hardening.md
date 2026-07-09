# Phase 6 — beta-readiness hardening (contract)

SPEC has no Phase 6; this is a self-defined **quality/hardening** phase to make the app
safe to hand to a beta user. No new product features.

## Scope (v1)
1. **CI** — a GitHub Actions workflow that runs the full gate set on every push and PR:
   backend `pytest` + `ruff`, frontend `tsc` lint + `vite build`, and the frontend
   smoke suite.
2. **Frontend confidence** — a small, deterministic **Playwright** smoke suite (Chromium)
   that mocks the `/api/*` layer (no backend, no ESPN, no secrets) and asserts the app
   shell renders and doesn't crash.
3. **User-facing robustness audit** — confirm empty/error/loading states exist across
   Portfolio Board, Manage, League detail, the AI-disabled state, and exports, and lock
   the key ones in with smoke assertions.
4. **Docs** — record the quality-gate workflow (how to run gates + e2e locally, what CI
   enforces).

## Non-goals
- No new endpoints, metrics, or UI features.
- No large frontend test framework (component/unit harness) — a focused Playwright smoke
  suite is enough. No visual-regression/screenshot testing.
- No live ESPN or Anthropic calls anywhere in tests.
- No deploy/release automation, no Docker image publishing.

## Acceptance criteria
- CI workflow exists (`.github/workflows/ci.yml`) with a backend job (pytest + ruff) and
  a frontend job (lint + build + Playwright), triggered on `push` and `pull_request`.
- Playwright suite (`web/e2e/`) passes locally and is deterministic/offline (all `/api/*`
  responses mocked via route interception). It asserts:
  1. Portfolio Board loads (header + a league row + summary rail).
  2. Export controls (CSV/JSON/XLSX) exist and point at the backend export endpoints
     (clicking CSV downloads `portfolio.csv`).
  3. Manage tab renders (add-account + add-league forms).
  4. A League detail route renders from mocked API data (standings visible).
  5. The AI-disabled state renders the "connect a key" empty state and does not crash.
- All existing gates still green:
  `pytest -p no:cacheprovider`, `ruff check api tests`, `npm run lint`, `npm run build`.

## Commands / gates
```bash
# backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
# frontend (from web/)
npm run lint
npm run build
npx playwright install chromium   # one-time: fetch the browser
npm run e2e                        # Playwright smoke suite
```

## Future work
- Component-level frontend tests (e.g. Vitest + Testing Library) for pure UI logic.
- Playwright runs against the **real** backend with a seeded DB (integration), not just
  mocked routes.
- CI caching (pip/npm/Playwright browsers), coverage reporting, and a release workflow.
- Accessibility (axe) and visual-regression checks.
