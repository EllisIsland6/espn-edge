# Phase 28 - Dedicated Analytics pages

Phase 28 replaces the single long Analytics document with four dedicated frontend routes.
It is a navigation and request-isolation change only: the Phase 23-27 read models, displayed
values, controls, exports, and backend contracts remain unchanged.

## Route contract

| Page | Route | Read model |
| --- | --- | --- |
| Leverage | `/analytics/leverage` | `GET /api/portfolio/exposure` |
| ADP capture | `/analytics/adp` | `GET /api/portfolio/draft-adp` |
| Strategy | `/analytics/strategy` | `GET /api/portfolio/strategies` |
| Opportunity | `/analytics/opportunity` | `GET /api/portfolio/opportunity` |

The shared Analytics header and horizontally scrollable page navigation render on every
route. The active page uses `aria-current="page"` and the existing red underline treatment.
The app-level Analytics link remains `/analytics` and stays active on every child route.

Bare `/analytics` redirects to `/analytics/leverage`. Existing bookmarks remain valid:

- `/analytics#exposure-heading` redirects to `/analytics/leverage`.
- `/analytics#adp-heading` redirects to `/analytics/adp`.
- `/analytics#strategy-heading` redirects to `/analytics/strategy`.
- `/analytics#opportunity-heading` redirects to `/analytics/opportunity`.

## Page behavior

- Each route requests only its own portfolio read model and has an independent loading and
  error state. A slow or failed Opportunity request cannot block Leverage, ADP, or Strategy.
- Existing tables, charts, coverage/empty states, filters, tooltips, team links, Opportunity
  refresh, and lazy player drilldown are preserved without React-side analytics math.
- Exposure CSV remains scoped to the selected combined/opponent and rostered/field/all view.
  Opportunity CSV remains scoped to the selected rostered/available/all view. ADP and
  Strategy keep their matching CSV actions.
- Master portfolio JSON and XLSX stay in the shared header because they span all analytics
  areas. FFC and nflverse attribution remain in the global footer.
- Route changes reset document scroll, and the page navigation remains usable without
  horizontal document overflow at the 390px mobile viewport.

## Verification

The mocked Playwright suite directly loads every route, verifies active navigation and
section-specific interactions, records portfolio endpoint traffic to enforce request
isolation, checks legacy hash redirects and scroll reset, and retains mobile overflow
coverage. All requests remain offline.

```bash
make test
make lint
cd web && npm run lint
cd web && npm run build
cd web && npm run e2e
```

No database/schema migration, API response change, ESPN access change, scoring change,
export schema change, or AI behavior change is part of Phase 28.
