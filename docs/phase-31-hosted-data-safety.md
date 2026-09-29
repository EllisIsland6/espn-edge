# Phase 31 — Hosted Data Safety

Source plan: `docs/sprint-9/06-phases.md`, Phase 31 `hosted-data-safety`
(7 sessions; 130 operator minutes; $0 AWS/month; existing model subscriptions only).
Predecessor gate: Phase 30 `private-recovery-baseline`, complete — Candidate 34 manifest
SHA-256 `863ac87b93f60733218898934e6a47bf1ed20a7eb231d56c7a5a5ba80afc9fbe`.

## Outcome and risk retired

Make it **structurally impossible** for public hosted or CI mode to ingest real ESPN data or to
authorize unbounded AI spend. Structurally means the guarantee does not depend on an operator
remembering a flag: in hosted mode the provider class cannot be instantiated, the credential path
cannot be reached, and generation cannot proceed without a settled reservation.

Two risks are retired. First, real member data leaking into a public artifact or CI log. Second, an
unbounded or double-spent AI bill from concurrent or crashed generation.

## Current behavior — measured, not assumed

Measured on the working tree at base Git SHA `7f81c76e24fd50635071f8b53f05a4aeb11881de`:

- `api/config.py` has **no** mode field. There is no `public_synthetic` / `private_operator` split.
- `EspnService(` is instantiated at exactly **1** site under `api/`. The kill-switch surface is
  therefore small; this is the phase's cheapest guarantee.
- `tests/fixtures/` holds **13** files, of which **7** are `real_*.json` recorded captures.
- `real_league_2025.json` and `real_league_2026.json` each contain **18 SWID-shaped GUID strings**
  (**9 distinct**, each appearing twice: once at `.members[].id` and once at `.teams[].owners[]`).
  A scan for cookie-shaped long tokens across all seven files returned **0**.
- **Those GUIDs are already synthetic.** All 9 distinct values per file carry **at least 20 zero
  characters out of 32 hex digits**, only **2 distinct first blocks** occur across 9 values, and the
  value that `tests/test_real_fixtures.py:15` documents as `ME = ... # fixed fake for the account's
  SWID` is **present in both fixtures**. Real ESPN SWIDs are high-entropy random GUIDs; these are
  low-entropy placeholders, so the captures were scrubbed when recorded.
- Both files are **tracked in git** and `.gitignore` contains **no rule** for `real_*`.
- There is **no** AI usage ledger, reservation, ceiling or settle path in `api/services/ai.py` or
  `api/ai_config.py`.
- There is **no** Alembic configuration anywhere in the repository.

### A correction Agent 1 owes the record

Agent 1 first reported these 18 shape-matches as a live exposure requiring remediation, and the
operator authorised an in-place scrub on that basis. On inspection **no member identifier is
exposed and no scrub is required**: the values are already low-entropy placeholders, per the measured
evidence above. The remediation work unit is therefore **withdrawn, not performed**.

The error is worth recording because of its shape. The scanner matched a GUID **pattern** and Agent 1
reported an exposure without checking **content entropy** — a check that fired without establishing
the thing it claimed. That is the same defect class as the four vacuous gates recorded in Phase 30
(E30.58, E30.60), two of which Agent 1 also authored. The corrective requirement is carried into
this phase's acceptance criteria: the provenance scanner must assert on **content**, not shape alone,
and must be proven by a negative control that a shape-match with synthetic content does **not** fail
the build while a real-entropy identifier **does**.

What remains genuinely open is narrower: `real_*.json` is tracked with no `.gitignore` rule, so the
naming convention implies a local-only guarantee the repository does not enforce. Future captures
recorded without scrubbing would be committed silently. This phase closes that by grammar and
provenance manifest rather than by history rewrite.

## The hosted boundary

### The Question

What, exactly, prevents a public hosted process from reading real ESPN data or spending unbounded
model budget — and is that prevention structural or procedural?

### The Lens

Zero-trust configuration: a hosted principal should be unable to express the dangerous operation,
not merely discouraged from it. Fail-closed: an unavailable ledger must stop generation, not permit
it. Least authority: the public mode gets no decrypt path, no cookie, no cache write.

### The Selection

Three candidate mechanisms for the data boundary:

1. **Runtime flag checks at call sites.** Cheapest, but procedural — a missed site reopens the hole,
   and the guarantee scales with reviewer diligence.
2. **Mode-gated construction.** Hosted mode raises at provider construction and at credential
   access, so the dangerous object cannot exist. One site today, and any future site inherits the
   guarantee for free.
3. **Separate deployment artifacts.** Strongest, but out of scope at this budget and premature
   before Phase 42 infrastructure.

Selection: **(2)**, with **(1)** retained only as defence in depth where a call site can be reached
without construction.

For the spend boundary, the choice is between optimistic post-hoc accounting and a
reserve/settle ledger. Post-hoc accounting cannot bound concurrency and loses a crashed call's
spend. Selection: **reserve before the call, settle after, and record an explicit
`unknown_spent` state when the response is lost**, so a crash is never silently free.

### The Synthesis

`Settings` gains a required, explicit mode. In `public_synthetic`:

- `EspnService` construction raises; no live ESPN call is reachable.
- Cookie decrypt and the accounts credential path raise.
- The raw JSON cache is read-only and cannot be written.
- Fixture loading is restricted to the synthetic allowlist; an unknown or `real_*` path raises.
- AI generation requires a settled reservation within the UTC-month ceiling.

In `private_operator` every existing behavior is unchanged. No metrics, ESPN parse, Edge Index, or
export semantics change in either mode.

## Implementation contract

### Owned paths

`api/config.py`; `api/services/espn.py`; `api/services/cache.py`; `api/crypto.py`;
`api/services/discovery.py`; `api/services/cross_check.py`; `api/services/ai.py`, `api/ai_config.py`;
new `api/services/fixtures.py` (synthetic factory), `api/services/provenance.py` (scanner +
manifest), `api/services/spend.py` (ledger); new `alembic/` baseline plus one additive revision;
`tests/conftest.py` (environment pinning only); `tests/fixtures/synthetic/**`; directly
corresponding tests; append-only `docs/evidence/phase-31-hosted-data-safety.md`.

**Amendment, unit 2 review.** The original list named only `api/services/espn.py` "(construction
guard only)". Parallel review proved that list unable to satisfy the contract's own acceptance
criteria: criterion 2 requires closing the **accounts credential path**, which lives in
`api/crypto.py`; and the Selection's defence-in-depth clause — mechanism (1) "retained where a call
site can be reached without construction" — is unsatisfiable without `api/services/discovery.py` and
`api/services/cross_check.py`, both of which issue live credentialed ESPN requests without
constructing `EspnService`. `api/services/cache.py` and `tests/conftest.py` were required by unit 2's
own description but omitted from the list. The paths above are added; no forbidden path is
relaxed.

### Forbidden paths

`api/services/metrics.py`, `api/services/sync.py` parse semantics, `api/services/exposure.py`,
`api/services/draft_analytics.py`, `api/services/opportunity.py`, `api/services/recovery.py` and all
Phase 30 recovery surfaces, the Phase 30 contract and evidence, `.env`, Keychain, the Restic
repository, and every frontend path except any strictly required cached-only trip indicator.

### Work units

1. **Contract acceptance.** Operator accepts this contract. The `real_*` remediation originally
   scoped here is **withdrawn** as unnecessary; see the correction above. The residual naming/ignore
   gap is handled by unit 3's grammar and provenance manifest.
2. **Mode split and kill switches.** `public_synthetic` / `private_operator`; construction and
   credential guards; cache write guard.
3. **Synthetic fixture factory, grammar scanner, provenance manifest.** Deterministic seeds
   including 115-league and colliding-tenant shapes; malformed variants; path grammar; adversarial
   SWID/GUID/cookie/member-name/league-name tests that fail the build.
4. **Spend ledger and Alembic baseline.** UTC-month reserve/settle/`unknown_spent`; $5 ceiling
   enforced under parallel reservations; ledger failure and ceiling breach both degrade to
   cached-only with no queued retry.

## Explicit deltas and guarantees

- No live ESPN call at any point in this phase.
- No real capture is an input to synthetic generation. The factory is seeded, not recorded.
- No real-data AI report migrates into hosted mode.
- No public principal gains a custody decrypt path.
- Existing metrics and ESPN parse semantics remain **byte-identical**; the Phase 3 reference value
  `82.5` must remain unchanged.
- Schema delta: the smallest reviewed Alembic baseline plus one additive revision for ledger state.
  Phase 35 validates and completes PostgreSQL dialect parity; this phase does not claim it.
- Cost delta: **$0 AWS/month**; no new external service. The $5 figure is a spend **ceiling** the
  ledger enforces, not a forecast.

## Acceptance criteria

1. Hosted config cannot instantiate `EspnService`; the attempt raises a stable, secret-free error.
2. Hosted config cannot decrypt a cookie or reach the accounts credential path.
3. Unknown fixture paths raise; every fixture string matches the path grammar.
4. Adversarial SWID, GUID, cookie, member-name and league-name probes **fail the build**, asserted
   on **content**, not shape. Proven by a two-sided control: a real-entropy identifier fails the
   build, and a low-entropy synthetic placeholder of identical shape does not.
11. `real_*.json` cannot be added to the tree without a provenance manifest entry declaring it
   scrubbed; an unscrubbed capture fails the build.
5. Deterministic seeds reproduce identical synthetic corpora, including the 115-league and
   colliding-tenant shapes.
6. Usage is persisted for three cases: success, lost response, and crashed reservation.
7. Parallel reservations cannot exceed the $5 UTC-month ceiling.
8. Ledger failure and ceiling breach both yield cached-only generation with no queued retry.
9. `make test` passes; `ruff check api tests` exits 0; frontend lint/build/e2e unaffected.
10. Phase 30 recovery gates still pass unchanged.

The public launch and any hosted AI route remain blocked until all eleven pass.

## Process

Phase 30 ran 34 candidates with per-candidate manifest freezes and parallel specialist review,
against a budget of 3 sessions. Agent 1 proposed dropping both for this phase, on the grounds that
it is fully offline, $0 and reversible. **The operator overruled the review half of that proposal.**
The accepted process is therefore:

- **Dropped:** per-candidate manifest freeze and verification, and per-candidate evidence entries.
  Evidence is one appended entry per work unit.
- **Retained:** this accepted contract, the write-lease record, all eleven acceptance gates, and
  **parallel read-only `qa_test` and `cybersecurity` review at the close of every work unit**.
  Reviewers assess the exact worktree bytes, identified by digest, rather than a frozen manifest.
- **Added:** Agent 1 executes the offline suite directly and reports measured results rather than
  returning commands to the operator.

The operator's reasoning is recorded because it is better than Agent 1's: Phase 30's own evidence
shows a `cybersecurity` P1 on a window that Agent 1's self-review had walked past (E30.59), and two
of the four vacuous gates in that phase were authored by Agent 1. Cost and reversibility bound the
blast radius of a defect; they do not improve the reviewer. Independent review is retained for every
work unit in this phase.

A work unit closes only when its gates pass and both reviewers return READY-OFFLINE. A NO-CLOSE
reopens that unit; it does not advance to the next.

## Non-goals

PostgreSQL dialect parity (Phase 35). Tenant isolation and RLS (Phase 36). Route retrofit
(Phase 37). Query and export budgets (Phase 38). Durable queueing (Phase 39). Identity and sessions
(Phase 40). Observability (Phase 41). Infrastructure and CI (Phase 42). Live provider measurement
(Phase 32).

## Review, acceptance, and lease gate

The human operator alone accepts this contract, the `real_*` remediation, any commit or merge, and
the residual risk of the lighter process. Agent 1 may activate and close the routine local write
lease once the contract is accepted.
