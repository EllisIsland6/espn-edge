# Active production write lease

Status: **closed — no lease has been held since Phase 29.**

Phases 36 through 41 were carried out without a production write lease, under the operator's standing
direction that this is a practice project to be narrowed and time-boxed. The record below is the last
lease that existed and is kept for its shape, not because it is active. Nothing in it is in force.

| Field | Value |
| --- | --- |
| Phase | `docs/phase-29-opportunity-chart-eligibility-fix.md` |
| Contract SHA | Human-accepted SHA-256 `49431b0725bfd89f082b6d5543135d70837ba5c872901bc0e88207c9fde03e0c`; 12,981 bytes; file remains immutable. |
| Accepted by / at | Human operator explicitly accepted this digest in this task; verified and recorded at `2026-09-16T20:36:38Z`. Independent `qa_test` returned READY-CONTRACT after closing four findings. |
| Lease holder | None; all corrective leases closed. Final independent QA READY-OFFLINE and Agent 1 post-review manifest verification completed at `2026-09-16T21:50:30Z`; no further implementation authorized. |
| Base identity | Git `7f81c76e24fd50635071f8b53f05a4aeb11881de`; exact component/test base hashes are in the accepted contract. |
| Owned paths | Append-only `docs/evidence/phase-29-opportunity-chart-eligibility-fix.md` only. Component and test paths from the previous implementation lease are frozen. |
| Forbidden | Every other production path; all backend, schema, mapping, provider, source DB, credentials, raw cache, contracts, infrastructure and Phase 31 surfaces. No external/network/provider/model call, data mutation, commit, merge, deployment or spend. Tests use mocked synthetic responses and local browser only. |
| Independent reviewer | `qa_test`, read-only after exact candidate freeze. |
| Verified base snapshots | `/private/tmp/espn-edge-phase29-eligibility-base.HNaRYY`; content-addressed `.tsx` and `.ts` copies verified against the accepted contract. `base.sha256` digest `21a68859589ea138d35ef1952120adbef14b3bac03b6b037e06f26c0574c547d`. |
| Dirty-work inventory | Same directory: `pre-existing-status.txt` SHA-256 `46c68dcfc3f3271e49c553db1313cf910b3fc452d5564fb107d1c6245854e500`; `pre-existing-hashes.sha256` SHA-256 `d4532d05b0b9c42ff2b55321d37ab68438c8676a04ca23be9e76d4192e800a39`. Preserve all unrelated bytes. |
| Return condition | Append exact targeted/full-suite results and accepted exception without changing historical evidence; disclose global-red status and checksum limitation; return final evidence SHA. Production/test/backend/contract hashes must remain unchanged. |
| Expiry | At candidate freeze/return, or immediately on scope conflict, unrelated concurrent edits, missing base artifacts or need for external action. |

Candidate 1 manifest: `/private/tmp/espn-edge-phase29-eligibility-base.HNaRYY/candidate1.sha256`,
SHA-256 `4d5c07af986253d817a0a30fd0880fcf48ececf8474eae6850f28b79d01383f0`; all three entries independently verified.
Component `74a3cebf...e6b37c`, smoke test `776c4344...5d503`, evidence `410cdc2c...0d3fd`.
The targeted mocked browser regression passed (one test); full E2E finished 56 passed / 1 failed.
QA found no correction defect but returns NO-CLOSE because the immutable contract's full-suite gate
remains red. The sole failure is the byte-for-byte pre-existing team-logo fallback test, also
recorded in E30.4. The operator accepted the independently reviewed exact-exception amendment
SHA-256 `5238ab0a028514026718f6fb39ac951fc124e79c816f75be6f3e0ccc76d36335`;
acceptance recorded at `2026-09-16T21:47:41Z`. Its exact logo-test exception applies only to
this correction, with full E2E still red and later release gates unchanged. The evidence-only
lease above permits append and freeze, followed by independent read-only QA re-review. No
provider access is authorized.

Final candidate 2 differs from candidate 1 only by the append-only evidence entry;
manifest `/private/tmp/espn-edge-phase29-eligibility-base.HNaRYY/candidate2.sha256`.
Evidence SHA-256 `0327f7080697b3cd12d43a4bcd541d0b6de6a941e0730f185c401f07677ab76e`;
component/test digests remain exactly those above. At this freeze, all writes were closed for
read-only QA; the final outcome follows.

Final closure recorded at `2026-09-16T21:50:30Z`: independent QA READY-OFFLINE under
the accepted parent and exact accepted amendment; final manifest SHA-256
`539069d40d997eb047148da998b790327a45c545a9d8ccf5ee864068bbb85684`, 3/3 entries
verified independently and again by Agent 1 after review. QA confirmed append-only evidence,
unchanged protected hashes and exact scratch rollback. Full browser suite remains RED:
56/57 passed, with only the accepted unchanged logo-test exception for this correction.
This is not release readiness. No commit, merge, deployment or provider action occurred;
Phase 31 Unit 4 has no active write lease.

## Historical closed Phase 30 lease

Status: **closed — Phase 30 complete at Candidate 34; restore succeeded and the verification gate can now fail**

| Field | Value |
| --- | --- |
| Phase | 30 `private-recovery-baseline`; `docs/phase-30-private-recovery-baseline.md` |
| Contract SHA | Amended accepted SHA-256 `1f06f12d6fb533f56e81caa4b55b72f8a9c15a91f44dc38b35a4d8311ca83c61`, size 48,953 bytes |
| Accepted by / at | Human operator accepted the amended contract at `2026-09-11T06:26:48Z`; Cybersecurity returned READY-CONTRACT. After two visible-terminal attempts proved that macOS kqueue rejects registration of the controlling `/dev/tty` before input, the operator explicitly authorized the exceptional Candidate 31 TTY-readiness micro-fix and exact parallel QA/Security review at `2026-09-11T23:22:23Z`. This authorizes reversible offline implementation only, not a real restore, credential entry, repository/device/Keychain mutation, provider/network action, commit or merge. |
| Lease holder | `production_sre`; Candidate 31 micro-lease activated at `2026-09-11T23:22:23Z`. |
| Base SHA | Git `7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 30 manifest SHA-256 `51bf3ecba1cabdcad755fbebf400c01501f49e0b107fbd29880c1c87d21996ab`, size 3,039 bytes; frozen Candidate 30 evidence SHA-256 `3ace73857d3d2e37ab1a99db99011f9cc32dd9986c7498b1718d29ee43da6cc1`, size 264,192 bytes. |
| Owned paths | `api/services/recovery.py`; directly corresponding cases in `tests/test_recovery.py` and `tests/test_recovery_integration.py`; append-only `docs/evidence/phase-30-private-recovery.md`. |
| Forbidden paths | Every other file; executable behavior in `api/recovery.py`; contract text; `.env`; Keychain; shell history; source database; KAPILI/SanDisk repositories; launchd; provider/sync/AI/frontend/schema/config paths. No live Restic/restore/device/credential/provider/network action, repository mutation, commit, merge or spend. Tests use synthetic credentials/fake children only. |
| Independent reviewers | `qa_test` and `cybersecurity`, read-only and parallel against the exact frozen Candidate 31 manifest. |
| Return condition | Change only the controlling-TTY read loop from the macOS-incompatible default kqueue selector to the standard-library `select()`-backed selector; add one delayed-input controlling-PTY regression proving the prompt waits, accepts bounded synthetic input and restores terminal state; run focused prompt/timeout/signal/cleanup regressions and lint; append secret-free measured evidence; freeze and verify Candidate 31; return the lease. |
| Expiry | At Candidate 31 freeze and verified manifest handoff, or immediately on any scope, secret, provider, repository/device or unrelated dirty-work conflict. |

Phase 30 closure at Candidate 34. Frozen manifest SHA-256
`863ac87b93f60733218898934e6a47bf1ed20a7eb231d56c7a5a5ba80afc9fbe`, 33 paths, 3,039 bytes, 33/33 verified, with the
inventory identical to Candidates 30-33. Agent 1 predicted that digest by independent reconstruction before freezing
and it matched. **Path deviation, recorded:** this manifest is at `.venv/phase30-candidate34.manifest` rather than
`/private/tmp/`, because the agent shell cannot reach macOS `/private/tmp`. The location is convention, not a contract
requirement; the digest, inventory and 33/33 verification are unchanged, and the file is gitignored so the dirty
worktree stays at 64 entries. Candidates 30-33 manifests remain at their original `/private/tmp` paths. Review
posture: Candidates 31 and 32 received full parallel `qa_test`/`cybersecurity` review; 33 and 34 were closed on Agent 1
self-review at the operator's direction, recorded as a deliberate reduction in assurance. Final bytes:
`api/services/recovery.py` `5fd82ef7...a59a1`, `tests/test_recovery.py` `eebdb9c2...77e8b`, evidence
`dabbd197...7e30d`. No commit, merge or further live action occurred.

Candidate 33 micro-lease. `cybersecurity` returned **NO-CLOSE** on Candidate 32 with a P1, and `qa_test` returned
READY-OFFLINE but independently reported the **same** window as its P3-1. Both are correct and Agent 1 confirmed the
mechanism by inspection: Candidate 32 used **two** consecutive cleanup boundaries, so `cleanup_depth` returned to 0
between the first boundary's exit after `_zeroize(read_buffer)` and the second boundary's entry. A guarded signal
dispatched in that gap reached the already-restored outer handler at depth 0, raised, and skipped both
`_zeroize(secret)` calls; because the caller had not yet bound `secret`, its boundary-protected zeroization was
skipped too. This is a **narrower instance of the very defect Candidate 32 was authorized to fix**, introduced by
that fix, intra-function and closable with the mechanism already in use. It is therefore completion of the existing
authorized objective, not new scope; if the operator considers it a separate cycle requiring its own authorization,
that ruling supersedes this activation. Scope is strictly narrower than Candidate 32's: replace the two boundaries
with one spanning all cleanup and both zeroizations, hoisting only the bare `raise` out via a `pending_error` local,
plus the directly corresponding regression and append-only evidence. Candidate 32 remains frozen at manifest SHA-256
`7e92ccfb8a9feca7bee897197270b3960f60a6dcac9b4b4010e3c39d28cba670` as the rollback point.

Candidate 32 closure. Agent 1 closed this lease at `2026-09-12T01:27:03Z`. The production delta is exactly two
added `with _break_glass_cleanup_boundary():` lines; a whitespace-insensitive diff against the captured Candidate 31
bytes reports those two additions and zero removals, and the test delta is purely additive. Candidate 32 froze as
sorted deterministic 33-path manifest `/private/tmp/phase30-candidate32.manifest`, SHA-256
`7e92ccfb8a9feca7bee897197270b3960f60a6dcac9b4b4010e3c39d28cba670`, size 3,039 bytes, 33/33 verified, using the
inventory derived from the Candidate 31 manifest so the path set is provably identical. Agent 1 predicted that digest
by independent reconstruction on a non-macOS host **before** the freeze ran, and the freeze matched it exactly.
Exactly 3 of 33 entries changed and 30 matched. Evidence E30.58 was appended before the freeze, so the manifest's
evidence entry is final. No commit, merge, credential, Keychain, repository, device, provider, network or restore
action occurred.

Candidate 32 micro-lease, activated by the human operator at `2026-09-12T00:20:00Z` after both Candidate 31
reviewers returned READY-OFFLINE and Agent 1 surfaced the `cybersecurity` P2. Lease holder `production_sre`.
Owned paths: `api/services/recovery.py` (only the `_read_break_glass_credential` cleanup boundary), the directly
corresponding cases in `tests/test_recovery.py`, and append-only `docs/evidence/phase-30-private-recovery.md`.
Forbidden: every other file, the contract text, `api/recovery.py` executable behavior, `.env`, Keychain, shell
history, source database, KAPILI/SanDisk repositories, launchd, and all provider/sync/AI/frontend/schema/config
paths. No live Restic/restore/device/credential/provider/network action, repository mutation, commit, merge or
spend. Tests use synthetic credentials and fake children only.

Scope, and nothing else: bring `_read_break_glass_credential`'s own cleanup under the Candidate 28 work-versus-
cleanup state, so that a guarded signal delivered after original-handler restoration cannot escape before mutable
credential buffers are zeroized. Candidate 28 already requires cleanup state to begin before mutable-secret
zeroization; this function's cleanup was never placed under that boundary, which is the defect. The writer may not
weaken the stable error envelope, add a credential fallback, disable or extend the timeout, forward Restic output,
change the accepted contract, or alter the selector backend that Candidate 31 froze. Return condition: Candidate 32
freeze with a deterministic regression that injects a guarded signal between handler restoration and zeroization and
proves the buffers are zeroized, the terminal and handlers are restored, and the stable credential-unavailable
envelope still returns. Expiry: at Candidate 32 freeze, or immediately on any scope, secret, provider, repository or
unrelated dirty-work conflict.

Candidate 31 closure. Agent 1 closed this lease at `2026-09-12T00:08:08Z` after independent
verification, not on the writer's report alone. Reverified: accepted contract SHA-256
`1f06f12d6fb533f56e81caa4b55b72f8a9c15a91f44dc38b35a4d8311ca83c61` over 48,953 bytes; base Git
`7f81c76e24fd50635071f8b53f05a4aeb11881de`; Candidate 30 manifest SHA-256
`51bf3ecba1cabdcad755fbebf400c01501f49e0b107fbd29880c1c87d21996ab` over 3,039 bytes. The
Candidate 31 inventory was derived from the Candidate 30 manifest itself rather than
reconstructed, so the 33-path set is provably identical; `shasum -a 256 -c` measured exactly
**3 of 33** changed entries (`api/services/recovery.py`, `tests/test_recovery.py`,
`docs/evidence/phase-30-private-recovery.md`) and **30** unchanged. Candidate 31 froze as sorted
deterministic 33-path manifest `/private/tmp/phase30-candidate31.manifest`, SHA-256
`a9b9cc58950589b9d27c1c1118ceed501beab0dbe57a748fa050602626697762`, size 3,039 bytes, with
**33/33** entries verified. That manifest was additionally reproduced byte-for-byte on an
independent non-macOS host from the same 33 paths, so the freeze does not rest on a single
machine or hash tool. Evidence was reconciled, not extended: E30.57 was checked against the
measured results and found accurate, so `docs/evidence/phase-30-private-recovery.md` remains at
SHA-256 `eb1cd42e7eaca2b0b13dceffa02e6ce7ff320fd5b52915f7378294df0bb39f6c` and the frozen
manifest stays valid. No commit, merge, credential, Keychain, repository, device, provider,
network or restore action occurred.

The Candidate 25 cycle is limited to: (1) arming process-group and descriptor cleanup across every
post-`Popen` `BaseException`, including selector setup; (2) pinning and rechecking the approved
binary device/inode/size/digest identity before every secret handoff, including identical-byte
replacement and in-place mutation tests; (3) validating UTF-8 without creating an immutable
decoded-text copy; and (4) implementing a bounded hidden noncanonical TTY reader that preserves
signals, accepts exactly 1–1,024 valid UTF-8 bytes, promptly rejects oversize, CR/LF/multiline or
trailing input without leaving queued bytes, and restores/verifies echo even after a transient
restore failure. No other behavior or path may change.

The Candidate 26 cycle is the second and final normal fix cycle. It may change only the hidden TTY
state boundary and directly corresponding tests: temporarily disable `VQUIT`, `VSUSP`, and
`VDSUSP` where the platform defines them while retaining `VINTR`/Ctrl-C; restore and verify the
original control-character slots and terminal flags; and add isolated PTY Ctrl-\\/Ctrl-Z cases
that prove stable secret-free interruption, zero queued bytes, terminal restoration, lock release
and scratch retryability. The writer must also stop normalizing a persistent restoration failure
that leaves echo off as acceptable: use a bounded independent safe fallback if feasible within the
accepted contract, or report the unsatisfied contract condition rather than weakening evidence.
No unrelated recovery behavior may change.

The exceptional Candidate 27 cycle is limited to the complete break-glass credential lifetime:
install cleanup-triggering guards before prompting and retain them through `check`, `snapshots`,
`dump`, child termination, credential-descriptor closure and mutable-secret zeroization; restore
the original handlers only afterward. The first SIGHUP/SIGQUIT/SIGTERM/SIGTSTP during a child must
enter the existing bounded kill/drain path, while a second signal cannot interrupt cleanup. Add
per-child synthetic signal cases proving no surviving process group, closed password descriptor,
restored handlers/terminal, released lock, retryable scratch and the stable secret-free error.
Terminal control-character mutation remains prompt-scoped. No other behavior may change.

The Candidate 28 micro-fix may only give the lifetime signal guard explicit work-versus-cleanup
state. Cleanup state must begin before bounded child kill/drain, password-read-FD closure,
lock/scratch unwind and mutable-secret zeroization. A first or subsequent guarded signal during
cleanup may mark interruption but cannot raise until cleanup completes; the result is then the
stable credential-unavailable error and original handlers restore. Tests are limited to first
SIGTERM/SIGQUIT at zeroization entry, first signal immediately before password-FD closure, first
signal when cleanup begins after a non-signal child failure, and normal successful context exit.
Review is exact-finding-only plus the existing 16-case signal matrix.

The Candidate 29 micro-fix may only make lifetime-handler restoration atomic: block the complete
guarded signal set before restoring any original handler; restore every handler and reset the
context state while blocked; consume and normalize any guarded signal pending in that interval;
then restore the prior thread signal mask without delivering a pending signal to an original or
default handler. Add deterministic injection between each adjacent handler restoration and around
context reset. Security re-runs only that reproducer plus the existing cleanup/signal regression.

This lease exists because the approved live doctor measured a real macOS incompatibility: `diskutil
info -plist` rejects the source database file and the repository subdirectory but accepts their
containing mount. The writer may not bypass topology validation, special-case Kapili, expose volume
identifiers, or change the contract. After freeze, both reviewers assess the exact candidate hash.

Candidate 7 froze with sorted 33-path manifest `/tmp/phase30-candidate7.manifest`, SHA-256
`960d6082cecf2fc03fa316d3b6f8fca756d9243b87e148ab9e2bfc2b554c622f`. Candidate 6→7 changes
exactly `api/services/recovery.py`, `tests/test_recovery.py`, and
`docs/evidence/phase-30-private-recovery.md`. Agent 1 reverified every manifest entry and the
contract digest before dispatching read-only QA and Cybersecurity reviews.

Both reviewers returned `NO-CLOSE`. The accepted fix-cycle scope is: descriptor-bound path/mount
resolution with deterministic ancestor-replacement rejection; stop at the actual filesystem device
boundary rather than lexically climbing into a host filesystem; normalize the complete APFS
physical-store set and reject source/target overlap; require a whole-disk parent for non-APFS media;
reject explicit virtual/network/disk-image media while accommodating the measured native omission
of `VirtualOrPhysical`; translate path-resolution failures to secret-free stable errors; and bound
the `diskutil` subprocess wall time. Evidence claims must be corrected to the implemented result.

Candidate 8 froze with sorted 33-path manifest `/tmp/phase30-candidate8.manifest`, SHA-256
`a6caa08caa526b864063d229d3bdd35d16196f9ed85858afb137b44a67a2be45`. Candidate 7→8 changes
exactly the same three leased files. Agent 1 reverified all 33 entries and closed the lease before
the targeted QA/Cybersecurity rerun.

Both reviewers returned `NO-CLOSE` on the same two residual issues. The final normal cycle may only:
(1) bind the `diskutil` subject to the opened filesystem/device identity so a transient
swap-query-restore cannot substitute topology; and (2) make timeout/overflow termination and pipe
drain wall-time bounded even when a descendant inherits both pipes. Tests must reproduce the exact
ABA and long-lived-descendant cases, and E30.5 must state only proven behavior.

Candidate 9 froze with sorted 33-path manifest `/tmp/phase30-candidate9.manifest`, SHA-256
`08a629d1b8e6dd7f803f5c0ed672c0ff5907073681c7c61e0913b76d535699a3`. Candidate 8→9 changes
exactly the same three leased files. Agent 1 reverified every entry and closed the lease before the
final targeted QA/Cybersecurity rerun.

Live runner evidence after Candidate 9: the approved hourly plist installed/loaded in 0.33 seconds,
but its `RunAtLoad` invocation exited `1`. The plist program was the base framework interpreter
because `_launchd_plist()` resolved `.venv/bin/python`; a secret-free import probe measured three
required packages missing from the base runtime and none missing from the lexical venv runtime.
Agent 1 booted out the failed service while retaining the plist and verified the manual recovery
point remained healthy. This exceptional lease fixes only that generator/runtime mismatch.

Candidate 10 froze with sorted 33-path manifest `/tmp/phase30-candidate10.manifest`, SHA-256
`39dcefc72b40c862520f60fe57608ae0184d5208b47a69e58a99653a659104e4`. Candidate 9→10 changes
exactly `api/recovery.py`, `tests/test_recovery.py`, and
`docs/evidence/phase-30-private-recovery.md`; Agent 1 reverified every entry and closed the lease
before independent review.

At `2026-08-13T04:39:58Z`, the human authorized a final runbook-only correction and hash re-review.
The sole owned production path is `ops/private-recovery/README.md`. The writer may correct only the
retention approval-boundary paragraph identified by Candidate-5 QA: hourly-only runner install/load,
separate dry-run, exact `retention --enable --apply` application/install/load/arm behavior, and
fail-closed rollback on load or arm failure. All code, tests, evidence, contract text and other
documentation are forbidden. The lease closes at Candidate-6 freeze.

Candidate 6 froze on 2026-08-13 with sorted 33-file manifest
`/tmp/phase30-candidate6.manifest`, SHA-256
`8d592d8ed80e0fc42f608b1e48a0fb1f78cd5541c8ff7e1fa9dd67b97ccecee3`. Candidate 5→6 changed
exactly the authorized runbook entry. The lease is closed before hash/diff-only review.

Candidate 4 froze on 2026-08-13 with sorted 33-file manifest SHA-256
`ac9618b8ae90093bc68f611770ed9b116d5c72785005d13459e2123cb582c92d` at
`/tmp/phase30-candidate4.manifest`. Final QA reported `READY-OFFLINE`; final Cybersecurity reproduced
two P1 contract violations. The human explicitly authorized one additional narrowly scoped fix and
review cycle. The writer may change only `api/recovery.py`, `api/services/recovery.py`, the directly
corresponding recovery tests, and `docs/evidence/phase-30-private-recovery.md`, solely to:

1. separate hourly-runner installation/loading from recurring retention enablement and implement
   the accepted `retention --enable --apply` approval boundary; and
2. retain verified inode authority through descriptor-relative, no-follow, same-device scratch
   deletion, with deterministic post-validation replacement and nested-mount tests.

This lease closes at Candidate-5 freeze or `2026-08-15T00:00:00Z`, whichever comes first. No other
production path or accepted contract text may change.

Candidate 5 froze on 2026-08-13. Its sorted 33-file manifest is
`/tmp/phase30-candidate5.manifest`, SHA-256
`136d26894104f9d955b73cd740fd3c49b6364dfc5ffccd5c072a93f101f135e6`. Independent verification
confirmed all entries and that Candidate 4→5 changed only the four explicitly permitted files. The
write lease is closed before final review.

Candidate 3 was blocked after the normal two-cycle limit. The human operator explicitly authorized
one exceptional third fix/review cycle on 2026-08-13. `production_sre` may change only the accepted
Phase 30 leased paths necessary to resolve the six recorded P1s and their tests/evidence. This lease
closes again at candidate-4 freeze or `2026-08-15T00:00:00Z`, whichever comes first. No live or
destructive action is authorized.

This lease authorizes only reversible local implementation and offline tests. It does not authorize
live providers, secrets in agent context, repository initialization, launchd loading, a real backup/
restore, retention deletion, commit, merge, cloud action, or spend beyond the already approved
Restic/GnuPG acquisition.

When the human operator activates a lease, replace the status block with all fields below and commit
or otherwise record the immutable lease artifact before implementation:

| Field | Required value |
| --- | --- |
| Phase | Number and contract path |
| Contract SHA | Immutable accepted commit/blob identifier |
| Accepted by / at | Human operator and UTC time |
| Lease holder | Exactly one of `aws_cloud`, `ui_ux`, `production_sre` or the matching Claude name |
| Base SHA | Candidate base supplied to the writer |
| Owned paths | Exact files/directories; no implicit repository-wide lease |
| Forbidden paths | Shared spine and all out-of-phase surfaces |
| Independent reviewers | Normally `cybersecurity`, `qa_test`, or matching Claude names |
| Return condition | Candidate SHA, evidence manifest, or explicit abort |
| Expiry | UTC time or phase-close event |

Agent 1 may draft this record. Once the human has accepted the immutable phase contract, a user's
instruction to run that path authorizes Agent 1 to activate and close this routine local write lease
without another prompt. A specialist cannot widen or renew its own lease. Freeze the candidate
before reviewers begin; reviewers remain read-only against production paths unless a separate
test-only lease is explicitly recorded. Commits, merges, external actions, destructive work, and
spend still require their own authorization.


---

# Phase 31 `hosted-data-safety` — lease closed

Status: **closed at phase completion.** All eleven acceptance criteria measured as one gate,
19/19 cases passing; 541 backend tests pass; `ruff check api tests alembic` exit 0; the Phase 30
recovery suite is unchanged at its 18 pre-existing platform failures.

| Field | Value |
| --- | --- |
| Contract | `docs/phase-31-hosted-data-safety.md`, accepted SHA-256 `4b405f01cbe94a038bf886710b5dbcee4bf1237348e798d5c758927b448d75f9` |
| Lease holder | Agent 1, units 2-4. Returned. |
| Amendments taken | Unit 2: owned paths widened to `api/crypto.py`, `api/services/discovery.py`, `api/services/cross_check.py`. Unit 4: `api/services/recovery.py` (two `EXPECTED_TABLE_COLUMNS` entries and a replacement of both pinned catalog digests), `alembic` added to dev dependencies. Each recorded with its reasoning in the evidence. |
| Reviewers | `qa_test` and `cybersecurity`, read-only and parallel, at the close of every work unit: three rounds on unit 3, one on unit 4. Every finding both reported independently was real. |
| Boundaries honoured | No provider, ESPN, Anthropic or AWS call; no Keychain or Restic mutation; no device mutation; no commit, merge, prune, deletion or spend. No secret, credential, private payload, AI report content or member identifier entered chat, logs, evidence, fixtures or handoffs. Pre-existing dirty worktree changes preserved; no `git reset --hard`, no `git checkout --`. |
| Data action | 16 team `abbrev` values scrubbed in place across the two recorded league captures, reviewed by path and shape statistics only. |
| Next lease | None open. Phase 32 requires fresh human authorization before any live read. |
