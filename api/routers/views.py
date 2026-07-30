"""Read-only view endpoints feeding the Phase 2 UI (SPEC 8).

Every number comes straight from the DB — no analytics computed here (that's Phase 3);
metric fields are returned null until then. No ESPN traffic in this router.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Account, DraftPick, League, Matchup, Player, Team, Transaction
from ..schemas import (
    AchievementOut,
    AllPlayOut,
    DraftPickOut,
    EdgeComponent,
    EdgeIndexComponentOut,
    EdgeIndexOut,
    LeagueOverview,
    LeagueSoftnessComponentOut,
    LeagueSoftnessOut,
    LineupEfficiencyOut,
    MatchupOut,
    MetricMomentumOut,
    MyEdgeComponentOut,
    MyEdgeOut,
    PortfolioRow,
    PortfolioSummary,
    TeamDetailOut,
    TeamOut,
    TransactionOut,
)
from ..services.metrics import (
    read_all_play,
    read_edge_index,
    read_league_softness,
    read_lineup_efficiency,
    read_my_edge,
    team_components,
    team_edge,
)
from ..services.momentum import EDGE_SCORE, metric_momentum, team_achievements
from ..services.parse import classify_scoring
from ..services.portfolio import build_portfolio_rows, build_summary
from ..services.read_models import build_league_out, build_team_detail

router = APIRouter(tags=["views"])


def _scoring_label(league: League) -> str | None:
    if not league.scoring_json:
        return None
    return classify_scoring(league.scoring_json)


def _standing_key(t: Team):
    # None standings sort last; otherwise ascending seed.
    return (t.standing is None, t.standing or 0)


@router.get("/api/portfolio", response_model=list[PortfolioRow])
def portfolio(session: Session = Depends(get_session)) -> list[PortfolioRow]:
    return build_portfolio_rows(session)


@router.get("/api/portfolio/summary", response_model=PortfolioSummary)
def portfolio_summary(session: Session = Depends(get_session)) -> PortfolioSummary:
    """Aggregate portfolio numbers derived from the same rows the board renders."""
    return build_summary(build_portfolio_rows(session))


def _get_league(session: Session, league_id: int) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(404, "league not found")
    return league


@router.get("/api/leagues/{league_id}/overview", response_model=LeagueOverview)
def league_overview(league_id: int, session: Session = Depends(get_session)) -> LeagueOverview:
    league = _get_league(session, league_id)
    account = session.get(Account, league.account_id) if league.account_id else None
    teams = sorted(
        session.scalars(select(Team).where(Team.league_id == league.id)),
        key=_standing_key,
    )
    edge = team_edge(session, league.id, league.my_team_id)
    components = team_components(session, league.id, league.my_team_id)
    momentum = metric_momentum(
        session, league.id, league.my_team_id, EDGE_SCORE, edge.edge_score
    )
    achievements = team_achievements(session, league, league.my_team_id)
    return LeagueOverview(
        league=build_league_out(session, league),
        account_label=account.label if account else None,
        scoring=_scoring_label(league),
        teams=[TeamOut.model_validate(t) for t in teams],
        edge_score=edge.edge_score,
        grade=edge.grade,
        verdict=edge.verdict,
        playoff_odds=edge.playoff_odds,
        momentum=MetricMomentumOut(**vars(momentum)),
        achievements=[AchievementOut(**vars(item)) for item in achievements],
        components=[EdgeComponent.model_validate(c) for c in components],
    )


@router.get("/api/leagues/{league_id}/teams", response_model=list[TeamOut])
def league_teams(league_id: int, session: Session = Depends(get_session)) -> list[Team]:
    _get_league(session, league_id)
    teams = session.scalars(select(Team).where(Team.league_id == league_id))
    return sorted(teams, key=_standing_key)


@router.get(
    "/api/leagues/{league_id}/teams/{team_id}",
    response_model=TeamDetailOut,
)
def league_team_detail(
    league_id: int,
    team_id: int,
    session: Session = Depends(get_session),
) -> TeamDetailOut:
    league = _get_league(session, league_id)
    team = session.scalar(
        select(Team).where(Team.id == team_id, Team.league_id == league.id)
    )
    if team is None:
        raise HTTPException(404, "team not found in league")
    return build_team_detail(session, league, team)


@router.get("/api/leagues/{league_id}/draft", response_model=list[DraftPickOut])
def league_draft(league_id: int, session: Session = Depends(get_session)) -> list[DraftPickOut]:
    _get_league(session, league_id)
    # Join players so the board shows names/positions instead of raw IDs (Phase 10).
    rows = session.execute(
        select(DraftPick, Player.name, Player.position)
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(DraftPick.league_id == league_id)
        .order_by(DraftPick.overall)
    ).all()
    return [
        DraftPickOut(
            overall=p.overall,
            round=p.round,
            round_pick=p.round_pick,
            team_id=p.team_id,
            espn_player_id=p.espn_player_id,
            keeper=p.keeper,
            autodraft=p.autodraft,
            bid_amount=p.bid_amount,
            adp_at_draft=p.adp_at_draft,
            value_delta=p.value_delta,
            player_name=name,
            player_position=pos,
        )
        for p, name, pos in rows
    ]


@router.get("/api/leagues/{league_id}/matchups", response_model=list[MatchupOut])
def league_matchups(league_id: int, session: Session = Depends(get_session)) -> list[Matchup]:
    _get_league(session, league_id)
    return list(
        session.scalars(
            select(Matchup).where(Matchup.league_id == league_id).order_by(Matchup.week)
        )
    )


@router.get("/api/leagues/{league_id}/all-play", response_model=list[AllPlayOut])
def league_all_play(league_id: int, session: Session = Depends(get_session)) -> list[AllPlayOut]:
    _get_league(session, league_id)
    names = dict(
        session.execute(select(Team.id, Team.name).where(Team.league_id == league_id)).all()
    )
    return [
        AllPlayOut(
            team_id=r.team_id,
            team_name=names.get(r.team_id),
            wins=r.actual_wins,
            losses=r.actual_losses,
            ties=r.actual_ties,
            win_pct=r.actual_win_pct,
            all_play_wins=r.all_play_wins,
            all_play_losses=r.all_play_losses,
            all_play_ties=r.all_play_ties,
            all_play_win_pct=r.all_play_win_pct,
            luck_delta=r.luck_delta,
        )
        for r in read_all_play(session, league_id)
    ]


@router.get("/api/leagues/{league_id}/my-edge", response_model=list[MyEdgeOut])
def league_my_edge(league_id: int, session: Session = Depends(get_session)) -> list[MyEdgeOut]:
    _get_league(session, league_id)
    teams = {
        t.id: t
        for t in session.scalars(select(Team).where(Team.league_id == league_id))
    }
    return [
        MyEdgeOut(
            team_id=r.team_id,
            team_name=teams[r.team_id].name if r.team_id in teams else None,
            is_me=bool(teams[r.team_id].is_me) if r.team_id in teams else False,
            my_edge_score=r.my_edge_score,
            components=[
                MyEdgeComponentOut(
                    key=c.key, label=c.label, weight=c.weight, percentile=c.percentile
                )
                for c in r.components
            ],
        )
        for r in read_my_edge(session, league_id)
    ]


@router.get("/api/leagues/{league_id}/edge-index", response_model=list[EdgeIndexOut])
def league_edge_index(
    league_id: int, session: Session = Depends(get_session)
) -> list[EdgeIndexOut]:
    _get_league(session, league_id)
    teams = {
        t.id: t
        for t in session.scalars(select(Team).where(Team.league_id == league_id))
    }
    return [
        EdgeIndexOut(
            team_id=r.team_id,
            team_name=teams[r.team_id].name if r.team_id in teams else None,
            is_me=bool(teams[r.team_id].is_me) if r.team_id in teams else False,
            edge_index_score=r.edge_index_score,
            grade=r.grade,
            verdict=r.verdict,
            components=[
                EdgeIndexComponentOut(key=c.key, label=c.label, weight=c.weight, value=c.value)
                for c in r.components
            ],
        )
        for r in read_edge_index(session, league_id)
    ]


@router.get("/api/leagues/{league_id}/league-softness", response_model=list[LeagueSoftnessOut])
def league_softness(
    league_id: int, session: Session = Depends(get_session)
) -> list[LeagueSoftnessOut]:
    _get_league(session, league_id)
    teams = {
        t.id: t
        for t in session.scalars(select(Team).where(Team.league_id == league_id))
    }
    return [
        LeagueSoftnessOut(
            team_id=r.team_id,
            team_name=teams[r.team_id].name if r.team_id in teams else None,
            is_me=bool(teams[r.team_id].is_me) if r.team_id in teams else False,
            league_softness_score=r.league_softness_score,
            components=[
                LeagueSoftnessComponentOut(
                    key=c.key, label=c.label, weight=c.weight, percentile=c.percentile
                )
                for c in r.components
            ],
        )
        for r in read_league_softness(session, league_id)
    ]


@router.get("/api/leagues/{league_id}/lineup-efficiency", response_model=list[LineupEfficiencyOut])
def league_lineup_efficiency(
    league_id: int, session: Session = Depends(get_session)
) -> list[LineupEfficiencyOut]:
    _get_league(session, league_id)
    names = dict(
        session.execute(select(Team.id, Team.name).where(Team.league_id == league_id)).all()
    )
    return [
        LineupEfficiencyOut(
            team_id=r.team_id,
            team_name=names.get(r.team_id),
            lineup_efficiency=r.lineup_efficiency,
            started_points_avg=r.started_points_avg,
            optimal_points_avg=r.optimal_points_avg,
            points_left_on_bench_avg=r.points_left_on_bench_avg,
        )
        for r in read_lineup_efficiency(session, league_id)
    ]


@router.get("/api/leagues/{league_id}/activity", response_model=list[TransactionOut])
def league_activity(
    league_id: int, session: Session = Depends(get_session)
) -> list[TransactionOut]:
    _get_league(session, league_id)
    transactions = list(
        session.scalars(
            select(Transaction)
            .where(Transaction.league_id == league_id)
            .order_by(Transaction.executed_at.desc().nullslast(), Transaction.id)
        )
    )
    player_ids = {
        player_id
        for transaction in transactions
        for player_id in (transaction.player_in, transaction.player_out)
        if player_id is not None
    }
    players = {
        player.espn_player_id: player
        for player in session.scalars(
            select(Player).where(Player.espn_player_id.in_(player_ids))
        )
    } if player_ids else {}

    return [
        TransactionOut(
            team_id=transaction.team_id,
            type=transaction.type,
            week=transaction.week,
            player_in=transaction.player_in,
            player_out=transaction.player_out,
            player_in_name=(players.get(transaction.player_in).name
                            if transaction.player_in in players else None),
            player_in_position=(players.get(transaction.player_in).position
                                if transaction.player_in in players else None),
            player_out_name=(players.get(transaction.player_out).name
                             if transaction.player_out in players else None),
            player_out_position=(players.get(transaction.player_out).position
                                 if transaction.player_out in players else None),
            bid=transaction.bid,
            executed_at=transaction.executed_at,
        )
        for transaction in transactions
    ]
