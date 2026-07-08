"""Phase 1.5 hardening tests: FK enforcement, corrupt-row guards, stale state."""

import copy

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.crypto import encrypt
from api.models import Account, League, LineupSlot, Player, Team, Transaction
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
