# Phase 7 — private-beta reliability (contract)

Make the app survive the #1 real-world failure — expired ESPN cookies / failed syncs —
with a clean recovery path and honest, persistent diagnostics. No new product features.

## Scope (v1)
1. **Account re-auth flow** — a way to replace an account's cookies after they expire and
   clear its `needs_reauth` status, without deleting the account (which is blocked while
   leagues are linked).
2. **Persistent per-league sync diagnostics** — store the outcome of the last sync on the
   league (`last_sync_ok`, `last_sync_error`) so a failure is visible after the transient
   toast, in Manage and on the League detail page.

## Non-goals
- Full paginated sync-run **history** table (only last-sync is stored).
- Retry/backoff **scheduler** or scheduling UI.
- Analytics depth (Edge Score component breakdown, lineup/all-play/luck).
- Desktop packaging / release artifacts.
- Mobile/tablet layout pass.

## Acceptance criteria
- `POST /api/accounts/{id}/reauth` (body `{swid, espn_s2}`) normalizes the SWID
  (existing helper), Fernet-encrypts `espn_s2`, sets `status="active"`, and returns
  `AccountOut`. Never logs or returns `swid`/`espn_s2`. 404 if the account is missing;
  400 if the SWID normalizes empty.
- `League.last_sync_ok: bool | None` and `last_sync_error: str | None` persist the last
  sync outcome, written on **every** exit path of `sync_league`:
  - clean success → `ok=True, error=None`
  - auth failure → `ok=False, error="auth_failed"` (+ account → `needs_reauth`)
  - initial fetch failure → `ok=False, error="fetch_failed: <safe>"`
  - partial errors (sync completes, `result["errors"]` non-empty) → `ok=False`, error is a
    concise joined+truncated safe summary
  - Diagnostics are **redacted** — never contain cookies/SWID/espn_s2 (a `_safe_error`
    helper sanitizes + truncates).
- These fields are exposed in `LeagueOut`, `PortfolioRow`, and `LeagueOverview` (via its
  nested `LeagueOut`), plus the TS types.
- Manage shows a per-account **Re-auth** form (prominent when `needs_reauth`) and each
  league's persistent last-sync status; League detail shows a persistent failed-sync note.
- No `swid`/`espn_s2` appears in any API response or in the DOM after submit.

## Commands / gates
```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests
cd web && npm run lint && npm run build && npm run e2e
```

## Schema / reset note
Local SQLite is fully re-syncable and there is no migration tool. The two new nullable
`leagues` columns require a rebuild: `make db-reset`, then re-add accounts/leagues and
sync (source data lives on ESPN; nothing is authoritative only in the DB).

## Future work
- Paginated sync-run history + per-run detail.
- Retry/backoff and a scheduling UI.
- Portfolio-level "N accounts need re-auth" banner.
- Account cookie-validity probe (dry-run auth check) before a full sync.
