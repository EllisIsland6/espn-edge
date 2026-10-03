"""Alarm definitions as data, and the rule that no data is bad news.

WHY THE DEFINITIONS ARE DATA
----------------------------
An alarm written as a CloudWatch API call is only checkable by making the call.
Written as a tuple of frozen records it is checkable by a test on a laptop, and
the thing most worth checking is a single field: `treat_missing_data`.

THE FIELD, AND WHY IT IS THE WHOLE PHASE
----------------------------------------
CloudWatch offers four readings of "no datapoint":

    notBreaching  -> treat the gap as OK
    breaching     -> treat the gap as ALARM
    ignore        -> keep the previous state
    missing       -> the default; retain state, and a brand-new alarm sits in
                     INSUFFICIENT_DATA

The default is the dangerous one. An alarm watching a worker that has *died*
receives no datapoints at all, so it never evaluates to ALARM -- it goes
INSUFFICIENT_DATA and stays there, and a dashboard full of grey is read by
every operator on earth as "probably fine, the metric is new". The outage is
invisible precisely because it is total.

So every alarm this repository publishes sets `breaching`, and
`tests/test_alarms.py` asserts it over the whole catalog. Flipping one entry
to `notBreaching` fails that test -- which is the only reason to believe the
test is doing anything.

`evaluate()` carries the same rule in code, because the rule has to hold twice:
once in the configuration AWS will enforce, and once in the local fault harness
that has no AWS to enforce it. A function that returned OK for an absent
datapoint would make the harness agree with a broken config.

NOTHING HERE CALLS AWS
----------------------
`as_put_metric_alarm()` renders the API shape as a dict. Rendering is not
calling. No boto3 import, no credentials, no network -- the dict is evidence
that can be diffed in a review, and Phase 43 hands it to a client.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .observability import SERIES, Kind, series

#: The only legal reading of a missing datapoint for an alarm we publish.
#:
#: Named rather than inlined so the invariant test can reference the same
#: constant the catalog does -- a test comparing against its own literal
#: "breaching" would still pass if this constant were changed to something
#: else, which is the sort of green check that establishes nothing.
MISSING_IS_BREACHING = "breaching"

#: The three readings that are refused, with the reason each is wrong here.
REFUSED_MISSING_TREATMENTS = {
    "notBreaching": "a dead worker publishes nothing; this would call that OK",
    "ignore": "retains the last state, so an outage inherits the health that "
    "preceded it",
    "missing": "the AWS default; a never-yet-fired alarm sits in "
    "INSUFFICIENT_DATA, which reads as health",
}


class Comparison(Enum):
    """CloudWatch comparison operators, narrowed to the two shapes used."""

    GREATER_THAN = "GreaterThanThreshold"
    LESS_THAN = "LessThanThreshold"


class State(Enum):
    """The tri-state an evaluation can reach.

    `INSUFFICIENT_DATA` exists in this enum so that the harness can *show* the
    state AWS would have reached, and show that we do not act on it as health.
    It is never returned by `evaluate()`: see that function.
    """

    OK = "OK"
    ALARM = "ALARM"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class AlarmSpec:
    """One alarm. Every field is something an operator would ask about."""

    name: str
    #: Must name a declared series. Validated in `__post_init__`, because an
    #: alarm on a series nobody publishes is an alarm that never fires and
    #: looks installed.
    series_name: str
    comparison: Comparison
    threshold: float
    #: How many consecutive periods must breach. One period is a flap; three
    #: is a condition.
    datapoints_to_alarm: int
    period_seconds: int
    #: Deliberately has **no default**. A default would mean the catalog test
    #: was testing the default rather than the catalog, and an author adding an
    #: alarm would never have to think about the field this phase is about.
    treat_missing_data: str
    #: What an operator should do. An alarm with no runbook line wakes somebody
    #: who then has to guess.
    runbook: str

    def __post_init__(self) -> None:
        # Raises UnknownSeries if the series is not declared.
        series(self.series_name)
        if self.datapoints_to_alarm < 1:
            raise ValueError(f"{self.name}: datapoints_to_alarm must be >= 1")
        if self.period_seconds < 60:
            raise ValueError(
                f"{self.name}: period below 60s costs high-resolution rates "
                "and this phase has a $0.50 reserve"
            )

    @property
    def evaluation_window_seconds(self) -> int:
        """How long a real outage stays invisible. Worth being able to state."""
        return self.datapoints_to_alarm * self.period_seconds

    def as_put_metric_alarm(self, *, namespace: str) -> dict:
        """Render the CloudWatch API shape. Rendering, not calling."""
        spec = series(self.series_name)
        return {
            "AlarmName": self.name,
            "Namespace": namespace,
            "MetricName": spec.name,
            "Unit": spec.unit.value,
            # Chosen by KIND, not by unit. An earlier version keyed off
            # Unit.COUNT and so summed `queue_depth` and `jobs_poisoned`,
            # which are gauges: a depth of 40 sampled five times in a period
            # would have evaluated as 200 and alarmed on nothing. A delta
            # accumulates and must be summed; a gauge describes an instant and
            # the worst instant is the one that matters.
            "Statistic": "Sum" if spec.kind is Kind.DELTA else "Maximum",
            "ComparisonOperator": self.comparison.value,
            "Threshold": self.threshold,
            "Period": self.period_seconds,
            "EvaluationPeriods": self.datapoints_to_alarm,
            "DatapointsToAlarm": self.datapoints_to_alarm,
            "TreatMissingData": self.treat_missing_data,
            "AlarmDescription": self.runbook,
            "ActionsEnabled": True,
        }


#: Every alarm this repository publishes.
#:
#: One per series is deliberate. A series with no alarm is a graph nobody
#: looks at, and `tests/test_alarms.py` asserts the two sets match exactly --
#: so adding a series without an alarm fails the build rather than quietly
#: producing decoration.
ALARMS: tuple[AlarmSpec, ...] = (
    AlarmSpec(
        name="worker-stopped",
        series_name="worker_ticks",
        comparison=Comparison.LESS_THAN,
        threshold=1,
        datapoints_to_alarm=3,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="The loop is not running. Check the host and the container "
        "state before anything else -- every other alarm below is "
        "downstream of this one.",
    ),
    AlarmSpec(
        name="worker-live-but-not-claiming",
        series_name="jobs_claimed",
        comparison=Comparison.LESS_THAN,
        threshold=1,
        datapoints_to_alarm=6,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Ticks are arriving and nothing is being claimed. Compare "
        "against queue_depth: zero depth means genuinely idle, non-zero "
        "depth means claiming is broken (lease contention, a provider "
        "permit held open, or a tenant filter).",
    ),
    AlarmSpec(
        name="job-failures-elevated",
        series_name="jobs_failed",
        comparison=Comparison.GREATER_THAN,
        threshold=10,
        datapoints_to_alarm=2,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Retries are burning attempt budget. Read the scrubbed "
        "last_error on the affected rows; do not read payloads.",
    ),
    AlarmSpec(
        name="job-poisoned",
        series_name="jobs_poisoned",
        comparison=Comparison.GREATER_THAN,
        threshold=0,
        datapoints_to_alarm=1,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Work has been abandoned. Nothing will retry it. Decide "
        "whether to fix and requeue or to drop it, and record which.",
    ),
    AlarmSpec(
        name="queue-backlog",
        series_name="queue_depth",
        comparison=Comparison.GREATER_THAN,
        threshold=200,
        datapoints_to_alarm=3,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Depth alone is not an incident. Check "
        "queue-oldest-age first; a deep queue that is draining needs "
        "nothing.",
    ),
    AlarmSpec(
        name="queue-oldest-age",
        series_name="queue_oldest_age_seconds",
        comparison=Comparison.GREATER_THAN,
        threshold=1800,
        datapoints_to_alarm=2,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="A job has waited half an hour. This is the backlog signal "
        "that matters; depth is context for it.",
    ),
    AlarmSpec(
        name="schedule-overdue",
        series_name="schedule_overdue_age_seconds",
        comparison=Comparison.GREATER_THAN,
        threshold=3600,
        datapoints_to_alarm=2,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Materialization has stopped while the worker kept looping. "
        "The slot key is deterministic, so re-running is safe.",
    ),
    AlarmSpec(
        name="outbox-undelivered",
        series_name="outbox_undelivered_age_seconds",
        comparison=Comparison.GREATER_THAN,
        threshold=1800,
        datapoints_to_alarm=2,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Somebody was promised a message half an hour ago. The relay "
        "is at-least-once, so re-running cannot lose one.",
    ),
    AlarmSpec(
        name="worker-heartbeat-stale",
        series_name="worker_staleness_seconds",
        comparison=Comparison.GREATER_THAN,
        threshold=900,
        datapoints_to_alarm=2,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="No worker has checked in for fifteen minutes. Unlike "
        "worker-stopped this survives the publisher dying, because any "
        "surviving process reports the newest heartbeat it can see.",
    ),
    AlarmSpec(
        name="database-unreachable",
        series_name="db_reachable",
        comparison=Comparison.LESS_THAN,
        threshold=1,
        datapoints_to_alarm=2,
        period_seconds=300,
        treat_missing_data=MISSING_IS_BREACHING,
        runbook="Something ran and could not reach the database. A zero here "
        "is a different incident from a gap: the gap means nothing ran.",
    ),
)


def evaluate(spec: AlarmSpec, datapoints: list[float | None]) -> State:
    """Decide the state the way the published configuration would.

    `None` in `datapoints` is a period with no sample.

    Never returns `INSUFFICIENT_DATA`. That is the point: with
    `treat_missing_data` set to breaching, a gap is a breach, so there is no
    state left for "we do not know". The enum member exists so the harness can
    name what AWS's default would have done -- not so this function can return
    it and let a caller decide silence is fine.

    Nothing in here consults the system clock or any configuration: the
    decision is a function of the datapoints and the spec, which is what makes
    it testable without AWS and identical to what AWS will compute.
    """
    if spec.treat_missing_data != MISSING_IS_BREACHING:
        # Not reachable through `ALARMS` -- the catalog test forbids it. Kept
        # as a loud failure rather than a silent branch, because the only way
        # to get here is a spec built outside the catalog, and such a spec
        # would otherwise be evaluated under rules nobody reviewed.
        raise ValueError(
            f"{spec.name} sets treat_missing_data={spec.treat_missing_data!r}; "
            f"this evaluator only implements {MISSING_IS_BREACHING!r}"
        )

    window = datapoints[-spec.datapoints_to_alarm :]
    if len(window) < spec.datapoints_to_alarm:
        # Fewer periods than the alarm needs, so the window is padded with
        # gaps and the gaps count as breaches -- CloudWatch's own rule.
        #
        # Note precisely what this does and does not do. It does NOT force an
        # alarm out of a short history: with three required datapoints and one
        # healthy sample the window is [gap, gap, 5.0], two breaches out of
        # three, and `all()` says OK. It only refuses to treat the gaps as
        # healthy. An earlier draft of this comment claimed the stronger
        # thing; `test_a_short_history_is_padded_with_breaches_not_with_health`
        # pins what actually happens, because a comment that overclaims is how
        # a reviewer is talked out of reading the code.
        window = [None] * (spec.datapoints_to_alarm - len(window)) + window

    def breaches(value: float | None) -> bool:
        if value is None:
            return True  # the rule, in one line
        if spec.comparison is Comparison.GREATER_THAN:
            return value > spec.threshold
        return value < spec.threshold

    return State.ALARM if all(breaches(v) for v in window) else State.OK


def coverage() -> dict[str, str | None]:
    """series name -> alarm name, or None. Read by the catalog test and the report."""
    by_series = {a.series_name: a.name for a in ALARMS}
    return {s.name: by_series.get(s.name) for s in SERIES}
