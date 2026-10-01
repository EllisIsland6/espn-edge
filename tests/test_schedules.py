"""Schedules materialise into jobs idempotently, with no leader.

The property worth proving is the one Phase 39's acceptance names: **three
schedulers materialise one window**. It holds because a job's identity comes
from (schedule, slot) rather than from the moment somebody noticed it was due,
so every scheduler computes the same key and the database collapses them.

Everything else in this file is a consequence of that, or a case where the
arithmetic has to be right for it to hold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.models import Job, Schedule, Tenant
from api.services.jobs import claim, complete, enqueue
from api.services.schedules import (
    _utc,
    materialize_due,
    oldest_due_age,
    slot_for,
    slot_key,
)

T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tenant_id(db_session) -> int:
    return db_session.query(Tenant).one().id


def _schedule(db_session, tenant_id, *, every=timedelta(minutes=15), name="sync-all"):
    row = Schedule(
        tenant_id=tenant_id,
        name=name,
        kind="report",
        interval_seconds=int(every.total_seconds()),
        anchor_at=T0,
        next_run_at=T0,
        enabled=True,
        created_at=T0,
        updated_at=T0,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _jobs(db_session) -> list[Job]:
    return list(db_session.query(Job).order_by(Job.id).all())


def test_a_due_schedule_materialises_one_job(db_session, clock, tenant_id):
    _schedule(db_session, tenant_id)
    created = materialize_due(db_session, clock=clock)
    assert len(created) == 1
    assert len(_jobs(db_session)) == 1


def test_three_schedulers_covering_one_window_produce_one_job(db_session, clock, tenant_id):
    """The acceptance property, and the reason the key contains no clock
    reading, no worker identity and no randomness.

    Three independent passes over the same window -- as three processes ticking
    at once would do -- and the database collapses them. No lock was taken and
    no leader was elected.
    """
    _schedule(db_session, tenant_id)
    for _ in range(3):
        materialize_due(db_session, clock=clock)

    jobs = _jobs(db_session)
    assert len(jobs) == 1, f"{len(jobs)} jobs materialised for one slot"


def test_schedulers_whose_cursors_disagree_still_produce_one_job(
    db_session, clock, tenant_id
):
    """A stale cursor is the realistic version of the race: one scheduler has
    been down and still thinks the window starts an hour ago.

    It does redundant arithmetic and gets back the jobs that already exist,
    because the slot keys are identical. Redundant effort, not duplicated
    work -- which is why `next_run_at` can be a cursor rather than the truth.
    """
    schedule = _schedule(db_session, tenant_id)
    clock.advance(timedelta(hours=1))
    materialize_due(db_session, clock=clock)
    first_count = len(_jobs(db_session))
    assert first_count > 1, "the catch-up did not walk the missed slots"

    # A second scheduler with a cursor an hour behind.
    schedule.next_run_at = T0
    db_session.flush()
    materialize_due(db_session, clock=clock)

    assert len(_jobs(db_session)) == first_count, "the stale scheduler duplicated work"


def test_a_scheduler_that_was_down_catches_up_slot_by_slot(db_session, clock, tenant_id):
    """Each slot is a separate idempotency key and therefore a separate piece
    of work somebody expected to happen. Collapsing an hour's gap into one run
    would silently drop three of four syncs."""
    _schedule(db_session, tenant_id, every=timedelta(minutes=15))
    clock.advance(timedelta(hours=1))
    created = materialize_due(db_session, clock=clock)

    # 12:00 through 13:00 inclusive on a 15-minute grid, plus the horizon.
    assert len(created) >= 5, [j.idempotency_key for j in created]
    keys = {j.idempotency_key for j in created}
    assert len(keys) == len(created), "the catch-up produced duplicate keys"


def test_the_slot_grid_is_absolute_not_relative_to_process_start(db_session, tenant_id):
    """Two schedulers that booted hours apart must compute the same
    boundaries, or their keys differ and the collapse never happens."""
    schedule = _schedule(db_session, tenant_id, every=timedelta(minutes=15))
    assert slot_for(schedule, T0 + timedelta(minutes=7)) == T0
    assert slot_for(schedule, T0 + timedelta(minutes=16)) == T0 + timedelta(minutes=15)
    assert slot_for(schedule, T0 + timedelta(minutes=29, seconds=59)) == T0 + timedelta(
        minutes=15
    )


def test_the_slot_key_has_no_clock_or_identity_in_it(db_session, tenant_id):
    """Guards the guard. If the key ever picked up a timestamp of its own, the
    idempotency above would quietly stop working and every test in this file
    would still pass except the ones that count rows."""
    schedule = _schedule(db_session, tenant_id)
    slot = slot_for(schedule, T0)
    assert slot_key(schedule, slot) == slot_key(schedule, slot)
    assert slot_key(schedule, slot) != slot_key(schedule, slot + timedelta(minutes=15))


def test_a_disabled_schedule_materialises_nothing(db_session, clock, tenant_id):
    schedule = _schedule(db_session, tenant_id)
    schedule.enabled = False
    db_session.flush()
    assert materialize_due(db_session, clock=clock) == []
    assert _jobs(db_session) == []


def test_a_future_schedule_is_not_materialised_before_its_horizon(
    db_session, clock, tenant_id
):
    schedule = _schedule(db_session, tenant_id)
    schedule.next_run_at = T0 + timedelta(hours=2)
    db_session.flush()
    assert materialize_due(db_session, clock=clock) == []


def test_two_tenants_with_the_same_schedule_name_get_separate_jobs(
    db_session, clock, tenant_id
):
    """Idempotency is per tenant. Two tenants on the same cadence are two
    pieces of work, and a global key would silently serve one of them."""
    other = Tenant(slug="second")
    db_session.add(other)
    db_session.flush()
    _schedule(db_session, tenant_id, name="sync-all")
    _schedule(db_session, other.id, name="sync-all")

    materialize_due(db_session, clock=clock)
    tenants = {j.tenant_id for j in _jobs(db_session)}
    assert tenants == {tenant_id, other.id}, tenants


def test_a_manual_enqueue_coalesces_with_the_scheduled_one(db_session, clock, tenant_id):
    """"Sync now" pressed while the scheduled sync for this slot is already
    queued should not run it twice -- the caller passes the slot key and gets
    the existing job back."""
    schedule = _schedule(db_session, tenant_id)
    materialize_due(db_session, clock=clock)
    scheduled = _jobs(db_session)[0]

    manual = enqueue(
        db_session,
        kind=schedule.kind,
        idempotency_key=slot_key(schedule, slot_for(schedule, clock())),
        tenant_id=tenant_id,
        clock=clock,
    )
    assert manual.id == scheduled.id
    assert len(_jobs(db_session)) == 1


def test_materialised_jobs_are_claimable_when_their_slot_arrives(
    db_session, clock, tenant_id
):
    """End to end: the schedule produces work the queue actually runs."""
    _schedule(db_session, tenant_id)
    materialize_due(db_session, clock=clock)

    job = claim(db_session, owner="w1", clock=clock)
    assert job is not None
    assert complete(db_session, job.id, owner="w1", clock=clock) is True


def test_a_job_materialised_for_a_future_slot_waits(db_session, clock, tenant_id):
    """The horizon exists so the next slot's row is there before it is due.
    It must not be runnable early, or the horizon becomes a way to run things
    ahead of schedule."""
    _schedule(db_session, tenant_id, every=timedelta(seconds=30))
    created = materialize_due(db_session, clock=clock)

    # Normalised through the same helper the service uses: SQLite hands back
    # naive timestamps, and comparing one to an aware `now` raises. Writing
    # `.replace(tzinfo=None)` on the aware side instead was the first attempt
    # and it raised anyway, because `available_at` is aware for rows still
    # live in the identity map and naive for rows reloaded after a flush.
    future = [j for j in created if _utc(j.available_at) > clock()]
    assert future, "the horizon produced no future slot, so this proves nothing"

    for _ in range(len(created)):
        job = claim(db_session, owner="w1", clock=clock)
        if job is None:
            break
        assert _utc(job.available_at) <= clock(), "a future slot was claimed early"
        complete(db_session, job.id, owner="w1", clock=clock)


def test_queue_age_is_zero_when_nothing_is_due(db_session, clock):
    assert oldest_due_age(db_session, clock=clock) == timedelta(0)


def test_queue_age_measures_the_oldest_due_job_not_the_count(db_session, clock, tenant_id):
    """Depth says nothing about health: a thousand jobs moving quickly is
    fine, one job stuck for an hour is not. Age is what an alarm should
    watch."""
    enqueue(
        db_session,
        kind="report",
        idempotency_key="old",
        tenant_id=tenant_id,
        available_at=clock() - timedelta(minutes=30),
        clock=clock,
    )
    enqueue(
        db_session,
        kind="report",
        idempotency_key="new",
        tenant_id=tenant_id,
        clock=clock,
    )
    age = oldest_due_age(db_session, clock=clock)
    assert timedelta(minutes=29) <= age <= timedelta(minutes=31), age


def test_queue_age_ignores_jobs_that_are_not_due_yet(db_session, clock, tenant_id):
    """A job on a cooldown is not a backlog. Counting it would make every
    retry look like an incident."""
    enqueue(
        db_session,
        kind="report",
        idempotency_key="later",
        tenant_id=tenant_id,
        available_at=clock() + timedelta(hours=1),
        clock=clock,
    )
    assert oldest_due_age(db_session, clock=clock) == timedelta(0)
