"""The worker loop: claim across tenants, then run inside one.

This is where the queue work and the tenancy work actually meet, and the
meeting point is one rule:

    **The worker claims across tenants. The handler runs inside one.**

Claiming has to see every tenant's queue -- one process serves everybody, which
is why `jobs` carries no row-level security policy. Running must see exactly
one tenant, because the handler reads and writes real data. So the two happen
on two different sessions, and the handler's is opened through
`session_scope(tenant_id=...)` -- which is precisely why that function was made
to take an explicit tenant rather than falling back to "the only one".

A job with no tenant is refused rather than run. Under row-level security it
would see nothing, so it would "succeed" having done nothing, and the queue
would record that as done. Failing closed and loudly is the only honest
option.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from ..db import SessionLocal, session_scope
from .jobs import DEFAULT_LEASE, Clock, _now, claim, complete, fail

#: kind -> handler. A handler takes a session already bound to the job's
#: tenant, plus the job's payload. It is deliberately not given the `Job` row:
#: a handler that could edit its own queue record would be able to extend its
#: own lease or mark itself done, and neither is its business.
Handler = Callable[[Session, dict | None], None]
HANDLERS: dict[str, Handler] = {}


class NonRetryable(Exception):
    """Raise from a handler when retrying cannot help.

    An expired credential, a payload that will never parse, a league that no
    longer exists. Retrying these burns the attempt budget and delays the
    person who has to fix the cause.
    """


class TenantlessJob(RuntimeError):
    """A job with no tenant reached the worker.

    Not a handler error -- a queue invariant broken upstream. Reported rather
    than guessed at, because the alternative is running the handler unbound
    and having it quietly see nothing.
    """


def register(kind: str) -> Callable[[Handler], Handler]:
    """Register a handler for a job kind.

    Refuses to overwrite. Two handlers claiming the same kind is a wiring
    mistake, and the one that happens to be imported second would silently
    win -- which is the kind of defect that only shows up as "the wrong thing
    ran" months later.
    """

    def decorate(handler: Handler) -> Handler:
        if kind in HANDLERS and HANDLERS[kind] is not handler:
            raise ValueError(f"a handler for {kind!r} is already registered")
        HANDLERS[kind] = handler
        return handler

    return decorate


def run_once(
    *,
    owner: str,
    lease: timedelta = DEFAULT_LEASE,
    clock: Clock = _now,
) -> str | None:
    """Claim one job, run it, record the outcome. Returns the final state.

    None means there was nothing to do.

    The claim is committed before the handler runs. That ordering is the crash
    contract: if this process dies mid-handler, the lease is already durable,
    so the job is reclaimable when it expires rather than invisible. The
    alternative -- one transaction around claim and run -- loses the lease on
    rollback and the job looks untouched, which is indistinguishable from
    never having been claimed and makes a poison job immortal.
    """
    with SessionLocal() as claiming:
        job = claim(claiming, owner=owner, lease=lease, clock=clock)
        if job is None:
            return None
        job_id, kind, tenant_id, payload = (
            job.id,
            job.kind,
            job.tenant_id,
            job.payload_json,
        )
        claiming.commit()

    error: str | None = None
    retryable = True
    try:
        if tenant_id is None:
            raise TenantlessJob(
                f"job {job_id} ({kind}) has no tenant. Running it unbound would "
                "read nothing under row-level security and report success."
            )
        handler = HANDLERS.get(kind)
        if handler is None:
            raise NonRetryable(f"no handler registered for kind {kind!r}")

        with session_scope(tenant_id=tenant_id) as working:
            handler(working, payload)
    except NonRetryable as exc:
        error, retryable = str(exc), False
    except TenantlessJob as exc:
        # Not retryable: the tenant will not appear by waiting.
        error, retryable = str(exc), False
    except Exception as exc:  # noqa: BLE001 - the worker must survive handlers
        error, retryable = f"{type(exc).__name__}: {exc}", True

    with SessionLocal() as finishing:
        if error is None:
            complete(finishing, job_id, owner=owner, clock=clock)
            finishing.commit()
            return "done"
        state = fail(
            finishing, job_id, owner=owner, error=error, retryable=retryable, clock=clock
        )
        finishing.commit()
        return state


def drain(*, owner: str, limit: int = 100, clock: Clock = _now) -> list[str]:
    """Run jobs until there are none due, or `limit` is reached.

    The limit is not politeness -- it is the stop condition. Without it a
    handler that re-enqueues its own kind turns one tick into an unbounded
    loop, and the symptom is a worker that never reports idle.
    """
    outcomes: list[str] = []
    for _ in range(limit):
        state = run_once(owner=owner, clock=clock)
        if state is None:
            break
        outcomes.append(state)
    return outcomes
