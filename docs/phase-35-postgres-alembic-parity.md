# Phase 35 — PostgreSQL / Alembic Parity (scoped; blocking half complete)

Source plan: `docs/sprint-9/06-phases.md`, Phase 35. Predecessors: Phases 33, 34.

**Outcome required:** make PostgreSQL and Alembic the tested schema authority without adding tenancy.

**Status: the blocking half is done and fully verified on a real PostgreSQL 16. The remainder is named and
deferred, not silently dropped.** The plan allots 6 sessions and 135 operator minutes; this is one
pass, scoped against the operator's standing bar for this project — narrow and time-box, done over
complete.

## What the app could not do before

Three defects, all found by running rather than reading, all of which stop the application dead on
PostgreSQL. Two were carried in from Phase 33; the third was found here.

**1. The app could not open a PostgreSQL connection at all.** `create_engine` passed
`connect_args={"check_same_thread": False}` unconditionally — a SQLite argument psycopg rejects — and
`PRAGMA foreign_keys=ON` was registered on the base `Engine` class, so it fired for *every* connection
including PostgreSQL. **Fixed:** connect args are chosen by URL scheme, and the PRAGMA listener returns
early unless the DBAPI is SQLite. PostgreSQL gets a bounded pool (`pool_size=5, max_overflow=5,
pool_pre_ping=True`) rather than SQLite's threading shim, because ADR-002 puts web and worker on one
2 GiB host and the web process holds the pool for the life of the process.

**2. The four metric uniqueness predicates silently became full unique indexes.** They were declared
with `sqlite_where` only. PostgreSQL ignores that kwarg but **still creates the index, without its
predicate**, so `uq_metric_league` became a plain unique on `(league_id, key)` — which forbids per-team
metrics outright. The second team metric in a league failed. The app did not degrade on PostgreSQL, it
stopped on the first write. **Fixed:** `postgresql_where` added beside `sqlite_where` in both the model
and the baseline migration, verified below as genuinely partial.

**3. Every freshness check would have raised `TypeError`.** The codebase produces aware UTC datetimes
everywhere — 27 call sites of `datetime.now(UTC)`, zero naive `utcnow()` — but all 18 datetime columns
were declared `DateTime` without timezone. Measured on PostgreSQL: an aware value is written and a
**naive** value comes back, so `datetime.now(UTC) - stored` raises
`TypeError: can't subtract offset-naive and offset-aware datetimes`. That is exactly what
`api/services/ffc_adp.py:216` does. It never surfaced in the offline suite because a session's identity
map hands back the same aware object that went in — only a fresh read after commit exposes it.
**Fixed:** all 18 columns and both migrations converted to `DateTime(timezone=True)`.

## Verified

Against PostgreSQL 16.13, a disposable cluster in an isolated container.

| Acceptance item | Result |
| --- | --- |
| App opens a PostgreSQL connection | **PASS** — dialect `postgresql`, database `edge` |
| Empty upgrade (`alembic upgrade head` on an empty database) | **PASS** — `0001` then `0002`, clean |
| Exact schema catalog | 21 tables, 41 indexes, alembic head `0002` |
| Four metric predicates are partial, not full | **PASS** — all four carry their `WHERE`; predicates match the model exactly |
| Four NULL shapes coexist under one key | **PASS** — 4 rows inserted with `key='edge_score'` |
| Each duplicate shape is refused | **PASS** — all four raise `IntegrityError` |
| Concurrent app starts perform zero DDL | **PASS** — 8 concurrent `init_db()`, 0 errors, **0 DDL statements**, schema unchanged at head `0002` |
| SQLite suite unaffected | **PASS** — 806 passed / 0 failed; `ruff check api tests` clean |

The four indexes as PostgreSQL actually built them:

```
uq_metric_league       (league_id, key)                   WHERE team_id IS NULL     AND week IS NULL
uq_metric_league_week  (league_id, key, week)             WHERE team_id IS NULL     AND week IS NOT NULL
uq_metric_team         (league_id, team_id, key)          WHERE team_id IS NOT NULL AND week IS NULL
uq_metric_team_week    (league_id, team_id, key, week)    WHERE team_id IS NOT NULL AND week IS NOT NULL
```

## The TIMESTAMPTZ fix, now verified

Re-run on a fresh PostgreSQL database after the conversion, in a **new session** so no identity map
could hide the result:

| Check | Result |
| --- | --- |
| Value read back after commit | `datetime(..., tzinfo=ZoneInfo('Etc/UTC'))` — **aware** |
| `datetime.now(UTC) - stored` | evaluates, 0.01s — **PASS** (previously `TypeError`) |
| The real caller's shape (`now(UTC) - pulled_at < timedelta(hours=24)`) | evaluates — **PASS** |
| `accounts.created_at` column type | `timestamp with time zone` |
| Naive timestamp columns remaining anywhere in the schema | **0** |

**Model/migration parity, checked directly rather than assumed:** 20 tables in the models, 20 in the
database, none present on only one side; 17 datetime columns compared attribute by attribute between
`Base.metadata` and `information_schema`, **zero mismatches**. That check exists because "the models
and the migrations agree" is the one claim this phase cannot take on trust — an autogenerate that
looks right and drifts is the failure it is named for.

## Deferred, and why

The plan's change surface is wider than this pass. Each of these is named rather than quietly dropped:

- **TLS configuration.** There is no target server to configure against; it belongs with the live
  deployment work, and `database_url` accepts a full URL so `sslmode` needs no new setting.
- **PostgreSQL CI service lane.** Requires CI access this session does not have.
- **SQLite staging/import transform**, and with it the `85,125`-style naive-to-UTC interpretation and
  the "sequences advance beyond max" check. There is no production data to migrate — Phase 31's
  corpus regenerates — so the transform has no input to be tested against yet. **This is the largest
  deferral and it is the one to schedule next** if a real database is ever carried across.
- **BIGINT identity.** Mechanical but touches every primary key and every foreign key; high churn,
  and nothing before tenancy depends on the wider range.
- **JSONB.** The 7 JSON columns still map to `json`, not `jsonb`. No query indexes into them today, so
  the difference is storage and future indexability rather than correctness.
- **Removal of runtime `create_all` on SQLite.** Read deliberately: PostgreSQL is the *release*
  authority and performs zero DDL at start, which is what the acceptance item protects against. SQLite
  keeps `create_all` because it is the offline suite's and a local operator's database, 838 tests build
  their schema through that call, and it has no concurrent-start problem worth the churn. The additive
  `ALTER` survives on the same branch and dies with it.

## Guarantees held

No public deployment, no tenant behaviour, no ESPN behaviour, no analytics formula, no API response
change. No provider or model call, no network egress, no AWS call, no spend. The disposable PostgreSQL
cluster lived only in an isolated container and nothing was installed on the operator's machine.
Backups of every file touched are in `.venv/phase35/`.
