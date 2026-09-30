"""SQLAlchemy 2 engine/session wiring for the local SQLite DB (SPEC 3, 4)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

# check_same_thread=False so APScheduler jobs / CLI can share the engine.
def _engine_kwargs(url: str) -> dict:
    """`check_same_thread` is a SQLite connect arg and psycopg rejects it outright.

    Phase 33 measured this: the app could not open a PostgreSQL connection at all
    while it was passed unconditionally.
    """
    if url.startswith("sqlite"):
        # so APScheduler jobs / CLI can share the engine
        return {"connect_args": {"check_same_thread": False}}
    # A bounded pool, because the web process holds it for the life of the
    # process and ADR-002 puts web and worker on one 2 GiB host.
    return {"pool_size": 5, "max_overflow": 5, "pool_pre_ping": True}


engine = create_engine(
    _settings.sqlalchemy_url,
    echo=False,
    future=True,
    **_engine_kwargs(_settings.sqlalchemy_url),
)


@event.listens_for(Engine, "connect")
def _enable_sqlite_fks(dbapi_connection, _connection_record) -> None:
    """SQLite does not enforce foreign keys unless asked, per-connection (SPEC 4).

    Registered on the base Engine class so every connection (app, CLI, tests,
    APScheduler) turns it on. Guards against corrupt writes like an FK pointing at
    a non-existent team.
    """
    # Registered on the base Engine class, so without this guard it fires for
    # EVERY connection including PostgreSQL, which rejects PRAGMA. Phase 33 hit
    # this the moment it pointed the app at a real server.
    if "sqlite" not in type(dbapi_connection).__module__.lower():
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Prepare local storage. On PostgreSQL this performs NO DDL.

    Phase 35 makes Alembic the schema authority for the release path. Two app
    processes starting at once must issue zero DDL between them — a `create_all`
    race is exactly the kind of thing that works on one developer's machine and
    corrupts a deploy — so on PostgreSQL this function creates directories and
    returns, and `alembic upgrade head` is the only thing that shapes the schema.

    SQLite keeps the old behaviour deliberately. It is the offline suite's and a
    local operator's database, it has no concurrent-start problem worth the churn,
    and 838 existing tests build their schema through this call.
    """
    _settings.db_file.parent.mkdir(parents=True, exist_ok=True)
    _settings.raw_cache_dir.mkdir(parents=True, exist_ok=True)
    from . import models  # noqa: F401  - registers every table on Base.metadata

    if _settings.is_postgres:
        return

    Base.metadata.create_all(engine)
    _apply_additive_migrations()

    # A database built by `create_all` never ran alembic 0003, so it never ran
    # the backfill that guarantees a tenant exists. Without this the seam has
    # nothing to bind to and every request fails closed at startup -- correct,
    # but useless. Postgres returns above; there, 0003 is the guarantee.
    from .tenancy import ensure_default_tenant

    with Session(engine) as session:
        ensure_default_tenant(session)
        session.commit()


def _apply_additive_migrations() -> None:
    """Keep existing local SQLite databases compatible with additive releases.

    SQLite only, and reached only from `init_db`'s SQLite branch. An additive
    `ALTER` at process start is a second schema authority competing with Alembic;
    it survives here because local databases predate the migrations and would
    otherwise need a manual step, and it dies with the SQLite path.
    """
    additions = {
        "receiving_tds": "FLOAT",
        "team_passing_yards": "FLOAT",
    }
    with engine.begin() as connection:
        if not inspect(connection).has_table("opportunity_weeks"):
            return
        existing = {
            column["name"] for column in inspect(connection).get_columns("opportunity_weeks")
        }
        for column, sql_type in additions.items():
            if column not in existing:
                connection.execute(
                    text(f"ALTER TABLE opportunity_weeks ADD COLUMN {column} {sql_type}")
                )


class UnsafeDatabaseRole(RuntimeError):
    """The connected role can see through row-level security."""


def assert_runtime_role_is_constrained() -> None:
    """Refuse to run as a role that RLS cannot constrain. PostgreSQL only.

    Phase 33 measured the trap: `ENABLE` + `FORCE ROW LEVEL SECURITY` on every
    table, every policy correct, and a superuser still saw every tenant's rows
    with no tenant context set. `FORCE` constrains the table OWNER; it does not
    constrain a superuser or a role with `BYPASSRLS`. Isolation is then silently
    off and nothing in the schema shows it.

    So the property is asserted at startup rather than assumed from the DDL. It
    fails closed: a role that cannot be checked is refused too, because "we could
    not tell" and "it is safe" are different answers.
    """
    if not _settings.is_postgres:
        return
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).first()
    if row is None:
        raise UnsafeDatabaseRole(
            "cannot determine the privileges of the connected role; refusing to start"
        )
    is_super, bypasses = bool(row[0]), bool(row[1])
    if is_super or bypasses:
        raise UnsafeDatabaseRole(
            "the application is connected as a role row-level security cannot "
            f"constrain (superuser={is_super}, bypassrls={bypasses}). Migrations run "
            "as the owner; the application and worker must use a NOSUPERUSER "
            "NOBYPASSRLS role."
        )


def _bound_session(*, request=None, tenant_id: int | None = None) -> Session:
    """A session with a tenant bound, which is the only kind this app hands out.

    Where the tenant comes from depends on the mode, and that is the design
    rather than a shortcut:

    - `private_operator` is one person on their own machine. There is no login
      because a login screen would protect nothing, and the single tenant is
      the right answer.
    - `public_synthetic` serves people who must prove who they are. The tenant
      comes from the caller's membership and from nothing else -- never from a
      request body, a header, or "there is only one".

    Background work has no caller, so it passes `tenant_id` explicitly. It must
    NOT fall back to the single tenant in hosted mode: a job that quietly picks
    a tenant is the background-job attack named in the Phase 36 contract, and
    it is how a scheduled export reads everyone's leagues.

    Imported here rather than at module scope: `tenancy` imports `models`,
    which imports `Base` from this module.
    """
    from .config import get_settings
    from .tenancy import TenantNotResolved, bind_session, resolve_tenant_id, tenant_for_user

    hosted = get_settings().is_public_synthetic
    session = SessionLocal()
    try:
        if tenant_id is not None:
            bind_session(session, tenant_id)
        elif not hosted:
            bind_session(session, resolve_tenant_id(session))
        elif request is None:
            raise TenantNotResolved(
                "hosted mode has no ambient tenant. Work with no authenticated "
                "caller must name its tenant: `session_scope(tenant_id=...)`."
            )
        else:
            from .auth import COOKIE_NAME, verify_session

            app_session = verify_session(session, request.cookies.get(COOKIE_NAME))
            # The user first, alone. Until `app.user_id` is set, the membership
            # rows that name the tenant are invisible to the query that needs
            # them -- measured on PostgreSQL 16, see tenancy.USER_GUC.
            bind_session(session, user_id=app_session.user_id)
            bind_session(
                session,
                tenant_for_user(session, app_session.user_id),
                user_id=app_session.user_id,
            )
    except Exception:
        session.close()
        raise
    return session


@contextmanager
def session_scope(tenant_id: int | None = None) -> Iterator[Session]:
    """Transactional scope for scripts/services outside request handlers.

    `tenant_id` is optional only because private-operator mode has exactly one
    tenant to fall back on. In hosted mode it is required, and omitting it
    raises rather than guessing.
    """
    session = _bound_session(tenant_id=tenant_id)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency. Every router obtains its session here.

    Takes the request because in hosted mode the tenant is derived from the
    caller's session cookie. A rejected or absent session is a 401: the caller
    is told nothing about why, because "expired" rather than "unknown"
    confirms a token was once real.
    """
    from .auth import SessionRejected

    try:
        session = _bound_session(request=request)
    except SessionRejected as exc:
        raise HTTPException(status_code=401, detail="authentication required") from exc

    try:
        yield session
    finally:
        session.close()
