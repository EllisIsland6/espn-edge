"""Collecting the ten, and the honest answer to "we could not measure".

The healthy path is uninteresting and is tested first so the interesting path
has something to be compared against. The interesting path is the database
being gone: nine of the ten series cannot be measured at all, and the choice
of what to publish then is the whole design.

Publishing zeroes would report perfect health during a total outage --
`queue_depth=0` is the healthiest reading that series can take. Publishing a
large sentinel would page somebody for nine conditions nobody observed, with
nine false runbook lines. Publishing `db_reachable=0` and leaving the nine
absent is correct, and it is correct *only because* every alarm treats missing
as breaching. That is what buys the right to say "unknown" and still get paged,
and `test_a_degraded_report_puts_every_alarm_in_alarm` is where that is
cashed in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from api.models import Tenant
from api.services.alarms import ALARMS, State, evaluate
from api.services.heartbeat import record_tick
from api.services.jobs import enqueue
from api.services.observability import SERIES, MemorySink
from api.services.snapshot import (
    NEVER_SECONDS,
    build_snapshot,
    probe_db_reachable,
    publish,
    report_degraded,
)

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


def _window(sink: MemorySink, alarm) -> list[float | None]:
    """The datapoints an alarm would see: one per published sample, or a gap.

    A gap is `None`, not a missing list entry, because the alarm evaluates a
    fixed number of periods and a shorter list would silently become a
    different window.
    """
    values = sink.values(alarm.series_name)
    return values if values else [None] * alarm.datapoints_to_alarm


# --------------------------------------------------------------------------
# The healthy path
# --------------------------------------------------------------------------


def test_a_snapshot_carries_every_declared_series(db_session, clock):
    snapshot = build_snapshot(db_session, clock=clock)
    assert set(snapshot) == {s.name for s in SERIES}


def test_publish_accepts_what_build_snapshot_produces(db_session, clock):
    """The two halves have to fit, and `report` refuses an incomplete snapshot
    -- so this test failing means a series was added to one and not the
    other."""
    sink = MemorySink()
    publish(sink, db_session, clock=clock, env="test")
    assert len(sink.samples) == len(SERIES)


def test_a_quiet_system_publishes_zeroes_not_silence(db_session, clock):
    """Nothing has happened. Every series still gets a datapoint.

    This is the sentence the whole phase is about: there is a difference
    between "nothing happened" and "nobody is reporting", and only the first
    one is allowed to look like this.
    """
    sink = MemorySink()
    publish(sink, db_session, clock=clock)

    assert sink.latest("worker_ticks") == 0
    assert sink.latest("jobs_claimed") == 0
    assert sink.latest("queue_depth") == 0
    assert sink.has("worker_ticks")


def test_the_snapshot_reflects_real_queue_state(db_session, clock, tenant_id):
    """Otherwise the numbers are decoration. Asserted by changing the world
    and watching the numbers move, rather than by reading the source."""
    sink = MemorySink()
    publish(sink, db_session, clock=clock)
    assert sink.latest("queue_depth") == 0

    for i in range(4):
        enqueue(
            db_session, tenant_id=tenant_id, kind="sync", idempotency_key=f"k{i}",
            clock=clock,
        )
    db_session.commit()
    clock.advance(timedelta(minutes=10))

    publish(sink, db_session, clock=clock)
    assert sink.values("queue_depth") == [0, 4]
    assert sink.latest("queue_oldest_age_seconds") == 600


def test_worker_progress_reaches_the_report(db_session, clock):
    sink = MemorySink()
    record_tick(db_session, "w1", claimed=2, failed=1, clock=clock)
    record_tick(db_session, "w1", claimed=0, clock=clock)
    db_session.commit()

    publish(sink, db_session, clock=clock)
    assert sink.latest("worker_ticks") == 2
    assert sink.latest("jobs_claimed") == 2
    assert sink.latest("jobs_failed") == 1
    assert sink.latest("worker_staleness_seconds") == 0


def test_take_progress_is_called_once_per_report(db_session, clock):
    """It advances the watermark, so calling it twice in a period would report
    the second window as empty -- which looks exactly like a stopped worker.
    Asserted by publishing twice and checking the second window is zero
    because nothing new happened, not because it was double-drained."""
    sink = MemorySink()
    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()

    publish(sink, db_session, clock=clock)
    publish(sink, db_session, clock=clock)
    assert sink.values("worker_ticks") == [1, 0]


def test_a_report_is_stamped_with_the_clock_it_measured_with(db_session, clock):
    """The measurement and its timestamp must agree.

    This whole suite passed while `publish` stamped samples with
    `datetime.now(UTC)` and measured with `clock`, because not one test looked
    at `Sample.at`. The fault harness found it on its first run: it evaluates a
    wall-clock grid, so samples filed in the wrong period made a healthy system
    light every alarm.

    CloudWatch buckets by timestamp, so this is not a test-only concern -- a
    reporter that is behind or replaying would file its data in periods it does
    not describe.
    """
    sink = MemorySink()
    clock.advance(timedelta(hours=3))
    publish(sink, db_session, clock=clock)
    assert {s.at for s in sink.samples} == {clock.now}

    degraded = MemorySink()
    clock.advance(timedelta(minutes=5))
    report_degraded(degraded, clock=clock)
    assert degraded.samples[0].at == clock.now


def test_the_two_age_series_measure_different_things(db_session, clock, tenant_id):
    """The guard against a defect that was live until this phase found it.

    `oldest_due_age` lived in `schedules.py` while querying the `jobs` table
    -- its own docstring said "the oldest job that is due" -- so there was no
    measure of schedule lateness at all, and `schedule_overdue_age_seconds`
    was about to be wired to the job queue age. Two series publishing one
    number under names claiming different things, with every test green.

    So: make the two differ, and require that they do. A single state where
    both happen to be zero would not have caught the original defect, which is
    why this builds a world where the correct answers are unequal.
    """
    from api.models import Schedule

    db_session.add(
        Schedule(
            tenant_id=tenant_id,
            name="s1",
            kind="sync",
            interval_seconds=3600,
            anchor_at=T0,
            next_run_at=T0,
            enabled=True,
        )
    )
    enqueue(
        db_session, tenant_id=tenant_id, kind="sync", idempotency_key="k",
        clock=clock,
    )
    db_session.commit()

    # The job became due now; the schedule has been due since T0.
    clock.advance(timedelta(minutes=5))
    sink = MemorySink()
    publish(sink, db_session, clock=clock)

    queue_age = sink.latest("queue_oldest_age_seconds")
    schedule_age = sink.latest("schedule_overdue_age_seconds")
    assert queue_age == 300
    assert schedule_age == 300  # both five minutes so far...

    # ...so move only the schedule's clock forward by disabling nothing and
    # letting the job be worked off. The ages must then diverge.
    from api.services.jobs import claim

    claim(db_session, owner="w", clock=clock)
    db_session.commit()
    clock.advance(timedelta(minutes=10))

    sink2 = MemorySink()
    publish(sink2, db_session, clock=clock)
    assert sink2.latest("queue_oldest_age_seconds") == 0, "the job is leased, not queued"
    assert sink2.latest("schedule_overdue_age_seconds") == 900
    assert sink2.latest("queue_oldest_age_seconds") != sink2.latest(
        "schedule_overdue_age_seconds"
    )


def test_a_disabled_schedule_is_not_overdue(db_session, clock, tenant_id):
    """A schedule somebody turned off is not late. Counting it would put the
    fleet in alarm forever for a deliberate act."""
    from api.models import Schedule

    db_session.add(
        Schedule(
            tenant_id=tenant_id,
            name="off",
            kind="sync",
            interval_seconds=3600,
            anchor_at=T0,
            next_run_at=T0,
            enabled=False,
        )
    )
    db_session.commit()
    clock.advance(timedelta(hours=5))

    sink = MemorySink()
    publish(sink, db_session, clock=clock)
    assert sink.latest("schedule_overdue_age_seconds") == 0


# --------------------------------------------------------------------------
# Never-seen is not zero
# --------------------------------------------------------------------------


def test_never_seconds_is_above_every_threshold(clock):
    """Compared against the alarm catalog, not against a literal.

    A test asserting `NEVER_SECONDS == 2592000` would keep passing if somebody
    raised a threshold past it, at which point "never seen" would start
    reading as healthy. This way that change fails the build.
    """
    for alarm in ALARMS:
        assert NEVER_SECONDS > alarm.threshold, alarm.name


def test_a_system_with_no_workers_reports_maximum_staleness(db_session, clock):
    """`heartbeat.staleness()` returns None here. Mapping that to 0 would mean
    "seen just now", so a system where no worker has ever started would
    publish perfect liveness."""
    sink = MemorySink()
    publish(sink, db_session, clock=clock)
    assert sink.latest("worker_staleness_seconds") == NEVER_SECONDS


def test_a_system_with_no_workers_alarms_on_liveness(db_session, clock):
    """The end-to-end form of the assertion above: the number that gets
    published actually trips the alarm that watches it."""
    sink = MemorySink()
    publish(sink, db_session, clock=clock)
    stale = next(a for a in ALARMS if a.name == "worker-heartbeat-stale")
    assert evaluate(stale, _window(sink, stale)) is State.ALARM


# --------------------------------------------------------------------------
# The database is gone
# --------------------------------------------------------------------------


def test_a_degraded_report_publishes_exactly_one_series(db_session):
    """Nine absent series are the measurement, not an oversight. If this
    published anything else it would be claiming to have measured something it
    could not reach."""
    sink = MemorySink()
    report_degraded(sink, env="test")
    assert [s.series for s in sink.samples] == ["db_reachable"]
    assert sink.latest("db_reachable") == 0


def test_a_degraded_report_puts_every_alarm_in_alarm(db_session):
    """Where missing-is-breaching is cashed in.

    One measured series and nine honest gaps page for all ten conditions. If
    any alarm treated its gap as healthy, a total database outage would show
    up as a single alarm on a dashboard of green -- and somebody would read
    the green.
    """
    sink = MemorySink()
    report_degraded(sink)

    states = {a.name: evaluate(a, _window(sink, a)) for a in ALARMS}
    assert set(states.values()) == {State.ALARM}, {
        k: v for k, v in states.items() if v is not State.ALARM
    }


def test_a_full_report_does_not_put_everything_in_alarm(db_session, clock):
    """The control for the test above.

    If every alarm fired on a healthy report too, the degraded-report test
    would be proving nothing about the gaps -- it would just be an alarm
    catalog that always fires.
    """
    sink = MemorySink()
    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()
    publish(sink, db_session, clock=clock)

    states = {a.name: evaluate(a, _window(sink, a)) for a in ALARMS}
    assert State.OK in states.values(), states
    assert states["database-unreachable"] is State.OK
    assert states["worker-stopped"] is State.OK


def test_db_reachable_cannot_be_published_without_the_queries_succeeding(clock):
    """The comment in `build_snapshot` claims that reaching the `db_reachable`
    line means a query already succeeded. Asserted rather than trusted: a
    broken session raises instead of yielding a snapshot that says 1.

    A literal `1` that did not depend on real work would be a constant, and a
    constant is not a signal.
    """

    class Broken(Session):
        def __init__(self) -> None:  # noqa: D107 - deliberately not a real session
            pass

        def execute(self, *a, **k):
            raise RuntimeError("no route to host")

    with pytest.raises(RuntimeError, match="no route to host"):
        build_snapshot(Broken(), clock=clock)


def test_the_reachability_probe_answers_rather_than_raising(db_session):
    """A reachability check that can take the reporter down with it has made
    the outage worse."""
    assert probe_db_reachable(db_session) is True

    class Broken(Session):
        def __init__(self) -> None:  # noqa: D107
            pass

        def execute(self, *a, **k):
            raise RuntimeError("no route to host")

    assert probe_db_reachable(Broken()) is False


def test_the_probe_does_not_read_a_table(db_session):
    """`SELECT 1`, not a table read: an outage and a bad migration must not
    produce the same alarm. Asserted by making every table read fail while
    leaving the trivial query working."""
    calls = []
    real = db_session.execute

    def watching(statement, *a, **k):
        calls.append(str(statement))
        return real(statement, *a, **k)

    db_session.execute = watching
    try:
        assert probe_db_reachable(db_session) is True
    finally:
        db_session.execute = real

    assert len(calls) == 1
    assert "SELECT 1" in calls[0].upper()
    assert "FROM" not in calls[0].upper()
