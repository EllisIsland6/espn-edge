"""SQLite data model — mirrors SPEC Section 4 exactly.

Every derived UI number must trace back to a `metrics` row or a raw table.
JSON blobs use SQLite's JSON affinity via SQLAlchemy's JSON type.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    swid: Mapped[str] = mapped_column(String, nullable=False)
    # espn_s2 stored Fernet-encrypted; never the plaintext (SPEC 2.10, 3).
    espn_s2_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")  # active | needs_reauth
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    leagues: Mapped[list[League]] = relationship(back_populates="account")


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = (UniqueConstraint("espn_league_id", "season", name="uq_league_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_league_id: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)

    name: Mapped[str | None] = mapped_column(String)
    size: Mapped[int | None] = mapped_column(Integer)
    scoring_json: Mapped[dict | None] = mapped_column(JSON)
    lineup_slots_json: Mapped[dict | None] = mapped_column(JSON)
    draft_type: Mapped[str | None] = mapped_column(String)
    playoff_team_count: Mapped[int | None] = mapped_column(Integer)  # for playoff_odds (SPEC §6)
    # pre_draft | drafted | in_season | complete
    lifecycle: Mapped[str] = mapped_column(String, default="pre_draft")
    my_team_id: Mapped[int | None] = mapped_column(Integer)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Persistent last-sync diagnostics (Phase 7). error is redacted — never secrets.
    last_sync_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_sync_error: Mapped[str | None] = mapped_column(String)

    account: Mapped[Account | None] = relationship(back_populates="leagues")
    teams: Mapped[list[Team]] = relationship(back_populates="league", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("league_id", "espn_team_id", name="uq_team_league_espn"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    espn_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String)
    abbrev: Mapped[str | None] = mapped_column(String)
    owner_swids_json: Mapped[list | None] = mapped_column(JSON)
    is_me: Mapped[bool] = mapped_column(Boolean, default=False)
    autodrafted: Mapped[bool] = mapped_column(Boolean, default=False)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    ties: Mapped[int] = mapped_column(Integer, default=0)
    points_for: Mapped[float] = mapped_column(Float, default=0.0)
    points_against: Mapped[float] = mapped_column(Float, default=0.0)
    standing: Mapped[int | None] = mapped_column(Integer)
    logo_url: Mapped[str | None] = mapped_column(String)

    league: Mapped[League] = relationship(back_populates="teams")


class DraftPick(Base):
    __tablename__ = "draft_picks"
    __table_args__ = (
        UniqueConstraint("league_id", "overall", name="uq_pick_league_overall"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    overall: Mapped[int | None] = mapped_column(Integer)
    round: Mapped[int | None] = mapped_column(Integer)
    round_pick: Mapped[int | None] = mapped_column(Integer)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    espn_player_id: Mapped[int | None] = mapped_column(Integer)
    keeper: Mapped[bool] = mapped_column(Boolean, default=False)
    autodraft: Mapped[bool] = mapped_column(Boolean, default=False)
    bid_amount: Mapped[int | None] = mapped_column(Integer)
    adp_at_draft: Mapped[float | None] = mapped_column(Float)
    value_delta: Mapped[float | None] = mapped_column(Float)


class Matchup(Base):
    __tablename__ = "matchups"
    __table_args__ = (
        UniqueConstraint(
            "league_id", "week", "home_team_id", "away_team_id", name="uq_matchup"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    home_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    away_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    home_points: Mapped[float | None] = mapped_column(Float)
    away_points: Mapped[float | None] = mapped_column(Float)
    is_playoff: Mapped[bool] = mapped_column(Boolean, default=False)


class LineupSlot(Base):
    __tablename__ = "lineup_slots"
    __table_args__ = (
        UniqueConstraint(
            "league_id", "week", "team_id", "espn_player_id", "slot", name="uq_lineup"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[str | None] = mapped_column(String)
    espn_player_id: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[float | None] = mapped_column(Float)
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    type: Mapped[str | None] = mapped_column(String)  # waiver|fa_add|drop|trade|...
    week: Mapped[int | None] = mapped_column(Integer)
    player_in: Mapped[int | None] = mapped_column(Integer)
    player_out: Mapped[int | None] = mapped_column(Integer)
    bid: Mapped[int | None] = mapped_column(Integer)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime)


class Player(Base):
    __tablename__ = "players"

    espn_player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    position: Mapped[str | None] = mapped_column(String)
    nfl_team: Mapped[str | None] = mapped_column(String)
    espn_adp: Mapped[float | None] = mapped_column(Float)
    espn_pct_owned: Mapped[float | None] = mapped_column(Float)
    espn_rank_ppr: Mapped[float | None] = mapped_column(Float)
    proj_ros: Mapped[float | None] = mapped_column(Float)
    ffc_id: Mapped[int | None] = mapped_column(Integer)
    ffc_adp: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=_now)


class AdpSnapshot(Base):
    __tablename__ = "adp_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String)  # espn | ffc
    pulled_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    format: Mapped[str | None] = mapped_column(String)
    teams: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[dict | None] = mapped_column(JSON)


class Metric(Base):
    __tablename__ = "metrics"
    # A plain UniqueConstraint over nullable columns does NOT enforce identity in
    # SQLite (NULLs compare distinct), so a league-scope metric (team_id/week NULL)
    # could be inserted many times. Use four *partial* unique indexes so identity
    # holds for every NULL/non-NULL combination of (team_id, week).
    __table_args__ = (
        Index(
            "uq_metric_league",  # league metric, no week
            "league_id", "key",
            unique=True,
            sqlite_where=text("team_id IS NULL AND week IS NULL"),
        ),
        Index(
            "uq_metric_league_week",  # league weekly metric
            "league_id", "key", "week",
            unique=True,
            sqlite_where=text("team_id IS NULL AND week IS NOT NULL"),
        ),
        Index(
            "uq_metric_team",  # team metric, no week
            "league_id", "team_id", "key",
            unique=True,
            sqlite_where=text("team_id IS NOT NULL AND week IS NULL"),
        ),
        Index(
            "uq_metric_team_week",  # team weekly metric
            "league_id", "team_id", "key", "week",
            unique=True,
            sqlite_where=text("team_id IS NOT NULL AND week IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String, nullable=False)
    week: Mapped[int | None] = mapped_column(Integer)
    value_float: Mapped[float | None] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MetricSnapshot(Base):
    """One append-only metric value from a clean sync batch."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "league_id", "batch_id", "team_id", "key", name="uq_metric_snapshot_batch"
        ),
        Index(
            "ix_metric_snapshot_lookup",
            "league_id", "team_id", "key", "period", "recorded_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    period: Mapped[int] = mapped_column(Integer, nullable=False)
    value_float: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class AiReport(Base):
    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String)  # league | team | portfolio
    kind: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    content_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RawCache(Base):
    __tablename__ = "raw_cache"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
