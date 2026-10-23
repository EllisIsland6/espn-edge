"""The SPA echoes the CSRF cookie on every unsafe request, and the two names
live in two codebases.

`api/security.py` sets `edge_csrf` (readable by the page) and, in hosted mode,
refuses any POST/PUT/PATCH/DELETE whose `X-CSRF-Token` header does not match
it. The first hosted POST ever made -- adding an account on the live site --
came back `403 csrf check failed`: the backend half of Phase 40 was proven by
test, and the page half did not exist. These pins keep the two halves naming
the same cookie and the same header, and keep every unsafe `fetch` in the
client sending it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURITY = ROOT / "api/security.py"
API_TS = ROOT / "web/src/api.ts"


def _backend_constant(name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"', SECURITY.read_text(encoding="utf-8"), re.M)
    assert match, f"api/security.py no longer defines {name}"
    return match.group(1)


def _spa_constant(name: str) -> str:
    match = re.search(rf'const {name} = "([^"]+)";', API_TS.read_text(encoding="utf-8"))
    assert match, f"web/src/api.ts no longer defines {name}"
    return match.group(1)


def test_the_spa_reads_the_cookie_the_backend_sets():
    assert _spa_constant("CSRF_COOKIE") == _backend_constant("CSRF_COOKIE")


def test_the_spa_sends_the_header_the_backend_checks():
    assert _spa_constant("CSRF_HEADER") == _backend_constant("CSRF_HEADER")


def test_the_backend_check_is_the_premise():
    body = SECURITY.read_text(encoding="utf-8")
    assert "request.method in UNSAFE_METHODS" in body
    assert 'content={"detail": "csrf check failed"}' in body


def test_every_unsafe_fetch_in_the_client_carries_the_csrf_header():
    body = API_TS.read_text(encoding="utf-8")
    # Every `fetch(` whose options name a method is an unsafe call; each must
    # spread csrfHeaders() into its headers. `get` has no options object.
    calls = re.findall(r"fetch\([^;]*?\{\s*method[\s\S]*?\}\);", body)
    assert len(calls) >= 2, "expected the generic send() and the backup POST"
    for call in calls:
        assert "csrfHeaders()" in call, call


MANAGE_TSX = ROOT / "web/src/pages/Manage.tsx"
HEALTH_ROUTER = ROOT / "api/routers/health.py"


COPY_TS = ROOT / "web/src/copy.ts"


def test_the_manage_page_offers_no_credential_form_on_a_synthetic_instance():
    """The operator pasted a real ESPN session into the live site's form: the
    chokepoint refused it, as designed, and the form should never have been
    there. The page gates the form on the mode the health route reports --
    an allow-list of the two modes that hold credentials, so an unknown mode
    (health not yet answered, or answered without one) shows no form."""
    manage = MANAGE_TSX.read_text(encoding="utf-8")
    assert "{holdsCredentials(mode) && <AddAccountForm" in manage, (
        "the form must be gated on a KNOWN credentialed mode, not on the absence of a flag"
    )
    copy = COPY_TS.read_text(encoding="utf-8")
    assert 'return mode === "private_operator" || mode === "private_invited";' in copy
    assert "public_synthetic" not in copy.split("export function holdsCredentials")[1]
    assert "mode=settings.app_mode" in HEALTH_ROUTER.read_text(encoding="utf-8")
