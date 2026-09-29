> **SUPERSEDED — do not implement from this document.**
>
> Draft 3 of `docs/phase-32-provider-behavior-spike.md` replaces the contract and this amendment in
> full. Review found that sections I and J of this document were themselves written against draft 1
> — the same stale-bytes failure this document opens by describing — and that its corrected call
> formula double-counted `fetch_pro_schedule`. Kept unedited as part of the record.

# Phase 32 amendment — corrections to the accepted contract

Amends `docs/phase-32-provider-behavior-spike.md`, accepted SHA-256
`dcfa19e7f2742479cdc6de4ba32d085a230d21ba17ef614e348aa1c583f39166` (the bytes above that document's
acceptance footer). The accepted bytes are not edited; this document supersedes them where they
conflict, and requires its own acceptance before work unit 2 begins.

## Why this exists, stated first

The parallel re-review of draft 2 **reviewed draft 1.** Agent 1 rewrote the contract on the operator's
machine and never re-staged it for the reviewers, then briefed them that sixteen fixes had been folded
in. Both reviewers independently computed the artifact's digest, found it was the rejected draft, and
refused to certify it — which is the correct response and the only reason this was caught.

Confirmed: the reviewed copy was `1233735e…`, 230 lines; the real draft 2 is 328 lines. The staged
Phase 31 evidence was likewise three entries old, which is why one reviewer reported the Phase 31
acceptance gate as unrecorded. That finding is withdrawn; the gate is recorded in E31.6.

The process lesson is recorded with the technical ones: **a review is of the bytes the reviewer can
read, and it is the author's job to make those the right bytes.** Every future dispatch re-stages
first and states the digest in the brief so the reviewer can check it against what they opened.

Everything below, however, is a real defect in draft 2, verified against the code before acceptance
here.

## A. "There is one HTTP boundary" is false at the system level

`EspnService._request` is the only request site **inside `EspnService`**, and draft 2 generalises that
to the system. It is not true:

- `api/services/discovery.py:72` builds **its own `httpx.Client`** and issues cookie-bearing requests.
- `api/services/cross_check.py:59` calls the third-party `espn_api` library, which makes its own.

Phase 31's own E31.1 recorded both, and Agent 1 wrote the wrong claim anyway.

**Consequences, all of which must be stated in the report rather than discovered later:** the spike
measures `EspnService` traffic only; the process-global rate gate does **not** throttle those two call
sites, so the deployment-wide 1 rps budget is not actually enforced by this phase; and a Phase 39
designer sizing a shared budget from these numbers would be sizing it against a subset. Every figure
this phase publishes carries the label **"EspnService traffic only"**.

## B. The call formula, corrected again

Draft 2 says `6 + completed_weeks`. Verified against `sync.py`, both the count and its conditionality
are wrong:

- `fetch_views` at `:195` and `fetch_pro_schedule` at `:207` are **both** inside
  `if current_period is not None` (`:192`), and `:207` is nested inside `:195`'s own `try`.
- `_completed_weeks` (`sync.py:626-638`) is computed **per league** from that league's matchups and
  its own `current_week`, so a portfolio has `W_i`, not a shared `W`.
- `completed_weeks` is derived at `:232` **from data the run itself fetched**, so the bound is not
  knowable before the run starts.

Corrected: per league, `4 + 2·B_i + W_i` where `B_i` is 1 when the current-period branch is taken and
0 otherwise, plus `S` shared `fetch_pro_schedule` calls per window — `S` being **distinct seasons**,
which is 1. Draft 2's criterion 10 term `+ W` is wrong: it re-modelled a season-scoped call as
per-week after review had already established it is per-season. That is a narrower instance of the
defect it was fixing, which is this project's most persistent failure shape.

**Criterion 10 is replaced by:**

> Wall time is `Σ` over **HTTP attempts**, not logical calls, of `max(1s, net_ms)` **plus every
> backoff sleep**, over `Σ_i (4 + 2·B_i + W_i) + S` logical calls, with `L` stated, the week stated,
> the fraction of attempts exceeding 1s reported, and **two** break-even figures published: the league
> count, and the **retry-exhaustion rate**. The second is the binding one. A logical call that
> exhausts all four attempts burns `1+2+4+8 = 15s` of `time.sleep` — and the final 8s buys nothing,
> because `range(self.max_retries)` is exhausted and `_request` raises immediately after
> (`espn.py:159-178`). Against a 7,200s window at L=115, the headroom is ~2.8x in league count but
> only ~13% in exhaustion rate at W=17. A model that omits backoff underestimates by 15s per failed
> call — precisely in the failure case this phase exists to measure.
>
> The sum is a **floor** on window occupancy, not the occupancy: it excludes parse and database time
> between calls, and it excludes the two out-of-seam call sites in section A. The report says "floor".

## C. There is no total request deadline, and the window therefore has no upper bound

`httpx.Client(timeout=30.0)` gives connect/read/write/pool 30s **each**, and httpx applies the read
timeout **per chunk**. A response delivering bytes slowly never trips it. There is no overall deadline
anywhere, and `max_retries=4` multiplies it.

This is not a percentile problem. **No p95 bounds it**, so the report must carry the unbounded term
explicitly rather than let the arithmetic in section B read as complete. Adding a total deadline is
the obvious fix and is a Forbidden path here — measure first, retune in Phase 39.

## D. A malformed body is never seen by the instrumented seam

`resp.json()` is called at `espn.py:335`, inside `_json_or_auth` — **outside `_request`**. So a
malformed body is not retried, and a recorder at `_request` records the attempt as a **successful
200**. Draft 2's offline malformed-body case, as written, would pass while proving nothing about how
that failure is counted.

The case must assert exactly that: the recorder's status count shows 200, and the classification
arrives from elsewhere or not at all. The same is true of every `_json_or_auth` failure.

## E. Three distinct behaviours share one label

`follow_redirects` is False, so a 302 returns, passes the `< 400` check at `:332`, reaches
`resp.json()` and becomes `EspnError("non-JSON response…")`. So does a `200` carrying
`Content-Type: text/html` — ESPN's known login-bounce shape. So does a genuinely truncated body. And
so does a 304, which is why draft 2's "304-if-supported" case asserts a classification the system
cannot produce.

A closed classification enum that does not separate these makes the error sample useless for the
purpose the phase exists for. The enum gains `redirect_not_followed` and `non_json_success`, the 304
case asserts the malformed-body path rather than a clean classification, and the report states that
these three aliased before this phase.

## F. The envelope is bounded in the wrong unit and cannot be bounded in code

Draft 2 bounds logical calls and HTTP attempts. The attempt ceiling is right; the wall clock is not
boundable without changing timeouts, which is forbidden. So the envelope becomes three ceilings the
operator approves **before** the run, rather than one derived from the run's own output:

1. **An absolute HTTP-attempt ceiling**, a number the operator sets. The `2 × 4 × (logical calls)`
   derivation is guidance for choosing it, not the bound itself — `completed_weeks` is not knowable
   beforehand (section B), so a derived equality cannot be the stop.
2. **A wall-clock ceiling in minutes**, after which the operator aborts manually. The contract states
   plainly that code cannot enforce this, and why.
3. **Manual abort instructions**, written out, because sections C and E mean a run can hang with no
   error.

And: clearing "the raw cache for that league only" does **not** clear `pro_schedule:{season}`. Either
that shape contributes zero network samples on the cold run — which must be stated — or the key is
cleared explicitly and stated. Draft 2's bound self-confirmed in the first branch and mis-fired in the
second.

## G. Reporting rules that were subtly wrong

- **The n-floor must not omit a row.** Suppressing a shape below the floor is the same defect rotated:
  an absent row reads as "not exercised", which is true for `players_url` (never called from sync) and
  false for a shape with n=3 because three of four attempts errored. Every shape is printed, with the
  count, and percentile cells filled with an explicit `n too small (n=3)` token.
- **`dropped_records` is not sufficient on its own.** Drops correlate with the pathological cases
  percentiles exist to capture, so a surviving p95 from a shape with drops is **biased, not merely
  incomplete**. The drop count appears in the same cell as every percentile derived from that shape.
- **The cache needs three verdicts, not two:** `HIT`, `MISS`, `BYPASS`. Five of six fetches pass
  `bust_cache=True` and never reach the consultation site at all, so they are neither hit nor miss.
  And `DBRawCache.get` returns `None` for both absent and stale, so **expired-vs-absent is not
  separable at the `espn.py` seam** — the report says so, and records that binding constraint 2 will
  need it, as a Phase 39 precondition.
- **`wait_ms` is the throttle and gate sleep only.** Connection setup happens inside
  `self._client.get` and belongs in `net_ms`; a `wait_ms` defined as "everything before the response
  object exists" would absorb DNS, TCP and TLS and invert the measurement for exactly the cold
  connections that matter. Backoff sleeps are a **third** field, `backoff_ms`, belonging to neither.

## H. The shape enum must cover every reachable URL family

Five families, not the three implied: `league_url` for season ≥ 2018 (`espn.py:134`), `league_url`
pre-2018 as `/leagueHistory/{league_id}` where the id is the **trailing** segment (`:136`),
`players_url` defaults (`:141`), `players_url` season (`:142`), and `season_url` (`:145`). A matcher
written against `/leagues/{id}` misses the pre-2018 form, and the natural fallback — recording the
unmatched path — records the league id.

The enum carries `UNKNOWN`, and the rule is written down: an unmatched URL yields `UNKNOWN` **and
discards the path entirely**. `espn_api_host` is operator-settable (`config.py:64`), so the reducer
must be host-independent.

## I. The declined-envelope branch

Draft 2's criterion 7 says "the live run, **if authorized**"; criteria 8, 9 and 10 carry no such
qualifier and all require live data. The envelope needs a separate approval that may not be given.

If the live envelope is declined or not exercised, the phase closes with work units 2 and 3 complete,
every criterion that does not need live data satisfied, and the network term explicitly **unmeasured**
— reported in those words, with the method for measuring it later. That is a legitimate outcome, not
a failure, and saying so in advance removes the incentive to reach for the live run.

## J. Corrections of record

- `_request` spans `espn.py:156-178`; `_get_authed`'s decoded-cookie attempt is `:316-324`; the
  `EspnError` carrying `resp.request.url` is `:333`, with the auth variant at `:331`. The six
  URL-bearing sites are `:171, :177, :326` (local `url`, path only) and `:331, :333, :337`
  (`resp.request.url`, full URL with query). Redaction written against one form misses the other.
- `max_retries=4` is four **total** attempts, not one plus four.
- `_get_authed`'s second attempt is conditional on `unquote(espn_s2) != espn_s2`; an unencoded cookie
  produces no second attempt.
- "Cooldown" appears in draft 2's criterion 2 and is defined nowhere; the code has `backoff` and
  `min_interval`. The term is replaced by those two.
- Draft 2's Lens cites a count of prior vacuous checks. The Phase 31 ledger's running count is what
  governs; the contract cites the ledger rather than restating a number.
- Criterion 11's operator-minute reforecast is program management inside a measurement gate. It stays
  in the phase's outcome and leaves the acceptance criteria.

## Acceptance

This amendment requires its own acceptance by exact SHA-256 before work unit 2 begins. It does not
authorize the live-read envelope, which is still approved separately at the time of the run.
