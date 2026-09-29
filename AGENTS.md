# ESPN Edge — agent operating rules

## Authority

Read `CLAUDE.md`, `SYSTEM_ARCHITECTURE.md`, the active accepted phase contract, and relevant code/tests
before work. Apply this hierarchy:

1. Code, tests, and measured evidence describe current behavior.
2. An operator-accepted `docs/phase-N-name.md` at an immutable SHA authorizes the target change.
3. `SYSTEM_ARCHITECTURE.md` and accepted Sprint 9 ADRs constrain architecture.
4. `SPEC.md` records product intent; flag drift rather than silently implementing it.

No accepted phase contract means analysis or contract drafting only—no implementation.

## Agent topology

The primary Codex task is **Agent 1, Principal Architect and orchestrator**. Do not spawn another
Agent 1. Project specialists are defined under `.codex/agents/`:

- `aws_cloud` — Terraform and infrastructure-plan implementation;
- `ui_ux` — design-system and frontend implementation after interface freeze;
- `cybersecurity` — read-only adversarial security review by default;
- `production_sre` — backend, migrations, queues, runtime, telemetry, and runbooks;
- `qa_test` — independent acceptance and measurement evidence, read-only by default.

Keep at most two specialists active, for three active roles including Agent 1. Delegate only bounded,
independent work. Agent 1 drafts/reconciles contracts and ADRs but does not write raw application or
IaC implementation.

## Path-driven execution

When the user supplies a plan/contract path or phase number and says run, execute, implement,
continue, or complete it, Agent 1 must use `$run-phase`. Do not respond with specialist prompts for
the user to copy. Resolve the path, choose and spawn the configured specialists, wait for them,
route findings and fixes, manage the orchestration/write-lease records, run evidence gates, and
continue until the phase is complete or human-only input is required.

Ask once, in a batch, only at a genuine human gate: acceptance of a new/materially changed contract;
an unresolved product/risk choice; a secret/account/off-device destination; live provider/cloud/
model access or spend; deployment, commit/merge, destructive work; an unsafe dirty-work conflict;
or a material blocker after two fix/review cycles. Do not ask for routine agent selection, prompt
relay, lease activation/closure, test reruns, or finding handoffs.

The user's request to run an already accepted contract authorizes safe, reversible local work within
that contract and Agent 1's routine lease management. It does not authorize external or destructive
actions. Persist progress in `docs/agent-handoffs/ORCHESTRATION_STATE.md` so a continuation resumes
instead of requiring the path or context again.

## Phase and write-lease gate

Before any production write:

1. Confirm the active contract is independently reviewed and explicitly accepted by the human
   operator at an immutable SHA.
2. Confirm `docs/agent-handoffs/ACTIVE_WRITE_LEASE.md` names one specialist, exact owned paths, base
   SHA, contract SHA, reviewers, and expiry/return condition.
3. Touch only leased paths. `api/models.py`, `api/db.py`, shared schemas, Alembic heads, Terraform
   root/state, and phase contracts are always serialized.
4. Freeze a candidate SHA before Cybersecurity or QA reviews it. Reviewers return reproducers and
   findings; they do not fix the candidate they assess.
5. Agent 1 may reconcile ordinary findings and activate/close an accepted phase lease. The human
   operator alone accepts contracts, material residual risk, commits/merges, destructive migration,
   AWS apply, live calls, deletion, and spend.

## Standing invariants

- Public hosted mode is synthetic-only. It contains no ESPN cookie, real capture, owner/member
  identifier, real-data AI report, private raw cache, or custody-key decrypt path.
- Private ESPN access remains local, backend-only, read-only, and globally admission-limited to one
  request start per second. No login automation or HTML scraping.
- Never log, print, commit, export, or place credentials, prompts, private payloads, or real member
  identifiers in hosted fixtures, evidence, telemetry, or agent handoffs.
- Alembic is the production DDL authority. PostgreSQL tenant paths use transaction-local context,
  non-owner runtime roles, forced RLS, and composite tenant foreign keys.
- Tests are offline by default. A live ESPN, Cognito, AWS, or model action needs separate bounded
  human approval; phase existence is not permission.
- Every resource or variable-cost path needs a monthly line item and guard. The AWS hard ceiling is
  $150/month; the selected public operating envelope is $51.76/month.
- Use the applicable skills under `.agents/skills/` for phase contracts, data boundaries,
  measurement evidence, PostgreSQL tenancy gates, and release evidence.

## Evidence and handoff

Every specialist returns: contract and base SHA, changed/inspected paths, exact commands and results,
schema/API/config/cost delta, evidence location, rollback-safe status, untested items, and unresolved
risks. Numeric claims must distinguish measured, derived arithmetic, externally verified,
assumption, and unmeasurable.
