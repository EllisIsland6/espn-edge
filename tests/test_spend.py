"""Phase 31 unit 4 — the UTC-month AI spend ledger (criteria 6, 7, 8).

The three things this file has to establish, because the contract says so:

  6. Usage is persisted for success, lost response, and crashed reservation.
  7. Parallel reservations cannot exceed the $5 UTC-month ceiling.
  8. Ledger failure and ceiling breach both yield cached-only generation with no
     queued retry.

Every ceiling test asserts the committed total as an integer. There is no
tolerance anywhere in this file: a ceiling that holds to within an epsilon is
not a ceiling.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from api.ai_config import UnpricedModel, price_micro_usd_per_mtok
from api.config import get_settings
from api.db import Base
from api.models import AiSpendEntry, AiSpendMonth
from api.services.spend import (
    CEILING_MICRO_USD,
    MICRO_PER_USD,
    STATE_RESERVED,
    STATE_SETTLED,
    STATE_UNKNOWN,
    SpendCeilingExceeded,
    SpendLedger,
    SpendLedgerError,
    SpendLedgerUnavailable,
    TokenUsage,
    actual_micro_usd,
    usd,
    utc_month,
    worst_case_micro_usd,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL = "test-model"
# 1 micro-USD per million input tokens, 2 per million output. Chosen so the
# arithmetic in these tests is checkable by hand.
CHEAP = "cheap-model"
# `CHEAP` is priced at 1 micro-USD per MILLION tokens, so a single token costs a
# millionth of a micro-dollar. It exists to make ceil and floor differ: at MODEL's
# round rate they agree, so a rounding test written only against MODEL proves
# nothing about rounding.
RATES = {MODEL: (1_000_000, 2_000_000), CHEAP: (1, 1)}


@pytest.fixture
def priced(monkeypatch):
    """Configure model prices, which is what switches the bound on."""
    settings = get_settings()
    monkeypatch.setattr(
        settings, "ai_price_micro_usd_per_mtok", RATES, raising=False
    )
    yield settings


@pytest.fixture
def ledger(db_session, priced):
    return SpendLedger(db_session)


def _reserve(ledger, *, tokens=1_000_000, out=0, kind="draft_recap", now=None):
    return ledger.reserve(
        kind=kind, model=MODEL, input_tokens=tokens, max_output_tokens=out, now=now
    )


# --------------------------------------------------------------- pricing


def test_there_is_no_default_price_table(monkeypatch):
    """A default rate would make the ceiling arithmetic over a guess.

    The number would have been invented in this repository and then trusted by a
    control, which reads as enforcement while bounding nothing.
    """
    monkeypatch.setattr(get_settings(), "ai_price_micro_usd_per_mtok", {}, raising=False)
    with pytest.raises(UnpricedModel):
        price_micro_usd_per_mtok("claude-sonnet-5")


def test_an_unpriced_model_stops_the_call_rather_than_guessing(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_price_micro_usd_per_mtok", {}, raising=False)
    with pytest.raises(SpendLedgerUnavailable):
        SpendLedger(db_session).reserve(
            kind="draft_recap", model=MODEL, input_tokens=10, max_output_tokens=10
        )


@pytest.mark.parametrize("rates", [{MODEL: (1,)}, {MODEL: "cheap"}, {MODEL: 5}])
def test_a_malformed_rate_cannot_even_be_configured(rates):
    """`Settings` validates on assignment, so a malformed rate is refused before
    the ledger ever sees it. Asserted rather than assumed, because the lookup's
    own guard below is written as if this were not true."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        get_settings().ai_price_micro_usd_per_mtok = rates


@pytest.mark.parametrize("rate", [(-1, 1), (1,), "cheap", None, 5])
def test_the_lookup_guards_a_malformed_rate_anyway(monkeypatch, rate):
    """Defence in depth: settings validation is one parser away from the env, and
    a rate that reached the lookup malformed must stop the call, not be coerced
    into a number the ceiling then trusts."""

    class _Stub:
        ai_price_micro_usd_per_mtok = {MODEL: rate}

    monkeypatch.setattr("api.config.get_settings", lambda: _Stub())
    with pytest.raises(UnpricedModel):
        price_micro_usd_per_mtok(MODEL)


def test_the_worst_case_rounds_up(priced):
    """Rounding a reservation down is a ceiling that can be stepped over one
    fraction at a time."""
    # A millionth of a micro-dollar still charges one: floor would charge zero,
    # and a zero charge is a call outside the ceiling.
    assert worst_case_micro_usd(CHEAP, 1, 0) == 1
    assert worst_case_micro_usd(CHEAP, 1_000_000, 0) == 1
    assert worst_case_micro_usd(CHEAP, 1_000_001, 0) == 2
    assert worst_case_micro_usd(MODEL, 1, 0) == 1
    assert worst_case_micro_usd(MODEL, 1_000_000, 0) == 1_000_000
    assert worst_case_micro_usd(MODEL, 0, 1_000_000) == 2_000_000
    assert isinstance(worst_case_micro_usd(MODEL, 3, 7), int)


def test_negative_token_counts_are_refused(priced):
    with pytest.raises(SpendLedgerUnavailable):
        worst_case_micro_usd(MODEL, -1, 0)
    with pytest.raises(SpendLedgerUnavailable):
        actual_micro_usd(MODEL, TokenUsage(input_tokens=0, output_tokens=-1))


# ------------------------------------------------------------ the period


def test_the_period_is_utc_not_local():
    """A local-time period resets the ceiling twice a year and is ambiguous for
    an hour each autumn."""
    late = datetime(2026, 9, 30, 23, 30, tzinfo=timezone(timedelta(hours=-7)))
    assert utc_month(late) == "2026-10"
    assert utc_month(late.astimezone(UTC)) == "2026-10"


def test_a_naive_datetime_is_refused_rather_than_assumed_utc():
    with pytest.raises(SpendLedgerUnavailable):
        utc_month(datetime(2026, 9, 30, 23, 30))


def test_reservations_land_in_the_month_they_belong_to(ledger):
    _reserve(ledger, now=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    _reserve(ledger, now=datetime(2026, 10, 1, 0, 30, tzinfo=UTC))
    assert ledger.committed_micro_usd("2026-09") == 1_000_000
    assert ledger.committed_micro_usd("2026-10") == 1_000_000


# ----------------------------------------- criterion 6: three persisted cases


def test_a_successful_call_settles_to_the_real_cost(ledger, db_session):
    handle = _reserve(ledger, tokens=1_000_000, out=1_000_000)
    assert handle.reserved_micro_usd == 3_000_000
    assert ledger.committed_micro_usd(handle.month) == 3_000_000

    row = ledger.settle(handle, actual_micro_usd(MODEL, TokenUsage(500_000, 100_000)))
    assert row.state == STATE_SETTLED
    assert row.settled_micro_usd == 700_000
    # The unused part of the hold is given back, so one long-prompt call does not
    # consume the month.
    assert ledger.committed_micro_usd(handle.month) == 700_000


def test_a_lost_response_stays_charged_in_full_and_says_so(ledger):
    handle = _reserve(ledger, tokens=2_000_000)
    row = ledger.record_unknown_spent(handle)
    assert row.state == STATE_UNKNOWN
    assert row.settled_micro_usd is None
    # The call left the process. A provider that billed it will not ask again,
    # so the worst case stands.
    assert ledger.committed_micro_usd(handle.month) == 2_000_000


def test_a_crashed_reservation_is_never_free(ledger, db_session):
    """The charge is recorded before the model call begins.

    A process that dies between reserve and settle leaves the month charged,
    which is the whole reason the reservation comes first.
    """
    handle = _reserve(ledger, tokens=2_000_000)
    db_session.expire_all()  # as a fresh process would see it
    assert ledger.committed_micro_usd(handle.month) == 2_000_000
    assert ledger.entry(handle.reservation).state == STATE_RESERVED

    swept = ledger.sweep_stale(datetime.now(UTC) + timedelta(seconds=1))
    assert swept == [handle.reservation]
    assert ledger.entry(handle.reservation).state == STATE_UNKNOWN
    # Sweeping makes the audit trail truthful. It must not make the crash free.
    assert ledger.committed_micro_usd(handle.month) == 2_000_000


def test_the_sweep_leaves_live_reservations_alone(ledger):
    handle = _reserve(ledger)
    assert ledger.sweep_stale(datetime.now(UTC) - timedelta(hours=1)) == []
    assert ledger.entry(handle.reservation).state == STATE_RESERVED


def test_all_three_states_are_persisted_and_distinguishable(ledger, db_session):
    settled = _reserve(ledger)
    ledger.settle(settled, 1)
    unknown = _reserve(ledger)
    ledger.record_unknown_spent(unknown)
    crashed = _reserve(ledger)
    rows = {row.reservation: row for row in db_session.scalars(select(AiSpendEntry))}
    assert {r: rows[r].state for r in rows} == {
        settled.reservation: STATE_SETTLED,
        unknown.reservation: STATE_UNKNOWN,
        crashed.reservation: STATE_RESERVED,
    }
    # Asserting three state strings would hold for a ledger that had stopped
    # accounting entirely, so the amounts and the month counter are asserted too.
    assert rows[settled.reservation].settled_micro_usd == 1
    assert rows[unknown.reservation].settled_micro_usd is None
    assert rows[crashed.reservation].settled_micro_usd is None
    held = rows[unknown.reservation].reserved_micro_usd
    assert ledger.committed_micro_usd(utc_month()) == 1 + held + held


def test_settling_twice_is_refused(ledger):
    """Applying the delta twice would decrement the month below what was spent."""
    handle = _reserve(ledger)
    ledger.settle(handle, 1)
    with pytest.raises(SpendLedgerUnavailable):
        ledger.settle(handle, 1)


def test_a_negative_settled_amount_is_refused(ledger):
    """A negative settle is a refund of spend that happened."""
    handle = _reserve(ledger, tokens=1_000_000)
    with pytest.raises(SpendLedgerUnavailable):
        ledger.settle(handle, -1)
    assert ledger.committed_micro_usd(handle.month) == 1_000_000
    assert ledger.entry(handle.reservation).state == STATE_RESERVED


def test_an_unknown_reservation_cannot_be_settled(ledger):
    handle = _reserve(ledger)
    forged = type(handle)(
        reservation="not-a-real-reservation",
        month=handle.month,
        kind=handle.kind,
        model=handle.model,
        reserved_micro_usd=handle.reserved_micro_usd,
    )
    with pytest.raises(SpendLedgerUnavailable):
        ledger.settle(forged, 1)


def test_an_overrun_is_recorded_and_then_blocks_the_next_call(ledger):
    """The tokens are already spent by the time anyone knows the number, so
    refusing the settle would only make the ledger disagree with reality."""
    handle = _reserve(ledger, tokens=1_000_000)
    ledger.settle(handle, CEILING_MICRO_USD + 1)
    assert ledger.committed_micro_usd(handle.month) == CEILING_MICRO_USD + 1
    with pytest.raises(SpendCeilingExceeded):
        _reserve(ledger, tokens=1)


# ------------------------------------ criterion 7: the ceiling under parallelism


def test_a_reservation_that_would_breach_the_ceiling_is_refused(ledger):
    _reserve(ledger, tokens=5_000_000)
    assert ledger.committed_micro_usd(utc_month()) == CEILING_MICRO_USD
    assert ledger.remaining_micro_usd(utc_month()) == 0
    with pytest.raises(SpendCeilingExceeded):
        _reserve(ledger, tokens=1)


def test_the_ceiling_may_be_reached_exactly(ledger):
    _reserve(ledger, tokens=4_999_999)
    _reserve(ledger, tokens=1)
    assert ledger.committed_micro_usd(utc_month()) == CEILING_MICRO_USD


def test_parallel_reservations_cannot_exceed_the_ceiling(tmp_path, priced):
    """Real threads against one SQLite file, not a simulation.

    `SELECT SUM(...)` then `INSERT` is the obvious implementation and it is not a
    bound: two workers both read a total under the ceiling, both insert, and
    neither was wrong when it looked. The conditional `UPDATE ... WHERE
    committed + amount <= ceiling` is what makes the arithmetic atomic, and this
    is the test that would fail if it were replaced by a read and a write.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'spend.db'}",
        # The driver waits zero seconds, so the ledger's own `PRAGMA
        # busy_timeout` is the only thing standing between 40 concurrent writers
        # and "database is locked". Remove that pragma and this test fails.
        connect_args={"check_same_thread": False, "timeout": 0},
    )
    Base.metadata.create_all(engine)
    Sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    each = 250_000  # exactly 20 reservations fit under the $5 ceiling
    workers, granted, refused = 40, [], []
    barrier = threading.Barrier(workers)
    lock = threading.Lock()

    def attempt():
        session = Sessions()
        try:
            barrier.wait(timeout=30)
            handle = SpendLedger(session).reserve(
                kind="draft_recap", model=MODEL, input_tokens=each, max_output_tokens=0
            )
            with lock:
                granted.append(handle)
        except SpendCeilingExceeded:
            with lock:
                refused.append("ceiling")
        except SpendLedgerError as error:  # contention, which must fail closed
            with lock:
                refused.append(type(error).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=attempt) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    audit = Sessions()
    try:
        committed = audit.scalar(
            select(AiSpendMonth.committed_micro_usd).where(
                AiSpendMonth.month == utc_month()
            )
        )
        entries = list(audit.scalars(select(AiSpendEntry)))
    finally:
        audit.close()
        engine.dispose()

    assert len(granted) + len(refused) == workers, "every worker must reach a verdict"
    # The bound itself, stated three ways: the total never passes the ceiling, it
    # is exactly the granted reservations and nothing else, and a refusal leaves
    # no row behind to be settled later.
    assert committed <= CEILING_MICRO_USD
    assert committed == len(granted) * each
    assert len(entries) == len(granted), "a refused reservation must leave no entry"
    # Every refusal must be the ceiling. A lock timeout would also be safe, but it
    # would mean this run never actually pushed against the ceiling, and the
    # assertions above would hold for a ledger that simply granted less.
    assert set(refused) == {"ceiling"}, sorted(set(refused))
    assert len(granted) == CEILING_MICRO_USD // each == 20


def test_one_month_reaching_the_ceiling_does_not_block_another(ledger):
    september = datetime(2026, 9, 15, tzinfo=UTC)
    october = datetime(2026, 10, 1, tzinfo=UTC)
    _reserve(ledger, tokens=5_000_000, now=september)
    with pytest.raises(SpendCeilingExceeded):
        _reserve(ledger, tokens=1, now=september)
    _reserve(ledger, tokens=1, now=october)
    assert ledger.committed_micro_usd("2026-10") == 1


def test_a_zero_cost_reservation_is_refused(ledger):
    """A reservation that charges nothing is a call outside the ceiling."""
    with pytest.raises(SpendLedgerUnavailable):
        _reserve(ledger, tokens=0, out=0)


# ------------------------------------------------------------ reporting


def test_dollars_appear_only_at_the_boundary():
    assert usd(CEILING_MICRO_USD) == 5.0
    assert CEILING_MICRO_USD == 5 * MICRO_PER_USD
    assert isinstance(CEILING_MICRO_USD, int)


def test_a_ledger_read_failure_is_a_ledger_error_not_a_stray_exception(ledger, monkeypatch):
    """Every caller catches `SpendLedgerError`; anything else escapes the
    degrade-to-cached-only path entirely."""
    from sqlalchemy.exc import OperationalError

    def boom(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("disk I/O error"))

    monkeypatch.setattr(ledger.session, "scalar", boom)
    with pytest.raises(SpendLedgerError):
        ledger.committed_micro_usd(utc_month())


# ---------------------------- the ledger survives a restore, by design


def test_the_ledger_tables_are_retained_by_the_recovery_bundle():
    """A restore that dropped them would reset the month's committed total, so
    "restore from backup" would become a way to clear the spend ceiling."""
    from api.services.recovery import EXPECTED_TABLE_COLUMNS

    retained = set(EXPECTED_TABLE_COLUMNS) - {"raw_cache"}
    assert {"ai_spend_months", "ai_spend_entries"} <= retained


# ===================== criterion 8: what a refused call does ==================
# "Ledger failure and ceiling breach both yield cached-only generation with no
# queued retry." The two halves are tested separately, because they reach the
# same outcome by different routes and a fix for one has repeatedly not been a
# fix for the other.

from api.ai_config import KIND_DRAFT_RECAP  # noqa: E402
from api.models import AiReport, League  # noqa: E402
from api.services.ai import (  # noqa: E402
    CACHED_ONLY_KEY,
    AiService,
    AiSpendBlockedError,
)


class RecordingClient:
    """Counts calls, so "the model was not called" is asserted, not assumed."""

    def __init__(self, usage: TokenUsage | None = None, explode: Exception | None = None):
        self.calls = 0
        self.usage = usage
        self.explode = explode
        self.last_usage: TokenUsage | None = None

    def complete_json(self, *, model, system, user, schema, max_tokens, reservation=None):
        self.calls += 1
        if self.explode is not None:
            raise self.explode
        self.last_usage = self.usage
        return {
            "strategy_label": "Balanced/BPA",
            "grade": "B",
            "confidence": "medium",
            "summary": "Balanced build.",
            "key_values": [],
            "key_reaches": [],
        }


@pytest.fixture
def league(db_session):
    row = League(espn_league_id="911", season=2026, is_public=True)
    db_session.add(row)
    db_session.flush()
    return row


def _exhaust_the_month(session):
    """Consume whatever is left of the ceiling, whatever earlier calls took.

    A fixed filler amount silently stopped exhausting the month as soon as a
    warm-up call held part of it, which would have left the "ceiling breach"
    tests passing for a reason other than a breach.
    """
    ledger = SpendLedger(session)
    remaining = ledger.remaining_micro_usd(utc_month())
    assert remaining > 0, "nothing left to exhaust; the test is not testing a breach"
    ledger.reserve(kind="filler", model=MODEL, input_tokens=remaining, max_output_tokens=0)
    assert ledger.remaining_micro_usd(utc_month()) == 0


def _generate(service, league_id, facts=None):
    return service.generate(
        kind=KIND_DRAFT_RECAP,
        scope="league",
        league_id=league_id,
        model=MODEL,
        facts=facts if facts is not None else {"picks": []},
    )


def test_private_operator_with_no_prices_is_unchanged(db_session, league, monkeypatch):
    """The contract says private-operator behaviour is unchanged.

    A ledger with no configured rate can only refuse, so enforcing it by default
    would have turned "unchanged" into "AI turned off".
    """
    monkeypatch.setattr(get_settings(), "ai_price_micro_usd_per_mtok", {}, raising=False)
    client = RecordingClient()
    assert _generate(AiService(db_session, client), league.id)["grade"] == "B"
    assert client.calls == 1
    assert db_session.scalars(select(AiSpendEntry)).all() == []


def test_configuring_a_price_opts_the_operator_into_their_own_ceiling(
    db_session, league, priced
):
    client = RecordingClient(usage=TokenUsage(input_tokens=1000, output_tokens=500))
    _generate(AiService(db_session, client), league.id)
    entry = db_session.scalars(select(AiSpendEntry)).one()
    assert entry.state == STATE_SETTLED
    assert entry.settled_micro_usd == actual_micro_usd(MODEL, client.usage)
    assert entry.settled_micro_usd < entry.reserved_micro_usd, (
        "the worst-case hold must be reconciled downward, or settle is decorative"
    )


def test_a_client_that_reports_no_usage_settles_at_the_hold(db_session, league, priced):
    """There is nothing to reconcile against, so the conservative charge stands
    and the row says it was never refined -- rather than implying a measurement
    that did not happen."""
    client = RecordingClient(usage=None)
    _generate(AiService(db_session, client), league.id)
    entry = db_session.scalars(select(AiSpendEntry)).one()
    assert entry.state == STATE_SETTLED
    assert entry.settled_micro_usd == entry.reserved_micro_usd


def test_one_call_cannot_settle_against_another_calls_token_counts(
    db_session, league, priced
):
    """A client that reports usage only sometimes would otherwise leave the
    previous call's counts in place, and the second call would settle against
    numbers that belong to the first."""

    class ReportsOnlyOnce(RecordingClient):
        """Sets `last_usage` on its first call and never touches it again.

        This is the shape that matters: the stale value is left in place by the
        CLIENT, so only the caller clearing it before each call can prevent the
        second settle from using the first call's numbers.
        """

        def complete_json(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                self.last_usage = self.usage
            return {
                "strategy_label": "Balanced/BPA", "grade": "B", "confidence": "medium",
                "summary": "Balanced build.", "key_values": [], "key_reaches": [],
            }

    client = ReportsOnlyOnce(usage=TokenUsage(input_tokens=10, output_tokens=10))
    service = AiService(db_session, client)
    _generate(service, league.id, facts={"picks": [1]})
    _generate(service, league.id, facts={"picks": [2]})
    entries = sorted(
        db_session.scalars(select(AiSpendEntry)).all(), key=lambda row: row.id
    )
    assert len(entries) == 2
    assert entries[0].settled_micro_usd == actual_micro_usd(MODEL, client.usage)
    assert entries[1].settled_micro_usd == entries[1].reserved_micro_usd, (
        "the second call must fall back to its own hold, not reuse the first "
        "call's token counts"
    )


def test_a_lost_response_leaves_the_month_charged_and_the_error_intact(
    db_session, league, priced
):
    from api.services.ai import AiError

    client = RecordingClient(explode=AiError("model call failed"))
    service = AiService(db_session, client)
    with pytest.raises(AiError):
        _generate(service, league.id)
    entry = db_session.scalars(select(AiSpendEntry)).one()
    assert entry.state == STATE_UNKNOWN
    assert SpendLedger(db_session).committed_micro_usd(utc_month()) == entry.reserved_micro_usd


def test_a_ceiling_breach_serves_the_cache_and_does_not_call_the_model(
    db_session, league, priced
):
    warm = RecordingClient(usage=TokenUsage(10, 10))
    first = _generate(AiService(db_session, warm), league.id)
    assert warm.calls == 1

    _exhaust_the_month(db_session)
    cold = RecordingClient(usage=TokenUsage(10, 10))
    # `force=True` alone did not establish this: both of these tests passed
    # against a `generate` that ignored `force` entirely, so the ordinary cache
    # could have been what answered. The trip indicator is what distinguishes
    # the two, and `test_force_really_regenerates` pins the premise.
    served = AiService(db_session, cold).generate(
        kind=KIND_DRAFT_RECAP, scope="league", league_id=league.id,
        model=MODEL, facts={"picks": []}, force=True,
    )
    assert served[CACHED_ONLY_KEY] == "exact"
    assert {k: v for k, v in served.items() if k != CACHED_ONLY_KEY} == first
    assert cold.calls == 0


def test_a_ledger_failure_serves_the_cache_and_does_not_call_the_model(
    db_session, league, priced, monkeypatch
):
    warm = RecordingClient(usage=TokenUsage(10, 10))
    first = _generate(AiService(db_session, warm), league.id)

    def unavailable(*_args, **_kwargs):
        raise SpendLedgerUnavailable("ledger is down")

    monkeypatch.setattr(SpendLedger, "reserve", unavailable)
    cold = RecordingClient(usage=TokenUsage(10, 10))
    served = AiService(db_session, cold).generate(
        kind=KIND_DRAFT_RECAP, scope="league", league_id=league.id,
        model=MODEL, facts={"picks": []}, force=True,
    )
    assert served[CACHED_ONLY_KEY] == "exact"
    assert {k: v for k, v in served.items() if k != CACHED_ONLY_KEY} == first
    assert cold.calls == 0


def test_a_refused_call_with_nothing_cached_fails_rather_than_queueing(
    db_session, league, priced
):
    """No retry and no queue. A retry against a monthly ceiling is a busy-wait
    until the calendar changes; a queue is an unbounded backlog of calls that
    were refused for cost."""
    _exhaust_the_month(db_session)
    client = RecordingClient(usage=TokenUsage(10, 10))
    service = AiService(db_session, client)
    with pytest.raises(AiSpendBlockedError):
        _generate(service, league.id)
    assert client.calls == 0

    # A second attempt behaves identically: nothing was queued by the first.
    with pytest.raises(AiSpendBlockedError):
        _generate(service, league.id)
    assert client.calls == 0
    assert db_session.scalars(
        select(AiSpendEntry).where(AiSpendEntry.kind == KIND_DRAFT_RECAP)
    ).all() == []


def test_a_refused_call_serves_a_report_of_another_shape_over_nothing(
    db_session, league, priced
):
    """A stale report of the same kind is degraded service; no report is an
    outage. The contract asks for cached-only, so the newest report for the kind
    is served when this exact input has never been generated."""
    db_session.add(
        AiReport(league_id=league.id, kind=KIND_DRAFT_RECAP, scope="league",
                 input_hash="some-other-inputs", model=MODEL,
                 content_json={"grade": "C", "summary": "older inputs"})
    )
    db_session.flush()
    _exhaust_the_month(db_session)
    client = RecordingClient(usage=TokenUsage(10, 10))
    served = _generate(AiService(db_session, client), league.id)
    assert served["grade"] == "C"
    # A report of another shape standing in for one that was never generated is
    # the case that most needs to be visible: a refused `week=9` request used to
    # return the stored week-1 report with nothing marking it as substituted.
    assert served[CACHED_ONLY_KEY] == "substituted"
    assert client.calls == 0


def test_hosted_mode_requires_the_bound_even_with_no_prices_configured(
    db_session, league, monkeypatch
):
    """A bound a hosted deployment can switch off by leaving configuration empty
    is not a structural bound."""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_price_micro_usd_per_mtok", {}, raising=False)
    monkeypatch.setattr(type(settings), "is_public_synthetic", property(lambda _self: True))
    client = RecordingClient(usage=TokenUsage(10, 10))
    with pytest.raises(AiSpendBlockedError):
        _generate(AiService(db_session, client), league.id)
    assert client.calls == 0


def test_the_input_bound_over_estimates_rather_than_under_estimates(priced):
    """Under-reserving is the direction that lets a ceiling be stepped over."""
    system, user = "a" * 40, "b" * 60
    assert AiService._input_token_bound(system, user) == 100
    # No real tokenizer emits more tokens than characters for any script, so the
    # hold is never short.
    assert AiService._input_token_bound(system, user) >= len(system + user)


# ============================ the Alembic baseline ===========================


def _alembic_config(db_path):
    from alembic.config import Config

    from api.db import Base  # noqa: F401

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _catalog(db_path) -> dict[str, dict]:
    """Columns WITH their declared type and nullability, plus unique indexes.

    Comparing names only let every one of these through with the suite green:
    `committed_micro_usd` created as TEXT, `reserved_micro_usd` made nullable,
    both UNIQUE constraints dropped, and the composite index dropped. Dropping
    `uq_ai_spend_months_month` breaks the ceiling outright -- two rows for one
    month, the conditional UPDATE charges the one that reads zero and the
    counter read returns the other, measured granting $4 on top of a full month.
    """
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name <> 'alembic_version'"
            )
        ]
        catalog = {}
        for table in tables:
            columns = {
                row[1]: (row[2].upper(), bool(row[3]))
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            indexes = set()
            for row in connection.execute(f'PRAGMA index_list("{table}")'):
                name, unique = row[1], bool(row[2])
                members = tuple(
                    entry[2]
                    for entry in connection.execute(f'PRAGMA index_info("{name}")')
                )
                indexes.add((unique, members))
            catalog[table] = {"columns": columns, "indexes": indexes}
        return catalog
    finally:
        connection.close()


def test_the_migrations_and_the_models_cannot_drift(tmp_path):
    """`alembic upgrade head` must produce exactly what `Base.metadata` declares.

    Without this the two definitions of the schema diverge quietly and the
    divergence surfaces as a production migration that half-works. It is also
    what makes the ledger tables real in a migrated database rather than only in
    a `create_all` one.
    """
    from alembic import command

    db_path = tmp_path / "migrated.db"
    command.upgrade(_alembic_config(db_path), "head")
    migrated = _catalog(db_path)

    created = tmp_path / "created.db"
    engine = create_engine(f"sqlite:///{created}")
    Base.metadata.create_all(engine)
    engine.dispose()
    declared = _catalog(created)

    assert set(migrated) == set(declared)
    for table in sorted(declared):
        assert migrated[table]["columns"] == declared[table]["columns"], table
        assert migrated[table]["indexes"] == declared[table]["indexes"], table


def test_the_ledger_arrives_in_the_additive_revision_not_the_baseline(tmp_path):
    """0002 must be the additive revision the contract asks for.

    If the ledger were folded into the baseline instead, the revision would be a
    re-baseline: it would say nothing about whether an existing database can be
    brought forward, which is the only thing a migration is for.
    """
    from alembic import command

    baseline = tmp_path / "baseline.db"
    config = _alembic_config(baseline)
    command.upgrade(config, "0001")
    at_baseline = _catalog(baseline)
    assert "ai_spend_months" not in at_baseline
    assert "ai_spend_entries" not in at_baseline

    command.upgrade(config, "0002")
    at_head = _catalog(baseline)
    assert {"ai_spend_months", "ai_spend_entries"} <= set(at_head)
    # Additive means additive: no existing table gains, loses or renames a column.
    for table, columns in at_baseline.items():
        assert at_head[table] == columns, table


def test_an_explicit_url_is_not_overridden_by_settings(tmp_path):
    """The first version of env.py set the URL unconditionally, which clobbers a
    caller's choice and points the migration at whatever the environment is
    configured for -- outside a test harness, the operator's real database."""
    from alembic import command

    target = tmp_path / "explicit.db"
    command.upgrade(_alembic_config(target), "head")
    assert target.exists(), "the migration must run against the database it was given"
    assert get_settings().db_file != target
    assert "alembic_version" not in _catalog(get_settings().db_file)


def test_the_migration_url_comes_from_settings_when_none_is_given(monkeypatch, tmp_path):
    """A URL in alembic.ini is a second, editable answer to "which database?",
    and it would commit the operator's own path to a public repository.

    Asserted by running a migration with no URL supplied and seeing it land in
    the configured database. The previous version grepped `env.py` for the
    string `get_settings().sqlalchemy_url`, which stayed true when that wiring
    was moved into a function nothing called."""
    from alembic.config import Config

    from alembic import command

    ini = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert "sqlalchemy.url" not in ini

    target = tmp_path / "from-settings.db"
    monkeypatch.setattr(get_settings(), "db_path", str(target), raising=False)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")
    assert target.exists(), "with no URL supplied, Settings must decide"
    assert "ai_spend_months" in _catalog(target)


def test_env_py_autogenerates_against_the_real_metadata(tmp_path):
    """`target_metadata = MetaData()` in env.py left every test green, and
    autogenerate would then have produced silent no-op revisions forever.

    `alembic check` runs env.py's OWN comparison, so it is the one call that
    exercises the variable rather than re-deriving it here.
    """
    from alembic import command

    config = _alembic_config(tmp_path / "check.db")
    command.upgrade(config, "head")
    command.check(config)  # raises AutogenerateDiffsDetected on any drift


def test_the_ledger_migration_does_not_reverse(tmp_path):
    """`alembic downgrade -1` then `upgrade head` was measured granting a fresh
    $5 in an already-spent month, repeatable, from any shell in the container."""
    from alembic import command

    config = _alembic_config(tmp_path / "down.db")
    command.upgrade(config, "head")
    with pytest.raises(NotImplementedError) as caught:
        command.downgrade(config, "0001")
    assert "resets the" in str(caught.value)
    assert "ai_spend_months" in _catalog(tmp_path / "down.db")


# ========================= self-review hardening (unit 4) ====================


def test_a_clock_that_moves_backwards_does_not_hand_back_a_spent_month(ledger):
    """The period is derived from the host clock, so a clock slip is a free $5.

    The ledger's own highest month is a floor: months only move forward.
    """
    _reserve(ledger, tokens=5_000_000, now=datetime(2026, 10, 5, tzinfo=UTC))
    with pytest.raises(SpendLedgerUnavailable) as caught:
        _reserve(ledger, tokens=1, now=datetime(2026, 9, 5, tzinfo=UTC))
    assert "moved backwards" in str(caught.value)
    # The same month, and any later month, still work.
    _reserve(ledger, tokens=1, now=datetime(2026, 11, 1, tzinfo=UTC))
    assert ledger.committed_micro_usd("2026-11") == 1


def test_a_forged_handle_cannot_settle_at_a_smaller_hold(ledger):
    """`Reservation` is a dataclass the caller holds, so its fields are not
    trustworthy. The delta must come from the stored row, not from the handle."""
    handle = _reserve(ledger, tokens=3_000_000)
    forged = type(handle)(
        reservation=handle.reservation,
        month=handle.month,
        kind=handle.kind,
        model=handle.model,
        reserved_micro_usd=1,  # claims a far smaller hold than was charged
    )
    ledger.settle(forged, 1_000_000)
    # 3_000_000 charged, 1_000_000 actual -> 1_000_000 committed. If the handle's
    # claim were believed the delta would be 0 and the month would still show
    # 3_000_000, or worse, a negative delta would refund spend that happened.
    assert ledger.committed_micro_usd(handle.month) == 1_000_000


def test_a_reservation_larger_than_the_whole_ceiling_is_refused(ledger):
    with pytest.raises(SpendCeilingExceeded):
        _reserve(ledger, tokens=6_000_000)
    assert ledger.committed_micro_usd(utc_month()) == 0


def test_a_refused_reservation_charges_nothing(ledger):
    _reserve(ledger, tokens=4_000_000)
    with pytest.raises(SpendCeilingExceeded):
        _reserve(ledger, tokens=2_000_000)
    assert ledger.committed_micro_usd(utc_month()) == 4_000_000
    assert len(ledger.session.scalars(select(AiSpendEntry)).all()) == 1


def test_a_reservation_settled_in_the_next_month_lands_in_its_own_month(ledger):
    handle = _reserve(ledger, tokens=2_000_000, now=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    assert handle.month == "2026-09"
    ledger.settle(handle, 500_000)
    assert ledger.committed_micro_usd("2026-09") == 500_000
    assert ledger.committed_micro_usd("2026-10") == 0


def test_a_blocked_call_reaches_the_routers_as_an_ordinary_ai_error():
    """The routers already turn `AiError` into a secret-free envelope. A new
    sibling class would have needed every router touched, and any router missed
    would have answered a refused-for-cost call with a stack trace."""
    from api.services.ai import AiError

    assert issubclass(AiSpendBlockedError, AiError)


def test_no_error_message_from_the_ledger_carries_anything_private(ledger):
    """The ledger's own text is the one thing that reaches a user-facing
    envelope, so it must name periods and amounts and nothing else."""
    _reserve(ledger, tokens=5_000_000)
    with pytest.raises(SpendCeilingExceeded) as caught:
        _reserve(ledger, tokens=1)
    message = str(caught.value)
    assert utc_month() in message and "$5.00" in message
    for forbidden in ("sk-", "api_key", "swid", "espn_s2", "/Users/", "password"):
        assert forbidden.lower() not in message.lower()


# ================= round-4 review hardening (both reviewers) =================
# Both reviewers returned NO-CLOSE on the previous shape. Everything below pins
# something one of them measured breaking, or an untested line one of them
# mutated successfully. The largest single finding was that this file's own
# concurrency gate failed 14 runs in 20, which made part of the author's
# mutation evidence luck rather than proof.


def _threaded_engine(tmp_path, name="race.db", timeout=5):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": timeout},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _race_for_reservations(tmp_path, round_number, workers=16, each=500_000):
    engine, Sessions = _threaded_engine(tmp_path, f"det{round_number}.db", timeout=0)
    granted, refused = [], []
    barrier, lock = threading.Barrier(workers), threading.Lock()

    def attempt():
        session = Sessions()
        try:
            barrier.wait(timeout=30)
            SpendLedger(session).reserve(
                kind="k", model=MODEL, input_tokens=each, max_output_tokens=0
            )
            with lock:
                granted.append(1)
        except SpendCeilingExceeded:
            with lock:
                refused.append("ceiling")
        except SpendLedgerError as error:
            with lock:
                refused.append(type(error).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=attempt) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    engine.dispose()
    return len(granted), refused


def test_the_concurrency_gate_is_deterministic(tmp_path, priced):
    """The gate for criterion 7 used to fail about 30% of the time.

    `reserve` ran its period check before installing `PRAGMA busy_timeout`, so
    that first SELECT was the one statement the pragma did not cover, and the
    rewind cross-check then compared a stale counter against a freshly committed
    entries sum. Both were measured. A gate that is green by luck is not a gate,
    so this runs the race repeatedly rather than once.
    """
    for round_number in range(5):
        granted, refused = _race_for_reservations(tmp_path, round_number)
        assert set(refused) == {"ceiling"}, sorted(set(refused))
        assert granted == 10


def test_two_concurrent_settles_cannot_double_apply_the_delta(tmp_path, priced):
    """Read the row, test its state in Python, then write: both settles passed
    the test and both applied their delta, driving the counter NEGATIVE."""
    engine, Sessions = _threaded_engine(tmp_path, "settle.db")
    opener = Sessions()
    handle = SpendLedger(opener).reserve(
        kind="k", model=MODEL, input_tokens=3_000_000, max_output_tokens=0
    )
    opener.close()

    outcomes, barrier, lock = [], threading.Barrier(2), threading.Lock()

    def settle_once():
        session = Sessions()
        try:
            barrier.wait(timeout=30)
            SpendLedger(session).settle(handle, 1_000_000)
            with lock:
                outcomes.append("settled")
        except SpendLedgerError as error:
            with lock:
                outcomes.append(type(error).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=settle_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    audit = Sessions()
    committed = SpendLedger(audit).committed_micro_usd(handle.month)
    audit.close()
    engine.dispose()
    assert outcomes.count("settled") == 1, outcomes
    assert committed == 1_000_000
    assert committed >= 0, "the committed total must never go negative"


def test_a_sweep_racing_a_settle_cannot_refund_a_crashed_reservation(tmp_path, priced):
    """`sweep_stale` is a background job, so this interleaving is reachable.

    The sweeper declared the reservation crashed -- which must stay charged in
    full -- and the settle, whose read happened microseconds earlier, applied its
    refund anyway. That is the release path this module says does not exist.
    """
    engine, Sessions = _threaded_engine(tmp_path, "sweep.db")
    opener = Sessions()
    handle = SpendLedger(opener).reserve(
        kind="k", model=MODEL, input_tokens=3_000_000, max_output_tokens=0
    )
    opener.close()

    results, barrier, lock = {}, threading.Barrier(2), threading.Lock()

    def sweeper():
        session = Sessions()
        try:
            barrier.wait(timeout=30)
            swept = SpendLedger(session).sweep_stale(datetime.now(UTC) + timedelta(seconds=1))
            with lock:
                results["sweep"] = swept
        except SpendLedgerError as error:
            with lock:
                results["sweep"] = type(error).__name__
        finally:
            session.close()

    def settler():
        session = Sessions()
        try:
            barrier.wait(timeout=30)
            SpendLedger(session).settle(handle, 1)
            with lock:
                results["settle"] = "settled"
        except SpendLedgerError as error:
            with lock:
                results["settle"] = type(error).__name__
        finally:
            session.close()

    threads = [threading.Thread(target=sweeper), threading.Thread(target=settler)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    audit = Sessions()
    ledger = SpendLedger(audit)
    committed = ledger.committed_micro_usd(handle.month)
    state = ledger.entry(handle.reservation).state
    audit.close()
    engine.dispose()
    # Exactly one of them owned the transition. If the sweep won, the crash stays
    # charged in full; if the settle won, the month holds the settled amount.
    # What must never happen is the sweep declaring a crash while the settle
    # refunds it.
    if state == STATE_UNKNOWN:
        assert committed == handle.reserved_micro_usd
        assert results["settle"] != "settled"
    else:
        assert state == STATE_SETTLED and committed == 1
        assert results["sweep"] == []


def test_a_zero_cost_report_is_not_a_refund(ledger):
    """`TokenUsage(0, 0)` -- one SDK field rename away -- returned the entire
    hold on every call, and the ceiling stopped bounding anything."""
    handle = _reserve(ledger, tokens=2_000_000)
    row = ledger.settle(handle, 0)
    assert row.state == STATE_UNKNOWN
    assert ledger.committed_micro_usd(handle.month) == 2_000_000


def test_a_zero_usage_report_through_generate_keeps_the_hold(db_session, league, priced):
    client = RecordingClient(usage=TokenUsage(input_tokens=0, output_tokens=0))
    _generate(AiService(db_session, client), league.id)
    entry = db_session.scalars(select(AiSpendEntry)).one()
    assert entry.state == STATE_UNKNOWN
    assert SpendLedger(db_session).committed_micro_usd(utc_month()) == entry.reserved_micro_usd


def test_a_ceiling_refusal_does_not_destroy_the_callers_pending_work(
    db_session, league, priced
):
    """The ledger used to borrow the caller's session and roll it back.

    A request that generated report A and then hit the ceiling on report B lost
    A -- already produced and already paid for -- and then got "no cached report
    exists" for it.
    """
    warm = RecordingClient(usage=TokenUsage(10, 10))
    _generate(AiService(db_session, warm), league.id, facts={"picks": ["a"]})
    _exhaust_the_month(db_session)

    cold = RecordingClient(usage=TokenUsage(10, 10))
    served = _generate(AiService(db_session, cold), league.id, facts={"picks": ["b"]})
    assert served[CACHED_ONLY_KEY] == "substituted"
    assert cold.calls == 0
    # The first report is still there.
    assert db_session.scalars(
        select(AiReport).where(AiReport.kind == KIND_DRAFT_RECAP)
    ).all()


def test_a_missing_counter_row_is_refused_not_silently_granted(ledger, db_session):
    """The counter row IS the bound, so losing it hands back a fresh $5. The
    entries are a second witness, written in the same transaction as each
    charge, and a counter behind them is evidence the counter alone was lost."""
    _reserve(ledger, tokens=5_000_000)
    db_session.execute(text("DELETE FROM ai_spend_months"))
    db_session.commit()
    with pytest.raises(SpendLedgerUnavailable) as caught:
        _reserve(ledger, tokens=1)
    assert "rewound" in str(caught.value)


def test_a_rewound_counter_is_refused(ledger, db_session):
    _reserve(ledger, tokens=4_000_000)
    db_session.execute(text("UPDATE ai_spend_months SET committed_micro_usd = 10"))
    db_session.commit()
    with pytest.raises(SpendLedgerUnavailable) as caught:
        _reserve(ledger, tokens=1)
    assert "rewound" in str(caught.value)


def test_settling_against_a_missing_counter_row_is_refused(ledger, db_session):
    """Ignoring the month UPDATE's rowcount discarded an overrun while still
    marking the entry settled."""
    handle = _reserve(ledger, tokens=1_000_000)
    db_session.execute(text("DELETE FROM ai_spend_months"))
    db_session.commit()
    with pytest.raises(SpendLedgerUnavailable):
        ledger.settle(handle, 2_000_000)


def test_every_ledger_failure_is_a_ledger_error(ledger, monkeypatch):
    """`pysqlite` raises a bare `OverflowError` for an integer too large for
    SQLite. It is not a DBAPI error, so SQLAlchemy does not wrap it, and it
    escaped `except SQLAlchemyError` as a 500 instead of degrading to
    cached-only -- against a docstring promising the opposite."""
    huge = 10 ** 30
    monkeypatch.setattr(
        get_settings(), "ai_price_micro_usd_per_mtok", {MODEL: (huge, huge)}, raising=False
    )
    with pytest.raises(SpendLedgerError):
        _reserve(ledger, tokens=1_000_000)


def test_a_naive_or_foreign_sweep_cutoff_is_refused(ledger):
    """SQLite stores `created_at` naive, so a cutoff in another zone had its
    offset silently discarded: a UTC+14 cutoff swept a LIVE reservation, whose
    settle was then refused and whose month stayed charged in full forever."""
    handle = _reserve(ledger)
    with pytest.raises(SpendLedgerUnavailable):
        ledger.sweep_stale(datetime.now())  # naive

    # One hour ago, expressed in UTC+14. Converted correctly it is in the past
    # and sweeps nothing. With the offset dropped -- which is what SQLite's naive
    # storage did to it -- its wall clock reads thirteen hours in the FUTURE and
    # it sweeps a live reservation, whose settle is then refused and whose month
    # stays charged in full forever.
    far_east = timezone(timedelta(hours=14))
    an_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).astimezone(far_east)
    assert an_hour_ago.replace(tzinfo=None) > datetime.now(UTC).replace(tzinfo=None), (
        "precondition: its naive wall clock must read as the future"
    )
    assert ledger.sweep_stale(an_hour_ago) == []
    assert ledger.entry(handle.reservation).state == STATE_RESERVED


def test_a_clock_that_jumps_forward_cannot_mint_a_period(ledger):
    """The forward direction is the one that GRANTS budget. The first version of
    this guard defended only backwards, so a jump to 2099 minted a period and a
    fabricated far-future period then refused every real month forever."""
    _reserve(ledger, tokens=1, now=datetime(2026, 9, 5, tzinfo=UTC))
    with pytest.raises(SpendLedgerUnavailable) as caught:
        _reserve(ledger, tokens=1, now=datetime(2099, 1, 1, tzinfo=UTC))
    assert "moved forward" in str(caught.value)
    # One month at a time is how a real calendar advances, and still works.
    _reserve(ledger, tokens=1, now=datetime(2026, 10, 1, tzinfo=UTC))
    assert ledger.committed_micro_usd("2026-10") == 1


def test_the_period_floor_reads_the_highest_month_not_the_lowest(ledger):
    """With one month row `asc()` and `desc()` agree, so the floor was untested."""
    _reserve(ledger, tokens=1, now=datetime(2026, 9, 5, tzinfo=UTC))
    _reserve(ledger, tokens=1, now=datetime(2026, 10, 5, tzinfo=UTC))
    with pytest.raises(SpendLedgerUnavailable) as caught:
        _reserve(ledger, tokens=1, now=datetime(2026, 9, 20, tzinfo=UTC))
    assert "moved backwards" in str(caught.value)


def test_december_rolls_into_january(ledger):
    _reserve(ledger, tokens=1, now=datetime(2026, 12, 20, tzinfo=UTC))
    _reserve(ledger, tokens=1, now=datetime(2027, 1, 3, tzinfo=UTC))
    assert ledger.committed_micro_usd("2027-01") == 1


def test_the_stored_month_is_used_not_the_handles(ledger):
    """The handle is caller-held, so crediting `handle.month` would let a caller
    move a settlement's delta into a different month."""
    handle = _reserve(ledger, tokens=2_000_000, now=datetime(2026, 9, 5, tzinfo=UTC))
    forged = type(handle)(
        reservation=handle.reservation, month="2026-10", kind=handle.kind,
        model=handle.model, reserved_micro_usd=handle.reserved_micro_usd,
    )
    ledger.settle(forged, 500_000)
    assert ledger.committed_micro_usd("2026-09") == 500_000
    assert ledger.committed_micro_usd("2026-10") == 0


def test_the_sweep_only_touches_reserved_entries(ledger):
    handle = _reserve(ledger)
    ledger.settle(handle, 1)
    assert ledger.sweep_stale(datetime.now(UTC) + timedelta(seconds=1)) == []
    assert ledger.entry(handle.reservation).state == STATE_SETTLED


@pytest.mark.parametrize("tokens,out", [(-1, 0), (0, -1)])
def test_both_halves_of_the_worst_case_guard(priced, tokens, out):
    with pytest.raises(SpendLedgerUnavailable):
        worst_case_micro_usd(MODEL, tokens, out)


@pytest.mark.parametrize("usage", [TokenUsage(-1, 0), TokenUsage(0, -1)])
def test_both_halves_of_the_actual_guard(priced, usage):
    with pytest.raises(SpendLedgerUnavailable):
        actual_micro_usd(MODEL, usage)


# -------------------------- what the reservation is FOR ---------------------


def test_force_really_regenerates(db_session, league, priced):
    """The premise the two cached-only tests rest on. `generate` ignoring
    `force=` left both of them passing for the wrong reason."""
    client = RecordingClient(usage=TokenUsage(10, 10))
    service = AiService(db_session, client)
    _generate(service, league.id)
    assert client.calls == 1
    _generate(service, league.id)  # same inputs: the ordinary cache answers
    assert client.calls == 1
    service.generate(
        kind=KIND_DRAFT_RECAP, scope="league", league_id=league.id,
        model=MODEL, facts={"picks": []}, force=True,
    )
    assert client.calls == 2


def test_the_reservation_is_for_the_prompt_actually_sent(db_session, league, priced):
    """Reserving against an empty prompt left the suite green: the under-reserve
    direction this module's own docstring names as the one that lets a ceiling
    be stepped over."""
    holds = {}
    for label, facts in (("small", {"picks": []}),
                         ("big", {"picks": ["x" * 400 for _ in range(40)]})):
        db_session.execute(text("DELETE FROM ai_spend_entries"))
        db_session.execute(text("DELETE FROM ai_spend_months"))
        db_session.commit()
        _generate(AiService(db_session, RecordingClient()), league.id, facts=facts)
        holds[label] = db_session.scalars(select(AiSpendEntry)).one().reserved_micro_usd
    assert holds["big"] > holds["small"], holds


def test_the_reserved_output_budget_is_the_one_sent(db_session, league, priced):
    """Requesting ten times the reserved output budget left the suite green."""
    seen = {}

    class Recorder(RecordingClient):
        def complete_json(self, **kwargs):
            seen["max_tokens"] = kwargs["max_tokens"]
            seen["reservation"] = kwargs.get("reservation")
            return RecordingClient.complete_json(self, **kwargs)

    _generate(AiService(db_session, Recorder()), league.id)
    entry = db_session.scalars(select(AiSpendEntry)).one()
    expected = worst_case_micro_usd(
        MODEL,
        AiService._input_token_bound("", ""),
        seen["max_tokens"],
    )
    # The held amount must cover the output budget that was actually requested.
    assert entry.reserved_micro_usd >= expected
    assert seen["reservation"] is not None, "the client must receive the charge"


def test_the_production_client_refuses_a_call_with_no_reservation(priced):
    """A single runtime check at one call site is the mechanism this contract
    rates procedural. The only client that can spend money refuses too."""
    from api.services.ai import AiError, AnthropicLlmClient

    client = AnthropicLlmClient.__new__(AnthropicLlmClient)
    with pytest.raises(AiError) as caught:
        AnthropicLlmClient.complete_json(
            client, model=MODEL, system="s", user="u", schema=object, max_tokens=1
        )
    assert "without a spend reservation" in str(caught.value)


def test_the_busy_timeout_is_the_first_statement_of_a_reserve(tmp_path, priced):
    """Any statement issued before the pragma is not covered by it.

    Today, on pysqlite's legacy transaction control, a leading SELECT does not
    open a transaction and so does not fail under contention -- which is why a
    load test cannot tell the two orderings apart. That is a property of this
    driver, not of the code: under Python's newer `sqlite3` autocommit handling,
    or on PostgreSQL in a later phase, SQLAlchemy emits BEGIN first and the
    leading read becomes a lock promotion, which SQLite refuses immediately and
    does NOT honour `busy_timeout` for. So the ordering is asserted structurally
    rather than measured, and the assertion says exactly what it establishes.
    """
    from sqlalchemy import event

    engine = create_engine(f"sqlite:///{tmp_path / 'order.db'}")
    Base.metadata.create_all(engine)
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split()[0].upper())

    session = sessionmaker(bind=engine)()
    try:
        statements.clear()
        SpendLedger(session).reserve(
            kind="k", model=MODEL, input_tokens=1_000, max_output_tokens=0
        )
    finally:
        session.close()
        engine.dispose()
    assert statements, "the reserve must have issued statements"
    assert statements[0] == "PRAGMA", statements[:4]


def test_the_service_ledger_does_not_share_the_callers_session(db_session):
    """Two separate properties, easily confused for one.

    `generate` COMMITTING the caller's session before it reserves is what stops
    a ceiling refusal from destroying work the request had already finished --
    that is `test_a_ceiling_refusal_does_not_destroy_the_callers_pending_work`.
    The ledger having its OWN session is what keeps the charge's transaction
    independent of the caller's, which is what makes the charge durable before
    the model call and therefore a crash non-free. With the commit in place the
    second property has no behavioural tell in the current flow, so it is
    asserted directly rather than inferred from one that does.
    """
    service = AiService(db_session, RecordingClient())
    with service._ledger() as ledger:
        assert ledger.session is not db_session


def test_a_client_with_a_malformed_usage_object_does_not_lose_the_result(
    db_session, league, priced
):
    """`actual_micro_usd` raised `AttributeError` past the settle handler AFTER
    the call was billed, so a bookkeeping failure discarded a paid-for result."""

    class BadUsage(RecordingClient):
        def complete_json(self, **kwargs):
            result = RecordingClient.complete_json(self, **kwargs)
            self.last_usage = object()  # not a TokenUsage
            return result

    content = _generate(AiService(db_session, BadUsage()), league.id)
    assert content["grade"] == "B", "the caller's result must survive"
    entry = db_session.scalars(select(AiSpendEntry)).one()
    # The conservative hold stands, which is the safe side of an unreconciled
    # call that definitely happened.
    assert entry.state == STATE_RESERVED
    assert SpendLedger(db_session).committed_micro_usd(utc_month()) == entry.reserved_micro_usd
