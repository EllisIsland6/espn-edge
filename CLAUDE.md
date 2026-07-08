# ESPN Edge — project rules
- SPEC.md is the source of truth; Section 1/2 (ESPN access) may not be changed without asking.
- ESPN traffic: backend only, lm-api-reads host, cookies from the accounts table, 1 rps throttle, cache raw JSON.
- Never print/log/commit SWID or espn_s2. Fernet-encrypt at rest.
- Read-only against ESPN. No login automation. No HTML scraping.
- Every UI number comes from the DB (`metrics` or raw tables). No math in components.
- Tests run offline on fixtures in tests/fixtures/. Run `make test` before declaring a phase done.
- Design tokens in Section 9 are canonical — no ad-hoc colors.
- SPEC §2.8 amended 2026-07-07: raw httpx client is the workhorse (own parser); espn-api stays installed as a `--cross-check` diff in the verify CLI, not the primary read path.
- `SyncService` owns the `EspnService`/httpx.Client it creates: always use `with SyncService(session) as svc:` (or call `svc.close()`) in scheduler jobs, routes, and CLI so clients don't leak. Injected services (test fakes / shared clients) are never closed by it.
- UI metric fields (edge_score/grade/playoff_odds/verdict) stay null until Phase 3; the Phase 2 UI renders placeholders, never computes.

## Build status
- Phase 0 (scaffold), Phase 1 (ESPN client, accounts, sync), Phase 1.5 (hardening),
  Phase 2 (Portfolio Board + League detail UI) implemented.
- Web stack: React+Vite+TS, Tailwind v4 (tokens in src/tokens.css @theme), TanStack
  Table (src/components/DataTable.tsx), react-router. Pages: PortfolioBoard, LeagueDetail
  (tabs), Manage. All data from the read-only /api view endpoints; no math in components.
- `make dev` runs api (uvicorn) + web (vite) natively with hot reload; docker-compose is provided for parity but not required.
- Verify CLI: `python -m api.verify --league <id> [--season 2026] [--cross-check] [--label main]`. Public-first, falls back to cookies from ESPN_SWID/ESPN_S2 (.env) or hidden prompt — never argv.
- Layout: /api (FastAPI), /web (Vite+React+TS), /data (SQLite + raw_cache), /tests (fixtures + unit tests).
