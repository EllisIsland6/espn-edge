# Phase 30 offline evidence ledger

Candidate status: targeted credential-authority isolation fix, candidate 15. This ledger separates
prior live observations from this writer's offline fix evidence. The Candidate 15 offline fix is
not new proof of the operator's device, off-device media, FileVault
state, Keychain credential, break-glass copy, successful hourly launchd execution, real retention
application, or real restore drill.

The frozen contract's implementation condition was adjudicated by Agent 1: independent review and
operator acceptance of SHA-256
`23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5` were recorded externally,
so the conditional draft wording did not prohibit this leased implementation.

## E30.1 — Recovery correctness, independent oracle and application closure

- Classification: **measured**, offline deterministic synthetic SQLite databases and fake
  repositories; no private database, credential, provider, network, or paid service.
- Environment captured by `date -u`, `sw_vers`, `uname -m`, and runtime version commands at
  `2026-08-13T04:06:23Z`: macOS 26.5 build 25F71, arm64, Python 3.14.3, Node 25.6.1 and npm 11.9.0;
  repository base HEAD `7f81c76e24fd50635071f8b53f05a4aeb11881de`.
- Command: `.venv/bin/python -m pytest tests/test_recovery.py tests/test_recovery_api.py
  tests/test_recovery_oracle.py tests/test_recovery_integration.py tests/test_hardening.py -q`
- Result on Candidate 5: **92 passed** in **12.41 seconds** pytest time / **12.95 seconds** measured
  wall time. The independent oracle discovers the synthetic SQLite catalog without importing
  production serializer/catalog/hash helpers and compares **17 retained tables / 183
  retained columns** source→bundle→restored, including storage types, nullable cases, both boolean
  values, IEEE-754 floats, JSON, exact datetime text and UTF-8 text. The operational drill invokes
  its own pinned child-process application verifier against source and restored scratch databases,
  compares the four profiled HTTP responses, proves discovery/router/verify/direct-sync require
  reauthentication with zero provider calls, proves a synthetic reauthentication permits a fake
  provider sync, and checks all four retained families. Six subprocess tests send `SIGKILL` at each
  actual drill stage and prove the next run scavenges the bounded marked scratch root and completes
  the operational verifier. Candidate 5 additionally proves the general runner installs/loads only
  the hourly backup; standalone `--apply` cannot enter the CLI; only the exact
  `retention --enable --apply` transition can install, load and atomically arm recurrence; load or
  state-arm failure rolls the installed inode back; and an unarmed scheduled invocation cannot
  prune. Descriptor-relative scratch tests replace the pathname after validation and insert a
  synthetic cross-device child after validation, proving the replacement survives and cleanup
  fails closed. This measures synthetic closure, not a live backup or RPO.

## E30.2 — Full backend suite

- Classification: **measured**, same offline environment.
- Command: `/usr/bin/time -p .venv/bin/python -m pytest -q`
- Result on the final Candidate 5 files: **318 passed** in **18.49 seconds** pytest time; measured
  wall time **19.07 seconds**, user **14.10 seconds**, system **3.62 seconds**. These timings
  characterize this one host/run only and
  are not an RTO.

## E30.3 — Static gates and frontend compilation

- Classification: **measured**, offline local toolchain.
- Commands: `.venv/bin/ruff check .`; `npm run lint`; and `npm run build` in `web/`.
- Result: repository Python lint **passed** after deterministic import sorting in two Phase 30 test
  files; TypeScript lint **passed**; production frontend build **passed** (701 modules transformed,
  measured build time **1.38 seconds**). Build bundle sizes are
  build artifacts, not a runtime-memory forecast.

## E30.4 — Browser acceptance and pre-existing disposition

- Classification: **measured**, local Chromium and deterministic route mocks; no backend/provider.
- Commands: `npm run e2e -- --grep "private recovery|recovery status failure"`; then
  `/usr/bin/time -p npm run e2e`; then the failed test alone via
  `npm run e2e -- --grep "team logo failure keeps"`.
- Result: Phase 30 recovery tests **2/2 passed** within the full run. Full suite **55/56 passed** in
  **11.8 seconds** Playwright time / **12.28 seconds** measured wall time.
  The sole failure reproduced alone and is outside Phase 30: the pre-existing dirty
  `web/src/components/TeamIdentity.tsx` renders a failed logo as `opacity-0`/`display:block`, while
  the pre-existing test expects `display:none`. Phase 30 did not edit that component or assertion;
  changing it would violate the scoped lease. This is a measured pre-existing disposition, not a
  green-suite claim.

## E30.5 — Native-macOS topology resolver compatibility

- Classification: **measured offline compatibility evidence**. Candidate 8's path-named
  `diskutil` subject and bounded-reader-thread claims are withdrawn: a swap-query-restore race could
  substitute the queried path, and an inherited pipe could keep a reader join alive after timeout.
  This replacement result did not query a real database path, external volume, repository,
  credential, or provider.
- UTC time and environment: `2026-08-16T19:14:55Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3,
  captured by `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version`.
- Dataset: pytest temporary files/directories, held-descriptor/device-subject fakes, synthetic plist
  field shapes, a deterministic fake `diskutil` runner, and local child processes with synthetic
  output sentinels. All physical-store and volume names are synthetic; no environment-specific
  device identifier or private path is committed.
- Method: `/usr/bin/time -p .venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_api.py tests/test_recovery_oracle.py tests/test_recovery_integration.py
  tests/test_hardening.py`; `/usr/bin/time -p .venv/bin/python -m pytest`;
  `.venv/bin/python -m pytest --collect-only tests/test_recovery.py | rg
  '::test_(topology_resolver|repository_rejects_same|repository_rejects_any|bounded_runner|macos_device_subject)'
  | wc -l`;
  `.venv/bin/ruff check .`; `git diff --check`; and `git diff --no-index --check /dev/null <path>`
  for each of the three untracked leased paths. One measured run of each; no warm-up.
- Result: the scoped recovery gate passed **133 tests** in **16.94 seconds** pytest time and
  **17.46 seconds wall time**. The full backend passed **359 tests** in **23.33 seconds** pytest time /
  **24.05 seconds wall time**; Ruff and whitespace/diff checks passed. The measured collection
  command reports **41 deterministic topology/process cases**. They now include native
  `devname(fstat(fd).st_dev, S_IFBLK)` call arguments and injected NULL handling; strict
  device-subject validation;
  exact returned-device cross-checking; a swap-query-restore ABA reproducer; and timeout, overflow,
  and independently bounded drain cases where descendants retain both output pipes. All descendant
  sentinels remained absent from exceptions, safe error envelopes, stdout and stderr, and each
  regression completed in under its two-second assertion. The prior descriptor walks, APFS-set
  normalization/overlap, local-media classification, path-free errors and object-token cases remain
  covered.
- Validity domain: proves fixed absolute/no-shell argv, minimal environment, descriptor-derived
  block-device subjects, exact response binding, bounded selector-driven stdin/stdout/stderr,
  isolated noninteractive process-group termination, and independent drain/cleanup deadlines under
  deterministic offline filesystem, plist and child-process attacks. It does not prove the
  operator's actual volume now passes or authorize initialization.
- Provider/data-boundary result: **pass for the candidate diff**. The code handles local filesystem
  metadata only; tests and evidence contain no ESPN/AI call, credential, payload, member identifier,
  private row, real path, or device identifier.
- Future method: after independent QA and Cybersecurity approve the frozen candidate, rerun only the
  secret-free `python -m api.recovery doctor --json` against the already approved local settings.
  The active contract's live compatibility threshold remains unmeasured by this writer until that
  separately authorized check reports a qualifying physically distinct target.

## E30.6 — Launchd interpreter identity

- Classification: **measured live failure by Agent 1, incorporated from a read-only orchestration
  record; measured offline fix verification by this writer**. The live source is
  `docs/agent-handoffs/ORCHESTRATION_STATE.md` at SHA-256
  `5dd866067c1f962464ae7d5c776826c6260aaeb1791318ac27211f4b7dbfa666`; this writer did not rerun
  launchctl or inspect the installed plist, repository, Keychain, device, private database or state.
- Live result carried forward: Candidate 9 installed/loaded the approved hourly agent in **0.33
  seconds**, but its immediate `RunAtLoad` invocation exited **1**. The generated program had
  resolved the venv entry point to the base framework interpreter: the secret-free probe measured
  **3 required packages missing** there and **0 missing** through the lexical venv entry point.
  Agent 1 booted out the failed service and recorded the existing manual recovery point as healthy.
  This is evidence of the Candidate 9 defect, not evidence that Candidate 10 runs under launchd.
- Offline UTC time and environment: `2026-08-16T19:39:43Z`; macOS 26.5 build 25F71, arm64, Python
  3.14.3, captured by `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version`.
- Offline method: `/usr/bin/time -p .venv/bin/python -m pytest tests/test_recovery.py -k 'launchd
  or runner_install or recurring_plist or retention_runner'`; `/usr/bin/time -p .venv/bin/python -m
  pytest tests/test_recovery.py tests/test_recovery_api.py tests/test_recovery_oracle.py
  tests/test_recovery_integration.py tests/test_hardening.py`; `/usr/bin/time -p .venv/bin/python -m
  pytest`; `.venv/bin/ruff check .`; `git diff --check`; and no-index whitespace checks for the three
  leased untracked paths. One measured run of each; no warm-up.
- Offline result: the targeted launchd/approval-boundary set passed **10 tests** in **0.94 seconds**
  pytest time / **1.40 seconds wall time**. The scoped recovery gate passed **139 tests** in **17.63
  seconds** / **18.17 seconds wall time**. The full backend passed **365 tests** in **24.42 seconds** /
  **25.15 seconds wall time**. Both hourly and retention plist tests independently parse the XML,
  require the exact absolute lexical `ROOT/.venv/bin/python`, reject the resolved base path, and
  retain the existing working directory, minimal environment and command separation. Each generated
  interpreter completed a bounded offline import of `api.recovery`, `fastapi`,
  `pydantic_settings`, and `sqlalchemy` with exit **0** and empty stdout/stderr. Missing and
  non-executable lexical runtimes returned the stable path-free `recovery_tool_invalid` envelope.
- Validity domain: proves plist generation and dependency availability through the generated
  lexical interpreter on this checkout and host. It does not prove launchd environment behavior,
  overwrite the retained failed plist, reload the agent, run a backup, or authorize retention.
- Provider/data-boundary result: **pass for the Candidate 10 diff**. The change and tests touch only
  local interpreter metadata and synthetic subprocess imports; no credential, provider, private
  payload, member identifier, repository content or secret is read or emitted.
- Future method: after Candidate 10's immutable hash passes independent QA and Cybersecurity review,
  Agent 1 may perform the separately authorized hourly-only plist overwrite/reload and verify its
  immediate result. Until that bounded retry succeeds, launchd execution remains unmeasured for
  Candidate 10.

## E30.7 — Bounded removable-volume path opening

- Classification: **measured live failure by Agent 1, incorporated from a read-only orchestration
  record; measured offline deterministic fix verification by this writer**. The live source is
  `docs/agent-handoffs/ORCHESTRATION_STATE.md` at SHA-256
  `1eb59e9fe369301ff172c8ed668c0e7e74da93ebc5431be3ca26c6b318afbf84`. This writer did not query
  the external volume, database, repository, Keychain, launchd, state file, provider or network.
- Live result carried forward: after the Candidate 10 interpreter retry itself returned
  `recovery_ok`, the immediate hourly process remained blocked in storage-topology path opening for
  more than **100 seconds** while holding the recovery lock. It had not reached the source database,
  Keychain or Restic. Agent 1 terminated the job. This measures the Candidate 10 failure signature;
  it does not prove Candidate 11 against the sick media.
- Offline UTC time and environment: `2026-08-16T20:45:03Z`; macOS 26.5 build 25F71, arm64, Python
  3.14.3, captured by `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version` at Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`.
- Dataset and method: pytest temporary regular files, directories and FIFOs; synthetic recovery
  state; fake `diskutil` results; isolated local child processes; and valid local descriptors in
  malformed, truncated and wrong-count ancillary responses. No real path, device identifier,
  repository value, credential, row or provider payload appears in the test data. Commands were:
  `.venv/bin/ruff check api/services/recovery.py tests/test_recovery.py` plus the six critical
  blocked-open/descriptor cases; `/usr/bin/time -p .venv/bin/pytest -q tests/test_recovery.py -k
  'topology or path_open or bounded_subprocess or blocked_path_open'`; three fresh same-order scoped
  recovery processes; `.venv/bin/pytest --collect-only -q tests/test_recovery.py
  tests/test_recovery_api.py tests/test_recovery_oracle.py tests/test_recovery_integration.py`;
  `/usr/bin/time -p .venv/bin/pytest -q`; `.venv/bin/pytest --collect-only -q`; and
  `.venv/bin/ruff check api tests`.
- Result: the **6** contract-critical cases passed in **1.65 seconds wall time** with scoped Ruff
  green; **38** topology/path-open/process cases passed in **9.89 seconds wall time**. The final
  scoped recovery set contains **136 tests** and passed in **43.34 seconds** wall time. Before the
  final ancillary-control hardening added one test, three fresh same-order scoped processes of
  **135 tests** passed at **35.94**, **37.49** and **36.30 seconds** wall time. The final full backend
  contains **371 tests** and passed in **79.99 seconds** wall time (user **56.57**, system **13.74**
  seconds); repository Python Ruff passed in **0.04 seconds** wall time.
- Safety result: the topology resolver now delegates only symlink-safe component opening to an
  isolated child, passes the absolute path over a private datagram socket rather than argv or
  output, receives the complete descriptor chain through `SCM_RIGHTS`, and bounds readiness,
  opening, kill and wait in the parent. The child is created with `close_fds=True` and a pass list
  containing only its socket; the test observes the active recovery-lock descriptor plus explicitly
  inheritable synthetic source/repository descriptors and proves none enter that pass list. A FIFO
  blocks the child in the actual `os.open`; the parent returns the stable path-free
  `recovery_target_unavailable` envelope in under the test's two-second bound, prior coverage and
  snapshot timestamps/artifact size remain unchanged, the lock is immediately reacquired, and a
  fresh helper resolves a healthy synthetic target. Malformed payload, truncated ancillary data and
  wrong descriptor count are rejected and every received descriptor is proven closed.
- Transient-test disposition: the first complete scoped run failed the existing
  `test_offline_restore_starts_app_matches_reads_and_enforces_reauth` comparison at response index 3,
  the opportunity-charts read. That test injects a repository whose `validate_topology()` returns
  static identities, so it never invokes Candidate 11's resolver/helper. It passed immediately in
  isolation (**5.17 seconds**) and then in each of the three fresh same-order processes above; the
  full backend also passed. This is recorded as intermittent application-read nondeterminism, not as
  a Candidate 11 fix or a permanently closed flake.
- Validity domain: the FIFO is a deterministic proxy for an interruptible blocking filesystem open;
  it proves that the parent and recovery lock are not hostage to that child syscall and that a killed
  helper cannot retain the lock/source/repository descriptors. It does not prove how quickly the
  kernel will reap a process stuck in an uninterruptible device wait; progress remains safe because
  the helper has no inherited recovery authority. Live removable-volume retry remains unmeasured.
- Provider/data-boundary result: **pass for the Candidate 11 diff**. The change observes local
  filesystem metadata only. No ESPN, Anthropic, AWS, credential, private database, external
  repository, member identifier, launchd or network action was performed.
- Future method: after Candidate 11 passes independent QA and Cybersecurity review, execute one
  separately authorized hourly-only retry against the already approved repaired target; measure
  total path-open time, lock release, result code and timestamp behavior without printing paths,
  device identifiers, credentials or row data.

## E30.8 — Repository authority closure and stable restore-read projection

- Classification: **measured offline deterministic evidence**, derived from synthetic files,
  FIFOs, SQLite databases, AF_UNIX descriptor transfer and fake repository/provider boundaries. No
  external volume, live repository, Keychain, launchd state, private database, credential, provider,
  network or paid service was accessed.
- UTC time and environment: `2026-08-16T21:18:17Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3,
  captured by `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 11 manifest SHA-256
  `102b6ba238422e29a2019db572d8ecb3e55e701dce3ad08ebccadc1775c47a2b`.
- Repository-path method: the tests replace a synthetic repository directory with a FIFO exactly at
  production `ResticRepository` authority calls **1**, **2** and **3**, respectively exercising
  backup topology/directory precheck, retention pin, and retention `_assert_pin` revalidation. The
  helper performs the real no-follow component open and blocks in `os.open`; the parent deadline
  kills it. The static audit command was `rg -n
  'self\.repository\.(exists|is_dir|stat|lstat)|os\.(open|stat|lstat)\(self\.repository'
  api/services/recovery.py`, which returned no direct repository-path syscall. Lexical construction
  and Restic `/dev/fd` argv remain and are not pathname prechecks.
- Descriptor-message method: a real AF_UNIX datagram socketpair sends an overlength payload and one
  `SCM_RIGHTS` descriptor into the one-byte receiver. Fake responses independently exercise
  `MSG_TRUNC`, `MSG_CTRUNC`, malformed payload, wrong descriptor count and unexpected control data.
  Command: `/usr/bin/time -p .venv/bin/pytest -q tests/test_recovery.py -k
  'production_repository_path_authority or path_open_session_closes_all_fds_from_invalid_response
  or path_open_session_real_socket_rejects_overlength_payload'`.
- Repository/message result: **9 tests passed** in **2.40 seconds wall time**. Every repository-stage
  failure returned the path-free `recovery_target_unavailable` envelope in under its two-second
  assertion; coverage timestamp, snapshot timestamp and artifact size remained exact; Restic had
  zero calls; every helper was closed; and the recovery lock was reacquired immediately. Every
  unexpected `recvmsg` flag was rejected and each received descriptor was proven closed while the
  sender's original descriptor remained valid.
- Restore-projection method: `api.services.opportunity.opportunity_status` computes
  `source.age_hours` from `datetime.now(UTC)`, rounds it to one decimal and derives `source.stale`
  from the TTL. Sequential source/restored reads can therefore straddle a rounding or TTL boundary
  despite identical persisted facts. Candidate 12's operational verifier removes only those two
  derived fields from the fourth profiled read before digest comparison. It requires and retains
  `source.fetched_at`; all other three responses and every other opportunity-chart field remain
  byte-canonical digest inputs. Tests cover a rounding transition, stale false→true at the TTL,
  missing `fetched_at`, a nonvolatile field change, and a real restored SQLite mutation of the
  persisted `OpportunityImport.completed_at` value.
- Restore-projection result: `/usr/bin/time -p .venv/bin/pytest -q
  tests/test_recovery_integration.py -k 'restore_read_projection'` passed **5 tests** in **1.27
  seconds wall time**. The previously intermittent operational restore test passed independently in
  **6.38 seconds**, then again inside both final gates. The earlier Candidate 11 mismatch at response
  index 3 is now explained by the two clock-derived fields; this projection is the fix, while no
  opportunity endpoint or production clock behavior changed.
- Final gates: `/usr/bin/time -p .venv/bin/pytest -q tests/test_recovery.py
  tests/test_recovery_api.py tests/test_recovery_oracle.py tests/test_recovery_integration.py`
  passed **146 tests** in **45.72 seconds wall time**. `/usr/bin/time -p .venv/bin/pytest -q` passed
  **381 tests** in **80.46 seconds wall time** (user **57.34**, system **13.22** seconds).
  `.venv/bin/ruff check api tests` passed in **0.04 seconds**. Counts were measured with the
  corresponding `pytest --collect-only -q` commands rather than inferred from progress output.
- Validity domain: proves bounded and path-free behavior for interruptible blocked opens, complete
  descriptor closure for real payload truncation and deterministic ancillary attacks, and stable
  restore equality across clock-derived boundary changes while detecting persisted-data drift. A
  process in an uninterruptible kernel device wait may remain until the kernel releases it, but it
  inherits no recovery lock, source, repository or credential authority. Candidate 12 remains
  unmeasured against the operator's repaired removable media.
- Provider/data-boundary and cost result: **pass; $0/month delta**. Shared recovery code handles only
  local metadata and synthetic recovery data in this candidate. No ESPN, Anthropic or AWS path,
  public fixture, hosted data, credential, private payload, member identifier, telemetry or spend
  changed.
- Future method: after independent QA and Cybersecurity review the immutable Candidate 12 hash,
  perform one separately authorized hourly-only retry. Record bounded repository precheck/pin
  timing, lock release, safe result code and unchanged-or-advanced recovery timestamps without
  exposing paths, identifiers, credentials or contents.

## E30.9 — End-to-end pinned repository and orphan-safe admission

- Classification: **measured offline deterministic evidence** for the synthetic tests and gate
  timings; **measured live observations carried forward** only for the already recorded 19.67-second
  manual backup and 1.17-second full repository check; **policy assumptions** for every newly
  introduced Restic deadline. No deadline is presented as a forecast of future duration, and the
  destructive-retention ceiling remains unrehearsed.
- UTC time and environment: `2026-08-16T22:23:54Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3,
  captured with `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 12 manifest SHA-256
  `ee82f1ab1369246685160923d0b560e14c6f1f3b449c837eb20f77469540c74b`.
- Dataset: fresh synthetic format-v1 SQLite databases; temporary local directories; fake Restic
  responses; AF_UNIX datagrams with synthetic metadata and descriptors; and isolated child
  processes. No external volume, live repository, private database, credential, Keychain,
  launchd state, provider, network, or paid service was read or changed.
- Whole-transaction method: `/usr/bin/time -p .venv/bin/pytest -q tests/test_recovery.py -k
  'backup_repository_calls_are_pinned or every_restic_subprocess_call or
  backup_rejects_persistent_repository_replacement or backup_aba_swap_restore or
  escaped_restic_descendant or path_open_session or parent_path_authority_consumes'`.
  Result: **28 tests passed** in **43.64 seconds wall time** (user 29.92, system 6.18). The backup
  adapter made seven repository calls: repository identity, initial dump, empty-inventory check,
  backup, post-backup inventory, verification dump, and integrity check. Each received the same
  open repository pin plus recovery-lock descriptor, used only `/dev/fd/<pin>` as its repository
  argument, and had a positive operation-specific deadline. Seven persistent replacement attacks
  failed closed with prior state intact. Seven true swap-query-restore ABA attacks independently
  replaced the lexical path during each call; the passed repository descriptor retained the
  original inode, the replacement was never the command target, and the verified backup completed.
- Orphan-admission result: a real synthetic Restic process forked a new-session descendant, held
  the two inherited authority descriptors, and left its parent in a 60-second hang. The configured
  dump policy deadline was 0.10 seconds. The parent returned within the test's six-second
  end-to-end bound (which also includes helper/topology work), leaving coverage, snapshot and
  artifact state unchanged. Direct lock acquisition, backup, retention and API reservation then
  all returned `recovery_busy`. After the escaped descendant's 1.2-second lifetime, the same lock
  was reusable and a full retry created, re-read and checked the synthetic snapshot. The complete
  regression, including wait and retry, measured **5.34 seconds** with
  `pytest --durations=1`. This proves the intended flock lifecycle for this process model; it is not
  a measurement of a future real Restic hang.
- Metadata-boundary result: the killable path worker now emits an exact, bounded magic/count frame
  with device, inode and mode for every no-follow-opened component alongside `SCM_RIGHTS`. Parent
  code consumes that fixed metadata for path authority, mount-boundary discovery, block-device
  subject selection, topology revalidation and repository-pin comparison; a test makes parent
  `fstat` fail and still resolves the authority. Wrong magic/count/mode, short and overlength
  payloads, wrong descriptor count, `MSG_TRUNC`, `MSG_CTRUNC`, unexpected control data and every
  nonzero receive flag fail closed, and all received descriptors are proven closed. The path helper
  still inherits only its private socket, never the recovery lock or repository pin.
- Deadline classification: the 10/30/60/120/300/900-second values in the implementation are policy
  ceilings chosen to bound failure, not measured capacity. Only the previously approved live
  manual backup (**19.67 seconds**) and full read-data check (**1.17 seconds**) are measured live
  Restic durations. The 900-second destructive-retention ceiling is explicitly an assumption until
  a separately approved dry rehearsal measures it.
- Final gates: collection measured **177 scoped tests** and **403 full backend tests**. The scoped
  recovery/oracle/integration/hardening gate passed **177/177** in **72.27 seconds wall time** (user
  52.58, system 10.02). `/usr/bin/time -p .venv/bin/pytest -q` passed **403/403** in **122.32 seconds
  wall time** (user 85.68, system 19.38). The only output was the existing Starlette/httpx
  deprecation warning. `.venv/bin/ruff check .` and `git diff --check` passed. Because the Phase 30
  leased files are untracked against the old Git base, `git diff --no-index --check /dev/null
  <path>` was also run for each of the three changed leased paths; each produced no whitespace-error
  output and the expected no-index difference exit 1. The Candidate 12 stable restore-read
  projection and `api/recovery.py` were unchanged.
- Provider/data-boundary and cost result: **pass; $0/month delta**. The change handles only local
  recovery authority and synthetic offline evidence. It adds no schema, endpoint, configuration,
  provider behavior, hosted/private-data path, resource, or variable-cost service.
- Future method: independent reviewers must verify the frozen candidate. Any live validation then
  remains a separately authorized hourly-only run: record safe result code, bounded duration, lock
  release and unchanged-or-advanced recovery timestamps without printing paths, device IDs,
  repository identity, credentials, hashes of private content, row counts, or payloads. Retention
  remains prohibited until its distinct exact approval boundary.

## E30.10 — Metadata-frame closure and break-glass authority

- Classification: **measured offline deterministic evidence** for test counts and durations;
  **policy assumptions** for subprocess deadlines. No live volume, repository, Keychain, launchd,
  private database, provider, network, destructive operation or paid service was accessed.
- UTC time and environment: `2026-08-16T23:14:20Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3,
  captured with `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 13 manifest SHA-256
  `7f63e4ae4585023696ba4de36ab19c5c84e01b5e29c12ab31c1dc291995f4382`.
- Metadata-frame method: real AF_UNIX datagrams and fake receive frames exercise high-bit unsigned
  mode, a device value outside the exact later native `c_int` domain, short/overlength framing,
  wrong magic/count/mode, empty and multiple rights records, an extra real descriptor, unexpected
  control data, wrong descriptor count, `MSG_TRUNC`, `MSG_CTRUNC`, and every nonzero receive flag.
  Result: all malformed inputs returned the fixed path-free `recovery_target_unavailable` envelope;
  every installed descriptor was proven closed while sender descriptors remained valid. Numeric
  bounds are checked before `stat.S_IFMT` or `ctypes`; the parsing boundary catches all ordinary
  exceptions. The child remains the only process that calls `fstat` on external descriptors, and
  parent authority/topology code consumes only the bounded fixed metadata frame.
- Break-glass method: the interactive restore path obtains a topology-validated repository
  directory descriptor without calling repository identity or the automation password command.
  The same descriptor and recovery-lock FD are passed to check, snapshots and dump; all three use
  only `/dev/fd/<pin>`, preserve interactive credential entry, and perform path/object/topology
  revalidation before the pin closes. Tests prove the password-command argument and `cat config`
  are absent. Path helpers, the diskutil adapter and internal application verifier receive neither
  authority descriptor.
- Orphan result: a real synthetic interactive repository process forked an escaped-session
  descendant, then left its parent in a 60-second hang. The test substitutes a **2.0-second**
  deadline solely to exercise cleanup; the descendant retains both authority descriptors for
  **4.0 seconds**. After the bounded parent return, direct lock acquisition, backup, retention and
  API reservation all returned `recovery_busy`. After descendant exit, the lock was reusable and a
  complete check/snapshot/dump, format-v1 restore, four-read comparison, pre-reauth provider block,
  synthetic reauthentication and retained-family verification succeeded. Consecutive fresh runs
  passed; the measured targeted run completed the entire orphan/wait/retry case in **9.57 seconds**.
  The production 300-second break-glass ceiling remains a policy bound, not a measured live restore
  duration.
- Targeted command: `/usr/bin/time -p .venv/bin/pytest -q tests/test_recovery.py
  tests/test_recovery_integration.py -k 'path_open_session or macos_device_subject or break_glass
  or diskutil_adapter_receives' --durations=3`. Result: **23/23 passed** in **12.64 seconds wall
  time** (user 5.96, system 1.28).
- Final gates: collection measured **188 scoped tests** and **414 full backend tests**. The scoped
  recovery/API/oracle/integration/hardening gate passed **188/188** in **101.37 seconds wall time**
  (user 68.82, system 14.31). `/usr/bin/time -p .venv/bin/pytest -q` passed **414/414** in **136.80
  seconds wall time** (user 93.23, system 20.96). The only warning was the existing Starlette/httpx
  deprecation notice. `.venv/bin/ruff check .` and `git diff --check` passed; no-index whitespace
  checks for all four changed untracked leased paths produced no error output and the expected
  difference exit 1. Candidate 13 whole-backup pin/deadline controls and Candidate 12's
  `api/recovery.py` restore projection remain unchanged.
- Provider/data-boundary and cost result: **pass; $0/month delta**. There is no schema, API,
  configuration, provider, hosted/private-data or resource change. The tests use only synthetic
  SQLite data, temporary local files, fake topology, local descriptors and isolated child
  processes.
- Future method: after independent review of the immutable candidate, any live restore remains a
  separately authorized runbook action. Record bounded duration, safe result, lock release,
  check/snapshot/dump completion and scratch cleanup without exposing paths, device identifiers,
  repository identity, credentials, private hashes, row counts or payloads. Retention remains under
  its separate exact destructive approval boundary.

## Not measured in candidate 15

The measurement method for device/live closure is the accepted operator runbook: capture the
verified physical-parent identities as redacted classes; verify FileVault; verify the pinned Restic
release; initialize the specifically approved empty repository; execute backup; apply only the
separately approved retention argv; then perform a break-glass scratch restore and record the
tool-produced restore-ready and verification durations. Those actions require human/device
authority and were intentionally not performed by this offline implementation agent.

Offline tests now cover a six-stage SIGKILL residue matrix using the real operational drill,
cross-process API/CLI lock recovery,
marked-scratch attack cases, repository/topology/plan swaps, the all-column oracle, four-read app
comparison and recovery Playwright behavior. The remaining evidence is device/live-only; no value
is estimated for it.

## E30.11 — Credential-broker authority isolation

- Classification: **measured offline deterministic evidence** for the synthetic launcher,
  descriptor inheritance, failure behavior, test counts and timings; **policy assumptions** for
  the five-second credential-command and six-second broker ceilings. No live credential, Keychain,
  `.env`, external volume, repository, private database, launchd state, provider, network,
  destructive operation or paid service was accessed.
- UTC time and environment: `2026-08-16T23:43:33Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3,
  captured with `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and
  `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 15 manifest SHA-256
  `01cf8114a7b31ddcf920810eb8e2e6be59d13319ca324cf69e84c623e72e5be1`.
- Relay method: production code starts an isolated bounded broker with `close_fds=True` and passes
  it only an anonymous password-pipe write descriptor and private one-byte status socket. The
  broker alone runs the configured credential command with no passed descriptors, bounds its
  output and duration, and writes transient output into the anonymous pipe. The application
  process observes only the status byte and password read descriptor. Automated Restic argv uses
  `--password-file /dev/fd/<fd>` and receives exactly repository pin, recovery lock and password
  read descriptors; it never receives or names the credential command. Interactive break-glass
  remains unchanged and receives only repository pin plus recovery lock.
- Boundary result: the successful test uses an equivalent synthetic Restic launcher which consumes
  a generated fake credential from the anonymous descriptor and validates only its length. The
  fake credential is not retained or asserted by the application test. Instrumented process
  launches and independent descriptor scans prove the broker and credential parent/child receive
  neither repository nor recovery-lock authority, path helpers receive neither, and both synthetic
  Restic calls receive the intended three descriptors. The already verified Restic binary was not
  invoked because its location is supplied only through forbidden live runtime configuration; the
  equivalent launcher proves the process/FD boundary but is not a live-binary compatibility claim.
- Failure result: independent empty-output, nonzero-exit, output-overflow and 60-second-hang cases
  all return the fixed path-free `recovery_repository_error` envelope within the three-second test
  bound. A synthetic redaction marker written to both credential-command output streams appears in
  neither application output nor the safe error. Prior coverage, snapshot and artifact state remain
  exact and the lock is immediately reusable. A credential command also forks a new-session child;
  that escaped child reports no repository/lock descriptor and does not block immediate recovery
  admission while it remains alive. Candidate 14's complementary escaped-Restic regression still
  proves that a Restic descendant retains the recovery lock until it exits.
- Targeted command: `/usr/bin/time -p .venv/bin/pytest -q tests/test_recovery.py -k
  'credential_broker or escaped_credential_descendant'`. Result: **6/6 passed** in **11.79 seconds
  wall time** (user 5.42, system 1.25).
- Final gates: collection measured **194 scoped tests** and **420 full backend tests** with the
  corresponding non-quiet `pytest --collect-only` commands. The scoped
  recovery/API/oracle/integration/hardening gate passed **194/194** in **134.59 seconds wall time**
  (user 88.92, system 18.62). `/usr/bin/time -p .venv/bin/pytest -q` passed **420/420** in **162.74
  seconds wall time** (user 110.11, system 24.14). The only warning was the pre-existing
  Starlette/httpx deprecation notice.
- Provider/data-boundary and cost result: **pass; $0/month delta**. There is no schema, API,
  configuration, dependency, provider, hosted/private-data, resource or variable-cost change. The
  candidate uses only local process/descriptor isolation and synthetic temporary inputs.
- Live exclusions and future method: the offline candidate does not prove a Keychain read, actual
  pinned-Restic invocation, real repository access or successful hourly execution. After immutable
  review, a separately authorized hourly-only run may record only bounded duration, safe result,
  lock release and unchanged-or-advanced recovery timestamps. It must not print the credential,
  command output, paths, device IDs, repository identity, private hashes, row counts or payloads.

## E30.12 — Live hourly runner validation

- Classification: **measured live local external-state evidence** for Candidate 16 verification,
  the settled launchd result, secret-free recovery state, recovery-lock release and hourly-service
  unload; **policy assumption** for the unused 720-second observation ceiling; **unmeasurable** for
  the RunAtLoad duration and exact coverage/snapshot direction because the hourly label was already
  loaded and its invocation had already settled before this writer's first observation.
- UTC time and environment: observed from `2026-08-20T21:13:15Z` through
  `2026-08-20T21:14:36Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3, captured with
  `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`.
- Dataset and boundary: the operator-authorized native hourly runner could read the existing local
  source database and automation Keychain item and could access only the initialized encrypted
  local repository through the reviewed `backup --reason hourly` path. No source row, credential,
  path, device/repository identifier, private hash, snapshot identifier or content was inspected or
  emitted. Retention, restore, initialization, repair, erase and source/Keychain mutation remained
  excluded.
- Method: `shasum -a 256 docs/phase-30-private-recovery-baseline.md
  /tmp/phase30-candidate16.manifest`; `shasum -a 256 -c
  /tmp/phase30-candidate16.manifest`; a bounded Python wrapper captured but did not stream raw
  `/bin/launchctl print gui/<current-uid>/com.espn-edge.private-recovery` output and emitted only
  loaded/running/runs/last-exit fields; `recovery_status()` and `load_state()` were projected only
  to configured/available/result/artifact/timestamp-relation fields; nonblocking
  `recovery_lock()` tested lock release. Because the already-settled result was a failure, no
  install/load or retry was issued. `/bin/launchctl bootout
  gui/<current-uid>/com.espn-edge.private-recovery` ran with stdout/stderr suppressed and a
  10-second subprocess timeout, followed by the same bounded, output-suppressed `launchctl print`
  existence check. One observation and one unload; no warm-up.
- Observation bound: the planned external observer ceiling was **720 seconds**, rounded above the
  changed-content path's implemented Restic version/metadata/dump/backup/check ceilings plus the
  credential-broker, topology/path-authority and cleanup ceilings. This is a failure-containment
  policy bound, not a measured backup duration. The loop was not started because the service was
  already not running at first observation.
- Raw bounded result: Candidate 16 verified **33/33 paths** and the accepted contract digest
  matched. The hourly label was loaded but `not running`, its cumulative launchd counter was
  **15 runs**, and its last exit code was **1**. The secret-free state result was
  `recovery_repository_error`; target availability was false; the recovery lock was immediately
  acquirable; and the previously allowed aggregate artifact size remained **43,161,199 bytes**.
  The unload returned **0** in **0.01 seconds** and the subsequent check measured the hourly label
  unloaded. The retention label was unloaded before and after and was untouched.
- Timestamp result: post-result state retained both coverage and snapshot timestamps and they were
  equal, but no pre-invocation values were captured by this writer. Therefore actual
  unchanged-versus-advanced direction for the settled RunAtLoad is **unmeasurable**, and the exact
  timestamp values are intentionally not recorded. Code review shows the handled
  `RecoveryError` path preserves prior coverage/snapshot fields, but that construction fact does
  not replace a missing live pre-state measurement.
- Provider/data-boundary and cost result: **pass for actions initiated by this writer; $0/month
  delta**. No ESPN, FFC, nflverse, Anthropic, Cognito, AWS, other network, provider, model or paid
  command was invoked. The launchd plist's reviewed program is only the local recovery CLI hourly
  backup with a minimal environment and null output paths; network absence was not packet-captured.
  There is no schema, API, configuration, dependency, provider, source-data or recurring-cost
  change.
- Validity domain and confounder: this proves safe failure observation, lock release and fail-closed
  unload on this host. It does not prove a successful Candidate 16 hourly run. The handoff recorded
  the hourly label as unloaded, but it was already loaded and settled before this writer acted; the
  cumulative run counter cannot be attributed to this validation and no start monotonic timestamp
  exists. The post-result target check was unavailable, so repository success is not claimed.
- Future method and next gate: leave both hourly and retention services unloaded. Any target
  diagnosis or another hourly attempt requires a new bounded operator decision; that attempt must
  capture secret-free pre-state and a monotonic start before one load. Retention dry-run,
  retention application/deletion and restore remain separately gated and were not approached.

## E30.13 — Read-only target diagnosis

- Classification: **measured live read-only local metadata evidence** for mount availability,
  filesystem class, application target status, elapsed time and unloaded service state;
  **derived inference** that the approved off-device mount was absent, from the measured APFS/no-
  distinct-mount and `target_available=false` inputs; **unmeasurable** for repository, credential
  and pinned-tool health because the stop-when-isolated ladder halted before those boundaries.
- UTC time and environment: observed from `2026-08-20T22:03:30Z` through
  `2026-08-20T22:04:28Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3, captured with
  `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`, and pre-diagnosis evidence
  SHA-256 `ea840da258ddcc1ce9c3982a30ab85ef5431bdfbb22bf665367b65ab18b4414c`.
- Dataset and boundary: configured local recovery metadata, the containing mounted-filesystem
  inventory, secret-free application recovery status/doctor output, and parsed launchd existence
  only. No path, device or repository identifier, credential, private hash, row/count, snapshot
  metadata or content was emitted. The source database, recovery state, Keychain and encrypted
  repository were not mutated.
- Preflight method and result: `shasum -a 256
  docs/phase-30-private-recovery-baseline.md /tmp/phase30-candidate16.manifest
  docs/evidence/phase-30-private-recovery.md`; the 32 non-evidence Candidate 16 manifest entries
  were reverified with `shasum -a 256 -c`. Bounded, raw-output-suppressed
  `/bin/launchctl print` queries measured both hourly and retention labels unloaded before the
  diagnosis and again at completion.
- Target method: a Python projection loaded typed configuration without emitting values, opened the
  configured directory through `_bounded_path_authority(require_directory=True)` with the
  implemented five-second topology ceiling and two-second path-open ceiling, then used
  `fstatvfs()` only for read-only/free-space classification. `/sbin/mount` ran through
  `BoundedSubprocessRunner` with a five-second timeout; its raw output was captured and discarded,
  and only the matched filesystem type and distinct-mount boolean were emitted. One run, no warm-up.
- Target result: completed in **0.35 seconds**. Configuration was present and absolute; the target
  directory existed, was a directory and its containing filesystem was available and writable,
  with **at least 4 GiB** free. However, it resolved to **APFS** with **no distinct mountpoint**.
  This is the internal containing filesystem, not the approved separately mounted off-device media.
- Application method and result: `_doctor()` ran once and was projected only to contract-allowlisted
  booleans, state/error codes, aggregate artifact bytes and media-class/separation fields; exact
  timestamps, paths, identifiers and schema/private hashes were excluded. It completed in **0.31
  seconds**, reported native topology supported and configuration present, but
  `target_available=false`. It therefore did not enter its tool/topology-separation branch. The
  prior safe state code remained `recovery_repository_error` and the allowed aggregate artifact
  size remained **43,161,199 bytes**. Doctor also reported FileVault off; this differs from the
  prior recorded observation, is not causal for the missing off-device mount, and requires a fresh
  bounded check before any future restore gate. The current `state=not_required` reflects local
  configuration policy and does not override `target_available=false`.
- Cause and confidence: **derived inference — approved off-device mount absent**, from the measured
  APFS/no-distinct-mount and `target_available=false` inputs. The independent filesystem check shows
  the configured directory has fallen through to internal APFS, and the application independently
  rejects it as unavailable. This is not a state-only stale error. Tool, automation credential and
  repository health are not classified because the ladder stopped before reading the Keychain or
  invoking Restic.
- Repository-step disposition: skipped as required because target availability was false. No
  repository identity, snapshot inventory, dump, check, Keychain access or Restic process occurred.
  No disk verification/repair command was run, so no unmount/remount or filesystem mutation was
  attempted.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No ESPN, FFC, nflverse,
  Anthropic, Cognito, AWS, other network, provider, AI, paid service, backup, initialization,
  retention, restore, repair, runner load/kickstart or retry action occurred. There is no schema,
  API, configuration, dependency, provider, source-data, repository or recurring-cost change.
- Validity domain and limitations: this isolates the immediate failure on this host at this time;
  it does not explain why or when the physical volume became unavailable and does not establish
  current filesystem integrity. The application's `fdesetup` doctor subprocess has no internal
  timeout parameter, although this observed call returned inside the measured 0.31-second doctor
  duration. Repository/tool/credential success and a successful hourly execution remain untested.
- Future method and next gate: keep both services unloaded. The operator must physically make the
  already approved off-device volume available at its configured mount without repair/erase, then
  separately authorize one bounded recheck: repeat the safe mount projection and doctor; only if
  `target_available=true`, run one pinned read-only repository integrity operation returning only
  pass/fail and duration. Reconfirm FileVault before any restore. A later hourly load/retry,
  filesystem verification/repair, retention action/deletion or restore remains a distinct human
  gate.

## E30.14 — Remounted target and doctor recheck

- Classification: **measured live read-only local metadata evidence** for the distinct-mount,
  filesystem type, writable/free-space-threshold, application doctor, elapsed-time and unloaded-
  service results; **unmeasurable/not evaluated** for tool verification, physical separation and
  source/target media classes because the application still reported the target unavailable.
- UTC time and environment: observed from `2026-08-21T01:59:53Z` through
  `2026-08-21T02:00:50Z`; macOS 26.5 build 25F71, arm64, Python 3.14.3, captured with
  `date -u +%Y-%m-%dT%H:%M:%SZ`, `sw_vers`, `uname -m`, and `.venv/bin/python --version`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`, Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`, and pre-recheck evidence
  SHA-256 `cf51ea8f9182e3053e4d2c7326244c8f76839631fa7e2fa6e65657d9f6494a2c`.
- Boundary and preflight: the accepted contract digest and Candidate 16 manifest digest matched;
  all 32 non-evidence Candidate 16 entries reverified. Bounded, raw-output-suppressed
  `/bin/launchctl print` queries measured both hourly and retention labels unloaded before and
  after the recheck. No path, device/repository identifier, precise free bytes, timestamp, private
  hash/count/content or credential was emitted.
- Mount method: one Python projection loaded typed configuration without emitting values, opened
  the configured directory through `_bounded_path_authority(require_directory=True)` with the
  implemented five-second topology ceiling and two-second path-open ceiling, then used
  `fstatvfs()` only for writable and at-least-4-GiB booleans. `/sbin/mount` ran once through
  `BoundedSubprocessRunner` with a five-second timeout; raw output was captured and discarded, and
  only filesystem type and distinct-mount status were emitted. No warm-up.
- Mount result: completed in **0.34 seconds**. The containing filesystem was **APFS**, the target
  was writable, at least **4 GiB** was available, and `distinct_mount=false`. This records only the
  measured configured-location result; it does not claim why an operator-reported remount was not
  visible there.
- Doctor method: one `_doctor()` call ran inside a wrapper with a **20-second** subprocess timeout.
  The wrapper captured and parsed only supported/configured/target-available/tool-verified/
  physical-separation/media-class/FileVault/result-code fields, discarded raw output and recorded
  total duration. No warm-up.
- Doctor result: completed in **0.59 seconds** with `supported=true`, `configured=true`,
  `target_available=false`, FileVault `off`, and safe result code `recovery_repository_error`.
  Tool verification, physical separation and both media classes were not evaluated because doctor
  stops that branch when target availability is false.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No Keychain, Restic/repository,
  source-database row, recovery bundle, provider, AI, cloud or network access occurred. No runner
  load/kickstart/retry, backup, initialization, filesystem verification/repair/erase, retention,
  restore, state mutation, code/config/test/runbook edit, commit or merge occurred. There is no
  schema, API, configuration, dependency, provider, data or recurring-cost change.
- Validity, rollback and external state: this is one bounded observation of the configured location
  and application doctor on this host. It does not prove the physical volume is absent, healthy or
  mounted elsewhere, and does not test the repository, automation credential or pinned tool. The
  operation is rollback-safe because it was read-only; both hourly and retention services remain
  unloaded.
- Future method and next gate: stop here. The operator must reconcile the physical volume's actual
  mounted location with the already approved configured location, without repair/erase, and must
  separately authorize any further mount/doctor recheck. Only after a measured
  `distinct_mount=true` and `target_available=true` may a separately approved pinned read-only
  repository integrity check occur. Hourly retry, filesystem verification/repair, retention action
  or restore remain distinct human gates.

## E30.15 — Host-level FileVault and doctor validity correction

- Classification: **measured** for the already-completed unsandboxed host command results;
  **externally/visually verified** for the operator screenshot showing the FileVault toggle on;
  **measured within a restricted execution environment only** for E30.13/E30.14's sandbox errors;
  and **unmeasurable** for command duration because the supplied host-level result did not retain a
  bounded duration value. This entry changes evidence validity, not host or application state.
- UTC time and environment: the host-level checks were completed during the authorized 2026-08-21
  recheck on native macOS 26.5 build 25F71, arm64, with Python 3.14.3. Exact observation timestamps
  are intentionally omitted. The prior E30.13/E30.14 commands ran inside the managed filesystem
  sandbox; the superseding commands ran unsandboxed on the same host.
- Identity and source: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-correction evidence
  SHA-256 `e4c3dbf3372409e366049203a242ad307a2fd560030cbc14fcce8d6a393060b2`.
- Method: visual inspection of the supplied system-settings screenshot was limited to the
  FileVault toggle and did not retain or describe the username, photo or other UI content. The
  exact already-completed host commands were `/usr/bin/fdesetup status` and
  `.venv/bin/python -m api.recovery doctor --json`. Outputs were projected to FileVault state,
  supported/configured/required/target-available booleans, policy state, safe result code,
  source/target media classes, physical-separation and tool-verification fields, plus the
  contract-allowed aggregate artifact bytes. Raw doctor output, paths, identifiers, precise
  timestamps and schema/private hashes were not retained in this ledger.
- Restricted-environment result: the screenshot visually showed FileVault on, while sandboxed
  `/usr/bin/fdesetup status` returned an unknown-volume error and the sandboxed `diskutil` path
  lacked DiskManagement access. Therefore E30.13/E30.14's FileVault `off`, APFS/no-distinct-mount
  and `target_available=false` results remain measured only for that restricted execution
  environment. They are superseded by the unsandboxed checks for current host state and must not be
  interpreted as physical-volume or FileVault failures on the host.
- Host FileVault result: unsandboxed `/usr/bin/fdesetup status` measured **FileVault On**,
  consistent with the visual screenshot. No recovery key, username, path or other system-settings
  content was captured.
- Host doctor result: unsandboxed `.venv/bin/python -m api.recovery doctor --json` measured native
  topology supported and recovery configured, `target_available=true`, source media class
  `internal`, target media class `external`, physical separation true, and the pinned tool
  verified. The current policy result was `required=false` and `state=not_required`; the prior safe
  result code remained `recovery_repository_error`. The contract-allowed aggregate artifact size
  remained **43,161,199 bytes**. No exact timestamps, schema fingerprint, paths, device/repository/
  snapshot identifiers, private counts/content or raw output are recorded.
- Policy gate: `required=false` is a remaining configuration/policy gate. This evidence entry does
  not change it, does not claim the protected-write cutoff is armed, and does not reinterpret
  `state=not_required` as Phase 30 closure.
- Provider/data-boundary and cost result: **pass; $0/month delta**. This writer executed no new host
  command or external action. No Keychain, Restic/repository, source-database row, recovery bundle,
  runner, provider, AI, cloud or network access occurred during this evidence correction. No
  backup, initialization, filesystem verification/repair/erase, retention, restore, code/config/
  test/runbook edit, commit or merge occurred. There is no schema, API, dependency, provider, data
  or recurring-cost change.
- External state, validity and next gate: both hourly and retention services remain unloaded. The
  host-level result establishes FileVault on and a qualifying available external target/tool
  boundary, but it does not prove repository readability or a successful Candidate 16 hourly run.
  Enabling the required recovery policy is not authorized here. The next human gate must separately
  decide the `required=true` configuration transition and any pinned read-only repository check or
  hourly retry; retention and restore remain separately gated.

## E30.16 — Bounded repository-integrity pass preflight

- Classification: **measured** for the immutable identity checks and the bounded preflight
  termination; **unmeasurable** for current launchd loaded state and repository integrity because
  the safe parsed service query did not return a projection inside its observation envelope. No
  repository operation was started, so no integrity result, tagged snapshot count or operation
  duration is claimed.
- UTC time and environment: the preflight began at `2026-08-21T05:39:48Z` on the native macOS host.
  The local identity checks used the repository shell; the service projection requested native,
  unsandboxed execution. No warm-up or retry occurred.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-entry evidence
  SHA-256 `13e7f8651726a46af33f6a1b8fd66f184a33753900ccac78c4441c9ff1818200`.
- Identity method and result: `shasum -a 256` matched the accepted contract, Candidate 16 manifest
  and pre-entry evidence identities. `shasum -a 256 -c
  /tmp/phase30-candidate16.manifest` matched all 32 non-evidence entries. Its evidence entry differed
  as expected because E30.15 postdates Candidate 16; the separate pre-entry evidence digest matched.
- Service preflight method and result: a bounded native Python projection invoked
  `/bin/launchctl print gui/<uid>/com.espn-edge.private-recovery` and
  `/bin/launchctl print gui/<uid>/com.espn-edge.private-recovery-retention`, with stdout/stderr
  suppressed and a five-second subprocess deadline per label. It was permitted to emit only two
  loaded booleans. No safe projection returned within the enclosing observation window, so the
  command envelope was terminated and current unloaded state was not established. Raw launchd
  output, paths, identifiers and process details were neither emitted nor retained.
- Repository result: **not run**. The reviewed `ResticRepository.validate_tool()`,
  `recovery_lock()`, `pin_repository()`, `check()` and tagged `snapshots()` sequence was not entered
  because the required service-unloaded precondition was unproven. Tagged snapshot count remains
  unmeasurable in this pass. The previously measured aggregate artifact size remains **43,161,199
  bytes**, but this aborted preflight did not revalidate it against repository metadata.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No Keychain credential broker,
  Restic/repository, source database, recovery bundle, provider, AI, cloud or network access
  occurred. No backup, snapshot creation/modification/deletion, initialization, retention, restore,
  runner load/kickstart/retry, filesystem action, state/configuration/code/test/runbook change,
  commit or merge occurred. The only repository change is this secret-free evidence entry; schema,
  API, configuration, dependency, provider, data and recurring-cost deltas are none.
- Validity, confounders, rollback and future method: the absence of a parsed response may reflect
  the native query, its authorization envelope or their interaction; it is not evidence that either
  service is loaded or unloaded. The operation is rollback-safe because it stopped before the
  repository sequence and performed no mutation. A separately authorized future pass must first
  establish both labels unloaded using a newly bounded safe projection, then run exactly one
  reviewed pinned integrity sequence. No retry is performed under this pass.

## E30.17 — Alternate bounded service preflight and conditional integrity result

- Classification: **measured** for immutable identity verification and termination of the alternate
  preflight envelope; **unmeasurable** for current service loaded state and repository integrity.
  The alternate query returned no safe projection before its outer deadline, so the conditional
  Keychain/Restic sequence was not authorized to start. No integrity pass, tagged snapshot count or
  repository-operation duration is claimed.
- UTC time and environment: this single attempt occurred in the authorized E30.17 window beginning
  `2026-08-21T19:01:06Z`, targeting the native macOS user launchd domain. The child projection used
  system Python only as a bounded wrapper. Raw child output was captured for exact missing-service
  classification and discarded; no raw value reached evidence or agent output. No warm-up or retry
  occurred.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-entry evidence
  SHA-256 `6a87832c676c880bbb572436f368c0b6116a85115f009378fa0c7d11dce80768`.
- Identity method and result: `shasum -a 256` matched the contract, Candidate 16 manifest and
  pre-entry evidence identities. `shasum -a 256 -c /tmp/phase30-candidate16.manifest`, excluding
  its evidence entry superseded by E30.12–E30.16, matched all other **32** entries.
- Alternate service method: exactly one wrapper attempted `/bin/launchctl list
  com.espn-edge.private-recovery` and `/bin/launchctl list
  com.espn-edge.private-recovery-retention`, a genuinely different exact-label mechanism from
  E30.16's `launchctl print`. Each child had a three-second deadline, used a minimal environment,
  suppressed stdin, captured/discarded stdout and stderr, and permitted only two loaded booleans
  plus aggregate duration. A nonzero result was eligible to mean unloaded only when bounded output
  contained launchd's exact missing-service classification; every other result was unknown.
- Alternate service result: the enclosing native-command envelope returned no projected booleans
  within the **20-second** observation window and was terminated. Consequently both labels remain
  `unknown` for this attempt. The absence of projection does not prove either loaded or unloaded;
  whether the child queries began is also unverified because the confounder may be the authorization
  envelope rather than launchd itself.
- Repository result: **not run**. The reviewed `ResticRepository.validate_tool()`, shared
  `recovery_lock()`, `pin_repository()`, read-only `check()` and tagged `snapshots()` sequence was
  never entered because both services were not proven unloaded. The automation credential broker,
  repository metadata and repository data were not accessed. Tagged snapshot count and integrity
  duration remain unmeasurable. The prior **43,161,199-byte** aggregate artifact observation was not
  revalidated by E30.17.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No secret, private payload,
  path, device/repository/snapshot/private identifier, raw launchd output or repository content was
  emitted. No source database, recovery bundle, provider, AI, cloud or network access occurred. No
  backup, snapshot mutation, initialization, dump, restore, retention, filesystem action, state/
  configuration, runner load/kickstart/retry, code/test/runbook change, commit or merge occurred.
  The sole repository change is this evidence entry; schema, API, configuration, dependency,
  provider, data and recurring-cost deltas are none.
- Validity, rollback and next gate: this result establishes only that the alternate projection did
  not complete through the execution envelope. It is rollback-safe and left external state
  unchanged. E30.17 is exhausted without retry. Any future service-state or integrity attempt needs
  a separate operator authorization and must again prove both exact labels unloaded before the
  reviewed pinned read-only sequence.

## E30.18 — Exact-label bootout and conditional read-only integrity

- Classification: **measured** for the exact-label bootout result, process projection, elapsed
  durations and repository sequence's stable safe result; **unmeasurable** for whether either
  bootout changed a loaded service versus confirmed an already-missing service, the internal stage
  represented by the redacted repository error, and the tagged snapshot count. No repository
  success is claimed.
- UTC time and environment: one authorized attempt ran during the E30.18 window beginning
  `2026-08-21T19:12:02Z` on the native macOS host, using the user launchd domain and the reviewed
  `.venv/bin/python` recovery runtime. There was no warm-up or retry. The service envelope and
  repository sequence each used monotonic wall-time measurement.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-entry evidence
  SHA-256 `c51fcb14a07b4db662eef72eb1cb1c7b2f8aeb2e9a6bdcd16261f026f124b7b1`.
- Identity method and result: `shasum -a 256` matched the accepted contract, Candidate 16 manifest
  and pre-entry evidence identities. `shasum -a 256 -c /tmp/phase30-candidate16.manifest`, excluding
  its evidence entry superseded by later ledger entries, matched all other **32** entries.
- Service method: one native wrapper issued `/bin/launchctl bootout
  gui/<uid>/com.espn-edge.private-recovery` and `/bin/launchctl bootout
  gui/<uid>/com.espn-edge.private-recovery-retention`, with a three-second deadline per exact label,
  minimal environment and suppressed stdin. Bounded stdout/stderr were captured and discarded;
  only exit zero or launchd's exact missing-service classification could produce an unloaded
  boolean. No plist was removed and no service was loaded or kicked.
- Service and process result: both exact labels projected `unloaded=true`. A subsequent bounded
  `/bin/ps -axo command=` projection allowed at most 262,144 captured bytes and three seconds, then
  discarded raw output and emitted only `recovery_process_active=false` for `api.recovery`
  backup/retention commands. The full service/process envelope completed in **0.067 seconds**.
  Resulting external state is that both Phase 30 labels are unloaded and no matching recovery
  process was observed; the projection does not distinguish successful state change from an
  already-absent label.
- Repository method: after those preconditions passed, one native `.venv/bin/python` projection
  constructed the reviewed `ResticRepository`, ran `validate_tool()`, acquired one shared
  `recovery_lock()`, entered one `pin_repository()` scope, and invoked only read-only `check()` then
  tagged `snapshots()`. Implemented deadlines were ten seconds for tool validation, bounded topology
  and path-open checks, thirty seconds for repository metadata, and sixty seconds for the integrity
  check. The existing isolated automation credential broker was the only credential consumer. Raw
  tool, credential, topology, repository and snapshot output was captured/discarded and never
  emitted.
- Repository result: **failed** in **1.809 seconds** with stable safe code
  `recovery_repository_error`. The projection intentionally cannot identify which internal
  read-only stage produced that redacted code. The tagged snapshot count is therefore
  **unmeasurable** for E30.18. The shared lock/pin scopes unwound when the bounded process returned;
  no retry or follow-up repository query was run. The prior **43,161,199-byte** aggregate artifact
  observation was not revalidated by this failed sequence.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No secret, path, device/
  repository/snapshot/private identifier, private count/content or raw command output was emitted.
  No source database rows, recovery bundle, provider, AI, cloud or network were accessed. There was
  no backup/snapshot write, dump, restore, initialization, forget/prune/retention action, filesystem
  action, state/configuration mutation, runner install/load/kickstart, plist deletion, code/test/
  runbook change, commit or merge. Schema, API, configuration, dependency, provider, data and
  recurring-cost deltas are none.
- Validity, rollback and next gate: this is one host-local observation of the exact-label state and
  one failed bounded read-only repository attempt. It proves neither repository integrity nor a
  tagged snapshot inventory. The only external-state effect is the authorized unloaded state for
  the two labels; reloading them is not authorized here. Source/repository contents remain
  rollback-safe because no mutation command ran. E30.18 is exhausted; diagnosis or another
  integrity attempt requires a separate operator gate, while retention, restore, runner retry and
  `required=true` policy enablement remain separately gated.

## E30.19 — Staged read-only repository diagnosis

- Classification: **measured** for the process projection, stage pass/fail results and monotonic
  durations; **unmeasurable** for whether the automation credential broker was invoked, whether
  repository identity metadata was accessed, the precise credential-versus-repository cause inside
  the redacted pin-stage error, tagged snapshot count and repository integrity. The final integrity
  check was conditionally authorized but correctly did not run because an earlier stage failed.
- UTC time and environment: one no-warm-up attempt ran during the authorized E30.19 window beginning
  `2026-08-21T20:02:07Z` on the native macOS host using the reviewed `.venv/bin/python` recovery
  runtime. E30.18's two exact labels remained unloaded; E30.19 neither queried nor mutated launchd.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-entry evidence
  SHA-256 `349c58f6507558f95802920b14aa7386df5ad875a822020597f35ba4c24ff301`.
- Identity method and result: `shasum -a 256` matched the accepted contract, Candidate 16 manifest
  and pre-entry evidence identities. `shasum -a 256 -c /tmp/phase30-candidate16.manifest`, excluding
  its evidence entry superseded by later ledger entries, matched all other **32** entries.
- Process preflight method and result: one native `/bin/ps -axo command=` projection used a
  three-second deadline and at most 262,144 captured bytes. Raw process output was discarded; only
  the `api.recovery` backup/retention boolean and aggregate duration were emitted. It measured
  `recovery_process_active=false` in **0.038 seconds**, so the staged repository envelope could
  begin.
- Staged method: one `.venv/bin/python` envelope constructed `ResticRepository` and used the
  implemented ten-second tool-validation deadline, bounded topology/path-open checks and
  thirty-second repository-metadata deadlines. It then attempted one nonblocking shared
  `recovery_lock()` and one `pin_repository()` context. Before yielding a pin, `pin_repository()`
  validates topology/path authority and internally calls credential-backed `repository_id()`, which
  may invoke the automation broker and Restic `cat config`; because the context did not yield, it is
  unmeasurable which of those internal operations were entered or completed. Only after the pin
  context yielded would the later explicit tagged `snapshots(pin=...)` stage run, and only after
  that metadata stage succeeded would it invoke exactly one `check(pin=...)` with its sixty-second
  integrity deadline. Every tool, topology, credential and repository raw output was captured/
  discarded; only safe stage status, stable code, count after success and duration were eligible for
  output.
- Stage results: `validate_tool` passed in **0.077 seconds**; `recovery_lock` passed in **0.001
  seconds**; `pin_repository` failed in **2.340 seconds** with stable safe code
  `recovery_repository_error`. Total envelope duration was **2.418 seconds**. The lock scope unwound
  when the bounded envelope returned. The stage result localizes the failure to pin/topology/
  repository-identity establishment. The stable code does not safely distinguish topology from
  credential availability or encrypted repository metadata access, and does not prove whether the
  broker or internal `repository_id()` read was entered or completed; no more specific cause is
  claimed.
- Conditional stages: the later explicit tagged `snapshots(pin=...)` stage was **not invoked**, so
  tagged snapshot count is **unmeasurable**. `check(pin=...)` was **not invoked**, so E30.19 makes no
  integrity claim. This does not exclude the possible internal `repository_id()` metadata read
  during the failed pin attempt. There was no second attempt, follow-up repository read or raw-output
  inspection. The prior **43,161,199-byte** aggregate artifact observation was not revalidated.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No secret, path, device/
  repository/snapshot/private identifier, private count/content, process listing or raw tool output
  was emitted. Automation-broker invocation and repository identity-metadata access are
  unmeasurable and may have occurred inside `pin_repository()`; no credential value or repository
  metadata was emitted or retained. No source database rows, recovery bundle, provider, AI, cloud
  or network were accessed. No launchd or plist action, backup/snapshot write, dump, restore,
  initialization, forget/prune/retention, filesystem action, state/configuration mutation, runner
  action, code/test/runbook change, commit or merge occurred. Schema, API, configuration,
  dependency, provider, data and recurring-cost deltas are none.
- Validity, rollback and next gate: this is one host-local staged observation. It proves the pinned
  tool and recovery lock were available, but does not prove repository pinning, credential-backed
  metadata success, broker non-invocation, snapshot inventory or integrity. Both labels remain
  unloaded, and source/repository contents remain rollback-safe because no mutation command ran.
  E30.19 is exhausted. Any narrower credential-versus-repository diagnosis or later integrity
  attempt requires a separate operator gate; retention, restore, runner reload and `required=true`
  policy enablement remain separately gated.

## E30.20 — Local macOS directory-descriptor suffix semantics

- Classification: **measured** for the synthetic descriptor-suffix open result and stable exception
  class/errno; **code inspection** for Candidate 16's repository-path and argv construction; and
  **derived high-confidence inference** for the relationship between those inputs and E30.19's live
  `pin_repository` failure. The causal statement is not direct proof of Restic internals.
- UTC time and environment: one no-warm-up local probe ran during the E30.20 evidence window
  beginning `2026-08-21T20:07:46Z`, from the ESPN Edge workspace on the same macOS host using
  `.venv/bin/python`. It accessed only the workspace directory and the public tracked
  `pyproject.toml` template; no private path, content or descriptor number was emitted.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-entry evidence
  SHA-256 `1c67b82c1925594f6423b60088ae9fab1169fcd4c79127fa671fc6621382a475`.
- Method: `.venv/bin/python -c <bounded probe>` opened `.` with
  `os.O_RDONLY | os.O_DIRECTORY`, attempted to open the public template
  `/dev/fd/<directory-fd>/pyproject.toml` with `os.O_RDONLY`, closed every opened descriptor in
  `finally`, and emitted only an openable boolean plus stable exception class and errno. It did not
  read file content, create a temporary file, inspect external state or access Keychain, Restic,
  the recovery repository, source database or network.
- Measured result: `descriptor_suffix_openable=false`; exception class `FileNotFoundError`; errno
  **2**. Thus this local macOS runtime did not treat `/dev/fd/<open-directory-fd>/<suffix>` as a
  traversable directory path, even though the original directory descriptor itself remained open.
- Code-construction input: `api/services/recovery.py` constructs
  `RepositoryPin.repository_path=f"/dev/fd/{fd}"`, `_argv()` passes that value as Restic's `-r`
  repository root, and `pin_repository()` calls `repository_id()`, which issues Restic `cat config`
  before yielding the pin. Restic local-repository operation then needs to resolve repository files
  such as `config` beneath the supplied repository root; this probe did not execute or trace that
  internal path resolution.
- Derived inference: E30.19 measured tool validation and lock acquisition passing, then a safe
  `recovery_repository_error` inside `pin_repository()` before it yielded. Combining that stage
  boundary, Candidate 16's `/dev/fd/<directory-fd>` root construction and the measured inability to
  traverse a suffix through that descriptor form makes descriptor-suffix incompatibility a
  **high-confidence derived cause** of the live pin failure. It is not direct proof: no Restic
  command, syscall trace, Keychain access or live repository operation ran in E30.20, and E30.19's
  stable redaction still prevents excluding another failure inside its internal credential/config
  read.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No secret, private identifier,
  private count/content, path beyond the public template, descriptor number or raw private output
  was emitted. No external state, repository/source data, provider, AI, cloud or network was
  accessed or mutated. No code, configuration, schema, API, dependency, runner, filesystem, test,
  runbook, commit or merge change occurred; the sole write is this evidence entry.
- Validity and next gate: the measured result is valid for the local Python/macOS descriptor-path
  semantics exercised by this synthetic public-file probe. It does not by itself prove the exact
  internal Restic syscall sequence or authorize a production-code fix. A narrow pin-transport fix
  requires its own accepted contract/lease and independent tests; any later live repository attempt,
  runner reload, retention, restore or `required=true` policy transition remains separately gated.

## E30.21 — Descriptor-bound Restic transport candidate

- Classification: **code inspection** for the transport construction and authority boundaries;
  **measured** for the offline synthetic tests, installed pinned-tool digest/version check, test
  durations and lint/diff results; and **derived arithmetic** for the zero schema/API/configuration/
  provider/cost deltas. No live recovery result is claimed.
- UTC time and environment: Candidate 17 implementation and verification ran during the local
  macOS window ending `2026-08-21T20:32:05Z`, from the ESPN Edge workspace and its reviewed
  `.venv/bin/python` environment. Tests were offline and used only synthetic temporary source,
  repository, credential, lock and cache paths. There was no warm-up and no live retry.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 16 sorted 33-path manifest SHA-256
  `2f84953ab069aa4a79acb512b51d9cd4d4df428dbebb426bad15ce830d23c5f8`; pre-entry evidence
  SHA-256 `08a876970586af1794e75101e513a77d345a2e485e71c9f7d34c0ebb2f946359`.
- Change method: `api/services/recovery.py` now supplies pinned Restic operations with repository
  root `.` and the already-open repository directory descriptor as explicit cwd authority. The
  fixed isolated Python child calls `os.fchdir(directory_fd)` and then `os.execv()` for the absolute
  pinned Restic argv. The existing bounded runner still uses no shell, a minimal environment,
  bounded output and deadlines, process-group cleanup for automation and interactive behavior for
  break-glass. Its explicit descriptor allowlist is exactly repository plus recovery lock and, for
  automation only, the password pipe. Credential and topology helpers receive no repository or
  recovery-lock authority.
- Focused method and result: `.venv/bin/python -m pytest` selected the descriptor-cwd validation/
  timeout-redaction case, every persistent and ABA replacement point, escaped-Restic-descendant
  lock/retry, break-glass, real-tool transport and integration orphan-lock/retry cases with `-q`;
  all **20** collected cases completed at 100% with exit zero. The broader `.venv/bin/python -m
  pytest tests/test_recovery.py tests/test_recovery_integration.py -q` gate collected **175** tests
  and completed at 100% with exit zero.
- Real-tool result: the optional direct test verified the installed Restic `0.19.1` binary against
  its accepted SHA-256 before execution, then used only a synthetic temp repository and credential.
  Descriptor-bound `init`, `cat config`, tagged `snapshots` and `check` passed for both a persistent
  lexical replacement and an ABA replacement. The persistent replacement remained empty, proving
  the operation stayed on the opened repository inode. Each automated Restic invocation received
  exactly three distinct inherited descriptors; separate process-inheritance tests proved the
  credential broker received neither repository nor recovery-lock authority and escaped Restic
  descendants retained the shared lock until exit. The first local real-tool probe encountered the
  sandbox's unwritable default user cache; rerunning the test with a synthetic temp-only
  `RESTIC_CACHE_DIR` removed that environment confounder without changing production code.
- Full gates: `make test` measured **423 passed**, one pre-existing Starlette deprecation warning
  and **186.44 seconds** wall time. `make lint` ran `.venv/bin/python -m ruff check api tests` and
  returned `All checks passed!`. `.venv/bin/python -m compileall -q api/services/recovery.py`,
  and tracked `git diff --check` returned exit zero. Each `git diff --no-index --check /dev/null
  <owned path>` returned the conventional difference exit 1 because the nonempty untracked file
  differs from `/dev/null`; empty diagnostic output proves no whitespace error. The no-index form
  covers these Candidate 16 paths even though they are untracked at the Git base.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No live Kapili repository,
  Keychain item, source database, recovery bundle, launchd label, provider, AI, cloud or network was
  accessed or mutated. No secret, private path/identifier/count/content or raw private output was
  emitted. Schema, public API, configuration, dependency, provider behavior, hosted/public data and
  recurring-cost deltas are none. Production behavior changes only for descriptor-bound local
  Restic child launch.
- Validity, rollback and next gate: all test repositories and credentials were synthetic and
  temporary; no external state remains. The change is rollback-safe by reverting the child launcher,
  cwd descriptor plumbing and Candidate 17 tests. Candidate 17 does not establish live repository
  integrity, reload either runner or resolve the remaining `required=false` policy gate. Independent
  Cybersecurity and QA review of the frozen manifest is next; any live retry, runner load, retention,
  restore or policy transition remains separately operator-gated.

## E30.22 — Native read-only integrity sequence authorization result

- Classification: **measured** for the bounded process projection, its duration and the execution
  approval result; **unmeasurable** for tool validation, lock acquisition, repository pinning,
  tagged snapshot count and repository integrity because the repository envelope was rejected
  before process creation. No repository result is claimed.
- UTC time and environment: one no-warm-up process preflight and one attempted execution approval
  occurred during the authorized E30.22 window ending `2026-08-21T21:31:26Z` on the native macOS
  host from the ESPN Edge workspace. The preflight used the reviewed `.venv/bin/python` runtime;
  the repository Python process did not launch.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `45013a46dfe6ab78f6a5bf3cdc78e14d5e027eb513fde889ebc8ee9bca83938a`.
- Identity method and result: `shasum -a 256` matched the accepted contract, Candidate 18 manifest
  and pre-entry evidence identities. `shasum -a 256 -c /tmp/phase30-candidate18.manifest` matched all
  **33** entries before the preflight and this evidence append.
- Process-preflight method: one native `.venv/bin/python -c <bounded projection>` invoked
  `BoundedSubprocessRunner.run(["/bin/ps", "-axo", "pid=,command="],
  max_output=262144, timeout_seconds=3.0)`. Raw process output was captured, parsed only for an exact
  `python -m api.recovery backup|retention` command shape and discarded; only the aggregate boolean,
  safe code and monotonic duration were emitted. It did not query launchd.
- Process-preflight result: `preflight_ok=true`, `recovery_process_active=false`, safe code `ok`, in
  **0.093 seconds**. This one bounded projection supports only the point-in-time absence of matching
  backup/retention processes; it does not prove launchd state.
- Repository-envelope method and result: one native `.venv/bin/python -c <bounded Candidate 18
  envelope>` was submitted to run, in order, `ResticRepository.validate_tool()`, one nonblocking
  `recovery_lock()`, one `pin_repository()` context, one tagged `snapshots(pin=...)` count and exactly
  one `check(pin=...)`, stopping at first failure and using the implemented deadlines. The execution
  approval layer rejected the command before process creation because this agent context did not
  contain a directly trusted user authorization for the live credential-brokered private-repository
  access. Therefore `validate_tool`, `recovery_lock`, `pin_repository`, the credential broker,
  `snapshots` and `check` were **not invoked**. Tagged snapshot count and integrity are
  **unmeasurable**. No retry or alternate execution was attempted.
- Provider/data-boundary and cost result: **pass; $0/month delta**. The rejected envelope did not
  read Keychain, repository metadata/content, the source database, recovery state or a recovery
  bundle. No secret, path, device/repository/snapshot/private identifier, private count/content or
  raw process output was emitted. No backup, initialization, dump, restore, retention, filesystem,
  state/configuration, launchd, runner, provider, AI, cloud or network action occurred. Schema, API,
  configuration, dependency, provider, data and recurring-cost deltas are none; the only write is
  this evidence entry.
- Validity, rollback and next gate: the measured preflight is valid only for its point-in-time
  process projection. E30.22 does not establish repository readability, tagged inventory or
  integrity. External state remains unchanged and rollback-safe. This attempt is exhausted without
  retry. The exact same bounded Candidate 18 sequence requires a new directly trusted user approval
  for live credential-brokered private-repository access; runner loading, backup, retention, restore
  and `required=true` remain separately gated.

## E30.23 — Completed native Candidate 18 read-only integrity sequence

- Classification: **measured** for the direct sequence's safe stage booleans, tagged snapshot
  count, null error and monotonic duration. The result is distinct from E30.22: E30.22 truthfully
  records this agent's approval-layer rejection before process creation, while one later direct
  Agent 1 command reached the repository exactly once.
- UTC time and environment: the one no-warm-up sequence ran after E30.22 during the amended live
  evidence lease on the native macOS host, from the ESPN Edge workspace using Candidate 18 and the
  reviewed `.venv/bin/python` runtime. The bounded safe result did not retain an exact UTC start, so
  that timestamp is **unmeasurable** from the supplied projection; its measured monotonic duration
  is recorded below.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `1c6a6da7392968a3bcafe62313872676a6a54d80644a95067ddfcdc62fe63921`.
- Method: one direct native `.venv/bin/python -c <safe Candidate 18 projection>` constructed the
  reviewed `ResticRepository`, ran `validate_tool()`, acquired one nonblocking `recovery_lock()`,
  entered one `pin_repository()` context, read only the count returned by tagged
  `snapshots(pin=...)`, and ran `check(pin=...)` exactly once with its implemented deadline. The
  existing automation credential was consumed only by the reviewed isolated broker. Repository,
  credential and topology output was captured/discarded; the command emitted only bounded safe JSON
  containing stage booleans, count, error and duration.
- Result: `tool=true`, `lock=true`, `pin=true`, `metadata=true`, `integrity=true`,
  `snapshot_count=1`, `error=null`, and `duration_seconds=5.766`. This is one successful read-only
  repository attempt. No snapshot identifier, repository/device identifier, path, credential,
  private content or raw Restic output was emitted. The integrity result is valid for Candidate
  18's implemented `check(pin=...)` operation; it is not a claim that a different or exhaustive
  future check mode ran.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No backup/snapshot write,
  initialization, dump, restore, forget/prune/retention, recovery-state/configuration change,
  runner/launchd action, filesystem mutation, provider, AI, cloud or network call occurred. No
  source row or recovery-bundle content was read or emitted by the projection. Schema, API,
  configuration, dependency, provider, hosted/public data and recurring-cost deltas are none. The
  current launchd-label loaded/unloaded state was not rechecked by E30.23 and is **unmeasurable**;
  the successful sequence establishes only that it performed no runner or launchd action. The sole
  current write is this E30.23 evidence append.
- Validity, rollback and next gate: this single native observation establishes that the exact
  Candidate 18 descriptor-bound adapter could validate its pinned tool, acquire admission, pin the
  configured existing repository, enumerate one tagged snapshot and complete its bounded read-only
  integrity operation in the observed host state. Repository/source contents and external state
  remain rollback-safe because no mutation command ran. Runner loading, backup, retention, restore
  and the remaining `required=true` policy transition are still separately gated.

## E30.24 — Hourly-only runner reload and one RunAtLoad result

- Classification: **measured** for the safe preflight classifications, exactly one RunAtLoad,
  stable result code, bounded durations, cleanup label projections and secret-free post-state;
  **unmeasurable** for the cause beneath the stable repository error, whether coverage or the
  existing snapshot advanced, and the current process-active state after the wrapper returned.
  No successful recovery run is claimed.
- UTC time and environment: the bounded actions ran during the active E30.24 lease on
  `2026-08-22`, on the native macOS host using exact Candidate 18 and its reviewed lexical
  `.venv/bin/python` recovery runtime. The safe projection did not retain exact private-state or
  operation timestamps; none are reported.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `3c03180f0e2217279455485f2a656958d42b92f3022a5c7cec873ec95f33a995`.
- Attempt classification: this agent's earlier execution-approval envelope was rejected before
  process creation and performed no action; it is not an operational attempt and produced no
  evidence entry. Agent 1's first direct preflight envelope reached exact-label cleanup but aborted
  before runner load because its broad process projection exceeded its output bound. That envelope
  completed in **0.067 seconds**, measured both exact label booleans as `false` at cleanup, and did
  not consume a RunAtLoad. The corrected PID-only preflight was therefore not a job retry; exactly
  one hourly RunAtLoad was installed, loaded and observed afterward.
- Operational method: one corrected native safe wrapper used a bounded PID-only projection, then
  Candidate 18's `.venv/bin/python -m api.recovery runner install --load` path for only the hourly
  runner. It captured/discarded raw launchd, process and recovery output and emitted only bounded
  safe fields. The wrapper observed the one RunAtLoad through completion, classified its stable
  result, booted out hourly on failure, booted out the exact retention label, and immediately
  projected only the two loaded booleans. There was no second load, kickstart or job retry.
- Operational result: corrected preflight measured `preflight_process_active=false`. Exactly one
  RunAtLoad completed and was observed inactive inside the escalated wrapper before classification.
  Its stable result was `recovery_repository_error`; the corrected wrapper's total measured duration
  was **0.848 seconds**. The cause within the reviewed recovery path is **unmeasurable** from the
  stable redaction. On failure the wrapper booted out hourly and retention; its immediate measured
  final projections were `hourly_loaded=false` and `retention_loaded=false`.
- Post-state method and result: one subsequent read-only secret-free state projection measured
  `artifact_bytes=43,161,199`, `coverage_present=true`, `snapshot_present=true`,
  `last_result_code=recovery_repository_error`, `retention_configured=false`,
  `retention_enforced=false`, and `retention_schedule_armed=false`. Presence does not establish
  advancement: exact coverage/snapshot times were neither emitted nor retained, so coverage
  advancement, snapshot advancement and whether this failed run created a new point are
  **unmeasurable**.
- Process-state limitation: a later sandboxed `pgrep` query could not access the process list and
  returned `sysmond service not found`. Its shell fallback boolean is invalid and is not reported.
  Thus current post-wrapper process-active state is **unmeasurable** from that later query. This does
  not alter the wrapper's measured observation that the one completed job was inactive before it
  classified the stable failure and unloaded both exact labels.
- Provider/data-boundary and cost result: **pass; $0/month delta**. The runner used only the reviewed
  local recovery boundary; no secret, path, device/repository/snapshot identifier, exact private
  timestamp/hash, private row/snapshot count, private payload or raw command output was emitted. No retention install/
  apply/forget/prune/delete, restore, repository initialization, filesystem repair/erase,
  `required=true` transition, provider, AI, cloud or network action occurred. No code, test,
  contract, configuration or runbook changed. External changes are limited to installing the hourly
  plist, its one failed RunAtLoad, the allowed secret-free recovery-state result update, and the
  measured final unloaded state for both exact labels.
- Validity, rollback and next gate: this one host-local observation establishes runner wiring and
  fail-closed cleanup, but fails the success threshold because the RunAtLoad returned
  `recovery_repository_error` and coverage advancement was not measured. Both labels were measured
  unloaded immediately after cleanup; no claim is made about a later label or process state. The
  installed hourly plist is inert while unloaded. A separately authorized bounded stage diagnosis
  is required to localize the repository error; E30.24 is exhausted without retry. Retention,
  restore and `required=true` remain separately gated.

## E30.25 — Hourly minimal-environment read-only stage diagnosis

- Classification: **measured** for exact-label cleanup/projections, process absence, every stage
  boolean and duration, unchanged-content/state booleans, null error and total duration;
  **unmeasurable** for E30.24's underlying failure cause because no deterministic failing stage was
  reproduced. Transient local dependency state is only a possible inference, not a measured cause.
- UTC time and environment: one no-warm-up direct Agent 1 diagnostic ran during the active E30.25
  lease on `2026-08-22`, on the native macOS host with exact Candidate 18. The child reproduced the
  hourly runner environment: `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, no additional parent environment,
  workspace root as cwd, and the lexical `.venv/bin/python` interpreter. The bounded safe output did
  not retain an exact operation timestamp.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `b04f6b17f59cde8c9e8b727a6edb3e3b4f7f11c7c2c0b5aee35800844db1ceb8`.
- Safety preflight: bounded bootout of the exact hourly and retention labels passed in **0.008
  seconds** and **0.004 seconds**, respectively. Immediate exact-label projections passed in
  **0.004 seconds** each and measured `hourly_loaded=false` and `retention_loaded=false`. The
  bounded exact-process preflight passed in **0.026 seconds** and measured the recovery process
  inactive. No runner was installed, loaded or kicked.
- Read-only method: one bounded child loaded settings and secret-free state, validated the pinned
  tool, acquired one nonblocking recovery lock, entered one descriptor-bound `pin_repository()`
  context, built the credential/raw-cache-excluding logical bundle, read the latest encrypted
  bundle, validated its manifest, compared only the safe content digest, read tagged snapshot
  inventory without emitting its contents/count, ran repository `check(pin=...)`, and rechecked
  topology. It stopped before any backup decision. The existing credential was consumed only by the
  reviewed isolated broker; raw source, bundle, repository, topology and credential output was
  captured/discarded.
- Stage result: settings passed in **0.003 seconds**; state read passed in **0.000 seconds**; tool
  validation passed in **0.082 seconds**; lock passed in **0.000 seconds**; repository pin passed in
  **2.320 seconds**; logical bundle passed in **4.118 seconds**; latest bundle passed in **1.309
  seconds**; manifest validation passed in **0.784 seconds**; snapshot inventory passed in **1.258
  seconds**; repository check passed in **1.492 seconds**; topology recheck passed in **0.884
  seconds**. The safe comparison measured `content_unchanged=true`; the final state projection
  measured `state_unchanged=true`; `error=null`; result `diagnostic_ok`. Total measured duration was
  **12.336 seconds**.
- Provider/data-boundary and cost result: **pass; $0/month delta**. Source rows were read only by the
  allowlisted logical-bundle builder; Keychain and the existing encrypted local repository were read
  only through reviewed boundaries. No secret, path, device/repository/snapshot identifier, private
  row/snapshot count/content, exact private timestamp/hash or raw command output was emitted. No
  snapshot/repository/state/source mutation, backup, initialization, raw dump-output emission, restore,
  retention action, runner load, filesystem repair/erase, `required=true` transition, provider,
  AI, cloud or network action occurred. Schema, API, configuration, dependency, hosted/public data
  and recurring-cost deltas are none; the only current write is this evidence entry.
- Validity, rollback and next gate: the complete read-only path passed under the reproduced hourly
  environment, establishing that no deterministic settings, tool, lock, pin, logical-bundle,
  latest-bundle, manifest, unchanged-content, inventory, check or topology stage failure was present
  during this observation. It does not explain E30.24's earlier stable
  `recovery_repository_error`; transient local dependency state is possible but not proven. Both
  labels were measured unloaded and diagnostic state remained unchanged. A single hourly retry
  requires a separate operator authorization if the operator chooses; E30.25 is exhausted without
  retry, while retention, restore and `required=true` remain separately gated.

## E30.26 — One hourly RunAtLoad retry

- Classification: **measured** for the exact-process preflight, one settled RunAtLoad, stable result
  code, total duration, failure cleanup label projections and secret-free post-state; **derived
  strong evidence** for a deterministic launchd-context-specific failure relative to E30.25's
  passing direct minimal-environment stages; and **unmeasurable** for the exact failing stage/cause,
  coverage or snapshot advancement, and whether the failed run created a snapshot.
- UTC time and environment: one no-warm-up direct Agent 1 retry ran during the active E30.26 lease
  on `2026-08-22`, on the native macOS host using exact Candidate 18 and its reviewed lexical
  `.venv/bin/python` RunAtLoad configuration. The bounded safe output did not retain exact private
  state or operation timestamps.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `02906503006e62d52e36ff021b0555eaa891f5aa9381bf2ebc6aae12a433cae3`.
- Attempt classification: this agent's delegated execution approval was rejected before process
  creation and performed no action; it is not the operational attempt and consumed no retry. Agent
  1 then ran the direct corrected wrapper exactly once. There was no second load, kickstart or
  RunAtLoad retry.
- Method: the corrected native wrapper performed a bounded exact-process preflight, installed and
  loaded only the hourly Candidate 18 runner, and observed exactly one RunAtLoad through settlement.
  Raw launchd, process and recovery output was captured/discarded; only bounded safe fields were
  emitted. The wrapper accepted success only after a success result, coverage advancement/new
  evidence-run relation and inactive settled job; otherwise it booted out hourly and retention and
  immediately projected only their loaded booleans.
- Result: `preflight_process_active=false`. Exactly one hourly RunAtLoad retry completed, was
  observed inactive and returned stable `recovery_repository_error`. Total measured wrapper
  duration was **0.791 seconds**. Failure cleanup immediately measured `hourly_loaded=false` and
  `retention_loaded=false`. No second retry occurred.
- Post-state result: one read-only secret-free projection measured
  `artifact_bytes=43,161,199`, `coverage_present=true`, `snapshot_present=true`,
  `last_result_code=recovery_repository_error`, `retention_configured=false`,
  `retention_enforced=false`, and `retention_schedule_armed=false`. Because the failure wrapper did
  not measure the exact before/after relations, coverage advancement, snapshot advancement and
  whether a snapshot was created are **unmeasurable**; presence alone is not advancement evidence.
- Causal interpretation: E30.24 and E30.26 independently measured the same immediate stable failure
  for one settled RunAtLoad, while E30.25 measured every direct read-only stage passing under the
  reproduced minimal environment. That repeat is strong evidence of a deterministic
  launchd-context-specific failure boundary. It does not localize the failing stage or prove a
  specific environment, credential, filesystem or process-lifecycle cause; those remain
  **unmeasurable**, and no automatic code fix is justified from this evidence.
- Provider/data-boundary and cost result: **pass; $0/month delta**. No secret, path,
  device/repository/snapshot identifier, private row/snapshot count/content, exact private
  timestamp/hash or raw command output was emitted. No retention install/apply/forget/prune/delete,
  restore, repository initialization/repair/erase, `required=true` transition, provider, AI, cloud
  or network action occurred. No code, test, contract, configuration or runbook changed. External
  changes are limited to the allowed hourly load, one failed RunAtLoad, allowed secret-free
  recovery-state handling and measured fail-closed cleanup to both labels unloaded.
- Validity, rollback and next gate: the retry again fails the hourly success threshold and confirms
  fail-closed cleanup. Both exact labels were measured unloaded immediately after the wrapper; no
  claim is made about later label/process state. E30.26 is exhausted without a second retry. The
  next human gate is either one separately authorized bounded launchd-context diagnostic that runs
  no backup, or stopping Phase 30 with hourly automation unresolved. Retention, restore and
  `required=true` remain separately gated.

## E30.27 — Temporary launchd-context read-only diagnosis and E30.26 correction

- Classification: **measured** for the temporary LaunchAgent's safe preflight, read-stage booleans
  and durations, unchanged-content/state results, driver/child durations, cleanup and final label
  projections; **code inspection** for the E30.26 observer control-flow defect; and
  **unmeasurable** for E30.24's underlying failure cause. E30.26's claimed second completed
  RunAtLoad and deterministic launchd-failure inference are withdrawn by this append-only
  correction.
- UTC time and environment: one no-warm-up temporary user LaunchAgent diagnostic ran during the
  active E30.27 lease on `2026-08-22`, on the native macOS host using exact Candidate 18. Its child
  used only the configured `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, workspace root as cwd and the
  lexical `.venv/bin/python` interpreter under actual user-launchd execution. The bounded safe
  output did not retain exact private-state or operation timestamps.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `37b5f6a1c0b012fa21589fb2dc8b9a742d4d91329098a33c5353e298b6101861`.
- Temporary method: the authorized driver created only the absent uniquely labelled diagnostic
  user-LaunchAgent plist and one secret-free result file, loaded the diagnostic label once, observed
  one bounded run, booted it out, and removed only those exact created artifacts after identity
  verification. The diagnostic executed only E30.25's read stages: exact production-label cleanup/
  projections, exact recovery-process preflight, settings/state reads, pinned-tool validation,
  recovery lock, repository pin, logical bundle, latest bundle, manifest validation/safe-content
  comparison, tagged inventory, repository check and topology recheck. It did not call backup or
  save recovery state.
- Preflight and stage result: hourly bootout passed in **0.037 seconds**; retention bootout passed in
  **0.034 seconds**; hourly and retention projections passed in **0.031** and **0.034 seconds** and
  measured both unloaded; exact-process preflight passed inactive in **0.121 seconds**. Settings
  passed in **0.013 seconds**; state read in **0.001 seconds**; tool in **0.289 seconds**; lock in
  **0.001 seconds**; repository pin in **13.675 seconds**; logical bundle in **26.834 seconds**;
  latest bundle in **7.123 seconds**; manifest validation in **5.203 seconds**; inventory in **6.115
  seconds**; repository check in **6.978 seconds**; topology recheck in **5.706 seconds**.
- Diagnostic result: `diagnostic_ok`, `error=null`, `content_unchanged=true`, and
  `state_unchanged=true`. Child duration was **72.597 seconds**; full driver duration was **74.810
  seconds** with `driver_error=null`. Thus every reviewed read stage passed in the actual temporary
  user-launchd context as well as in E30.25's direct reproduction.
- Cleanup result: `diagnostic_unloaded=true`, `plist_removed=true`, and `result_removed=true` after
  verified cleanup. Final immediate production projections measured `hourly_loaded=false` and
  `retention_loaded=false`. No temporary diagnostic plist/result remains, and no production runner
  was loaded or retried.
- E30.26 code-inspection correction: `/private/tmp/phase30_hourly_once.py` initializes its observed
  `final` state from `prior`. Its success branch requires a changed `evidence_run_id`, advanced
  coverage and an inactive process, but its failure branch raises whenever the process projection
  is false and `final.last_result_code` is not a success code. Because the prior state already held
  `recovery_repository_error`, that failure branch could execute on the first momentarily inactive
  poll without a changed evidence run, advanced coverage or prior observation of the new job as
  active. Therefore E30.26 does **not** prove that a second RunAtLoad started or completed, and its
  deterministic launchd-context-failure inference is withdrawn. E30.24 remains one measured actual
  failure because its prior state did not already contain that error; E30.25 and E30.27 demonstrate
  that all reviewed read stages pass both directly and under an actual temporary LaunchAgent.
- Provider/data-boundary and cost result: **pass; $0/month delta**. Source rows, Keychain and the
  existing encrypted local repository were read only through reviewed boundaries. No secret, path,
  device/repository/snapshot identifier, private row/snapshot count/content, exact private
  timestamp/hash or raw command output was emitted. No backup, save-state, snapshot/repository/
  state/source mutation, production-runner load/retry, retention action, restore, initialization,
  filesystem repair/erase, `required=true` transition, provider, AI, cloud or network action
  occurred. Schema, API, configuration, dependency, hosted/public data and recurring-cost deltas
  are none; the only current write is this evidence entry.
- Validity, rollback and next gate: E30.27 proves the Candidate 18 read-only stages can complete in
  an actual temporary user-launchd context, but does not localize or reproduce E30.24's one measured
  failure. That underlying cause remains **unmeasurable**; no automatic code fix is supported. The
  system is rollback-safe with the temporary artifacts removed and both production labels measured
  unloaded. The next gate is a separately authorized single corrected hourly observation that
  requires positive start proof—an observed active transition or new evidence state—before
  interpreting its result. Retention, restore and `required=true` remain separately gated.

## E30.28 — Corrected positive-start hourly observation

- Classification: **measured** for the exact-process preflight, two independent fresh-start proof
  channels, one settled hourly result, coverage/snapshot relations, artifact aggregate, total
  duration and immediate final label projections. Active-process transition is **unmeasurable**
  because the job completed between process samples; no active transition is claimed.
- UTC time and environment: one no-warm-up direct Agent 1 observation ran during the active E30.28
  lease on `2026-08-22`, on the native macOS host using exact Candidate 18 and the reviewed hourly
  user-LaunchAgent with its lexical `.venv/bin/python` runtime. The safe result retained relational
  state only and did not emit exact private-state or operation timestamps.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `8f7faff14f0d179b55490b9bb84e825556ae7a56f27cea6040433894ef7bf4ec`.
- Corrected method: the bounded wrapper captured prior secret-free logical state plus state-file
  identity, booted out the exact hourly and retention labels, proved no exact recovery process,
  installed/loaded only hourly and observed launchd/state/process projections within the accepted
  bound. It did not interpret the prior result code after bootstrap. A result became eligible for
  interpretation only after positive fresh-start proof and a settled inactive job. Success further
  required a new evidence-run identifier, coverage advancement and result
  `recovery_ok|recovery_unchanged`; otherwise the wrapper would unload hourly. Raw launchd, process,
  source, credential and repository output was captured/discarded.
- Fresh-start proof: preflight measured `preflight_process_active=false`. After bootstrap, launchd
  measured `runs=1` and the state-file identity was replaced, providing two positive proof channels
  that a new RunAtLoad occurred. The active-process sampler measured `false`; the job could complete
  between samples, so this value is not evidence that no job ran and no active transition is
  claimed. Completion was interpreted only after the positively proven job was settled inactive.
- Result: the one corrected hourly RunAtLoad exited successfully with
  `result_code=recovery_unchanged`, `error=null`, and total measured duration **69.103 seconds**. It
  produced a new evidence-run state and measured `coverage_advanced=true`,
  `snapshot_advanced=false`, with `artifact_bytes=43,161,199`. The unchanged-content branch verified
  the prior encrypted recovery point and advanced coverage without creating a new snapshot.
- Final disposition: immediate exact-label projections measured `hourly_loaded=true` and
  `retention_loaded=false`. Retention bootout before and after the run each reported the exact
  already-unloaded class, so neither removed a loaded retention service. Hourly remains loaded only
  because the corrected success conditions all passed. This is the first corrected successful
  hourly RunAtLoad observation after E30.27; there was no second run or retry.
- Provider/data-boundary and cost result: **pass; $0/month delta**. Source rows, Keychain and the
  existing encrypted local repository were accessed only through reviewed local recovery
  boundaries. No secret, path, device/repository/snapshot identifier, private row/snapshot
  count/content, exact private timestamp/hash or raw command output was emitted. No new snapshot,
  retention install/apply/forget/prune/delete, restore, initialization, filesystem repair/erase,
  `required=true` transition, provider, AI, cloud or network action occurred. No code, test,
  contract, configuration or runbook changed. External changes are the allowed hourly load, one
  successful unchanged-content RunAtLoad, its secret-free recovery-state replacement and the
  measured final hourly-loaded/retention-unloaded disposition.
- Validity, rollback and next gate: E30.28 corrects the stale-error observer defect identified in
  E30.27 and meets the hourly success threshold with positive start proof, new evidence state,
  advanced coverage, an unchanged verified point and settled success. The intended hourly service
  remains loaded; retention remains unloaded and no retention action ran. Rollback remains bounded
  to booting out the exact hourly label. Retention, restore and the `required=true` policy transition
  remain separately gated.

## E30.29 — One retention dry-run and safe-projection limitation

- Classification: **measured** for the exact-label/process preflight, command exit class, bounded
  duration, wrapper result, post-state relations and immediate final label projections. The
  underlying CLI stable error and survivor-plan safety fields are **unmeasurable** because the safe
  projector rejected and discarded the non-success envelope; no application cause or successful
  dry-run is inferred.
- UTC time and environment: one no-warm-up native-host attempt ran during the active E30.29 lease on
  `2026-08-23` UTC using exact Candidate 18 and its lexical `.venv/bin/python`. The observation
  emitted only allowlisted booleans, a stable wrapper code and duration; it retained no exact
  private-state or operation timestamp.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `0fe4d1f67ae3ad6418edbac9af8487d234e5a850d663bc81c1fe58e807e48910`.
- Preflight: bounded exact-label and PID-only projections measured `hourly_loaded=true`,
  `retention_loaded=false` and `recovery_process_active=false`. The preflight left the hourly label
  loaded and did not install, load or alter either service.
- Exact method and bound: `.venv/bin/python -m api.recovery retention --dry-run --json` was invoked
  exactly once through the implemented shared-lock path. An outer bounded subprocess runner used a
  **180-second** deadline and **65,536-byte** output ceiling. Raw stdout/stderr and launchd/process
  output were captured and discarded; the wrapper attempted to retain only the reviewed safe
  result booleans and stable result code.
- Result: the command exited nonzero after **8.795 seconds**. Its safe wrapper returned
  `result_code=invalid_safe_envelope`. This is the wrapper's classification of an unexpected
  non-success JSON shape, not the application's stable error: the projector discarded that
  envelope, so the underlying CLI error and the exact stage reached are unmeasurable. There was no
  retry and no causal inference. Nonzero-survivor, survivor-plan-safety and current-drill-point
  relations are likewise unmeasurable because no plan-safe output survived projection.
- State and final disposition: the post-attempt read measured retention
  `configured=false`, `enforced=false`, `pending=false` and `schedule_armed=false`; the prior
  secret-free result remained unchanged as `recovery_unchanged`. Immediate final projections
  measured `hourly_loaded=true` and `retention_loaded=false`. Thus no retention configuration or
  enforcement transition was measured, and the hourly schedule remained loaded as required.
- Provider/data-boundary and cost result: **pass; $0/month delta**. The exact credential/repository
  read stage reached before the discarded error is unmeasurable; no raw credential, secret, path,
  device/repository/snapshot identifier, private count/content, exact private timestamp/hash or
  command output was emitted. No retention apply/forget/prune/delete/install, backup, restore,
  initialization, filesystem action, runner retry, `required=true` transition, provider, AI, cloud
  or network action occurred. No code, test, contract, configuration or runbook changed; the only
  repository write is this evidence entry.
- Validity, rollback and next gate: E30.29 exhausts the authorized single dry-run attempt but does
  not meet the retention dry-run acceptance threshold because its safe result was not measurable.
  No apply or deletion occurred, hourly remains measured loaded and retention measured unloaded, so
  rollback requires no action. If the operator chooses to continue, the next gate is a separately
  authorized single dry-run using a projector that safely preserves the reviewed non-success error
  envelope; no automatic retry is authorized.

## E30.30 — Corrected retention dry-run envelope

- Classification: **measured** for immutable identity, exact-label/PID preflight, the single command
  exit class and duration, exact safe error envelope, before/after secret-free state, final
  exact-label/PID projections and absence of a retry. The failing internal stage, error message,
  survivor-plan safety and current-drill-point survival are **unmeasurable** because the projector
  intentionally discarded the message and retained no repository identifiers or plan contents.
- UTC time and environment: one no-warm-up native macOS attempt ran during the active E30.30 lease
  on `2026-08-23` UTC using exact Candidate 18, the workspace lexical `.venv/bin/python`, workspace
  root as current directory and the fixed `PATH=/usr/bin:/bin:/usr/sbin:/sbin` child environment.
  No exact private-state or operation timestamp was retained.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `8c96b76f5fa2c250c7f2696ad896230035dff0da3e0eed962602f0cd6dcdbd05`.
- Preflight method and result: bounded `/bin/launchctl list <exact-label>` projections took
  **0.009 seconds** for hourly and **0.004 seconds** for retention; a bounded PID-only exact recovery
  command projection took **0.018 seconds**. They measured `hourly_loaded=true`,
  `retention_loaded=false` and `recovery_process_active=false`, so the authorized command was
  eligible to start without unloading or reloading hourly.
- Exact command, bounds and projector: `.venv/bin/python -m api.recovery retention --dry-run
  --json` was invoked exactly once through its implemented shared-lock path. The outer runner used a
  **180-second** deadline and **65,536-byte** output ceiling. Return code zero was accepted only for
  exact keys `{result_code,dry_run,applied,enforced}` with
  `recovery_retention_dry_run/true/false/false`; return code one was accepted only for exact keys
  `{code,message}`, with a reviewed stable code and string message. For the error branch only
  `code` was retained; the message, raw stdout/stderr and launchd/process output were captured and
  discarded.
- Result: the command returned exit class **one** after **9.084 seconds** with the exact accepted
  error-envelope projection `code=recovery_retention_invalid`; total preflight, command and final
  projection time was **9.160 seconds**. This stable code is measured, but its message and exact
  failing stage were not retained, so no cause is inferred. The success envelope was not produced;
  the survivor set, nonzero-survivor condition and current-drill-point survival are therefore
  unmeasurable rather than passed. There was no retry.
- State relation and confounder: immediately before and after the command, retention measured
  `configured=false`, `enforced=false`, `pending=false` and `schedule_armed=false`; the secret-free
  state result was `recovery_repository_error` both times. Relative to E30.29's committed
  `recovery_unchanged` post-state, that result-code drift was already present before this command;
  its cause and exact occurrence time are unmeasurable. The equal before/after projections show
  that this dry-run did not produce a measured retention-state or result-code transition.
- Final disposition: immediate post-command projections measured `hourly_loaded=true`,
  `retention_loaded=false` and `recovery_process_active=false`. Their durations were **0.009**,
  **0.006** and **0.024 seconds**, respectively. Hourly was neither unloaded nor reloaded;
  retention was neither installed nor loaded.
- Provider/data-boundary and cost result: **pass; $0/month delta**. The exact credential/repository
  stage reached is unmeasurable; any access was confined to the reviewed local credential broker,
  pinned tool and encrypted repository boundary. No credential, message, path,
  device/repository/snapshot identifier, private count/content, exact private timestamp/hash or raw
  command output was emitted. No retention apply/forget/prune/delete/install/load, backup, restore,
  initialization, filesystem action, source-row mutation, runner retry, `required=true` transition,
  provider, AI, cloud or network action occurred. No code, test, contract, configuration or runbook
  changed; the only repository write is this evidence entry.
- Validity, rollback and next gate: E30.30 corrects E30.29's projection defect and measures the
  stable application error, but it does **not** meet the retention dry-run acceptance threshold.
  With no apply/deletion, unchanged retention state, hourly loaded, retention unloaded and no active
  recovery process, rollback requires no action. The unresolved next gate is either to stop Phase
  30 retention work or separately authorize one bounded read-only stage diagnosis that returns only
  the first failing stage and stable code; no automatic dry-run retry, destructive retention or
  restore is authorized.

## E30.31 — Read-only retention first-failing-stage diagnosis

- Classification: **measured** for immutable identity, wrapper identity, exact-label/PID preflight,
  production-stage ordering and durations, the first failing stage, stable error code, secret-free
  state comparisons, total duration and final exact-label/PID projections. The exact rejected
  retention-plan property is **unmeasurable** because the raw plan and error message were captured
  and discarded; no parser branch or remediation is inferred.
- UTC time and environment: one no-warm-up native macOS diagnosis ran during the active E30.31 lease
  on `2026-08-23` UTC using exact Candidate 18, its lexical `.venv/bin/python`, workspace root as
  current directory and a fixed `PATH=/usr/bin:/bin:/usr/sbin:/sbin` child environment. No exact
  private-state or operation timestamp was retained.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `e26843b73f1a6951b77444cbcb0f408789e7c9da4f7bc93998851f7fcba1f54b`.
- Diagnostic artifact: the reviewed non-secret temporary wrapper
  `/private/tmp/phase30_e30_31_retention_stage_diagnosis.py` had SHA-256
  `e3cf02f6acd4842e8a913124ad16c4c6638af64da2868afdf9b4b7232b146804`. Static review proved it
  imported no `apply_retention` or state-save/backup/restore helper, used `apply=False` only, emitted
  the allowlisted safe projection and was valid Python syntax. It was executed once inside a
  **240-second**, **4,096-byte** outer bound and removed after execution and hash verification.
- Preflight: bounded exact-label projections and a PID-only exact recovery-command projection
  measured `hourly_loaded=true`, `retention_loaded=false` and
  `recovery_process_active=false` in **0.010**, **0.005** and **0.019 seconds**, respectively. The
  diagnosis therefore entered one nonblocking shared recovery lock without unloading or reloading
  either service.
- Exact read-only method: under that one lock, the wrapper called existing Candidate 18 production
  primitives once in this order and stopped at the first failure: `validate_tool`;
  `pin_repository`; pinned `repository_id` comparison; `snapshots` plus `_snapshot_ids`;
  `_current_snapshot_id`; `latest_bundle(expect_snapshot=True)`; `_bundle_manifest`;
  `repo.retention(apply=False)`; `_parse_retention_dry_run`; and, only after a successful parse,
  current-snapshot membership in the survivor set. It never invoked `apply_retention`, the CLI
  retention command, `save_state`, backup, restore or a destructive adapter call. Repository,
  credential, bundle, plan and subprocess output was retained only in process and discarded.
- Stage result: `validate_tool` passed in **0.063 seconds**; `pin_repository` in **2.893**;
  `repository_identity` in **1.313**; `snapshot_inventory` in **1.276**;
  `current_snapshot` in **0.000**; `latest_bundle` in **1.505**; `bundle_manifest` in **0.847**; and
  `retention_dry_run` in **1.226 seconds**. The first failure was
  `retention_plan_parse` in **0.000 seconds**, with stable code
  `recovery_retention_invalid`. `current_snapshot_survival` was not invoked. Total preflight,
  locked diagnosis and final projection time was **9.289 seconds**. There was no retry.
- Interpretation and limitation: the measured stage boundary isolates E30.30's error to Candidate
  18 `_parse_retention_dry_run` rejecting the completed Restic dry-run result. The exact rejected
  invariant—envelope shape, allowed keys, tag/disposition structure, identifier coverage or another
  parser condition—is unmeasurable because neither the private plan nor the error message was
  retained. Repository identity, inventory, current-point selection, encrypted bundle read and
  manifest validation all completed before this failure; survivor membership was not evaluated.
- No-change projection: `state_unchanged=true` was an equality comparison of the complete parsed
  secret-free recovery state before and after; `content_unchanged=true` was an independent exact
  byte-content comparison of that same state file without emitting its bytes or digest. These
  booleans prove no recovery-state mutation by the diagnosis; they are not a source-database or
  encrypted-repository byte-for-byte claim. No source or repository content hash was emitted.
- Final disposition: immediate post-diagnosis projections measured `hourly_loaded=true`,
  `retention_loaded=false` and `recovery_process_active=false` in **0.009**, **0.007** and **0.025
  seconds**, respectively. Hourly remained loaded and idle; retention remained unloaded.
- Provider/data-boundary and cost result: **pass; $0/month delta**. Existing credential and private
  repository data were accessed only through the reviewed automation broker, pinned tool and
  encrypted repository boundary. No credential, message, path from private configuration,
  device/repository/snapshot identifier, snapshot/row count, private content, exact private
  timestamp/hash or raw output was emitted. No retention application, non-dry-run forget/prune,
  deletion, install/load/unload, backup, restore, initialization, filesystem action, state/source mutation,
  `required=true` transition, provider, AI, cloud or network action occurred. Schema, API,
  configuration, dependency, hosted/public data and recurring-cost deltas are none; the only
  repository write is this evidence entry.
- Validity, rollback and next gate: E30.31 meets its diagnostic objective by measuring the first
  failing stage without mutation, but the Phase 30 retention dry-run threshold remains **not met**.
  Rollback requires no action. The next gate is independent QA review of this frozen evidence,
  followed—only if accepted—by an offline synthetic-Restic reproduction of the dry-run JSON/parser
  mismatch and a separately leased candidate fix with regression tests. No further live dry-run,
  destructive retention or restore is authorized.

## E30.32 — Candidate 19 Restic retention-plan compatibility

- Classification: **measured** for the pinned-tool synthetic reproduction, redacted output schema,
  pre-fix rejection, post-fix acceptance, test counts and durations, Ruff/diff results and frozen
  file identities. The compatibility rule is **derived** from that measured schema plus Candidate
  18 parser control flow. No private/live behavior is claimed by the synthetic result.
- UTC time and environment: one cold pre-fix and one cold post-fix reproduction ran on `2026-08-23`
  UTC on the native macOS development host, using the installed pinned Restic `0.19.1` Darwin ARM64
  binary and lexical Python 3.14 virtual environment. Each reproduction created a wholly synthetic
  temporary local repository, data file, password file and cache beneath `/private/tmp`, made no
  network call and removed the temporary contents on exit.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 18 sorted 33-path manifest SHA-256
  `e23b226d3c73d6d179ecb7b1018e28cbdf3b9a32ad6dfacd2463ca154f4941e6`; pre-entry evidence
  SHA-256 `7ac2f22906816f6b0fd442143f20d31f3f80b3723e701c005e9cf6f57d6026b1`.
- Synthetic method and artifact: the non-secret probe
  `/private/tmp/phase30_candidate19_restic_schema_probe.py`, SHA-256
  `20ac89e38405ab1fb9ad8d78245ea7ac934dfbed1c1827315ba87f114803c643`, verified the pinned binary
  SHA, initialized a disposable local repository, created one tagged synthetic snapshot, then ran
  the exact `forget --tag <fixed-recovery-tag> --keep-hourly 48 --keep-daily 30 --keep-monthly 12
  --prune --json --dry-run` transport. It retained only recursive JSON key/type schema and parser
  pass/fail; generated identifiers, paths, password, repository output and synthetic file contents
  were discarded. The probe and all temporary data were removed after the post-fix measurement.
- Measured mismatch: the pre-fix probe completed in **6.817 seconds** and returned stable parser code
  `recovery_retention_invalid`. Its redacted schema showed a list of policy groups with present
  `host`, `paths`, `tags`, `keep`, `remove` and `reasons` fields; `keep` was a list, while the empty
  `remove` disposition was JSON `null`. Candidate 18 treated every present disposition as list-only,
  so it rejected Restic `0.19.1` before survivor evaluation. No generated value was retained.
- Implementation: `api/services/recovery.py::_parse_retention_dry_run` now normalizes only a
  **present** JSON `null` keep/remove disposition to an empty list. Both disposition fields remain
  mandatory, unknown group fields and non-list/non-null values fail closed, and all existing
  invariants remain: every row has an exact recovery tag and inventory identifier, each identifier
  appears exactly once across keep/remove, the partition covers the exact inventory, and the keep
  set is nonempty. The unchanged caller still requires the selected current snapshot in that keep
  set before any state change or separately approved destructive call.
- Post-fix compatibility: the same synthetic method ran once after the change and completed in
  **5.707 seconds** with no parser error. This measures compatibility with the reproduced Restic
  `0.19.1` null-empty disposition shape only; it is not live-repository evidence and did not invoke
  application retention state or any real/private path.
- Offline tests: `.venv/bin/python -m pytest tests/test_recovery.py -k retention` returned
  **32 passed, 138 deselected in 2.50 seconds**. The new in-memory cases reproduce the redacted
  `0.19.1` group/null shape and reject unknown group fields, omitted keep/remove fields, malformed
  dispositions, duplicate identifiers, wrong tags, empty survivors and incomplete inventory
  partitions. `.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py` returned **185 passed in 134.13 seconds**.
- Full gates: `make test` returned **433 passed, one pre-existing Starlette/httpx deprecation warning
  in 169.21 seconds**. `.venv/bin/ruff check api/services/recovery.py tests/test_recovery.py
  tests/test_recovery_integration.py` and `.venv/bin/ruff check api tests` both returned
  `All checks passed!`; `git diff --check` returned exit zero. Tests were offline and used no
  private fixture, credential, repository, provider or network.
- Path/data boundary: `api/services/recovery.py` is shared recovery code; the change parses only
  pinned-tool JSON already inside the private-local adapter and adds no public/private mode switch.
  `tests/test_recovery.py` uses constructed synthetic strings and identifiers only. No raw Restic
  output, real member/provider fact, private path, snapshot/repository identifier, credential,
  prompt/report content or private hash/count entered source, tests, evidence or handoff.
- Deltas and cost: source/application schema, migration, API, configuration, dependency, provider,
  analytics, scheduling, custody and hosted/public-data deltas are **none**. AWS, external recurring
  and software-license cost delta is **$0/month**. No source/private state, Kapili, Keychain,
  production runner, live repository, provider, AI, cloud or network access occurred.
- Validity, rollback and remaining gates: Candidate 19 is rollback-safe because it changes one
  parser compatibility branch plus synthetic tests and evidence, with no data or external-state
  migration. Reverting restores Candidate 18's fail-closed live incompatibility. Independent QA and
  Cybersecurity review of the frozen candidate remain mandatory. A real dry-run, retention apply,
  post-retention break-glass restore and `required=true` policy transition are **untested and not
  authorized**; each remains a separate operator gate after review.

## E30.33 — Candidate 20 selected drill-point binding and stable parser errors

- Classification: **measured** for immutable input identities, offline test results and durations,
  Ruff/diff results and frozen file hashes; **derived** for the safety conclusion that a destructive
  adapter cannot be reached when the selected point is absent from either survivor plan, based on
  measured zero-apply call assertions plus the reviewed Candidate 20 control flow. No live/private
  retention behavior is claimed.
- UTC time and environment: offline work completed at `2026-08-23T05:25:28Z` on macOS 26.5 build
  25F71, arm64, with lexical Python 3.14.3. Tests used only constructed synthetic repositories,
  credentials, snapshot rows and parser inputs; no private fixture or external service was used.
- Identity: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 19 sorted 33-path manifest SHA-256
  `26ffc1d5b88578fed7ddee321c2ecaae2ea63ab34a6e8b95c86373cef2b7ce51`; pre-entry evidence
  SHA-256 `b148183b11064edb66c4ac2b7405675f32770acbf8e7006deeac071c59330f88`.
- Implementation: manual `retention --dry-run` and the exact `retention --enable --apply`
  transition now require `--drill-snapshot latest` or one full 64-hex identifier. The selector is
  normalized and resolved only against the exact tagged pinned inventory, never emitted or written
  to recovery state. The selected full identifier and current point must both survive the first
  plan; the selected identifier is included in the in-process plan digest, rebound unchanged
  against the second inventory, and must survive with the rebound current point before the
  destructive adapter. Hidden scheduled apply accepts no selector and uses its then-current point
  internally. The runbook records the exact approval syntax and explains both survivor obligations.
- Parser stability: `_parse_retention_dry_run` retains Candidate 19's strict present-null
  compatibility and exact partition/tag/inventory rules, while translating invalid UTF-8, JSON
  `ValueError` including oversized integers, and excessive-nesting `RecursionError` to stable
  `recovery_retention_invalid`. Direct and CLI tests retain only bounded stable envelopes and prove
  that synthetic raw input and path canaries are not reflected.
- Selector and apply tests: `.venv/bin/python -m pytest tests/test_recovery.py -k retention`
  returned **53 passed, 138 deselected in 2.27 seconds**. Synthetic cases cover `latest`, an exact
  older point, absent/empty/unsupported/short/non-hex selectors, missing exact point, ambiguous
  newest point, selected older-point removal, rebound plan drift, distinct selector-bound digests,
  hidden scheduled-current selection and CLI redaction. The selected-point removal case measured
  one read-only plan call and **zero** destructive adapter calls; rebound drift measured two
  read-only plan calls and **zero** destructive adapter calls.
- Scoped and full gates: `.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py` returned **205 passed in 130.94 seconds**. The first
  `make test` run returned **453 passed and one failure in 157.23 seconds** because the pre-existing
  escaped credential-descendant timing test observed its 1.2-second exit marker before its negative
  timing assertion. Its exact isolated reproducer immediately returned **one passed in 3.43
  seconds**; one full rerun then returned **454 passed with one pre-existing Starlette/httpx
  deprecation warning in 162.14 seconds**. No product change was made for that transient failure.
- Static gates: `.venv/bin/ruff check api/services/recovery.py api/recovery.py
  tests/test_recovery.py tests/test_recovery_integration.py` and `make lint` both returned `All
  checks passed!`; Python compile checks and `git diff --check` returned exit zero. Per-file
  `git diff --no-index --check /dev/null <owned-file>` commands conventionally return exit one for
  differing nonempty untracked files; empty diagnostic output proves no whitespace error.
- Provider/data boundary: shared recovery code handles only operator-private local encrypted
  repository metadata through existing adapters. Tests contain generated hexadecimal identifiers
  and synthetic canaries only. No credential, private path, device/repository/snapshot identifier,
  private content/count/hash, member/provider fact, prompt/report content or raw Restic output was
  written to source, tests, evidence or handoff. No Kapili, Keychain, source DB, launchd, provider,
  AI, cloud or network action occurred.
- Deltas, cost and rollback: source/application schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, custody and hosted/public-data deltas are **none**.
  The operator CLI and private runbook syntax change only; scheduled behavior remains internal.
  AWS, external recurring and software-license cost delta is **$0/month**. Candidate 20 is
  rollback-safe with no data or external-state migration; reverting restores Candidate 19's
  fail-closed manual selector gap and unstable hostile-parser exceptions.
- Validity and remaining gates: the offline Candidate 20 threshold passes. Independent QA and
  Cybersecurity review of the frozen candidate remain mandatory. A real dry-run, destructive
  retention apply, post-retention break-glass restore and `required=true` policy transition remain
  untested, separately operator-gated and unauthorized by this evidence.

## E30.34 — Candidate 20 live retention dry-run preflight stopped before action

- Classification: **measured** for immutable identities, manifest verification, the secret-free
  recovery-state booleans, the preflight failure and zero live-command invocations. Target
  availability, exact recovery-process inactivity, live selector binding, survivor safety and the
  retention state transition are **unmeasurable in this envelope** because the required process
  projection failed before doctor or retention execution. No live/private retention result is
  claimed.
- UTC time and environment: the bounded envelope ran on `2026-08-25` UTC in the workspace on the
  native macOS arm64 development host with the lexical Python 3.14 virtual environment. The wrapper
  and all subprocesses received the minimal environment containing only
  `PATH=/usr/bin:/bin:/usr/sbin:/sbin`; raw subprocess output was kept only in bounded memory and was
  never emitted or persisted.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 20 sorted 33-path manifest SHA-256
  `fd2f3aac7dddbeb813601319cb4dd4ba0d836838d715967cc30f76766615e60a`; pre-entry evidence
  SHA-256 `97f216c1141bec07a4f76d9af5ef1f6233f11e3905f3a8ed09ced54df3f5e5c1` over exactly **147,520
  bytes**. All **33/33** manifest entries matched before preflight, including the evidence entry;
  the other **32/32** entries matched independently.
- Pre-state: the secret-free state read measured `retention_configured=false`,
  `retention_enforced=false`, `retention_applied_pending_drill=false` and
  `retention_schedule_armed=false`. Coverage and snapshot timestamp values were retained only in
  process memory for a possible relational comparison and were never emitted. Because the
  envelope stopped at the next preflight stage, no post-state values or timestamp relations are
  claimed.
- Method and bounded result: the projector first verified immutable identities and state, then
  queried exact launchd labels with three-second per-call bounds and attempted a bounded PID-only
  projection for exact `api.recovery backup|retention` processes. The local execution sandbox
  denied the process-table read, so exact recovery-process inactivity could not be established.
  Per the lease's stop-at-first-preflight-failure rule, the projector stopped before the safe
  doctor projection and before the authorized `.venv/bin/python -m api.recovery retention
  --dry-run --drill-snapshot latest --json` action. The measured action invocation count is
  **zero**; therefore the action's 240-second deadline and 65,536-byte capture ceiling were never
  entered. The bounded wrapper returned in **0.4 seconds** measured tool wall time. No retry
  occurred.
- Safety and boundary: no target, repository or snapshot identifier, private timestamp/hash/count,
  path, credential, message, raw tool output, private content, provider fact, prompt/report content
  or process command line entered this evidence or handoff. No retention plan/application,
  forget/prune/delete, install/load/unload, backup, restore, source mutation, `required=true`
  transition, provider, AI, cloud or network action occurred. Target, selector and survivor safety
  remain unmeasurable rather than being inferred from offline code review.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, custody, hosted/public-data and recurring-cost
  deltas are **none** and **$0/month**. The attempt is rollback-safe because it performed only
  read-only identity/state/launchd checks before stopping. The E30.34 live dry-run acceptance
  threshold is **not met**: the command did not start, no accepted success/error envelope exists,
  and no post-success state relation was measured.
- Next gate: Agent 1 must close this failed-preflight evidence lease and obtain a fresh bounded
  envelope capable of the exact PID-only process projection before any one-shot dry-run. The
  destructive `--enable --apply`, post-retention break-glass restore and `required=true` transition
  remain separately operator-gated and unauthorized.

## E30.35 — Direct native preflight stopped before retention action

- Classification: **measured** for the immutable evidence prefix, temporary-wrapper identity and
  static checks, safe projected envelope, process exit, wrapper duration, command wall time and
  zero retention-command invocations supplied by Agent 1. The exact failing preflight subcheck,
  active recovery-process status, exact launchd-label states, target state and any Keychain or
  off-device-volume availability are **unmeasurable** because the safe envelope intentionally
  retained none of those details. No live/private repository result or state transition is
  claimed.
- UTC time and environment: Agent 1 ran one bounded direct native-preflight envelope on
  `2026-08-25` UTC on the native macOS arm64 development host. This entry records only Agent 1's
  supplied secret-free projection; the evidence writer did not execute or inspect the temporary
  wrapper.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 20 sorted 33-path manifest SHA-256
  `fd2f3aac7dddbeb813601319cb4dd4ba0d836838d715967cc30f76766615e60a`; pre-entry evidence
  SHA-256 `a282e59672225ad46ed803eaf5bd2c576358e8c03e23f06ab293a98aa2b0b917` over exactly **151,806
  bytes**. The temporary wrapper SHA-256 was
  `2ff7d32df1b9e3684960106e1b97255f5e4b9e1c304020ce06638a355151d239`; Ruff and Python compile
  checks passed before Agent 1 executed it.
- Safe projected result: the escalated native invocation returned process exit **2**. Its complete
  retained envelope was `command_invocations=0`, `duration_seconds=0.011`,
  `stable_code=wrapper_failed`, `stage=preflight` and `wrapper_status=failed`. The enclosing command
  measured **0.121 seconds** wall time. No message, raw output, command line, private timestamp,
  identifier, count, hash, content, credential or private path was retained.
- Interpretation and limitation: the wrapper failed somewhere in preflight before starting the
  authorized retention command. The safe projection does not identify the failing subcheck and
  does not establish whether an exact recovery process was active, whether either launchd label was
  loaded, whether the target was available or whether Keychain/repository access began. Those
  properties remain unmeasurable rather than inferred. The selector and survivor checks were not
  reached, so no target/selector/survivor safety pass is claimed.
- Safety and boundary: the measured retention-command invocation count is **zero**, so no live
  retention command was invoked. The safe projection evidenced no mutation or output. Whether
  Keychain or repository access began remains unmeasurable because the failing preflight subcheck
  is unknown. The evidence-only change crosses no public/private boundary and contains no private
  data.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, custody, hosted/public-data and recurring-cost
  deltas are **none** and **$0/month**. The evidence edit is rollback-safe; the projected attempt
  evidenced no mutation, but the unknown failing preflight subcheck prevents a stronger operational
  claim. The E30.35 live dry-run threshold is **not met**: no accepted success/error envelope exists
  and no required pre/post relation was measured.
- Next gate: close this no-action evidence lease. Any further preflight or one-shot dry-run needs a
  newly activated bounded lease; destructive `--enable --apply`, post-retention break-glass restore
  and `required=true` remain separately operator-gated and unauthorized.

## E30.36 — Direct native preflight subcheck diagnosis

- Classification: **measured** for the immutable evidence prefix, temporary-wrapper identity and
  static checks, safe projected subcheck classes, no-mutation relation, per-stage and total
  durations, enclosing-command wall time and zero retention-command invocations supplied by Agent
  1. Whether the retention launchd label was loaded or unloaded is **unmeasurable** because its raw
  result and return code were discarded after projection to `other_error`. Target availability is
  measured only for the topology/path-authority check; it is not repository-readability or
  credential-custody evidence.
- UTC time and environment: Agent 1 ran one bounded read-only native-preflight diagnosis on
  `2026-08-25` UTC on the native macOS arm64 development host. Each subcheck had a five-second and
  16-KiB bound. This entry records only Agent 1's supplied secret-free JSON; the evidence writer did
  not execute or inspect the temporary wrapper.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 20 sorted 33-path manifest SHA-256
  `fd2f3aac7dddbeb813601319cb4dd4ba0d836838d715967cc30f76766615e60a`; pre-entry evidence
  SHA-256 `23e18dc71efa0e397781ed5a3de430c55a459ec3f606fdba10bb0785e8f3a1f5` over exactly **155,561
  bytes**. The temporary wrapper SHA-256 was
  `b7256e5355a098b09dc32fcdc5b2799cf7cab0ad33738a5f830b2c201b23d8ea`; Ruff and Python compile
  checks passed before Agent 1 executed it.
- Safe projected result: the exact hourly-label subcheck returned class `loaded` in **0.007
  seconds**. The exact retention-label subcheck returned class `other_error` in **0.004 seconds**;
  because neither raw output nor return code survived projection, this does not establish loaded or
  unloaded state. The exact recovery-process projection returned class `inactive` in **0.025
  seconds**. The topology-only target projection returned `target_available=true` in **1.143
  seconds**. Total wrapper duration was **1.182 seconds**, and the enclosing escalated command
  measured **1.334 seconds** wall time.
- State and action relation: the safe projection returned `state_bytes_unchanged=true` and
  `command_invocations=0`. Thus the measured state-file bytes were relationally unchanged across
  the diagnosis and no retention command was invoked. No raw output, command line, private
  timestamp, identifier, count, hash, content, credential or private path was retained.
- Limitations and boundary: the result measures one bounded host observation only. Retention-label
  disposition remains unmeasurable, and topology-only target availability does not prove encrypted
  repository readability, selector binding, survivor safety or any credential property. No
  Keychain conclusion is drawn. No retention command or state mutation occurred; the evidence-only
  change crosses no public/private boundary and contains no private data.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, custody, hosted/public-data and recurring-cost
  deltas are **none** and **$0/month**. The diagnosis is rollback-safe because it was read-only and
  the state-file bytes were measured unchanged. The live dry-run acceptance threshold remains
  **not met** because the command was not invoked and retention-label disposition is unresolved.
- Next gate: close this diagnosis lease. Any further preflight must use a bounded exact-label
  mechanism that safely distinguishes loaded from unloaded before a new one-shot dry-run envelope.
  Destructive `--enable --apply`, post-retention break-glass restore and `required=true` remain
  separately operator-gated and unauthorized.

## E30.37 — Exact retention-label numeric return-code measurement

- Classification: **measured** for the immutable evidence prefix, temporary-wrapper identity and
  static checks, exact numeric return code, output-bounded boolean, state-byte relation, wrapper and
  enclosing-command durations and zero retention-command invocations supplied by Agent 1. The
  label's loaded/unloaded disposition remains **unmeasurable**: existing evidence requires either
  exit zero or the bounded exact missing-service output classification, while this measurement
  intentionally discarded stdout and stderr. No prior repository evidence maps numeric code 113
  alone to that semantic class.
- UTC time and environment: Agent 1 ran one bounded return-code-only native label query on
  `2026-08-25` UTC on the native macOS arm64 development host. This entry records only Agent 1's
  supplied secret-free projection; the evidence writer did not execute or inspect the temporary
  wrapper.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 20 sorted 33-path manifest SHA-256
  `fd2f3aac7dddbeb813601319cb4dd4ba0d836838d715967cc30f76766615e60a`; pre-entry evidence
  SHA-256 `6457c55de55f2a3f445eeaa5cd1d88be617fa0549e8656616613c41beec69b98` over exactly **159,434
  bytes**. The temporary wrapper SHA-256 was
  `eea66ea5ddf16d54786bbb931d8365e009a27f1752378a4a5b41e0cb67ea085f`; Ruff and Python compile
  checks passed before Agent 1 executed it.
- Safe projected result: the exact retention-label query returned numeric code **113**. Wrapper
  duration was **0.007 seconds**, and the enclosing escalated command measured **0.022 seconds**
  wall time. The projection returned `output_bounded=true`, `state_bytes_unchanged=true` and
  `command_invocations=0`; stdout and stderr were discarded.
- Interpretation and future method: code 113 is the exact measured host/runtime result to recognize
  in the next reviewed wrapper, but this evidence does not relabel it as missing-service or
  unloaded. That wrapper must retain a bounded boolean showing that code 113 accompanies launchd's
  exact missing-service classification, discard the raw output, and reject every other nonzero
  result before it may project `retention_loaded=false`.
- Safety and boundary: no live retention command was invoked, the recovery-state bytes were
  relationally unchanged, and no Keychain, repository or retention action occurred. No raw label
  output, command line, private timestamp, identifier, count, hash, content, credential or private
  path was retained. The evidence-only change crosses no public/private boundary and contains no
  private data.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, custody, hosted/public-data and recurring-cost
  deltas are **none** and **$0/month**. The diagnosis is rollback-safe because it was read-only and
  state bytes were measured unchanged. The live dry-run acceptance threshold remains **not met**
  because the retention command was not invoked and the exact label disposition remains unresolved.
- Next gate: close this return-code-only lease. A newly reviewed bounded wrapper may accept numeric
  code 113 only together with the exact missing-service classifier described above before the
  one-shot dry-run. Destructive `--enable --apply`, post-retention break-glass restore and
  `required=true` remain separately operator-gated and unauthorized.

## E30.38 — Successful Candidate 20 live retention dry-run

- Classification: **measured** for the immutable evidence prefix, temporary-wrapper identity and
  static checks, exact safe output, process exit, wrapper/action/enclosing durations, before/after
  label/process/target projections, timestamp relations and secret-free post-state booleans
  supplied by Agent 1. The conclusion that Candidate 20's selected-point and survivor gates passed
  before success is **derived** from the measured safe success projection plus the reviewed E30.33
  control flow; no repository or snapshot identifier was retained.
- UTC time and environment: Agent 1 ran the single already-authorized native dry-run on
  `2026-08-25` UTC on the native macOS arm64 development host. The exact action was
  `.venv/bin/python -m api.recovery retention --dry-run --drill-snapshot latest --json`. This entry
  records only Agent 1's supplied secret-free projection; the evidence writer did not execute or
  inspect the temporary wrapper and ran no external or live command.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 20 sorted 33-path manifest SHA-256
  `fd2f3aac7dddbeb813601319cb4dd4ba0d836838d715967cc30f76766615e60a`; pre-entry evidence
  SHA-256 `4c098c1ac44de9588d8f0f2b1c4b114a3576f754b70660e1a0d7255aa859a5c0` over exactly **163,054
  bytes**. The temporary wrapper SHA-256 was
  `2035e9753ec7c7f7d69064efec1b711135d2fe63b6fa0d5b7e33e44a26c18cb8`; Ruff and Python compile
  checks passed before Agent 1 executed it.
- Exact safe result: the wrapper returned `wrapper_status=complete`, `return_class=success`, stable
  code `recovery_retention_dry_run` and `command_invocations=1`; the process exited **0**. The action
  completed in **8.559 seconds**, total wrapper duration was **10.387 seconds**, and the enclosing
  command measured **10.531212125 seconds** wall time.
- Pre/post envelope: the exact hourly label measured loaded before and after; the exact retention
  label measured unloaded before and after; no exact recovery process was active before or after;
  and topology target availability measured true before and after. Coverage and snapshot timestamps
  were relationally unchanged; their values were not retained. The safe projection returned
  `state_transition_valid=true` and post-state
  `retention_configured=true`, `retention_enforced=false`,
  `retention_applied_pending_drill=false`, `retention_schedule_armed=false`.
- Retention safety: the measured result returned `dry_run_applied=false` and
  `selector_and_survivor_checks_passed=true`. In Candidate 20's reviewed flow, a successful dry-run
  can occur only after the exact tagged inventory resolves `latest`, both the current point and
  selected drill point are present in the verified nonempty survivor set, and the safe state update
  records configuration without applying retention. This is a control-flow-derived safety
  conclusion, not disclosure or independent recounting of private repository metadata.
- Provider/data boundary: the approved operator-private local dry-run read the Keychain-backed
  credential and encrypted repository metadata inside the bounded recovery adapter. No secret,
  credential value, raw tool output, repository/snapshot identifier, private count, private
  timestamp/hash, path, payload, report content or member/provider fact was retained in the wrapper
  projection, evidence or handoff. The exact local recovery path makes no provider or network call;
  none occurred. No apply, delete, prune, retention-runner install/load, backup, restore, source
  mutation or `required=true` transition occurred.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, custody, hosted/public-data and recurring-cost
  deltas are **none** and **$0/month**. The action is rollback-safe for source and repository data:
  it applied no retention and changed only secret-free local state from an unconfigured dry-run
  status to configured while leaving enforcement, pending and schedule arming false. The E30.38
  live dry-run threshold **passes**. The operator's single dry-run authorization is now
  **consumed**; no retry is permitted.
- Next gate: Agent 1 must freeze and independently review this evidence, then close the consumed
  lease. Destructive `--enable --apply`, post-retention break-glass restore and `required=true`
  remain separately operator-gated and unauthorized; dry-run success does not authorize them.

## E30.39 — Candidate 21 bounded retention service-control safety gate

- Classification: **measured** for the immutable evidence prefix, host/runtime, exact offline
  commands, pass counts, durations, lint/diff results and changed-file hashes. The conclusion that
  every `launchctl` invocation in this module receives the shared finite deadline/output bound is
  **derived** from the passing AST construction test plus the reviewed single-helper call graph.
  Live macOS service behavior after Candidate 21 is **unmeasurable in this entry** because the
  active lease prohibited `launchctl`, Keychain, repository and retention execution.
- UTC time and environment: `2026-08-26T23:41:41Z` on native macOS **26.5** build **25F71**, arm64,
  CPython **3.14.3** (Clang 16). Tests ran from the project virtual environment on the local host,
  without a container/cgroup limit. `sysctl -n hw.ncpu hw.memsize` was attempted and denied by the
  execution sandbox, so host CPU count and physical memory are **unmeasurable here**; they do not
  affect the deterministic timeout-state assertions.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 20 sorted 33-path manifest SHA-256
  `fd2f3aac7dddbeb813601319cb4dd4ba0d836838d715967cc30f76766615e60a`; pre-entry evidence
  SHA-256 `fe70ed69fbec3aaeda97c5053de2146837e099fd65935e3e89cadeee40c99e92` over exactly **167,721
  bytes**. The blocked destructive wrapper SHA-256
  `96613d3c88882f85ee69dba7d9c2ac6445cedbcd223c6bfb71d2078786bbe6e1` invoked the authorized
  live command **zero times** and was not executed by this writer; the operator authorization
  remains unconsumed.
- Question and method: determine whether every retention-enable service-control operation is
  bounded and whether timeout/error paths can reach destructive apply, state arming or unsafe
  cleanup. Candidate 21 routes all module `launchctl` calls through one no-shell helper with a
  **5.0-second** wall deadline and **4,096-byte** per-stream output bound. Deterministic fake runners
  inject: preflight timeout before apply; bootstrap timeout after a simulated late load; first
  rollback bootout timeout followed by bounded load detection and a second bootout; and lexical
  target replacement before rollback. The AST proof requires exactly one `/bin/launchctl` literal,
  one helper runner call, named positive deadline/output constants, timeout at most 30 seconds and
  output at most 64 KiB.
- Exact targeted command and result: `.venv/bin/python -m pytest
  tests/test_recovery.py::test_retention_bootout_timeout_still_verifies_and_cleans_exact_inode
  tests/test_recovery.py::test_retention_preflight_timeout_blocks_destructive_apply_and_arm
  tests/test_recovery.py::test_retention_bootstrap_timeout_late_load_is_bounded_and_rolled_back
  tests/test_recovery.py::test_retention_timeout_cleanup_preserves_replacement_inode
  tests/test_recovery.py::test_launchctl_service_control_has_static_finite_bounds` passed **5/5**
  tests in **0.37 seconds**.
- Exact scoped command and result: `.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_api.py tests/test_recovery_integration.py` passed **223/223** tests in
  **126.90 seconds** with one existing Starlette/httpx deprecation warning. Exact full-backend
  command `make test` passed **459/459** tests in **157.59 seconds** with the same warning. Exact
  lint command `.venv/bin/ruff check api tests` passed. Exact whitespace check `git diff --check --
  api/recovery.py tests/test_recovery.py tests/test_recovery_integration.py
  docs/evidence/phase-30-private-recovery.md` passed before this append; it is repeated at freeze.
- Result and failure behavior: a loaded-check timeout raises before `apply_retention`; bootstrap
  timeout/nonzero, inode change or failed post-load verification runs bounded bootout/verification
  before returning an error; an observed late load receives a second bounded bootout; and cleanup
  executes from `finally` even when service control errors. Cleanup removes only the installed
  `(st_dev, st_ino)` and preserves a replacement inode. None of the injected load/rollback failures
  call `arm_retention_schedule`. If both bounded rollback attempts and their verification fail,
  the command returns `recovery_repository_error`, removes only its installed plist inode, leaves
  application scheduling unarmed and does not claim the external service state is known.
- Provider/data boundary: touched production code is shared private-recovery control code; tests
  use temporary synthetic state and fake runners. No source/private database, Keychain, mounted
  repository, real snapshot, credential, provider, model, network, `launchctl`, delete, prune,
  restore or cloud action was read or invoked. No raw command output, repository/snapshot ID,
  private count/timestamp/hash/path, payload, report content or member/provider fact enters this
  evidence.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API/CLI schema,
  configuration, dependency, retention selector/policy/schedule, provider, analytics, custody,
  hosted/public-data and recurring-cost deltas are **none** and **$0/month**. The change is
  rollback-safe for source/repository data and has no migration. Reverting restores unbounded
  service-control waits and the ambiguous-load cleanup defect, so operational rollback is not
  recommended. The Candidate 21 offline safety threshold **passes**; live apply, deletion/prune,
  loaded-label observation, post-retention break-glass restore and `required=true` remain untested
  and separately operator-gated.

## E30.40 — Candidate 22 fail-closed launchd transaction correction

- Classification: **measured** for the immutable evidence prefix, host/runtime, exact offline
  commands, test counts and durations, lint/diff results, changed-path hashes and absence of a live
  command in this writer's method. The conclusion that module-owned `launchctl` calls are bounded
  is **derived** from the passing AST construction test and reviewed single-helper call graph. Live
  label state, destructive retention behavior and post-retention recovery remain **unmeasurable in
  this entry** because the active lease prohibited `launchctl`, Keychain, Kapili/repository, source
  database and retention execution.
- UTC time and environment: `2026-08-30T17:03:08Z` on native macOS **26.5** build **25F71**, Darwin
  **25.5.0 arm64**, CPython **3.14.3** (Clang 16). Tests ran from the project virtual environment on
  the local host without a container/cgroup limit. Dataset and state were pytest temporary
  directories, synthetic plist bytes and deterministic fake service-control runners only.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 21 sorted 33-path manifest SHA-256
  `b8dade2dd364fea79901ee9a96084a7c40ea8d37441ecf89165a588988767709`; pre-entry evidence
  SHA-256 `431a0ed664a7c31f914ac2fc27dc9d6ca0be7571bc46db8767e0d47dc4543a2b` over exactly
  **173,463 bytes**. Candidate 21 QA artifact SHA-256 is
  `ce3544443411466f95694ceeb0db1e9a855c83a4b0bd95a1a14a897f9265f53a`.
- Correction to E30.39: Candidate 21's offline safety threshold did **not** pass independent review.
  It treated ambiguous service-status results too broadly, submitted a lexical plist path, and did
  not establish one shared transactional install/rollback path for both labels with bounded
  late-registration reconciliation. Candidate 22 accepts absence only for return code **113** plus
  the exact bounded case-insensitive `could not find service` phrase. Return code zero alone means
  loaded; every other return code/output/malformed result fails closed before destructive apply.
- Implementation method and result: both hourly and retention labels use the same installer. It
  creates the plist relative to an `O_NOFOLLOW` directory descriptor, validates and holds the exact
  regular-file `(st_dev, st_ino)`, submits `/dev/fd/<plist-fd>` with only that descriptor in
  `pass_fds`, and retains the directory/file authority through bounded bootstrap, verification and
  rollback. Rollback repeatedly performs bounded bootout plus strict print-state reconciliation;
  it requires two consecutive proven-absent observations, never arms on ambiguity, removes only
  the installed inode through the held parent descriptor, preserves a lexical replacement inode,
  closes descriptors and returns only stable redacted service-state/install/cleanup errors. All
  module-owned service-control calls still pass the shared **5.0-second** deadline and **4,096-byte**
  per-stream output bound.
- Exact focused command and result: `/usr/bin/time -p .venv/bin/python -m pytest -q
  tests/test_recovery.py tests/test_recovery_integration.py -k 'retention_ambiguous_service_status
  or retention_service_control_failures or retention_malformed_launchd_domain or
  retention_reconciles_registration or hourly_bootstrap_timeout or repeated_bootout_ambiguity or
  descriptor_bound_plist_survives or retention_cli_redacts_service_control or
  retention_bootout_timeout or retention_preflight_timeout or retention_bootstrap_timeout or
  retention_timeout_cleanup or launchctl_service_control or
  bounded_runner_descriptor_submission'` passed **20/20** selected cases in **1.41 seconds**.
- Candidate 21 QA-artifact compatibility check: `/usr/bin/time -p .venv/bin/python -m pytest -q
  /tmp/test_phase30_c21_adversarial.py` produced **3 passed, 1 failed** in **0.71 seconds**. The
  remaining old assertion expected two bootouts after its initial fake `print` returned bare code
  113 with empty output. Candidate 22 correctly treats that preflight as ambiguous, calls
  `apply_retention` zero times, never bootstraps or arms and therefore performs zero rollback
  bootouts. Changing Candidate 22 to satisfy that obsolete setup would recreate the strict-label
  finding; independent QA must assess the corrected contract behavior rather than reinterpret this
  recorded result as a live service failure.
- Exact scoped command and result: `/usr/bin/time -p .venv/bin/python -m pytest -q
  tests/test_recovery.py tests/test_recovery_api.py tests/test_recovery_integration.py` passed
  **238/238** tests in **151.82 seconds** with one existing Starlette/httpx deprecation warning.
  Exact full-backend command `/usr/bin/time -p make test` passed **474/474** tests in **159.61
  seconds** (160.35 seconds enclosing wall time) with the same warning. Exact lint command
  `.venv/bin/ruff check api tests` passed. `git diff --check -- api/recovery.py
  tests/test_recovery.py tests/test_recovery_integration.py
  docs/evidence/phase-30-private-recovery.md` passed before this append and is repeated at freeze.
- Candidate 21→22 pre-evidence changed paths and SHA-256 values are exactly: `api/recovery.py`
  `79aab6c9…` → `d8ca7efd…`; `tests/test_recovery.py` `45446ed8…` → `224bc45d…`; and
  `tests/test_recovery_integration.py` `ca5ce3f3…` → `b49aa1f5…`. The evidence path becomes the
  fourth delta with this append; the other 29 Candidate 21 manifest entries were hash-identical
  before freeze.
- Provider/data boundary: production code remains shared private-recovery control code; tests use
  only synthetic bytes, temporary files and fake bounded runners. This writer made no source/private
  database, Keychain, Kapili, encrypted repository, snapshot, provider, model, network,
  `/bin/launchctl`, retention delete/prune/apply, restore or cloud call. No raw command output,
  private path/content/count/identifier, credential, report, payload or member/provider fact entered
  tests, stdout, this evidence or the candidate. The operator's destructive authorization remains
  **unconsumed**; `command_invocations=0` for this Candidate 22 implementation/evidence cycle.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API/CLI/configuration,
  dependency, retention selector/policy/schedule, provider, analytics, custody, public/hosted data,
  and recurring-cost deltas are **none** and **$0/month**. The candidate is rollback-safe for source
  and repository data and has no migration. Reverting restores the independently rejected
  service-control ambiguity and lexical-submission behavior, so operational rollback is not
  recommended. Candidate 22's repository-owned offline tests pass; destructive application,
  deletion/prune, native label observation, post-retention break-glass restore and `required=true`
  remain separately gated and untested here.

## E30.41 — Candidate 23 retention-arm error redaction correction

- Classification: **measured** for the immutable input identities, exact offline commands, pass
  counts, elapsed times, injected failure results, changed-path hashes and absence of live command
  execution. The conclusion that an unexpected retention-arm exception cannot escape the CLI is
  **derived** from the exact injected-`OSError` unit/CLI construction tests plus the reviewed
  `_enable_retention`/`main` control flow. Native destructive retention, label mutation and
  post-retention recovery remain **unmeasurable in this entry** because this exceptional lease
  prohibited Keychain, Kapili/repository, source database, `/bin/launchctl`, retention execution,
  restore, provider and network access.
- UTC time and environment: `2026-08-30T18:32:42Z` on native macOS **26.5** build **25F71**, arm64,
  CPython **3.14.3**. Tests ran from the project virtual environment on the local host without a
  container/cgroup limit. Dataset and state were pytest temporary directories, one synthetic
  fail-closed `RecoveryState`, synthetic plist bytes and deterministic fake service-control runners
  only.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 22 sorted 33-path manifest SHA-256
  `6ccc27a24db8d56205aeb4b4e25dcc36b80d0d6c90dad4b37cd01365fdc454c6`; pre-entry evidence
  SHA-256 `2181d6f6dd3e900340def1274ee061770646a1a444f7f7b747c48d1ad168d221` over exactly
  **180,484 bytes**. The operator authorized exactly this exceptional offline fix/review cycle; the
  previously authorized destructive retention command was invoked **zero times** and remains
  unconsumed.
- Correction to E30.40: Candidate 22's repository-owned tests passed, but its offline threshold did
  **not** pass independent Cybersecurity review. `_enable_retention` caught every arm exception,
  rolled back the runner and then re-raised the original exception. A non-`RecoveryError` such as an
  `OSError` containing a private state path therefore bypassed `main`'s stable error envelope and
  could emit a traceback. Candidate 23 preserves an original `RecoveryError` only; every other arm
  exception is discarded and, after bounded rollback, becomes exact stable
  `recovery_repository_error` / `Launchd service control failed.`. A rollback failure still wins as
  the existing stable cleanup-required error rather than claiming cleanup succeeded.
- Construction and failure result: the direct test injects an `OSError` with a synthetic canary path
  from the atomic state-save boundary. It proves exact secret-free error projection, no retained
  cause, runner unloaded, exact plist absent, the prior pending-drill/schedule-unarmed state
  byte-semantically unchanged, and both retained plist/parent authority descriptors closed. The CLI
  test injects the same failure class after one synthetic apply and one arm attempt; it proves exit
  **1**, exact JSON `{code,message}`, no traceback/canary/path, no successful arm and completed
  rollback. The existing `RecoveryError` test now proves object identity is preserved after
  rollback.
- Exact focused command and result: `/usr/bin/time -p .venv/bin/python -m pytest -q
  tests/test_recovery.py::test_retention_runner_is_rolled_back_when_state_arm_fails
  tests/test_recovery.py::test_retention_state_arm_oserror_is_redacted_and_rolled_back
  tests/test_recovery.py::test_retention_cli_redacts_state_arm_oserror` passed **3/3** tests in
  **0.87 seconds** enclosing wall time.
- Exact scoped command and result: `/usr/bin/time -p .venv/bin/python -m pytest -q
  tests/test_recovery.py tests/test_recovery_api.py tests/test_recovery_integration.py` first
  produced **239 passed / 1 failed** in **126.10 seconds** when the unrelated timing-sensitive
  escaped-credential-descendant test's 1.2-second marker already existed. An exact isolated rerun
  of that unchanged test passed in **3.36 seconds**; a clean exact scoped rerun then passed
  **240/240** in **128.62 seconds** with one existing Starlette/httpx deprecation warning. This is
  recorded as a test timing confounder, not relabelled as a Candidate 23 product failure.
- Exact full-backend command and result: `/usr/bin/time -p make test` passed **476/476** tests in
  **156.09 seconds** pytest time (**156.69 seconds** enclosing wall time), with the same existing
  warning. Exact lint command `.venv/bin/ruff check api tests` passed. Exact whitespace command
  `git diff --check -- api/recovery.py tests/test_recovery.py
  docs/evidence/phase-30-private-recovery.md` passed before this append and is repeated at freeze.
- Candidate 22→23 pre-evidence changed paths and SHA-256 values are exactly: `api/recovery.py`
  `d8ca7efd…` → `38524090…`; and `tests/test_recovery.py` `224bc45d…` → `e9b15a06…`.
  This evidence append is the third and final changed path; all other Candidate 22 manifest entries
  are required hash-identical at freeze.
- Provider/data boundary: production code remains shared private-recovery control code. Tests use
  only synthetic canaries, temporary state/plists and fake runners. This writer made no
  source/private database, Keychain, Kapili, encrypted repository, snapshot, provider, model,
  network, `/bin/launchctl`, retention delete/prune/apply, restore or cloud call. No raw exception,
  private path/content/count/identifier, credential, report, payload or member/provider fact entered
  stdout, committed evidence or the candidate.
- Deltas, rollback and threshold: source/application schema, migration, HTTP API/CLI/configuration,
  dependency, retention state/policy/selector/schedule, provider, analytics, custody, hosted/public
  data and recurring-cost deltas are **none** and **$0/month**. The change is rollback-safe for
  source/repository data and has no migration. Reverting restores the traceback disclosure, so
  operational rollback is not recommended. Candidate 23's repository-owned offline threshold
  **passes**; destructive retention application, deletion/prune, native label observation,
  post-retention break-glass restore and `required=true` remain separately gated and untested.

## E30.42 — One-shot destructive retention transition result

- Classification: the bounded wrapper projection, invocation count, wrapper/command durations,
  before/after label and lock observations, topology/target rechecks, and recovery-state transition
  predicates are **measured**. The conclusion that persisted
  `retention_applied_pending_drill=true` was reachable only after destructive retention, rebound-plan
  agreement and post-apply inventory/bundle/topology verification is **derived** from the frozen
  Candidate 23 control flow. The exact deleted snapshot count, survivor count and identifiers, and
  the exact service-control substage that failed are **unmeasurable** because the approved wrapper
  intentionally emitted none of that sensitive/raw output and no retry or diagnostic live command
  is authorized.
- UTC time and environment: the supplied one-shot result was recorded at
  `2026-09-01T15:08:38Z` on the Phase 30 native-macOS operator topology. Runtime, source,
  repository, tool and credential configuration were the same bounded identities approved for the
  Candidate 23 wrapper; this evidence append did not reopen or print any of them.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Candidate 23 sorted
  33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `9ca46137bdfe294d46c7e32739e020c8f2e550c8be727fb5584e097977a3f6e6` over exactly
  **186,759 bytes**; independently reviewed wrapper SHA-256
  `e549dce41192de287a79a1e26ae84e863d9515bbcabb3829c0bb5c34331e1f01` with zero
  pre-execution wrapper-test failures. Agent 1 invoked that exact self-verifying wrapper once with
  the approved hash argument; the wrapper invoked the exact authorized
  `retention --enable --apply --drill-snapshot latest` transition once. The enclosing execution
  wall time was **25.626187917 seconds**.
- Raw bounded artifact: the complete secret-free projection supplied to this evidence writer was:

  ```json
  {"active_after":false,"active_before":false,"command_invocations":1,"command_seconds":22.791,"coverage_unchanged":true,"destructive_transition_authorized":true,"duration_seconds":25.38,"hourly_loaded_after":true,"hourly_loaded_before":true,"identity_verified_twice":true,"label_transition_valid":true,"lock_free_after":true,"lock_free_before":true,"retention_armed_after":false,"retention_configured_after":true,"retention_enforced_after":false,"retention_loaded_after":false,"retention_loaded_before":false,"retention_pending_after":true,"return_class":"stable_error","snapshot_unchanged":true,"stable_code":"recovery_repository_error","state_transition_valid":true,"target_available_after":true,"target_available_before":true,"wrapper_status":"complete"}
  ```

- Result: preflight proved no active recovery process, a free shared lock, the hourly label loaded,
  the retention label absent, the approved destructive transition present, and the recovery target
  available. It verified the approved identity twice and invoked the destructive command exactly
  **once**. The command ran **22.791 seconds**; wrapper measurement was **25.38 seconds**. It returned
  the stable code `recovery_repository_error`, not success. No retry is permitted or attempted.
- State and repository interpretation: coverage and snapshot timestamps remained unchanged.
  `retention_configured=true`, `retention_applied_pending_drill=true`,
  `retention_enforced=false` and `retention_schedule_armed=false` form the valid fail-closed
  post-apply/pre-drill state. In Candidate 23, that pending state is persisted only after the
  destructive repository operation has completed and the selected drill point, survivor inventory,
  repository identity, safe bundle and topology have been rebound and reverified. Therefore the
  destructive retention transition occurred, but this evidence does not invent an exact deletion
  count or claim which service-control operation later failed.
- Scheduling and lock result: the hourly label remained loaded. The recurring-retention label was
  absent before and after; it is not loaded or armed. No recovery process remained active, the lock
  was free after the command, and the recovery target remained available. The wrapper's state and
  label transition validators both passed, so the command failed closed rather than reporting
  recurring retention as operational.
- Provider/data boundary and side effects: the approved action accessed the macOS
  Keychain-backed automation credential and the encrypted off-device repository and performed the
  destructive retention transition. It made no provider or network call and performed no backup,
  restore or source-database mutation; those absences are established by the frozen wrapper/command
  scope rather than a new source-content measurement. No credential, repository/snapshot ID,
  private path, private count, payload, report or raw command/service output entered this evidence.
- Authorization and next gates: the destructive authorization is **consumed**. The result may not
  be retried under that authorization. Because retention is pending and not enforced, the accepted
  post-retention break-glass restore is now mandatory and remains a separate human gate. The
  recurring retention runner remains unresolved and must be repaired/reloaded only after the
  restore evidence is preserved and under a separately reviewed and authorized action; Phase 30
  cannot close while it remains unloaded/unarmed.
- Deltas, rollback and threshold: this entry changes evidence only. Application/source schema,
  HTTP API, configuration, dependency, provider, analytics, public/hosted data and recurring-cost
  deltas are **none** and **$0/month**. Repository deletion is intentionally not reversible by an
  application rollback; recoverability instead depends on the verified surviving post-retention
  point. The destructive transition itself reached the required fail-closed pending state, but
  Phase 30 does **not** pass or close until the post-retention restore succeeds and recurring
  retention is safely operational.

## E30.43 — Operator transcript of the first post-retention restore attempt

- Classification: the two shell outcomes and exact stable JSON envelope are **operator-observed**
  transcript evidence. The first shell failure being a non-attempt and the second shell command
  reaching one recovery CLI invocation are **derived arithmetic/control-flow classifications** from
  that transcript. The possible pre-error steps and the operations not yet reached are **derived**
  from frozen Candidate 23 `restore_drill` ordering. Duration, active-process state, current launchd
  labels, recovery state, current target availability, plaintext-scratch cleanup result, exact
  source-versus-repository path, and whether any credential prompt appeared are **unmeasurable from
  the supplied transcript**. No value is estimated for them.
- Evidence time and environment: operator-supplied terminal transcript received after E30.42 on the
  accepted native-macOS Phase 30 topology. The transcript did not contain a UTC timestamp, OS build,
  runtime version or resource limits, so those fields are unmeasurable for this entry rather than
  copied from an earlier run.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Candidate 23 sorted
  33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `df92609a1542a298b001597b64940e092dff671c9f930126482529929e119059`.
  This entry is transcript-only: its writer ran no command and inspected no private/live state.
- First shell outcome: from the operator's home directory, `.venv/bin/python` was not present and
  zsh returned `no such file or directory` before Python started. This is **zero restore
  invocations** and consumed no restore attempt; it proves only that the relative interpreter path
  was invalid from that working directory.
- Second shell outcome: from the project directory, the operator ran exactly
  `./.venv/bin/python -m api.recovery restore-drill --snapshot latest --json`. This is exactly
  **one restore invocation**. It returned the exact stable envelope:

  ```json
  {"code":"recovery_target_unavailable","message":"Recovery path open timed out."}
  ```

  No traceback, private path, credential, snapshot/repository identifier, private count or raw tool
  output appears in the supplied result. The transcript contains no duration, so none is asserted.
- Candidate 23 ordering interpretation: before this error, the FileVault check and recovery
  lock/scratch setup **may** have occurred; the transcript does not prove each completed. The exact
  error arises while `pin_break_glass_repository` opens/binds the recovery path, before
  `break_glass_check`, snapshot listing/selection or bundle retrieval. Therefore no Restic
  repository operation and no break-glass credential use is evidenced as having been reached. The
  frozen command attempts scratch cleanup in `finally`, but cleanup success is unmeasured and must
  not be inferred from the stable error envelope.
- Provider/data boundary: the frozen restore command has no ESPN, FFC, nflverse, Anthropic,
  Cognito, AWS or other provider/network path. The transcript does not evidence a repository read,
  bundle restore, source-database mutation, provider call or network call. It also does not prove
  current target, label, lock, state or cleanup health after exit; those require a separately
  authorized bounded observation rather than a guessed postcondition.
- Threshold and next gate: the post-retention restore threshold **failed**. No successful restore,
  verification, cleanup or `retention_enforced=true` transition is proven. Exactly one restore
  invocation occurred and this evidence authorizes **no retry**. Phase 30 remains open at a human
  gate for a separately bounded diagnostic/retry decision; the recurring-retention runner also
  remains unresolved from E30.42.
- Deltas, rollback and cost: this entry changes evidence only. Application/source schema, API,
  configuration, dependency, provider, analytics, hosted/public-data and recurring-cost deltas are
  **none** and **$0/month**. No operational rollback claim can be made from this failed transcript;
  the verified surviving post-retention recovery point still requires a successful restore drill.

## E30.44 — First SanDisk FAT32 pre-restore migration attempt stopped at configuration preflight

- Classification: the command, stable output and **5.7-second** enclosing tool wall time are
  **measured**. The absence of copy, destination creation, Restic invocation, Keychain access and
  `.env` mutation is **derived** from the frozen one-shot script's fail-first control flow. The
  configured recovery value and the exact reason it did not equal the fixed KAPILI source are
  deliberately not printed and are **unmeasurable from this bounded result**.
- UTC time and environment: `2026-09-01T15:49Z`; native macOS `26.5`, arm64, Python `3.14.3`, on
  the accepted private-local Phase 30 topology. The script ran from the project root with bytecode
  writes disabled. No provider or network access was permitted.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 23 sorted 33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `382a891a3eed8589134d45815dcbe3759b78807b6c7141e73971714d057e5bec` over exactly
  **197,318 bytes**. The one-shot helper at `/private/tmp/phase30_sandisk_copy_check.py` had SHA-256
  `cc6469c52f3d7d87e7d371adc94d2f420d454a84396bb3e1c928081ba1921f8e`.
- Method: after a syntax check, Agent 1 approved one escalated execution of exactly:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python /private/tmp/phase30_sandisk_copy_check.py
  ```

  The helper's fixed sequence was: require the configured repository to equal the KAPILI recovery
  root; require source present and destination absent; resolve and compare both external FAT32
  physical parents; fingerprint the source; copy without source mutation; compare file count,
  bytes, path/type set and file-content hashes; validate and run one full-data Restic check against
  the destination; then re-fingerprint the source. It contained no format, erase, delete, backup,
  retention, restore, launchd, provider or network operation.
- Raw bounded output and result:

  ```text
  result=fail code=configured_source_mismatch
  ```

  The attempt stopped at the first configuration equality check. It did not reach source or
  destination inspection, topology resolution, fingerprinting, copy, Restic binary validation,
  repository integrity checking or Keychain access. Because `.env` update and application doctor
  were later stages contingent on a passing copy/integrity result, neither occurred. Exact private
  file counts, byte totals, hashes, filenames, repository/snapshot identifiers, device identifiers,
  credentials and contents were never emitted.
- Provider/data boundary and side effects: no ESPN, FFC, nflverse, Anthropic, Cognito, AWS or other
  network call occurred. No source database or repository mutation was reachable. No destination
  repository was created by this attempt. These are control-flow conclusions, not a post-run media
  inventory measurement.
- Threshold and next gate: the copy-comparison and destination-integrity thresholds were **not
  exercised**, so they do not pass. The exactly-once authorization is consumed and no automatic
  retry is allowed. The next safe method is a separately authorized read-only comparison that
  classifies the current configured repository against the mounted KAPILI root without printing
  either value, followed—only if the operator explicitly authorizes it—by one corrected migration
  attempt.
- Deltas, rollback and cost: this entry changes committed evidence only. Source schema, API,
  configuration, dependency, provider, analytics, hosted/public data and recurring-cost deltas are
  **none** and **$0/month**. Source data and the existing encrypted KAPILI repository remain the
  rollback point; SanDisk has not yet been established as a verified recovery target.

## E30.45 — Corrected SanDisk attempt stopped at canonical mount-name preflight

- Classification: the exact command, stable output and **4.6-second** enclosing tool wall time are
  **measured**. Identification of the source-side canonical mount-name comparison as the failing
  check, and the absence of later operations, is **derived** from the frozen helper's ordered
  control flow. The diskutil response itself was deliberately suppressed, so no device identifier,
  serial or full private path is disclosed.
- UTC time and environment: `2026-09-02T18:51:54Z`; native macOS `26.5`, arm64, Python `3.14.3`, on
  the accepted private-local Phase 30 topology. The operator explicitly authorized exactly one
  corrected attempt at `2026-09-02T18:50:43Z`. No provider or network access was authorized.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 23 sorted 33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `52ad1d13aa8666dffec1513261dd46eb0bd684ee03d9f533460834195daa0c82` over exactly
  **201,282 bytes**. The corrected one-shot helper SHA-256 was
  `b50e8baa790077086bc6fc21534af1438e35b73144c70dee83c6f1c3a41f1b9b`; the sole correction from
  E30.44 accepted the previously measured case-only configured-source spelling while retaining the
  fixed actual source opening and every other check.
- Method: after lease and helper review plus syntax validation, production_sre executed exactly:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python /private/tmp/phase30_sandisk_copy_check.py
  ```

  The helper first accepted the case-only configuration spelling, required the fixed source
  repository to be a real directory and the destination repository root to be absent, and obtained
  bounded diskutil metadata for both named volume roots. Its first exact canonical-mount comparison
  then failed on the source volume. This is a mount-label canonicalization failure, not evidence
  that the repository contents or physical-device separation are invalid.
- Raw bounded output and result:

  ```text
  result=fail code=volume_mount_mismatch
  ```

  Source canonical-mount equality failed before external-media, FAT32, writability, distinct-parent,
  tree-fingerprint or copy work. Destination creation, Restic validation/integrity, Keychain access,
  `.env` update and application doctor were therefore not reached. Exact private file counts,
  byte totals, hashes, filenames, repository/snapshot identifiers, device identifiers, credentials
  and contents were never emitted.
- Provider/data boundary and side effects: no ESPN, FFC, nflverse, Anthropic, Cognito, AWS or other
  network call occurred. No source database or repository mutation, destination creation, backup,
  restore, retention, launchd action, format, erase or deletion was reachable. These are ordered
  control-flow conclusions; the attempt intentionally performed no follow-up media command after
  failure.
- Threshold and next gate: copy equivalence, full-data integrity and doctor thresholds were **not
  exercised** and do not pass. The corrected-attempt authorization is consumed; no retry occurred.
  A further attempt requires a new operator authorization after the temporary gate compares
  canonicalized mount references (or filesystem identity) instead of case-sensitive display labels,
  while preserving fixed roots, distinct physical-parent validation and destination-absence checks.
- Deltas, rollback and cost: this entry changes committed evidence only. Source schema, API,
  configuration, dependency, provider, analytics, hosted/public data and recurring-cost deltas are
  **none** and **$0/month**. The existing encrypted source repository remains untouched; SanDisk is
  not yet a verified recovery target.

## E30.46 — Canonical-mount-corrected SanDisk attempt stopped at writability metadata

- Classification: the exact command, stable result and **5.4-second** enclosing tool wall time are
  **measured**. The checks completed before the failure and the non-execution of later operations
  are **derived** from the frozen helper's ordered control flow. Whether diskutil reported a true
  read-only value or omitted/non-boolean writability metadata is **unmeasurable** because the raw
  plist was intentionally suppressed.
- UTC time and environment: `2026-09-02T19:03:28Z`; native macOS `26.5`, arm64, Python `3.14.3`, on
  the accepted private-local Phase 30 topology. The operator authorized this one canonical-mount-
  corrected attempt at `2026-09-02T19:01:24Z`. No provider or network access was authorized.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 23 sorted 33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `d8ad468899c580f32db54c9bbc3e6a071ea909b1f46bc3cbcd15d0039565994d` over exactly
  **205,224 bytes**. The one-shot helper SHA-256 was
  `eae8d38209a7daa6da82957956f334fccf4941152dc21a1d12d82ab600d874d4`; relative to E30.45 it
  case-normalized only the diskutil canonical-mount comparison and added the already leased
  success-only single-assignment `.env` update plus one bounded doctor assertion.
- Method: after re-reading the active lease, reviewing and syntax-checking the helper,
  production_sre executed exactly once:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python /private/tmp/phase30_sandisk_copy_check.py
  ```

  The helper accepted the case-only configured source, required source presence and destination
  absence, fetched bounded diskutil metadata for both volumes, and validated the source canonical
  mount, external-media class, USB protocol, FAT filesystem and physical-parent presence. It then
  accepted the destination canonical mount and external-media class but stopped because destination
  writability was not represented by the exact expected false `ReadOnlyVolume` boolean.
- Raw bounded output and result:

  ```text
  result=fail code=destination_not_writable
  ```

  Destination FAT/USB/physical-parent and cross-device checks were not reached. No tree fingerprint,
  copy, destination creation, Restic validation/integrity invocation, Keychain access, `.env`
  mutation or application doctor occurred. Exact private counts, bytes, hashes, filenames,
  repository/snapshot identifiers, disk identifiers, credentials and contents were never emitted.
- Provider/data boundary and side effects: no ESPN, FFC, nflverse, Anthropic, Cognito, AWS or other
  network call occurred. No source database/repository mutation, destination creation, backup,
  restore, retention, launchd action, format, erase or deletion was reachable. The existing source
  repository and local configuration remain the rollback state by control-flow construction.
- Threshold and next gate: copy equivalence, full-data integrity and doctor thresholds were **not
  exercised**. The one-shot authorization is consumed and no retry occurred. Before any further
  attempt, a separately authorized read-only diagnosis must classify the destination's actual
  diskutil writability fields and a harmless create/write/fsync/read/unlink probe would require its
  own explicit temporary-write authorization. Only then should another copy attempt be considered.
- Deltas, rollback and cost: this entry changes committed evidence only. Source schema, API,
  configuration, dependency, provider, analytics, hosted/public data and recurring-cost deltas are
  **none** and **$0/month**. SanDisk remains unverified and is not the configured recovery target.

## E30.47 — Writability-corrected SanDisk copy completed but failed exact tree equivalence

- Classification: the exact command, bounded output and **30.1-second** enclosing tool wall time
  are **measured**. The prior positive `Writable=true`, `WritableMedia=true` and
  `WritableVolume=true` diagnosis is **operator-authorized measured handoff evidence** supplied to
  production_sre; the raw plist remains outside this ledger. Source immutability through the copy,
  successful copy-process exit, completed destination fingerprinting, and non-execution of later
  stages are **derived** from the frozen helper's ordered control flow. Exact copy duration and
  which of count, bytes, path/type or content differed are **unmeasurable** from the intentionally
  bounded failure output.
- UTC time and environment: `2026-09-02T19:08:29Z`; native macOS `26.5`, arm64, Python `3.14.3`, on
  the accepted private-local Phase 30 topology. The operator authorized this one writability-field-
  corrected attempt at `2026-09-02T19:06:45Z`. No provider or network access was authorized.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 23 sorted 33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `7cee6df7ba3bf7b2c7cbcb6dc3914334c8d0ee21f10f37e26ac6d17b76b5a307` over exactly
  **209,130 bytes**. The one-shot helper SHA-256 was
  `0444cfe145e2fd0d5dec3bc6f12c569899cfc98726f77033ada390490ade72d7`; relative to E30.46 its sole
  preflight correction replaced the stale inverse read-only assertion with all three measured
  positive writability booleans.
- Method: production_sre statically checked the helper for stale writability keys and prohibited
  operation strings, syntax-checked it, then executed exactly once:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python /private/tmp/phase30_sandisk_copy_check.py
  ```

  The helper re-resolved both mounted volumes immediately before action; required fixed roots,
  case-normalized canonical mount equality, external USB/FAT media, positive destination
  writability, distinct whole-disk parents, source repository presence and destination absence;
  fingerprinted the source; copied without extended metadata or ACLs; re-fingerprinted the source;
  then fingerprinted the destination and compared regular-file count, total bytes, path/type set
  and per-file SHA-256-derived content equality without emitting private values.
- Raw bounded output and result:

  ```text
  preflight=pass distinct_external_physical_volumes=true filesystem=fat32
  result=fail code=copy_comparison_failed
  ```

  Preflight therefore proved both named mounted volumes were external physical USB FAT media,
  writable where required and backed by distinct whole-disk parents. The copy process returned
  success, the source pre/post fingerprints matched, and destination fingerprinting completed; at
  least one exact equivalence dimension differed. No private count, byte total, hash, filename,
  repository/snapshot identifier, device identifier, credential or content was emitted.
- Provider/data boundary and side effects: the source repository was not modified by the copy
  fingerprint. A destination repository path was created and populated but is **unverified** and
  must not be used. No Restic process or Keychain access occurred because comparison failed first;
  local `.env` remains on the source target and application doctor did not run. No source-database
  mutation, backup, restore, retention, launchd action, format, erase, repository deletion, ESPN,
  FFC, nflverse, Anthropic, Cognito, AWS or other network call occurred.
- Threshold and next gate: physical separation and source immutability passed; copy equivalence,
  full-data Restic integrity and doctor did **not** pass. The one-shot authorization is consumed and
  no retry occurred. The next action requires explicit authorization for a bounded read-only
  comparison that reports only which equivalence dimensions failed and classifies FAT-generated
  metadata without exposing private names/counts/hashes. Any replacement or deletion of the
  unverified destination is a separate destructive gate.
- Deltas, rollback and cost: the destination now contains an unverified encrypted repository copy;
  committed evidence gained this entry. Source schema, API, local recovery configuration,
  dependency, provider, analytics, hosted/public data and recurring-cost deltas remain **none** and
  **$0/month**. Rollback-safe for the authoritative source is **yes**; operational migration is
  incomplete and SanDisk must not be treated as a recovery point.

## E30.48 — SanDisk canonical copy passes full-data Restic integrity and local doctor

- Classification: the fresh source/destination aggregate comparison, Restic exit result, local
  configuration transition, doctor assertions and durations are **measured**. The conclusion that
  the additional destination entries are FAT/Apple metadata is a **measured classification** against
  fixed basename/prefix classes. Absence of provider/network behavior is **derived** from the local
  repository argv, minimal environment and frozen command scope; packet capture was not performed.
- UTC time and environment: completed `2026-09-02T19:19:54Z`; native macOS `26.5`, arm64, Python
  `3.14.3`, on the accepted private-local Phase 30 topology. The operator authorized exactly one
  Restic full-data check plus success-only target activation and doctor at
  `2026-09-02T19:17:40Z`.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 23 sorted 33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `1ac3f71cf7038454b0a21467d3e3042d4c595862ee747ad37855f552f2e01bb8` over exactly
  **213,921 bytes**. The read-only comparator SHA-256 was
  `da47deae69ac46304d933ad6849f46ddfb9c27e72170c766646a560c9f1a1d0e`; the activation helper
  SHA-256 was `aa1599a10a0e8a61a352f0d35aed72124d28d9a7f3477988ce23a8ea5526701c`.
- Dataset and method: the existing encrypted KAPILI source and SanDisk destination were re-resolved
  as distinct physical volumes immediately before activation. The read-only comparator ran once:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python /private/tmp/phase30_sandisk_readonly_compare.py
  ```

  It measured zero missing source entries, **267** additional destination metadata entries, zero
  type mismatches, zero size mismatches, zero content mismatches and **1,093,632** additional
  destination bytes. Every extra matched an allowlisted AppleDouble/Finder/FAT metadata class and
  every canonical source entry had an exact destination content match. Comparator wall time was
  **3.6 seconds**. No filename, content hash, repository/snapshot identifier or private content was
  emitted.
- Restic, activation and doctor method: production_sre then executed exactly one bounded activation
  helper invocation:

  ```bash
  PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python /private/tmp/phase30_sandisk_activate.py
  ```

  The helper revalidated the configured source, pinned Restic version and binary digest, and
  source-versus-destination physical topology. It invoked Restic once against the local SanDisk
  repository with `check --read-data`, the existing Keychain automation credential command,
  bounded captured output, a 1,800-second timeout and only `HOME`, `LANG`, `LC_ALL` and system
  `PATH` in its environment. On the zero exit status, it atomically replaced exactly one
  `RECOVERY_REPOSITORY` assignment and ran exactly one bounded
  `./.venv/bin/python -m api.recovery doctor --json`; raw tool and doctor output were not recorded.
- Raw bounded projection and result:

  ```text
  integrity=pass full_pack_read=true credential=keychain duration_ms=1543
  configuration=pass assignment_updated=true unrelated_assignments_unchanged=true
  doctor=pass configured=true target_available=true tool_verified=true physical_separation=true source_internal=true target_external=true duration_ms=2362
  result=pass
  ```

  The full activation invocation took **8.6 seconds**. A post-command secret-free configuration
  assertion measured exactly one repository assignment and confirmed that its effective value is
  the SanDisk target. It did not rerun doctor or touch either repository.
- Provider/data boundary and side effects: the authorized Restic check may create only its transient
  repository lock; no backup snapshot, retention action or cleanup was requested. The KAPILI source
  was read-only, no copy or metadata deletion occurred, and the SanDisk encrypted repository was
  read for integrity. The local repository assignment now selects SanDisk. No source-database
  mutation, restore, launchd action, format, erase, ESPN, FFC, nflverse, Anthropic, Cognito, AWS or
  other provider/network path was invoked. Credentials, raw Restic/doctor output, private names,
  paths, contents, hashes and repository/snapshot/device identifiers remain absent from evidence.
- Threshold and next gate: exact canonical-copy equivalence, pinned Restic full-data integrity,
  configured-target availability, tool verification and physical separation **pass**. This proves
  the encrypted repository is readable and internally consistent; it does **not** prove an
  application restore. The next human gate remains the single interactive break-glass restore drill,
  with the independent secret entered directly in a terminal and never in agent context.
- Deltas, rollback and cost: configuration delta is one local uncommitted repository assignment
  from KAPILI to the verified SanDisk target. Source schema, API, dependency, provider, analytics,
  hosted/public data and recurring-cost deltas are **none** and **$0/month**. Rollback-safe for data
  is **yes** because the original encrypted repository was not modified; reverting the assignment
  would be a separate explicit configuration action, not an automatic rollback.

## E30.49 — First SanDisk break-glass restore session aborted before credential entry

- Classification: the preflight, one restore CLI session, reached interactive stage, interrupt,
  process exit and post-abort inspection are **measured/observed**. The statement that the separate
  zsh interaction did not receive the actual break-glass credential is **operator attestation**.
  Source-database non-mutation is **derived** from the frozen restore ordering. Repository byte
  immutability is **unmeasurable** under the post-abort restriction against repository-data
  inspection; no post-session repository fingerprint is asserted.
- Time and environment: the operator authorized exactly one interactive SanDisk break-glass restore
  against `latest` at `2026-09-02T19:25:43Z`, on the accepted native-macOS private-local topology.
  Exact session-start, interrupt and post-abort UTC timestamps were not retained in the bounded
  transcript and are not estimated. The accepted contract, runtime, configured target and pinned
  tool identities remained those established by E30.48.
- Identity and append boundary: accepted Phase 30 contract SHA-256
  `23d100d111186c4ec1388ebe12b092be87b75e14e15e488d60a027342937b8f5`; Git base
  `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 23 sorted 33-path manifest SHA-256
  `0eab1ca1215aafc1800eb2cb0893213b3296a32efe8d2657d3ba7287e18f9069`; pre-entry evidence
  SHA-256 `984435a9e0622bbe29593e9a153bde674556e7793fe73715a60ac82bb7eb899a` over exactly
  **219,392 bytes**.
- Preflight: the secret-free preflight returned `READY-TO-PROMPT`. It observed no active recovery
  process or lock conflict and one doctor pass with the SanDisk target configured and available,
  the pinned tool verified, physical separation true, and internal source/external target media
  classes. The frozen restore implementation was statically verified to use interactive Restic
  calls with `use_credential=false`; it does not invoke the Keychain automation credential. No
  restore or credential operation was started during preflight.
- Restore method and result: Agent 1 opened exactly one TTY session running exactly:

  ```bash
  ./.venv/bin/python -m api.recovery restore-drill --snapshot latest --json
  ```

  The invocation reached `break_glass_check` and waited for interactive input. No credential was
  entered in that TTY. Agent 1 sent Ctrl-C, and the process exited `1` with `KeyboardInterrupt`.
  This consumed the one-invocation authorization. No successful repository verification, snapshot
  selection, bundle retrieval, database construction, application verification or restore result
  occurred.
- Credential disposition: a separate, unrelated zsh interaction received no actual break-glass
  credential, according to the operator's explicit `did not` attestation. No credential value was
  sent through chat or agent context, and none appears in this evidence. On that evidence, secret
  rotation is **not required**. This attestation does not convert the aborted session into a restore
  result.
- Post-abort method and result: the read-only post-abort helper SHA-256 was
  `c07bbfe81dbe6e7c24cab2cbe9f2c64f3cd9eb71702c224cadfd88153b200fcc`; its enclosing wall time was
  **5.7 seconds**. It measured: no recovery process, no Restic process, no lock conflict, zero
  retained run directories, zero retained quarantine directories, zero retained plaintext files
  and zero unexpected scratch entries. The SanDisk target remained mounted and available, and one
  sanitized doctor invocation passed. The helper did not inspect repository contents, credentials,
  shell history or private data and performed no mutation.
- Data and provider boundary: the interrupted stage precedes bundle retrieval, restored-database
  creation, source-oracle comparison and operational read verification. Therefore the source
  database was not mutated by the frozen control path. No backup, retention, launchd, format,
  erase, deletion, ESPN, FFC, nflverse, Anthropic, Cognito, AWS or other provider/network path was
  invoked. The break-glass check is defined as read-only, but repository byte immutability remains
  unmeasured because post-abort repository inspection was expressly prohibited.
- Threshold and next gate: the Phase 30 restore threshold is **unmet**. No restore-ready duration,
  verification duration, recovered canary/constraint/read evidence or successful cleanup-after-
  restore result exists; only cleanup after the aborted pre-credential wait was observed. The
  authorization is consumed and no retry is permitted under it. The next safe step is a non-secret
  terminal-handoff test that proves the operator can type an inert value into the intended TTY;
  after that passes, a new explicit authorization is required for one new break-glass restore
  invocation.
- Deltas, rollback and cost: this entry records evidence only. Source schema, API, local recovery
  configuration, dependency, provider, analytics, hosted/public data and recurring-cost deltas are
  **none** and **$0/month**. The verified SanDisk repository remains configured, but Phase 30 cannot
  close without a successful independent-secret restore drill.

## E30.50 — Candidate 24 trusted break-glass prompt broker offline evidence

- Classification: immutable identities, test counts/results, command timings and file hashes are
  **measured**. Descriptor, cleanup, prompt and output-boundary conclusions are **derived from the
  measured synthetic assertions and inspected Candidate 24 control flow**; they are not claims
  about a live Restic repository. The real controlling-TTY prompt, real independent break-glass
  credential and one real restore remain **unmeasured** and require a new operator authorization
  only after independent review.
- UTC time and environment: final offline gates completed on `2026-09-08` UTC on macOS 26.5 build
  25F71, Darwin 25.5.0 arm64, with lexical Python 3.14.3. Commands were run from the workspace with
  pytest temporary directories, generated SQLite databases, synthetic credentials, PTYs and fake
  local child binaries only. CPU and memory allocation were not recorded: the restricted process
  denied the host `sysctl` query, so no hardware quantity is invented.
- Identity and pre-entry boundary: `shasum -a 256` and `wc -c` reverified accepted contract
  SHA-256 `eff1c28e485fb43f651074cd7b7f5cd6b883706d8eec475b530166c1615794e7` over exactly
  **44,058 bytes** and frozen E30.49 SHA-256
  `43b11a5fa26e0f2ac0100c9edcb6258ae2f91640a65a10ec8218f57e219e2416` over exactly
  **224,577 bytes**, at Git base `7f81c76e24fd50635071f8b53f05a4aeb11881de`.
- Implementation: the private-local restore now validates the pinned Restic authority before the
  fixed application-owned controlling-TTY prompt and again before each child handoff. It reads one
  hidden, non-empty, strict-UTF-8 credential of at most **1,024 bytes** under a **300-second
  monotonic bound**, then best-effort overwrites its mutable buffers. Each of check, snapshots and
  dump receives a fresh anonymous password read descriptor containing the exact bytes with no
  added newline and immediate EOF; the write descriptor is closed before child launch and is never
  inherited. Restic stdin is closed, both output streams remain bounded/captured/unforwarded, and
  the automation credential/Keychain command is structurally excluded. Parent interruption and
  bounded-child failure clean the process group and descriptors; expected prompt failures produce
  only `recovery_repository_error / Recovery credential is unavailable.` The CLI module
  documentation and local runbook now describe this exact trusted-prompt boundary.
- Focused amended gate: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery.py tests/test_recovery_integration.py -q` returned exit **0** on the final
  source bytes; `pytest --collect-only` measured **247 tests**, and the run used **145.84 seconds
  real**, **95.68 user**, **16.35 system**. The suite covers prompt visibility exactly once and no
  echo; missing TTY, EOF, empty, invalid UTF-8/NUL/CR/LF, oversize, fake-clock timeout, terminal
  restore failure and Ctrl-C; exact descriptor bytes/cardinality/EOF/closure; wrong-secret
  single-shot failure; output/path/identifier/ANSI canary suppression; binary replacement before
  each later handoff; timeout/overflow/long-lived-descendant/parent-interruption cleanup; and one
  full synthetic check/snapshots/dump PTY restore with valid final JSON. Because this execution
  sandbox denies reopening `/dev/tty` in the isolated PTY child, the PTY helper maps that fixed
  open to a duplicate of the child's controlling terminal; production still opens only fixed
  `/dev/tty`. A native real-prompt exercise is therefore not claimed.
- Adjacent and static gates: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery_api.py tests/test_hardening.py -q` returned **21 passed** in **2.64 seconds
  real**, **1.95 user**, **0.44 system**, with the one pre-existing Starlette/httpx deprecation
  warning. `/usr/bin/time -p ./.venv/bin/ruff check api tests` and `/usr/bin/time -p make lint`
  both passed in **0.05** and **0.06 real seconds**, respectively. `git diff --check` returned exit
  zero.
- Proportional full gate: `/usr/bin/time -p make test` returned **495 passed** with the same one
  pre-existing warning in **170.59 seconds pytest time**. The command reported **380.39 seconds
  real**, **114.59 user** and **21.29 system**; the unexplained wall/pytest timing divergence is
  retained as measured rather than normalized into a forecast. An earlier same-implementation full run
  before the final memory-hygiene-only UTF-8 validation refinement returned the same **495 passed**
  result in **174.07 real seconds**.
- Candidate-set reconstruction: the accepted Candidate 23 manifest file was absent from both
  `/tmp` and `/private/tmp`; its ledger preserved only the digest, not its entries. Therefore a
  Candidate 23→24 byte comparison of unrelated entries is **unmeasurable** and is not inferred. The
  deterministic Candidate 24 set is reconstructed from the accepted original Phase 30 surfaces:
  all **31** recovery-bearing implementation/test/runbook files plus the phase contract and this
  ledger, exactly **33 paths**. The frozen manifest path/digest is returned in the specialist
  handoff; all 33 current entries and the contract digest are independently checkable there.
- Provider/data boundary: no real credential, Keychain item, source database, repository/device,
  launchd state, private fixture, member/provider fact, AI prompt/report, raw child output, cloud,
  paid service or network was accessed. Synthetic credential canaries exist only in test code/processes and
  are absent from this ledger and stable output assertions. No live Restic or provider action was
  performed.
- Deltas, cost and rollback: application/source schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, hosted/public-data and recurring-cost deltas are
  **none** and **$0/month**. The operator-only restore credential transport, corresponding module
  documentation, tests and runbook text change. Candidate 24 is **rollback-safe**: reverting
  restores Candidate 23's unusable hidden-prompt path without changing source or repository data.
  Independent QA and Cybersecurity review of the exact frozen manifest remain mandatory; only a
  reviewed offline pass may open a new operator gate for one real `make recovery-restore`.

## E30.51 — Candidate 25 trusted-prompt finding remediation

- Classification: contract, source and manifest hashes; test collection/pass counts; command
  results; timings; and host/runtime data below are **measured**. The security conclusions are
  **derived from measured synthetic negative tests plus inspected Candidate 25 control flow**.
  They do not establish that a real independent credential or Restic repository works. A native
  operator prompt and real restore remain **unmeasured**; the measurement method is an independently
  reviewed candidate followed by a separately authorized `make recovery-restore` using the human's
  break-glass secret at the controlling terminal.
- UTC time and environment: final gates completed at `2026-09-09T18:18:40Z` on macOS 26.5 build
  25F71, Darwin 25.5.0 arm64, with Python 3.14.3. Commands ran from the workspace against pytest
  temporary directories, generated SQLite databases, synthetic credentials, PTYs and fake local
  executables only. No hardware-capacity quantity was collected or inferred.
- Identity and pre-entry boundary: `shasum -a 256` and `wc -c` reverified accepted contract
  SHA-256 `eff1c28e485fb43f651074cd7b7f5cd6b883706d8eec475b530166c1615794e7` over exactly
  **44,058 bytes** and frozen Candidate 24 evidence SHA-256
  `c3dfe93509391d57b59e33bb8955beb25f34a5f49bfa196a1363158c9cc5fac9` over exactly
  **230,923 bytes**, at Git base `7f81c76e24fd50635071f8b53f05a4aeb11881de`. Before this
  append, `shasum -a 256 -c /private/tmp/phase30-candidate24.manifest` identified exactly the three
  implementation/test paths changed by this fix cycle; the other **30 of 33** entries still
  matched. This ledger append is the fourth and final changed Candidate 25 path.
- Finding 1, post-spawn cleanup: every operation after `Popen`, including selector construction,
  now lies inside a `BaseException` cleanup boundary. A synthetic reproducer interrupts selector
  setup immediately after child creation and proves the process group exits, inherited authority
  descriptors close and the lock can be reacquired. No child output is forwarded.
- Finding 2, binary authority: validation hashes the configured executable through a descriptor
  while comparing device, inode, size, mode and nanosecond modification/change metadata before and
  after hashing and against the path. The resulting authority is established before prompting and
  must match before every password-descriptor handoff. Offline tests replace the executable with
  identical bytes on a new inode and mutate it in place at the same size; both are rejected before
  the next child receives a credential descriptor.
- Finding 3, mutable UTF-8 validation: credential validation now walks integer octets in the
  mutable buffer and enforces scalar UTF-8 byte ranges without calling `decode`, `bytes` or a codec
  returning immutable secret text. A static boundary test enforces those exclusions. Source and
  delivery buffers are still overwritten on every exit on a best-effort basis; Python cannot offer
  a formal memory-erasure guarantee.
- Finding 4, controlling-TTY boundary: the hidden reader disables `ECHO`, `ECHONL` and `ICANON`
  while preserving `ISIG`, uses zero-byte noncanonical reads under the accepted **300-second
  monotonic bound**, accepts exactly 1 through 1,024 strict-UTF-8 bytes, and promptly rejects byte
  1,025. CRLF, multiline and trailing pasted input are rejected; a bounded quiet window and
  unconditional input flush leave no queued shell bytes in the PTY assertions. Terminal restore
  retries up to three times and verifies the original echo/canonical/signal and control-character
  state; a transient failure recovers with echo on, while a persistent failure returns the stable
  credential-unavailable envelope after exhausting bounded restoration attempts.
- Focused amended gates: `./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q -k 'trusted_prompt or break_glass or selector_setup'`
  returned **40 passed** on corrected final implementation bytes; elapsed time was not recorded and
  is not invented. `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q` returned exit **0** in **141.55 seconds real**, **94.51
  user** and **17.10 system**. A separate `--collect-only` measured **268 tests** in these files.
- Adjacent/static gates: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery_api.py tests/test_hardening.py -q` returned **21 passed** in **2.64 seconds
  real**, **1.95 user** and **0.44 system**, with the pre-existing Starlette/httpx deprecation
  warning. `./.venv/bin/ruff check api/services/recovery.py tests/test_recovery.py
  tests/test_recovery_integration.py`, `./.venv/bin/ruff check api tests`, and `/usr/bin/time -p
  make lint` all returned exit zero; `make lint` measured **0.08 seconds real**.
- Proportional full gate: `/usr/bin/time -p make test` returned **516 passed** with the same one
  pre-existing warning in **170.31 seconds pytest time**. The wrapper reported **739.97 seconds
  real**, **114.90 user** and **21.42 system**. That large wall/pytest divergence is retained as
  measured rather than normalized or presented as a performance forecast.
- Provider/data boundary: no real credential, Keychain item, private source database, Restic
  repository, removable device, launchd state, provider payload, member identifier, AI input/output,
  cloud API, paid service or network was accessed. Synthetic secret canaries existed only inside
  offline test processes and were neither printed nor written to this evidence. The automation
  Keychain credential path remains structurally excluded from the break-glass flow.
- Deltas, cost and rollback: application/source schema, migration, HTTP API, configuration,
  dependency, provider behavior, analytics, scheduling, hosted/public-data and recurring-cost
  deltas are **none** and **$0/month**. Candidate 25 changes only the trusted operator prompt and
  child-authority safety boundary plus its directly corresponding offline tests and this ledger.
  It is **rollback-safe** at the data/schema boundary, but reverting would intentionally restore
  the four independently reproduced Candidate 24 defects. Independent Cybersecurity and QA review
  of the exact frozen Candidate 25 manifest remains mandatory before any real restore gate opens.

## E30.52 — Candidate 26 terminal-signal and restoration remediation

- Classification: contract, manifest and file hashes; changed-path count; test pass counts; command
  results; timings; and host/runtime data below are **measured**. The terminal-safety conclusion is
  **derived from inspected Candidate 26 control flow and measured synthetic PTY negative tests**.
  It does not prove the independent credential or real repository path. A native break-glass
  restore remains **unmeasured**; its method is a separately authorized `make recovery-restore`
  after independent QA and Cybersecurity accept the frozen Candidate 26 manifest.
- UTC time and environment: final gates completed at `2026-09-10T15:46:25Z` on macOS 26.5 build
  25F71, Darwin 25.5.0 arm64, with Python 3.14.3. Tests used pytest temporary directories,
  generated SQLite databases, synthetic PTYs and fake local executables only. No production
  database, credential store, repository, removable device or network was opened.
- Identity and input set: `shasum -a 256` reverified accepted contract SHA-256
  `eff1c28e485fb43f651074cd7b7f5cd6b883706d8eec475b530166c1615794e7` over exactly **44,058
  bytes** and Candidate 25 manifest SHA-256
  `0b77e55c612145edeeeace0d1dc35dfe411bd4d0726e7b5d02a130bec168e27f`. Before this append,
  `shasum -a 256 -c /private/tmp/phase30-candidate25.manifest` measured exactly **3 of 33** entries
  changed: `api/services/recovery.py`, `tests/test_recovery.py` and
  `tests/test_recovery_integration.py`; the remaining **30 of 33** entries matched. This append is
  the fourth and final Candidate 26 changed path.
- Remediation boundary: while hidden input is active, the implementation retains the interrupt
  control and temporarily disables the platform-defined quit/suspend control slots, guards hangup,
  quit and suspend signals until cleanup completes, and then restores/verifies the original local
  flags and control-character values. A bounded application-independent terminal-state capture is
  available before echo is disabled and is used only if the primary bounded restoration attempts
  fail. The implementation returns the stable credential-unavailable envelope rather than treating
  an echo-off terminal as acceptable. The accepted same-UID process and Python memory-erasure
  limitations are unchanged.
- Focused final-byte gates: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery.py -q -k trusted_prompt` returned exit **0** for **20 tests** in **2.15
  seconds real**, **0.90 user** and **0.44 system**. `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery_integration.py -q -k
  pty_restore_prompt_releases_lock_and_scratch_after_terminal_input` returned exit **0** for all
  **3 parametrizations** in **6.14 seconds real**, **4.64 user** and **0.86 system**. Those cases
  cover success, Ctrl-backslash and Ctrl-Z; the negative cases prove the stable secret-free error,
  zero queued input, restored flags/control slots/signal handlers, released lock and retryable
  scratch. `/usr/bin/time -p ./.venv/bin/ruff check api/services/recovery.py
  tests/test_recovery.py tests/test_recovery_integration.py` returned exit **0** in **0.07 seconds
  real**.
- Proportional full gate: `/usr/bin/time -p make test` returned exit **0** with **521 passed** and
  the one pre-existing Starlette/httpx deprecation warning in **181.31 seconds pytest time**. The
  wrapper measured **182.22 seconds real**, **125.52 user** and **27.42 system**. This is one local
  sample and is acceptance evidence, not a CI-duration forecast.
- Provider/data boundary: no real credential, Keychain item, private source database, Restic
  repository, removable device, launchd state, provider payload, member identifier, AI input/output,
  cloud API, paid service or network was accessed. Synthetic values existed only inside offline
  test processes and were not written to this ledger or stable output.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none** and
  **$0/month**. Candidate 26 is rollback-safe at the source/repository data boundary; reverting
  would restore the independently reproduced terminal-signal/restoration defect. Independent QA
  and Cybersecurity review of the exact frozen manifest remains mandatory before a real restore
  authorization can open.

## E30.53 — Candidate 27 complete credential-lifetime signal remediation

- Classification: contract, manifest and file hashes; changed-path count; test collection/pass
  counts; commands; timings; and host/runtime values are **measured**. Cleanup behavior is
  **derived from inspected Candidate 27 control flow and measured synthetic PTY/child-process
  tests**. A native operator credential and repository remain **unmeasured**; the future method is
  one separately authorized `make recovery-restore` only after independent QA and Cybersecurity
  accept the exact frozen Candidate 27 manifest.
- UTC time and environment: final gates completed at `2026-09-10T22:29:40Z` on macOS 26.5 build
  25F71, Darwin 25.5.0 arm64, with Python 3.14.3. Commands ran from the workspace using generated
  SQLite databases, pytest temporary directories, synthetic PTYs, synthetic mutable credentials
  and fake local child executables. No production database, credential store, recovery repository,
  removable device or network was opened.
- Identity and pre-entry boundary: `shasum -a 256` reverified accepted contract SHA-256
  `eff1c28e485fb43f651074cd7b7f5cd6b883706d8eec475b530166c1615794e7` over **44,058 bytes** and
  Candidate 26 manifest SHA-256
  `1b02b72986f935106eb0e656ea30c491181d0f7a8c1a6042bfb03b90d417347f`. Before this append,
  `shasum -a 256 -c /private/tmp/phase30-candidate26.manifest` measured exactly **3 of 33** changed
  entries: `api/services/recovery.py`, `tests/test_recovery.py` and
  `tests/test_recovery_integration.py`; the other **30 of 33** entries matched. This append is the
  fourth and final Candidate 27 changed path.
- Remediation: one outer signal guard is installed before break-glass tool validation and the
  trusted prompt. It remains active across the yielded credential, all check/snapshots/dump child
  lifetimes, password-descriptor context exits and mutable-secret zeroization. The first hangup,
  quit, terminate or suspend signal marks the lifetime interrupted and raises into the existing
  bounded subprocess `BaseException` cleanup; subsequent guarded signals are ignored until cleanup
  completes. Original handlers restore only after descriptor closure and zeroization. Terminal
  control-character mutation remains confined to the prompt reader and is restored before the
  first repository child starts.
- Targeted signal gate: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q -k 'lifetime_guard or signal_during_each_child'` returned
  exit **0** for **16 of 16 tests** in **22.08 seconds real**, **13.32 user** and **3.03 system**.
  Four unit cases verify the first signal interrupts, a second cannot interrupt cleanup and each
  original handler returns. Twelve real-subprocess PTY cases cover check, snapshots and dump
  crossed with SIGHUP, SIGQUIT, SIGTERM and SIGTSTP. Every case measured a stable secret-free error,
  no live child/process group, closed password descriptors, best-effort zeroization observable
  through the retained mutable test buffer, restored terminal/handlers, released lock and retryable
  scratch.
- Complete recovery gates: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q` returned exit **0** in **193.25 seconds real**, **127.11
  user** and **26.78 system**. A separate `--collect-only -q` measured **258** recovery tests plus
  **31** integration tests, **289 total**. `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery_api.py tests/test_hardening.py -q` returned **21 passed** in **3.42 seconds
  real**, **2.47 user** and **0.54 system**, with the pre-existing Starlette/httpx deprecation
  warning. `/usr/bin/time -p make lint` returned exit **0** in **0.12 seconds real**.
- Proportional full gate: `/usr/bin/time -p make test` returned exit **0** with **537 passed** and
  the same one pre-existing deprecation warning in **204.56 seconds pytest time**. The wrapper
  measured **205.15 seconds real**, **137.47 user** and **27.87 system**. This is one local
  acceptance sample and is not a CI-duration or production-performance forecast.
- Provider/data boundary: no real credential, Keychain item, private source database, Restic
  repository, removable device, launchd state, provider payload, member identifier, AI input/output,
  cloud API, paid service or network was accessed. Synthetic values stayed inside offline test
  processes and are absent from this ledger and stable error assertions.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none** and
  **$0/month**. Candidate 27 is rollback-safe for source and repository data; reverting would
  restore the independently reproduced post-prompt signal escape. Independent QA and Cybersecurity
  review of the exact frozen manifest remains mandatory before any real restore authorization.

## E30.54 — Candidate 28 cleanup-entry signal deferral

- Classification: contract, manifest and file hashes; changed-path count; test collection/pass
  counts; command results; timings; and host/runtime values are **measured**. The cleanup-state
  conclusion is **derived from inspected Candidate 28 control flow and deterministic synthetic
  signal injection**. A native credential and real repository remain **unmeasured**; the future
  method is a separately authorized `make recovery-restore` only after independent QA and
  Cybersecurity accept the exact Candidate 28 manifest.
- UTC time and environment: final gates completed at `2026-09-10T23:33:30Z` on macOS 26.5 build
  25F71, Darwin 25.5.0 arm64, with Python 3.14.3. Commands used generated SQLite databases, pytest
  temporary directories, synthetic PTYs, mutable synthetic credentials and fake local child
  executables. No production database, credential store, recovery repository, removable device or
  network was opened.
- Identity and pre-entry boundary: `shasum -a 256` reverified accepted contract SHA-256
  `eff1c28e485fb43f651074cd7b7f5cd6b883706d8eec475b530166c1615794e7` over **44,058 bytes** and
  Candidate 27 manifest SHA-256
  `ba9cf10fc9da49d2ca46b7ecdf5df0d58a3a96747dd5e2e63f7511aae7f08fc0`. Before this append,
  `shasum -a 256 -c /private/tmp/phase30-candidate27.manifest` measured exactly **2 of 33** changed
  entries: `api/services/recovery.py` and `tests/test_recovery.py`; the other **31 of 33** entries,
  including `tests/test_recovery_integration.py`, matched. This append is the third and final
  Candidate 28 changed path.
- Remediation: a context-local signal state now distinguishes active work from nested cleanup.
  Guarded signals interrupt active work, but a first or subsequent guarded signal during bounded
  child kill/drain, password-read descriptor closure, scratch quarantine, lock closure or final
  mutable-secret zeroization only records a deferred interruption. Once cleanup completes, the
  original handlers are restored and the deferred event becomes the stable credential-unavailable
  error. A deferred descriptor-close signal is checked before another child can receive the
  credential, and a deferred scratch signal is checked before successful state publication.
- Exact cleanup-entry gate: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery.py -q -k 'zeroize_entry or password_read_close or
  nonsignal_child_cleanup or lifetime_guard_normal_exit'` returned exit **0** for **5 of 5 tests**
  in **1.17 seconds real**, **0.74 user** and **0.22 system**. The cases inject first SIGTERM and
  SIGQUIT at mutable-secret zeroization entry, first SIGTERM immediately before password-read FD
  close, first SIGTERM at bounded cleanup entry after a child timeout, and verify normal successful
  guard exit. They measure completed zeroization/descriptor or child cleanup, stable secret-free
  normalization and original-handler restoration.
- Regression signal gate: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q -k 'lifetime_guard_suppresses_second_signal or
  signal_during_each_child'` returned exit **0** for the existing **16 of 16** Candidate 27 cases in
  **23.54 seconds real**, **13.88 user** and **3.12 system**. The check/snapshots/dump by
  SIGHUP/SIGQUIT/SIGTERM/SIGTSTP matrix still proves no surviving child/process group, closed
  password descriptors, best-effort mutable-secret zeroization, restored terminal/handlers,
  released lock, retryable scratch and the stable error.
- Complete recovery gates: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q` returned exit **0** in **179.68 seconds real**, **119.89
  user** and **24.70 system**. A separate `--collect-only -q` measured **263** recovery tests plus
  **31** integration tests, **294 total**. `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery_api.py tests/test_hardening.py -q` returned **21 passed** in **2.99 seconds
  real**, **2.25 user** and **0.51 system**, with the pre-existing Starlette/httpx deprecation
  warning. `/usr/bin/time -p make lint` returned exit **0** in **0.08 seconds real**.
- Proportional full gate: `/usr/bin/time -p make test` returned exit **0** with **542 passed** and
  the same one pre-existing deprecation warning in **209.48 seconds pytest time**. The wrapper
  measured **210.17 seconds real**, **143.45 user** and **30.85 system**. This is one local
  acceptance sample, not a CI-duration or production-performance forecast.
- Provider/data boundary: no real credential, Keychain item, private source database, Restic
  repository, removable device, launchd state, provider payload, member identifier, AI input/output,
  cloud API, paid service or network was accessed. Synthetic values remained inside offline test
  processes and are absent from this ledger and stable error assertions.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none** and
  **$0/month**. Candidate 28 is rollback-safe for source and repository data; reverting would
  restore the independently reproduced cleanup-entry interruption. Independent QA and
  Cybersecurity review of the exact frozen manifest remains mandatory before any real restore
  authorization.

## E30.55 — Candidate 29 atomic signal-authority restoration

- Classification: contract, manifest and file hashes; changed-path count; test pass counts;
  commands; timings; and host/runtime values are **measured**. Atomic restoration behavior is
  **derived from inspected Candidate 29 control flow and deterministic native-signal injection**.
  A native credential and real repository remain **unmeasured**; the future measurement method is
  one separately authorized `make recovery-restore` only after independent review accepts the
  exact frozen Candidate 29 manifest.
- UTC time and environment: focused gates completed at `2026-09-11T05:14:10Z` on macOS 26.5 build
  25F71, Darwin 25.5.0 arm64, with Python 3.14.3. Commands used pytest temporary directories and
  process-directed synthetic signals only. No production database, credential store, recovery
  repository, removable device or network was opened.
- Identity and pre-entry boundary: `shasum -a 256` reverified accepted contract SHA-256
  `eff1c28e485fb43f651074cd7b7f5cd6b883706d8eec475b530166c1615794e7` over **44,058 bytes** and
  Candidate 28 manifest SHA-256
  `54963bdf2cec19b1f4b7e156f6e804cd03cf134b0a56539b4fbcdfb083fd645b`. Before this append,
  recomputing all manifest entries measured exactly **2 of 33** changed paths:
  `api/services/recovery.py` and `tests/test_recovery.py`; the other **31 of 33**, including
  `tests/test_recovery_integration.py`, matched. This append is the third and final Candidate 29
  changed path.
- Remediation: before any original handler is restored, the current thread blocks the complete
  SIGHUP, SIGQUIT, SIGTERM and SIGTSTP guard set. All original handlers are restored and the
  lifetime ContextVar is reset while that set remains blocked. Guarded signals pending during
  that interval are synchronously consumed, mark the operation interrupted, and normalize to the
  stable credential-unavailable error. Only after the pending set is empty does the implementation
  restore the thread's prior signal mask. The surrounding cleanup boundary remains active through
  this handoff.
- Exact atomic-restoration gate: `./.venv/bin/ruff check api/services/recovery.py
  tests/test_recovery.py tests/test_recovery_integration.py` returned exit **0**. `/usr/bin/time -p
  ./.venv/bin/python -m pytest tests/test_recovery.py -q -k handler_restoration_is_atomic`
  returned exit **0** for **5 of 5 parametrizations** in **1.16 seconds real**, **0.73 user** and
  **0.12 system**. The cases deliver a real SIGHUP after each of the first three handler
  restorations and immediately before and after the ContextVar reset. They assert that an original
  handler which raises cannot escape, every original handler returns, the ContextVar is cleared,
  SIGHUP is no longer pending, the prior mask returns, outer cleanup completes, and the stable
  secret-free error is emitted.
- Regression gates: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py -q -k
  'zeroize_entry or password_read_close or nonsignal_child_cleanup or
  lifetime_guard_normal_exit'` returned exit **0** for the existing **5 of 5** cleanup-entry cases
  in **0.69 seconds real**, **0.55 user** and **0.09 system**. `/usr/bin/time -p
  ./.venv/bin/python -m pytest tests/test_recovery.py tests/test_recovery_integration.py -q -k
  'lifetime_guard_suppresses_second_signal or signal_during_each_child'` returned exit **0** for
  the existing **16 of 16** complete-lifetime cases in **23.33 seconds real**, **14.09 user** and
  **3.28 system**. `make lint` and `git diff --check` both returned exit **0**. Per the authorized
  proportional scope, no unrelated broad suite was rerun; Candidate 28's full-suite evidence is
  not relabelled as Candidate 29 evidence.
- Provider/data boundary: no real credential, Keychain item, private source database, Restic
  repository, removable device, launchd state, provider payload, member identifier, AI input/output,
  cloud API, paid service or network was accessed. Tests used no real secret value.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none** and
  **$0/month**. Candidate 29 is rollback-safe for source and repository data; reverting would
  restore the independently reproduced sequential handler-restoration escape. Exact frozen-byte
  Cybersecurity review remains mandatory before any real restore authorization.

## E30.56 — Candidate 30 terminal-session correction

- Classification: the failed live command result, terminal-helper return/output sizes, scratch
  entry counts, contract/manifest/file hashes, offline test counts, command results, timings and
  host/runtime values are **measured** unless qualified below. The detached helper's **29 stderr
  bytes** are **derived arithmetic** from the exact captured byte string; zero stderr for the
  non-detached probe is **derived** from its printed success predicate. The mechanism conclusion is
  **derived from the one-line Candidate 30 control-flow change and the real-PTY regression**.
- Live failure observation: in an operator-authorized, escalated Codex PTY, `make
  recovery-restore` invoked `.venv/bin/python -m api.recovery restore-drill --snapshot latest
  --json`, returned the stable `recovery_repository_error / Recovery credential is unavailable.`
  envelope and `make` exited with status **1**. The attempt stopped before the trusted prompt and
  credential entry. Its split-session wall duration is **unmeasurable from retained evidence**;
  the future method is one separately authorized single `/usr/bin/time -p make recovery-restore`
  invocation after both reviewers accept Candidate 30.
- Detached-helper diagnosis: from the repository root in that separately authorized PTY, `.venv/bin/python
  -c 'import os; from api.services.recovery import _tty_state_command; fd=os.open("/dev/tty",
  os.O_RDWR | getattr(os, "O_NOCTTY", 0)); result=_tty_state_command(fd, "-g"); print("rc",
  result.returncode, "stdout_bytes", len(result.stdout), "stderr", repr(result.stderr));
  os.close(fd)'` returned `rc 1 stdout_bytes 0` and the exact fixed diagnostic `stty: stdin isn't
  a terminal\n` on captured stderr. The byte string is **29 bytes**. This command opened no
  credential, Restic binary, repository, source database, provider or network path.
- Non-detached diagnosis: `.venv/bin/python -c 'import os; from api.services.recovery import
  BoundedSubprocessRunner,_TTY_STATE_EXEC_CODE,_minimal_env,_STTY_PATH; import sys;
  fd=os.open("/dev/tty",os.O_RDWR|getattr(os,"O_CLOEXEC",0)|getattr(os,"O_NOCTTY",0));
  result=BoundedSubprocessRunner().run([sys.executable,"-I","-S","-c",
  _TTY_STATE_EXEC_CODE,str(fd),_STTY_PATH,"-g"],env=_minimal_env(),max_output=1024,
  pass_fds=(fd,),timeout_seconds=1.0,interactive=True); print("non_detached_capture",
  result.returncode==0 and bool(result.stdout) and not result.stderr,"stdout_bytes",
  len(result.stdout)); os.close(fd)'` returned `non_detached_capture True stdout_bytes 220`.
  Thus return code was zero, captured stdout was **220 bytes**, and stderr was empty. The stdout
  content was not retained in this ledger.
- Scratch post-check: `.venv/bin/python -c 'from pathlib import Path; from api.config import
  get_settings; root=Path(get_settings().recovery_scratch_dir); names=[p.name for p in
  root.iterdir()] if root.is_dir() else []; print("run_entries",sum(n.startswith("run-") for n in
  names),"quarantine_entries",sum(n.startswith(".quarantine-") for n in
  names),"total_entries",len(names))'` returned **0** `run-*`, **0** `.quarantine-*`, and **1**
  total entry. No authenticated repository operation or source-database write occurred.
- UTC time and offline environment: Candidate 30 gates completed at `2026-09-11T20:39:04Z` on
  macOS 26.5 build 25F71, Darwin 25.5.0 arm64, with Python 3.14.3. Offline commands used pytest
  temporary directories, a synthetic PTY and fake/local child processes only.
- Identity and input boundary: `shasum -a 256` reverified accepted amended contract SHA-256
  `1f06f12d6fb533f56e81caa4b55b72f8a9c15a91f44dc38b35a4d8311ca83c61` over **48,953 bytes**
  and Candidate 29 manifest SHA-256
  `beb6ff708018a946f36348b166bd555bbb5425f6e84282954a9f1d6311e781e8`. Before this append,
  recomputing the Candidate 29 inventory measured three changed entries: the already accepted
  contract amendment plus Candidate 30's `api/services/recovery.py` and
  `tests/test_recovery.py`. The remaining **30 of 33** entries matched; this append is Candidate
  30's third writer-owned change and the current contract is authorization input, not a writer
  edit.
- Minimal correction: `_tty_state_command` now sets the existing bounded runner's
  `interactive=True` mode so this fixed `/bin/stty` helper retains the caller's terminal session.
  Fixed no-shell argv, isolated Python launcher, captured/bounded stdout and stderr, one-second
  timeout, minimal environment, inherited terminal descriptor and descriptor closure remain
  unchanged. Restic children and every other runner caller retain their prior isolation behavior.
- Exact and focused PTY gates: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery.py -q -k independent_tty_state_helper_retains_real_pty_session_and_restores`
  returned exit **0** for **1 of 1** test in **1.01 seconds real**, **0.62 user** and **0.10
  system**. The test uses a real PTY without credential input, executes the detached comparison
  path where platform semantics expose it, proves both fixed capture and restore calls select
  caller-session mode, captures an independent state, mutates echo/canonical flags, restores and
  verifies the original state, and bounds both output streams. `/usr/bin/time -p
  ./.venv/bin/python -m pytest tests/test_recovery.py tests/test_recovery_integration.py -q -k
  'trusted_prompt or independent_tty_state_helper or lifetime_guard or signal_during_each_child or
  zeroize_entry or password_read_close or nonsignal_child_cleanup'` returned exit **0** for **42
  of 42** cases in **20.51 seconds real**, **12.30 user** and **2.63 system**.
- Proportional suites: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q` returned exit **0** in **162.43 seconds real**, **108.33
  user** and **19.60 system**; `--collect-only -q` measured **269** recovery plus **31** integration
  tests, **300 total**. `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery_api.py
  tests/test_hardening.py -q` returned **21 passed** in **2.54 seconds real**, **1.96 user** and
  **0.38 system**, with the pre-existing Starlette/httpx deprecation warning. `./.venv/bin/ruff
  check api tests` returned exit **0** in **0.04 seconds real**; `git diff --check` returned exit
  **0**. No unrelated full `make test` run was relabelled as Candidate 30 evidence.
- Provider/data boundary: Candidate 30 offline work accessed no real credential, Keychain item,
  source database, Restic repository, removable device, provider payload, member identifier, AI
  input/output, cloud API, paid service or network. The previously authorized failed live attempt
  reached neither prompt nor authenticated repository operation and made no source write.
- Deltas, cost, rollback and untested items: schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, hosted/public-data and recurring-cost deltas are
  **none / $0/month**. Candidate 30 is rollback-safe for source and repository data; reverting
  restores the measured terminal-session failure and blocks restore closure. The real credential,
  authenticated repository sequence, restored scratch verification and end-to-end duration remain
  untested on Candidate 30. Exact frozen-byte QA and Cybersecurity review are mandatory before a
  new human authorization may open one real restore.

## E30.57 — Candidate 31 controlling-TTY readiness correction

- Classification: the two visible pre-input failures and shell timings, selector diagnosis/control,
  scratch counts, contract/manifest/file hashes, offline test counts, commands and timings are
  **measured**. Root cause and fix sufficiency are **derived from those measurements, inspected
  control flow and the real-PTY regression**. Neither visible duration measures restoration work.
- Visible operator failures: the app-terminal transcript records two exact `time make
  recovery-restore` invocations. Both displayed the fixed trusted prompt, returned the stable
  `recovery_repository_error / Recovery credential is unavailable.` envelope and exited `make`
  with status **1** before accepting input. The first measured `1.11s user 0.36s system 77% cpu
  1.897 total`; the second measured `0.92s user 0.28s system 77% cpu 1.545 total`. The operator
  confirmed no credential was entered on the first; the second did not provide an input interval.
  These are pre-input failure timings, not restore duration or repository-performance evidence.
- Exact failed selector diagnosis: in a separate disposable escalated Codex PTY, the secret-free
  command `./.venv/bin/python -c 'import os,selectors,termios;
  fd=os.open("/dev/tty",os.O_RDWR); original=termios.tcgetattr(fd); hidden=list(original);
  hidden[6]=list(original[6]); hidden[3]&=~(termios.ECHO|termios.ECHONL|termios.ICANON);
  hidden[6][termios.VMIN]=0; hidden[6][termios.VTIME]=0;
  termios.tcsetattr(fd,termios.TCSAFLUSH,hidden); selector=selectors.DefaultSelector();
  selector.register(fd,selectors.EVENT_READ); events=selector.select(0.2);
  print("ready_events",len(events),"zero_read",len(os.read(fd,2))==0 if events else "not_read");
  termios.tcsetattr(fd,termios.TCSANOW,original); selector.close(); os.close(fd)'` exited **1** at
  `selector.register(fd, selectors.EVENT_READ)` with `OSError: [Errno 22] Invalid argument`.
  Because that diagnostic lacked `finally`, its terminal restoration is **unmeasured**; it ran in a
  disposable tool PTY which then exited, not the operator's terminal.
- Runtime selector identity: `./.venv/bin/python -c 'import selectors;
  selector=selectors.DefaultSelector(); print(type(selector).__name__); selector.close()'` printed
  `KqueueSelector` on this host. This identifies the runtime implementation separately; the failed
  diagnostic itself reported only the standard-library default-selector call and EINVAL.
- Portable readiness control: in the same type of disposable escalated PTY, `./.venv/bin/python -c
  'import os,select,termios,time; fd=os.open("/dev/tty",os.O_RDWR);
  original=termios.tcgetattr(fd); started=time.monotonic(); ready=[]; ok=False; error=None;
  hidden=list(original); hidden[6]=list(original[6]);
  hidden[3]&=~(termios.ECHO|termios.ECHONL|termios.ICANON);
  hidden[6][termios.VMIN]=0; hidden[6][termios.VTIME]=0;
  termios.tcsetattr(fd,termios.TCSAFLUSH,hidden);
  try: ready,_,_=select.select([fd],[],[],0.2); ok=True
  except BaseException as exc: error=type(exc).__name__
  finally: termios.tcsetattr(fd,termios.TCSANOW,original); os.close(fd)
  print("select_ok",ok,"ready",len(ready),"elapsed_at_least_0_19",
  time.monotonic()-started>=0.19,"error",error)'` exited **0** and printed `select_ok True ready 0
  elapsed_at_least_0_19 True error None`. Restoration executed in `finally`; that command did not
  independently byte-compare the restored state.
- Scratch post-check: the E30.56 exact count command was repeated after the second attempt and
  returned **0** `run-*`, **0** `.quarantine-*`, and **1** total scratch-root entry. No authenticated
  repository operation or source-database write occurred.
- UTC time and offline environment: final Candidate 31 checks completed at
  `2026-09-11T23:30:12Z` on macOS 26.5 build 25F71, Darwin 25.5.0 arm64, with Python 3.14.3.
  Offline tests used pytest temporary directories, a synthetic controlling PTY, bounded synthetic
  input and fake/local child processes only.
- Identity and input boundary: `shasum -a 256` reverified accepted contract SHA-256
  `1f06f12d6fb533f56e81caa4b55b72f8a9c15a91f44dc38b35a4d8311ca83c61` over **48,953 bytes**
  and Candidate 30 manifest SHA-256
  `51bf3ecba1cabdcad755fbebf400c01501f49e0b107fbd29880c1c87d21996ab`. Before this append,
  Candidate 31 changed exactly `api/services/recovery.py` and `tests/test_recovery.py` among the
  **33** manifest paths; the other **31**, including the accepted contract and
  `tests/test_recovery_integration.py`, matched. This append is the third and final changed path.
- Minimal correction: only the controlling-TTY credential read loop now constructs
  `selectors.SelectSelector`, the standard-library `select()`-backed implementation. The generic
  bounded subprocess runner continues to use `DefaultSelector`; no Restic or other child isolation
  changed. Noncanonical bounds, 300-second monotonic timeout, 100-millisecond poll ceiling, signal
  handling, trailing-input rejection, independent restoration fallback, descriptor closure,
  prompt/output containment and stable error envelope remain unchanged.
- Exact delayed-input gate: `/usr/bin/time -p ./.venv/bin/python -m pytest
  tests/test_recovery.py -q -k trusted_prompt_select_readiness_waits_for_delayed_real_pty_input`
  returned **1 passed** in **1.13 seconds real**, **0.75 user** and **0.10 system** on final bytes.
  The real-PTY child replaces only `DefaultSelector` with a kqueue-like TTY-registering EINVAL
  reproducer, proves the production path does not use it, remains waiting for at least **0.19
  seconds** before parent input, then accepts one bounded synthetic value without echo, best-effort
  zeroizes the observable mutable buffer, and independently verifies terminal restoration.
- Focused regressions: the first combined run correctly exposed one stale fake-EOF harness that
  patched `DefaultSelector`; it timed out because production now uses `SelectSelector`. After the
  corresponding fake was changed to patch the selected class, `/usr/bin/time -p
  ./.venv/bin/python -m pytest tests/test_recovery.py -q -k
  'trusted_prompt_select_readiness_waits_for_delayed_real_pty_input or
  trusted_prompt_eof_is_stable_hidden_and_restores_terminal'` returned **2 passed** in **1.20
  seconds real**, **0.73 user** and **0.13 system**. The full prompt/terminal/cleanup/signal command
  `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q -k 'trusted_prompt or independent_tty_state_helper or
  lifetime_guard or signal_during_each_child or zeroize_entry or password_read_close or
  nonsignal_child_cleanup'` then returned **43 passed** in **24.13 seconds real**, **14.20 user**
  and **3.44 system**.
- Proportional suites: `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery.py
  tests/test_recovery_integration.py -q` returned exit **0** in **170.03 seconds real**, **113.37
  user** and **21.09 system**; `--collect-only -q` measured **270** recovery plus **31** integration
  tests, **301 total**. `/usr/bin/time -p ./.venv/bin/python -m pytest tests/test_recovery_api.py
  tests/test_hardening.py -q` returned **21 passed** in **3.03 seconds real**, **2.31 user** and
  **0.50 system**, with the pre-existing Starlette/httpx deprecation warning. Final-byte
  `./.venv/bin/ruff check api/services/recovery.py tests/test_recovery.py
  tests/test_recovery_integration.py`, repository-wide `./.venv/bin/ruff check api tests`, and
  `git diff --check` returned exit **0**. No unrelated full `make test` was relabelled as Candidate
  31 evidence.
- Provider/data boundary: Candidate 31 work accessed no real credential, Keychain item, Restic
  repository, removable device, source database, provider payload, member identifier, AI
  input/output, cloud API, paid service or network. Synthetic input remained within the offline PTY
  test and is absent from its transcript and this ledger.
- Deltas, cost, rollback and untested items: schema, migration, HTTP API, configuration,
  dependency, provider, analytics, scheduling, hosted/public-data and recurring-cost deltas are
  **none / $0/month**. Candidate 31 is rollback-safe for source and repository data; reverting
  restores the measured macOS controlling-TTY readiness failure and blocks restore closure. The
  real credential, authenticated repository sequence, scratch read verification and end-to-end
  duration remain untested on Candidate 31. Exact frozen-byte QA and Cybersecurity review are
  mandatory before a new human authorization may open one real restore.

## E30.58 — Candidate 32 credential-cleanup zeroization ordering

- Classification: file/contract/manifest hashes, the negative-control output, offline test counts,
  commands, timings and the whitespace scan are **measured**. The defect mechanism and the
  sufficiency of the correction are **derived** from inspected control flow plus the new
  deterministic regression. The residual noted below is **untested** and labelled as such.
- Origin: both Candidate 31 reviewers returned READY-OFFLINE with no P0 and no P1 findings.
  `cybersecurity` recorded a P2 against `_read_break_glass_credential`'s cleanup ordering; Agent 1
  independently reproduced the mechanism by inspection and surfaced it rather than carrying it. The
  human operator ruled at `2026-09-12` that it reopens implementation, because the accepted
  amendment names an un-zeroized mutable credential buffer as an effect the accepted residual may
  not have. Candidate 31 remains frozen at manifest SHA-256
  `a9b9cc58950589b9d27c1c1118ceed501beab0dbe57a748fa050602626697762` as the rollback point.
- Exact defect: in `_read_break_glass_credential`, original signal handlers were restored inside the
  `finally` **before** the cleanup newline write, the descriptor close and `_zeroize(read_buffer)`,
  and that tail was not inside `_break_glass_cleanup_boundary()`. A guarded signal arriving in that
  window was therefore handled by the restored outer guard's `interrupt_for_cleanup`, which raises
  `KeyboardInterrupt` while `cleanup_depth == 0`. `contextlib.suppress(OSError)` does not catch it,
  so `_zeroize(read_buffer)` and both `_zeroize(secret)` calls were skipped. Because the caller
  assigns `secret` only after the function returns, the caller's own boundary-protected
  `_zeroize(secret)` was also skipped, leaving the buffer orphaned. Per-chunk `read_buffer`
  zeroization does not cover this, since it is itself skipped when the read loop raises mid-chunk.
  This is **not** the accepted pending-check-to-prior-mask-restore window inside
  `_restore_break_glass_signal_authority`.
- Minimal correction: exactly **two** added lines, both `with _break_glass_cleanup_boundary():` —
  one wrapping the `finally` body, one wrapping the post-`finally` block that holds the deferred
  interrupt conversion, both `_zeroize(secret)` calls and both raise paths. Every other line in the
  production diff is re-indentation; a whitespace-insensitive diff against the Candidate 31 bytes
  reports those two additions and no removals. This restores the Candidate 28 requirement that
  cleanup state begin before mutable-secret zeroization, which this function's own cleanup had never
  been placed under. The Candidate 31 selector backend, noncanonical bounds, 300-second monotonic
  timeout, 100-millisecond poll ceiling, trailing-input rejection, independent restoration fallback,
  descriptor closure, prompt containment and stable error envelope are unchanged.
- Negative control: a secret-free in-process control printed
  `['without_boundary_raises', 'with_boundary_defers']`, measuring that the guard handler raises at
  `cleanup_depth == 0` and defers inside the boundary. This isolates the mechanism from the
  regression.
- New regression `test_cleanup_signal_after_handler_restoration_still_zeroizes_credential_buffers`:
  forks a real PTY, installs the outer lifetime guard, and delivers `SIGTERM` from inside the
  cleanup newline write — the only `b"\n"` write in the module and the exact post-restoration
  window. It asserts the read buffer was zeroized, the terminal and original handlers were restored,
  the prompt appeared once, the synthetic value never reached the transcript, and the stable
  credential-unavailable envelope still returned. Recorded precisely: the **envelope alone does not
  discriminate** Candidate 31 from Candidate 32, since both produce `recovery_repository_error`; the
  load-bearing assertion is the zeroization observation, and this is stated in the test itself.
- Measured gates on macOS 26.5, Darwin 25.5.0 arm64, Python 3.14.3, between
  `2026-09-12T01:16:17Z` and `2026-09-12T01:20:01Z`, all with `/usr/bin/time -p`: new cleanup-signal
  regression **1 passed** in **0.89s real** (0.61 user, 0.09 system); Candidate 31 delayed-input gate
  still **1 passed** in **0.81s**; delayed-input plus EOF **2 passed** in **0.82s**; focused
  prompt/terminal/cleanup/signal matrix **44 passed** in **21.64s** (12.59 user, 2.73 system), the
  `-k` expression extended with the new case rather than the test renamed to match the old filter;
  recovery plus integration **302 passed** in **197.41s** (127.24 user, 26.30 system); adjacent
  `tests/test_recovery_api.py tests/test_hardening.py` **21 passed** in **2.49s** with the one
  pre-existing Starlette/httpx deprecation warning. Focused and repository-wide
  `ruff check` both returned exit **0**. Independent static collection predicted 44 and 302 before
  the run and matched exactly.
- Whitespace gate correction: both Candidate 31 reviewers recorded that `git diff --check` is
  **vacuous** for this phase, because every Phase 30 file is untracked and therefore absent from any
  diff; its exit **0** is earned entirely by unrelated tracked files, and the selected ruff rule set
  omits `W`. A first attempt to close this with `git diff --check --no-index /dev/null <file>` was
  itself measured to be **invalid**, because `--no-index` exits 1 whenever the inputs differ, which
  is always true against `/dev/null`. Reading that command's **output** instead of its exit code
  reported no whitespace complaints for all three changed files, and an independent byte scan
  measured 0 CR bytes, 0 trailing-whitespace lines, 0 tab lines, 0 conflict markers and a
  terminating newline in each. The four over-100-column lines in `tests/test_recovery.py` are
  pre-existing and explicitly exempt under the `"tests/*" = ["E501"]` per-file ignore; none are in
  the new test.
- Residual, untested and unclosed: a signal delivered between `return secret` and the caller's
  assignment still raises and orphans the buffer, because no context manager can span that
  boundary. Closing it requires the caller to supply the buffer rather than receive it, which is
  outside a micro-fix. The window narrows from a sequence containing a TTY write that can block
  indefinitely under `IXON` flow control to a single assignment, so exposure is materially reduced
  but not eliminated. No regression covers it; it is recorded for operator ruling, not claimed
  closed.
- Process deviation, recorded rather than omitted: Agent 1 implemented this correction directly
  instead of dispatching `production_sre`. The writer role's value is iterating against tests, and
  no agent in this session can execute the macOS suite; dispatching would have added collateral-edit
  risk to the most sensitive path without adding test capability. Independent review remains the
  control and is mandatory before close.
- Provider/data boundary: no real credential, Keychain item, Restic repository, removable device,
  source database, provider payload, member identifier, AI input/output, cloud API, paid service or
  network was accessed. Synthetic input stayed inside the offline PTY test and is absent from its
  transcript and this ledger. No commit, merge, prune, deletion or spend occurred.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none / $0/month**.
  Candidate 32 is rollback-safe: reverting the two lines restores Candidate 31 exactly, including
  its frozen manifest, without touching source or repository data. The real credential, the
  authenticated repository sequence, scratch read verification and end-to-end restore duration
  remain untested. Exact frozen-byte QA and Cybersecurity review are mandatory before a new human
  authorization may open one real restore.

## E30.59 — Candidate 33 single cleanup boundary

- Classification: file/contract/manifest hashes, gate counts, commands, timings and the whitespace
  scan are **measured**. The defect mechanism and the sufficiency of the correction are **derived**
  from inspected control flow plus the new regression. The residual in the final bullet is
  **untested** and labelled as such.
- Origin, recorded without softening: Candidate 32 review split. `qa_test` returned READY-OFFLINE
  with no P0/P1/P2 and four P3s; `cybersecurity` returned **NO-CLOSE** with a P1. Both reviewers
  **independently identified the same window**, which corroborates it rather than leaving it
  contested, and Agent 1 confirmed the mechanism by inspection and accepted the NO-CLOSE. The
  defect and the incomplete disclosure were introduced by Agent 1's own Candidate 32 work; the
  review gate is what caught them.
- Exact defect in Candidate 32: the fix used **two consecutive** cleanup boundaries. `cleanup_depth`
  therefore returned to 0 between the first boundary's exit, immediately after
  `_zeroize(read_buffer)`, and the second boundary's entry — while `interrupt_for_cleanup` was
  already the installed handler, restored earlier in the same `finally`. A guarded signal dispatched
  in that gap raised `KeyboardInterrupt` at depth 0; the `try` was already complete so nothing caught
  it, both `_zeroize(secret)` calls were skipped, and because the caller binds `secret` only after
  the function returns, its boundary-protected zeroization was skipped as well. This affected every
  failure path — timeout, EOF, bound exceeded, trailing input, rejected control, restore failure —
  with a partial credential in the buffer, and the success path with the full credential. It was a
  **narrower instance of the defect Candidate 32 was authorized to remove**, and unlike the residual
  below it was intra-function and closable with the mechanism already in use.
- Correction: **one** boundary spanning all cleanup and both zeroizations. The deferred-interrupt
  conversion and both `_zeroize(secret)` calls moved inside the existing `finally` boundary; the two
  `raise` statements became assignments to a new `pending_error` local; the second `if` became `elif`
  (exactly equivalent, since it was only ever reachable when the first did not raise); and a bare
  `if pending_error is not None: raise pending_error` was added after the `try`. `_credential_
  unavailable` sets `__cause__` at construction, so raising it later preserves causality, and the
  raise site was already outside any active `except`, so `__context__` is unchanged. By the time the
  raise executes, both buffers are already zeroized. The Candidate 31 selector backend, bounds,
  300-second monotonic timeout, 100-millisecond poll ceiling, trailing-input rejection, independent
  restoration fallback, descriptor closure and stable error envelope are unchanged.
- Regression method, and why it is not a signal race: the window is a few-bytecode gap and cannot be
  entered deterministically by signal injection — `cybersecurity` recorded its own P1 as derived by
  inspection rather than demonstrated. The new test therefore asserts the **observable invariant**
  instead: it wraps `_break_glass_cleanup_boundary` in a recorder, drives the oversize failure path
  so the secret holds exactly `_MAX_CREDENTIAL_BYTES` bytes, and asserts that **no boundary exit
  occurs between the read-buffer zeroization and the secret zeroization**. Under Candidate 32 the
  recorded sequence is `zeroize:1026, exit, enter, zeroize:1024` and the assertion fails; under
  Candidate 33 it is `zeroize:1026, zeroize:1024` and it passes. The recorder delegates to the real
  boundary, so semantics are preserved, and all patching is confined to a `pty.fork()` child that
  ends in `os._exit(0)`.
- Test naming corrected: the Candidate 32 test claimed both buffers while asserting only the read
  buffer, as both reviewers noted. It is renamed to
  `test_cleanup_signal_after_handler_restoration_still_zeroizes_the_read_buffer`, and the new
  `test_cleanup_signal_on_failure_path_zeroizes_the_secret_inside_one_boundary` covers the secret.
  The focused `-k` key is `cleanup_signal`, which matches both.
- Measured gates on macOS 26.5, Darwin 25.5.0 arm64, Python 3.14.3, between
  `2026-09-12T16:47:27Z` and `2026-09-12T16:51:13Z`, all with `/usr/bin/time -p`: new
  secret-zeroization regression **1 passed** in **1.07s real**; both cleanup-signal regressions
  **2 passed** in **0.59s**; Candidate 31 delayed-input gate still **1 passed** in **0.79s**; focused
  prompt/terminal/cleanup/signal matrix **45 passed** in **24.07s**; recovery plus integration
  **303 passed** in **197.15s**; adjacent `tests/test_recovery_api.py tests/test_hardening.py`
  **21 passed** in **2.47s** with the one pre-existing Starlette/httpx deprecation warning. Focused
  and repository-wide `ruff check` returned exit **0**. Independent static collection predicted 45
  and 303 before the run and matched exactly. `git diff --check --no-index` output reported no
  whitespace complaints for all three changed files.
- Carried-forward disclosures from the Candidate 32 review, deliberately **not** fixed here because
  each would widen the delta beyond the authorized objective, and recorded for operator ruling:
  (a) the cleanup newline write is now unbounded and **uninterruptible by guarded signals** under
  `IXON` flow control, since PEP 475 retries the write once a non-raising handler returns — a
  deliberate consequence of deferral that E30.58 described only as a benefit and never as a cost;
  `SIGINT` is not in the guarded set, so Ctrl-C still raises, and `SIGKILL` is unaffected;
  (b) on the success path a deferred cleanup signal is not converted until
  `_raise_deferred_break_glass_signal()` after the first break-glass Restic call, so one bounded
  read-only repository call can execute before the operator's signal surfaces as the stable
  envelope — no mutation, no spend, and the abort still happens;
  (c) `qa_test` additionally observed that the new regression proves `os.kill` was called rather than
  that the handler ran, with the envelope assertion closing that chain indirectly.
- Residual, untested and unclosed: a guarded signal delivered between `return secret` and the
  caller's binding still orphans the populated buffer, because no context manager can span a
  function boundary. This is now the **only** remaining instance of that effect; the intra-function
  window is closed. Closing it requires the caller to supply the buffer rather than receive it.
  `cybersecurity` judged it a legitimate candidate for a narrowly-worded contract amendment
  extending the acceptance, unlike the Candidate 32 window which was an ordinary incomplete fix. No
  regression covers it. It is recorded for operator ruling, not claimed closed.
- Process deviation, recorded as in E30.58: Agent 1 implemented this correction directly rather than
  dispatching `production_sre`, for the same reason — no agent in this session can execute the macOS
  suite, so dispatching would add collateral-edit risk without adding test capability. The split
  Candidate 32 verdict demonstrates that independent review remains an effective control.
- Provider/data boundary: no real credential, Keychain item, Restic repository, removable device,
  source database, provider payload, member identifier, AI input/output, cloud API, paid service or
  network was accessed. Synthetic input stayed inside the offline PTY tests and is absent from their
  transcripts and this ledger. No commit, merge, prune, deletion or spend occurred.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none / $0/month**.
  Candidate 33 is rollback-safe: reverting restores Candidate 32 and its frozen manifest exactly,
  reinstating the inter-boundary window. The real credential, the authenticated repository sequence,
  scratch read verification and end-to-end restore duration remain untested. Exact frozen-byte QA and
  Cybersecurity review are mandatory before a new human authorization may open one real restore.

## E30.60 — First successful break-glass restore drill, and a vacuous read-closure gate

- Classification: the restore result, timings, source-database counts and the hardcoded filter are
  **measured**. The consequence for the read-closure gate is **derived** from the measured filter
  and counts.
- Restore result: the operator's authorized `time make recovery-restore` on Candidate 33 **succeeded**
  — the first successful break-glass restore in Phase 30. The trusted prompt displayed, accepted the
  credential with echo suppressed, authenticated the repository and completed. Measured:
  `integrity`, `constraints`, `exclusions` and `foreign_keys` all **ok**; every one of the nine
  `application_evidence` flags **true**; `restore_ready_seconds` **24.941946**;
  `verification_seconds` **10.265985**; shell wall **35.606s total**, **11.34 user**, **2.05 system**.
  The Candidate 31 selector fix, the Candidate 30 terminal-session fix and the Candidate 32/33
  zeroization ordering are therefore all confirmed against a real credential and a real repository.
  This one-run authorization is consumed.
- **Finding, P1, pre-existing and outside every Candidate 30-33 delta:** the read-closure gate is
  largely vacuous. `verify_restored_read_closure` at `api/services/recovery.py:4174` hardcodes
  `PortfolioFilters(season=2025)`, but the source database contains **only season 2026** data —
  measured read-only: **125** leagues, all `drafted`, all 2026; **20,000** `draft_picks`; **7,845**
  `draft_strategy_*` metrics rows; **28,065** metrics rows total; **1,036** players; **1,250** teams.
  The drill accordingly reported `exposure_players` **0** and `strategy_rows` **0** while the source
  holds thousands of corresponding rows. `build_portfolio_rows` takes no filters, which is why
  `portfolio_rows` **125** is the only count carrying real signal; `opportunity_charts` **5** counts
  chart shapes rather than data rows and is season-independent.
- Consequence: a restored database with a valid schema and **zero rows** would produce
  `portfolio_rows: 0, exposure_players: 0, strategy_rows: 0, opportunity_charts: 5` and still report
  `integrity: ok`, `constraints: ok` and `foreign_keys: ok`. The gate proves the four read surfaces
  do not raise; it does **not** prove restored data is readable. The contract's "verified recovery
  point" therefore rests on less than the output reads as asserting. The docstring's claim that the
  drill provides "a runnable application-level closure check rather than a SQL-only integrity check"
  is true only for the unfiltered portfolio surface.
- This is the third instance of the same green-but-vacuous gate pattern recorded in this phase,
  after `git diff --check` over untracked files (E30.58) and Agent 1's own invalid
  `--no-index` exit-code check (E30.58). It is the most consequential of the three because it sits in
  production code and is the gate that is supposed to demonstrate the recovery baseline works.
- Resolution, not applied here: derive the filter season from the restored data rather than hardcoding
  it, and assert non-zero counts for surfaces the source is known to populate, so an empty restore
  fails instead of passing. Applying it would invalidate the frozen Candidate 33 manifest and require
  a fresh gate run, so it is recorded for operator ruling rather than taken unilaterally.
- Boundary: the source-database counts above were obtained by a read-only `mode=ro` connection for
  diagnosis. No write, no member identifier, no credential, no repository mutation, no provider or
  network call occurred.

## E30.61 — Candidate 34 read-closure gate made real

- Classification: the Linux test counts, failure names and hashes below are **measured on a
  non-macOS host** and labelled as such. The macOS gate run for Candidate 34 is **not yet
  performed**; no macOS timing or pass count is claimed here.
- Defect fixed (E30.60 P1): `verify_restored_read_closure` hardcoded `PortfolioFilters(season=2025)`
  against a database holding only 2026 data, so `exposure_players` and `strategy_rows` reported 0
  while the source held 20,000 draft picks and 7,845 `draft_strategy_*` rows. The season is now
  derived from the restored bundle via `MAX(season) FROM leagues`, and both `restored_season` and
  `configured_season` are reported so a mismatch is visible rather than silent. The project's own
  convention already supported this: `PortfolioFilters(season=None)` falls back to the configured
  season, documented in `portfolio_filters.py` as reading current-year data "without hardcoding
  2026". The recovery gate had bypassed that convention with a literal.
- Non-emptiness guard, and a corrected over-reach recorded in full: the first implementation
  asserted three grounded relationships — leagues ⇒ portfolio rows, draft picks ⇒ exposure rows,
  `draft_strategy_*` metrics ⇒ strategy rows. The second and third are **false**. A legitimate
  sparse bundle can hold draft picks and still yield zero exposure or strategy rows when no team is
  flagged `is_me`, no players match, or no strategy metrics are persisted. That guard failed **9
  integration tests that were green at Candidate 33**, which is conclusive evidence the guard was
  wrong rather than the restore. The guard now asserts only the **definitional** relationship:
  leagues present ⇒ portfolio rows present, because `build_portfolio_rows` is unfiltered and emits a
  row per league in scope. A code comment records why the derived surfaces must not be asserted, so
  the guard is not re-tightened later.
- `test_read_closure_rejects_a_bundle_whose_derived_reads_return_no_rows` was **removed**, because
  its premise was exactly the false assumption above. Deleting a test to make a suite pass is
  normally a defect; here the test asserted something untrue and retaining it would have locked in
  the error.
- Correction of a conflation in the prior reasoning: the non-emptiness guard was never the mechanism
  that fixed the season defect. Derivation is. The guard's only job is rejecting an empty bundle,
  and an empty bundle is already rejected because `MAX(season)` returns NULL. Season derivation is
  asserted directly by two offline tests, over 2026 and 2024 bundles.
- Measured on Linux aarch64 with Python 3.14.7, in an environment built for this verification (the
  repository `.venv` is a macOS build and cannot execute there): read-closure tests **3 passed** in
  **1.34 seconds**; `test_offline_restore_starts_app_matches_reads_and_enforces_reauth` and
  `test_break_glass_orphan_retains_admission_until_exit_and_retry_completes` — two of the nine the
  over-reaching guard had broken — **2 passed**; full recovery plus integration collected **306**
  with **19 failed** and **287 passed**; `ruff check api tests` exit **0**.
- The 19 Linux failures are **platform-dependent and unrelated to this candidate**: 17 are launchd,
  plist, retention-runner or bootout cases that require `launchctl`, which does not exist on Linux;
  one is the PTY Ctrl-C terminal case; one is an escaped-descendant flock/process-group case.
  **Zero** failures reference either read-closure error message, and no `read_closure` test failed.
  An earlier run of the same suite in the same environment failed additional integration tests
  purely because `openpyxl` and other application dependencies were absent; installing them cleared
  those, which is recorded so the two failure classes are not confused.
- Untested here: the 19 macOS-only surfaces, and any macOS timing. A macOS gate run remains the
  authoritative check before Candidate 34 is frozen for review.
- Provider/data boundary: no credential, Keychain item, Restic repository, removable device,
  provider payload, member identifier, AI input/output, cloud API or paid service was accessed. The
  source-database counts in E30.60 were read-only. The verification environment was built in an
  isolated sandbox and wrote nothing to the repository or to macOS.
- Deltas, cost and rollback: schema, migration, HTTP API, configuration, dependency, provider,
  analytics, scheduling, hosted/public-data and recurring-cost deltas are **none / $0/month**.
  Rollback restores Candidate 33 and its frozen manifest, reinstating the vacuous gate. The restore
  drill recorded in E30.60 already succeeded against the previous gate, so Candidate 34 strengthens
  verification rather than changing any restore outcome.

### E30.61a — Candidate 34 macOS gate confirmation

- Correction to E30.61: that entry recorded the macOS gate run as **not yet performed**. It has now
  run, so the prior statement is superseded rather than edited, per the append-only ledger rule.
- Measured on macOS 26.5, Darwin 25.5.0 arm64, Python 3.14.3: read-closure tests **3 passed** in
  **0.89 seconds real** (0.53 user, 0.12 system); recovery plus integration **306 passed** in
  **198.82 seconds real** (128.39 user, 26.52 system); adjacent
  `tests/test_recovery_api.py tests/test_hardening.py` **21 passed** in **2.55 seconds** with the one
  pre-existing Starlette/httpx deprecation warning; `ruff check api tests` exit **0**; accepted
  contract digest rematched.
- **306**, not 307: the removal of `test_read_closure_rejects_a_bundle_whose_derived_reads_return_no_rows`
  drops the collected total by exactly one from Candidate 33's 303 plus the three surviving new
  read-closure tests. Independent static collection predicted 306 before the run and matched.
- Final Candidate 34 bytes: `api/services/recovery.py` SHA-256
  `5fd82ef7fe9172bd1f1f62fb39239204409f38fb9c8725759535524cf81a59a1`; `tests/test_recovery.py`
  SHA-256 `eebdb9c2576f7cfe80e43816e3a545688a4c0874d08af67b877f659113577e8b`. Both match the digests
  computed independently on the non-macOS verification host before this run, so the bytes exercised
  on macOS are the bytes verified there.
- The 19 Linux-only failures reported in E30.61 do **not** reproduce on macOS, confirming that
  classification as platform-dependent rather than regression.
- An earlier macOS invocation in the same terminal session reproduced the pre-fix failures and is
  retained as measured history: it is the run that exposed both the `leagues.is_public` fixture
  omission and the over-reaching non-emptiness guard.
