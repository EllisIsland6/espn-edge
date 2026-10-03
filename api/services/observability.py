"""Operational telemetry: the ten series, and the rule that zero is a value.

WHAT THIS IS NOT
----------------
`api/services/telemetry.py` is Phase 32 *provider* telemetry -- a closed-value
recorder for ESPN call outcomes, with its own redaction history. This module is
the *operational* surface: the small set of numbers that say whether the system
is running, published to a sink that something outside this host can read.

The two are separate on purpose. Provider telemetry answers "did the call go
well". This answers "is anybody home". A dead process cannot report that it is
dead, so the second question can only be answered from outside, and that
changes what the code has to do: it has to keep publishing while *nothing is
happening*, which is the one case every naive implementation skips.

THE DEFECT THIS MODULE EXISTS TO PREVENT
----------------------------------------
Before this module, a probe (`.venv/phase41/probe_liveness.py`) ran the worker
against an empty queue and counted every durable row that changed. The answer
was *none*. So a worker that is up and idle and a worker that died an hour ago
produced byte-identical state, and no alarm could tell them apart.

The instinct is to emit a metric when work happens. That instinct is the bug.
A counter that is only written when it is non-zero has no datapoint during an
outage, an alarm on it sits in `INSUFFICIENT_DATA`, and CloudWatch's default
reading of `INSUFFICIENT_DATA` is *not alarming*. Silence therefore reads as
health, which is exactly backwards: silence is the thing we are trying to
detect.

So this module makes the omission impossible rather than discouraged:

* `report()` takes a whole snapshot and **requires every series in the group**.
  A missing key raises `IncompleteReport`. "Nothing happened so I published
  nothing" is a crash here, not a gap in a graph.
* `emit()` accepts `0` like any other number. There is no "skip if falsy"
  anywhere in this file, and `tests/test_observability.py` removes that control
  to prove the difference is detectable.

WHAT MAY BECOME A DIMENSION
---------------------------
Only `env` and `kind`. Both are closed sets fixed at deploy time.

`tenant_id` is named and refused. Not because it is useless -- it is the most
useful dimension there is -- but because a per-tenant dimension on a custom
metric multiplies the series count by the tenant count, and the bill and the
cardinality limit both arrive without warning. Per-tenant numbers belong in a
query against the database, where they cost a query rather than a subscription.

`job_id`, `user_id`, `league_id` and `owner` are refused for the same reason,
unbounded growth, and additionally because `owner` is a hostname and member
identifiers are not telemetry.

NOTHING HERE CALLS AWS
----------------------
A `Sink` is a protocol with one method. `MemorySink` is for tests, `NullSink`
is the resting state, and CloudWatch is a sink somebody writes in Phase 43 when
there is an account to write it against. The split is not ceremony: it is what
makes every guarantee in this module provable on a laptop with no credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Protocol


class Unit(Enum):
    """What a number means. CloudWatch's unit vocabulary, narrowed.

    A unit is part of a series' identity rather than a label on a sample,
    because a series that is sometimes seconds and sometimes a count cannot be
    alarmed on -- the threshold would mean two things.
    """

    COUNT = "Count"
    SECONDS = "Seconds"
    #: 0 or 1. Kept distinct from COUNT so a threshold of `< 1` reads as
    #: "not healthy" rather than "fewer than one of something".
    BOOLEAN = "None"


class Kind(Enum):
    """Does the number accumulate, or describe this instant?

    It decides what a missing sample means. A GAUGE with no sample means
    nobody looked. A DELTA with no sample means nobody *ran*. Both are
    failures, which is why `treat_missing_data` is `breaching` for every alarm
    in `alarms.py` regardless of kind -- but they are different failures and
    the runbook differs, so the distinction is recorded.
    """

    #: A value describing now: depth, age, reachability.
    GAUGE = "gauge"
    #: How much happened since the last report. Zero is the common case.
    DELTA = "delta"


@dataclass(frozen=True, slots=True)
class Series:
    """One metric series. Declared once, here, so a test can read the whole set."""

    name: str
    unit: Unit
    kind: Kind
    #: Why an operator would look at this number. Not decoration: a series
    #: nobody can say a sentence about is a series nobody will act on.
    reads_as: str


#: The ten. Deliberately a fixed, small set.
#:
#: Ten is not a target that was hit by padding -- each one is here because
#: removing it leaves a specific failure invisible, and that claim is tested in
#: `tests/test_observability.py::test_every_series_has_a_failure_it_makes_visible`
#: by requiring each to be reachable from a real source function.
SERIES: tuple[Series, ...] = (
    Series(
        "worker_ticks",
        Unit.COUNT,
        Kind.DELTA,
        "The loop ran. Missing means the process is gone; this is the heartbeat "
        "and it is the only series whose absence is unambiguous.",
    ),
    Series(
        "jobs_claimed",
        Unit.COUNT,
        Kind.DELTA,
        "Work was picked up. Zero with worker_ticks non-zero is the "
        "live-but-not-claiming case: up, looping, and getting nothing done.",
    ),
    Series(
        "jobs_failed",
        Unit.COUNT,
        Kind.DELTA,
        "Attempts that ended in an error. Rising with jobs_claimed flat is a "
        "retry storm.",
    ),
    Series(
        "jobs_poisoned",
        Unit.COUNT,
        # A GAUGE, not a DELTA, and the distinction was a real bug caught in
        # review: the source is `count_by_state(POISON)`, a running total, and
        # a DELTA fed a running total reports a flood every period and never
        # comes back to zero. As a gauge it also keeps firing until somebody
        # clears the rows, rather than firing once and going quiet while the
        # abandoned work sits there.
        Kind.GAUGE,
        "Jobs sitting in poison right now. Any non-zero value is work that "
        "nobody will retry and somebody has to look at.",
    ),
    Series(
        "queue_depth",
        Unit.COUNT,
        Kind.GAUGE,
        "Jobs due right now. High and falling is healthy; high and flat is not.",
    ),
    Series(
        "queue_oldest_age_seconds",
        Unit.SECONDS,
        Kind.GAUGE,
        "How long the oldest due job has waited. The latency signal -- depth "
        "alone cannot tell a fast thousand from one stuck job.",
    ),
    Series(
        "schedule_overdue_age_seconds",
        Unit.SECONDS,
        Kind.GAUGE,
        "How late the most overdue schedule slot is. Catches a materializer "
        "that stopped while the worker kept looping.",
    ),
    Series(
        "outbox_undelivered_age_seconds",
        Unit.SECONDS,
        Kind.GAUGE,
        "How long the oldest undelivered message has waited. A promise made "
        "and not yet kept.",
    ),
    Series(
        "worker_staleness_seconds",
        Unit.SECONDS,
        Kind.GAUGE,
        "Age of the newest worker heartbeat. Published by whoever is still "
        "alive, so it survives one worker dying -- unlike worker_ticks, which "
        "goes silent with the process that emits it.",
    ),
    Series(
        "db_reachable",
        Unit.BOOLEAN,
        Kind.GAUGE,
        "1 if a trivial query succeeded. Zero is distinct from missing: zero "
        "means something ran and could not reach the database, missing means "
        "nothing ran at all.",
    ),
)

_BY_NAME: MappingProxyType = MappingProxyType({s.name: s for s in SERIES})


def series(name: str) -> Series:
    """Look up a declared series, or refuse.

    There is no "create on first use". A typo in a metric name is otherwise a
    series nobody alarms on, which is worse than a crash because it looks like
    it is working.
    """
    try:
        return _BY_NAME[name]
    except KeyError:
        raise UnknownSeries(
            f"{name!r} is not a declared series. Add it to SERIES and give it "
            "an alarm, or do not publish it."
        ) from None


class UnknownSeries(KeyError):
    """A name that is not in `SERIES`."""


class ForbiddenDimension(ValueError):
    """A dimension that would make the series unbounded, or leak."""


class BadValue(ValueError):
    """A value that is not a finite number, or not legal for the unit."""


class IncompleteReport(ValueError):
    """A snapshot that left a series out.

    This is the whole point of `report()`. See the module docstring: the
    omission is the defect, so the omission raises.
    """


#: The only dimension keys that may ever appear.
ALLOWED_DIMENSIONS = frozenset({"env", "kind"})

#: Named and refused, with the reason, so the error teaches rather than scolds.
REFUSED_DIMENSIONS: MappingProxyType = MappingProxyType(
    {
        "tenant_id": "multiplies the series count by the tenant count; query the database instead",
        "tenant": "see tenant_id",
        "user_id": "member identifiers are not metrics",
        "user": "see user_id",
        "email": "member identifiers are not metrics",
        "league_id": "unbounded, and a league id identifies a person's team",
        "job_id": "unbounded by construction -- one series per row",
        "owner": "a hostname; unbounded under autoscaling",
        "trace_id": "unbounded by construction",
        "session_id": "a credential handle has no business in a metric name",
    }
)

#: A dimension *value* must be a short closed token. Free text in a dimension
#: is how a payload fragment or an error message ends up in a metric name.
_TOKEN = re.compile(r"\A[a-z][a-z0-9_-]{0,31}\Z")


@dataclass(frozen=True, slots=True)
class Sample:
    """One published number. Values only -- no message, no payload, no id."""

    series: str
    value: float
    unit: Unit
    at: datetime
    dimensions: tuple[tuple[str, str], ...] = ()


class Sink(Protocol):
    """Where samples go. One method, so a real one is cheap to write later."""

    def publish(self, samples: tuple[Sample, ...]) -> None: ...


class NullSink:
    """Drops everything. The resting state.

    Telemetry that is off must never change behaviour, so the default sink is
    a working sink that discards rather than a `None` every caller has to
    branch on. The guarantee "telemetry failure never authorizes provider/AI
    work" starts here: nothing in this module returns a decision.
    """

    def publish(self, samples: tuple[Sample, ...]) -> None:
        return None


@dataclass
class MemorySink:
    """Keeps samples for tests and for the fault harness.

    `samples` is append-only. Absence of a series is therefore observable,
    which is the property the zero-versus-missing tests need: they assert on
    *what is not there*, and a sink that pre-seeded zeroes would make that
    assertion unfalsifiable.
    """

    samples: list[Sample] = field(default_factory=list)
    #: Set to raise on the next publish, for the "telemetry failure is not a
    #: decision" test.
    fail_next: bool = False

    def publish(self, samples: tuple[Sample, ...]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("sink unavailable")
        self.samples.extend(samples)

    # -- read helpers; tests use these so they do not depend on list order --

    def values(self, name: str) -> list[float]:
        return [s.value for s in self.samples if s.series == name]

    def latest(self, name: str) -> float | None:
        vals = self.values(name)
        return vals[-1] if vals else None

    def has(self, name: str) -> bool:
        """Was this series published at all? Distinct from `latest() == 0`."""
        return any(s.series == name for s in self.samples)


def _check_value(spec: Series, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadValue(f"{spec.name}: {value!r} is not a number")
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        raise BadValue(f"{spec.name}: {value!r} is not finite")
    if spec.unit is Unit.BOOLEAN and v not in (0.0, 1.0):
        raise BadValue(f"{spec.name} is a boolean series; {v} is neither 0 nor 1")
    if spec.unit in (Unit.COUNT, Unit.SECONDS) and v < 0:
        raise BadValue(f"{spec.name} cannot be negative; got {v}")
    return v


def _check_dimensions(dimensions: dict[str, str]) -> tuple[tuple[str, str], ...]:
    for key, value in dimensions.items():
        if key in REFUSED_DIMENSIONS:
            raise ForbiddenDimension(
                f"{key!r} may not be a metric dimension: {REFUSED_DIMENSIONS[key]}"
            )
        if key not in ALLOWED_DIMENSIONS:
            raise ForbiddenDimension(
                f"{key!r} is not an allowed dimension. Allowed: "
                f"{sorted(ALLOWED_DIMENSIONS)}. Adding one is a cardinality "
                "decision, so it is made here and not at a call site."
            )
        if not isinstance(value, str) or not _TOKEN.match(value):
            raise ForbiddenDimension(
                f"dimension {key}={value!r} is not a short closed token. Free "
                "text in a dimension is how an error message becomes a metric."
            )
    return tuple(sorted(dimensions.items()))


def emit(
    sink: Sink,
    name: str,
    value: float,
    *,
    at: datetime | None = None,
    **dimensions: str,
) -> Sample:
    """Publish one number.

    Zero publishes. There is no falsy check here and there must never be one:
    see the module docstring, and
    `tests/test_observability.py::test_a_zero_is_published_not_skipped`.
    """
    spec = series(name)
    checked = _check_value(spec, value)
    dims = _check_dimensions(dimensions)
    sample = Sample(
        series=spec.name,
        value=checked,
        unit=spec.unit,
        at=at or datetime.now(UTC),
        dimensions=dims,
    )
    sink.publish((sample,))
    return sample


def report(
    sink: Sink,
    snapshot: dict[str, float],
    *,
    at: datetime | None = None,
    **dimensions: str,
) -> tuple[Sample, ...]:
    """Publish a whole snapshot, or refuse.

    Every declared series must be present. This is the structural form of the
    zero-versus-missing rule: a caller that has nothing to say about
    `jobs_claimed` must say `0`, because saying nothing raises.

    An extra key is also an error. A snapshot with a name that is not declared
    is either a typo or a series somebody forgot to add to `SERIES`, and both
    produce a number nobody alarms on.
    """
    missing = sorted(set(_BY_NAME) - set(snapshot))
    if missing:
        raise IncompleteReport(
            f"snapshot is missing {missing}. Every series must carry a value: a "
            "series with no datapoint reads as INSUFFICIENT_DATA, which alarms "
            "treat as health unless told otherwise. If nothing happened, the "
            "value is 0."
        )
    extra = sorted(set(snapshot) - set(_BY_NAME))
    if extra:
        raise UnknownSeries(
            f"snapshot carries undeclared series {extra}. Declare them in "
            "SERIES and alarm on them, or drop them."
        )
    when = at or datetime.now(UTC)
    dims = _check_dimensions(dimensions)
    samples = tuple(
        Sample(
            series=spec.name,
            value=_check_value(spec, snapshot[spec.name]),
            unit=spec.unit,
            at=when,
            dimensions=dims,
        )
        # SERIES order, not dict order, so a report is comparable across calls.
        for spec in SERIES
    )
    sink.publish(samples)
    return samples
