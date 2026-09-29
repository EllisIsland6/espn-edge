"""Troubleshooting CLI for Phase 27 Opportunity Analytics."""

from __future__ import annotations

import argparse
import json
from typing import Any

from .config import get_settings
from .db import init_db, session_scope
from .services.opportunity import (
    opportunity_doctor,
    opportunity_status,
    refresh_opportunity,
)
from .services.recovery import (
    RecoveryAdmissionError,
    assert_recovery_write_allowed,
    secret_free_error,
)


def _print(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=str, sort_keys=True))
        return
    print(json.dumps(value, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect or refresh nflverse opportunity data")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "doctor", "refresh"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--season", type=int, default=get_settings().season)
        sub.add_argument("--json", action="store_true", dest="as_json")
        if name == "refresh":
            sub.add_argument("--force", action="store_true")
            sub.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "refresh":
        try:
            assert_recovery_write_allowed()
        except RecoveryAdmissionError as exc:
            _print(secret_free_error(exc), args.as_json)
            return 1
    init_db()
    with session_scope() as session:
        if args.command == "status":
            result = opportunity_status(session, args.season)
        elif args.command == "doctor":
            result = opportunity_doctor(session, args.season)
        else:
            result = refresh_opportunity(
                session,
                args.season,
                force=args.force,
                dry_run=args.dry_run,
            )
        _print(result, args.as_json)
        return 1 if result.get("state") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
