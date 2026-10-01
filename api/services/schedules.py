"""Materialising schedules into jobs, idempotently and without a leader.

The whole design is one observation: if a job's identity is computed from
(schedule, slot) rather than from the moment somebody noticed it was due, then
any number of schedulers can cover the same window and the database collapses
their work into one job per slot. No leader election, no advisory lock, no
"primary scheduler" that becomes a single point of failure.

The alternative -- a lock, or one designated scheduler -- is the usual answer
and it is worse here: it adds a failure mode (the leader dies holding the
lock) to prevent a problem arithmetic can prevent outright.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Job, Schedule
from .jobs import QUEUED, Clock, _now, enqueue

#: How far ahead a tick materialises. Larger means fewer ticks and more rows
#: sitting queued with a future `available_at`; smaller means a missed tick
#: delays work. One interval is the honest default: it guarantees the next
#: slot exists before it is due.
DEFAULT_HORIZON = timedelta(minutes=1)


def _utc(value: datetime) -> datetime:
    """SQLite returns stored timestamps naive. Fourth place in this codebase
    that has to say so."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def slot_for(schedule: Schedule, moment: datetime) -> datetime:
    """The start of the slot `moment` falls in, on this schedule's grid.

    Floor division from the anchor, so the grid is absolute rather than
    relative to whenever a process happened to start. Two schedulers that
    booted hours apart compute the same boundaries.
    """
    anchor = _utc(schedule.anchor_at)
    interval = max(int(schedule.interval_seconds), 1)
    elapsed = (moment - anchor).total_seconds()
    index = int(elapsed // interval)
    return anchor + timedelta(seconds=index * interval)


def slot_key(schedule: Schedule, slot: datetime) -> str:
    """The idempotency key for one slot of one schedule.

    This is the load-bearing line of the module. It contains no clock reading,
    no worker identity and no randomness -- only the schedule and the slot --
    so it is the same string no matter who computes it or when.
    """
    return f"sched:{schedule.id}:{slot.isoformat()}"


def materialize_due(
    session: Session,
    *,
    horizon: timedelta = DEFAULT_HORIZON,
    clock: Clock = _now,
) -> list[Job]:
    """Enqueue a job for every schedule slot that opens within the horizon.

    Returns the jobs that this call created or found. Safe to run from any
    number of processes at any cadence: duplicate work is collapsed by
    `enqueue`'s per-tenant uniqueness, which is why this function needs no
    coordination of its own.

    `next_run_at` is advanced as a cursor. A scheduler whose cursor is stale
    does redundant arithmetic and gets back the already-existing jobs; it does
    not create duplicates, because the keys are identical.
    """
    now = clock()
    cutoff = now + horizon

    due = (
        session.execute(
            select(Schedule)
            .where(Schedule.enabled.is_(True), Schedule.next_run_at <= cutoff)
            .order_by(Schedule.id)
        )
        .scalars()
        .all()
    )

    created: list[Job] = []
    for schedule in due:
        interval = timedelta(seconds=max(int(schedule.interval_seconds), 1))
        slot = slot_for(schedule, _utc(schedule.next_run_at))

        # Walk forward through every slot that opens inside the window. A
        # scheduler that has been down for an hour catches up slot by slot
        # rather than collapsing the gap into one run -- which matters because
        # each slot is a separate idempotency key and therefore a separate
        # piece of work somebody expected to happen.
        while slot <= cutoff:
            created.append(
                enqueue(
                    session,
                    kind=schedule.kind,
                    idempotency_key=slot_key(schedule, slot),
                    tenant_id=schedule.tenant_id,
                    payload=schedule.payload_json,
                    available_at=slot,
                    clock=clock,
                )
            )
            slot = slot + interval

        schedule.next_run_at = slot
        schedule.updated_at = now

    session.flush()
    return created


def oldest_due_age(session: Session, *, clock: Clock = _now) -> timedelta:
    """How long the oldest job that is due has been waiting.

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
