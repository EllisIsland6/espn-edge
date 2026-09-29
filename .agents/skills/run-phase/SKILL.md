---
name: run-phase
description: Orchestrate an ESPN Edge phase end to end from a user-supplied plan or contract path. Use when the user says run, execute, implement, continue, or complete a phase and supplies a docs path, phase number, or plan section; Agent 1 must select and spawn specialists, manage contract review and write leases, iterate implementation and independent review, preserve evidence, and continue until complete or genuinely blocked on human-only input.
---

# Run Phase

Treat the primary session as Agent 1. Do not ask the user to copy specialist prompts or manually
relay findings. Orchestrate the configured specialists directly and keep the primary context focused
on decisions, state, and final evidence.

## 1. Resolve the input

1. Resolve the supplied path and optional phase number. Read the file completely plus `AGENTS.md`,
   `SYSTEM_ARCHITECTURE.md`, current orchestration/lease state, relevant ADRs, code, and tests.
2. If the path is a program/summary rather than a phase contract, extract only the named next phase
   and draft `docs/phase-N-name.md` with `$phase-contract`.
3. Inspect the worktree and record a base commit plus the pre-existing dirty-path inventory. Never
   absorb unrelated user changes into the phase or overwrite them.
4. If another orchestration or write lease is active, resume it when it matches; otherwise stop and
   ask the user to resolve the conflict.

## 2. Establish authorization once

- A phase contract marked accepted in the orchestration record with an immutable contract digest is
  ready for implementation. The user's instruction to run that path authorizes ordinary reversible
  local implementation within its scope and Agent 1's activation/closure of its write lease.
- If the contract is missing or unaccepted, Agent 1 must draft it, spawn `cybersecurity` and
  `qa_test` to review it in parallel, reconcile their findings, calculate the contract digest, then
  ask the user **one concise batched acceptance question**. Do not ask the user to run or relay those
  reviews.
- Contract acceptance does not authorize live ESPN/Cognito/AWS/model calls, AWS apply, merge/commit,
  deletion, destructive migration, purchase, secret creation/rotation, or another external side
  effect unless the user's instruction explicitly authorizes that exact action.

After acceptance, write the accepted contract/digest and orchestration state, activate the exact
write lease, and proceed without asking for another routine handoff approval.

## 3. Select and dispatch specialists

Choose only specialists needed by the phase:

| Work | Write owner | Independent input |
| --- | --- | --- |
| Backend, recovery, data, migrations, queues, runtime, telemetry, runbooks | `production_sre` | `cybersecurity` and/or `qa_test` |
| Frontend/design system after frozen interface | `ui_ux` | `cybersecurity` for session/browser security; `qa_test` for behavior |
| Terraform/infrastructure after frozen runtime contract | `aws_cloud` | `cybersecurity` for IAM/network; `qa_test` for plan/policy evidence |

Keep no more than two specialists active. One is the sole writer for leased production paths; the
other may review or work in a contract-frozen disjoint path. Never let two agents edit the shared
database/API/IaC spine.

Send each specialist a bounded task containing the plan/contract path, contract digest, base
identity, exact owned or read-only paths, relevant skill(s), deliverable, evidence format, and stop
conditions. Wait for results and route follow-ups yourself.

## 4. Drive the work to convergence

1. Have the write owner implement the smallest reversible contract-compliant change and return a
   candidate identity plus evidence. A candidate identity may be a commit SHA or a deterministic
   patch/tree digest; never create a commit unless the user requested one.
2. Freeze the candidate. Spawn the assigned independent reviewers in parallel against that exact
   candidate. Reviewers must derive checks from the contract, not the writer's conclusion.
3. Triage findings by contract impact. Send accepted findings back to the writer, then have only the
   affected reviewer rerun failed attacks. Preserve rejected findings with reasoning.
4. Continue for up to two complete fix/review cycles. If a material blocker remains after that,
   ask the user one batched decision question with evidence and options; do not silently relax the
   contract.
5. Run all final offline gates and applicable boundary/evidence skills. Update the phase contract's
   evidence/closure section and reconcile `SYSTEM_ARCHITECTURE.md` only when implementation changed
   the as-built/target relationship.
6. Close the write lease and orchestration state. Report completion, files, evidence, tests, cost/
   schema/API/provider deltas, residual risks, and the next gated phase.

## 5. Persist state and survive interruptions

Update `docs/agent-handoffs/ORCHESTRATION_STATE.md` after contract review, acceptance, lease
activation, candidate freeze, review, each fix cycle, and closure. Store paths/digests and compact
facts, never secrets or raw private content. On continuation, read this file and resume the first
unfinished step rather than restarting or asking the user to re-paste context.

## 6. Ask the user only when necessary

Pause and batch questions only for:

- acceptance of a newly drafted/materially changed phase contract;
- a product or risk choice not determined by the repository;
- a required secret, account, destination, credential, or physical-device choice;
- live provider/cloud/model access, external spend, deployment, merge/commit, destructive action, or
  another permission only the user can grant;
- a conflict with unrelated dirty work that cannot be isolated safely;
- a material finding unresolved after two fix/review cycles;
- unavailable tools/permissions or external state that prevents meaningful progress.

Do not pause for agent selection, prompt relay, routine lease activation/closure, test reruns,
finding handoff, documentation reconciliation, or progress confirmation. Provide brief commentary
updates during long work, but keep working.

