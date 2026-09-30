"""tenant kernel, contract step

The second half of the expand-contract pair begun in 0003. Two changes, both
of which remove a way for the schema to be wrong:

  - `leagues.tenant_id` becomes NOT NULL. While it was nullable a league
    could exist owned by nobody. That fails closed under row-level security
    -- no policy matches, so no tenant can see it -- but it fails closed
    *silently*, which is how a league disappears and nobody learns why.
  - the old global unique `uq_league_season` is dropped. Phase 36 measured
    that it makes the colliding-tenant case impossible to insert: two tenants
    could not both hold the same ESPN league, so the attack the phase existed
    to test could not be set up (P36-1). Uniqueness is per tenant now, and
    `uq_league_tenant_season` from 0003 is what enforces it.

Order matters within this revision. The NOT NULL comes first: only once every
row has a tenant does `uq_league_tenant_season` actually constrain anything,
because SQL treats NULLs as distinct and two NULL-tenant rows with the same
(espn_league_id, season) satisfy it. Dropping the old constraint before the
column is NOT NULL would open a window in which neither holds.

NOT WIRED YET -- this file lives in `alembic/pending/`, which alembic does not
scan. `head` is 0003 and stays there until the contract can be satisfied.

Why it is parked rather than merged: making `tenant_id` NOT NULL makes every
writer that does not supply a tenant fail. There are 37 `League(...)`
construction sites across 18 files and 4 raw INSERTs, and none of them supply
one yet. That retrofit is Phase 37. Landing the NOT NULL first would not make
the system safer, it would make the suite red in sixteen files at once and
invite the fix to be a default value -- which is how every league ends up
owned by tenant 1 forever.

Holding the window open is what expand-contract is FOR. At 0003 the schema
accepts both tenant-aware and tenant-unaware writers, so the call sites can be
converted a few at a time with the suite green throughout. When the last one
supplies a tenant, this file moves into `alembic/versions/` and
`api/models.py` changes `tenant_id` to non-nullable in the same commit -- the
parity test in tests/test_spend.py fails if only one of the two is done.

It is not speculative. It was run end to end on SQLite during Phase 36:
upgrade to 0004 contracted the column and dropped `uq_league_season`;
downgrade restored both with child rows intact; and the collision guard below
refused a downgrade of a database where two tenants held the same ESPN league,
leaving it unchanged at 0004. See docs/phase-36-tenant-isolation-kernel.md.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None

BACKFILL_SLUG = "default"


def upgrade() -> None:
    _catch_up_backfill()

    with op.batch_alter_table('leagues', schema=None) as batch_op:
        batch_op.alter_column(
            'tenant_id', existing_type=sa.Integer(), nullable=False
        )
        batch_op.drop_constraint('uq_league_season', type_='unique')


def _catch_up_backfill() -> None:
    """Re-run 0003's backfill for rows written during the rollback window.

    This is the point of splitting the migration: between 0003 and 0004 the
    old application code is still running and still inserting leagues with no
    tenant. Those rows are private-operator rows by the same argument 0003
    used, and they belong to the same tenant. Without this the NOT NULL fails
    on a production database for reasons that look like a migration bug.

    Deliberately inlined rather than imported from 0003: alembic loads
    revisions by path, not as an importable package, and a cross-revision
    import is a failure that only shows up under `alembic upgrade head` from
    a clean checkout.
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

    orphans = bind.execute(
        sa.select(sa.func.count())
        .select_from(leagues)
        .where(leagues.c.tenant_id.is_(None))
    ).scalar_one()
    if not orphans:
        return

    tenant_id = bind.execute(
        sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
    ).scalar_one_or_none()
    if tenant_id is None:
        bind.execute(
            tenants.insert().values(slug=BACKFILL_SLUG, created_at=sa.func.now())
        )
        tenant_id = bind.execute(
            sa.select(tenants.c.id).where(tenants.c.slug == BACKFILL_SLUG)
        ).scalar_one()

    bind.execute(
        leagues.update()
        .where(leagues.c.tenant_id.is_(None))
        .values(tenant_id=tenant_id)
    )


def downgrade() -> None:
    """Conditionally possible, and it checks rather than discovering.

    Restoring `uq_league_season` restores a rule the data may already
    violate: after 0004, two tenants may legitimately hold the same ESPN
    league for the same season, which is the whole reason the constraint was
    dropped. Recreating it on such a database raises an IntegrityError from
    inside a table rebuild, which reads as a migration defect rather than as
    what it is.

    So the collision is counted first and refused by name. The operator's
    choice at that point is a real one -- merge or delete the duplicate
    leagues -- and not one a migration should make.
    """
    bind = op.get_bind()
    leagues = sa.table(
        'leagues',
        sa.column('espn_league_id', sa.String),
        sa.column('season', sa.Integer),
    )
    dupes = sa.select(
        leagues.c.espn_league_id, leagues.c.season
    ).group_by(
        leagues.c.espn_league_id, leagues.c.season
    ).having(sa.func.count() > 1)
    colliding = bind.execute(
        sa.select(sa.func.count()).select_from(dupes.subquery())
    ).scalar_one()
    if colliding:
        raise RuntimeError(
            f"cannot restore uq_league_season: {colliding} (espn_league_id, "
            "season) pair(s) are held by more than one tenant. That is legal "
            "at revision 0004 and illegal at 0003. Resolve the duplicates "
            "before downgrading -- a migration should not choose which "
            "tenant loses its league."
        )

    with op.batch_alter_table('leagues', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_league_season', ['espn_league_id', 'season']
        )
        batch_op.alter_column(
            'tenant_id', existing_type=sa.Integer(), nullable=True
        )
