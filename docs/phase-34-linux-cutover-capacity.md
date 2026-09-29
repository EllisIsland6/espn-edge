# Phase 34 — Linux Cutover Capacity (offline evidence; live run not authorized)

Source plan: `docs/sprint-9/06-phases.md`, Phase 34. Predecessor: Phase 33, complete.

**Outcome required:** choose the compute/cutover mode from target-Linux evidence rather than macOS RSS.

**Status: the decision is made, and it is conditional.** `t4g.small` dual-slot is viable **if and only
if the exports stream**. Buffered, no candidate instance in the phase's list is safe — including
`t4g.medium`. Streaming is therefore not an optimisation to schedule, it is the precondition for the
cheapest option being available at all.

**The phase is not closed.** Its live half — a disposable `t4g.small` under a $0.25 cap with Cost
Explorer evidence — requires AWS API calls and real spend, neither of which is authorized. See
"What is not done" below. The offline half is complete and it is the half that decides the answer.

## The finding

ADR-002 records the buffered export peaking at **435 MiB** and R9 requiring streaming and **≤256 MiB**
before cutover. The 435 is macOS RSS. On Linux, measured:

| Rows | Payload | Buffered peak | Streamed peak | Ratio |
| ---: | ---: | ---: | ---: | ---: |
| 26,000 (≈1 NFL season) | 7.73 MiB | **177.2 MiB** | **97.6 MiB** | 1.8× |
| 100,000 | 29.75 MiB | **407.3 MiB** | **97.7 MiB** | 4.2× |
| 250,000 | 74.36 MiB | **856.0 MiB** | **97.8 MiB** | 8.8× |

Process baseline — interpreter, app imports, database open, no export — is **92.0 MiB**.

Two things follow, and they are the whole phase:

**The buffered export crosses R9's 256 MiB budget between 26,000 and 100,000 rows**, i.e. between one
and roughly four NFL seasons of player-game data. It is not a far-off limit. Linux is *not* materially
cheaper than the macOS figure the ADR recorded: 407 MiB at 100k rows brackets the 435 that phase
exists to re-derive, so the ADR's number was sound and its conclusion holds on the target platform.

**The streamed export is flat at ~98 MiB across a 10× payload range** — 6 MiB above the bare process
baseline, and essentially independent of data volume. That flatness is the evidence it is genuinely
bounded rather than merely smaller.

## One and two concurrent exports

The phase requires both. Each export measured alone in a fresh process and summed, because two real
processes cannot share a high-water mark.

| Mode | Rows each | One | Two concurrent | Two, vs 25% headroom on 2 GiB |
| --- | ---: | ---: | ---: | :--- |
| buffered | 26,000 | 177.2 | 354.4 MiB | ok |
| buffered | 250,000 | 856.0 | **1,711.8 MiB** | **OVER** — and that is before ECS agent, Caddy, worker or web |
| streamed | 26,000 | 97.6 | 195.4 MiB | ok |
| streamed | 250,000 | 97.8 | **195.4 MiB** | ok — flat |

## The capacity arithmetic

`t4g.small` is 2 GiB = 2,048 MiB. At 25% headroom the usable budget is **1,536 MiB**.

| Component | MiB | Source |
| --- | ---: | --- |
| ECS agent | ~90 | **AWS published guidance, NOT measured** — no AWS access |
| Caddy | ~30 | **typical, NOT measured** |
| worker (idle app process) | 92 | measured on Linux |
| web task, active | 98 | measured on Linux (baseline + streamed export) |
| second web slot (dual-slot) | 98 | measured on Linux |
| **total, streamed, dual-slot** | **~408** | **27% of the 1,536 MiB budget** |
| same stack with one buffered 250k export | ~1,166 | 76% of budget, single export |
| same stack with two buffered 250k exports | ~2,022 | **exceeds the whole instance** |

## Recorded choice

**`t4g.small` dual-slot, conditional on streaming exports.** Not `t4g.medium`, and not staged drain
with brief unavailability — neither is needed once the exports stream, and neither rescues the buffered
path: two concurrent 250k buffered exports are 1,712 MiB of export alone, which `t4g.medium`'s 4 GiB
survives only until the dataset grows again. Paying +$12.26/month to postpone an unbounded growth curve
is not a fix.

**The runbook language does not need to change before Phase 42** — dual-slot fits, with room. The
conditional is the part to carry forward: **if the streaming work is dropped or deferred, this choice
is void** and the phase must be re-decided, because the buffered path fails on every option offered.

## Streaming equivalence

The prototype yields bounded chunks from a Core select with `yield_per`, never materialising rows.
Output is **byte-identical** to the buffered path — SHA-256 prefix `9ef746144158e828` both ways at
26,000 rows. A faster different answer would not be the same answer.

Two implementation facts worth carrying, both measured rather than assumed:

- **An ORM `yield_per` is not sufficient.** It still builds a persistent object per row and registers
  it in the identity map; expunging mid-iteration invalidates the map the iterator is still using, and
  it raises. Core rows are tuples the session never tracks, which is what makes the peak flat rather
  than merely smaller. A "streaming" rewrite that keeps the ORM measures the same as the old one.
- **The CSV writer's defaults matter.** The first prototype used `lineterminator="\n"` and skipped
  `_csv_value`; output came out 26,001 bytes short at 26,000 rows — exactly one per row. Matching
  `_dict_rows_csv` exactly is what makes the equivalence check meaningful.

## A measurement error, recorded

The first streamed run reported a peak of **1721.9 MiB at every scale**. That was not a measurement:
`/proc/self/clear_refs` does not reset `VmHWM` on this kernel, so the figure was the high-water mark
left by the buffered runs earlier in the same process. **The tell was that it was constant across a
10× payload range** — a real bounded measurement is flat, but so is a stale watermark, and the two are
distinguishable only by knowing which process ran what. Every number above was re-taken with one case
per process. Same defect class this project has recorded forty-plus times: a figure that reads as an
answer to the question asked.

## What is not done, and why

**The live half of this phase is not authorized.** It requires launching a disposable arm64
`t4g.small`, ECS/Caddy/worker under load, CPU-credit observation, Cost Explorer evidence and teardown.
That is AWS API calls and real spend against the operator's account — outside the standing constraints
and not something to infer consent for.

What the offline evidence cannot supply:

- **arm64 behaviour.** Measured on x86_64. Python object sizes are architecture-sensitive at the
  margins; the flatness of the streamed path is structural and would not change, but the absolute
  ~98 MiB could move.
- **ECS agent and Caddy footprints.** Published/typical figures above, labelled as such. They are ~120
  of the ~408 MiB total, so an error there moves the total but not the verdict.
- **CPU credits.** `CpuCredits=standard` with the 24-credit/hour earn rate and the 48-credit alarm is
  a T-series billing behaviour; it has no local analogue and is untested.
- **OOM and restart behaviour** under a real 2 GiB cgroup limit. The container has 8 GB, so peaks were
  measured rather than induced.
- **Cost Explorer evidence and teardown**, which the phase names as part of done.

**Recommendation: do not launch the instance yet.** The offline evidence already decides the question
the instance was to answer, and it decides it against the buffered path on every candidate size. The
useful sequence is: implement streaming, then spend the $0.25 confirming arm64 and CPU credits on a
stack that can actually fit. Launching now would buy a measurement of a configuration that is not
going to ship.

## Scope and guarantees

- No real data, no cookie, no provider call, no model call, no AWS call, no spend.
- Disposable prototype in an isolated cloud container; **nothing installed on the operator's machine
  and no production file changed.** No streaming code was added to the repo — the prototype is
  measurement scaffolding, and the implementation is Phase 38's or a follow-on's to own.
- Dataset is `OpportunityWeek` at player-game grain, deterministic from seed 20260928, spanning
  seasons beyond 25,200 rows because ~1,400 players × 18 weeks is one season's worth.
- Phase 31's corpus emits no `OpportunityWeek` rows at all — the same gap Phase 33 recorded — so the
  heavy-table rows are prototype-generated and that is stated rather than blurred.
