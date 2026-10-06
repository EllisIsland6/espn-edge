"""Revision 0015 — `UNIQUE (tenant_id, swid)` on `accounts`.

The decision was "swid should be unique". The SHAPE of that is what these tests
pin, because the obvious reading of it is the wrong one:

  * a **global** `UNIQUE (swid)` on a tenant-scoped table re-opens the
    enumeration oracle 0012 closed for `raw_cache` and 0013 removed from
    `leagues` -- a colliding INSERT raises `23505`, which row-level security
    does not hide, so one tenant learns another holds that credential;
  * and the restore path substituted ONE constant placeholder into every
    account's swid, so the constraint would have made a restore refuse for any
    tenant holding two accounts. Measured before this was written.

Both halves are asserted here by inserting rows, not by reading a constraint
inventory: an inventory can look right while the database refuses the row.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa

from api.crypto import encrypt
from api.models import Account, Tenant
from api.services.recovery import is_reauth_placeholder, reauth_swid_for
from api.tenancy import current_tenant_id

ROOT = Path(__file__).resolve().parents[1]
SHARED_SWID = "{COLLIDE-1111}"


def _account(tenant_id: int, label: str, swid: str = SHARED_SWID) -> Account:
    return Account(
        tenant_id=tenant_id,
        label=label,
        swid=swid,
        espn_s2_encrypted=encrypt(f"S2-{label}"),
        status="active",
    )


# --------------------------------------------------------------- the two sides


def test_two_tenants_can_hold_the_same_espn_credential(db_session):
    """The half a global unique would have broken.

    Two people can share an ESPN login, and two tenants of this system can each
    hold it. Asserted by inserting both rows.
    """
    first = db_session.query(Tenant).one()
    second = Tenant(slug="beta")
    db_session.add(second)
    db_session.flush()

    db_session.add(_account(first.id, "first"))
    db_session.add(_account(second.id, "second"))
    db_session.flush()

    held = sorted(
        row.tenant_id
        for row in db_session.query(Account).filter(Account.swid == SHARED_SWID)
    )
    assert held == sorted([first.id, second.id])


def test_one_tenant_cannot_hold_the_same_credential_twice(db_session):
    """The half the constraint exists for."""
    tenant_id = current_tenant_id(db_session)
    db_session.add(_account(tenant_id, "first"))
    db_session.flush()
    db_session.add(_account(tenant_id, "duplicate"))
    with pytest.raises(sa.exc.IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_tenant_may_still_hold_several_distinct_credentials(db_session):
    """The constraint must not become "one account per tenant".

    Stated because that is the failure mode of getting the constraint wrong in
    the other direction, and because the restore test below depends on it.
    """
    tenant_id = current_tenant_id(db_session)
    db_session.add(_account(tenant_id, "first", "{AAAA-1111}"))
    db_session.add(_account(tenant_id, "second", "{BBBB-2222}"))
    db_session.flush()
    assert (
        db_session.query(Account).filter(Account.tenant_id == tenant_id).count() == 2
    )


# ------------------------------------------------------- the placeholder itself


def test_the_restore_placeholder_is_per_row_distinct():
    """The property that lets the constraint and the restore coexist."""
    assert reauth_swid_for(1) != reauth_swid_for(2)
    assert len({reauth_swid_for(n) for n in range(50)}) == 50


def test_every_placeholder_form_is_recognised_and_nothing_else_is():
    """`is_reauth_placeholder` is matched by pattern, not by prefix.

    A prefix test would accept `{REAUTH-REQUIREDX}`, and a real swid that
    happened to start the same way would be read as a placeholder -- i.e. a
    credential mistaken for an absence of one.
    """
    assert is_reauth_placeholder("{REAUTH-REQUIRED}")  # the format-v2 constant
    assert is_reauth_placeholder(reauth_swid_for(7))
    for value in (
        "{REAUTH-REQUIREDX}",
        "{REAUTH-REQUIRED-}",
        "{REAUTH-REQUIRED-7X}",
        "REAUTH-REQUIRED-7",
        "{AAAA-1111}",
        "",
        None,
        7,
    ):
        assert not is_reauth_placeholder(value), value


def test_the_placeholder_survives_swid_normalisation_unchanged():
    """Nothing re-normalises it today, but the form was chosen so that if
    anything ever does, it is idempotent rather than silently rewritten."""
    from api.parse_helpers import normalize_swid_braced

    for value in ("{REAUTH-REQUIRED}", reauth_swid_for(42)):
        assert normalize_swid_braced(value) == value


# ------------------------------------------------------------- the migration


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run alembic against a throwaway database, as the operator would.

    Deliberately NOT via `.venv/phase36/probe.sh`, which is the local helper
    the rest of this work used. That file is gitignored, so these three tests
    would have skipped in CI and on every machine but one -- and they are the
    tests that prove the migration refuses a database it cannot migrate. A
    test that runs in one place has been measured in one place.

    The environment mirrors `test_recovery_format.py::_build_migrated`: the
    settings are required and frozen, and `Settings` reads `ROOT/".env"` by
    absolute path, so these have to be supplied explicitly rather than
    inherited.
    """
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": f"sqlite:///{db_path}",
            "APP_MODE": "private_operator",
            "SEASON": "2026",
            "ANTHROPIC_API_KEY": "",
            "RECOVERY_REQUIRED": "false",
            "TELEMETRY_ENABLED": "false",
            "TELEMETRY_REPORT_PATH": f"{db_path}.report.md",
            "ESPN_API_HOST": "https://127.0.0.1:9",
        }
    )
    env.setdefault(
        "FERNET_KEY",
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from cryptography.fernet import Fernet;"
                "print(Fernet.generate_key().decode())",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


@pytest.fixture
def scratch_db():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    path = Path(handle.name)
    path.unlink()
    yield path


def test_the_constraint_is_named_in_the_migrated_schema(scratch_db):
    """Named, and asserted from the real migration chain.

    0012 learned what an unnamed constraint costs: hand-written DDL that
    wrapped `CONSTRAINT ... FOREIGN KEY` onto two lines made the key reflect as
    unnamed, and the failure surfaced in a different migration four revisions
    further down as `No such constraint`.
    """
    assert _alembic(scratch_db, "upgrade", "head").returncode == 0
    connection = sqlite3.connect(scratch_db)
    try:
        (sql,) = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='accounts'"
        ).fetchone()
    finally:
        connection.close()
    assert "CONSTRAINT uq_account_tenant_swid UNIQUE (tenant_id, swid)" in sql
    # And the rebuild did not cost the FK its name on the way through.
    assert "CONSTRAINT fk_accounts_tenant_id FOREIGN KEY" in sql


def test_the_upgrade_refuses_a_database_that_already_holds_duplicates(scratch_db):
    """The rollback window, and what an operator is told.

    Nothing prevented duplicate swids before 0015, so a real database may hold
    them. The refusal has to name the rows WITHOUT naming the credential: a
    migration that prints a swid into a terminal, a log or a CI transcript has
    leaked one. Asserted both ways -- the message identifies the tenant and the
    account ids, and the swid does not appear in the output at all.
    """
    assert _alembic(scratch_db, "upgrade", "0014").returncode == 0
    connection = sqlite3.connect(scratch_db)
    try:
        connection.execute(
            "INSERT INTO tenants (slug, created_at) VALUES ('dupe', datetime('now'))"
        )
        tenant_id = connection.execute(
            "SELECT id FROM tenants WHERE slug='dupe'"
        ).fetchone()[0]
        for label in ("a", "b"):
            connection.execute(
                "INSERT INTO accounts"
                " (label, swid, espn_s2_encrypted, status, created_at, tenant_id)"
                " VALUES (?, '{DUPE-9999}', 'x', 'active', datetime('now'), ?)",
                (label, tenant_id),
            )
        connection.commit()
    finally:
        connection.close()

    result = _alembic(scratch_db, "upgrade", "head")
    output = result.stdout + result.stderr
    assert result.returncode != 0, "the upgrade must refuse, not succeed"
    assert "already hold a duplicate swid" in output
    assert f"tenant {tenant_id}" in output
    assert "uq_account_tenant_swid" in output
    assert "{DUPE-9999}" not in output, "the swid must not be printed"
    assert "DUPE-9999" not in output, "nor any part of it"


def test_the_downgrade_removes_the_constraint_and_keeps_the_rows(scratch_db):
    assert _alembic(scratch_db, "upgrade", "head").returncode == 0
    connection = sqlite3.connect(scratch_db)
    try:
        connection.execute(
            "INSERT INTO tenants (slug, created_at) VALUES ('down', datetime('now'))"
        )
        tenant_id = connection.execute(
            "SELECT id FROM tenants WHERE slug='down'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO accounts"
            " (label, swid, espn_s2_encrypted, status, created_at, tenant_id)"
            " VALUES ('keep', '{KEEP-1}', 'x', 'active', datetime('now'), ?)",
            (tenant_id,),
        )
        connection.commit()
        before = connection.execute("SELECT count(*) FROM accounts").fetchone()[0]
    finally:
        connection.close()

    assert _alembic(scratch_db, "downgrade", "0014").returncode == 0
    connection = sqlite3.connect(scratch_db)
    try:
        (sql,) = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='accounts'"
        ).fetchone()
        after = connection.execute("SELECT count(*) FROM accounts").fetchone()[0]
    finally:
        connection.close()
    assert "uq_account_tenant_swid" not in sql
    assert after == before, "the rebuild must not lose a row"
    # And it goes back up.
    assert _alembic(scratch_db, "upgrade", "head").returncode == 0


def test_the_models_and_the_migration_agree_on_the_constraint():
    """The pair that has to match, asserted against the model metadata.

    A migration that adds a constraint the model does not declare leaves
    `create_all` and `alembic upgrade head` producing different schemas -- which
    the recovery catalog would then refuse for one of them.
    """
    from api.db import Base

    table = Base.metadata.tables["accounts"]
    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert uniques == {"uq_account_tenant_swid": ("tenant_id", "swid")}, uniques
    source = (ROOT / "alembic/versions/0015_accounts_swid_unique.py").read_text()
    assert '"tenant_id", "swid"' in source
    assert "uq_account_tenant_swid" in source
