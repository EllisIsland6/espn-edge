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
from api.models import (
    Account,
    AiReport,
    DraftPick,
    League,
    LineupSlot,
    Matchup,
    Player,
    Team,
    Transaction,
)
from api.services import ai_inputs
from api.services.ai import AiError, AiService
from api.tenancy import resolve_tenant_id

from .conftest import FakeEspn, load_fixture

client = TestClient(app)


class FakeLlmClient:
    """Returns a canned, schema-valid dict per report kind; counts calls (class-level
    so it survives the per-request AiService construction in the API tests)."""

    calls = 0
    invalid = False  # when True, return a schema-invalid dict to exercise validation
    raise_ai_error = False  # when True, simulate what AnthropicLlmClient raises on failure
    trade_override = None  # when set, the TradeFinder payload the fake returns (Phase 22)

    def __init__(self, api_key=None):
        pass

    def complete_json(self, *, model, system, user, schema, max_tokens, reservation=None):
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
            ai_schemas.TradeFinder: FakeLlmClient.trade_override
            if (schema is ai_schemas.TradeFinder and FakeLlmClient.trade_override is not None)
            else {"proposals": [], "note": "Advisory only."},
        }
        return canned[schema]


# --------------------------------------------------------------------------- #
# AiService unit tests (injected fake client; enabled regardless of env key)
# --------------------------------------------------------------------------- #
def _league(session) -> League:
    lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="900", season=2026, is_public=True, lifecycle="drafted")
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
        lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="111", season=2026, account_id=acct.id, is_public=False)
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
        last_kwargs = None

        def parse(self, **kwargs):
            self.last_kwargs = kwargs
            return behavior()

    class _Sdk:
        messages = _Messages()

    c = AnthropicLlmClient(api_key="x")  # no network at construction
    c._client = _Sdk()
    return c


class _Resp:
    def __init__(self, parsed, *, stop_reason="end_turn", content=None):
        self.parsed_output = parsed
        self.stop_reason = stop_reason
        self.content = content or []


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


def test_anthropic_client_disables_thinking_for_structured_reports():
    valid = ai_schemas.LeagueBrief(difficulty_tier="Average", narrative="n", exploit_plan=["a"])
    anthropic_client = _anthropic_client_with(lambda: _Resp(valid))

    _call(anthropic_client)

    assert anthropic_client._client.messages.last_kwargs["thinking"] == {"type": "disabled"}


def test_anthropic_client_token_limit_has_actionable_error():
    with pytest.raises(AiError) as ei:
        _call(_anthropic_client_with(lambda: _Resp(None, stop_reason="max_tokens")))
    assert "output token limit" in str(ei.value)


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

    assert SCHEMA_VERSION == "v4"  # Phase 22 bump
    facts = {"team": "me", "edge_index_score": 68.0}
    v4 = compute_input_hash("advantage_verdict", "m", facts)
    # The same facts under the previous version hash to a different value → cache is busted.
    monkeypatch.setattr("api.services.ai.SCHEMA_VERSION", "v3")
    v3 = compute_input_hash("advantage_verdict", "m", facts)
    assert v3 != v4


# --------------------------------------------------------------------------- #
# Phase 19: weekly recap grounded on all-play, luck & waivers (offline)
# --------------------------------------------------------------------------- #
def _make_weekly_league(session) -> League:
    lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="960", season=2026, is_public=True, lifecycle="in_season",
                size=4, playoff_team_count=2)
    session.add(lg)
    session.flush()
    teams = []
    for espn, w, losses, pf in [(1, 1, 0, 120.0), (2, 0, 1, 100.0), (3, 1, 0, 90.0), (4, 0, 1, 80.0)]:
        t = Team(league_id=lg.id, espn_team_id=espn, name=f"W{espn}", is_me=(espn == 1),
                 wins=w, losses=losses, ties=0, points_for=pf, points_against=0.0, standing=espn)
        session.add(t)
        teams.append(t)
    session.flush()
    lg.my_team_id = teams[0].id
    ids = {t.espn_team_id: t.id for t in teams}
    # Week 1 completed matchups.
    session.add_all([
        Matchup(league_id=lg.id, week=1, home_team_id=ids[1], away_team_id=ids[2],
                home_points=120.0, away_points=100.0),
        Matchup(league_id=lg.id, week=1, home_team_id=ids[3], away_team_id=ids[4],
                home_points=90.0, away_points=80.0),
    ])
    # Players + a week-1 waiver transaction for team 1 (week 2 has none).
    session.add_all([
        Player(espn_player_id=501, name="Adds Player", position="RB"),
        Player(espn_player_id=502, name="Drops Player", position="WR"),
    ])
    session.flush()
    session.add(Transaction(league_id=lg.id, team_id=ids[1], type="waiver", week=1,
                            player_in=501, player_out=502, bid=17))
    session.flush()
    # metrics power season all-play/luck.
    from api.services import metrics
    metrics.recompute_league(session, lg)
    session.flush()
    return lg


def test_weekly_recap_input_grounds_all_play_luck_waivers(db_session):
    lg = _make_weekly_league(db_session)
    facts = ai_inputs.weekly_recap_input(db_session, lg, 1)
    assert {"league", "week", "matchups", "week_all_play", "season_all_play", "transactions"} <= set(facts)

    # Per-game winner/loser/margin from the DB scores (W1 beat W2 by 20).
    g = next(g for g in facts["matchups"] if g["home"] == "W1")
    assert g["winner"] == "W1" and g["loser"] == "W2" and g["margin"] == 20.0 and g["tie"] is False

    # Per-week all-play: W1 posted the top score → undefeated all-play this week.
    ap_me = next(r for r in facts["week_all_play"] if r["team"] == "W1")
    assert ap_me["all_play_wins"] == 3 and ap_me["all_play_losses"] == 0
    assert ap_me["all_play_win_pct"] == 1.0

    # Season all-play/luck context present.
    assert facts["season_all_play"] and all(
        {"team", "all_play_win_pct", "luck_delta"} <= set(r) for r in facts["season_all_play"]
    )

    # Waiver highlights resolved from persisted Transaction + Player only.
    assert len(facts["transactions"]) == 1
    tx = facts["transactions"][0]
    assert tx["team"] == "W1" and tx["type"] == "waiver" and tx["bid"] == 17
    assert tx["player_in"] == "Adds Player" and tx["player_out"] == "Drops Player"


def test_weekly_recap_input_empty_transaction_week_is_empty_list(db_session):
    lg = _make_weekly_league(db_session)
    facts = ai_inputs.weekly_recap_input(db_session, lg, 2)  # week 2 has no transactions
    assert facts["transactions"] == []


def test_weekly_recap_prompt_grounds_all_play_and_waivers():
    from api.services.ai import _TASK

    wk = _TASK["weekly_recap"]
    assert "week_all_play" in wk and "season_all_play" in wk  # luck grounding
    assert "transactions" in wk and "waiver_highlights" in wk  # waiver grounding
    assert "quiet transaction week" in wk  # empty-feed instruction, no invention


# --------------------------------------------------------------------------- #
# Phase 20: weekly recap per-week GET/POST (offline, fake LLM)
# --------------------------------------------------------------------------- #
@pytest.fixture
def weekly_league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        lg = _make_weekly_league(session)
        session.commit()
        lid = lg.id
    finally:
        session.close()
    yield lid
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _enable_fake(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr("api.services.ai.AnthropicLlmClient", FakeLlmClient)


def test_api_weekly_recap_get_returns_correct_week(weekly_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    _enable_fake(monkeypatch)
    base = f"/api/leagues/{weekly_league_id}/ai/weekly-recap"

    p1 = client.post(f"{base}?week=1").json()
    p2 = client.post(f"{base}?week=2").json()
    assert p1["content"]["week"] == 1 and p2["content"]["week"] == 2

    # GET must return the requested week — never another week's latest recap.
    g1 = client.get(f"{base}?week=1").json()
    g2 = client.get(f"{base}?week=2").json()
    assert g1["enabled"] is True and g1["content"]["week"] == 1
    assert g2["content"]["week"] == 2


def test_api_weekly_recap_caches_per_week(weekly_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    _enable_fake(monkeypatch)
    base = f"/api/leagues/{weekly_league_id}/ai/weekly-recap"

    client.post(f"{base}?week=1")
    assert FakeLlmClient.calls == 1
    client.post(f"{base}?week=1")  # same week, no force → cache hit
    assert FakeLlmClient.calls == 1
    client.post(f"{base}?week=2")  # different week → separate model call
    assert FakeLlmClient.calls == 2
    client.post(f"{base}?week=1&force=true")  # force regenerates
    assert FakeLlmClient.calls == 3


def test_api_weekly_recap_stale_is_per_week(weekly_league_id, monkeypatch):
    from sqlalchemy import select as _select

    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    _enable_fake(monkeypatch)
    base = f"/api/leagues/{weekly_league_id}/ai/weekly-recap"

    client.post(f"{base}?week=1")
    assert client.get(f"{base}?week=1").json()["stale"] is False

    # Change week-1 facts → that week's recap becomes stale (week-2 unaffected).
    client.post(f"{base}?week=2")
    with SessionLocal() as s:
        m = s.scalar(
            _select(Matchup).where(Matchup.league_id == weekly_league_id, Matchup.week == 1)
        )
        m.home_points = 999.0
        s.commit()
    assert client.get(f"{base}?week=1").json()["stale"] is True
    assert client.get(f"{base}?week=2").json()["stale"] is False


def test_api_weekly_recap_disabled_without_key(weekly_league_id, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    base = f"/api/leagues/{weekly_league_id}/ai/weekly-recap"
    g = client.get(f"{base}?week=1")
    p = client.post(f"{base}?week=1")
    assert g.status_code == 200 and g.json()["enabled"] is False
    assert p.status_code == 200 and p.json()["enabled"] is False


def test_latest_for_week_filters_by_week(db_session):
    lg = _make_weekly_league(db_session)
    svc = AiService(db_session, client=FakeLlmClient())
    svc.generate(kind="weekly_recap", scope="league", league_id=lg.id, model="m",
                 facts={"week": 1}, extra={"week": 1})
    svc.generate(kind="weekly_recap", scope="league", league_id=lg.id, model="m",
                 facts={"week": 2}, extra={"week": 2})
    assert svc.latest_for_week(lg.id, "weekly_recap", 1).content_json["week"] == 1
    assert svc.latest_for_week(lg.id, "weekly_recap", 2).content_json["week"] == 2
    assert svc.latest_for_week(lg.id, "weekly_recap", 3) is None


# --------------------------------------------------------------------------- #
# Phase 21: trade finder per-opponent GET/POST (offline, fake LLM)
# --------------------------------------------------------------------------- #
def _opponent_internal_ids(league_id: int) -> dict[int, int]:
    from sqlalchemy import select as _select

    with SessionLocal() as s:
        return {
            t.espn_team_id: t.id
            for t in s.scalars(_select(Team).where(Team.league_id == league_id))
        }


def test_api_trade_finder_get_returns_correct_opponent(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    _enable_fake(monkeypatch)
    ids = _opponent_internal_ids(synced_league_id)
    a, b = ids[2], ids[3]
    base = f"/api/leagues/{synced_league_id}/ai/trade-finder"

    pa = client.post(f"{base}?opponent_team_id={a}").json()
    pb = client.post(f"{base}?opponent_team_id={b}").json()
    assert pa["content"]["opponent_team_id"] == a and pb["content"]["opponent_team_id"] == b

    # GET must return the requested opponent — never another opponent's proposal.
    ga = client.get(f"{base}?opponent_team_id={a}").json()
    gb = client.get(f"{base}?opponent_team_id={b}").json()
    assert ga["content"]["opponent_team_id"] == a and ga["content"]["opponent_name"]
    assert gb["content"]["opponent_team_id"] == b


def test_api_trade_finder_caches_per_opponent(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    _enable_fake(monkeypatch)
    ids = _opponent_internal_ids(synced_league_id)
    a, b = ids[2], ids[3]
    base = f"/api/leagues/{synced_league_id}/ai/trade-finder"

    client.post(f"{base}?opponent_team_id={a}")
    assert FakeLlmClient.calls == 1
    client.post(f"{base}?opponent_team_id={a}")  # same opponent, no force → cache hit
    assert FakeLlmClient.calls == 1
    client.post(f"{base}?opponent_team_id={b}")  # different opponent → separate call
    assert FakeLlmClient.calls == 2
    client.post(f"{base}?opponent_team_id={a}&force=true")  # force regenerates
    assert FakeLlmClient.calls == 3


def test_api_trade_finder_ignores_legacy_unscoped_report(synced_league_id, monkeypatch):
    from api.ai_config import KIND_TRADE_FINDER, STANDARD_MODEL
    from api.services.ai import compute_input_hash
    from api.services.ai_inputs import trade_finder_input

    _enable_fake(monkeypatch)
    ids = _opponent_internal_ids(synced_league_id)
    a = ids[2]
    base = f"/api/leagues/{synced_league_id}/ai/trade-finder"

    # Insert a pre-Phase-21 legacy report: same input_hash, but content lacks opponent_team_id.
    with SessionLocal() as s:
        lg = s.get(League, synced_league_id)
        me = s.get(Team, lg.my_team_id)
        opp = s.get(Team, a)
        facts = trade_finder_input(s, lg, me, opp)
        h = compute_input_hash(KIND_TRADE_FINDER, STANDARD_MODEL, facts)
        s.add(AiReport(league_id=lg.id, kind=KIND_TRADE_FINDER, scope="team", input_hash=h,
                       model=STANDARD_MODEL, content_json={"proposals": [], "note": "legacy"}))
        s.commit()

    # GET must not return the legacy/unscoped report for this opponent.
    assert client.get(f"{base}?opponent_team_id={a}").json()["content"] is None

    # POST must not reuse the legacy cache entry (same hash) → it generates a tagged report.
    FakeLlmClient.calls = 0
    p = client.post(f"{base}?opponent_team_id={a}").json()
    assert FakeLlmClient.calls == 1
    assert p["content"]["opponent_team_id"] == a and p["content"]["note"] != "legacy"


def test_api_trade_finder_stale_is_per_opponent(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    _enable_fake(monkeypatch)
    ids = _opponent_internal_ids(synced_league_id)
    a, b = ids[2], ids[3]
    base = f"/api/leagues/{synced_league_id}/ai/trade-finder"

    client.post(f"{base}?opponent_team_id={a}")
    client.post(f"{base}?opponent_team_id={b}")
    assert client.get(f"{base}?opponent_team_id={a}").json()["stale"] is False

    # Change opponent A's facts (its name feeds trade_finder_input) → A's recap is stale, B not.
    with SessionLocal() as s:
        s.get(Team, a).name = "Renamed Rival"
        s.commit()
    assert client.get(f"{base}?opponent_team_id={a}").json()["stale"] is True
    assert client.get(f"{base}?opponent_team_id={b}").json()["stale"] is False


def test_api_trade_finder_rejects_self_and_cross_league(synced_league_id, monkeypatch):
    FakeLlmClient.calls = 0
    _enable_fake(monkeypatch)
    base = f"/api/leagues/{synced_league_id}/ai/trade-finder"
    with SessionLocal() as s:
        my_id = s.get(League, synced_league_id).my_team_id

    # Own team as opponent → error, no content, no generation.
    r_self = client.post(f"{base}?opponent_team_id={my_id}").json()
    assert r_self["error"] and r_self["content"] is None
    assert client.get(f"{base}?opponent_team_id={my_id}").json()["error"]
    # Unknown / cross-league team id → error, no generation.
    assert client.post(f"{base}?opponent_team_id=999999").json()["error"]
    assert FakeLlmClient.calls == 0


def test_api_trade_finder_disabled_without_key(synced_league_id, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    ids = _opponent_internal_ids(synced_league_id)
    a = ids[2]
    base = f"/api/leagues/{synced_league_id}/ai/trade-finder"
    g = client.get(f"{base}?opponent_team_id={a}")
    p = client.post(f"{base}?opponent_team_id={a}")
    assert g.status_code == 200 and g.json()["enabled"] is False
    assert p.status_code == 200 and p.json()["enabled"] is False


def test_latest_for_opponent_filters_by_opponent(db_session):
    lg = _league(db_session)
    svc = AiService(db_session, client=FakeLlmClient())
    svc.generate(kind="trade_finder", scope="team", league_id=lg.id, model="m",
                 facts={"o": 1}, extra={"opponent_team_id": 11, "opponent_name": "A"})
    svc.generate(kind="trade_finder", scope="team", league_id=lg.id, model="m",
                 facts={"o": 2}, extra={"opponent_team_id": 22, "opponent_name": "B"})
    assert svc.latest_for_opponent(lg.id, "trade_finder", 11).content_json["opponent_team_id"] == 11
    assert svc.latest_for_opponent(lg.id, "trade_finder", 22).content_json["opponent_team_id"] == 22
    assert svc.latest_for_opponent(lg.id, "trade_finder", 33) is None


# --------------------------------------------------------------------------- #
# Phase 22: Trade Finder grounding on roster snapshots (offline)
# --------------------------------------------------------------------------- #
# lineupSlotCounts: QB1, RB2, WR2, TE1, FLEX1, D/ST1, K1, BE6, and an unsupported OP (superflex).
_TRADE_SLOTS = {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1, "20": 6, "7": 1}


def _make_trade_league(session) -> tuple[League, Team, Team]:
    lg = League(tenant_id=resolve_tenant_id(session), espn_league_id="770", season=2026, is_public=True, lifecycle="in_season",
                size=4, playoff_team_count=2, lineup_slots_json=_TRADE_SLOTS, last_sync_ok=True)
    session.add(lg)
    session.flush()
    t1 = Team(league_id=lg.id, espn_team_id=1, name="Mine", is_me=True)
    t2 = Team(league_id=lg.id, espn_team_id=2, name="Rival", is_me=False)
    session.add_all([t1, t2])
    session.flush()
    lg.my_team_id = t1.id
    players = [
        (101, "QB One", "QB", 20.0), (102, "RB One", "RB", 15.0), (103, "RB Two", "RB", 12.0),
        (104, "RB Three", "RB", 10.0), (110, "RB Four", "RB", 9.0), (111, "RB Five", "RB", None),
        (105, "WR One", "WR", 14.0), (107, "TE One", "TE", 9.0), (108, "K One", "K", 7.0),
        (109, "DST One", "D/ST", 6.0),
        (201, "QB Opp", "QB", 18.0), (202, "RB Opp", "RB", 13.0), (203, "WR OppA", "WR", 16.0),
        (204, "WR OppB", "WR", 11.0), (205, "WR OppC", "WR", 9.0), (206, "TE Opp", "TE", 8.0),
        (207, "K Opp", "K", 6.0), (208, "DST Opp", "D/ST", 5.0), (199, "RB Traded", "RB", 20.0),
    ]
    for pid, name, pos, proj in players:
        session.add(Player(espn_player_id=pid, name=name, position=pos, proj_ros=proj))
    session.flush()

    def slot(pid, s, week, team, starter):
        session.add(LineupSlot(league_id=lg.id, team_id=team.id, week=week, slot=s,
                               espn_player_id=pid, points=None, is_starter=starter))

    # T1 week 1 roster (starters + bench); duplicate p102 on bench (dedup), plus a missing-Player row.
    for pid, s, st in [(101, "QB", True), (102, "RB", True), (103, "RB", True), (104, "FLEX", True),
                       (110, "BE", False), (111, "BE", False), (105, "WR", True), (107, "TE", True),
                       (108, "K", True), (109, "D/ST", True), (102, "BE", False), (777, "BE", False)]:
        slot(pid, s, 1, t1, st)
    # T1 also has a WEEK 2 snapshot with a traded-in RB (must NOT leak into the week-1 facts).
    slot(199, "RB", 2, t1, True)
    # T2 week 1 roster only (so latest common week is 1; newest league week is 2 → snapshot stale).
    for pid, s, st in [(201, "QB", True), (202, "RB", True), (203, "WR", True), (204, "WR", True),
                       (205, "BE", False), (206, "TE", True), (207, "K", True), (208, "D/ST", True)]:
        slot(pid, s, 1, t2, st)
    session.flush()
    return lg, t1, t2


def test_trade_finder_input_uses_latest_common_week_no_mixing(db_session):
    lg, t1, t2 = _make_trade_league(db_session)
    facts = ai_inputs.trade_finder_input(db_session, lg, t1, t2)
    snap = facts["roster_snapshot"]
    assert snap["grounding_source"] == "lineup_snapshot"
    assert snap["snapshot_week"] == 1          # common week (T2 only has week 1)
    assert snap["snapshot_stale"] is True       # newest league week is 2
    assert snap["projections_stale"] is False   # last_sync_ok True
    assert snap["unsupported_slots"] == ["OP"]  # superflex reported, not treated as FLEX
    # No week-2 roster leaks in: the traded-in RB (week 2 only) must be absent.
    names = {p["name"] for p in facts["me"]["players"]}
    assert "RB Traded" not in names
    # Dedup: p102 appears once and is marked a starter (its bench dup is collapsed).
    p102 = [p for p in facts["me"]["players"] if p["espn_player_id"] == 102]
    assert len(p102) == 1 and p102[0]["is_starter"] is True
    # Missing Player row is kept (name None), not dropped or zeroed.
    assert any(p["espn_player_id"] == 777 and p["name"] is None for p in facts["me"]["players"])
    # Null projection stays null; coverage reflects it. The roster has 11 entries (10 named
    # players + the missing-Player row); 9 carry a proj_ros → 9/11 ≈ 0.818.
    assert any(p["espn_player_id"] == 111 and p["proj_ros"] is None for p in facts["me"]["players"])
    assert len(facts["me"]["players"]) == 11
    assert facts["me"]["projection_coverage"] == round(9 / 11, 3)


def test_trade_finder_input_positional_surplus_deficit_hand_computed(db_session):
    lg, t1, t2 = _make_trade_league(db_session)
    by_pos = ai_inputs.trade_finder_input(db_session, lg, t1, t2)["me"]["by_position"]
    rb = by_pos["RB"]
    # 5 RBs (one null-proj), need 2 + 1 FLEX (RB Three, the best remaining) → 3 starters, surplus 2.
    assert rb["count"] == 5 and rb["starting_need"] == 2 and rb["flex_share"] == 1
    assert rb["surplus_count"] == 2 and rb["deficit"] == 0
    assert rb["proj_total"] == 46.0                     # 15+12+10+9 (null excluded)
    assert rb["surplus_proj"] == 9.0                    # depth beyond starters (null excluded)
    wr = by_pos["WR"]
    assert wr["count"] == 1 and wr["starting_need"] == 2 and wr["deficit"] == 1
    assert wr["surplus_count"] == -1


def test_trade_finder_input_drafted_fallback_and_pending(db_session):
    # No lineup_slots anywhere → drafted-roster fallback, labeled honestly.
    lg = League(tenant_id=resolve_tenant_id(db_session), espn_league_id="771", season=2026, is_public=True, lifecycle="drafted",
                size=2, lineup_slots_json=_TRADE_SLOTS, last_sync_ok=True)
    db_session.add(lg)
    db_session.flush()
    t1 = Team(league_id=lg.id, espn_team_id=1, name="A", is_me=True)
    t2 = Team(league_id=lg.id, espn_team_id=2, name="B")
    db_session.add_all([t1, t2])
    db_session.flush()
    db_session.add_all([Player(espn_player_id=301, name="Draftee", position="RB", proj_ros=11.0)])
    db_session.flush()
    db_session.add(DraftPick(league_id=lg.id, overall=1, team_id=t1.id, espn_player_id=301))
    db_session.flush()
    snap = ai_inputs.trade_finder_input(db_session, lg, t1, t2)["roster_snapshot"]
    assert snap["grounding_source"] == "drafted_roster"
    assert snap["snapshot_week"] is None and snap["fallback_reason"]

    # No lineup AND no picks → insufficient data, no invented players.
    t2b = db_session.get(Team, t2.id)
    facts_none = ai_inputs.trade_finder_input(db_session, lg, t2b, t1)
    # t2 has no picks → its roster is empty; my (t1) roster is the single draftee.
    assert facts_none["me"]["players"] == []


def test_trade_finder_input_projections_stale_when_sync_not_ok(db_session):
    lg, t1, t2 = _make_trade_league(db_session)
    lg.last_sync_ok = False  # last sync did not complete cleanly → freshness unconfirmed
    db_session.flush()
    assert ai_inputs.trade_finder_input(db_session, lg, t1, t2)["roster_snapshot"]["projections_stale"] is True


def test_trade_finder_facts_change_when_roster_or_projection_changes(db_session):
    from api.ai_config import STANDARD_MODEL
    from api.services.ai import compute_input_hash

    lg, t1, t2 = _make_trade_league(db_session)
    base = compute_input_hash("trade_finder", STANDARD_MODEL,
                              ai_inputs.trade_finder_input(db_session, lg, t1, t2))
    # Change a projection value → facts (and hash) change → prior report would be stale.
    db_session.get(Player, 105).proj_ros = 99.0
    db_session.flush()
    changed = compute_input_hash("trade_finder", STANDARD_MODEL,
                                 ai_inputs.trade_finder_input(db_session, lg, t1, t2))
    assert changed != base


# --- Player-name validation (pure) -----------------------------------------
def _facts_with_rosters(mine, theirs) -> dict:
    return {
        "me": {"players": [{"name": n} for n in mine]},
        "opponent": {"players": [{"name": n} for n in theirs]},
    }


def test_validate_trade_proposals_keeps_valid_drops_invalid():
    from api.services.ai import trade_name_tables, validate_trade_proposals

    my, opp = trade_name_tables(_facts_with_rosters(["RB One", "WR One"], ["WR OppA", "QB Opp"]))
    content = {"proposals": [
        {"i_give": ["rb one"], "i_get": ["WR OppA"], "rationale": "ok (case/space normalized)"},
        {"i_give": ["Ghost Player"], "i_get": ["WR OppA"], "rationale": "unknown give"},
        {"i_give": ["RB One"], "i_get": ["My WR One"], "rationale": "i_get names my player"},
        {"i_give": ["RB One + WR One"], "i_get": ["WR OppA"], "rationale": "combined string"},
    ], "note": "n"}
    out = validate_trade_proposals(content, my, opp)
    assert len(out["proposals"]) == 1
    # Canonical DB names are emitted, one player per list item.
    assert out["proposals"][0]["i_give"] == ["RB One"] and out["proposals"][0]["i_get"] == ["WR OppA"]


def test_validate_trade_proposals_all_invalid_returns_empty_with_note():
    from api.services.ai import trade_name_tables, validate_trade_proposals

    my, opp = trade_name_tables(_facts_with_rosters(["RB One"], ["WR OppA"]))
    out = validate_trade_proposals(
        {"proposals": [{"i_give": ["Nobody"], "i_get": ["WR OppA"], "rationale": "x"}], "note": "n"},
        my, opp,
    )
    assert out["proposals"] == [] and "No grounded trade" in out["note"]


# --- API: provenance + validation before persistence -----------------------
@pytest.fixture
def trade_league_id():
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        lg, _t1, _t2 = _make_trade_league(session)
        session.commit()
        lid, opp_id = lg.id, _t2.id
    finally:
        session.close()
    yield lid, opp_id
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_api_trade_finder_persists_only_validated_players_and_provenance(trade_league_id, monkeypatch):
    lid, opp_id = trade_league_id
    FakeLlmClient.calls = 0
    FakeLlmClient.invalid = False
    # The model returns one valid + one invalid (unknown player) proposal.
    FakeLlmClient.trade_override = {
        "proposals": [
            {"i_give": ["RB One"], "i_get": ["WR OppA"], "rationale": "RB depth for WR need."},
            {"i_give": ["Made Up Guy"], "i_get": ["WR OppA"], "rationale": "invented"},
        ],
        "note": "advisory",
    }
    _enable_fake(monkeypatch)
    try:
        base = f"/api/leagues/{lid}/ai/trade-finder"
        p = client.post(f"{base}?opponent_team_id={opp_id}").json()
        # Only the valid proposal survives; the invented one is never stored/served.
        assert len(p["content"]["proposals"]) == 1
        assert p["content"]["proposals"][0]["i_give"] == ["RB One"]
        assert p["content"]["proposals"][0]["i_give_players"] == [
            {"espn_player_id": 102, "name": "RB One", "position": "RB"}
        ]
        assert p["content"]["proposals"][0]["i_get_players"] == [
            {"espn_player_id": 203, "name": "WR OppA", "position": "WR"}
        ]
        # Provenance persisted alongside content.
        assert p["content"]["grounding_source"] == "lineup_snapshot"
        assert p["content"]["snapshot_week"] == 1
        assert p["content"]["opponent_team_id"] == opp_id
        # GET returns the same validated, provenance-tagged content.
        g = client.get(f"{base}?opponent_team_id={opp_id}").json()
        assert len(g["content"]["proposals"]) == 1
        assert g["content"]["proposals"][0]["i_give_players"][0]["espn_player_id"] == 102
    finally:
        FakeLlmClient.trade_override = None
