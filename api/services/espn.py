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
from . import telemetry

# Browser-like headers — ESPN rejects some default clients (SPEC 2.3).
_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _header_int(headers, key: str) -> int:
    """A header as an int, or -1. Cannot raise: a provider may send anything.

    `wire_bytes` is Content-Length AS SENT, read from the header rather than
    counted off the wire, which is why a fixture response can carry it.
    """
    raw = headers.get(key)
    if raw is None:
        return -1
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return -1
    return parsed if parsed >= 0 else -1


def _short_hash(value: str) -> str:
    """Stable 8-char hash for cache-key scoping (never reveals the input)."""
    return hashlib.sha1(value.encode()).hexdigest()[:8]


class EspnError(RuntimeError):
    """Any non-auth failure talking to ESPN."""


class EspnAuthError(EspnError):
    """401/403 — cookies are missing/expired. Caller flips account to needs_reauth."""


class EspnReauthRequired(EspnAuthError):
    """Stored account is deliberately unusable until explicit reauthentication."""


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


class HostedModeForbidden(RuntimeError):
    """Raised when public hosted mode attempts a real-provider operation.

    Phase 31 makes the hosted boundary structural rather than procedural: the
    dangerous object cannot be constructed and the credential path cannot be
    reached, so a future call site inherits the guarantee instead of reopening
    the hole. The message is fixed and carries no configuration detail.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"hosted synthetic mode forbids {operation}")
        self.operation = operation


def _forbid_in_hosted_mode(operation: str) -> None:
    if get_settings().is_public_synthetic:
        raise HostedModeForbidden(operation)


class EspnService:
    def __init__(
        self,
        host: str | None = None,
        client: httpx.Client | None = None,
        cache: RawCacheStore | None = None,
        min_interval: float = 1.0,
        max_retries: int = 4,
        recorder: telemetry.Recorder | None = None,
    ):
        _forbid_in_hosted_mode("real provider construction")
        settings = get_settings()
        self.host = (host or settings.espn_api_host).rstrip("/")
        self._client = client or httpx.Client(timeout=30.0, headers=_BASE_HEADERS)
        self._owns_client = client is None
        self.cache = cache
        self.min_interval = min_interval
        self.max_retries = max_retries
        # last request time per throttle key (per account swid, or "public").
        self._last_req: dict[str, float] = {}
        # Phase 32: one row per HTTP attempt, and nothing else. Flag off means
        # None, and a provider holding None records nothing -- there is no branch
        # to get wrong at each call site. An explicit `recorder=` wins over the
        # flag so a test can hold its own rows without touching global state.
        if recorder is not None:
            self._recorder: telemetry.Recorder | None = recorder
        elif settings.telemetry_enabled:
            self._recorder = telemetry.shared_recorder()
        else:
            self._recorder = None

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

    def season_url(self, season: int) -> str:
        return f"{self.host}/apis/v3/games/ffl/seasons/{season}"

    # ---- throttle + backoff (SPEC 2.10) -------------------------------------
    def _throttle(self, key: str) -> int:
        """Space requests per key, returning the milliseconds waited.

        The return value is new in Phase 32 and is the only change: `throttle_ms`
        has to come from the component that actually blocked. Nothing reads the
        old `None`.
        """
        waited = 0.0
        last = self._last_req.get(key)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
                waited = wait
        self._last_req[key] = time.monotonic()
        return int(waited * 1000)

    def _request(
        self,
        url: str,
        *,
        params: list[tuple[str, str]],
        headers: dict,
        throttle_key: str,
        shape: telemetry.Shape | None = None,
    ) -> httpx.Response:
        """Issue one logical call, retrying per SPEC 2.10.

        `shape` is **optional** and defaults to None, deliberately. Seven unit-2
        probes call this method directly with no such keyword, and a required
        keyword-only parameter would break all seven and fail the criterion that
        requires unit 2's suite to pass unchanged. Nothing is recorded when it is
        None, which is also what the flag-off path produces.
        """
        backoff = 1.0
        last_exc: Exception | None = None
        for attempt_index in range(self.max_retries):
            throttle_ms = self._throttle(throttle_key)
            last = attempt_index == self.max_retries - 1
            started = time.monotonic()
            try:
                resp = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # network hiccup
                net_ms = int((time.monotonic() - started) * 1000)
                last_exc = exc
                self._file(
                    shape,
                    telemetry.Outcome.EXHAUSTED if last else telemetry.Outcome.TRANSPORT_ERROR,
                    attempt=attempt_index + 1,
                    status=0,
                    resp=None,
                    net_ms=net_ms,
                    throttle_ms=throttle_ms,
                    backoff_ms=int(backoff * 1000),
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            net_ms = int((time.monotonic() - started) * 1000)
            retryable = resp.status_code in (429, 500, 502, 503, 504)
            if retryable:
                last_exc = EspnError(f"ESPN {resp.status_code} on {url}")
                self._file(
                    shape,
                    telemetry.Outcome.EXHAUSTED if last else telemetry.Outcome.RETRYABLE_STATUS,
                    attempt=attempt_index + 1,
                    status=resp.status_code,
                    resp=resp,
                    net_ms=net_ms,
                    throttle_ms=throttle_ms,
                    backoff_ms=int(backoff * 1000),
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            self._file(
                shape,
                telemetry.Outcome.OK,
                attempt=attempt_index + 1,
                status=resp.status_code,
                resp=resp,
                net_ms=net_ms,
                throttle_ms=throttle_ms,
                backoff_ms=0,
            )
            return resp
        raise EspnError(
            f"ESPN request failed after {self.max_retries} attempts: {url}"
        ) from last_exc

    def _file(
        self,
        shape: telemetry.Shape | None,
        outcome: telemetry.Outcome,
        *,
        attempt: int,
        status: int,
        resp: httpx.Response | None,
        net_ms: int,
        throttle_ms: int,
        backoff_ms: int,
    ) -> None:
        """Hand one attempt to the recorder. Never alters what the caller gets.

        `gate_ms` is always 0: this phase does not call the rate gate, so the
        field is a structural zero and the report labels it as one rather than
        publishing a percentile over a column of zeros as though it measured
        waiting. `backoff_ms` is the NOMINAL ladder value, not elapsed sleep --
        the suite patches `time.sleep` so nothing elapses, and the report's
        occupancy model consumes this as wall time.
        """
        if self._recorder is None or shape is None:
            return
        if resp is None:
            wire = -1
            decoded = -1
            etag = False
            last_modified = False
            content_type_json = False
        else:
            headers = resp.headers
            wire = _header_int(headers, "content-length")
            try:
                decoded = len(resp.content)
            except Exception:
                decoded = -1
            etag = "etag" in headers
            last_modified = "last-modified" in headers
            content_type_json = "json" in str(headers.get("content-type", "")).lower()
        self._recorder.record(
            {
                "shape": shape,
                "outcome": outcome,
                "attempt": attempt,
                "status": status,
                "wire_bytes": wire,
                "decoded_bytes": decoded,
                "net_ms": net_ms,
                "gate_ms": 0,
                "throttle_ms": throttle_ms,
                "backoff_ms": backoff_ms,
                "etag": etag,
                "last_modified": last_modified,
                "content_type_json": content_type_json,
            }
        )

    def _verdict(self, shape: telemetry.Shape | None, verdict: telemetry.CacheVerdict) -> None:
        """Take a cache verdict where it is decidable, which is not at the request seam.

        A HIT never reaches `_request`, so counting hits there is structurally
        zero and flatteringly so. BYPASS is taken at the guard because on that
        path the cache is never consulted at all.
        """
        if self._recorder is None or shape is None:
            return
        self._recorder.cache_verdict(shape, verdict)

    def _league_shape(self, season: int) -> telemetry.Shape:
        return (
            telemetry.Shape.LEAGUE_MODERN if season >= 2018 else telemetry.Shape.LEAGUE_HISTORY
        )

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
        shape = self._league_shape(season)
        if self.cache is not None and not bust_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._verdict(shape, telemetry.CacheVerdict.HIT)
                return cached
            # Absent and stale are NOT separable here: `DBRawCache.get` returns
            # None for both, so this is a MISS and the report says it cannot tell.
            self._verdict(shape, telemetry.CacheVerdict.MISS)
        else:
            self._verdict(shape, telemetry.CacheVerdict.BYPASS)

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
            shape=shape,
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
            shape=telemetry.Shape.PLAYERS_DEFAULTS,
        )

    def fetch_pro_schedule(self, season: int) -> dict:
        """Fetch the shared NFL schedule used to resolve opponents and kickoffs."""
        cache_key = f"pro_schedule:{season}"
        # Not league-scoped: a league-scoped clear leaves this warm.
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._verdict(telemetry.Shape.SEASON, telemetry.CacheVerdict.HIT)
                return cached
            self._verdict(telemetry.Shape.SEASON, telemetry.CacheVerdict.MISS)
        else:
            self._verdict(telemetry.Shape.SEASON, telemetry.CacheVerdict.BYPASS)
        data = self._get_authed(
            self.season_url(season),
            params=[("view", "proTeamSchedules_wl")],
            headers=dict(_BASE_HEADERS),
            cookies=None,
            throttle_key="public",
            shape=telemetry.Shape.SEASON,
        )
        if self.cache is not None:
            self.cache.set(cache_key, data)
        return data

    # ---- auth-aware GET ------------------------------------------------------
    def _get_authed(
        self,
        url: str,
        *,
        params: list[tuple[str, str]],
        headers: dict,
        cookies: Cookies | None,
        throttle_key: str,
        shape: telemetry.Shape | None = None,
    ) -> Any:
        if cookies is None:
            resp = self._request(
                url, params=params, headers=headers, throttle_key="public", shape=shape
            )
            return self._json_or_auth(resp)

        # Attempt 1: cookies as stored.
        h = {**headers, **self._cookie_header(cookies)}
        resp = self._request(
            url, params=params, headers=h, throttle_key=throttle_key, shape=shape
        )
        if resp.status_code not in (401, 403):
            return self._json_or_auth(resp)

        # Attempt 2 (once): URL-decoded espn_s2 (SPEC 2.3 gotcha). Entered only
        # when the first response was 401/403 AND the stored value differs from
        # its decoded form -- both conditions, which is why the doubled-loop case
        # needs a URL-encoded fixture and a 401/403 to reach 8 attempts at all.
        decoded = unquote(cookies.espn_s2)
        if decoded != cookies.espn_s2:
            retry = Cookies(swid=cookies.swid, espn_s2=decoded)
            h2 = {**headers, **self._cookie_header(retry)}
            resp2 = self._request(
                url, params=params, headers=h2, throttle_key=throttle_key, shape=shape
            )
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
    _forbid_in_hosted_mode("credential decrypt")

    from ..crypto import decrypt

    if not account or not account.swid:
        return None
    if account.status != "active":
        # This check, not the sentinel text, prevents a restored account from
        # decrypting or silently retrying a private league as public.
        raise EspnReauthRequired("ESPN account requires reauthentication")
    return Cookies(swid=account.swid, espn_s2=decrypt(account.espn_s2_encrypted))
