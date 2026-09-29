# Phase 32 — Provider Behavior Spike (narrowed)

Source plan: `docs/sprint-9/06-phases.md`, Phase 32. Predecessor: Phase 31, complete.

**Draft 8.** Drafts 1–7 are superseded in full. This draft is **narrower than all of them by design**, on
a Product Manager decision: the live ESPN read is **out of scope for Phase 32, permanently** — not
deferred, not authorized separately later. Out.

That is a scope cut, not a finding. It is taken because eleven review rounds across seven drafts spent
their cost on machinery that exists only to make a live read safe, on a practice project where the live
read was never going to be authorized.

## What this removes, and what removing it costs

**Removed outright:**

| Removed | Why it existed | What it took with it |
| --- | --- | --- |
| The live-read envelope, all eight clauses | Bounding one authorized cold sync | The approval-id protocol, the operator-set ceilings, the scheduled-window suspension, the entry-point exclusion of `verify.py` |
| The **hard stop** (`HardStop`, `ProviderHardStop`, the arming site) | Stopping a run after a 429/401/403 | Draft 7's **P1-2** in full — the arm site at `espn.py:170` never fires for 401/403 and would have broken criterion 1's own 5xx case. Also register entries **D-1** and **D-5** |
| The **EspnService-wide rate gate** | Bounding live provider traffic to one start per second | Register entries **D-3** (no delivery path for a clean gate) and **D-4** (doubled loop unreachable flag-on). Both were caused by the gate, not by the recorder |
| The publication grid, its ceilings, `censored`, and the six-plant two-sided test | Stopping a real league id reaching a published figure from a live run | Roughly a third of draft 7's prose and the site of most of the last three rounds' defects |

**What it costs, stated plainly because this is the whole cost:** the phase's original risk is **not
retired**. The ESPN network term stays **unmeasured**. Phase 39 will design worker windows, retries and
fairness against an unmeasured term unless it measures it first. Every draft since 3 carried this exit
and described it as legitimate; taking it is not a failure of the phase, but it is not the phase's
stated outcome either, and the report says so in those words.

**What survives, and why it is worth finishing:** the recorder wired into the provider behind a flag,
and an **offline** measurement harness over it. That is a real deliverable — it turns "we do not know
what our provider does" into a suite that answers it from fixtures and a loopback socket, re-runnable
by anyone, with the call arithmetic derived from source rather than eyeballed. It is also the artifact
Phase 39 actually needs first.

The three rules the previous drafts were written under are kept:

- **Every quantity is either measured by a named probe, derived from source by a named tool, or
  labelled an assumption in the same sentence.** No third category.
- **Every capability claim is accompanied by its limit**, stated as explicitly.
- **Where two things could be conflated, both are given.**

**Time box.** One review round on this draft, both lanes in parallel. **The phase closes on that round
regardless of verdict.** P1s are fixed; P2s and below are recorded in
`docs/evidence/phase-32-provider-behavior.md` as known-open and carried to Phase 39 rather than fixed
here. That is a deliberate change to the acceptance discipline and it is the last one.

## What unit 2 built

**Complete.** `api/services/telemetry.py` and `tests/test_telemetry_seams.py`: 211 tests, 50/50 controls
held under control-removal, **138 of 145 mutations killed — 3 equivalent, 2 masked by a deliberate
redundancy in `cache_verdict`, 2 unevaluable (they break module import)** — 100% branch coverage over
231 statements and 72 branches with 0 partial, 0 flaky runs in 12. Account in E32.1 and its corrections
E32.1a–E32.1h. **None of these figures is verifiable by reading**; criterion 6 re-establishes them.

`RateGate` and `shared_gate()` stay in the module as built and tested. **Nothing calls them.** They are
dead code for this phase, retained rather than deleted because deleting them would change a module
whose suite is the phase's one closed artifact. That is stated here so a later reader does not infer a
live gate from their presence.

| Guaranteed, held by a named probe | Not guaranteed |
| --- | --- |
| A `Record` field accepts only a closed-enum member, an `int` or a `bool`, checked at construction against an immutable rule table; a rejection names the field and the type, never the value. | That an `int` field contains a measurement. A league id is a number and **this phase establishes no lower bound on how narrow one can be.** Range bounds refuse nine or ten digits; everything below is in range. The precondition is on the caller. With synthetic-only data this is a latent defect rather than a live exposure — see criterion 4. |
| Enum members render only through `_token`, an immutable table looked up by identity, so a member whose `_name_` was rewritten carries nothing into a repr, a counter or `__getstate__`. | That an exporter reading `record.shape.value` is safe, or that `dataclasses.asdict(record)` is — it still emits live members. Criterion 5 enforces both. It does **not** pin rebindable `_TOKENS` or `_FIELD_BOUNDS`, which stay rebindable because the suite monkeypatches `_TOKENS` itself. |
| `Recorder.record` never raises an `Exception` into the caller, for any input, including hostile mappings. | `BaseException`. A `KeyboardInterrupt` propagates by design, and three paths through `record()` file nothing: field rejection, overflow at `:600`, and `BaseException`. |
| The provider contains no reference to the telemetry module, by an AST scan over every module under `api/` **and** a clean-subprocess import check. | **That this survives unit 3.** It cannot: wiring the recorder requires the import. Both probes retire. See D-1 — now the only deferred item, and the one the draft-7 review showed is not closed by the successor the suite names. |

## Measured behaviour

Pinned by `tests/test_telemetry_seams.py`; recorded in E32.1.

| Fact | Consequence |
| --- | --- |
| A cache HIT never reaches `_request` | A hit rate counted at the request boundary is structurally zero, flatteringly. Hits are counted where they are decided. |
| On the only path producing a BYPASS, the cache is never consulted | The bypass verdict is taken at the guard, not the consultation. |
| A retry does **not** re-consult the cache (3 attempts, 1 consultation) | No MISS inflation. |
| A `text/html` 200 is separable from a JSON 200 **by header, not status** | Recoverable at the boundary, so `_json_or_auth` is not instrumented — though three of the six URL-bearing messages are raised there. |
| Under a declared `Content-Length`, a truncated body is **not** a 200: it raises `RemoteProtocolError`, an `HTTPError`, and is retried | Measured on a real loopback socket after a `MockTransport` version silently measured nothing. **One framing, not two** — see the scope note. |
| A 4xx is returned, not retried, and costs no backoff; 429/500/502/503/504 are retried to exhaustion | Every status named is exercised. |
| The five URL families have three prefix collisions; the pre-2018 family's id is the trailing segment | `Shape` is chosen by the builder that ran. No matcher, no `UNKNOWN` sink. |
| An exhausted call sleeps 1+2+4+8 = **15s**; the final 8s precedes no request | Backoff dominates the failure case and is recorded in its own field. |
| Four requests across three throttle keys sleep **once** | A deployment-wide budget cannot be expressed per key. **This phase no longer fixes that** — it is Phase 39's, and the built-but-uncalled `RateGate` is the head start. |

**Scope of the truncation fact — three framings, and unit 2 measured one.** Drafts 4-7 said two and
this draft said two; review measured one, and the correction is here rather than carried:

- **Declared `Content-Length` — measured.** The one loopback socket in the suite
  (`tests/test_telemetry_seams.py:334`) sends an explicit `Content-Length` at `:356` with
  `Connection: close`, and the retry half at `:384` replays a `RemoteProtocolError` through `FakeClient`.
- **Chunked framing — NOT measured.** The strings `chunk`, `transfer-encoding`, `gzip` and
  `content-encoding` appear **nowhere** in the suite. There was no probe; the claim was inferred from the
  declared-length one and printed under a heading that says "Pinned by `tests/test_telemetry_seams.py`".
  It becomes a unit-3 case in criterion 1, not a unit-2 fact.
- **Identity framing closed by connection close — not coverable.** Truncation is undetectable at the
  transport: a clean short 200 with `wire_bytes = -1`, so no cross-check exists. **Named as not covered.**

The scope note in earlier drafts named the third framing carefully while mislabelling the second as
measured, which is the phase's signature defect committed inside the sentence written to prevent it.

Two facts from source, not seam-testable:

- **`EspnService` is not the only ESPN client.** `discovery.py:72` builds its own `httpx.Client`;
  `cross_check.py:59-65` uses `espn_api`. Neither is instrumented. Every figure this phase publishes is
  labelled **"EspnService traffic only."**
- **There is no total request deadline.** `timeout=30.0` is per-operation (`espn.py:123`) and httpx
  applies the read timeout per chunk, so a slow body is unbounded and `max_retries` multiplies it. The
  report carries it as an unbounded term. Adding a deadline is Phase 39's.

## The call arithmetic — derived from source, and the phase's substantive output

By walking `sync.py`'s AST: call site, method and `bust_cache` keyword per call. Seven `self.espn.*`
sites; the count is not eyeballed.

**Logical calls per league:** `4 + 2·B_i + W_i`, `B_i` = 1 when the `current_period is not None` branch
runs. Four unconditional `fetch_views` at `sync.py:116, 180, 253, 269`; the branch carries
`fetch_views:195` **and** `fetch_pro_schedule:207`; `W_i` is the per-week boxscore at `:238`.

**Network calls.** Five of six `fetch_views` pass `bust_cache=True` (`:121, 185, 201, 258, 275`) and
always reach the network. **`:238` passes none**, so it defaults to `False`, consults the cache, and on a
warm cache produces no network call and no HTTP record. `fetch_pro_schedule:207` consults a cache keyed
`pro_schedule:{season}`, not league-scoped.

```
network_calls_per_league  <=  4 + B_i + W_i        (cold boxscore cache)
network_calls_per_league  >=  4 + B_i              (warm: every W_i call is a HIT)
shared per window          S = 1 if cold, 0 if warm  (pro_schedule, not league-scoped)
```

`W_i` is per league (`sync.py:626-637`), derived at `:232` **from data the run itself fetched**, and
collapses to one call if step 1 fails — so a bound from this formula is not knowable before a run starts.

**What a refusal costs, kept because it is the finding most worth carrying to Phase 39.** `sync_league`
spans `sync.py:91-337` with **eleven** handlers — **seven** `except EspnError` at
`:131, 189, 209, 225, 247, 263, 280`, plus `:108` (`EspnReauthRequired`), `:123` (`EspnAuthError`) and
two bare `except Exception` at `:310` and `:324`. It returns early at **only three sites**: `:111`,
`:130`, `:136`, all in pre-flight and step 1. **Every handler after step 1 appends to `result["errors"]`
and falls through**, including the `W_i` loop at `:236-248`. So a refusal first seen at step 2 costs up
to `5 + W_i` further logical calls — order twenty at a full season, each up to four attempts on the 15s
ladder, **order eighty further HTTP attempts into a provider that is refusing** — and a 401/403 at step 2
does not flip `needs_reauth`, since only `:130` does. **Nothing in this phase fixes that.** It is
recorded as a Phase 39 precondition with the magnitude attached, which is more than the phase started
with.

## Observable shapes

**Two of five have no caller.**

| Shape | Builder | Call sites under `api/` | Observable |
| --- | --- | --- | --- |
| `LEAGUE_MODERN` / `LEAGUE_HISTORY` | `league_url` via `fetch_views` | six in `sync.py` | yes, offline |
| `SEASON` | `season_url` via `fetch_pro_schedule` | one (`sync.py:207`) | yes, offline |
| `PLAYERS_DEFAULTS` | `players_url(defaults=True)` via `fetch_player_pool` | **no caller under `api/`** | **no** |
| `PLAYERS_SEASON` | `players_url(defaults=False)` | **never invoked at all** | **no** |

A zero with no trials is not a rate with a wide interval; it is **not a measurement**. Criterion 2
distinguishes them.

## Implementation contract

**Owned paths.** `api/services/telemetry.py`; `api/services/espn.py` — record attempts and take the cache
verdicts where each is decidable, **nothing else** (no gate call, no hard stop); `api/config.py` — the
flag and the report destination, neither defaulted; directly corresponding tests; append-only
`docs/evidence/phase-32-provider-behavior.md`; and `docs/evidence/phase-32-report.md`.

**Forbidden paths.** Every other file, and `api/services/discovery.py` and `api/services/cross_check.py`
by name. No change to `sync.py` — including no change to the missing `bust_cache` at `:238`, **recorded
as a finding and left alone**; no change to any parser, schema, retry rule, backoff, timeout,
`max_retries`, header, query parameter or URL construction; no conditional-request headers; no new or
write endpoint; no login or HTML; no frontend; no Phase 30 recovery path; no Phase 31 hosted-mode guard
or spend ledger. Threading a `Shape` keyword through `_get_authed` and `_request` is **permitted**.

**No provider network call is made by this phase at any point.** Every case is a fixture or a loopback
socket. This replaced an envelope clause, and as first written it was **not enforceable**: Forbidden
paths are enforced by reading a diff, and this is a runtime property. `api/config.py:64` defaults
`espn_api_host` to the real provider and `api/services/espn.py:122` falls back to it, so a construction
that omits `host=` builds real provider URLs — and `tests/conftest.py` pins six environment values with
**no host pin and no socket guard**, in a suite that criterion 1 now requires to speak real HTTP on a
loopback socket. So the statement is given teeth rather than left as discipline: **criterion 1 requires
`tests/conftest.py` to pin the host to a loopback sentinel and to carry an autouse guard permitting
outbound connections to `127.0.0.1` only**, failing any test that attempts another host. That is the
enforceable form, and it is cheap.

**Work units.** (1) Contract acceptance. (2) Seams — **done**. (3) Wiring and exporter, offline. There is
no unit 4.

## D-1 — the one deferred item

`espn.py` must import telemetry to call the recorder, so
`test_nothing_under_api_mentions_the_telemetry_module` (`tests/test_telemetry_seams.py:1911-1949`) and
the clean-subprocess check (`:2024+`) both fail at wiring. **Drafts 1–7 all said "the suite passes
unchanged," which was never true.** The suite named a successor at `:1996-2002` —
`test_nothing_under_api_assigns_to_this_modules_internals` (`:1995-2021`).

**The draft-7 review showed that successor does not close what retires, and this is the correction.** Its
offender predicate fires only when the assignment target's base expression textually contains
"telemetry". So under `from api.services.telemetry import Recorder` it is **vacuous** — it cannot fire,
and it passes. `setattr(telemetry, "_TOKENS", x)` is a `Call`, not an `Assign`, and is not caught either.
The retiring scan carried `Constant`/`BinOp`/`JoinedStr`/`Call` passes and the subprocess check covered
runtime-assembled references; the successor is static-only and covers neither.

**The predicate is narrower still than that diagnosis.** Review measured it with controls: it collects
targets only from `Assign`/`AugAssign`/`AnnAssign` and then tests `isinstance(t, ast.Attribute)`, so
every non-`Attribute` target escapes **even when the base is literally `telemetry`** —
`telemetry._TOKENS, telemetry._FIELD_BOUNDS = (), ()` misses, and so do a `__dict__` subscript, a `for`
target, a `with … as` target and a starred unpack. The module's RESIDUALS at `telemetry.py:77-84` name
`telemetry._FIELD_BOUNDS = ()` as their own worst vector — "a single statement and every bound is gone"
— and the tuple-assignment spelling of that exact statement is uncaught. So "fires only when the base
expression textually contains 'telemetry'" is **necessary, not sufficient**, and this draft's first
wording read as sufficient.

**Unit 3's close must show the successor *firing on a planted offender* in each of five forms, not
merely passing:** (1) a direct attribute assignment through an alias not containing "telemetry";
(2) `setattr` with a literal name; (3) an assignment through a name bound by `from … import …`;
(4) `setattr` with a **runtime-assembled** name — the form the retiring scan covered at `:1933` via
`re.sub` over `Constant`/`BinOp`/`JoinedStr`/`Call`, and which its own comment at `:1934-1945` records as
claimed in E32.1f, measured absent, claimed again in E32.1g, and measured absent again; and (5) a
**non-`Attribute` assignment target** with the literal module name — tuple, `__dict__` subscript, `for`
and `with … as`.

**And the fallback is not dischargeable by a docstring.** Whatever the widened predicate cannot catch is
pinned in the form the suite already uses at `:1972-1992` — `test_and_what_the_scan_cannot_catch`, whose
probes **fail when a limit moves** ("this form is now catchable; move it to the probe above") — not
stated in prose. A prose fallback would let an author widen nothing and satisfy this entry, which is the
"satisfiable while establishing nothing" defect this entry was rewritten to close.

**The subprocess check's coverage retires with nothing static replacing it.** `:2024+` is what covered
runtime-assembled *references*; no static scan can. That is named here as **uncovered after unit 3**, not
as replaced.

Every unit-2 probe not named in this entry or in criterion 1's skip amendment passes unchanged. This is
the only register entry because the other four died with the gate and the hard stop.

## Acceptance criteria

Six. All decidable offline; none depends on a live read, a gate, or a stop.

1. **Offline cases, each asserting status, outcome, the four time fields separately, and redaction:**
   200; 401; 403; 404; 429; 5xx; connect timeout; malformed body (**records as a 200**); 302;
   `text/html` 200 (**`content_type_json` false**); truncation under a declared `Content-Length` and
   under chunked framing (**records as a transport error, not a 200**); empty 200; chunked with no
   `Content-Length`; gzip (**wire and decoded differ, both recorded**); **retry exhaustion driven by a
   5xx** (four attempts, `backoff_ms` summing 15,000 with the final 8,000 present); and `_get_authed`'s
   doubled loop, asserting **exactly 8 attempts**, with all three preconditions stated because any one
   of them missing makes the case pass while measuring 2: (a) the first response is **401 or 403**, or
   `:313` returns before the second ladder; (b) `unquote(espn_s2) != espn_s2`, or `:318` returns; and
   (c) **each** `_request` spends three retryable statuses and returns the 401/403 on its fourth attempt
   — because 401/403 are not in the retry set at `:170` and return at `:175` after one attempt, so a
   plain 401-then-401 script costs **2**, and an all-retryable first ladder raises at `:176` and never
   reaches `:313` at all. "Up to 8" is satisfied by 2; the criterion says exactly 8 and names the
   script.

   With no hard stop, **every case above is reachable with the flag on** — the exhaustion ladder and the
   doubled loop both included. Draft 7 had to bind exhaustion to a 5xx and defer the doubled loop
   because the stop interrupted them; that constraint is gone with the stop.

   **Two cases require a real loopback socket and must not pass by skipping**: truncation under a
   declared `Content-Length`, and truncation under chunked framing — protocol enforcement is the one
   thing a `MockTransport` cannot do. **Not four.** `wire_bytes` is "`Content-Length` as sent" read from
   a header (`telemetry.py:339`), not counted off the wire, so `FakeClient:82-90` can already produce
   wire != decoded for gzip and `wire_bytes = -1` for chunked-with-no-`Content-Length` by constructing
   the response directly. The claim that a `MockTransport` cannot "produce distinct wire and decoded
   counts" was false, and mandating a socket for those two doubled the exposure to a
   bind-unavailable environment for no measurement gain.

   A probe fails the build if either socket case skipped. The suite's only current skip is `:339`, on
   `OSError` from `server.bind`, inside `test_a_short_body_against_a_declared_length_is_a_protocol_error`
   — which is itself one of these two cases, so criterion 6's "passes unchanged" and this requirement
   touch the same test: **the skip becomes a failure, and that is a named amendment to a unit-2 probe,
   the second one after D-1.** Loopback-bind availability is hereby a **declared build precondition**;
   an environment without it cannot satisfy this contract, and the contract says so rather than letting
   the build pass having measured nothing.

   304 is included and **labelled dead** until conditional requests exist. Two cases are **excluded and
   named**: slow body (no total deadline, so no bounded assertion) and **truncation under identity
   framing closed by connection close**.
2. **Cache verdicts where each is decidable, and a zero is never rendered as a rate.** The three-way
   denominator is reported: HIT (no HTTP record), MISS (absent and stale **not** separable at this seam,
   and the report says so), BYPASS (`bust_cache=True` or no cache); `bust_cache` is named as the cause
   and not changed. The exporter emits `(events, trials)` and prints `0/N observed; 95% upper bound ~
   3/N` when `trials > 0`, and `not measured — no call site` with **no interval at all** when
   `trials == 0`. Each structural zero is named: error rate, cache hit rate at the request boundary, 304
   count (trials > 0, dead by design), and the two shapes with no caller (trials == 0).
3. **Flag off: nothing recorded, no file. Flag on: request content, order and count unchanged** — over
   every case in criterion 1, comparing URL, ordered params, headers, attempt count, raised exception
   type **and raised exception message**. The message is in the tuple because **six** sites interpolate
   the request URL into one — `espn.py:171, 177, 326, 331, 333, 337` — and three of those, in
   `_json_or_auth`, raise the messages for criterion 1's own `text/html` 200, malformed-body and 401/403
   cases. (Of the six, `:171` assigns `last_exc` rather than raising; it reaches a caller as the
   `__cause__` of `:176`, whose message is `:177`. So six sites construct such a message and **five**
   chains reach a caller.)

   **This criterion is now unconditional.** Draft 7 needed two carve-outs — one for the gate's timing,
   one for the hard stop's deliberate truncation of the request count. Neither exists. The comparison is
   still **single-threaded and per-`_request`**, and cross-thread ordering is not claimed, since nothing
   in this phase reorders concurrent starts any more.

   **A recorder that raises on every call changes nothing**: same requests, same results, non-zero
   `dropped`.
4. **Nothing published or recorded carries an identifier, and the limits of that are stated.** Two
   artifacts:
   - `docs/evidence/phase-32-report.md`, machine-generated in full between declared `BEGIN`/`END`
     markers, passes a grammar in `make test`: a **closed allowlist written literally in the test file**
     — not derived from the exporter, or every future token is auto-admitted; **no URL**; **no digit run
     of six or more**, with no exemption; byte counts **bucketed to the nearest 64 KiB and rendered as a KiB
     or MiB multiple** (`3.0 MiB`), never as a raw byte integer, and published only as per-shape
     aggregates with `n`, never as a per-attempt series. The rendering is not cosmetic: a 64 KiB bucket
     printed in bytes is a multiple of 65536, so every bucket from the second (128 KiB) upward trips the
     digit-run rule, and with `wire_bytes` bounded at `1 << 26` and a league payload on the order of
     megabytes (`telemetry.py:308`) the two rules as first written admitted exactly **two** publishable
     values and failed the build on any realistic payload. Draft 7 rendered in human units and this draft
     dropped that when it simplified the grid away — the simplification broke the control. The grammar is two-sided: a planted
     league-id-shaped segment and a planted real-entropy GUID each fail the build, and both the real
     file and a **fixture-produced** one pass.
   - `docs/evidence/phase-32-provider-behavior.md`, append-only, from **line 888 forward**: no URL, no
     `leagues/<digits>`, no `str(exc)` of an `EspnError`, no `result["errors"]` entry. Digests are exempt
     by anchoring to the ``SHA-256 `<64 hex>` `` form — of the ten lines in the first 887 carrying a
     ≥6-digit run, **eight are entirely inside digests** by span; the two others are line **94** (a
     literal league-shaped URL inside a `Record(...)` illustration, the synthetic constant from the
     round-4 finding) and line **652** (a duration at seven decimal places). Both are pre-contract
     record and the rule is forward-looking because the file cannot be edited. The **mechanically
     enforced half is the URL and digit-run check**; the `str(exc)` and `result["errors"]` rules are
     operator discipline, because text cannot reveal its provenance.

   **The limits, stated as explicitly as the control.** Bucketing bounds *precision*, not *linkability*.
   The grammar closes nothing that is not a URL, a long digit run or an unlisted token — an eight-hex
   `_short_hash` of a SWID (`espn.py:41-43`) is a **linkable pseudonym** that no rejection rule catches,
   which is why the allowlist is literal and closed rather than a blocklist. And **no range check
   distinguishes a measurement from an identifier**: drafts 5–7 built an elaborate grid and ceiling
   scheme for that, justified entirely by a live read putting a real league id into a published figure.
   **With the live read out of scope and every input synthetic, that is a latent defect in the caller's
   precondition, not an exposure.** It is recorded as a Phase 39 precondition — the grid design is in
   draft 7 in git history if Phase 39 wants it — and it is the single largest thing this narrowing gives
   up. Said here rather than dropped quietly.

   **`result["errors"]` is also persisted, not only read from a console.** `sync.py:329-332` joins it
   through `_safe_error` into `_record_diagnostics` (`:340-345`), which writes `league.last_sync_error`.
   `_safe_error` (`:348-358`) redacts only `espn_s2`, `SWID` and `swid` and has **no URL rule**, so it
   runs and does not redact the request path. With no live read the persisted text is synthetic, so this
   is recorded rather than fixed — `sync.py` is Forbidden — and carried to Phase 39. Drafts 4–7 called
   this a console channel only; there are two read paths and both are now given.
5. **No owned module reads a member's name or value, and the exporter's output is invariant under a
   rewritten member.** The probe at `tests/test_telemetry_seams.py:834-863` reads exactly one file while
   its docstring says the exporter inherits the rule, and it has **two** offender passes — the Attribute
   comprehension at `:843-849` and the `getattr(member, "value")` Call pass at `:854-862` — both of which
   a widening must carry. The traversal it needs is at `:1920-1923`, walking `(ROOT / "api").rglob("*.py")`
   with a `telemetry.py` skip at `:1921-1922` the widening must drop.

   Extended to **every owned path**, failing on `.name`, `.value`, `_name_` or `_value_` on a `Shape`,
   `Outcome` or `CacheVerdict` expression, and on any `dataclasses.asdict` call. Owned-path scoping is
   load-bearing: the five `asdict` sites under `api/` are all in `api/services/recovery.py`, a Forbidden
   path, so a literally tree-wide scan fails on day one. **The limit:** identifying "a `Shape`
   expression" statically needs type inference — `x = record.shape; x.value` is invisible to any
   receiver-name test. Unit 3 states which spellings its predicate catches and which it does not, in the
   same place it claims the capability.

   Paired with it: a probe asserting the exporter's output is **byte-identical with and without
   `Shape.LEAGUE_MODERN._value_` rewritten**. This pins **one** documented residual — `asdict` emitting
   live members. It does not pin rebindable `_TOKENS` or `_FIELD_BOUNDS`.
6. **The suite and the gates.** `make test` passes; **`make lint` exits 0** — the target is
   `ruff check api tests` (`Makefile:56-57`); the `alembic` arm drafts 4–5 specified runs in no target
   and is manual. The Phase 30 recovery suite is unchanged at its 18 pre-existing platform failures; the
   Phase 31 acceptance gate passes. **Every unit-2 probe not named in D-1 passes unchanged.** This is
   also what re-establishes unit 2's figures, which no reading can verify.

   The window arithmetic is published **offline and labelled as such**: wall time summed over recorded
   attempts as `sum of max(1s, net_ms + backoff_ms)` over the bounded network term above, with the
   7,200s window **an assumption from `06-phases.md`, not a measurement**, and break-even published as a
   function of the window. Every input is a fixture, so the output is **a model of occupancy, not an
   observation of it**, and the report uses those words. That distinction is the whole difference between
   this close and the one the phase originally aimed at.

## How the phase closes

Units 2 and 3 complete; D-1 answered; criteria 1–6 satisfied; **the ESPN network term reported as
unmeasured, in those words**, with the method for measuring it later — which is draft 7, in git history,
envelope and grid intact. The report states that Phase 32 retired the *measurement instrument* risk and
**not** the network-term risk, and names the four things it hands Phase 39: the unbucketed-identifier
precondition, the persisted `result["errors"]` channel, the fall-through cost of a refusal with its
magnitude, and the built-but-uncalled rate gate.

## Non-goals

Retuning retries, backoff or timeouts; the total request deadline. Conditional requests. Changing
`bust_cache` anywhere. Changing `DBRawCache.get`'s inability to separate stale from absent.
Instrumenting the two out-of-seam clients. Worker windows, fairness, backpressure. Any change to what is
fetched. Any provider network call. Calling the rate gate. Changing the two bare `except Exception`
handlers at `sync.py:310` and `:324`.

## Process and acceptance

Accepted by **exact SHA-256 of this file**. **One** parallel read-only `qa_test` + `cybersecurity` round,
then the phase closes regardless of verdict: P1s fixed, P2s and below recorded as known-open in
`docs/evidence/phase-32-provider-behavior.md` and carried to Phase 39. No provider, network, ESPN,
Anthropic, AWS, Keychain, Restic or device mutation; no commit, merge, prune, deletion or spend. The
worktree stays at 46 untracked / 46 modified.

**Where this draft most likely fails.** The narrowing itself: a cut this large can remove a control whose
absence is not obvious, and criteria 1 and 3 were both *widened* by the removal of the gate and the stop
— every case is now reachable flag-on, and criterion 3 lost both carve-outs. A claim that got easier is
a claim nobody re-checked. Both reviewers are asked to check those two, and the "no provider network
call" Forbidden-path statement, hardest.
