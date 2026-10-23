"""ai_credentials: a tenant's own AI provider key, held like its ESPN session

ADR-012's gate G7, decided by the operator on 2026-10-23: *they pay for their
own AI API, and we store what's needed and practical, per best practice.*

## What is stored

One row per tenant -- the tenant id is the primary key, so a second key for
the same tenant is a replacement, not an addition. The key is Fernet
ciphertext written through `api/crypto.py`, the chokepoint that refuses
custody on the synthetic site and whose key is the one the custody CMK
protects (gate G3). Beside it: the provider, the key's last four characters
as a hint, when it was saved and last used, and the CLASS of the last
failure. No plaintext anywhere in the schema, in a response, or in a log.

## Who can read it

Row security, the same shape as `accounts`: `tenant_id = current_tenant()`
for USING and WITH CHECK, FORCE so the owner is bound too. 0016's default
privileges already grant the application role DML on this table; nothing is
narrowed here, because unlike `identities` the application is exactly who
writes it -- on the tenant's own request, through the CSRF-checked `PUT`.

## Why no cascade from tenants

`accounts` has none either. A credential row that vanishes because a tenant
row went is a credential nobody counted; `ops/provision_user.py` deletes it
explicitly in `REVOKE_ORDER` and prints the count.

On SQLite the table is simply created; row security is PostgreSQL's and is
measured there (`tests/test_ai_key_custody.py`, the PostgreSQL half).

Revision ID: 0018
Revises: 0017
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "ai_credentials"
PREDICATE = "tenant_id = current_tenant()"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(), nullable=False, server_default='anthropic'),
        sa.Column('api_key_encrypted', sa.String(), nullable=False),
        sa.Column('key_hint', sa.String(length=8), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id']),
        sa.PrimaryKeyConstraint('tenant_id'),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    # 0005's `_policy`, verbatim in shape: USING and WITH CHECK, always both,
    # and FORCE so the owner is bound as well. `current_tenant()` is 0005's
    # function: NULL when nothing is bound, so nothing matches.
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS t_{TABLE} ON {TABLE}")
    op.execute(
        f"CREATE POLICY t_{TABLE} ON {TABLE} "
        f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    """Reverses cleanly, and every stored key is gone with the table -- which
    is the honest meaning of reversing "store the key": each person pastes it
    again. Nothing else references the table."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS t_{TABLE} ON {TABLE}")
    op.drop_table(TABLE)
