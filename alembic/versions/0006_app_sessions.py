"""application sessions

The application's own answer to "who is this request", separate from whatever
identity provider vouched for the person. A Cognito `sub` proves identity; it
does not say which tenant someone may act in. An access token that carried
that authority would make the identity provider the authorization system, and
Phase 40's contract says explicitly that it must not be.

So a session names a user, and the user's membership names the tenant. Two
lookups, each answering one question.

`token_hash` is SHA-256 of a random opaque value. The raw token exists in
exactly two places -- the response that mints it and the caller's cookie --
and never on disk, so a stolen database yields no usable session. Same reason
password hashes exist.

`expires_at` and `revoked_at` are separate on purpose. Expiry is a fact about
time; revocation is a decision someone made. Collapsing them loses the ability
to answer "was this cut off, or did it run out?", which is the first question
asked after an incident.

No row-level security policy on this table, and that is deliberate: it is read
*before* a tenant is known, by the code that works out which tenant to use.
A policy here would be circular. It is protected by the token being unguessable
and unreadable at rest, not by a predicate.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'app_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('origin', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_app_sessions_token_hash'),
    )
    with op.batch_alter_table('app_sessions', schema=None) as batch_op:
        batch_op.create_index(
            'ix_app_sessions_user_expires', ['user_id', 'expires_at'], unique=False
        )


def downgrade() -> None:
    """Reverses cleanly, and signs everyone out.

    Dropping the table ends every live session. That is the honest meaning of
    reversing "add sessions" and it is not guarded, because a guard would turn
    an intended rollback into a stuck one. Nothing else is lost: a session is
    derived state, and the users and memberships it points at survive.
    """
    with op.batch_alter_table('app_sessions', schema=None) as batch_op:
        batch_op.drop_index('ix_app_sessions_user_expires')
    op.drop_table('app_sessions')
