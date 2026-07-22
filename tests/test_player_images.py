"""Player portrait proxy stays same-origin and never forwards account credentials."""

import httpx
from fastapi.testclient import TestClient

from api.main import app
from api.routers import player_images

client = TestClient(app)


def test_portrait_source_urls_cover_players_and_defenses():
    assert player_images.portrait_source_url(4262921) == (
        "https://a.espncdn.com/i/headshots/nfl/players/full/4262921.png"
    )
    assert player_images.portrait_source_url(-16016) == (
        "https://a.espncdn.com/i/teamlogos/nfl/500/min.png"
    )
    assert player_images.portrait_source_url(-16999) is None


def test_player_portrait_proxies_image_without_cookies(monkeypatch):
    called: dict = {}

    class Upstream:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"fake-png"

    def fake_get(url, **kwargs):
        called.update(url=url, kwargs=kwargs)
        return Upstream()

    monkeypatch.setattr(player_images.httpx, "get", fake_get)
    response = client.get("/api/players/4262921/portrait")
    assert response.status_code == 200 and response.content == b"fake-png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"].startswith("public, max-age=86400")
    assert called["url"].endswith("/4262921.png")
    assert "headers" not in called["kwargs"]


def test_player_portrait_fails_safely_when_espn_is_offline(monkeypatch):
    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(player_images.httpx, "get", offline)
    response = client.get("/api/players/4262921/portrait")

    assert response.status_code == 502
    assert response.json() == {"detail": "ESPN portrait request failed"}
