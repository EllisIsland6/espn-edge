"""Unit 3 — the recorder wired into the provider, and the offline report.

Contract: `docs/phase-32-provider-behavior-spike.md`, SHA-256 43af4696…, criteria
1-6. Unit 2's seams live in `tests/test_telemetry_seams.py` and are not retested
here; this file is about the wiring and the exporter.

Two things this file is deliberately careful about, because the phase recorded
thirty-two instances of the opposite:

* Every case asserts what it claims to assert. A case that records a row and then
  checks only that the row exists is not a case.
* Where a claim is true of one framing, one spelling or one status, the scope is
  in the test name or the assertion, not in a docstring.
"""

from __future__ import annotations

import ast
import re
import socket
import threading

import httpx
import pytest

from api.services import espn as espn_module
from api.services import telemetry as telemetry_module
from api.services.espn import Cookies, EspnAuthError, EspnError, EspnService, RawCacheStore
from api.services.telemetry import CacheVerdict, Outcome, Recorder, Shape, render_report

ROOT = espn_module.__file__.rsplit("/api/", 1)[0]
HOST = "https://probe.invalid"

#: The owned paths criterion 5 scans. Written out, not globbed: a new owned file
#: is a contract change and should have to appear here.
OWNED = ("api/services/telemetry.py", "api/services/espn.py", "api/config.py")


class ScriptedClient:
    """Replays a scripted sequence and records every call made through it."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, tuple(params or ()), tuple(sorted((headers or {}).items()))))
        item = self.script.pop(0) if self.script else (200, b'{"ok":true}', {})
        if isinstance(item, Exception):
            raise item
        status, body, extra = item
        return httpx.Response(
            status, content=body, headers=extra or {}, request=httpx.Request("GET", url)
        )

    def close(self):
        pass


class MemCache(RawCacheStore):
    def __init__(self, seeded=None):
        super().__init__()
        self.store = dict(seeded or {})

    def get(self, key):
        return self.store.get(key)

    def set(self, key, payload):
        self.store[key] = payload


@pytest.fixture
def no_sleep(monkeypatch):
    """Capture every sleep without performing it.

    `backoff_ms` therefore has to be the NOMINAL ladder value rather than elapsed
    time, or criterion 1's 15,000 assertion and the report's occupancy model would
    both read zero. That is stated here because it is a property of the seam, not
    of the fixture.
    """
    slept = []
    monkeypatch.setattr(espn_module.time, "sleep", lambda s: slept.append(s))
    return slept


def build(script, *, recorder=None, cache=None, min_interval=0.0):
    return EspnService(
        host=HOST,
        client=ScriptedClient(script),
        cache=cache,
        min_interval=min_interval,
        recorder=recorder if recorder is not None else Recorder(),
    )


JSON = {"content-type": "application/json", "content-length": "13"}


# ===========================================================================
# Criterion 1 — the offline cases
# ===========================================================================

CASES = [
    # id, script item, expected status, expected outcome, raises
    ("200", (200, b'{"ok":true}', JSON), 200, Outcome.OK, None),
    ("404", (404, b"nope", {"content-type": "text/plain"}), 404, Outcome.OK, EspnError),
    ("302", (302, b"", {"location": "/login"}), 302, Outcome.OK, EspnError),
    ("empty 200", (200, b"", {"content-type": "application/json"}), 200, Outcome.OK, EspnError),
    (
        "text/html 200",
        (200, b"<html>login</html>", {"content-type": "text/html"}),
        200,
        Outcome.OK,
        EspnError,
    ),
    (
        "malformed body records as a 200",
        (200, b"{not json", {"content-type": "application/json"}),
        200,
        Outcome.OK,
        EspnError,
    ),
]


@pytest.mark.parametrize("label,item,status,outcome,raises", CASES, ids=[c[0] for c in CASES])
def test_each_boundary_case_files_one_row_with_its_status_and_outcome(
    label, item, status, outcome, raises, no_sleep
):
    """One row per attempt, with the status and outcome the boundary actually saw.

    Note what this pins that is easy to get backwards: a malformed body and a
    `text/html` login bounce are both **200s at the transport**. The recorder files
    them as 200/OK and the caller still raises, because recoverability at the
    boundary and the caller's verdict are different questions.
    """
    recorder = Recorder()
    service = build([item], recorder=recorder)
    if raises is None:
        service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    else:
        with pytest.raises(raises):
            service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)

    assert len(recorder.records) == 1, "exactly one attempt, exactly one row"
    row = recorder.records[0]
    assert row.status == status
    assert row.outcome is outcome
    assert row.shape is Shape.LEAGUE_MODERN
    # The four time fields, separately — not "the timings are present".
    assert row.net_ms >= 0
    assert row.gate_ms == 0, "structural zero: this phase does not call the gate"
    assert row.throttle_ms == 0, "first call through a fresh service never waits"
    assert row.backoff_ms == 0, "a returned response is not followed by a backoff"


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_refusal_files_one_row_and_costs_no_backoff(status, no_sleep):
    """401/403 are not in the retry set, so they cost ONE attempt and no sleep.

    This is the fact that made a hard stop armed at the retry branch never fire for
    them, and it is why "up to 8 attempts" is satisfied by 2.
    """
    recorder = Recorder()
    service = build([(status, b"denied", {})], recorder=recorder)
    with pytest.raises(EspnAuthError):
        service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    assert len(recorder.records) == 1
    assert recorder.records[0].status == status
    assert recorder.records[0].outcome is Outcome.OK, "returned to the caller, not retried"
    assert recorder.records[0].backoff_ms == 0
    assert no_sleep == [], "no backoff was slept"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_retryable_status_exhausts_in_four_attempts_on_the_15s_ladder(status, no_sleep):
    """Criterion 1's exhaustion case, driven by every retryable status.

    Four attempts; `backoff_ms` sums to 15,000 with the final 8,000 present, and
    the final 8s precedes no request. The last attempt is EXHAUSTED rather than
    RETRYABLE_STATUS, because it is the one that raised.
    """
    recorder = Recorder()
    service = build([(status, b"busy", {})] * 4, recorder=recorder)
    with pytest.raises(EspnError):
        service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)

    rows = recorder.records
    assert [r.attempt for r in rows] == [1, 2, 3, 4]
    assert [r.backoff_ms for r in rows] == [1000, 2000, 4000, 8000]
    assert sum(r.backoff_ms for r in rows) == 15_000
    assert rows[-1].backoff_ms == 8000, "the final 8s is slept and precedes no request"
    assert [r.outcome for r in rows[:-1]] == [Outcome.RETRYABLE_STATUS] * 3
    assert rows[-1].outcome is Outcome.EXHAUSTED
    assert no_sleep == [1.0, 2.0, 4.0, 8.0]
    assert len(service._client.calls) == 4, "four requests, not five"


def test_a_transport_error_files_a_row_with_no_response_fields(no_sleep):
    """A connect timeout has no response, so status is 0 and both byte fields are -1.

    -1 is the absent sentinel, and the report renders it `absent` rather than -1.
    """
    recorder = Recorder()
    service = build([httpx.ConnectTimeout("timed out")] * 4, recorder=recorder)
    with pytest.raises(EspnError):
        service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    rows = recorder.records
    assert len(rows) == 4
    assert all(r.status == 0 for r in rows)
    assert all(r.wire_bytes == -1 and r.decoded_bytes == -1 for r in rows)
    assert [r.outcome for r in rows] == [Outcome.TRANSPORT_ERROR] * 3 + [Outcome.EXHAUSTED]


def test_gzip_records_wire_and_decoded_separately_without_a_socket(no_sleep):
    """wire_bytes is Content-Length AS SENT, read from the header, not counted off
    the wire — which is why this needs no socket.

    The contract first mandated a loopback socket for this case on the grounds that
    a mock transport "cannot produce distinct wire and decoded counts". It can: the
    header and the body are set independently.
    """
    recorder = Recorder()
    body = b'{"ok":true}'
    service = build(
        [(200, body, {"content-type": "application/json", "content-length": "7"})],
        recorder=recorder,
    )
    service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    row = recorder.records[0]
    assert row.wire_bytes == 7
    assert row.decoded_bytes == len(body)
    assert row.wire_bytes != row.decoded_bytes, "compressed on the wire, larger decoded"


def serve_raw(payload: bytes):
    """A loopback server that sends exactly these bytes. Returns the port.

    Needed because `httpx.Response(content=...)` sets `content-length` for you, so
    the ABSENCE of that header cannot be faked at the response level — only on a
    wire. Measured, not assumed: the first version of the chunked case below set no
    content-length and httpx supplied one, so the case asserted -1 and got 11.
    """
    server = socket.socket()
    server.bind(("127.0.0.1", 0))          # a failure here is a FAILURE, see below
    server.listen(1)
    server.settimeout(5.0)
    port = server.getsockname()[1]

    def run():
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5.0)           # NOT inherited from the listener
            try:
                conn.recv(65535)
                conn.sendall(payload)
            except OSError:
                return

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server, thread, port


def test_loopback_bind_is_available_and_a_failure_is_not_a_skip():
    """Criterion 1: the socket cases must not pass by skipping.

    Loopback-bind availability is a declared build precondition. An environment
    without it cannot satisfy the contract, and this says so in one failing test
    instead of letting three cases skip and the build go green having measured
    nothing. This replaces unit 2's `pytest.skip` on the same capability.
    """
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.fail(
            f"loopback bind unavailable ({exc}); the truncation and chunked cases "
            f"cannot be measured here, which is a build failure by declared precondition"
        )
    finally:
        probe.close()


CHUNKED_OK = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Transfer-Encoding: chunked\r\n\r\n"
    b"b\r\n{\"ok\":true}\r\n0\r\n\r\n"
)
DECLARED_SHORT = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 512\r\n"
    b"Connection: close\r\n\r\n" + b'{"ok":true}'
)
CHUNKED_SHORT = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Transfer-Encoding: chunked\r\n\r\n"
    b"ff\r\nonly-a-few-bytes"
)


def test_a_chunked_response_records_wire_bytes_absent(no_sleep):
    """Chunked framing: no Content-Length on the wire, so wire_bytes is the -1
    sentinel and the report renders it `absent` rather than -1.

    A real wire, for the reason in `serve_raw`.
    """
    server, thread, port = serve_raw(CHUNKED_OK)
    recorder = Recorder()
    try:
        with httpx.Client(timeout=5.0) as client:
            service = EspnService(
                host=f"http://127.0.0.1:{port}",
                client=client,
                cache=None,
                min_interval=0.0,
                recorder=recorder,
            )
            service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    finally:
        server.close()
        thread.join(timeout=6.0)
    assert not thread.is_alive()
    row = recorder.records[0]
    assert row.wire_bytes == -1, "no Content-Length on a chunked response"
    assert row.decoded_bytes == 11, "the body still decodes"
    assert row.status == 200


@pytest.mark.parametrize(
    "label,payload",
    [("declared content-length", DECLARED_SHORT), ("chunked framing", CHUNKED_SHORT)],
)
def test_a_truncated_body_is_a_transport_error_not_a_200(label, payload, no_sleep):
    """Both framings the contract claims, each measured on a real wire.

    Unit 2 measured ONE of these and three drafts asserted two; `chunk`,
    `transfer-encoding` and `gzip` appeared nowhere in its suite. This is the case
    that was claimed and missing.

    A truncated body does not reach the boundary as a 200 at all — it raises
    `RemoteProtocolError`, which is an `httpx.HTTPError`, so `_request` retries it
    and the recorder files it as a transport error with status 0.
    """
    server, thread, port = serve_raw(payload)
    recorder = Recorder()
    try:
        with httpx.Client(timeout=5.0) as client:
            service = EspnService(
                host=f"http://127.0.0.1:{port}",
                client=client,
                cache=None,
                min_interval=0.0,
                max_retries=1,
                recorder=recorder,
            )
            with pytest.raises(EspnError):
                service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    finally:
        server.close()
        thread.join(timeout=6.0)
    assert not thread.is_alive()
    assert len(recorder.records) == 1
    row = recorder.records[0]
    assert row.status == 0, "no response was received, so not a 200"
    assert row.outcome is Outcome.EXHAUSTED
    assert row.wire_bytes == -1 and row.decoded_bytes == -1


def test_etag_and_last_modified_are_recorded_as_bools_not_values(no_sleep):
    """Presence, never the value — an ETag is an opaque identifier."""
    recorder = Recorder()
    service = build(
        [(200, b'{"ok":true}', {**JSON, "etag": 'W/"abc123"', "last-modified": "Wed, 01 Jan 2025"})],
        recorder=recorder,
    )
    service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    row = recorder.records[0]
    assert row.etag is True and row.last_modified is True
    assert "abc123" not in repr(row), "the value must not reach a repr"


def test_304_is_reachable_but_dead_by_design(no_sleep):
    """Included and labelled dead: nothing sends a conditional request, so a 304
    can only arrive unprovoked. Not counted as coverage."""
    recorder = Recorder()
    service = build([(304, b"", {})], recorder=recorder)
    with pytest.raises(EspnError):
        service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    assert recorder.records[0].status == 304
    sent = service._client.calls[0][2]
    assert not any(k in ("if-none-match", "if-modified-since") for k, _ in sent)


def test_the_doubled_loop_reaches_exactly_eight_attempts(no_sleep):
    """Criterion 1: exactly 8, with all three preconditions, not "up to 8".

    "Up to 8 attempts" is satisfied by 2, and a plain 401-then-401 script produces
    exactly 2 — which is how this case could pass while measuring nothing. All
    three conditions are needed:

      (a) the first response is 401/403, or `_get_authed` returns before the second
          ladder;
      (b) `unquote(espn_s2) != espn_s2`, or the second ladder is never entered;
      (c) EACH `_request` spends three retryable statuses and returns the 401 on its
          fourth attempt — an all-retryable ladder raises and never reaches (a).
    """
    recorder = Recorder()
    script = [(500, b"busy", {})] * 3 + [(401, b"denied", {})]
    service = build(script * 2, recorder=recorder)
    cookies = Cookies(swid="{S}", espn_s2="a%3Db")          # (b): encoded != decoded
    assert "%" in cookies.espn_s2, "precondition (b) must actually hold"

    with pytest.raises(EspnAuthError):
        service.fetch_views(1, 2026, ["mTeam"], cookies=cookies, bust_cache=True)

    assert len(service._client.calls) == 8, "two ladders of four"
    assert len(recorder.records) == 8
    assert [r.attempt for r in recorder.records] == [1, 2, 3, 4] * 2, "1-based within each call"
    assert [r.status for r in recorder.records] == [500, 500, 500, 401] * 2


def test_a_plain_auth_refusal_costs_two_attempts_not_eight(no_sleep):
    """The other half of the claim above — the number "up to 8" would have hidden."""
    recorder = Recorder()
    service = build([(401, b"denied", {})] * 2, recorder=recorder)
    with pytest.raises(EspnAuthError):
        service.fetch_views(1, 2026, ["mTeam"], cookies=Cookies("{S}", "a%3Db"), bust_cache=True)
    assert len(recorder.records) == 2


def test_the_second_ladder_is_not_entered_when_the_cookie_needs_no_decoding(no_sleep):
    """Precondition (b), asserted rather than described."""
    recorder = Recorder()
    service = build([(401, b"denied", {})], recorder=recorder)
    with pytest.raises(EspnAuthError):
        service.fetch_views(1, 2026, ["mTeam"], cookies=Cookies("{S}", "plain"), bust_cache=True)
    assert len(recorder.records) == 1, "nothing to decode, so no second ladder"


# ===========================================================================
# Criterion 2 — cache verdicts, and a zero that is not a rate
# ===========================================================================


def test_a_hit_is_counted_where_it_is_decided_and_never_reaches_the_request_seam(no_sleep):
    key = EspnService._cache_key(1, 2026, ["mTeam"], None, None, None)
    recorder = Recorder()
    service = build([], recorder=recorder, cache=MemCache({key: {"cached": True}}))
    assert service.fetch_views(1, 2026, ["mTeam"]) == {"cached": True}
    assert recorder.records == [], "a HIT makes no HTTP attempt at all"
    assert recorder.counters.cache[("league_modern", "hit")] == 1


def test_a_miss_says_so_and_cannot_separate_absent_from_stale(no_sleep):
    recorder = Recorder()
    service = build([(200, b'{"ok":true}', JSON)], recorder=recorder, cache=MemCache())
    service.fetch_views(1, 2026, ["mTeam"])
    assert recorder.counters.cache[("league_modern", "miss")] == 1
    assert len(recorder.records) == 1, "a MISS does reach the network"


def test_bypass_is_taken_at_the_guard_because_the_cache_is_never_consulted(no_sleep):
    recorder = Recorder()
    cache = MemCache()
    service = build([(200, b'{"ok":true}', JSON)], recorder=recorder, cache=cache)
    service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    assert recorder.counters.cache[("league_modern", "bypass")] == 1
    assert ("league_modern", "miss") not in recorder.counters.cache


def test_the_pro_schedule_cache_is_not_league_scoped(no_sleep):
    """A league-scoped clear leaves this warm, which is why the report records both
    cache states separately."""
    recorder = Recorder()
    service = build([], recorder=recorder, cache=MemCache({"pro_schedule:2026": {"s": 1}}))
    service.fetch_pro_schedule(2026)
    assert recorder.counters.cache[("season", "hit")] == 1


@pytest.mark.parametrize(
    "events,trials,expect_interval",
    [(0, 10, True), (3, 10, False), (0, 0, False)],
)
def test_a_zero_with_no_trials_is_not_rendered_as_a_bound(events, trials, expect_interval):
    """`trials == 0` is not a rate with a wide interval — it is not a measurement.

    Draft 3 prescribed `3/N` for two shapes whose N is zero. The failing edit is
    printing the interval unconditionally.
    """
    rendered = telemetry_module._upper_bound(events, trials)
    assert ("upper bound" in rendered) is expect_interval
    if trials == 0:
        assert "no call site" in rendered


# ===========================================================================
# Criterion 3 — flag off/on equivalence, unconditional
# ===========================================================================


def _drive(recorder, script, *, cookies=None):
    """Run one identical fetch and return (calls, raised type, raised message)."""
    service = build(list(script), recorder=recorder)
    raised = None
    try:
        service.fetch_views(1, 2026, ["mTeam"], cookies=cookies, bust_cache=True)
    except Exception as exc:  # noqa: BLE001 - the type and message are the assertion
        raised = (type(exc), str(exc))
    return service._client.calls, raised


EQUIV_SCRIPTS = [
    ("200", [(200, b'{"ok":true}', JSON)]),
    ("404", [(404, b"nope", {})]),
    ("401", [(401, b"denied", {})]),
    ("malformed", [(200, b"{not json", {"content-type": "application/json"})]),
    ("text/html", [(200, b"<html>", {"content-type": "text/html"})]),
    ("exhaustion", [(500, b"busy", {})] * 4),
    ("transport", [httpx.ConnectTimeout("t")] * 4),
]


@pytest.mark.parametrize("label,script", EQUIV_SCRIPTS, ids=[s[0] for s in EQUIV_SCRIPTS])
def test_recording_changes_no_request_and_no_raised_message(label, script, no_sleep):
    """Criterion 3, and it is unconditional now.

    Draft 7 needed two carve-outs — one for the gate's timing, one for a hard stop
    that truncated the request count. Neither exists, so there is nothing to carve.

    The exception MESSAGE is in the compared tuple, not just the type: six sites in
    `espn.py` interpolate the request URL into a message, three of them in
    `_json_or_auth`, and those three raise the messages for the malformed-body,
    text/html and 401 cases above. Comparing only the type would leave the messages
    for this criterion's own required cases outside the compared surface.

    Scope, stated because it is what makes this true rather than merely green: the
    comparison is single-threaded and per-`_request`. Nothing in this phase reorders
    concurrent starts, so cross-thread ordering is not at issue.
    """
    off_calls, off_raised = _drive(None, script)
    on_recorder = Recorder()
    on_calls, on_raised = _drive(on_recorder, script)

    assert off_calls == on_calls, "same URL, same ordered params, same headers, same count"
    assert off_raised == on_raised, "same exception type AND same message"
    assert len(on_recorder.records) == len(on_calls), "one row per attempt, flag on"


def test_a_rejected_row_costs_a_row_and_not_a_request(no_sleep):
    """Criterion 3's recorder clause, scoped to what unit 2 actually guarantees.

    Unit 2 guarantees that the REAL `Recorder.record` never raises an `Exception`
    into the caller, for any input, and converts a bad row into a counted drop.
    That is what this asserts. It does NOT assert that an arbitrary object
    substituted for the recorder is absorbed: `_file` calls `record` unguarded, on
    purpose, because a blanket `except` there would swallow real bugs and unit 2
    deliberately does not catch `BaseException` either. The first version of this
    test asserted the unscoped version and passed for the wrong reason.
    """
    recorder = Recorder()
    service = build([(200, b'{"ok":true}', JSON)], recorder=recorder)
    service._file(
        Shape.LEAGUE_MODERN, Outcome.OK, attempt=1, status="not-an-int",
        resp=None, net_ms=0, throttle_ms=0, backoff_ms=0,
    )
    assert recorder.counters.dropped == 1
    assert recorder.records == [], "the bad row was refused at construction"
    assert recorder.dropped_by_shape.get("league_modern") == 1, "attributed per shape"

    service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    assert len(service._client.calls) == 1, "the drop did not cost a request"
    assert len(recorder.records) == 1, "and the next good row still files"


def test_header_extraction_cannot_break_a_fetch(no_sleep):
    """A provider may send anything, so a Content-Length that is not a number is a
    -1, not an exception into the caller."""
    recorder = Recorder()
    service = build(
        [(200, b'{"ok":true}', {"content-type": "application/json", "content-length": "banana"})],
        recorder=recorder,
    )
    service.fetch_views(1, 2026, ["mTeam"], bust_cache=True)
    assert recorder.records[0].wire_bytes == -1


def test_the_flag_off_path_records_nothing_and_writes_no_file():
    """`recorder=None` with the flag off is the resting state: no rows, no object."""
    service = EspnService(host=HOST, client=ScriptedClient([]), cache=None, min_interval=0.0)
    assert service._recorder is None, "the flag is false in this suite"


def test_the_shape_keyword_is_optional_so_unit_twos_probes_still_call_request():
    """K-1, pinned. Seven unit-2 probes call `_request` with no `shape=`.

    A required keyword-only parameter would break all seven and fail the criterion
    requiring unit 2's suite to pass unchanged. This is the probe that stops someone
    tightening the signature later.
    """
    import inspect

    sig = inspect.signature(EspnService._request)
    assert sig.parameters["shape"].default is None
    service = build([(200, b'{"ok":true}', JSON)], recorder=Recorder())
    resp = service._request(HOST, params=[], headers={}, throttle_key="public")
    assert resp.status_code == 200
    assert service._recorder.records == [], "no shape means no row, not a crash"


# ===========================================================================
# Criterion 4 — the report grammar
# ===========================================================================

#: WRITTEN OUT, not derived from the exporter. An allowlist whose contents are
#: whatever the checked artifact emits is not closed, and it would auto-admit every
#: token a future exporter invents. Adding a word to the report means adding it
#: here, deliberately, in a diff someone reviews.
ALLOWED_TOKENS = frozenset("""
PHASE32 REPORT BEGIN END KiB MiB
a absent an and api are at attempts backoff behaviour below bound boundary bounds
bucketed builder by bypass byte cache call called caller ceiling censored check
claim conditional context count cross dead decoded defaults design discovery
dropped durations error espn established every exercised exist field figures filed
fixture from gate generated grid hit i identifier input invoked is it kib label
league_history league_modern leagues limit limits linkability loopback lower made
measured model ms n near net network never no not observation observed occupancy
offline ok only or outcome outcomes overflowed p50 p95 pct phase players
players_defaults players_season precision provider publishes rate rates reaches
reason recorded report request requests s scope seam season service shape shapes
site status statuses structural term the this throttle to traffic under ungated
unmeasured until upper value verdict was week where width wire zeros
decided over exhausted retryable_status transport_error miss too small hit
unpublishable classified
""".split())

_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_DIGIT_RUN = re.compile(r"[0-9]{6,}")
_URL = re.compile(r"https?://")
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}")


def grammar_violations(text: str) -> list[str]:
    """The closed grammar, as one function so both sides of the test share it."""
    bad = [f"unlisted token {t!r}" for t in _WORDS.findall(text) if t not in ALLOWED_TOKENS]
    bad += [f"digit run {m!r}" for m in _DIGIT_RUN.findall(text)]
    bad += [f"url {m!r}" for m in _URL.findall(text)]
    bad += [f"guid {m!r}" for m in _GUID.findall(text)]
    return bad


def a_report(recorder=None, **ctx):
    recorder = recorder if recorder is not None else Recorder()
    return render_report(recorder, **{"season": 2026, "week": 3, "leagues": 1, **ctx})


def filled_recorder(**overrides):
    """A recorder covering the exporter's ENTIRE output space, not a subset.

    The first version filed only `Outcome.OK`, only `CacheVerdict.BYPASS` and six
    rows of one shape — so the grammar test passed over a report the exporter
    cannot actually produce, and the committed artifact carried seven violations
    the suite could not see. Every outcome, every verdict, and a thin shape (so the
    `n too small` label is rendered) are all exercised here.
    """
    recorder = Recorder()

    def row(shape, outcome, **extra):
        base = {
            "shape": shape, "outcome": outcome, "attempt": 1, "status": 200,
            "wire_bytes": 3_145_728, "decoded_bytes": 3_200_000, "net_ms": 120,
            "gate_ms": 0, "throttle_ms": 1000, "backoff_ms": 0,
            "etag": True, "last_modified": False, "content_type_json": True,
        }
        base.update(extra)
        base.update(overrides)
        recorder.record(base)

    for i, outcome in enumerate(Outcome):                 # all four outcomes
        for _ in range(5):                                 # >= _THIN_BELOW: "observed"
            row(Shape.LEAGUE_MODERN, outcome, net_ms=120 + i)
    row(Shape.LEAGUE_HISTORY, Outcome.OK)                  # n=1: "n too small"
    row(Shape.SEASON, Outcome.EXHAUSTED, status=0, wire_bytes=-1, decoded_bytes=-1)
    for verdict in CacheVerdict:                           # all three verdicts
        recorder.cache_verdict(Shape.LEAGUE_MODERN, verdict)
    return recorder


def test_the_fixture_covers_every_token_the_exporter_can_emit():
    """The fixture is a control, so it gets its own probe.

    A grammar assertion over a report that exercises one outcome and one verdict
    establishes the grammar over a subset. This pins the fixture against the enums
    themselves, so adding a member fails HERE rather than silently shrinking the
    coverage of every grammar test below.
    """
    text = a_report(filled_recorder())
    for member in list(Outcome) + list(CacheVerdict):
        token = telemetry_module._token(member)
        assert token in text, f"the fixture never renders {token}"
    assert "n too small" in text, "the thin-shape label is never rendered"
    assert "no call site" in text
    assert "observed" in text


def test_a_fixture_produced_report_passes_the_grammar():
    """So the grammar holds on the declined-envelope branch, where every input is a
    fixture and no live read ever happens."""
    assert grammar_violations(a_report(filled_recorder())) == []


def test_an_empty_report_passes_the_grammar():
    assert grammar_violations(a_report()) == []


def test_the_report_is_delimited_by_its_markers():
    text = a_report(filled_recorder())
    assert text.startswith(telemetry_module.REPORT_BEGIN)
    assert text.rstrip().endswith(telemetry_module.REPORT_END)


EXPECTED_REPORT_SHAPES = (
    "league_modern", "league_history", "players_defaults", "players_season", "season",
)
EXPECTED_NO_CALL_SITE = frozenset({"players_defaults", "players_season"})


def test_the_report_shape_list_is_the_one_written_down_here_and_matches_Shape():
    """Round 6's defect, in a new spelling: the label probe below iterated
    `_REPORT_SHAPES`, so REMOVING a member stopped the exporter printing its row and
    stopped the probe looking for it — all green. Pinned twice now: against a literal
    here, and against `Shape`'s own members, so neither list can drift alone.
    """
    assert telemetry_module._REPORT_SHAPES == EXPECTED_REPORT_SHAPES
    assert telemetry_module._NO_CALL_SITE == EXPECTED_NO_CALL_SITE
    assert set(EXPECTED_REPORT_SHAPES) == {telemetry_module._token(m) for m in Shape}


@pytest.mark.parametrize("token", EXPECTED_REPORT_SHAPES)
def test_every_shape_is_printed_with_exactly_one_label(token):
    """Parametrised over the LITERAL, and asserting the label in that shape's own row.

    The previous assertion was `text.count("no call site") >= 2`, which an empty
    report satisfies eight times over — five of those from the `## rates` table — so
    it established "the phrase appears somewhere", not "these two shapes are labelled
    as having no caller".
    """
    text = a_report(filled_recorder())
    shapes = text.split("## shapes", 1)[1].split("## outcomes", 1)[0]
    match = re.search(rf"^\| {token} \| ([a-z ]+?) \|", shapes, re.M)
    assert match, f"{token} has no row in the shapes table"
    label = match.group(1)
    assert label in ("observed", "n too small", "no call site", "not exercised")
    if token in EXPECTED_NO_CALL_SITE:
        assert label == "no call site", f"{token} has no caller and must say so"
    else:
        assert label != "no call site"


# ---- the two-sided plants -------------------------------------------------

def test_a_planted_league_shaped_segment_fails_on_the_digit_run_rule():
    """Free-text position. The rule that catches it is named, because a plant that
    trips a different rule than the one it is testing proves nothing."""
    bad = grammar_violations(a_report(filled_recorder()) + "\nleague 1234567 was slow\n")
    assert any("digit run" in b for b in bad)


@pytest.mark.parametrize(
    "plant,expect_kind,forbid_kinds",
    [
        pytest.param("league 1234567 was slow", "digit run", ("url", "guid"), id="digit run"),
        pytest.param("see https://x.test/a", "url", ("digit run", "guid"), id="url"),
        pytest.param(
            "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d", "guid", ("digit run",), id="guid"
        ),
        pytest.param("zzqq", "unlisted token", ("digit run", "url", "guid"), id="allowlist"),
    ],
)
def test_each_grammar_rule_has_its_own_failing_case(plant, expect_kind, forbid_kinds):
    """Four rules, four dedicated plants — and each plant must trip ONLY its rule.

    Removing the url, guid or allowlist rule previously left every test green,
    because the one plant that existed tripped several rules at once and the
    assertion accepted any of them. "A plant that trips a different rule than the
    one it is testing proves nothing" was this file's own standard, stated in the
    sibling test and broken here.
    """
    bad = grammar_violations(a_report(filled_recorder()) + f"\n{plant}\n")
    assert any(b.startswith(expect_kind) for b in bad), f"{expect_kind} did not fire: {bad}"
    for kind in forbid_kinds:
        assert not any(b.startswith(kind) for b in bad), (
            f"the plant also tripped {kind}, so it cannot pin {expect_kind}"
        )


def test_a_duration_above_the_ceiling_carries_no_digit_of_itself():
    """The ceiling is the control for durations: every six-digit value is over it,
    and so is the top three quarters of the five-digit range."""
    rendered = telemetry_module._render_ms(543_210)
    assert rendered == "over-ceiling"
    assert "543" not in rendered and "210" not in rendered
    assert telemetry_module._render_ms(54_321) == "over-ceiling", "five digits, over 30 s"


def test_a_duration_at_or_below_the_ceiling_publishes_and_that_is_the_residual():
    """The residual, asserted rather than described. A plant asserting failure here
    would be false, and draft 6 specified exactly that."""
    assert telemetry_module._render_ms(12_345) == "12.35 s"
    assert telemetry_module._render_ms(1) == "0.05 s", "on the 50 ms grid"


def test_a_seven_digit_byte_count_is_absorbed_by_the_grid_not_refused():
    """Bucketing is a mitigation, not a refusal: the value publishes, bucketed. 64 KiB
    over a seven-digit id destroys its low 16 bits, which is the strongest argument
    for unconditional bucketing and is not the same as closing the channel."""
    assert telemetry_module._render_bytes(3_145_728) == "3.0 MiB"
    assert telemetry_module._render_bytes(1_234_567) == "1.2 MiB"
    assert "1234567" not in telemetry_module._render_bytes(1_234_567)


def test_a_five_digit_count_publishes_exactly_which_is_the_worst_residual():
    """The grid closes NOTHING below its ceiling in any step-1 integer field. In a
    count, season or league count an in-range value publishes exactly, with no
    bucketing at all — strictly worse than the duration residual, and asserted here
    rather than left to be discovered."""
    assert telemetry_module._render_count(54_321) == "54321"
    assert telemetry_module._render_count(1_000_000) == "over-ceiling"
    assert telemetry_module._render_small(2026, telemetry_module._SEASON_CEILING) == "2026"


def test_a_byte_count_is_never_rendered_as_a_raw_integer():
    """The rule that broke when the grid was simplified away: a 64 KiB bucket printed
    in bytes is a multiple of 65536, so every bucket from the second upward is a
    six-digit run and the digit rule rejects it. Rendering in KiB/MiB is the fix."""
    for value in (1 << 17, 1 << 20, 1 << 24, (1 << 26) - 1):
        rendered = telemetry_module._render_bytes(value)
        assert not _DIGIT_RUN.search(rendered), rendered
        assert rendered.endswith(("KiB", "MiB"))


def test_the_absent_sentinel_is_not_printed_as_minus_one():
    assert telemetry_module._render_bytes(-1) == "absent"


# ===========================================================================
# Criterion 5 — no owned module reads a member's name or value
# ===========================================================================

#: `spec.name` is a `dataclasses.Field`, not an enum member, and is the only
#: sanctioned `.name` in the owned tree. Carried over from unit 2's probe, which
#: the widening instruction omitted: dropping the `telemetry.py` skip without
#: carrying this allowance puts that one legitimate read in scope.
_ALLOWED_RECEIVERS = {"spec"}


def member_reads(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    offenders = [
        f"{filename}:{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in {"name", "value", "_name_", "_value_"}
        and not (isinstance(node.value, ast.Name) and node.value.id in _ALLOWED_RECEIVERS)
    ]
    # `getattr(member, "value")` is a Call, not an Attribute — the round-6 form.
    offenders += [
        f"{filename}:{node.lineno}: getattr"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in {"name", "value", "_name_", "_value_"}
    ]
    # `dataclasses.asdict(record)` still emits LIVE enum members — the one residual
    # this criterion converts into a pinned control.
    offenders += [
        f"{filename}:{node.lineno}: asdict"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "asdict")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "asdict")
        )
    ]
    return offenders


def test_no_owned_module_reads_a_members_name_or_value():
    """Widened from unit 2's one-file probe to every owned path.

    Owned-path scoping is load-bearing, not incidental: the five `dataclasses.asdict`
    call sites under `api/` are all in `api/services/recovery.py`, a Forbidden path,
    so a literally tree-wide scan would fail on day one.

    The limit, in the same place as the capability: identifying "a `Shape` expression"
    statically needs type inference. `x = record.shape; x.value` is invisible to any
    receiver-name test, and this predicate flags EVERY `.name`/`.value` in the owned
    tree rather than trying to infer a type — which is why it is sound here and why
    it could not be extended to a module full of legitimate `.value` reads.
    """
    offenders = []
    for rel in OWNED:
        path = f"{ROOT}/{rel}"
        with open(path) as handle:
            offenders += member_reads(handle.read(), rel)
    assert offenders == [], offenders


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("x = record.shape.value\n", id="a direct .value read"),
        pytest.param("x = record.shape._value_\n", id="the underlying storage"),
        pytest.param('x = getattr(record.shape, "value")\n', id="getattr with a literal"),
        pytest.param("from dataclasses import asdict\nx = asdict(record)\n", id="asdict"),
        pytest.param("import dataclasses\nx = dataclasses.asdict(record)\n", id="qualified asdict"),
    ],
)
def test_the_member_read_scan_fires_on_each_planted_form(source):
    assert member_reads(source, "<planted>") != []


def test_the_scan_still_allows_the_one_sanctioned_field_name():
    """The false-positive direction. `spec.name` is a dataclass Field, and dropping
    unit 2's `telemetry.py` skip without carrying its `allowed` set would flag it."""
    assert member_reads("x = spec.name\n", "<planted>") == []


def test_the_exporter_output_is_identical_with_a_rewritten_member():
    """Rewriting a member's `_value_` changes nothing the exporter publishes.

    This converts ONE documented residual into a pinned control — `asdict` emitting
    live members. It does NOT pin rebindable `_TOKENS` (a `_value_` rewrite does not
    touch it) or `_FIELD_BOUNDS` (construction-time range checks, no bearing on
    member rendering). Draft 6 claimed three; it is one.
    """
    before = a_report(filled_recorder())
    original = Shape.LEAGUE_MODERN._value_
    try:
        Shape.LEAGUE_MODERN._value_ = "leagues/1234567"
        after = a_report(filled_recorder())
    finally:
        Shape.LEAGUE_MODERN._value_ = original
    assert after == before, "the exporter renders through _token, never through .value"
    assert "1234567" not in after
    assert grammar_violations(after) == []


# ===========================================================================
# The controls review found removable — each now has a failing case
# ===========================================================================


def test_the_loopback_guard_blocks_a_non_loopback_connect():
    """Criterion 1's network enforcement, exercised rather than declared.

    Deleting the `monkeypatch.setattr(socket.socket, "connect", guarded)` line in
    `conftest.py`, repointing `ESPN_API_HOST` at the real provider, or adding that
    host to `_ALLOWED_HOSTS` each left the whole suite green — because no test ever
    attempted a non-loopback connect. The mechanism worked and nothing established
    that it worked, which is the distinction this project exists to hold.
    """
    sock = socket.socket()
    try:
        with pytest.raises(AssertionError) as caught:
            sock.connect(("fantasy.espn.example", 443))
    finally:
        sock.close()
    assert "fantasy.espn.example" in str(caught.value), "the guard names the host it blocked"


def test_the_configured_host_is_loopback_so_a_forgotten_host_fails_closed():
    """The second half: the pin, not just the guard.

    `espn_api_host` defaults to the real provider and `EspnService` falls back to it,
    so a construction that omits `host=` builds provider URLs. Pinning the env var is
    what makes that slip land on a closed port instead.
    """
    from api.config import get_settings

    assert get_settings().espn_api_host.startswith("https://127.0.0.1")


def test_the_flag_on_path_binds_the_shared_recorder(monkeypatch):
    """The only path by which this feature turns on in production, exercised.

    Replacing `telemetry.shared_recorder()` with `None` in `espn.py` left all tests
    green: every test either passed `recorder=` explicitly or ran flag-off, so the
    production wiring was never executed. Neutering the flag was invisible.
    """

    class Enabled:
        telemetry_enabled = True
        espn_api_host = HOST
        is_public_synthetic = False

    monkeypatch.setattr(espn_module, "get_settings", lambda: Enabled())
    service = EspnService(host=HOST, client=ScriptedClient([]), cache=None, min_interval=0.0)
    assert service._recorder is telemetry_module.shared_recorder(), (
        "flag on must bind the process-wide recorder, not None and not a fresh one"
    )


def test_a_row_with_no_shape_is_not_counted_as_an_attempt(no_sleep):
    """Removing `or shape is None` from `_file` left every test green — but it does
    not merely skip the row: the row is refused at construction, `attempts` goes to 1
    and `dropped` to 1, unattributable to any shape. `attempts` is the denominator of
    both published rates, so every unit-2-style `_request` call without `shape=` would
    inflate the denominator and deflate the rates.
    """
    recorder = Recorder()
    service = build([(200, b'{"ok":true}', JSON)], recorder=recorder)
    service._request(HOST, params=[], headers={}, throttle_key="public")
    assert recorder.records == []
    assert recorder.counters.attempts == 0, "an unshaped call is not an attempt"
    assert recorder.counters.dropped == 0, "and it is not a drop either"
    assert recorder.dropped_by_shape == {}


def test_the_censored_column_is_non_zero_when_a_duration_is_refused():
    """`censored` appeared in no assertion, so `_censored_ms` returning 0 unconditionally
    was invisible — the artifact's all-zero column was equally consistent with "nothing
    was refused" and "the counter is broken"."""
    recorder = Recorder()
    for _ in range(5):
        recorder.record({
            "shape": Shape.SEASON, "outcome": Outcome.OK, "attempt": 1, "status": 200,
            "wire_bytes": -1, "decoded_bytes": -1,
            "net_ms": 600_000,        # legal under _FIELD_BOUNDS, over the 30 s ceiling
            "gate_ms": 0, "throttle_ms": 0, "backoff_ms": 0,
            "etag": False, "last_modified": False, "content_type_json": True,
        })
    text = render_report(recorder, season=2026, week=3, leagues=1)
    # Scoped to the shapes table: `| season | 2026 |` in the CONTEXT table matches a
    # looser pattern first, which is how the first version of this assertion read a
    # row that was never the one under test.
    shapes = text.split("## shapes", 1)[1].split("## outcomes", 1)[0]
    row = re.search(r"^\| season \|.*$", shapes, re.M).group(0)
    assert "over-ceiling" in row, "the duration must not publish"
    assert row.rstrip().endswith("| 5 |"), f"censored must count all five: {row}"
    assert grammar_violations(text) == []


def test_a_malformed_cache_key_is_never_published():
    """The round-6 `Counters.__repr__` finding, re-opened on the publication path.

    Every other cell goes through `_token` or a renderer; the cache table went
    through bare `str()`, and review measured a full league-shaped URL published into
    a committed file. The gate is now the same one `Counters.__repr__` uses.
    """
    leaky = "https://x.test/apis/v3/games/ffl/seasons/2026/segments/0/leagues/1234567"
    recorder = Recorder()
    recorder.counters.cache[("league_modern", leaky)] = 1
    recorder.counters.cache[(leaky, "hit")] = 1
    recorder.counters.cache[leaky] = 1
    recorder.counters.cache[(leaky,)] = 1
    text = render_report(recorder, season=2026, week=3, leagues=1)
    assert leaky not in text
    assert "1234567" not in text
    assert "unpublishable" in text, "refused keys are counted, not silently dropped"
    assert grammar_violations(text) == []


def test_the_committed_artifact_passes_the_grammar():
    """The artifact itself, not a report rendered inside the test.

    "The report passes its own grammar" was true of a test-rendered report and false
    of the committed file, which carried seven violations — because the fixture filed
    a strict subset of the exporter's output space and nothing ever pointed the
    grammar at `docs/evidence/phase-32-report.md`.
    """
    with open(f"{ROOT}/docs/evidence/phase-32-report.md") as handle:
        artifact = handle.read()
    assert grammar_violations(artifact) == []
    assert artifact.startswith(telemetry_module.REPORT_BEGIN)


def test_the_report_has_a_committed_writer_so_the_artifact_is_reproducible(tmp_path):
    """`render_report` had two references repo-wide — its definition and a test helper.

    No committed entry point produced the artifact, so nobody could regenerate it and
    `telemetry_report_path` was a required setting with no reader.
    """
    target = tmp_path / "nested" / "report.md"
    text = telemetry_module.write_report(
        filled_recorder(), season=2026, week=3, leagues=1, path=target
    )
    assert target.read_text() == text
    assert grammar_violations(text) == []
