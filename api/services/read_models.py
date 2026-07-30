"""Shared builders for DB-backed API read models."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    CurrentRosterEntry,
    CurrentRosterSnapshot,
    League,
    Matchup,
    Player,
    Team,
)
from ..schemas import (
    LeagueOut,
    RosterSlotOut,
    TeamDetailOut,
    TeamMatchupOut,
    TeamMatchupSideOut,
    TeamOut,
)
from .espn_constants import slot_name, slot_section, slot_sort_key
from .parse import classify_scoring


def build_league_out(session: Session, league: League) -> LeagueOut:
    """Serialize a league with its detected owner-team identity when available."""
    me = (
        session.scalar(
            select(Team).where(
                Team.id == league.my_team_id,
                Team.league_id == league.id,
            )
        )
        if league.my_team_id is not None
        else None
    )
    return LeagueOut.model_validate(league).model_copy(
        update={
            "my_team_name": me.name if me else None,
            "my_team_logo_url": me.logo_url if me else None,
        }
    )


def _configured_slots(league: League) -> list[tuple[int, int]]:
    configured: list[tuple[int, int]] = []
    for raw_slot_id, raw_count in (league.lineup_slots_json or {}).items():
        try:
            slot_id = int(raw_slot_id)
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            configured.append((slot_id, count))
    return sorted(configured, key=lambda item: slot_sort_key(item[0]))


def _roster_slot(
    slot_id: int,
    slot_index: int,
    entry: CurrentRosterEntry | None,
    players: dict[int, Player],
) -> RosterSlotOut:
    player = players.get(entry.espn_player_id) if entry else None
    return RosterSlotOut(
        slot_id=slot_id,
        slot_label=slot_name(slot_id),
        slot_index=slot_index,
        section=slot_section(slot_id),
        espn_player_id=entry.espn_player_id if entry else None,
        player_name=(entry.player_name or (player.name if player else None)) if entry else None,
        player_position=(
            entry.player_position or (player.position if player else None)
        ) if entry else None,
        nfl_team=(entry.nfl_team or (player.nfl_team if player else None)) if entry else None,
        opponent=entry.opponent if entry else None,
        kickoff_at=entry.kickoff_at if entry else None,
        game_status=entry.game_status if entry else None,
        injury_status=entry.injury_status if entry else None,
        actual_points=entry.actual_points if entry else None,
        projected_points=entry.projected_points if entry else None,
    )


def _build_roster_sections(
    session: Session,
    league: League,
    team: Team,
    snapshot: CurrentRosterSnapshot | None,
) -> tuple[list[RosterSlotOut], list[RosterSlotOut], list[RosterSlotOut]]:
    entries = (
        list(
            session.scalars(
                select(CurrentRosterEntry)
                .where(
                    CurrentRosterEntry.snapshot_id == snapshot.id,
                    CurrentRosterEntry.team_id == team.id,
                )
                .order_by(
                    CurrentRosterEntry.lineup_slot_id,
                    CurrentRosterEntry.slot_index,
                )
            )
        )
        if snapshot is not None
        else []
    )
    player_ids = {entry.espn_player_id for entry in entries}
    players = (
        {
            player.espn_player_id: player
            for player in session.scalars(
                select(Player).where(Player.espn_player_id.in_(player_ids))
            )
        }
        if player_ids
        else {}
    )
    by_slot: dict[int, list[CurrentRosterEntry]] = defaultdict(list)
    for entry in entries:
        by_slot[entry.lineup_slot_id].append(entry)

    rows: list[RosterSlotOut] = []
    configured_ids: set[int] = set()
    for slot_id, count in _configured_slots(league):
        configured_ids.add(slot_id)
        occupied = by_slot.get(slot_id, [])
        for index in range(max(count, len(occupied))):
            entry = occupied[index] if index < len(occupied) else None
            rows.append(_roster_slot(slot_id, index, entry, players))
    for slot_id in sorted(set(by_slot) - configured_ids, key=slot_sort_key):
        rows.extend(
            _roster_slot(slot_id, index, entry, players)
            for index, entry in enumerate(by_slot[slot_id])
        )
    return (
        [row for row in rows if row.section == "starters"],
        [row for row in rows if row.section == "bench"],
        [row for row in rows if row.section == "ir"],
    )


def _team_out(session: Session, team_id: int | None) -> TeamOut | None:
    team = session.get(Team, team_id) if team_id is not None else None
    return TeamOut.model_validate(team) if team is not None else None


def _current_matchup(
    session: Session,
    league: League,
    team: Team,
    snapshot: CurrentRosterSnapshot | None,
) -> TeamMatchupOut | None:
    matchup_period = league.current_matchup_period
    if matchup_period is None and snapshot is not None:
        matchup_period = snapshot.matchup_period
    if matchup_period is None:
        return None
    matchup = session.scalar(
        select(Matchup).where(
            Matchup.league_id == league.id,
            Matchup.week == matchup_period,
            (Matchup.home_team_id == team.id) | (Matchup.away_team_id == team.id),
        )
    )
    if matchup is None:
        return None

    kickoff_at = None
    if snapshot is not None:
        team_ids = {
            team_id
            for team_id in (matchup.home_team_id, matchup.away_team_id)
            if team_id is not None
        }
        kickoffs = list(
            session.scalars(
                select(CurrentRosterEntry.kickoff_at).where(
                    CurrentRosterEntry.snapshot_id == snapshot.id,
                    CurrentRosterEntry.team_id.in_(team_ids),
                    CurrentRosterEntry.kickoff_at.is_not(None),
                )
            )
        ) if team_ids else []
        kickoff_at = min(kickoffs) if kickoffs else None

    return TeamMatchupOut(
        matchup_period=matchup_period,
        scoring_period=league.current_scoring_period,
        is_playoff=matchup.is_playoff,
        home=TeamMatchupSideOut(
            team=_team_out(session, matchup.home_team_id),
            points=matchup.home_points,
            projected_points=matchup.home_projected_points,
        ),
        away=TeamMatchupSideOut(
            team=_team_out(session, matchup.away_team_id),
            points=matchup.away_points,
            projected_points=matchup.away_projected_points,
        ),
        next_kickoff_at=kickoff_at,
    )


def build_team_detail(session: Session, league: League, team: Team) -> TeamDetailOut:
    snapshot = session.scalar(
        select(CurrentRosterSnapshot).where(
            CurrentRosterSnapshot.league_id == league.id
        )
    )
    starters, bench, injured_reserve = _build_roster_sections(
        session, league, team, snapshot
    )
    if snapshot is None:
        roster_status = "unavailable"
    elif (
        snapshot.scoring_period != league.current_scoring_period
        or league.last_sync_ok is False
    ):
        roster_status = "stale"
    else:
        roster_status = "current"
    account = session.get(Account, league.account_id) if league.account_id else None
    scoring = classify_scoring(league.scoring_json) if league.scoring_json else None
    return TeamDetailOut(
        league=build_league_out(session, league),
        account_label=account.label if account else None,
        scoring=scoring,
        team=TeamOut.model_validate(team),
        current_scoring_period=league.current_scoring_period,
        current_matchup_period=league.current_matchup_period,
        roster_status=roster_status,
        roster_synced_at=snapshot.synced_at if snapshot else None,
        starters=starters,
        bench=bench,
        ir=injured_reserve,
        matchup=_current_matchup(session, league, team, snapshot),
    )
