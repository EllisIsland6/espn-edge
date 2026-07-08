"""ESPN fantasy football id → label maps.

These are stable community-documented constants (SPEC Appendix B gist). Field names
on live responses must still be verified on first pull (SPEC 2.4).
"""

from __future__ import annotations

# lineupSlotId → slot label
LINEUP_SLOT_NAMES: dict[int, str] = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BE",
    21: "IR",
    22: "RES",
    23: "FLEX",
    24: "ER",
}

# Slots that do NOT count as a started lineup position.
BENCH_SLOTS: set[int] = {20, 21, 24}

# defaultPositionId → position label
POSITION_NAMES: dict[int, str] = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

# Playoff tier markers on schedule entries indicate a playoff matchup.
PLAYOFF_TIER_NONE = "NONE"

# statSourceId: 0 = actual, 1 = projection (SPEC 2.9).
STAT_SOURCE_ACTUAL = 0
STAT_SOURCE_PROJECTED = 1

# Reception stat id — used to classify PPR vs standard scoring.
STAT_ID_RECEPTIONS = 53


def slot_name(slot_id: int | None) -> str:
    if slot_id is None:
        return "?"
    return LINEUP_SLOT_NAMES.get(slot_id, str(slot_id))


def position_name(pos_id: int | None) -> str:
    if pos_id is None:
        return "?"
    return POSITION_NAMES.get(pos_id, str(pos_id))


def is_starter_slot(slot_id: int | None) -> bool:
    return slot_id is not None and slot_id not in BENCH_SLOTS
