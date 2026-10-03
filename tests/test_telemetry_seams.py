"""What the provider's seams can and cannot report — measured, not argued.

Phase 32's contract was rejected three times and every surviving disagreement was
a question about code. These are those questions, executed. No production
behaviour runs through a live path: `EspnService` accepts an injected client, so
every provider probe runs the real request code against a fake transport.

The unit has now been reopened three times, and each round's lesson is built into
this file rather than only into the module:

* **Round 4** — the probes tested the provider, not the module being introduced.
* **Round 5** — three probes stayed **green with their control removed**.
* **Round 6** — two more did, and the control-removal harness itself reported
  21/21 where an independent run measured 20/21: `Recorder.cache_verdict`'s lock
  removal is *masked* by `record()`'s lock, because one lock serialises the
  threads that were supposed to collide in the other. A wall-clock contention
  probe cannot tell those apart.

So every probe in this file that claims a concurrency control **forces** the
interleaving with a barrier on an injected seam and asserts the barrier engaged
(`assert gate.broken`), rather than turning down the switch interval and hoping.
Every threaded probe records exceptions from its threads, because
`threading.Thread` swallows them and a probe whose target raises still passes.
Every barrier has a timeout, because an untimed one turns a failed `start()`
into a hung suite — and the harness scores a hang as "held".
"""

from __future__ import annotations

import ast
import collections
import dataclasses
import gc
import inspect
import os
import pickle
import re
import socket
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest

import api.services.espn as espn_module
import api.services.telemetry as telemetry_module
from api.services.espn import EspnError, EspnService, RawCacheStore
from api.services.telemetry import (
    _FIELD_BOUNDS,
    _FIELD_RULES,
    GATE_INTERVAL_SECONDS,
    CacheVerdict,
    Counters,
    Outcome,
    RateGate,
    Record,
    Recorder,
    RecordRejected,
    Shape,
    _build_shared_gate_accessor,
    _token,
    shared_gate,
)

HOST = "https://probe.invalid"
ROOT = Path(__file__).resolve().parents[1]
LEAKY = "https://fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/9998887"
BARRIER_TIMEOUT = 0.5


class FakeClient:
    """Replays a scripted sequence and records every call made through it."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, tuple(params or ()), dict(headers or {})))
        item = self.script.pop(0) if self.script else (200, b'{"ok":true}', None)
        if isinstance(item, Exception):
            raise item
        status, body, extra = item
        return httpx.Response(
            status, content=body, headers=extra or {}, request=httpx.Request("GET", url)
        )

    def close(self):
        pass


class MemCache(RawCacheStore):
    def __init__(self):
        super().__init__()
        self.store = {}
        self.gets = []

    def get(self, key):
        self.gets.append(key)
        return self.store.get(key)

    def set(self, key, payload):
        self.store[key] = payload


@pytest.fixture
def no_sleep(monkeypatch):
    """Capture every sleep the provider performs, without performing it."""
    slept = []
    monkeypatch.setattr(espn_module.time, "sleep", lambda seconds: slept.append(seconds))
    return slept


def a_record(**overrides):
    """A well-formed record; override one field to make it ill-formed."""
    values = {
        "shape": Shape.LEAGUE_MODERN,
        "outcome": Outcome.OK,
        "attempt": 1,
        "status": 200,
        "wire_bytes": 120,
        "decoded_bytes": 120,
        "net_ms": 17,
        "gate_ms": 0,
        "throttle_ms": 0,
        "backoff_ms": 0,
        "etag": False,
        "last_modified": False,
        "content_type_json": True,
    }
    values.update(overrides)
    return values


class FakeTime:
    """A clock the test moves by hand, and a sleeper that moves it.

    Phase 31 shipped a contention test that raced the wall clock and passed about
    70% of the time; `RateGate` takes an injectable clock precisely so that
    cannot happen again.
    """

    def __init__(self, start: float = 0.0):
        self.now = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Threads:
    """Run N callables concurrently and surface what they raised.

    `threading.Thread` swallows exceptions: the thread dies, `is_alive()` is
    False, the traceback goes to stderr, and a probe asserting only on shared
    state passes. Round 6 found that `test_the_gate_admits_one_caller_at_a_time`
    would stay green on a `wait()` that raised into every caller.
    """

    def __init__(self, target, count):
        self.errors: list[BaseException] = []
        self.results: list[object] = []
        self._lock = threading.Lock()
        self._target = target
        # daemon: on a genuine deadlock `join(timeout=...)` fires the assertion
        # below, and a non-daemon thread would then block the interpreter at
        # exit — hanging the suite *after* it had already reported the failure.
        self._threads = [
            threading.Thread(target=self._run, daemon=True) for _ in range(count)
        ]

    def _run(self):
        try:
            value = self._target()
        except BaseException as exc:  # noqa: BLE001 - re-raised by check()
            with self._lock:
                self.errors.append(exc)
        else:
            with self._lock:
                self.results.append(value)

    def run(self, join_timeout: float = 10.0):
        for thread in self._threads:
            thread.start()
        for thread in self._threads:
            thread.join(timeout=join_timeout)
            assert not thread.is_alive(), "a probe thread did not finish"
        assert not self.errors, f"a probe thread raised: {type(self.errors[0]).__name__}"
        return self


# ------------------------------------------------------------------ the cache


def test_a_cache_hit_never_reaches_the_request_boundary():
    """So a hit rate counted at `_request` is structurally zero, flatteringly."""
    cache, client = MemCache(), FakeClient([(200, b'{"v":1}', None)])
    service = EspnService(host=HOST, client=client, cache=cache)
    service.fetch_views("111", 2026, ["mTeam"])
    assert len(client.calls) == 1, "the cold call must reach the network"
    service.fetch_views("111", 2026, ["mTeam"])
    assert len(client.calls) == 1, "the warm call must not"
    assert len(cache.gets) == 2, "both consulted the cache; only one made a request"


def test_the_bypass_case_never_reaches_the_consultation(no_sleep):
    """The contract nominated the consultation site for the BYPASS verdict.

    Behavioural form. The previous version read `espn.py` as text and asserted
    the `not bust_cache` guard was the line above the consultation, which
    measures the source file, not the program. What matters is that on the only
    path producing a BYPASS the consultation does not run at all.
    """
    cache = MemCache()
    client = FakeClient([(200, b'{"v":1}', None)] * 2)
    service = EspnService(host=HOST, client=client, cache=cache, min_interval=0.0)

    service.fetch_views("333", 2026, ["mTeam"], bust_cache=True)
    assert cache.gets == [], "the bypass path never consults the cache"
    assert len(client.calls) == 1, "and it does make the request"

    service.fetch_views("333", 2026, ["mTeam"])
    assert len(cache.gets) == 1, "the non-bypass path does consult it"


def test_a_retry_does_not_re_consult_the_cache(no_sleep):
    """Review asserted a MISS inflates four-fold on a retried call. It does not:
    the consultation is in `fetch_views`, outside `_request`'s loop."""
    cache = MemCache()
    client = FakeClient([(429, b"", None), (429, b"", None), (200, b'{"v":2}', None)])
    service = EspnService(host=HOST, client=client, cache=cache, min_interval=0.0)
    service.fetch_views("222", 2026, ["mTeam"])
    assert len(client.calls) == 3, "three HTTP attempts"
    assert len(cache.gets) == 1, "one cache consultation"


# ------------------------------------------------- what the boundary can tell


@pytest.mark.parametrize(
    "label,response,expected_status,expected_json_content_type",
    [
        ("login bounce", (200, b"<html>login</html>", {"content-type": "text/html"}), 200, False),
        ("short json body", (200, b'{"a": [1,2', {"content-type": "application/json"}), 200, True),
        ("redirect", (302, b"", {"location": "https://elsewhere.invalid"}), 302, False),
        ("not modified", (304, b"", None), 304, False),
    ],
)
def test_the_boundary_sees_headers_even_though_the_body_is_unparsed(
    label, response, expected_status, expected_json_content_type
):
    """All four become the same `EspnError` upstream. Two are the same STATUS at
    the boundary — and the headers still separate them, which is why `Record`
    carries `content_type_json`.

    The second case was labelled "truncated body" in an earlier version and is
    not one: `httpx.Response(200, content=b'...')` derives a Content-Length from
    the bytes given, so those ten bytes are a *complete* payload that happens to
    be invalid JSON. A real truncation is below, and is not a 200 at all.
    """
    client = FakeClient([response])
    service = EspnService(host=HOST, client=client, cache=None, min_interval=0.0)
    resp = service._request(
        service.league_url("111", 2026), params=[], headers={}, throttle_key="public"
    )
    assert resp.status_code == expected_status
    is_json = resp.headers.get("content-type", "").startswith("application/json")
    assert is_json is expected_json_content_type, label

    with pytest.raises(EspnError):
        service._json_or_auth(resp)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_what_a_4xx_actually_does_at_the_boundary(status, no_sleep):
    """`Outcome.OK` used to claim it covered "any 2xx/3xx/4xx not below" with no
    probe putting a 4xx through `_request`."""
    client = FakeClient([(status, b'{"err":1}', {"content-type": "application/json"})] * 6)
    service = EspnService(host=HOST, client=client, cache=None, min_interval=0.0)
    resp = service._request(
        service.league_url("111", 2026), params=[], headers={}, throttle_key="public"
    )
    assert resp.status_code == status, "a 4xx is RETURNED, not raised, at the boundary"
    assert len(client.calls) == 1, "and it is not retried"
    assert no_sleep == [], "so it costs no backoff"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_the_retryable_status_set_is_exactly_what_the_comment_says(status, no_sleep):
    """A set with three unexercised members is a claim, not a measurement."""
    client = FakeClient([(status, b"", None)] * 10)
    service = EspnService(host=HOST, client=client, cache=None, min_interval=0.0)
    with pytest.raises(EspnError):
        service._request(
            service.league_url("111", 2026), params=[], headers={}, throttle_key="public"
        )
    assert len(client.calls) == 4, f"{status} is retried to exhaustion"
    assert no_sleep == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.parametrize(
    "label,body,declared,expect_raise",
    [
        ("complete", b'{"a": [1,2]}', 12, False),
        ("truncated", b'{"a": [1,2', 512, True),
        ("off by one", b"0123456789", 11, True),
    ],
)
def test_a_short_body_against_a_declared_length_is_a_protocol_error(
    label, body, declared, expect_raise
):
    """The premise of the next test, established on a real HTTP wire.

    It has to be a real wire. The first attempt used `httpx.MockTransport` with
    an explicit `content-length: 512` header over ten bytes and **nothing
    raised** — a mock transport hands back a response object and never speaks the
    protocol. Measuring the wrong thing and getting a green tick is the defect
    this file exists to stop, and it reappeared here on the first try.

    Both sockets carry timeouts. `settimeout` on the listener does not transfer
    to the socket `accept()` returns, so round 6 measured the strand simply
    moving from `accept()` to `recv()`; and `server.close()` runs before the
    liveness assertion, so a failing assertion cannot skip the cleanup it exists
    to survive.
    """
    server = socket.socket()
    try:
        server.bind(("127.0.0.1", 0))
    except OSError as exc:  # pragma: no cover - sandbox without bind
        server.close()
        pytest.skip(f"loopback bind unavailable: {exc}")
    server.listen(1)
    server.settimeout(5.0)
    port = server.getsockname()[1]

    def serve():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5.0)            # NOT inherited from the listener
            try:
                conn.recv(65535)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(declared).encode() + b"\r\n"
                    b"Connection: close\r\n\r\n" + body
                )
            except OSError:
                return

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        if expect_raise:
            with (
                pytest.raises(httpx.RemoteProtocolError) as caught,
                httpx.Client(timeout=5.0) as client,
            ):
                client.get(f"http://127.0.0.1:{port}/x")
            assert isinstance(caught.value, httpx.HTTPError), "so the retry loop catches it"
            assert isinstance(caught.value, httpx.TransportError)
        else:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"http://127.0.0.1:{port}/x")
            assert resp.status_code == 200
            assert len(resp.content) == declared, "a consistent length reads clean"
    finally:
        server.close()                      # before the assert, not after
        thread.join(timeout=6.0)
    assert not thread.is_alive(), "the server thread must not outlive the test"


def test_a_truncated_body_is_a_transport_error_not_a_200(no_sleep):
    """Three drafts said a truncated body records at the boundary as a successful
    200. It does not reach the boundary as a 200 at all.

    What this measures is `_request`'s handling. It does **not** measure a
    mapping to `Outcome.TRANSPORT_ERROR` — nothing in this module assigns an
    `Outcome`; that is unit 3, and a docstring previously claimed this test
    measured it.
    """
    truncation = httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body"
    )
    client = FakeClient([truncation, truncation, (200, b'{"v":9}', None)])
    service = EspnService(host=HOST, client=client, cache=None, min_interval=0.0)
    resp = service._request(
        service.league_url("111", 2026), params=[], headers={}, throttle_key="public"
    )
    assert resp.status_code == 200, "the third attempt succeeded"
    assert len(client.calls) == 3, "the first two were retried, not returned"
    assert no_sleep == [1.0, 2.0], "and they backed off like any transport error"


def test_the_body_is_parsed_after_the_instrumented_seam_returns(no_sleep):
    """Behavioural form of a probe that used to compare line offsets in source."""
    client = FakeClient([(200, b"not json at all", {"content-type": "application/json"})])
    service = EspnService(host=HOST, client=client, cache=None, min_interval=0.0)

    resp = service._request(
        service.league_url("111", 2026), params=[], headers={}, throttle_key="public"
    )
    assert resp.status_code == 200, "the seam saw a success"
    assert len(client.calls) == 1, "and did not retry, because nothing had failed yet"

    with pytest.raises(EspnError):
        service._json_or_auth(resp)


# ------------------------------------------------------------- the URL shapes


def test_the_url_families_are_not_disjoint_under_prefix_matching():
    """Which is why `Shape` is chosen by the builder that ran, never matched back
    out of an assembled URL."""
    service = EspnService(host=HOST, client=FakeClient(), cache=None)
    families = {
        Shape.LEAGUE_MODERN: service.league_url("111", 2026),
        Shape.LEAGUE_HISTORY: service.league_url("111", 2017),
        Shape.PLAYERS_DEFAULTS: service.players_url(2026, defaults=True),
        Shape.PLAYERS_SEASON: service.players_url(2026, defaults=False),
        Shape.SEASON: service.season_url(2026),
    }
    assert set(families) == set(Shape), "every shape names a builder that exists"
    collisions = {
        (_token(a), _token(b))
        for a, url_a in families.items()
        for b, url_b in families.items()
        if a is not b and url_b.startswith(url_a)
    }
    assert collisions == {
        ("season", "league_modern"),
        ("season", "players_defaults"),
        ("season", "players_season"),
    }
    assert families[Shape.LEAGUE_HISTORY].endswith("/111"), "the id is the last segment"


def test_there_is_no_unknown_shape():
    """An `UNKNOWN` member would convert a wiring bug into a row that looks like
    data, and is where a "record the path" patch would land."""
    assert "UNKNOWN" not in Shape.__members__
    assert {_token(s) for s in Shape} == {
        "league_modern",
        "league_history",
        "players_defaults",
        "players_season",
        "season",
    }


# ------------------------------------------------ the provider's own throttle


def test_an_exhausted_call_sleeps_fifteen_seconds_and_the_last_buys_nothing(no_sleep):
    """The contract said 1+2+4 = 7s. It is 1+2+4+8 = 15s: the loop sleeps after
    the final failure and then raises, so the last 8s precedes no request."""
    client = FakeClient([(500, b"", None)] * 10)
    service = EspnService(host=HOST, client=client, cache=None, min_interval=0.0)
    with pytest.raises(EspnError):
        service._request(
            service.league_url("111", 2026), params=[], headers={}, throttle_key="public"
        )
    assert len(client.calls) == 4, "max_retries is four total attempts"
    assert no_sleep == [1.0, 2.0, 4.0, 8.0]
    assert sum(no_sleep) == 15.0
    assert len(no_sleep) == len(client.calls), "the final sleep precedes no request"


def test_the_per_key_throttle_does_not_space_different_accounts(no_sleep):
    """Four requests across three keys sleep once — for the repeated key only.
    A deployment-wide budget cannot be expressed per key."""
    client = FakeClient([(200, b'{"v":1}', None)] * 4)
    service = EspnService(host=HOST, client=client, cache=None, min_interval=1.0)
    for key in ("acct-A", "acct-B", "acct-C", "acct-A"):
        service._request(
            service.league_url("111", 2026), params=[], headers={}, throttle_key=key
        )
    assert len(client.calls) == 4
    assert len(no_sleep) == 1, f"only the repeated key waited: {no_sleep}"


# --------------------------------------------------------------- the rate gate


def test_the_gate_spaces_every_start():
    """Deterministic: injectable clock and sleeper, so this is arithmetic."""
    fake = FakeTime(1000.0)
    gate = RateGate(interval=1.0, clock=fake.clock, sleeper=fake.sleep)
    waits = [gate.wait() for _ in range(4)]
    assert waits[0] == 0.0
    assert waits[1:] == [1.0, 1.0, 1.0]
    assert fake.slept == [1.0, 1.0, 1.0]


def test_a_caller_that_already_waited_is_not_made_to_wait_again():
    """The late-arrival branch. The next slot is measured from the caller's
    arrival, not backdated to the slot it missed."""
    fake = FakeTime()
    gate = RateGate(interval=1.0, clock=fake.clock, sleeper=fake.sleep)

    assert gate.wait() == 0.0, "the first caller never waits"
    fake.now = 60.0
    assert gate.wait() == 0.0, "the late caller is owed nothing"
    assert fake.slept == [], "and it did not sleep"
    assert gate._last_start == 60.0, "the next slot is measured from NOW..."

    fake.now = 60.5
    assert gate.wait() == pytest.approx(0.5)
    assert fake.slept == [pytest.approx(0.5)], "no backdated burst"


def test_a_caller_arriving_exactly_on_the_interval_does_not_sleep():
    """The boundary at `waited == 0` exactly. Found by mutation: `> 0` relaxed to
    `>= 0` survived every other probe, and reports a wait it did not perform."""
    fake = FakeTime()
    gate = RateGate(interval=1.0, clock=fake.clock, sleeper=fake.sleep)

    assert gate.wait() == 0.0
    fake.now = 1.0
    assert gate.wait() == 0.0, "owes nothing"
    assert fake.slept == [], "and must not sleep zero seconds to say so"


def test_the_gate_admits_one_caller_at_a_time():
    """`wait()`'s mutual exclusion. Its absence admitted seven callers into a
    one-request slot with every probe green.

    Deterministic: the injected clock parks the first caller inside the critical
    section on a barrier that fills only if a second gets in alongside it. With
    the lock, the second is blocked, the barrier times out, and `broken` is True.
    `Threads` surfaces anything either caller raised, because a `wait()` that
    raised immediately would otherwise leave both assertions satisfied.
    """
    inside = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    entered = []

    def clock() -> float:
        entered.append(1)
        try:
            inside.wait()
        except threading.BrokenBarrierError:
            pass
        return 0.0

    gate = RateGate(interval=1.0, clock=clock, sleeper=lambda _s: None)
    runner = Threads(gate.wait, 2).run()

    assert len(entered) == 2, "both callers did reach the gate"
    assert inside.broken, "but never at the same time: the barrier never filled"
    assert sorted(runner.results) == [0.0, 1.0], (
        "both returned a wait rather than raising: the first free, the second spaced"
    )


def test_every_caller_gets_its_own_slot_under_contention():
    """N callers consume N distinct slots, not one shared one.

    Round 6 measured the previous version of this probe passing with the lock
    removed: a `threading.Barrier` on the *start* synchronises arrival, but
    nothing inside `wait()` released the GIL, so the eight short critical
    sections ran one at a time anyway. The interleave is now forced from inside
    the critical section, on the injected clock, exactly as the probe above.
    """
    fake = FakeTime()
    inside = threading.Barrier(2, timeout=BARRIER_TIMEOUT)
    seen = []

    def clock() -> float:
        seen.append(1)
        try:
            inside.wait()
        except threading.BrokenBarrierError:
            pass
        return fake.now

    gate = RateGate(interval=1.0, clock=clock, sleeper=fake.sleep)
    Threads(gate.wait, 8).run()

    assert len(seen) == 8, "eight callers reached the gate"
    assert inside.broken, "never two at once inside it"
    assert len(fake.slept) == 7, "one free start, seven spaced"
    assert sum(fake.slept) == pytest.approx(7.0)
    assert gate._last_start == pytest.approx(7.0), "seven distinct slots consumed"


def test_the_default_interval_is_one_request_per_second():
    """SPEC 2.10's rate, and the only number a caller that passes nothing gets.
    Found by mutation: widening the default survived every other probe."""
    assert GATE_INTERVAL_SECONDS == 1.0
    assert RateGate().interval == 1.0
    assert shared_gate().interval == 1.0


@pytest.mark.parametrize("attribute", ["_interval", "_clock", "_sleep", "_lock", "_sealed"])
def test_the_gates_configuration_cannot_be_deleted(attribute):
    """The other half of the seal.

    `__setattr__` read the seal as `getattr(self, "_sealed", False)`, so
    `del gate._sealed` made that default fire and the next plain assignment was
    permitted — reproducing the measurement-fabrication finding the seal exists
    to prevent, with ordinary syntax. The word `del` appeared nowhere in this
    file, so the control-removal harness had never exercised deletion.
    """
    gate = shared_gate()
    with pytest.raises(AttributeError, match="cannot be deleted"):
        delattr(gate, attribute)
    assert shared_gate().interval == 1.0
    assert shared_gate()._sealed is True


@pytest.mark.parametrize("attribute", ["_interval", "_clock", "_sleep", "_lock", "_sealed"])
def test_the_gates_configuration_is_sealed_after_construction(attribute):
    """Round 5 found `shared_gate().interval = 0.0`; a read-only property was
    added; round 6 then found `_interval` and `_sleep`, where a no-op sleeper
    made `wait()` report 1.0s of throttling against 0.0s of real blocking —
    fabricating the number this phase exists to measure, every probe green."""
    gate = shared_gate()
    with pytest.raises(AttributeError):
        setattr(gate, attribute, 0.0)
    assert shared_gate().interval == 1.0




def test_the_gate_has_no_instance_dictionary():
    """`__slots__`, so there is no `__dict__` through which a new attribute could
    shadow a sealed one."""
    assert not hasattr(shared_gate(), "__dict__")
    assert set(RateGate.__slots__) == {
        "_interval",
        "_clock",
        "_sleep",
        "_lock",
        "_last_start",
        "_sealed",
    }


def test_the_gate_can_still_record_its_own_progress():
    """The seal must not freeze `_last_start`, which `wait()` writes on every
    call. A seal that broke the gate would be caught here rather than in unit 3."""
    fake = FakeTime()
    gate = RateGate(interval=1.0, clock=fake.clock, sleeper=fake.sleep)
    gate.wait()
    fake.now = 5.0
    gate.wait()
    assert gate._last_start == 5.0


def test_the_process_wide_gate_takes_no_arguments():
    """The signature IS the control. `shared_gate(interval, clock, sleeper)`
    ignored its arguments after the first call, which was wrong in both
    directions: silently discarding a later caller's intent, and letting whoever
    imported first fix the budget for everyone."""
    assert inspect.signature(shared_gate).parameters == {}


def test_the_module_has_no_gate_global_to_rebind():
    """Round 6: `telemetry._shared_gate = RateGate(interval=0.0)` — one
    statement, *fewer* than the two public calls the round-5 disarm needed. The
    gate is now built at import and held in a closure cell, so there is no
    module-level name bound to it and no `None` state to exploit."""
    assert not hasattr(telemetry_module, "_shared_gate")
    assert not hasattr(telemetry_module, "_shared_gate_lock")
    assert [n for n in dir(telemetry_module) if "reset" in n.lower()] == []
    cells = [c.cell_contents for c in (shared_gate.__closure__ or ())]
    assert any(isinstance(c, RateGate) for c in cells), "the gate lives in a closure cell"


def test_the_gate_is_shared_across_separately_constructed_callers():
    """The portfolio loop builds a fresh `EspnService` per league, so an
    instance-scoped gate would be no gate at all. Identity only — an earlier
    version appended a timing assertion that called `wait()` three times through
    aliases of one object, the exact shape its own docstring criticised."""
    first = shared_gate()
    assert all(shared_gate() is first for _ in range(3))
    assert isinstance(first, RateGate)


def test_a_fresh_accessor_yields_one_gate_to_every_caller():
    """The accessor factory, exercised independently of the module-level one, so
    the singleton property is measured rather than inherited from import order.
    No construction race exists to test for: the gate is built eagerly, which is
    why the lazy initialiser and its hard-to-force lock are gone."""
    accessor = _build_shared_gate_accessor()
    gates = Threads(accessor, 8).run().results
    assert len(gates) == 8
    assert len({id(g) for g in gates}) == 1, "one gate, eight callers"
    assert gates[0] is not shared_gate(), "and it is not the module's own"


# --------------------------------------------------------- the record itself


def test_a_record_rejects_a_value_that_could_carry_an_identifier():
    """The guarantee, at runtime. An earlier probe compared
    `dataclasses.fields(Record)[i].type` against type NAMES, and under PEP 563
    that attribute is the annotation's source text.

    The wiring mistake is one token wide:
    `wire_bytes=resp.headers.get("content-length", -1)` without the `int()`.
    """
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(wire_bytes=LEAKY))
    assert "wire_bytes" in str(caught.value), "the field is named..."
    assert LEAKY not in str(caught.value), "...but never the value"
    assert "9998887" not in str(caught.value)


@pytest.mark.parametrize("field_name", [name for name, _ in _FIELD_RULES])
def test_no_field_lets_the_offending_value_into_the_message(field_name):
    """`RecordRejected` documents the no-leak property for every field. One field
    was pinned for it and twelve were not."""
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(**{field_name: LEAKY}))
    message = str(caught.value)
    assert field_name in message
    assert LEAKY not in message
    assert "9998887" not in message


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("shape", "league_modern"),
        ("outcome", "ok"),
        ("attempt", "1"),
        ("status", None),
        ("wire_bytes", 12.0),
        ("decoded_bytes", True),
        ("net_ms", b"17"),
        ("gate_ms", "0"),
        ("throttle_ms", None),
        ("backoff_ms", 1.5),
        ("etag", 1),
        ("last_modified", "yes"),
        ("content_type_json", None),
    ],
)
def test_every_field_refuses_a_plausible_wrong_type(field_name, bad_value):
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(**{field_name: bad_value}))
    assert field_name in str(caught.value)


def test_an_int_look_alike_is_not_an_int():
    """`type(x) is int`, not `isinstance`."""

    class Sneaky(int):
        pass

    class Indexable:
        def __index__(self):
            return 7

    class Convertible:
        def __int__(self):
            return 7

    for value in (Sneaky(7), Indexable(), Convertible(), True):
        with pytest.raises(RecordRejected):
            Record(**a_record(wire_bytes=value))


def test_an_object_impersonating_an_enum_is_refused():
    """`isinstance` honours a `__class__` property, so an impostor passed it."""

    class FakeShape:
        def __init__(self, payload):
            self.value = payload
            self.name = payload

        @property
        def __class__(self):
            return Shape

    impostor = FakeShape(LEAKY)
    assert isinstance(impostor, Shape), "the old check would have passed it"

    with pytest.raises(RecordRejected):
        Record(**a_record(shape=impostor))

    recorder = Recorder()
    recorder.cache_verdict(impostor, CacheVerdict.MISS)  # type: ignore[arg-type]
    assert recorder.counters.cache == {}
    assert recorder.counters.dropped == 1
    assert recorder.dropped_by_shape == {}


@pytest.mark.parametrize("attribute", ["_name_", "_value_"])
def test_a_genuine_member_with_a_rewritten_field_carries_nothing(attribute):
    """The round-6 P1, and the reason nothing here reads `.name` or `.value`.

    Enum members are ordinary mutable objects. `Shape.SEASON._value_ = <payload>`
    succeeds and `type(member) is Shape` stays True, so the identity check that
    closed the impostor vector is immune to this by construction. Both reprs
    printed the payload, and `.value` — what an exporter serialises — carried it
    verbatim. Rendering now goes through `_token`, an immutable table looked up
    by identity.
    """
    original = getattr(Shape.SEASON, attribute)
    try:
        setattr(Shape.SEASON, attribute, LEAKY)
        assert type(Shape.SEASON) is Shape, "still a genuine member"

        record = Record(**a_record(shape=Shape.SEASON))
        assert LEAKY not in repr(record)
        assert "shape=season" in repr(record)

        recorder = Recorder()
        recorder.cache_verdict(Shape.SEASON, CacheVerdict.MISS)
        assert recorder.counters.cache == {("season", "miss"): 1}
        assert LEAKY not in repr(recorder.counters)

        recorder.record(a_record(shape=Shape.SEASON, status="bad"))
        assert recorder.dropped_by_shape == {"season": 1}
        assert LEAKY not in str(recorder.dropped_by_shape)
    finally:
        setattr(Shape.SEASON, attribute, original)


def test_the_module_never_reads_name_or_value_off_a_member():
    """`_token` is only a control if nothing bypasses it. A `.value` anywhere in
    this module would reintroduce the vector above, and the exporter (unit 3)
    inherits the same rule."""
    source = (ROOT / "api" / "services" / "telemetry.py").read_text()
    tree = ast.parse(source)
    # `spec.name` is a `dataclasses.Field`, not an enum member, and is the only
    # sanctioned `.name` in the module. Everything else is flagged.
    allowed = {"spec"}
    offenders = [
        f"line {node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"name", "value", "_name_", "_value_"}
        and not (isinstance(node.value, ast.Name) and node.value.id in allowed)
    ]
    # `getattr(member, "value")` is a Call, not an Attribute, so the walk above
    # cannot see it — and `_name_`/`_value_` are the actual storage, which is
    # what the round-6 finding was about.
    offenders += [
        f"line {node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in {"name", "value", "_name_", "_value_"}
    ]
    assert offenders == [], offenders


def test_the_token_lookup_is_by_identity_not_equality():
    """`candidate is member`, not `==`.

    For two genuine members those are the same thing, because `Enum` inherits
    `object.__eq__`. They are not the same thing for an object that lies: an
    `__eq__` returning True for anything collects the FIRST token in the table
    and is then rendered, counted and admitted as a real member.
    """

    class AlwaysEqual:
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    liar = AlwaysEqual()
    assert liar == Shape.SEASON, "equality would have matched it"
    assert _token(liar) == "<not a member>", "identity does not"

    with pytest.raises(RecordRejected):
        Record(**a_record(shape=liar))

    recorder = Recorder()
    recorder.cache_verdict(liar, CacheVerdict.MISS)  # type: ignore[arg-type]
    assert recorder.counters.cache == {}
    assert recorder.counters.dropped == 1


def test_the_public_interval_is_read_only_because_the_seal_covers_it():
    """Not an independent control, and worth saying so.

    `interval` is a property with no setter, but that is not what refuses the
    assignment: `__setattr__` runs *before* the data descriptor, so the seal
    catches `gate.interval = 0.0` first and a setter added to the property would
    be unreachable. The harness removed the property's read-only-ness and every
    probe stayed green — correctly. One control, two spellings.
    """
    gate = shared_gate()
    assert isinstance(type(gate).interval, property)
    assert type(gate).interval.fset is None, "no setter on the property"
    with pytest.raises(AttributeError, match="fixed after construction"):
        gate.interval = 0.0  # type: ignore[misc]
    assert shared_gate().interval == 1.0


def test_a_member_with_no_token_is_refused_everywhere(monkeypatch):
    """A member added to an enum without a token entry must be **refused**, not
    silently rendered `<not a member>` and counted anyway.

    This is what makes the second half of each enum-admission check reachable —
    the type check catches every impostor first, so the only way to hold a
    genuine member that has no token is to remove one. Coverage found those
    branches unexecuted, and an unexecutable branch is how a control ends up
    unverified.
    """
    trimmed = tuple(pair for pair in telemetry_module._TOKENS if pair[0] is not Shape.SEASON)
    monkeypatch.setattr(telemetry_module, "_TOKENS", trimmed)
    assert _token(Shape.SEASON) == "<not a member>"

    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(shape=Shape.SEASON))
    assert "not a member of its enum" in str(caught.value)

    recorder = Recorder()
    recorder.cache_verdict(Shape.SEASON, CacheVerdict.MISS)
    assert recorder.counters.cache == {}, "not counted under a placeholder token"
    assert recorder.counters.dropped == 1
    assert recorder.dropped_by_shape == {}, "and not keyed by one either"


def test_the_token_table_covers_every_member_of_every_enum():
    """A member with no token renders as `<not a member>` and is refused, so a
    member added without one would be silently unusable."""
    for enum_type in (Shape, Outcome, CacheVerdict):
        for member in enum_type:
            assert _token(member) != "<not a member>", member
    assert _token(object()) == "<not a member>"


def test_record_is_sealed_against_subclassing():
    """A subclass's extra fields were outside the check, and a subclass
    overriding `__post_init__` had no check at all."""
    with pytest.raises(TypeError) as caught:

        class Extended(Record):  # type: ignore[misc]
            pass

    assert "sealed" in str(caught.value)


def test_a_record_has_no_dict_to_bolt_an_attribute_onto():
    record = Record(**a_record())
    assert not hasattr(record, "__dict__")
    with pytest.raises(AttributeError):
        object.__setattr__(record, "url", LEAKY)


def test_the_rule_table_hands_out_nothing_mutable():
    """`MappingProxyType` looked immutable and was not: round 6 measured
    `gc.get_referents(proxy)[0]` returning the backing dict, and one write to it
    disarmed both the validation and the value-free `__repr__`, because both read
    this table. A tuple has no backing object to hand out."""
    assert type(_FIELD_RULES) is tuple
    referents = gc.get_referents(_FIELD_RULES)
    assert not any(isinstance(r, (dict, list, set)) for r in referents), referents
    with pytest.raises(TypeError):
        _FIELD_RULES[0] = ("wire_bytes", str)  # type: ignore[index]


def test_the_token_table_hands_out_nothing_mutable():
    from api.services.telemetry import _TOKENS

    assert type(_TOKENS) is tuple
    assert not any(isinstance(r, (dict, list, set)) for r in gc.get_referents(_TOKENS))


def test_a_field_without_a_rule_is_refused(monkeypatch):
    """The round-5 root cause: validation iterated the table, so a field with no
    rule was not merely unchecked — it was invisible."""
    trimmed = tuple(rule for rule in _FIELD_RULES if rule[0] != "wire_bytes")
    monkeypatch.setattr(telemetry_module, "_FIELD_RULES", trimmed)
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record())
    assert "wire_bytes has no validation rule" in str(caught.value)


def test_the_rule_table_matches_the_fields_exactly():
    assert [f.name for f in dataclasses.fields(Record)] == [name for name, _ in _FIELD_RULES]


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("attempt", 0),
        ("attempt", 65),
        ("status", -1),
        ("status", 600),
        ("net_ms", -1),
        ("gate_ms", -1),
        ("throttle_ms", -1),
        ("backoff_ms", -1),
        ("wire_bytes", -2),
        ("decoded_bytes", -2),
        ("wire_bytes", 10**40),
    ],
)
def test_a_measurement_outside_its_range_is_refused(field_name, value):
    """Not a privacy control — a league id is a number and no range check can
    tell it from a byte count — but it catches the wiring slip that puts an id
    where a measurement belongs, and it makes that residual explicit."""
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(**{field_name: value}))
    assert field_name in str(caught.value)
    assert "out of range" in str(caught.value)


#: Written out, NOT imported from the module. A probe parametrised over
#: `_FIELD_BOUNDS` reads whatever the module says and then tests the edge of
#: that — so widening a bound moved the probe with it and every mutation
#: survived. This project has recorded that exact defect before: "a test
#: parametrised over the very set it was testing".
EXPECTED_BOUNDS = (
    ("attempt", 1, 64),
    ("status", 0, 599),
    ("wire_bytes", -1, 1 << 26),
    ("decoded_bytes", -1, 1 << 26),
    ("net_ms", 0, 600_000),
    ("gate_ms", 0, 600_000),
    ("throttle_ms", 0, 600_000),
    ("backoff_ms", 0, 600_000),
)


def test_the_bounds_are_the_ones_written_down_here():
    """So the probe below cannot follow a bound that moved."""
    assert _FIELD_BOUNDS == EXPECTED_BOUNDS


@pytest.mark.parametrize("field_name,low,high", EXPECTED_BOUNDS)
def test_each_bound_is_pinned_at_its_exact_edge(field_name, low, high):
    """Boundary values, not values far outside the range.

    Found by the mutation sweep: every probe used a value miles outside the
    bound, so widening `1 << 32` to `1 << 33`, or relaxing `<=` to `<`, survived
    the whole suite. A limit nobody asserts at its edge is a limit that can move
    without a failing test — the same gap that left the default interval and the
    records cap unpinned.
    """
    assert Record(**a_record(**{field_name: low})).__getattribute__(field_name) == low
    assert Record(**a_record(**{field_name: high})).__getattribute__(field_name) == high
    for outside in (low - 1, high + 1):
        with pytest.raises(RecordRejected) as caught:
            Record(**a_record(**{field_name: outside}))
        assert f"{field_name} is out of range" == str(caught.value)


@pytest.mark.parametrize(
    "field_name,identifier,refused",
    [
        # 64 MiB and 10 minutes. A nine- or ten-digit identifier is outside
        # every bound; at the old `1 << 40` / `1 << 32` none of these were.
        ("wire_bytes", 123456789, True),
        ("wire_bytes", 1234567890, True),
        ("decoded_bytes", 1234567890, True),
        ("net_ms", 9998887, True),
        ("gate_ms", 123456789, True),
        ("status", 9998887, True),
        ("attempt", 9998887, True),
        # ...and the residual, asserted rather than glossed: 9,998,887 bytes is
        # a plausible body size, so a seven-digit id in a BYTE field is inside
        # the bound and passes. No range check can distinguish one from the
        # other. That is why the module calls this a wiring-slip check and not
        # a privacy control, and why the precondition stays on the caller.
        ("wire_bytes", 9998887, False),
        ("decoded_bytes", 9998887, False),
    ],
)
def test_what_the_bounds_do_and_do_not_catch(field_name, identifier, refused):
    if refused:
        with pytest.raises(RecordRejected):
            Record(**a_record(**{field_name: identifier}))
    else:
        assert Record(**a_record(**{field_name: identifier})) is not None


def test_the_sentinels_the_fields_document_are_accepted():
    """`-1` means "absent" for both byte counts, and `status=0` means "no
    response". A range check that refused the documented sentinels would be a
    control that breaks the thing it guards."""
    record = Record(**a_record(wire_bytes=-1, decoded_bytes=-1, status=0))
    assert (record.wire_bytes, record.decoded_bytes, record.status) == (-1, -1, 0)


def test_a_record_is_frozen():
    """Found by mutation: flipping `frozen=True` to `False` left every other
    probe green."""
    record = Record(**a_record())
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.wire_bytes = LEAKY  # type: ignore[misc]
    assert record.wire_bytes == 120


def test_a_tampered_record_does_not_print_what_it_holds():
    """Drops are silent, so `repr` and a pytest assertion diff are the only
    places a rejected value would surface — and CI logs are the most-copied
    artifact in this project."""
    record = Record(**a_record())
    object.__setattr__(record, "wire_bytes", LEAKY)
    printed = repr(record)
    assert LEAKY not in printed
    assert "9998887" not in printed
    assert "wire_bytes=<invalid str>" in printed
    assert "shape=league_modern" in printed, "a valid field still prints"


def test_a_tampered_shape_does_not_print_what_it_holds():
    """`Record.__repr__`'s enum branch, which no probe reached: relaxing its
    `type(...) is` to `isinstance` would print an impostor's `.name`."""

    class FakeShape:
        def __init__(self, payload):
            self.name = payload
            self.value = payload

        @property
        def __class__(self):
            return Shape

    record = Record(**a_record())
    object.__setattr__(record, "shape", FakeShape(LEAKY))
    printed = repr(record)
    assert LEAKY not in printed
    assert "shape=<not a member>" in printed


def test_an_out_of_range_int_does_not_print_either():
    """`object.__setattr__` can put one on a live record, and the repr is the
    last place a value is seen before it reaches a log."""
    record = Record(**a_record())
    object.__setattr__(record, "status", 9998887)
    printed = repr(record)
    assert "9998887" not in printed
    assert "status=<invalid int>" in printed


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(("league", LEAKY), id="both halves wrong"),
        pytest.param(("season", LEAKY), id="verdict is not a token"),
        pytest.param((LEAKY, "hit"), id="shape is not a token"),
        pytest.param(LEAKY, id="not a tuple at all"),
        pytest.param(("season", "hit", LEAKY), id="three-tuple"),
        pytest.param((LEAKY,), id="one-tuple"),
        pytest.param((Shape.SEASON, CacheVerdict.HIT), id="members rather than tokens"),
    ],
)
def test_counters_do_not_print_a_malformed_key_again(key):
    """Every shape of wrong key, because mutation showed the earlier probe only
    exercised the case where both halves were wrong."""
    counters = Counters()
    counters.cache[key] = 3  # type: ignore[index]
    printed = repr(counters)
    assert LEAKY not in printed
    assert "9998887" not in printed
    assert "<invalid key" in printed


def test_counters_do_not_print_a_key_that_merely_compares_equal_to_a_token():
    """`_token` was converted to identity in round 6; this line was missed.

    `shape in _KNOWN_TOKENS` runs the candidate's `__hash__`/`__eq__`, so an
    object that hashes like a token and compares equal to anything was judged
    safe — and then rendered through its own `__str__`, unbounded.
    """

    class PretendsToBeAToken:
        def __hash__(self):
            return hash("season")

        def __eq__(self, other):
            return True

        def __str__(self):
            return LEAKY

    counters = Counters()
    counters.cache[(PretendsToBeAToken(), PretendsToBeAToken())] = 1  # type: ignore[index]
    printed = repr(counters)
    assert LEAKY not in printed
    assert "9998887" not in printed
    assert "<invalid key" in printed


def test_counters_do_not_print_a_malformed_count():
    """The key was guarded and the count was not, for no stated reason."""
    counters = Counters()
    counters.cache[("season", "hit")] = LEAKY  # type: ignore[assignment]
    printed = repr(counters)
    assert LEAKY not in printed
    assert "<invalid count>" in printed


def test_two_malformed_keys_do_not_collide():
    """Both used to render as the literal `<invalid key>`, so one count was
    silently lost into the other."""
    counters = Counters()
    counters.cache[(LEAKY, 1)] = 7  # type: ignore[index]
    counters.cache[(LEAKY, 2)] = 9  # type: ignore[index]
    printed = repr(counters)
    assert "<invalid key 1>" in printed
    assert "<invalid key 2>" in printed
    assert "7" in printed and "9" in printed


def test_counters_print_a_well_formed_key():
    counters = Counters()
    counters.cache[("season", "hit")] = 3
    printed = repr(counters)
    assert "season/hit" in printed
    assert "<invalid" not in printed


def test_a_type_name_that_is_itself_a_payload_is_not_printed():
    """A class can be created at runtime with an arbitrary `__name__`."""
    sneaky = type(LEAKY, (), {})()
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(wire_bytes=sneaky))
    assert LEAKY not in str(caught.value)
    assert "<unprintable>" in str(caught.value)


def test_an_identifier_shaped_type_name_is_not_printed_either():
    """A 40-character identifier-shaped name is ample room for a league id, so
    the regex alone only blocked the sloppiest payloads. The allowlist does the
    work; the regex is the cheap pre-filter."""
    sneaky = type("league_0000000_seasons_2024_segment0", (), {})()
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(wire_bytes=sneaky))
    assert "league_0000000" not in str(caught.value)
    assert "<other>" in str(caught.value)


@pytest.mark.parametrize("field_name", ["shape", "outcome"])
def test_a_rules_own_type_name_is_never_read_off_the_class(field_name):
    """`_type_name` exists because a class can be created with an arbitrary
    `__name__` — and two lines away the rejection message read `__name__` off
    the RULE's type with no allowlist. `Shape` is an ordinary mutable class
    reachable as a module global, so one assignment put a payload into every
    message for that field, through a channel
    `test_no_field_lets_the_offending_value_into_the_message` cannot see: it
    checks the offending *value* is absent, and this arrives via the rule.

    Only the enum rules are parametrised here: `int.__name__` and `bool.__name__`
    are not writable, so those two rules were never a channel.
    """
    rule_type = dict(_FIELD_RULES)[field_name]
    original = rule_type.__name__
    try:
        rule_type.__name__ = LEAKY
        with pytest.raises(RecordRejected) as caught:
            Record(**a_record(**{field_name: object()}))
        assert LEAKY not in str(caught.value)
        assert "9998887" not in str(caught.value)
        assert field_name in str(caught.value)
    finally:
        rule_type.__name__ = original


@pytest.mark.parametrize(
    "field_name,expected_phrase",
    [
        ("attempt", "must be int"),
        ("wire_bytes", "must be int"),
        ("net_ms", "must be int"),
        ("etag", "must be bool"),
        ("content_type_json", "must be bool"),
        ("shape", "must be <enum>"),
        ("outcome", "must be <enum>"),
    ],
)
def test_the_rule_name_is_pinned(field_name, expected_phrase):
    """`_rule_name` replaced `expected.__name__` — and then nothing asserted
    what it returns, so the mutation sweep walked straight through its lookup
    and its fallback. A message that says the right thing is the only reason to
    render a rule name at all."""
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(**{field_name: object()}))
    assert expected_phrase in str(caught.value)


def test_the_rule_name_lookup_is_by_identity(monkeypatch):
    """Identity-not-equality is this module's stated core pattern, and this was
    the one instance of it with neither a probe nor a harness entry.

    An earlier attempt at this probe passed an always-equal *value*, which never
    reaches `_rule_name` — `expected` only ever comes from `_FIELD_RULES`, so no
    reachable input distinguishes `is` from `==`. The distinction is made
    reachable instead: a decoy at the front of the table that compares equal to
    `int` without being it. Under `==` the decoy's label wins; under `is` the
    real entry does.
    """

    class EqualToAnyType:
        def __eq__(self, other):
            return True

        def __hash__(self):
            return 0

    decoy = (EqualToAnyType(), "DECOY")
    monkeypatch.setattr(
        telemetry_module, "_RULE_TOKENS", (decoy, *telemetry_module._RULE_TOKENS)
    )
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(wire_bytes="not an int"))
    assert "must be int" in str(caught.value), "identity skips the decoy"
    assert "DECOY" not in str(caught.value)


def test_a_well_formed_record_prints_every_field():
    """The bound check in `__repr__` must not turn a valid record into a row of
    `<invalid ...>`. Found by mutation: `_within_bounds` returning nothing, or
    comparing with `<` instead of `<=`, left every probe green because none of
    them read the repr of a record that was entirely well formed."""
    record = Record(**a_record(wire_bytes=-1, decoded_bytes=1 << 26, attempt=1, status=599))
    printed = repr(record)
    assert "<invalid" not in printed
    for fragment in (
        "shape=league_modern",
        "outcome=ok",
        "attempt=1",
        "status=599",
        "wire_bytes=-1",
        f"decoded_bytes={1 << 26}",
        "etag=False",
        "last_modified=False",
        "content_type_json=True",
    ):
        assert fragment in printed, fragment


def test_an_ordinary_wrong_type_is_still_named():
    """The allowlist must not make every message useless."""
    with pytest.raises(RecordRejected) as caught:
        Record(**a_record(wire_bytes=1.5))
    assert "got float" in str(caught.value)


def test_setstate_refuses_before_it_writes():
    """The round-6 P1. `__setstate__` assigned every field first and validated
    after, so calling it on a record already in `recorder.records` mutated that
    record past `frozen=True`; the validation then raised, but the payload was
    already in the slots, and `__getstate__`, `pickle` and `asdict` all carried
    it. Re-validating after the write made the write the delivery mechanism."""
    recorder = Recorder()
    recorder.record(a_record())
    live = recorder.records[0]

    state = live.__getstate__()
    state["wire_bytes"] = LEAKY
    with pytest.raises(RecordRejected):
        live.__setstate__(state)

    assert live.wire_bytes == 120, "the live record was not mutated"
    assert recorder.records[0] is live
    assert LEAKY.encode() not in pickle.dumps(live)
    assert dataclasses.asdict(live)["wire_bytes"] == 120
    assert LEAKY not in str(live.__getstate__())


@pytest.mark.parametrize(
    "state,fragment",
    [
        pytest.param("not a mapping", "must be a mapping", id="not a mapping"),
        pytest.param({}, "is missing", id="empty"),
        pytest.param({"shape": Shape.SEASON}, "is missing outcome", id="partial"),
    ],
)
def test_setstate_refuses_a_malformed_state(state, fragment):
    """The partial case names the FIRST missing field, in declaration order.
    Found by mutation: reporting a different one survived, because the probe
    only checked that the words "is missing" appeared."""
    record = Record(**a_record())
    with pytest.raises(RecordRejected) as caught:
        record.__setstate__(state)
    assert fragment in str(caught.value)
    assert record.wire_bytes == 120


def test_setstate_refuses_an_unexpected_key_without_naming_it():
    """It echoed 32 characters of the key and ran its `__str__` to do it — the
    module's only direct value-bearing interpolation, against a docstring that
    says the messages name the field and the type and never the value."""
    record = Record(**a_record())
    state = {**record.__getstate__(), LEAKY: 1}
    with pytest.raises(RecordRejected) as caught:
        record.__setstate__(state)
    message = str(caught.value)
    assert LEAKY not in message
    assert "9998887" not in message
    assert "fantasy" not in message
    assert "1 unexpected" in message
    assert record.wire_bytes == 120


def test_setstate_refuses_a_token_that_names_no_member():
    """The unresolved branch of `_member`, which coverage found unexecuted.

    A serialised `shape` that is not one of the tokens must be refused, not
    passed through as whatever string it is — otherwise the deserialised record
    holds a `str` where an enum belongs, which is the shape of every finding in
    this module's history.
    """
    record = Record(**a_record())
    state = {**record.__getstate__(), "shape": LEAKY}
    with pytest.raises(RecordRejected) as caught:
        record.__setstate__(state)
    # Name the FIELD. Without this, a `_member` that resolves every token to the
    # first entry in the table still raises -- on `outcome`, because it hands
    # back a `Shape` -- and the probe was satisfied by a refusal it did not ask
    # for. The masking pattern, once more.
    assert "shape" in str(caught.value)
    # The arriving TYPE too. Without it, `_member` returning `None` instead of
    # the unresolved token reads identically to this probe, and the mutation
    # sweep walked through it as an "equivalent" it is not.
    assert "got str" in str(caught.value)
    assert LEAKY not in str(caught.value)
    assert record.shape is Shape.LEAGUE_MODERN, "and the live record is untouched"


def test_the_pickle_format_is_a_plain_mapping_of_tokens():
    """Pinned deliberately: an interpreter-chosen format would leave
    `__setstate__` branches that only some Python version reaches, and a branch
    no test can reach is how a privacy control ends up unverified.

    The enum fields serialise as **tokens**. RESIDUALS used to hand the
    live-member problem to unit 3's exporter as an obligation; the module's own
    sanctioned serialisation format can simply not carry the object.
    """
    record = Record(**a_record())
    state = record.__getstate__()
    assert isinstance(state, dict)
    assert list(state) == [name for name, _ in _FIELD_RULES]
    assert state["shape"] == "league_modern"
    assert state["outcome"] == "ok"
    assert not any(isinstance(v, (Shape, Outcome)) for v in state.values())


def test_a_rewritten_member_does_not_reach_the_serialised_form():
    original = Shape.LEAGUE_MODERN._value_
    try:
        Shape.LEAGUE_MODERN._value_ = LEAKY
        state = Record(**a_record()).__getstate__()
        assert state["shape"] == "league_modern"
        assert LEAKY not in str(state)
    finally:
        Shape.LEAGUE_MODERN._value_ = original


def test_a_well_formed_record_survives_a_pickle_round_trip():
    record = Record(**a_record())
    assert pickle.loads(pickle.dumps(record)) == record


def test_a_tampered_record_does_not_survive_a_pickle_round_trip():
    record = Record(**a_record())
    object.__setattr__(record, "wire_bytes", LEAKY)
    with pytest.raises(RecordRejected):
        pickle.loads(pickle.dumps(record))


def test_dataclasses_replace_re_validates():
    record = Record(**a_record())
    assert dataclasses.replace(record, wire_bytes=7).wire_bytes == 7
    with pytest.raises(RecordRejected):
        dataclasses.replace(record, wire_bytes=LEAKY)


# ------------------------------------------------------------- the recorder


def test_the_resting_state_is_no_recorder_at_all():
    assert Recorder.disabled() is None


def test_a_recorder_starts_empty():
    recorder = Recorder()
    assert recorder.records == []
    assert recorder.counters == Counters(attempts=0, filed=0, dropped=0, overflowed=0, cache={})
    assert recorder.dropped_by_shape == {}
    assert recorder.overflowed_by_shape == {}


def test_recording_an_attempt_files_it_and_counts_it():
    recorder = Recorder()
    recorder.record(a_record())
    recorder.record(a_record(shape=Shape.SEASON, attempt=2))
    assert [_token(r.shape) for r in recorder.records] == ["league_modern", "season"]
    assert recorder.counters.attempts == 2
    assert recorder.counters.filed == 2
    assert recorder.counters.dropped == 0


def test_record_takes_a_mapping_positionally():
    """It took `**values` and promised it never raises into the caller. Round 6
    measured `record(**{1: 2})` raising `TypeError: keywords must be strings` at
    argument binding, before the guarded body — and a dict built from parsed data
    is exactly where a non-string key comes from."""
    parameters = list(inspect.signature(Recorder.record).parameters.values())
    assert [p.name for p in parameters] == ["self", "values"]
    assert parameters[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD


class _NoGet(Mapping):
    """A registered `Mapping` whose `get` is unusable."""

    get = None

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __getitem__(self, key):
        raise KeyError(key)


class _RaisingGet(dict):
    def get(self, key, default=None):
        raise RuntimeError(f"boom {LEAKY}")


class _RaisingKey(str):
    def __eq__(self, other):
        raise RuntimeError("key comparison")

    def __hash__(self):
        return hash("shape")


class _RaisingClass:
    @property
    def __class__(self):
        raise RuntimeError("class property")


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({1: 2}, id="non-string key"),
        pytest.param({}, id="empty"),
        pytest.param(None, id="None"),
        pytest.param([1, 2], id="a list"),
        pytest.param("a string", id="a string"),
        pytest.param({"shape": Shape.SEASON}, id="partial"),
        # The four that actually behave badly. The previous parameters were all
        # inert on `.get` — every one a wrong *type*, none a hostile
        # *behaviour* — so this probe's name asserted a property it never
        # tested, and the recovery path it was meant to guard raised on all
        # four of these. The identical defect shape the round-6 review recorded
        # for the `counters.cache` probe.
        pytest.param(_NoGet(), id="Mapping whose get is None"),
        pytest.param(_RaisingGet({"x": 1}), id="Mapping whose get raises"),
        pytest.param({_RaisingKey("shape"): 1}, id="dict whose key __eq__ raises"),
        pytest.param(_RaisingClass(), id="object whose __class__ raises"),
    ],
)
def test_no_hostile_mapping_raises_into_the_caller(values):
    recorder = Recorder()
    recorder.record(values)  # type: ignore[arg-type]
    assert recorder.records == []
    assert recorder.counters.dropped == 1
    assert recorder.counters.attempts == 1, "a rejected attempt is still an attempt"


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(dict, id="dict"),
        pytest.param(collections.OrderedDict, id="OrderedDict"),
        pytest.param(lambda d: collections.defaultdict(int, d), id="defaultdict"),
        pytest.param(lambda d: collections.ChainMap(dict(d)), id="ChainMap"),
        pytest.param(MappingProxyType, id="MappingProxyType"),
        pytest.param(lambda d: type("Sub", (dict,), {})(d), id="dict subclass"),
    ],
)
def test_a_drop_keeps_its_shape_whatever_mapping_carried_it(factory):
    """`_shape_of` accepts only an exact `dict`, so no hostile `__class__` or
    `get` runs on the recovery path — and that hardening silently cost per-shape
    attribution for every other `Mapping`. Measured: identical payload, and only
    the exact `dict` populated `dropped_by_shape`.

    `Recorder`'s docstring says the per-shape counters exist because "any
    aggregate derived from a shape that dropped anything is biased, not just
    incomplete". A drop that loses its shape makes that bias invisible, which is
    worse than not counting it at all.
    """
    recorder = Recorder()
    recorder.record(factory(a_record(wire_bytes=LEAKY)))  # type: ignore[arg-type]
    assert recorder.counters.dropped == 1
    assert recorder.counters.attempts == 1
    assert recorder.dropped_by_shape == {"league_modern": 1}


def test_record_is_the_only_entry_point():
    assert not hasattr(Recorder, "attempt")
    public = [n for n in vars(Recorder) if not n.startswith("_")]
    assert sorted(public) == ["MAX_RECORDS", "cache_verdict", "disabled", "record"]


def test_a_bad_field_costs_a_row_and_never_the_request():
    recorder = Recorder()
    recorder.record(a_record(wire_bytes=LEAKY))
    assert recorder.records == [], "nothing filed"
    assert recorder.counters.filed == 0
    assert recorder.counters.attempts == 1
    assert recorder.counters.dropped == 1
    assert recorder.dropped_by_shape == {"league_modern": 1}, "attributed to its shape"


def test_a_drop_whose_shape_is_itself_malformed_lands_only_in_the_total():
    """So `dropped` and `sum(dropped_by_shape.values())` may legitimately differ,
    and the exporter must not substitute one for the other."""
    recorder = Recorder()
    recorder.record(a_record(shape="league_modern"))
    assert recorder.counters.dropped == 1
    assert recorder.dropped_by_shape == {}
    assert sum(recorder.dropped_by_shape.values()) != recorder.counters.dropped


def test_a_non_record_rejected_exception_is_still_a_counted_drop(monkeypatch):
    """The `except` clause has to catch what validation might actually raise, not
    only the exception this module defines."""

    def boom(self):
        raise ValueError("something else went wrong")

    monkeypatch.setattr(Record, "__post_init__", boom)
    recorder = Recorder()
    recorder.record(a_record())
    assert recorder.counters.dropped == 1
    assert recorder.records == []


def test_a_keyboard_interrupt_is_not_swallowed(monkeypatch):
    """The handler was `except BaseException`, which ate `KeyboardInterrupt` and
    `SystemExit` inside a `try` whose body could not fail."""

    def interrupt(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(Record, "__post_init__", interrupt)
    recorder = Recorder()
    with pytest.raises(KeyboardInterrupt):
        recorder.record(a_record())
    assert recorder.counters.dropped == 0, "and it is not miscounted as a drop"


def test_drops_accumulate_per_shape():
    recorder = Recorder()
    for _ in range(3):
        recorder.record(a_record(shape=Shape.SEASON, status="200"))
    recorder.record(a_record(shape=Shape.PLAYERS_SEASON, status="200"))
    assert recorder.counters.dropped == 4
    assert recorder.dropped_by_shape == {"season": 3, "players_season": 1}


def test_a_recorder_never_raises_whatever_it_is_handed():
    recorder = Recorder()
    for junk in (None, 0, "record", [], object()):
        recorder.record({"shape": junk, "outcome": junk})  # type: ignore[dict-item]
        recorder.cache_verdict(junk, junk)  # type: ignore[arg-type]
    assert recorder.counters.dropped == 10
    assert recorder.records == []


def test_the_records_cap_has_a_stated_default():
    """The overflow probe overrides `MAX_RECORDS`, so the shipped value was
    pinned by nothing — the same gap mutation found in the default interval."""
    assert Recorder.MAX_RECORDS == 50_000
    assert Recorder().MAX_RECORDS == 50_000


def test_records_are_capped_and_the_overflow_is_counted_without_losing_the_attempt():
    """`attempts` used to stop incrementing at the cap, so every rate computed
    against it drifted in the flattering direction past 50,000 with nothing
    flagging it. `attempts` counts attempts; `filed` counts rows retained."""
    recorder = Recorder()
    recorder.MAX_RECORDS = 3
    for n in range(5):
        recorder.record(a_record(attempt=n + 1))
    assert len(recorder.records) == 3
    assert recorder.counters.filed == 3
    assert recorder.counters.overflowed == 2
    assert recorder.counters.attempts == 5, "an overflowed attempt is still an attempt"
    assert recorder.counters.attempts == recorder.counters.filed + recorder.counters.overflowed
    assert recorder.counters.dropped == 0, "an overflow is not a rejection"


def test_overflow_is_attributed_per_shape():
    """`dropped` is per-shape because drops are biased; overflow is a **head**
    bias — the earliest rows are kept — which is worse, and the exporter needs to
    know which shapes lost rows."""
    recorder = Recorder()
    recorder.MAX_RECORDS = 1
    recorder.record(a_record(shape=Shape.SEASON))
    recorder.record(a_record(shape=Shape.SEASON))
    recorder.record(a_record(shape=Shape.PLAYERS_SEASON))
    assert recorder.overflowed_by_shape == {"season": 1, "players_season": 1}
    assert recorder.counters.overflowed == 2


# -------------------------------------------------- the recorder's own locks


def _forced_interleave(getter_name):
    """A `Counters` whose getter parks the first reader until a second arrives.

    Under the lock the second never gets in, the barrier breaks, and both
    updates land. Without it both read the same value and one update is lost.
    Round 6's lesson: a wall-clock contention probe cannot distinguish those,
    because `Recorder`'s three call sites share one lock, so the still-locked
    ones serialise the threads that were supposed to collide in the unlocked one.
    """

    class Forking(Counters):
        gate = threading.Barrier(2, timeout=BARRIER_TIMEOUT)

        def _park(self):
            try:
                type(self).gate.wait()
            except threading.BrokenBarrierError:
                pass

    def make_property(private):
        def getter(self):
            value = getattr(self, private)   # read FIRST, then synchronise
            self._park()
            return value

        def setter(self, value):
            setattr(self, private, value)

        return property(getter, setter)

    setattr(Forking, getter_name, make_property(f"_{getter_name}"))
    return Forking


def test_two_recorders_cannot_interleave_an_attempt_update():
    """`counters.attempts += 1` is a read-modify-write, and the lock makes it
    atomic. Under the GIL the interleaving essentially never happens on its own —
    a wall-clock probe reported the lock's removal as harmless through 1600
    concurrent records, which is how an unheld control looks."""
    forking = _forced_interleave("filed")
    recorder = Recorder()
    recorder.counters = forking()
    recorder.counters.filed = 0

    Threads(lambda: recorder.record(a_record()), 2).run()

    assert forking.gate.broken, "the second thread was never inside the getter"
    assert recorder.counters.filed == 2, "one increment was lost to a race"
    assert len(recorder.records) == 2


def test_two_callers_cannot_interleave_a_cache_verdict():
    """The control round 6 measured as held by nothing.

    `Recorder` has one lock used at three sites. Removing it from
    `cache_verdict` alone was masked by the still-locked `record()` in the only
    probe that touched both, so the harness reported 21/21 where an independent
    run measured 20/21. This forces the interleave inside `cache_verdict` itself
    and touches nothing else.
    """

    class Forking(Counters):
        gate = threading.Barrier(2, timeout=BARRIER_TIMEOUT)

        @property  # type: ignore[misc]
        def cache(self):
            value = self._cache
            try:
                type(self).gate.wait()
            except threading.BrokenBarrierError:
                pass
            return value

        @cache.setter
        def cache(self, value):
            self._cache = value

    recorder = Recorder()
    recorder.counters = Forking()
    recorder.counters.cache = {}

    Threads(lambda: recorder.cache_verdict(Shape.SEASON, CacheVerdict.MISS), 2).run()

    assert Forking.gate.broken, "the second thread was never inside the getter"
    assert recorder.counters.cache == {("season", "miss"): 2}, "a count was lost to a race"


def test_two_callers_cannot_interleave_a_drop():
    """`_drop` is the third use of the one lock and had no entry in the harness
    and no probe. Removing it races `dropped` and `dropped_by_shape`, and the
    only threaded probe that touched the recorder asserted `dropped == 0` — it
    deliberately never entered `_drop` at all."""
    forking = _forced_interleave("dropped")
    recorder = Recorder()
    recorder.counters = forking()
    recorder.counters.dropped = 0

    Threads(lambda: recorder.record(a_record(status="bad")), 2).run()

    assert forking.gate.broken, "the second thread was never inside the getter"
    assert recorder.counters.dropped == 2, "one drop was lost to a race"
    assert recorder.dropped_by_shape == {"league_modern": 2}


def test_the_counters_survive_bulk_concurrent_recording():
    """A volume check on top of the forced probes, not a substitute for them:
    this shape passed with either lock removed, which is why the three probes
    above exist."""
    recorder = Recorder()
    barrier = threading.Barrier(8, timeout=30.0)

    def work():
        barrier.wait()
        for _ in range(200):
            recorder.record(a_record())
            recorder.cache_verdict(Shape.LEAGUE_MODERN, CacheVerdict.MISS)

    Threads(work, 8).run(join_timeout=60.0)

    assert recorder.counters.attempts == 1600
    assert recorder.counters.filed == 1600
    assert len(recorder.records) == 1600
    assert recorder.counters.cache == {("league_modern", "miss"): 1600}
    assert recorder.counters.dropped == 0


# --------------------------------------------------------- the cache counters


def test_cache_verdicts_count_per_shape_and_verdict():
    recorder = Recorder()
    recorder.cache_verdict(Shape.LEAGUE_MODERN, CacheVerdict.MISS)
    recorder.cache_verdict(Shape.LEAGUE_MODERN, CacheVerdict.MISS)
    recorder.cache_verdict(Shape.LEAGUE_MODERN, CacheVerdict.HIT)
    recorder.cache_verdict(Shape.SEASON, CacheVerdict.BYPASS)
    assert recorder.counters.cache == {
        ("league_modern", "miss"): 2,
        ("league_modern", "hit"): 1,
        ("season", "bypass"): 1,
    }
    assert recorder.counters.dropped == 0


def test_the_counters_hold_tokens_not_members():
    """So the structures most likely to be serialised into a published artifact
    hold no live object whose fields someone could rewrite."""
    recorder = Recorder()
    recorder.cache_verdict(Shape.SEASON, CacheVerdict.HIT)
    recorder.record(a_record(shape=Shape.SEASON, status="bad"))
    key = next(iter(recorder.counters.cache))
    assert all(type(part) is str for part in key)
    assert all(type(k) is str for k in recorder.dropped_by_shape)


def test_a_cache_verdict_takes_closed_enums_only():
    recorder = Recorder()
    recorder.cache_verdict(Shape.SEASON, "hit")  # type: ignore[arg-type]
    recorder.cache_verdict("season", CacheVerdict.HIT)  # type: ignore[arg-type]
    assert recorder.counters.cache == {}
    assert recorder.counters.dropped == 2
    assert recorder.dropped_by_shape == {"season": 1}, "only the attributable one"
    # A rejected cache verdict is not an HTTP attempt. Found by mutation:
    # flipping `_drop`'s `counts_as_attempt` default to True inflated `attempts`
    # on every verdict drop, and nothing objected.
    assert recorder.counters.attempts == 0, "a verdict is not an attempt"


def test_the_cache_verdicts_are_exactly_the_three_the_code_can_produce():
    assert {_token(v) for v in CacheVerdict} == {"hit", "miss", "bypass"}


def test_the_outcomes_are_the_four_the_boundary_could_distinguish():
    """Nothing in this module assigns one — the mapping is unit 3. This pins the
    set, not a behaviour, and the docstring no longer claims otherwise."""
    assert {_token(o) for o in Outcome} == {
        "ok",
        "retryable_status",
        "transport_error",
        "exhausted",
    }


# ------------------------------------------------------- the provider is clean

_PROVIDER_MODULES = ("api.services.espn", "api.services.sync", "api.services.cache")


def test_only_the_provider_mentions_the_telemetry_module():
    """Unit 2 builds the seams; wiring them is unit 3 and is gated on a contract.

    This reads source text, which is the right instrument for exactly this claim
    and was the wrong one for the behavioural probes it does not replace. It
    walks every module under `api/`, not three files, and it also catches an
    `importlib` call assembled from fragments — which a plain import scan misses.
    """
    offenders = []
    for path in sorted((ROOT / "api").rglob("*.py")):
        # `telemetry.py` is itself, and `espn.py` is the ONE module unit 3 wired.
        # Retiring this scan outright was the contract's plan and was wrong: the
        # claim "nothing mentions telemetry" became false, but "only the provider
        # mentions it" is still true, still worth pinning, and still fails the
        # moment a second module reaches for the recorder.
        if path.name in ("telemetry.py", "espn.py"):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        # Docstrings are exempt, and ONLY docstrings. A docstring cannot be an
        # import, so a module that names `telemetry.py` in prose -- to say what
        # it is not, which `observability.py` does -- is not wiring. Collected
        # by identity from the places a docstring can structurally occur, not
        # by "the first statement" or by line number: a scan narrowed by
        # guesswork is a scan with a hole in it.
        docstrings = {
            id(ast.get_docstring(scope, clean=False) and scope.body[0].value)
            for scope in [tree, *ast.walk(tree)]
            if isinstance(
                scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and ast.get_docstring(scope) is not None
        }
        for node in ast.walk(tree):
            if id(node) in docstrings:
                continue
            if isinstance(node, ast.ImportFrom) and "telemetry" in (node.module or ""):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} from-import")
            elif isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.relative_to(ROOT)}:{node.lineno} import"
                    for alias in node.names
                    if "telemetry" in alias.name
                )
            elif isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr, ast.Call)):
                # `ast.unparse` with quotes and whitespace stripped, so
                # `"tele" + "metry"`, `"".join([...])` and an f-string all read
                # as one token. Matching whole `ast.Constant` values missed
                # every one of those — the exact "assembled from fragments"
                # case this probe was documented as catching, and was not.
                # Strip EVERY non-identifier character. The previous version
                # stripped whitespace and quotes only, so `"tele" + "metry"`
                # flattened to `tele+metry` and was missed -- as were
                # `"".join([...])` and an f-string, i.e. all three forms the
                # comment named. E32.1f claimed this was covered, measurement
                # showed it was not, E32.1g claimed the fix covered it, and
                # measurement showed that too was not. Third time.
                flattened = re.sub(r"[^A-Za-z0-9_]", "", ast.unparse(node))
                if "telemetry" in flattened:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} assembled reference")
    assert offenders == [], offenders


def test_the_docstring_exemption_does_not_exempt_ordinary_strings():
    """The narrowing above is a weakening, so it is pinned.

    A module docstring naming `telemetry.py` in prose is not a wiring; a
    string literal anywhere else in the same module still is, because that is
    where an `import_module` argument lives. Both halves are asserted, because
    exempting only one of them is the whole point and a scan that exempted
    both would pass this file's other tests unchanged.
    """
    source = (
        '"""A module docstring mentioning api.services.telemetry in prose."""\n'
        'TARGET = "api.services.telemetry"\n'
    )
    tree = ast.parse(source)
    docstrings = {
        id(ast.get_docstring(scope, clean=False) and scope.body[0].value)
        for scope in [tree, *ast.walk(tree)]
        if isinstance(
            scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and ast.get_docstring(scope) is not None
    }
    hits = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and id(node) not in docstrings
        and "telemetry" in re.sub(r"[^A-Za-z0-9_]", "", ast.unparse(node))
    ]
    assert len(hits) == 1, hits
    assert "api.services.telemetry" in hits[0]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('import_module("api.services.telemetry")', id="plain literal"),
        pytest.param('import_module("api.services.tele" "metry")', id="adjacent literals"),
        pytest.param('import_module("api.services.tele" + "metry")', id="concatenated"),
        pytest.param('"".join(["tele", "metry"])', id="joined"),
        pytest.param('"".join(["te", "le", "me", "try"])', id="joined in four"),
        pytest.param("f\"tele{'metry'}\"", id="f-string"),
    ],
)
def test_the_scan_catches_a_reference_assembled_from_fragments(source):
    """The probe above says it catches these. Twice the evidence has said so and
    twice measurement has said otherwise, so each form is pinned by name here
    rather than described in a comment."""
    node = ast.parse(source).body[0].value
    flattened = re.sub(r"[^A-Za-z0-9_]", "", ast.unparse(node))
    assert "telemetry" in flattened, flattened


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('f"tele{chr(109)}etry"', id="a fragment computed at runtime"),
        pytest.param('import_module("api.services." + name)', id="a name from a variable"),
        pytest.param('getattr(api.services, name)', id="getattr with a variable"),
    ],
)
def test_and_what_the_scan_cannot_catch(source):
    """The limit, pinned as deliberately as the capability.

    A static scan reads text; a fragment computed at runtime is not text. Every
    previous round stated what this probe catches and was wrong, so what it does
    NOT catch is written down here too — and the `sys.modules` subprocess check
    is what covers these, because it runs the import rather than reading it.
    """
    node = ast.parse(source).body[0].value
    flattened = re.sub(r"[^A-Za-z0-9_]", "", ast.unparse(node))
    assert "telemetry" not in flattened, (
        "this form is now catchable; move it to the probe above"
    )


def _telemetry_module_refs(tree):
    """Local names that refer to the telemetry MODULE, however it was imported.

    The original predicate tested `"telemetry" in ast.unparse(target.value)`,
    which is *necessary and not sufficient*: `import api.services.telemetry as t`
    binds a name with no such substring, and review measured the probe vacuous
    under it. Resolving the binding is what closes that, so the alias is tracked
    rather than the spelling guessed.
    """
    refs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "telemetry" in alias.name:
                    refs.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if "telemetry" in (node.module or ""):
                # `from ...telemetry import X` binds members, not the module.
                continue
            for alias in node.names:
                if alias.name == "telemetry":
                    refs.add(alias.asname or alias.name)
    return refs


def _base_is_telemetry(node, refs):
    """Does this expression resolve to the telemetry module?"""
    text = ast.unparse(node)
    if "telemetry" in text.lower():
        return True
    root = node
    while isinstance(root, (ast.Attribute, ast.Subscript)):
        root = root.value
    return isinstance(root, ast.Name) and root.id in refs


def _flatten_targets(target):
    """Every leaf of an assignment target, through tuple/list/starred unpacking."""
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _flatten_targets(element)
    elif isinstance(target, ast.Starred):
        yield from _flatten_targets(target.value)
    else:
        yield target


def telemetry_internal_writes(source, filename="<planted>"):
    """Every write to a private attribute of the telemetry module, as file:line.

    Shared by the tree-wide probe and by the planted-offender cases below, so the
    cases exercise the predicate the probe actually runs rather than a copy of it.

    Every form below was a measured miss of some earlier version of this predicate:
    an aliased module ref; `setattr`/`delattr` with a literal or an assembled name; a
    non-`Attribute` target (tuple unpack, `__dict__` subscript, `for`, `with ... as`,
    comprehension); the async twins of the binding constructs; `del`; and
    `__dict__.update`.

    **The enumeration is this predicate's weakness and it is stated rather than
    implied.** It widens by naming node kinds, so every kind not named is a gap —
    structurally the same shape as the three rounds of `re.sub` widening it replaced.
    The forms it still cannot reach are pinned below in the form that FAILS when a
    limit moves, not described in this docstring.
    """
    tree = ast.parse(source, filename=filename)
    refs = _telemetry_module_refs(tree)
    offenders = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            targets = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            targets = [i.optional_vars for i in node.items if i.optional_vars is not None]
        elif isinstance(node, (ast.comprehension,)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            # `del telemetry._FIELD_BOUNDS` is the `del gate._sealed` blind spot
            # recurring: `RateGate.__delattr__` exists because "the word `del`
            # appeared nowhere in the suite". It appeared nowhere in this predicate
            # either. `del Record.__init_subclass__` re-opens the round-5 subclass
            # vector outright, so deletion is a write for this purpose.
            targets = node.targets

        for raw in targets:
            for leaf in _flatten_targets(raw):
                # `ast.comprehension` is not a statement and carries no `lineno`, so
                # the leaf's own position is used rather than the node's.
                where = getattr(node, "lineno", None) or getattr(leaf, "lineno", 0)
                if isinstance(leaf, ast.Attribute):
                    if leaf.attr.startswith("_") and _base_is_telemetry(leaf.value, refs):
                        offenders.append(f"{filename}:{where} assigns {leaf.attr}")
                elif isinstance(leaf, ast.Subscript):
                    # `telemetry.__dict__["_TOKENS"] = x` and `vars(t)["_TOKENS"] = x`
                    # are module rebinding with no `Attribute` target at all.
                    if _base_is_telemetry(leaf.value, refs):
                        offenders.append(f"{filename}:{where} subscript-writes")

        # `setattr(t, "_TOK" + "ENS", x)` is a Call, outside every node kind above,
        # and the name may be assembled so no constant test can see it. The first
        # argument is what matters, so the name is not tested at all.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("setattr", "delattr")
            and node.args
            and _base_is_telemetry(node.args[0], refs)
        ):
            offenders.append(f"{filename}:{node.lineno} {node.func.id}")

        # `telemetry.__dict__.update({...})` and `vars(t).update({...})` are neither
        # an assignment nor a `setattr` Name call, and are strictly MORE powerful than
        # the `__dict__["_x"] = y` subscript form the predicate already catches: any
        # number of internals in one statement.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and _base_is_telemetry(node.func.value, refs)
        ):
            offenders.append(f"{filename}:{node.lineno} dict-update")

    return offenders


def test_nothing_under_api_assigns_to_this_modules_internals():
    """The probe that survives unit 3 — and the one D-1 had to widen first.

    The scan above was written to be retired when the provider legitimately
    imports telemetry, leaving this as the only thing preventing production code
    from rebinding the module's internals. Review then measured that it could not
    fire at all under an aliased import, so "it passes" established nothing. It is
    widened here, and the five forms below assert it FIRES rather than passes.
    """
    offenders = []
    for path in sorted((ROOT / "api").rglob("*.py")):
        if path.name == "telemetry.py":
            continue
        offenders += telemetry_internal_writes(
            path.read_text(), filename=str(path.relative_to(ROOT))
        )
    assert offenders == [], offenders


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import api.services.telemetry as t\nt._TOKENS = ()\n",
            id="an aliased module ref with no 'telemetry' in the name",
        ),
        pytest.param(
            "from api.services import telemetry\nsetattr(telemetry, '_TOKENS', ())\n",
            id="setattr with a literal name",
        ),
        pytest.param(
            "import api.services.telemetry as t\nsetattr(t, '_TOK' + 'ENS', ())\n",
            id="setattr with a name assembled at runtime",
        ),
        pytest.param(
            "from api.services import telemetry\n"
            "telemetry._TOKENS, telemetry._FIELD_BOUNDS = (), ()\n",
            id="a tuple-unpacked target, the module's own worst vector",
        ),
        pytest.param(
            "from api.services import telemetry\ntelemetry.__dict__['_TOKENS'] = ()\n",
            id="a __dict__ subscript, no Attribute target at all",
        ),
        pytest.param(
            "from api.services import telemetry\nfor telemetry._TOKENS in [()]:\n    pass\n",
            id="a for-loop target",
        ),
        pytest.param(
            "from api.services import telemetry\nwith open('f') as telemetry._TOKENS:\n    pass\n",
            id="a with-as target",
        ),
        pytest.param(
            "import sys\nsys.modules['api.services.telemetry']._TOKENS = ()\n",
            id="reached through sys.modules, caught by the literal in the subscript",
        ),
        pytest.param(
            "import api.services.telemetry as t\nasync def f():\n"
            "    async for t._TOKENS in agen():\n        pass\n",
            id="an async for target, a distinct node class from For",
        ),
        pytest.param(
            "import api.services.telemetry as t\nasync def f():\n"
            "    async with ctx() as t._TOKENS:\n        pass\n",
            id="an async with target, a distinct node class from With",
        ),
        pytest.param(
            "from api.services import telemetry\ndel telemetry._FIELD_BOUNDS\n",
            id="del, the vector RateGate.__delattr__ exists for",
        ),
        pytest.param(
            "from api.services import telemetry\n"
            "telemetry.__dict__.update({'_TOKENS': (), '_FIELD_BOUNDS': ()})\n",
            id="__dict__.update, stronger than the subscript form",
        ),
        pytest.param(
            "import api.services.telemetry as t\nx = [0 for t._TOKENS in [()]]\n",
            id="a comprehension target",
        ),
        pytest.param(
            "from api.services import telemetry\ndelattr(telemetry, '_TOKENS')\n",
            id="delattr",
        ),
    ],
)
def test_the_internals_scan_fires_on_each_planted_form(source):
    """"Must pass" is not a requirement; "must fail when X" is.

    D-1's first wording required only that the successor pass, and a vacuous
    predicate passes. Every form here was a measured miss of the original
    predicate, so each is a plant the widened one has to catch.
    """
    assert telemetry_internal_writes(source) != [], (
        "the widened scan no longer fires on this form; the control has regressed"
    )


def test_the_internals_scan_does_not_fire_on_ordinary_code():
    """The other half — a predicate that flags everything is not a control either."""
    clean = (
        "from api.services import telemetry\n"
        "rec = telemetry.shared_recorder()\n"
        "rec.record({})\n"
        "obj._private = 1\n"
        "setattr(obj, '_private', 1)\n"
        "d['_k'] = 1\n"
    )
    assert telemetry_internal_writes(clean) == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "mod = __import__('api.services.' + n, fromlist=['x'])\nmod._TOKENS = ()\n",
            id="a module object bound from a computed name",
        ),
        pytest.param(
            "mod = registry.lookup()\nmod._TOKENS = ()\n",
            id="a module object with no import statement and no name text",
        ),
    ],
)
def test_and_what_the_widened_internals_scan_still_cannot_catch(source):
    """The limit, pinned in the same falsifiable form as the capability.

    D-1's fallback said unit 3 "states which it cannot catch and why" — which a
    docstring discharges, so an author could widen nothing and satisfy it. These
    fail if the limit moves, exactly like `test_and_what_the_scan_cannot_catch`.
    The subprocess check covered runtime-assembled *references* and no static scan
    replaces that; these two are what remains uncovered, named rather than implied.
    """
    assert telemetry_internal_writes(source) == [], (
        "this form is now catchable; move it to the planted-forms probe above"
    )


def test_the_provider_imports_telemetry_and_telemetry_imports_no_provider():
    """Unit 3 inverted this probe rather than retiring it.

    Before unit 3 it asserted the provider pulls in NO telemetry, transitively at
    any depth. Wiring the recorder makes that false by design, so the probe now
    asserts the two halves that are still worth pinning: the provider DOES import
    telemetry (so the wiring is real and not an unused import someone removed),
    and importing telemetry ALONE does not drag in the provider — no cycle, and
    the module stays standalone, which is what lets its own suite run without a
    provider at all.

    It runs in a subprocess because `sys.modules` here is already polluted by
    this file's own imports, with `-I` for isolation and a minimal environment
    rather than the operator's whole one. A failure is a **failure**, not a
    skip — an earlier version skipped on any non-zero exit, so the probe could
    vanish while still being counted as assurance.
    """
    assert (ROOT / "api" / "services" / "espn.py").exists(), (
        "the provider package is incomplete, so this probe cannot run at all — "
        "which must be a failure, not a skip that leaves the claim unmeasured"
    )
    script = (
        "import sys\n"
        # APPEND, not PYTHONPATH. `-P` drops the implicit cwd entry, but
        # `PYTHONPATH` put the repo root back at *higher* precedence than the
        # stdlib — measured: a `re.py` at the root was imported as `re`. So the
        # probe re-enabled the shadowing the flag was added to prevent, and the
        # evidence said the concern was addressed. Appending keeps the stdlib
        # first, and `-I` can go back on.
        f"sys.path.append({str(ROOT)!r})\n"
        f"for name in {_PROVIDER_MODULES!r}:\n"
        "    __import__(name)\n"
        "forward = 'api.services.telemetry' in sys.modules\n"
        "for mod in list(sys.modules):\n"
        "    if mod.startswith('api.'):\n"
        "        del sys.modules[mod]\n"
        "__import__('api.services.telemetry')\n"
        "backward = 'api.services.espn' in sys.modules\n"
        "print(('WIRED' if forward else 'UNWIRED') + ':' + ('CYCLE' if backward else 'STANDALONE'))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "APP_MODE": "private_operator",
            # The docstring above says this runs with "a minimal environment
            # rather than the operator's whole one". It did not: `cwd=ROOT`
            # let pydantic-settings read the operator's `.env`, which supplied
            # these two required settings, so the probe depended on a
            # gitignored file being present. Found by running the suite against
            # a clean clone of the committed branch -- there the subprocess
            # exits non-zero on a missing setting, which this test correctly
            # treats as a failure rather than a skip.
            "TELEMETRY_ENABLED": "false",
            "TELEMETRY_REPORT_PATH": str(ROOT / ".telemetry-probe-unused.md"),
        },
        timeout=120,
        check=False,
    )
    # No skip branch at all. It skipped on any `ModuleNotFoundError`, including
    # a transitive dependency of the provider — which is how the strongest claim
    # in the evidence silently stopped being measured for a whole round.
    assert proc.returncode == 0, proc.stderr.strip().splitlines()[-1][:200] if proc.stderr else ""
    assert proc.stdout.strip() == "WIRED:STANDALONE", proc.stdout
