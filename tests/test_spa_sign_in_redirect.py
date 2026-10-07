"""The SPA bounces to the front door on exactly one 401, and the string that
identifies it lives in two codebases.

`api/db.py` raises `401 "authentication required"` when hosted mode has no
session. `web/src/api.ts` navigates to `/api/auth/login` when it sees that
status with that detail -- and only that detail, because a league route's
`401 "ESPN session expired; re-authenticate this account"` must not send a
private-operator user to a login route that is 404 there. Two literals in two
languages, pinned here so they cannot drift apart silently.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "api/db.py"
API_TS = ROOT / "web/src/api.ts"
AUTH_ROUTER = ROOT / "api/routers/auth.py"


def _backend_detail() -> str:
    match = re.search(r'status_code=401,\s*detail="([^"]+)"', DB.read_text(encoding="utf-8"))
    assert match, "api/db.py no longer raises the hosted-mode 401 with a literal detail"
    return match.group(1)


def _spa_constant(name: str) -> str:
    match = re.search(rf'const {name} = "([^"]+)";', API_TS.read_text(encoding="utf-8"))
    assert match, f"web/src/api.ts no longer defines {name}"
    return match.group(1)


def test_the_spa_recognises_the_backends_sign_in_401():
    assert _spa_constant("SIGN_IN_REQUIRED") == _backend_detail()


def test_the_spa_navigates_to_a_login_route_that_exists():
    path = _spa_constant("SIGN_IN_PATH")
    router = AUTH_ROUTER.read_text(encoding="utf-8")
    assert 'prefix="/api/auth"' in router
    assert path.startswith("/api/auth/")
    assert f'@router.get("{path.removeprefix("/api/auth")}"' in router, path


def test_the_redirect_is_gated_on_the_detail_not_just_the_status():
    body = API_TS.read_text(encoding="utf-8")
    assert re.search(r"res\.status === 401 && detail === SIGN_IN_REQUIRED", body), (
        "the SPA must compare the detail: a bare `status === 401` would also "
        "fire on an expired ESPN cookie in private-operator mode"
    )
