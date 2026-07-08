"""Phase 1.5 hardening tests: FK enforcement, corrupt-row guards, stale state."""

import copy

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.crypto import encrypt
from api.models import Account, League, LineupSlot, Metric, Player, Team, Transaction
from api.services.sync import SyncService

from .conftest import FakeEspn


def _account(session, swid="{AAAA-1111}") -> Account:
    a = Account(label="main", swid=swid, espn_s2_encrypted=encrypt("s2"), status="active")
    session.add(a)
    session.flush()
    return a


def _league(session, account) -> League:
    lg = League(espn_league_id="111", season=2026, account_id=account.id, is_public=False)
    session.add(lg)
    session.flush()
    return lg


# --- goal 2: FK enforcement + no corrupt rows ------------------------------
def test_foreign_keys_are_enforced(db_session):
    acct = _account(db_session)
    lg = _league(db_session, acct)
    db_session.commit()
    # team_id 999999 does not exist -> FK violation (PRAGMA foreign_keys=ON).
    db_session.add(LineupSlot(league_id=lg.id, team_id=999999, week=1, slot="QB"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_sync_skips_boxscore_entry_for_unknown_team(db_session, league_fixture, players_fixture):
    acct = _account(db_session)
    lg = _league(db_session, acct)
    # Inject a week-1 matchup for a phantom team (id 99) not in teams[].
    data = copy.deepcopy(league_fixture)
    data["schedule"].append(
        {
            "matchupPeriodId": 1,
            "playoffTierType": "NONE",
            "home": {
                "teamId": 99,
                "totalPoints": 50.0,
                "rosterForCurrentScoringPeriod": {
                    "entries": [
                        {"playerId": 9999, "lineupSlotId": 0,
                         "playerPoolEntry": {"id": 9999, "appliedStatTotal": 12.0}}
                    ]
                },
            },
            "away": {"teamId": 100, "totalPoints": 0},
        }
    )
    result = SyncService(db_session, espn=FakeEspn(data, players_fixture)).sync_league(lg)
    db_session.commit()
    # Sync completed without an FK error and did NOT write the phantom player.
    assert "boxscore" not in " ".join(result["errors"])
    phantom = db_session.scalar(
        select(func.count()).select_from(LineupSlot).where(LineupSlot.espn_player_id == 9999)
    )
    assert phantom == 0


# --- goal 4: stale my-team / autodraft cleared -----------------------------
def test_sync_clears_stale_my_team_on_account_change(db_session, league_fixture, players_fixture):
    acct = _account(db_session)  # SWID owns team 1
    lg = _league(db_session, acct)
    svc = SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture))
    svc.sync_league(lg)
    db_session.commit()
    assert lg.my_team_id is not None
    assert db_session.scalar(select(func.count()).select_from(Team).where(Team.is_me.is_(True))) == 1

    # Account SWID changes to one that owns no team → detection must clear.
    acct.swid = "{ZZZZ-9999}"
    db_session.flush()
    svc.sync_league(lg)
    db_session.commit()
    assert lg.my_team_id is None
    assert db_session.scalar(select(func.count()).select_from(Team).where(Team.is_me.is_(True))) == 0


# --- goal 7: new fields persisted ------------------------------------------
def test_proj_ros_and_executed_at_persisted(db_session, league_fixture, players_fixture):
    acct = _account(db_session)
    lg = _league(db_session, acct)
    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()

    qb = db_session.get(Player, 1001)
    assert qb.proj_ros == 305.7
    waiver = db_session.scalar(select(Transaction).where(Transaction.type == "waiver"))
    assert waiver.executed_at is not None


# --- finding 1: metric identity unique across all NULL/non-NULL shapes ------
def test_metric_identity_unique_for_every_null_shape(db_session):
    acct = _account(db_session)
    lg = _league(db_session, acct)
    team = Team(league_id=lg.id, espn_team_id=1)
    db_session.add(team)
    db_session.commit()  # persist so per-shape rollbacks don't remove it

    shapes = [
        {"team_id": None, "week": None},  # league metric, no week
        {"team_id": None, "week": 3},  # league weekly metric
        {"team_id": team.id, "week": None},  # team metric, no week
        {"team_id": team.id, "week": 3},  # team weekly metric
    ]
    for shape in shapes:
        db_session.add(
            Metric(league_id=lg.id, key="edge_score", value_float=1.0, **shape)
        )
        db_session.commit()
        # An identical-identity row must be rejected (esp. the NULL team/week cases,
        # which a plain UniqueConstraint would silently allow in SQLite).
        db_session.add(
            Metric(league_id=lg.id, key="edge_score", value_float=2.0, **shape)
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


def test_metric_distinct_shapes_coexist(db_session):
    acct = _account(db_session)
    lg = _league(db_session, acct)
    team = Team(league_id=lg.id, espn_team_id=1)
    db_session.add(team)
    db_session.flush()
    # Same league+key but distinct (team_id, week) identities must all be allowed.
    db_session.add_all(
        [
            Metric(league_id=lg.id, key="edge_score", team_id=None, week=None),
            Metric(league_id=lg.id, key="edge_score", team_id=None, week=3),
            Metric(league_id=lg.id, key="edge_score", team_id=team.id, week=None),
            Metric(league_id=lg.id, key="edge_score", team_id=team.id, week=3),
        ]
    )
    db_session.flush()  # no IntegrityError
    assert db_session.scalar(select(func.count()).select_from(Metric)) == 4


# --- finding 2: proj_ros is not left stale when ESPN omits projections ------
def test_proj_ros_cleared_when_projection_missing(db_session, league_fixture, players_fixture):
    acct = _account(db_session)
    lg = _league(db_session, acct)
    SyncService(db_session, espn=FakeEspn(league_fixture, players_fixture)).sync_league(lg)
    db_session.commit()
    assert db_session.get(Player, 1001).proj_ros == 305.7

    # Re-sync with projections dropped from the player pool.
    no_proj = copy.deepcopy(players_fixture)
    for wrapper in no_proj["players"]:
        wrapper["player"].pop("stats", None)
    SyncService(db_session, espn=FakeEspn(league_fixture, no_proj)).sync_league(lg)
    db_session.commit()
    assert db_session.get(Player, 1001).proj_ros is None


# --- finding 3: owned EspnService is closed; injected one is not ------------
def test_syncservice_closes_owned_espn(monkeypatch):
    import api.services.sync as syncmod

    created = {}

    class TrackingEspn:
        def __init__(self, *a, **k):
            self.closed = False
            created["svc"] = self

        def close(self):
            self.closed = True

    monkeypatch.setattr(syncmod, "EspnService", TrackingEspn)
    with syncmod.SyncService(session=None):
        pass
    assert created["svc"].closed is True


def test_syncservice_does_not_close_injected_espn(league_fixture, players_fixture):
    class CloseTracking(FakeEspn):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.closed = False

        def close(self):
            self.closed = True

    fake = CloseTracking(league_fixture, players_fixture)
    with SyncService(session=None, espn=fake):
        pass
    assert fake.closed is False
