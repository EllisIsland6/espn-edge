"""Same-origin proxy for public ESPN NFL player portraits and D/ST logos."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/players", tags=["players"])

_NFL_TEAM_SLUG = {
    1: "atl", 2: "buf", 3: "chi", 4: "cin", 5: "cle", 6: "dal", 7: "den",
    8: "det", 9: "gb", 10: "ten", 11: "ind", 12: "kc", 13: "lv", 14: "lar",
    15: "mia", 16: "min", 17: "ne", 18: "no", 19: "nyg", 20: "nyj", 21: "phi",
    22: "ari", 23: "pit", 24: "lac", 25: "sf", 26: "sea", 27: "tb", 28: "wsh",
    29: "car", 30: "jax", 33: "bal", 34: "hou",
}
_NFL_ABBR_SLUG = {
    "ARI": "ari", "ATL": "atl", "BAL": "bal", "BUF": "buf", "CAR": "car",
    "CHI": "chi", "CIN": "cin", "CLE": "cle", "DAL": "dal", "DEN": "den",
    "DET": "det", "GB": "gb", "HOU": "hou", "IND": "ind", "JAX": "jax",
    "KC": "kc", "LAC": "lac", "LAR": "lar", "LV": "lv", "MIA": "mia",
    "MIN": "min", "NE": "ne", "NO": "no", "NYG": "nyg", "NYJ": "nyj",
    "PHI": "phi", "PIT": "pit", "SEA": "sea", "SF": "sf", "TB": "tb",
    "TEN": "ten", "WAS": "wsh", "WSH": "wsh",
}


def portrait_source_url(espn_player_id: int) -> str | None:
    if espn_player_id > 0:
        return f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_player_id}.png"
    nfl_team_id = -espn_player_id - 16000
    slug = _NFL_TEAM_SLUG.get(nfl_team_id)
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{slug}.png" if slug else None


def team_logo_source_url(team: str) -> str | None:
    slug = _NFL_ABBR_SLUG.get(team.strip().upper())
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{slug}.png" if slug else None


def _image_response(source: str, label: str, *, request_label: str | None = None) -> Response:
    try:
        upstream = httpx.get(source, timeout=10.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"ESPN {request_label or label} request failed") from exc
    content_type = upstream.headers.get("content-type", "")
    if upstream.status_code != 200 or not content_type.startswith("image/"):
        raise HTTPException(404, f"{label} unavailable")
    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400, stale-if-error=604800"},
    )


@router.get("/{espn_player_id}/portrait", response_class=Response)
def player_portrait(espn_player_id: int) -> Response:
    source = portrait_source_url(espn_player_id)
    if source is None:
        raise HTTPException(404, "player portrait unavailable")
    return _image_response(source, "player portrait", request_label="portrait")


@router.get("/team-logo/{team}", response_class=Response)
def team_logo(team: str) -> Response:
    source = team_logo_source_url(team)
    if source is None:
        raise HTTPException(404, "team logo unavailable")
    return _image_response(source, "team logo")
