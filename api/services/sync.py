"""SyncService — the idempotent sync_league pipeline (SPEC Section 5).

Steps (upsert everything; safe to re-run):
  1. mSettings + mTeam (+ mDraftDetail) → league config, teams, standings, my-team.
  2. mDraftDetail → draft picks (skip cleanly if not drafted → lifecycle pre_draft).
  3. Each completed week: mMatchupScore + mBoxscore → matchups + per-player lineup rows.
  4. mTransactions2 (fallback: communication feed) → transactions.
  5. kona_player_info (league-scoped) → refresh touched players' ADP/ownership/proj.
  6. (metrics recompute is Phase 3) → bump last_synced_at.

Auth failures flip the owning account to needs_reauth without crashing (SPEC 2.3).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import (
    DraftPick,
    League,
    LineupSlot,
    Matchup,
    Player,
    Team,
    Transaction,
)
from . import metrics, parse
from .cache import DBRawCache
from .espn import EspnAuthError, EspnError, EspnService, cookies_for_account

log = logging.getLogger("espn.sync")

# X-Fantasy-Filter for league-scoped player pulls (SPEC 2.5).
_PLAYER_FILTER = {
    "players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS", "ONTEAM"]},
        "limit": 1500,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
    }
}


class SyncResult(dict):
    """Plain summary returned by sync_league (also handy for the verify CLI)."""


class SyncService:
    def __init__(self, session: Session, espn: EspnService | None = None):
        self.session = session
        # Only close a service we created ourselves — an injected one (e.g. a test
        # FakeEspn, or a shared app-level client) is owned by the caller.
        self._owns_espn = espn is None
        self.espn = espn or EspnService(cache=DBRawCache(session))

    def close(self) -> None:
        """Close the owned EspnService (its httpx.Client) so request-path syncs and
        scheduler jobs don't leak connections. No-op for injected services."""
        if self._owns_espn:
            close = getattr(self.espn, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> SyncService:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- public ------------------------------------------------------------
    def sync_league(self, league: League) -> SyncResult:
        result = SyncResult(
            league_id=league.espn_league_id,
            season=league.season,
            errors=[],
        )
        # Resolve the account by FK if the relationship wasn't populated — guards
        # against a caller that set league.account_id without the relationship,
        # which would otherwise silently fall back to a cookie-less public request.
        account = league.account
        if account is None and league.account_id is not None:
            from ..models import Account

            account = self.session.get(Account, league.account_id)
        cookies = cookies_for_account(account) if account else None

        # ---- Step 1: settings + teams (+ draft in one stacked request) -----
        try:
            data = self.espn.fetch_views(
                league.espn_league_id,
                league.season,
                ["mSettings", "mTeam", "mDraftDetail"],
                cookies=cookies,
                bust_cache=True,
            )
        except EspnAuthError:
            if account is not None:
                account.status = "needs_reauth"
                self.session.flush()
            result["errors"].append("auth_failed")
            result["needs_reauth"] = True
            log.warning("league %s: auth failed → account needs_reauth", league.espn_league_id)
            return result
        except EspnError as exc:
            result["errors"].append(f"fetch_failed: {exc}")
            return result

        # If cookies' espn_s2 was rewritten to the working variant, persist it.
        self._persist_working_cookie(account, cookies)

        settings = parse.parse_settings(data)
        teams = parse.parse_teams(data)
        account_swid = account.swid if account else None
        my_espn_team = parse.detect_my_team(teams, account_swid)

        # Upsert league config.
        league.name = settings.name or league.name
        league.size = settings.size
        league.scoring_json = settings.scoring_json
        league.lineup_slots_json = settings.lineup_slots
        league.draft_type = settings.draft_type
        league.playoff_team_count = settings.playoff_team_count
        self.session.flush()

        # Clear stale my-team / autodraft state before repopulating so a changed
        # (or removed) account, or a team that dropped out of the league, can't
        # leave a stale is_me/my_team_id/autodrafted flag behind (SPEC 2.7).
        self._reset_team_flags(league)
        id_map = self._upsert_teams(league, teams, my_espn_team)
        result["name"] = league.name
        result["size"] = league.size
        result["scoring"] = settings.scoring_label
        result["teams"] = len(teams)
        result["my_team_espn_id"] = my_espn_team

        # ---- Step 2: draft picks ------------------------------------------
        drafted, picks = parse.parse_draft(data)
        self._replace_picks(league, picks, id_map)
        result["draft_picks"] = len(picks)
        result["drafted"] = drafted
        self._flag_autodrafted_teams(league, picks, id_map)

        # ---- Step 3: matchups + boxscores ---------------------------------
        completed_weeks: list[int] = []
        try:
            mdata = self.espn.fetch_views(
                league.espn_league_id,
                league.season,
                ["mMatchupScore"],
                cookies=cookies,
                bust_cache=True,
            )
            matchups = parse.parse_schedule(mdata)
            self._replace_matchups(league, matchups, id_map, settings.current_week)
            completed_weeks = self._completed_weeks(matchups, settings.current_week)
            result["matchups"] = len(matchups)
        except EspnError as exc:
            result["errors"].append(f"matchups_failed: {exc}")

        self.session.execute(delete(LineupSlot).where(LineupSlot.league_id == league.id))
        for week in completed_weeks:
            try:
                bdata = self.espn.fetch_views(
                    league.espn_league_id,
                    league.season,
                    ["mBoxscore"],
                    scoring_period=week,
                    cookies=cookies,
                )
                entries = parse.parse_boxscore_week(bdata, week)
                self._insert_lineup(league, week, entries, id_map)
            except EspnError as exc:
                result["errors"].append(f"boxscore_week_{week}_failed: {exc}")
        result["completed_weeks"] = completed_weeks

        # ---- Step 4: transactions -----------------------------------------
        try:
            tdata = self.espn.fetch_views(
                league.espn_league_id,
                league.season,
                ["mTransactions2", "mPendingTransactions"],
                cookies=cookies,
                bust_cache=True,
            )
            txns = parse.parse_transactions(tdata)
            self._replace_transactions(league, txns, id_map)
            result["transactions"] = len(txns)
        except EspnError as exc:
            result["errors"].append(f"transactions_failed: {exc}")

        # ---- Step 5: players (league-scoped kona) -------------------------
        try:
            pdata = self.espn.fetch_views(
                league.espn_league_id,
                league.season,
                ["kona_player_info"],
                cookies=cookies,
                x_fantasy_filter=_PLAYER_FILTER,
                bust_cache=True,
            )
            players = parse.parse_player_pool(pdata)
            self._upsert_players(players)
            result["players"] = len(players)
        except EspnError as exc:
            result["errors"].append(f"players_failed: {exc}")

        # ---- Step 6: lifecycle, metrics recompute, last_synced_at ---------
        league.lifecycle = self._lifecycle(drafted, completed_weeks, data)
        self.session.flush()  # lifecycle drives the analytics branch below
        # Recompute Edge metrics from the just-synced DB state (Phase 3, SPEC §6).
        # Deterministic + isolated; recompute-on-sync is the invalidation strategy.
        try:
            metrics_result = metrics.recompute_league(self.session, league)
            result["metrics"] = metrics_result
        except Exception as exc:  # analytics must never break a sync
            result["errors"].append(f"metrics_failed: {exc}")
            log.warning("league %s: metrics recompute failed: %s", league.espn_league_id, exc)
        league.last_synced_at = datetime.now(UTC)
        result["lifecycle"] = league.lifecycle
        self.session.flush()
        return result

    # ---- upsert helpers ----------------------------------------------------
    def _reset_team_flags(self, league: League) -> None:
        """Zero out my-team/autodraft flags before a fresh sync repopulates them."""
        self.session.execute(
            update(Team)
            .where(Team.league_id == league.id)
            .values(is_me=False, autodrafted=False)
        )
        league.my_team_id = None
        self.session.flush()

    def _upsert_teams(
        self, league: League, teams: list[parse.ParsedTeam], my_espn_team: int | None
    ) -> dict[int, int]:
        # Query directly rather than via league.teams — we set FKs on new rows
        # without appending to the relationship, so the collection can be stale.
        existing = {
            t.espn_team_id: t
            for t in self.session.scalars(select(Team).where(Team.league_id == league.id))
        }
        id_map: dict[int, int] = {}
        for pt in teams:
            row = existing.get(pt.espn_team_id)
            if row is None:
                row = Team(league_id=league.id, espn_team_id=pt.espn_team_id)
                self.session.add(row)
            row.name = pt.name
            row.abbrev = pt.abbrev
            row.owner_swids_json = pt.owner_swids
            row.is_me = pt.espn_team_id == my_espn_team
            row.wins, row.losses, row.ties = pt.wins, pt.losses, pt.ties
            row.points_for, row.points_against = pt.points_for, pt.points_against
            row.standing = pt.standing
            row.logo_url = pt.logo_url
            self.session.flush()
            id_map[pt.espn_team_id] = row.id
            if row.is_me:
                league.my_team_id = row.id
        return id_map

    def _replace_picks(
        self, league: League, picks: list[parse.ParsedPick], id_map: dict[int, int]
    ) -> None:
        self.session.execute(delete(DraftPick).where(DraftPick.league_id == league.id))
        for p in picks:
            self.session.add(
                DraftPick(
                    league_id=league.id,
                    overall=p.overall,
                    round=p.round,
                    round_pick=p.round_pick,
                    team_id=id_map.get(p.espn_team_id),
                    espn_player_id=p.espn_player_id,
                    keeper=p.keeper,
                    autodraft=p.autodraft,
                    bid_amount=p.bid_amount,
                )
            )
        self.session.flush()

    def _flag_autodrafted_teams(
        self, league: League, picks: list[parse.ParsedPick], id_map: dict[int, int]
    ) -> None:
        # Preseason abandonment proxy: autodrafted on every pick (SPEC 6.1.3).
        by_team: dict[int, list[parse.ParsedPick]] = {}
        for p in picks:
            if p.espn_team_id is not None:
                by_team.setdefault(p.espn_team_id, []).append(p)
        team_rows = {
            t.espn_team_id: t
            for t in self.session.scalars(select(Team).where(Team.league_id == league.id))
        }
        for espn_id, tpicks in by_team.items():
            row = team_rows.get(espn_id)
            if row is not None:
                row.autodrafted = bool(tpicks) and all(p.autodraft for p in tpicks)
        self.session.flush()

    def _replace_matchups(
        self,
        league: League,
        matchups: list[parse.ParsedMatchup],
        id_map: dict[int, int],
        current_week: int | None,
    ) -> None:
        self.session.execute(delete(Matchup).where(Matchup.league_id == league.id))
        for m in matchups:
            self.session.add(
                Matchup(
                    league_id=league.id,
                    week=m.week,
                    home_team_id=id_map.get(m.home_espn_team_id),
                    away_team_id=id_map.get(m.away_espn_team_id),
                    home_points=m.home_points,
                    away_points=m.away_points,
                    is_playoff=m.is_playoff,
                )
            )
        self.session.flush()

    def _insert_lineup(
        self,
        league: League,
        week: int,
        entries: list[parse.ParsedLineupEntry],
        id_map: dict[int, int],
    ) -> None:
        for e in entries:
            team_id = id_map.get(e.espn_team_id)
            if team_id is None:
                # Unknown team → don't write a lineup row pointing at a phantom
                # team (would be an FK violation with enforcement on). SPEC 12.
                log.warning(
                    "lineup week %s: unknown espn_team_id %s, skipping entry",
                    week,
                    e.espn_team_id,
                )
                continue
            self.session.add(
                LineupSlot(
                    league_id=league.id,
                    team_id=team_id,
                    week=week,
                    slot=e.slot,
                    espn_player_id=e.espn_player_id,
                    points=e.points,
                    is_starter=e.is_starter,
                )
            )
        self.session.flush()

    def _replace_transactions(
        self, league: League, txns: list[parse.ParsedTransaction], id_map: dict[int, int]
    ) -> None:
        self.session.execute(delete(Transaction).where(Transaction.league_id == league.id))
        for t in txns:
            self.session.add(
                Transaction(
                    league_id=league.id,
                    team_id=id_map.get(t.espn_team_id),
                    type=t.type,
                    week=t.week,
                    player_in=t.player_in,
                    player_out=t.player_out,
                    bid=t.bid,
                    executed_at=t.executed_at,
                )
            )
        self.session.flush()

    def _upsert_players(self, players: list[parse.ParsedPlayer]) -> None:
        for p in players:
            row = self.session.get(Player, p.espn_player_id)
            if row is None:
                row = Player(espn_player_id=p.espn_player_id)
                self.session.add(row)
            row.name = p.name
            row.position = p.position
            row.nfl_team = p.nfl_team
            row.espn_adp = p.espn_adp
            row.espn_pct_owned = p.espn_pct_owned
            row.espn_rank_ppr = p.espn_rank_ppr
            # Always overwrite (even with None): if ESPN drops projection data we
            # must not keep a stale value that would pollute Phase 3 roster strength.
            row.proj_ros = p.proj_ros
            row.updated_at = datetime.now(UTC)
        self.session.flush()

    # ---- lifecycle ---------------------------------------------------------
    @staticmethod
    def _completed_weeks(
        matchups: list[parse.ParsedMatchup], current_week: int | None
    ) -> list[int]:
        weeks: set[int] = set()
        for m in matchups:
            if m.week is None:
                continue
            if current_week is not None and m.week >= current_week:
                continue
            if (m.home_points or 0) > 0 or (m.away_points or 0) > 0:
                weeks.add(m.week)
        return sorted(weeks)

    @staticmethod
    def _lifecycle(drafted: bool, completed_weeks: list[int], data: dict) -> str:
        if not drafted:
            return "pre_draft"
        status = data.get("status") or {}
        is_active = status.get("isActive", True)
        if completed_weeks:
            final_period = status.get("finalScoringPeriod")
            latest = status.get("latestScoringPeriod")
            if is_active is False or (final_period and latest and latest >= final_period):
                return "complete"
            return "in_season"
        return "drafted"

    @staticmethod
    def _persist_working_cookie(account, cookies) -> None:
        """If the URL-decoded espn_s2 was the one that worked, re-encrypt+store it."""
        if account is None or cookies is None:
            return
        from ..crypto import decrypt, encrypt

        try:
            stored = decrypt(account.espn_s2_encrypted)
        except Exception:
            stored = None
        if stored is not None and cookies.espn_s2 != stored:
            account.espn_s2_encrypted = encrypt(cookies.espn_s2)
