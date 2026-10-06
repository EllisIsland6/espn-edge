"""`web/Dockerfile` is a development image. Pin the two facts that matter.

Two things about it are worth asserting rather than remembering:

  * it installs with `npm ci` against a lockfile that is **not** optional, so
    the image cannot quietly resolve different versions from the ones CI
    tested; and
  * its `CMD` runs the Vite **dev server**, which must not face the internet,
    and the file says so where someone deploying it would look.

Nothing here claims the image builds. Neither environment this repository has
been built in has docker, so that is not a claim this suite can make -- and
`test_the_dev_server_cmd_is_labelled_as_such` exists precisely so the gap
between "a development image" and "a deployable one" stays written down
instead of being discovered by deploying it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "web/Dockerfile"
LOCKFILE = ROOT / "web/package-lock.json"


def _body() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_the_image_installs_from_the_lockfile():
    body = _body()
    assert re.search(r"^RUN\s+npm ci\b", body, re.MULTILINE), (
        "web/Dockerfile must install with `npm ci`. `npm install` resolves "
        "against the registry and may pick versions the lockfile does not "
        "name, so the image can differ from what CI tested while both report "
        "success."
    )
    assert not re.search(r"^RUN\s+npm install\b", body, re.MULTILINE), body


def test_the_lockfile_copy_is_not_optional():
    """`COPY package-lock.json* ./` was the original, and the glob is the
    defect: `npm ci` needs the lockfile, and a glob that matches nothing
    succeeds silently, so the build would fail later with a confusing error
    instead of at the COPY."""
    assert "package-lock.json*" not in _body(), (
        "the lockfile COPY is globbed, so a missing lockfile is not an error "
        "at the point it goes missing"
    )
    assert re.search(r"^COPY\s+package\.json\s+package-lock\.json\s", _body(), re.MULTILINE)


def test_the_lockfile_exists_and_is_a_real_lockfile():
    """The instrument check for the two tests above: they are assertions about
    a file that has to be there, and a missing one would make `npm ci` fail in
    the image while both of those tests still pass."""
    assert LOCKFILE.exists(), LOCKFILE
    data = json.loads(LOCKFILE.read_text(encoding="utf-8"))
    assert data.get("lockfileVersion", 0) >= 2, data.get("lockfileVersion")
    assert data.get("packages"), "a lockfile with no packages locks nothing"


def test_the_dev_server_cmd_is_labelled_as_such():
    """The CMD serves the Vite dev server. That is fine for development and
    not fine on the internet, so the file has to say so -- and if someone
    changes the CMD to serve a build, this test is what tells them to delete
    the warning with it."""
    body = _body()
    runs_dev_server = re.search(r'CMD\s*\[.*"dev".*\]', body) is not None
    warned = "must not face the internet" in body
    assert runs_dev_server == warned, (
        "the CMD and the warning disagree: runs_dev_server="
        f"{runs_dev_server}, warning present={warned}. Either the image still "
        "serves the dev server and must say so, or it no longer does and the "
        "warning is stale."
    )
