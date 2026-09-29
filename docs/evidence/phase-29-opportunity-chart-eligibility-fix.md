# Phase 29 corrective chart-eligibility evidence

Accepted contract SHA-256: `49431b0725bfd89f082b6d5543135d70837ba5c872901bc0e88207c9fde03e0c`.
Base Git: `7f81c76e24fd50635071f8b53f05a4aeb11881de`; dirty base snapshots:
`/private/tmp/espn-edge-phase29-eligibility-base.HNaRYY`.

## E29F.1 — pre-change mismatch and validity domain

Classification: measured code inspection; inherited aggregate measurements, not remeasured here.
UTC time: 2026-09-16T20:39:30Z.
Environment: macOS 26.5 build 25F71, Darwin arm64; Node v25.6.1, npm 11.9.0.
Method: `sw_vers`, `uname -sm`, `node --version`, `npm --version`; inspect
`OpportunityChartExplorer.tsx` against its exact base snapshot.
Result: base chart predicate checked rank/team/finite axes but not observed games; rendering
required only one filtered point. Backend eligibility/count rules were retained unchanged.
Dataset: new inline schema-shaped synthetic responses only; no live or private dataset read.
The accepted contract records prior aggregate SQLite results and original diagnostic commands;
those numbers are not independently remeasured by this frontend-only lease.
Validity domain: presentation regression, not provider completeness or live refresh behavior.

## E29F.2 — frontend gates

Classification: measured.
Environment and dataset: as E29F.1; existing installed dependencies; Chromium project with
mocked `/api/**`, no provider access.
Method/results:

- `(cd web && npm run lint)`: exit 0; tool wall time 2.636764583 seconds.
- `(cd web && npm run build)`: exit 0; tool wall time 3.644472709 seconds; Vite 6.4.3.
- `(cd web && npm run e2e -- --grep 'one-game raw opportunity points')`: exit 1 before
  test execution; sandbox rejected Vite's local `127.0.0.1:5173` bind with `listen EPERM`.
  This is not a passing test or a demonstrated product failure. Agent 1 must rerun with
  local-server permission, followed by `(cd web && npm run e2e)`.

New synthetic case exercises nonempty one-game rows with zero server points, team selection,
one returned eligible point, only two population points, drawable mixed rows, null active metric,
and calendar Week 2 with only one observed game. Raw table row stays accessible throughout.
Limitation: E2E remains unverified at writer handoff; do not declare release readiness.

## E29F.3 — boundary, scope and scratch rollback

Classification: measured.
Method: `shasum -a 256 docs/phase-29-opportunity-chart-eligibility-fix.md api/services/opportunity.py`;
`diff -u` against content-addressed component/test base snapshots. Only the component, new
corresponding E2E case, and this new append-only evidence document were edited by this writer.
Protected backend result: `b9082ab3aa29d21b398e0182e4d3602749f690017924541428668e55540b68dc`.
Contract result: accepted digest unchanged.

Rollback method: `mktemp -d /private/tmp/espn-edge-phase29-rollback.XXXXXX` returned
`/private/tmp/espn-edge-phase29-rollback.bkgP9r`; copy both candidate frontend files there,
then overwrite **only scratch copies** using the corresponding content-addressed base copies;
run `shasum -a 256` on both scratch files.
Result: restored component `eaecf362bcabe51b25ce99e45b3301e835375218f726748b221aad5dcc42ef67`;
restored test `c359421a764add3024eb485491cce93dbaa11ecfd0b433e47553200dea65f807`.
`rollback_safe=true` for exact dirty-base byte restoration; live worktree was never reverted.

| Flow | Classification | Evidence / verdict |
| --- | --- | --- |
| Chart rendering | Shared presentation code | Uses existing server counts/domains; defensive sample predicate only. |
| New mocked responses | Synthetic test data | Generated names `Synthetic Player P000N`, team `SYN`, synthetic numeric IDs; no captures or credentials. |
| PNG export | Existing client-only path | Disabled when server declares pending; export handler also guards drawability. |
| Provider / DB / config | Untouched | No refresh, network/provider/model call, source DB read/write, schema or auth change. |

Schema/API/config/cost delta: none; incremental recurring and one-time service cost $0.
Untested: real-provider response freshness, other browser projects, production deployment.
Independent QA must assess the frozen candidate; writer does not approve its own work.

## E29F.4 — Agent 1 browser execution and exact accepted exception

Classification: measured results reported by Agent 1; operator acceptance recorded separately.
UTC record: acceptance and evidence-only lease recorded at `2026-09-16T21:47:41Z`.
Environment: current macOS/Chromium environment from E29F.1; installed dependencies,
local Vite server, mocked `/api/**`; one invocation of each command, working directory `web`.
No provider response or source database is used by these browser tests.

| Exact command | Reported result | Playwright duration | `/usr/bin/time -p` output |
| --- | --- | --- | --- |
| `/usr/bin/time -p npm run e2e -- --grep 'one-game raw opportunity points'` | 1 passed | 4.9 seconds; regression 4.0 seconds | real 5.41; user 3.00; sys 0.66 seconds |
| `/usr/bin/time -p npm run e2e` | 56 passed, 1 failed | 11.8 seconds | real 12.39; user 44.82; sys 11.61 seconds |

All Opportunity tests passed. **Full browser suite remains RED: 56/57 passed.** The earlier
sandbox `listen EPERM` attempt in E29F.2 is preserved; the later local-server run supplies
actual execution evidence rather than rewriting the blocked attempt as a pass.

Sole failure: `team logo failure keeps the team initials visible`, current
`web/e2e/smoke.spec.ts:2265`; line 2274 expects `display:none` but receives `display:block`
for the `opacity-0` image. Agent 1 verified the complete failing test block against the dirty
base with the following zsh command; result exit 0, no diff output:

```bash
diff -u <(sed -n '/test("team logo failure keeps the team initials visible"/,/^});/p' /private/tmp/espn-edge-phase29-eligibility-base.HNaRYY/c359421a764add3024eb485491cce93dbaa11ecfd0b433e47553200dea65f807.ts) <(sed -n '/test("team logo failure keeps the team initials visible"/,/^});/p' web/e2e/smoke.spec.ts)
```

Agent 1's checksum verification found `web/src/components/TeamIdentity.tsx` unchanged at
`771a6e29140a0dcba2e1f25f7b3c659b7a97ba92c631f280b9600633d2f90547`.
The same prior failure is recorded in Phase 30 E30.4. Byte identity supports attribution to
the known baseline failure; it does not make that assertion or the global suite pass.

Authority, not measurement: the operator accepted the independently reviewed amendment
`docs/phase-29-opportunity-chart-eligibility-fix-amendment.md`, SHA-256
`5238ab0a028514026718f6fb39ac951fc124e79c816f75be6f3e0ccc76d36335`, 4,748 bytes.
The immutable parent digest remains
`49431b0725bfd89f082b6d5543135d70837ba5c872901bc0e88207c9fde03e0c`.
The exact unchanged-logo-test exception permits only this corrective closure after independent
QA verifies the final candidate. It does not waive a later release gate or declare a release ready.
Any additional browser failure blocks closure.

## E29F.5 — final scope verification and explicit checksum limitation

Classification: Agent 1-reported measured byte verification; not an absence-of-risk proof.
Method: verify baseline inventory from
`/private/tmp/espn-edge-phase29-eligibility-base.HNaRYY/pre-existing-hashes.sha256`;
verify protected backend and parent with `shasum -a 256`; rehearse snapshot restoration on
scratch copies as E29F.3, never overwrite the live worktree.
Result: remaining baseline files verified unchanged except the leased component/smoke edits
and Agent 1's orchestration records. **Six directory-valued `.claude/skills` symlinks produced
malformed checksum entries and were not checksum-verified.** Do not claim every inventory
entry passed; the checksum evidence is limited to successfully parsed file entries.
Scratch rollback restored both exact dirty-base byte identities; protected backend and parent
hashes remained unchanged. Source database was not mutated. No live provider, spend,
deployment, commit or merge action occurred.

Frozen application/test hashes at this evidence-only close-out:

```text
74a3cebfca11ffa43d261733b976a7c6aced1af7f7e261a6cc6280ac26e6b37c  web/src/components/OpportunityChartExplorer.tsx
776c434426de133fca7154a72788d8ead86f1ee35636b2fb733fd24f9855d503  web/e2e/smoke.spec.ts
```

Validity domain: exact accepted corrective candidate and current offline Chromium suite only.
The evidence-only writer did not rerun tests, alter application/test bytes, or independently
reproduce Agent 1's browser executions. Independent QA must verify this final freeze.

## E29F.6 — inherited aggregate provenance clarification

Classification: inherited earlier measured aggregate results, not remeasured under either UI lease.
E29F.1's statement that the parent contains original diagnostic commands was imprecise:
the parent records aggregate results, not the fully reproducible SQLite query. Agent 1 supplied
the exact earlier method below for provenance. **This command was not run during close-out.**

```bash
sqlite3 -readonly -header -column data/edge.db "SELECT season,week,position,COUNT(*) AS player_games, COUNT(DISTINCT gsis_id) AS players,COUNT(DISTINCT game_id) AS games, SUM(CASE WHEN target_share IS NULL THEN 1 ELSE 0 END) AS target_share_nulls, SUM(CASE WHEN air_yards_share IS NULL THEN 1 ELSE 0 END) AS air_share_nulls FROM opportunity_weeks WHERE season=2026 GROUP BY season,week,position ORDER BY week,position; SELECT COUNT(*) AS player_games,COUNT(DISTINCT gsis_id) AS players, COUNT(DISTINCT game_id) AS games,MIN(week) AS min_week,MAX(week) AS max_week FROM opportunity_weeks WHERE season=2026; SELECT COUNT(*) AS mapped_week1_players FROM opportunity_weeks w JOIN nflverse_player_maps m ON m.gsis_id=w.gsis_id AND m.status='matched' WHERE w.season=2026 AND w.week=1;"
```

Reported prior result: 89 RB, 80 TE, 151 WR player-game rows; total 320 across 16 games,
320 mapped, minimum and maximum week both 1. These are prior aggregate measurements,
not a freshness claim about the current database. Validity domain: the earlier local dataset.
Scratch rollback paths are explicitly
`/private/tmp/espn-edge-phase29-rollback.bkgP9r/component.tsx` and
`/private/tmp/espn-edge-phase29-rollback.bkgP9r/smoke.ts`, not the original source filenames.
