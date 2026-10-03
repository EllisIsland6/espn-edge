"""The durable job queue: enqueue, claim, finish.

Every transition is a conditional UPDATE whose row count is the answer. That
is the whole design. Read-then-write loses races silently -- two workers both
see `queued`, both write `leased`, both run the job -- whereas
`UPDATE ... WHERE state = 'queued'` lets exactly one win and tells the loser
it lost.

Time is injected. A queue's hard cases are all about time: a lease that
expired, a cooldown that has not, a job scheduled for later. Tests that have
to sleep are slow and flaky, and tests that cannot control time do not test
those cases at all.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from ..models import Job

QUEUED = "queued"
LEASED = "leased"
DONE = "done"
FAILED = "failed"
POISON = "poison"

#: How long a claim is good for. A worker that dies leaves a lease that
#: expires; nothing has to notice the death.
DEFAULT_LEASE = timedelta(minutes=5)

#: Retry backoff, indexed by attempt. Deliberately a table rather than a
#: formula: the values are a policy decision someone should be able to read
#: and change without reasoning about exponents.
BACKOFF = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))

#: `last_error` is a database column, and the thing that fails is often a
#: provider response. Truncating here rather than trusting callers is the
#: difference between a diagnostic and an accidental payload store.
MAX_ERROR = 300


# Every ORM-enabled UPDATE below carries `synchronize_session="fetch"`, and
# that is a correctness fix rather than a tuning knob.
#
# SQLAlchemy 2.0 defaults to "auto", which tries the "evaluate" strategy: it
# re-checks the UPDATE's WHERE criteria **in Python** against objects already
# in the identity map, to decide which ones to expire. Those objects carry the
# timestamp as SQLite returned it -- naive, because SQLite has no type that
# carries an offset, even though the column is `DateTime(timezone=True)`. The
# bound `now` is aware. So `lease_expires_at <= now` raises `TypeError` inside
# the ORM, on the lease-expiry path only, and only once an object happened to
# be loaded.
#
# "fetch" issues a SELECT for the affected primary keys and expires those,
# comparing nothing in Python. Measured: the two lease-expiry tests raised
# before this and pass after. Same Phase 35 trap, surfacing through a
# mechanism I did not write.


def _utc(value: datetime) -> datetime:
    """Normalise a stored timestamp to aware UTC.

    SQLite has no type that carries an offset, so a value read back from disk
    is naive even though the column is `DateTime(timezone=True)`. Sorting a
    mix of naive and aware datetimes raises -- the Phase 35 trap, which has
    now surfaced in four places in this codebase, including inside SQLAlchemy.
    Every Python-side comparison of a stored timestamp goes through here.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    *,
    kind: str,
    idempotency_key: str,
    tenant_id: int | None = None,
    payload: dict | None = None,
    max_attempts: int = 3,
    available_at: datetime | None = None,
    clock: Clock = _now,
) -> Job:
    """Queue a job, or return the one already queued for this key.

    Idempotent by (tenant, key), and the uniqueness is enforced by the
    database rather than by looking first. A check-then-insert has a window:
    two requests both find nothing and both insert, and the second one's
    failure is an error the user sees for a request that was actually a
    duplicate. Here the second one catches the violation and returns the
    existing row, which is what the caller wanted either way.
    """
    now = clock()
    existing = session.execute(
        select(Job).where(Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    job = Job(
        tenant_id=tenant_id,
        kind=kind,
        idempotency_key=idempotency_key,
        payload_json=payload,
        state=QUEUED,
        attempts=0,
        max_attempts=max_attempts,
        available_at=available_at or now,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        # Lost the race. The row the winner inserted is the answer.
        session.rollback()
        return session.execute(
            select(Job).where(
                Job.tenant_id == tenant_id, Job.idempotency_key == idempotency_key
            )
        ).scalar_one()
    return job


#: Kinds that reach ESPN. The provider rate-limits the whole deployment, not
#: each process, so the queue has to as well: Phase 39's guarantee is that
#: "increasing workers never raises provider rps", and the only way to make
#: that structurally true is to let at most one provider-touching job hold a
#: lease at a time, globally. Adding a worker then buys throughput for
#: everything else and none for the provider, which is the intended shape.
PROVIDER_KINDS = frozenset({"sync", "discover", "reauth"})

#: States meaning "this tenant was served recently". Fairness ordering only --
#: nothing about correctness depends on them.
_SERVED_STATES = (LEASED, DONE, FAILED, POISON)


def _runnable(now: datetime):
    """Queued and due, or leased with an expired lease.

    The second half is how a crashed worker's job comes back without anything
    detecting the crash.
    """
    return or_(
        and_(Job.state == QUEUED, Job.available_at <= now),
        and_(Job.state == LEASED, Job.lease_expires_at <= now),
    )


def _held(now: datetime):
    """A lease somebody still holds."""
    return and_(Job.state == LEASED, Job.lease_expires_at > now)


def _eligible_candidates(session: Session, now: datetime) -> list:
    """Runnable rows, in the order they should be offered.

    Three rules, each from a specific failure:

    **One active job per tenant.** Without it a tenant with two hundred queued
    leagues occupies every worker and everyone else waits. Cruder than a
    weighted share, and it cannot be gamed by enqueueing more.

    **One provider-touching job globally.** See `PROVIDER_KINDS`.

    **Least-recently-served tenant first.** Fairness, not correctness: it stops
    a tenant that enqueues constantly from permanently sitting at the head of
    the due-time order. A tenant nobody has served has no served rows at all
    and so sorts first, which is what a newcomer should get.

    Ordering happens in Python on purpose. It reads clearly, the queue is
    small, and -- the part that matters -- **this is not where safety lives**.
    `claim` re-checks every one of these conditions in its UPDATE, so a
    candidate that goes stale between here and there is rejected by the
    database rather than by this list having been right.
    """
    runnable = session.execute(
        select(Job.id, Job.tenant_id, Job.kind, Job.available_at)
        .where(_runnable(now))
        .order_by(Job.available_at, Job.id)
    ).all()
    if not runnable:
        return []

    busy_tenants = set(
        session.execute(select(Job.tenant_id).where(_held(now))).scalars().all()
    )
    provider_busy = (
        session.execute(
            select(Job.id).where(_held(now), Job.kind.in_(PROVIDER_KINDS)).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    last_served = dict(
        session.execute(
            select(Job.tenant_id, func.max(Job.updated_at))
            .where(Job.state.in_(_SERVED_STATES))
            .group_by(Job.tenant_id)
        ).all()
    )

    eligible = [
        row
        for row in runnable
        if row.tenant_id not in busy_tenants
        and not (provider_busy and row.kind in PROVIDER_KINDS)
    ]

    def fairness_key(row):
        served = last_served.get(row.tenant_id)
        return (
            served is not None,
            _utc(served) if served is not None else now,
            _utc(row.available_at),
            row.id,
        )

    eligible.sort(key=fairness_key)
    return eligible


def claim(
    session: Session,
    *,
    owner: str,
    lease: timedelta = DEFAULT_LEASE,
    clock: Clock = _now,
) -> Job | None:
    """Take the next runnable job, or None.

    Runnable means queued and due, OR leased with an expired lease -- the
    second half is how a crashed worker's job comes back without anything
    detecting the crash.

    The claim is a conditional UPDATE and its row count decides. Two workers
    racing for the same row: one UPDATE matches, the other matches zero rows
    and moves on. A `SELECT` followed by an `UPDATE` would let both through,
    and the symptom would be a job that ran twice -- which for a sync means
    two provider calls against a rate limit that allows one.
    """
    now = clock()
    for row in _eligible_candidates(session, now):
        other = aliased(Job)
        guards = [
            Job.id == row.id,
            _runnable(now),
            # No other live lease for this tenant. Re-checked here rather than
            # trusted from the list: another worker may have taken one for the
            # same tenant in between.
            ~select(other.id)
            .where(
                other.id != row.id,
                other.state == LEASED,
                other.lease_expires_at > now,
                other.tenant_id == row.tenant_id,
            )
            .exists(),
        ]
        if row.kind in PROVIDER_KINDS:
            # And nobody else holds the provider permit.
            guards.append(
                ~select(other.id)
                .where(
                    other.id != row.id,
                    other.state == LEASED,
                    other.lease_expires_at > now,
                    other.kind.in_(PROVIDER_KINDS),
                )
                .exists()
            )

        claimed = session.execute(
            update(Job)
            .where(*guards)
            .values(
                state=LEASED,
                lease_owner=owner,
                lease_expires_at=now + lease,
                attempts=Job.attempts + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session="fetch")
        ).rowcount
        if claimed:
            session.flush()
            return session.get(Job, row.id)
        # Lost it between the list and the update. Try the next candidate
        # rather than returning None, or a busy queue reports itself empty.
    return None


def complete(session: Session, job_id: int, *, owner: str, clock: Clock = _now) -> bool:
    """Mark a leased job done. False if the lease was no longer ours.

    Takes the owner explicitly rather than reading it off a `Job` object, and
    that is not style. The first version took the object and read
    `job.lease_owner` -- which the ORM had already refreshed to whichever
    worker reclaimed the job, so a stalled worker's write matched its own
    guard and succeeded. The test for exactly that case caught it.

    A worker knows its own identity. Anything it reads back from a shared row
    is a fact about the world, not about itself.
    """
    now = clock()
    ok = session.execute(
        update(Job)
        .where(Job.id == job_id, Job.state == LEASED, Job.lease_owner == owner)
        .values(state=DONE, lease_owner=None, lease_expires_at=None, updated_at=now)
        .execution_options(synchronize_session="fetch")
    ).rowcount
    session.flush()
    return bool(ok)


def fail(
    session: Session,
    job_id: int,
    *,
    owner: str,
    error: str,
    retryable: bool = True,
    clock: Clock = _now,
) -> str | None:
    """Record a failure and return the state the job ended in, or None if the
    lease was no longer ours.

    The attempt count is re-read from the row inside the transaction rather
    than taken from a caller's object, for the same reason `complete` takes an
    explicit owner: a stale `attempts` would decide retry-versus-poison from
    an out-of-date number.

    Three outcomes, and which applies is not the worker's choice:

    - `failed` immediately when the error is not retryable. An expired
      credential does not get better by being tried again; retrying it burns
      the attempt budget and delays the human who has to fix it.
    - `poison` when the attempt budget is spent. Quarantined rather than
      deleted, because the row is the only evidence of what happened.
    - `queued` with a cooldown otherwise.
    """
    now = clock()
    row = session.execute(
        select(Job.attempts, Job.max_attempts, Job.available_at).where(
            Job.id == job_id, Job.state == LEASED, Job.lease_owner == owner
        )
    ).one_or_none()
    if row is None:
        return None
    attempts, max_attempts, available_at = row

    if not retryable:
        state, next_at = FAILED, available_at
    elif attempts >= max_attempts:
        state, next_at = POISON, available_at
    else:
        state = QUEUED
        next_at = now + BACKOFF[min(max(attempts - 1, 0), len(BACKOFF) - 1)]

    session.execute(
        update(Job)
        .where(Job.id == job_id, Job.state == LEASED, Job.lease_owner == owner)
        .values(
            state=state,
            available_at=next_at,
            lease_owner=None,
            lease_expires_at=None,
            last_error=error[:MAX_ERROR],
            updated_at=now,
        )
        .execution_options(synchronize_session="fetch")
    )
    session.flush()
    return state


def oldest_due_age(session: Session, *, clock: Clock = _now) -> timedelta:
    """How long the oldest job that is due has been waiting.

    Moved here from `schedules.py`, where it had been living while querying
    this module's table -- which is why there was no measure of schedule
    lateness at all. See `schedules.oldest_overdue_age`.

    Queue *age*, not queue depth, because depth says nothing about whether
    anything is wrong: a thousand jobs being worked through quickly is
    healthy, and one job stuck for an hour is not. This is the number a
    backpressure rule should read, and the one an alarm should watch.
    """
    now = clock()
    oldest = session.execute(
        select(Job.available_at)
        .where(Job.state == QUEUED, Job.available_at <= now)
        .order_by(Job.available_at)
        .limit(1)
    ).scalar_one_or_none()
    if oldest is None:
        return timedelta(0)
    return now - _utc(oldest)


def count_by_state(session: Session, state: str) -> int:
    """How many jobs are in one state.

    Added for the poison gauge. Takes the state as a value rather than offering
    one function per state, because the states are a closed set declared at the
    top of this module and a caller that passes something else should get zero
    rather than an attribute error -- the number is for a graph, and a reporter
    that raises on a typo takes the whole report down with it.
    """
    return len(
        session.execute(select(Job.id).where(Job.state == state)).scalars().all()
    )


def queue_depth(session: Session, *, clock: Clock = _now) -> int:
    """How many jobs are due now.

    Due, not merely queued: a job with a cooldown still ahead of it is not
    work anyone is waiting on, and counting it would make every retry look
    like a backlog. This is the number an operator reads and the one a
    backpressure rule would act on.
    """
    return len(
        session.execute(
            select(Job.id).where(Job.state == QUEUED, Job.available_at <= clock())
        )
        .scalars()
        .all()
    )
