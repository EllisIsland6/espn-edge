"""The transactional outbox: emit inside the write, deliver after it.

`emit` adds a row and nothing else -- no flush, no commit, no network. That is
the whole point: the message becomes part of the caller's transaction, so it
commits with the state change or disappears with it. A caller that rolls back
has emitted nothing, and no code had to remember to undo anything.

`relay` delivers afterwards and marks each row only once the sink has
returned. **At-least-once, never zero.** A relay that delivers and then dies
before marking will deliver again, which is why `dedupe_key` travels with the
message: exactly-once across a process boundary is not available, so the
design makes the duplicate recognisable rather than pretending it cannot
happen.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import OutboxMessage
from .jobs import MAX_ERROR, Clock, _now

#: Backoff for a sink that is refusing. Flat rather than exponential: an
#: outbox backlog is an operational problem somebody should see, and a growing
#: delay hides it by making the queue look like it is draining.
RETRY_AFTER = timedelta(seconds=30)

#: A delivery function. Raising means "not delivered" -- the row stays
#: pending. Returning means delivered, and the row is marked.
Sink = Callable[[OutboxMessage], None]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def emit(
    session: Session,
    *,
    topic: str,
    dedupe_key: str,
    tenant_id: int | None = None,
    payload: dict | None = None,
    clock: Clock = _now,
) -> OutboxMessage:
    """Queue a message as part of the caller's transaction.

    Deliberately does not flush. A flush here would be harmless today and
    wrong in principle: it invites a caller to believe the message is
    persisted when the transaction may still roll back. The message is as
    durable as the write it accompanies, and no more.
    """
    now = clock()
    message = OutboxMessage(
        tenant_id=tenant_id,
        topic=topic,
        dedupe_key=dedupe_key,
        payload_json=payload,
        created_at=now,
        available_at=now,
        attempts=0,
    )
    session.add(message)
    return message


def pending(session: Session, *, limit: int = 100, clock: Clock = _now) -> list[OutboxMessage]:
    """Undelivered messages that are due, oldest first."""
    now = clock()
    return list(
        session.execute(
            select(OutboxMessage)
            .where(
                OutboxMessage.delivered_at.is_(None),
                OutboxMessage.available_at <= now,
            )
            .order_by(OutboxMessage.available_at, OutboxMessage.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def relay(
    session: Session,
    sink: Sink,
    *,
    limit: int = 100,
    clock: Clock = _now,
) -> tuple[int, int]:
    """Deliver what is pending. Returns (delivered, failed).

    Each message is marked only after the sink returns. Marking first would
    turn every sink failure into a silently dropped message, which is the one
    outcome this table exists to prevent -- so the ordering here is the whole
    guarantee, not an implementation detail.

    A sink that raises leaves the row pending with a cooldown. One bad message
    therefore delays the ones behind it by `RETRY_AFTER` rather than blocking
    them forever, and it does not consume a budget it cannot see.
    """
    now = clock()
    delivered = failed = 0

    for message in pending(session, limit=limit, clock=clock):
        message.attempts += 1
        try:
            sink(message)
        except Exception as exc:  # noqa: BLE001 - one bad sink must not stop the rest
            message.last_error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR]
            message.available_at = now + RETRY_AFTER
            failed += 1
        else:
            message.delivered_at = now
            message.last_error = None
            delivered += 1

    session.flush()
    return delivered, failed


def undelivered_age(session: Session, *, clock: Clock = _now) -> timedelta:
    """How long the oldest undelivered message has waited.

    The number to alarm on. Count says nothing -- a burst draining quickly is
    healthy; one message stuck for an hour means a sink is down and somebody
    has not been told something they were promised.
    """
    now = clock()
    oldest = session.execute(
        select(OutboxMessage.created_at)
        .where(OutboxMessage.delivered_at.is_(None))
        .order_by(OutboxMessage.created_at)
        .limit(1)
    ).scalar_one_or_none()
    return timedelta(0) if oldest is None else now - _utc(oldest)
