"""`api/Dockerfile` hand-copies the runtime dependency list. Pin the agreement.

The Dockerfile copies `pyproject.toml` "for layer caching" and then
`pip install`s a **duplicate** of its `[project] dependencies`, written out by
hand. So the image's dependency set and the project's can drift, and the
failure mode is an `ImportError` in a deployed container for a package the
suite has had all along -- which no test here would see, because the suite
installs from `pyproject.toml`.

Compared name by name, the two agree today. This test is what notices when
they stop. It does not compare VERSION specifiers: the Dockerfile may
legitimately pin tighter than the project's floor, and asserting equality
there would fail on a deliberate pin and teach everyone to ignore it.

The honest fix is for the Dockerfile to install the project rather than a copy
of its list. That cannot be verified from here -- there is no docker in either
environment this was written in -- so this pins the invariant instead of
changing the thing it is an invariant about.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "api/Dockerfile"

#: Quoted tokens in the Dockerfile that are not package requirements.
NOT_PACKAGES = frozenset({"--port", "0.0.0.0", "8000", "api.main:app", "--host"})


def _name(requirement: str) -> str:
    """The distribution name, without extras or version specifier."""
    head = re.split(r"[<>=!~\[;\s]", requirement.strip(), maxsplit=1)[0]
    return head.lower().replace("_", "-")


def _project_dependencies() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {_name(item) for item in data["project"]["dependencies"]}


def _image_dependencies() -> set[str]:
    body = DOCKERFILE.read_text(encoding="utf-8")
    quoted = re.findall(r'"([^"]+)"', body)
    return {_name(item) for item in quoted if _name(item) not in NOT_PACKAGES}


def test_the_image_installs_every_runtime_dependency():
    missing = sorted(_project_dependencies() - _image_dependencies())
    assert missing == [], (
        f"api/Dockerfile does not install {missing}, which `pyproject.toml` "
        "lists as a runtime dependency. The container would raise ImportError "
        "on a package this suite has, because the suite installs from "
        "pyproject.toml and the image installs a hand-copied list."
    )


def test_the_image_installs_nothing_the_project_does_not_declare():
    """The other direction. An extra package in the image is a dependency the
    project does not declare, so nothing pins its version and no other
    environment has it."""
    extra = sorted(_image_dependencies() - _project_dependencies())
    assert extra == [], (
        f"api/Dockerfile installs {extra}, which `pyproject.toml` does not "
        "declare. Either declare it or stop installing it."
    )


def test_the_comparison_is_actually_comparing_something():
    """The instrument check.

    Both tests above are satisfied by two empty sets -- a regex that matched
    nothing would pass them and establish nothing. So: both sides must be
    non-empty, and they must be the size the lists actually are.
    """
    project, image = _project_dependencies(), _image_dependencies()
    assert len(project) >= 10, sorted(project)
    assert len(image) >= 10, sorted(image)
    assert project == image, sorted(project ^ image)


def test_the_dockerfile_still_installs_by_hand_rather_than_the_project():
    """If someone fixes the Dockerfile properly, this test should go.

    It exists so the fix is noticed rather than leaving three tests pinning an
    invariant about a duplication that no longer exists. `pip install .` or
    `pip install -e .` makes the whole file redundant.
    """
    body = DOCKERFILE.read_text(encoding="utf-8")
    installs_project = re.search(r"pip install[^\n]*\s\.(\s|$)", body) is not None
    assert not installs_project, (
        "api/Dockerfile now installs the project itself, which is the right "
        "fix -- so this file's premise is gone. Delete it."
    )
