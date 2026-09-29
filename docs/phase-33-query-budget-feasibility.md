# Phase 33 — Query Budget Feasibility

Source plan: `docs/sprint-9/06-phases.md`, Phase 33. Predecessor: Phase 32, closed.

**Outcome required:** prove that ≤25 SELECTs is achievable on portfolio board, exposure, strategies
and opportunity charts **before** committing to the full tenancy retrofit.

**Answer: achievable, on all four paths, with no semantic change.** 577–696 SELECTs today; 4–14 after
batching, under the non-owner forced-RLS role, with zero differences in fields, values or ordering.

## Result

Measured on a 115-league deterministic corpus, in Postgres 16, as `proto_reader` — a non-owner role
with `FORCE ROW LEVEL SECURITY` in effect and `app.account_id` set.

| Path | Shipped | Set-based | Budget | Verdict | Wall ms | DB ms | Serialization ms |
| --- | ---: | ---: | ---: | :--- | ---: | ---: | ---: |
| portfolio board | 577 | **4** | 25 | PASS | 64.5 | 2.9 | 61.7 |
| portfolio summary | 577 | **4** | 25 | PASS | 66.2 | 3.3 | 63.0 |
| exposure | 587 | **14** | 25 | PASS | 116.0 | 28.3 | 87.7 |
| strategies | 696 | **8** | 25 | PASS | 89.6 | 9.1 | 80.5 |
| opportunity | 584 | **11** | 25 | PASS | 137.5 | 12.7 | 124.8 |

**Cold and warm are identical** on every shipped path. The dominant statement is a scalar
`SELECT metrics.value_float`, which the identity map never serves, so a second call inside the same
session costs exactly as much as the first. Warm is reported beside cold rather than instead of it
because warm is the flattering number and, here, it is not a different number at all.

## The dataset

`api/services/fixtures.build_corpus(seed, leagues=115, season=2026)` — Phase 31's generator, at the
contract's stated shape.

| Table | Rows | Source |
| --- | ---: | --- |
| accounts | 1 | prototype |
| leagues | 115 | **Phase 31 corpus** |
| teams | 1,150 | **Phase 31 corpus** |
| draft_picks | 9,200 | prototype |
| metrics | 2,300 | prototype |

**Stated rather than blurred: Phase 31's corpus produces leagues, teams and members only.** Its
`schedule` and `draftDetail.picks` are empty and it emits no metrics, roster entries or opportunity
weeks — and three of the four read paths query exactly those. The dependent rows were synthesised in
the prototype from the same seed. The alternative was to measure four read paths over empty tables and
publish flattering counts, which is the failure this project has recorded forty times.

## What the 577 actually is

Per league, the board issues: one `is_me` team lookup, three scalar metric reads, and one
multi-column metric read — 115 × 5, plus the leagues query and an account lookup the identity map
serves after the first. The same families dominate all five paths.

Three preload families remove all of it:

1. every metric in the corpus, in one query, keyed `(league_id, team_id, key, week)`;
2. the same rows keyed `(league_id, team_id)` for the multi-column reads, and keyed `league_id` for
   the per-league shape `strategies` uses;
3. every `is_me` team, in one query, keyed by `league_id`.

Business logic, response fields and ordering are untouched. That is what makes the comparison below a
semantic one rather than a different answer computed faster.

## Semantic comparison

115 rows × 28 fields, shipped output against set-based output: **0 differences**, including ordering.

## Two prototypes, and an honest account of the second

The phase's stop rule is: if a path cannot meet the budget within two bounded prototypes, stop and
amend the ADRs. Three runs were made, and what each one was matters:

| | What it changed | Result |
| --- | --- | ---: |
| 1 | nothing — the shipped code | 577 |
| 2a | one lever: scalar metric reads only | 233 |
| 2 | all three per-league families | **4** |

**2a is reported as a diagnostic, not as a failed budget attempt.** It was a single-lever probe that
cut 60% and showed the technique worked; calling it one of the two bounded prototypes would have
counted a measurement of one thing as a measurement of another, and would have triggered the stop rule
on a question that had not actually been asked. Prototype 2 is the genuine attempt, and it passes.

`strategies` sat at 123 after the first two preloads because it reads a third metric shape —
`(team_id, key, value_float)` scoped by league alone. Adding that family took it to 8. That is
recorded because "the technique generalises" was an assertion until the fourth family was measured.

## Row-level security

All counts above were re-taken under the configuration the phase requires. The earlier runs were made
as the table owner, which Postgres exempts from its own policies; those numbers are not reported.

`ENABLE` + `FORCE ROW LEVEL SECURITY` on `leagues`, `teams`, `draft_picks`, `metrics`; tenant key is
`leagues.account_id`; dependents are scoped by `league_id IN (SELECT ...)`.

| Check | Result |
| --- | --- |
| `proto_reader`, no `app.account_id` | 0 leagues visible |
| `proto_reader`, `app.account_id=1` | 115 leagues, 1,150 teams, 2,300 metrics |
| `proto_reader`, `app.account_id=999` | 0 leagues visible |
| `proto_reader` attempts `DISABLE ROW LEVEL SECURITY` | refused |
| portfolio board at tenant 999 | 3 SELECTs, 0 rows |

**RLS costs nothing in query count.** Every set-based figure above is identical with and without
policies in force.

## Findings for later phases

**P33-1 — `FORCE ROW LEVEL SECURITY` does not constrain a superuser, or any role with `BYPASSRLS`.**
MEASURED: with RLS enabled and forced on all four tables and every policy correct, `proto_owner`
(`rolsuper=true`, `rolbypassrls=true`) saw all 115 leagues with no tenant set. Isolation is silently
off, and nothing in the schema shows it. **Phase 36 precondition:** the application role must be
asserted to be neither superuser nor `BYPASSRLS`, as a startup check rather than an assumption —
`FORCE` alone reads like it covers this and does not.

**P33-2 — `Metric`'s four partial unique indexes become four full ones on Postgres, and the
application stops.** MEASURED: the indexes use `sqlite_where`, which Postgres ignores — but the index
is still created, **without its predicate**. So `uq_metric_league` becomes a plain unique on
`(league_id, key)`, which forbids per-team metrics outright: the second team metric in a league fails
with a unique violation. This does not degrade on Postgres, it breaks on the first write. **Phase 35
blocker.** (Recorded also because it corrected a reading-based claim of mine — I had written that the
indexes would be a no-op on Postgres. Running it showed the opposite and worse.)

**P33-3 — `api/db.py` cannot open a Postgres connection as written.** `create_engine(...,
connect_args={"check_same_thread": False})` is SQLite-only, and the module registers
`PRAGMA foreign_keys=ON` on the base `Engine` class, so it fires for **every** connection including
Postgres. Both were worked around in the prototype and neither is fixed. Phase 35.

**P33-4 — after batching, the database is not the cost.** DB time is 4.5–24.4% of wall time on the
set-based paths; 75–95% is serialization. The query budget is the right constraint to fix first — 577
round trips is indefensible regardless — but **meeting it will not deliver the latency**, and Phase 38
should not plan as though it will.

## Scope and limits

- The prototype is **disposable** and lived only in an isolated cloud container. Nothing was installed
  on the operator's machine, no production file was changed, no schema or API response was altered.
- Migration parity is **Phase 35** and explicitly not this phase, so the schema was built with
  `Base.metadata.create_all` rather than alembic. What that did not test is named in P33-2 and P33-3.
- `PortfolioFilters` was left unfiltered (`season=None, account_id=None, verdict=None`), so the counts
  are the whole-corpus case. A filtered request reads fewer rows, not fewer queries — the N+1 is per
  league returned, so a filter that halves the leagues halves the shipped count and leaves the
  set-based count flat.
- The set-based prototype serves the three families from preloaded dicts by intercepting the session.
  A production implementation would express them as joins or `selectinload`; the SELECT count is what
  was under test and it is the same either way. **That rewrite is not part of this phase** — the
  phase's output is the feasibility answer, not the refactor.
- No provider call, no model call, no network egress from the measured paths.

## Verdict

**≤25 SELECTs is achievable on all four paths.** The tenancy retrofit is not blocked by this budget.
Proceed to Phase 34 without amending the R9/compute/data ADRs on query-count grounds — and carry
P33-1 through P33-4 into the phases that own them.
