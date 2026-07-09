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
        lg = League(espn_league_id="111", season=2026, account_id=acct.id, is_public=False)
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
    assert {"league_id", "wins", "edge_score", "playoff_odds"} <= set(rows[0].keys())


def test_export_json(synced):
    r = client.get("/api/exports/portfolio.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert {"generated_at", "summary", "rows", "leagues"} <= set(body)
    assert body["summary"]["total_leagues"] == 1
    lg = body["leagues"][0]
    assert {"league", "teams", "draft", "matchups", "activity"} <= set(lg)
    assert len(lg["teams"]) == 4  # toy fixture has 4 teams


def test_export_xlsx_opens_with_openpyxl(synced):
    r = client.get("/api/exports/portfolio.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "portfolio.xlsx" in r.headers["content-disposition"]

    wb = load_workbook(io.BytesIO(r.content))
    assert "Portfolio" in wb.sheetnames
    assert len(wb.sheetnames) >= 2  # Portfolio + at least one league sheet

    portfolio = wb["Portfolio"]
    header = [c.value for c in portfolio[1]]
    assert "League Name" in header and "Playoff Odds" in header

    # The league sheet has the standings headers.
    league_sheet = wb[[s for s in wb.sheetnames if s != "Portfolio"][0]]
    lheader = [c.value for c in league_sheet[1]]
    assert lheader[:2] == ["Standing", "Team"]
    assert "Playoff %" in lheader


def test_exports_empty_db_still_valid():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    assert client.get("/api/exports/portfolio.csv").status_code == 200
    assert client.get("/api/exports/portfolio.json").json()["summary"]["total_leagues"] == 0
    wb = load_workbook(io.BytesIO(client.get("/api/exports/portfolio.xlsx").content))
    assert "Portfolio" in wb.sheetnames
