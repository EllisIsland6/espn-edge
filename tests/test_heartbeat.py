"""Worker liveness: the row that says "I woke up and did nothing".

A probe ran `drain()` against an empty queue and counted every durable row that
changed. None did. So an idle worker and a worker that died an hour ago were
byte-identical from outside the host, and nothing could have told them apart.

The first two tests are that probe, now with an answer. Everything else guards
the two-timestamp design, which is the only shape that can express
live-but-not-claiming: one timestamp says the loop ran, the other says it got
work, and the interesting failure is a fresh first beside a stale second.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db import SessionLocal
from api.models import WorkerHeartbeat
from api.services.heartbeat import idle_age, record_tick, staleness, take_progress

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


def _row(session, owner: str = "w1") -> WorkerHeartbeat:
    """Re-read from the database, expiring first.

    Three worker tests in Phase 39 read a stale value off the identity map and
    passed while asserting the wrong thing. The flush in `record_tick` does not
    expire the instance, so without this the object still holds the values it
    was constructed with.
    """
    session.expire_all()
    return session.get(WorkerHeartbeat, owner)


# --------------------------------------------------------------------------
# The probe, answered
# --------------------------------------------------------------------------


def test_an_idle_tick_leaves_a_durable_trace(db_session, clock):
    """What the probe found missing.

    Nothing was claimed, so under any "record the work" design this tick would
    be invisible. It is the tick that matters most: it is the difference
    between idle and dead.
    """
    record_tick(db_session, "w1", claimed=0, clock=clock)
    db_session.commit()

    row = _row(db_session)
    assert row is not None
    assert row.ticks == 1
    assert row.claims == 0


def test_an_idle_tick_advances_last_seen_but_not_last_claimed(db_session, clock):
    """The two-timestamp design, in one assertion.

    A single `last_updated_at` could not express this state, and that is the
    reason there are two columns rather than one.
    """
    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()
    claimed_at = _row(db_session).last_claimed_at

    clock.advance(timedelta(minutes=30))
    record_tick(db_session, "w1", claimed=0, clock=clock)
    db_session.commit()

    row = _row(db_session)
    assert row.last_seen_at.replace(tzinfo=UTC) == clock.now
    assert row.last_claimed_at.replace(tzinfo=UTC) == claimed_at.replace(tzinfo=UTC)
    assert row.last_seen_at > row.last_claimed_at


def test_live_but_not_claiming_is_distinguishable_from_both_neighbours(
    db_session, clock
):
    """Three states, three readings. The middle one is the one that hides.

    dead:      last_seen stale
    idle/live: last_seen fresh, last_claimed stale   <- this one
    working:   both fresh

    Asserted together because each state is only interesting relative to the
    others -- a test of the middle state alone would pass on an implementation
    that reported it for all three.
    """
    record_tick(db_session, "working", claimed=1, clock=clock)
    record_tick(db_session, "dead", claimed=1, clock=clock)
    db_session.commit()

    clock.advance(timedelta(hours=1))
    record_tick(db_session, "working", claimed=1, clock=clock)
    record_tick(db_session, "live-idle", claimed=0, clock=clock)
    db_session.commit()

    db_session.expire_all()
    rows = {r.owner: r for r in db_session.query(WorkerHeartbeat).all()}

    fresh = clock.now
    assert rows["working"].last_seen_at.replace(tzinfo=UTC) == fresh
    assert rows["working"].last_claimed_at.replace(tzinfo=UTC) == fresh

    assert rows["live-idle"].last_seen_at.replace(tzinfo=UTC) == fresh
    assert rows["live-idle"].last_claimed_at is None

    assert rows["dead"].last_seen_at.replace(tzinfo=UTC) < fresh


def test_a_worker_that_never_claimed_has_a_null_last_claimed(db_session, clock):
    """Nullable deliberately. A default of "now" would have made a worker that
    has never done anything look like one that just finished a job."""
    record_tick(db_session, "w1", claimed=0, clock=clock)
    db_session.commit()
    assert _row(db_session).last_claimed_at is None


# --------------------------------------------------------------------------
# One row per worker
# --------------------------------------------------------------------------


def test_repeated_ticks_update_one_row_rather_than_appending(db_session, clock):
    """A second row for the same worker would make "is it alive" depend on
    which row happened to be read first."""
    for _ in range(5):
        clock.advance(timedelta(minutes=1))
        record_tick(db_session, "w1", claimed=0, clock=clock)
    db_session.commit()

    assert db_session.query(WorkerHeartbeat).count() == 1
    assert _row(db_session).ticks == 5


def test_two_workers_keep_two_rows(db_session, clock):
    record_tick(db_session, "w1", claimed=1, clock=clock)
    record_tick(db_session, "w2", claimed=0, clock=clock)
    db_session.commit()
    assert db_session.query(WorkerHeartbeat).count() == 2


def test_first_seen_does_not_move(db_session, clock):
    """So "how long has this worker been up" is answerable, which is what
    turns a crash loop into a visible pattern rather than a fresh-looking
    worker every time."""
    record_tick(db_session, "w1", clock=clock)
    db_session.commit()
    first = _row(db_session).first_seen_at

    clock.advance(timedelta(hours=2))
    record_tick(db_session, "w1", clock=clock)
    db_session.commit()
    assert _row(db_session).first_seen_at.replace(tzinfo=UTC) == first.replace(
        tzinfo=UTC
    )


def test_counters_accumulate_across_ticks(db_session, clock):
    record_tick(db_session, "w1", claimed=1, failed=0, clock=clock)
    record_tick(db_session, "w1", claimed=1, failed=1, clock=clock)
    record_tick(db_session, "w1", claimed=0, failed=0, clock=clock)
    db_session.commit()

    row = _row(db_session)
    assert (row.ticks, row.claims, row.failures) == (3, 2, 1)


def test_counts_cannot_be_negative(db_session, clock):
    """`claimed` is a count, not an adjustment. A caller passing -1 is doing
    arithmetic the queue owns."""
    with pytest.raises(ValueError):
        record_tick(db_session, "w1", claimed=-1, clock=clock)


# --------------------------------------------------------------------------
# Freshness: None is not zero
# --------------------------------------------------------------------------


def test_staleness_is_none_when_no_worker_has_ever_checked_in(db_session, clock):
    """Not `timedelta(0)`.

    Zero means "seen just now", so a system where no worker has ever started
    would report perfect liveness -- the INSUFFICIENT_DATA-reads-as-health
    mistake moved one layer down, where no alarm setting could catch it.
    """
    assert staleness(db_session, clock=clock) is None
    assert idle_age(db_session, clock=clock) is None


def test_staleness_measures_the_newest_heartbeat_not_the_oldest(db_session, clock):
    """One worker of three dying should not make the fleet look dead. The
    reverse case -- one surviving while the rest are stuck -- is what
    `idle_age` is for, and the two are different questions."""
    record_tick(db_session, "old", clock=clock)
    db_session.commit()
    clock.advance(timedelta(hours=3))
    record_tick(db_session, "new", clock=clock)
    db_session.commit()

    assert staleness(db_session, clock=clock) == timedelta(0)


def test_idle_age_ignores_workers_that_never_claimed(db_session, clock):
    """A worker that has never claimed has no `last_claimed_at` to be stale.
    Counting it as "claimed at the beginning of time" would put the whole
    fleet in alarm the moment a fresh worker joined."""
    record_tick(db_session, "claimer", claimed=1, clock=clock)
    db_session.commit()
    clock.advance(timedelta(minutes=10))
    record_tick(db_session, "never", claimed=0, clock=clock)
    db_session.commit()

    assert idle_age(db_session, clock=clock) == timedelta(minutes=10)


def test_freshness_arithmetic_survives_a_naive_column_read(db_session, clock):
    """Phase 35, again. SQLite has no type carrying an offset, so a committed
    timestamp reads back naive and `now - stored` raises TypeError. The
    subtraction happening at all is the assertion."""
    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()
    db_session.expire_all()
    probe = SessionLocal()
    try:
        clock.advance(timedelta(minutes=5))
        assert staleness(probe, clock=clock) == timedelta(minutes=5)
        assert idle_age(probe, clock=clock) == timedelta(minutes=5)
    finally:
        probe.close()


# --------------------------------------------------------------------------
# The watermark
# --------------------------------------------------------------------------


def test_take_progress_returns_the_delta_and_then_zero(db_session, clock):
    """The deltas the metrics need, from counters that only go up.

    The second call returning zeroes is the important half: a reporter that
    re-reported the same ticks would make a stopped worker look busy forever.
    """
    record_tick(db_session, "w1", claimed=1, failed=0, clock=clock)
    record_tick(db_session, "w1", claimed=0, failed=1, clock=clock)
    db_session.commit()

    assert take_progress(db_session, clock=clock) == (2, 1, 1)
    db_session.commit()
    assert take_progress(db_session, clock=clock) == (0, 0, 0)


def test_zero_progress_is_a_real_answer_not_an_absent_one(db_session, clock):
    """A worker that ticked without claiming reports (ticks>0, claims=0).

    That tuple is the live-but-not-claiming signal as a number, and it is
    distinct from the no-workers-at-all case below.
    """
    record_tick(db_session, "w1", claimed=0, clock=clock)
    record_tick(db_session, "w1", claimed=0, clock=clock)
    db_session.commit()
    assert take_progress(db_session, clock=clock) == (2, 0, 0)


def test_progress_with_no_workers_at_all_is_also_zero(db_session, clock):
    """And indistinguishable from an idle worker, by design: the two are told
    apart by `worker_staleness_seconds`, not by the delta. Stated here so
    nobody later "fixes" this by returning None and reintroducing a gap."""
    assert take_progress(db_session, clock=clock) == (0, 0, 0)


def test_progress_sums_across_workers(db_session, clock):
    """The metric has no `owner` dimension -- a hostname is unbounded under
    autoscaling and `observability.py` refuses it -- so the fleet total is
    what gets published."""
    record_tick(db_session, "w1", claimed=1, clock=clock)
    record_tick(db_session, "w2", claimed=1, clock=clock)
    record_tick(db_session, "w2", claimed=0, clock=clock)
    db_session.commit()
    assert take_progress(db_session, clock=clock) == (3, 2, 0)


def test_ticks_are_conserved_across_windows(db_session, clock):
    """A tick arriving between the read and the write is not lost.

    The first version of this test asserted the split -- that the window
    reported (0,0,0) and the next reported both ticks. That was an assertion
    about scheduling, and it was wrong: the window reports the tick it saw and
    the late one lands in the next. Summing is the property that actually
    matters and the only one that is stable.

    The interleaving is forced through `heartbeat.update`, the SQLAlchemy
    construct `take_progress` calls, and `calls["n"]` asserts the interleaving
    happened -- a test that silently failed to interleave would pass while
    proving nothing.
    """
    import api.services.heartbeat as hb

    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()

    original = hb.update
    calls = {"n": 0}

    def interposing(*args, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            other = SessionLocal()
            try:
                record_tick(other, "w1", claimed=1, clock=clock)
                other.commit()
            finally:
                other.close()
        return original(*args, **kwargs)

    hb.update = interposing
    try:
        first = take_progress(db_session, clock=clock)
        db_session.commit()
    finally:
        hb.update = original

    assert calls["n"] == 1, "the interleaving did not happen; the test proved nothing"
    second = take_progress(db_session, clock=clock)
    db_session.commit()

    # Two ticks happened. Two ticks were reported, across the two windows.
    assert first[0] + second[0] == 2
    assert first[1] + second[1] == 2
    db_session.expire_all()
    assert _row(db_session).ticks == 2


def test_two_reporters_cannot_report_the_same_ticks_twice(db_session, clock):
    """What the `reported_ticks` guard is actually for.

    The first version of this test ran the two reporters one after the other
    and PASSED WITH THE GUARD DELETED -- because the second reporter's SELECT
    came after the first one's commit, so it read the advanced watermark and
    got the right answer from fresh state. The guard was never exercised. Same
    shape as the Phase 39 claim-race test, which also had to be forced to
    interleave before it tested anything.

    So the two reads are forced to both happen before either write, by running
    the second reporter's whole pass from inside the first one's UPDATE call.
    `calls["n"]` asserts the interleaving happened.

    The assertion is on the SUM: two ticks occurred, so two ticks may be
    reported in total. Which reporter gets them is a race and asserting that
    would be asserting the scheduling. Double-counting them is the bug --
    `worker_ticks` reported twice makes a stopped worker look busy.
    """
    import api.services.heartbeat as hb

    record_tick(db_session, "w1", claimed=1, clock=clock)
    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()

    original = hb.update
    calls = {"n": 0}
    other_result = {}

    def interposing(*args, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            # A second reporter reads AND writes while the first is between
            # its own read and write.
            other = SessionLocal()
            try:
                other_result["r"] = take_progress(other, clock=clock)
                other.commit()
            finally:
                other.close()
        return original(*args, **kwargs)

    hb.update = interposing
    try:
        mine = take_progress(db_session, clock=clock)
        db_session.commit()
    finally:
        hb.update = original

    assert calls["n"] == 1, "the interleaving did not happen; the test proved nothing"
    theirs = other_result["r"]
    assert mine[0] + theirs[0] == 2, (
        f"two ticks were reported {mine[0] + theirs[0]} times "
        f"(this reporter {mine}, the other {theirs})"
    )
    assert mine[1] + theirs[1] == 2


def test_the_watermark_is_durable_not_in_process(db_session, clock):
    """A restart is precisely the event these metrics exist to reveal, so the
    previous total cannot live in process memory. Asserted by reading it
    through a session that never saw the first call."""
    record_tick(db_session, "w1", claimed=1, clock=clock)
    db_session.commit()
    assert take_progress(db_session, clock=clock) == (1, 1, 0)
    db_session.commit()

    fresh = SessionLocal()
    try:
        assert take_progress(fresh, clock=clock) == (0, 0, 0)
    finally:
        fresh.close()
