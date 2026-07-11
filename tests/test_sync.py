"""End-to-end sync pipeline tests against fixtures + a temp SQLite DB (SPEC 5, 12)."""

from sqlalchemy import func, select

from api.crypto import encrypt
from api.models import (
    Account,
    DraftPick,
    League,
    LineupSlot,
    Matchup,
    Player,
    Team,
    Transaction,
)
from api.services.sync import SyncService

from .conftest import FakeEspn


def _make_account(session) -> Account:
    acct = Account(
        label="Main",
        swid="{AAAA-1111}",
        espn_s2_encrypted=encrypt("some-espn-s2-value"),
        status="active",
    )
    session.add(acct)
    session.flush()
    return acct


def _make_league(session, account) -> League:
    lg = League(
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


def test_my_team_detection_and_autodraft_flag(db_session, league_fixture, players_fixture):
    acct = _make_account(db_session)
    lg = _make_league(db_session, acct)
    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()

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
    svc.sync_league(lg)
    db_session.commit()

    # Counts unchanged after a second sync (no duplication).
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
