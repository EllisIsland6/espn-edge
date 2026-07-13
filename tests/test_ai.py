"""Phase 4 AI layer tests — all offline (fake LLM client; never calls Anthropic).

Covers: disabled-without-key behavior, input-hash caching, regenerate, pydantic
schema validation, grounding inputs, and a full-league draft-recap run that caches.
"""

import pytest
from fastapi.testclient import TestClient

from api import ai_schemas
from api.config import get_settings
from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.main import app
from api.models import Account, AiReport, League, Team
from api.services import ai_inputs
from api.services.ai import AiError, AiService

from .conftest import FakeEspn, load_fixture

client = TestClient(app)


class FakeLlmClient:
    """Returns a canned, schema-valid dict per report kind; counts calls (class-level
    so it survives the per-request AiService construction in the API tests)."""

    calls = 0
    invalid = False  # when True, return a schema-invalid dict to exercise validation
    raise_ai_error = False  # when True, simulate what AnthropicLlmClient raises on failure

    def __init__(self, api_key=None):
        pass

    def complete_json(self, *, model, system, user, schema, max_tokens):
        FakeLlmClient.calls += 1
        if FakeLlmClient.raise_ai_error:
            raise AiError("model output could not be parsed")
        if FakeLlmClient.invalid:
            return {"nonsense": True}
        canned = {
            ai_schemas.DraftRecap: {
                "strategy_label": "Balanced/BPA", "grade": "B", "confidence": "medium",
                "summary": "Balanced build.", "key_values": [], "key_reaches": [],
            },
            ai_schemas.LeagueBrief: {
                "difficulty_tier": "Average", "narrative": "Middle of the pack.",
                "exploit_plan": ["a", "b", "c"],
            },
            ai_schemas.AdvantageVerdict: {
                "verdict_label": "Neutral", "paragraph": "Even footing.",
                "highest_leverage_move": "Work the waiver wire.",
            },
            ai_schemas.WeeklyRecap: {"headline": "Week", "body": "Recap.", "luck_notes": [], "waiver_highlights": []},
            ai_schemas.TradeFinder: {"proposals": [], "note": "Advisory only."},
        }
        return canned[schema]


# --------------------------------------------------------------------------- #
# AiService unit tests (injected fake client; enabled regardless of env key)
# --------------------------------------------------------------------------- #
def _league(session) -> League:
    lg = League(espn_league_id="900", season=2026, is_public=True, lifecycle="drafted")
    session.add(lg)
    session.flush()
    return lg


def test_generate_caches_by_input_hash_and_regenerate_busts(db_session):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    lg = _league(db_session)
    svc = AiService(db_session, client=FakeLlmClient())

    a = svc.generate(kind="draft_recap", scope="team", league_id=lg.id, model="m", facts={"x": 1})
    assert FakeLlmClient.calls == 1
    b = svc.generate(kind="draft_recap", scope="team", league_id=lg.id, model="m", facts={"x": 1})
    assert FakeLlmClient.calls == 1  # cache hit — no second call
    assert a == b
    svc.generate(kind="draft_recap", scope="team", league_id=lg.id, model="m", facts={"x": 1}, force=True)
    assert FakeLlmClient.calls == 2  # force regenerates
    svc.generate(kind="draft_recap", scope="team", league_id=lg.id, model="m", facts={"x": 2})
    assert FakeLlmClient.calls == 3  # changed facts → new hash → new call


def test_generate_validates_against_schema(db_session):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = True
    lg = _league(db_session)
    svc = AiService(db_session, client=FakeLlmClient())
    with pytest.raises(AiError):
        svc.generate(kind="league_brief", scope="league", league_id=lg.id, model="m", facts={})
    FakeLlmClient.invalid = False
    # nothing persisted for the failed generation
    assert db_session.query(AiReport).count() == 0


def test_generate_disabled_without_key(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    svc = AiService(db_session)  # no injected client, no key
    assert svc.enabled is False


# --------------------------------------------------------------------------- #
# Grounding: input builders emit DB facts only
# --------------------------------------------------------------------------- #
@pytest.fixture
def synced_league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        acct = Account(label="Main", swid="{AAAA-1111}", espn_s2_encrypted=encrypt("s2"))
        session.add(acct)
        session.flush()
        lg = League(espn_league_id="111", season=2026, account_id=acct.id, is_public=False)
        session.add(lg)
        session.flush()
        from api.services.sync import SyncService

        SyncService(
            session,
            espn=FakeEspn(load_fixture("public_league.json"), load_fixture("players_pool.json")),
        ).sync_league(lg)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_draft_recap_input_is_grounded(synced_league_id):
    session = SessionLocal()
    try:
        lg = session.get(League, synced_league_id)
        team = session.scalar(select_team(lg.id, 1))
        facts = ai_inputs.draft_recap_input(session, lg, team)
        assert facts["team"]["espn_team_id"] == 1
        assert facts["picks"], "expected the team's picks as facts"
        p = facts["picks"][0]
        assert {"overall", "round", "pos", "player", "adp", "value_delta", "auto"} <= set(p)
        assert facts["fingerprint"]["total_picks"] == len(facts["picks"])
        # Phase 10: ADP + value delta come from the persisted DraftPick, not a live recompute.
        # Team 1's first pick is player 1001 (ADP 3.4) at overall 1 → delta 3.4 - 1 = 2.4.
        assert p["overall"] == 1 and p["adp"] == 3.4 and p["value_delta"] == 2.4
    finally:
        session.close()


def select_team(league_id, espn_team_id):
    from sqlalchemy import select

    return select(Team).where(Team.league_id == league_id, Team.espn_team_id == espn_team_id)


# --------------------------------------------------------------------------- #
# API: disabled without a key
# --------------------------------------------------------------------------- #
def test_api_ai_status_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    r = client.get("/api/ai/status").json()
    assert r["enabled"] is False
    assert r["standard_model"] and r["bulk_model"]


def test_api_draft_recaps_disabled_no_crash(synced_league_id, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    g = client.get(f"/api/leagues/{synced_league_id}/ai/draft-recaps").json()
    assert g["enabled"] is False and g["reports"] == []
    p = client.post(f"/api/leagues/{synced_league_id}/ai/draft-recaps").json()
    assert p["enabled"] is False


# --------------------------------------------------------------------------- #
# API: enabled path (fake client), full-league draft recap completes + caches (AC)
# --------------------------------------------------------------------------- #
def test_api_full_league_draft_recap_completes_and_caches(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr("api.services.ai.AnthropicLlmClient", FakeLlmClient)

    first = client.post(f"/api/leagues/{synced_league_id}/ai/draft-recaps").json()
    assert first["enabled"] is True
    assert len(first["reports"]) == 4  # 4 teams have picks in the toy fixture
    assert FakeLlmClient.calls == 4
    assert {r["strategy_label"] for r in first["reports"]} <= set(ai_schemas.StrategyLabel.__args__)

    # Re-POST without force → all cache hits, no new model calls.
    again = client.post(f"/api/leagues/{synced_league_id}/ai/draft-recaps").json()
    assert len(again["reports"]) == 4
    assert FakeLlmClient.calls == 4

    # GET returns the stored per-team recaps.
    got = client.get(f"/api/leagues/{synced_league_id}/ai/draft-recaps").json()
    assert got["enabled"] is True and len(got["reports"]) == 4
    assert all("espn_team_id" in r for r in got["reports"])


def test_api_league_brief_and_verdict_generate(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr("api.services.ai.AnthropicLlmClient", FakeLlmClient)

    brief = client.post(f"/api/leagues/{synced_league_id}/ai/league-brief").json()
    assert brief["enabled"] is True and brief["content"]["difficulty_tier"] == "Average"

    verdict = client.post(f"/api/leagues/{synced_league_id}/ai/advantage-verdict").json()
    assert verdict["enabled"] is True and verdict["content"]["verdict_label"] == "Neutral"

    # GET brief returns stored content, not stale (inputs unchanged).
    got = client.get(f"/api/leagues/{synced_league_id}/ai/league-brief").json()
    assert got["content"]["difficulty_tier"] == "Average" and got["stale"] is False


# --------------------------------------------------------------------------- #
# Finding 1: parse/validation failures become AiError, not a 500
# --------------------------------------------------------------------------- #
def _anthropic_client_with(behavior):
    """AnthropicLlmClient with its SDK swapped for a fake whose parse() runs `behavior`."""
    from api.services.ai import AnthropicLlmClient

    class _Messages:
        def parse(self, **kwargs):
            return behavior()

    class _Sdk:
        messages = _Messages()

    c = AnthropicLlmClient(api_key="x")  # no network at construction
    c._client = _Sdk()
    return c


class _Resp:
    def __init__(self, parsed):
        self.parsed_output = parsed


def _call(c):
    return c.complete_json(
        model="m", system="s", user="u", schema=ai_schemas.LeagueBrief, max_tokens=64
    )


def test_anthropic_client_generic_exception_becomes_ai_error():
    def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(AiError) as ei:
        _call(_anthropic_client_with(boom))
    # message is user-safe (no key/prompt/raw output)
    assert "could not be parsed" in str(ei.value)


def test_anthropic_client_validation_error_becomes_ai_error():
    from pydantic import ValidationError

    def bad_validate():
        ai_schemas.LeagueBrief.model_validate({})  # raises ValidationError (missing fields)

    with pytest.raises((AiError,)):
        _call(_anthropic_client_with(bad_validate))
    # sanity: the trigger really is a ValidationError
    with pytest.raises(ValidationError):
        ai_schemas.LeagueBrief.model_validate({})


def test_anthropic_client_missing_parsed_output_becomes_ai_error():
    with pytest.raises(AiError) as ei:
        _call(_anthropic_client_with(lambda: _Resp(None)))
    assert "no parsed output" in str(ei.value)


def test_anthropic_client_success_returns_dict():
    valid = ai_schemas.LeagueBrief(difficulty_tier="Average", narrative="n", exploit_plan=["a"])
    out = _call(_anthropic_client_with(lambda: _Resp(valid)))
    assert out["difficulty_tier"] == "Average" and out["exploit_plan"] == ["a"]


def test_api_returns_error_envelope_not_500_on_model_failure(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    FakeLlmClient.raise_ai_error = True
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr("api.services.ai.AnthropicLlmClient", FakeLlmClient)
    try:
        r = client.post(f"/api/leagues/{synced_league_id}/ai/league-brief")
        assert r.status_code == 200  # not a raw 500
        body = r.json()
        assert body["enabled"] is True and body["content"] is None
        assert body["error"] and "parsed" in body["error"]
    finally:
        FakeLlmClient.raise_ai_error = False


# --------------------------------------------------------------------------- #
# Phase 18: AI grounding uses the Edge Index model (offline)
# --------------------------------------------------------------------------- #
def test_advantage_verdict_input_uses_edge_index(synced_league_id):
    session = SessionLocal()
    try:
        lg = session.get(League, synced_league_id)
        me = session.get(Team, lg.my_team_id)
        facts = ai_inputs.advantage_verdict_input(session, lg, me)["me"]
        # Edge Index (primary) + MyEdge + LeagueSoftness breakdown present.
        assert {
            "edge_index_score", "edge_index_grade", "edge_index_verdict", "edge_index_components",
            "my_edge_score", "my_edge_components",
            "league_softness_score", "league_softness_components",
            "playoff_odds",
        } <= set(facts)
        # Legacy renamed; no bare edge_score/grade/verdict keys that could confuse the model.
        assert "legacy_edge_score" in facts and "legacy_grade" in facts
        assert "edge_score" not in facts and "grade" not in facts and "verdict" not in facts
        # The synced league scores an Edge Index for my team → components are populated.
        assert facts["edge_index_score"] is not None
        assert {c["key"] for c in facts["edge_index_components"]} == {"my_edge", "league_softness"}
    finally:
        session.close()


def test_league_brief_input_uses_edge_index(synced_league_id):
    session = SessionLocal()
    try:
        lg = session.get(League, synced_league_id)
        facts = ai_inputs.league_brief_input(session, lg)
        assert facts["teams"], "expected team facts"
        for t in facts["teams"]:
            assert "edge_index_score" in t and "legacy_edge_score" in t
            assert "edge_score" not in t  # no bare legacy key
    finally:
        session.close()


def test_ai_prompts_lead_with_edge_index():
    from api.services.ai import _TASK

    brief, verdict = _TASK["league_brief"], _TASK["advantage_verdict"]
    # Both edge-driven prompts now lead with the Edge Index and mention legacy as secondary.
    assert "edge_index_score" in brief and "legacy_edge_score" in brief
    assert "edge_index_score" in verdict and "legacy_edge_score" in verdict
    # No longer lead with the old within-league phrasing.
    assert "from its edge_score/record/standing/points" not in verdict
    assert "grounded in the teams' edge_scores" not in brief


def test_schema_version_bumped_and_busts_cache(monkeypatch):
    from api.ai_config import SCHEMA_VERSION
    from api.services.ai import compute_input_hash

    assert SCHEMA_VERSION == "v2"
    facts = {"team": "me", "edge_index_score": 68.0}
    v2 = compute_input_hash("advantage_verdict", "m", facts)
    # The same facts under the old version hash to a different value → cache is busted.
    monkeypatch.setattr("api.services.ai.SCHEMA_VERSION", "v1")
    v1 = compute_input_hash("advantage_verdict", "m", facts)
    assert v1 != v2
