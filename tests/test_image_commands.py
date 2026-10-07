"""Every command the deployment asks the image to run must exist in the image.

THE DEFECT THIS WAS WRITTEN FOR
-------------------------------
`Dockerfile` installed the project with a bare `pip install .`, which installs
`[project] dependencies` and nothing else. `infra/app.py` defines the one-off
schema task as `command=["alembic", "upgrade", "head"]` -- and alembic is not
a `[project] dependency`, deliberately, because nothing under `api/` imports
it.

So the image built, the service ran, the health check passed, and the migrate
task exited `executable file not found in $PATH`. The schema would never have
been created, and every signal pointing at the image was green.

That is the same shape as the forty-odd others on record: a check that is
green while establishing something other than what it claims. The image was
verified as *an image*. Nothing verified it against the commands something
else had been told to run with it.

WHAT THIS PINS
--------------
Three files have to agree and none of them imports the others:

    infra/app.py   says WHAT to run         (`command=[...]`)
    Dockerfile     says WHAT IS INSTALLED   (`RUN pip install ".[...]"`)
    pyproject.toml says WHAT THAT MEANS     (dependencies + extras)

The argv[0] of every such command is resolved to the distribution that
provides it as a console script, and that distribution must be covered by the
Dockerfile's install. Resolution comes from the environment running the suite
when possible, so the mapping maintains itself; a name that resolves nowhere
FAILS rather than skipping, because a mapping that silently returned nothing
would pass this file while checking nothing.
"""
from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
DOCKERFILE = ROOT / "Dockerfile"
INFRA = ROOT / "infra/app.py"

#: Fallback for console scripts whose provider cannot be resolved from the
#: environment running the suite (a minimal env, or a script this project does
#: not itself install). Deliberately tiny: an unresolved name must fail, not
#: be guessed at, so anything added here is a decision someone made on
#: purpose.
KNOWN_PROVIDERS = {
    "alembic": "alembic",
    "uvicorn": "uvicorn",
}


def _canonical(name: str) -> str:
    """PEP 503 canonical distribution name."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _requirement_name(requirement: str) -> str:
    head = re.split(r"[<>=!~\[;\s]", requirement.strip(), maxsplit=1)[0]
    return _canonical(head)


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


# --------------------------------------------------------------- the commands

def _infra_commands() -> list[list[str]]:
    """Every `command=[...]` literal in the CDK stack."""
    tree = ast.parse(INFRA.read_text(encoding="utf-8"))
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "command" or not isinstance(kw.value, ast.List):
                continue
            parts = [
                element.value
                for element in kw.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if len(parts) == len(kw.value.elts) and parts:
                found.append(parts)
    return found


def _dockerfile_cmd() -> list[str]:
    """The image's own exec-form `CMD`, which is what the service runs."""
    body = DOCKERFILE.read_text(encoding="utf-8")
    body = re.sub(r"\\\s*\n", " ", body)          # join line continuations
    body = re.sub(r"^\s*#.*$", "", body, flags=re.MULTILINE)   # drop comments
    match = re.search(r"^\s*CMD\s+(\[.*?\])", body, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, (
        "No exec-form CMD found in Dockerfile. A shell-form CMD would also "
        "mean the stop signal reaches a shell instead of the server, so this "
        "is worth failing on."
    )
    return json.loads(match.group(1))


# ------------------------------------------------------- what is installed

def _installed_extras() -> set[str]:
    """Extras named in the Dockerfile's `pip install` of the project."""
    body = DOCKERFILE.read_text(encoding="utf-8")
    body = re.sub(r"^\s*#.*$", "", body, flags=re.MULTILINE)
    extras: set[str] = set()
    installs_project = False
    for line in body.splitlines():
        if "pip install" not in line:
            continue
        # `.`, `".[a,b]"`, `'.[a]'`, `-e .[a]` -- the project, with extras.
        for match in re.finditer(r"""["']?\.(\[([^\]]*)\])?["']?(?=\s|$)""", line):
            installs_project = True
            if match.group(2):
                extras |= {part.strip() for part in match.group(2).split(",") if part.strip()}
    assert installs_project, (
        "Dockerfile does not appear to `pip install` the project at all. "
        "Either it regressed to a hand-copied dependency list (see "
        "tests/test_container_dependencies.py for why that is a defect "
        "generator) or this parse broke."
    )
    return extras


def _covered_distributions() -> set[str]:
    """Distributions the image's install step brings in, directly."""
    data = _pyproject()
    covered = {_requirement_name(item) for item in data["project"]["dependencies"]}
    optional = data["project"].get("optional-dependencies", {})
    for extra in _installed_extras():
        assert extra in optional, (
            f"Dockerfile installs the extra {extra!r}, which pyproject.toml "
            f"does not declare. Declared: {sorted(optional)}"
        )
        covered |= {_requirement_name(item) for item in optional[extra]}
    return covered


def _provider_of(script: str) -> str | None:
    """Which distribution provides `script` as a console script."""
    from importlib.metadata import distributions

    for dist in distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        for entry in dist.entry_points:
            if entry.group == "console_scripts" and entry.name == script:
                return _canonical(name)
    fallback = KNOWN_PROVIDERS.get(script)
    return _canonical(fallback) if fallback else None


def _uncovered(argv: list[str]) -> str | None:
    """The reason `argv` cannot run in the image, or None."""
    script = argv[0]
    provider = _provider_of(script)
    if provider is None:
        return (
            f"{script!r} resolves to no distribution. Either it is a typo, or "
            f"it is provided by something not installed here -- add it to "
            f"KNOWN_PROVIDERS deliberately rather than letting this pass."
        )
    if provider not in _covered_distributions():
        return (
            f"{script!r} is provided by {provider!r}, which the image's "
            f"install does not cover. Covered: {sorted(_covered_distributions())}"
        )
    return None


# ------------------------------------------------------------------- the tests

def test_every_infra_command_can_run_in_the_image():
    problems = {
        " ".join(argv): _uncovered(argv)
        for argv in _infra_commands()
        if _uncovered(argv) is not None
    }
    assert problems == {}, (
        "infra/app.py tells the image to run a command the image cannot run. "
        "The service would deploy green and this task would exit "
        f"`executable file not found in $PATH`: {problems}"
    )


def test_the_images_own_cmd_can_run_in_the_image():
    argv = _dockerfile_cmd()
    reason = _uncovered(argv)
    assert reason is None, f"The image's CMD cannot run in the image: {reason}"


def test_the_migrate_command_is_what_makes_the_extra_load_bearing():
    """Why the extra exists, stated as a test rather than a comment.

    If alembic ever moves into `[project] dependencies`, this fails and the
    Dockerfile's extra becomes removable. If the migrate task stops using
    alembic, likewise. Either is a real change worth noticing.
    """
    data = _pyproject()
    runtime = {_requirement_name(item) for item in data["project"]["dependencies"]}
    assert "alembic" not in runtime, (
        "alembic is now a runtime dependency, so `Dockerfile`'s `[migrate]` "
        "extra and this file's premise are both obsolete."
    )
    assert "migrate" in _installed_extras(), (
        "Dockerfile no longer installs the `[migrate]` extra, so the one-off "
        "`alembic upgrade head` task has no alembic."
    )
    migrate = {_requirement_name(item) for item in
               data["project"]["optional-dependencies"]["migrate"]}
    assert migrate == {"alembic"}, sorted(migrate)


def test_the_migrate_and_dev_extras_do_not_drift():
    """alembic is spelled in two extras. Pin them together."""
    optional = _pyproject()["project"]["optional-dependencies"]
    in_migrate = [item for item in optional["migrate"]
                  if _requirement_name(item) == "alembic"]
    in_dev = [item for item in optional["dev"]
              if _requirement_name(item) == "alembic"]
    assert in_migrate == in_dev != [], (
        f"`migrate` asks for {in_migrate} and `dev` for {in_dev}. A developer "
        "and the image would get different alembic floors."
    )


def test_the_parses_are_actually_parsing_something():
    """The instrument check.

    Every assertion above is satisfied by an empty command list or an
    everything-covered set. A regex that matched nothing, or an ast walk that
    found nothing, would pass this whole file and establish nothing -- which
    is the exact failure the file exists to catch, one level up.
    """
    commands = _infra_commands()
    assert commands, "No `command=[...]` found in infra/app.py -- parse broke."
    assert ["alembic", "upgrade", "head"] in commands, commands

    cmd = _dockerfile_cmd()
    assert cmd and cmd[0] == "uvicorn", cmd

    covered = _covered_distributions()
    assert len(covered) >= 10, sorted(covered)
    assert "alembic" in covered, sorted(covered)

    # The detector must be able to say no. If it cannot, a covered-everything
    # bug would make every test above vacuous.
    assert _uncovered(["definitely-not-a-real-console-script"]) is not None
    assert _provider_of("definitely-not-a-real-console-script") is None
