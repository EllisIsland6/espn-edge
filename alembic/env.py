"""Alembic environment for ESPN Edge (Phase 31, unit 4).

The URL comes from `Settings`, never from alembic.ini, so migrations and the
application cannot be pointed at different databases, and no operator path is
committed to the repository.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import api.models  # noqa: F401  -- registers every table on Base.metadata
from alembic import context
from api.config import get_settings
from api.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Settings supply the URL, but they do not OVERRIDE one that a caller has already
# set on this Config. The unconditional form clobbered an explicitly-provided URL
# and silently retargeted the migration at whatever database the environment
# happened to be configured for -- which, run outside the test harness, is the
# operator's real one. A caller that has named a database means it.
#
# The value goes through ConfigParser, which reads `%` as interpolation
# syntax -- and a URL-encoded password is full of them. MEASURED on the first
# migrate task against RDS (2026-10-08): `ValueError: invalid interpolation
# syntax`, with the whole URL, password included, in the traceback and so in
# CloudWatch. `%%` is the escape; get_main_option() hands back the original.
# And the value never goes into an exception message from here again.
if not config.get_main_option("sqlalchemy.url", None):
    try:
        config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_url.replace("%", "%%"))
    except ValueError:
        raise RuntimeError("the database URL could not be stored in alembic's config") from None
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most things in place; batch mode rewrites the table
        # instead, which is what makes an additive revision runnable here at all.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


class DanglingReferenceError(RuntimeError):
    """A migration left a child row pointing at a parent that is not there."""


def _set_sqlite_fk_enforcement(connection, on: bool) -> None:
    """Turn SQLite foreign-key enforcement on or off, and prove it took.

    `PRAGMA foreign_keys` is silently a no-op inside a transaction. A guard
    that can quietly fail to apply is worse than no guard, because the
    migration then reports success either way. So the value is read back and
    a mismatch is an error.
    """
    want = 1 if on else 0
    connection.exec_driver_sql(f"PRAGMA foreign_keys={want}")
    got = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
    if got != want:
        raise RuntimeError(
            f"PRAGMA foreign_keys={want} did not take (reads back {got!r}). "
            "This is the no-op-inside-a-transaction case; the batch rebuild "
            "below would cascade-delete child rows. Refusing to migrate."
        )


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"

        # Measured, Phase 36: with foreign keys enforced, SQLite's DROP TABLE
        # performs an implicit DELETE that fires ON DELETE CASCADE. Alembic's
        # batch mode rebuilds a table by copy-drop-rename, so a batch operation
        # on `leagues` emptied all nine of its cascading child tables -- and
        # reported success. Seeding child rows before the run is the only
        # reason it was seen; an empty-database probe passes.
        #
        # SQLite's own documented table-rebuild procedure turns enforcement off
        # first for exactly this reason. `foreign_key_check` below is the other
        # half: with enforcement off a migration CAN leave dangling references,
        # so the run is verified before it is allowed to commit.
        if is_sqlite:
            _set_sqlite_fk_enforcement(connection, False)
            # `exec_driver_sql` implicitly BEGINs a SQLAlchemy transaction. If
            # one is still open when alembic calls `begin_transaction()`,
            # alembic hands back a no-op context manager -- and then NOTHING
            # IS EVER COMMITTED. Measured: `alembic upgrade 0002` logged
            # "Running upgrade 0001 -> 0002" and left an empty database, which
            # the next command found by failing with "table accounts already
            # exists". The log line is not the evidence; the committed row is.
            connection.rollback()
            if connection.in_transaction():
                raise RuntimeError(
                    "a transaction is still open before begin_transaction(); "
                    "alembic would run this migration without committing it"
                )

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()

            if is_sqlite:
                dangling = connection.exec_driver_sql(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if dangling:
                    tables = sorted({row[0] for row in dangling})
                    raise DanglingReferenceError(
                        f"migration left {len(dangling)} dangling foreign-key "
                        f"reference(s) in {tables}. Foreign keys are not "
                        "enforced during migration, so this was not caught as "
                        "it happened. Rolling back."
                    )

        # Enforcement is deliberately NOT restored on this connection. The
        # first version of this function did restore it, and the read-back
        # guard rejected the attempt: by that point the migration's own DML
        # has opened a transaction, and `PRAGMA foreign_keys` inside one is a
        # silent no-op. Restoring it would have been ceremony either way --
        # the pragma is per-connection, NullPool holds nothing, and this
        # connection is closed on the next line. The application's own engine
        # sets it on every connect (api/db.py); nothing inherits this one.


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
