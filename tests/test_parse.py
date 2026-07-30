"""Offline parser tests against the recorded fixture (SPEC 12).

Truth table for the 4-team toy league:
  - PPR (reception statId 53 = 1.0 pt)
  - my SWID {aaaa-1111} owns team 1
  - 8 draft picks; team 4 autodrafted every pick
  - week 1 complete (currentMatchupPeriod=2); week 2 future (0 pts)
"""

from api.services import parse


def test_parse_settings(league_fixture):
    s = parse.parse_settings(league_fixture)
    assert s.name == "Test Public League"
    assert s.size == 4
    assert s.scoring_label == "PPR"
    assert s.draft_type == "SNAKE"
    assert s.playoff_team_count == 2
    assert s.current_week == 2
    assert s.current_scoring_period == 2
    assert s.current_matchup_period == 2
    assert s.drafted is True
    assert s.lineup_slots["0"] == 1  # 1 QB slot


def test_scoring_half_ppr_and_standard():
    assert parse.classify_scoring({"scoringItems": [{"statId": 53, "points": 0.5}]}) == "Half-PPR"
    assert parse.classify_scoring({"scoringItems": [{"statId": 53, "points": 0.0}]}) == "Standard"


def test_parse_teams_and_my_team(league_fixture):
    teams = parse.parse_teams(league_fixture)
    assert len(teams) == 4
    alpha = next(t for t in teams if t.espn_team_id == 1)
    assert alpha.name == "Alpha"
    assert alpha.wins == 1 and alpha.losses == 0
    assert alpha.points_for == 120.5
    # Case-insensitive, brace-insensitive SWID match (SPEC 2.7).
    assert parse.detect_my_team(teams, "{aaaa-1111}") == 1
    assert parse.detect_my_team(teams, "AAAA-1111") == 1
    assert parse.detect_my_team(teams, "{ZZZZ-0000}") is None


def test_parse_draft(league_fixture):
    drafted, picks = parse.parse_draft(league_fixture)
    assert drafted is True
    assert len(picks) == 8
    first = picks[0]
    assert first.overall == 1 and first.round == 1 and first.espn_team_id == 1
    # team 4 (picks overall 4 & 5) are autodraft.
    team4 = [p for p in picks if p.espn_team_id == 4]
    assert all(p.autodraft for p in team4)
    assert not picks[0].autodraft


def test_parse_schedule(league_fixture):
    m = parse.parse_schedule(league_fixture)
    assert len(m) == 4
    wk1 = [x for x in m if x.week == 1]
    assert len(wk1) == 2
    game = next(x for x in wk1 if x.home_espn_team_id == 1)
    assert game.home_points == 120.5 and game.away_points == 100.0
    assert game.home_projected_points is None
    assert game.is_playoff is False


def test_parse_current_roster_and_pro_schedule(
    current_roster_fixture,
    pro_schedule_fixture,
):
    settings = parse.parse_settings(current_roster_fixture)
    assert settings.current_scoring_period == 1
    assert settings.current_matchup_period == 1
    games = parse.parse_pro_schedule(pro_schedule_fixture, 1)
    assert games[1].opponent == "BUF"
    assert games[1].game_status == "pregame"
    assert games[1].kickoff_at is not None

    entries = parse.parse_current_rosters(
        current_roster_fixture,
        scoring_period=1,
        matchup_period=1,
        pro_schedule=games,
    )
    mine = [entry for entry in entries if entry.espn_team_id == 1]
    assert len(mine) == 5
    quarterback = next(entry for entry in mine if entry.espn_player_id == 1001)
    assert quarterback.slot_id == 0
    assert quarterback.slot_index == 0
    assert quarterback.actual_points == 0.0
    assert quarterback.projected_points == 18.5
    assert quarterback.nfl_team == "ATL"
    assert quarterback.opponent == "BUF"
    assert quarterback.injury_status == "QUESTIONABLE"
    running_backs = [entry for entry in mine if entry.slot_id == 2]
    assert [entry.slot_index for entry in running_backs] == [0, 1]


def test_parse_live_matchup_totals(current_roster_fixture):
    matchup = parse.parse_schedule(current_roster_fixture)[0]
    assert matchup.home_points == 0.0
    assert matchup.away_points == 0.0
    assert matchup.home_projected_points == 108.4
    assert matchup.away_projected_points == 111.2


def test_parse_boxscore_week(league_fixture):
    entries = parse.parse_boxscore_week(league_fixture, 1)
    # team1 has a QB starter + a bench player.
    t1 = [e for e in entries if e.espn_team_id == 1]
    starter = next(e for e in t1 if e.espn_player_id == 1001)
    bench = next(e for e in t1 if e.espn_player_id == 1008)
    assert starter.is_starter is True and starter.slot == "QB" and starter.points == 25.0
    assert bench.is_starter is False and bench.slot == "BE"


def test_parse_transactions(league_fixture):
    txns = parse.parse_transactions(league_fixture)
    assert len(txns) == 2
    waiver = next(t for t in txns if t.type == "waiver")
    assert waiver.player_in == 2001 and waiver.player_out == 2002 and waiver.bid == 5
    # processDate (epoch ms) -> tz-aware datetime.
    assert waiver.executed_at is not None
    assert waiver.executed_at.tzinfo is not None
    fa = next(t for t in txns if t.type == "fa_add")
    assert fa.player_in == 2003
    # falls back to proposedDate when processDate absent.
    assert fa.executed_at is not None


def test_parse_player_pool(players_fixture):
    players = parse.parse_player_pool(players_fixture)
    assert len(players) == 2
    qb = next(p for p in players if p.espn_player_id == 1001)
    assert qb.position == "QB"
    assert qb.espn_adp == 3.4
    assert qb.espn_rank_ppr == 2
    # proj_ros = season-split projection (statSourceId=1, statSplitTypeId=0),
    # not the single-game (18.5) or the actual (120.0).
    assert qb.proj_ros == 305.7
    rb = next(p for p in players if p.espn_player_id == 1002)
    assert rb.proj_ros == 281.3
