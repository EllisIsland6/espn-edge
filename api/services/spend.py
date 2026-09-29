"""UTC-month AI spend ledger: reserve before the call, settle after (Phase 31, unit 4).

WHY RESERVE/SETTLE RATHER THAN POST-HOC ACCOUNTING
--------------------------------------------------
Post-hoc accounting records what a call cost after it returns. It cannot bound
concurrency -- N calls in flight are all unaccounted until they finish -- and it
loses a crashed call's spend entirely, so a process that dies mid-call is
silently free. The contract's Selection is therefore: charge the worst case
before the call, reconcile to the actual after it, and record an explicit
`unknown_spent` when the response is lost.

The consequence to keep in mind while reading this file: nothing here can
"release" a reservation. A reservation that was never used still charged the
month. That is the fail-closed direction, and the alternative -- a release path
-- is exactly how a crash becomes free again.

WHY A COUNTER ROW RATHER THAN A SUM
-----------------------------------
`SELECT SUM(...)` followed by an `INSERT` is not a bound. Two processes both read
a total under the ceiling and both insert; neither was wrong when it looked, and
the ceiling is breached. The month's committed total is a single row updated by a
conditional `UPDATE ... WHERE committed + :amount <= :ceiling`, which is atomic
in SQLite and in every other engine this could run on, so the bound does not
depend on an isolation level the local SQLite file does not give us.

Every state transition in this file follows the same rule, because review found
the parts that did not: `_finish` used to SELECT the entry, test its state in
Python, and then write. Two concurrent settles of one reservation both passed
that test and both applied their delta, driving the month counter NEGATIVE; a
sweep racing a settle refunded a reservation the sweep had just declared
crashed. Both were measured. The transition is now the conditional UPDATE itself
and `rowcount` is the verdict, so the delta and the transition commit together or
not at all.

WHY INTEGER MICRO-DOLLARS
-------------------------
A ceiling compared against accumulated binary floating point is off by a
representation error some of the time, and "some of the time" is not a bound.
Every amount in this module is an integer number of micro-dollars (1 USD =
1_000_000). Dollars appear only at the boundary, in `usd()`.

WHAT THIS DOES NOT BOUND, STATED PLAINLY
----------------------------------------
The ledger's durability IS the bound. A full restore of the database to an
earlier point inside the same UTC month rewinds the counter, and no check inside
that database can tell: the evidence and the witness are restored together. The
cross-check in `_refuse_a_rewound_counter` catches every PARTIAL loss -- a
dropped or truncated counter row while the entries survive -- and a container on
an ephemeral volume is the same class of hole. See E31.5.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from ..ai_config import UnpricedModel, price_micro_usd_per_mtok
from ..models import AiSpendEntry, AiSpendMonth

MICRO_PER_USD = 1_000_000

# The contract's figure. A ceiling the ledger enforces, not a forecast.
CEILING_MICRO_USD = 5 * MICRO_PER_USD

# How long a write waits for another writer before failing closed. Long enough
# that ordinary contention resolves, short enough that a wedged writer degrades
# to cached-only rather than hanging a request.
_BUSY_TIMEOUT_MS = 5_000

STATE_RESERVED = "reserved"
STATE_SETTLED = "settled"
STATE_UNKNOWN = "unknown_spent"


class SpendLedgerError(RuntimeError):
    """Base for every ledger condition. Callers degrade to cached-only on this."""


class SpendCeilingExceeded(SpendLedgerError):
    """The reservation would take the UTC month past the ceiling."""


class SpendLedgerUnavailable(SpendLedgerError):
    """The ledger could not be consulted, or the model has no configured price.

    Both are the same thing to a caller: the spend bound cannot be established,
    so the call must not happen. An unpriced model is not a reason to guess a
    price -- it is a reason to stop.
    """


def usd(micro: int) -> float:
    """Micro-dollars to dollars, for display and evidence only. Never for math."""
    return micro / MICRO_PER_USD


def utc_month(now: datetime | None = None) -> str:
    """The ledger period. UTC, so the ceiling does not reset twice a year."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise SpendLedgerUnavailable("ledger period requires an aware datetime")
    return moment.astimezone(UTC).strftime("%Y-%m")


def _next_month(month: str) -> str:
    year, number = (int(part) for part in month.split("-"))
    return f"{year + 1}-01" if number == 12 else f"{year}-{number + 1:02d}"


@dataclass(frozen=True)
class TokenUsage:
    """What a completed call actually consumed, as reported by the provider."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Reservation:
    """A handle, not a lock. The charge is already recorded when this exists.

    Every field here is caller-held and therefore untrusted: `_finish` reads the
    month and the held amount back out of the stored row, never from this.
    """

    reservation: str
    month: str
    kind: str
    model: str
    reserved_micro_usd: int


def worst_case_micro_usd(model: str, input_tokens: int, max_output_tokens: int) -> int:
    """What the call could cost if the model emits its entire output budget.

    Reserving the expected cost would let a long response breach the ceiling
    between reserve and settle, so the reservation is the worst case and settle
    gives the difference back. Rounded UP, because a reservation that rounds down
    is a ceiling that can be stepped over one fraction at a time.
    """
    if input_tokens < 0:
        raise SpendLedgerUnavailable("input token count cannot be negative")
    if max_output_tokens < 0:
        raise SpendLedgerUnavailable("output token count cannot be negative")
    return _price(model, input_tokens, max_output_tokens)


def actual_micro_usd(model: str, usage: TokenUsage) -> int:
    """What the call really cost, from the provider's own token counts."""
    if usage.input_tokens < 0:
        raise SpendLedgerUnavailable("input token count cannot be negative")
    if usage.output_tokens < 0:
        raise SpendLedgerUnavailable("output token count cannot be negative")
    return _price(model, usage.input_tokens, usage.output_tokens)


def _price(model: str, input_tokens: int, output_tokens: int) -> int:
    try:
        rate_in, rate_out = price_micro_usd_per_mtok(model)
    except UnpricedModel as error:
        # An unpriced model is not a reason to guess a price. It is a reason to
        # stop, which the caller turns into cached-only generation.
        raise SpendLedgerUnavailable(str(error)) from error
    total = rate_in * input_tokens + rate_out * output_tokens
    return -(-total // 1_000_000)  # ceil division, integers only


class SpendLedger:
    """Reserve/settle over its own short-lived session.

    It does NOT use the caller's session. Review demonstrated the damage: the
    ledger's `commit()` published a request's half-finished rows and its
    `rollback()` on a ceiling refusal destroyed an `AiReport` the same request
    had already generated and flushed. The ledger needs its own transaction
    anyway -- committing the charge before the model call is the entire design --
    so it opens one and leaves the caller's alone.
    """

    def __init__(
        self,
        session: Session | None = None,
        ceiling_micro_usd: int = CEILING_MICRO_USD,
        *,
        session_factory=None,
    ):
        # `session` is accepted so tests can inspect the same rows they wrote,
        # and so a caller that has already opened one does not pay for a second
        # connection. Production passes a factory.
        self._session = session
        self._owns_session = session is None
        self._session_factory = session_factory
        self.ceiling = ceiling_micro_usd

    def __enter__(self) -> SpendLedger:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        """Release a session this ledger opened. A borrowed one is left alone."""
        if self._owns_session and self._session is not None:
            try:
                self._session.close()
            except BaseException:
                pass
            self._session = None

    @property
    def session(self) -> Session:
        if self._session is None:
            if self._session_factory is None:
                from ..db import SessionLocal

                self._session_factory = SessionLocal
            self._session = self._session_factory()
        return self._session

    # ---- failure containment ----------------------------------------------
    def _guarded(self, what: str, work):
        """Run `work`, converting ANY failure into a `SpendLedgerError`.

        The class docstring's promise -- that a caller catching
        `SpendLedgerError` has caught everything the ledger can do to it -- was
        not true: `pysqlite` raises a bare `OverflowError` for an integer too
        large for SQLite, which is not a DBAPI error, so SQLAlchemy does not wrap
        it and it escaped as a 500 instead of degrading to cached-only. Anything
        that is not already a ledger error becomes one, so the promise holds for
        every failure rather than for the ones that were anticipated.
        """
        try:
            return work()
        except SpendLedgerError:
            self._safe_rollback()
            raise
        except BaseException as error:
            self._safe_rollback()
            raise SpendLedgerUnavailable(f"spend ledger {what} failed") from error

    def _safe_rollback(self) -> None:
        try:
            self.session.rollback()
        except BaseException:  # a rollback that fails must not mask the cause
            pass

    def _wait_for_the_write_lock(self) -> None:
        """Let SQLite wait for the writer rather than refusing immediately.

        Without this, concurrent statements get "database is locked" and the
        ledger fails closed -- safe, but it turns every burst of parallel
        generation into cached-only. It must run BEFORE the first statement of
        any operation: it used to run after `reserve`'s period check, leaving
        that SELECT unprotected, which made this module's own concurrency gate
        fail 14 runs in 20. Set on the ledger's own connection, so no global
        engine behaviour changes with it.
        """
        try:
            self.session.execute(text(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}"))
        except BaseException:
            # Not SQLite, or the pragma is unavailable. The bound does not depend
            # on it, so this is a latency property that may be skipped.
            pass

    # ---- reads -------------------------------------------------------------
    def committed_micro_usd(self, month: str) -> int:
        def work():
            row = self._month_row(month)
            return 0 if row is None else int(row.committed_micro_usd)

        return self._guarded("read", work)

    def remaining_micro_usd(self, month: str) -> int:
        return max(0, self.ceiling - self.committed_micro_usd(month))

    def entry(self, reservation: str) -> AiSpendEntry | None:
        return self.session.scalar(
            select(AiSpendEntry).where(AiSpendEntry.reservation == reservation)
        )

    def _month_row(self, month: str) -> AiSpendMonth | None:
        return self.session.scalar(select(AiSpendMonth).where(AiSpendMonth.month == month))

    def _charged_by_entries(self, month: str) -> int:
        """What the audit trail says the month holds, independent of the counter."""
        total = self.session.scalar(
            select(
                func.sum(
                    func.coalesce(
                        AiSpendEntry.settled_micro_usd, AiSpendEntry.reserved_micro_usd
                    )
                )
            ).where(AiSpendEntry.month == month)
        )
        return int(total or 0)

    # ---- reserve -----------------------------------------------------------
    def reserve(
        self,
        *,
        kind: str,
        model: str,
        input_tokens: int,
        max_output_tokens: int,
        now: datetime | None = None,
    ) -> Reservation:
        """Charge the worst case, then return a handle. Raises before any spend."""
        month = utc_month(now)
        amount = worst_case_micro_usd(model, input_tokens, max_output_tokens)
        if amount <= 0:
            raise SpendLedgerUnavailable("a reservation must charge something")

        def work():
            # The pragma comes FIRST, before any statement, or the statements
            # before it are the ones that fail under contention.
            self._wait_for_the_write_lock()
            self._refuse_a_period_outside_the_ledgers_own_range(month)
            # The first statement that takes the lock must be a WRITE.
            #
            # SQLite refuses to promote a transaction that has already read into
            # a writer -- promotion could deadlock, so it returns SQLITE_BUSY
            # immediately and does NOT honour busy_timeout. This no-op update is
            # that write: it acquires the lock and tells us whether the month row
            # exists, in one statement and on any engine.
            touched = self.session.execute(
                update(AiSpendMonth)
                .where(AiSpendMonth.month == month)
                .values(updated_at=AiSpendMonth.updated_at)
            ).rowcount
            if not touched:
                # The lock is already held, so no second writer can race the
                # insert: it is waiting on this transaction and will find the row.
                self.session.add(AiSpendMonth(month=month, committed_micro_usd=0))
                self.session.flush()
            # Inside the write lock, and only inside it. Read outside, this
            # check compared a stale counter against a freshly committed entries
            # sum and refused 20 of 40 concurrent reservations as "rewound" --
            # a check firing without establishing what it claimed, in the very
            # function whose comments are about not doing that.
            self._refuse_a_rewound_counter(month)
            charged = self.session.execute(
                update(AiSpendMonth)
                .where(
                    AiSpendMonth.month == month,
                    AiSpendMonth.committed_micro_usd + amount <= self.ceiling,
                )
                .values(committed_micro_usd=AiSpendMonth.committed_micro_usd + amount)
            ).rowcount
            if not charged:
                # Deliberately not a retry and not a queue: the month is spent,
                # and a retry loop against a monthly ceiling is a busy-wait until
                # the calendar changes.
                raise SpendCeilingExceeded(
                    f"UTC month {month} would exceed the ${usd(self.ceiling):.2f} ceiling"
                )
            handle = Reservation(
                reservation=uuid.uuid4().hex,
                month=month,
                kind=kind,
                model=model,
                reserved_micro_usd=amount,
            )
            self.session.add(
                AiSpendEntry(
                    reservation=handle.reservation,
                    month=month,
                    kind=kind,
                    model=model,
                    state=STATE_RESERVED,
                    reserved_micro_usd=amount,
                )
            )
            self.session.commit()
            return handle

        return self._guarded("write", work)

    def _refuse_a_period_outside_the_ledgers_own_range(self, month: str) -> None:
        """Periods move forward one month at a time, and never backwards.

        The period comes from the host clock, so a clock that slips to last month
        gets a fresh $5 with last month's ledger sitting right there saying it
        was spent. The forward direction is the one that GRANTS budget, and the
        first version of this guard defended only backwards: a clock jumping to
        2099 minted a period, and a fabricated far-future period then refused
        every real month forever. Months only ever advance by one, so both ends
        are bounded by the ledger's own highest period.
        """
        latest = self.session.scalar(
            select(AiSpendMonth.month).order_by(AiSpendMonth.month.desc()).limit(1)
        )
        if latest is None:
            return
        if month < latest:
            raise SpendLedgerUnavailable(
                f"ledger period {month} precedes {latest}; the clock moved backwards"
            )
        if month > _next_month(latest):
            raise SpendLedgerUnavailable(
                f"ledger period {month} is more than one month after {latest}; "
                "the clock moved forward"
            )

    def _refuse_a_rewound_counter(self, month: str) -> None:
        """The audit trail is a second witness to what the month already holds.

        The counter row is the bound, so anything that rewinds or removes it
        hands back budget: a truncated table, a partial restore, a hand-edited
        row. The entries are written in the same transaction as every charge, so
        in a consistent read the counter equals the sum of each entry's settled
        amount (or its hold, where none was settled); a counter BELOW that sum is
        evidence the counter alone was rewound, and costs one indexed sum to see.
        It must be read under the write lock, or ordinary concurrency looks
        identical to a rewind. It cannot see a FULL restore, where witness and
        evidence are rewound together -- that residual is stated in the module
        docstring and in E31.5 rather than implied away.
        """
        row = self._month_row(month)
        committed = 0 if row is None else int(row.committed_micro_usd)
        charged = self._charged_by_entries(month)
        if committed < charged:
            raise SpendLedgerUnavailable(
                f"ledger counter for {month} is behind its own entries "
                f"({committed} < {charged}); the ledger has been rewound"
            )

    # ---- terminal states ---------------------------------------------------
    def settle(self, handle: Reservation, actual_micro_usd: int) -> AiSpendEntry:
        """Reconcile to the real cost, giving back the unused part of the charge.

        An overrun is recorded rather than prevented: the tokens are already
        spent by the time anyone knows the number, so refusing it here would only
        make the ledger disagree with reality.

        The honest bound, which an earlier version of this docstring stated
        wrongly as "at most one call's overrun": the month can end up over the
        ceiling by the SUM of the overruns of every call that was in flight when
        the ceiling filled, because each of those calls was admitted against the
        ceiling and each can settle above its hold. Review measured 50 such calls
        settling at 4x their hold, ending at $20 on a $5 ceiling. Nothing here
        caps concurrency; that cap is recorded as an open item in E31.5 rather
        than implied away by a bound this code does not enforce.

        A non-positive actual is NOT a refund. A provider report of zero tokens
        is an unusable cost report, not a free call, and treating it as a refund
        returned the entire hold: a client reporting `TokenUsage(0, 0)` -- one SDK
        field rename away -- zeroed the month on every call and the ceiling
        stopped bounding anything. An unusable report is the same epistemic state
        as a lost response, so it gets the same handling.
        """
        if actual_micro_usd < 0:
            raise SpendLedgerUnavailable("a settled amount cannot be negative")
        if actual_micro_usd == 0:
            return self.record_unknown_spent(handle)
        return self._finish(handle, STATE_SETTLED, actual_micro_usd)

    def record_unknown_spent(self, handle: Reservation) -> AiSpendEntry:
        """The response was lost, so the spend is unknown and stays charged in full.

        Assuming the worst case is the only safe assumption available: the call
        left the process, and a provider that billed it will not ask again.
        """
        return self._finish(handle, STATE_UNKNOWN, None)

    def _finish(
        self, handle: Reservation, state: str, actual_micro_usd: int | None
    ) -> AiSpendEntry:
        def work():
            self._wait_for_the_write_lock()
            # The state transition IS the concurrency control. Reading the row,
            # testing its state in Python and then writing let two concurrent
            # settles both pass the test and both apply their delta -- measured
            # driving the month counter negative -- and let a sweep and a settle
            # each believe they owned the same reservation.
            claimed = self.session.execute(
                update(AiSpendEntry)
                .where(
                    AiSpendEntry.reservation == handle.reservation,
                    AiSpendEntry.state == STATE_RESERVED,
                )
                .values(state=state, updated_at=datetime.now(UTC))
            ).rowcount
            if not claimed:
                row = self.entry(handle.reservation)
                if row is None:
                    raise SpendLedgerUnavailable("no such reservation")
                raise SpendLedgerUnavailable(
                    f"reservation is already {row.state}, not {STATE_RESERVED}"
                )
            row = self.entry(handle.reservation)
            if actual_micro_usd is not None:
                # `row`, never `handle`: every field of the handle is caller-held.
                delta = actual_micro_usd - int(row.reserved_micro_usd)
                if delta:
                    moved = self.session.execute(
                        update(AiSpendMonth)
                        .where(AiSpendMonth.month == row.month)
                        .values(
                            committed_micro_usd=AiSpendMonth.committed_micro_usd + delta
                        )
                    ).rowcount
                    if not moved:
                        # The month row is gone. Ignoring the rowcount silently
                        # discarded an overrun while still marking the entry
                        # settled, so the spend was recorded as reconciled
                        # against a counter that no longer existed.
                        raise SpendLedgerUnavailable(
                            f"no ledger counter for {row.month}; the ledger has been rewound"
                        )
                row.settled_micro_usd = actual_micro_usd
            self.session.commit()
            return row

        return self._guarded("write", work)

    # ---- crashed reservations ---------------------------------------------
    def sweep_stale(self, older_than: datetime) -> list[str]:
        """Mark reservations a dead process left behind, WITHOUT refunding them.

        The month was charged at reserve time, so a crash is already accounted
        for; this only makes the audit trail say so rather than leaving a row
        that reads as an in-flight call forever. Refunding here is the one change
        that would make a crash free, so it is not made.

        `older_than` must be timezone-aware. It used to accept anything, and
        SQLite stores `created_at` naive, so a cutoff in another zone was
        compared against UTC values with its offset silently discarded -- a
        cutoff in UTC+14 swept a LIVE reservation, whose settle was then refused
        and whose month stayed charged in full forever.
        """
        if older_than.tzinfo is None:
            raise SpendLedgerUnavailable("a sweep cutoff requires an aware datetime")
        cutoff = older_than.astimezone(UTC).replace(tzinfo=None)

        def work():
            self._wait_for_the_write_lock()
            # This filter narrows the candidate set; it is NOT the control.
            # Removing it is behaviour-preserving, because the per-row
            # conditional UPDATE below carries the same predicate and is what
            # actually decides. Recorded so the surviving mutation is not read
            # as an untested line.
            rows = list(
                self.session.scalars(
                    select(AiSpendEntry).where(
                        AiSpendEntry.state == STATE_RESERVED,
                        AiSpendEntry.created_at < cutoff,
                    )
                )
            )
            swept = []
            for row in rows:
                claimed = self.session.execute(
                    update(AiSpendEntry)
                    .where(
                        AiSpendEntry.id == row.id,
                        AiSpendEntry.state == STATE_RESERVED,
                    )
                    .values(state=STATE_UNKNOWN, updated_at=datetime.now(UTC))
                ).rowcount
                if claimed:
                    swept.append(row.reservation)
            self.session.commit()
            return swept

        return self._guarded("sweep", work)
