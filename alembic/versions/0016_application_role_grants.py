"""grants for the application role — without these a fresh deployment is dead

WHAT WAS MEASURED
-----------------
On a fresh PostgreSQL 16, `alembic upgrade head` produces **29 tables and
zero privileges** for the application role. Row-level security was already
enabled and forced, the policies were correct, and the app still could not
read or write a single row — because nothing had ever granted it anything.

That is worse than it sounds. The migration succeeds, the deploy looks
healthy, and every request fails at the first query. And a table added later
inherits the same problem silently, which is the shape this repository has
been fixing all sprint: a check that is green while establishing nothing.

WHY A MIGRATION AND NOT A RUNBOOK STEP
--------------------------------------
Because a runbook step is a thing somebody remembers. `ALTER DEFAULT
PRIVILEGES` below is the part that matters most: it makes every table created
*after* this revision carry the grants automatically, so the next migration
cannot reintroduce the hole by adding a table.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not CREATE the role, and it does not set a password. Role creation
with a credential is an infrastructure concern — it belongs with whatever
provisions the database (Terraform, CDK, a managed-secret rotation), not in
application migrations where the password would end up in a committed file
and in every developer's history.

So the role must already exist. On PostgreSQL this revision **fails loudly**
if it does not, naming the environment variable, rather than skipping: a
silent skip here produces exactly the dead-on-arrival deploy described above,
which is the thing it exists to prevent.

On SQLite it is a no-op, because SQLite has no roles and no GRANT.

Revision ID: 0016
Revises: 0015
"""
from __future__ import annotations

import os

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None

#: The login role the application connects as. Overridable because it differs
#: between environments; defaulted because every environment this repository
#: has been run against uses this name.
ROLE_ENV_VAR = "APP_DB_ROLE"
DEFAULT_ROLE = "edge_app"

#: The app reads its own schema version for diagnostics but must never write
#: it: the migration tool owns that table, and an application that can edit it
#: can lie about what schema it is running.
MIGRATION_TABLE = "alembic_version"


def _role() -> str:
    name = os.environ.get(ROLE_ENV_VAR, DEFAULT_ROLE).strip()
    if not name.replace("_", "").isalnum():
        raise RuntimeError(
            f"{ROLE_ENV_VAR}={name!r} is not a plain identifier. It is "
            "interpolated into GRANT statements, so it is restricted to "
            "letters, digits and underscores rather than quoted and hoped for."
        )
    return name


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    role = _role()
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
    ).scalar()
    if not exists:
        raise RuntimeError(
            f"the application role {role!r} does not exist. This revision "
            "grants privileges to it; it deliberately does not create it, "
            "because creating a login role means setting a password and that "
            "belongs with whatever provisions the database. Create it first "
            "(NOSUPERUSER NOBYPASSRLS — see api/db.py's startup check for why) "
            f"or set {ROLE_ENV_VAR} to the role you use."
        )

    quoted = _quote(role)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {quoted}")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {quoted}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}")
    # The migration tool's table: readable, never writable. Granted above by
    # ALL TABLES, so the write half is taken back explicitly.
    op.execute(
        f"REVOKE INSERT, UPDATE, DELETE ON {MIGRATION_TABLE} FROM {quoted}"
    )
    # The half that stops this hole coming back: anything the migration owner
    # creates from now on carries these grants without anyone remembering.
    owner = bind.execute(sa.text("SELECT current_user")).scalar()
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(owner)} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(owner)} IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {quoted}"
    )

    _assert_every_table_is_reachable(role)


def _assert_every_table_is_reachable(role: str) -> None:
    """The post-condition, checked rather than assumed.

    `GRANT ... ON ALL TABLES` applies to the tables that exist at the moment
    it runs. If a future edit reorders this revision before one that creates a
    table, the grant silently misses it — so the outcome is verified here
    instead of being inferred from the statement having succeeded.
    """
    bind = op.get_bind()
    missing = bind.execute(
        sa.text(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relkind = 'r'
               AND NOT has_table_privilege(:role, c.oid, 'SELECT')
             ORDER BY c.relname
            """
        ),
        {"role": role},
    ).scalars().all()
    if missing:
        raise RuntimeError(
            f"{role} still cannot SELECT from {missing}. The application would "
            "migrate cleanly and then fail every request touching them."
        )
    writable = bind.execute(
        sa.text("SELECT has_table_privilege(:role, :t, 'INSERT')"),
        {"role": role, "t": MIGRATION_TABLE},
    ).scalar()
    if writable:
        raise RuntimeError(
            f"{role} can write {MIGRATION_TABLE}. An application that can edit "
            "the migration table can lie about what schema it is running."
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role = _role()
    if not bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
    ).scalar():
        return
    quoted = _quote(role)
    owner = bind.execute(sa.text("SELECT current_user")).scalar()
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(owner)} IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {quoted}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(owner)} IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {quoted}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {quoted}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {quoted}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {quoted}")
