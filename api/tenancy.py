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
from .models import Membership, Tenant

#: The tenant that alembic 0003's backfill assigns pre-tenancy rows to.
DEFAULT_TENANT_SLUG = "default"

#: Postgres GUC the row-level security policies read. Phase 36's policies are
#: written against `current_setting('app.tenant_id', true)`.
TENANT_GUC = "app.tenant_id"

#: Postgres GUC naming the authenticated user, set BEFORE the tenant is known.
#:
#: Measured on PostgreSQL 16: without this, logging in is impossible. The flow
#: reads a session to learn who you are, then reads your membership to learn
#: which tenant -- but `memberships` and `users` are policied on
#: `current_tenant()`, which is NULL until a tenant is bound. So the second
#: read returns nothing, always, and no tenant can ever be derived. The
#: isolation was complete enough to lock out its own key.
#:
#: The read policies therefore admit "my own rows" keyed on this. The WRITE
#: policies deliberately do not: reading your own membership is how you find
#: your tenant, whereas writing one would let you grant yourself membership of
#: any tenant. This is the one place in the schema where USING and WITH CHECK
#: must differ, and it is on purpose.
USER_GUC = "app.user_id"


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

    A CONFIGURED tenant short-circuits the query, because on PostgreSQL the
    query cannot work. Measured, on a correctly migrated database: the app
    connects as a NOSUPERUSER NOBYPASSRLS role, `tenants` has FORCE ROW LEVEL
    SECURITY, and the policy hides every row until `app.tenant_id` is bound --
    so the SELECT that exists to FIND the tenant needs a tenant already bound
    to return anything. As `edge_app` with nothing bound it reads 0 rows; with
    `SET LOCAL app.tenant_id='1'` it reads 1. Every request 500s with
    `TenantNotResolved`. SQLite has no row-level security, so discovery works
    there and the offline suite never saw this.

    The configured id is NOT taken on trust: `bind_session` verifies it after
    binding, which is the only order in which the check can see anything. See
    `_verify_configured_tenant`.

    Discovery below is unchanged, and deliberately reads two rows and not one.
    `LIMIT 1` would silently pick the lowest id the day a second tenant
    exists, which is the exact failure this phase is about, and it would do it
    without a symptom.
    """
    configured = get_settings().tenant_id
    if configured is not None:
        if configured < 1:
            raise TenantNotResolved(
                f"TENANT_ID={configured} is not a usable tenant id. Ids are "
                "positive; a zero or negative value is a misconfiguration, "
                "and binding it would read nothing under row-level security "
                "and look like an empty database."
            )
        return configured
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


def _apply_guc(connection, tenant_id: int | None, user_id: int | None = None) -> None:
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
    for name, value in ((TENANT_GUC, tenant_id), (USER_GUC, user_id)):
        if value is None:
            continue
        connection.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": name, "value": str(value)},
        )


def bind_session(
    session: Session, tenant_id: int | None = None, *, user_id: int | None = None
) -> None:
    """Bind a tenant and/or a user to a session, and keep them bound.

    Callable twice, which the login flow needs: the user is bound first, on its
    own, so the membership lookup that finds the tenant can see anything at
    all; then the tenant is bound alongside it. The listener is registered once
    per session and reads whatever `session.info` holds at the time it fires,
    so the second call updates the binding rather than stacking a second one.

    A single `set_config(..., true)` is not enough on its own. Transaction-local
    means the binding dies with the transaction: a handler that commits half
    way through then runs its remaining statements in a NEW transaction with
    nothing set. Under RLS that second half reads nothing, which presents as a
    mysteriously empty result rather than as an error. So the binding is
    re-applied on every transaction this session begins.

    The listener is attached to this session instance, not globally -- tests
    construct `SessionLocal()` directly in 28 places and none of them should
    change behaviour by importing this module.
    """
    if tenant_id is not None:
        session.info["tenant_id"] = tenant_id
    if user_id is not None:
        session.info["user_id"] = user_id
    session.info.setdefault("tenant_binds", 0)
    verify_after_binding = (
        tenant_id is not None and get_settings().tenant_id == tenant_id
    )

    if not get_settings().is_postgres:
        # SQLite has no GUCs and no row-level security. The binding is still
        # recorded so the seam is observable offline; what it is NOT is a
        # substitute for the isolation, and no test should imply otherwise.
        session.info["tenant_binds"] = session.info.get("tenant_binds", 0) + 1
        if verify_after_binding:
            _verify_configured_tenant(session, tenant_id)
        return

    def _rebind(session_, transaction_, connection) -> None:
        bound_tenant = session_.info.get("tenant_id")
        bound_user = session_.info.get("user_id")
        if bound_tenant is None and bound_user is None:
            return
        _apply_guc(connection, bound_tenant, bound_user)
        session_.info["tenant_binds"] = session_.info.get("tenant_binds", 0) + 1

    if not session.info.get("_rebind_registered"):
        event.listen(session, "after_begin", _rebind)
        session.info["_rebind_registered"] = True
    if session.in_transaction():
        _rebind(session, None, session.connection())
    if verify_after_binding:
        _verify_configured_tenant(session, tenant_id)


def _verify_configured_tenant(session: Session, tenant_id: int) -> None:
    """Prove a CONFIGURED tenant id names a real tenant -- after binding it.

    The order is the whole point and it cannot be the other way round. Under
    FORCE ROW LEVEL SECURITY the `tenants` table is invisible to the
    application role until `app.tenant_id` is bound, so a check before
    binding reads nothing and would reject every id including the correct
    one. A check after binding reads exactly the row the binding claims, so
    it answers the only question worth asking: does the tenant this process
    has been told it is actually exist?

    Without this, a typo in `TENANT_ID` is invisible. The app binds a tenant
    that does not exist, every policy then matches nothing, and the result is
    an application that starts cleanly, answers 200, and shows an empty
    database -- which is the worst of the three possible outcomes, because it
    looks like no data rather than like a misconfiguration.

    Once per bind, by primary key.
    """
    exists = session.execute(
        select(Tenant.id).where(Tenant.id == tenant_id)
    ).scalar_one_or_none()
    if exists is None:
        raise TenantNotResolved(
            f"TENANT_ID={tenant_id} does not name an existing tenant. The "
            "binding succeeded, so every row-level security policy will now "
            "match nothing: the application would serve an empty database "
            "rather than report a misconfiguration. Check the value against "
            "`SELECT id, slug FROM tenants` as a role that can see them."
        )


def tenant_for_user(session: Session, user_id: int) -> int:
    """The tenant this user acts in, from their membership.

    Requires `bind_session(session, user_id=...)` to have run first on
    PostgreSQL, or the membership rows are invisible to the very query that
    needs them -- see USER_GUC above.

    Reads two rows, not one, for the same reason `resolve_tenant_id` does: a
    user who belongs to two tenants is a real state this application has no way
    to disambiguate yet, and silently serving the lower id would be a
    cross-tenant read that looks like a successful request. Choosing between
    memberships needs a tenant-selection step that does not exist.
    """
    rows = (
        session.execute(
            select(Membership.tenant_id)
            .where(Membership.user_id == user_id)
            .order_by(Membership.tenant_id)
            .limit(2)
        )
        .scalars()
        .all()
    )
    if not rows:
        raise TenantNotResolved(
            f"user {user_id} has no membership. Default deny: a user who "
            "belongs to no tenant sees nothing, which is the correct answer "
            "and not an error to work around."
        )
    if len(rows) > 1:
        raise TenantNotResolved(
            f"user {user_id} belongs to more than one tenant, and there is no "
            "tenant-selection step to choose between them. Serving the lower "
            "id would be a cross-tenant read that looks like success."
        )
    return rows[0]


def current_user_id(session: Session) -> int | None:
    """The authenticated user bound to this session, if any.

    None in private-operator mode, where there is no login and no user row.
    """
    return session.info.get("user_id")


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
