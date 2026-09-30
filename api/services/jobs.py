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

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


def _next_candidate_id(session: Session, now: datetime) -> int | None:
    """The id of the next runnable job, or None.

    A separate function so a test can interpose between choosing a candidate
    and claiming it -- which is the only window the conditional UPDATE in
    `claim` exists to close. Without that seam the guard is unobservable in a
    single process: the SELECT below already filters by state, so a second
    sequential `claim` finds nothing and returns before the UPDATE runs. The
    control-removal check for that guard passed with it deleted, which is how
    the gap was found.
    """
    return session.execute(
        select(Job.id)
        .where(
            or_(
                and_(Job.state == QUEUED, Job.available_at <= now),
                and_(Job.state == LEASED, Job.lease_expires_at <= now),
            )
        )
        .order_by(Job.available_at, Job.id)
        .limit(1)
    ).scalar_one_or_none()


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
    while True:
        candidate = _next_candidate_id(session, now)
        if candidate is None:
            return None

        claimed = session.execute(
            update(Job)
            .where(
                Job.id == candidate,
                or_(
                    and_(Job.state == QUEUED, Job.available_at <= now),
                    and_(Job.state == LEASED, Job.lease_expires_at <= now),
                ),
            )
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
            return session.get(Job, candidate)
        # Somebody else took it between the select and the update. Look again
        # rather than returning None, or a busy queue would report empty.


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
