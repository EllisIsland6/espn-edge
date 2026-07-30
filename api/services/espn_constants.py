"""ESPN fantasy football id → label maps.

These are stable community-documented constants (SPEC Appendix B gist). Field names
on live responses must still be verified on first pull (SPEC 2.4).
"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal


class LineupSlotId(IntEnum):
    QB = 0
    TEAM_QB = 1
    RB = 2
    RB_WR = 3
    WR = 4
    WR_TE = 5
    TE = 6
    OFFENSIVE_PLAYER = 7
    DT = 8
    DE = 9
    LB = 10
    DL = 11
    CB = 12
    SAFETY = 13
    DB = 14
    DEFENSIVE_PLAYER = 15
    DEFENSE_SPECIAL_TEAMS = 16
    KICKER = 17
    PUNTER = 18
    HEAD_COACH = 19
    BENCH = 20
    INJURED_RESERVE = 21
    RESERVE = 22
    FLEX = 23
    EMERGENCY_RESERVE = 24


class PositionId(IntEnum):
    QB = 1
    RB = 2
    WR = 3
    TE = 4
    KICKER = 5
    DEFENSE_SPECIAL_TEAMS = 16


LINEUP_SLOT_NAMES: dict[int, str] = {
    LineupSlotId.QB: "QB",
    LineupSlotId.TEAM_QB: "TQB",
    LineupSlotId.RB: "RB",
    LineupSlotId.RB_WR: "RB/WR",
    LineupSlotId.WR: "WR",
    LineupSlotId.WR_TE: "WR/TE",
    LineupSlotId.TE: "TE",
    LineupSlotId.OFFENSIVE_PLAYER: "OP",
    LineupSlotId.DT: "DT",
    LineupSlotId.DE: "DE",
    LineupSlotId.LB: "LB",
    LineupSlotId.DL: "DL",
    LineupSlotId.CB: "CB",
    LineupSlotId.SAFETY: "S",
    LineupSlotId.DB: "DB",
    LineupSlotId.DEFENSIVE_PLAYER: "DP",
    LineupSlotId.DEFENSE_SPECIAL_TEAMS: "D/ST",
    LineupSlotId.KICKER: "K",
    LineupSlotId.PUNTER: "P",
    LineupSlotId.HEAD_COACH: "HC",
    LineupSlotId.BENCH: "BE",
    LineupSlotId.INJURED_RESERVE: "IR",
    LineupSlotId.RESERVE: "RES",
    LineupSlotId.FLEX: "FLEX",
    LineupSlotId.EMERGENCY_RESERVE: "ER",
}

# Slots that do NOT count as a started lineup position.
BENCH_SLOTS: set[int] = {
    LineupSlotId.BENCH,
    LineupSlotId.INJURED_RESERVE,
    LineupSlotId.RESERVE,
    LineupSlotId.EMERGENCY_RESERVE,
}

LINEUP_SLOT_ORDER: tuple[LineupSlotId, ...] = (
    LineupSlotId.QB,
    LineupSlotId.TEAM_QB,
    LineupSlotId.RB,
    LineupSlotId.RB_WR,
    LineupSlotId.WR,
    LineupSlotId.WR_TE,
    LineupSlotId.TE,
    LineupSlotId.OFFENSIVE_PLAYER,
    LineupSlotId.FLEX,
    LineupSlotId.DEFENSE_SPECIAL_TEAMS,
    LineupSlotId.KICKER,
    LineupSlotId.PUNTER,
    LineupSlotId.HEAD_COACH,
    LineupSlotId.DT,
    LineupSlotId.DE,
    LineupSlotId.LB,
    LineupSlotId.DL,
    LineupSlotId.CB,
    LineupSlotId.SAFETY,
    LineupSlotId.DB,
    LineupSlotId.DEFENSIVE_PLAYER,
    LineupSlotId.BENCH,
    LineupSlotId.INJURED_RESERVE,
    LineupSlotId.RESERVE,
    LineupSlotId.EMERGENCY_RESERVE,
)
_LINEUP_SLOT_RANK = {slot_id: rank for rank, slot_id in enumerate(LINEUP_SLOT_ORDER)}

# defaultPositionId → position label
POSITION_NAMES: dict[int, str] = {
    PositionId.QB: "QB",
    PositionId.RB: "RB",
    PositionId.WR: "WR",
    PositionId.TE: "TE",
    PositionId.KICKER: "K",
    PositionId.DEFENSE_SPECIAL_TEAMS: "D/ST",
}

# proTeamId → current NFL abbreviation.
NFL_TEAM_ABBREVIATIONS: dict[int, str] = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WSH",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}

RosterSection = Literal["starters", "bench", "ir"]

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


def slot_section(slot_id: int) -> RosterSection:
    if slot_id == LineupSlotId.BENCH:
        return "bench"
    if slot_id in {
        LineupSlotId.INJURED_RESERVE,
        LineupSlotId.RESERVE,
        LineupSlotId.EMERGENCY_RESERVE,
    }:
        return "ir"
    return "starters"


def slot_sort_key(slot_id: int) -> tuple[int, int]:
    section_rank = {"starters": 0, "bench": 1, "ir": 2}[slot_section(slot_id)]
    return section_rank, _LINEUP_SLOT_RANK.get(slot_id, len(_LINEUP_SLOT_RANK) + slot_id)


def nfl_team_name(team_id: int | None) -> str | None:
    if team_id is None or team_id == 0:
        return None
    return NFL_TEAM_ABBREVIATIONS.get(team_id, str(team_id))
