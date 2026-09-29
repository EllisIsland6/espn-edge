"""tenant kernel, expand step

Phase 36 proved a tenant kernel against a live Postgres: three tables
(`tenants`, `users`, `memberships`), a `tenant_id` on `leagues`, and row-level
security policies that made 13 cross-tenant attacks fail with SQLSTATE 42501.
This revision lands the *schema* half of that kernel. It does not create the
policies -- those are Postgres-only and belong with the runtime role work, and
a policy on a column that half the rows leave NULL protects nothing.

Expand only. Every change here is additive and reversible:

  - three new tables, which did not exist;
  - `leagues.tenant_id` added NULLABLE, so existing rows stay valid;
  - a backfill assigning every existing league to one tenant;
  - the tenant-scoped unique added ALONGSIDE the old global one, not instead
    of it, so a database at this revision satisfies both and can be rolled
    back to 0002 without a rewrite.

Revision 0004 contracts: NOT NULL, and the old unique dropped. The split is
what makes the rollback window exist -- at this revision the application can
run with or without tenant awareness.

Why the old unique cannot simply be edited in place: Phase 36 measured that
`uq_league_season` -- (espn_league_id, season), no tenant -- makes the
colliding-tenant case *impossible to insert*. Two tenants could not both hold
the same ESPN league, so the attack the phase existed to test could not be
set up. That was recorded as P36-1.

Note what the new constraint does NOT do while `tenant_id` is nullable: SQL
treats NULLs as distinct, so two rows with a NULL tenant and the same
(espn_league_id, season) both satisfy `uq_league_tenant_season`. The old
constraint is what holds that line until 0004 makes the column NOT NULL. That
is a reason the two revisions are ordered, not an oversight.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | None = None
depends_on: str | None = None

# Pre-tenancy rows belonged to whoever ran the box: private_operator mode has
# exactly one operator. They are assigned to one tenant with a reserved slug
# rather than to a guessed identity.
BACKFILL_SLUG = "default"


def upgrade() -> None:
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_tenants_slug'),
    )
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_users_email'),
    )
    op.create_table(
        'memberships',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'user_id', name='uq_membership_tenant_user'),
    )

    # One rebuild of `leagues`, not three. On SQLite every one of these is a
    # table recreate, and `leagues` is the parent of nine cascading children.
    with op.batch_alter_table('leagues', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_leagues_tenant_id', 'tenants', ['tenant_id'], ['id']
        )
        batch_op.create_index('ix_leagues_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_unique_constraint(
            'uq_league_tenant_season', ['tenant_id', 'espn_league_id', 'season']
        )

    _backfill()


def _backfill() -> None:
    """Assign every existing league to one tenant.

    Runs unconditionally and is idempotent on a second pass: the tenant is
    created only if absent, and only leagues with a NULL tenant are touched.
    A database with no leagues still gets the tenant, because 0004 will
    require one to exist for the NOT NULL to be satisfiable by new rows.
    """
    bind = op.get_bind()
    tenants = sa.table(
        'tenants',
        sa.column('id', sa.Integer),
        sa.column('slug', sa.String),
        sa.column('created_at', sa.DateTime(timezone=True)),
    )
    leagues = sa.table(
        'leagues',
        sa.column('id', sa.Integer),
        sa.column('tenant_id', sa.Integer),
    )

    existing = bind.execute(
        sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
    ).scalar_one_or_none()
    if existing is None:
        bind.execute(
            tenants.insert().values(
                slug=BACKFILL_SLUG, created_at=sa.func.now()
            )
        )
        existing = bind.execute(
            sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
        ).scalar_one()

    bind.execute(
        leagues.update()
        .where(leagues.c.tenant_id.is_(None))
        .values(tenant_id=existing)
    )


def downgrade() -> None:
    """Reverses cleanly only because the expand step added nothing destructive.

    It does drop `tenants`, `users` and `memberships` with their contents. At
    this revision that content is the backfill row plus whatever was created
    after -- there is no pre-existing tenancy data to lose, because there was
    no tenancy before 0003. If real memberships have been created, this
    downgrade discards them; that is what reversing "create these tables"
    means, and it is stated rather than guarded, because a guard here would
    only convert an intended rollback into a stuck one.
    """
    with op.batch_alter_table('leagues', schema=None) as batch_op:
        batch_op.drop_constraint('uq_league_tenant_season', type_='unique')
        batch_op.drop_index('ix_leagues_tenant_id')
        batch_op.drop_constraint('fk_leagues_tenant_id', type_='foreignkey')
        batch_op.drop_column('tenant_id')

    op.drop_table('memberships')
    op.drop_table('users')
    op.drop_table('tenants')
