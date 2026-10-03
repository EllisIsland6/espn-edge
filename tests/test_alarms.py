"""Alarm definitions, and the one field this phase is about.

`treat_missing_data` decides what a *dead* system looks like. AWS's default
(`missing`) retains the previous state and leaves a never-fired alarm in
INSUFFICIENT_DATA, which every operator reads as "probably fine, the metric is
new". So a total outage -- the case where no datapoint arrives at all -- is the
one case the default handles wrongly, and it handles it by reporting health.

Every assertion here is written against the module's own constants rather than
against the string "breaching". A test comparing to its own literal would keep
passing if `MISSING_IS_BREACHING` were changed, which would make it a test of
the literal and not of the catalog.
"""

from __future__ import annotations

import dataclasses

import pytest

from api.services.alarms import (
    ALARMS,
    MISSING_IS_BREACHING,
    REFUSED_MISSING_TREATMENTS,
    AlarmSpec,
    Comparison,
    State,
    coverage,
    evaluate,
)
from api.services.observability import SERIES, UnknownSeries

NAMESPACE = "EspnEdge/Test"


# --------------------------------------------------------------------------
# The field
# --------------------------------------------------------------------------


def test_every_self_published_alarm_treats_missing_as_breaching():
    """Phase 41's acceptance criterion, as one assertion over the catalog.

    Control removal: flipping any single entry to `notBreaching` fails this,
    which is the only reason to believe it is doing anything.
    """
    wrong = [a.name for a in ALARMS if a.treat_missing_data != MISSING_IS_BREACHING]
    assert wrong == []


def test_the_rendered_api_call_carries_the_setting():
    """The catalog being right is worthless if the rendering drops the field."""
    for alarm in ALARMS:
        rendered = alarm.as_put_metric_alarm(namespace=NAMESPACE)
        assert rendered["TreatMissingData"] == MISSING_IS_BREACHING, alarm.name


def test_treat_missing_data_has_no_default():
    """Structural, not stylistic.

    With a default of "breaching" the catalog test above would be testing the
    default -- it could not fail -- and an author adding an alarm would never
    have to think about the field. Asserted against the dataclass's own
    metadata so it survives a reorder.
    """
    field = AlarmSpec.__dataclass_fields__["treat_missing_data"]
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING


def test_the_three_wrong_treatments_are_named_with_their_reasons():
    """So a future author reaching for one finds out why not, here, rather
    than discovering it during an outage."""
    assert set(REFUSED_MISSING_TREATMENTS) == {"notBreaching", "ignore", "missing"}
    assert MISSING_IS_BREACHING not in REFUSED_MISSING_TREATMENTS
    for reason in REFUSED_MISSING_TREATMENTS.values():
        assert len(reason) > 30


# --------------------------------------------------------------------------
# evaluate(): the rule in code, not only in configuration
# --------------------------------------------------------------------------


def _spec(**over) -> AlarmSpec:
    base = dict(
        name="t",
        series_name="queue_depth",
        comparison=Comparison.GREATER_THAN,
        threshold=10,
        datapoints_to_alarm=3,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="x" * 40,
    )
    base.update(over)
    return AlarmSpec(**base)


def test_an_all_gap_window_alarms():
    """The dead-system case. This is the whole point: nothing arrived, and the
    alarm fires anyway."""
    assert evaluate(_spec(), [None, None, None]) is State.ALARM


def test_an_empty_history_alarms():
    """A brand-new alarm with no datapoints at all. Under AWS's default this
    sits in INSUFFICIENT_DATA forever and reads as health."""
    assert evaluate(_spec(), []) is State.ALARM


def test_evaluate_never_returns_insufficient_data():
    """With missing treated as breaching there is no state left for "we do not
    know", so there is no return path a caller could mistake for health.

    Swept across every alarm in the catalog and every shape of history,
    because a single hand-picked case would not establish the claim.
    """
    histories = [
        [],
        [None],
        [None, None, None, None, None, None],
        [0.0],
        [0.0] * 6,
        [1.0] * 6,
        [10_000.0] * 6,
        [None, 0.0, None, 1.0, None, 10_000.0],
    ]
    for alarm in ALARMS:
        for history in histories:
            assert evaluate(alarm, list(history)) is not State.INSUFFICIENT_DATA, (
                alarm.name,
                history,
            )


def test_a_gap_inside_a_healthy_run_does_not_clear_the_breach_requirement():
    """A single gap among healthy samples is not an alarm -- `all()` -- but the
    gap itself counts as a breach rather than as a healthy datapoint. Pinned
    because the two readings differ only when the rest of the window breaches.
    """
    spec = _spec(datapoints_to_alarm=2)
    assert evaluate(spec, [0.0, None]) is State.OK  # one breach of two
    assert evaluate(spec, [99.0, None]) is State.ALARM  # both breach


def test_a_short_history_is_padded_with_breaches_not_with_health():
    """What `evaluate` actually does, stated exactly.

    It does not fabricate an alarm from a thin history; it refuses to read the
    gaps as healthy. Three required datapoints and one healthy sample is two
    breaches out of three, so: OK. An earlier comment in `alarms.py` claimed
    the stronger behaviour and this test exists because of it.
    """
    spec = _spec(datapoints_to_alarm=3)
    assert evaluate(spec, [0.0]) is State.OK
    # ...but one *breaching* sample plus two gaps is three out of three.
    assert evaluate(spec, [99.0]) is State.ALARM


def test_less_than_alarms_read_zero_as_a_breach_and_absence_as_one_too():
    """`db_reachable` and the worker liveness alarms are all LESS_THAN 1, so
    this is the shape that has to distinguish "ran and failed" from "did not
    run" -- and treat both as bad, for different reasons."""
    spec = _spec(series_name="db_reachable", comparison=Comparison.LESS_THAN,
                 threshold=1, datapoints_to_alarm=2)
    assert evaluate(spec, [0.0, 0.0]) is State.ALARM  # ran, could not reach
    assert evaluate(spec, [None, None]) is State.ALARM  # nothing ran
    assert evaluate(spec, [1.0, 1.0]) is State.OK


def test_only_the_last_n_periods_are_considered():
    """An alarm that never recovers is an alarm that gets muted."""
    spec = _spec(datapoints_to_alarm=2)
    assert evaluate(spec, [99.0, 99.0, 99.0, 0.0, 0.0]) is State.OK


def test_evaluate_refuses_a_spec_it_does_not_implement():
    """A spec built outside the catalog with a different treatment would
    otherwise be evaluated under rules nobody reviewed."""
    with pytest.raises(ValueError):
        evaluate(_spec(treat_missing_data="notBreaching"), [None])


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------


def test_every_series_has_exactly_one_alarm():
    """A series with no alarm is a graph nobody looks at. Asserted as a
    bijection so neither side can drift."""
    uncovered = [name for name, alarm in coverage().items() if alarm is None]
    assert uncovered == []
    assert len({a.series_name for a in ALARMS}) == len(ALARMS) == len(SERIES)


def test_alarm_names_are_unique():
    names = [a.name for a in ALARMS]
    assert len(set(names)) == len(names)


def test_an_alarm_cannot_name_a_series_nobody_publishes():
    """Validated in __post_init__, so a typo is a build failure rather than an
    alarm that looks installed and never fires."""
    with pytest.raises(UnknownSeries):
        _spec(series_name="queue_dpeth")


def test_every_alarm_has_a_runbook_line():
    """An alarm with no runbook wakes somebody who then has to guess."""
    for alarm in ALARMS:
        assert len(alarm.runbook) > 40, alarm.name


def test_no_alarm_uses_a_high_resolution_period():
    """Phase 41's cost guarantee: the recurring spend stays inside Stage 3's
    $0.50 reserve, and sub-minute periods are billed differently."""
    for alarm in ALARMS:
        assert alarm.period_seconds >= 60, alarm.name


def test_every_alarm_can_state_how_long_an_outage_stays_invisible():
    """`datapoints_to_alarm * period` is the detection delay. Worth being able
    to say out loud, and worth bounding: an hour of silence before anybody
    hears about it is not monitoring."""
    for alarm in ALARMS:
        assert 0 < alarm.evaluation_window_seconds <= 3600, alarm.name


def test_the_rendered_shape_has_the_fields_cloudwatch_requires():
    """Rendering is not calling -- there is no boto3 here. But a dict missing
    a required key is a Phase 43 failure discovered against a live account,
    which is the expensive place to discover it."""
    required = {
        "AlarmName",
        "Namespace",
        "MetricName",
        "Statistic",
        "ComparisonOperator",
        "Threshold",
        "Period",
        "EvaluationPeriods",
        "TreatMissingData",
    }
    for alarm in ALARMS:
        rendered = alarm.as_put_metric_alarm(namespace=NAMESPACE)
        assert required <= set(rendered), alarm.name
        assert rendered["Namespace"] == NAMESPACE


def test_deltas_are_summed_and_gauges_are_maximum():
    """The statistic follows the kind, and getting this wrong is silent.

    The first version of this test asserted the rule the code had -- statistic
    by *unit* -- and passed, while that rule summed `queue_depth` and
    `jobs_poisoned`, which are gauges. A depth of 40 sampled five times in a
    period would have evaluated as 200 and alarmed on nothing at all. The test
    was green and established the bug.

    So this asserts the property rather than the implementation: a delta
    accumulates and must be summed, a gauge describes an instant and the worst
    instant is the one that matters. And it asserts that both branches are
    actually exercised by the catalog, because a rule only one side of which
    is reachable is a rule nobody has tested.
    """
    from api.services.observability import Kind, series

    seen = set()
    for alarm in ALARMS:
        stat = alarm.as_put_metric_alarm(namespace=NAMESPACE)["Statistic"]
        kind = series(alarm.series_name).kind
        assert stat == ("Sum" if kind is Kind.DELTA else "Maximum"), alarm.name
        seen.add(stat)
    assert seen == {"Sum", "Maximum"}


def test_no_gauge_is_ever_summed():
    """The same claim from the other side, named so a reviewer can find it.

    Stated separately because the test above would still pass if the catalog
    lost every gauge -- and `queue_depth` being summed is the specific defect
    that shipped.
    """
    from api.services.observability import Kind, series

    for alarm in ALARMS:
        if series(alarm.series_name).kind is Kind.GAUGE:
            rendered = alarm.as_put_metric_alarm(namespace=NAMESPACE)
            assert rendered["Statistic"] != "Sum", alarm.name
