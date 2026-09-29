# Phase 36 — Table Classification Audit (the gap the attacks could not catch)

Companion to `docs/phase-36-tenant-isolation-kernel.md`. Done before Phase 37 because that report
named this as its own honest gap: **13/13 proves the mechanism on the tables it was pointed at; it
does not prove the table list is complete.** A table wrongly left global is a leak the attack suite
is structurally incapable of finding, because it only probes tables the policies already cover.

All 20 tables classified from their columns and foreign keys, not from their names.

## Result: three tenant-scoped tables had no policy, and two of them are the most sensitive in the database

| Table | Holds | Verdict |
| --- | --- | --- |
| **`accounts`** | `label`, `swid`, `espn_s2_encrypted` | **TENANT — UNPROTECTED.** A tenant's ESPN identity and encrypted session cookie. `leagues.account_id` points here. No tenant column, no policy: every tenant would see every other tenant's ESPN accounts. **The single most sensitive table in the schema.** |
| **`raw_cache`** | `key`, `payload_json` | **TENANT — UNPROTECTED.** Raw ESPN JSON for private leagues — rosters, transactions, member names. The key itself is `{league_id}:{season}:…:acct={hashed swid}:…`, so even the key list leaks league ids and hashed SWIDs. No tenant column, no policy. |
| **`ai_spend_months`**, **`ai_spend_entries`** | the spend ledger and its ceiling | **TENANT — UNPROTECTED, and a correctness bug as much as an isolation one.** The ceiling is enforced against a single global counter row, so one tenant's AI spend consumes every tenant's budget. Isolation and billing both require a tenant dimension here. |

**Correctly global** — public NFL and market reference data, no tenant dimension and none needed:
`players`, `nflverse_player_maps`, `adp_snapshots`, `opportunity_weeks`, `opportunity_imports`.

**Correctly tenant-scoped and covered** (11): `leagues` plus `teams`, `draft_picks`, `metrics`,
`matchups`, `transactions`, `lineup_slots`, `current_roster_snapshots`, `metric_snapshots`,
`ai_reports` — and `current_roster_entries`, with the correction below.

## A correction to the Phase 36 report, one turn after writing it

That report says ten child tables received `ENABLE` + `FORCE ROW LEVEL SECURITY` "each with `USING`
**and** `WITH CHECK`". **That is false for one of them.**

`current_roster_entries` has **no `league_id` column** — it reaches its league through
`snapshot_id → current_roster_snapshots.league_id`. The policy loop generated
`USING (league_id IN (...))` for it, which cannot compile against a column that does not exist, so
that `CREATE POLICY` failed. **The error was filtered out of the `psql` output by a `grep -v` that
was suppressing routine `NOTICE`/`CREATE` noise**, and the claim was written from the filtered view.

Consequence, stated precisely: the table got `ENABLE` + `FORCE` with **no policy**, and a table in
that state denies everything to a non-owner role. So it fails **closed** — broken rather than leaky —
and attack #5 probed `teams`, not this table, so nothing caught it. The correct predicate is:

```sql
USING (snapshot_id IN (
  SELECT id FROM current_roster_snapshots
  WHERE league_id IN (SELECT id FROM leagues WHERE tenant_id = current_tenant())))
```

Two lessons, both already in this project's ledger and both earned again here:

- **Filtering tool output to reduce noise can filter out the errors.** The `grep -v` that hid
  `NOTICE` also hid `ERROR`, and the report was written from what survived the filter.
- **A pass on a sample is not a pass on the class.** Ten tables were claimed from a loop that ran over
  ten names; nine of them worked.

## What Phase 36 must now carry

1. A tenant dimension on `accounts` — or the tenant reached through it — plus policy and `FORCE`.
2. The same for `raw_cache`. Its primary key is the cache key string, so this needs a real column,
   not a predicate over the key's shape.
3. A tenant dimension on the spend ledger, which is a **ceiling-semantics change** and not only an
   isolation one: a per-tenant ceiling is a different product decision from a global one and should be
   made deliberately.
4. The corrected `current_roster_entries` policy, via `snapshot_id`.
5. **A completeness check that fails the build**, not another audit. A test that enumerates
   `Base.metadata.tables`, subtracts a written-out allowlist of genuinely global tables, and asserts
   every remaining table has RLS enabled, forced, and at least one policy. A new tenant-scoped table
   added without a policy should break the suite, not wait for the next audit.

Item 5 is the one that matters most: this audit is a point-in-time answer, and the reason it was
needed is that nothing enforces the invariant.

## Method

Classification is from `Base.metadata` — every table's columns and foreign keys — not from names.
Read-only; no database was modified and nothing was executed against a live cluster.
