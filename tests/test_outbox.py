"""The transactional outbox.

One property matters more than all the others, and it is the first test:
**a message emitted inside a transaction that rolls back does not exist.**
Everything else here is about what happens after a commit.

The second theme is the ordering in `relay`: a row is marked delivered only
after the sink returns. Marking first would turn every sink failure into a
silently dropped message, which is the single outcome the table exists to
prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db import SessionLocal
from api.models import OutboxMessage, Tenant
from api.services.outbox import (
    RETRY_AFTER,
    emit,
    pending,
    relay,
    undelivered_age,
)

T0 = datetime(2026, 10, 2, 10, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tenant_id(db_session) -> int:
    return db_session.query(Tenant).one().id


def _count(**filters) -> int:
    probe = SessionLocal()
    try:
        query = probe.query(OutboxMessage)
        for key, value in filters.items():
            query = query.filter(getattr(OutboxMessage, key) == value)
        return query.count()
    finally:
        probe.close()


def test_a_rolled_back_transaction_emits_nothing(db_session, clock, tenant_id):
    """The property the whole pattern exists for.

    Nothing had to remember to undo the message, because it was never a
    separate action -- it was part of the write that did not happen.
    """
    emit(
        db_session,
        topic="league.synced",
        dedupe_key="d1",
        tenant_id=tenant_id,
        clock=clock,
    )
    db_session.rollback()

    assert _count() == 0, "a message survived a rolled-back transaction"


def test_a_committed_transaction_emits_exactly_one(db_session, clock, tenant_id):
    emit(
        db_session,
        topic="league.synced",
        dedupe_key="d1",
        tenant_id=tenant_id,
        payload={"league_id": 7},
        clock=clock,
    )
    db_session.commit()

    assert _count() == 1
    message = pending(db_session, clock=clock)[0]
    assert message.topic == "league.synced"
    assert message.payload_json == {"league_id": 7}
    assert message.delivered_at is None


def test_emit_does_not_flush(db_session, clock, tenant_id):
    """Deliberate. Flushing here would be harmless today and wrong in
    principle: it invites a caller to believe the message is persisted when
    the transaction may still roll back."""
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    assert _count() == 0, "emit reached the database before the caller committed"
    db_session.commit()
    assert _count() == 1


def test_the_relay_delivers_and_marks(db_session, clock, tenant_id):
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    seen: list[str] = []
    delivered, failed = relay(db_session, lambda m: seen.append(m.dedupe_key), clock=clock)
    db_session.commit()

    assert (delivered, failed) == (1, 0)
    assert seen == ["d"]
    assert pending(db_session, clock=clock) == []


def test_a_sink_that_raises_leaves_the_message_pending(db_session, clock, tenant_id):
    """The ordering guarantee. If `delivered_at` were set before the sink ran,
    this message would be gone and nobody would ever learn the notification
    did not happen."""
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    def _broken(_message):
        raise ConnectionError("sink down")

    delivered, failed = relay(db_session, _broken, clock=clock)
    db_session.commit()

    assert (delivered, failed) == (0, 1)
    assert _count(delivered_at=None) == 1
    row = db_session.query(OutboxMessage).one()
    assert "ConnectionError" in row.last_error
    assert row.attempts == 1


def test_a_failed_message_waits_before_being_retried(db_session, clock, tenant_id):
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()
    relay(db_session, lambda _m: (_ for _ in ()).throw(ConnectionError()), clock=clock)
    db_session.commit()

    assert pending(db_session, clock=clock) == [], "retried with no cooldown"
    clock.advance(RETRY_AFTER + timedelta(seconds=1))
    assert len(pending(db_session, clock=clock)) == 1


def test_a_recovered_sink_delivers_the_waiting_message(db_session, clock, tenant_id):
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()
    relay(db_session, lambda _m: (_ for _ in ()).throw(ConnectionError()), clock=clock)
    db_session.commit()
    clock.advance(RETRY_AFTER + timedelta(seconds=1))

    seen: list[str] = []
    delivered, _ = relay(db_session, lambda m: seen.append(m.dedupe_key), clock=clock)
    db_session.commit()

    assert (delivered, seen) == (1, ["d"])
    row = db_session.query(OutboxMessage).one()
    assert row.delivered_at is not None
    assert row.last_error is None, "the stale error survived a successful delivery"
    assert row.attempts == 2


def test_one_bad_message_does_not_block_the_others(db_session, clock, tenant_id):
    """A sink that raises for one message must not stop the rest of the batch.
    Otherwise a single permanently-bad message is a complete outage."""
    for key in ("good1", "bad", "good2"):
        emit(db_session, topic="t", dedupe_key=key, tenant_id=tenant_id, clock=clock)
    db_session.commit()

    seen: list[str] = []

    def _picky(message):
        if message.dedupe_key == "bad":
            raise ValueError("cannot handle this one")
        seen.append(message.dedupe_key)

    delivered, failed = relay(db_session, _picky, clock=clock)
    db_session.commit()

    assert (delivered, failed) == (2, 1)
    assert seen == ["good1", "good2"], seen
    assert _count(delivered_at=None) == 1


def test_a_delivered_message_is_not_delivered_again(db_session, clock, tenant_id):
    """At-least-once is the guarantee, but the relay must not re-send what it
    already marked -- that would make every tick a redelivery storm."""
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    seen: list[str] = []
    relay(db_session, lambda m: seen.append(m.dedupe_key), clock=clock)
    db_session.commit()
    relay(db_session, lambda m: seen.append(m.dedupe_key), clock=clock)
    db_session.commit()

    assert seen == ["d"], seen


def test_the_dedupe_key_travels_with_the_message(db_session, clock, tenant_id):
    """At-least-once means the receiver will sometimes see a repeat. The key
    is what lets it recognise one -- the design makes the duplicate cheap
    rather than pretending it cannot happen."""
    emit(db_session, topic="t", dedupe_key="stable-key", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    keys: list[str] = []
    relay(db_session, lambda m: keys.append(m.dedupe_key), clock=clock)
    assert keys == ["stable-key"]


def test_the_tenant_travels_with_the_message(db_session, clock, tenant_id):
    """The relay drains for every tenant, so the receiver needs to know whose
    event this is from the row rather than from ambient context."""
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    tenants: list[int | None] = []
    relay(db_session, lambda m: tenants.append(m.tenant_id), clock=clock)
    assert tenants == [tenant_id]


def test_messages_are_delivered_oldest_first(db_session, clock, tenant_id):
    for key in ("first", "second", "third"):
        emit(db_session, topic="t", dedupe_key=key, tenant_id=tenant_id, clock=clock)
        clock.advance(timedelta(seconds=1))
    db_session.commit()

    seen: list[str] = []
    relay(db_session, lambda m: seen.append(m.dedupe_key), clock=clock)
    assert seen == ["first", "second", "third"], seen


def test_the_relay_respects_its_limit(db_session, clock, tenant_id):
    for i in range(5):
        emit(db_session, topic="t", dedupe_key=f"d{i}", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    delivered, _ = relay(db_session, lambda _m: None, limit=2, clock=clock)
    db_session.commit()
    assert delivered == 2
    assert _count(delivered_at=None) == 3


def test_undelivered_age_is_zero_when_everything_is_delivered(db_session, clock, tenant_id):
    emit(db_session, topic="t", dedupe_key="d", tenant_id=tenant_id, clock=clock)
    db_session.commit()
    relay(db_session, lambda _m: None, clock=clock)
    db_session.commit()
    assert undelivered_age(db_session, clock=clock) == timedelta(0)


def test_undelivered_age_measures_the_oldest_stuck_message(db_session, clock, tenant_id):
    """Count says nothing: a burst draining quickly is healthy, and one
    message stuck for an hour means somebody has not been told something they
    were promised."""
    emit(db_session, topic="t", dedupe_key="old", tenant_id=tenant_id, clock=clock)
    db_session.commit()
    clock.advance(timedelta(hours=2))
    emit(db_session, topic="t", dedupe_key="new", tenant_id=tenant_id, clock=clock)
    db_session.commit()

    age = undelivered_age(db_session, clock=clock)
    assert timedelta(hours=1, minutes=59) <= age <= timedelta(hours=2, minutes=1), age


def test_nothing_pending_is_not_an_error(db_session, clock):
    assert relay(db_session, lambda _m: None, clock=clock) == (0, 0)
    assert pending(db_session, clock=clock) == []
