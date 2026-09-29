# Sprint 9, Stage 1 — credential custody and AI spend authority

Status: decision gate. This stage decides whether a hosted product may accept real ESPN credentials
and who may create provider/AI spend. It does not select the rest of the AWS architecture.

## Executive decision

**Decision (judgment call): ship a multi-tenant hosted portfolio demo with synthetic tenants and no
stranger ESPN credentials. Keep real ESPN access, if retained at all, in a private single-tenant
operator mode for my own accounts.** The optional private mode may use one KMS-backed envelope key
so an unattended worker can sync my data; the public mode exercises the same tenant/job boundaries
with sanitized fixtures and has nothing to decrypt. AI generation in the hosted demo uses synthetic
facts only and has a hard **$5/month Anthropic API ceiling**. Background sync never invokes AI.

This is not avoiding the architecture problem. It isolates the parts the artifact can legitimately
demonstrate—tenancy, authorization, queues, idempotency, encryption, budgets, deletion, audit and
restore—without turning the operator into custodian of strangers' replayable Disney sessions.

Two premise corrections bind the decision:

1. The current DB has **five account credential bundles referenced by 115 leagues**, not 115 unique
   cookie pairs ([Stage 0b rows](00-current-state.md#e2)). A competent 03:00 worker groups leagues by
   account and decrypts at most five bundles for the current portfolio. The future number of
   distinct user accounts is unknown.
2. KMS changes the DB-theft boundary, not the runtime-compromise boundary. A task role allowed to
   read every ciphertext and call `kms:Decrypt` can recover every credential in its scope. Per-user
   data keys improve separation, deletion and audit; they do not save a compromised all-tenant
   worker.

## Why the external contract is the first gate

`SWID` and `espn_s2` are replayable session material, not scoped OAuth grants. ESPN provides this
app no documented client registration, delegated scopes, refresh-token lifecycle, revocation
callback or supported service contract. Cookie expiry requires the person to reauthenticate; the
server cannot safely “refresh” it.

The current Disney Terms apply to ESPN-branded products and grant personal, noncommercial use.
Without written permission they prohibit sharing account information, business-related use, and
using scripts or other automated means to access, extract or compile the products into a database.
They also expressly restrict using product material in prompting/testing an AI tool
([Disney Terms, account security and license restrictions](https://disneytermsofuse.com/english/)).
That is unusually direct exposure for both the sync engine and sending ESPN-derived facts to
Anthropic. This is an architecture review, not legal advice, but encryption cannot cure a provider
contract problem.

## Custody models

All costs below are incremental **custody-control** costs; base compute/database costs remain for
Stage 3. AWS currently charges $1/month per customer-managed KMS key and provides 20,000 KMS
requests/month in its request free tier
([AWS KMS pricing](https://aws.amazon.com/kms/pricing/)). Secrets Manager is $0.40/secret-month plus
$0.05 per 10,000 API calls
([AWS Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/)).

### Model A — hosted server custody with KMS envelope encryption

- **Mechanism/background:** encrypt both cookies as one per-account credential bundle with a data
  key; store ciphertext and the KMS-encrypted data key beside the account. The scheduled worker's
  IAM role calls KMS, obtains the plaintext data key, decrypts in memory and syncs. AWS documents
  this envelope pattern: KMS returns a plaintext data key for immediate use and an encrypted copy
  stored with the data ([AWS data-key documentation](https://docs.aws.amazon.com/kms/latest/developerguide/data-keys.html)).
- **Threat model:** a DB/backup leak without KMS authority exposes ciphertext, account metadata and
  league data but not cookies. A compromised worker, IAM role, dependency or process memory exposes
  every account it can query/decrypt. Logging, crash dumps and SSRF remain exfiltration paths. KMS
  disable/delete also makes every credential unavailable.
- **Blast radius:** passive DB theft is per encrypted bundle; active worker-role compromise is all
  accounts in that role's data scope. One KMS key per user does not reduce that active blast radius
  if the same role may use every key. Encryption context can bind tenant/account identifiers and
  improve audit/tamper detection, but it is non-secret and appears in CloudTrail
  ([AWS encryption context](https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html)).
- **ToS exposure:** **very high.** The operator solicits and stores account information, performs
  unattended scripted extraction and potentially supports business/third-party use.
- **Cost:** one application KMS key **$1.00/month**. Current nightly decryption is about 150 calls
  per 30-day month (five accounts × 30), within 20,000 free requests: **$0 request charge**. A
  per-user KMS key would cost $50/month at 50 users with little protection against the shared-role
  threat; reject it. Storing each cookie pair directly as a Secrets Manager secret would cost
  $2/month for five accounts and $20/month for 50 accounts before calls; it is costlier than
  ciphertext rows plus one KMS key and is not selected.
- **Forecloses:** “server never possesses credentials,” low-liability hobby operation, and casual
  stranger onboarding. Enables reliable unattended sync, which is precisely why its breach and
  provider-contract burden is highest.

### Model B — per-user key that the server never persists

- **Mechanism/background:** the browser derives or holds a key, encrypts the cookie bundle and
  supplies the key only for an active sync session. The server may still see plaintext while it
  calls ESPN; “key not persisted” is not “server never sees the credential.”
- **Threat model:** DB/backup and later server compromise cannot decrypt dormant bundles. A
  compromised web process during use, XSS, malicious browser extension or stolen client key can.
  Key loss is credential-data loss; account recovery cannot silently restore it.
- **Blast radius:** normally one active user/session. A live server compromise can harvest every
  concurrent user's submitted key/cookies, but not dormant users.
- **ToS exposure:** **high.** Automated access and third-party service use remain; reduced custody
  does not grant provider permission.
- **Cost:** **$0/month AWS custody service** beyond existing app storage/compute.
- **Forecloses:** dependable 03:00 sync, server push based on fresh data, and any job that runs with
  no user/key present. Persisting or escrow­ing the key to restore background sync collapses this
  model back into server custody.

### Model C — browser-extension-held credentials, used ephemerally

- **Mechanism/background:** a narrowly permissioned extension reads/uses ESPN cookies locally and
  either calls ESPN itself or sends an ephemeral request through the backend; the backend stores
  normalized facts, not session material.
- **Threat model:** central server breach cannot steal dormant cookies. Extension supply-chain
  compromise, broad browser permissions, local malware and update-channel takeover become the
  primary credential threats. Sending raw responses still creates server-held private data.
- **Blast radius:** one browser profile per extension compromise; extension publisher compromise can
  become every installed profile.
- **ToS exposure:** **high**, though lower on account-sharing custody. A script still automates
  access/extraction; extension distribution does not create authorization.
- **Cost:** **$0/month AWS custody service**. Store registration/review effort is operational, not
  an AWS monthly line item.
- **Forecloses:** a pure web experience and reliable server-side 03:00 jobs. Browser alarms while a
  machine happens to be online are not an unattended cloud schedule.

### Model D — single-tenant self-host

- **Mechanism/background:** each user operates an isolated deployment and supplies credentials to
  their own environment. A local Fernet key or their KMS role enables their own scheduled worker.
- **Threat model:** host compromise exposes one operator's sessions; insecure deploy templates and
  stale versions distribute risk across users. The project author still publishes automation code
  but does not operate a central cookie honeypot.
- **Blast radius:** one deployment/owner, assuming no shared control plane or telemetry receives
  secrets.
- **ToS exposure:** **high for the operator's automated use**, but centralized credential sharing,
  pooled extraction and operator custody are reduced. It is not a ToS exemption.
- **Cost:** local-only custody **$0/month**; KMS-backed AWS custody **$1/month per deployment**, plus
  that user's compute/database chosen later. Funding 50 such KMS keys centrally is $50/month and
  unjustified for this portfolio.
- **Forecloses:** centralized SaaS onboarding, cross-user portfolio aggregation, uniform upgrades
  and one shared operating budget.

### Model E — synthetic tenants plus operator-owned real data only

- **Mechanism/background:** the public hosted system admits no ESPN cookies and replays sanitized
  fixtures through real tenant/job boundaries. An optional private operator deployment may hold my
  five account bundles using Model A and sync them unattended. Public synthetic jobs decrypt
  nothing.
- **Threat model:** the public service's credential blast radius is zero because there are no ESPN
  credentials. The private deployment's active blast radius is my five account bundles. Synthetic
  fixtures must exclude cookies, owner identifiers and raw private responses.
- **Blast radius:** hosted breach exposes synthetic/demo data and application secrets, not stranger
  ESPN sessions. Private breach affects the operator only.
- **ToS exposure:** **lowest hosted option.** Synthetic fixture execution does not access ESPN. Any
  optional live private sync still has the automated-access exposure above. Hosted AI uses synthetic
  facts so it does not transmit real ESPN-derived material to an AI provider.
- **Cost:** public ESPN custody **$0/month**. Optional private KMS custody **$1/month**. This is the
  selected model.
- **Forecloses:** claiming a production-ready ESPN SaaS for strangers or demonstrating real-user
  cookie onboarding. It does not foreclose demonstrating genuine multi-tenant infrastructure,
  isolation tests, quotas, jobs, deletion/export or one private real-data canary.

## The 03:00 question

Background sync makes custody concrete: **the principal that can sync without a person is the
credential custodian.** There is no cryptographic wording that avoids that conclusion.

Under the selected model:

- Public/synthetic mode: a scheduler queues fixture-backed tenant jobs. No cookie exists and no
  decrypt call occurs.
- Optional private operator mode: a dedicated sync-worker IAM role may read only the operator
  account ciphertexts and call one KMS key with a non-sensitive account encryption context. It
  groups 115 leagues by the five current accounts, decrypts each bundle once per run, uses plaintext
  only in memory, and never logs headers, cookies, payloads or encryption context containing private
  values.
- The worker has **no Anthropic API key and no permission to invoke AI**. A clean sync may change
  facts and make a later AI request miss its hash; it does not itself spend AI tokens.
- An expired cookie marks the account `needs_reauth` and stops its jobs. Only the operator can submit
  a replacement session. There is no automated login or refresh-token fiction.

This answers *who decrypts* but not how quickly 115 leagues can sync under the shared 1 rps provider
budget. Queue fairness/capacity remains Stage 2/3 work.

## AI authority and hard spend ceiling

Credential authority and spend authority must not inhabit the same unattended principal. The
selected control is:

1. **No scheduled AI.** AI is an authenticated interactive action over synthetic facts in the
   hosted demo. Private real-data AI is disabled by default because the current Disney terms
   explicitly address AI prompting.
2. **$5/month hard Anthropic API budget (judgment call).** The current 48-report all-miss pattern has
   a measured maximum *output* component below $1 at the temporary Sonnet launch price, but exact
   input/output usage was not stored ([Stage 0b AI evidence](00-current-state.md#e25)). Five dollars
   leaves portfolio-demo headroom without creating an open wallet.
3. **Reserve before calling.** Atomically reserve the token-counted input charge plus the configured
   model's maximum output charge in a monthly ledger; reject when the reservation would exceed $5;
   settle to authoritative response usage afterward. Batch draft recaps reserve per team. Concurrent
   requests cannot spend the same remaining balance. `force=true` is operator-only.
4. **Price conservatively.** Budget reservations use standard Sonnet 5 pricing ($3/M input,
   $15/M output), not the launch discount that ends 2026-08-31; Haiku 4.5 is $1/$5
   ([Anthropic model/pricing overview](https://platform.claude.com/docs/en/about-claude/models/overview)).
   At the code's 2,048-token output cap, Sonnet reserves at least $0.03072 output per call before
   input; a 10-team forced recap batch reserves at least $0.30720 plus input.
5. **Provider limit is defense in depth.** Configure the lowest available provider workspace spend
   limit, but enforce the product ledger because external limits and reset behavior are not an
   authorization system.

Selected incremental monthly control cost:

| Component | Monthly cost | Justification |
| --- | ---: | --- |
| One customer-managed KMS key for optional private ESPN custody | $1.00 AWS | Enables audited unattended decryption without an app master key in `.env`; public mode does not need it. |
| KMS symmetric requests | $0.00 AWS at current scale | About 150 nightly decrypts/month plus enrollment, below the documented 20,000-request free tier. |
| One Secrets Manager secret for the Anthropic API key | $0.40 AWS | Separates the AI principal/key from the sync worker; unlike ESPN cookies, this is one application secret. |
| Secrets Manager API requests | <$0.01 AWS assuming ≤1,000/month | List price is $0.05/10,000 calls; fetch on process/task initialization, not per report. |
| Anthropic API hard ceiling | $5.00/month external | Explicit wallet-loss bound for the portfolio demo. |
| **Total selected custody/spend controls** | **≤$1.41 AWS + $5.00 Anthropic/month** | Base application infrastructure is priced in Stage 3. No item exceeds $10/month. |

## Direct answers

### 1. Does this make the product unshippable to strangers?

**As a hosted product that asks strangers for real ESPN cookies: yes, that is the responsible
conclusion absent written provider authorization.** The blocker is the combination of replayable
bearer custody, unattended automation, explicit Disney usage restrictions, no supported API
contract, no security/on-call team and no demonstrated product need. KMS reduces one breach path;
it does not make that product coherent.

For a portfolio artifact, “shippable” means the repository, hosted synthetic demo, private
single-tenant mode, threat model, ADRs, tests and migration evidence can be reviewed and exercised.
It does not require accepting strangers' sessions. In fact, a clearly enforced provider boundary is
stronger architecture evidence than implementing a risky signup form merely to claim users.

### 2. Is real multi-tenancy the right target?

**Argument for real tenants:** real onboarding would expose lifecycle problems synthetic tenants
miss—cookie expiry, duplicate/shared leagues, ownership transfer, provider latency, noisy-neighbor
rate use, deletion and incident response. It would prove the isolation path under genuine data and
make UX feedback less hypothetical.

**Argument against:** it tests those properties by creating the highest-risk asset in the system,
contradicts the provider's plain-language restrictions, requires a privacy/security support posture
the solo portfolio does not have, and consumes effort that does not improve the core architecture
demonstration. One real user cannot validate demand; strangers are not free test fixtures.

**Recommendation:** build genuinely multi-tenant infrastructure and adversarial isolation tests with
synthetic tenants; run my own real data only as a private, explicitly non-generalizable canary if I
accept the remaining provider risk. Say exactly that in interviews. Do not describe synthetic
tenancy as proof that real ESPN credential operations have been validated.

## Evidence that would change the decision

I would reconsider real-user hosted custody only with most of the following evidence:

- ESPN/Disney offers a documented delegated API with scoped, revocable tokens **or grants written
  permission** for this use, including automated access and derived/AI processing;
- legal review resolves the account-sharing, automation, database and AI-prompting terms;
- validated users specifically need unattended hosted sync rather than local/client-present sync;
- a security review validates tenant authorization, credential/log redaction, key policy, deletion,
  incident response and restore behavior; and
- measured provider capacity and a support/abuse budget show that operating strangers is worth the
  risk and time.

A cleverer cipher alone would not change my mind. The server must still possess decryption authority
for unattended work.

## What a staff engineer would ask about this

1. **“Does KMS protect cookies from a compromised worker?”** No. It protects a database/backup leak
   that lacks KMS authority and improves audit/key lifecycle. A worker that can read all ciphertext
   and decrypt all accounts has the same active credential blast radius as the application data it
   can access. IAM/data scoping, not per-user key count, controls that radius.
2. **“Can a server-never-persists-key design still run at 03:00?”** No. Not reliably. Retaining the
   user key, escrow­ing it or giving a scheduler equivalent authority is server-side custody under a
   different name. Client-held models trade unattended freshness for a smaller dormant breach
   surface.
3. **“What exactly is multi-tenant about a synthetic demo?”** Identity, authorization enforcement,
   tenant-keyed schema/RLS, queue ownership/fairness, quotas, audit, deletion/export, restore and
   adversarial cross-tenant tests. It does **not** validate third-party credential onboarding or
   real provider operations, and the artifact must state that limitation.

**Whiteboard cold for a senior interview:** bearer cookie versus OAuth delegated token; encryption
at rest versus runtime authorization; envelope encryption and encrypted data keys; passive versus
active breach blast radius; human-present versus unattended authority; service-principal separation;
atomic spend reservation; provider terms as an architecture constraint; synthetic tenancy versus
real operational validation.

**Implementation detail to look up:** AWS Encryption SDK APIs, exact KMS key/grant policy syntax,
browser-extension permission manifests, Anthropic token-count/usage fields, and provider-console
spend-limit configuration.

**GATE: Stage 1 complete. Stop before requirements or AWS architecture.**

## Amendments

These amendments close the accepted Stage 1 decision before Stage 2. They do not reopen stranger
credential custody.

### A1. Third-party league data in private mode

The concern is correct and the current count is stronger than the estimate. This query against the
measured DB—`SELECT count(*), sum(is_me), count(*) - sum(is_me) FROM teams`—returned **1,150 total,
115 mine, and 1,035 opponent teams**. Every team has a populated `owner_swids_json`; the current
parser needs owners only to compare them with the account SWID and set `is_me`
([`models.py`](../../api/models.py#L76), [`sync.py`](../../api/services/sync.py#L121)). Rosters,
drafts and transactions then retain other members' fantasy activity even if direct identifiers are
removed.

**Private-mode disposition (judgment call):** compare owner identifiers with my SWID in memory,
persist `is_me`, then drop all owner/member identifiers rather than hash them. A stable hash remains
linkable and buys no application feature. Pseudonymize opponent team/league display names at ingest;
retain roster, matchup, draft and transaction facts only in the private deployment because the
league-relative analytics cannot work without opponents. Retain those competitive facts under the
same deletion and season-retention policy as the league. They must never enter hosted fixtures,
prompts, logs, exports from the public deployment, or shared telemetry.

This accepts provider-contract risk for my own private analytics; minimization does not authorize
automated extraction. Model E remains the **lowest-risk hosted/public option only**. The optional
private real-data mode retains the high automated-access/database-compilation exposure described
for Model D, even after direct identifiers are dropped.

### A2. Deployment separation is an invariant

**Decision (judgment call): use separate AWS accounts if private real-data mode is deployed to AWS;
otherwise keep it local. Never implement public/private as a flag in one deployment.** Sharing source
code is acceptable; sharing a runtime, database, deployment role, observability sink or secret trust
boundary is not.

The invariant is: **no principal trusted by the public account may read private data or call
`kms:Decrypt` on the private custody key.** Enforce this in the private key policy by naming only the
private sync-worker role; do not rely on an identity policy or an application mode check. The public
account receives no private ciphertext, KMS grant, ESPN credential secret, raw capture, real-data
backup or cross-account log subscription. Its CI role cannot deploy the private account.

An AWS account has no monthly fee, but an always-on second stack duplicates some combination of
compute, database, networking, logs and backups. Stage 3 must show (a) the public synthetic stack,
(b) the optional private-stack delta, and (c) the combined total under $150/month. If the combined
topology does not fit, private mode remains local and gives up reliable cloud 03:00 sync. Separate
deployments inside one account are not inherently cheaper when every resource is duplicated; their
cost advantage appears only when networking, data or operations resources are shared, which expands
the common blast radius. One deployment with a flag is rejected.

| Topology | Monthly cost consequence | Decision consequence |
| --- | --- | --- |
| Separate AWS accounts and stacks | AWS account fee is $0; duplicate compute/database/network resources are a Stage 3 line item. | **Selected if private AWS mode exists.** Strongest operator/public administrative boundary. |
| Separate deployments in one AWS account | $0 account fee and approximately the same base cost if fully duplicated; sharing resources can reduce the bill but increases the account/resource blast radius. | Rejected for this artifact because the saving comes from weakening the selected boundary. |
| One deployment with a mode flag | Lowest incremental infrastructure cost because nearly everything is shared. | Rejected: a configuration error or public workload compromise reaches the real-data decrypt path. |

### A3. Synthetic Fixture Factory is a release-blocking deliverable

The repo does not currently provide reproducible proof of sanitization. `raw_cache` contains
709,222,400 bytes, and [`tests/fixtures/README.md`](../../tests/fixtures/README.md) says the
`real_*.json` fixtures were projected and pseudonymized by a recorder kept in a session scratchpad.
That is better than committing raw captures, but the transformation cannot be rerun or audited and
residual structure still derives from a real league.

**Decision:** hosted and CI fixtures are generated from schemas with no real capture as input. Do
not sanitize captures for this path. Synthesis gives up some production-shape realism and requires
explicit schema-drift tests, but it removes the residual re-identification risk that no sanitizer
can prove away.

The named deliverable is **Synthetic Fixture Factory**, provisionally owned by Phase 30
(`hosted-data-safety`; Stage 6 will finalize numbering). It must:

- deterministically generate every ESPN view exercised by parsing/sync plus malformed and
  missing-field variants from a versioned seed and schema contract;
- project only a path-specific allowlist. Allowed string values must come from path-specific enums
  or synthetic grammars such as `Synthetic League L001`, `Synthetic Team T001` and `Synthetic
  Player P0001`; unknown fields fail generation. Cookie/header/member fields are not in the schema;
- emit a manifest containing generator version, schema version, seed and `source=synthetic`, but no
  source league/capture metadata; and
- run `test_hosted_fixture_paths_match_allowlist`, `test_hosted_fixture_strings_match_synthetic_grammar`,
  `test_hosted_fixtures_reject_swid_guid_and_cookie_shapes`, and
  `test_hosted_fixture_manifest_proves_synthetic_provenance` in CI. The string test recursively
  validates **every** string at its JSON path, so a real owner/league name fails for not belonging to
  an allowed grammar; this is not a grep denylist of known people.

No raw ESPN response, transformed real member identifier, real league/team name, athlete name or
derived private fact may appear in the hosted fixture set. Synthetic player names are intentional;
public athlete identity is unnecessary for tenancy evidence.

### A4. Existing AI reports and recorded-derived fixtures are not cloud payload

**Decision:** the 48 existing `ai_reports` are excluded from export and purged before any cloud
import. They are not part of the cloud backup/restore closure. The design-only sprint does not
delete the local rows now; Stage 4 must make the purge a verified, irreversible pre-export step and
record only the count, not the content. Deletion does not retroactively resolve the provider-terms
question, but migration would compound it for no architectural benefit.

The committed `real_*.json` and any fixture projected from a real league are replaced by Synthetic
Fixture Factory output before hosted CI/deployment. Until replacement, they may run only in the
existing local/offline test path; hosted CI is blocked. After equivalent synthetic coverage exists,
remove recorded-derived fixtures from version control rather than treating pseudonymization as
synthetic provenance. Raw-cache objects are also excluded from migration.

### A5. AI ledger prerequisites and trip behavior

The premise is correct. `AnthropicLlmClient.complete_json` currently extracts `parsed_output` and
returns only a dictionary; the `response.usage` object is discarded, and `AiReport` has no token or
cost fields ([`ai.py`](../../api/services/ai.py#L45), [`models.py`](../../api/models.py#L411)).
**Phase 30 (`hosted-data-safety`) owns usage capture and the ledger and must finish before the
public AI generate routes are enabled.** Anthropic's token-count endpoint can reserve input tokens
without a billed model invocation, while the completed response remains authoritative for actual
usage ([token-count documentation](https://platform.claude.com/docs/en/build-with-claude/token-counting)).

The contract is:

- The budget window is a **UTC calendar month**, matching a simple operator budget—not a rolling
  window. Model/pricing version and integer microdollars are stored with each entry; no binary
  floating-point money.
- A PostgreSQL ledger holds one global $5 limit, optional lower per-tenant allowances, and immutable
  reservation/settlement rows keyed by request id. Reserve input-token cost plus configured maximum
  output cost in one transaction before calling Anthropic.
- If the ledger is unavailable or reservation does not commit, generation fails closed; cached
  reports remain readable. There is no provider call and no queued request.
- On a successful provider response, persist authoritative input/cache/output usage and settle the
  reservation. Change the client result contract to return validated content **and** usage; never
  log prompts or responses.
- If the process dies after reservation, a reaper marks the expired reservation `unknown_spent` and
  conservatively charges its full reserve. It is not released automatically because Anthropic may
  have completed the call. Operator reconciliation may reduce it from provider usage records. This
  accepts temporary under-utilization to preserve the hard ceiling.
- When the ceiling trips, generation returns a stable `ai_budget_exhausted` response with the next
  UTC reset time. The UI becomes cached-only and shows a banner; requests are neither queued nor
  retried next month. Emit one deduplicated alarm/notification to the solo operator, plus metrics
  for reserved, settled, unknown and rejected microdollars.

The residual race is external: a provider may accept a request whose response is lost. Conservatively
charging the reservation bounds application-authorized spend; the provider workspace spend limit
remains defense in depth for calls outside this ledger.

### Why this is a stronger portfolio boundary

Removing stranger credentials and real data from the hosted path improves the artifact rather than
merely shrinking it. The hard remaining problems are tenant enforcement, adversarial isolation,
fair scheduling against a non-scaling external request budget, migration correctness and a tested
restore. Those are more transferable senior cloud-architecture signals than a cookie vault built
around an unsupported API.

**Amended Stage 1 gate satisfied. Stage 2 may proceed.**
