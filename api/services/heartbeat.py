"""Worker liveness: the row that says "I woke up and did nothing".

THE SIGNAL THIS CREATES
-----------------------
`record_tick` is called on every pass of the worker loop, including the passes
that claim nothing. That is the only unusual thing about this module, and it is
the whole value of it: before it, an idle worker and a dead worker wrote the
same number of rows (zero), so the two were indistinguishable from outside the
host.

Two timestamps carry two different facts:

* `last_seen_at` -- the loop ran.
* `last_claimed_at` -- the loop got work.

Fresh first, stale second, is the live-but-not-claiming case. It is the failure
most likely to survive a deploy unnoticed, because the process is up, the logs
are quiet, and every "is it running" check says yes.

WHY THE COUNTERS CARRY A WATERMARK
----------------------------------
`ticks`/`claims`/`failures` accumulate; the published metrics are deltas. The
obvious implementation holds the previous total in a module variable, which
works until the process restarts -- and a restart is exactly the event these
metrics exist to reveal. So the watermark is a column and `take_progress`
advances it in one conditional UPDATE, which also means two reporters cannot
report the same ticks twice.

WHAT A MISSING ROW MEANS, AND WHY IT IS NOT ZERO
------------------------------------------------
`staleness` returns `None` when no worker has ever checked in, rather than
`timedelta(0)`. Zero would mean "seen just now", so an empty table would
publish perfect health -- the same INSUFFICIENT_DATA-reads-as-OK mistake that
this phase exists to remove, moved one layer down where no alarm setting could
catch it. The caller must decide what `None` publishes, and
`snapshot.py` maps it above every threshold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import WorkerHeartbeat
from .jobs import Clock, _now


def _utc(value: datetime) -> datetime:
    """Re-attach UTC to a value a driver handed back naive.

    Phase 35, for the seventh time: SQLite has no type that carries an offset,
    so a column written aware reads back naive and any arithmetic against
    `datetime.now(UTC)` raises. Every freshness number in this file goes
    through here.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def record_tick(
    session: Session,
    owner: str,
    *,
    claimed: int = 0,
    failed: int = 0,
    clock: Clock = _now,
) -> WorkerHeartbeat:
    """Record one pass of the worker loop.

    Call this even when `claimed` is 0. Especially then -- a heartbeat that is
    only written when work happened is not a heartbeat, it is a second copy of
    the work counter, and it goes silent at the exact moment somebody needs it.

    The update is UPDATE-then-INSERT rather than INSERT-then-catch, because the
    steady state is "row exists" and the first path should be the common one.
    The IntegrityError fallback covers two workers with the same owner string
    starting at once, which is a misconfiguration rather than a design, but one
    that should not crash a loop.
    """
    if claimed < 0 or failed < 0:
        raise ValueError("claimed and failed are counts, not adjustments")
    now = clock()

    values: dict = {
        "last_seen_at": now,
        # Incremented in SQL, not read-modify-write: a second thread in the
        # same process would otherwise lose a tick, and the loss would look
        # like a slow worker rather than a bug.
        "ticks": WorkerHeartbeat.ticks + 1,
        "claims": WorkerHeartbeat.claims + claimed,
        "failures": WorkerHeartbeat.failures + failed,
    }
    if claimed:
        # Only on a tick that actually claimed. Advancing this unconditionally
        # is the single change that would destroy the live-but-not-claiming
        # signal, which is why `tests/test_heartbeat.py` removes exactly that
        # condition and requires a failure.
        values["last_claimed_at"] = now

    result = session.execute(
        update(WorkerHeartbeat)
        .where(WorkerHeartbeat.owner == owner)
        .values(**values)
        .execution_options(synchronize_session="fetch")
    )
    if result.rowcount == 0:
        row = WorkerHeartbeat(
            owner=owner,
            first_seen_at=now,
            last_seen_at=now,
            last_claimed_at=now if claimed else None,
            ticks=1,
            claims=claimed,
            failures=failed,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return record_tick(
                session, owner, claimed=claimed, failed=failed, clock=clock
            )
        return row

    session.flush()
    return session.execute(
        select(WorkerHeartbeat).where(WorkerHeartbeat.owner == owner)
    ).scalar_one()


def staleness(session: Session, *, clock: Clock = _now) -> timedelta | None:
    """Age of the newest heartbeat across every worker, or None if there are none.

    *Newest*, not oldest: one worker of three dying should not make the fleet
    look dead, and one worker of three surviving should not make it look
    healthy either -- that second case is what `idle_age` is for. This number
    answers only "is anybody running".

    `None` rather than `timedelta(0)` when the table is empty. See the module
    docstring: zero here would publish health from absence.
    """
    newest = session.execute(
        select(WorkerHeartbeat.last_seen_at)
        .order_by(WorkerHeartbeat.last_seen_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return None if newest is None else clock() - _utc(newest)


def idle_age(session: Session, *, clock: Clock = _now) -> timedelta | None:
    """How long since any worker last claimed anything, or None if never.

    Read beside `queue_depth`. Long idle with an empty queue is a quiet
    weekend; long idle with a deep queue is the live-but-not-claiming failure
    and somebody should be woken for it.
    """
    newest = session.execute(
        select(WorkerHeartbeat.last_claimed_at)
        .where(WorkerHeartbeat.last_claimed_at.is_not(None))
        .order_by(WorkerHeartbeat.last_claimed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return None if newest is None else clock() - _utc(newest)


def take_progress(
    session: Session, *, clock: Clock = _now
) -> tuple[int, int, int]:
    """Deltas since the last call: (ticks, claims, failures). Advances the watermark.

    Summed across workers, because the metric has no `owner` dimension -- a
    hostname is unbounded under autoscaling and `observability.py` refuses it.
    Per-worker numbers are a query against this table, which costs a query
    rather than a subscription.

    Each row is advanced by a conditional UPDATE that writes back the totals it
    *read*, never the current ones, and that one detail gives two different
    guarantees:

    * **A tick arriving mid-window is not lost.** The watermark moves only as
      far as the read, so the late tick is still ahead of it and arrives in
      the next window. Across windows the reported total equals the real total
      -- `test_ticks_are_conserved_across_windows` asserts exactly that, by
      summing, rather than asserting a particular split between the two
      windows, which is a scheduling detail.
    * **Two reporters cannot report the same ticks twice.** The `WHERE
      reported_ticks = <read value>` guard fails for whichever one writes
      second, so its rowcount is 0 and it reports nothing instead of
      double-counting. A double count on `worker_ticks` would make a stopped
      worker look busy, which is the failure this whole module exists to make
      impossible.

    An earlier docstring here claimed the first bullet was the guard's doing.
    It is not; the write-back-what-you-read is. Keeping the two straight
    matters because only one of them survives changing the WHERE clause.
    """
    rows = session.execute(
        select(
            WorkerHeartbeat.owner,
            WorkerHeartbeat.ticks,
            WorkerHeartbeat.claims,
            WorkerHeartbeat.failures,
            WorkerHeartbeat.reported_ticks,
            WorkerHeartbeat.reported_claims,
            WorkerHeartbeat.reported_failures,
        )
    ).all()

    now = clock()
    d_ticks = d_claims = d_failures = 0
    for owner, ticks, claims, failures, r_ticks, r_claims, r_failures in rows:
        advanced = session.execute(
            update(WorkerHeartbeat)
            .where(
                WorkerHeartbeat.owner == owner,
                # The guard: only advance from the watermark we actually read.
                WorkerHeartbeat.reported_ticks == r_ticks,
            )
            .values(
                reported_ticks=ticks,
                reported_claims=claims,
                reported_failures=failures,
                reported_at=now,
            )
            .execution_options(synchronize_session="fetch")
        )
        if advanced.rowcount == 1:
            d_ticks += ticks - r_ticks
            d_claims += claims - r_claims
            d_failures += failures - r_failures
    session.flush()
    return d_ticks, d_claims, d_failures
