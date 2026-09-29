# Phase 29 corrective patch — Opportunity chart eligibility

Status: **draft; not accepted; no implementation authority**

This is a narrow corrective contract for the implemented Phase 29 visualizations. It temporarily
pauses Phase 31 Unit 4 only for this bounded fix; it does not amend Phase 31 or authorize work on
its spend-ledger/Alembic surface.

## Outcome and risk retired

Make the Opportunity chart render the same player set that the backend declares chart-eligible.
When the API returns raw one-game rows but reports `point_count=0`, the page must show the existing
pending/empty state and must not draw those rows inside a fallback domain.

The defect is presentation inconsistency, not missing Week 1 data. A read-only aggregate over the
current local database measured 320 distinct 2026 Week 1 player-game rows across 16 games: 89 RB,
151 WR and 80 TE. All 320 joined to a matched ESPN-to-GSIS mapping. For the selected WR chart, 66
top-250 rows had finite target/air-share values, but every row had only one observed game. The
backend therefore correctly reported zero eligible points; the frontend plotted the raw rows
anyway and the empty-population fallback domain clipped 61 of 66, leaving five visible.

Current behavior is grounded in:

- `api/services/opportunity.py`: chart eligibility requires `sample_games >= 2` plus finite axis
  values; `point_count` and `OPP-CHART-NO-SAMPLE` use that rule, while `points` contains the broader
  ranked row set.
- `web/src/components/OpportunityChartExplorer.tsx`: `chartPoints` currently checks rank, team and
  finite metrics but omits the two-game eligibility predicate.
- `web/e2e/smoke.spec.ts`: the sparse fixture sets both `point_count=0` and `points=[]`, so it does
  not exercise the real Week 1 response shape.
- `docs/phase-29-opportunity-visualizations.md`: a player needs at least two observed games and
  finite values for both axes; early-season no-sample is an expected state.

## Question, lens, selection, synthesis

1. **Question:** Should one-game raw rows be drawn when the server says the active chart has no
   eligible sample?
2. **Lens:** One source of analytical truth and an honest early-season empty state. A chart that
   reports zero valid points while displaying a clipped subset is less trustworthy than a clear
   pending state.
3. **Selection:**
   - Keep plotting raw Week 1 points and relabel them provisional: rejected because it changes the
     accepted two-game chart contract and requires coordinated server count/domain semantics.
   - Remove one-game rows from the API: valid future cleanup, but wider than the requested frontend
     correction and changes the response payload.
   - Mirror the accepted point predicate and honor the server's chart-level drawability counts:
     **selected**; smallest reversible fix with no API or analytics change.
4. **Synthesis:** Filter chart-render candidates to `sample_games >= 2` before team/metric display
   filtering, and draw a chart only when the server reports both `population_point_count >= 3` and
   `point_count >= 2`. Preserve the server-provided counts, domain, warning and table data. A
   response with raw one-game points and `point_count=0` renders `OPP-CHART-NO-SAMPLE`, no
   plot/markers, the existing empty-state copy, and the still-usable Opportunity table.

The frontend predicate is intentionally a **defensive mirror**, not a new analytics authority.
Current code and the accepted API contract have drifted: `points[]` contains the broader ranked row
set while server counts/domains use the two-game predicate, despite Phase 29's promise that React
would receive chart-ready eligible points. Code wins as current behavior. This patch does not
redefine that behavior because the user requested the frontend correction; a later API cleanup
should either return chart-eligible points only or expose explicit per-chart eligibility so the
mirror can be removed.

## Dependencies and authority

- Original Phase 27 and Phase 29 contracts remain authoritative for scoring and visualization
  semantics.
- Phase 30 is complete. Phase 31 Unit 4 remains pending and is neither changed nor advanced here.
- Implementation begins only after independent QA contract review and explicit operator acceptance
  of this file at an immutable SHA-256 digest.
- Base Git identity is `7f81c76e24fd50635071f8b53f05a4aeb11881de`. Because the working tree is intentionally dirty and
  the Phase 29 component is currently untracked, Git diff alone is not evidence. The pre-change
  byte identities are:
  - `web/src/components/OpportunityChartExplorer.tsx`:
    `eaecf362bcabe51b25ce99e45b3301e835375218f726748b221aad5dcc42ef67`
  - `web/e2e/smoke.spec.ts`:
    `c359421a764add3024eb485491cce93dbaa11ecfd0b433e47553200dea65f807`
  - `api/services/opportunity.py` (must remain unchanged):
    `b9082ab3aa29d21b398e0182e4d3602749f690017924541428668e55540b68dc`

## Ownership and write lease

The sole implementation writer is `ui_ux`.

Owned paths:

- `web/src/components/OpportunityChartExplorer.tsx`
- the directly corresponding mocked case in `web/e2e/smoke.spec.ts`
- new append-only `docs/evidence/phase-29-opportunity-chart-eligibility-fix.md`

Forbidden paths:

- all `api/**`, schemas, database files and migrations;
- all other `web/**` files and all non-corresponding E2E cases;
- Phase 27, Phase 29, Phase 30 and Phase 31 contracts;
- provider, sync, mapping, refresh, opportunity-score, export and recovery behavior;
- `.env`, credentials, raw cache, fixtures containing private data, external services and network
  calls.

`qa_test` independently reviews the accepted contract before implementation and the exact frozen
candidate afterward. The reviewer does not fix the candidate. Agent 1 manages the lease and
reconciles findings; the operator alone accepts this contract or any material residual risk.

## Explicit deltas and guarantees

| Surface | Delta / guarantee |
| --- | --- |
| Frontend behavior | Add the accepted two-observed-game predicate to chart-render candidates. Early Week 1 raw rows remain available to the table but not the chart. |
| E2E coverage | Add the missing `point_count=0` plus non-empty one-game `points` response shape. |
| API/schema | **No change.** Response fields, counts, warnings and status codes remain byte-for-byte governed by the backend. |
| Database/migration | **No change.** No DDL, data mutation, refresh or backfill. |
| Analytics | **No change.** No score, signal, domain, median, mapping or threshold calculation moves into React. |
| Provider/data boundary | **No change.** Tests are mocked/offline; no ESPN or nflverse call and no private identifier in evidence. |
| Security/configuration | **No change.** No auth, session, CSP, environment or secret surface. |
| Cost | **$0 one-time and $0/month.** No service, dependency or variable-cost path. |
| Four unmatched players | Out of scope and unchanged; they are unrelated to the 320 mapped Week 1 rows. |

The frontend also treats the chart as pending whenever the server reports fewer than two returned
eligible points or fewer than three eligible population points. The existing table remains visible
and usable in the pending chart state. The existing
`OPP-NO-SAMPLE` and chart-specific `OPP-CHART-NO-SAMPLE` messages remain unchanged. Week 2 is not
hardcoded: the gate is observed-game count, so a bye/inactive player with only one observed game
remains ineligible even after calendar Week 2.

## Acceptance criteria

1. Given an otherwise valid chart response with `point_count=0`,
   `population_point_count=0`, an `OPP-CHART-NO-SAMPLE` warning and at least one raw point whose
   `sample_games=1` and axis metrics are finite:
   - `opportunity-chart-plot` is absent;
   - `opportunity-chart-marker` count is zero;
   - `opportunity-chart-empty` is visible with the existing no-sample message;
   - the chart warning remains visible; and
   - the Opportunity table remains visible, contains a mocked raw Week 1 row, and retains its
     accessible table semantics.
2. A response with `point_count=1` remains pending even when that point has `sample_games >= 2` and
   finite axis metrics.
3. A response with `population_point_count=2` remains pending even when `point_count >= 2`.
4. A drawable mixed response with `population_point_count >= 3`, `point_count >= 2`, at least two
   eligible points and at least one raw one-game point:
   - renders only the eligible points;
   - renders exactly `point_count` markers in the all-team view;
   - never renders the one-game row as a marker; and
   - preserves the raw row in the table.
5. While the server reports no sample, selecting a team does not replace the no-sample empty-state
   message with a misleading team-metric message.
6. Team filtering, chart switching, zoom, selection, tooltip and PNG behavior for eligible points
   retain their existing tests.
7. The frontend does not recompute `point_count`, domains, medians, opportunity scores or signals;
   it consumes the server counts as the chart-level drawability authority.
8. `api/services/opportunity.py` retains the exact pre-change SHA-256 recorded above.
9. Every new inline mock uses synthetic names, teams and IDs. No test performs a provider/network
   call; Playwright continues to mock `/api`.
10. The candidate changes only the three owned paths and passes independent QA review.

## Negative cases

- A one-game point with finite values must not render merely because it fits the fallback domain.
- One otherwise eligible point must not render when `point_count=1`.
- Two returned eligible points must not render when `population_point_count=2`.
- A one-game point must not become eligible after selecting a team or changing chart tabs.
- A two-game point with one missing active-axis metric remains omitted.
- The frontend must not replace the observed-game predicate with `through_week >= 2`; byes and
  inactive games are not observations.
- The fix must not suppress the table or the no-sample warning.

## Offline evidence and commands

Before the write lease opens, Agent 1 creates content-addressed byte snapshots of both dirty
frontend files under a freshly allocated `/private/tmp/espn-edge-phase29-eligibility-base.*`
directory, records the exact path and SHA-256 manifest in the lease, and verifies both copies match
the hashes in this contract. Git checkout is not a rollback mechanism for this patch. After the
candidate is frozen, rollback is rehearsed on scratch copies: replace candidate scratch files with
the two base snapshots and prove their hashes return exactly to the values above without touching
the working tree.

The implementation evidence records environment, exact commands, durations and results as
E29F.1–E29F.3 under the new evidence file.

```bash
(cd web && npm run lint)
(cd web && npm run build)
(cd web && npm run e2e -- --grep "one-game raw opportunity points")
(cd web && npm run e2e)
shasum -a 256 api/services/opportunity.py
```

E29F.1 records the pre-change mismatch and aggregate-only SQLite queries. E29F.2 records the
targeted and full frontend gates. E29F.3 records the three-path candidate manifest, protected
backend hash, scope inspection and rollback result. Numeric claims are labelled measured or
derived; no names, provider identifiers or payloads enter evidence. The new test title must contain
the exact phrase `one-game raw opportunity points` so the targeted command cannot pass vacuously.

## Rollback and failure mode accepted

`rollback_safe=true` only after the pre-write snapshots and scratch rollback rehearsal above pass.
Rollback restores the two frontend/test base byte snapshots; no data or schema reversal is needed.
The accepted failure is that Week 1 has no scatter plot even though raw
one-game rows exist in the table. This sacrifices an unstable provisional visualization to keep the
chart's displayed population consistent with its analytical eligibility contract.

Revisit only through a new contract if the product intentionally wants provisional one-game charts.
That change must redefine point counts, domains, references, warnings and labels together; simply
removing the warning is not acceptable.

## Operator and review budget

- Implementation: one bounded `ui_ux` session.
- Independent QA contract/candidate review: one bounded session.
- Operator: approximately 10 minutes for contract acceptance and final evidence review.
- First cut under time pressure: full cross-browser expansion beyond the existing Playwright
  project. The targeted regression, full current E2E suite, lint/build and independent review are
  not cut.

## Human gate

Stop after independent contract review and reconciliation. Implementation is blocked until the
operator explicitly accepts this contract's final SHA-256 digest. Acceptance authorizes only safe,
reversible local changes and offline tests within the owned paths; it does not authorize a commit,
merge, deployment, provider call, data mutation or spend.
