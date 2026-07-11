# Live smoke test — validating one real ESPN league

A short, repeatable manual checklist to confirm ESPN Edge works end-to-end against a
**real** league before trusting the numbers. Offline unit/e2e tests already run in CI
(`make test`); this is the one place we intentionally touch ESPN, by hand, once.

> **Never paste real cookies anywhere they persist.** `SWID` and `espn_s2` are live
> session credentials. Do not put them in logs, terminal history you'll share, commits,
> screenshots, bug reports, or chat. The app encrypts `espn_s2` at rest and never prints
> either value — keep it that way when you report results. If a cookie leaks, log out of
> ESPN to invalidate it and grab fresh ones.

## 0. Prerequisites
- A league you can see in the ESPN app/website (public, or private with your own account).
- Its **league ID** (from the league URL: `...?leagueId=NNNNN`).

## 1. Fresh clone to running
```bash
make setup     # venv + Python deps, npm install, bootstrap .env with a generated FERNET_KEY
make dev       # api on :8000, web on :5173
```
- [ ] `http://127.0.0.1:8000/api/health` returns `status: ok`, the season, and the DB path.
- [ ] `http://127.0.0.1:5173` loads the Portfolio Board (empty state guides you to Manage).
- [ ] The **Status** tab loads and shows API health, season, DB path, and AI on/off.

## 2. Add an account (skip for a public league)
- [ ] In Chrome at fantasy.espn.com (logged in) → DevTools → Application → Cookies, copy
      `SWID` (keep the braces) and `espn_s2`.
- [ ] Manage tab → **Add ESPN account** → paste label + SWID + espn_s2 → Add.
- [ ] The account shows `active`. No cookie value appears anywhere in the UI.

## 3. Add and sync the league
- [ ] Manage tab → **Add league** → paste the league ID or URL → pick the owning account
      (or leave Public) → Add.
- [ ] Click **Sync** on the league row. It completes without an error toast.
- [ ] The Portfolio Board now shows the league with record, PF/PA, standing, last-synced.

## 4. Diff headline numbers against the ESPN UI
Confirm the app matches what you see in the ESPN app/site by eye:
- [ ] League **name** and **size** match.
- [ ] Your team's **record (W-L-T)**, **points for**, and **standing** match.
- [ ] Draft pick count matches (0 for an undrafted league).

Cross-check with the CLI (public-first; falls back to `ESPN_SWID`/`ESPN_S2` from `.env`
or a hidden prompt — never pass cookies on the command line):
```bash
python -m api.verify --league <id>                 # our parser vs ESPN, headline numbers
python -m api.verify --league <id> --cross-check    # also diff against the espn-api library
```
- [ ] `verify` prints league name, size, scoring, standings, my team, draft-pick count.
- [ ] `--cross-check` reports no meaningful diffs on the headline numbers.

## 5. Re-auth path (private league)
- [ ] Temporarily break the account (e.g. re-auth with an obviously stale espn_s2, or wait
      for real expiry): a sync flips the account to `needs_reauth`, and the Status tab's
      "Need re-auth" count increments.
- [ ] Manage tab auto-opens the **Re-auth** form on that account → paste fresh cookies →
      Save. The account returns to `active` and the badge clears. No cookie is shown after.

## 6. Sync diagnostics
- [ ] After a clean sync, the league shows no "last sync failed" note.
- [ ] The Status tab shows `Last sync failed: 0`.
- [ ] (If you forced a failure above) the failed-sync note and count appear, then clear on
      the next clean sync.

## 7. Exports
- [ ] Portfolio board → Export → **CSV**, **JSON**, **XLSX** each download.
- [ ] The `.xlsx` opens clean in Excel/Numbers (a summary sheet + one sheet per league).
- [ ] The numbers in the export match the board (exports are built from the same DB view).

## Done
If every box is checked, the beta is healthy for that league. Re-run this whenever ESPN's
API shape might have changed (a sync starts failing, or numbers look off).
