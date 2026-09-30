# Phase 40 (narrowed) — application sessions, and the deadlock RLS created

Source plan: Phase 40 `identity-session-frontend`, 6 sessions / 135 operator minutes. **Outcome:** add
Cognito authentication without outsourcing authorization, and serve the SPA/API as one deliberate
cookie origin.

**Status: the lock is built and proven. The front door is not, and is named rather than implied.**

## The narrowing, and the decision behind it

**Authentication belongs to hosted mode.** In `private_operator` there is one person on their own
machine; a login screen protects nothing and the single-tenant resolution is correct. In
`public_synthetic`, a request without a valid session gets nothing. This is not a device to keep the
suite green — it is what `app_mode` already exists for, and it matches how the project gates raw
cache writes and provider construction today.

Built: opaque application sessions, tenant derived from membership with default deny, and
`session_scope` made explicit. **Deferred and stated:** the Cognito/OIDC callback, PKCE, the SPA
work, CSP/HSTS headers, the delete/export UI, the re-link workflow. The phase's own acceptance
already defers the *live* callback to Phase 43; this additionally defers the fake-OIDC front door,
because the session and the membership check are what make phases 36–37b operate, and the front door
is replaceable without touching them.

## What the probe found first

| | |
| --- | --- |
| Application authentication in the codebase | **none** — every "cookie" is the ESPN provider credential |
| Rows in `users` / `memberships` | **0 / 0** — created by 0003, written by nothing |
| `get_session()` signature | **no arguments**, so it cannot see a request, so it cannot see a cookie |

That last row is the real architectural change this phase needs. Not the Cognito integration.

## The finding: row-level security locked out its own key

Logging in needs two reads — a session row to learn WHO, then a membership row to learn WHICH
TENANT. The second runs with no tenant bound, because the tenant is what it is looking for. Every
policy from 0005 compares against `current_tenant()`, NULL at that moment.

Measured on PostgreSQL 16 as `edge_app`, no tenant set:

```
app_sessions by token  -> [(1,)]   OK      (deliberately unpolicied)
memberships for user 1 -> []       EMPTY   <-- no tenant can ever be derived
users row for id 1     -> []       EMPTY
```

and the control: bind a tenant, and both reads succeed at once — which is precisely what the flow
cannot yet know. **The isolation proved 27/27 in Phase 37b was complete enough to make login
impossible.** No design review would have found this, because the design is not wrong. It is
finished.

## The fix, and the asymmetry that makes it safe

Migration `0007` adds `current_app_user()`, reading a second GUC (`app.user_id`) that is set *before*
the tenant is known. The READ policies on `users` and `memberships` admit "my own rows". The WRITE
policies do not change.

**That asymmetry is the whole design.** Reading your own membership is how you discover your tenant.
Writing one would let a user grant themselves membership of any tenant — the privilege escalation
Phase 37b's correction 2 closed by adding `WITH CHECK` in the first place. This is the one place in
the schema where `USING` and `WITH CHECK` must differ, and they differ deliberately.

Verified on the schema `alembic upgrade head` produces — not on hand-made tables — as a
`NOSUPERUSER NOBYPASSRLS` role. `docs/sprint-9/kernel/login_e2e.py`, **9/9**:

| | |
| --- | --- |
| user A logs in and lands in its tenant | user=1 tenant=1 |
| sees only its own tenant's leagues | `[(1, 1)]` |
| tenant 2's league invisible to A | 0 rows |
| **user B lands in a DIFFERENT tenant** | user=2 tenant=2 |
| and sees only tenant 2's leagues | `[(2, 2)]` |
| unknown token yields no user | — |
| revoked token yields no user | — |
| B cannot grant itself tenant 1 | `42501` |
| CONTROL: B can still write in its own tenant | accepted |

Rows four and five are the point of the last four phases: **two tenants served at once, each seeing
only their own.** Until this revision that was impossible — `resolve_tenant_id` refused outright when
a second tenant existed.

## What was built

`api/auth.py` — mint, verify, revoke, revoke-all. Tokens are 256 bits of randomness stored only as
SHA-256, so a stolen database yields nothing usable; the raw value exists in the minting response and
the caller's cookie and nowhere else. Expiry and revocation are separate columns, because expiry is a
fact about time and revocation is a decision someone made, and collapsing them loses the first
question anyone asks after an incident. Every way a session can be dead returns the same message to
the caller — telling someone "expired" rather than "unknown" confirms their token was once real.

`api/db.py` — `get_session(request)` derives the tenant from the caller's membership in hosted mode
and maps a rejected session to 401. `session_scope(tenant_id=...)` is explicit: background work has
no caller, and in hosted mode omitting the tenant raises rather than guessing. A job that quietly
picks a tenant is the background-job attack the Phase 36 contract names.

Migrations `0006` (app_sessions) and `0007` (login read policies), both reversible, both no-ops on
SQLite where there is no RLS to amend. The parked contract renumbered to `0008`.

## The Phase 35 trap, hit again, and a claim of mine it disproved

Every datetime column here is `DateTime(timezone=True)`, but SQLite has no type to carry an offset,
so a stored expiry comes back naive and comparing it to `datetime.now(UTC)` raises. `_as_utc` handles
it; removing it fails **5 of 10** tests in the auth suite.

I wrote in a docstring that this "never shows up in a test that wrote the row moments earlier,
because the identity map hands back the same aware object". **The very next test disproved that.** A
flush expires the instance and the next SELECT reloads it naive — so any flush is enough, not just a
fresh process. Corrected in place.

One false alarm chased to ground: `spend.py` compares a stored timestamp to a cutoff in SQL, which
looked like the same trap. Measured — SQLAlchemy's SQLite bind processor normalises both sides and
the filter is correct. No finding, recorded because "we checked and it was fine" is worth as much as
a defect when the next person wonders.

## Still not true

- **No front door.** Nothing calls `mint_session` yet. The OIDC callback, PKCE and state/nonce
  handling are unbuilt.
- **Grants.** `app_sessions` is new, and like every table has no grants for the runtime role until an
  operator issues them. A table the app cannot SELECT is a login that always fails.
- **One membership per user.** `tenant_for_user` refuses when a user belongs to two tenants, because
  there is no tenant-selection step. Serving the lower id would be a cross-tenant read that looks
  like success.
- **The contract migration is still parked** at `0008`, so the colliding-tenant case remains
  unstageable at head — the e2e above uses distinct ESPN ids and says so.
- **No CSRF, no security headers, no SPA origin work.**

## Suite

**871 passed / 0 failed**, ruff clean. `test_recovery*` unchanged at its pre-existing 89
environmental failures.

---

# Addendum — hosted-mode default deny

## The front door is blocked on a dependency, not on effort

Verifying an OIDC ID token needs a JWT library. This environment has no `pip`
in its virtualenv and PyJWT cannot be installed. **Hand-rolling the
verification was not an option** -- `alg: none` and key-confusion bugs live
exactly there, and writing one for a practice project is the wrong risk to
take. So `api/routers/auth.py` ships with no session-minting endpoint at all,
and its docstring says why rather than implying a callback exists.

`mint_session` stays internal. The two routes that do ship exercise everything
around it.

## What is proven instead

Phase 40's acceptance asks that routes default-deny. Half of that is provable
offline and now is: **in hosted mode, without a valid session cookie, nothing
answers.** The refusal happens in the dependency, before any handler runs.

`tests/test_hosted_default_deny.py` -- 76 tests over every GET route that takes
a tenant-bound session:

| | |
| --- | --- |
| no cookie, hosted mode | **401** on all 37 routes |
| forged cookie, hosted mode | **401** on all 37 routes |
| a real session | 200, and `/api/auth/me` reports the right user |
| logout | the same token stops working |
| **control:** private-operator mode, no cookie | **200** |

That last row matters: without it, the 401s above would be equally consistent
with "hosted mode broke every route".

**Control removed** -- force `hosted = False` in `_bound_session` and **74 of
76 fail**, each naming the route that answered 200 to an unauthenticated
caller.

The other half of the acceptance -- that an authenticated caller sees only
their own tenant's rows -- is row-level security's job, is PostgreSQL-only,
and was measured in `login_e2e.py`. Asserting it here on SQLite would be a
green tick establishing nothing.

## Suite

**947 passed / 0 failed**, ruff clean.

---

# Addendum 2 — CSRF, response headers, and a guard that was inert

`api/security.py`: double-submit CSRF and response security headers, both
hosted-mode only. In private-operator mode there is no browser session to ride
on and no cross-origin attacker, so either check would add a failure mode and
remove no risk.

**The mechanism is an asymmetry.** An attacker's page can make a browser
*send* our cookies to us; it cannot *read* them to forge the matching header,
because it is on another origin. So the session cookie is `HttpOnly` -- Phase
40's explicit guarantee that JavaScript cannot read it -- and the CSRF cookie
deliberately is not, because our own page must read it. Both halves are
pinned by test.

Cookie flags are set in one place (`set_session_cookies`) so the front door,
whenever it lands, cannot get them wrong. `clear_session_cookies` mirrors them
exactly: a browser will not replace a cookie whose attributes differ, so a
delete that forgets `path` leaves it in place and sign-out silently does
nothing.

## Two defects found in my own work, both by a test that should have failed

**1. The CSRF middleware was inert, and the hosted tests passed anyway.**
`api/security.py` did `from .config import get_settings`, binding the function
at import. The hosted-mode tests patch `api.config.get_settings`, which never
reaches a name bound that way -- so the middleware read the real mode
(`private_operator`), skipped every check, and `test_logout_...` passed
without sending a token.

This is *precisely* the defect recorded in `tests/test_hosted_mode.py`'s
`_mode` docstring, about `api/services/espn.py`, written weeks earlier in this
same repository. I reproduced it. Measured it directly -- patch
`api.config.get_settings`, then observe `security.get_settings()` still
returning a real `Settings` -- then fixed it by reading through the module
(`config.get_settings()`), and added the case that catches it.

**2. The middleware order was backwards.** I wrote a comment asserting that
Starlette runs middleware in reverse registration order and that registering
the headers wrapper first makes it outermost. The opposite is true: each
registration is prepended, so the *last* added is outermost. The CSRF check
was outermost and its 403 short-circuited before any header was stamped --
and a 403 is a response an attacker's page can see.
`test_the_headers_reach_a_refusal_too` caught it, which is the only reason I
know the comment was wrong rather than merely unverified.

## Evidence

`tests/test_csrf_and_headers.py` -- 21 tests. Unsafe methods refused without a
token and with a mismatched one; a matching token reaching 401 rather than 403
(CSRF passed, the session check then refused for its own unrelated reason, and
distinguishing those is the point); safe methods ungated; private-operator
ungated; four headers present; no `unsafe-inline` or `unsafe-eval`; HSTS
hosted-only, because sending it from a local HTTP origin pins a developer's
browser to HTTPS for a host that does not serve it.

**Controls removed:** disable the CSRF check and **9 of 21** fail. Put
`unsafe-inline` back in the CSP and the one test that exists for it fails.

## Grants

`docs/sprint-9/kernel/rls.sql` now grants `app_sessions` to the runtime role.
It is read before any tenant is known, so no policy covers it, and a table the
app cannot SELECT is a login that always fails. `DELETE` is withheld:
revocation sets `revoked_at` rather than removing the row, which is what lets
you say afterwards when each session was cut off. `DELETE` on the spend ledger
is withheld for the same reason Phase 37b recorded -- that one is a grant, not
a policy.

## Suite

**969 passed / 0 failed**, ruff clean.
