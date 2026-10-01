"""The durable job queue, with time under the test's control.

A queue's hard cases are all about time -- a lease that expired, a cooldown
that has not, a job scheduled for later. Tests that sleep are slow and flaky;
tests that cannot control time do not exercise those cases at all. So the
service takes a clock and every case below sets it.

The other theme is that each transition is a conditional UPDATE whose row
count is the answer. Read-then-write loses races silently: two workers both
see `queued`, both write `leased`, and the job runs twice -- which for a sync
means two provider calls against a rate limit that allows one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import api.services.jobs as jobs_module
from api.models import Job, Tenant
from api.services.jobs import (
    BACKOFF,
    DONE,
    FAILED,
    LEASED,
    MAX_ERROR,
    POISON,
    PROVIDER_KINDS,
    QUEUED,
    claim,
    complete,
    enqueue,
    fail,
    queue_depth,
)

T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


class Clock:
    """A clock the test moves by hand."""

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


def test_a_job_is_queued_and_immediately_due(db_session, clock, tenant_id):
    job = enqueue(
        db_session, kind="sync", idempotency_key="k1", tenant_id=tenant_id, clock=clock
    )
    assert job.state == QUEUED
    assert job.attempts == 0
    assert queue_depth(db_session, clock=clock) == 1


def test_enqueuing_the_same_key_twice_returns_the_same_job(db_session, clock, tenant_id):
    """Idempotency is the property a retrying client depends on. Without it a
    dropped response means a second job, and a sync runs twice."""
    first = enqueue(
        db_session, kind="sync", idempotency_key="same", tenant_id=tenant_id, clock=clock
    )
    second = enqueue(
        db_session, kind="sync", idempotency_key="same", tenant_id=tenant_id, clock=clock
    )
    assert first.id == second.id
    assert queue_depth(db_session, clock=clock) == 1


def test_the_same_key_in_another_tenant_is_a_different_job(db_session, clock, tenant_id):
    """Idempotency is per tenant, not global. Two tenants syncing the same
    ESPN league are two jobs -- a global key would silently collapse them,
    which is the mistake `uq_league_season` made."""
    other = Tenant(slug="other")
    db_session.add(other)
    db_session.flush()

    a = enqueue(db_session, kind="sync", idempotency_key="dup", tenant_id=tenant_id, clock=clock)
    b = enqueue(db_session, kind="sync", idempotency_key="dup", tenant_id=other.id, clock=clock)
    assert a.id != b.id
    assert queue_depth(db_session, clock=clock) == 2


def test_a_future_job_is_not_claimable_until_it_is_due(db_session, clock, tenant_id):
    enqueue(
        db_session,
        kind="sync",
        idempotency_key="later",
        tenant_id=tenant_id,
        available_at=clock() + timedelta(minutes=10),
        clock=clock,
    )
    assert queue_depth(db_session, clock=clock) == 0
    assert claim(db_session, owner="w1", clock=clock) is None

    clock.advance(timedelta(minutes=11))
    assert claim(db_session, owner="w1", clock=clock) is not None


def test_claiming_leases_the_job_and_counts_the_attempt(db_session, clock, tenant_id):
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    job = claim(db_session, owner="w1", clock=clock)
    assert job is not None
    assert job.state == LEASED
    assert job.lease_owner == "w1"
    assert job.attempts == 1
    assert queue_depth(db_session, clock=clock) == 0, "a leased job still looks due"


def test_two_sequential_claims_do_not_both_succeed(db_session, clock, tenant_id):
    """The easy half, and on its own it proves less than it looks.

    Two sequential calls are separated by the candidate SELECT, which already
    filters on state -- so this passes even with the conditional UPDATE's
    guard deleted. Kept because the outcome is worth pinning; the test below
    is the one that measures the guard.
    """
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    first = claim(db_session, owner="w1", clock=clock)
    second = claim(db_session, owner="w2", clock=clock)
    assert first is not None
    assert second is None, "the second worker claimed a job that was already leased"


def test_a_worker_that_is_beaten_to_the_row_claims_nothing(db_session, clock, tenant_id, monkeypatch):
    """The race the conditional UPDATE actually exists for.

    Real workers interleave between choosing a candidate and claiming it. One
    process cannot produce that by calling `claim` twice, so the candidate
    lookup is interposed: worker 1 picks the row, worker 2 takes it while
    worker 1 is still deciding, and worker 1's UPDATE must then match zero
    rows and fall through to finding nothing.

    This is the test that fails when the guard is removed. The sequential one
    above does not, which is why both exist.
    """
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)

    real = jobs_module._eligible_candidates
    stolen = {"done": False}

    def _steal_then_return(session, now):
        candidates = real(session, now)
        if candidates and not stolen["done"]:
            stolen["done"] = True
            # Worker 2 gets there first, using the real code path.
            monkeypatch.setattr(jobs_module, "_eligible_candidates", real)
            assert claim(session, owner="w2", clock=clock) is not None
            monkeypatch.setattr(jobs_module, "_eligible_candidates", _steal_then_return)
        return candidates

    monkeypatch.setattr(jobs_module, "_eligible_candidates", _steal_then_return)
    loser = claim(db_session, owner="w1", clock=clock)

    assert stolen["done"], "the interleaving never happened; this test proved nothing"
    assert loser is None, "worker 1 claimed a row worker 2 had already leased"
    assert db_session.get(Job, 1).lease_owner == "w2"


def test_a_crashed_worker_s_job_comes_back_when_the_lease_expires(db_session, clock, tenant_id):
    """No supervisor, no heartbeat, nothing detecting the death -- the lease
    simply runs out and the next worker takes it."""
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    first = claim(db_session, owner="dies", lease=timedelta(minutes=5), clock=clock)
    assert first is not None

    clock.advance(timedelta(minutes=4))
    assert claim(db_session, owner="w2", clock=clock) is None, "reclaimed before expiry"

    clock.advance(timedelta(minutes=2))
    reclaimed = claim(db_session, owner="w2", clock=clock)
    assert reclaimed is not None
    assert reclaimed.lease_owner == "w2"
    assert reclaimed.attempts == 2, "the reclaim did not count as a second attempt"


def test_a_stalled_worker_cannot_report_success_after_losing_its_lease(
    db_session, clock, tenant_id
):
    """Two workers can briefly hold the same job -- one stalled past its
    lease, one that reclaimed it. Refusing the stalled worker's write is what
    keeps the record honest about who did the work."""
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    stalled = claim(db_session, owner="stalled", lease=timedelta(minutes=1), clock=clock)
    clock.advance(timedelta(minutes=2))
    reclaimed = claim(db_session, owner="fresh", clock=clock)
    assert reclaimed is not None

    assert complete(db_session, stalled.id, owner="stalled", clock=clock) is False
    assert complete(db_session, reclaimed.id, owner="fresh", clock=clock) is True
    assert db_session.get(Job, reclaimed.id).state == DONE


def test_completing_clears_the_lease(db_session, clock, tenant_id):
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    job = claim(db_session, owner="w1", clock=clock)
    assert complete(db_session, job.id, owner=job.lease_owner, clock=clock) is True

    row = db_session.get(Job, job.id)
    assert (row.state, row.lease_owner, row.lease_expires_at) == (DONE, None, None)
    assert claim(db_session, owner="w2", clock=clock) is None


def test_a_retryable_failure_goes_back_with_a_cooldown(db_session, clock, tenant_id):
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    job = claim(db_session, owner="w1", clock=clock)

    assert fail(db_session, job.id, owner="w1", error="provider timeout", clock=clock) == QUEUED
    assert claim(db_session, owner="w2", clock=clock) is None, "retried with no cooldown"

    clock.advance(BACKOFF[0] + timedelta(seconds=1))
    assert claim(db_session, owner="w2", clock=clock) is not None


def test_a_non_retryable_failure_does_not_come_back(db_session, clock, tenant_id):
    """An expired credential does not get better by being tried again.
    Retrying it burns the attempt budget and delays the human who must fix
    it."""
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    job = claim(db_session, owner="w1", clock=clock)

    assert fail(db_session, job.id, owner="w1", error="auth expired", retryable=False, clock=clock) == FAILED
    clock.advance(timedelta(days=1))
    assert claim(db_session, owner="w2", clock=clock) is None
    assert db_session.get(Job, job.id).attempts == 1, "a non-retryable error consumed retries"


def test_exhausting_the_attempt_budget_quarantines_rather_than_looping(
    db_session, clock, tenant_id
):
    """Poison, not deleted: the row is the only evidence of what happened."""
    enqueue(
        db_session,
        kind="sync",
        idempotency_key="k",
        tenant_id=tenant_id,
        max_attempts=2,
        clock=clock,
    )
    states = []
    for _ in range(5):
        job = claim(db_session, owner="w1", clock=clock)
        if job is None:
            break
        states.append(fail(db_session, job.id, owner="w1", error="boom", clock=clock))
        clock.advance(max(BACKOFF) + timedelta(seconds=1))

    assert states == [QUEUED, POISON], states
    clock.advance(timedelta(days=7))
    assert claim(db_session, owner="w1", clock=clock) is None, "a poisoned job was retried"
    assert db_session.get(Job, 1).last_error == "boom"


def test_a_long_error_is_truncated_rather_than_stored_whole(db_session, clock, tenant_id):
    """`last_error` is a database column and the thing that fails is often a
    provider response. Truncating here rather than trusting callers is the
    difference between a diagnostic and an accidental payload store."""
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    job = claim(db_session, owner="w1", clock=clock)
    fail(db_session, job.id, owner="w1", error="x" * 5000, clock=clock)

    stored = db_session.get(Job, job.id).last_error
    assert len(stored) == MAX_ERROR


def test_jobs_are_claimed_oldest_due_first(db_session, clock, tenant_id):
    """Not strict fairness -- that is the part of Phase 39 this slice skips --
    but a queue that ignored due order would starve whatever arrived first."""
    for i in range(3):
        enqueue(
            db_session,
            kind="sync",
            idempotency_key=f"k{i}",
            tenant_id=tenant_id,
            available_at=clock() - timedelta(minutes=3 - i),
            clock=clock,
        )
    order = []
    for _ in range(3):
        job = claim(db_session, owner="w1", clock=clock)
        order.append(job.idempotency_key)
        complete(db_session, job.id, owner=job.lease_owner, clock=clock)
    assert order == ["k0", "k1", "k2"], order


def test_nothing_is_claimable_from_an_empty_queue(db_session, clock):
    assert claim(db_session, owner="w1", clock=clock) is None
    assert queue_depth(db_session, clock=clock) == 0


def test_a_worker_cannot_fail_a_job_it_no_longer_holds(db_session, clock, tenant_id):
    """The mirror of the stalled-completion case. A worker that lost its lease
    must not be able to push the job to poison either -- that would let a
    zombie retire work another worker is actively running."""
    enqueue(db_session, kind="sync", idempotency_key="k", tenant_id=tenant_id, clock=clock)
    stalled = claim(db_session, owner="stalled", lease=timedelta(minutes=1), clock=clock)
    assert stalled is not None
    clock.advance(timedelta(minutes=2))
    reclaimed = claim(db_session, owner="fresh", clock=clock)
    assert reclaimed is not None

    assert fail(db_session, stalled.id, owner="stalled", error="late", clock=clock) is None
    assert db_session.get(Job, stalled.id).state == LEASED, "the zombie changed the state"
    assert fail(db_session, reclaimed.id, owner="fresh", error="real", clock=clock) == QUEUED


def test_the_classification_test_covers_this_new_table():
    """Guards against the guard being skipped: `jobs` must be classified, and
    `tests/test_tenant_classification.py` is what fails if it is not. Asserted
    here too so a reader of this file knows the decision was made."""
    from tests.test_tenant_classification import TENANT_TABLES

    assert "jobs" in TENANT_TABLES


# ---------------------------------------------------------------------------
# Claim policy: one job per tenant, one provider permit, fairest tenant first
# ---------------------------------------------------------------------------


@pytest.fixture
def three_tenants(db_session, tenant_id) -> list[int]:
    extra = [Tenant(slug="second"), Tenant(slug="third")]
    db_session.add_all(extra)
    db_session.flush()
    return [tenant_id, extra[0].id, extra[1].id]


def _queue(db_session, clock, tenant, key, kind="report"):
    return enqueue(
        db_session, kind=kind, idempotency_key=key, tenant_id=tenant, clock=clock
    )


def test_a_tenant_gets_one_active_job_at_a_time(db_session, clock, three_tenants):
    """Without this a tenant with two hundred queued leagues occupies every
    worker and everyone else waits."""
    a, b, _ = three_tenants
    _queue(db_session, clock, a, "a1")
    _queue(db_session, clock, a, "a2")

    first = claim(db_session, owner="w1", clock=clock)
    assert first is not None and first.tenant_id == a

    second = claim(db_session, owner="w2", clock=clock)
    assert second is None, "a second job was leased for a tenant already busy"

    # ...but another tenant is unaffected, which is the point of the rule.
    _queue(db_session, clock, b, "b1")
    other = claim(db_session, owner="w3", clock=clock)
    assert other is not None and other.tenant_id == b


def test_a_tenant_s_next_job_becomes_claimable_once_the_first_finishes(
    db_session, clock, three_tenants
):
    a = three_tenants[0]
    _queue(db_session, clock, a, "a1")
    _queue(db_session, clock, a, "a2")

    first = claim(db_session, owner="w1", clock=clock)
    assert claim(db_session, owner="w2", clock=clock) is None
    complete(db_session, first.id, owner="w1", clock=clock)
    assert claim(db_session, owner="w2", clock=clock) is not None


def test_only_one_provider_touching_job_runs_at_a_time(db_session, clock, three_tenants):
    """Phase 39's guarantee: increasing workers never raises provider rps.

    Two different tenants, so the per-tenant rule is not what is doing the
    work here -- without the permit both would lease and the deployment would
    make two concurrent ESPN calls against a limit that allows one.
    """
    a, b, c = three_tenants
    kind = sorted(PROVIDER_KINDS)[0]
    _queue(db_session, clock, a, "pa", kind=kind)
    _queue(db_session, clock, b, "pb", kind=kind)

    first = claim(db_session, owner="w1", clock=clock)
    assert first is not None and first.kind == kind

    assert claim(db_session, owner="w2", clock=clock) is None, (
        "a second provider-touching job was leased while one was running"
    )

    # Non-provider work still flows -- scaling out buys something.
    _queue(db_session, clock, c, "local", kind="report")
    local = claim(db_session, owner="w3", clock=clock)
    assert local is not None and local.kind == "report"


def test_the_provider_permit_is_released_when_the_job_finishes(
    db_session, clock, three_tenants
):
    a, b, _ = three_tenants
    kind = sorted(PROVIDER_KINDS)[0]
    _queue(db_session, clock, a, "pa", kind=kind)
    _queue(db_session, clock, b, "pb", kind=kind)

    first = claim(db_session, owner="w1", clock=clock)
    complete(db_session, first.id, owner="w1", clock=clock)
    assert claim(db_session, owner="w2", clock=clock) is not None


def test_the_provider_permit_is_released_when_the_lease_expires(
    db_session, clock, three_tenants
):
    """A worker that dies holding the permit must not block the provider
    forever -- the lease expiring is what releases it, with nothing noticing
    the death."""
    a, b, _ = three_tenants
    kind = sorted(PROVIDER_KINDS)[0]
    _queue(db_session, clock, a, "pa", kind=kind)
    _queue(db_session, clock, b, "pb", kind=kind)

    claim(db_session, owner="dies", lease=timedelta(minutes=1), clock=clock)
    assert claim(db_session, owner="w2", clock=clock) is None

    clock.advance(timedelta(minutes=2))
    assert claim(db_session, owner="w2", clock=clock) is not None


def test_the_least_recently_served_tenant_goes_first(db_session, clock, three_tenants):
    """Fairness, not correctness. A tenant that enqueues constantly would
    otherwise sit permanently at the head of the due-time order."""
    a, b, c = three_tenants
    for tenant, key in ((a, "a1"), (b, "b1"), (c, "c1")):
        _queue(db_session, clock, tenant, key)

    served = []
    for _ in range(3):
        job = claim(db_session, owner="w1", clock=clock)
        assert job is not None
        served.append(job.tenant_id)
        complete(db_session, job.id, owner="w1", clock=clock)
        clock.advance(timedelta(seconds=1))

    assert sorted(served) == sorted([a, b, c]), served

    # Round two: everyone has now been served once, so the order should follow
    # who was served longest ago -- the same order as round one.
    for tenant, key in ((a, "a2"), (b, "b2"), (c, "c2")):
        _queue(db_session, clock, tenant, key)
    second_round = []
    for _ in range(3):
        job = claim(db_session, owner="w1", clock=clock)
        second_round.append(job.tenant_id)
        complete(db_session, job.id, owner="w1", clock=clock)
        clock.advance(timedelta(seconds=1))

    assert second_round == served, (
        f"round two did not follow least-recently-served order: "
        f"{second_round} after {served}"
    )


def test_a_tenant_never_served_jumps_ahead_of_one_that_has(
    db_session, clock, three_tenants
):
    """A newcomer should not queue behind a tenant that has been running all
    day, even if the newcomer's job is newer."""
    a, b, _ = three_tenants
    first = _queue(db_session, clock, a, "a1")
    claim(db_session, owner="w1", clock=clock)
    complete(db_session, first.id, owner="w1", clock=clock)
    clock.advance(timedelta(seconds=1))

    _queue(db_session, clock, a, "a2")       # served tenant, older
    clock.advance(timedelta(seconds=1))
    _queue(db_session, clock, b, "b1")       # newcomer, newer

    nxt = claim(db_session, owner="w1", clock=clock)
    assert nxt.tenant_id == b, "the never-served tenant waited behind a served one"
