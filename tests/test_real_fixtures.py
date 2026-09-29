"""Parser tests against SANITIZED REAL ESPN responses (league 999000001).

Recorded live 2026-07-07 and PII-sanitized (see tests/fixtures/README.md). Values
below were eyeball-verified against the ESPN UI via `python -m api.verify`. The real
account's SWID was mapped to a fixed fake so my-team detection still resolves to the
real team (espn_team_id=1).
"""

import json
from pathlib import Path

from api.services import parse

FIX = Path(__file__).parent / "fixtures"
ME = "{FADE0000-0000-0000-0000-000000000001}"  # fixed fake for the account's SWID


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


# --------------------------------------------------------------------------- #
# 2025 — completed season
# --------------------------------------------------------------------------- #
def test_real_2025_settings():
    s = parse.parse_settings(load("real_league_2025.json"))
    assert s.name == "Synthetic Test League"
    assert s.size == 8
    assert s.scoring_label == "PPR"
    assert s.drafted is True
    assert s.playoff_team_count == 4


def test_real_2025_teams_and_my_team():
    data = load("real_league_2025.json")
    teams = parse.parse_teams(data)
    assert len(teams) == 8
    # SWID-match resolves to espn_team_id=1 (the asserted team).
    assert parse.detect_my_team(teams, ME) == 1
    t1 = next(t for t in teams if t.espn_team_id == 1)
    assert (t1.wins, t1.losses, t1.ties) == (7, 7, 0)
    assert t1.standing == 6
    assert round(t1.points_for, 1) == 2618.1


def test_real_2025_draft_counts_dst_picks():
    drafted, picks = parse.parse_draft(load("real_league_2025.json"))
    assert drafted is True
    assert len(picks) == 168  # matches espn-api cross-check (incl. D/ST)
    dst = [p for p in picks if p.espn_player_id and p.espn_player_id < -1]
    assert len(dst) == 10  # D/ST use large-negative ids, not the -1 empty sentinel
    assert all(p.espn_player_id != -1 for p in picks)


def test_real_2025_matchups_and_boxscore():
    matchups = parse.parse_schedule(load("real_matchups_2025.json"))
    assert len(matchups) >= 8
    completed = [m for m in matchups if (m.home_points or 0) > 0]
    assert completed, "expected some completed-week matchups with points"

    entries = parse.parse_boxscore_week(load("real_boxscore_2025_wk1.json"), 1)
    assert entries, "expected roster entries in week-1 boxscore"
    assert any(e.is_starter for e in entries)
    assert any(e.points is not None for e in entries)


# --------------------------------------------------------------------------- #
# 2026 — pre-draft (empty states)
# --------------------------------------------------------------------------- #
def test_real_2026_pre_draft_state():
    data = load("real_league_2026.json")
    s = parse.parse_settings(data)
    assert s.size == 8
    assert s.scoring_label == "PPR"
    assert s.drafted is False

    drafted, picks = parse.parse_draft(data)
    assert drafted is False
    # 128 empty slots (8x16) with playerId=-1 are filtered → 0 real picks.
    assert picks == []

    teams = parse.parse_teams(data)
    assert len(teams) == 8
    assert parse.detect_my_team(teams, ME) == 1
    # pre-draft: all records zeroed
    assert all((t.wins, t.losses, t.ties) == (0, 0, 0) for t in teams)


def test_real_players_pool_parses():
    players = parse.parse_player_pool(load("real_players_2025.json"))
    assert players
    assert all(p.espn_player_id for p in players)
    assert any(p.espn_adp is not None for p in players)
