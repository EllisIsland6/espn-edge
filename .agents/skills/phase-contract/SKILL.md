---
name: phase-contract
description: Create, amend, or validate ESPN Edge phase contracts under docs/phase-N-name.md. Use before implementing or closing any phase, when scope or acceptance changes, or when a request might violate a no-schema, no-provider, cost, rollback, ownership, or offline-test guarantee.
---

# Phase Contract

## Workflow

1. Read `AGENTS.md`, `SYSTEM_ARCHITECTURE.md`, `docs/sprint-9/06-phases.md`, the relevant accepted
   ADRs, current code/tests, and the immediately preceding phase contract.
2. Separate current behavior from target behavior. Cite code/measurements for the former and the
   accepted architecture for the latter.
3. Draft one `docs/phase-N-name.md` containing:
   - outcome and risk retired;
   - dependencies and owned/forbidden paths;
   - schema, API, provider, configuration, security, cost, and data-migration deltas;
   - explicit guarantees and non-goals, including evidence for every “no change” clause;
   - rollback expectation and `rollback_safe` state;
   - acceptance criteria, negative cases, exact offline commands, and required E-ledger entries;
   - specialist write lease, independent reviewers, and operator review budget.
4. Have Cybersecurity and/or QA review the contract before implementation. Resolve findings without
   letting the author approve the result.
5. Stop at the gate. The human records acceptance of an immutable contract SHA in the handoff/lease;
   editing the contract afterward invalidates acceptance.

## Validation rules

- Refuse implementation when the contract is missing, draft, ambiguous, or unaccepted.
- Treat live ESPN, Cognito, AWS, model spend, deletion, migration, and apply as separately approved
  actions even when named in a phase.
- Price every new recurring/variable component; justify each line over $10/month.
- Express acceptance as observable pass/fail evidence, not “works,” “secure,” or model confidence.
- Preserve the repository's phase convention and avoid widening the phase to adjacent cleanup.

Return the contract path, contract hash method, unresolved choices, reviewers required, and the
precise gate that blocks implementation.

