# Sprint 9, Stage 2 — derived cloud requirements

Status: requirements derived from the measured system, binding constraints and amended custody
decision. This chooses the required isolation and behavioral contracts. Stage 3 still owns the
infrastructure ADRs and final all-in bill. Public means the hosted synthetic artifact; private means
the optional operator-only real-data deployment in a different AWS account or on the laptop.

Prices were checked on 2026-08-09 against linked vendor pages. They are list prices, exclude tax and
credits, and use US East where a region matters. The target is 1–50 hosted users; no forecast assumes
stranger ESPN credentials.

## Requirements that follow from Stage 0/1

| Decision | Trade-off and accepted failure mode | Incremental monthly cost |
| --- | --- | ---: |
| Bridge tenancy: pooled synthetic tenants, private operator silo | RLS pooling proves real tenant isolation cheaply; the account boundary prevents public compromise reaching operator data. It does not provide per-tenant compute isolation. A private cloud stack may be omitted if its duplicate base cost breaks the ceiling. | AWS account: $0; RLS: $0; optional private stack: must be priced separately in Stage 3. |
| Cognito Lite for identity; application-owned authorization | Cheapest AWS-native identity at this scale, at the cost of AWS coupling and less polished organization UX than Clerk/Auth0. Cognito outage blocks new sign-ins; already-issued tokens survive until expiry. | $0 through 10,000 direct/social MAU; no SMS. |
| PostgreSQL discriminator columns plus forced RLS | One schema and migration stream; every tenant table carries `tenant_id`. Policy/table-owner mistakes remain dangerous, so separate roles and direct-RLS tests are mandatory. | $0 feature charge; database cost is Stage 3. |
| EventBridge Scheduler → SQS Standard fair queue → persistent worker | Durable at-least-once work and backpressure replace an inert scheduler setting. Duplicates and reordering are accepted and handled by idempotency; exactly-once is not promised. | $0 at expected use: Scheduler includes 14M invocations/month and SQS 1M requests/month. |
| Global 1 request/second ESPN admission control | Protects the unsupported upstream and makes capacity explicit. Freshness/manual sync latency degrades before the system increases request rate. | $0 service feature; implementation/storage included in selected compute/database. |
| S3 raw cache with ≤24-hour lifecycle, private mode only | Removes 709 MB from relational backup/IO. S3 latency and loss of old replay payloads are accepted; cache is never a system of record. | ≤$0.11 at the measured uncompressed sub-GB retained volume and daily PUT count; exact S3 requests/storage in Stage 3. |
| 5-minute relational RPO; recovery exercise within 4 operator-hours | Cheap PITR protects the whole ~14.5 MB cache-free relational database, including its ~4.17 MB currently irreplaceable subset. Single-AZ outage and no overnight operator response are accepted; this is not a public SLA. | PITR/backup delta must be priced with the selected database; no second live stack is required for RPO. |
| Hard $5 UTC-calendar-month Anthropic ledger | Fails closed and may strand budget after an ambiguous call rather than overspend. AI is cached-only on trip. | ≤$0.41 AWS for one secret/calls plus $5 external; the optional KMS key is another $1. |

The EventBridge and SQS free allowances are current official terms
([Scheduler pricing](https://aws.amazon.com/eventbridge/pricing/),
[SQS pricing](https://aws.amazon.com/sqs/pricing/)). “$0 expected” is not “free forever”; Stage 3
must calculate request volumes and alarms.

## Product requirements

### R1. Tenancy model: bridge, with two different guarantees

**Decision (judgment call): use a bridge model.** The hosted public deployment pools synthetic
tenants in one database/schema. The private operator deployment is a silo in another AWS account or
remains local. It is never a privileged tenant inside the public pool.

The guarantees are deliberately narrow:

1. A public tenant member can read or mutate only rows belonging to a tenant for which the member
   has an active membership. This is enforced by PostgreSQL, not by remembering a `WHERE` clause.
2. Public workloads cannot reach operator credentials or real data because those assets, IAM roles,
   KMS key and database do not exist in the public AWS account.
3. Pooling does **not** guarantee dedicated CPU, memory, queue capacity or encryption keys per
   tenant. Quotas/fair queuing limit noisy neighbors; they do not create a hard compute silo.

Pure silo was rejected because 50 synthetic tenants would multiply databases/migrations and teach
little beyond account vending. Pure pool was rejected because it would put operator credentials
inside the public blast radius. The bridge accepts two deployment paths and their integration cost
to preserve the custody invariant.

Required target entities are `users`, `tenants`, `tenant_memberships`, `sync_jobs`, `audit_events`
and `ai_budget_entries`. Public fixtures create at least two tenants with deliberately colliding
ESPN league/team IDs so tenant context—not globally unique sample data—is what separates them.

### R2. Identity: Cognito authenticates; the application authorizes

#### Price/fit comparison at 1, 10 and 50 users

| Option | Current entry price | What it buys | What remains ours | Decision |
| --- | ---: | --- | --- | --- |
| Cognito Lite | **$0/month** through 10,000 direct/social MAU; then the first paid tier is $0.0055/MAU | Managed signup/sign-in, password policy, TOTP MFA, hosted login, signed OIDC/OAuth tokens | Tenant membership, roles, object ownership, RLS context, session/CSRF integration | **Select.** Lowest coupling friction inside the AWS budget, though UI/DX are less polished. |
| Auth0 Free | **$0/month** through 25,000 MAU and five Organizations; Essentials is **$35/month** for 500 MAU | Strong universal login and mature identity tooling | App data authorization unless buying/configuring more; free-plan/org limits | Reject. $35 is >10 and unjustified when the selected features cost $0 in Cognito. |
| Clerk Hobby | **$0/month** through 50,000 monthly retained users; basic B2B includes 100 organizations/20 members; Pro is **$20/month billed annually** | Best React components and organization UX | Database RLS and worker/object authorization still ours | Reject. Excellent DX, but adds a non-AWS control plane and $20 to remove branding/get Pro features. |
| Roll our own | Identity-service line item **$0**, plus email delivery | Full control | Password hashing, recovery, verification, MFA, session revocation, abuse defense and incident response | Reject. “Free” transfers security work to one developer and is indefensible for no product differentiation. |

Sources: [Cognito pricing](https://aws.amazon.com/cognito/pricing/),
[Auth0 pricing](https://auth0.com/pricing), [Clerk pricing](https://clerk.com/pricing). SMS is
disabled because it is separately metered; use password plus TOTP, or email flow if Stage 3 confirms
the delivery cost. At the target 1/10/50 users, the selected identity charge is $0 in every case.

**Premise correction:** OAuth 2.0 is an authorization/delegation framework, not an authentication
protocol ([RFC 6749](https://www.rfc-editor.org/info/rfc6749/)). OpenID Connect adds user
authentication. Cognito's OIDC flow tells us *who* authenticated and its OAuth access token conveys
coarse scopes; neither establishes that user X belongs to ESPN Edge tenant Y or owns league Z.

The required flow is authorization code plus PKCE. The API accepts an **access token**, validates
signature, issuer, client/audience, expiry and token use against Cognito's published keys, and maps
immutable Cognito `sub` to `users.identity_subject`. Do not use an ID token as an API bearer. Do not
store tokens in browser local storage; use a same-site, secure, HTTP-only session/refresh cookie and
CSRF protection for mutations, or an equivalently reviewed in-memory PKCE client.

Authorization lives in three layers:

- `tenant_memberships(user_id, tenant_id, role, state)` is the source of tenant roles (`owner`,
  `member`, `viewer`); Cognito groups are not tenant membership.
- Route/service policy maps role plus action (`read`, `sync`, `generate_ai`, `export`, `delete`) to
  permission. `force=true`, budget reconciliation and tenant deletion are owner-only.
- PostgreSQL RLS and composite foreign keys enforce the data boundary even when service code omits
  a tenant predicate.

Accepted failure: Cognito is a regional external dependency. Existing valid application sessions
can continue until their short access-token expiry, but new sessions and refresh fail; the app shows
an authentication-degraded state rather than bypassing identity.

### R3. Database tenant isolation: direct discriminator plus forced RLS

**Decision:** every tenant-owned row gets a non-null `tenant_id UUID`; tenant IDs are included in
unique keys and composite foreign keys. A child may not point at a parent in another tenant even
when integer IDs collide. Schema-per-tenant was rejected because Alembic work and connection/search
path risk scale with tenants. App-only discriminator filters were rejected because Stage 0 counted
122 sensitive call sites across 19 files and one forgotten filter is already known to be plausible.

Tenant-owned target tables include accounts, leagues, teams, draft picks, matchups, lineup/current
rosters, transactions, tenant player observations, metrics/snapshots/rollups, AI reports, sync jobs,
audit events, exports and AI budget entries. Shared read-only reference tables are NFL player
identity, nflverse mappings/opportunity facts and FFC reference snapshots. Current `players` must be
split: identity/name may be shared, but ESPN ADP/ownership/projection values are tenant/league
observations today and cannot remain global last-writer-wins state.

Enforcement requirements:

- Enable and `FORCE ROW LEVEL SECURITY` on every tenant table. Default is deny. Policies use both
  `USING` and `WITH CHECK` against transaction-local `app.tenant_id`; missing/malformed context
  returns no rows/rejects writes.
- The web and worker runtime roles are neither table owners, superusers nor `BYPASSRLS`. PostgreSQL
  documents that superusers, `BYPASSRLS` roles and normally table owners bypass policies
  ([row-security documentation](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)). A
  separate migration owner performs DDL and is unavailable to application tasks.
- Set verified `app.user_id` and `app.tenant_id` with `SET LOCAL` inside the same transaction as
  business queries. Connection-pool reuse must begin a new transaction and prove context cannot
  bleed. Never accept tenant authority from an unverified header, URL ID or queue body alone.
- Membership listing is scoped by `app.user_id`. Selecting a tenant requires an active membership;
  only then is tenant context set. A worker message is accepted only from the queue producer role,
  then sets its tenant context and must find the matching tenant/job under RLS.
- Use composite references such as `(tenant_id, league_id)` and `(tenant_id, team_id)` throughout.
  Referential-integrity checks can bypass RLS, so error messages must not reveal whether a foreign
  tenant key exists.
- Fix the current repoint primitive before hosted migration: `POST /api/leagues` may create an
  absent `(tenant_id, espn_league_id, season)` or return the existing same-account record. A
  different `account_id` returns `409 league_account_conflict`; implicit transfer is unsupported.

The accepted failure mode is an intentionally privileged migration/restore operator. That role can
see all public synthetic tenants, so it is short-lived, audited and never used by the API or worker.
RLS is defense against application mistakes, not against the AWS account administrator.

### R4. Adversarial isolation tests are the proof, not supporting coverage

Synthetic tenancy has no production user history to lean on, so release evidence is a named suite
run against PostgreSQL migrations with the real runtime role:

1. `test_tenant_a_cannot_read_tenant_b_object_by_guessed_id`
2. `test_tenant_a_cannot_update_delete_or_repoint_tenant_b_league`
3. `test_missing_tenant_context_denies_select_insert_update_delete`
4. `test_forged_tenant_header_and_path_cannot_override_membership`
5. `test_connection_pool_does_not_reuse_prior_tenant_context`
6. `test_composite_fk_rejects_cross_tenant_team_and_league_links`
7. `test_raw_sql_without_tenant_where_is_still_filtered_by_rls`
8. `test_worker_job_tenant_mismatch_performs_no_fetch_and_is_quarantined`
9. `test_export_ai_cache_and_budget_are_tenant_scoped`
10. `test_s3_cache_key_is_derived_from_authorized_tenant_not_request_input`
11. `test_runtime_roles_cannot_disable_rls_or_assume_migration_role`
12. `test_delete_tenant_removes_primary_rows_and_queues_backup_expiry_record`

Each test creates two tenants with colliding external IDs and canary strings, attacks through both
HTTP and a direct SQLAlchemy session, and asserts no canary appears in body, status detail, logs,
export or AI facts. SQLite runs remain useful unit feedback; only the PostgreSQL lane is isolation
evidence.

### R5. Data classification, minimization and retention

Classification is about the meaning of the row, not whether an individual scalar looks harmless:
an athlete ID associated with a private fantasy roster is tenant-confidential even though the
athlete is public. All network paths require TLS 1.2+; PostgreSQL clients require certificate and
hostname verification. All database/storage volumes are encrypted at rest. `S0` additionally uses
application-level KMS envelope encryption; `S1/S2/S3` use encrypted RDS storage, and S3 objects use
server-side encryption with public access blocked.

| Current table and every current field | Class | Target retention/disposition | Authorized readers |
| --- | --- | --- | --- |
| `accounts.id`, `label`, `status`, `created_at` | S1 restricted account metadata | Private account only; until operator deletes account, then primary copy ≤24h and backup expiry ≤35d. Public uses synthetic account records only. | Private operator API and scoped sync worker. |
| `accounts.swid`, `espn_s2_encrypted` | S0 credential/identifier material | Replace both columns with one KMS-envelope-encrypted credential bundle in private mode. SWID is decrypted only to make the request and identify `is_me`; no plaintext column. Never exists publicly. | Private credential-write path and sync-worker role; no read-back endpoint. |
| `leagues.id`, `espn_league_id`, `season`, `account_id`, `size`, `scoring_json`, `lineup_slots_json`, `draft_type`, `playoff_team_count`, `current_scoring_period`, `current_matchup_period`, `lifecycle`, `my_team_id`, `is_public`, `last_synced_at`, `last_sync_ok`, `last_sync_error` | S2 tenant facts; diagnostics are S3 but remain tenant-scoped | Public synthetic: regenerate/delete with tenant. Private: current plus previous completed season, then delete. Errors are redacted and retained 90d/current value only. | Tenant members by role; private sync worker may mutate. |
| `leagues.name` | S1 potentially identifying display text | Synthetic grammar publicly. Private pseudonymized on ingest; same two-season limit. | Same tenant only. |
| `teams.id`, `league_id`, `espn_team_id`, `is_me`, `autodrafted`, `wins`, `losses`, `ties`, `points_for`, `points_against`, `standing` | S2 league-member competitive facts | Same league retention; delete transitively with league. | Same tenant only. |
| `teams.name`, `abbrev`, `owner_swids_json`, `logo_url` | S1 member identifiers/potential personal text | `owner_swids_json` is never persisted in the target. Opponent name/abbrev are pseudonymized; external opponent logos are dropped. Synthetic values only publicly. | Private ingest sees owners transiently; persisted pseudonyms are same-tenant. |
| `draft_picks.id`, `league_id`, `overall`, `round`, `round_pick`, `team_id`, `espn_player_id`, `keeper`, `autodraft`, `bid_amount`, `adp_at_draft`, `value_delta` | S2 tenant competitive facts | Two-season private limit or synthetic tenant lifetime; cascade with league. | Same tenant only. |
| `matchups.id`, `league_id`, `week`, `home_team_id`, `away_team_id`, `home_points`, `away_points`, `home_projected_points`, `away_projected_points`, `is_playoff` | S2 tenant competitive facts | Same as league. | Same tenant only. |
| `lineup_slots.id`, `league_id`, `team_id`, `week`, `slot`, `espn_player_id`, `points`, `is_starter` | S2 roster/performance facts | Same as league. | Same tenant only. |
| `current_roster_snapshots.id`, `league_id`, `scoring_period`, `matchup_period`, `synced_at` | S2 tenant roster metadata | Latest successful snapshot only; replace on clean sync and cascade with league. | Same tenant and sync worker. |
| `current_roster_entries.id`, `snapshot_id`, `team_id`, `lineup_slot_id`, `slot_index`, `espn_player_id`, `player_name`, `player_position`, `nfl_team`, `opponent`, `kickoff_at`, `game_status`, `injury_status`, `actual_points`, `projected_points` | S2 association of public sports facts with a private roster | Latest snapshot only; public values generated synthetically; cascade with snapshot. | Same tenant and sync worker. |
| `transactions.id`, `league_id`, `team_id`, `type`, `week`, `player_in`, `player_out`, `bid`, `executed_at` | S2 league-member behavior | Same two-season/private or synthetic-tenant retention; cascade with league. | Same tenant only. |
| `players.espn_player_id`, `name`, `position`, `nfl_team`, `ffc_id` | S4 shared sports reference | Shared identity/reference while a retained season needs it; remove when no referencing season remains. Synthetic hosted fixtures use synthetic players despite public athlete identity. | Authenticated API/worker read; controlled reference importer writes. |
| `players.espn_adp`, `espn_pct_owned`, `espn_rank_ppr`, `proj_ros`, `updated_at` | S2 provider-derived observation currently overwritten globally | Move to tenant/league-season player observations with observation time; two-season retention. Do not expose as a shared global row. | Same tenant only. |
| `players.ffc_adp` | S4 attributed FFC reference | Move to versioned FFC snapshot/observation; retain latest plus 30d unless referenced by a retained draft. | Authenticated users read; importer writes. |
| `nflverse_player_maps.espn_player_id`, `gsis_id`, `status`, `method`, `updated_at` | S4 public-reference mapping; manual method is S3 provenance | Retain while supported/referenced; version changes and last-good state. | Authenticated read; opportunity importer writes. |
| `opportunity_weeks.id`, `season`, `season_type`, `week`, `game_id`, `gsis_id`, `team`, `opponent_team`, `position`, `carries`, `carry_share`, `targets`, `receptions`, `rushing_yards`, `receiving_yards`, `receiving_air_yards`, `receiving_tds`, `team_passing_yards`, `target_share`, `air_yards_share`, `wopr`, `rushing_epa`, `receiving_epa`, `fantasy_points_ppr` | S4 public nflverse reference | Current plus previous season; reloadable but preserve version/fingerprint diagnostics. | Authenticated read; importer writes. |
| `opportunity_imports.id`, `season`, `state`, `started_at`, `completed_at`, `latest_week`, `input_rows`, `stored_rows`, `matched_players`, `unmatched_players`, `retries`, `package_version`, `schema_fingerprint`, `error_code`, `error_message`, `details_json` | S3 operational provenance | 90d detailed diagnostics; keep one terminal summary per source version/season with backup. Errors must contain no payload/identity. | Operator and importer; API may expose bounded non-sensitive status. |
| `adp_snapshots.id`, `source`, `pulled_at`, `format`, `teams` | S3 source/provenance | Latest plus 30d; referenced historical snapshot may follow two-season limit. | Authenticated read; importer writes. |
| `adp_snapshots.payload_json` | S4 if FFC; S2 if ESPN-derived | Split by source. FFC is shared/versioned; ESPN becomes tenant-scoped and is never copied to public real-data fixtures. | Reference importer/authenticated read or same tenant, respectively. |
| `metrics.id`, `league_id`, `team_id`, `key`, `week`, `value_float`, `computed_at` | S2 derived tenant analytics | Current recomputable value only; replaced on recompute and cascades with league. | Same tenant only. |
| `metric_snapshots.id`, `league_id`, `team_id`, `batch_id`, `key`, `period`, `value_float`, `recorded_at` | S2 app-generated history | Raw clean-sync events 90d, at most one unchanged-equivalent batch/league/day; weekly rollups for two seasons; then delete. | Same tenant only. |
| `ai_reports.id`, `league_id`, `scope`, `kind`, `input_hash`, `model`, `content_json`, `created_at` | S2 derived narrative; model/provenance fields are S3 | Existing 48 real-data rows are purged before migration. Hosted synthetic reports expire after 90d or tenant deletion, whichever is first. Private real-data AI remains disabled. | Same tenant for synthetic reports; operator can purge, not inspect across tenants via app. |
| `raw_cache.key`, `fetched_at`, `payload_json` | S1 high-risk raw provider response; key/metadata may disclose IDs | Do not migrate existing 576 rows. Private S3 objects only, path-derived tenant/account namespace, ≤24h lifecycle, no versioning/backups. Public deployment has no raw cache. | Private sync worker; operator break-glass only. Never normal API/export. |

New target fields follow the same rules: `users.identity_subject` and optional alias are S1;
`tenants`/`tenant_memberships` are S1/S3; `sync_jobs`, `audit_events` and AI usage/reservations are S3
with tenant scope. Authentication email stays in Cognito unless the app has a demonstrated need to
copy it. Audit entries retain actor subject, tenant, action, target type/opaque ID, outcome,
request/job ID and timestamp—not payloads, prompts, names, SWIDs, cookies or response bodies.

The two-season private retention limit is a **judgment call**: it preserves one comparison season
while bounding third-party fantasy history. It accepts loss of long-term trend analysis. Raw
snapshot retention is separately bounded because Constraint 13 shows it would add about 187.8 MB
per year at one nightly clean sync with no policy.

### R6. Compliance: privacy engineering applies; certification theater does not

This section is engineering scope, not legal advice.

- **SOC 2:** not a law and not a requirement for this portfolio. It is a CPA examination of a
  service organization's controls requested by customers/partners
  ([AICPA SOC overview](https://www.aicpa-cima.com/soc)). Buying a compliance platform, writing
  fictional policies or claiming “SOC 2 ready” would be theater. Implement the useful controls—least
  privilege, change history, backups, audit events and incident notes—without claiming attestation.
- **GDPR/UK-style data rights:** scale is not a blanket exemption. GDPR can apply to an organization
  outside the EU that deliberately offers even free services to people in the EU; some duties such
  as a DPO may not apply to low-risk non-core processing
  ([European Commission](https://commission.europa.eu/law/law-topic/data-protection/reform/rules-business-and-organisations/application-regulation/who-does-data-protection-law-apply_en)).
  The public artifact should not market to EU users without a legal review, but minimization,
  purpose notice, export, correction and deletion are requirements regardless.
- **CCPA:** this solo artifact is far below the current $26.625M revenue and 100,000-person
  thresholds and does not sell/share personal data, so it is not presently a CCPA business on the
  known facts ([California Privacy Protection Agency](https://cppa.ca.gov/faq)). Re-evaluate if the
  facts change; do not build a “Do Not Sell” workflow for data that is never sold.
- **COPPA:** the artifact is general-audience and must not target children under 13. If the operator
  gains actual knowledge of an under-13 user, suspend and delete rather than attempt parental-consent
  operations; COPPA covers child-directed services and general services with actual knowledge
  ([FTC](https://www.ftc.gov/legal-library/browse/rules/childrens-online-privacy-protection-rule-coppa)).
- **HIPAA, PCI DSS, FedRAMP:** no health data, cardholder data or government authorization boundary
  exists. They do not apply. AWS's certifications do not transfer certification to this app.
- **Provider contract:** Disney/ESPN restrictions remain binding product constraints in the private
  mode; privacy controls do not cure them. Hosted fixtures and AI remain synthetic only.

Required user-data operations:

- **Export:** owner/member-authorized, tenant-scoped JSON plus CSVs with a manifest/schema version;
  no credentials, raw cache, internal audit details or another tenant's rows. Audit start/outcome.
- **Delete:** owner confirmation creates an idempotent deletion job, blocks new work, removes queue
  jobs and S3 prefix, cascades primary tenant rows within 24h, then deletes the Cognito user only if
  it has no other memberships. A tombstoned audit record contains no direct identity. Backups age
  out within the configured maximum 35 days; the UI states that delay rather than claiming instant
  backup erasure.
- **Audit:** append-only application events for login linkage, membership/role changes, credential
  enrollment/reauth/deletion, league-account conflict, sync enqueue/result/replay, export, AI
  reservation/settlement/trip, tenant deletion and migration. Retain 90d, restrict to operator, and
  test log redaction. Do not log every ordinary read or install `pgAudit` without a concrete need;
  that adds noise/cost and can capture sensitive statement values.

## Non-functional requirements

### R7. Recovery objectives and availability

**RPO:** at most **five minutes** for the cache-free relational dependency closure, including
accounts/configuration, tenant membership, jobs, AI ledger, metrics history and audit events. Raw
cache and generated exports are explicitly outside RPO. The five-minute target follows RDS's
documented transaction-log upload interval
([PITR documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html)); a
different PostgreSQL option must prove an equal or better continuous-backup bound. Configure 14-day
PITR retention plus a pre-migration/manual snapshot. `LatestRestorableTime` lag is monitored.

**RTO:** complete a restore, migrate, verify and switch the public artifact within **four hours after
the operator starts recovery**. With no on-call rotation, failure-to-human-response time is
unbounded; calling this a four-hour calendar RTO would be dishonest. A restore drill must create a
separate database, verify row counts/constraints/RLS/canaries and serve smoke reads before deletion.
Stage 4 owns the runbook and timed drill.

**Availability target (judgment call): 99.0% measured monthly, no external SLA.** That permits about
7h18m downtime in a 30.44-day month. The public demo may be unavailable during a single-AZ failure
or planned migration, and the private optional mode has no availability promise. “Zero downtime”
later refers only to a rehearsed cutover, not this target.

| Level | Monthly downtime budget | Required cost step | Decision |
| --- | ---: | --- | --- |
| 99.0% | ~7h18m | Single-AZ base architecture plus tested restore; no HA premium beyond backup. | Select for portfolio. |
| 99.9% | ~43m50s | At minimum: Multi-AZ database (roughly another full DB instance/storage and doubled write IO), a second compute instance/task in another AZ, health-based routing and usually an ALB. An ALB alone starts at **$16.43/month** (`$0.0225 × 730`) before LCU; exact DB/compute delta depends on the Stage 3 choices. | Reject until measured use/need justifies the recurring duplicate stack. |
| 99.99% | ~4m23s | Multi-region data/identity/routing, automated regional failover and continuously tested operations. It adds at least another regional stack and coordination failure modes; the bill cannot be credibly stated before selecting the 99.9% components and is incompatible with this solo $150 ceiling. | Explicitly out of scope. |

The cost of a nine is not a magic percentage: it is the duplicate fault domain and operational
automation required to remove the next class of failure. Stage 3 must substitute actual line items
for the 99.9% formula and may not claim the target from a managed-service SLA alone. Current ALB
pricing is from [AWS](https://aws.amazon.com/elasticloadbalancing/pricing/).

### R8. ESPN capacity is one admission-controlled resource

**Premise correction:** the current throttle is not per app. It is an in-memory timestamp per
account key inside each `EspnService` instance; concurrent routes/workers bypass one another
([Stage 0](00-current-state.md#outbound-calls-and-scheduled-work),
[`espn.py`](../../api/services/espn.py#L94)). Also, 1 rps is a project guardrail, not a documented
ESPN quota. The cloud requirement deliberately strengthens it to **one global request start per
second, burst one, across every worker/account in the private deployment**, including retries and
manual sync.

The current sync code makes five league-specific base calls when a current period exists, one call
per completed week, and one cacheable pro-schedule call shared across the run. Therefore the current
115-league shape requires approximately `115 × (5 + completed_weeks) + 1`: **576 request starts
(9.6 minutes)** at week 0 and **2,646 (44.1 minutes)** at 18 completed weeks, before retry/backoff.
The representative replay's six provider calls confirms the zero-week per-league shape, but these
wall times are arithmetic lower bounds, not live ESPN measurements.

Capacity/fairness requirements:

- The limiter is distributed and fail-closed: if workers cannot acquire the shared permit, no ESPN
  call starts. Increasing worker count does not increase the rate.
- Every queued sync has an estimated request weight `5 + completed_weeks`; a shared pro-schedule
  fetch is charged once. Scheduling uses weighted oldest-first/round-robin service, with one active
  sync per tenant and duplicate scheduled/manual requests coalesced. When multiple tenants wait,
  each receives one league job before a tenant receives its second, subject to a currently running
  job. One league can therefore delay another by its bounded call count, not an entire portfolio.
- SQS Standard messages set `MessageGroupId=tenant_id` to enable AWS fair-queue behavior, but that
  feature only affects delivery/dwell time; it does **not** allocate ESPN permits. The application
  admission ledger remains authoritative
  ([SQS fair queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-fair-queues.html)).
- A nightly private run opens at 03:00 in the configured operator timezone with a two-hour flexible
  window. Newer scheduled runs coalesce with an unfinished equivalent run rather than accumulating.
  Manual work may be prioritized but never bypasses the global limiter or starves an older tenant.
- On 429/5xx, use jittered exponential backoff and a shared cooldown visible to all workers. An auth
  failure is non-retryable and sets `needs_reauth`; schema/validation poison is quarantined. A live
  measurement of latency, response bytes, 429s and cache hits is required before changing cadence.
- Public hosted synthetic work uses the same queue/idempotency/fairness code with a fake provider
  and fake clock, but consumes no ESPN permit. Do not present synthetic fairness as measured ESPN
  capacity.

The accepted failure is stale data: queue age rises and manual requests wait rather than violating
the fixed upstream budget. Alert before the nightly two-hour window is missed. Do not autoscale a
non-scaling dependency.

### R9. Performance and resource bounds

The cloud migration may not carry 697–929 serialized SELECTs across a network and call the result a
database problem. Before PostgreSQL cutover:

- portfolio board and the three profiled analytics reads issue **no more than 25 SELECT statements
  each** on the 115-league representative dataset; no ordinary endpoint may exceed 50;
- warm p95 for those four reads is ≤750ms with compute and database in the selected topology, with
  query count and database time reported separately;
- direct persistent pooled connections are the baseline. RDS Proxy is not enabled without a
  container benchmark showing that reduced connection churn outweighs its extra hop and monthly
  charge **after** N+1 removal; per-invocation connection storms are not an accepted design;
- JSON export streams rows/arrays incrementally and must not materialize the current 14.35 MB body.
  CSV streams; XLSX uses write-only generation with bounded encrypted temporary storage. On the
  representative export, peak process RSS must be ≤256 MiB and the first response byte ≤1s;
- repeat the sync/read/export profile under the target Linux container/cgroup with one and two
  concurrent exports before final compute sizing. Until then, concurrency remains unknown and is
  bounded to one active export per tenant and a small global worker limit.

These are judgment-call budgets chosen to force removal of the measured amplification and 435 MiB
export high-water. The accepted failure is lower export concurrency and loss of resumability for a
failed streaming response; the client retries from the beginning.

### R10. Historical growth and retention job

On a clean sync, write at most one metric-snapshot batch per league/day and only when the tracked
metric vector differs from the latest retained vector. Keep raw events 90d; create one weekly
rollup per league/team/key for two seasons; delete older raw and rollup rows in bounded batches.
Retention must be idempotent, observable and excluded from request latency.

At the measured 20 rows/batch and 115 leagues, 90 raw days cap the steady raw term near 207,000 rows
before indexes; two years of weekly rollups add about 239,200 rows. This accepts loss of subweekly
detail after 90d and history beyond two seasons. Partitioning is not a requirement at this size;
revisit only when delete/vacuum/index measurements justify it.

## Backend requirements: changes to `api/`

### R11. PostgreSQL and versioned schema migration

SQLite remains a fast local/test option; **hosted mode requires PostgreSQL**. The production engine
comes from `DATABASE_URL`, uses a bounded persistent pool, `pool_pre_ping`, TLS hostname validation
and transaction-scoped tenant context. SQLite-only PRAGMA/connect arguments are dialect-gated.

Alembic becomes the only production schema authority:

- Build one reviewed baseline from the target model, not a blind autogenerate. It must include
  explicit PostgreSQL predicates for all four metric partial unique indexes, `tenant_id`, composite
  keys/FKs, `TIMESTAMPTZ`, correct identity sequences and the two opportunity columns currently
  added by handwritten `ALTER TABLE`.
- Interpret all 85,125 existing naive datetime strings as UTC during import; application models
  return aware UTC values. Use JSONB for the current SQLAlchemy JSON columns, but add a JSONB index
  only where a measured predicate needs it. AI weekly/opponent scope should become explicit columns
  rather than loading every report and inspecting `content_json` in Python.
- Change global `UNIQUE(espn_league_id, season)` to
  `UNIQUE(tenant_id, espn_league_id, season)`. Add target uniqueness to membership, job
  idempotency, budget request ID and snapshot run ID.
- Apply migrations in a single one-off runner before new application tasks receive traffic, under a
  database advisory lock. Web/worker startup performs no DDL and fails readiness if the Alembic
  revision is not the expected one. Remove production `create_all` and additive-ALTER startup paths.
- Use expand/contract for running releases: add nullable/backfill/validate before making non-null or
  dropping old columns. A migration test builds empty PostgreSQL from baseline and another upgrades
  a scrubbed SQLite-derived export through the import path.
- After explicit integer ID import, set each PostgreSQL sequence above `max(id)` and prove the next
  insert. Validate all FKs, unique/partial indexes, row counts, UTC interpretations and RLS policies.

Trade-off: one migration path and strong constraints replace inspectable file resets; local repair is
less casual. Accepted failure is deployment pause on schema mismatch, not best-effort startup. The
feature has no separate AWS charge; runner minutes and database instance are priced in Stage 3.

### R12. Tenant context and authorization dependency

Replace direct `Depends(get_session)` use with one request-unit-of-work dependency that:

1. validates the Cognito access token and resolves application user;
2. resolves active membership for the tenant selected by the route/session;
3. opens a database transaction and sets `app.user_id`, `app.tenant_id` and a request ID with
   `SET LOCAL`; and
4. commits only after the handler succeeds, otherwise rolls back and closes.

Service methods accept an already-scoped session; they do not accept arbitrary tenant IDs as a way
to bypass context. Object lookup outside the current tenant returns 404, including guessed IDs, so
existence is not disclosed. Routers declare the required action/role. Public deployment does not
mount credential-enrollment or real ESPN sync routes at all; absence of KMS/data is the primary
boundary, route absence is defense in depth.

Worker context is separate from user context. Only the producer role may send SQS messages. A
message contains opaque `job_id`, `tenant_id`, `kind`, `idempotency_key` and trace correlation—never
cookies, KMS plaintext, external names or payloads. The worker sets tenant context from the trusted
message, then must find the same job/tenant under RLS before doing work.

### R13. Scheduler and queue-based sync worker

**Premise correction:** there is no APScheduler job to migrate. The dependency and `SYNC_CRON` are
inert; Stage 0 found no scheduler construction or registration. Implement scheduling for the first
time as EventBridge Scheduler invoking an SQS Standard queue. One schedule tick discovers due work;
do not create a schedule/resource per synthetic tenant.

The durable job contract is:

- `sync_jobs` records `tenant_id`, target/kind, scheduled window, request-cost estimate, state,
  attempt, idempotency key, timestamps, redacted error code and source (`scheduled|manual|replay`).
  States are `queued → running → succeeded|partial|needs_reauth|failed|dead`.
- A unique key such as `(tenant_id, target_type, target_id, sync_kind, scheduled_window)` coalesces
  duplicate schedule/API requests. `POST` returns the existing job when appropriate. A database
  row with `dispatched_at IS NULL` doubles as an outbox; a bounded relay sends it and marks dispatch.
  Send-success/update-failure may duplicate, which idempotency accepts; commit-success/send-failure
  is retried, so accepted work is not silently lost.
- SQS delivery is at least once and unordered. League writes occur in one transaction; replay uses
  the same run ID so a clean retry cannot append a second metric snapshot. A worker crash after
  commit but before delete therefore becomes a no-op success on replay.
- Heartbeat/visibility timeout exceeds measured work and is extended while active. Transient
  network/429/5xx/database conflicts retry with bounded jitter. Auth expiry is terminal
  `needs_reauth`. Parser/schema invariant, tenant mismatch or invalid payload goes directly to
  quarantine/DLQ. After five receives, move to a 14-day DLQ, alarm, and require an operator-audited
  replay.
- Backpressure: one active sync per tenant, one pending equivalent per target, a bounded manual
  submission rate, coalesced schedule ticks and no recursive retry enqueue. Queue-age/fairness
  controls from R8 apply before workers scale.
- The private account-level run groups its 115 leagues by the five current credential bundles and
  decrypts each bundle at most once for that run. Per-league checkpoints/idempotency allow resume.
  Plaintext remains process memory only for the account batch; this accepts a longer active-memory
  window to avoid 115 decrypts and is confined to the private worker process.

SQS fair queues add no separate surcharge over Standard requests. At 115 nightly league jobs plus
manual/retry operations, expected use is far below the one-million-request free allowance, but
Stage 3 must count send/receive/delete/visibility operations rather than count only messages.

### R14. Credentials, keys and raw payloads

`api/crypto.py` becomes a credential-vault interface. Public mode has no implementation that can
reach a custody key. Private mode uses one customer-managed KMS key for envelope encryption, with
`tenant_id` and `account_id` as non-secret encryption context and a key policy naming only the
private sync role and narrowly scoped enrollment path. KMS failure fails the sync closed. Python
cannot promise reliable memory zeroization; do not claim it—minimize plaintext lifetime and process
scope instead.

Production `.env` contains no secret. Store the Anthropic API key as one Secrets Manager secret
available only to the public AI principal. Database credentials use the database service's managed
secret/rotation or a separately scoped secret selected in Stage 3. The private sync role has no
Anthropic secret; the public role has no ESPN key/ciphertext.

`RawCache` no longer writes JSON into PostgreSQL:

- Private mode writes compressed JSON to a private S3 bucket/prefix using an opaque account UUID,
  request digest and fetch timestamp. Neither object key nor metadata contains SWID, cookie, league
  name or raw URL/query values.
- Block public access, require TLS/encryption, deny cross-account principals, disable versioning and
  expire objects within 24h. Cache objects are absent from relational backup and cross-account
  replication. Existing 709 MB/576 rows are not migrated.
- Persist only bounded cache metadata needed for hit/miss/age/bytes/error telemetry. Normal APIs,
  exports and hosted fixtures cannot read/list raw objects. An audited operator-only replay path may
  read them before expiry.
- Public synthetic mode reads immutable generated fixture artifacts and never calls this cache.

S3 is selected over a TTL database because payloads average 1.23 MB and already dominate the DB by
48.9×. The accepted failure is losing replay material after 24h; normalized facts and backups—not
raw cache—provide durability.

### R15. Exports

All export services receive the scoped unit of work and stream tenant rows. JSON/CSV include a
schema version and generated timestamp; XLSX uses write-only mode. Exclude credential fields, raw
payloads, internal budget rows and unrestricted audit details. Team/member fields follow the
private pseudonymization/public synthesis rules.

One active export per tenant is allowed. Audit actor/tenant/type/start/result/byte count, never
content. If a format requires staging, use an encrypted opaque S3 object with a ≤15-minute signed
download and ≤24h lifecycle; otherwise stream directly. A disconnected client cancels work and
cleans temporary files. The accepted failure is restart-from-zero rather than resumable multipart
export. Memory/latency bounds are R9.

### R16. AI reports and spend ledger

Phase 30 must change the LLM client return type to validated content plus authoritative usage, add
token/cost/pricing-version fields, create global/per-tenant ledger rows, and implement the exact
reserve/settle/ambiguous-expiry behavior in Stage 1 A5. The hosted API accepts only synthetic facts.
Existing 48 reports are a purge count, not migration rows; private AI remains disabled.

AI reads check tenant-scoped cache first. A cache hit needs no reservation; a miss/`force=true`
requires owner permission and a successful atomic reservation. Generation never runs in the sync
worker or scheduler. Expose stable enabled/cached-only/reset state without provider balance or key
details. Metrics distinguish attempts, cache hits, rejected budget, reserved/settled/unknown tokens
and dollars by model/kind with no prompt content.

Accepted failure: an ambiguous provider call consumes its full reservation and can exhaust the $5
budget early. Cost is capped at $5 external plus the Stage 1 ≤$0.41 secret/call charge; no AI
component exceeds $10.

### R17. Typed, fail-closed configuration

Replace `DB_PATH`-only assumptions with typed settings for environment, deployment class
(`public_synthetic` or `private_operator`), database URL/SSL, Cognito issuer/client/audience, queue
and DLQ URLs, scheduler timezone/window, S3 bucket, KMS key reference, ESPN/FFC/nflverse hosts,
global rate, retention, AI budget/model prices and observability sampling.

Secrets are loaded by workload identity, not supplied in committed config or container command
lines. Validate at startup:

- public mode rejects any ESPN credential/KMS custody configuration or real-fixture manifest;
- private mode rejects Anthropic configuration;
- provider hosts are fixed allowlists, not user URLs; seasons and external IDs remain data;
- production refuses SQLite, absent TLS, missing Alembic revision or a runtime DB role with unsafe
  privileges; and
- `/health` reveals no filesystem path, secret state, tenant counts or provider details. Liveness is
  process-only; readiness verifies database/schema/required dependencies without calling ESPN.

The mode check is not the security boundary—the separate account/key policy is—but it prevents
accidental mixed configuration. Non-secret environment variables/Parameter Store standard values
have $0 incremental storage charge; secret pricing is listed above.

### R18. Budgeted observability that detects silent sync failure

Emit one-line structured JSON logs with `timestamp`, `level`, `environment`, `service`, `event`,
`request_id`, `trace_id`, `tenant_id`, `user_subject` (opaque), `job_id`, `league_id` (internal),
duration/outcome and a bounded error code. Never attach headers, cookies, SWIDs, request/response
bodies, raw payloads, prompts, AI output, team/league names or encryption context containing private
values. Tenant ID is required in logs but prohibited as a custom-metric dimension.

Ten low-cardinality custom metric series are the ceiling:

1. `sync_due_leagues`
2. `sync_clean_succeeded_leagues`
3. `sync_partial_or_failed_leagues`
4. `sync_oldest_success_age_seconds`
5. `espn_request_starts`
6. `espn_throttle_or_5xx_responses`
7. `provider_schema_missing_path_count`
8. `metrics_recompute_failure_count`
9. `ai_authorized_spend_microdollars`
10. `ai_budget_rejection_count`

Logs additionally record per-job provider latency/bytes/cache hit-miss, parsed/stored row counts,
idempotent duplicate count, retry reason, snapshot batches/retention deletes and KMS/cache errors.
Use AWS's standard no-extra-charge SQS queue depth/oldest-age/DLQ, RDS CPU/storage/connections and
load-balancer/compute metrics rather than republishing them.

Eight operator alarms are required: due-versus-clean-success gap after the nightly window; oldest
league success >26h; SQS oldest message >2h; DLQ visible >0; schema-missing path >0; recompute
failure >0; AI spend at 80%/100%; and latest-restorable-time lag >10m or restore drill overdue.
Notifications deduplicate into one operator email/topic; there is no on-call escalation fiction.

Instrument FastAPI, SQLAlchemy, SQS jobs, provider calls, KMS and Anthropic with OpenTelemetry trace
context. Export all error traces and a bounded sample of successful traces to X-Ray, stripping SQL
parameters and external headers/bodies. A request trace must separate connection wait, DB time,
serialized query count, provider wait and response serialization so a slow request is diagnosable.

Cost guardrail: ≤5 GB logs/month with 14-day retention, ≤10 custom metrics, ≤10 standard alarms and
≤100,000 recorded traces/month. AWS currently includes those quantities in the CloudWatch/X-Ray
free tier; expected cost is **$0/month**
([CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)). If any threshold is crossed,
sample/aggregate before buying observability. Application Signals, high-cardinality per-tenant
metrics, full SQL logging and paid third-party APM are out of scope.

### R19. Offline tests, synthetic provenance and PostgreSQL CI

The hosted test contract is network-off by default and synthetic-only:

- Synthetic Fixture Factory from Stage 1 A3 replaces every committed recorded-derived `real_*`
  input before hosted CI. Generated happy-path, missing-field, malformed, auth-error, 429 and retry
  fixtures cover each provider view without ever calling ESPN.
- Parser truth tests remain pure. Sync tests run the real service against a deterministic fake
  provider, fake distributed clock/rate permit, S3 fake and SQS/job state machine. Retry tests assert
  call counts and redacted errors; idempotency tests replay after each transaction boundary.
- Keep the fast SQLite unit lane, but add a PostgreSQL service-container lane that runs Alembic from
  empty, upgrade/import checks, all data-service tests, target `TIMESTAMPTZ` behavior and the R4
  adversarial RLS suite using the actual runtime role. No hosted release may rely only on SQLite.
- Add DDL assertions for the four PostgreSQL partial-index predicates, all RLS/FORCE flags, runtime
  role attributes and composite tenant FKs. Add query-count and memory regression tests for R9.
- Local live smoke remains explicit, operator-run and private, with the §11 no-login/no-write/no-HTML
  guardrails. It is never a CI job, scheduled check or source of committed fixtures.

The PostgreSQL lane adds **$0 AWS/month** and an as-yet unmeasured number of GitHub Actions minutes.
Stage 0b correctly refused to invent that number because no driver/migration/lane exists. After
implementation, record ten cold and ten warm runs and report incremental billed minutes; if the repo
is private, compare them with the owner's included-minute allowance. A cost estimate before that is
not evidence.

### R20. Concrete source-surface ownership

This is the minimum backend retrofit implied by the requirements; it is not a file-by-file
implementation plan yet.

| Surface | Required change |
| --- | --- |
| `api/models.py` | Tenant/auth/job/audit/budget models; direct `tenant_id`; composite keys/FKs; split shared player identity from tenant observations; remove persisted owner identifiers; aware datetimes and reviewed JSONB. |
| `api/db.py` | `DATABASE_URL`, dialect-safe engine, bounded pool, scoped transactions, RLS context; no production `create_all`, PRAGMA or additive DDL. |
| `api/config.py` | Typed public/private/cloud settings and fail-closed validation; no production secret values or SQLite path assumptions. |
| `api/crypto.py` | Vault interface and private KMS envelope implementation; public account has no decrypt implementation/authority. |
| `api/main.py` and new auth/tenant dependencies | Cognito access-token verification, secure session/CSRF boundary, tenant membership/action policy, sanitized errors and trace/request context. |
| All routers | Require authenticated/scoped unit of work; object-not-found behavior; ownership on reads/mutations; asynchronous job responses; credential routes absent publicly; safe export/AI controls. |
| `api/services/sync.py` | Durable account/league run IDs, transactional idempotency/checkpoints, tenant scope, no duplicate snapshots, job result codes and grouped private credential use. |
| `api/services/espn.py` | Distributed global permit/cooldown, request telemetry, S3 raw cache adapter, no instance-local rate claim; guardrails unchanged. |
| `api/services/portfolio.py` and analytics readers | Set-based/eager reads meeting the 25-query profiled-path budget; no tenant filters required for safety but explicit filters retained for plans/readability. |
| `api/services/exports.py` | Tenant-scoped streaming JSON/CSV and write-only XLSX with bounded cleanup and audit. |
| `api/services/ai.py` and AI routers | Synthetic-only hosted facts, tenant cache key, usage-return contract, atomic reservation/settlement, cached-only trip state and owner-only force. |
| New scheduler/job worker/storage/observability modules | EventBridge/SQS contract, outbox relay, DLQ/replay, distributed fairness/limiter, S3 adapter, retention jobs and low-cardinality telemetry. |
| `alembic/` and CI | Reviewed baseline/upgrades, single runner, schema/RLS assertions, PostgreSQL service lane and synthetic provenance gate. |

Every one of the 122 tenant-sensitive call sites and 50 data-returning routes from Stage 0 remains
in the migration inventory until it is either protected by the new unit of work/RLS or deleted. A
router middleware alone cannot close the inventory.

## Stage 2 control-cost subtotal

This is not the Stage 3 infrastructure bill; compute, PostgreSQL, network, frontend/CDN and base
storage have not been selected. It prices every control selected in this stage so none disappears
inside those ADRs.

| Selected control | Public synthetic | Optional private AWS delta | Basis |
| --- | ---: | ---: | --- |
| Cognito Lite at 1–50 MAU | $0.00 | $0.00 if private uses same class in its own account | Below 10,000 MAU free allowance. |
| EventBridge Scheduler | $0.00 | $0.00 | Tens of monthly invocations vs 14M free. |
| SQS Standard + fair queues + DLQ | $0.00 | $0.00 | Expected operations far below 1M requests. |
| PostgreSQL RLS + Alembic | $0.00 feature charge | $0.00 feature charge | Included in database/software; instance priced Stage 3. |
| Credential KMS key/requests | $0.00 | $1.00 | One private key; expected symmetric calls below 20,000 free requests. |
| Anthropic secret/calls | ≤$0.41 | $0.00 | One $0.40 secret plus ≤1,000 calls. Private worker has no key. |
| Private raw-cache S3 | $0.00 | **≤$0.11** | Conservative uncompressed 0.71 GB retained plus 576 PUT/day at current S3 Standard rates; GETs negligible. |
| CloudWatch logs/metrics/alarms + X-Ray | $0.00 expected | $0.00 expected | Stay within explicit free-tier cardinality/volume/trace caps per account. |
| Anthropic API | **≤$5.00 external** | $0.00 | Hard ledger ceiling; not AWS spend. |
| **Subtotal before base infrastructure** | **≤$0.41 AWS + $5 external** | **≤$1.11 AWS** | **Combined ≤$1.52 AWS + $5 external.** |

Pricing inputs for the control subtotal were verified against
[KMS](https://aws.amazon.com/kms/pricing/),
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/) and
[S3](https://aws.amazon.com/s3/pricing/) list pricing in addition to the service links above.

No selected Stage 2 control exceeds $10/month. The rejected 99.9% ALB would exceed $10 before its
LCU and is not justified for a portfolio with a 99% target.

## What a staff engineer would ask about this

1. **“What stops one forgotten query from returning another tenant's data?”** The runtime role sees
   forced-RLS tables through transaction-local context, is not owner/`BYPASSRLS`, and cross-tenant
   references fail composite FKs. The proof is direct SQL plus HTTP attack tests with colliding IDs,
   including absent context and reused pooled connections. A migration/admin role remains a trusted
   exception and is kept out of workloads.
2. **“Can 115 leagues actually finish in the nightly window at one request per second?”** The
   current code-shape lower bound is 9.6 minutes at week 0 and 44.1 minutes at 18 completed weeks,
   before retries, so a two-hour window has margin but no live latency/error evidence. Queue-age,
   due/success and 429 metrics decide whether cadence or retained views must change; worker scaling
   cannot improve the external 1 rps bound.
3. **“How can RPO be five minutes when availability is only 99%?”** RPO limits committed data loss;
   it does not make a standby available. Continuous logs can preserve a five-minute recovery point
   while a Single-AZ failure still takes hours to restore. The accepted trade is cheap durability
   plus a four-operator-hour recovery exercise instead of paying continuously for a warm standby.

**Whiteboard cold for a senior interview:** pool/silo/bridge tenancy; OIDC authentication versus
OAuth scopes versus application authorization; PostgreSQL RLS owner/`BYPASSRLS` traps; composite
tenant FKs; at-least-once delivery and idempotency; outbox failure windows; global token bucket plus
fair scheduling; RPO versus RTO versus availability; retention as a capacity control; envelope
encryption's runtime boundary.

**Implementation detail to look up:** Cognito hosted-login parameters/JWK library calls, exact
Alembic operation syntax, PostgreSQL policy DDL, SQS visibility APIs, EventBridge expressions, KMS
policy JSON, OpenTelemetry exporters and S3 lifecycle/IAM syntax.

**GATE: Stage 2 complete. Stop before architecture and ADRs.**

## Amendments

These amendments move premature product choices back across the requirements/architecture boundary.
They use 730 hours/month and current `us-east-1` list prices checked on 2026-08-09. Credits and the
temporary T4g free trial are excluded from recurring cost.

### B1. Networking is a priced architecture constraint

The concern is correct, but “three endpoints replace NAT” is not a complete runnable topology.
EventBridge Scheduler calls its target from the AWS service plane; the application does not call
Scheduler at runtime. More importantly, a Cognito interface endpoint cannot serve a user-pool
domain, hosted UI or OAuth/OIDC authorization-code flow. AWS documents user pools with assigned
domains as incompatible with private transit for those endpoints. Anthropic is also a public
internet dependency. A fully private workload therefore still needs public egress unless AI and
browser-domain/OIDC access are removed
([Cognito PrivateLink limitations](https://docs.aws.amazon.com/cognito/latest/developerguide/vpc-interface-endpoints.html)).

Current unit rates are $0.045/NAT-gateway-hour plus $0.045/GB processed, $0.005/public-IPv4-hour,
and $0.01/interface-endpoint-hour/AZ plus $0.01/GB for the first PB
([VPC pricing](https://aws.amazon.com/vpc/pricing/),
[PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/)). S3 gateway endpoints have no
hourly or processing charge
([S3 gateway endpoint pricing](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-s3.html)).

| Egress path | Fixed monthly cost | Variable processing | What it really provides | Disposition entering Stage 3 |
| --- | ---: | ---: | --- | --- |
| One NAT gateway in one AZ | `$0.045 × 730 + $0.005 × 730` = **$36.50** including its public IPv4 | **$0.045/GB** through NAT, plus applicable internet transfer | Private-subnet workloads can reach AWS public APIs and Anthropic. It is a single-AZ dependency unless duplicated. | **Re-open.** Plausible but consumes more than the selected RDS instance. |
| NAT gateways in two AZs | **$73.00** including two public IPv4 addresses | **$0.045/GB**, plus applicable transfer | Removes the cross-AZ NAT dependency only when each workload routes to its local NAT. | Reject at this availability/budget unless the target changes. |
| Exactly three interface endpoints, one AZ | `3 × $0.01 × 730` = **$21.90** | **$0.01/GB** | Could privately expose three services such as SQS, Secrets Manager and KMS. It does not provide Cognito-domain or Anthropic egress. | Not a complete application network. |
| Exactly three interface endpoints, two AZs | **$43.80** | **$0.01/GB** | Same service limitation with an endpoint ENI per AZ. | Not a complete application network. |
| Container-minimum endpoints, one AZ | At least SQS, Secrets Manager, ECR API, ECR Docker and CloudWatch Logs: `5 × $0.01 × 730` = **$36.50**; private custody adds KMS for **$43.80** | **$0.01/GB** | Allows private image pull/log/selected AWS APIs with a free S3 gateway endpoint. Still lacks Anthropic/Cognito-domain egress. | Reject as an endpoint-only answer; compare only as an addition to other egress. |
| Same endpoint set, two AZs | **$73.00 public / $87.60 private-custody** | **$0.01/GB** | Removes an endpoint-AZ dependency but still is not an internet path. | Reject for the portfolio target. |
| Compute in a public subnet, one public IPv4, no NAT/interface endpoints | **$3.65** for the address; internet gateway $0; S3 gateway endpoint $0 | First 100 GB/month aggregate regional internet egress is currently free; normal service request charges still apply | Direct TLS egress to AWS public APIs and approved external hosts. A security group can close inbound ports but cannot restrict outbound by DNS name. | **Preferred hypothesis for ADR-001.** Must restrict inbound to the edge origin, allow only required outbound ports, use IAM least privilege and accept the public-IP/kernel exposure. |

“Public subnet” does not mean “open security group.” The viable cheap shape has no SSH ingress,
only TLS ingress from the selected CDN origin set/security group, SSM for administration, a private
RDS subnet, one egress public IP and no NAT. The accepted weakness is that security groups cannot
express the external hostname allowlist; host-level policy/telemetry and application host allowlists
must catch SSRF/egress mistakes. Stage 3 must decide this before compute.

The SQS decision is also reopened. The required property is: **durable at-least-once asynchronous
work, idempotent leases/retries, poison handling, backpressure and tenant fairness**.

| Queue option | Service/network cost at measured scale | Operational burden | Architectural signal |
| --- | ---: | --- | --- |
| PostgreSQL `sync_jobs` queue (`FOR UPDATE SKIP LOCKED`, leases and terminal poison state) | **$0 incremental** on the required database; no SQS endpoint/API | One atomic store removes outbox-to-SQS ambiguity, but the team owns polling, lease expiry, fairness, cleanup and queue-table bloat. | Demonstrates durable state machines, locking, idempotency and backpressure without managed-queue theater. |
| SQS Standard/fair queue + DLQ + relational outbox | **$0 request charge** below 1M requests; public-egress topology adds no endpoint, private-subnet topology adds **$7.30/AZ/month** for SQS | AWS owns delivery/visibility/DLQ scale; the app owns outbox relay, duplicate reconciliation and state split across SQS/PostgreSQL. | Demonstrates a recognizable AWS async boundary and scales workers independently. |
| Direct in-process/background queue | **$0** | Lost on crash/redeploy; no durable backpressure or poison isolation. | Does not satisfy the requirement. |

SQS is therefore not confirmed. ADR-004 must choose between the first two using the selected compute
topology and actual concurrency, rather than treating a free request tier as zero total cost.

### B2. Service choices are returned to ADR ownership

Stage 2 should have specified properties, then allowed Stage 3 to select products. The inventory is:

| Stage 2 preselection | Status before Stage 3 | Requirement that survives |
| --- | --- | --- |
| Cognito Lite | **Confirmed after B4**, but documented in an identity ADR because the classic/managed-login distinction matters. | Managed OIDC authentication, PKCE, TOTP, no custom password service, app-owned tenant authorization. |
| PostgreSQL | **Confirmed** as the engine because forced RLS and the measured SQLite migration are requirements; RDS/Aurora/external hosting are **re-opened**. | PostgreSQL semantics, forced RLS, five-minute RPO, reviewed Alembic migrations. |
| EventBridge Scheduler | **Re-open.** | Durable schedule intent, missed-run recovery, one global cadence and an observable two-hour window. |
| SQS Standard/fair queue/DLQ | **Re-open per B1.** | Durable at-least-once jobs, fairness, retries, poison handling and backpressure. |
| S3 raw cache | **Re-open and conditional on private AWS mode.** | Raw payloads stay outside PostgreSQL/backups, expire within 24h and are unavailable to the hosted public app. |
| KMS envelope encryption | **Confirmed only for private-in-AWS custody.** | No public principal can decrypt real cookies; unattended private AWS custody, if deployed, uses a non-exportable policy boundary. |
| Secrets Manager | **Re-open for application/database secrets.** | No production secret in source, image, command line or ordinary environment configuration; workloads receive least-privilege secret access. |
| CloudWatch/X-Ray | **Re-open.** | Structured redacted logs, bounded low-cardinality metrics, traces and the named silent-sync alarms within budget. |
| OpenTelemetry | **Requirement-only.** | Vendor-neutral trace context at HTTP/database/job/provider boundaries. |
| S3 export staging | **Requirement-only.** | Streaming by default; encrypted ≤24h staging only for formats that require it. |

All reopened selections receive an ADR with at least three options, dollar delta and accepted
failure. Stage 3 may retain a product, but Stage 2 no longer counts as its decision record.

### B3. Public-only is the baseline; private AWS is an unselected option

Stage 3 must price and make the public synthetic deployment complete first. Private-in-AWS is a
separate-account option after that baseline, never a hidden second copy in the main total.

**Expected outcome (judgment call): deploy only the public synthetic stack.** Even if a duplicate
private stack fits under $150, it must justify duplicated operations and the residual provider-term
risk. Unless its ADR does so, the private KMS key, decrypt role, S3 raw-cache bucket and unattended
03:00 cloud sync remain designed/testable IaC modules but are not applied. Private real-data mode
stays local.

If private remains local, raw cache moves out of SQLite into compressed files under an
operator-configured cache directory, with a 24-hour TTL, a 1 GiB hard cap, cleanup on start/end and
exclusion from database backups, exports and fixture generation. Local full-disk encryption is the
at-rest boundary. Losing cache files is accepted. Stage 3 must still price the hypothetical private
AWS delta so the decision is visible.

### B4. Cognito Lite is sufficient, but the UI name was imprecise

AWS's current feature matrix confirms that Lite includes username/password sign-in, OAuth/OIDC,
the **classic hosted UI**, and MFA using authenticator-app TOTP. The newer product named **Managed
Login** with its visual branding editor requires Essentials
([feature-plan matrix](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html)).

R2 requires TOTP and hosted pages, not passkeys, email OTP, passwordless login, access-token claim
customization or the visual editor. Therefore select **Cognito Lite plus classic hosted UI** and
correct every ambiguous “managed login” reference. Essentials would also cost $0 for 1, 10 or 50
direct/social MAU because both Lite and Essentials share the indefinite 10,000-MAU free tier; above
that, current rates begin at $0.0055/MAU for Lite and $0.015/MAU for Essentials
([Cognito pricing](https://aws.amazon.com/cognito/pricing/)). The Auth0/Clerk conclusion does not
depend on this distinction at portfolio scale, but classic UI polish is an accepted product cost.

### B5. Identity loss is outside database PITR and needs reconciliation

Add this requirement: Terraform is authoritative for user-pool configuration, while Cognito is
authoritative for current authentication credentials/status and PostgreSQL is authoritative for
application user IDs, tenant membership and authorization. `users.id` must be stable and separate
from a replaceable `external_identities(provider, subject, user_id)` mapping; do not make Cognito
`sub` the application's primary key.

Once per day, export non-secret user attributes/status/subject plus pool/app-client configuration
metadata to an encrypted, versioned operations bucket with 35-day retention. This is an inventory,
not a credential backup: Cognito passwords and authenticator seeds are not exportable. On total pool
loss, Terraform recreates the pool, all users re-enroll and the operator re-links each new subject
to the stable application user only after verified email plus an operator recovery check. At 50
users, budget **up to two business days** for complete user access restoration; recreating the pool
and making the app available remains within the four operator-hour infrastructure RTO. This longer
identity recovery is an explicit accepted failure, not hidden inside the database RTO.

After a database PITR to T-1 while Cognito remains at T:

- a pool-only subject is unaffiliated and receives no tenant data until invited/reconciled;
- a DB-only identity mapping is dormant because no current principal can authenticate as it; and
- reconciliation never auto-deletes or auto-links by email alone. It emits a difference report and
requires an operator decision supported by the audit/user export.

Multi-Region Cognito replication is rejected at this availability target: it adds a replicated
identity/KMS configuration and does not replace application-level reconciliation after operator
error. Revisit if identity access must share the four-hour calendar RTO.

### B6. Frontend and origin/session requirements

The deployed frontend must satisfy the following before Stage 3 selects hosting:

- Build the SPA once with relative `/api` and `/auth` paths. Non-secret environment metadata comes
  from a small runtime `config.json` served `no-store`; a promoted artifact is not rebuilt merely to
  change `VITE_API_BASE`. Secrets never enter the Vite build.
- Present SPA, API and OAuth callback through one application origin. The session cookie is
  host-only `__Host-session; Secure; HttpOnly; Path=/; SameSite=Lax`; state-changing requests also
  require a CSRF token. Cognito's authorization endpoint is an intentional cross-site redirect,
  not an API CORS exception.
- This refines R2's “API accepts an access token” wording: the same-origin authentication gateway
  validates the Cognito access token during the callback and creates a bounded opaque application
  session. Ordinary business API routes authenticate that session, not a browser-supplied Cognito
  bearer token. The session cannot outlive the validated token and stores no provider token in the
  browser or database.
- If a future topology uses another API origin, CORS is an exact allowlist of the one SPA origin,
  credentials are explicit, preflight is tested, and wildcard origins are forbidden. The selected
  single-origin topology keeps application CORS disabled.
- Hashed JS/CSS assets use `Cache-Control: public,max-age=31536000,immutable`; `index.html`, service
  worker and runtime config use `no-cache`/`no-store` as appropriate. Deployment uploads assets
  before the new index and invalidates only entry documents. Rollback restores the prior index and
  retained hashed assets.
- Apply HSTS, `frame-ancestors 'none'`, `object-src 'none'`, restrictive `default-src`, explicit
  `connect-src`/`img-src`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and a
  restrictive Permissions Policy. Start CSP in report-only during migration, then enforce it; no
  third-party scripts are allowed without an ADR.
- Private object storage is the SPA origin; public bucket website hosting is forbidden. CDN access
  uses origin access control. Frontend storage, requests, CDN, DNS, certificates, invalidations and
  domain registration each receive a Stage 3 cost line.

### Test-scope correction

R9 query-count, latency, explain-plan and memory regressions run against PostgreSQL using the same
non-owner, non-`BYPASSRLS`, forced-RLS runtime role and transaction-local tenant context as the
deployed web process. Test data contains two tenants with colliding IDs. A bare-schema result is
only a developer diagnostic and cannot satisfy the ≤25-query or ≤256 MiB release gates.

Because ADR-004 may replace SQS, R18's alarm semantics are product-neutral: “SQS oldest message” is
`oldest eligible job age`, and “DLQ visible” is `terminal poison/dead job count`. The selected queue
adapter supplies them without republishing high-cardinality job labels.

**Amended Stage 2 gate satisfied. Stage 3 may proceed, networking ADR first and public-only first.**
