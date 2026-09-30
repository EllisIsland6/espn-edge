"""login read policies: a second GUC for the authenticated user

PostgreSQL only. Fixes a deadlock that revision 0005 created and that only a
live database revealed.

## The deadlock, measured

Logging in needs two reads: a session row to learn WHO the caller is, then a
membership row to learn WHICH TENANT they act in. The second read happens with
no tenant bound -- the tenant is the thing it is trying to find. Every policy
from 0005 compares against `current_tenant()`, which is NULL at that moment.

Measured on PostgreSQL 16 as `edge_app`, no tenant set:

    app_sessions by token  -> [(1,)]   OK   (deliberately unpolicied)
    memberships for user 1 -> []       EMPTY
    users row for id 1     -> []       EMPTY

and the control: bind a tenant and both reads succeed immediately -- which is
precisely what the flow cannot yet know. The isolation proved 27/27 in Phase
37b was complete enough to lock out its own key.

## The fix, and the asymmetry that makes it safe

`app.user_id` is set before the tenant is known. The READ policies on `users`
and `memberships` admit "my own rows" keyed on it. The WRITE policies do NOT
change.

That asymmetry is the entire design. Reading your own membership is how you
discover your tenant. WRITING one would let a user grant themselves membership
of any tenant -- the privilege escalation Phase 37b's correction 2 closed by
adding `WITH CHECK` in the first place. So this is the one place in the schema
where `USING` and `WITH CHECK` must differ, and they differ deliberately.

Verified before this file was written, on the same cluster, as `edge_app`:

    user 1 derives its own tenant .................... PASS
    sees only itself before binding .................. PASS
    cannot read another user's membership ............ PASS
    still sees ZERO tenant data before binding ....... PASS
    cannot grant itself another tenant (42501) ....... PASS
    CONTROL: membership in its own tenant accepted ... PASS
    after binding, sees only its tenant's leagues .... PASS

## Grants

`app_sessions` is new in 0006 and, like every table, has no grants for the
runtime role until an operator issues them. A table the app cannot SELECT is
a login that always fails.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | None = None
depends_on: str | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return

    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_app_user() RETURNS bigint AS $$
          SELECT NULLIF(current_setting('app.user_id', true), '')::bigint;
        $$ LANGUAGE sql STABLE
        """
    )

    # Reading your own membership is how you find your tenant. Writing one is
    # privilege escalation, so WITH CHECK stays strictly tenant-scoped.
    op.execute("DROP POLICY IF EXISTS t_memberships ON memberships")
    op.execute(
        """
        CREATE POLICY t_memberships ON memberships
          USING (tenant_id = current_tenant() OR user_id = current_app_user())
          WITH CHECK (tenant_id = current_tenant())
        """
    )

    op.execute("DROP POLICY IF EXISTS t_users ON users")
    op.execute(
        """
        CREATE POLICY t_users ON users
          USING (
            id = current_app_user()
            OR id IN (SELECT user_id FROM memberships WHERE tenant_id = current_tenant())
          )
          WITH CHECK (id = current_app_user())
        """
    )


def downgrade() -> None:
    """Restores 0005's policies, which means restoring the deadlock.

    Not guarded. Going back to 0005 is going back to a schema where login
    cannot work, and that is what reversing this revision means -- stated here
    rather than prevented, because a guard would turn an intended rollback
    into a stuck one.
    """
    if not _is_postgres():
        return
    op.execute("DROP POLICY IF EXISTS t_memberships ON memberships")
    op.execute(
        "CREATE POLICY t_memberships ON memberships "
        "USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant())"
    )
    op.execute("DROP POLICY IF EXISTS t_users ON users")
    op.execute(
        "CREATE POLICY t_users ON users USING ("
        "id IN (SELECT user_id FROM memberships WHERE tenant_id = current_tenant())"
        ") WITH CHECK ("
        "id IN (SELECT user_id FROM memberships WHERE tenant_id = current_tenant()))"
    )
    op.execute("DROP FUNCTION IF EXISTS current_app_user()")
