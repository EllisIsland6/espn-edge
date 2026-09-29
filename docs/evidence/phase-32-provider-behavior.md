# Phase 32 evidence — `provider-behavior-spike`

## E32.1 — Seams built before the contract, and what they settled

### Why this entry exists before an accepted contract

The Phase 32 contract was rejected **three times** by both reviewers. Each round fixed real defects
and introduced narrower instances of the same ones: a `+W` correction became a double-count, a
`wait_ms` split made connection reuse unmeasurable, a two-verdict cache taxonomy became a
three-verdict taxonomy the nominated seam cannot emit. Both reviewers also judged the contract plus
its amendment unsatisfiable inside the plan's two sessions.

Every surviving disagreement was a question about **code**, and prose kept answering by assertion.
The operator ruled: build the seams first, offline, and write the contract against what they report.

Nothing here touches the network, and `git diff` over `api/services/espn.py`, `sync.py` and
`cache.py` contains no telemetry reference — the provider is unchanged and the new module has no
caller. `api/services/telemetry.py` and `tests/test_telemetry_seams.py` are the whole delta.

### What the probes measured

Fifteen tests, each running the **real** request path against an injected fake transport.

| # | Question three rounds of prose could not settle | Measured |
| --- | --- | --- |
| 1 | Does a cache HIT reach the request boundary? | **No.** Cold call: 1 HTTP request. Warm call: 0. Both consulted the cache. A hit rate counted at `_request` is structurally zero, in the flattering direction. |
| 2 | Is `BYPASS` observable at the nominated site, `espn.py:205`? | **No.** The `not bust_cache` guard is `:204` and the consultation is `:205` — the bypass case never reaches the line where the verdict would be taken. |
| 3 | Can a `text/html` login bounce be told from a truncated body at the boundary? | **Yes, by header — not by status.** Both are 200. Their content types differ (`text/html` vs `application/json`) and the headers are in scope before the body is parsed. |
| 4 | Does a retry re-consult the cache, inflating MISS? | **No.** Three HTTP attempts, one consultation. **This withdraws a finding Agent 1 had accepted from review**: the consultation sits in `fetch_views`, outside `_request`'s loop. |
| 5 | Are the five URL families disjoint under prefix matching? | **No.** Three collisions, all with `season_url` as the prefix of `league_modern`, `players_defaults` and `players_season`. The pre-2018 family puts the league id in the **trailing** segment. |
| 6 | What does an exhausted call sleep? | **15.0s across four sleeps — 1, 2, 4, 8.** Four attempts, four sleeps: the final 8s precedes no request at all. The contract's original "1+2+4 = 7s" was wrong. |
| 7 | Does the per-key throttle space different accounts? | **No.** Four requests across three keys produced **one** sleep — for the repeated key only. A deployment-wide budget cannot be expressed per key. |

### What the measurements changed in the design

- **`Shape` is chosen by the builder that ran, never matched out of an assembled URL.** Finding 5 is
  why: a matcher over non-disjoint families is ambiguous, and the natural fallback for an unmatched
  URL — recording the path — records the league id, which for the pre-2018 family is the last
  segment.
- **`Record` carries `content_type_json`.** Finding 3 corrects both the earlier draft and the
  reviewer who rejected it. The distinction is recoverable at the boundary, so it does **not** require
  instrumenting `_json_or_auth` — the one function that must not be instrumented, because
  `resp.request.url` is in scope there.
- **`Outcome` stays four members.** Naming a classification this seam cannot compute would move two
  behaviours from aliasing each other to aliasing *success*, which is worse than the aliasing it was
  meant to fix.
- **A cache HIT and a BYPASS are recorded where each is decidable**, which is not where the rejected
  contract put them, and a HIT produces no HTTP record by construction.
- **`RateGate` is module-level with an injectable clock and sleeper.** Finding 7 is why it exists;
  the injection is so a contention test is arithmetic rather than the wall-clock race that passed
  about 70% of the time in Phase 31.
- **`Record` has no field of a type that could hold an identifier.** Not a probe that tries forbidden
  values and fails — there is nowhere to put one.

### Measured gates

- `tests/test_telemetry_seams.py` **15 passed**.
- Whole `tests/` excluding the Phase 30 macOS recovery suite: **556 passed, 0 failed** (541 before).
- `ruff check api tests alembic` exit **0**.
- `tests/test_recovery.py` still exactly **18** platform failures, unchanged.
- The provider is untouched: no telemetry symbol appears in any diff of `espn.py`, `sync.py` or
  `cache.py`, and the recorder has no caller.

### What this leaves for the contract

The contract can now be written against seven measured facts instead of seven assertions. Still
open, and still unbuilt: wiring the recorder into `_request` behind a flag, the exporter and its
n-floor and zero-event rules, the evidence-file grammar, and the live-read envelope — which remains
**unauthorized** and is approved separately at the time of a run.

---

## E32.1a — Correction to E32.1: three claims withdrawn, unit 2 reopened

**This entry supersedes parts of E32.1 above. E32.1 is left intact — the file is append-only, and
the wrong version is part of the record.** Round-4 review returned **NO-CONTRACT** from both
reviewers. The two most serious findings were reported independently by both, which in this project
has meant *real* every time so far.

### What E32.1 got wrong

E32.1 recorded unit 2 as done on the strength of fifteen passing probes. Those probes tested the
**provider**. They did not test **`api/services/telemetry.py`**, the module unit 2 exists to
introduce. `Recorder` and `Counters` appeared zero times in the test file; branch coverage showed
`Recorder.__init__`, `disabled`, `attempt`, `cache_verdict` and `RateGate`'s late-arrival branch
never executed; **24 of 27 mutations to the module survived**. Contract draft 3 lines 21–23 claim
"every fact below is pinned by `tests/test_telemetry_seams.py`" — true of `espn.py`, false of
`telemetry.py`. **On that evidence unit 2 was not done, and is reopened.**

Three specific claims in E32.1 are withdrawn:

| E32.1 said | Withdrawn because | Now |
| --- | --- | --- |
| lines 52–53: "`Record` has no field of a type that could hold an identifier… there is nowhere to put one." | A frozen dataclass validates nothing. Review constructed `Record(…, wire_bytes="https://…/leagues/9998887", …)` and it succeeded. The probe guarding the claim **passed on that record**, because under PEP 563 `dataclasses.fields(...)[i].type` is the annotation's *source text* and the probe compared the string `"int"`. The wiring slip that reaches it is one token wide: `wire_bytes=resp.headers.get("content-length", -1)` without the `int()`. | Enforced at construction by `_FIELD_TYPES` and `Record.__post_init__`, raising `RecordRejected`; `Recorder.record(...)` converts that into a **counted drop**, so telemetry can never break a fetch. Pinned by 13 per-field cases, the exact URL case, a frozen-ness case and a duck-typed-look-alike case. The rejection message names the **field** and the offending **type**, never the value. |
| line 49: "**`RateGate` is module-level** with an injectable clock and sleeper." | There was no module-level instance and no shared lock — the state was per-instance, which is no gate at all for the portfolio loop, the precise failure the gate was introduced to fix. The probe that claimed to prove sharing constructed **one** gate and called `wait()` on it three times. | `shared_gate()` is the process-wide accessor, built once under a module lock. Pinned by three independent callers receiving the same object, an eight-thread contention probe, a later caller being unable to widen the interval, and the default interval being 1.0 s. |
| table row 3: the "truncated body" measurement | The probe did not truncate anything. `httpx.Response(200, content=b'{"a": [1,2')` derives a **consistent** Content-Length from the bytes given, so it is a *complete* ten-byte payload that happens to be invalid JSON. A real truncation — fewer bytes than a declared Content-Length — raises `httpx.RemoteProtocolError`, a subclass of `HTTPError`, which `espn.py:165` catches and **retries**, so it never records as a 200. | Split in two. The login-bounce half of row 3 **stands unchanged**: a `text/html` 200 *is* separable from a JSON 200 by header at the boundary. The truncation half is replaced by two probes: one establishes on a real wire that a short body is a protocol error, the other shows `_request` retrying it and returning a later success. The old case is relabelled "short json body", which is what it always was. |

The truncation row was **this question's third wrong answer in three rounds**. Each round narrowed
it; each round still asserted rather than measured. That is the same defect this phase was
reorganised to stop — *a check that is green while establishing something other than what it claims*
— and it was reintroduced inside the file written to prevent it.

It happened a **fourth** time during this rework, and the fourth attempt is worth recording because
it was caught by execution rather than by review. The first replacement probe used
`httpx.MockTransport` with an explicit `content-length: 512` header over ten bytes, and **nothing
raised**: a mock transport returns a response object and never speaks HTTP, so it cannot enforce a
Content-Length and could not have answered the question. The probe now stands up a loopback socket
(127.0.0.1, one connection, no egress) that declares 512 bytes and sends 10, and measures what real
httpx does with it:

```
complete   -> 200, 12 bytes, no raise
truncated  -> httpx.RemoteProtocolError: peer closed connection without sending
              complete message body (received 10 bytes, expected 512)
              isinstance httpx.HTTPError: True   <- so espn.py:165 catches it
```

### What else changed in unit 2

- `Shape.UNKNOWN` **deleted.** An `UNKNOWN` bucket turns a wiring bug into a row that looks like
  data: it cannot be audited, it is a legal value so it cannot be excluded, and it is exactly where
  a future "record the path so we can tell them apart" patch lands — which for the pre-2018 family
  records the league id in the trailing segment. Without the member, an undeclared shape surfaces
  as a counted drop.
- `except BaseException` → `except Exception` in both handlers. The old form swallowed
  `KeyboardInterrupt` and `SystemExit` inside a `try` whose body — a list append and an integer
  increment — could not fail, so the drop path was simultaneously over-broad and unreachable. The
  validation now lives inside that `try`, which is what makes the path reachable and testable.
- `dropped` and `sum(dropped_by_shape.values())` are now **allowed to differ**, and that is written
  down: a drop whose `shape` is itself the malformed field cannot be attributed, so it lands only
  in the total. The exporter must not substitute one for the other.
- The two probes that read `espn.py` as **text** and asserted on line offsets are replaced by
  behavioural forms. Both measured the source file rather than the program: the BYPASS probe would
  have passed on any refactor that kept two lines adjacent while inverting the guard. The
  replacements assert what matters — that the bypass path never consults the cache at all, and that
  a malformed body still returns from `_request` as an ordinary 200.
- One text-reading probe is **added** deliberately, and it is the one case where reading the file is
  the right instrument: a check that `espn.py`, `sync.py` and `cache.py` contain no reference to the
  telemetry module. That claim *is* a claim about the source text. It fails if anything is wired
  ahead of its gate.

### Found by mutation, not by review

Four mutations survived the first reworked suite. Three were real gaps and are now killed; each is
a control that was present in the code and held by nothing:

| Mutation | What it would have allowed |
| --- | --- |
| `@dataclass(frozen=True)` → `frozen=False` | Post-construction assignment, so validation-at-construction would guard nothing. |
| delete the `isinstance(record, Record)` raise | A duck-typed object with the right attribute names would be filed, and nothing downstream re-checks a filed row. |
| `if waited > 0` → `>= 0` | A caller arriving exactly on the interval calls `sleep(0.0)` and reports a wait it did not perform — and `gate_ms` is one of the numbers this phase exists to measure. |
| `interval: float = 1.0` → `2.0` | The deployment-wide budget widened silently, because every probe passed `interval=` explicitly. |

The one surviving mutation is `return None` → `return None` in `Recorder.disabled`, which is an
equivalent mutation and cannot be killed.

### Measured gates

Run in a **reconstructed environment**, not on the device: the operator's machine was disconnected
for this work, so the module and its suite were exercised in the cloud workspace against a reduced
tree (`api/config.py`, `api/services/{espn,cache,sync,telemetry}.py`, the suite). Python 3.11.15,
httpx 0.28.1, pydantic 2.13.3 — httpx and the app's own modules are the versions the repo pins.

- `tests/test_telemetry_seams.py` — **57 passed** (was 15).
- Branch coverage of `api/services/telemetry.py` — **115 statements, 20 branches, 100%, 0 partial**
  (was: five functions and one branch never executed).
- Mutation sweep over `api/services/telemetry.py` — **38 of 39 killed**, the single survivor
  equivalent. Run twice with identical results; source verified byte-identical afterwards.
  (was 3 of 27.)
- `ruff check` over both files — clean except `RET501` and three `BLE001`, which are stock-ruff
  rules the repo does not select: the accepted Phase 31 code and the rejected draft of this module
  both carry the same shapes and both passed the repo's `ruff check api tests alembic`.

**Not yet re-run, and not claimed:** the whole-suite gate (556 tests), `ruff check api tests
alembic` under the repo's own configuration, and the Phase 30 recovery suite's 18 known platform
failures. Those need the device and are the first thing to run when it is back. **This entry is not
a completion claim for unit 2 until they pass.**

### Standing corrections to draft 3, carried forward

Review findings this entry **accepts but does not resolve**. They belong to the contract, not to
unit 2, and the contract is unaccepted.

- The network formula omits that `sync.py:238` — the `W_i` term — is the one call site that does
  **not** pass `bust_cache=True`.
- Byte bucketing conditions on the n-floor, which is a **sample-size** floor, not a k-anonymity
  floor; the envelope guarantees k=1 on the cold-sync-only branch.
- Criterion 8 divides by zero for both shapes it names: `players_defaults` and `players_season` have
  no caller.
- Both reviewers judged the review cadence and criterion 2 unsatisfiable within the plan's two
  sessions / 70 operator minutes.

### Status

Unit 2 is **reopened and reworked**; its completion in E32.1 is withdrawn, pending the three gates
above. The provider (`espn.py`, `sync.py`, `cache.py`) remains untouched and the recorder still has
**no caller** — wiring it is unit 3 and is gated on an accepted contract, and a probe now fails if
anything is wired ahead of that gate. No provider call, no network call, no credential read, no
commit. The live-read envelope remains **unauthorized**; contract acceptance is explicitly not that
approval.

---

## E32.1b — The device gates E32.1a deferred, now run

E32.1a's measurements were taken in the cloud workspace against a reduced tree, because the
operator's machine was disconnected for that work, and it said so and withheld the completion
claim. The machine reconnected; these are the gates it deferred, run on it, under the repository's
own `ruff` and `pytest` configuration. Python 3.14.7, macOS arm64.

| Gate | Result |
| --- | --- |
| `tests/test_telemetry_seams.py` | **57 passed**, 0 failed (was 15). |
| Whole `tests/`, excluding `tests/test_recovery.py` | **596 passed, 2 skipped, 0 failed**; exit 0. |
| `ruff check api tests alembic` | **All checks passed**, exit 0. Confirms that the four findings E32.1a reported from stock `ruff` (`RET501`, three `BLE001`) are rules this repository does not select. |
| `tests/test_recovery.py` | **257 passed, 18 failed** — exactly the pre-existing macOS platform failures, unchanged. |
| Mutation sweep, `api/services/telemetry.py` | **38 of 39 killed**, run in two chunks (19 + 19), the single survivor the equivalent `return None`. Independently reproduces the cloud result on a different Python. Source verified byte-identical against a backup held outside the repository after each chunk. |
| Branch coverage, `api/services/telemetry.py` | **115 statements, 20 branches, 100%, 0 partial.** |

**E32.1a's completion condition is therefore met** and unit 2 is complete as reworked. What is
complete is the *seam module and its suite*; the contract is still unaccepted and unit 3 is still
gated on it.

### A counting correction to E32.1

E32.1 recorded "**556 passed**, 0 failed (541 before)" for the whole suite. Measured today at the
same scope: everything except `tests/test_telemetry_seams.py` is **539 passed, 2 skipped**, and the
old seam file held 15 tests. So that run was 554 passed + 2 skipped = **556 collected** — the entry
reported the collected total as the passed total. Nothing was failing and nothing is wrong with the
code; the number was simply read from the wrong column, in an evidence file whose purpose is that
the numbers in it can be re-derived. Today's figure is stated as passed / skipped / failed
separately so it cannot be misread the same way.

### Repository state

- `api/services/telemetry.py` SHA-256 `72c7fee62103ef064a1238808d791a301357ebee999c5d9bc0bcc92e82a91628`
- `tests/test_telemetry_seams.py` SHA-256 `5bccd8219c88a43583a89c45cdae0c687ebd2916e947d4ab0596f19c3a8f5eb0`
- The provider is untouched: `espn.py`, `sync.py` and `cache.py` carry no telemetry reference, and
  a probe in the suite fails if that stops being true.
- Untracked worktree entries: **46**, the count before this work unit. The mutation harness and the
  coverage database live under the gitignored `.venv/`; nothing was added to the worktree inventory
  and nothing pre-existing was modified or removed.
- No commit, no merge, no network call, no provider call, no credential read, no spend. The
  live-read envelope remains **unauthorized**.

---

## E32.1c — Round 5: NO-UNIT from both reviewers. E32.1b's completion claim withdrawn.

Both reviewers returned **NO-UNIT** on the unit-2 rework, having verified they were reading the
right bytes. Every finding the two reported independently was real and reproduced here before
anything was changed. That is now true of every overlapping finding in this project without
exception.

### The finding that matters is not the list of bypasses

It is that **the probes guarding three controls stayed green with the controls removed.**

| Control | What its removal costs | What the suite said |
| --- | --- | --- |
| `RateGate.wait()`'s `with self._lock:` | Eight callers at a 0.25 s interval produced one start and then **seven at the same instant** — a thundering herd, the exact failure the gate was introduced to prevent. | All forty probes passed. |
| `shared_gate()`'s module lock | Two threads can build two gates, so the process-wide gate is not process-wide. | The "built once under contention" probe passed **2000 times out of 2000** — it compared object identities after every thread had joined, and `RateGate.__init__` is too short to lose a check-then-set race under the GIL. |
| "a later caller cannot widen the shared interval" | `_reset_shared_gate()` then `shared_gate(interval=3600.0)` — two public calls, no privilege — set the deployment-wide budget to one request per hour. A third path needed no reset at all: `interval` was a plain attribute, so `shared_gate().interval = 0.0` disabled it. | The probe pinning the guarantee passed throughout. |

A probe that passes with its control removed is this project's recurring defect — *a check that is
green while establishing something other than what it claims* — three more times, in the file
written to stop it.

### The other confirmed findings, all reproduced

- **A `Record` subclass filed a league URL.** `__post_init__` iterated `_FIELD_TYPES`, the TABLE,
  rather than `dataclasses.fields(self)`, the INSTANCE. A subclass's extra fields were not merely
  unchecked, they were invisible; `attempt`'s guard was `isinstance`, which a subclass satisfies;
  and `record.__post_init__()` dispatched virtually, so a subclass overriding it had no check at
  all. `test_the_validation_table_matches_the_fields_exactly` stayed green because it inspects the
  base class. Both reviewers found this independently. Measured: `class Quiet(Record)` with a
  no-op `__post_init__` filed a record whose `wire_bytes` was the URL.
- **A stray attribute on a plain `Record` was filed.** No `__slots__`, so
  `object.__setattr__(record, "url", …)` added something the field walk could not see, the
  dataclass `repr` did not show, and `vars()` would hand to any exporter reflecting over `__dict__`.
- **An object impersonating an enum was accepted.** `isinstance` honours a `__class__` property.
  The impostor's payload reached a `Record`, and through `cache_verdict` it reached
  `counters.cache` and `dropped_by_shape` — the structures most likely to be serialised into a
  published artifact.
- **`recorder.attempt(Record(...))` raised into the caller.** The constructor runs before `attempt`
  is entered, outside any `try` — and the docstring recommended that form "for callers that built
  one themselves". The one-token wiring slip the module was rewritten to defend against would have
  broken the fetch, which is the outcome the class exists to prevent.
- **`_FIELD_TYPES` was a mutable module global.** `_FIELD_TYPES["wire_bytes"] = str` made a URL a
  legal byte count for the rest of the process. The privacy control had no integrity boundary.
- **The value-free `__repr__` had been dropped.** Drops are silent — `record()` swallows the
  rejection and logs nothing — so `repr` and a pytest assertion diff are the only places a rejected
  value ever becomes visible, and CI logs are the most-copied artifact in this project.
- **Pickle did not re-validate.** A tampered record survived a round trip intact.
  (`dataclasses.replace` did re-validate; that was checked and was already correct.)
- **A type name can itself be a payload.** `type(x).__name__` is interpolated into the rejection
  message, and a class can be created at runtime with an arbitrary `__name__`.
- **`Outcome`'s docstring claimed a recording behaviour nothing measures.** It said a truncated body
  "records as `TRANSPORT_ERROR`, and as `EXHAUSTED` if it recurs… It is now measured, in
  `test_a_truncated_body_is_a_transport_error_not_a_200`" — a test that constructs no `Outcome` at
  all. Nothing in this module maps an httpx result to an `Outcome`; that is unit 3. This is the same
  overclaim E32.1a withdrew, committed one docstring later.
- **`except BaseException` → `except Exception` was presented as a fix and held by nothing.** So was
  the narrowing in the other direction: `except RecordRejected` also survived, because every object
  the probe passed failed an earlier check first.
- **The counters had no lock** while `RateGate` reasoned explicitly about eight-thread contention.
  `counters.cache`'s read-modify-write lost 53–70% of its counts with the switch interval turned
  down.
- **`Recorder.records` was unbounded**, in a module whose unit-3 wiring appends one row per HTTP
  attempt in a long-lived process.
- **The loopback socket test stranded a thread** for the rest of the session if the test body failed
  before the client connected: `accept()` had no timeout and `server.close()` does not wake it.

### The correction that applies to this evidence file, not the code

E32.1b reported **"mutation sweep 38 of 39 killed, the one survivor equivalent."** Review is right
that this number was the wrong shape. 38/39 describes **the harness's mutation universe**, not the
adequacy of the suite: the harness swaps operators and constants, and it never generates "delete
this lock" or "replace this identity check with `isinstance`", which are the edits that mattered.
A high kill rate over a narrow universe reads as assurance and is not. One reviewer independently
found seven semantically meaningful surviving mutations the harness does not produce.

So the instrument changed. **Control-removal testing** (`.venv/control32.py`) enumerates each
control in the module by name, removes it with the exact edit a careless refactor would make, and
reports which probe objects. A control with no objector is a control held by nothing. The generic
sweep is still run, as a secondary signal, and is reported as what it is.

The first run of that harness reported two controls unheld, and **one of the two was the harness's
own fault**: it "removed" the value-free `__repr__` by flipping `repr=False` to `repr=True` in the
decorator, which does nothing, because `@dataclass` does not overwrite a `__repr__` defined in the
class body. An instrument that reports a control unheld on the strength of an edit that removed
nothing is the same defect one level up, and it is recorded here rather than quietly fixed. The
inert `repr=False` flag has been deleted from the module for the same reason: a flag that looks
like a control and is not is what this module keeps being rejected for.

Three further corrections to E32.1a and E32.1b:

- **"19 + 19" was ambiguous.** Those were kills per chunk, not mutants per chunk. Stated explicitly
  below.
- **"the recorder still has no caller" was unscoped in E32.1a and scoped to three files in
  E32.1b**, and the two entries disagreed. The probe now walks **every** module under `api/` with
  an AST scan, and a second probe imports the provider in a clean subprocess and asserts
  `api.services.telemetry` never enters `sys.modules` — which covers a transitive import at any
  depth, as a per-file scan structurally cannot.
- **Withdrawn line numbers were still cited as fact.** `espn.py:204`, `:205` and `:165` appear in
  E32.1 and E32.1a as evidence, and the probes that produced them were withdrawn as the wrong
  instrument. Treat those three citations as unverified; the behaviours they were offered for are
  now established behaviourally and do not depend on them.

### What unit 2 is now

- `Record` walks `dataclasses.fields(self)` and refuses a field with no rule; `slots=True`;
  sealed against subclassing by `__init_subclass__`; every type test is `type(x) is T`;
  `_FIELD_TYPES` is a `MappingProxyType`; `__getstate__`/`__setstate__` pin one pickle format and
  re-validate; `__repr__` prints a field's value only once it is known to be an int or a bool, and
  `Counters.__repr__` does the same for its keys; the rejection message's type name is filtered.
- `Recorder.record(...)` is the **only** entry point, building the `Record` inside its own `try`.
  `attempt` is gone. Updates are serialised. `records` is capped at 50,000 with a counted
  `overflowed`, which is deliberately not the same thing as `dropped`.
- `RateGate` keeps the injectable clock and sleeper; `interval` is read-only. `shared_gate()` takes
  **no arguments at all** — the first caller could otherwise fix the budget for the process, and the
  argument nobody was guarding was `sleeper`, where a no-op fabricates `gate_ms` (measured: 10.0 s
  of reported throttling against 0.0 s of real blocking, every probe green).
- **`_reset_shared_gate` is gone from `api/`.** The test suite rebinds the module global itself, in
  a fixture, where it is visibly a test doing something irregular. Shipping a disarm function for a
  suite's convenience was the defect, not the thing to re-test.
- Two unreachable lines were **deleted** rather than covered: a defensive `isinstance` and a second
  unbound `__post_init__` call inside `record()`, both dead once the class is sealed and that is the
  only construction site. The generic sweep found them by deleting them with nothing noticing.
- `Shape.UNKNOWN` stays deleted. The `Outcome` docstring now states what the probes establish about
  the boundary and says plainly that no code in this module assigns an `Outcome`.

### Measured

Taken in the cloud workspace (Python 3.11.15, httpx 0.28.1, pydantic 2.13.3) against a reduced tree,
because the operator's machine was disconnected for this work. **The device gates are not yet
re-run and this entry is not a completion claim for unit 2 until they are.**

- `tests/test_telemetry_seams.py` — **106 passed, 1 skipped** (was 57). The skip is the subprocess
  import probe, which needs the complete package and will run on the device; the AST scan covering
  the same claim runs everywhere.
- **Control removal — 21 of 21 controls held**, each by a named probe that fails when the control
  is removed. Source verified byte-identical after the run.
- Generic AST mutation sweep — **58 of 59 killed over 59 sites**; the single survivor is
  `return None` → `return None` in `Recorder.disabled`, an equivalent mutation. Reported as a
  secondary signal over a narrow universe, not as an adequacy measure.
- Branch coverage of `api/services/telemetry.py` — **150 statements, 28 branches, 100%, 0 partial.**
- **Flakiness — 0 failures in 12 consecutive runs.** The suite now has seven threaded probes, and
  Phase 31 shipped a contention test that passed about 70% of the time; every one of these is
  forced by a barrier or an injected clock rather than by timing.
- `ruff check` — clean but for `RET501` and two `BLE001`, stock rules this repository does not
  select (confirmed against the repo's own configuration in E32.1b).

### Status

Unit 2 is reopened for the second time. E32.1b's completion claim is **withdrawn**. The provider
remains untouched, the recorder still has no caller, and no provider call, network call, credential
read, commit or spend occurred. The live-read envelope remains **unauthorized**.

---

## E32.1d — The device gates E32.1c deferred, now run

E32.1c's measurements were taken in the cloud workspace against a reduced tree, because the
operator's machine was disconnected, and it withheld the completion claim on that basis. The
machine reconnected. These are the gates it deferred, run on it, under the repository's own `ruff`
and `pytest` configuration. macOS arm64, Python **3.14.7** — a different interpreter from the
3.11.15 the cloud figures came from, so agreement between the two is worth something.

| Gate | Result |
| --- | --- |
| `tests/test_telemetry_seams.py` | **107 passed, 0 failed, 0 skipped.** One more than the cloud's 106: the subprocess import probe skips where the package is incomplete and runs here. |
| Whole `tests/`, excluding `tests/test_recovery.py` | **646 passed, 2 skipped, 0 failed**; exit 0. Reconciles exactly: 539 everything-else + 107 seam = 646. |
| `ruff check api tests alembic` | **All checks passed**, exit 0. Confirms again that `RET501` and `BLE001` are rules this repository does not select. |
| `tests/test_recovery.py` | **257 passed, 18 failed** — the pre-existing macOS platform failures, unchanged. |
| **Control removal** | **21 of 21 controls held**, each by a named probe that fails when the control is removed. Same result as the cloud, on a different interpreter. Source verified byte-identical afterwards. |
| Generic AST mutation sweep | **58 of 59 killed** (two chunks of 29 kills each), the single survivor `return None` → `return None` in `Recorder.disabled`, an equivalent mutation. Reported as a secondary signal over a narrow universe — see E32.1c on why the previous framing of this number was wrong. |
| Branch coverage, `api/services/telemetry.py` | **150 statements, 28 branches, 100%, 0 partial.** |
| Flakiness | **0 failures in 10 consecutive runs.** The suite has seven threaded probes; each is forced by a barrier or an injected clock, never by timing. |

**E32.1c's completion condition is met.** Unit 2 is complete as reworked — the seam module and its
suite. The contract remains unaccepted and unit 3 remains gated on it.

### Repository state

- `api/services/telemetry.py` SHA-256 `4953d095fcc7c091b0df6d3dbdb137e897d20b9b8fd41519ba44d152579eb0e2`
- `tests/test_telemetry_seams.py` SHA-256 `29cc70538412e207c0e056d723bdc2c094740fe7b2cf7584d1b0517c291715f1`
- The provider is untouched. Two probes hold that now: an AST scan over **every** module under
  `api/`, and a clean-subprocess import of `espn`, `sync` and `cache` asserting
  `api.services.telemetry` never enters `sys.modules` — which covers a transitive import at any
  depth. The scoping disagreement between E32.1a and E32.1b is resolved in favour of the broader
  claim, now that a probe supports it.
- Untracked worktree entries: **46**, unchanged. Both harnesses (`.venv/mutate32.py`,
  `.venv/control32.py`) and the coverage database live under the gitignored `.venv/`; nothing was
  added to the worktree inventory and nothing pre-existing was modified or removed.
- No commit, no merge, no network call, no provider call, no credential read, no spend. The
  live-read envelope remains **unauthorized**.

---

## E32.1e — Round 6: NO-UNIT from both reviewers. E32.1d's headline claim withdrawn.

Both reviewers returned **NO-UNIT** on the round-5 rework, having verified they were reading the
right bytes. Every finding reproduced here before anything was changed. Across this project, every
finding the two reviewers have reported independently has been real — still without exception.

### E32.1d's headline number was false

E32.1d reported **"Control removal — 21 of 21 controls held, each by a named probe that fails when
the control is removed. Same result as the cloud, on a different interpreter."**

An independent run of the same harness on the same bytes measured **20 of 21**, and the mechanism is
not luck. `Recorder` has **one** lock used at **three** sites. Removing it from `cache_verdict`
alone is *masked* by the still-locked `record()`, because the only probe touching both alternated
them and the locked call serialised the eight threads that were supposed to collide in the unlocked
one. A wall-clock contention probe — turn the switch interval down and hope — cannot distinguish
"the lock kept them apart" from "they never overlapped". Two runs of that shape agreeing is weak
evidence, and the third disagreed.

Three further claims in E32.1d are withdrawn or narrowed:

- **"each by a named probe that fails when the control is removed"** — the harness recorded whatever
  went red, not what objected. Removing `_FIELD_TYPES`'s read-only-ness listed four probes, three of
  which were collateral: the first probe's own `_FIELD_TYPES["wire_bytes"] = str` executed before
  `pytest.raises` reported it, poisoning every later test in the process.
- **"each is forced by a barrier or an injected clock, never by timing"** — false for the two probes
  that mattered. `test_every_caller_gets_its_own_slot_under_contention` synchronised only the
  *start*, so nothing inside `wait()` released the GIL and the eight short critical sections ran one
  at a time regardless; it passed with the lock removed.
- **"21 controls"** over-counted: three pairs were two mutations of one control each.

### The three P1 privacy bypasses, all measured

The round-5 fixes closed the **spelling** of each vector rather than the vector:

| Round-5 fix | How round 6 defeated it |
| --- | --- |
| `dict` → `MappingProxyType` for the rule table | `gc.get_referents(proxy)[0]` returns the backing dict. One write made a league URL a legal `wire_bytes`, and because `__repr__` decides what is safe to print **from the same table**, the same write disarmed the value-free repr too. The control and its backstop shared one point of failure. |
| `__setstate__` re-validates | It validated *after* writing. Called on a record already in `recorder.records`, it mutated that record past `frozen=True`; the validation then raised, but the payload was already in the slots, the object was still in the list, and `__getstate__`, `pickle` and `dataclasses.asdict` all carried it. Re-validating after the write made the write the delivery mechanism. |
| `isinstance` → `type(x) is Shape` | Stops an impostor, not a **genuine** member. Enum members are ordinary mutable objects: `Shape.SEASON._name_ = <payload>` succeeds, `type(member) is Shape` stays true, and both reprs printed it through `.name` while `.value` — what an exporter serialises — carried it verbatim. |

The two round-5 fixes that **held** were `slots=True` and `__init_subclass__`. Those changed what
the object *is*; the three that failed changed which name reaches it. That is the lens the rework
was built with.

Also confirmed and fixed: `recorder.record(**{1: 2})` raised `TypeError: keywords must be strings`
at **argument binding**, outside the guarded body — the same shape as the round-5
`attempt(Record(...))` finding, and a dict built from parsed data is exactly where a non-string key
comes from. `counters.attempts` stopped incrementing at the cap, so every rate computed from it
drifted in the flattering direction past 50,000. The shared gate's `_interval`, `_sleep` and
`_clock` were plain attributes on the returned instance — a no-op sleeper made `wait()` report
1.0 s of throttling against 0.0 s of real blocking, **fabricating the number this phase exists to
measure**, with every probe green. And `telemetry._shared_gate = RateGate(interval=0.0)` was one
statement: *fewer* than the two public calls the round-5 disarm needed.

### What changed

- The rule table is a **tuple of pairs**. A tuple has no mutable backing object to hand out.
- Enum members are never rendered through `.name` or `.value`. Every printed or counted token comes
  from `_TOKENS`, an immutable tuple captured at import and looked up **by identity**. The counters
  hold those tokens rather than the members, so the structures most likely to be exported carry no
  live object at all. A probe AST-scans this module and fails on any `.name`/`.value` access.
- `__setstate__` validates the whole incoming state **before** it writes anything.
- `record(values)` takes a mapping positionally — there is no keyword binding left to fail.
- `RateGate` is slotted and its configuration is sealed after construction; the process-wide gate is
  built at import and held in a **closure cell**, so there is no module global to rebind and no lazy
  construction race to test for.
- `attempts` counts attempts; a new `filed` counts rows retained; `overflowed_by_shape` mirrors
  `dropped_by_shape`, because overflow is a **head** bias — the earliest rows are the ones kept —
  which makes a percentile from an overflowed recorder unusable rather than merely truncated.
- `_type_name` reports a type only from a closed allowlist. A 40-character identifier-shaped class
  name is ample room for a league id, so the regex alone blocked only the sloppiest payloads.
- Cheap range bounds on the measurement fields, pinned at their exact edges.
- The loopback socket probe sets a timeout on the **accepted** socket too (a listener's timeout is
  not inherited, so the strand simply moved from `accept()` to `recv()`), and closes the listener
  **before** the liveness assertion rather than after it.

### The module now states its residuals instead of claiming closure

Three rounds of claiming a vector closed produced three rounds of findings, so the module carries a
**RESIDUALS** section naming what it does not close: `Record.shape` still holds a live member, so an
exporter that reads `.value` instead of `_token(...)` can still carry a rewritten payload;
`object.__setattr__`, `Record.__post_init__ = ...` and rebinding module globals are reachable from
any in-process code and Python cannot prevent that; a type control cannot tell a league id from a
byte count, so the int fields rest on a caller precondition; and the gate is sealed, not immutable.

### The instrument, and what applying it to my own rework found

The harness was rebuilt around review's findings: it now runs a **baseline** first and aborts if the
suite is already red (without one, a red suite made every entry report "held" and printed a perfect
score); each entry **names the probe that must fail**, so a cascade is not mistaken for an objector;
a hang and a broken collection are reported **UNKNOWN** rather than caught; the edits are behavioural
rather than signature-only; and the backup is written outside the repository before the first
mutation, because the file it edits is untracked and `git checkout --` cannot restore it.

Run against the rework, it first reported **33 of 39**. Five of the six gaps were **redundant
controls masking each other** — exactly the defect review had just found in `Recorder`'s three lock
sites — and one was a harness bug. Redundant pairs are now scored as one control and removed
together, because a harness that scores them separately reports assurance it does not have.

The generic sweep then found the bounds probe **parametrised over `_FIELD_BOUNDS` itself**: widening
a bound moved the probe with it, so every mutation to every bound survived. This project has
recorded that exact defect before, in Phase 31, as *"a test parametrised over the very set it was
testing"*. The expected bounds are now written out in the test file and compared against the
module's.

### Measured

Taken in the cloud workspace (Python 3.11.15, httpx 0.28.1, pydantic 2.13.3) against a reduced tree,
because the operator's machine was disconnected for most of this work. **The device gates are not
yet re-run and this entry is not a completion claim for unit 2 until they are.**

- `tests/test_telemetry_seams.py` — **162 passed, 1 skipped** (was 107). The skip is the subprocess
  import probe, which needs the complete package; the AST scan covering the same claim runs
  everywhere, and the probe now **fails** rather than skipping on any cause but a missing module.
- **Control removal — 41 of 41 controls held**, each by the probe that names it, with a green
  baseline. Source verified byte-identical afterwards.
- Generic AST mutation sweep — **124 of 127 killed**. Three survivors: `return None` → `return None`
  in `Recorder.disabled` (equivalent), and two edits to `cache_verdict`'s first enum check that are
  masked by the token-completeness check on the following lines — the redundancy is deliberate, and
  the harness removes both halves as one control.
- Branch coverage — **200 statements, 56 branches, 100%, 0 partial.** Three branches that coverage
  found unreachable were handled by making one of them reachable with a real control (a member
  added without a token must be refused, not silently mis-rendered) and by **deleting** the other,
  which no caller could reach.
- **Flakiness — 0 failures in 12 consecutive runs**, with eleven threaded probes.
- `ruff check` — clean but for `RET501` and two `BLE001`, stock rules this repository does not
  select.

### Status

Unit 2 is reopened for the third time. E32.1d's completion claim is **withdrawn**. The provider
remains untouched, the recorder still has no caller, and no provider call, network call, credential
read, commit or spend occurred. The live-read envelope remains **unauthorized**.

---

## E32.1f — The device gates E32.1e deferred, now run

E32.1e's measurements were taken in the cloud workspace against a reduced tree and withheld the
completion claim on that basis. These are the gates it deferred, run on the operator's machine under
the repository's own `ruff` and `pytest` configuration. macOS arm64, Python **3.14.7**.

### One defect the device found, in a probe, introduced by the fix for a review finding

The device run first reported **162 passed, 1 skipped** — and the skip was
`test_importing_the_provider_does_not_import_telemetry`, the probe E32.1e calls the strongest
evidence that the recorder has no caller. It had run on the device the round before.

Cause: the round-6 hardening added `-I` to the subprocess invocation, for isolation. `-I` is
*defined* as ignoring `PYTHONPATH`, so the child could not import `api` at all; the probe hit its
`ModuleNotFoundError` branch and skipped. Review's P2-5 had warned precisely that an unconditional
skip lets this probe vanish while still being counted as assurance. The fix made the skip
conditional on a missing module — and then caused a missing module.

That is the **fifth** instance in this phase of a check that is green (or absent) while establishing
something other than what it claims, and the first that was caught by simply noticing a skip count
change between two runs of the same suite. The flags are now `-s -P`: `-s` drops user
site-packages, `-P` drops the implicit working-directory entry from `sys.path` — which was review's
separate concern, that a stray file at the repo root could shadow a stdlib module — and `PYTHONPATH`
puts the repository on the path deliberately rather than by accident. Measured on the device: the
child imports all three provider modules and reports `CLEAN`, and `sys.path` carries no cwd entry.

### Gates

| Gate | Result |
| --- | --- |
| `tests/test_telemetry_seams.py` | **163 passed, 0 failed, 0 skipped** (was 107). |
| Whole `tests/`, excluding `tests/test_recovery.py` | **702 passed, 2 skipped, 0 failed**; exit 0. Reconciles: 539 everything-else + 163 seam = 702 passed, with both skips in the 539 group. |
| `ruff check api tests alembic` | **All checks passed**, exit 0. |
| `tests/test_recovery.py` | **257 passed, 18 failed** — the pre-existing macOS platform failures, unchanged. |
| **Control removal** | **41 of 41 held**, each by the probe that names it. Run in three chunks (14 + 14 + 13), **each chunk re-running the baseline**, because the baseline is the check that makes the rest mean anything and is not the thing to skip for speed. Source verified byte-identical against a backup outside the repository after every chunk. |
| Generic AST mutation sweep | **124 of 127 killed** (45 + 44 + 35), matching the cloud result exactly on a different interpreter. Three survivors, all documented: `return None` → `return None` in `Recorder.disabled` (equivalent), and two edits to `cache_verdict`'s first enum check that the token-completeness check on the following lines masks — a deliberate redundancy the harness removes as one control. |
| Branch coverage, `api/services/telemetry.py` | **200 statements, 56 branches, 100%, 0 partial.** |
| Flakiness | **0 failures in 12 consecutive runs**, with eleven threaded probes. |

**E32.1e's completion condition is met.** Unit 2 is complete as reworked — the seam module and its
suite. The contract remains unaccepted and unit 3 remains gated on it.

### Repository state

- `api/services/telemetry.py` SHA-256 `0923d0c8843097b5b4431d16e77ff184075acd9d24cc3da137e8d88a1466717a`
- `tests/test_telemetry_seams.py` SHA-256 `7bef55a8cab008086f3c99963554323be53c941bac853d956c4b0e3e83a4c63f`
- The provider is unmodified **by this phase** — `espn.py`, `sync.py` and `cache.py` carry only
  pre-existing Phase 31 changes relative to `HEAD`, none of which reference telemetry. Stated that
  way deliberately: review noted that "untouched" could be read as "identical to HEAD", which it is
  not and never was.
- The recorder has no caller, held by two probes: an AST scan over every module under `api/`
  (including string references, so an `importlib` call assembled from fragments is caught), and the
  clean-subprocess import check above.
- Untracked worktree entries: **46**, unchanged. The desktop app had deposited a `Claude outputs/`
  directory of delivered copies into the repository root, taking the count to 47; those four files
  were moved to the gitignored `.venv/claude-outputs/` rather than deleted, restoring the inventory.
  Both harnesses and the coverage database also live under `.venv/`.
- No commit, no merge, no network call, no provider call, no credential read, no spend. The
  live-read envelope remains **unauthorized**; contract acceptance is explicitly not that approval.

---

## E32.1g — Round 7: NO-UNIT from cybersecurity. Two P1s, both the next spelling of an accepted finding.

`cybersecurity` returned **NO-UNIT** with two P1s, five P2s and six P3s, every one measured against
byte-verified copies. `qa_test` did not report: its run was cut off by a session rate limit before it
could hand back, so **this round had one reviewer, not two**, and that is a reduction in assurance
worth stating rather than glossing. Every finding below reproduced here before anything changed.

### The two P1s

| Finding | Measured |
| --- | --- |
| **`Recorder.record()` raises into the caller.** Round 6 moved the failure out of *argument binding* into the guarded body. The **recovery** path — `values.get("shape") if isinstance(values, Mapping)`, in the `except` clause — was left outside any guard, and it is the path that runs on every malformed input. | Four inputs escaped: a `Mapping` whose `get` is unusable (`AttributeError`), a `get` that raises (**its own message, payload and all, into the caller's traceback**), a `dict` key whose `__eq__` raises, and an object whose `__class__` is a raising property, which `isinstance` evaluates. Worse than the raise: neither `attempts` nor `dropped` incremented, so the counters under-reported exactly the pathological attempts the percentiles exist to capture. |
| **`del gate._sealed` disarms the process-wide gate with ordinary syntax.** `RateGate` defined `__setattr__` and no `__delattr__`, and the seal was read as `getattr(self, "_sealed", False)` — so deleting the slot made the default fire and the next plain assignment was permitted. | `wait()` reported **0.9999985 s of throttling against 0.0 s of real blocking** — the round-6 measurement-fabrication P1, verbatim, through the fix that was added to close it. The word `del` appeared **nowhere** in the 1,689-line suite, so the control-removal harness had never exercised deletion against any control. |

### The five P2s

- **`expected.__name__` interpolated raw**, two lines from the `_type_name` allowlist that exists
  because "a class can be created at runtime with an arbitrary `__name__`". `Shape` is an ordinary
  mutable class reachable as a module global; one assignment put a payload into every rejection
  message for that field, through a channel the value-absence probe structurally cannot see.
- **`Counters.__repr__` decided safety by equality.** `_token` was converted to identity in round 6;
  `shape in _KNOWN_TOKENS` was missed. `in` on a frozenset runs the candidate's `__hash__`/`__eq__`,
  so an object that hashes like a token and compares equal to anything was judged safe and then
  rendered through its own `__str__`, unbounded.
- **`__setstate__` echoed 32 characters of an attacker-controlled key** and ran its `__str__` to do
  it — the module's only direct value-bearing interpolation, against a docstring promising the
  messages name the field and the type and never the value.
- **`PYTHONPATH=ROOT` undid `-P`.** Measured directly: with `-s -P` and `PYTHONPATH=<dir>`, a
  `re.py` in that directory **was imported as the stdlib `re`**. `PYTHONPATH` entries precede the
  stdlib, so the flag bought nothing — and E32.1f claimed the shadowing concern was addressed.
- **`test_no_hostile_mapping_raises_into_the_caller` exercised no hostile mapping.** All six
  parameters were inert on `.get` — every one a wrong *type*, none a hostile *behaviour*. It is the
  probe that should have caught P1-1, and it is the same defect shape round 6 recorded for the
  `counters.cache` probe.

### What changed

The rule type is named through `_rule_name`, an identity lookup over an immutable table, never
`__name__`. `Counters.__repr__` tests token membership by identity. `__setstate__` reports a count,
never a key. `_shape_of` recovers the shape without running the failed mapping's code —
`type(values) is dict` so no `__class__` property runs, unbound `dict.get` so no overridden `get`
runs, and a guard around the lookup because a key's `__eq__` still executes inside it.
`RateGate.__delattr__` refuses every deletion, and the seal probe is parametrised over `delattr` as
well as `setattr`. The bounds are `1 << 26` bytes and 600,000 ms rather than a terabyte and 49 days.

Two things went further than the findings asked:

- **`__getstate__` now emits tokens, not live members.** Review noted that RESIDUALS handed the
  live-member problem to unit 3's exporter as an obligation while the module's own sanctioned
  serialisation format still carried the object. It no longer does; `__setstate__` resolves tokens
  back by identity and refuses one that names no member.
- **RESIDUALS was corrected in three places.** It opened a bullet with "No range check on the int
  fields" while `_FIELD_BOUNDS` existed two hundred lines above; it claimed the gate left only
  "visibly irregular" paths, which `del gate._sealed` disproved; and it omitted
  `RateGate.__setattr__ = object.__setattr__`. All three are now stated as they are.

### One of my own corrections was itself an overclaim

The new bounds probe asserted that a seven-digit league id is refused by every bound. **It is not.**
9,998,887 bytes is a plausible body size, so a seven-digit id in `wire_bytes` passes a 64 MiB cap.
The probe now asserts what is true — nine- and ten-digit identifiers are refused, seven-digit ones
in a byte field are not — and RESIDUALS names that gap explicitly rather than leaving a reviewer to
find it. A bound cannot distinguish a measurement from an identifier; only the caller can.

### The harness broke three times, and each break was a real defect

- **A 900 s subprocess timeout let one hanging mutant eat an entire bounded shell.** The harness was
  killed mid-mutation and **left the module edited on disk** — twice. The out-of-repo backup added
  last round is what recovered it both times, and the harness now restores from that backup at
  startup if the file on disk differs, so an interrupted run self-heals instead of faithfully
  backing up the mutant.
- **The timeout did not time out.** One probe spawns a python subprocess of its own, and
  `subprocess.run(timeout=...)` kills the direct child but then blocks forever on a pipe the
  grandchild still holds. Measured: a background sweep sat on one mutant for eight minutes with a
  30 s timeout configured. The harness now uses `start_new_session` and kills the process group.
- **Four entries were stale or wrong** after the module changed: two anchors no longer matched, one
  control (`name[:64]`) was **dead code** — `safe` requires both halves to be identity-matched
  tokens and every token is a short literal, so the truncation could never fire, and the harness was
  right that nothing held it — and one probe was satisfied by a refusal it had not asked for. That
  last one is the masking pattern again: a `_member` that resolves every token to the first entry
  still raises, on `outcome`, because it hands back a `Shape`. The probe now names the field.

The generic sweep then found `_rule_name`'s output and `_within_bounds`' return pinned by nothing —
nine mutations walked through them, because no probe read the repr of a record that was entirely
well formed, and none asserted what a rule name actually says.

### Measured, on the device (macOS arm64, Python 3.14.7, repo `ruff`/`pytest` config)

| Gate | Result |
| --- | --- |
| `tests/test_telemetry_seams.py` | **195 passed, 0 failed, 0 skipped** (was 163). |
| Whole `tests/`, excluding `tests/test_recovery.py` | **734 passed, 2 skipped, 0 failed**; exit 0. Reconciles: 539 + 195 = 734, both skips in the 539 group. |
| `ruff check api tests alembic` | **All checks passed**, exit 0. |
| `tests/test_recovery.py` | **257 passed, 18 failed** — the pre-existing macOS platform failures, unchanged. |
| **Control removal** | **48 of 48 held**, each by the probe that names it, in three chunks of 16, **each chunk re-running the baseline**. Source verified byte-identical against an out-of-repo backup after every chunk. |
| Generic AST mutation sweep | **138 of 144 killed** (69 + 69). Six survivors, all documented: four `return None` → `return None` equivalents, and two edits to `cache_verdict`'s first enum check that the token-completeness check on the following lines masks — a deliberate redundancy the harness removes as one control. |
| Branch coverage, `api/services/telemetry.py` | **229 statements, 72 branches, 100%, 0 partial.** |
| Flakiness | **0 failures in 12 consecutive runs**, with eleven threaded probes. |

### Repository state

- `api/services/telemetry.py` SHA-256 `80b8327b523422ce36e126572377405e2ca95f0e20e7bc181dd3129d6af915b1`
- `tests/test_telemetry_seams.py` SHA-256 `5d86b3e2128973426da19c99dff8f3c09307fd24f01a9919e79ecd7117321a49`
- The provider is unmodified **by this phase**: `espn.py`, `sync.py` and `cache.py` carry only
  pre-existing Phase 31 changes relative to `HEAD`, none referencing telemetry, and were last
  written twelve days before the telemetry files.
- The recorder has no caller. The AST scan now flattens `ast.unparse` output, so an `importlib`
  call assembled from fragments is caught — E32.1f claimed that and measurement showed it false.
  The subprocess check runs with `-I` and appends the root inside the child, so the stdlib keeps
  precedence, and it has **no skip branch at all**.
- Untracked worktree entries: **46**, unchanged.
- No commit, no merge, no network call, no provider call, no credential read, no spend. The
  live-read envelope remains **unauthorized**.

### Status

Unit 2 is complete as reworked **on one reviewer's findings**. `qa_test` has not reviewed this
version, and round 6 was decided by a finding only `qa_test` made — so this is not the two-reviewer
close the previous rounds had, and it should not be treated as one until that review runs.

---

## E32.1h — `qa_test`'s round-7 review, and what it found in E32.1g

`qa_test`'s round-7 run was cut off by a session rate limit before it reported, which E32.1g recorded
as a reduction in assurance. It has now run. Verdict: **NO-UNIT**, one P1, four P2s and eight P3s.
Round 6 was decided by a finding only `qa_test` made, and so was this round.

### The P1 is a claim this evidence file has now got wrong three times

E32.1g, Repository state: *"The AST scan now flattens `ast.unparse` output, so an `importlib` call
assembled from fragments is caught — E32.1f claimed that and measurement showed it false."*

Measured: **the flattening caught nothing a plain constant scan did not.** It stripped whitespace and
quotes and left the punctuation *between* the fragments:

| Form | flattened to | caught |
| --- | --- | --- |
| `"api.services.telemetry"` | `api.services.telemetry` | yes (a constant scan already did) |
| `("api.services.tele" "metry")` | `api.services.telemetry` | yes — the **parser** merges adjacent literals |
| `"tele" + "metry"` | `tele+metry` | **no** |
| `import_module("api.services.tele" + "metry")` | `…tele+metry` | **no** |
| `"".join(["tele", "metry"])` | `.join([tele,metry])` | **no** |
| `f"tele{'metry'}"` | `ftele{metry}` | **no** |

All three forms the probe's own comment named were missed. E32.1f asserted the coverage and was
falsified; E32.1g asserted the fix and is falsified here. The fix is `re.sub(r"[^A-Za-z0-9_]", "",
...)`, verified against all six forms — and because prose about this has now been wrong twice, **each
form is pinned by name in its own probe**, and a second probe pins what the scan **cannot** catch (a
fragment computed at runtime, a name from a variable, `getattr` with a variable). The capability and
its limit are both assertions now, not sentences.

### The hardening that cost the thing it was protecting

`_shape_of` accepts only an exact `dict`, so that no hostile `__class__` or `get` runs on the
recovery path — the round-7 P1 fix. Measured consequence: for an `OrderedDict`, `defaultdict`,
`ChainMap`, `MappingProxyType` or `dict` subclass carrying an identical payload, the drop landed in
the total and **`dropped_by_shape` stayed empty**.

`Recorder`'s own docstring says the per-shape counters exist because *"any aggregate derived from a
shape that dropped anything is biased, not just incomplete"*. A drop that loses its shape does not
merely fail to record the bias — it makes the bias **invisible**, so an exporter publishes an
unbiased-looking percentile for a shape that dropped rows. `record()` now hands `_shape_of` the
`dict(values)` conversion its guarded body already performs, and a probe covers six mapping types.

### The other harness had none of the protections the evidence attributed to "the harness"

E32.1g: *"The harness broke three times, and each break was a real defect."* True of
`control32.py`. `mutate32.py` — the script that produces the kill count, and the other script that
edits an untracked file in place — had been untouched for three days and still carried **every one**:

| Documented as fixed | State in `mutate32.py` |
| --- | --- |
| a timeout scored as caught | present — `except TimeoutExpired: passed = False`, i.e. a **hang counted as a kill** |
| any non-zero exit scored as held | present — a mutant that broke collection counted as a kill |
| no baseline | present — a red suite would have scored every site killed |
| the timeout cannot fire (a grandchild holds the pipe) | present |
| mutates an untracked file with no recovery path | present |

All five are ported. The port paid for itself within the hour: the sweep was interrupted twice more —
once by the shell ceiling, once by the bridge dropping — and each time recovery was a one-line
restore from a backup that had not existed before.

It also changed the number. The sweep now reports **2 of 145 sites as UNEVALUABLE** rather than
killed: both make `RateGate.__init__` raise, so the module cannot be imported and the suite never
runs. The old harness counted a non-zero exit as a kill, so it would have reported them as caught by
a suite that never executed.

### Three more, and all of them are the same shape

- **The survivor account in E32.1g was wrong on one of six.** `_member`'s `return token` → `return
  None` is not an equivalent mutant and is not in `cache_verdict`; the count of "four `return None`
  equivalents" is three. Both forms produced a `RecordRejected` naming `shape`, so the probe could
  not tell them apart. It now asserts the arriving **type** as well.
- **RESIDUALS was incomplete again**, along the axis this module's history says matters most: every
  identity table — `_TOKENS`, `_FIELD_RULES`, `_FIELD_BOUNDS`, `_RULE_TOKENS`, `_NAMEABLE_TYPES` —
  is a **rebindable module global**. `telemetry._FIELD_BOUNDS = ()` is one statement and every bound
  is gone. `shared_gate` was moved into a closure precisely because a module global "could simply be
  rebound"; the tables were not, and the suite itself uses that vector. Three further residuals are
  now written down: the `BaseException` channel, `_drop` being unguarded, and a `Counters`
  round-tripped through JSON printing every key as invalid because the repr tests identity.
- **`_rule_name`'s identity lookup was pinned by nothing**, and an earlier attempt at a probe for it
  was itself wrong: `expected` only ever comes from `_FIELD_RULES`, so no reachable input
  distinguishes `is` from `==`. The distinction is now made reachable — a decoy at the front of the
  table that compares equal to `int` without being it.

Smaller corrections: the `.name`/`.value` scan now covers `_name_`/`_value_` (the actual storage, and
what the round-6 finding was about) and `getattr`, which is a `Call` the walk could not see; the
control harness's entries 0 and 1 were byte-identical mutations counted as two controls — the inverse
of its own rule about redundant checks — and are merged; it printed "backup removed" while keeping
the backup; and its self-heal now **refuses** rather than restoring when the file on disk is not one
of its own mutations, because silently overwriting an untracked file with a stale backup is a
destructive default.

### Measured, on the device (macOS arm64, Python 3.14.7, repo `ruff`/`pytest` config)

| Gate | Result |
| --- | --- |
| `tests/test_telemetry_seams.py` | **211 passed, 0 failed, 0 skipped** (was 195). |
| Whole `tests/`, excluding `tests/test_recovery.py` | **750 passed, 2 skipped, 0 failed**; exit 0. Reconciles: 539 + 211 = 750, both skips in the 539 group. |
| `ruff check api tests alembic` | **All checks passed**, exit 0. |
| `tests/test_recovery.py` | **257 passed, 18 failed** — pre-existing macOS platform failures, unchanged. |
| **Control removal** | **50 of 50 held**, each by the probe that names it, in three chunks each re-running the baseline. Source verified byte-identical after every chunk. |
| Generic AST mutation sweep | **138 killed, 5 survived, 2 unevaluable**, of 145 sites, in six chunks each re-running the baseline. Survivors: three `return None` equivalents and the two `cache_verdict` edits the token-completeness check masks. Unevaluable: two edits that break module import, reported as such rather than counted as kills. |
| Branch coverage, `api/services/telemetry.py` | **231 statements, 72 branches, 100%, 0 partial.** |
| Flakiness | **0 failures in 12 consecutive runs.** |

### Repository state

- `api/services/telemetry.py` SHA-256 `bfd9696bf8da367c75b5accd13cefacf7c5a2082afa1367938923d7f39d993d6`
- `tests/test_telemetry_seams.py` SHA-256 `fdea2ad58d3c66a009bb210ea860a8f3cefb7deb168d0895f7ac060cef579c9c`
- The provider is unmodified **by this phase**; `qa_test` confirmed the `espn.py` diff is Phase 31
  hosted-mode work only and noted that "twelve days" understates — `sync.py` is 42 days older.
- Untracked worktree entries: **46**, unchanged; 46 modified, unchanged.
- No commit, no merge, no network call, no provider call, no credential read, no spend. The
  live-read envelope remains **unauthorized**.

### Status

Both reviewers have now returned on unit 2 and both findings sets are acted on. What is closed is the
seam module and its suite; the contract is unaccepted and unit 3 is gated on it.

One thing worth carrying forward rather than filing: the three rounds of findings against this
evidence file were all the same defect as the rounds against the code — **a sentence that is true of
one instrument, or one spelling, or one form, asserted as true of the class**. "The harness" meant
one of two harnesses. "Assembled from fragments" meant two of six forms. "Sized to refuse a league
id" meant nine digits and not seven. The fix each time was to name the instrument, enumerate the
forms, and assert the limit as explicitly as the capability.

---

## E32.2 — contract draft 4 reviewed, NO-CONTRACT; draft 5 written

**This entry is the first written under criterion 10's prose rule** (no URL, no league-shaped path,
no `str(exc)` of a provider error, no error-list entry; digests exempt only in the
``SHA-256 `<64 hex>` `` form). Everything before this entry is pre-contract record and is not
retro-fitted, because the file is append-only.

**Dispatch.** Draft 4, SHA-256 `7ddabc2a0b899d57554f1961da47d3ddf0d30bca9b8ce6af041834a47fd3c1e9`,
to `qa_test` and `cybersecurity` in parallel, read-only. `qa_test` died to a session rate limit and
delivered nothing. `cybersecurity` returned **NO-CONTRACT** on three P1s, five P2s and four P3s,
having verified all three dispatched digests byte for byte and executed nothing.

**Every finding was re-verified from source before draft 5 was written**, because unverified
line-number claims have been this phase's repeat defect. All thirteen hold. Three of them hold
*more* strongly than filed:

| Filed | Verified | Difference |
| --- | --- | --- |
| "4 of the 10 lines with a digit run of six or more are digests" | **8 of 10**, checked by span rather than by eye; the two that are not are line 94 (a league-shaped path) and line 652 (a duration printed at six decimals) | Strengthens P1-1: the digest exemption must be anchored to the digest *form*, and line 652 is an independent case for criterion 9's grid — a genuine measurement, printed raw, trips the rule |
| "`_safe_error` is never applied to the error list" | It **is** applied, at `sync.py:332`, and redacts only the three cookie names (`:348-358`) | Worse than filed: the redaction runs and does not redact the request path. Two interpolation sites were named; there are **three** (`espn.py:171, 177, 326`) |
| "only step 1's handler returns" | `sync_league` spans `sync.py:91-337`; the only early returns are `:111`, `:130`, `:136` | Confirms P1-3 exactly. Every later handler falls through, including the per-week loop at `:236-248` |

**P2-4 is the one that matters as method.** Draft 4 wrote "5 equivalent" where E32.1h measured *3
equivalent + 2 masked*. E32.1g had already corrected this same drift once, in this file. Collapsing
*masked* into *equivalent* is the class-from-instrument overclaim draft 4 opens by forbidding,
committed in the sentence that establishes unit 2's assurance — the twenty-third recorded instance of
a check reading as something other than what it establishes.

**Draft 5**, SHA-256 `2a92da9a70b300d5f3107f1f282955e7542bd100b82944489c8dcb2edd2599dd`, 468 lines.
Changes:

- **P1-1 + P1-2 + P2-3 + P2-5, closed by one change.** Two output artifacts, governed separately: a
  machine-generated report with its own owned path, declared markers and a **literal** token
  allowlist; and the append-only prose file under a forward-looking rule. Every published numeric is
  put on a declared grid under a declared ceiling, and the "named numeric fields" exemption is
  deleted — so the grammar becomes "no digit run of six or more, no exemption," which is what
  dissolves draft 4's collision between its own digit rule and its own digest requirement.
- **The grid and the ceiling are separated, with separate reach.** 64 KiB over a seven-digit value in
  a byte field destroys its low 16 bits; 50 ms over a six-digit value destroys about 5.6, which is
  not a control. The **ceiling** is what closes the duration fields. Draft 4 had one mechanism and
  claimed it for both.
- **P1-3.** Clause 4 now states the magnitude as a formula, and a process-wide hard-stop latch is
  added inside owned files: set by the recorder on a refusal (a flag write, so the never-raises
  contract holds), raised by the gate, deliberately **not** subclassing the provider error so the six
  swallowing handlers cannot absorb it. Its three limits are stated as explicitly. Clause 2 is
  amended as well, because the latch cannot reach a scheduled window in a fresh process.
- **Criteria 5 and 6 were made to stop contradicting the latch** rather than being left to. Criterion
  5 carves the latch cases out and scopes the comparison single-threaded and per-request; criterion 6
  pairs "an arbitrary gate exception changes nothing" with "the hard stop is the one that is not
  absorbed," so a test distinguishes them.
- **P2-1** becomes criterion 12: the one-file AST probe is widened to the traversal that already
  exists in the same suite, plus a rewrite-invariance probe that pins three documented residuals.
- **P2-2** relabels the gate EspnService-wide throughout and adds envelope clause 7. **P3-1** adds the
  exception *message* to criterion 5's tuple. **P3-2** scopes the truncation fact to the two framings
  measured and names the third. **P3-3** becomes clause 8. **P3-4** aligns criterion 13 with the
  repo's actual lint target.

Draft 5 passes its own criterion 9 grammar as prose: no path literal, no league-shaped segment, no
digit run of six or more anywhere in its 468 lines.

**Control state.** Worktree 46 untracked / 46 modified before and after. No execution, no network, no
provider call, no commit.

---

## E32.3 — contract draft 5 reviewed by both, NO-CONTRACT from both; draft 6 written

**Dispatch.** Draft 5, SHA-256 `2a92da9a70b300d5f3107f1f282955e7542bd100b82944489c8dcb2edd2599dd`,
to `qa_test` and `cybersecurity` in parallel, read-only. Both delivered this time. Both verified all
dispatched digests byte for byte, both verified the append-only chain at `head -887`, both executed
nothing, both reported the worktree unchanged at 46/46. **Both returned NO-CONTRACT.**

`cybersecurity`: one P1, four P2, five P3. `qa_test`: seven P1, six P2, four P3. The two lanes were
briefed separately — data boundary and envelope against testability and falsifiability — and
converged independently on the same four defects, which is the strongest signal either has produced
in this phase.

### The hard stop was not constructible, and every constructible form was self-disarming

The largest finding, reached independently from both lanes. Draft 5 specified three properties:
the latch lives "in the same closure as the gate"; `Recorder.record` sets it; `RateGate.wait()`
checks it. **No implementation satisfies all three.** `Recorder` holds no gate reference;
`RateGate` is defined at `telemetry.py:647`, lexically before `_build_shared_gate_accessor` at
`:734`, so `wait()` cannot reach that closure. And `record()`'s body is wholly inside one
`try`/`except Exception` with an early `return` at `:600` on overflow, so on three paths — field
rejection, overflow, `BaseException` — a refusal would arm nothing at all.

The sharper half is what that means together with the envelope. Clause 3 already concedes the
attempt ceiling is unenforceable "on the run where telemetry degrades, the counter under-counts."
Draft 5 then claimed the latch "is the only reason the operator's attempt ceiling has anything to
enforce it." **Both depend on the same healthy `Recorder.record`.** They were presented as
complementary and were common-mode: on a degraded recorder — which for brand-new unit-3 wiring means
a single field-name or type slip in the mapping passed to `record()` — the exposure clause 4
correctly sizes returns in full, on the one authorized read into a possibly-refusing provider.

`qa_test` added the test-isolation corollary: a genuinely process-wide latch needs a clear between
probes, but `tests/test_telemetry_seams.py:677` asserts `dir(telemetry)` contains no name with
"reset" in it, and criterion 1 requires the suite to pass unchanged. Naming around that assertion is
a string dodge that reintroduces the one-public-call disarm vector rounds 5 and 6 spent two rounds
closing — and draft 5 listed three limits for the latch, none of them "it needs a disarm path, and
that path is a new disarm vector."

**Draft 6's redesign removes the recorder from the path entirely.** `HardStop` is a slotted object
constructed by `RateGate.__init__` into a seventh sealed slot; `wait()` checks it; `espn.py` arms it
from `resp.status_code`. Clearing is construction of a fresh gate, so no reset name is added and
`:677` still passes. The limit this leaves is stated rather than discovered: the process-wide gate,
once armed, stays armed for the life of the process.

### The signature defect, instance #24, in the sentence correcting instance #23

Draft 5 states in criterion 5 **and again** in clause 8 that `espn.py:171, 177` and `:326` are "the
three places the request URL is interpolated" into an exception message. There are **six**:
`:171, 177, 326, 331, 333, 337`. The three it missed are all inside `_json_or_auth`
(`espn.py:328-337`) — **the function draft 5's own behaviour table names as the one "where
`resp.request.url` is in scope."**

The lineage matters more than the count. Draft 4 said two. E32.2 faulted that and said three. Draft 5
carried the corrected three. The re-derivation stopped at the same function boundary both times, and
neither round noticed the document contradicting itself on the same page. `qa_test` also drew out the
consequence: `:337` raises the message for both the `text/html` 200 case and the malformed-body case,
and `:331` for the 401/403 case — three of criterion 2's own required cases — so an implementer
following criterion 5's stated justification leaves the messages for its own required cases outside
the compared surface.

Both reviewers also found the handler count short: `sync_league` has **eight** handlers, not six, and
`:310` and `:324` are bare `except Exception`, against which `ProviderHardStop` not subclassing
`EspnError` buys nothing. It holds only because those two wrap `metrics.recompute_league` and
`momentum.record_metric_snapshots`, neither of which can reach an ESPN client — verified by both, and
a reason draft 5 never gave. And `result["errors"]` has eight append sites, not seven (`:311`).

### Criterion 9 failed on day one, exactly as draft 4's did

Criterion 9's grid is prefaced "Every numeric the exporter prints, **without exception**" and omits
`season`, `week` and `league count` — which criterion 7 requires in every row, and none of which is a
`Record` field, so none passes through `_FIELD_BOUNDS` either. This is structurally the defect draft 5
rejected draft 4 for, in the criterion written to replace it.

Two more in the same criterion. The counts ceiling was "the operator's attempt ceiling", which clause
3 says is not code-enforced — so the criterion is **undecidable on the declined-envelope branch** the
contract offers as a legitimate close, the "longest digit run is three" derivation contains a free
variable, and the over-ceiling token prints the ceiling itself. And ceiling-ing `filed` and
`overflowed` at an *attempt* ceiling is a category error: they are specified against
`MAX_RECORDS = 50_000`.

### The round-6 defect, on the control that replaced the one draft 4 was rejected for

`qa_test`: criterion 9 pins the **token allowlist** literally in the test file and says nothing about
the **grid and ceiling table**. A probe reading steps from an exporter constant moves when the
constant moves. The suite already carries both the fix and the name for this defect, at
`tests/test_telemetry_seams.py:1023-1045` — `EXPECTED_BOUNDS` written out, an equality assertion, and
the per-field probes parametrised over the literal — and its comment says in as many words: "a test
parametrised over the very set it was testing". Draft 5 applied the lesson to the allowlist and not to
the grid, inside one criterion.

### Three further overclaims, all of the same family

- **Criterion 12** claimed its probe pair "converts three documented RESIDUALS into a pinned
  control." It converts **one**. A `_value_` rewrite does not touch rebindable `_TOKENS`, and
  `_FIELD_BOUNDS` governs range checks at construction with no bearing on member rendering. The
  module's RESIDUALS record *why* both stay rebindable — the suite monkeypatches `_TOKENS` itself —
  so the claim contradicted the recorded reason for leaving them.
- **"A league id in any `*_ms` field can never be printed as a number at all"** is true of the
  six-digit-and-up form only; a five-digit value is under the ceiling and publishes. **No lower bound
  on league-id width is established anywhere in this phase**, and draft 5 did not label the gap. The
  mirror image of this error was already caught once in this file.
- **Ceiling-censoring `net_ms` silently censors the percentiles criterion 7 exists to publish.** A
  slow body is unbounded, so a duration above the ceiling is a *legitimate* measurement, and a p95
  over a censored sample is not a p95. Draft 5 applied the carry-it-in-the-same-cell template twice,
  for `dropped` and `overflowed`, and missed the instance its own ceiling created.

### What both reviewers credited

Every source citation in draft 5 held. `qa_test` checked roughly thirty line references across eight
files and broke none — the first draft of which that is true. Specifically confirmed: `sync_league` at
`sync.py:91-337` with early returns at **only** `:111, :130, :136`; the seven call sites with `:238`
passing no `bust_cache`; `_safe_error` applied at `:332` and redacting only the three cookie names;
`_short_hash` as an eight-character SHA-1 prefix and a linkable pseudonym, which both called the
document's strongest single argument; the ten/eight/94/652 evidence-file counts, independently
reproduced by span; and `make lint`. `cybersecurity` additionally audited every handler under `api/`
and confirmed `ProviderHardStop` propagates on every path — `_request` catches only
`httpx.HTTPError` with the gate call outside the `try`, `SyncService.__exit__` returns `None`, the
router catches only `RecoveryAdmissionError` — so the propagation claim is sound even though the
handler *count* was wrong.

**Draft 6**, SHA-256 `a2be25df04a2ef5fd458c367ee6c411b2ac261c1510f79d4892e70a691a2c06b`, 577 lines.
Acts on every P1 and P2 and all nine P3s: the hard stop redesigned to arm from the response status;
every ceiling a literal constant; `season`, `week` and `league count` added to the grid; the grid
pinned literally in the test file; `censored` added as a third same-cell count; the two-sided test
naming the field, the rule and the expected verdict for each of five plants, including the two that
**publish** rather than fail; criterion 2's exhaustion bound to a 5xx and its four loopback-dependent
cases required not to pass by skipping; the envelope's entry point named and `verify.py` excluded; and
all six enumerations corrected.

Draft 6 passes its own grammar as prose: no path literal, no league-shaped segment, no digit run of
six or more across 577 lines.

**Control state.** Worktree 46 untracked / 46 modified throughout, across a bridge drop that stranded
draft 6 in the session workspace for one turn. No execution, no network, no provider call, no commit.
**No live read is authorized, and neither review authorizes one.**

---

## E32.4 — contract draft 6 reviewed by both, NO-CONTRACT from both; the pattern is now the finding

**Dispatch.** Draft 6, SHA-256 `a2be25df04a2ef5fd458c367ee6c411b2ac261c1510f79d4892e70a691a2c06b`,
to both reviewers in parallel, read-only. Both verified every digest and both chain prefixes, both
executed nothing, both reported 46/46 before and after. `cybersecurity`: one P1, six P2, five P3.
`qa_test`: five P1, five P2, five P3.

**Both credited the hard-stop redesign's premise.** Arming from `resp.status_code` genuinely removes
the recorder from the path and closes E32.3's common-mode failure. `cybersecurity` verified the 429
ordering exactly — arm at `espn.py:170`, one backoff sleep at `:172`, attempt 2 raises at the `:162`
gate call — **zero further requests**, as clause 4 claims. Both confirmed the seventh slot is
admissible by `__setattr__` because `_sealed` is set last, and both called parking the mutable bit in
a separate object a careful way to keep the gate's docstring literally true.

**And both broke it anyway, at the seams the redesign creates.**

### The blocker neither of my drafts could have survived: criterion 1 was always false

`cybersecurity`, independently: the contract orders `espn.py` to call the gate and arm the stop.
`RateGate` and `shared_gate()` live in `telemetry.py`. `sync.py`, the routers and `verify.py` are all
Forbidden and `config.py` holds only the flag, so **`espn.py` must import telemetry** — which fails
`test_nothing_under_api_mentions_the_telemetry_module` (`tests/test_telemetry_seams.py:1911-1949`)
and the clean-subprocess check at `:2024+`. Criterion 1 says the suite passes **unchanged**, with a
non-exhaustive "including" list and no carve-out. **Every draft from 1 to 6 carried that sentence.**

The suite anticipated this in text no draft carried across, at `:1996-2002`: the scan "is designed to
be deleted when the provider legitimately imports telemetry — and at that moment the only thing
preventing production code from rebinding this module's internals disappears," naming
`test_nothing_under_api_assigns_to_this_modules_internals` (`:1995-2021`) as the successor guard.
**No draft mentions the successor**, so nothing in the contract requires unit 3 to keep the one
control that replaces the two it retires.

`qa_test` found the same contradiction from the other end: `tests/test_telemetry_seams.py:637-648`
asserts `set(RateGate.__slots__)` equals a **six-element literal**. A seventh slot fails it. And
draft 6's limit 3 argues no reset name may be added *because* `:677` pins it and criterion 1 freezes
the suite — so either the literals are frozen and `_hard_stop` breaks one, or they are editable and
limit 3's argument was never a constraint on the design. I asserted the first while requiring the
second.

### The seam I never named

Criterion 2 requires each hard-stop probe "on its own freshly constructed `RateGate`". Both reviewers
independently found there is no way to deliver that object: `EspnService.__init__`
(`espn.py:111-118`) takes no gate, and the only access the contract names is `shared_gate()`, whose
**no-argument signature is itself pinned** at `:666-667`. So probe 1 arms the import-time singleton
and probes 2 and 3 raise before reaching their own status. "Clearing is construction" says how to get
a clean object and never how it reaches the code under test — and the document names the analogous
seam explicitly for `Shape` ("a function signature inside an owned file is not a query parameter")
and not for the gate. Adding the parameter reopens the no-op-sleeper vector `telemetry.py:663-665`
exists to close: the round-6 measurement-fabrication finding.

### The bit is unsealed

`cybersecurity`: slots plus a read-only property block `hs.armed = False` — that is the round-5 fix on
`interval`. They do not block `gate._hard_stop._armed = False`: one statement, ordinary syntax, no
`object.__setattr__`. **That is the round-6 finding one level down**, and draft 6's argument that
"what flips lives inside the other object" is correct about the docstring and is exactly what leaves
the flip unprotected. None of the four stated limits names it.

### Five more of the signature defect, in draft 6's newest text

The phase's running total is now **29**, and three of this round's were introduced *by the fixes for
the previous round's*:

- **"The longest digit run any conforming field can produce is therefore three"** — it is **four**.
  `season` renders four digits under the ceiling `9999`. `season`, `week` and `league count` were
  added to the grid in response to draft 5's finding and **the run-length sentence was not
  re-derived over the widened table** — the same "re-derivation stopped at the old boundary" lineage
  E32.3 records for the URL count.
- **"Neither closes a five-digit or shorter identifier in a duration field"** is false: the `30.00 s`
  ceiling closes 30,001 through 99,999, and it contradicts a sentence two bullets above it. The plant
  built to name the residual honestly asserts a verdict that is **wrong for more than three quarters
  of its stated range**.
- **The residual is stated of duration fields only.** In any step-1 integer field — every count,
  `season`, `league count` — a value below the ceiling publishes **exactly, with no bucketing at
  all**. Strictly worse than the residual named, and unnamed.
- **The `except EspnError` enumeration is short a third consecutive round.** Seven, not six: `:131`
  is omitted, and it is the step-1 handler — the first one a cold sync into a refusing provider
  reaches, and the only `EspnError` handler that terminates rather than falling through. The full
  handler count is eleven, not eight.
- **`result["errors"]` has eleven append sites, not eight.** The eight named are exactly the
  `{exc}`-interpolating subset; `:109, 126, 325` append bare literals, so "its entries interpolate
  `str(exc)`" is true of the subset and false of the channel.

Also: the `_get_authed` doubled-loop case is required flag-on by criterion 2 and proved unreachable
flag-on by clause 3, in the same document — structurally identical to the exhaustion defect draft 6
**correctly fixed four items above it in the same enumeration**. And the hard stop permanently
disables the documented URL-decoded `espn_s2` retry (`espn.py:313-324`), which is the same "benign
401/403 indistinguishable from a refusal" hazard draft 6 closes for `verify.py` and asserts complete.

### What this round establishes about the method, not the document

Five drafts have now been rejected on facts that are **properties of the code that would be
written** — a pinned slots literal, a missing constructor parameter, a pinned accessor signature, two
probes that must retire, a successor guard named only in a docstring. None was discoverable by
reading the contract; several were discoverable only by reading the suite closely enough to find
assertions about objects that do not exist yet.

The contract-first loop is doing real work — every finding both reviewers reported has been real,
without exception, across eleven rounds — but it is no longer converging on *contract text*. It is
converging on unit 3's implementation, one review round at a time, at roughly one round per
discovered constructor signature. That is an observation for the Product Manager, recorded here
rather than acted on unilaterally.

**Control state.** Worktree 46/46 throughout. No execution, no network, no provider call, no commit.
**No live read is authorized, and neither review authorizes one.**

---

## E32.5 — the contract is split, on a Product Manager decision

**Decision.** Presented with four options after draft 6's double NO-CONTRACT — write draft 7 as more
of the same, split the contract, take the declined-envelope close, or invert and build unit 3 first —
the Product Manager chose **split the contract**. E32.4's closing observation is what the choice acts
on: the loop was converging on unit 3's implementation at roughly one review round per discovered
constructor signature, and that is not what a contract is for.

**The line drawn.** A criterion belongs in the contract if it can be falsified by **reading source
that exists today**. It belongs in the **Deferred register** if falsifying it requires code that does
not exist yet. Five criteria moved. Nothing was dropped, and the register is a commitment rather than
a dodge: each entry states the question, why prose cannot answer it, and what unit 3's close must
establish. Unit 3 keeps full parallel read-only review, and **that review checks the register first**.

This is a narrowing of what the contract claims, not a loosening of the gate. The distinction matters
because the opposite move — keeping the criteria and accepting them unverified — is exactly the
"a check green while establishing something other than what it claims" failure this phase has
recorded twenty-nine times.

**The register**, each entry traceable to a reviewer finding rather than to a convenience:

| | Question deferred | Why prose cannot answer it |
| --- | --- | --- |
| D-1 | Where the hard stop's flag lives and how it is sealed | Draft 5's placement made telemetry degradation disarm it; draft 6's seventh slot fails a pinned six-element literal at `tests/test_telemetry_seams.py:637-648`. What **is** settled and stays in the contract: it arms from the response status and never from a filed record. |
| D-2 | Which unit-2 probes retire at wiring, and what replaces them | `espn.py` must import telemetry to call the gate, so `:1911-1949` and the clean-subprocess check at `:2024+` both fail. **Every draft from 1 to 6 said "the suite passes unchanged."** The suite named its own successor at `:1996-2002` and no draft mentioned it. This is the one register entry with a security consequence rather than a construction one. |
| D-3 | How a probe gets a clean gate in front of `_request` | `EspnService.__init__` takes no gate; `shared_gate()`'s no-argument signature is pinned at `:666-667` and exists to close the round-6 no-op-sleeper vector. The obvious seam reopens it. |
| D-4 | Whether the doubled-loop case is assertable flag-on | Required flag-on by one criterion and proved unreachable flag-on by one envelope clause, in the same document. |
| D-5 | What the stop costs the documented `espn_s2` decode recovery | Arming on the first 401/403 permanently disables a benign documented retry — the same hazard class clause 1 closes for `verify.py` and asserted complete. |

**Corrections carried in**, each re-verified: `sync_league` has **eleven** handlers, **seven** of them
`except EspnError` (`sync.py:131, 189, 209, 225, 247, 263, 280`) — `:131`, the step-1 handler, was
omitted three drafts running; `result["errors"]` has **eleven** append sites, eight interpolating and
three bare literals, so "its entries interpolate `str(exc)`" is true of the subset and false of the
channel; the doubled loop is entered at `espn.py:313`/`:318`, not `:322`, its exit check; this file's
line 652 prints **seven** decimal places.

**Two substantive corrections to criterion 4 itself**, both from the draft-6 review and both of the
signature shape:

- **"The longest digit run any conforming field can produce is three" was four.** `season` renders
  four digits under the ceiling `9999`. `season`, `week` and `league count` were added to the grid in
  response to the *previous* round's finding and the run-length sentence was not re-derived over the
  widened table — the same lineage as the URL-site count. The grammar's six-digit threshold survives
  with a two-digit margin.
- **The residual was stated of duration fields only.** The truth is that the grid closes nothing below
  its ceiling in **any step-1 integer field**: in a count, in `season`, in `league count`, an in-range
  value publishes **exactly, with no bucketing at all** — strictly worse than the residual draft 6
  named, in the bullet written to name it honestly. And draft 6's five-digit `net_ms` plant asserted
  publication for a range of which **more than three quarters is censored** by its own `30.00 s`
  ceiling. Both corrected; the plant list now names the field, the rule and the expected verdict for
  each of six plants, two of which assert publication rather than failure.

**Draft 7**, SHA-256 `fbdbc7a23615168bdaf99ed710356da648b74076150de8640331edbf52d915dd`, 536 lines —
shorter than draft 6 despite carrying more, because the deferred material left the criteria. Eight
criteria, eight envelope clauses, five register entries. It passes its own grammar as prose: no path
literal, no league-shaped segment, no digit run of six or more.

It also names where it most likely fails, which no previous draft did: criterion 4, as the largest
block of new prose, and the register, which asserts what five entries *are* without having built any
of them.

**Control state.** Worktree 46/46. No execution, no network, no provider call, no commit. **No live
read is authorized.**

---

## E32.6 — Phase 32 narrowed: the live read is cut, permanently

**Decision.** The Product Manager time-boxed the remainder: the drive-and-terminal workflow failed
repeatedly, the restore checks surfaced real bugs, and the fix-and-review cycles outgrew a practice
project. **The live ESPN read is out of scope for Phase 32 — not deferred, out.** The rate gate goes
with it, because a gate exists only to bound live provider traffic.

**What the cut removes, and what it removed for free.** The eight-clause envelope; the hard stop
(`HardStop`, `ProviderHardStop`, the arming site); the EspnService-wide gate's use; and the publication
grid with its ceilings, `censored` count and six-plant two-sided test. Four of the five register entries
die with them, and **so does draft 7's P1-2** — the arm site at `espn.py:170` never fires for a 401 or
403, which return at `:175`, and arming there would have broken criterion 1's own 5xx exhaustion case by
the exact mechanism criterion 1 invoked to rule out a 429. A scope cut retired that defect rather than a
fix.

**Two criteria got *easier*, which is the new risk.** With no stop interrupting the retry ladder, every
case in criterion 1 is reachable with the flag on — the exhaustion ladder and the doubled loop included,
both of which draft 7 had to bind to a 5xx or defer. And criterion 3 lost **both** carve-outs: no gate
timing to exempt, no deliberate request-count truncation to carve out. **A claim that got easier is a
claim nobody re-checked**, and draft 8 says so in its closing section and asks both reviewers to check
those two hardest.

**Draft 7's P1-1 survives the cut and is fixed.** D-1 is now the only deferred entry, and the fix is the
one the draft-7 review's finding requires: the successor guard at
`tests/test_telemetry_seams.py:1995-2021` fires only when the assignment target's base expression
textually contains "telemetry", so under `from api.services.telemetry import Recorder` it is **vacuous**
— it cannot fire and it passes. `setattr(telemetry, "_TOKENS", x)` is a `Call`, not an `Assign`, and is
missed too. The retiring scan carried `Constant`/`BinOp`/`JoinedStr`/`Call` passes and the subprocess
check covered runtime-assembled references; the successor is static-only and covers neither. **D-1 now
requires unit 3 to show the successor *firing on a planted offender* in three named forms, not merely
passing.** "Must pass" was satisfiable while establishing nothing — which is this project's defining
defect, in the register written to prevent it.

**Two more findings carried in rather than fixed**, because `sync.py` is Forbidden and the live read is
gone:

- **`result["errors"]` is persisted, not only read from a console.** `sync.py:329-332` joins it through
  `_safe_error` into `_record_diagnostics` (`:340-345`), which writes `league.last_sync_error`;
  `_safe_error` (`:348-358`) redacts only the three cookie needles and has no URL rule, so it runs and
  does not redact the request path. Drafts 4–7 called this a console channel only. With synthetic-only
  input the persisted text is synthetic, so it is recorded and carried to Phase 39.
- **No range check distinguishes a measurement from an identifier.** Drafts 5–7 built the grid and
  ceiling scheme entirely to stop a real league id reaching a published figure on a live read. With the
  read out of scope and every input a fixture, that is a latent precondition defect, not an exposure. The
  grid design is preserved in draft 7 in git history for Phase 39. **This is the single largest thing the
  narrowing gives up, and draft 8 says so rather than dropping it quietly.**

**What the phase now retires, stated honestly.** The *measurement instrument* risk, not the *network
term* risk. The ESPN network term stays **unmeasured**, in those words, and Phase 39 inherits four named
items: the unbucketed-identifier precondition, the persisted error channel, the fall-through cost of a
refusal with its magnitude attached (order eighty further HTTP attempts after a step-2 refusal, from
`sync_league`'s eleven handlers and only three early returns), and a built, tested, uncalled rate gate.
That is less than the phase set out to do and more than it started with.

**Draft 8**, SHA-256 `65aef065537e3c5eae2abbc0dcebc48c1856f7a7f4b2fe37815e04ad4e1465c0`, **336 lines** —
down from draft 7's 536 while carrying two more findings. Six criteria, one deferred entry, no envelope.
Passes its own grammar as prose: no path literal, no league-shaped segment, no digit run of six or more.

**Acceptance discipline, changed once and deliberately.** **One** parallel review round on this draft,
then the phase closes regardless of verdict: P1s fixed, P2s and below recorded here as known-open and
carried to Phase 39. Eleven rounds of unbounded fix-and-review is what the time box exists to stop.

**Control state.** Worktree 46/46. No execution, no network, no provider call, no commit.

---

## E32.7 — final review round; P1s fixed; contract accepted under the time box; Phase 32 closed

**Dispatch.** Draft 8, SHA-256 `65aef065537e3c5eae2abbc0dcebc48c1856f7a7f4b2fe37815e04ad4e1465c0`, to both
lanes in parallel, read-only, briefs scoped to the narrowing rather than to re-verification. Both
delivered. Both verified every digest and the chain, both executed nothing, both reported 46/46 before
and after. **Both returned NO-CONTRACT.** Per the time box declared in draft 8 and E32.6: the five P1s
are fixed, everything below is recorded here, and the phase closes.

**Accepted contract:** SHA-256 `43af4696fa1458a418701905794f6da3ef120a506486ae7f23f2b513e263802a`,
401 lines.

### The five P1s, and what each one was

**1. The chunked-framing half of the truncation fact was never measured.** `chunk`,
`transfer-encoding`, `gzip` and `content-encoding` appear **nowhere** in the 2,068-line suite. The one
loopback socket (`tests/test_telemetry_seams.py:334`) sends an explicit `Content-Length` at `:356`; the
retry half at `:384` replays a hand-built `RemoteProtocolError`. So three drafts said "two framings,
measured on a real loopback socket", under a heading reading "Pinned by
`tests/test_telemetry_seams.py`", and **one framing was measured.** The scope note named the *third*
framing as not covered while mislabelling the second as measured — the signature defect committed inside
the sentence written to prevent it. Now: one measured, one a unit-3 case, one not coverable.

**2. Criterion 4's bucketing and its digit-run rule were mutually unsatisfiable.** A 64 KiB bucket
printed in bytes is a multiple of 65536, so every bucket from the second (128 KiB) upward trips "no digit
run of six or more, with no exemption". With `wire_bytes` bounded at `1 << 26` and a league payload on
the order of megabytes, the two rules admitted **two** publishable values and failed the build on any
realistic payload. Draft 7 rendered byte figures in human units; **this draft dropped that when it
simplified the grid away, and the simplification broke the control.** That is the cost of the narrowing
showing up exactly where a narrowing's cost shows up: in the part that looked like surplus.

**3. "No provider network call is made by this phase at any point" was not enforceable.** Forbidden paths
are enforced by reading a diff; this is a runtime property. `config.py:64` defaults `espn_api_host` to
the real provider, `espn.py:122` falls back to it, and `tests/conftest.py` pins six environment values
with **no host pin and no socket guard** — in a suite the contract now requires to speak real HTTP on a
loopback socket. A forgotten `host=` reaches the provider. Fixed with teeth rather than labelled
discipline: criterion 1 requires a loopback host pin and an autouse guard permitting `127.0.0.1` only.

**4. The doubled-loop case was satisfiable while measuring 2 attempts and claiming 8.** Three
preconditions gate it and the draft stated one. 401/403 are not in the retry set at `espn.py:170` and
return at `:175` after one attempt, so a plain 401-then-401 script costs **2**; an all-retryable first
ladder raises at `:176` and never reaches `:313`. Eight needs a *mixed* ladder in **each** `_request`.
"Up to 8" is satisfied by 2 — D-1's own lesson, recurring in a criterion the draft asked reviewers to
check hardest.

**5. D-1's five forms were three, and its fallback was dischargeable by a docstring.** Measured with
controls: the successor predicate collects targets only from `Assign`/`AugAssign`/`AnnAssign` and tests
`isinstance(t, ast.Attribute)`, so every non-`Attribute` target escapes **even when the base is literally
`telemetry`** — including `telemetry._TOKENS, telemetry._FIELD_BOUNDS = (), ()`, the tuple spelling of the
statement the module's own RESIDUALS name as their worst vector. So "fires only when the base contains
'telemetry'" is *necessary, not sufficient*. Two forms added: a runtime-assembled `setattr` name (the form
the retiring scan covered, and which E32.1f claimed, E32.1g re-claimed, and measurement refuted twice),
and a non-`Attribute` target with the literal module name. And the fallback must now be pinned in the
`:1972-1992` form, whose probes **fail when a limit moves**, because a prose fallback let an author widen
nothing and satisfy the entry.

One P2 was folded in rather than recorded, because it sat inside a sentence a P1 fix rewrote: only **two**
cases need a real socket, not four. `wire_bytes` is `Content-Length` **as sent**, read from a header
(`telemetry.py:339`), so `FakeClient:82-90` produces gzip's wire != decoded and chunked's `wire_bytes = -1`
directly. Mandating a socket for those two doubled the exposure to a bind-unavailable environment for no
measurement gain. Loopback-bind availability is now a **declared build precondition** and `:339`'s skip
becomes a failure — the second named amendment to a unit-2 probe after D-1.

**A note on instance 32.** The patch text written to explain the digit-run rule contained a six-digit
run. Caught by the same grep the rule describes, one command after writing it. Recorded because the
pattern's persistence is the phase's most durable finding and it does not exempt the person naming it.

### Known-open — carried to Phase 39, not fixed

Recorded under the time box. **The first two bite unit 3 on day one; read them before writing code.**

| | Finding | Source |
| --- | --- | --- |
| K-1 | **The threaded `Shape` keyword must be optional.** Seven unit-2 probes call `service._request(...)` directly (`tests/test_telemetry_seams.py:271, 288, 302, 398, 411, 472, 487`); a required keyword-only parameter breaks all seven and fails criterion 6. The contract permits the threading without specifying a default. | qa P2-6 |
| K-2 | **`gate_ms` is a fifth structural zero.** Criterion 1 requires "the four time fields separately" on every case, and nothing calls the gate, so `gate_ms` is identically 0 in every record — a quarter of that assertion is vacuous throughout. Criterion 2 enumerates four structural zeros and omits it. | qa P2-3 |
| K-3 | **Criterion 3's scope did not widen with criterion 1.** The doubled loop is a `_get_authed`-level invariant across *two* `_request` calls; per-`_request` the attempt count reads 4 and 4, never 8. And `_get_authed:323` mutates caller state — `cookies.espn_s2 = decoded`, written onto the caller's object — with no state term in criterion 3's tuple, so "request content, order and count unchanged" does not cover the one side effect the newly-admitted case has. | cyber P2-1 |
| K-4 | **The refusal-cost magnitude is one arithmetic asserted of two refusals.** "Order eighty further HTTP attempts" holds for a retryable-status refusal. A 401/403 costs one attempt per `_request` and **no backoff**, up to two `_request`s via the doubled loop — order **forty**, zero backoff. Carried unchanged into the Phase 39 handoff. Instance 31. | cyber P2-2 |
| K-5 | **The two carried findings are live in the running system, not latent.** `sync_league` is reached from `routers/leagues.py:99`, `verify.py:121` and `:143`, and `recovery.py:253` and `:293`. So the persisted `result["errors"]` channel is exercised against real leagues today, and the wiring ships permanently in `espn.py`. "With no live read the text is synthetic" is true of *the phase's* runs and false of *the system's*. Two further parts: `config.py` currently has **no telemetry flag at all**, so the undefaulted-flag fail-safe is entirely prospective and no criterion asserts it beyond "flag off: nothing recorded"; and an id of **five digits or fewer** passes both the field bounds and the report grammar. `espn.py:106-108` (`_forbid_in_hosted_mode`) is an existing guard pattern of exactly the needed shape that this phase does not use. | cyber P2-3 |
| K-6 | **The GUID plant lands on the wrong rule about 28% of the time.** A random UUIDv4 contains a ≥6-digit run with p≈0.276, so it is often rejected by the digit-run rule rather than by the closed allowlist — leaving the allowlist, the rule the contract argues *must* be literal and closed, without a dedicated failing case. Pin the plant to a GUID with no six-digit run and assert which rule rejected it. | qa P2-5 |
| K-7 | **Criterion 5's widening creates false positives it does not name.** Dropping the `telemetry.py` skip puts `getattr(type(value), "__name__", "")` (`:140`) and `spec.name` (`:358`, a dataclass `Field`) in scope, and the instruction omits carrying `allowed = {"spec"}` from `:842`. The stated limit covers only the under-catching direction. | cyber P3-1, qa P3-1 |
| K-8 | **`backoff_ms` is not declared nominal or measured.** `no_sleep` patches `time.sleep` so it never elapses, criterion 1 requires `backoff_ms` summing 15,000, and criterion 6's window model consumes it as wall time. Consistent only if it records the nominal ladder value. Under the hard stop the ladder was truncated and the question never arose. | cyber P3-2 |
| K-9 | **Criterion 2 keeps "error rate" as a `0/N` bucket.** Every record is fixture-derived, and criterion 1 deliberately exercises 401/403/404/429/5xx, so the numerator is not zero and the denominator is not provider trials. | qa P3-3 |

### What Phase 32 retired, and what it did not

**Retired:** the *measurement instrument* risk. There is a recorder, a suite that answers what the
provider does from fixtures and a loopback socket, call arithmetic derived from source rather than
eyeballed, and two output artifacts under a grammar.

**Not retired:** the *network term* risk. **The ESPN network term is unmeasured.** No live read occurred
and none was ever authorized. Phase 39 inherits: the unbucketed-identifier precondition (K-5), the
persisted error channel (K-5), the fall-through cost of a refusal with its magnitude corrected (K-4),
the built-tested-uncalled `RateGate`, and K-1 through K-9. The envelope and grid design are preserved in
draft 7 in git history.

**Rounds:** twelve. Four NO-UNIT on unit 2's code, eight on the contract across eight drafts. **Every
finding both reviewers reported independently was real, without exception, in all twelve.** The
recurring defect — a sentence true of one instrument, one spelling or one form, asserted of the class —
reached **32 recorded instances** and appeared in the newest text of every round including this one.

**Control state.** Worktree 46 untracked / 46 modified throughout. No execution, no network, no provider
call, no Keychain, no Restic, no commit, no deletion, no spend. **No live read was ever authorized or
performed.**

**Phase 32: CLOSED.**

---

## E32.8 — unit 3 built: recorder wired, exporter written, report generated, gates green

Against the accepted contract, SHA-256
`43af4696fa1458a418701905794f6da3ef120a506486ae7f23f2b513e263802a`.

| Artifact | SHA-256 | Size |
| --- | --- | --- |
| `api/services/telemetry.py` | `b919b6a477e4b4fcde287fdd8a2ae02eab9a810dbd846f04f52feec1a72e0d48` | 1,079 lines |
| `api/services/espn.py` | `323d1a05a8695f688de8e902be155bfc8724a5ba877b2e31dc8ea35f7f4b2678` | 567 lines |
| `api/config.py` | `bc124e952e65cd3bcd7af94939ea102da2a26e0a6874607cfe7cf923d1ef46cc` | |
| `tests/conftest.py` | `7ad83462b2ef44ee6c607db9b44f084a2e41f4782fce3c0ed4044a0feb6bab37` | |
| `tests/test_telemetry_seams.py` | `a29b1145af98af30f0a6e0f2e213db232539ee254c279a67f6caeb329463bcfc` | 2,265 lines, 222 tests |
| `tests/test_provider_telemetry.py` | `35683cced2ccdf533e72c254e81799f5f4ec9d1821838fac4efec471e7c72e15` | 875 lines, 62 tests |
| `docs/evidence/phase-32-report.md` | `1bad4986c93e04217fad02993baa7795a22ecda166880f1544869a6df4151c39` | 91 lines |

**Gates.** 783 passed / 0 failed across every suite but recovery; recovery unchanged at its **18
pre-existing platform failures** (macOS launchd and plist paths, in a Linux VM — counted, not fixed);
`ruff check api tests` clean; the Phase 31 acceptance gate passes 19/19. Worktree **47 untracked / 46
modified** — the extra untracked entry is `tests/test_provider_telemetry.py`, a new owned file. The
report adds no entry because `docs/evidence/` is already one untracked directory. The 46/46 invariant
held through every other step and the one change is accounted for here.

### What was built

**The wiring.** `espn.py` imports telemetry, resolves a recorder from the flag (`recorder=` beats the
flag so a test holds its own rows), returns waited milliseconds from `_throttle`, and files one row per
HTTP attempt from `_request` with an optional `shape=`. Cache verdicts are taken in `fetch_views` and
`fetch_pro_schedule` — HIT and MISS at the consultation, BYPASS at the guard, because on that path the
cache is never consulted at all. No gate call. No hard stop. `gate_ms` is written as a literal 0.

**The config.** `telemetry_enabled` and `telemetry_report_path`, both `Field(...)` and therefore
**required**. A defaulted flag means whoever never decided gets our default, and both directions are
wrong: off-by-default silently discards the measurement, on-by-default silently starts recording on
someone's machine. `.env.example` documents both and `.env` was appended blind, never read.

**The exporter**, in `telemetry.py` because it is the only owned module that can host it. Every number
goes through a renderer; nothing is interpolated raw.

### K-1 and K-2, the two known-opens flagged to bite on day one, both confirmed

**K-1 held exactly as predicted.** `shape` is optional with a `None` default, and
`test_the_shape_keyword_is_optional_so_unit_twos_probes_still_call_request` asserts the default via
`inspect.signature` so nobody tightens it later. Seven unit-2 probes call `_request` with no such
keyword and all seven still pass.

**K-2 held too**, and the report labels `gate_ms` a structural zero rather than publishing a percentile
over a column of zeros as though it had measured waiting.

### D-1 answered, and the answer is better than the contract's plan

The contract planned to **retire** two unit-2 probes. Retiring was the wrong move and the build showed
it: "nothing under `api/` mentions telemetry" became false, but **"only the provider mentions it" is
still true, still worth pinning, and still fails the moment a second module reaches for the recorder.**
So `test_only_the_provider_mentions_the_telemetry_module` skips `telemetry.py` and `espn.py` and flags
everything else, and the subprocess check was **inverted** rather than deleted:
`test_the_provider_imports_telemetry_and_telemetry_imports_no_provider` now asserts the wiring is real
(so an unused import cannot be quietly removed) **and** that importing telemetry alone drags in no
provider — no cycle, and the module stays standalone, which is what lets its own suite run without a
provider at all. Two probes amended, zero retired.

The successor guard was widened as D-1 required and the plants were written to **fire**, not pass. Eight
forms, every one a measured miss of the original predicate: an aliased module ref; `setattr` with a
literal name; `setattr` with a runtime-assembled name; a tuple-unpacked target — the module's own
RESIDUALS name `telemetry._FIELD_BOUNDS = ()` as their worst vector and the tuple spelling of it was
uncaught; a `__dict__` subscript; a `for` target; a `with ... as` target; and a `sys.modules` subscript.
Plus a negative case, because a predicate that flags everything is not a control either.

**And the limit-pinning probe immediately caught my own stated limit being wrong.** I listed
`sys.modules['api.services.telemetry']._TOKENS = ()` as uncatchable; it is catchable, because the
literal contains the substring. The `:1972-1992`-form probe failed rather than letting the claim stand,
and the form moved to the plants. That is the first time in this phase a limit claim was refuted by the
mechanism built to refute it instead of by a reviewer.

### Three corrections the build forced, each an instance of the phase's pattern

**Instance 33 — my own "two socket cases, not four" was wrong on one case.** I accepted a review
finding that `wire_bytes` is read from a header so gzip and chunked need no socket. Gzip indeed needs
none — measured, wire 7 against decoded 11 from a constructed response. **Chunked does**, because
`httpx.Response(content=...)` *sets* `content-length` for you, so the ABSENCE of that header cannot be
faked at the response level at all. The first version of the chunked case asserted `wire_bytes == -1`
and measured 11. Three socket cases, not two and not four: declared-length truncation, chunked
truncation, and chunked-without-`Content-Length`.

**The chunked-framing truncation case now exists.** E32.7 recorded that three drafts claimed two
framings measured while unit 2 measured one, and that `chunk`, `transfer-encoding` and `gzip` appeared
nowhere in its suite. Both framings are now measured on a real wire, and the declared build precondition
replaced the skip: `test_loopback_bind_is_available_and_a_failure_is_not_a_skip` **fails** where unit 2
skipped, so an environment without bind cannot go green having measured nothing.

**Two report labels said something other than what their numbers established.** "cache hit rate at
request boundary" was computing the rate **where hits are decided** — the request-boundary framing is a
structural zero and is listed as one. Relabelled to name its denominator, as was "error rate over
recorded attempts". Caught by reading the generated artifact, which is why the artifact is generated
rather than described.

### The residual, demonstrated rather than asserted

Criterion 4's plants assert what publishes as well as what fails, which is the half every earlier draft
got wrong: a seven-digit byte count is **absorbed by the 64 KiB grid and published bucketed** (1.2 MiB —
a mitigation, not a refusal); a duration at or below the 30 s ceiling **publishes**; and a five-digit
count **publishes exactly, with no bucketing at all**, which is the worst case of the residual and the
plant says so. A value above a ceiling renders `over-ceiling` and carries no digit of itself.

The report passes its own grammar: no URL, no digit run of six or more, and a **literal** closed
allowlist in the test file — which earned itself twice during the build by failing on two new words
before they could reach the artifact.

**Control state.** No provider network call at any point — now enforced rather than declared, by a
loopback host pin and an autouse guard in `conftest.py` that fails any test attempting another host. No
network, no Keychain, no Restic, no commit, no deletion, no spend. **No live read.**

**Next:** the contract's process section requires unit 3 to close with parallel read-only review, and
that review checks the Deferred register first.

---

## E32.9 — unit 3 reviewed, seven P1s fixed, Phase 32 CLOSED

Both lanes delivered. Both ran the suite, both restored everything they mutated, both reported the
worktree unchanged. **Both returned NO-UNIT** — `cybersecurity` one P1, `qa_test` six. Under the time
box declared in draft 8: **P1s fixed, everything below recorded here, phase closed.**

### The seven P1s

**1. The cache table published keys through bare `str()`.** `cybersecurity`, MEASURED with the suite's
own seven-way malformed-key fixture: a cache key holding a full league-shaped URL **published into a
committed file**. Every other cell in the report goes through `_token` or a renderer; these two did not.
It is the round-6 `Counters.__repr__` finding re-opened on the publication path, and the sink is worse —
a file under `docs/evidence/`, not a CI log. `sorted()` also raised on a heterogeneous key set and
`k[1]` was indexed unguarded. Fixed with the same `_is_token` gate `Counters.__repr__` uses; refused
keys are **counted as `unpublishable`**, not silently dropped.

**2. The committed artifact failed the grammar in seven places.** `qa_test`, MEASURED: `exhausted`,
`retryable_status`, `transport_error`, `too`, `small` were absent from the allowlist, because
`filled_recorder()` filed only `Outcome.OK`, only `CacheVerdict.BYPASS` and six rows of one shape — **a
strict subset of the exporter's output space.** E32.8's "the report passes its own grammar" was true of
a test-rendered report and false of the artifact. The fixture now covers every outcome, every verdict
and a thin shape; a new probe pins the fixture **against the enums themselves**, so adding a member
fails there rather than silently shrinking every grammar test; and a further probe points the grammar at
`docs/evidence/phase-32-report.md` itself.

**3. Three of four grammar rules had no failing case.** Removing the url, guid or allowlist rule left
all 284 green. The single GUID plant tripped two rules at once and its assertion accepted either, so
each rule propped up the others. Four plants now, each asserting its rule fires **and that no other rule
fires** — this file's own stated standard, broken in the test written to uphold it.

**4. Criterion 1's network enforcement had no exercising test.** Deleting the `connect` monkeypatch,
repointing the host at the real provider, or allowlisting it each left everything green. The mechanism
worked; nothing established that it worked. Two probes now: one attempts a non-loopback connect and
asserts the guard blocks it **naming the host**, one asserts the configured host is loopback.

**5. The label probe was parametrised over the tuple it was checking.** Round 6's defect in a new
spelling: removing `"season"` from `_REPORT_SHAPES` stopped the exporter printing the row **and** stopped
the probe looking for it. And `count("no call site") >= 2` was satisfied eight times over by an empty
report, five of them from a different table. Now pinned against a literal **and** against `Shape`'s own
members, parametrised over the literal, and asserting the label inside that shape's own row.

**6. The flag-on production wiring was never executed.** Replacing `telemetry.shared_recorder()` with
`None` left everything green — every test either passed `recorder=` or ran flag-off. The only path by
which this feature turns on in production was unexercised and neutering the flag was invisible.

**7. `_file`'s `shape is None` guard was removable, and its removal corrupts the published rates.**
Removing it leaves the row refused at construction — so the existing assertion still held — while
`counters.attempts` goes to 1 and `dropped` to 1, unattributable. `attempts` is the denominator of both
published rates, so every unit-2-style call without `shape=` would inflate it and deflate them.

### Also fixed, because each was cheap and load-bearing

- **The flag was required but not frozen** (`cybersecurity`). `get_settings().telemetry_enabled = True`
  succeeded on the cached singleton under `validate_assignment`, and every `EspnService` built afterwards
  would record. The comment claimed "the same fail-closed shape as `app_mode`" while omitting the half
  that makes `app_mode` fail closed. Both settings are now `Field(..., frozen=True)`.
- **The ninth form, and five more.** `cybersecurity` broke the widened internals scan: `ast.AsyncFor`
  and `ast.AsyncWith` are distinct node classes and were never visited; `__dict__.update` is strictly
  stronger than the subscript form already caught; comprehension targets were missed; and **`del` was
  missing entirely** — the `del gate._sealed` blind spot recurring, in the predicate written after it,
  with `del telemetry.Record.__init_subclass__` re-opening the round-5 subclass vector. All added, plus
  `delattr`. **The finding behind the findings is stated in the predicate now: it widens by enumerating
  node kinds, so every kind not enumerated is a gap** — structurally the same shape as the three rounds
  of `re.sub` widening it replaced.
- **The artifact had no writer and `telemetry_report_path` had no reader.** `render_report` had two
  references repo-wide, both in test scope; the artifact was produced by an ad-hoc script, so nobody
  could regenerate it and a required setting was dead. `write_report` is the committed entry point, the
  artifact was regenerated through it, and a probe covers it.
- **`censored` appeared in no assertion**, so `_censored_ms` returning 0 unconditionally was invisible
  and the artifact's all-zero column attested nothing. Now exercised at five refused durations.

### Final state

| Artifact | SHA-256 |
| --- | --- |
| `api/services/telemetry.py` (1,131 lines) | `de1ad3ebf37ee711d2ca3b8a8b1a00e504d404f56ee1e1639c3536e6fe9544ad` |
| `api/services/espn.py` (567 lines) | `323d1a05a8695f688de8e902be155bfc8724a5ba877b2e31dc8ea35f7f4b2678` |
| `api/config.py` | `bd7aa0eb7f62be3d0f2dddd55f8bd5d8a076e90993daca12bc181c991b01a2ee` |
| `tests/conftest.py` | `7ad83462b2ef44ee6c607db9b44f084a2e41f4782fce3c0ed4044a0feb6bab37` |
| `tests/test_telemetry_seams.py` (2,323 lines, 228 tests) | `67fdc863d8e7642608e837d0c5617f997bd0a8442bc0e2801d03a4801d1bc019` |
| `tests/test_provider_telemetry.py` (1,098 lines, 79 tests) | `960c7815a79a9c4aaef420eac3451809ade7c049fbb2e151907dd990435fecd6` |
| `docs/evidence/phase-32-report.md` (93 lines) | `81ca84fd958c64fca1f33bc668c5d5e8be3dcf96c140bbcbc7b602c699dc59b6` |

**Gates: 806 passed / 0 failed** across every suite but recovery, with `test_recovery*.py` excluded;
recovery unchanged at its **18 pre-existing platform failures**; `ruff check api tests` clean; Phase 31
acceptance gate 19/19. Worktree **47 untracked / 46 modified** — the one added entry is
`tests/test_provider_telemetry.py`.

**On the pass count.** E32.8 said 783; `cybersecurity` measured 782 and `qa_test` 823, over different
ignore sets. The number above is 806 with the four `test_recovery*` files excluded, and the scope is
stated because that is the whole reason three measurements disagreed. E32.1b and E32.1e both withdrew a
headline number for the same reason; this is the third.

### Known-open, carried to Phase 39

K-1 through K-9 from E32.7 stand except K-1 and K-2, which unit 3 discharged. Added:

| | Finding |
| --- | --- |
| K-10 | **The rates table mixes populations.** Numerators come from filed rows, the denominator from `counters.attempts`, so a drop or overflow biases every rate in the flattering direction. An `attempts not classified` row now travels with them, but the rates themselves are still not per-shape and `overflowed`'s head bias is not marked in that table. |
| K-11 | **The loopback guard is connect-time, in-process and exception-signalled.** `connect_ex` bypasses it; subprocesses are outside it (21 across 5 files); `getaddrinfo` precedes it, so a forgotten host resolves before being blocked; and the `AssertionError` is swallowable by an `except Exception` — `api/verify.py:191` has one around the ungated cross-check call. |
| K-12 | **Unexercised wired branches**: `_league_shape`'s pre-2018 arm, `fetch_player_pool`'s body (so `PLAYERS_DEFAULTS` is never filed by the wiring), `fetch_pro_schedule`'s MISS/BYPASS path, `_file`'s decode-failure branch. |
| K-13 | **`_render_small` and `_render_bytes` ceilings are unexercised**, so `season`, `week`, `leagues` and `status` have no over-ceiling coverage. |
| K-14 | **`_censored_ms` and `_render_ms` disagree on `bool`**, and `_ATTEMPT_CEILING` is declared with no consumer. |
| K-15 | **Both scans exempt by `path.name`, not path** — a future `api/routers/espn.py` would be silently exempt. |
| K-16 | **The mention scan's plants re-implement its predicate** instead of calling it, unlike the internals scan which was deliberately extracted for that reason. |

### Phase 32: CLOSED

**Retired:** the measurement instrument risk — a recorder wired behind a required, frozen flag; 307
tests across two suites; call arithmetic derived from source; a report under a closed grammar with a
committed writer.

**Not retired:** the network term risk. **The ESPN network term is unmeasured.** No live read was ever
authorized or performed, at any point in the phase.

**Fourteen review rounds.** Four NO-UNIT on unit 2, eight on the contract across eight drafts, two on
unit 3. **Every finding both reviewers reported independently was real, without exception, in all
fourteen.** The recurring defect reached **40 recorded instances** and appeared in the newest text of
every round — including the patch that fixed it, which introduced a six-digit run while explaining the
six-digit rule.

**Control state.** No provider network call at any point. No Keychain, no Restic, no commit, no
deletion, no spend. **No live read.**
