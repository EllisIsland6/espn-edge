import logging

import httpx
import pytest

from api.services.discovery import (
    DiscoveryAuthError,
    _parse_fan_profile,
    discover_leagues,
    parse_league_id,
)
from api.services.espn import Cookies


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
    assert _parse_fan_profile([], 2026) == []


def test_parse_fan_profile_excludes_other_seasons():
    def preference(season: int, league_id: int) -> dict:
        return {
            "metaData": {
                "entry": {
                    "abbrev": "FFL",
                    "seasonId": season,
                    "entryId": league_id,
                    "groups": [{"groupId": league_id, "groupName": f"League {season}"}],
                }
            }
        }

    found = _parse_fan_profile(
        {"preferences": [preference(2025, 25), preference(2026, 26)]}, 2026
    )
    assert [(league.espn_league_id, league.season) for league in found] == [("26", 2026)]


def test_parse_fan_profile_skips_malformed_entries_without_crashing():
    found = _parse_fan_profile(
        {
            "preferences": [
                "not-an-object",
                {"metaData": []},
                {
                    "metaData": {
                        "entry": {
                            "abbrev": "FFL",
                            "seasonId": "not-a-season",
                            "groups": [{"groupId": 901}],
                        }
                    }
                },
                {
                    "metaData": {
                        "entry": {
                            "abbrev": "FFL",
                            "seasonId": 2026,
                            "entryId": "not-a-team",
                            "groups": [{"groupId": 902, "groupName": "Still Valid"}],
                        }
                    }
                },
            ]
        },
        2026,
    )
    assert [(league.espn_league_id, league.team_id) for league in found] == [("902", None)]


def test_discovery_request_never_logs_credentials(caplog):
    swid = "{SWID-DISCOVERY-CANARY}"
    espn_s2 = "S2-DISCOVERY-CANARY"

    def handler(request: httpx.Request) -> httpx.Response:
        assert swid.strip("{}") in str(request.url)
        assert espn_s2 in request.headers["cookie"]
        return httpx.Response(200, json={"preferences": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with caplog.at_level(logging.DEBUG):
            assert discover_leagues(
                Cookies(swid=swid, espn_s2=espn_s2), season=2026, client=client
            ) == []

    assert swid not in caplog.text
    assert swid.strip("{}") not in caplog.text
    assert espn_s2 not in caplog.text


def test_discovery_raises_safe_auth_error_for_expired_session():
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401))
    ) as client:
        with pytest.raises(DiscoveryAuthError, match="session expired"):
            discover_leagues(
                Cookies(swid="{EXPIRED}", espn_s2="expired-s2"),
                season=2026,
                client=client,
            )
