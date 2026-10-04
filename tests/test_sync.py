"""End-to-end sync pipeline tests against fixtures + a temp SQLite DB (SPEC 5, 12)."""

import copy

from sqlalchemy import func, select

from api.crypto import encrypt
from api.models import (
    Account,
    CurrentRosterEntry,
    CurrentRosterSnapshot,
    DraftPick,
    League,
    LineupSlot,
    Matchup,
    MetricSnapshot,
    Player,
    Team,
    Transaction,
)
from api.services.sync import SyncService
from api.tenancy import current_tenant_id

from .conftest import FakeEspn


def _make_account(session) -> Account:
    acct = Account(
        tenant_id=current_tenant_id(session),
        label="Main",
        swid="{AAAA-1111}",
        espn_s2_encrypted=encrypt("some-espn-s2-value"),
        status="active",
    )
    session.add(acct)
    session.flush()
    return acct


def _make_league(session, account) -> League:
    lg = League(tenant_id=current_tenant_id(session), 
        espn_league_id="111",
        season=2026,
        account_id=account.id,
        is_public=False,
        lifecycle="pre_draft",
    )
    session.add(lg)
    session.flush()
    return lg


def test_sync_populates_all_tables(db_session, league_fixture, players_fixture):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    fake = FakeEspn(league_fixture, players_fixture)

    result = SyncService(db_session, espn=fake).sync_league(lg)
    db_session.commit()

    assert result["name"] == "Test Public League"
    assert result["size"] == 4
    assert result["scoring"] == "PPR"
    assert result["drafted"] is True
    assert result["lifecycle"] == "in_season"  # week 1 complete

    assert (
        db_session.scalar(select(func.count()).select_from(Team).where(Team.league_id == lg.id))
        == 4
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(DraftPick).where(DraftPick.league_id == lg.id)
        )
        == 8
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(Matchup).where(Matchup.league_id == lg.id)
        )
        == 4
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.league_id == lg.id)
        )
        == 2
    )
    assert db_session.scalar(select(func.count()).select_from(Player)) == 2
    # week 1 boxscore rows for team 1 (1 starter + 1 bench)
    assert (
        db_session.scalar(
            select(func.count()).select_from(LineupSlot).where(LineupSlot.league_id == lg.id)
        )
        == 3
    )


def test_sync_persists_current_week_roster_and_matchup_detail(
    db_session,
    current_roster_fixture,
    players_fixture,
    pro_schedule_fixture,
):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    fake = FakeEspn(
        current_roster_fixture,
        players_fixture,
        pro_schedule_data=pro_schedule_fixture,
    )

    result = SyncService(db_session, espn=fake).sync_league(lg)
    db_session.commit()

    assert result["errors"] == []
    assert result["current_roster_entries"] == 6
    assert lg.current_scoring_period == 1
    assert lg.current_matchup_period == 1
    snapshot = db_session.scalar(
        select(CurrentRosterSnapshot).where(CurrentRosterSnapshot.league_id == lg.id)
    )
    assert snapshot is not None
    assert snapshot.scoring_period == 1
    entries = list(
        db_session.scalars(
            select(CurrentRosterEntry).where(CurrentRosterEntry.snapshot_id == snapshot.id)
        )
    )
    assert len(entries) == 6
    quarterback = next(entry for entry in entries if entry.espn_player_id == 1001)
    assert quarterback.player_name == "Alpha Quarterback"
    assert quarterback.nfl_team == "ATL"
    assert quarterback.opponent == "BUF"
    assert quarterback.projected_points == 18.5
    matchup = db_session.scalar(select(Matchup).where(Matchup.league_id == lg.id))
    assert matchup is not None
    assert matchup.home_points == 0.0
    assert matchup.home_projected_points == 108.4


def test_current_roster_failure_retains_prior_snapshot(
    db_session,
    current_roster_fixture,
    players_fixture,
    pro_schedule_fixture,
):
    from api.services.espn import EspnError

    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    SyncService(
        db_session,
        espn=FakeEspn(
            current_roster_fixture,
            players_fixture,
            pro_schedule_data=pro_schedule_fixture,
        ),
    ).sync_league(lg)
    db_session.commit()
    original = db_session.scalar(
        select(CurrentRosterSnapshot).where(CurrentRosterSnapshot.league_id == lg.id)
    )
    assert original is not None
    original_id = original.id

    next_period = copy.deepcopy(current_roster_fixture)
    next_period["scoringPeriodId"] = 2
    next_period["status"]["currentMatchupPeriod"] = 2

    class CurrentRosterFailEspn(FakeEspn):
        def fetch_views(self, league_id, season, views, **kwargs):
            if "mRoster" in views:
                raise EspnError("current roster unavailable")
            return super().fetch_views(league_id, season, views, **kwargs)

    result = SyncService(
        db_session,
        espn=CurrentRosterFailEspn(
            next_period,
            players_fixture,
            pro_schedule_data=pro_schedule_fixture,
        ),
    ).sync_league(lg)
    db_session.commit()

    retained = db_session.scalar(
        select(CurrentRosterSnapshot).where(CurrentRosterSnapshot.league_id == lg.id)
    )
    assert retained is not None and retained.id == original_id
    assert retained.scoring_period == 1
    assert lg.current_scoring_period == 2
    assert any("current_roster_failed" in error for error in result["errors"])


def test_my_team_detection_and_autodraft_flag(db_session, league_fixture, players_fixture):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    result = SyncService(
        db_session, espn=FakeEspn(league_fixture, players_fixture)
    ).sync_league(lg)
    db_session.commit()

    assert result["my_team_espn_id"] == 1
    me = db_session.scalar(select(Team).where(Team.league_id == lg.id, Team.is_me.is_(True)))
    assert me is not None and me.name == "Alpha"
    assert lg.my_team_id == me.id

    delta = db_session.scalar(select(Team).where(Team.league_id == lg.id, Team.espn_team_id == 4))
    assert delta.autodrafted is True  # every pick autodrafted
    alpha = db_session.scalar(select(Team).where(Team.league_id == lg.id, Team.espn_team_id == 1))
    assert alpha.autodrafted is False


def test_sync_is_idempotent(db_session, league_fixture, players_fixture):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    svc = SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture))
    svc.sync_league(lg)
    db_session.commit()
    snapshot_count = db_session.scalar(
        select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.league_id == lg.id)
    )
    assert snapshot_count and snapshot_count > 0
    first_batches = set(
        db_session.scalars(
            select(MetricSnapshot.batch_id).where(MetricSnapshot.league_id == lg.id)
        )
    )
    assert len(first_batches) == 1
    svc.sync_league(lg)
    db_session.commit()

    # Source tables remain idempotent; clean-sync snapshot events are append-only.
    assert (
        db_session.scalar(select(func.count()).select_from(Team).where(Team.league_id == lg.id))
        == 4
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(DraftPick).where(DraftPick.league_id == lg.id)
        )
        == 8
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(Matchup).where(Matchup.league_id == lg.id)
        )
        == 4
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(LineupSlot).where(LineupSlot.league_id == lg.id)
        )
        == 3
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(MetricSnapshot).where(
                MetricSnapshot.league_id == lg.id
            )
        )
        == snapshot_count * 2
    )
    assert len(
        set(
            db_session.scalars(
                select(MetricSnapshot.batch_id).where(MetricSnapshot.league_id == lg.id)
            )
        )
    ) == 2


def test_interrupted_snapshot_write_is_nonfatal_and_next_sync_recovers(
    db_session, league_fixture, players_fixture, monkeypatch
):
    from api.services import momentum

    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    original = momentum._write_snapshot
    failed = False

    def interrupt_once(*args, **kwargs):
        nonlocal failed
        original(*args, **kwargs)
        if not failed:
            failed = True
            raise RuntimeError("simulated snapshot interruption")

    monkeypatch.setattr(momentum, "_write_snapshot", interrupt_once)
    first = SyncService(
        db_session, espn=FakeEspn(league_fixture, players_fixture)
    ).sync_league(lg)
    db_session.commit()
    assert "snapshots_failed" in first["errors"]
    assert db_session.scalar(
        select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.league_id == lg.id)
    ) == 0

    monkeypatch.setattr(momentum, "_write_snapshot", original)
    second = SyncService(
        db_session, espn=FakeEspn(league_fixture, players_fixture)
    ).sync_league(lg)
    db_session.commit()
    assert second["errors"] == []
    assert db_session.scalar(
        select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.league_id == lg.id)
    ) > 0


def test_bad_cookie_flips_account_to_needs_reauth(db_session, league_fixture, players_fixture):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    fake = FakeEspn(league_fixture, players_fixture, auth_error=True)

    result = SyncService(db_session, espn=fake).sync_league(lg)
    db_session.commit()

    assert result.get("needs_reauth") is True
    assert acct.status == "needs_reauth"
    # Persistent diagnostics: auth failure is recorded, redacted, on the league.
    assert lg.last_sync_ok is False
    assert lg.last_sync_error == "auth_failed"
    # No crash, no partial team rows written.
    assert (
        db_session.scalar(select(func.count()).select_from(Team).where(Team.league_id == lg.id))
        == 0
    )


def test_successful_sync_records_ok_and_clears_prior_diagnostics(
    db_session, league_fixture, players_fixture
):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    # Simulate a prior failed sync still recorded on the league.
    lg.last_sync_ok = False
    lg.last_sync_error = "auth_failed"
    db_session.flush()

    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()

    assert lg.last_sync_ok is True
    assert lg.last_sync_error is None


def test_partial_failure_records_safe_diagnostic(db_session, league_fixture, players_fixture):
    from api.services.espn import EspnError

    class PartialFailEspn(FakeEspn):
        # Settings/teams/draft succeed; the players pull blows up mid-sync.
        def fetch_views(self, league_id, season, views, **kw):
            if "kona_player_info" in views:
                raise EspnError("players endpoint 500")
            return super().fetch_views(league_id, season, views, **kw)

    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)

    result = SyncService(
        db_session, espn=PartialFailEspn(league_fixture, players_fixture)
    ).sync_league(lg)
    db_session.commit()

    # Sync completed (teams written) but a step failed → ok=False + safe summary.
    assert result["errors"]
    assert lg.last_sync_ok is False
    assert lg.last_sync_error and "players_failed" in lg.last_sync_error
    # The diagnostic never carries the account's secrets.
    assert "some-espn-s2-value" not in lg.last_sync_error
    assert acct.swid not in lg.last_sync_error
    assert db_session.scalar(
        select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.league_id == lg.id)
    ) == 0


def test_sync_stamps_draft_adp_and_value_delta(db_session, league_fixture, players_fixture):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()

    picks = {
        p.overall: p
        for p in db_session.scalars(select(DraftPick).where(DraftPick.league_id == lg.id))
    }
    # Player 1001 (ADP 3.4) drafted at overall 1 → value_delta = 3.4 - 1 = 2.4.
    assert picks[1].adp_at_draft == 3.4
    assert picks[1].value_delta == 2.4
    # Player 1002 (ADP 1.1) drafted at overall 2 → value_delta = 1.1 - 2 = -0.9.
    assert picks[2].adp_at_draft == 1.1
    assert picks[2].value_delta == -0.9
    # Picks whose player isn't in the pool have no ADP/value stamped.
    assert picks[3].adp_at_draft is None and picks[3].value_delta is None


def test_sync_failure_leaves_draft_adp_null_no_stale(db_session, league_fixture, players_fixture):
    from api.services.espn import EspnError

    class PlayerFailEspn(FakeEspn):
        def fetch_views(self, league_id, season, views, **kw):
            if "kona_player_info" in views:
                raise EspnError("players endpoint 500")
            return super().fetch_views(league_id, season, views, **kw)

    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    # First sync succeeds and stamps ADP.
    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()
    assert db_session.scalar(
        select(DraftPick.adp_at_draft).where(DraftPick.league_id == lg.id, DraftPick.overall == 1)
    ) == 3.4

    # Second sync: player pool fetch fails → picks are replaced and left null (no stale ADP).
    SyncService(db_session, espn=PlayerFailEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()
    adps = [
        row[0]
        for row in db_session.execute(
            select(DraftPick.adp_at_draft).where(DraftPick.league_id == lg.id)
        )
    ]
    assert all(a is None for a in adps)
    deltas = [
        row[0]
        for row in db_session.execute(
            select(DraftPick.value_delta).where(DraftPick.league_id == lg.id)
        )
    ]
    assert all(d is None for d in deltas)
