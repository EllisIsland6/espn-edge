"""Read the raw-cache rows of one tenant: which tenant, which key, when, how far apart.

    export DATABASE_URL=postgresql+psycopg2://edge_owner@127.0.0.1:15432/edge?sslmode=require
    python ops/cache_rows.py --tenant-id 1

Gate G5 of ADR-012 asks two things of the first live ESPN read that only the
database can answer: that the raw cache row is the operator's tenant's, and
that the limiter's cadence is visible. This prints exactly those -- one line
per row with the tenant id, the cache key, the fetch time and the gap in
milliseconds since the previous fetch -- and nothing else. **It never prints
`payload_json`**: a raw ESPN payload for a private league is private, and the
terminal is a log. Runs as the owner through the SSM tunnel (README section
7), the password in `PGPASSWORD` so the URL carries none; the owner is bound
by row security, so the tenant must be named and only that tenant's rows come
back.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import create_engine, text

TENANT_GUC = "app.tenant_id"


def gaps_ms(times: Iterable[datetime]) -> list[int | None]:
    """The gap from each fetch to the one before it, None for the first.

    The limiter spaces requests per key by at least a second, so consecutive
    fetches of different views in one sync should read a thousand or more.
    """
    out: list[int | None] = []
    previous: datetime | None = None
    for current in times:
        out.append(None if previous is None else int((current - previous).total_seconds() * 1000))
        previous = current
    return out


def format_rows(rows: list[tuple[int, str, datetime]]) -> list[str]:
    lines = []
    for (tenant_id, key, fetched_at), gap in zip(rows, gaps_ms(r[2] for r in rows), strict=True):
        gap_text = "first" if gap is None else f"+{gap} ms"
        lines.append(f"tenant={tenant_id} fetched_at={fetched_at.isoformat()} {gap_text} key={key}")
    return lines


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cache_rows.py",
        description=(
            "Print one tenant's raw-cache rows: tenant, key, fetch time, gap. Never the payload."
        ),
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DATABASE_URL", ""),
        help="the OWNER's SQLAlchemy URL, password via PGPASSWORD (default: $DATABASE_URL)",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        default=int(os.environ.get("TENANT_ID", "1")),
        help="the tenant whose rows to read (default: $TENANT_ID or 1)",
    )
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=120,
        help="only rows fetched this recently (default 120)",
    )
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("a database URL is required: --url or $DATABASE_URL")
    if not args.url.startswith("postgresql"):
        parser.error("the owner URL must be postgresql://; the live cache is nowhere else")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    engine = create_engine(args.url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config(:name, :value, true)"),
                {"name": TENANT_GUC, "value": str(args.tenant_id)},
            )
            rows = conn.execute(
                text(
                    "SELECT tenant_id, key, fetched_at FROM raw_cache "
                    "WHERE fetched_at >= now() - make_interval(mins => :m) "
                    "ORDER BY fetched_at, key"
                ),
                {"m": args.since_minutes},
            ).all()
    finally:
        engine.dispose()
    lines = format_rows([(int(r[0]), str(r[1]), r[2]) for r in rows])
    print(
        "\n".join(lines)
        if lines
        else f"no raw_cache rows for tenant {args.tenant_id} in the window"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
