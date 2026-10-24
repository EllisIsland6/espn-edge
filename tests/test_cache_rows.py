"""ops/cache_rows.py -- the G5 read: whose rows, when, how far apart, never what.

The offline half pins the arithmetic and the one property that matters most:
no code path can print a payload, because the SQL never selects it. The
PostgreSQL half runs where `PROVISION_PG_OWNER_URL` points at a migrated
cluster and proves the tenant binding: rows of another tenant do not come back.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("cache_rows", ROOT / "ops" / "cache_rows.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cr = _load()


def test_gaps_are_milliseconds_since_the_previous_fetch():
    t0 = datetime(2026, 10, 24, 1, 0, 0, tzinfo=UTC)
    times = [
        t0,
        t0 + timedelta(seconds=1.2),
        t0 + timedelta(seconds=2.25),
        t0 + timedelta(seconds=2.3),
    ]
    assert cr.gaps_ms(times) == [None, 1200, 1050, 50]


def test_the_lines_carry_tenant_time_gap_and_key_and_nothing_else():
    t0 = datetime(2026, 10, 24, 1, 0, 0, tzinfo=UTC)
    rows = [(1, "league:123:mSettings", t0), (1, "league:123:mTeam", t0 + timedelta(seconds=1))]
    lines = cr.format_rows(rows)
    assert lines == [
        "tenant=1 fetched_at=2026-10-24T01:00:00+00:00 first key=league:123:mSettings",
        "tenant=1 fetched_at=2026-10-24T01:00:01+00:00 +1000 ms key=league:123:mTeam",
    ]


def test_the_sql_never_selects_the_payload():
    source = (ROOT / "ops" / "cache_rows.py").read_text(encoding="utf-8")
    select = source.split('"SELECT tenant_id, key, fetched_at FROM raw_cache "')[0]
    assert "payload_json" not in select.split("def main")[-1]
    assert "SELECT tenant_id, key, fetched_at FROM raw_cache" in source
    assert source.count("payload_json") == 1, "the only mention is the docstring's promise"


def test_parse_args_requires_a_postgres_owner_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TENANT_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        cr.parse_args(["--tenant-id", "1"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        cr.parse_args(["--url", "sqlite:///x.db"])
    args = cr.parse_args(["--url", "postgresql://x@h/db"])
    assert (args.tenant_id, args.since_minutes) == (1, 120)
    monkeypatch.setenv("TENANT_ID", "4")
    assert cr.parse_args(["--url", "postgresql://x@h/db"]).tenant_id == 4


OWNER_URL = os.environ.get("PROVISION_PG_OWNER_URL", "")

needs_postgres = pytest.mark.skipif(
    not OWNER_URL,
    reason="row security is real only on PostgreSQL: set PROVISION_PG_OWNER_URL to a migrated cluster",
)


@needs_postgres
def test_only_the_named_tenants_rows_come_back(capsys):
    from sqlalchemy import create_engine, text

    engine = create_engine(OWNER_URL, future=True)
    tag = os.urandom(3).hex()
    try:
        with engine.begin() as conn:
            for tenant_id in (1, 2):
                conn.execute(
                    text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_id)}
                )
                conn.execute(
                    text(
                        "INSERT INTO tenants (id, slug, created_at) VALUES (:t, :s, now()) ON CONFLICT DO NOTHING"
                    ),
                    {"t": tenant_id, "s": f"cache-rows-{tenant_id}-{tag}"},
                )
                conn.execute(
                    text(
                        "INSERT INTO raw_cache (tenant_id, key, fetched_at, payload_json)"
                        " VALUES (:t, :k, now(), '{\"private\": true}')"
                    ),
                    {"t": tenant_id, "k": f"probe:{tag}:{tenant_id}"},
                )
        assert cr.main(["--url", OWNER_URL, "--tenant-id", "1", "--since-minutes", "5"]) == 0
        out = capsys.readouterr().out
        assert f"probe:{tag}:1" in out and f"probe:{tag}:2" not in out
        assert "private" not in out, "a payload reached the terminal"
    finally:
        with engine.begin() as conn:
            for tenant_id in (1, 2):
                conn.execute(
                    text("SELECT set_config('app.tenant_id', :v, true)"), {"v": str(tenant_id)}
                )
                conn.execute(
                    text("DELETE FROM raw_cache WHERE key = :k"), {"k": f"probe:{tag}:{tenant_id}"}
                )
        engine.dispose()
