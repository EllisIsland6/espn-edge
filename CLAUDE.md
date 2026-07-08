# ESPN Edge — project rules
- SPEC.md is the source of truth; Section 1/2 (ESPN access) may not be changed without asking.
- ESPN traffic: backend only, lm-api-reads host, cookies from the accounts table, 1 rps throttle, cache raw JSON.
- Never print/log/commit SWID or espn_s2. Fernet-encrypt at rest.
- Read-only against ESPN. No login automation. No HTML scraping.
- Every UI number comes from the DB (`metrics` or raw tables). No math in components.
- Tests run offline on fixtures in tests/fixtures/. Run `make test` before declaring a phase done.
- Design tokens in Section 9 are canonical — no ad-hoc colors.
- SPEC §2.8 amended 2026-07-07: raw httpx client is the workhorse (own parser); espn-api stays installed as a `--cross-check` diff in the verify CLI, not the primary read path.

## Build status
- Phase 0 (scaffold) and Phase 1 (ESPN client, accounts, sync) implemented.
- `make dev` runs api (uvicorn) + web (vite) natively with hot reload; docker-compose is provided for parity but not required.
- Verify CLI: `python -m api.verify --league <id> [--season 2026] [--cross-check] [--label main]`. Public-first, falls back to cookies from ESPN_SWID/ESPN_S2 (.env) or hidden prompt — never argv.
- Layout: /api (FastAPI), /web (Vite+React+TS), /data (SQLite + raw_cache), /tests (fixtures + unit tests).
