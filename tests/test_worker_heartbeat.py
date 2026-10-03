"""The worker's heartbeat, at the integration level.

`tests/test_heartbeat.py` proves the row behaves. This file proves the loop
actually writes one -- including on the pass that finds nothing, which is the
pass a "record the work" design skips and the pass that distinguishes an idle
worker from a dead one.

The reason it is a separate file: `tests/test_worker.py` asserts on job rows
and runs `drain` with `record=False`, so a heartbeat assertion added there
would be testing a configuration production does not use.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.models import Tenant, WorkerHeartbeat
from api.services import worker
from api.services.jobs import enqueue
from api.services.worker import NonRetryable, drain, register

T0 = datetime(2026, 10, 2, 9, 0, 0, tzinfo=UTC)


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


@pytest.fixture(autouse=True)
def _clean_handlers():
    saved = dict(worker.HANDLERS)
    yield
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


def _beats(db_session) -> dict[str, WorkerHeartbeat]:
    db_session.expire_all()
    return {r.owner: r for r in db_session.query(WorkerHeartbeat).all()}


def test_an_empty_drain_leaves_a_heartbeat(db_session, clock):
    """The probe's finding, answered at the level the probe ran.

    `.venv/phase41/probe_liveness.py` called this exact function against an
    empty queue and found that no durable row changed. Now one does.
    """
    assert drain(owner="w1", clock=clock) == []

    beats = _beats(db_session)
    assert "w1" in beats
    assert beats["w1"].ticks == 1
    assert beats["w1"].claims == 0
    assert beats["w1"].last_claimed_at is None


def test_a_productive_drain_records_the_claims(db_session, clock, tenant_id):
    @register("noop")
    def _noop(session, payload):
        return None

    for i in range(3):
        enqueue(db_session, tenant_id=tenant_id, kind="noop", idempotency_key=f"k{i}", clock=clock)
    db_session.commit()

    assert drain(owner="w1", clock=clock) == ["done", "done", "done"]

    beat = _beats(db_session)["w1"]
    # Four passes: three that claimed, one that found the queue empty. The
    # empty pass is counted too -- it is still proof the loop ran.
    assert beat.ticks == 4
    assert beat.claims == 3
    assert beat.last_claimed_at is not None


def test_a_failing_job_still_leaves_a_heartbeat(db_session, clock, tenant_id):
    """The heartbeat runs on its own session for this reason.

    Sharing the job's transaction would mean the record of "a worker was here"
    rolled back with the work that failed -- so a worker failing every job
    would look like a worker that was never running, which is the wrong
    incident entirely.
    """

    @register("boom")
    def _boom(session, payload):
        raise NonRetryable("nope")

    enqueue(db_session, tenant_id=tenant_id, kind="boom", idempotency_key="k", clock=clock)
    db_session.commit()

    # "failed", not "poison": a NonRetryable is terminal on the first attempt
    # rather than attempt-exhausted. Both count as a failure for the metric.
    assert drain(owner="w1", clock=clock) == ["failed"]

    beat = _beats(db_session)["w1"]
    assert beat.ticks == 2
    assert beat.claims == 1
    assert beat.failures == 1


def test_a_broken_heartbeat_does_not_stop_the_work(db_session, clock, tenant_id):
    """Telemetry must not be load-bearing.

    And it does not need to be: a heartbeat that cannot be written stops
    advancing, `worker_staleness_seconds` climbs, and
    `worker-heartbeat-stale` fires. The failure is reported by the mechanism
    it broke, which beats a traceback in a log nobody reads.
    """

    @register("noop")
    def _noop(session, payload):
        return None

    enqueue(db_session, tenant_id=tenant_id, kind="noop", idempotency_key="k", clock=clock)
    db_session.commit()

    original = worker.heartbeat.record_tick
    calls = {"n": 0}

    def exploding(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("heartbeat table is gone")

    worker.heartbeat.record_tick = exploding
    try:
        outcomes = drain(owner="w1", clock=clock)
    finally:
        worker.heartbeat.record_tick = original

    assert calls["n"] >= 1, "the heartbeat was never attempted; the test proved nothing"
    assert outcomes == ["done"], "a telemetry failure stopped the work"
    assert _beats(db_session) == {}


def test_record_false_writes_nothing(db_session, clock):
    """So the queue suites can assert on job rows without a heartbeat session
    in the way. Not for production: a caller passing it chooses to be
    invisible, and the docstring says so."""
    drain(owner="w1", clock=clock, record=False)
    assert _beats(db_session) == {}


def test_repeated_empty_drains_keep_advancing_last_seen(db_session, clock):
    """A worker parked on an empty queue for an hour must look alive the whole
    time. This is the shape the `worker-stopped` alarm reads, and the shape a
    work-only counter would report as six periods of silence."""
    seen = []
    for _ in range(6):
        clock.advance(timedelta(minutes=5))
        drain(owner="w1", clock=clock)
        seen.append(_beats(db_session)["w1"].last_seen_at.replace(tzinfo=UTC))

    assert seen == sorted(seen)
    assert len(set(seen)) == 6
    assert _beats(db_session)["w1"].ticks == 6
    assert _beats(db_session)["w1"].claims == 0
