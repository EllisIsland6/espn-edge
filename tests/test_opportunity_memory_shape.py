"""The opportunity universe must not materialise ORM rows.

Phase 34 concluded that `t4g.small` dual-slot is viable **if and only if the
exports stream**, and recommended implementing streaming before spending the
$0.25 to confirm it. Measuring the production code found the conclusion right
and the mechanism wrong: the memory is not spent in the export writer, it is
spent in `_build_opportunity_universe`, which every opportunity route calls --
the table, the charts, the per-player view and the CSV. Streaming the CSV
writer would not have moved it, because by the time the writer runs the whole
universe is already resident.

Measured on Linux at 99,990 player-game rows, one case per process, baseline
67.2 MiB:

    ORM objects only ................. 308.1 MiB
    ORM objects -> dicts (the old) ... 388.1 MiB
    whole universe (old) ............. 396.1 MiB   CSV export: 412.3
    Core mappings -> dicts (the new) . 232.4 MiB
    whole universe (new) ............. 245.3 MiB   CSV export: 261.6

This file cannot measure that. Peak RSS needs one case per process, and
`/proc/self/clear_refs` does not reset `VmHWM` on this kernel -- a second
measurement in the same process reports the first one's watermark, which reads
as a flat bounded curve and is the exact opposite of the truth (Phase 34
recorded losing an hour to it).

So it asserts the **mechanism** instead, by counting how many
`OpportunityWeek` instances the ORM loads while the universe is built. Zero
for a Core select, one per row for an ORM one.

**The first version of this test checked the session's identity map instead,
and it passed with the ORM path restored.** SQLAlchemy's identity map holds
*weak* references: once the list comprehension finished, nothing else
referenced the ORM objects, they were collected, and the map was empty by the
time the assertion ran. A green tick establishing nothing -- caught only
because the control was actually removed and the test was expected to fail.
The mapper-level `load` event fires when the instance is built and cannot be
undone by garbage collection afterwards.
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import event

from api.models import OpportunityWeek
from api.services.opportunity import _build_opportunity_universe
from api.services.portfolio_filters import PortfolioFilters


@contextmanager
def counting_orm_loads():
    """Counts `OpportunityWeek` instances the ORM builds from result rows.

    The mapper-level `load` event fires once per instance as it is populated.
    Unlike an identity-map check it records that the object EXISTED, which is
    what costs the memory, rather than that it is still reachable afterwards.
    """
    loads = []

    def _on_load(target, _context):
        loads.append(target)

    event.listen(OpportunityWeek, "load", _on_load)
    try:
        yield loads
    finally:
        event.remove(OpportunityWeek, "load", _on_load)


def _seed(session, count: int) -> None:
    session.add_all(
        OpportunityWeek(
            season=2026,
            season_type="REG",
            week=(i % 18) + 1,
            game_id=f"2026_{(i % 18) + 1:02d}_A_B{i % 4}",
            gsis_id=f"00-00{i // 18:05d}",
            team="AAA",
            opponent_team="BBB",
            position=("QB", "RB", "WR", "TE")[i % 4],
            carries=1.0,
            targets=2.0,
            receptions=1.0,
            rushing_yards=10.0,
            receiving_yards=12.0,
            fantasy_points_ppr=7.5,
        )
        for i in range(count)
    )
    session.commit()


def test_the_universe_builds_without_constructing_a_single_orm_row(db_session):
    _seed(db_session, 72)
    db_session.expunge_all()

    with counting_orm_loads() as loads:
        universe = _build_opportunity_universe(db_session, PortfolioFilters(season=2026))

    assert universe.stored_player_games == 72, (
        "the universe read a different number of rows than were seeded; this "
        "test is not exercising the path it claims to"
    )
    assert not loads, (
        f"the ORM constructed {len(loads)} OpportunityWeek instances. The "
        f"universe is materialising ORM rows again, which measured 396 MiB "
        f"against 245 for the Core path at 100k rows -- and this is the read "
        f"path, on every opportunity request, not just the export."
    )


def test_the_load_counter_actually_counts(db_session):
    """Guards the guard. If the `load` event stopped firing -- a SQLAlchemy
    upgrade, a renamed event -- the test above would pass against a counter
    that is always empty, which is the failure mode it exists to prevent."""
    from sqlalchemy import select

    _seed(db_session, 5)
    db_session.expunge_all()
    with counting_orm_loads() as loads:
        rows = list(db_session.scalars(select(OpportunityWeek)))
    assert len(rows) == 5
    assert len(loads) == 5, (
        f"an explicit ORM query loaded {len(rows)} rows but the counter saw "
        f"{len(loads)}. The instrument is broken, so the test above proves nothing."
    )


def test_the_rows_carry_every_column_except_the_primary_key(db_session):
    """Guards the guard. A Core select that dropped columns would use less
    memory and pass the test above while returning less data."""
    _seed(db_session, 18)
    db_session.expunge_all()

    universe = _build_opportunity_universe(db_session, PortfolioFilters(season=2026))
    assert universe.stored_player_games == 18, (
        "the universe read a different number of rows than were seeded; "
        "nothing below is meaningful"
    )

    expected = {
        column.name for column in OpportunityWeek.__table__.columns if column.name != "id"
    } - {"tenant_id"}
    # `universe.rows` is the scored read model and is empty without
    # `NflversePlayerMap` rows to join through, so the shape is checked on the
    # query the universe issues rather than on its output.
    from sqlalchemy import select

    columns = [c for c in OpportunityWeek.__table__.columns if c.name != "id"]
    sample = dict(
        db_session.execute(select(*columns).limit(1)).mappings().one()
    )
    missing = expected - set(sample)
    assert not missing, f"the Core select is dropping columns: {sorted(missing)}"
    assert "id" not in sample, "the primary key is back in the payload"
