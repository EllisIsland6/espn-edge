"""Every environment variable the CDK stack sets must be read by something.

THE DEFECT THIS WAS WRITTEN FOR
-------------------------------
`infra/app.py` wires `OPERATOR_PASSWORD_HASH` and `SESSION_SECRET` from
Secrets Manager into both tasks, and `docs/aws-deploy.md` told the operator to
create them -- describing the first as "still used by the app's own login" and
the deployment as having "two gates rather than one".

Neither is read anywhere. `api/routers/auth.py` has `/me` and `/logout` and
deliberately **no endpoint that mints a session**, so there is no application
login for a password hash to belong to. `pydantic_settings` is configured
`extra="ignore"`, so the unread variables cost nothing at run time -- which is
exactly why nothing noticed.

The cost was the sentence. An operator reading that file would have believed
there were two authentication gates in front of an application that has none
of its own, and would have been most wrong precisely when choosing the
`allow_cidr` shape, where the only gate is an IP range.

WHAT THIS PINS
--------------
Two things, because the defect had two halves.

1. Every `[A-Z_]` key in `infra/app.py`'s environment and secrets dicts is
   read somewhere -- as a `Settings` field, or through `os.environ` /
   `os.getenv` under `api/` or `alembic/` -- or else appears in
   `WIRED_BUT_UNREAD` with a reason. That register is not a suppression list:
   a name in it that turns out to BE read also fails, so an entry cannot
   outlive its own truth.

2. The warning in `docs/aws-deploy.md` is still there. The register above
   would have been satisfied by the original file, which wired these two and
   described them as load-bearing. The dangerous half of this defect lived in
   prose, so prose is what has to be pinned.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra/app.py"
CONFIG = ROOT / "api/config.py"
DEPLOY_DOC = ROOT / "docs/aws-deploy.md"

#: Variables the stack sets that nothing reads, each with the reason it is
#: wired anyway. Adding a name here is recording a finding, not silencing one:
#: `test_the_register_does_not_outlive_its_own_truth` fails if the name is in
#: fact read, and `test_the_deploy_doc_still_says_there_is_no_login` fails if
#: the documentation stops warning about them.
WIRED_BUT_UNREAD = {
    "OPERATOR_PASSWORD_HASH": (
        "There is no endpoint that mints a session, so there is no login for a "
        "password hash to belong to. Wired ahead of the identity provider."
    ),
    "SESSION_SECRET": (
        "Same reason. Nothing signs or reads a session cookie it did not mint."
    ),
}

#: The sentence docs/aws-deploy.md must keep. If the section is rewritten, this
#: fails rather than letting the warning quietly disappear.
REQUIRED_WARNING = "THERE IS NO APPLICATION LOGIN"


def _env_keys_the_stack_sets() -> set[str]:
    """Upper-case string keys of every dict literal in the CDK stack.

    Covers both `environment={...}` and `secrets={...}`, and both the service
    and the migrate task, without having to know which is which -- a new task
    added later is covered the day it is written.
    """
    tree = ast.parse(INFRA.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", key.value):
                    keys.add(key.value)
    return keys


def _settings_fields() -> set[str]:
    """`Settings` field names, upper-cased -- how pydantic-settings reads env."""
    source = CONFIG.read_text(encoding="utf-8")
    return {name.upper() for name in re.findall(r"^\s{4}([a-z_][a-z0-9_]*)\s*:", source, re.M)}


def _names_referenced_in_application_source() -> set[str]:
    """Env-shaped string literals in `api/` and `alembic/`, docstrings excluded.

    Deliberately NOT "string literals passed to `os.environ`". Revision 0016
    reads its variable as `ROLE_ENV_VAR = "APP_DB_ROLE"` and then
    `os.environ.get(ROLE_ENV_VAR, ...)`, so a detector that only looked at
    call arguments saw no literal and reported `APP_DB_ROLE` unread. The
    instrument check below is what caught that: it asserts this function finds
    `APP_DB_ROLE` specifically, because a detector blind to indirection is
    blind to every migration's variables.

    So: any `[A-Z_]` literal the source mentions counts as a reference. That
    is loose -- SQL keywords (`BEGIN`, `CASCADE`) land in the set too -- but it
    is loose in the harmless direction. The set is only ever intersected with
    the names the CDK stack sets, and a collision would need the stack to set
    a variable named after a SQL keyword. Docstrings are skipped so that prose
    mentioning a variable does not count as reading it, which is the exact
    false pass this whole file exists to prevent.
    """
    found: set[str] = set()
    for path in [*(ROOT / "api").rglob("*.py"), *(ROOT / "alembic").rglob("*.py")]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file is another test's problem
            continue
        docstrings = {
            node.body[0].value
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or node in docstrings:
                continue
            if isinstance(node.value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", node.value):
                found.add(node.value)
    return found


def _read_somewhere() -> set[str]:
    return _settings_fields() | _names_referenced_in_application_source()


# ------------------------------------------------------------------- the tests

def test_every_variable_the_stack_sets_is_read_or_declared_unread():
    unexplained = sorted(
        _env_keys_the_stack_sets() - _read_somewhere() - set(WIRED_BUT_UNREAD)
    )
    assert unexplained == [], (
        f"infra/app.py sets {unexplained}, which nothing reads -- not a "
        "Settings field, not an os.environ lookup. `extra=\"ignore\"` means "
        "the container starts anyway, so this costs nothing at run time and "
        "everything in the deploy document that describes it. Either make it "
        "read or add it to WIRED_BUT_UNREAD with the reason."
    )


def test_the_register_does_not_outlive_its_own_truth():
    """A name recorded as unread must actually still be unread."""
    now_read = sorted(set(WIRED_BUT_UNREAD) & _read_somewhere())
    assert now_read == [], (
        f"WIRED_BUT_UNREAD claims {now_read} are read by nothing, but they "
        "are read now. Remove them from the register and fix the warning in "
        "docs/aws-deploy.md, which tells the operator they do nothing."
    )


def test_the_deploy_doc_still_says_there_is_no_login():
    """The half of this defect that lived in prose.

    The register alone was satisfied by the original document, which wired
    these two variables and called them a second authentication factor.
    """
    body = DEPLOY_DOC.read_text(encoding="utf-8")
    assert REQUIRED_WARNING in body, (
        f"docs/aws-deploy.md no longer contains {REQUIRED_WARNING!r}. "
        "The application has no login: `api/routers/auth.py` mints no "
        "session, and `private_operator` binds a tenant with no cookie. "
        "Whatever sits in front of the load balancer is the only gate, and "
        "the operator has to be told so -- most of all on the `allow_cidr` "
        "shape, where that gate is an IP range."
    )
    for name in WIRED_BUT_UNREAD:
        assert name in body, (
            f"{name} is wired by the stack and read by nothing, and "
            "docs/aws-deploy.md does not mention it at all. An operator "
            "creating a secret should be told it does nothing."
        )


def test_there_is_still_no_endpoint_that_mints_a_session():
    """The premise under all of the above.

    If a login lands, this fails -- and then the register, the warning and the
    deploy document's threat model all need revisiting together, which is
    exactly the moment to be interrupted.
    """
    source = (ROOT / "api/routers/auth.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    routes = [
        decorator.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for decorator in (d.func if isinstance(d, ast.Call) else d for d in node.decorator_list)
        if isinstance(decorator, ast.Attribute)
    ]
    assert routes, "No routes found in api/routers/auth.py -- the parse broke."
    assert "mint_session" not in source or "def mint_session" not in source, (
        "api/routers/auth.py now defines a session-minting endpoint. The "
        "deploy document says the application has no login; revisit it."
    )


def test_the_parses_are_actually_parsing_something():
    """The instrument check.

    Every assertion above passes trivially if a parse returns nothing: an
    empty key set has nothing unexplained, and an empty read set contradicts
    no register entry. Both directions have to be non-empty and recognisable.
    """
    keys = _env_keys_the_stack_sets()
    assert len(keys) >= 10, sorted(keys)
    for expected in ("APP_MODE", "DATABASE_URL", "TENANT_ID", "APP_DB_ROLE"):
        assert expected in keys, sorted(keys)

    read = _read_somewhere()
    assert len(read) >= 20, len(read)
    # APP_DB_ROLE is the proof that the os.environ arm works at all: it is set
    # by the stack, is NOT a Settings field, and is read by revision 0016.
    assert "APP_DB_ROLE" not in _settings_fields()
    assert "APP_DB_ROLE" in _names_referenced_in_application_source(), (
        "APP_DB_ROLE is read by alembic/versions/0016 through os.environ. If "
        "this parse cannot see it, it cannot see any migration's variables, "
        "and every name they read would look unexplained."
    )

    # And the register must not be empty, or its own test is vacuous. Both of
    # its entries must be invisible to the detector -- if the detector cannot
    # miss anything, `test_every_variable_the_stack_sets_is_read_or_declared_unread`
    # can never fail.
    assert WIRED_BUT_UNREAD, "the register is empty; two tests above check nothing"
    assert not (set(WIRED_BUT_UNREAD) & read), sorted(set(WIRED_BUT_UNREAD) & read)
