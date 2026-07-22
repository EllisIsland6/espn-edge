"""League discovery per account (SPEC 2.6).

Path 1: the fan-profile API (fan.api.espn.com) — attempted, verify at build time.
Path 2 (guaranteed): manual add by league id / URL, handled in the leagues router.

This module never raises to the caller for a shape mismatch; it returns [] and lets
the manual fallback carry the flow (SPEC 2.6: "Do not let this block Phase 1").
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from ..config import get_settings
from .espn import _BASE_HEADERS, Cookies

# The fan-profile host is separate from the read API host (SPEC 2.6).
FAN_HOST = "https://fan.api.espn.com"
# ffl = fantasy football; used to filter the fan's entries to NFL redraft.
_FFL_GAME_ABBREVS = {"ffl"}

# HTTPX logs complete request URLs at INFO. The fan-profile path necessarily
# contains the SWID, so suppress its request log only while this call is active.
_DISCOVERY_REQUEST_ACTIVE: ContextVar[bool] = ContextVar(
    "espn_discovery_request_active", default=False
)


class _DiscoveryCredentialLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _DISCOVERY_REQUEST_ACTIVE.get()


logging.getLogger("httpx").addFilter(_DiscoveryCredentialLogFilter())


class DiscoveryAuthError(RuntimeError):
    """The account's ESPN session is no longer accepted."""


@dataclass
class DiscoveredLeague:
    espn_league_id: str
    name: str | None
    season: int | None
    team_id: int | None


def _fan_url(swid: str) -> str:
    # SWID must keep its braces and be URL-encoded (SPEC 2.6).
    return f"{FAN_HOST}/apis/v2/fans/{quote(swid, safe='')}"


def discover_leagues(
    cookies: Cookies, season: int | None = None, client: httpx.Client | None = None
) -> list[DiscoveredLeague]:
    """Best-effort discovery; only an explicit auth failure is raised to the caller."""
    season = season or get_settings().season
    owns = client is None
    client = client or httpx.Client(timeout=30.0, headers=_BASE_HEADERS)
    try:
        headers = {
            **_BASE_HEADERS,
            "Cookie": f"SWID={cookies.swid}; espn_s2={cookies.espn_s2}",
        }
        token = _DISCOVERY_REQUEST_ACTIVE.set(True)
        try:
            resp = client.get(_fan_url(cookies.swid), headers=headers)
        finally:
            _DISCOVERY_REQUEST_ACTIVE.reset(token)
        if resp.status_code in {401, 403}:
            raise DiscoveryAuthError("ESPN session expired")
        if resp.status_code >= 400:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        return _parse_fan_profile(data, season)
    except httpx.HTTPError:
        return []
    finally:
        if owns:
            client.close()


def _parse_fan_profile(data: dict, season: int) -> list[DiscoveredLeague]:
    """Parse the fan-profile 'preferences' entries into fantasy leagues.

    The exact shape is under-documented (SPEC 2.6). We defensively pull the common
    fields and skip anything we can't map, rather than trusting a rigid schema.
    """
    if not isinstance(data, dict):
        return []
    out: list[DiscoveredLeague] = []
    prefs = data.get("preferences") or []
    for pref in prefs:
        if not isinstance(pref, dict):
            continue
        meta = pref.get("metaData") or {}
        if not isinstance(meta, dict):
            continue
        entry = meta.get("entry") or {}
        if not isinstance(entry, dict) or not entry:
            continue
        # gameAbbrev / abbrev tells us this is fantasy football.
        abbrev = str(entry.get("abbrev") or entry.get("gameAbbrev") or "").lower()
        groups = entry.get("groups") or []
        league_id = None
        league_name = None
        if isinstance(groups, list) and groups and isinstance(groups[0], dict):
            g0 = groups[0]
            league_id = g0.get("groupId") or g0.get("id")
            league_name = g0.get("groupName") or g0.get("name")
        if abbrev and abbrev not in _FFL_GAME_ABBREVS:
            continue
        if league_id is None:
            continue

        entry_season = entry.get("seasonId") or entry.get("season")
        try:
            normalized_season = int(entry_season) if entry_season else int(season)
        except (TypeError, ValueError):
            continue
        if season and normalized_season != int(season):
            continue

        team_id = entry.get("entryId") or entry.get("teamId")
        try:
            normalized_team_id = int(team_id) if team_id else None
        except (TypeError, ValueError):
            normalized_team_id = None
        out.append(
            DiscoveredLeague(
                espn_league_id=str(league_id),
                name=league_name,
                season=normalized_season,
                team_id=normalized_team_id,
            )
        )
    return out


_LEAGUE_ID_RE = re.compile(r"leagueId=(\d+)")


def parse_league_id(raw: str) -> str:
    """Accept a bare league id or a league URL and return the id (SPEC 2.6 fallback)."""
    raw = raw.strip()
    m = _LEAGUE_ID_RE.search(raw)
    if m:
        return m.group(1)
    if raw.isdigit():
        return raw
    # last-ditch: first run of digits
    m2 = re.search(r"(\d{4,})", raw)
    if m2:
        return m2.group(1)
    raise ValueError(f"could not parse a league id from {raw!r}")
