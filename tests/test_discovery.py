from api.services.discovery import _parse_fan_profile, parse_league_id


def test_parse_league_id_variants():
    assert parse_league_id("123456") == "123456"
    assert (
        parse_league_id("https://fantasy.espn.com/football/league?leagueId=987654&seasonId=2026")
        == "987654"
    )
    assert parse_league_id("  leagueId=42 ") == "42"


def test_parse_fan_profile_extracts_ffl_leagues():
    data = {
        "preferences": [
            {
                "metaData": {
                    "entry": {
                        "abbrev": "FFL",
                        "seasonId": 2026,
                        "entryId": 7,
                        "groups": [{"groupId": 555, "groupName": "Dynasty Warriors"}],
                    }
                }
            },
            {
                "metaData": {
                    "entry": {
                        "abbrev": "FBA",  # basketball — should be skipped
                        "seasonId": 2026,
                        "groups": [{"groupId": 999, "groupName": "Hoops"}],
                    }
                }
            },
        ]
    }
    found = _parse_fan_profile(data, 2026)
    assert len(found) == 1
    assert found[0].espn_league_id == "555"
    assert found[0].name == "Dynasty Warriors"
    assert found[0].team_id == 7


def test_parse_fan_profile_handles_empty():
    assert _parse_fan_profile({}, 2026) == []
    assert _parse_fan_profile({"preferences": []}, 2026) == []
