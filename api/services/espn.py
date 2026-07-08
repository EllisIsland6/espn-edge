"""EspnService — the workhorse client for ESPN's unofficial v3 JSON API (SPEC 2).

Design decisions locked to SPEC Section 2 (do not change without asking):
  - Base host from config only (SPEC 2.2), 2018+ league endpoint vs pre-2018
    leagueHistory archive.
  - Auth via SWID + espn_s2 cookies only; no login automation (SPEC 2.3, guardrail 11).
  - Views are stacked as repeated `view=` params (SPEC 2.4); player-pool caps are
    lifted with the X-Fantasy-Filter *header* (SPEC 2.5).
  - ~1 req/sec throttle per account, exponential backoff on 429/5xx (SPEC 2.10).
  - Every raw JSON response is cached (SPEC 2.10) — see RawCacheStore.

SPEC 2.8 names `espn-api` (cwendt94) as the documented fallback client; it is a
listed dependency and can be enabled for debugging, but the sync pipeline parses
raw JSON directly so tests replay recorded fixtures offline (SPEC 12).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from ..config import get_settings

# Browser-like headers — ESPN rejects some default clients (SPEC 2.3).
_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _short_hash(value: str) -> str:
    """Stable 8-char hash for cache-key scoping (never reveals the input)."""
    return hashlib.sha1(value.encode()).hexdigest()[:8]


class EspnError(RuntimeError):
    """Any non-auth failure talking to ESPN."""


class EspnAuthError(EspnError):
    """401/403 — cookies are missing/expired. Caller flips account to needs_reauth."""


@dataclass
class Cookies:
    """A single account's (SWID, espn_s2) pair.

    `espn_s2` may be mutated in place by the client if the URL-decoded variant is
    the one that authenticates, so callers can persist whichever worked (SPEC 2.3).
    """

    swid: str
    espn_s2: str


class RawCacheStore:
    """Read-through/write-through cache for raw ESPN JSON, backed by `raw_cache`.

    Kept as a small protocol-ish object so tests can inject a dict-backed stub and
    the service never needs a live DB.
    """

    def __init__(self, ttl: timedelta = timedelta(hours=6)):
        self.ttl = ttl

    def get(self, key: str) -> dict | None:  # pragma: no cover - overridden by DB store
        return None

    def set(self, key: str, payload: dict) -> None:  # pragma: no cover
        return None

    def fresh(self, fetched_at: datetime) -> bool:
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        return datetime.now(UTC) - fetched_at < self.ttl


class EspnService:
    def __init__(
        self,
        host: str | None = None,
        client: httpx.Client | None = None,
        cache: RawCacheStore | None = None,
        min_interval: float = 1.0,
        max_retries: int = 4,
    ):
        settings = get_settings()
        self.host = (host or settings.espn_api_host).rstrip("/")
        self._client = client or httpx.Client(timeout=30.0, headers=_BASE_HEADERS)
        self._owns_client = client is None
        self.cache = cache
        self.min_interval = min_interval
        self.max_retries = max_retries
        # last request time per throttle key (per account swid, or "public").
        self._last_req: dict[str, float] = {}

    # ---- URL construction (SPEC 2.2) ----------------------------------------
    def league_url(self, league_id: str | int, season: int) -> str:
        if season >= 2018:
            return f"{self.host}/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"
        # Pre-2018 historical archive returns a JSON array; caller takes [0].
        return f"{self.host}/apis/v3/games/ffl/leagueHistory/{league_id}"

    def players_url(self, season: int, defaults: bool = True) -> str:
        if defaults:
            # leaguedefaults/3 = ESPN's PPR default scoring universe (SPEC 2.2/2.9).
            return f"{self.host}/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3"
        return f"{self.host}/apis/v3/games/ffl/seasons/{season}/players"

    # ---- throttle + backoff (SPEC 2.10) -------------------------------------
    def _throttle(self, key: str) -> None:
        last = self._last_req.get(key)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_req[key] = time.monotonic()

    def _request(
        self, url: str, *, params: list[tuple[str, str]], headers: dict, throttle_key: str
    ) -> httpx.Response:
        backoff = 1.0
        last_exc: Exception | None = None
        for _attempt in range(self.max_retries):
            self._throttle(throttle_key)
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # network hiccup
                last_exc = exc
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = EspnError(f"ESPN {resp.status_code} on {url}")
                time.sleep(backoff)
                backoff *= 2
                continue
            return resp
        raise EspnError(
            f"ESPN request failed after {self.max_retries} attempts: {url}"
        ) from last_exc

    def _cookie_header(self, cookies: Cookies) -> dict:
        return {"Cookie": f"SWID={cookies.swid}; espn_s2={cookies.espn_s2}"}

    # ---- core fetch ----------------------------------------------------------
    def fetch_views(
        self,
        league_id: str | int,
        season: int,
        views: list[str],
        *,
        scoring_period: int | None = None,
        cookies: Cookies | None = None,
        x_fantasy_filter: dict | None = None,
        bust_cache: bool = False,
    ) -> Any:
        """Fetch one or more stacked views for a league. Returns parsed JSON.

        Private leagues pass `cookies`; on an auth failure with a URL-encoded
        espn_s2 we retry once with the decoded value and, if it works, write the
        working value back onto `cookies.espn_s2` so the caller can persist it.
        """
        cache_key = self._cache_key(
            league_id, season, views, scoring_period, cookies, x_fantasy_filter
        )
        if self.cache is not None and not bust_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        params = [("view", v) for v in views]
        if scoring_period is not None:
            params.append(("scoringPeriodId", str(scoring_period)))

        headers = dict(_BASE_HEADERS)
        if x_fantasy_filter is not None:
            headers["X-Fantasy-Filter"] = json.dumps(x_fantasy_filter)

        data = self._get_authed(
            self.league_url(league_id, season),
            params=params,
            headers=headers,
            cookies=cookies,
            throttle_key=cookies.swid if cookies else "public",
        )
        # Pre-2018 archive returns a JSON array; take the first element (SPEC 2.2).
        if season < 2018 and isinstance(data, list):
            data = data[0] if data else {}

        if self.cache is not None:
            self.cache.set(cache_key, data)
        return data

    def raw_view(
        self,
        league_id: str | int,
        season: int,
        views: list[str],
        scoring_period: int | None = None,
        x_fantasy_filter: dict | None = None,
        cookies: Cookies | None = None,
    ) -> Any:
        """Thin raw escape hatch (SPEC 2.8) — same as fetch_views, explicit name."""
        return self.fetch_views(
            league_id,
            season,
            views,
            scoring_period=scoring_period,
            cookies=cookies,
            x_fantasy_filter=x_fantasy_filter,
            bust_cache=True,
        )

    def fetch_player_pool(
        self,
        season: int,
        *,
        cookies: Cookies | None = None,
        limit: int = 1500,
    ) -> Any:
        """kona_player_info pull with the X-Fantasy-Filter header (SPEC 2.5/2.9)."""
        x_filter = {
            "players": {
                "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
                "limit": limit,
                "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            }
        }
        headers = dict(_BASE_HEADERS)
        headers["X-Fantasy-Filter"] = json.dumps(x_filter)
        return self._get_authed(
            self.players_url(season, defaults=True),
            params=[("view", "kona_player_info")],
            headers=headers,
            cookies=cookies,
            throttle_key=cookies.swid if cookies else "public",
        )

    # ---- auth-aware GET ------------------------------------------------------
    def _get_authed(
        self,
        url: str,
        *,
        params: list[tuple[str, str]],
        headers: dict,
        cookies: Cookies | None,
        throttle_key: str,
    ) -> Any:
        if cookies is None:
            resp = self._request(url, params=params, headers=headers, throttle_key="public")
            return self._json_or_auth(resp)

        # Attempt 1: cookies as stored.
        h = {**headers, **self._cookie_header(cookies)}
        resp = self._request(url, params=params, headers=h, throttle_key=throttle_key)
        if resp.status_code not in (401, 403):
            return self._json_or_auth(resp)

        # Attempt 2 (once): URL-decoded espn_s2 (SPEC 2.3 gotcha).
        decoded = unquote(cookies.espn_s2)
        if decoded != cookies.espn_s2:
            retry = Cookies(swid=cookies.swid, espn_s2=decoded)
            h2 = {**headers, **self._cookie_header(retry)}
            resp2 = self._request(url, params=params, headers=h2, throttle_key=throttle_key)
            if resp2.status_code not in (401, 403):
                cookies.espn_s2 = decoded  # persist the variant that worked
                return self._json_or_auth(resp2)

        raise EspnAuthError(f"ESPN auth failed ({resp.status_code}) for {url}")

    @staticmethod
    def _json_or_auth(resp: httpx.Response) -> Any:
        if resp.status_code in (401, 403):
            raise EspnAuthError(f"ESPN auth failed ({resp.status_code}) for {resp.request.url}")
        if resp.status_code >= 400:
            raise EspnError(f"ESPN {resp.status_code} for {resp.request.url}")
        try:
            return resp.json()
        except ValueError as exc:
            raise EspnError(f"non-JSON response from {resp.request.url}") from exc

    @staticmethod
    def _cache_key(
        league_id: str | int,
        season: int,
        views: list[str],
        scoring_period: int | None,
        cookies: Cookies | None = None,
        x_fantasy_filter: dict | None = None,
    ) -> str:
        """Cache key scoped by account + filter so identical view-sets under a
        different account (cross-account leagues) or a different X-Fantasy-Filter
        never collide (SPEC 2.10). The SWID is hashed, never stored raw.
        """
        vs = "+".join(sorted(views))
        # Account identity: short hash of the SWID (or "public"). Never the raw SWID.
        ident = _short_hash(cookies.swid) if cookies else "public"
        filt = "-"
        if x_fantasy_filter:
            filt = _short_hash(json.dumps(x_fantasy_filter, sort_keys=True))
        return f"{league_id}:{season}:{vs}:sp={scoring_period}:acct={ident}:filt={filt}"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EspnService:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def cookies_for_account(account) -> Cookies | None:
    """Build a Cookies pair from an Account row, decrypting espn_s2 (SPEC 2.3)."""
    from ..crypto import decrypt

    if not account or not account.swid:
        return None
    return Cookies(swid=account.swid, espn_s2=decrypt(account.espn_s2_encrypted))
