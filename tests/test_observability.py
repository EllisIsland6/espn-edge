"""The ten series, and the one rule: zero is a value, absence is not.

The test this file exists for is
`test_a_zero_is_published_not_skipped` and its control removal. Everything
else guards the dimension policy, which is the other half of Phase 41's
explicit guarantee ("no credential/prompt/payload/member data or
high-cardinality tenant metric dimension").

A note on what these tests are allowed to assume. Several of them compare the
module's own constants against each other rather than against literals --
`REFUSED_DIMENSIONS` against `ALLOWED_DIMENSIONS`, `SERIES` against the alarm
catalog. That is deliberate: a test written against a literal keeps passing
when the constant it was guarding is edited, which makes it a test of the
literal and not of the code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.services.observability import (
    ALLOWED_DIMENSIONS,
    REFUSED_DIMENSIONS,
    SERIES,
    BadValue,
    ForbiddenDimension,
    IncompleteReport,
    Kind,
    MemorySink,
    NullSink,
    Sample,
    Unit,
    UnknownSeries,
    emit,
    report,
    series,
)

T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


def full_snapshot(**overrides) -> dict[str, float]:
    """A complete snapshot of zeroes, so a test can vary one value.

    Zeroes and not ones: a helper of plausible-looking numbers would hide a
    test that forgot to set the value it claims to be about.
    """
    snap = {s.name: 0 for s in SERIES}
    snap["db_reachable"] = 1
    snap.update(overrides)
    return snap


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def test_a_zero_is_published_not_skipped():
    """The defect this module exists to prevent.

    A counter written only when it is non-zero has no datapoint during an
    outage. The alarm then sits in INSUFFICIENT_DATA, which CloudWatch reads
    as not-alarming, so total silence reads as health -- exactly backwards.
    """
    sink = MemorySink()
    emit(sink, "jobs_claimed", 0, at=T0)
    assert sink.has("jobs_claimed")
    assert sink.values("jobs_claimed") == [0.0]


def test_absence_and_zero_are_different_observations():
    """The assertion the whole phase rests on, stated as a comparison.

    Two sinks, two stories: one worker said "I claimed nothing", the other
    said nothing at all. If these were indistinguishable there would be no
    liveness signal anywhere in the system.
    """
    said_zero = MemorySink()
    emit(said_zero, "jobs_claimed", 0, at=T0)

    said_nothing = MemorySink()

    assert said_zero.has("jobs_claimed") is True
    assert said_nothing.has("jobs_claimed") is False
    assert said_zero.latest("jobs_claimed") == 0.0
    assert said_nothing.latest("jobs_claimed") is None


def test_report_refuses_a_snapshot_that_leaves_a_series_out():
    """"Nothing happened so I published nothing" is a crash, not a gap."""
    sink = MemorySink()
    incomplete = full_snapshot()
    del incomplete["jobs_claimed"]

    with pytest.raises(IncompleteReport) as exc:
        report(sink, incomplete, at=T0)

    assert "jobs_claimed" in str(exc.value)
    # And nothing was published: a partial report is worse than none, because
    # the series that did arrive make the window look measured.
    assert sink.samples == []


def test_report_publishes_every_series_including_the_zeroes():
    sink = MemorySink()
    samples = report(sink, full_snapshot(), at=T0)

    assert len(samples) == len(SERIES)
    assert {s.series for s in sink.samples} == {s.name for s in SERIES}
    # Nine zeroes and one reachability 1. The zeroes are the point.
    assert sorted(s.value for s in sink.samples) == [0.0] * 9 + [1.0]


def test_report_refuses_an_undeclared_series():
    """A name nobody alarms on is worse than a missing one: it looks installed."""
    with pytest.raises(UnknownSeries):
        report(MemorySink(), full_snapshot(queue_dpeth=3), at=T0)


def test_report_order_is_the_declared_order_not_the_dict_order():
    """So two reports are comparable, and a diff of them means something."""
    sink = MemorySink()
    backwards = dict(reversed(list(full_snapshot().items())))
    report(sink, backwards, at=T0)
    assert [s.series for s in sink.samples] == [s.name for s in SERIES]


def test_every_sample_in_a_report_carries_one_timestamp():
    """A report is one observation. Samples drifting apart by a few
    milliseconds would make a dashboard's periods disagree with each other."""
    sink = MemorySink()
    report(sink, full_snapshot())
    assert len({s.at for s in sink.samples} ) == 1


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------


def test_there_are_exactly_ten_series():
    """Phase 41's change surface says ten. Ten is a budget, not a target:
    each series costs a CloudWatch subscription forever."""
    assert len(SERIES) == 10


def test_series_names_are_unique():
    names = [s.name for s in SERIES]
    assert len(set(names)) == len(names)


def test_every_series_can_say_what_an_operator_would_do_with_it():
    """A series nobody can write a sentence about is a series nobody acts on."""
    for s in SERIES:
        assert len(s.reads_as) > 40, s.name


def test_an_unknown_series_is_refused_rather_than_created():
    with pytest.raises(UnknownSeries):
        series("worker_tikcs")
    with pytest.raises(UnknownSeries):
        emit(MemorySink(), "made_up", 1)


def test_boolean_series_refuse_anything_but_zero_or_one():
    """`db_reachable=0.5` is not a degraded database, it is a bug."""
    with pytest.raises(BadValue):
        emit(MemorySink(), "db_reachable", 0.5)
    emit(MemorySink(), "db_reachable", 0)
    emit(MemorySink(), "db_reachable", 1)


def test_counts_and_durations_refuse_negatives():
    with pytest.raises(BadValue):
        emit(MemorySink(), "queue_depth", -1)
    with pytest.raises(BadValue):
        emit(MemorySink(), "queue_oldest_age_seconds", -0.5)


def test_non_finite_values_are_refused():
    """A NaN is a division somebody did not guard, and it publishes silently
    as a gap in the graph -- the one thing this module must not produce."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(BadValue):
            emit(MemorySink(), "queue_depth", bad)


def test_a_bool_is_not_a_number():
    """`True` is an int in Python, so `emit(..., True)` would publish 1.0 and
    look fine. A boolean reaching a metric value means a caller passed a
    predicate where a measurement belongs."""
    with pytest.raises(BadValue):
        emit(MemorySink(), "db_reachable", True)


# --------------------------------------------------------------------------
# Dimensions: the redaction boundary
# --------------------------------------------------------------------------


def test_tenant_id_is_refused_as_a_dimension():
    """Phase 41's explicit guarantee, named. Not because it is useless -- it is
    the most useful dimension there is -- but because it multiplies the series
    count by the tenant count."""
    with pytest.raises(ForbiddenDimension) as exc:
        emit(MemorySink(), "queue_depth", 1, tenant_id="7")
    assert "tenant" in str(exc.value)


@pytest.mark.parametrize("key", sorted(REFUSED_DIMENSIONS))
def test_every_named_refused_dimension_is_actually_refused(key):
    """Parametrised over the module's own table, so adding a name to
    REFUSED_DIMENSIONS without enforcing it fails here."""
    with pytest.raises(ForbiddenDimension):
        emit(MemorySink(), "queue_depth", 1, **{key: "x"})


def test_the_refused_and_allowed_sets_do_not_overlap():
    """A key in both tables would resolve by whichever check ran first."""
    assert not (set(REFUSED_DIMENSIONS) & ALLOWED_DIMENSIONS)


def test_an_unlisted_dimension_is_refused_rather_than_allowed():
    """Default-deny. A new dimension is a cardinality decision, so it is made
    in the module and not at a call site."""
    with pytest.raises(ForbiddenDimension):
        emit(MemorySink(), "queue_depth", 1, region="us-east-1")


def test_a_dimension_value_must_be_a_short_closed_token():
    """Free text in a dimension is how an error message or a payload fragment
    becomes part of a metric name -- and a metric name is not redactable after
    the fact."""
    with pytest.raises(ForbiddenDimension):
        emit(MemorySink(), "queue_depth", 1, env="prod; DROP TABLE jobs")
    with pytest.raises(ForbiddenDimension):
        emit(MemorySink(), "queue_depth", 1, env="x" * 64)
    with pytest.raises(ForbiddenDimension):
        emit(MemorySink(), "queue_depth", 1, env=7)
    emit(MemorySink(), "queue_depth", 1, env="prod")


def test_allowed_dimensions_reach_the_sample_sorted():
    sample = emit(MemorySink(), "queue_depth", 1, kind="sync", env="prod")
    assert sample.dimensions == (("env", "prod"), ("kind", "sync"))


def test_a_sample_carries_only_numbers_and_closed_tokens():
    """The structural redaction claim: there is nowhere in a Sample to put a
    payload, an error string or an identifier. Asserted over the dataclass's
    own fields rather than over one instance, so a field added later without
    thought fails here."""
    allowed = {"series", "value", "unit", "at", "dimensions"}
    assert set(Sample.__dataclass_fields__) == allowed


# --------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------


def test_the_null_sink_is_the_resting_state_and_changes_nothing():
    """Telemetry that is off must not require every caller to branch on None,
    and must not return a decision -- "telemetry failure never authorizes
    provider/AI work" starts with telemetry never authorizing anything."""
    assert emit(NullSink(), "queue_depth", 3).value == 3.0
    assert report(NullSink(), full_snapshot()) is not None


def test_a_sink_that_raises_raises_rather_than_being_swallowed_here():
    """This module does not decide what a publish failure means; the worker
    does, and it decides to keep working (`worker._record_tick`). Swallowing
    it here would deny the caller that choice and hide a broken sink."""
    sink = MemorySink()
    sink.fail_next = True
    with pytest.raises(RuntimeError):
        emit(sink, "queue_depth", 1)


def test_gauges_and_deltas_are_both_declared():
    """Both kinds exist, so the "what does a gap mean" distinction in the
    docstring is describing the real catalog rather than a hypothetical one."""
    kinds = {s.kind for s in SERIES}
    assert kinds == {Kind.GAUGE, Kind.DELTA}


def test_units_are_from_the_narrowed_vocabulary():
    assert {s.unit for s in SERIES} <= {Unit.COUNT, Unit.SECONDS, Unit.BOOLEAN}
