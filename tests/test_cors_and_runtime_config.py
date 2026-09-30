"""CORS, and the runtime config the SPA reads instead of baking one in.

Neither is exciting. Both are pinned because the failure mode is a one-line
change that nothing else would catch: `allow_origins=["*"]` together with
`allow_credentials=True` hands any page an authenticated read of this API, and
a secret added to the runtime config is public the moment it ships.
"""

from __future__ import annotations

from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from api.config import get_settings
from api.main import app


def _cors_options() -> dict:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return dict(middleware.kwargs)
    raise AssertionError("CORSMiddleware is not installed; this file proves nothing")


def test_no_wildcard_origin():
    """Phase 40's explicit guarantee. A wildcard is only safe while
    credentials are off, and the two settings live far enough apart in a
    config block that changing one without the other is easy."""
    origins = _cors_options().get("allow_origins", [])
    assert origins, "allow_origins is empty; CORS is not configured as expected"
    assert "*" not in origins, f"wildcard CORS origin: {origins}"


def test_credentials_are_not_allowed_cross_origin():
    """The SPA and the API share an origin, so the session cookie never needs
    to travel cross-origin. Turning this on would be the change that makes a
    wildcard dangerous."""
    assert _cors_options().get("allow_credentials", False) is False


def test_runtime_config_is_served_and_relative():
    """An empty `api_base` is the answer, not a gap: a relative base is what
    lets one built artifact be promoted between environments, which is the
    dependency the phase forbids."""
    with TestClient(app) as client:
        response = client.get("/config.json")
    assert response.status_code == 200
    body = response.json()
    assert body["api_base"] == ""
    assert body["mode"] == get_settings().app_mode
    assert body["season"] == get_settings().season


def test_runtime_config_carries_nothing_private():
    """Read before anyone logs in, so everything in it is public. Checked
    against the settings object rather than against a list of names I
    remembered, so a newly added secret is covered the day it appears."""
    with TestClient(app) as client:
        body = response_json = client.get("/config.json").json()

    settings = get_settings()
    sensitive = {
        name
        for name in type(settings).model_fields
        if any(
            hint in name
            for hint in ("key", "secret", "password", "token", "dsn", "url", "path", "cron")
        )
    }
    assert sensitive, "no sensitive setting names matched; this check is vacuous"

    leaked = sorted(set(body) & sensitive)
    assert not leaked, f"runtime config exposes settings: {leaked}"

    serialized = str(response_json)
    for name in sensitive:
        value = getattr(settings, name, None)
        if isinstance(value, str) and len(value) > 8:
            assert value not in serialized, f"the value of {name} appears in /config.json"
