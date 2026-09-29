# Phase 38 (narrowed) — the export memory floor, and where it actually was

Phase 34 concluded: **`t4g.small` dual-slot is viable if and only if the exports stream**, and
recommended implementing streaming before spending the $0.25 to confirm arm64 and CPU credits.

The conclusion was right. **The mechanism was wrong**, and measuring the production code is what
showed it.

## The memory is not in the export writer

`_build_opportunity_universe` loads **every `OpportunityWeek` row for the season** as ORM objects and
then copies each one into a dict — both alive at once — before any scoring, formatting or writing
happens. It is called by four routes, not one:

`GET /api/portfolio/opportunity` · `GET /api/portfolio/opportunity/charts` ·
`GET /api/players/{id}/opportunity` · `GET /api/exports/opportunity.csv`

So this is a **read path**, on every request, and streaming the CSV writer could not have touched it:
by the time the writer runs, the whole universe is already resident. Measured at 99,990 player-game
rows, one case per process (baseline 67.2 MiB):

| stage | peak |
| --- | ---: |
| interpreter + app imports + open database | 67.2 |
| Core select, tuples only | 209.6 |
| **Core mappings → dicts (the fix)** | **232.4** |
| ORM objects only | 308.1 |
| **ORM objects → dicts (what was there)** | **388.1** |
| whole universe, old | 396.1 |
| whole read model (`build_portfolio_opportunity`), old | 396.1 |

The last two lines are the finding: **the entire read model adds nothing measurable on top of the
universe.** Everything downstream — scoring, sorting, the table, the CSV — is noise against the cost
of loading the rows.

## The change

One expression in one function. A Core select yielding mappings, consumed with `yield_per`, building
each dict directly. No ORM instance is ever constructed, so there is no second copy and nothing for
the session to track.

**Equivalence was checked before the swap, not assumed**: same row count, same key order, identical
values row-for-row, and `compute_opportunity_scores` returns an identical result. A faster different
answer is not the same answer.

## Before and after, on arm64

Measured on Linux **aarch64** — which closes one of the gaps Phase 34 explicitly named, since its
figures were x86_64 and `t4g` is Graviton. One case per process, baseline 67.2 MiB.

| rows | seasons | `opportunity.csv` before | after | saved |
| ---: | :-- | ---: | ---: | ---: |
| 25,992 | ~1 | 176.4 | **137.9** | 22% |
| 99,990 | ~4 | 412.3 | **261.6** | 37% |
| 249,984 | ~10 | 888.2 | **513.9** | 42% |

## What that does to the `t4g.small` decision

2 GiB, 25% headroom → **1,536 MiB usable**. Two concurrent requests, plus an idle worker, plus the
ECS agent (~90, AWS published, **not measured**) and Caddy (~30, typical, **not measured**):

| concurrent load | before | after |
| --- | ---: | ---: |
| 2 × 26k | ~540 MiB (35%) | **~463 MiB (30%)** |
| 2 × 100k | ~1,012 MiB (66%) | **~710 MiB (46%)** |
| 2 × 250k | **~1,964 MiB — exceeds the instance** | **~1,215 MiB (79%)** |

Phase 34's failing case was two concurrent 250k exports at 1,711.8 MiB. It now fits, at 79% of
budget — which is fitting, not comfortable.

**R9's ≤256 MiB per process**: met at one season (137.9), missed at four (261.6). The honest
statement is that the realistic case passes and the growth case does not.

## The remaining floor, and the next lever

The dicts are now the cost. `compute_opportunity_scores` takes the whole list and returns roughly one
row per player — 99,990 rows in, 4,166 out — and it adds only ~13 MiB on top of the dicts. So the
memory is spent materialising rows that are about to be aggregated away.

The next lever is pushing that aggregation into SQL, which would make the peak a function of
*players* rather than of *player-games* and flatten the curve entirely. It is not done here because
it changes where an analytics formula is evaluated, and Phase 38's own guarantee is that
"server-side analytics formulas remain unchanged". That is a phase contract, not an afternoon.

## Evidence, and a test that was green for the wrong reason

`tests/test_opportunity_memory_shape.py` — 3 tests. It cannot measure peak RSS: that needs one case
per process, and `/proc/self/clear_refs` does not reset `VmHWM` on this kernel, so a second
measurement in the same process reports the first one's watermark and reads as a flat bounded curve.
Phase 34 lost an hour to exactly that.

So it counts **ORM instance loads** during the universe build, via the mapper-level `load` event.
Zero for a Core select, one per row for an ORM one. Control removed: reverting to the ORM path fails
it with *"the ORM constructed 72 OpportunityWeek instances"*.

**The first version of this test checked the session's identity map instead, and it passed with the
ORM path restored.** SQLAlchemy's identity map holds *weak* references — once the comprehension
finished nothing referenced the objects, they were collected, and the map was empty by the time the
assertion ran. It was caught only because the control was actually removed and the test was expected
to fail. The `load` event records that the instance *existed*, which is what costs the memory, rather
than that it is still reachable afterwards.

A third test guards the instrument: an explicit ORM query must make the counter read 5 for 5 rows. If
the event stopped firing, the main test would pass against a counter that is always empty — the exact
failure it exists to prevent.

## Not done

- The set-based portfolio readers. Phase 33 already measured that after batching **the database is
  not the cost** (DB time 4.5–24% of wall; the rest is serialization), so the query-count refactor
  buys compliance with the ≤25 budget, not latency. For a practice project that is the wrong order.
- Streaming the CSV writer itself. It is now the small term, and it would have been the wrong fix
  before.
- Aggregation in SQL (above).
- Phase 34's live half is still unauthorized, and the arm64 figures here remove one of its three
  stated reasons for running it. CPU credits and OOM behaviour under a real 2 GiB cgroup remain
  untestable offline.

## Scope

No ESPN call, no model call, no AWS call, no spend, no commits. Probe databases were throwaway and
seeded deterministically from seed 20260929. Suite **861 passed / 0 failed**, ruff clean.
