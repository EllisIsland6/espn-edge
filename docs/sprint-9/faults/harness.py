"""Local fault harness: four failures, four distinguishable observations.

Phase 41's acceptance asks for a harness that "proves host stop, container
crash loop, DB unreachable and live-but-not-claiming signal paths". The word
doing the work is *proves*, and the trap is obvious once stated: a harness in
which every fault lights every alarm has proved nothing. It would be
indistinguishable from an alarm catalog that always fires.

So this script makes a stronger claim and checks it:

    The four faults produce four observations that are pairwise distinct, and
    a healthy baseline is distinct from all four.

Each scenario builds a world, injects one fault, collects what would have been
published, and evaluates the real alarm catalog against it. Then every pair of
scenarios is compared and any pair with the same signature is a failure --
because two faults that look alike cannot be told apart at three in the
morning.

WHAT IS AND IS NOT PROVEN HERE
------------------------------
Proven: the signal paths. Given each fault, what reaches the sink, and what
the published alarm configuration computes from it.

Not proven: that CloudWatch behaves as `alarms.evaluate` says it does. That is
an AWS claim and needs an account; `evaluate` is written to AWS's documented
rule for `treatMissingData=breaching` and Phase 43 is where it is checked
against the real thing. Said here rather than left implied, because a harness
that quietly claimed to have tested AWS would be the exact defect this project
keeps producing.

Also not proven: that a real host stopping produces no samples. That is
arithmetic -- a stopped process does not publish -- and the harness models it
by not publishing. The thing worth checking is what the *reader* does with
that silence, and that is what is checked.

Run:  APP_MODE=private_operator python docs/sprint-9/faults/harness.py
Exit: 0 if every claim holds, 1 otherwise.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

os.environ.setdefault("APP_MODE", "private_operator")
sys.path.insert(0, os.getcwd())

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import api.db as db  # noqa: E402
from api.models import Base, WorkerHeartbeat  # noqa: E402
from api.services.alarms import ALARMS, State, evaluate  # noqa: E402
from api.services.heartbeat import idle_age, record_tick  # noqa: E402
from api.services.jobs import enqueue  # noqa: E402
from api.services.observability import MemorySink  # noqa: E402
from api.services.snapshot import publish, report_degraded  # noqa: E402
from api.tenancy import ensure_default_tenant  # noqa: E402

T0 = datetime(2026, 10, 2, 9, 0, 0, tzinfo=UTC)
PERIOD = timedelta(minutes=5)
#: Ten periods (fifty minutes). Two constraints set this: longer than the
#: longest `datapoints_to_alarm` in the catalog, so no alarm is evaluated on a
#: padded window and every result comes from real data or a real gap; and long
#: enough for a neglected queue to age past `queue-oldest-age`'s thirty-minute
#: threshold, which is the reading that confirms a live-but-not-claiming worker
#: rather than a genuinely empty one. Six periods was the first value and the
#: second constraint failed silently -- the claim was simply unprovable.
PERIODS = 10


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def fresh_world():
    """A private in-memory database per scenario.

    Per scenario, not shared: a leaked heartbeat row from the previous
    scenario would make the next one's signature wrong in a way that still
    looked plausible.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db.SessionLocal = maker
    db.engine = engine
    with maker() as s:
        ensure_default_tenant(s)
        s.commit()
    with maker() as s:
        tenant_id = s.execute(
            __import__("sqlalchemy").text("SELECT id FROM tenants LIMIT 1")
        ).scalar_one()
    return maker, tenant_id


def windows(sink: MemorySink) -> dict:
    """series -> one datapoint per PERIOD of wall-clock time, gaps as None.

    On the time grid, not on the sample list, and the difference is the whole
    harness. The first version of this function returned `sink.values(name)`
    and padded only when that list was empty -- so a host that published twice
    and then died produced the window [None, 1.0, 1.0], a single gap among two
    healthy samples, and every alarm read OK. The harness reported a stopped
    host as indistinguishable from a healthy one, which is precisely the
    failure Phase 41 exists to make impossible.

    Silence occupies periods. A period with no sample is a gap whether or not
    any sample ever arrived, and that is what CloudWatch evaluates.
    """
    grid = [T0 + i * PERIOD for i in range(PERIODS)]
    out = {}
    for alarm in ALARMS:
        row: list[float | None] = []
        for start in grid:
            end = start + PERIOD
            in_period = [
                s.value
                for s in sink.samples
                if s.series == alarm.series_name and start <= s.at < end
            ]
            # Several samples in one period reduce the way the alarm's
            # statistic does: a delta accumulates, a gauge takes its worst.
            if not in_period:
                row.append(None)
            elif alarm.as_put_metric_alarm(namespace="h")["Statistic"] == "Sum":
                row.append(sum(in_period))
            else:
                row.append(max(in_period))
        out[alarm.series_name] = row
    return out


def signature(sink: MemorySink, maker) -> dict:
    """The observation: which alarms fire, plus the two facts that separate
    the faults that fire the same alarms.

    `db_reachable_observed` is the zero-versus-absent distinction doing real
    work: a host that stopped and a database that is down both light every
    alarm, and the only thing that tells them apart is whether anything was
    alive to publish a zero.

    `short_lived_owners` is what separates a crash loop from a slow worker:
    a container restarting takes a new task identity each time, so the table
    fills with rows that ticked once and never again.
    """
    w = windows(sink)
    firing = tuple(
        sorted(a.name for a in ALARMS if evaluate(a, w[a.series_name]) is State.ALARM)
    )
    # The reading in the last period, which is what distinguishes "something
    # was alive and could not reach the database" from "nothing was alive".
    # `sink.has()` was used here first and could not tell them apart: it asks
    # whether the series was ever published, not what the window says now.
    last = w["db_reachable"][-1]
    db_now = "absent" if last is None else last
    with maker() as s:
        rows = s.query(WorkerHeartbeat).all()
        short_lived = sum(
            1 for r in rows if r.ticks == 1 and r.last_seen_at == r.first_seen_at
        )
        owners = len(rows)
        # How long since anything was claimed, at the end of the window. This
        # is the reading an operator uses to confirm a live-but-not-claiming
        # diagnosis, so the harness has to look at it: without it, removing
        # the `if claimed:` condition in `record_tick` -- the single change
        # that destroys the signal -- left this harness passing.
        idle = idle_age(s, clock=lambda: T0 + PERIODS * PERIOD)
    return {
        "firing": firing,
        "db_reachable_in_last_period": db_now,
        "owners": owners,
        "short_lived_owners": short_lived,
        "ever_claimed": idle is not None,
    }


# --------------------------------------------------------------------------
# The scenarios
# --------------------------------------------------------------------------


def scenario_healthy():
    """The baseline. Without it the other four prove only that alarms exist."""
    maker, tenant_id = fresh_world()
    clock = Clock()
    sink = MemorySink()
    for _ in range(PERIODS):
        with maker() as s:
            record_tick(s, "worker-1", claimed=1, clock=clock)
            s.commit()
        with maker() as s:
            publish(sink, s, clock=clock, env="test")
            s.commit()
        clock.advance(PERIOD)
    return sink, maker


def scenario_host_stop():
    """The host is gone. Nothing publishes, because nothing is running.

    Modelled by publishing for two periods and then not at all -- which is
    what a stopped process does. The claim under test is not "a dead process
    is quiet" (arithmetic) but "quiet is read as broken".
    """
    maker, tenant_id = fresh_world()
    clock = Clock()
    sink = MemorySink()
    for _ in range(2):
        with maker() as s:
            record_tick(s, "worker-1", claimed=1, clock=clock)
            s.commit()
        with maker() as s:
            publish(sink, s, clock=clock, env="test")
            s.commit()
        clock.advance(PERIOD)
    # ...and then silence for the rest of the window. No publish, no tick.
    return sink, maker


def scenario_crash_loop():
    """The container starts, ticks once, dies, and is restarted.

    Each restart takes a new task identity, so the heartbeat table fills with
    rows that ticked exactly once. The loop is "running" by any
    is-the-process-up check, and `worker_ticks` is non-zero, so neither of
    those distinguishes it -- the short-lived-owner count does.
    """
    maker, tenant_id = fresh_world()
    clock = Clock()
    sink = MemorySink()
    with maker() as s:
        for i in range(3):
            enqueue(
                s, tenant_id=tenant_id, kind="sync", idempotency_key=f"k{i}", clock=clock
            )
        s.commit()
    for attempt in range(PERIODS):
        # A new task identity every time, which is what ECS does.
        with maker() as s:
            record_tick(s, f"worker-{attempt}", claimed=0, clock=clock)
            s.commit()
        with maker() as s:
            publish(sink, s, clock=clock, env="test")
            s.commit()
        clock.advance(PERIOD)
    return sink, maker


def scenario_db_unreachable():
    """The reporter is alive; the database is not.

    Nine series cannot be measured, so nine are absent and `db_reachable=0` is
    published. That published zero is the entire difference between this and
    a stopped host, and it is why `emit` must never skip a falsy value.
    """
    maker, tenant_id = fresh_world()
    clock = Clock()
    sink = MemorySink()
    for _ in range(PERIODS):
        report_degraded(sink, clock=clock, env="test")
        clock.advance(PERIOD)
    return sink, maker


def scenario_live_not_claiming():
    """The worst one: up, looping, claiming nothing, with work waiting.

    Every "is it running" check passes. The logs are quiet. `worker_ticks`
    arrives on schedule. The only readings that differ from healthy are
    `jobs_claimed=0` beside a non-zero `queue_depth`, which is exactly the
    pair the `worker-live-but-not-claiming` alarm watches.
    """
    maker, tenant_id = fresh_world()
    clock = Clock()
    sink = MemorySink()
    with maker() as s:
        for i in range(5):
            enqueue(
                s, tenant_id=tenant_id, kind="sync", idempotency_key=f"k{i}", clock=clock
            )
        s.commit()
    for _ in range(PERIODS):
        with maker() as s:
            record_tick(s, "worker-1", claimed=0, clock=clock)
            s.commit()
        with maker() as s:
            publish(sink, s, clock=clock, env="test")
            s.commit()
        clock.advance(PERIOD)
    return sink, maker


SCENARIOS = {
    "healthy": scenario_healthy,
    "host_stop": scenario_host_stop,
    "crash_loop": scenario_crash_loop,
    "db_unreachable": scenario_db_unreachable,
    "live_not_claiming": scenario_live_not_claiming,
}

#: What each scenario must prove. Only the alarms that carry the diagnosis are
#: named: `must_fire` has to fire, `must_not_fire` has to stay quiet, and
#: anything unnamed is allowed either way. Over-specifying would make this a
#: snapshot test of the catalog rather than a claim about the signal paths.
CLAIMS = {
    "healthy": {
        "must_fire": (),
        "must_not_fire": ("worker-stopped", "worker-live-but-not-claiming",
                          "database-unreachable", "worker-heartbeat-stale"),
    },
    "host_stop": {
        "must_fire": ("worker-stopped", "worker-heartbeat-stale",
                      "database-unreachable"),
        "must_not_fire": (),
    },
    "crash_loop": {
        "must_fire": ("worker-live-but-not-claiming", "queue-oldest-age"),
        "must_not_fire": ("database-unreachable",),
    },
    "db_unreachable": {
        "must_fire": ("database-unreachable", "worker-stopped",
                      "queue-oldest-age"),
        "must_not_fire": (),
    },
    "live_not_claiming": {
        "must_fire": ("worker-live-but-not-claiming", "queue-oldest-age"),
        "must_not_fire": ("worker-stopped", "database-unreachable",
                          "worker-heartbeat-stale"),
    },
}


#: The zero-versus-absent reading each scenario must produce.
#:
#: Asserted, not merely printed. `host_stop` and `db_unreachable` light the
#: same ten alarms, so this field is the only thing that tells an operator
#: whether anything was alive to report the failure -- and the harness passed
#: once with both reading "absent", separated by a coincidence in their alarm
#: counts. A published 0 and a gap are different observations; that claim is
#: worthless unless something checks it.
#: Whether any worker ever claimed anything, as the heartbeat table records it.
#:
#: The three scenarios in which a worker ran but never claimed must read False.
#: Removing the `if claimed:` condition in `heartbeat.record_tick` makes them
#: read True -- and before this was asserted, that removal left the harness
#: passing even though it is the one change that destroys the
#: live-but-not-claiming signal entirely.
EXPECTED_EVER_CLAIMED = {
    "healthy": True,
    "host_stop": True,
    "crash_loop": False,
    "db_unreachable": False,
    "live_not_claiming": False,
}

EXPECTED_DB_READING = {
    "healthy": 1.0,
    "host_stop": "absent",
    "crash_loop": 1.0,
    "db_unreachable": 0.0,
    "live_not_claiming": 1.0,
}


def main() -> int:
    failures: list[str] = []
    signatures: dict[str, dict] = {}

    print("FAULT HARNESS -- four faults, four observations")
    print("=" * 70)

    for name, build in SCENARIOS.items():
        sink, maker = build()
        sig = signature(sink, maker)
        signatures[name] = sig

        print(f"\n{name}")
        print(f"  samples published : {len(sink.samples)}")
        print(f"  db_reachable now  : {sig['db_reachable_in_last_period']}"
              f"   (0 = ran and failed; absent = nothing ran)")
        print(f"  heartbeat owners  : {sig['owners']}"
              f" ({sig['short_lived_owners']} ticked once and died)")
        print(f"  ever claimed      : {sig['ever_claimed']}")
        print(f"  alarms firing     : {len(sig['firing'])}")
        for alarm_name in sig["firing"]:
            print(f"      - {alarm_name}")

        claims = CLAIMS[name]
        expected_db = EXPECTED_DB_READING[name]
        if sig["ever_claimed"] != EXPECTED_EVER_CLAIMED[name]:
            failures.append(
                f"{name}: ever_claimed read {sig['ever_claimed']}, "
                f"expected {EXPECTED_EVER_CLAIMED[name]} -- the "
                "live-but-not-claiming signal is not being recorded"
            )
        if sig["db_reachable_in_last_period"] != expected_db:
            failures.append(
                f"{name}: db_reachable read "
                f"{sig['db_reachable_in_last_period']!r}, expected {expected_db!r}"
            )
        for expected in claims["must_fire"]:
            if expected not in sig["firing"]:
                failures.append(f"{name}: {expected} did not fire")
        for forbidden in claims["must_not_fire"]:
            if forbidden in sig["firing"]:
                failures.append(f"{name}: {forbidden} fired and should not have")

    print("\n" + "=" * 70)
    print("PAIRWISE DISTINCTNESS -- two faults that look alike cannot be triaged")
    print("=" * 70)
    names = list(SCENARIOS)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            same = signatures[a] == signatures[b]
            print(f"  {a:>18}  vs  {b:<18} {'IDENTICAL' if same else 'distinct'}")
            if same:
                failures.append(f"{a} and {b} produce the same observation")

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED ({len(failures)})")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"PASSED -- {len(SCENARIOS)} scenarios, "
          f"{len(names) * (len(names) - 1) // 2} pairs all distinct")
    print("\nNot proven here: that CloudWatch computes what alarms.evaluate")
    print("computes. That needs an account and is Phase 43.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
