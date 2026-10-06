"""`schedules` and `outbox` are built, tested, measured -- and uncalled.

The tenant classification records both as scoped by a NULLABLE `tenant_id`
with nothing refusing a tenantless row, which reads as a live hole. Measured,
it is not one yet: **no code under `api/` writes either table.** The modules
are reached only by `snapshot.py`, and only for their two measurement
functions (`oldest_overdue_age`, `undelivered_age`).

That makes the hole an OBLIGATION rather than an exposure, and an obligation
nobody wrote down is one somebody discharges by accident. So this file pins
the uncalled state. It fails the moment a production caller appears, and its
message says what has to come with it.

What has to come with it, stated here so the next person does not have to
derive it: `jobs` has the same nullable column and the worker refuses a
tenantless job before running its handler -- `TenantlessJob`, marked NOT
retryable, because "the tenant will not appear by waiting". A tenantless job
run unbound reads nothing under row-level security and reports success, which
is the failure this exists to prevent. The same argument applies to a schedule
that materialises jobs and to a message a receiver cannot attribute.

Note what is NOT asserted: that a guard exists. Writing one now would mean
inventing policy for a feature with no caller and no requirements -- skip and
retry forever, disable the row, or raise and stop every tenant's scheduler are
three different answers and nothing in the repository chooses between them.
This test makes the choice unavoidable at the moment it becomes answerable.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api"

#: The modules whose writers are being watched, and the names that write.
WRITERS = {
    "outbox": {"emit", "relay"},
    "schedules": {"materialize_due"},
}
#: Direct ORM construction counts as a writer too -- `Schedule(...)` bypasses
#: `materialize_due` entirely, which is how `leagues` acquired five writers an
#: earlier scan could not see.
MODELS = {"Schedule", "OutboxMessage"}
#: ...and so does raw SQL, for the same reason.
RAW_MARKERS = ("insert into schedules", "update schedules", "insert into outbox", "update outbox")

#: The defining modules call their own functions; that is not a production
#: caller. Keyed by path so a rename does not quietly widen the exemption.
SELF = {"api/services/outbox.py", "api/services/schedules.py"}


def _bound_names(tree: ast.AST) -> set[str]:
    """Names bound by `from <watched module> import <name>`.

    Only these count as bare-name writers. Binding the MODULE is not enough,
    and that distinction is not hypothetical: the first version of this scan
    treated any bare `emit()` in a file that imports `outbox` as an outbox
    write, and immediately flagged `snapshot.py`, which imports `outbox` for
    `undelivered_age` and separately imports **observability's** unrelated
    `emit`. A scan that cannot tell two functions of the same name apart
    reports a writer that does not exist -- and a false alarm in a build gate
    is worse than a missed one, because it teaches everyone to override it.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (node.module or "").rsplit(".", 1)[-1]
            if module in WRITERS:
                for alias in node.names:
                    if alias.name in WRITERS[module]:
                        found.add(alias.asname or alias.name)
    return found


def scan_source(source: str, label: str) -> list[str]:
    """Writers to the watched tables in one module's source."""
    tree = ast.parse(source, label)
    watched_names = _bound_names(tree)
    callers: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                owner = getattr(func.value, "id", None)
                if owner in WRITERS and func.attr in WRITERS[owner]:
                    callers.append(f"{label}:{node.lineno} {owner}.{func.attr}()")
                elif func.attr in MODELS:
                    callers.append(f"{label}:{node.lineno} {func.attr}()")
            else:
                name = getattr(func, "id", None)
                if name in watched_names:
                    callers.append(f"{label}:{node.lineno} {name}()")
                elif name in MODELS:
                    callers.append(f"{label}:{node.lineno} {name}()")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            for marker in RAW_MARKERS:
                if marker in low:
                    callers.append(f"{label}:{node.lineno} raw SQL: {marker!r}")
    return callers


def _production_writers() -> list[str]:
    found: list[str] = []
    for path in sorted(API.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in SELF:
            continue
        found.extend(scan_source(path.read_text(encoding="utf-8"), rel))
    return found


def test_nothing_in_the_app_writes_schedules_or_outbox():
    writers = _production_writers()
    assert writers == [], (
        "a production caller now writes `schedules` or `outbox`:\n  "
        + "\n  ".join(writers)
        + "\n\nBoth tables carry a NULLABLE `tenant_id` and nothing refuses a "
        "tenantless row. Before this caller ships, decide what happens to one, "
        "and follow the `jobs` precedent: the worker raises `TenantlessJob` "
        "before running the handler and marks it NOT retryable, because the "
        "tenant will not appear by waiting. Then update the entries in "
        "tests/test_tenant_classification.py, which currently say there is no "
        "production writer."
    )


def test_the_scan_can_see_a_writer_of_every_shape():
    """The instrument check.

    An empty result is the passing state above, so a scan that found nothing
    -- a wrong import name, a node kind not walked -- would look identical to
    the truth. Every shape a writer can take is planted here and has to be
    seen: module-qualified, bare-imported, ORM construction, and raw SQL.
    """
    shapes = {
        "module-qualified": "from api.services import outbox\noutbox.emit(session, topic='t', dedupe_key='k')\n",
        "bare import": "from api.services.outbox import emit\nemit(session, topic='t', dedupe_key='k')\n",
        "relay": "from api.services import outbox\noutbox.relay(session, sink)\n",
        "materialize": "from api.services import schedules\nschedules.materialize_due(session)\n",
        "bare materialize": "from api.services.schedules import materialize_due\nmaterialize_due(session)\n",
        "ORM construction": "from api.models import Schedule\nrow = Schedule(kind='x')\n",
        "ORM via attribute": "import api.models as m\nrow = m.OutboxMessage(topic='x')\n",
        "raw SQL": "session.execute('INSERT INTO outbox (topic) VALUES (1)')\n",
    }
    for label, source in shapes.items():
        assert scan_source(source, label), f"the scan missed a {label} writer"


def test_the_scan_does_not_fire_on_things_that_are_not_writers():
    """The other half. A scan that flagged everything would also pass the test
    above, and would then fail the real one for the wrong reason -- which is
    worse than missing a writer, because it trains everyone to override it."""
    for label, source in {
        "the measurement functions": (
            "from api.services import outbox, schedules\n"
            "a = schedules.oldest_overdue_age(session)\n"
            "b = outbox.undelivered_age(session)\n"
        ),
        "observability's unrelated emit": (
            "from api.services.observability import emit\nemit(sink, 'db_reachable', 0)\n"
        ),
        "reading, not writing": "from api.services import outbox\nrows = outbox.pending(session)\n",
        # The real shape that broke the first version of this scan.
        "a module importing outbox AND observability's emit": (
            "from api.services import outbox, schedules\n"
            "from api.services.observability import emit\n"
            "emit(sink, 'db_reachable', 0)\n"
            "age = outbox.undelivered_age(session)\n"
        ),
    }.items():
        assert scan_source(source, label) == [], f"the scan fired on {label}"


def test_the_self_exemption_names_files_that_exist():
    """A stale exemption is an exemption for nothing, and it would hide the
    first real writer if either module were ever renamed."""
    missing = sorted(rel for rel in SELF if not (ROOT / rel).exists())
    assert missing == [], missing
