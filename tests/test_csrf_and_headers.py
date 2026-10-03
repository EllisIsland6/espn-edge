"""CSRF and response headers, hosted mode.

The double-submit mechanism rests on an asymmetry: an attacker's page can make
a browser SEND our cookies to us, but cannot READ them to forge the matching
header, because it sits on another origin. So the session cookie is HttpOnly
and the CSRF cookie deliberately is not -- our own page must read it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import app
from api.security import CSRF_COOKIE, CSRF_HEADER, clear_session_cookies, set_session_cookies


def _hosted(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.config.get_settings",
        lambda: Settings(app_mode="public_synthetic"),
        raising=True,
    )


UNSAFE = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.mark.parametrize("method", UNSAFE)
def test_an_unsafe_method_without_a_token_is_refused(method, monkeypatch):
    _hosted(monkeypatch)
    with TestClient(app) as client:
        response = client.request(method, "/api/auth/logout")
    assert response.status_code == 403, f"{method} passed with no CSRF token"


@pytest.mark.parametrize("method", UNSAFE)
def test_a_mismatched_token_is_refused(method, monkeypatch):
    """Having *a* token is not enough -- an attacker can set a cookie on a
    sibling domain. The two must be equal."""
    _hosted(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(CSRF_COOKIE, "cookie-value")
        response = client.request(
            method, "/api/auth/logout", headers={CSRF_HEADER: "different-value"}
        )
    assert response.status_code == 403, f"{method} accepted a mismatched token"


def test_a_matching_token_passes_the_check(monkeypatch):
    """Reaching 401 rather than 403 is the pass: CSRF let it through, and the
    session check then refused it for the unrelated reason that there is no
    session. Distinguishing the two is the point."""
    _hosted(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(CSRF_COOKIE, "same")
        response = client.post("/api/auth/logout", headers={CSRF_HEADER: "same"})
    assert response.status_code == 401, response.text


def test_safe_methods_are_not_gated(monkeypatch):
    """A plain navigation carries no header. Gating GET would break every page
    load while preventing nothing -- GET is not state-changing."""
    _hosted(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200


def test_private_operator_mode_is_not_gated():
    """No browser session to ride on and no cross-origin attacker, so the
    check would add a failure mode and remove no risk."""
    with TestClient(app) as client:
        assert client.post("/api/auth/logout").status_code == 204


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Content-Security-Policy", "default-src 'self'"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("X-Frame-Options", "DENY"),
    ],
)
def test_security_headers_are_present(header, expected, monkeypatch):
    _hosted(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert expected in response.headers.get(header, ""), (
        f"{header} missing or wrong: {response.headers.get(header)!r}"
    )


def test_the_csp_has_no_unsafe_inline(monkeypatch):
    """`unsafe-inline` is what makes a CSP decorative. Pinned so a future
    convenience cannot quietly restore it."""
    _hosted(monkeypatch)
    with TestClient(app) as client:
        csp = client.get("/api/health").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, csp


def test_hsts_is_hosted_only(monkeypatch):
    """Sending HSTS from a local HTTP origin pins a developer's browser to
    HTTPS for a host that does not serve it, which is very hard to undo."""
    with TestClient(app) as client:
        assert "Strict-Transport-Security" not in client.get("/api/health").headers
    _hosted(monkeypatch)
    with TestClient(app) as client:
        assert "Strict-Transport-Security" in client.get("/api/health").headers


def test_the_headers_reach_a_refusal_too(monkeypatch):
    """A 403 is a response an attacker's page can see. If the wrapper sat
    inside the CSRF check rather than outside it, refusals would go out bare."""
    _hosted(monkeypatch)
    with TestClient(app) as client:
        response = client.post("/api/auth/logout")
    assert response.status_code == 403
    assert "Content-Security-Policy" in response.headers


class _Recorder:
    """Captures what `set_cookie`/`delete_cookie` were actually asked for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def set_cookie(self, name, value=None, **kw):
        self.calls.append((name, kw))

    def delete_cookie(self, name, **kw):
        self.calls.append((name, kw))


def test_the_session_cookie_is_httponly_and_the_csrf_cookie_is_not(monkeypatch):
    """Both halves are deliberate. JavaScript must not read the session
    cookie -- that is Phase 40's explicit guarantee. It MUST read the CSRF
    cookie, or it cannot send the header that proves same-origin."""
    _hosted(monkeypatch)
    recorder = _Recorder()
    set_session_cookies(recorder, "session-token", "csrf-token")

    flags = dict(recorder.calls)
    assert flags["edge_session"]["httponly"] is True
    assert flags[CSRF_COOKIE]["httponly"] is False
    for name in flags:
        assert flags[name]["secure"] is True, f"{name} not Secure in hosted mode"
        assert flags[name]["samesite"] == "lax"
        assert flags[name]["path"] == "/"


def test_clearing_uses_the_same_flags_it_set(monkeypatch):
    """A browser will not replace a cookie whose attributes do not match, so a
    delete that forgets `path` leaves the cookie in place and sign-out
    silently does nothing."""
    _hosted(monkeypatch)
    setter, clearer = _Recorder(), _Recorder()
    set_session_cookies(setter, "s", "c")
    clear_session_cookies(clearer)

    # `strict=True`, not bare `zip`: a clearer that forgot one of the two
    # cookies would otherwise truncate the loop to the pair it did clear, and
    # this test would report that clearing matches setting while sign-out left
    # a cookie in place. Asserted first as a count, because the strict=
    # failure is a ValueError at the end of the loop rather than a readable
    # message about what went wrong.
    assert len(clearer.calls) == len(setter.calls), (
        f"{len(setter.calls)} cookies set, {len(clearer.calls)} cleared"
    )
    for (name, set_kw), (cleared_name, clear_kw) in zip(
        setter.calls, clearer.calls, strict=True
    ):
        assert name == cleared_name
        assert clear_kw["path"] == set_kw["path"]
        assert clear_kw["httponly"] == set_kw["httponly"]
        assert clear_kw["samesite"] == set_kw["samesite"]


def test_the_session_cookie_is_not_secure_on_a_local_origin():
    """Conditional only because a local HTTP origin drops a Secure cookie
    silently and the whole flow then fails with no visible cause."""
    recorder = _Recorder()
    set_session_cookies(recorder, "s", "c")
    assert all(kw["secure"] is False for _, kw in recorder.calls)
