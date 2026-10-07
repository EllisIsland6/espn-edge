"""identities: which identity-provider subject is which user

The front door's lookup table, and deliberately NOT row-level secured.

## Why it cannot be policied

Revision 0007 explains the login deadlock: the reads that discover a caller's
tenant run before any tenant is bound, so every policy keyed on
`current_tenant()` returns nothing. 0007 fixed it with a second GUC,
`app.user_id`, admitting "my own rows" -- which works once the USER is known.

At the OIDC callback the user is not known. What arrives is an identity
provider's `sub` claim, verified, and the question is "which user is this".
`users` is invisible until `app.user_id` is set, and `app.user_id` is the
answer being looked for. The same chicken-and-egg, one step earlier.

`app_sessions` solved its version of this by being unpolicied: a session row
is found by its token hash with nothing bound, and the user id it names is
then bound. This table is the same shape for the same reason. A row holds an
issuer, an opaque subject, and a user id -- no claim, no email, no secret --
so an unpolicied read of it discloses only that a subject exists, which the
provider that issued it already knows.

## Why the application cannot write it

Who may enter is the identity provider's decision (sign-up is invitation-only
there). Which user a subject IS, and which tenant that user acts in, are the
operator's decisions, made as the owner role when provisioning. The
application role gets SELECT and nothing else: a compromised application that
could write this table could bind any subject to any user, and while it could
already mint a session for any user directly (app_sessions is writable, by
necessity), there is no reason to hand it a second, quieter way. The REVOKE
is measured below on the same cluster as 0016, as `edge_app`:

    SELECT ... FROM identities      OK
    INSERT INTO identities ...      42501 permission denied

On SQLite there are no roles and the table is simply created.

## Provisioning a user, as the owner -- and why it is not three plain INSERTs

The first draft of this recipe was three INSERTs, and it does not work:
`FORCE ROW LEVEL SECURITY` binds the table OWNER too, and the `users` policy's
`WITH CHECK (id = current_app_user())` needs `app.user_id` to equal an id that
does not exist yet. MEASURED as `edge_owner`:
`new row violates row-level security policy for table "users"`.

The policies allow exactly one shape: reserve the id first, bind it, then
insert with it explicitly. In one transaction, as the owner:

    BEGIN;
    SELECT set_config('app.tenant_id', '1', true);
    SELECT set_config('app.user_id', nextval('users_id_seq')::text, true);
    INSERT INTO users (id, email, created_at)
      VALUES (current_setting('app.user_id')::bigint, '<email>', now());
    INSERT INTO memberships (tenant_id, user_id, role)
      VALUES (1, current_setting('app.user_id')::bigint, 'member');
    INSERT INTO identities (issuer, subject, user_id, created_at)
      VALUES ('https://cognito-idp.<region>.amazonaws.com/<pool>', '<sub>',
              current_setting('app.user_id')::bigint, now());
    COMMIT;

MEASURED on PostgreSQL 16: the three rows land; the application role then
finds the identity with nothing bound, binds the user, and reads its own
membership -- the callback's exact read path. Deleting the user needs the
same two `set_config` calls first, and cascades to the membership and the
identity.

The subject is the `sub` claim of the user's ID token, which Cognito shows as
the user's "Username" / `sub` attribute in the pool. No BYPASSRLS role is
needed for provisioning, and none should be created for it.

Revision ID: 0017
Revises: 0016
"""
from __future__ import annotations

import os

import sqlalchemy as sa

from alembic import op

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | None = None
depends_on: str | None = None

#: Same variable and default as revision 0016, which is where the role's
#: grants come from. This revision only narrows them for one table.
ROLE_ENV_VAR = "APP_DB_ROLE"
DEFAULT_ROLE = "edge_app"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def upgrade() -> None:
    op.create_table(
        'identities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('issuer', sa.String(), nullable=False),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('issuer', 'subject', name='uq_identities_issuer_subject'),
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # 0016's ALTER DEFAULT PRIVILEGES has already granted the role DML on this
    # new table (measured in 0016: default privileges cover future tables).
    # Narrow it: the application reads identities and never writes them.
    role = os.environ.get(ROLE_ENV_VAR, DEFAULT_ROLE).strip()
    exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
    ).scalar()
    if not exists:
        raise RuntimeError(
            f"the application role {role!r} does not exist; revision 0016 "
            "should have refused before this one ran. Create the role or set "
            f"{ROLE_ENV_VAR}."
        )
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON identities FROM {_quote(role)}")


def downgrade() -> None:
    """Reverses cleanly. Every provisioned identity is lost, which is the
    honest meaning of reversing "add identities": users and memberships
    survive, and nobody can sign in until the rows are provisioned again."""
    op.drop_table('identities')
