"""Collect the ten series and publish them, including when the database is gone.

THE HONEST ANSWER TO "WE COULD NOT MEASURE"
-------------------------------------------
The healthy path reads nine numbers out of the database and publishes all ten.
The interesting path is the other one: the database is unreachable, so nine of
the ten cannot be measured at all. There are three things to do and two of them
are wrong.

Publish zeroes for the nine. Wrong, and the worst of the three: `queue_depth=0`
and `queue_oldest_age_seconds=0` are the healthiest readings those series can
take, so a total database outage would publish a clean bill of health. This is
the defect pattern this whole phase exists to remove, moved one layer down
where no alarm setting can catch it.

Publish a large sentinel for the nine. Also wrong, more defensibly: it pages
somebody for nine conditions we did not observe, and the runbook lines are all
false. An operator chasing `outbox-undelivered` during a database outage is
being actively misled.

Publish `db_reachable=0` and leave the nine absent. Correct, and only correct
*because* every alarm treats missing as breaching -- which is what buys the
right to say "unknown" and still get paged. The gap is not an omission here,
it is the measurement: nothing ran that could have measured those nine.

That is why `report_degraded` exists beside `observability.report`, and why it
is a named function with a fixed payload rather than a partial-snapshot
parameter. `report()` still refuses incomplete snapshots, so no caller can
dribble out whatever it happens to have; the one documented exception is this
one, and `tests/test_snapshot.py` asserts that it publishes exactly one series
and that the other nine alarms reach ALARM on the resulting window.

WHY STALENESS IS NOT ZERO WHEN THERE ARE NO WORKERS
---------------------------------------------------
`heartbeat.staleness()` returns `None` for an empty table. Mapping that to `0`
would mean "seen just now", so a system where no worker has ever started would
publish perfect liveness. It maps to `NEVER_SECONDS` instead, which is above
every threshold in `alarms.py` by construction -- and the test that asserts
that comparison is the reason it stays true when a threshold changes.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import heartbeat, jobs, outbox, schedules
from .jobs import POISON, Clock, _now
from .observability import Sink, emit, report

#: What a never-seen timestamp publishes. Thirty days: above every threshold in
#: `alarms.py`, and recognisable in a graph as "never" rather than as a real
#: measurement somebody might try to interpret.
#:
#: `tests/test_snapshot.py::test_never_seconds_is_above_every_threshold`
#: compares this against the alarm catalog rather than against a literal, so
#: raising a threshold past it fails the build instead of quietly making this
#: value read as healthy.
NEVER_SECONDS = 30 * 24 * 60 * 60


def _seconds(value: timedelta | None) -> float:
    return NEVER_SECONDS if value is None else value.total_seconds()


def build_snapshot(session: Session, *, clock: Clock = _now) -> dict[str, float]:
    """Read all ten numbers. Every key is present, including the zeroes.

    Takes a session rather than opening one: the caller owns the transaction,
    and a reporter that opened its own would be a second connection appearing
    in the pool every period forever.

    `take_progress` is called here and nowhere else, because it has a side
    effect -- it advances the watermark. Calling it twice in one period would
    report the second window as empty, which looks exactly like a stopped
    worker.
    """
    ticks, claims, failures = heartbeat.take_progress(session, clock=clock)
    poisoned = jobs.count_by_state(session, POISON)
    return {
        "worker_ticks": ticks,
        "jobs_claimed": claims,
        "jobs_failed": failures,
        "jobs_poisoned": poisoned,
        "queue_depth": jobs.queue_depth(session, clock=clock),
        "queue_oldest_age_seconds": jobs.oldest_due_age(
            session, clock=clock
        ).total_seconds(),
        "schedule_overdue_age_seconds": schedules.oldest_overdue_age(
            session, clock=clock
        ).total_seconds(),
        "outbox_undelivered_age_seconds": outbox.undelivered_age(
            session, clock=clock
        ).total_seconds(),
        "worker_staleness_seconds": _seconds(
            heartbeat.staleness(session, clock=clock)
        ),
        # Reaching this line means a query already succeeded, which is the
        # measurement. A literal 1 that did not depend on a real query would
        # be a constant, and a constant is not a signal -- see
        # `probe_db_reachable` in the fault harness.
        "db_reachable": 1,
    }


def publish(sink: Sink, session: Session, *, clock: Clock = _now, **dimensions: str):
    """Build and publish a full report. Raises if a series is missing.

    `at=now` is passed explicitly, and the omission of it was a real defect:
    `clock` reached the measurement but not the timestamp, so `report` fell
    through to `datetime.now(UTC)` and every sample was stamped with wall-clock
    time while describing state read at clock time. Fifteen tests passed,
    because none of them asserted on `Sample.at`; the fault harness found it on
    its first run, because a harness that evaluates a time grid cannot miss it.

    Outside tests it matters more: CloudWatch buckets by timestamp, so a
    reporter that is behind, replaying or backfilling would file its samples
    in the wrong periods and the alarm windows would be built from data that
    belongs elsewhere.
    """
    now = clock()
    return report(sink, build_snapshot(session, clock=clock), at=now, **dimensions)


def report_degraded(sink: Sink, *, clock: Clock = _now, **dimensions: str):
    """Publish `db_reachable=0` and nothing else.

    The nine absent series are the honest reading, not an oversight. See the
    module docstring. Every alarm treats missing as breaching, so this window
    pages for all ten conditions while claiming to have measured only one.
    """
    return emit(sink, "db_reachable", 0, at=clock(), **dimensions)


def probe_db_reachable(session: Session) -> bool:
    """Did a trivial query succeed?

    `SELECT 1` and not a table read: a reachability probe must not be able to
    fail because of a schema problem, or an outage and a bad migration become
    the same alarm.

    Returns a bool rather than raising, because the caller's next move is to
    publish a number either way -- and a reachability check that can take the
    reporter down with it has made the outage worse.
    """
    try:
        return session.execute(text("SELECT 1")).scalar_one() == 1
    except Exception:  # noqa: BLE001 - any driver error is unreachable
        return False
