"""worker heartbeats

A probe ran the worker loop against an empty queue and counted every durable
row that changed. None did. So an idle worker and a dead worker produced
byte-identical database state, and no alarm outside the host could tell them
apart. This table is the difference.

Two timestamps, not one. `last_seen_at` advances on every tick including the
empty ones; `last_claimed_at` only when work was picked up. A fresh
`last_seen_at` beside a stale `last_claimed_at` is a process that is looping
and achieving nothing -- a state no single timestamp can express, and the one
most likely to go unnoticed because the process is up and the logs are quiet.

Keyed on `owner` rather than a surrogate id: a second row for the same worker
would make "is it alive" depend on which row happened to be read first.

The `reported_*` watermark turns cumulative counters into deltas without
holding state in process memory, because a restart is precisely the event
these metrics exist to reveal and in-memory state does not survive one.

No tenant column and no row-level security policy, for the same reason as
`jobs` and `outbox`: one worker serves every tenant, so a policy here would
have to be bypassed to function.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("owner", sa.String(), primary_key=True, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        # Nullable deliberately: a worker that has never claimed anything is a
        # real state worth reporting, and defaulting it to the insert time
        # would make it indistinguishable from one that just finished a job.
        sa.Column("last_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claims", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reported_ticks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reported_claims", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reported_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The only query the staleness metric issues: newest heartbeat across all
    # workers. Without it that is a full scan on every report, and the report
    # runs on a schedule forever.
    op.create_index(
        "ix_worker_heartbeats_last_seen", "worker_heartbeats", ["last_seen_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_last_seen", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
