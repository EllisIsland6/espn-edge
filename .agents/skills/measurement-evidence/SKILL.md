---
name: measurement-evidence
description: Record reproducible evidence for performance, capacity, cost behavior, query counts, memory, recovery, compatibility, provider observations, CI duration, or review yield. Use whenever ESPN Edge reports a number, benchmark, projection, limit, test timing, restore result, or claims a quantity cannot currently be measured.
---

# Measurement Evidence

## Produce one E-ledger entry per claim

Record:

```text
E<number> — <question>
Classification: measured | derived arithmetic | externally verified | assumption | unmeasurable
UTC time:
Environment: host, OS, architecture, runtime/image, CPU/memory limits
Dataset: seed/source, rows/leagues/seasons, relevant state
Method: exact command/query/file and warm-up/sample count
Raw artifact: path plus hash, or inline bounded output
Result: value, units, p50/p95/worst where relevant
Derivation: formula and cited measured inputs, when applicable
Validity domain:
Confounders/limitations:
Future method: required when unmeasurable
```

## Rules

- Measure rather than estimate when the current environment can answer the question safely.
- Never relabel a theoretical maximum, service quota, or arithmetic ceiling as an observed forecast.
- Keep external published rates separate from measured resource quantities and derived monthly cost.
- Capture the deployed semantic role: RLS runtime role, cgroup/container limit, cache state, warm/cold
  state, concurrency, dataset size, and provider window as applicable.
- Preserve commands and raw evidence so another engineer can reproduce the result. Redact secrets,
  cookies, prompts, private payloads, member identifiers, and real-data contents.
- When live/provider/spend evidence lacks authorization, mark it unmeasurable now and state the exact
  bounded future method; do not manufacture a proxy number.

Return the ledger entry and state whether the active contract threshold passed. Evidence records the
result; it does not weaken or reinterpret the threshold.

