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
