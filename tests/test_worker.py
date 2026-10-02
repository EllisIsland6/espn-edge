"""The worker loop, where the queue work and the tenancy work meet.

The rule this file exists to prove: **the worker claims across tenants, the
handler runs inside one.** Claiming must see every queue, because one process
serves everybody -- which is why `jobs` has no row-level security policy.
Running must see exactly one tenant, because the handler touches real data.

What can be proven offline is that the handler's session is bound to the
job's tenant and to nothing else. That the binding then *isolates* is
PostgreSQL's job and was measured in `docs/sprint-9/kernel/login_e2e.py`.
Asserting isolation here, on SQLite, would be a green tick establishing
nothing -- so this file asserts the binding and says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.models import Job, Tenant
from api.services import worker
from api.services.jobs import BACKOFF, DONE, FAILED, POISON, QUEUED, enqueue
from api.services.worker import NonRetryable, drain, register, run_once

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
    """Handlers are module-global, so a test that registers one would leak
    into the next. Snapshot and restore rather than clear: the application may
    register real handlers at import, and wiping them would make this file
    pass while breaking the thing it is testing."""
    saved = dict(worker.HANDLERS)
    yield
    worker.HANDLERS.clear()
    worker.HANDLERS.update(saved)


def _state(db_session, job_id: int) -> str:
    db_session.expire_all()
    return db_session.get(Job, job_id).state


def _row(db_session, job_id: int) -> Job:
    """Re-read from the database.

    The worker commits on its own sessions, so the test session's identity map
    still holds the row as it was before the job ran. Reading
    `last_error` off that cached object returns None and looks like the worker
    never wrote one -- which is exactly what three tests in this file reported
    before this helper existed.
    """
    db_session.expire_all()
    return db_session.get(Job, job_id)


def test_nothing_to_do_reports_nothing(clock):
    assert run_once(owner="w1", clock=clock) is None


def test_a_registered_handler_runs_and_the_job_completes(db_session, clock, tenant_id):
    ran: list[dict | None] = []

    @register("report")
    def _handler(session, payload):
        ran.append(payload)

    job = enqueue(
        db_session,
        kind="report",
        idempotency_key="k",
        tenant_id=tenant_id,
        payload={"league": 7},
        clock=clock,
    )
    db_session.commit()

    assert run_once(owner="w1", clock=clock) == "done"
    assert ran == [{"league": 7}], ran
    assert _state(db_session, job.id) == DONE


def test_the_handler_session_is_bound_to_the_job_s_tenant(db_session, clock, tenant_id):
    """The rule, as far as SQLite can show it.

    The handler is handed a session whose bound tenant is the job's. Whether
    that binding then hides other tenants' rows is row-level security's job
    and is PostgreSQL-only; this asserts the binding, which is the half the
    application is responsible for.
    """
    from api.tenancy import current_tenant_id

    seen: list[int] = []

    @register("report")
    def _handler(session, _payload):
        seen.append(current_tenant_id(session))

    enqueue(db_session, kind="report", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    assert run_once(owner="w1", clock=clock) == "done"
    assert seen == [tenant_id], f"handler ran bound to {seen}, expected [{tenant_id}]"


def test_each_job_runs_bound_to_its_own_tenant(db_session, clock, tenant_id):
    """Two tenants, one worker, one tick each. The binding must follow the
    job rather than being set once for the process."""
    other = Tenant(slug="second")
    db_session.add(other)
    db_session.flush()

    from api.tenancy import current_tenant_id

    seen: list[int] = []

    @register("report")
    def _handler(session, _payload):
        seen.append(current_tenant_id(session))

    enqueue(db_session, kind="report", idempotency_key="a", tenant_id=tenant_id, clock=clock)
    enqueue(db_session, kind="report", idempotency_key="b", tenant_id=other.id, clock=clock)
    db_session.commit()

    assert drain(owner="w1", clock=clock) == ["done", "done"]
    assert sorted(seen) == sorted([tenant_id, other.id]), seen


def test_a_job_with_no_tenant_is_refused_rather_than_run(db_session, clock):
    """Fail closed, and loudly.

    Under row-level security a tenantless job would read nothing, so the
    handler would "succeed" having done nothing and the queue would record it
    as done. That is the worst outcome available: silent, permanent, and
    indistinguishable from real work.
    """
    ran: list[int] = []

    @register("report")
    def _handler(_session, _payload):
        ran.append(1)

    job = enqueue(db_session, kind="report", idempotency_key="k", tenant_id=None, clock=clock)
    db_session.commit()

    assert run_once(owner="w1", clock=clock) == FAILED
    assert ran == [], "the handler ran for a job with no tenant"
    assert "no tenant" in _row(db_session, job.id).last_error


def test_an_unregistered_kind_fails_without_retrying(db_session, clock, tenant_id):
    """A handler that does not exist will not appear by waiting."""
    job = enqueue(db_session, kind="mystery", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    assert run_once(owner="w1", clock=clock) == FAILED
    assert "no handler registered" in _row(db_session, job.id).last_error
    clock.advance(timedelta(days=1))
    assert run_once(owner="w1", clock=clock) is None, "an unhandled kind was retried"


def test_a_handler_raising_an_ordinary_error_is_retried(db_session, clock, tenant_id):
    @register("report")
    def _handler(_session, _payload):
        raise TimeoutError("provider slow")

    job = enqueue(db_session, kind="report", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    assert run_once(owner="w1", clock=clock) == QUEUED
    assert "TimeoutError" in _row(db_session, job.id).last_error
    assert run_once(owner="w1", clock=clock) is None, "retried with no cooldown"

    clock.advance(BACKOFF[0] + timedelta(seconds=1))
    assert run_once(owner="w1", clock=clock) == QUEUED


def test_a_handler_raising_nonretryable_is_not_retried(db_session, clock, tenant_id):
    @register("report")
    def _handler(_session, _payload):
        raise NonRetryable("credential expired")

    enqueue(db_session, kind="report", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    assert run_once(owner="w1", clock=clock) == FAILED
    clock.advance(timedelta(days=1))
    assert run_once(owner="w1", clock=clock) is None


def test_a_failing_handler_eventually_poisons(db_session, clock, tenant_id):
    @register("report")
    def _handler(_session, _payload):
        raise RuntimeError("always")

    enqueue(
        db_session,
        kind="report",
        idempotency_key="k",
        tenant_id=tenant_id,
        max_attempts=2,
        clock=clock,
    )
    db_session.commit()

    outcomes = []
    for _ in range(4):
        state = run_once(owner="w1", clock=clock)
        if state is None:
            break
        outcomes.append(state)
        clock.advance(max(BACKOFF) + timedelta(seconds=1))

    assert outcomes == [QUEUED, POISON], outcomes


def test_a_handler_writing_to_its_bound_session_is_committed(db_session, clock, tenant_id):
    """The handler's work and the job's completion are separate transactions,
    so a handler that writes must have its write committed by the scope it was
    given rather than depending on the bookkeeping that follows."""
    other = Tenant(slug="written-by-handler")

    @register("report")
    def _handler(session, _payload):
        session.add(other)

    enqueue(db_session, kind="report", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    db_session.commit()
    assert run_once(owner="w1", clock=clock) == "done"

    db_session.expire_all()
    slugs = {t.slug for t in db_session.query(Tenant).all()}
    assert "written-by-handler" in slugs


def test_the_worker_survives_a_handler_that_raises_anything(db_session, clock, tenant_id):
    """A worker that dies with its handler stops the queue for everybody. The
    broad except is deliberate and this is what justifies it."""

    @register("report")
    def _handler(_session, _payload):
        raise BaseException  # noqa: TRY002 - the point of the test

    enqueue(db_session, kind="report", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    with pytest.raises(BaseException):  # noqa: B017, PT011
        run_once(owner="w1", clock=clock)

    # The lease was committed before the handler ran, so the job is not lost:
    # it comes back when the lease expires.
    clock.advance(timedelta(minutes=10))
    assert _state(db_session, 1) != DONE


def test_registering_two_handlers_for_one_kind_is_refused():
    """Whichever module imported second would silently win, and the symptom
    months later is "the wrong thing ran"."""

    @register("report")
    def _first(_session, _payload):
        pass

    with pytest.raises(ValueError, match="already registered"):

        @register("report")
        def _second(_session, _payload):
            pass


def test_drain_stops_when_the_queue_is_empty(db_session, clock, tenant_id):
    @register("report")
    def _handler(_session, _payload):
        pass

    for i in range(3):
        enqueue(
            db_session, kind="report", idempotency_key=f"k{i}", tenant_id=tenant_id, clock=clock
        )
    db_session.commit()

    assert drain(owner="w1", clock=clock) == ["done", "done", "done"]


def test_drain_respects_its_limit(db_session, clock, tenant_id):
    """Without the limit a handler that re-enqueues its own kind turns one
    tick into an unbounded loop, and the symptom is a worker that never
    reports idle."""
    counter = {"n": 0}

    @register("report")
    def _handler(session, _payload):
        counter["n"] += 1
        enqueue(
            session,
            kind="report",
            idempotency_key=f"spawn{counter['n']}",
            tenant_id=tenant_id,
            clock=clock,
        )

    enqueue(db_session, kind="report", idempotency_key="seed", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    outcomes = drain(owner="w1", limit=4, clock=clock)
    assert len(outcomes) == 4, outcomes
