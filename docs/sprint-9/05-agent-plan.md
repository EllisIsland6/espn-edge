# Sprint 9, Stage 5 — agent and model plan

Status: amended 2026-08-12. The current decision is Agent 1 plus five specialists below. Runnable
project definitions now exist under `.codex/agents/` and `.claude/agents/`; shared gates exist under
`.agents/skills/`. No specialist is dispatched and no write lease is active. The
original two-agent analysis is retained as decision history and still governs write leases,
dependency ordering and operator-capacity controls. No production code or infrastructure is
authorized by this document.

## Current decision — Agent 1 plus five specialists

Configure six persistent roles:

1. **Agent 1 — Principal Architect and orchestrator:** owns architecture/ADR/phase-contract drafts,
   SSOT reconciliation and handoff boundaries; writes no raw application or IaC implementation.
2. **Agent 2 — AWS Cloud specialist:** owns accepted Terraform implementation and infrastructure
   plan evidence after Phase 42's contract gate.
3. **Agent 3 — UI/UX specialist:** owns the design system and `web/**` implementation after the API,
   session and runtime-config contracts freeze.
4. **Agent 4 — Cybersecurity specialist:** owns threat models, adversarial security review and
   assigned security/policy tests; it is read-only against the implementation candidate by default.
5. **Agent 5 — Production application and SRE specialist:** owns the backend build-engine gap—
   `api/**`, Alembic, queues, runtime telemetry hooks, operations workflows and runbooks—one accepted
   phase at a time.
6. **Agent 6 — QA and test specialist:** independently derives acceptance, RLS, browser, load,
   failure-injection and N-1 compatibility evidence.

The human Product Manager remains outside the agent count and is the only contract/risk/merge/apply
authority. Agent 1 may draft but never accept a phase contract.

This supersedes the count in the historical decision below. It does **not** supersede the evidence
that this program is dependency-heavy or that operator attention is scarce. The operating ceiling
is three active agents total: Agent 1, one phase owner holding the sole production-write lease for
its paths, and one independent reviewer or specialist in a contract-frozen clean directory. A
fourth requires an operator-approved ownership map and reserved review capacity. Shared spine files
remain serialized.

The roster correction is deliberate: the requested AWS/UI/security/SRE/QA list otherwise leaves no
backend production-code owner while Agent 1 is barred from raw code. Agent 5 is therefore
**Production application and SRE**, not an observability-only role. Combining these concerns accepts
a broad role, but preserves QA and security independence and avoids adding a seventh agent.

| Phase/workstream | Write-lease owner | Independent specialist input |
| --- | --- | --- |
| 30–39 backend/data/queue spine | Agent 5 | Agent 4 for custody/RLS/session threats; Agent 6 for executable evidence. |
| 40 identity/session/frontend | Agent 5 for `api/**`; then Agent 3 for `web/**` against the frozen interface | Agent 4 reviews session/CSP/CORS/authz; Agent 6 owns end-to-end acceptance. |
| 41 observability/retention | Agent 5 | Agent 2 checks AWS metric/service semantics; Agent 6 runs failure injection. |
| 42 infrastructure/CI | Agent 2 for `infra/**`; Agent 5 for explicitly leased workflow/runbook paths | Agent 4 reviews IAM/network/secrets; Agent 6 runs policy/plan tests. |
| 43–45 rehearsal/launch/rollback | No agent receives standing mutation authority; the operator runs approved actions | All relevant specialists produce bounded preflight or read-only evidence through Agent 1. |

Model choice remains per workstream, not persona prestige. The five specialist packages live under
`docs/agent-prompts/`; none grants deployment, live-provider, spend, merge or self-approval
authority. The existing 1,990-minute human review budget is not silently enlarged. Phase 30–32
measure handoff/review actuals; if participation exceeds the phase budget, low-yield specialists are
dropped from that phase before any safety criterion is weakened.

**Accepted failure:** configured specialists may sit idle, and handoffs cost more context/operator
time than the two-agent model. **Revisit:** collapse roles or concurrency after two phases above
120% of operator budget or when a specialist produces no material finding/evidence improvement.
The recurring AWS delta remains **$0/month**; model-plan quota and human attention are the costs.

### What a staff engineer would ask about the expanded model

1. **“Why configure six roles if only three may be active?”** Role count isolates standing context,
   permissions and review lenses; concurrency follows dependency and operator capacity. Activating
   all six would re-create the same-file and review-saturation failures the earlier analysis found.
2. **“Who actually ships backend code?”** Agent 5, through one phase-scoped write lease. Agent 1
   owns architecture, Agent 4 attacks the security boundary, and Agent 6 derives independent
   evidence; none can silently become a second backend writer.
3. **“How do security and QA stay independent if they are on the same AI team?”** They receive the
   accepted contract and frozen candidate SHA, default to read-only production paths, and return
   reproducers rather than approvals. This is procedural independence, not organizational
   independence; the human still owns residual risk and merge authority.

## Superseded decision — retained for rationale

Do **not** run this sprint as a many-agent implementation team. **Configure exactly two project
agents**, with no more than two active at once:

1. one implementation integrator with the only production-write lease; and
2. one independent verifier, normally read-only against production files.

Default to one active agent. Activate the verifier after a phase-sized diff is frozen, or use the
second slot temporarily for one clean-directory implementation lane after its interface is fixed.
Never have two agents editing the same database/API spine.

That is fewer than the original proposal implies. It follows from how the design actually evolved:
credential custody changed the product; the product boundary changed requirements; the requirements
changed networking; networking constrained compute/data; and those choices changed the migration.
Five sequential gates each corrected the preceding stage. Parallel drafts made before those gates
would have been faster ways to produce incompatible answers.

The accepted failure is less apparent parallelism. The benefit is one coherent schema/security
model and a review process a solo owner can actually supervise. The cost is **$0 AWS/month** and no
new model/API purchase; it consumes time and the usage pools of the already-owned tools.

This is a judgment call grounded in this repository, not a universal claim that multi-agent work is
bad. Claude Code's own guidance says agent teams add coordination/token overhead and are a poor fit
for sequential, same-file or dependency-heavy work; it also labels that team feature experimental
([Claude Code agent teams](https://code.claude.com/docs/en/agent-teams)). Codex describes subagents
as useful for independent exploration/tests while warning that each does its own model/tool work
and consumes more tokens ([Codex subagents](https://developers.openai.com/codex/subagents)). The
vendor guidance matches the evidence here.

## 1. What multi-agent solves—and what it does not

| Property | Real benefit here | Cost/failure introduced | Use it? |
| --- | --- | --- | --- |
| Parallel independent context | A verifier can rerun PostgreSQL/RLS attacks, pricing checks or long test logs without burying the phase owner's context. | It starts without the full reasoning chain and can miss a custody or cost decision unless the handoff is complete. | **Yes, for bounded evidence/review.** |
| Failure isolation | A failed experiment in an isolated worktree does not corrupt the integration checkout. A read-only reviewer cannot silently “fix” the finding it should report. | Worktree/branch reconciliation becomes another operator task. | **Yes, selectively.** |
| Specialized instructions/tool limits | A verifier can be denied production writes and preloaded with the tenancy/provider skills. | Too many personas create stale, duplicated rules. | **Yes, two definitions only.** |
| Competing hypotheses | Two agents can independently review an RLS policy, migration or incident cause before anchoring on one explanation. | Duplicate work and model usage; someone still adjudicates. | **Yes, on high-blast-radius decisions.** |
| Parallel feature implementation | Backend and a contract-frozen frontend or `infra/` module can sometimes progress independently. | The identity/session/config contracts cross all three; premature work creates integration drift. | **Rarely, after a signed interface.** |
| More “reviewers” | More text is not more assurance. | The solo developer becomes a merge manager, prompt author and reviewer of reviewers. | **No.** |
| Shared-state coordination | An agent framework can advertise tasks and messages. | It cannot make simultaneous edits to `api/models.py` semantically compatible. | **Serialize the file; do not orchestrate around it.** |

Multi-agent does not supply accountability, authorization or independent human approval. The solo
developer still owns custody decisions, AWS applies, destructive migration, cost acceptance and the
final merge. An agent saying “reviewed” is evidence only when its command output and attack cases are
reproducible.

## 2. The workstream count that produces the agent count

The implementation has six apparent lanes, but only one is independent early enough to run in
parallel:

| Workstream | Principal files | Dependency shape | Parallel verdict |
| --- | --- | --- | --- |
| Hosted-data safety/private recovery | synthetic fixture generator under `tests/`; backup/restore tooling; AI usage contract | Must precede hosted CI and migration; backup schema depends on the protected-table inventory. | Sequential risk gate. |
| PostgreSQL/tenancy spine | `api/models.py`, `api/db.py`, `api/schemas.py`, `alembic/`, runtime-role tests | Defines IDs, FKs, policies, transaction context and schema compatibility used everywhere. | Critical path; one writer. |
| Query/export retrofit | `api/services/portfolio.py`, analytics/export services and routers | Depends on tenant context/schema and supplies measured compute gates. | Sequential after the spine. |
| Jobs/sync/AI ledger | `api/services/sync.py`, `api/services/espn.py`, AI service/router, worker/job modules | Depends on tenant IDs, transactions/outbox and provider boundary. | Sequential interface consumer; may split only after data contracts freeze. |
| Frontend/auth edge | `web/src/**`, `web/e2e/**`, session/auth routers | Can be independent after cookie, route and runtime-config schemas are frozen. | One genuine clean parallel lane later. |
| AWS/CI/operations | future `infra/**`, `.github/workflows/**`, container/operations assets | Directory-isolated, but waits for ports, roles, health, metrics, migration and image contracts. | One genuine clean parallel lane later. |

There are therefore at most **two simultaneous clean ownership domains**: the core/runtime lane and
one of `web/` or `infra/`. Verification is cross-cutting and cannot be delegated away as an
independent product module. Three or more implementation agents would either wait on the tenancy
spine or edit its consumers against a guessed contract.

## 3. The two agent contracts

### Agent 1 — implementation integrator

| Contract item | Requirement |
| --- | --- |
| Role | Turn one accepted `docs/phase-N-*.md` contract into the smallest reversible diff and integrate all findings. |
| Exclusive ownership | For the active phase: `api/**`, `alembic/**`, database/runtime configuration and the phase contract. `api/models.py`, `api/db.py`, shared schemas and migration heads never have another writer. Root build/config files (`pyproject.toml`, `Makefile`, `docker-compose.yml`) are single-owner when touched. |
| Inputs | Accepted Stages 0–5; the active phase contract; base commit SHA; clean ownership map; measured acceptance thresholds; current code/tests. Never use another agent's prose summary instead of reading the governing contract. |
| Outputs | Reviewable diff; migration/schema compatibility manifest when applicable; tests; exact commands/results; changed config/secret/IAM surface; cost delta; rollback-safe flag; unresolved risks. |
| Definition of done | Phase acceptance passes offline; PostgreSQL/RLS paths use deployed roles; no unpriced resource; no real hosted data; provider behavior is unchanged unless explicitly authorized; diff is handed to Agent 2 at an immutable SHA. |
| Boundary | Does not approve its own security/cost/migration claims. Does not apply AWS, run live ESPN, spend Anthropic money, delete data or merge without the operator's explicit authorization. |

### Agent 2 — adversarial verifier

| Contract item | Requirement |
| --- | --- |
| Role | Try to falsify the phase contract and the integrator's evidence, not restyle the implementation. |
| Default ownership | Production files are read-only. It may own new verification assets under future `tests/postgres/**`, `tests/security/**`, `tests/contract/**` and a phase review report only after the operator accepts that scope. It must not edit `api/models.py`, `api/db.py` or `alembic/**`. |
| Optional clean lane | When review is not yet possible and an interface is frozen, it may instead own exactly one of `web/**` or future `infra/**` in an isolated worktree. While it is an implementer, it is not the independent reviewer of that same work. The cross-model review happens afterward. |
| Inputs | Phase contract; immutable candidate SHA/digests; explicit invariants and exclusions; commands claimed by Agent 1. Start with the contract and diff, not Agent 1's conclusion, to reduce anchoring. |
| Outputs | Ranked findings with file/line/evidence; reproducer or failing test for each material claim; command/environment/results for successful attacks; explicit “not tested” items. No silent fixes. |
| Definition of done | Every acceptance criterion is mapped to observed evidence; RLS/provider/backup/migration/cost negative paths are attempted; findings are either fixed and rerun or accepted by the operator in the phase contract. |
| Boundary | Cannot declare release readiness, change architecture, broaden scope or turn a finding into a production edit without handback. |

Agent 2's independence is procedural, not magical. A second model reading a persuasive implementation
summary can inherit the first model's premise. A fresh contract-first review and reproducible failing
test are more valuable than a third model agreeing with the prose.

## 4. Handoff contract

Every agent handoff is a checked-in or attached evidence record, not chat history. It contains:

```text
phase_id and contract path
base SHA; candidate SHA; image digest if built
agent role; owned paths; forbidden paths
accepted invariants and explicit non-goals
schema head and compatible application heads
API/event/config/metric contracts changed
commands, environment and exact results
cost delta and variable-cost guard
known failures, unmeasured items and live-call prohibitions
rollback_safe: true|false with evidence
next owner and the decision required
```

The receiver first verifies SHA and ownership, reads the phase contract and reruns the smallest
smoke command. A missing field is a rejected handoff, not something the next agent infers. This costs
roughly one short evidence file per phase; it avoids reloading five design documents into every
delegation prompt and makes review reproducible by a human interviewer.

### When both agents need `api/models.py`

They do not edit it concurrently.

1. Agent 1 owns the file and lands the schema/API contract at a recorded SHA.
2. Agent 2 reviews that SHA read-only and returns a patch proposal or failing test, never a competing
   version of the model.
3. Agent 1 integrates the accepted finding, advances the SHA and reruns the schema/compatibility
   gates.
4. Downstream `web/`/`infra/`/worker work rebases onto that SHA only after the interface is marked
   frozen.

If both need a change, that is a dependency and the work serializes. Worktrees prevent filesystem
collision; they do not solve semantic collision. An automatic merge of two migration heads or two
tenant-identity designs is forbidden. The solo developer adjudicates any disagreement.

## 5. Model selection and multi-model review

### Pricing correction

The original “Claude Code ($20)” statement conflicts with the stated **Max tier**. Current Anthropic
pricing lists Pro at $20/month when billed monthly and Max from **$100/month**, with 5× or 20× Pro
usage; Claude Code draws from that shared plan pool ([Claude pricing](https://claude.com/pricing)).
Assume the already-owned Max plan, but do not invent whether it is 5× or 20×. It is developer-tool
spend, not AWS spend, and does not change the $100–150 AWS ceiling.

| Tool | Recurring cost carried here | Marginal agent cost | Guard |
| --- | ---: | ---: | --- |
| Claude Code Max | **At least $100/month existing** | $0 until plan limits; additional usage credits/API would be variable | Do not enable pay-as-you-go credits for this sprint without a separate cap. |
| Codex | Existing access; account price was not supplied and is not inferable from the repo | $0 additional purchase authorized | Stay inside the existing product plan; no API-key fallback. |
| Agent orchestration | **$0 AWS/month** | More quota/tokens and operator review time | Two active agents maximum; one production writer. |

Subscription quotas are not free capacity. Claude says Max usage is still limited and shared across
Claude/Claude Code; Codex says each subagent performs its own model/tool work. Broad parallelism can
therefore exhaust the tools earlier even when the marginal invoice is $0.

### Model facts versus judgment

Current first-party positioning is:

- Codex **GPT-5.6 Sol** is the high-capability option for complex/open-ended coding, research and
  security; **Terra** is the everyday tool-using workhorse ([Codex models](https://developers.openai.com/codex/models)).
- Anthropic positions **Opus 5** for its most demanding production-code/agent work and **Sonnet 5**
  as the more cost-efficient general agentic model available in Claude Code
  ([Opus](https://www.anthropic.com/claude/opus?app=claude-code),
  [Sonnet](https://www.anthropic.com/news/claude-sonnet-5)).

Those are vendor claims, not an independent ESPN Edge benchmark. The assignments below are
**judgment calls** based on task shape and the need for different review priors, not proof that one
vendor is categorically better.

| Workstream | Implementer | Independent review | Why / accepted trade-off |
| --- | --- | --- | --- |
| Phase contracts and architecture-sensitive backend changes | Codex GPT-5.6 Sol, high reasoning | Claude Opus 5, fresh read-only session | Sol is explicitly positioned for complex code/research/security; Opus supplies a different model family for adversarial judgment. Costs more plan quota and one extra review session. |
| Alembic, PostgreSQL RLS, composite FKs, N−1 compatibility, import/restore | Codex GPT-5.6 Sol, high or extra-high | Claude Opus 5 with `tenant-postgres-gate` | Highest blast radius and hardest rollback. Cross-model review is worth the time; neither model may approve from prose without PostgreSQL evidence. |
| Queue/outbox/leases, global ESPN admission, AI ledger | Codex GPT-5.6 Sol, high | Claude Opus 5 focused on races, failure injection and wallet/provider boundaries | State-machine errors are subtle. Accepted cost is duplicate reasoning; implementation stays with one writer. |
| Synthetic Fixture Factory and repetitive offline fixture cases | Claude Sonnet 5 in an isolated phase/worktree | Codex Terra for contract/test review; Sol only for a disputed provenance issue | Bounded schema-to-fixture generation suits the efficient execution model. The danger is generating tests that merely agree with the implementation, so the parser allowlist/negative tests are reviewed cross-model. |
| Frontend/session/runtime-config implementation after API freeze | Claude Sonnet 5, owning `web/**` only | Codex Sol on cookie/CSP/CORS/security contract; Terra for ordinary UI diff | This is the cleanest second implementation lane. It stops immediately if the auth/API contract changes. |
| Terraform, CI and operational evidence | Codex Sol for security/cost-sensitive resources; Terra for mechanical modules | Claude Opus 5 for IAM/network/budget/rollback review | The important skill is tracing the accepted ADRs into code and cost, not generating more AWS services. No agent may apply. |
| Test-log triage, command reruns, manifests and mechanical documentation | Codex Terra or Claude Sonnet 5, whichever has available plan capacity | None unless a material discrepancy appears | A second model usually adds review theater here. Preserve commands/results and spend review time on high-risk paths. |

### Concrete cross-model review pattern

Use both models for tenancy, credential/data boundaries, migration/restore, IAM/networking, queue
leases/fairness and AI spend authorization:

1. Model A implements from the phase contract and hands off an immutable SHA plus evidence.
2. Model B receives only the contract, SHA/diff and evidence location, then attacks it read-only.
3. Model B returns ranked findings with reproducers; “looks good” without mapped evidence is not a
   review.
4. Model A fixes accepted findings and reruns gates. Model B reruns only material failed attacks.
5. The human operator decides residual risk and merge/readiness.

Do not pay this tax for formatting, generated fixtures after the generator is proven, routine type
changes or deterministic documentation updates. Track review yield for the first three high-risk
phases: material findings, false positives and operator minutes. If the second model finds no
material issue while consuming substantial integration time, replace it with one same-model
read-only verifier. Multi-model is valuable as independent review, not as a badge.

## 6. Reusable skills worth building

Claude Code and Codex both implement the open Agent Skills shape, but discover/invoke repository
skills differently: Claude project skills live under `.claude/skills/` and invoke as
`/skill-name`; Codex repo skills live under `.agents/skills/` and invoke via `$skill-name`
([Claude skills](https://code.claude.com/docs/en/slash-commands),
[Codex skills](https://developers.openai.com/codex/skills)). Keep one canonical skill source and a
small validation/copy step for the two discovery paths; do not maintain divergent prose.

| Skill | Trigger | Repeated knowledge encoded | Honest ROI |
| --- | --- | --- | --- |
| `phase-contract` | Planning, changing or closing any `docs/phase-N-*.md`; explicit `/phase-contract` or `$phase-contract` | Existing outcome/guarantee/change-surface convention; required acceptance, offline tests, cost delta, migration/rollback-safe status, measured evidence and explicit “no schema/ESPN behavior change” clauses. | **High.** Stage 6 will create several phase contracts and every implementation phase reuses the gate. |
| `tenant-postgres-gate` | Any change to models, DB/session context, Alembic, pooled queries, data-returning routers, jobs or restore | Forced RLS/runtime roles; transaction-local tenant context; composite tenant FKs; four partial-index predicates; UTC import; sequence repair; 25-query tests under RLS; N−1 compatibility. | **Very high.** This spans the majority of the 122 sensitive call sites and several phases. |
| `provider-data-boundary` | Touching ESPN/sync/cache, fixtures, imports/exports/backups, AI grounding or deployment mode | SPEC §11 read-only/no-login/no-HTML rules; one global request-start permit; public-synthetic/private-local invariant; no credentials/owner IDs/real captures/reports in hosted paths; offline default. | **Very high.** It prevents the easiest trust-boundary regression across unrelated directories. |
| `release-evidence` | Preparing or evaluating a candidate release, migration or restore drill | Positive ten-metric publication proof; external alarms; RDS credit precondition/$8 guard; dual-slot memory; N−1 schema job; PITR/private restore canaries; cost and rollback evidence bundle. | **Medium-high after hosting.** Repeated for every risky release; not needed during ordinary local edits. |

Do **not** build skills for AWS price research, the one-time custody decision, ADR writing or the
SQLite import itself. Prices change and must be re-verified; the other workflows occur once. A
stale skill would preserve obsolete conclusions more efficiently.

## 7. Subagent definitions worth building

Only two project definitions earn maintenance:

| Definition | Trigger | Tools/permissions | Output | ROI |
| --- | --- | --- | --- | --- |
| `adversarial-reviewer` | Candidate SHA exists for a high-risk phase | Read/search/test commands; production paths read-only; no network/live provider/AWS mutation; preload the relevant boundary/gate skill | Ranked findings with contract mapping, file/line, reproducer, severity and untested list | **High.** It operationalizes the second-agent contract across many phases. |
| `evidence-runner` | Long PostgreSQL/RLS/performance/restore/CI command set would pollute the main context | Read and bounded command execution; writes only to temporary/test artifacts; no code edits; routine model (Terra/Sonnet) | Exact command, environment/digest, raw-result artifact location and compact pass/fail/numbers | **High.** It isolates noisy logs while keeping the integrator focused. |

Do not create persistent `aws-architect`, `frontend-agent`, `database-agent` or `docs-agent`
personas. Their boundaries overlap this small monolith and their instructions would restate phase
contracts. For one-off independent price verification or competing-debug hypotheses, use an ad hoc
read-only subagent rather than another maintained definition.

Do not enable Claude Code agent teams for this sprint. Focused subagents return results to the
integrator with less coordination state; the team feature is experimental and explicitly poorly
matched to same-file/sequential work. Codex subagents should follow the same two-definition limit.

## 8. Commands worth exposing

Custom commands should be thin entry points into the skills or deterministic repository scripts,
not another copy of the rules.

| Human command | Trigger/action | Why it repeats | ROI |
| --- | --- | --- | --- |
| Claude `/phase-contract N`; Codex `$phase-contract N` | Create/validate the next phase contract and print missing guarantees/evidence before code changes. | Every Stage 6 phase and amendment. | **High.** |
| Claude `/tenant-postgres-gate`; Codex `$tenant-postgres-gate` | Run or enumerate the forced-RLS, composite-FK, partial-index, timezone, sequence, query-count and N−1 checks for the current diff. | Every database/router/job phase. | **Very high.** |
| Claude `/provider-data-boundary`; Codex `$provider-data-boundary` | Inspect the current diff/artifacts for forbidden hosted data, provider calls, cookie/log or rate-limit regressions. | Fixture, sync, AI, export, backup and deployment phases. | **High.** |
| Claude `/release-evidence`; Codex `$release-evidence` | Produce the preflight evidence manifest; never deploy automatically. | Candidate launches, migrations and restore drills. | **Medium-high.** |

There is no `/deploy`, `/migrate-prod`, `/live-sync` or `/purge` command. Those are high-impact,
infrequent operator actions requiring explicit scope and approval; making them easy for an agent to
invoke has negative ROI.

## 9. `CLAUDE.md` and Codex instruction rules

The current `CLAUDE.md` contains valuable project invariants but also a long phase-by-phase history.
Keep standing rules there and move procedural checklists to skills; link historical phase contracts
instead of loading their summaries in every session. Add a thin root `AGENTS.md` that tells Codex to
read the canonical `CLAUDE.md` completely and applies the same hierarchy. Codex reads `AGENTS.md`
before work and supports nested overrides ([Codex `AGENTS.md`](https://developers.openai.com/codex/guides/agents-md)).

The standing rule block should say, concisely:

1. **Truth hierarchy:** code/tests/database measurements describe current behavior; the accepted
   phase contract describes the authorized target; `SPEC.md` describes product intent. When they
   disagree, do not silently implement the SPEC—flag drift and obey the phase contract.
2. **Mode invariant:** public hosted mode is synthetic-only and has no ESPN cookie, real capture,
   owner/member identifier, real-data AI report or custody-key decrypt path. Private real mode stays
   local unless a new custody decision explicitly changes it.
3. **Provider invariant:** ESPN remains read-only, backend-only, no login automation/HTML scraping,
   and globally admission-limited. Tests are offline unless a human explicitly approves a bounded
   live smoke.
4. **Database invariant:** Alembic is the only production DDL authority; no startup `create_all` or
   hand-written production `ALTER`. Tenant data uses forced RLS under a non-owner runtime role,
   transaction-local context and composite tenant FKs. Run isolation/query tests as that role.
5. **Secret/data invariant:** never log/commit credentials, prompts, private payloads or real member
   identifiers. Hosted fixture fields are allowlisted synthetic grammar, not sanitized captures.
6. **Cost invariant:** no AWS service/resource, AI call or variable-cost path without a priced line
   and guard. No NAT, endpoints, ALB, private stack or pay-as-you-go model credits unless the active
   phase explicitly authorizes them.
7. **Phase invariant:** no implementation before an accepted phase contract; preserve its explicit
   no-change guarantees and attach exact offline evidence before done.
8. **Agent ownership:** one production writer per phase and one owner for `api/models.py`, `api/db.py`
   and Alembic. Delegate only bounded independent/read-only work. A subagent cannot merge, apply,
   migrate, delete or run live providers.

Do not copy the full Stage 0–5 documents into these instruction files. They are decision records and
handoff inputs, not always-on prompt material. The failure accepted by concise rules is that an agent
must open a referenced contract; the benefit is less stale context and less chance that Phase 14
history buries the current trust boundary.

## What a staff engineer would ask about this

1. **“Why call this two agents if only one normally writes?”** Because the scarce independent
   workstream is verification, not typing. A read-only second context can challenge RLS, migration,
   custody and cost claims without creating merge conflicts. If the goal were maximum code volume,
   two writers would be faster; the accepted trade is lower throughput for higher coherence.
2. **“How will you know cross-model review is more than expensive agreement?”** Track material
   findings, false positives and operator minutes for the first three high-risk phases. Require a
   reproducer/evidence mapping, not an approval paragraph. If yield is negligible, keep the verifier
   role but use the same routine model; diversity has to earn its integration cost.
3. **“What prevents two agents from quietly changing the tenant model anyway?”** Ownership is a
   serialized phase contract tied to an immutable SHA. Only Agent 1 writes the database spine;
   Agent 2 returns tests/findings. Worktrees isolate files, CI checks schema heads, and the human
   owner rejects a handoff with an unowned path. The mechanism is exclusive ownership, not hope that
   a merge tool understands tenancy.

**Whiteboard cold for a senior interview:** critical path versus parallelizable work; interface and
file ownership; subagent versus agent-team trade-offs; context isolation/handoff loss; independent
adversarial review; worktrees versus semantic conflicts; why reproducible tests outrank model
consensus.

**Implementation detail to look up:** exact Claude/Codex agent frontmatter, model aliases, worktree
commands, skill discovery/sync mechanics, permission syntax and evidence-report file format.

## Amendments

### E1. Operator review is a finite control-plane resource

The original plan priced model subscriptions but not the person through whom every contract,
finding, merge, apply and residual-risk decision flows. That omission matters more than another
agent's token budget: when the operator is saturated, adversarial review tends to become approval
and the plan's central control fails silently.

For planning, one **working session** is 90 focused minutes. Build-session estimates in Stage 6 do
not hide review; the following operator minutes are a separate capacity reservation. They are
estimates, not measured productivity. Actual availability was not supplied, so the plan makes no
calendar-duration claim.

| Phase | Contract acceptance | Handoff verification | Finding adjudication | Merge/close | Total operator minutes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 30 `private-recovery-baseline` | 35 | 20 | 35 | 15 | **105** |
| 31 `hosted-data-safety` | 40 | 25 | 45 | 20 | **130** |
| 32 `provider-behavior-spike` | 25 | 15 | 20 | 10 | **70** |
| 33 `query-budget-feasibility` | 30 | 20 | 35 | 15 | **100** |
| 34 `linux-cutover-capacity` | 30 | 20 | 30 | 15 | **95** |
| 35 `postgres-alembic-parity` | 40 | 25 | 50 | 20 | **135** |
| 36 `tenant-isolation-kernel` | 45 | 30 | 60 | 25 | **160** |
| 37 `tenant-api-retrofit` | 40 | 30 | 60 | 25 | **155** |
| 38 `query-export-productionization` | 30 | 25 | 35 | 20 | **110** |
| 39 `durable-work-orchestration` | 40 | 25 | 50 | 20 | **135** |
| 40 `identity-session-frontend` | 40 | 25 | 50 | 20 | **135** |
| 41 `observability-retention` | 30 | 25 | 35 | 20 | **110** |
| 42 `public-infrastructure-ci` | 45 | 30 | 60 | 25 | **160** |
| 43 `migration-recovery-rehearsal` | 40 | 30 | 55 | 20 | **145** |
| 44 `public-synthetic-launch` | 45 | 30 | 60 | 30 | **165** |
| 45 `rollback-window-contract` | 25 | 15 | 25 | 15 | **80** |
| **Total** | **580** | **390** | **705** | **315** | **1,990 minutes / 33.2 hours** |

The first three phase evidence ledgers record actual minutes in the same four categories, estimated
versus actual variance, material findings, false positives and material findings per operator hour.
After Phase 32, reforecast Phases 33–45. Two consecutive phases above 120% of budget, or inability to
reserve the next phase's minutes before it starts, is a capacity incident—not permission to skim.

Cuts are predetermined:

1. **Drop cross-model review first** for query/export implementation after its security boundary is
   fixed, routine queue/telemetry mechanics, deterministic fixture expansion after the generator
   core and ordinary Terraform module refactors. Keep a same-model verifier and executable gates.
2. **Drop the separate verifier entirely** for presentation-only frontend changes, documentation
   formatting, generated fixture cases and mechanical manifests. The integrator still runs the
   deterministic phase checks and the operator still accepts/merges.
3. **Do not cut review** for private recovery, the initial hosted-data boundary, phase contracts,
   Alembic/RLS/composite FKs, identity/session authorization, IAM/network/cost guards, AI spend
   authorization, migration/restore or cutover. Defer or reduce those phases instead.

The failure accepted is slower delivery or less polish. Shipping a high-blast-radius phase after
human review has degraded into assent is not accepted.

### E2. Contract authorship and acceptance are separate gates

Agent 1 may draft a phase contract, but cannot authorize it. Before any implementation:

1. Agent 2 reviews the contract itself for scope, guarantees, acceptance criteria, named negative
   tests, no-change clauses, cost, dependency assumptions and expected `rollback_safe` state.
2. The operator adjudicates those findings and explicitly accepts an immutable contract SHA.
3. The handoff record adds `contract_sha`, author, Agent-2 review evidence, resolved/open contract
   findings, operator identity/UTC acceptance time, accepted cost/risks and expected rollback status.
4. Only then may Agent 1 acquire the production-write lease. A later material contract change
   invalidates acceptance and repeats this gate; it is not smuggled into an implementation diff.

Agent 2 still reviews the candidate after implementation. Pre-code review asks whether the team is
building the right contract; post-code review asks whether the accepted contract was satisfied. A
perfect implementation of a wrong contract must fail before code exists.

### E3. Add the `measurement-evidence` skill

Add a fifth high-ROI skill because Stage 0b's evidence method repeats across every implementation
phase and does not go stale like a price table.

| Skill | Trigger | Repeated method encoded | ROI |
| --- | --- | --- | --- |
| `measurement-evidence` | Any numeric performance, capacity, cost-behavior, recovery, compatibility or review-yield claim; explicit Claude `/measurement-evidence` or Codex `$measurement-evidence` | An `E<n>` ledger entry containing question, exact command/query/file, raw artifact hash/location, UTC time, host/OS/architecture/runtime/container limits, dataset/seed/row counts, warm-up/sample count and units. Label every result **measured**, **derived arithmetic**, **externally verified**, **assumption** or **unmeasurable**. State validity domain and confounders. For an unmeasurable quantity, give the exact future method instead of inventing a value. Never turn a theoretical maximum into an expected forecast. | **Very high.** Query counts under RLS, Linux RSS, restore timing, credit draw, N−1 results and operator review yield all reuse it. |

Derived values cite their measured inputs and formula. Assumptions are never promoted to measurements
because they happen to be numerically precise. The skill emits evidence; it does not decide whether
a result satisfies the phase—that remains the accepted contract's job.

### E4. File isolation is not dependency independence

The model-assignment table's isolated worktree for the Synthetic Fixture Factory describes blast
containment, not permission to run it concurrently with the tenancy/schema spine.

| Workstream | Separate-file/worktree isolation | Contract-independent of the spine? | May execute concurrently with the spine? |
| --- | --- | --- | --- |
| Private recovery baseline | Mostly yes | Yes for the current SQLite closure | **No.** It is the first safety gate and finishes before later mutation. |
| Synthetic Fixture Factory + AI ledger | Mostly `tests/`/AI service, but also schema/config | **No.** It consumes parser paths, deployment-mode and ledger/schema contracts. | **No.** Phase 31 is sequential. |
| Live provider measurement | Instrumentation is bounded | Yes after its measurement contract freezes | Technically yes, but deliberately sequential because it uses the only private credentials and operator attention. |
| Query-budget spike | Can use a disposable worktree | **No.** It needs representative synthetic data and a provisional RLS/schema contract. | **No.** Isolation only makes discard safe. |
| Linux cutover-capacity spike | Harness/infrastructure is isolated | **No.** It needs the query/export prototype and task shape. | **No.** |
| Frontend implementation | Yes, `web/**` | Yes only after API/session/runtime-config contracts freeze | **Yes, then only.** |
| Terraform modules | Yes, future `infra/**` | Yes only after ports, roles, metrics, migration and image contracts freeze | **Yes, then only.** |
| Read-only verification/evidence runs | Yes | Yes when supplied an immutable contract/SHA | **Yes.** This is the default useful parallelism. |

Separate files prevent merge conflicts. Only a frozen input/output contract removes a scheduling
dependency. Phase 31 remains assigned to Sonnet 5 as a bounded implementation choice, but it does
not overlap an unfrozen schema phase.

## What a staff engineer would ask about the amendments

1. **“Is 1,990 minutes a forecast or a budget?”** A budget. No prior phase-time telemetry exists.
   The first three phases measure actuals and trigger a reforecast; presenting the estimate as
   measured capacity would violate the new evidence skill.
2. **“Who can weaken an acceptance criterion after implementation starts?”** Nobody unilaterally.
   Any material contract change invalidates the recorded SHA, repeats Agent-2 contract review and
   requires a new operator acceptance before the write lease resumes.
3. **“Why keep a second agent if review time is the bottleneck?”** Keep it only where independent
   falsification can change a high-blast-radius outcome. Low-risk cross-model work and then low-risk
   verification are the first predetermined cuts; safety-critical work is deferred rather than
   waved through.

**GATE: Stage 5 amendments complete. Proceed to Stage 6 only.**
