"""Live smoke-test CLI (SPEC Phase 1 AC, Section 12).

    python -m api.verify --league <id> [--season 2026] [--cross-check] [--label main]

Prints league name, size, scoring type, standings, my team, draft-pick count, weeks
synced, and transaction count for a real league so the numbers can be eyeballed
against the ESPN UI.

Access model (SPEC 2.3):
  - Tries PUBLIC access first (no cookies).
  - On 401/403, loads cookies from env (ESPN_SWID / ESPN_S2 in .env) or an interactive
    hidden prompt — NEVER as command-line arguments — and retries. The espn_s2
    URL-decode fallback inside EspnService still applies on top of that.

Never prints cookie values (SPEC guardrail 11).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import select

from .config import ROOT, get_settings
from .crypto import encrypt
from .db import init_db, session_scope
from .models import Account, DraftPick, League, Team
from .parse_helpers import normalize_swid_braced
from .services.espn import cookies_for_account
from .services.sync import SyncService


def _get_or_create_league(session, espn_league_id: str, season: int, account_id: int | None):
    league = session.scalar(
        select(League).where(League.espn_league_id == espn_league_id, League.season == season)
    )
    if league is None:
        league = League(
            espn_league_id=espn_league_id,
            season=season,
            account_id=account_id,
            is_public=account_id is None,
            lifecycle="pre_draft",
        )
        session.add(league)
        session.flush()
    return league


def _load_cookies() -> tuple[str, str] | None:
    """Cookies from env (.env: ESPN_SWID/ESPN_S2) or a hidden prompt. Never argv."""
    load_dotenv(ROOT / ".env")
    swid = os.environ.get("ESPN_SWID")
    espn_s2 = os.environ.get("ESPN_S2")
    if swid and espn_s2:
        print("  (using cookies from ESPN_SWID / ESPN_S2)")
        return swid, espn_s2
    if not sys.stdin.isatty():
        return None
    print("  private league — enter cookies (input hidden; not echoed, not stored in argv):")
    swid = getpass.getpass("    SWID: ").strip()
    espn_s2 = getpass.getpass("    espn_s2: ").strip()
    if swid and espn_s2:
        return swid, espn_s2
    return None


def _upsert_account(session, label: str, swid: str, espn_s2: str) -> Account:
    account = session.scalar(select(Account).where(Account.label == label))
    if account is None:
        account = Account(label=label, swid="", espn_s2_encrypted="")
        session.add(account)
    account.swid = normalize_swid_braced(swid)
    account.espn_s2_encrypted = encrypt(espn_s2.strip())
    account.status = "active"
    session.flush()
    return account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api.verify", description="ESPN Edge live smoke test")
    parser.add_argument("--league", required=True, help="ESPN league id")
    parser.add_argument("--season", type=int, default=None, help="defaults to SEASON env")
    parser.add_argument("--label", default="main", help="account label for cookie storage")
    parser.add_argument(
        "--cross-check",
        action="store_true",
        help="also load via espn-api and diff headline numbers (SPEC §2.8)",
    )
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="do not fall back to cookies even if access is denied",
    )
    args = parser.parse_args(argv)

    season = args.season or get_settings().season
    league_id = str(args.league)

    init_db()
    with session_scope() as session:
        league = _get_or_create_league(session, league_id, season, account_id=None)

        # ---- Attempt 1: PUBLIC (no cookies) --------------------------------
        print(f"[{league_id} / {season}] attempting public access…")
        result = SyncService(session).sync_league(league)
        used_cookies_plain: tuple[str, str] | None = None

        # ---- Attempt 2: cookies, on auth failure ---------------------------
        if result.get("needs_reauth") and not args.public_only:
            print("  public access denied (401/403) — this is a private league.")
            creds = _load_cookies()
            if creds is None:
                print(
                    "NO ACCESS: private league and no cookies available "
                    "(set ESPN_SWID/ESPN_S2 in .env or run interactively)."
                )
                return 3
            swid, espn_s2 = creds
            account = _upsert_account(session, args.label, swid, espn_s2)
            league.account_id = account.id
            league.is_public = False
            result = SyncService(session).sync_league(league)
            if result.get("needs_reauth"):
                print(
                    "NO ACCESS: cookies were sent but ESPN still returned 401/403. "
                    "The account may not have been a member of this league/season, "
                    "or the cookies are expired. (Data reality, not necessarily a bug.)"
                )
                return 3
            # espn-api cross-check needs the working plaintext cookies.
            cookies = cookies_for_account(account)
            used_cookies_plain = (account.swid, cookies.espn_s2) if cookies else None

        if result.get("errors") and result.get("name") is None:
            errs = "; ".join(result["errors"])
            if "404" in errs or "not found" in errs.lower():
                print(
                    f"NOT FOUND / NO ACCESS for league {league_id} in {season}: {errs}\n"
                    "(League may not exist that season, or the account wasn't a member. "
                    "Data reality — continuing is fine.)"
                )
                return 4
            print("SYNC FAILED:", errs)
            return 1

        teams = list(
            session.scalars(select(Team).where(Team.league_id == league.id).order_by(Team.standing))
        )
        pick_count = session.query(DraftPick).filter(DraftPick.league_id == league.id).count()
        _print_report(result, teams, pick_count)

        if args.cross_check:
            _run_cross_check(session, league, season, used_cookies_plain)

        if result.get("errors"):
            print("\nnon-fatal warnings:", "; ".join(result["errors"]))
    return 0


def _run_cross_check(session, league, season, cookies_plain) -> None:
    from .services import cross_check

    print("\n" + "-" * 60)
    print("CROSS-CHECK vs espn-api (SPEC §2.8):")
    swid = espn_s2 = None
    if cookies_plain:
        swid, espn_s2 = cookies_plain
    try:
        theirs = cross_check.from_espn_api(league.espn_league_id, season, swid, espn_s2)
    except Exception as exc:  # espn-api raised — no-access / shape change / etc.
        print(f"  espn-api could not load this league: {type(exc).__name__}: {exc}")
        return
    ours = cross_check.from_db(session, league)
    mismatches = cross_check.diff(ours, theirs)
    print(f"  ours:     {ours.team_count} teams, {ours.draft_pick_count} draft picks")
    print(f"  espn-api: {theirs.team_count} teams, {theirs.draft_pick_count} draft picks")
    if not mismatches:
        print("  ✅ headline numbers AGREE (team count, records, PF/PA, standings, picks)")
    else:
        print("  ⚠️  MISMATCHES:")
        for m in mismatches:
            print(f"    - {m}")


def _print_report(result: dict, teams: list[Team], pick_count: int) -> None:
    line = "=" * 60
    print(line)
    print(f"League:    {result.get('name')}  (ESPN id {result.get('league_id')})")
    print(f"Season:    {result.get('season')}")
    print(f"Size:      {result.get('size')} teams")
    print(f"Scoring:   {result.get('scoring')}")
    print(f"Lifecycle: {result.get('lifecycle')}")
    print(f"Drafted:   {result.get('drafted')}   Draft picks: {pick_count}")
    weeks = result.get("completed_weeks") or []
    print(f"Weeks synced (boxscores): {len(weeks)}  {weeks if weeks else ''}")
    print(f"Matchups:  {result.get('matchups')}   Transactions: {result.get('transactions')}")
    print(line)
    if teams:
        print(f"{'#':>2}  {'Team':<28} {'W-L-T':>7} {'PF':>8} {'PA':>8}  me")
        for t in teams:
            rank = t.standing if t.standing is not None else "-"
            rec = f"{t.wins}-{t.losses}-{t.ties}"
            me = "  <-- ME" if t.is_me else ""
            print(
                f"{str(rank):>2}  {(t.name or '')[:28]:<28} {rec:>7} "
                f"{t.points_for:>8.1f} {t.points_against:>8.1f}{me}"
            )
    else:
        print("(no teams — pre_draft/empty league or league not yet populated)")
    my = next((t for t in teams if t.is_me), None)
    print(line)
    if my:
        print(f"My team:   {my.name}  (espn_team_id={my.espn_team_id}, "
              f"record {my.wins}-{my.losses}-{my.ties})")
    else:
        print("My team:   not detected (public access, or SWID not on any roster)")


if __name__ == "__main__":
    raise SystemExit(main())
