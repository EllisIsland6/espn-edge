"""EspnService unit tests — URL building, cache keys, cookie auth retry.

Uses a fake httpx.Client transport so no real network is touched (SPEC 12).
"""

import httpx
import pytest

from api.services.espn import Cookies, EspnAuthError, EspnService


def test_league_url_switches_on_season():
    svc = EspnService(host="https://host")
    assert svc.league_url(5, 2026).endswith("/seasons/2026/segments/0/leagues/5")
    assert "leagueHistory/5" in svc.league_url(5, 2017)


def test_cache_key_is_order_independent():
    k1 = EspnService._cache_key(1, 2026, ["mTeam", "mSettings"], None)
    k2 = EspnService._cache_key(1, 2026, ["mSettings", "mTeam"], None)
    assert k1 == k2
    assert "sp=3" in EspnService._cache_key(1, 2026, ["mBoxscore"], 3)


def test_cache_key_scoped_by_account_and_filter():
    base = EspnService._cache_key(1, 2026, ["mTeam"], None)
    a = EspnService._cache_key(1, 2026, ["mTeam"], None, Cookies(swid="{A}", espn_s2="x"))
    b = EspnService._cache_key(1, 2026, ["mTeam"], None, Cookies(swid="{B}", espn_s2="x"))
    # public vs each account vs cross-account are all distinct keys.
    assert base != a != b and a != b
    # raw SWID never appears in the key.
    assert "{A}" not in a and "{B}" not in b
    # different X-Fantasy-Filter → different key.
    f1 = EspnService._cache_key(1, 2026, ["kona_player_info"], None, None, {"limit": 100})
    f2 = EspnService._cache_key(1, 2026, ["kona_player_info"], None, None, {"limit": 200})
    assert f1 != f2


def _client_returning(status_by_cookie: dict[str, int], body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("Cookie", "")
        # match on the espn_s2 value present in the cookie header
        for needle, status in status_by_cookie.items():
            if needle in cookie:
                if status == 200:
                    return httpx.Response(200, json=body)
                return httpx.Response(status, text="denied")
        return httpx.Response(401, text="no match")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_public_fetch_no_cookies():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": 1}))
    )
    svc = EspnService(host="https://host", client=client, min_interval=0)
    assert svc.fetch_views(1, 2026, ["mTeam"]) == {"ok": 1}


def test_auth_retry_with_url_decoded_espn_s2():
    # Stored value is URL-encoded and fails; decoded value works. (SPEC 2.3 gotcha)
    encoded = "abc%2Fdef%3D"
    decoded = "abc/def="
    client = _client_returning({encoded: 401, decoded: 200}, {"ok": 1})
    svc = EspnService(host="https://host", client=client, min_interval=0)
    cookies = Cookies(swid="{S}", espn_s2=encoded)
    out = svc.fetch_views(1, 2026, ["mTeam"], cookies=cookies)
    assert out == {"ok": 1}
    # working variant persisted back onto the cookie object
    assert cookies.espn_s2 == decoded


def test_auth_failure_raises_auth_error():
    client = _client_returning({"anything": 401}, {})
    svc = EspnService(host="https://host", client=client, min_interval=0)
    with pytest.raises(EspnAuthError):
        svc.fetch_views(1, 2026, ["mTeam"], cookies=Cookies(swid="{S}", espn_s2="plainvalue"))
