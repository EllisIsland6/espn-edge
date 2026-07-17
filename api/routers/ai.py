"""AI analysis endpoints (SPEC §7, §8.4 AI Brief).

Every endpoint degrades gracefully with no API key: GET returns empty content, POST
returns enabled=false — never a 500. No secrets in responses. See docs/phase-4-ai.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_config import (
    BULK_MODEL,
    KIND_ADVANTAGE_VERDICT,
    KIND_DRAFT_RECAP,
    KIND_LEAGUE_BRIEF,
    KIND_TRADE_FINDER,
    KIND_WEEKLY_RECAP,
    STANDARD_MODEL,
)
from ..db import get_session
from ..models import DraftPick, League, Team
from ..schemas import AiReportEnvelope, AiReportList, AiStatus
from ..services import ai_inputs
from ..services.ai import AiError, AiService, compute_input_hash

router = APIRouter(tags=["ai"])


@router.get("/api/ai/status", response_model=AiStatus)
def ai_status() -> AiStatus:
    svc = AiService(session=None)  # enabled check needs no DB
    return AiStatus(enabled=svc.enabled, standard_model=STANDARD_MODEL, bulk_model=BULK_MODEL)


def _get_league(session: Session, league_id: int) -> League:
    league = session.get(League, league_id)
    if league is None:
        raise HTTPException(404, "league not found")
    return league


def _my_team(session: Session, league: League) -> Team | None:
    if league.my_team_id is None:
        return None
    return session.get(Team, league.my_team_id)


def _single(
    svc: AiService, league: League, *, kind: str, scope: str, model: str, facts: dict,
    generate: bool, force: bool,
) -> AiReportEnvelope:
    """Shared GET/POST path for a single league/team-scoped report."""
    if not svc.enabled:
        return AiReportEnvelope(enabled=False, kind=kind)
    if generate:
        try:
            content = svc.generate(
                kind=kind, scope=scope, league_id=league.id, model=model, facts=facts, force=force
            )
        except AiError as exc:
            return AiReportEnvelope(enabled=True, kind=kind, error=str(exc))
        row = svc.latest(league.id, kind)
        return AiReportEnvelope(
            enabled=True, kind=kind, model=model, content=content,
            created_at=row.created_at if row else None, stale=False,
        )
    # GET: return the latest stored report + staleness vs current facts.
    row = svc.latest(league.id, kind)
    if row is None:
        return AiReportEnvelope(enabled=True, kind=kind, content=None)
    fresh = compute_input_hash(kind, row.model or model, facts)
    return AiReportEnvelope(
        enabled=True, kind=kind, model=row.model, content=row.content_json,
        created_at=row.created_at, stale=row.input_hash != fresh,
    )


# --- Draft recaps (per team) -----------------------------------------------
@router.get("/api/leagues/{league_id}/ai/draft-recaps", response_model=AiReportList)
def get_draft_recaps(league_id: int, session: Session = Depends(get_session)) -> AiReportList:
    _get_league(session, league_id)
    svc = AiService(session)
    if not svc.enabled:
        return AiReportList(enabled=False, kind=KIND_DRAFT_RECAP)
    # Dedupe stored recaps by team, keeping the most recent (all_reports is newest-first).
    seen: set[int] = set()
    reports: list[dict] = []
    for row in svc.all_reports(league_id, KIND_DRAFT_RECAP):
        tid = (row.content_json or {}).get("espn_team_id")
        if tid in seen:
            continue
        seen.add(tid)
        reports.append(row.content_json or {})
    return AiReportList(enabled=True, kind=KIND_DRAFT_RECAP, reports=reports)


@router.post("/api/leagues/{league_id}/ai/draft-recaps", response_model=AiReportList)
def generate_draft_recaps(
    league_id: int, force: bool = Query(False), session: Session = Depends(get_session)
) -> AiReportList:
    league = _get_league(session, league_id)
    svc = AiService(session)
    if not svc.enabled:
        return AiReportList(enabled=False, kind=KIND_DRAFT_RECAP)
    drafted_team_ids = [
        tid
        for (tid,) in session.execute(
            select(DraftPick.team_id).where(DraftPick.league_id == league_id).distinct()
        )
        if tid is not None
    ]
    reports: list[dict] = []
    try:
        for tid in drafted_team_ids:
            team = session.get(Team, tid)
            if team is None:
                continue
            facts = ai_inputs.draft_recap_input(session, league, team)
            content = svc.generate(
                kind=KIND_DRAFT_RECAP, scope="team", league_id=league_id,
                model=STANDARD_MODEL, facts=facts, force=force,
                extra={"espn_team_id": team.espn_team_id, "team_name": team.name},
            )
            reports.append(content)
    except AiError as exc:
        session.commit()
        return AiReportList(enabled=True, kind=KIND_DRAFT_RECAP, reports=reports, error=str(exc))
    session.commit()
    return AiReportList(enabled=True, kind=KIND_DRAFT_RECAP, reports=reports)


# --- League brief ----------------------------------------------------------
@router.get("/api/leagues/{league_id}/ai/league-brief", response_model=AiReportEnvelope)
def get_league_brief(league_id: int, session: Session = Depends(get_session)) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    facts = ai_inputs.league_brief_input(session, league) if svc.enabled else {}
    return _single(svc, league, kind=KIND_LEAGUE_BRIEF, scope="league",
                   model=STANDARD_MODEL, facts=facts, generate=False, force=False)


@router.post("/api/leagues/{league_id}/ai/league-brief", response_model=AiReportEnvelope)
def generate_league_brief(
    league_id: int, force: bool = Query(False), session: Session = Depends(get_session)
) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    facts = ai_inputs.league_brief_input(session, league) if svc.enabled else {}
    env = _single(svc, league, kind=KIND_LEAGUE_BRIEF, scope="league",
                  model=STANDARD_MODEL, facts=facts, generate=True, force=force)
    session.commit()
    return env


# --- Advantage verdict -----------------------------------------------------
@router.get("/api/leagues/{league_id}/ai/advantage-verdict", response_model=AiReportEnvelope)
def get_verdict(league_id: int, session: Session = Depends(get_session)) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    me = _my_team(session, league)
    if svc.enabled and me is None:
        return AiReportEnvelope(enabled=True, kind=KIND_ADVANTAGE_VERDICT,
                                error="no 'my team' detected in this league")
    facts = ai_inputs.advantage_verdict_input(session, league, me) if (svc.enabled and me) else {}
    return _single(svc, league, kind=KIND_ADVANTAGE_VERDICT, scope="team",
                   model=STANDARD_MODEL, facts=facts, generate=False, force=False)


@router.post("/api/leagues/{league_id}/ai/advantage-verdict", response_model=AiReportEnvelope)
def generate_verdict(
    league_id: int, force: bool = Query(False), session: Session = Depends(get_session)
) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    me = _my_team(session, league)
    if svc.enabled and me is None:
        return AiReportEnvelope(enabled=True, kind=KIND_ADVANTAGE_VERDICT,
                                error="no 'my team' detected in this league")
    facts = ai_inputs.advantage_verdict_input(session, league, me) if (svc.enabled and me) else {}
    env = _single(svc, league, kind=KIND_ADVANTAGE_VERDICT, scope="team",
                  model=STANDARD_MODEL, facts=facts, generate=True, force=force)
    session.commit()
    return env


# --- Weekly recap (per week; Phase 20) -------------------------------------
@router.get("/api/leagues/{league_id}/ai/weekly-recap", response_model=AiReportEnvelope)
def get_weekly_recap(
    league_id: int, week: int = Query(...), session: Session = Depends(get_session)
) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    if not svc.enabled:
        return AiReportEnvelope(enabled=False, kind=KIND_WEEKLY_RECAP)
    row = svc.latest_for_week(league.id, KIND_WEEKLY_RECAP, week)
    if row is None:
        return AiReportEnvelope(enabled=True, kind=KIND_WEEKLY_RECAP, content=None)
    # Staleness is measured against THIS week's current facts (facts include the week).
    facts = ai_inputs.weekly_recap_input(session, league, week)
    fresh = compute_input_hash(KIND_WEEKLY_RECAP, row.model or BULK_MODEL, facts)
    return AiReportEnvelope(
        enabled=True, kind=KIND_WEEKLY_RECAP, model=row.model,
        content=row.content_json, created_at=row.created_at, stale=row.input_hash != fresh,
    )


@router.post("/api/leagues/{league_id}/ai/weekly-recap", response_model=AiReportEnvelope)
def generate_weekly_recap(
    league_id: int, week: int = Query(...), force: bool = Query(False),
    session: Session = Depends(get_session),
) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    if not svc.enabled:
        return AiReportEnvelope(enabled=False, kind=KIND_WEEKLY_RECAP)
    facts = ai_inputs.weekly_recap_input(session, league, week)
    try:
        # extra persists the week in content_json so GET can find the right report.
        content = svc.generate(
            kind=KIND_WEEKLY_RECAP, scope="league", league_id=league.id,
            model=BULK_MODEL, facts=facts, force=force, extra={"week": week},
        )
    except AiError as exc:
        session.commit()
        return AiReportEnvelope(enabled=True, kind=KIND_WEEKLY_RECAP, error=str(exc))
    row = svc.latest_for_week(league.id, KIND_WEEKLY_RECAP, week)
    session.commit()
    return AiReportEnvelope(
        enabled=True, kind=KIND_WEEKLY_RECAP, model=BULK_MODEL, content=content,
        created_at=row.created_at if row else None, stale=False,
    )


# --- Trade finder (per opponent; Phase 21) ---------------------------------
def _valid_opponent(session: Session, league: League, opponent_team_id: int) -> Team | None:
    """The opponent team iff it belongs to this league and is not the detected my-team.
    Cross-league ids, unknown ids, and the user's own team all resolve to None."""
    opponent = session.get(Team, opponent_team_id)
    if opponent is None or opponent.league_id != league.id or opponent.is_me:
        return None
    return opponent


_TRADE_BAD_TARGET = "need a detected 'my team' and a valid opponent team in this league"


@router.get("/api/leagues/{league_id}/ai/trade-finder", response_model=AiReportEnvelope)
def get_trade_finder(
    league_id: int, opponent_team_id: int = Query(...), session: Session = Depends(get_session)
) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    if not svc.enabled:
        return AiReportEnvelope(enabled=False, kind=KIND_TRADE_FINDER)
    me = _my_team(session, league)
    opponent = _valid_opponent(session, league, opponent_team_id)
    if me is None or opponent is None:
        return AiReportEnvelope(enabled=True, kind=KIND_TRADE_FINDER, error=_TRADE_BAD_TARGET)
    # Only the report tagged for THIS opponent — legacy/unscoped reports are ignored.
    row = svc.latest_for_opponent(league.id, KIND_TRADE_FINDER, opponent.id)
    if row is None:
        return AiReportEnvelope(enabled=True, kind=KIND_TRADE_FINDER, content=None)
    facts = ai_inputs.trade_finder_input(session, league, me, opponent)
    fresh = compute_input_hash(KIND_TRADE_FINDER, row.model or STANDARD_MODEL, facts)
    return AiReportEnvelope(
        enabled=True, kind=KIND_TRADE_FINDER, model=row.model,
        content=row.content_json, created_at=row.created_at, stale=row.input_hash != fresh,
    )


@router.post("/api/leagues/{league_id}/ai/trade-finder", response_model=AiReportEnvelope)
def generate_trade_finder(
    league_id: int, opponent_team_id: int = Query(...), force: bool = Query(False),
    session: Session = Depends(get_session),
) -> AiReportEnvelope:
    league = _get_league(session, league_id)
    svc = AiService(session)
    if not svc.enabled:
        return AiReportEnvelope(enabled=False, kind=KIND_TRADE_FINDER)
    me = _my_team(session, league)
    opponent = _valid_opponent(session, league, opponent_team_id)
    if me is None or opponent is None:
        return AiReportEnvelope(enabled=True, kind=KIND_TRADE_FINDER, error=_TRADE_BAD_TARGET)

    facts = ai_inputs.trade_finder_input(session, league, me, opponent)
    fresh = compute_input_hash(KIND_TRADE_FINDER, STANDARD_MODEL, facts)
    existing = svc.latest_for_opponent(league.id, KIND_TRADE_FINDER, opponent.id)
    # Reuse ONLY a report already tagged for this opponent whose facts match — never a
    # legacy/unscoped report that happens to share the input_hash (Phase 21 cache edge case).
    if not force and existing is not None and existing.input_hash == fresh:
        content, row = existing.content_json, existing
    else:
        try:
            # force=True skips generate's hash-only cache so a legacy row can't be served; the
            # store step overwrites-or-creates a report correctly tagged with this opponent.
            content = svc.generate(
                kind=KIND_TRADE_FINDER, scope="team", league_id=league.id,
                model=STANDARD_MODEL, facts=facts, force=True,
                extra={"opponent_team_id": opponent.id, "opponent_name": opponent.name},
            )
        except AiError as exc:
            session.commit()
            return AiReportEnvelope(enabled=True, kind=KIND_TRADE_FINDER, error=str(exc))
        row = svc.latest_for_opponent(league.id, KIND_TRADE_FINDER, opponent.id)
    session.commit()
    return AiReportEnvelope(
        enabled=True, kind=KIND_TRADE_FINDER, model=row.model if row else STANDARD_MODEL,
        content=content, created_at=row.created_at if row else None, stale=False,
    )
