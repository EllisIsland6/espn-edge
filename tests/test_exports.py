"""Phase 5 export endpoint tests — offline, populated via FakeEspn sync."""

import csv
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from api.crypto import encrypt
from api.db import Base, SessionLocal, engine, init_db
from api.main import app
from api.models import Account, League
from api.services.sync import SyncService
from api.tenancy import resolve_tenant_id

from .conftest import FakeEspn, load_fixture

client = TestClient(app)


@pytest.fixture
def synced():
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
        SyncService(
            session,
            espn=FakeEspn(load_fixture("public_league.json"), load_fixture("players_pool.json")),
        ).sync_league(lg)
        session.commit()
    finally:
        session.close()
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_export_csv(synced):
    r = client.get("/api/exports/portfolio.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "portfolio.csv" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1  # one league synced
    assert rows[0]["league_name"] == "Test Public League"
    # Legacy edge_score columns AND the new Edge Index columns are both present (Phase 17).
    assert {"league_id", "wins", "edge_score", "playoff_odds"} <= set(rows[0].keys())
    assert {"edge_index_score", "edge_index_grade", "edge_index_verdict"} <= set(rows[0].keys())


def test_export_json(synced):
    r = client.get("/api/exports/portfolio.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert {
        "generated_at", "summary", "rows", "leagues", "exposure", "draft_adp",
        "strategies", "opportunity",
    } <= set(body)
    assert {"me", "opponents"} == set(body["exposure"])
    assert body["summary"]["total_leagues"] == 1
    # Summary carries both the Edge Index (primary) and legacy edge_score aggregates.
    assert {
        "edge_index_scored_count", "edge_index_advantaged_count",
        "best_edge_index_score", "worst_edge_index_score",
        "scored_count", "advantaged_count", "best_edge_score", "worst_edge_score",
    } <= set(body["summary"])
    lg = body["leagues"][0]
    assert {"league", "teams", "draft", "matchups", "activity"} <= set(lg)
    assert len(lg["teams"]) == 4  # toy fixture has 4 teams
    assert lg["league"]["my_team_name"] == "Alpha"
    assert lg["league"]["my_team_logo_url"] == "http://x/1.png"
    assert body["rows"][0]["my_team_name"] == "Alpha"
    assert body["rows"][0]["my_team_logo_url"] == "http://x/1.png"


def test_export_xlsx_opens_with_openpyxl(synced):
    r = client.get("/api/exports/portfolio.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "portfolio.xlsx" in r.headers["content-disposition"]

    wb = load_workbook(io.BytesIO(r.content))
    analytics_sheets = {
        "Portfolio", "Exposure Rostered", "Exposure Field Owns", "Exposure All",
        "Exposure Headlines", "NFL Concentration", "Positional Spend", "Draft ADP",
        "Strategies", "Opportunity",
    }
    assert {
        "Portfolio", "Exposure Rostered", "Exposure Field Owns", "Exposure All",
        "Draft ADP", "Strategies", "Opportunity",
    } <= set(wb.sheetnames)
    assert len(wb.sheetnames) >= 5  # Four portfolio sheets + at least one league sheet

    portfolio = wb["Portfolio"]
    header = [c.value for c in portfolio[1]]
    assert "League Name" in header and "Playoff Odds" in header

    # The league sheet has the standings headers.
    league_sheet = wb[
        next(
            sheet
            for sheet in wb.sheetnames
            if sheet not in analytics_sheets
        )
    ]
    lheader = [c.value for c in league_sheet[1]]
    assert lheader[:2] == ["Standing", "Team"]
    assert "Playoff %" in lheader


def test_exports_empty_db_still_valid():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    assert client.get("/api/exports/portfolio.csv").status_code == 200
    assert client.get("/api/exports/portfolio.json").json()["summary"]["total_leagues"] == 0
    wb = load_workbook(io.BytesIO(client.get("/api/exports/portfolio.xlsx").content))
    assert {
        "Portfolio", "Exposure Rostered", "Exposure Field Owns", "Exposure All",
        "Draft ADP", "Strategies", "Opportunity",
    } <= set(wb.sheetnames)


def test_analytics_csv_exports_share_api_row_fields(synced):
    exposure = client.get("/api/exports/exposure.csv?scope=me")
    draft_adp = client.get("/api/exports/draft-adp.csv")
    strategies = client.get("/api/exports/strategies.csv")
    opportunity = client.get("/api/exports/opportunity.csv?view=all")
    assert (
        exposure.status_code
        == draft_adp.status_code
        == strategies.status_code
        == opportunity.status_code
        == 200
    )
    assert "exposure-me.csv" in exposure.headers["content-disposition"]
    assert "draft-adp.csv" in draft_adp.headers["content-disposition"]
    assert "strategies.csv" in strategies.headers["content-disposition"]
    assert "opportunity-all.csv" in opportunity.headers["content-disposition"]
    assert {"player_name", "exposure_pct", "share", "leagues"} <= set(
        next(csv.DictReader(io.StringIO(exposure.text)))
    )
    assert {
        "team_name", "draft_value_capture_espn", "draft_value_capture_ffc",
    } <= set(next(csv.DictReader(io.StringIO(draft_adp.text))))
    assert {"primary_label", "secondary_label", "triggering_picks"} <= set(
        next(csv.DictReader(io.StringIO(strategies.text)))
    )
    assert {"player_name", "opportunity_score", "signal", "available_leagues"} <= set(
        csv.DictReader(io.StringIO(opportunity.text)).fieldnames or []
    )
