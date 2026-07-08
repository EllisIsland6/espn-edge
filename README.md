# ESPN Edge

A locally-run web app that connects to all your ESPN fantasy football leagues across
multiple ESPN accounts and answers one question with data: **"Am I an advantaged
player in each league — and across my portfolio?"** See [SPEC.md](./SPEC.md) for the
full design; ESPN data access is Section 2 (the verified technical foundation).

> Status: **Phase 0 (scaffold)** and **Phase 1 (ESPN client, accounts, sync)** complete.
> The Portfolio Board UI (Phase 2), analytics/Edge Index (Phase 3), AI layer (Phase 4),
> and playoff odds/exports (Phase 5) are not built yet.

## Quick start

```bash
make install         # venv + Python deps
make install-web     # npm install in /web
cp .env.example .env  # then set FERNET_KEY (see below)
make dev             # api on :8000, web on :5173, both hot-reload
```

Generate the cookie-encryption key and paste it into `.env` as `FERNET_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`GET http://127.0.0.1:8000/api/health` returns the season + DB path. The web app at
`http://127.0.0.1:5173` shows backend health (Phase 0 placeholder for the board).

Docker parity is available (`docker compose up`) but not required — `make dev` runs the
same two services natively.

## Connecting a private league (cookies)

ESPN has no official public API. Private leagues authenticate with two cookies from a
logged-in browser session — **cookies only; no username/password automation** (SPEC 2.3).

1. Log in at fantasy.espn.com in Chrome.
2. DevTools → Application → Cookies → `https://fantasy.espn.com`.
3. Copy `SWID` (keep the curly braces, e.g. `{ABC123-...}`) and `espn_s2` (a long string).
4. Add the account:

```bash
curl -X POST http://127.0.0.1:8000/api/accounts \
  -H 'Content-Type: application/json' \
  -d '{"label":"Main","swid":"{...}","espn_s2":"..."}'
```

`espn_s2` is Fernet-encrypted at rest; `SWID`/`espn_s2` are never logged or returned by
the API. On a bad/expired cookie the owning account flips to `needs_reauth` rather than
crashing. Public leagues need no account.

## Adding & syncing a league

```bash
# Manual add (always works): a bare league id or a full league URL.
curl -X POST http://127.0.0.1:8000/api/leagues \
  -H 'Content-Type: application/json' \
  -d '{"league_ref":"https://fantasy.espn.com/football/team?leagueId=123456","account_id":1}'

# Auto-discover leagues on an account (best-effort fan-profile API):
curl http://127.0.0.1:8000/api/leagues/discover/1

# Sync a league (id from the add/list response):
curl -X POST http://127.0.0.1:8000/api/leagues/1/sync
```

## Verify against a real league (live smoke test)

Prints league name, size, scoring type, standings, my team, and draft-pick count so you
can eyeball them against the ESPN UI (SPEC Phase 1 AC):

```bash
make verify LEAGUE=123456                 # public league
make verify LEAGUE=123456 ACCOUNT=1       # private league (account must exist)
# or directly:
python -m api.verify --league 123456 --season 2026 --account 1
```

## Tests

All parsing/sync/metric tests run **offline** against recorded fixtures in
`tests/fixtures/` (no network, no cookies):

```bash
make test    # pytest
make lint    # ruff
```

## Layout

```
/api    FastAPI: config, db, models, crypto, routers/, services/ (espn, sync, discovery, cache, parse), verify.py
/web    Vite + React + TS: src/ (App, api, tokens.css)
/data   edge.db + raw_cache/   (gitignored — holds session cookies)
/tests  fixtures/ + offline unit tests
```

*Unofficial-API disclaimer: ESPN's fantasy API is undocumented and unsupported; this tool
is for personal use with your own accounts' data. Expect occasional breakage.*
