<!-- PHASE32-REPORT-BEGIN -->

# phase 32 provider behaviour report

generated offline from recorded attempts. espn service traffic only.
no provider network call was made. the espn network term is unmeasured.

## context

| field | value |
| --- | --- |
| season | 2026 |
| week | 3 |
| leagues | 1 |
| attempts | 21 |
| filed | 21 |
| dropped | 0 |
| overflowed | 0 |

## shapes

| shape | label | n | net p50 | net p95 | gate p50 | gate p95 | throttle p50 | throttle p95 | backoff p50 | backoff p95 | wire p50 | decoded p50 | dropped | overflowed | censored |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| league_modern | observed | 17 | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 8.00 s | 64 KiB | 64 KiB | 0 | 0 | 0 |
| league_history | n too small | 3 | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 64 KiB | 64 KiB | 0 | 0 | 0 |
| players_defaults | no call site | 0 | - | - | - | - | - | - | - | - | absent | absent | 0 | 0 | 0 |
| players_season | no call site | 0 | - | - | - | - | - | - | - | - | absent | absent | 0 | 0 | 0 |
| season | n too small | 1 | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 0.00 s | 64 KiB | 64 KiB | 0 | 0 | 0 |

## outcomes

| shape | outcome | count |
| --- | --- | --- |
| league_modern | exhausted | 2 |
| league_modern | ok | 9 |
| league_modern | retryable_status | 3 |
| league_modern | transport_error | 3 |
| league_history | ok | 3 |
| season | ok | 1 |

## statuses

| shape | status | count |
| --- | --- | --- |
| league_modern | 0 | 4 |
| league_modern | 200 | 9 |
| league_modern | 500 | 4 |
| league_history | 200 | 3 |
| season | 200 | 1 |

## cache

| shape | verdict | count |
| --- | --- | --- |
| league_history | bypass | 3 |
| league_modern | bypass | 10 |
| league_modern | miss | 1 |
| season | bypass | 1 |
| season | hit | 1 |

## rates

| rate | value |
| --- | --- |
| error rate over recorded attempts | 8/21 observed |
| attempts not classified | 0 |
| cache hit rate where decided | 1/16 observed |
| status 304 | 0/21 observed; 95 pct upper bound near 3/21 |
| players defaults | not measured - no call site |
| players season | not measured - no call site |

## structural zeros

| field | reason |
| --- | --- |
| gate ms | no call site - the rate gate is not called by this phase |
| status 304 | dead by design until conditional requests exist |
| cache hit at request boundary | a hit never reaches the request seam |
| players defaults | no caller under api |
| players season | builder never invoked |

## limits

| claim | limit |
| --- | --- |
| byte figures | bucketed to 64 kib; bounds precision not linkability |
| durations | 50 ms grid under a 30 s ceiling; a value below it publishes |
| identifier width | no lower bound is established by this phase |
| espn network term | unmeasured; every input is a fixture or loopback |
| occupancy | a model, not an observation |
| scope | espn service traffic only; discovery and cross check are ungated |

<!-- PHASE32-REPORT-END -->
