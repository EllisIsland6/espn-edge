"""The tenant context seam.

Phase 37's outcome is that every read and write derives ownership from the
authenticated tenant transaction. Its plan sizes that as 122 sensitive query
call sites across 19 files. Measured, the application obtains a session in
exactly **two** places -- `db.session_scope` for services and scripts, and
`db.get_session`, the FastAPI dependency every router uses. The other 28
constructions of `SessionLocal()` in this repository are all in tests.

So the retrofit is two functions, not a hundred and twenty-two edits, and that
is not a shortcut: adding `.where(tenant_id == ...)` to each call site would
be a hundred and twenty-two chances to forget one, and the one you forget is
the leak. Phase 36's own contract says it -- **row-level security remains the
safety boundary; explicit predicates aid plans and readability but are not the
isolation control.** The app's job is to bind the tenant reliably. The
database's job is to refuse everything when it is not bound.

What this module does NOT do is decide *who* the caller is. There is no
authentication in this application: no session cookie, no bearer token, no
`current_user`. In `private_operator` mode that is correct -- there is one
operator -- and `resolve_tenant_id` enforces it by refusing to guess when the
database holds more than one tenant. Real multi-tenant identity needs Cognito,
which Phase 36 explicitly excluded, and it is not pretended here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Tenant

#: The tenant that alembic 0003's backfill assigns pre-tenancy rows to.
DEFAULT_TENANT_SLUG = "default"

#: Postgres GUC the row-level security policies read. Phase 36's policies are
#: written against `current_setting('app.tenant_id', true)`.
TENANT_GUC = "app.tenant_id"


class TenantNotResolved(RuntimeError):
    """No single tenant could be determined for this session.

    Raised rather than defaulted. A default here is how every request ends up
    reading tenant 1 -- including, eventually, a request that should not have.
    """


def ensure_default_tenant(session: Session) -> int:
    """Create the single tenant if it is absent, and return its id.

    The migration does this for any database that went through alembic 0003.
    SQLite databases built by `Base.metadata.create_all` -- the offline suite
    and a local operator's box -- never run a migration, so the guarantee has
    to exist on that path too or the seam has nothing to bind to.

    Only ever creates the ONE tenant, and only when there are none. It is not
    a get-or-create keyed on a caller-supplied slug, because that would make
    tenant creation a side effect of reading.
    """
    existing = session.execute(
        select(Tenant.id).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    session.add(Tenant(slug=DEFAULT_TENANT_SLUG))
    session.flush()
    return session.execute(
        select(Tenant.id).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).scalar_one()


@event.listens_for(Tenant.__table__, "after_create")
def _seed_single_tenant(target, connection, **_kw) -> None:
    """A `tenants` table that has just been created has its single tenant.

    This is the `create_all` path's equivalent of alembic 0003's backfill, and
    it exists for the same reason: the seam refuses to hand out a session when
    no tenant exists, so a schema with no tenant is a schema no request can
    use. Making it a property of the table rather than of a fixture is what
    makes it hold everywhere -- the offline suite resets the schema inside
    dozens of individual test fixtures, and a guarantee that has to be
    remembered at each of them is not a guarantee.

    It cannot double-insert: `after_create` fires exactly once per creation of
    a table that is empty by definition. Postgres never reaches here, because
    `init_db` returns before `create_all` and the migration owns that schema.

    A DDL hook that inserts a row is unusual, and worth being uneasy about. It
    is scoped as narrowly as it can be: one row, one table, only at creation,
    only the reserved slug, and `resolve_tenant_id` still refuses outright if
    a second tenant ever appears.
    """
    connection.execute(
        target.insert().values(slug=DEFAULT_TENANT_SLUG, created_at=datetime.now(UTC))
    )


def resolve_tenant_id(session: Session) -> int:
    """The tenant this session acts as, or a refusal.

    Deliberately reads two rows and not one. `LIMIT 1` would silently pick the
    lowest id the day a second tenant exists, which is the exact failure this
    phase is about, and it would do it without a symptom.
    """
    rows = session.execute(select(Tenant.id).order_by(Tenant.id).limit(2)).scalars().all()
    if not rows:
        raise TenantNotResolved(
            "no tenant exists. A migrated database has one (alembic 0003 "
            "backfill); a create_all database needs `ensure_default_tenant`."
        )
    if len(rows) > 1:
        raise TenantNotResolved(
            "more than one tenant exists, and this application has no "
            "authentication to choose between them. Multi-tenant serving "
            "needs the identity work Phase 36 excluded; until then a second "
            "tenant is a configuration error, not a request to guess."
        )
    return rows[0]


def _apply_guc(connection, tenant_id: int) -> None:
    """Set the tenant for the CURRENT TRANSACTION only.

    The third argument to `set_config` is `is_local`, and it is the whole
    control. `true` scopes the value to this transaction. `false` scopes it to
    the *session*, which under a connection pool means the next request to
    borrow this connection inherits the previous request's tenant -- the pool
    reuse leak Phase 36 named. It is one boolean and it is the difference
    between isolation and a cross-tenant read.

    `SET LOCAL app.tenant_id = :t` cannot be used: it takes no bind parameter,
    so the tenant would have to be interpolated into the SQL string.
    """
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_GUC, "value": str(tenant_id)},
    )


def bind_session(session: Session, tenant_id: int) -> None:
    """Bind a tenant to a session, and keep it bound.

    A single `set_config(..., true)` is not enough on its own, and this is the
    subtle part. Transaction-local means the binding dies with the
    transaction: a handler that commits half way through then runs its
    remaining statements in a NEW transaction, with no tenant set. Under RLS
    that second half reads nothing, which presents as a mysterious empty
    result rather than as an error.

    So the binding is re-applied on every transaction this session begins,
    through a listener attached to this session instance. The listener is not
    global -- tests construct `SessionLocal()` directly in 28 places and none
    of them should change behaviour by importing this module.
    """
    session.info["tenant_id"] = tenant_id
    session.info["tenant_binds"] = 0

    if not get_settings().is_postgres:
        # SQLite has no GUCs and no row-level security. The bind is still
        # recorded so the seam is observable offline; what it is NOT is a
        # substitute for the isolation, and no test here should imply it is.
        session.info["tenant_binds"] = 1
        return

    def _rebind(session_, transaction_, connection) -> None:
        bound = session_.info.get("tenant_id")
        if bound is None:
            return
        _apply_guc(connection, bound)
        session_.info["tenant_binds"] = session_.info.get("tenant_binds", 0) + 1

    event.listen(session, "after_begin", _rebind)
    if session.in_transaction():
        _rebind(session, None, session.connection())


def current_tenant_id(session: Session) -> int:
    """The tenant bound to this session, or a refusal.

    For call sites that need the id as a value -- creating a row, filtering a
    query for plan quality. Reading it from the session rather than resolving
    it again is the point: two resolutions can disagree.
    """
    tenant_id = session.info.get("tenant_id")
    if tenant_id is None:
        raise TenantNotResolved(
            "this session never went through the tenant seam. Sessions from "
            "`db.get_session` and `db.session_scope` are bound; a raw "
            "`SessionLocal()` is not."
        )
    return tenant_id
