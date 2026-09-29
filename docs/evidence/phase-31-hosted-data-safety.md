# Phase 31 — hosted-data-safety evidence ledger

Accepted contract `docs/phase-31-hosted-data-safety.md`. Secret-free: no credential, cookie, member
identifier or GUID value appears in this ledger. Counts and booleans only.

## E31.1 — Unit 2, mode split and kill switches

- Classification: digests, test counts, mutation results and the hosted-mode simulation are
  **measured** on Linux aarch64 / Python 3.14.7. Sufficiency of the boundary is **derived** from
  those measurements plus inspected control flow. macOS is **unverified** for this unit.
- Delivered: `Settings.app_mode` as a two-value `Literal` defaulting to `private_operator`, with
  per-field `frozen=True`; `HostedModeForbidden` and the shared `_forbid_in_hosted_mode` helper;
  guards at `EspnService.__init__`, `cookies_for_account`, `DBRawCache.set`, `crypto._fernet`,
  `discovery.discover_leagues` and `cross_check.from_espn_api`.
- **Review outcome: split verdict.** `qa_test` returned **NO-CLOSE**; `cybersecurity` returned
  READY-OFFLINE with two P1s. QA's verdict was correct and was accepted. Three findings were raised
  **independently by both reviewers**, which is what established them as real rather than arguable.
- Finding 1, accepted and fixed — **the credential ingest direction was unguarded.** Agent 1 guarded
  decrypt and recorded criterion 2 as met. `POST /api/accounts` and `POST /api/accounts/{id}/reauth`
  still accepted a real `espn_s2`, encrypted it and persisted it, so a hosted deployment could create
  credential material **inside** the boundary rather than merely failing to read it. Closed at
  `api/crypto.py:_fernet`, the single chokepoint through which every encrypt and decrypt passes.
- Finding 2, accepted and fixed — **two live ESPN call sites bypassed the construction guard.**
  `discovery.discover_leagues` builds its own `httpx.Client` and issues a cookie-bearing request;
  `cross_check.from_espn_api` calls the third-party `espn_api` library with plaintext credentials.
  Neither constructs `EspnService`. Both were closed only *transitively*, by what their single
  current caller happened to do first. The accepted contract had already reserved mechanism (1),
  defence in depth, for exactly "a call site that can be reached without construction"; Agent 1 did
  not apply it. Both now guard directly.
- Finding 3, accepted and fixed — **two of three monkeypatch targets in the test helper were inert.**
  `api/services/cache.py` imports `_forbid_in_hosted_mode`, not `get_settings`; `api/services/espn.py`
  binds `get_settings` at import, so patching `api.config.get_settings` never reached the guard.
  `raising=False` concealed both. Only one patch was load-bearing. This is the **fourth** recorded
  instance in this project of a check that fires without establishing what it claims, after the three
  in Phase 30 (E30.58, E30.60). The helper now patches one target with `raising=True`.
- Finding 4, raised by `cybersecurity` alone and fixed — **the boundary was mutable.** `Settings`
  set neither `frozen` nor `validate_assignment`, and `get_settings()` returns a cached singleton, so
  a single unvalidated `get_settings().app_mode = "private_operator"` disabled every guard for the
  process lifetime. A whole-model `frozen=True` was tried first and **rejected**: it froze every
  unrelated field and broke `tests/test_ai.py`. Narrowed to per-field `frozen` on `app_mode` with
  `validate_assignment=True`; unrelated fields remain mutable and both facts are tested.
- Owned-paths amendment: the reviews proved the original list could not satisfy the contract's own
  criteria. `api/crypto.py`, `api/services/discovery.py`, `api/services/cross_check.py`,
  `api/services/cache.py` and `tests/conftest.py` were added. Recorded as a review outcome, not a
  silent widening. No forbidden path was relaxed or modified.
- Measured mutation testing, five mutations, each caught by a distinct test and every file restored
  to its original digest afterward: neutering the shared guard helper, removing the crypto custody
  guard, removing the discovery guard, removing the cross-check guard, and restoring mutability all
  produced at least one failure. **No vacuous test** among the 14.
- Measured hosted-mode simulation under `APP_MODE=public_synthetic`, all seven closed: construct
  `EspnService`; decrypt a cookie; **store** a credential; read back a credential; live league
  discovery; cross-check provider call; runtime mode flip (`ValidationError`).
- Measured gates: `tests/test_hosted_mode.py` **14 passed**; the whole `tests/` tree excluding the
  two Phase 30 recovery suites **exit 0, zero failures**; `ruff check api tests` exit **0**.
- Carried open, **outside unit 2's owned paths and requiring operator action**: (1) `api/Dockerfile`
  sets no `APP_MODE` and the default is `private_operator`, so a hosted container **fails open** —
  `cybersecurity` P1-1, and Agent 1 agrees the default is the wrong failure direction; (2)
  `extra="ignore"` means a misspelled `APP_MODEE=public_synthetic` is silently dropped, also failing
  open. Neither can bite while the contract blocks public launch, and Phase 42 owns infrastructure,
  but both must close before any hosted artifact is built.
- Boundary: no live ESPN, Anthropic, AWS or network call; no credential, Keychain, Restic repository
  or Phase 30 surface touched; no commit or merge.

## E31.2 — Unit 3, fixture factory, provenance scanner and manifest

- Classification: digests, scan counts, mutation results and threshold measurements are **measured**
  on Linux aarch64 / Python 3.14.7. macOS is **unverified** for this unit.
- **Review outcome: both reviewers returned NO-CLOSE.** Both independently identified the same three
  defects, which is what established them. All are fixed.
- Finding 1, the worst — **the manifest gate was vacuous and its own docstring denied it.**
  `assert_manifest_complete` read the `scrubbed` flag out of the manifest and never re-scanned, so a
  hand-written entry claiming `scrubbed: true` over a file of live-entropy GUIDs passed green while
  the docstring stated "a future unscrubbed capture would fail the build". Reproduced by both
  reviewers. This was written **in the module whose header documents that exact defect class**, and is
  the sixth recorded instance in this project after four in Phase 30 and one in unit 2. The gate now
  re-scans every declared fixture and fails when the declaration disagrees with the rescan.
- Finding 2 — **both gates were bound to a top-level `*.json` glob.** A capture at
  `recorded/real_league_2027.json`, or named `.JSON`/`.json.orig`/`.har`, left both gates green.
  Criterion 11 says "added to the tree". Now `rglob`, with a separate `real_*` sweep across any depth
  and any extension.
- Finding 3 — **criterion 4 claimed five probe classes and delivered one.** Only GUID detection
  existed; the cookie branch was dead weight (no test exercised `_LONG_TOKEN`), and member-name and
  league-name probes did not exist at all, undisclosed.
- Additional defects found by review and fixed: `_walk` yielded string values only, so a GUID used as
  a dict key and a numeric league id were invisible; `Finding.json_path` echoed a GUID-valued dict key
  verbatim into `ProvenanceError` and thus into any build log — **the one identifier class the scanner
  could not detect was the one it printed**; `_hex_body` starved a non-hex secret to empty and passed
  it as a candidate with no finding; `_MIN_DISTINCT_HEX` was non-load-bearing, since generated
  placeholders reach 7 distinct hex characters against a floor of 6, so only entropy ever separated
  the two sides; and the threshold comment overstated its margin.
- **Threshold, measured rather than asserted:** over 1,000,000 random v4 GUIDs the minimum observed
  Shannon entropy over the 32-char hex body is **2.69 bits** (mean 3.61; 4.0 is the unreachable
  ceiling, not the average). Generated placeholders top out at **1.18**. The 2.5 floor therefore has
  **0.19 bits** of margin on the real side, not the 1.5 an earlier comment implied. Zero misses in
  1,000,000 real GUIDs; zero false positives across 2,500 synthetic placeholders. The comment now
  states the measured figure.
- **Name detection: four content rules were tried and each failed differently.** A synthetic-prefix
  allowlist was brittle; non-emptiness flagged nine identical scrubbed placeholders; entropy cannot
  separate a scrubbed "Test League" from a real league name; uniqueness fails because scrubbed
  placeholders are distinct **and** patterned. The conclusion recorded in the code is that a scrubbed
  name and a real name are **not reliably separable by content**, so any rule claiming to do it is
  wrong in one direction or the other. Name fields are therefore **counted and declared**, never
  auto-classified: the manifest must carry an explicit `names_reviewed` declaration, and the gate
  blocks without it. A gate that forces a decision it cannot make is honest; one that guesses and
  calls it detection is the pattern this phase exists to remove.
- **Real data found and removed.** Forcing that declaration immediately blocked the build on the two
  recorded captures, and inspection confirmed the reviewers' suspicion: the league name was real,
  and it was committed in **four** places — both captures, a hardcoded test assertion, and
  `tests/fixtures/README.md`. A real ESPN **league id** was also present in six places including
  `SPEC.md`. Both are now replaced with synthetic values throughout the working tree; the operator
  authorised the scrub. **Both remain in git history**, which the operator has previously declined to
  rewrite; that is a recorded, accepted residual.
- Measured gates: `tests/test_provenance.py` **42 passed**; whole `tests/` excluding the two Phase 30
  recovery suites **exit 0, zero failures**; `ruff check api tests` exit **0**; the shipped fixtures
  scan **5,275 strings, 36 name fields, 0 findings**, and the manifest passes with the declaration.
- **Mutation testing, six mutations, all caught**: manifest stops re-scanning; globs revert to
  non-recursive; `names_reviewed` no longer required; dict keys unscanned; `json_path` redaction
  removed; long tokens scored on a hex projection again. Three of these passed silently when first
  attempted, because the code fixes had been written **without tests** — that gap is recorded here
  because it is the same failure mode as shipping the fix untested. A seventh mutation was measured
  to be a **no-op**, not an uncaught defect: an empty-body guard was unreachable once long tokens
  scored the whole candidate, so the dead branch was removed rather than left looking load-bearing.
- Known residuals, disclosed rather than claimed closed: regex candidate generation cannot see a
  base64-, URL- or separator-encoded GUID, or one split across two fields; the 32-character token
  floor is a judgement, and the repository holds no evidence of real `espn_s2` length outside `.env`,
  which was not read, so its adequacy against a real cookie is **unquantified**; `load_synthetic` has
  no realpath containment, latent because it has no non-test caller; and name detection is
  declaration-based, not content-based, by the reasoning above.

## E31.3 — Unit 3, second review round

- **Both reviewers returned NO-CLOSE a second time, and both independently found the same two P1s.**
  Both are fixed. The recurrence is itself the finding: the first round's fixes were written and
  self-verified, and independent review still found two live evasions plus a decorative branch.
- P1, found by both — **a basename collision defeated the very sweep added to close the previous
  round's nested-capture finding.** `undeclared_captures` carried an `or p.name not in declared`
  escape, so a capture at `recorded/real_league_2025.json` collapsed onto the declared top-level file
  of the same name: the sweep found it and then excused it. The manifest is now keyed on the
  **relative path** in `build_manifest`, in `present`, and in the sweep, with the basename fallback
  deleted.
- P1, found by both — **a two-character `//` prefix was a complete evasion.** The long-token rule
  skipped the *whole value* on a URL prefix, but a credential's home in a URL is the query string,
  and `espn_s2` is exactly a long URL-encoded token carried there. Measured before the fix: a
  303-char token scored a finding bare, and **zero candidates** behind `https://`, `http://` or `//`.
  Now only the scheme/host/path is skipped and the query and fragment are scanned; a genuine logo
  URL still produces no finding.
- P1, found by both — **the numeric-id walk was decorative.** `_walk` yielded integers to matchers
  that could never match one: `_GUID` needs hyphens, `_LONG_TOKEN` needs 32+ characters. The branch
  read like detection and detected nothing, under a docstring implying coverage — the same defect as
  the `_MIN_DISTINCT_HEX` conjunct removed one round earlier, reintroduced in a new place. Numeric
  ids are undecidable by content for exactly the reason names are, so they are now **counted and
  declared** on the same footing, with an `ids_reviewed` gate. This matters: the real ESPN league id
  was found by human grep, not by the scanner, and the scanner would not have caught its return.
- Additional fixes: `_redact` covered GUID-shaped path segments only, so a numeric key — the common
  ESPN shape, and a real league id is exactly this — printed verbatim into `ProvenanceError` and thus
  into any build log; all-digit segments of 6+ characters are now redacted. `names_reviewed` bound to
  a **count**, so every name value could be replaced at the same paths with both gates staying green;
  the manifest now carries an `undecidable_digest` over the sorted (path, value) pairs and the rescan
  compares it, so a declaration stops vouching the moment the content changes. `build_manifest` reset
  every declaration to false on regeneration, which pushes the next operator to flip it back without
  re-reviewing; it now carries a prior declaration forward **only** while the digest is unchanged.
  `name_families` was decorative grouping left over from the removed uniqueness rule, replaced with a
  plain list. `api/services/fixtures.py` claimed its allowlist stops hosted mode reaching a recorded
  capture; it has **no caller outside the tests**, so that was a conclusion the module had not
  established — the docstring now says so, and names `Settings.app_mode` as the boundary that
  actually exists. Dead `SYNTHETIC_ROOT` removed.
- Measured gates: `tests/test_provenance.py` **56 passed**; whole `tests/` excluding the two Phase 30
  recovery suites **exit 0, zero failures**; `ruff check api tests` exit **0**.
- **Mutation testing, eight mutations, seven caught**: URL skips the whole value again; numeric-key
  redaction removed; digest binding removed; `ids_reviewed` not required; declaration carried
  regardless of digest; findings comparison removed; sweep restricted to `.json` again. The eighth —
  restoring the basename escape hatch — was **verified to be a no-op**, not an uncaught gap: the
  relative-path `present` check already reports any `.json` at any depth, and a non-`.json` capture
  can never match a declared basename, so the escape is unreachable. Verified by execution rather
  than asserted.
- Every fix in this round shipped **with a test**. The previous round shipped three code fixes with
  no tests and review caught all three by mutation; that is recorded in E31.2 and was not repeated.

## E31.4 — Unit 3, third review round

- **Round 3 opened with a defect I introduced in round 2 and found myself.** Round 2 closed the
  `//`-prefix evasion by scanning only the part of a URL after `?` or `#`. A credential in a URL
  **path** segment — `https://host/<token>/x` — then sat in neither and scanned clean: 0 candidates.
  Placement was the wrong axis both times. It is replaced by segmentation (`_url_segments`): every
  structural part of a URL is scored independently and no part is privileged. Measured: all nine
  placements (path, mid-path, path-with-extension, first and later query parameters, fragment,
  userinfo, matrix parameter, protocol-relative) now produce a finding; **0 misses**.
- **The URL exemption is load-bearing in both directions, and that is now asserted rather than
  claimed.** Three real ESPN URL shapes score **4.25, 4.46 and 4.79 bits** as one token — above the
  2.5 floor, so whole-value scoring flags all three. Segmentation clears them because every
  structural segment is 13–21 characters, under the 32-character token floor. The test establishes
  the precondition before asserting cleanliness, so it cannot pass for a scanner that has stopped
  detecting anything.
- **`undecidable_digest` joined `path=value` pairs with `|`, and the join was ambiguous.** Two
  genuinely different undecidable sets produce byte-identical material, so one declaration vouched
  for the other's content. Replaced with `json.dumps` of the sorted pairs, full digest, not
  truncated. The collision is pinned end-to-end through the public API, with the naive join asserted
  to collide first so the test proves what it says.
- **Making the digest full-length made the scanner flag its own record**: a 64-character sha256 is a
  high-entropy long token. Truncating it back would have weakened a gate to silence a scanner, and
  skipping the manifest wholesale would have hidden the part a human writes. Both digest fields are
  dropped from the manifest's own content scan only, on the argument that
  `assert_manifest_complete` recomputes both — and a test establishes that argument instead of
  asserting it.
- **A basename escape hatch was still open on the manifest side.** Both exclusion sites compared
  `path.name`, so a capture planted at `recorded/provenance.json` was excluded from the sweep **and**
  from the declaration requirement. `_is_manifest` now compares the exact relative path. Findings
  also name the path relative to the sweep root, not the basename, because a finding that names
  `real_league_2025.json` when the file is `recorded/real_league_2025.json` sends the operator to the
  wrong file.

### Parallel review, two independent reviewers, round 3

Both dispatched read-only with explicit instructions to mutate rather than read, to demonstrate
evasions with invented values only, and not to re-report residuals already disclosed in E31.2/E31.3.
They applied roughly 97 and 40 mutations respectively. **Findings reported by both independently are
listed first; those are the ones that were real every time.**

- **P1 — a capture that is neither `*.json` nor `real_*`-prefixed was in no gate at all.** Both gates
  were bound to `rglob("*.json")` and the separate any-extension sweep fired only on the `real_`
  prefix, so the intersection was empty. Measured green on `espn_capture.har`, `capture.JSON`,
  `league.json.bak`, `members.ndjson` — and on `tests/fixtures/README.md`, which is one of the files
  this phase already found a real league name in. A `.har` is the single most likely artifact of
  "record a live capture to debug this". Fixed: every file under the tree at any depth with any
  extension must be declared, and non-JSON text is scanned line by line rather than skipped.
- **P1 — the entire name and id control was switched on by a filename prefix.** A capture saved under
  any other name was scanned as synthetic: zero name fields, zero id fields, and `build_manifest`
  then auto-declared it reviewed *because its own counts were zero*. The human was asked exactly
  where the scanner had already seen the risk and skipped exactly where it had not, so the
  declaration could never backstop the scanner's blind spots — which is its entire purpose. Fixed:
  `origin` is a human declaration that drives the scan; `build_manifest` writes `"undeclared"` for
  any file it has not classified before and refuses to auto-declare; a `real_*` file may not be
  relabelled synthetic; anything undeclared is scanned on the strict side; and both reviewed
  declarations are required for every recorded capture whatever the counts say.
- **P1 — my own round-3 comment claimed a control that did not exist.** It said a real league name in
  a free-text `reviewed_note` would be caught. The manifest was scanned with `recorded=False`, so the
  name rule was structurally off, and no entropy rule can recognise a name in any case. The test I
  paired with it planted a SWID, which the GUID rule catches, so it established a different
  proposition than the one the comment made. This is the recurring defect of this phase — a check
  that does not establish what it claims — committed by me, twenty minutes after writing the fix, and
  caught by both reviewers. Fixed structurally rather than by comment: the manifest is scanned with
  `recorded=True`, and `_MANIFEST_ENTRY_KEYS` is exhaustive, so there is no free-text field at all.
  A control, where the comment was a wish.
- **P1 — a declaration bound to a projection of the file, not to the file.** `undecidable_digest`
  covers only what the scanner classified. A JSON **string** id matches neither the GUID rule nor the
  32-character token rule, so a capture re-recorded against a different real league id had a
  byte-identical undecidable digest: the carry-forward held and both gates stayed green. Fixed with a
  `content_digest` over the file's bytes, compared in the rescan and gating the carry-forward.
- **P1 — the gate could have moved the leak into a public CI log.** `Finding` is value-free and a
  test checks every attribute; its sibling `ScanResult` held the raw member and league names and had
  the dataclass default `__repr__`. `test_the_shipped_fixtures_are_clean` binds the result and
  asserts a count, and pytest prints the receiver of a failing assertion with no flags — so the one
  moment the log is most likely to be read is the moment it would have carried the names. Fixed with
  an explicit value-free `__repr__`.
- **P2 — `_redact` covered a bare digit run only.** One separator reopened it: `<leagueId>:<season>`
  and `<season>_<leagueId>` are ordinary raw-cache key shapes and printed verbatim into
  `ProvenanceError`, as did a name-valued key and a 22-character base64 identifier (under the token
  floor, so undetectable as a value *and* unredacted as a key). Widened to any identifier-length
  digit run anywhere in the key, any key containing whitespace, and any non-alphabetic key of 16
  characters or more. Structural camelCase keys are unaffected, which is asserted in both directions.
- **P2 — two constants disagreed with each other.** `_MIN_REDACTED_DIGITS = 6` held that a six-digit
  number is identifier-shaped enough to redact from a log while `_ID_PLAUSIBLE_MAGNITUDE = 1_000_000`
  held that it is structural noise not worth counting. Legacy ESPN league ids are five and six
  digits, so they were invisible to the count and absent from the digest. One now derives from the
  other. Floats are walked too: `{"leagueId": 17739342.0}` is what a round-trip through a JS client
  or a spreadsheet produces and it was skipped entirely.
- **P2 — the name scope was narrower than its own comment.** `abbrev` was outside it while `location`
  and `nickname` — the other two members of the same operator-chosen team triple — were inside, and
  `?view=mCommunication` league chat was outside entirely. Both added. `athletes[]` was added to the
  player exclusion: the substring test covered `player*` only, so an athletes-shaped capture forced a
  declaration over public NFL names — the 36-false-positive over-reach the scope comment says the
  rule prevents.
- **P2 — `_PLAYER_PATH_MARKER`'s companion guard was provably unreachable.** `not
  json_path.endswith("<key>")` could never change an outcome, because `<key>` is appended before the
  field match, so no key yield could set `is_name_field`. Its side effect was that a name used as a
  dict key was never counted at all. Replaced with the rule that actually covers that shape: a key
  containing whitespace is human-authored text, counted as undecidable and redacted from the path.
- **P3 — structural hazards.** A symlinked directory is invisible to `rglob`, so the gate reported
  having examined a tree it never entered; it now refuses rather than skips. A directory named
  `*.json` raised `IsADirectoryError` out of the gate instead of a `ManifestError`.

### A live residual the review surfaced, and what was done about it

The widened name scope surfaced **16 `abbrev` values** across `real_league_2025.json` and
`real_league_2026.json` — 8 per file, identical sets, 2–4 characters, entropy 1.00–2.00, matching no
scrub-placeholder pattern in this repository. The scrub pass that cleaned team names did not cover
`abbrev` and the scanner's scope did not either, so member-chosen team abbreviations were shipped
alongside scrubbed team names in files tracked in a repository intended to be published. They were
scrubbed in place to `TM<n>` (entropy 1.58, 8 distinct per file), deriving from the team index rather
than from anything in the capture. No value was printed at any point; the review was conducted over
paths and shape statistics only. The resulting diff is 22 lines per file. The pre-scrub league name
is confirmed absent from every file in the worktree; it remains in git history, which is the residual
already recorded in E31.2.

A second review of the id class, by path and shape only: every 7-digit id in the recorded captures is
a **public NFL player id** (`playerId`, and `id` within player objects; 151 in one file, 37+37 in
another, 8 each in the player fixtures), and the only 9-digit ids are the two **scrubbed league ids**
at entropy 1.35. No member ids are present. That is the basis on which `ids_reviewed` is declared
true, and it is recorded here rather than left implicit in a boolean.

### Measured gates

- `tests/test_provenance.py` **141 tests, all passing** (42 at the start of this round).
- Whole `tests/` excluding the Phase 30 macOS recovery suite: **435 passed, 2 skipped, 0 failed**.
  `tests/test_recovery.py` has 18 failures on Linux for launchd and Darwin-path reasons; it contains
  **zero references to provenance** and is unaffected by this round.
- `ruff check api tests` exit **0**.
- Both gates green over the regenerated 13-entry manifest: **5363 values scanned, 88 shape
  candidates, 0 findings**.
- **Mutation sweep: 43 mutations, 42 killed**, each by a named test. The single survivor — moving the
  entropy floor from 2.5 to 2.0 — is behaviour-preserving on both measured populations (placeholder
  maximum 1.18 bits, real-GUID minimum 2.69 bits), and both *ends* of that interval are pinned by
  `test_the_entropy_floor_sits_strictly_between_the_two_measured_populations`. It is recorded as an
  intentional survivor so it is not re-reported.
- The four mutations that had survived because a test was parametrised over the set it was testing
  (`sorted(_NAME_FIELDS)`) are now killed: the expected field names are written out as a literal and
  the set is asserted equal to it. Deleting an entry used to delete its own test — the
  self-referential shape this phase exists to remove, found in the very sweep added to prevent it.

### Residuals carried forward, stated rather than closed

- `id_fields` is dominated by public player ids (151 of 152 in one capture), which asks a human to
  "review 152 ids" that are overwhelmingly public data. Narrowing the count is a scope reduction to a
  safety control and is deferred to its own round with its own two-sided tests rather than slipped in
  after review.
- Nested JSON-in-a-string hides the name and id **declaration** side (the GUID and token rules still
  fire inside it). The raw-response cache is exactly that shape.
- Unicode evasions — zero-width space, soft hyphen, fullwidth hex digits — each yield zero
  candidates. Judged out of scope for a capture pipeline and within the separator-encoded residual
  already disclosed in E31.2.
- Two operator-action items from unit 2 remain open: `api/Dockerfile` sets no `APP_MODE` and so fails
  open, and `extra="ignore"` silently drops a misspelled `APP_MODE`.

## E31.5 — Unit 4, spend ledger and Alembic baseline

Criteria 6, 7 and 8. Built, reviewed in parallel by `qa_test` and `cybersecurity`, returned
**NO-CLOSE by both**, rebuilt, and re-measured. The findings below are almost all theirs; the ones
Agent 1 found are marked.

### What was built

- `api/services/spend.py` — reserve before the call, settle after, `unknown_spent` when the response
  is lost. Amounts are integer **micro-dollars** end to end, never floats: a ceiling compared against
  accumulated binary floating point is off by a representation error some of the time, and "some of
  the time" is not a bound.
- The ceiling is a single counter row updated by a conditional `UPDATE ... WHERE committed + :amount
  <= :ceiling`. `SELECT SUM(...)` then `INSERT` is not a bound — two processes both read a total
  under the ceiling, both insert, and neither was wrong when it looked.
- `api/models.py` — `ai_spend_months` (the counter) and `ai_spend_entries` (the audit trail).
- `api/ai_config.py` — model prices from operator configuration, with **no default price table**. A
  default would be a number invented in this repository and then trusted by a ceiling, which reads as
  enforcement while bounding nothing. An unpriced model stops the call.
- `alembic/` — baseline plus one additive revision, the baseline cut by autogenerating against models
  with the ledger removed so revision 0002 could be *proven* additive: it detected exactly two tables
  and one index and nothing else, which is only possible if the baseline matches everywhere else.

### Contract amendments, recorded rather than assumed

1. **`api/services/recovery.py` is outside unit 4's owned paths and had to change.** Phase 30 made
   the schema an allowlist that fails closed on any table it has not been told about, and it pins the
   catalog by exact SHA-256. The same contract requires "one additive revision for ledger state", so
   the owned-paths list could not satisfy the contract it belongs to — the same reason the unit-2
   amendment was taken. Two `EXPECTED_TABLE_COLUMNS` entries added; the two pinned catalog digests
   **replaced**, not extended, because a catalog without the ledger can no longer reach that check at
   all. Each new digest was reproduced from a real database by a recipe first confirmed to reproduce
   the previous pinned pair exactly.
2. **The ledger tables are RETAINED in the recovery bundle, not excluded like `raw_cache`.** A
   restore that dropped them would reset the month's committed total, so "restore from backup" would
   become a way to clear the ceiling.
3. `alembic` added to the dev dependencies; `alembic/versions/*` exempted from the line-length rule,
   because reflowing generated DDL by hand is how a migration acquires an edit nobody reviewed.
4. `AiSpendBlockedError` subclasses `AiError` so the existing routers render it as their ordinary
   secret-free error envelope. A new sibling class would have needed every router touched, and any
   router missed would have answered a refused-for-cost call with a stack trace.

### Agent 1's own finding, before review

SQLite does **not** honour `busy_timeout` when promoting a transaction that has already read into a
writer. A `reserve` beginning with `SELECT` therefore failed closed under ordinary contention, and 40
concurrent reservations produced lock errors rather than ceiling refusals. `reserve` now makes a
no-op `UPDATE` its first locking statement.

### Parallel review, round 1 — both reviewers, both NO-CLOSE

Findings reported by **both independently** are listed first. As in unit 3, those were real every
time.

- **P1 — the concurrency gate for criterion 7 was about 30% flaky, on the unmutated tree.** One
  reviewer measured 2 passes in 12, the other 6 failures in 20 across the suite and 14 in 20 for the
  test alone, both always on `assert set(refused) == {"ceiling"}`. Two causes: the period check ran
  before `PRAGMA busy_timeout` was installed, so the one statement the pragma existed for was the one
  it did not cover; and Agent 1's rewind cross-check read the counter and the entries sum outside the
  write lock, so ordinary concurrency looked identical to a rewind. **A gate that is green by luck is
  not a gate** — and one reviewer showed the consequence directly: re-running Agent 1's 30-mutation
  sweep with that test deselected left **13 of 14 mutations in the first batch alive**. The sweep
  Agent 1 reported as 30/30 was substantially the flaky test failing at random. Fixed and measured at
  **20 passes in 20**, and the gate now runs the race five times rather than once.
- **P1 — `_finish` was a read-check-write with no atomicity.** Both reviewers measured two concurrent
  settles of one reservation both passing the state test and both applying their delta, driving the
  month counter **negative**; one also measured a sweep racing a settle refunding a reservation the
  sweep had just declared crashed — the release path the module's own docstring says does not exist.
  The state transition is now the conditional `UPDATE` itself, with `rowcount` as the verdict.
- **P1 — the ledger committed and rolled back the CALLER's session.** Measured destroying an
  `AiReport` the same request had already generated and flushed, after which the cached-only path
  reported no cached report for it. The ledger now opens its own session; `generate` **commits** the
  caller's pending work before reserving, rather than leaving it to be rolled back — on one SQLite
  file a second connection cannot write while the first holds a write lock, and committing the charge
  before the call is what makes a crashed call non-free.
- **P2 — `_finish` ignored the month `UPDATE`'s rowcount**, silently discarding an overrun while
  still marking the entry settled.
- **P2 — the stated overrun bound was false.** "At most one call's overrun" ignores concurrency; one
  reviewer measured 50 in-flight calls settling at four times their hold, ending at **$20.00 on a
  $5.00 ceiling**. The docstring now states the real formula and names the missing control.
- **P2 — `sweep_stale` accepted naive and foreign-zone cutoffs.** SQLite stores `created_at` naive
  and drops the offset, so a cutoff in UTC+14 **swept a live reservation**, whose settle was then
  refused and whose month stayed charged in full forever.
- **P3 — the clock guard defended one direction.** The forward direction is the one that GRANTS
  budget: a jump to 2099 minted a period, and the fabricated period then refused every real month
  forever. Periods now advance one month at a time, bounded at both ends by the ledger's own highest.

Found by one reviewer, accepted and closed:

- **P1 — a zero-cost report refunded the entire hold.** `settle` refused only negatives, so a client
  reporting `TokenUsage(0, 0)` — one SDK field rename away, and the code already builds usage with
  `int(getattr(usage, "input_tokens", 0) or 0)` — zeroed the month on every call. Measured: 60 calls,
  committed `0`, the full $5 still showing as remaining. An unusable cost report is the same
  epistemic state as a lost response and now gets the same handling. **No test called `settle(0)`, so
  there was no assertion for a mutation to break** — the clearest example in this phase of a mutation
  sweep proving less than it appears to.
- **P1 — `alembic downgrade -1` then `upgrade head` granted a fresh $5 in an already-spent month**,
  repeatably, from any shell in the container, with no argument and no confirmation because `env.py`
  reads the URL from `Settings`. `downgrade()` now refuses.
- **P2 — the spend gate was mechanism (1), which this contract itself rates procedural.** The
  reservation is now a parameter of the `LlmClient` protocol and the production client refuses a
  required-but-absent one, so calling the model without having charged for it is something a caller
  has to write on purpose.
- **P2 — `OverflowError` escaped `SpendLedgerError`**, against a docstring promising that catching
  the base class caught everything the ledger could do. `pysqlite` raises it bare for an integer too
  large for SQLite, so SQLAlchemy never wrapped it. All ledger failures are now converted.
  Separately, a client whose `last_usage` is not a `TokenUsage` made reconciliation raise
  `AttributeError` **after** the call was billed, discarding a paid-for result.
- **P2 — nothing tied the reservation to the call.** Mutating `generate` to reserve against an empty
  prompt, and to request ten times the reserved output budget, both left the suite green — the
  under-reserve direction this module's own docstring names as the one that lets a ceiling be stepped
  over. One binding each, used by both the reservation and the call.
- **P2 — both cached-only tests passed against a `generate` that ignored `force=`**, so the ordinary
  input-hash cache could have been what answered and neither test established that the cached-only
  path ran. A `cached_only` trip indicator now distinguishes them (the contract's forbidden-paths
  clause exempts "any strictly required cached-only trip indicator", so one was contemplated), a
  separate test pins that `force` really regenerates, and a refused `week=9` request no longer
  returns the stored week-1 report with nothing marking it as substituted.
- **P2 — the Alembic drift test compared names only.** Creating the counter as TEXT, making the hold
  nullable, and dropping either UNIQUE constraint or the composite index all survived. Dropping
  `uq_ai_spend_months_month` **breaks the ceiling outright**: two rows for one month, the conditional
  UPDATE charges the one reading zero while the counter read returns the other, measured granting
  $4.00 on top of a full month. The test now compares declared types, nullability and every index.
- **P3 — two Alembic tests were source greps.** One asserted `env.py` contains the string
  `get_settings().sqlalchemy_url`, which stays true when that wiring is moved into a function nothing
  calls; both are behavioural now, and `alembic check` exercises env.py's own `target_metadata`
  rather than re-deriving it.
- **P3 — `.desc()` on the period floor was untested** (one month row makes ascending and descending
  agree), as were `row.month` vs `handle.month`, the sweep's state filter, and half of each negative
  token guard.

### A near-miss worth recording

`alembic/env.py` originally set `sqlalchemy.url` from `Settings` **unconditionally**, overriding a
URL the caller had already set. Run outside the test harness — which redirects `DB_PATH` before any
`api` import — `alembic upgrade head` would have targeted the operator's real 886 MB database. It did
not: a read-only check confirmed 18 tables, no `alembic_version`, no `ai_spend*` tables and
`quick_check: ok`. The wiring now defers to an explicitly-provided URL, with a behavioural test.

### Measured gates

- `tests/test_spend.py` **83 tests**; whole `tests/` excluding the Phase 30 macOS recovery suite
  **520 passed, 0 failed**; `ruff check api tests alembic` exit **0**.
- `tests/test_recovery.py` still **exactly 18** failures on Linux, all launchd/Darwin, unchanged by
  this unit — criterion 10. It contains zero references to the ledger.
- The concurrency gate: **20 passes in 20** runs, where the reviewers measured 2 in 12 and 6 in 20.
- **Mutation sweep: 59 mutations across `spend.py`, `ai.py`, `ai_config.py`, `models.py` and the
  migration; 58 killed**, each by a named test. The single survivor — removing the `state` filter
  from `sweep_stale`'s candidate SELECT — is behaviour-preserving, because the per-row conditional
  UPDATE carries the same predicate and is what actually decides. It is recorded in the code so it is
  not re-reported as an untested line.

### Open items, stated rather than closed

1. **Operator ruling required: the spend bound is off by default.** `_spend_bound_required()` is true
   in hosted mode OR when prices are configured, and both are off in a default configuration, so a
   container with only an API key set has no ceiling. The contract's own Synthesis says "`Settings`
   gains a **required, explicit** mode", and `app_mode` was given a default instead — the reviewer is
   on the contract's side and Agent 1 is not. Making it required fails closed for every deployment
   that omits it, which is the point, and is a one-line change. It is not taken unilaterally because
   it will stop any existing environment that has no `APP_MODE` from starting at all. This subsumes
   the carried `api/Dockerfile` item.
2. **A full database restore rewinds the ledger.** The cross-check catches every partial loss — a
   dropped, truncated or hand-edited counter row while the entries survive — but not a restore, where
   the witness and the evidence come back together. A container on an ephemeral volume is the same
   hole. Stated in the module docstring rather than implied away.
3. **No cap on simultaneously in-flight reservations**, so the month can exceed the ceiling by the sum
   of their overruns rather than by one. The docstring now says so.
4. **`sweep_stale` has no production caller**, so criterion 6's crashed-reservation state is produced
   only by the test harness. The CHARGE is applied at reserve time, so the bound holds and this is
   audit-trail completeness, not a bypass.
5. `ceiling_micro_usd` is a constructor argument rather than a module invariant, and `reserve` takes a
   public `now=`. Both exist for the tests and neither has a production call site.
6. `extra="ignore"` still drops a misspelled `APP_MODE` key silently (carried from unit 2). A
   misspelled *value* fails closed at import, which is correct.

## E31.6 — Phase close-out

### The operator ruling that was open in E31.5

The operator ruled: **make `app_mode` required.** It now has no default, so `Settings()` refuses to
construct without it and a process that has not said what it is does not start. This closes the
finding both reviewers circled from opposite directions — that the dangerous configuration was
reached by OMITTING a setting while the safe one had to be expressed — and it restores what the
contract's own Synthesis said all along: "`Settings` gains a **required, explicit** mode."

What that required, stated plainly rather than left as a surprise:

- `.env` on the operator's machine had no `APP_MODE`, so the application would have stopped starting.
  One non-secret line was appended (`APP_MODE=private_operator`); nothing in that file was read,
  printed or otherwise handled.
- `.env.example` now documents the setting and both values.
- `api/Dockerfile` deliberately still sets nothing, and says so in a comment. A container started
  without `APP_MODE` fails to construct `Settings` and does not serve — which is the intended
  behaviour, because a hosted image that silently defaulted to `private_operator` would have the ESPN
  provider, the credential decrypt path and the raw cache all enabled and the spend ceiling off.
  Baking a value in would put the decision back in an image rather than in the deployment that knows
  the answer. **This closes the carried unit-2 item**, which is why it is not repeated below.
- The comment claiming `frozen` "makes the hosted boundary a process-lifetime invariant" is corrected.
  It is a typo guard: it refuses `settings.app_mode = ...` and `object.__setattr__` still gets
  through. The boundary is that the value must be declared before the process exists.

### The eleven criteria, measured as one gate

`tests/test_phase31_acceptance.py` is that gate, kept in the repository rather than run once and
described: one assertion per criterion, through the real code path, so this is re-runnable rather
than a list of test names to be trusted. 19 cases, all passing.

| # | Criterion | Measured |
| --- | --- | --- |
| 1 | Hosted config cannot instantiate `EspnService` | `HostedModeForbidden: hosted synthetic mode forbids real provider construction`, raised in a child process because `app_mode` is fixed for the life of a process |
| 2 | Hosted config cannot decrypt a cookie or reach the accounts credential path | `HostedModeForbidden: hosted synthetic mode forbids credential custody`, at the `_fernet()` chokepoint |
| 2b | Both errors stable and secret-free | Neither message contains `espn_s2`, `swid`, `sk-` or `cookie=` |
| 3 | Unknown fixture paths raise; every fixture string matches the grammar | 6/6 probes refused, including traversal, case variants and a well-formed unknown name |
| 4 | SWID, cookie, member-name and league-name probes fail the build | 4/4: `ProvenanceError`, `ProvenanceError`, `ManifestError`, `ManifestError` |
| 4b | On **content**, not shape, two-sided | A real v4 GUID is detected; a placeholder of identical shape is not |
| 5 | Deterministic seeds reproduce identical corpora | Seed 4242 reproduces byte-identically, 4243 differs, 115 leagues, colliding tenants present |
| 6 | Usage persisted for success, lost response and crashed reservation | `settled`/40,000 · `unknown_spent`/full hold · `reserved`/full hold; month committed = 240,000, which is the sum of all three and not merely three state strings |
| 7 | Parallel reservations cannot exceed the $5 UTC-month ceiling | 40 workers, 20 granted / 20 refused, every refusal a ceiling refusal, committed exactly 20x250,000 = $5.00, entries = granted, so a refusal leaves no row |
| 8 | Ledger failure and ceiling breach both give cached-only, no queued retry | Both routes through the real `AiService`: **0** model calls, both attempts marked `cached_only`, the second identical to the first — nothing was queued. Plus a structural check that no sleep, backoff, queue or `apply_async` exists in the reserve-call-settle path |
| 9 | `make test` passes; `ruff check api tests` exits 0 | **541 backend tests passed, 0 failed**; `ruff check api tests alembic` exit 0 |
| 10 | Phase 30 recovery gates still pass unchanged | `tests/test_recovery.py` exactly **18** failures, the same launchd/Darwin platform set as before the phase; it contains zero references to any Phase 31 module |
| 11 | `real_*.json` cannot be added without a manifest entry declaring it scrubbed | `fixtures present without a manifest entry: ['real_new_capture.json']` |

Criterion 9's frontend clause: **Phase 31 changed no file under `web/`**. The frontend changes in the
worktree belong to earlier phases, including the Phase 29 corrective closure, which records that
suite's own result. Agent 1 did not run the browser suites and makes no claim about them beyond
"unaffected by this phase", which the diff establishes.

### What this phase actually changed, in one paragraph

Public hosted mode can no longer construct the ESPN provider, decrypt a credential, write the raw
cache, or load anything but an allowlisted synthetic fixture — all by construction, not by a flag a
call site is trusted to check. Every fixture in the tree, at any depth and any extension, must carry
a human origin declaration bound to the file's bytes, and a build fails on real identifier content
judged by entropy rather than by shape. AI generation charges a worst-case reservation against an
integer-micro-dollar UTC-month ceiling before the model is called and reconciles afterwards, so a
crashed call is never free, and both a ceiling breach and a ledger failure degrade to cached-only
with no retry and no queue. And the process must declare which of those two worlds it is in before it
will start at all.

### The pattern this phase was about

Fourteen separate times across units 2-4, a check was green while establishing something other than
what it claimed: a scanner whose declaration vouched for a projection of a file rather than the file;
a control switched on by a filename convention; a test parametrised over the very set it was testing,
so deleting an entry deleted its own test; a concurrency gate that passed about 70% of the time and
made a 30-mutation sweep look conclusive; a comment promising that a free-text field was scanned for
names by rules that cannot see a name. Agent 1 wrote every one of them, found four, and independent
review found the rest. Across three review rounds in unit 3 and one in unit 4, **every finding that
both reviewers reported independently was real**, without exception. That is the strongest single
argument in this record for the operator's decision to keep parallel review after Agent 1 proposed
dropping it.

### Residuals carried out of the phase

1. A full database restore to an earlier point in the same UTC month rewinds the ledger. The entries
   cross-check catches every partial loss; it cannot catch a restore, where the witness and the
   evidence return together. A container on an ephemeral volume is the same hole.
2. No cap on simultaneously in-flight reservations, so the month can exceed the ceiling by the sum of
   their overruns rather than by one call's. The docstring states the real formula.
3. `sweep_stale` has no production caller, so criterion 6's crashed-reservation state is produced only
   by the test harness. The charge lands at reserve time, so the bound holds; this is audit-trail
   completeness.
4. `extra="ignore"` still drops a misspelled `APP_MODE` **key** silently. A misspelled **value** now
   fails closed at import, and a missing key fails closed too, so the remaining gap is narrow.
5. `ceiling_micro_usd` is a constructor argument and `reserve` takes a public `now=`; both exist for
   the tests and neither has a production call site.
6. Nested JSON-in-a-string hides the name and id declaration side of the scanner, and separator-encoded
   or unicode-split identifiers evade the entropy rules. Both were disclosed in E31.2 and remain.
7. The pre-scrub league name remains in git history. It is absent from every file in the worktree.

**Phase 31 `hosted-data-safety` is complete.** No provider, ESPN, Anthropic or AWS call was made; no
Keychain, Restic repository or device was touched; nothing was committed, merged or spent.
