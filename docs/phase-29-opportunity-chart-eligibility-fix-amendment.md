# Phase 29 corrective patch — exact pre-existing E2E exception

Status: **draft; operator acceptance required; no exception is active yet**

Parent contract: `docs/phase-29-opportunity-chart-eligibility-fix.md`, immutable accepted SHA-256
`49431b0725bfd89f082b6d5543135d70837ba5c872901bc0e88207c9fde03e0c`.
The parent file remains unchanged. This additive amendment authorizes no production/test change.

## Question and evidence

May this bounded chart correction close when its lint/build and targeted regression pass, but the
complete browser suite reproduces one unrelated, previously documented failure?

Measured candidate-1 results:

- Targeted `npm run e2e -- --grep 'one-game raw opportunity points'`: 1 passed, Playwright 4.9s,
  `/usr/bin/time -p` real 5.41s.
- Full `npm run e2e`: 56 passed, 1 failed, Playwright 11.8s, timed real 12.39s. All Opportunity
  tests, including the new regression, passed.
- Sole failure: `team logo failure keeps the team initials visible`, at
  `web/e2e/smoke.spec.ts:2265`; assertion expects CSS `display:none` but the image is
  `display:block` with `opacity:0`.
- The complete failing test block is byte-for-byte identical to the pre-write test snapshot:
  `diff -u <(sed -n '/test("team logo failure keeps the team initials visible"/,/^});/p'
  /private/tmp/espn-edge-phase29-eligibility-base.HNaRYY/c359421a764add3024eb485491cce93dbaa11ecfd0b433e47553200dea65f807.ts)
  <(sed -n '/test("team logo failure keeps the team initials visible"/,/^});/p'
  web/e2e/smoke.spec.ts)` returned exit 0 with no output under zsh.
- `web/src/components/TeamIdentity.tsx` is unchanged at SHA-256
  `771a6e29140a0dcba2e1f25f7b3c659b7a97ba92c631f280b9600633d2f90547`, matching the pre-write
  dirty-path checksum inventory. Phase 30 E30.4 records the same earlier 55/56 browser-suite
  failure.
- Independent QA reviewed candidate-1 manifest SHA-256
  `4d5c07af986253d817a0a30fd0880fcf48ececf8474eae6850f28b79d01383f0`: correction behavior,
  new synthetic oracles, exact scope, protected hashes and scratch rollback pass. QA returns
  NO-CLOSE only because it cannot waive the parent's full-suite gate.

These timings/counts are measured on the current macOS/Chromium environment, not forecasts or
cross-browser claims. No live provider response or real production deployment was tested.

## Lens and selection

The lens is bounded practice-project scope without a false green gate.

1. Fix the unrelated logo behavior/test under this lease: rejected; it widens the accepted scope.
2. Leave the chart correction indefinitely blocked by the known baseline failure: safe, but adds no
   assurance about this correction.
3. Accept an exact, auditable baseline exception for corrective closure only: **proposed judgment
   call**, subject to operator acceptance.

## Synthesis: exact exception, not a weakened test

The parent full E2E command must still run in full. It is not skipped, filtered, rewritten or called
passing. Corrective closure is permitted only when:

- the only failing full-suite test is the exact unchanged logo test named above;
- its failing assertion and TeamIdentity component remain at the verified baseline;
- there are no additional browser failures and all parent correction-specific criteria, lint,
  build, targeted regression, boundary checks and rollback evidence pass;
- independent QA verifies those conditions and the final candidate hashes; and
- evidence and final handoff state **full browser suite remains red: 56/57 passed**.

The exception applies only to this Phase 29 corrective patch. It does not approve the logo behavior,
waive any later release gate, declare the application/release ready, or authorize an unrelated fix.
Resolving the logo issue later requires its own scoped authority. Any new failure blocks closure.

## Boundaries, ownership and cost

All parent owned/forbidden paths, analytics/API/schema/provider/data guarantees, rollback procedure
and review roles remain unchanged. No new production or test edit is authorized. After acceptance,
UI/UX may receive an **evidence-only** lease to append final measured results; Agent 1 manages
orchestration/closure and QA independently rechecks the hashes.

Schema/API/config/provider/data-migration delta: none. Incremental cost: **$0 one-time and
$0/month**. Accepted residual: the global browser suite still has its pre-existing logo-test
failure. No deployment, commit, merge, external call or spend is authorized.

## Gate

Independent QA reviews this amendment and returns its exact SHA-256. The human must accept that
digest before the exception can take effect. Until then the candidate stays frozen, the production
write lease remains closed, and the corrective phase is blocked on this human risk choice.
