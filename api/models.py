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
    """Aware UTC. Every column that stores one is `DateTime(timezone=True)`.

    Phase 35 measured what a naive column does on PostgreSQL: an aware value is
    written, a NAIVE value comes back, and `datetime.now(UTC) - stored` raises
    `TypeError: can't subtract offset-naive and offset-aware datetimes` -- which
    is exactly what every freshness check in the app does. It did not surface on
    SQLite in tests because a session's identity map hands back the same aware
    object that went in; only a fresh read after a commit shows it.
    """
    return datetime.now(UTC)


class Tenant(Base):
    """The isolation boundary. Every tenant-scoped row reaches exactly one of these.

    Phase 36 proved the isolation design against thirteen R4 attacks before this
    landed; `docs/sprint-9/kernel/` holds the SQL that was proven and the attack
    suite that proved it. The identity tables are deliberately thin — Cognito and
    external identities are Phase 37+, and a wider table now would be a guess.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class User(Base):
    """A person. Users are global; membership is what scopes them to a tenant."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[AppSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Membership(Base):
    """A user's place in a tenant. The join is the authorization fact."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False, default="member")

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class AppSession(Base):
    """An authenticated application session. Not an ESPN cookie, not a Cognito token.

    The distinction is the point. A Cognito `sub` proves who someone is; it does
    not say which tenant they may act in, and an access token that carried that
    authority would make the identity provider the authorization system. This
    row is the application's own answer: it names a user, and the user's
    membership names the tenant.

    `token_hash` is SHA-256 of a random opaque value. The raw token exists in
    exactly two places -- the response that mints it, and the caller's cookie --
    and never on disk. A stolen database therefore yields no usable session,
    which is the same reason password hashes exist. Comparison is constant-time
    so the lookup cannot be turned into an oracle for guessing tokens.

    Expiry and revocation are separate columns on purpose. Expiry is a fact
    about time; revocation is a decision someone made, and collapsing them
    would lose the ability to answer "was this session cut off, or did it just
    run out?" -- which is the first question asked after an incident.
    """

    __tablename__ = "app_sessions"
    __table_args__ = (Index("ix_app_sessions_user_expires", "user_id", "expires_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-text, deliberately coarse: "password", "oidc", "operator". Never a
    # token, a provider payload, or anything that identifies a device.
    origin: Mapped[str] = mapped_column(String, nullable=False, default="unknown")

    user: Mapped[User] = relationship(back_populates="sessions")


class Job(Base):
    """One unit of durable work. Replaces in-process scheduling.

    APScheduler holds its queue in memory, so a restart loses everything that
    had not run and a second process runs everything twice. This table is the
    queue instead: a crash loses at most the work of one attempt, and the row
    is the only thing that decides what happens next.

    The state machine is deliberately small -- `queued`, `leased`, `done`,
    `failed`, `poison` -- because every extra state is another transition
    somebody has to get right under a crash.

    `lease_owner` and `lease_expires_at` are what make a crash recoverable
    without a supervisor: a worker that dies mid-job leaves a lease that
    expires, and the next worker reclaims it. Nothing needs to notice the
    death.

    `available_at` carries both the retry cooldown and any deliberate delay,
    so "not yet" and "not again until" are one concept rather than two.

    `last_error` is a short, scrubbed reason. It is written by code that must
    never put a provider payload or a credential in a database column, which
    is why it is capped and why the service layer truncates rather than
    trusting callers.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        # Idempotency is per tenant, not global: two tenants syncing the same
        # ESPN league are two jobs, and a global key would silently collapse
        # them into one -- the same mistake `uq_league_season` made.
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_key"),
        Index("ix_jobs_claimable", "state", "available_at"),
        Index("ix_jobs_lease", "state", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON)

    state: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_error: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    swid: Mapped[str] = mapped_column(String, nullable=False)
    # espn_s2 stored Fernet-encrypted; never the plaintext (SPEC 2.10, 3).
    espn_s2_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")  # active | needs_reauth
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The Phase 36 audit's first finding: this is the most sensitive table in
    # the schema -- swid plus the Fernet-encrypted espn_s2 -- and it had no
    # tenant column and therefore no policy. It reaches no league, so there was
    # no path to scope it by; it needed one of its own. Nullable during expand,
    # like `leagues.tenant_id`.
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )

    leagues: Mapped[list[League]] = relationship(back_populates="account")


class League(Base):
    __tablename__ = "leagues"
    # Phase 36 measured that the old unique -- (espn_league_id, season), with no
    # tenant in it -- made the colliding-tenant case impossible to insert: two
    # tenants could not both hold the same ESPN league, which is exactly the
    # "guessed/colliding IDs" case the phase names. Uniqueness is per tenant.
    __table_args__ = (
        # Both, deliberately, for as long as `tenant_id` is nullable. The old
        # global one is what actually holds the line during the expand window:
        # SQL treats NULLs as distinct, so two NULL-tenant rows with the same
        # (espn_league_id, season) satisfy the tenant-scoped constraint and
        # nothing else would stop them. It is dropped by alembic/pending/0004,
        # in the same commit that makes `tenant_id` NOT NULL.
        #
        # Its cost is real and is the reason it goes: it makes the
        # colliding-tenant case impossible to insert, so two tenants cannot
        # both hold the same ESPN league (P36-1) -- which is exactly the
        # attack Phase 36 existed to test.
        UniqueConstraint("espn_league_id", "season", name="uq_league_season"),
        UniqueConstraint(
            "tenant_id", "espn_league_id", "season", name="uq_league_tenant_season"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    espn_league_id: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    # Nullable on purpose, and this is the open half of an expand-contract pair.
    # A league with no tenant is invisible to every policy and therefore to
    # everyone: that fails closed, but silently, which is how a league vanishes
    # and nobody learns why. The contract step that makes it impossible rather
    # than merely unlikely is written and proven -- alembic/pending/0004 -- and
    # parked until every writer supplies a tenant (Phase 37). This annotation
    # and that file move together; tests/test_spend.py fails if only one does.
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )

    name: Mapped[str | None] = mapped_column(String)
    size: Mapped[int | None] = mapped_column(Integer)
    scoring_json: Mapped[dict | None] = mapped_column(JSON)
    lineup_slots_json: Mapped[dict | None] = mapped_column(JSON)
    draft_type: Mapped[str | None] = mapped_column(String)
    playoff_team_count: Mapped[int | None] = mapped_column(Integer)  # for playoff_odds (SPEC §6)
    current_scoring_period: Mapped[int | None] = mapped_column(Integer)
    current_matchup_period: Mapped[int | None] = mapped_column(Integer)
    # pre_draft | drafted | in_season | complete
    lifecycle: Mapped[str] = mapped_column(String, default="pre_draft")
    my_team_id: Mapped[int | None] = mapped_column(Integer)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    home_projected_points: Mapped[float | None] = mapped_column(Float)
    away_projected_points: Mapped[float | None] = mapped_column(Float)
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


class CurrentRosterSnapshot(Base):
    """The latest successfully fetched scoring-period roster for one league."""

    __tablename__ = "current_roster_snapshots"
    __table_args__ = (
        UniqueConstraint("league_id", name="uq_current_roster_snapshot_league"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False
    )
    scoring_period: Mapped[int] = mapped_column(Integer, nullable=False)
    matchup_period: Mapped[int | None] = mapped_column(Integer)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CurrentRosterEntry(Base):
    """One occupied slot in a current-roster snapshot."""

    __tablename__ = "current_roster_entries"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "team_id",
            "lineup_slot_id",
            "slot_index",
            name="uq_current_roster_slot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("current_roster_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False
    )
    lineup_slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    espn_player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    player_name: Mapped[str | None] = mapped_column(String)
    player_position: Mapped[str | None] = mapped_column(String)
    nfl_team: Mapped[str | None] = mapped_column(String)
    opponent: Mapped[str | None] = mapped_column(String)
    kickoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    game_status: Mapped[str | None] = mapped_column(String)
    injury_status: Mapped[str | None] = mapped_column(String)
    actual_points: Mapped[float | None] = mapped_column(Float)
    projected_points: Mapped[float | None] = mapped_column(Float)


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
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now)


class NflversePlayerMap(Base):
    """Exact ESPN-to-GSIS identity used by Opportunity Analytics."""

    __tablename__ = "nflverse_player_maps"

    espn_player_id: Mapped[int] = mapped_column(
        ForeignKey("players.espn_player_id", ondelete="CASCADE"), primary_key=True
    )
    gsis_id: Mapped[str | None] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # matched|unmatched|ambiguous
    method: Mapped[str | None] = mapped_column(String)  # registry|manual
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class OpportunityWeek(Base):
    """Selected nflverse regular-season player usage for one NFL game."""

    __tablename__ = "opportunity_weeks"
    __table_args__ = (
        UniqueConstraint(
            "season", "season_type", "game_id", "gsis_id", name="uq_opportunity_player_game"
        ),
        Index("ix_opportunity_player_week", "season", "gsis_id", "week"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[str] = mapped_column(String, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    game_id: Mapped[str] = mapped_column(String, nullable=False)
    gsis_id: Mapped[str] = mapped_column(String, nullable=False)
    team: Mapped[str | None] = mapped_column(String)
    opponent_team: Mapped[str | None] = mapped_column(String)
    position: Mapped[str] = mapped_column(String, nullable=False)
    carries: Mapped[float | None] = mapped_column(Float)
    carry_share: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[float | None] = mapped_column(Float)
    receptions: Mapped[float | None] = mapped_column(Float)
    rushing_yards: Mapped[float | None] = mapped_column(Float)
    receiving_yards: Mapped[float | None] = mapped_column(Float)
    receiving_air_yards: Mapped[float | None] = mapped_column(Float)
    receiving_tds: Mapped[float | None] = mapped_column(Float)
    team_passing_yards: Mapped[float | None] = mapped_column(Float)
    target_share: Mapped[float | None] = mapped_column(Float)
    air_yards_share: Mapped[float | None] = mapped_column(Float)
    wopr: Mapped[float | None] = mapped_column(Float)
    rushing_epa: Mapped[float | None] = mapped_column(Float)
    receiving_epa: Mapped[float | None] = mapped_column(Float)
    fantasy_points_ppr: Mapped[float | None] = mapped_column(Float)


class OpportunityImport(Base):
    """Bounded, secret-free diagnostics for one nflverse refresh attempt."""

    __tablename__ = "opportunity_imports"
    __table_args__ = (Index("ix_opportunity_import_season_time", "season", "started_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_week: Mapped[int | None] = mapped_column(Integer)
    input_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stored_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    matched_players: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unmatched_players: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    package_version: Mapped[str | None] = mapped_column(String)
    schema_fingerprint: Mapped[str | None] = mapped_column(String)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    details_json: Mapped[dict | None] = mapped_column(JSON)


class AdpSnapshot(Base):
    __tablename__ = "adp_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String)  # espn | ffc
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    format: Mapped[str | None] = mapped_column(String)
    teams: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[dict | None] = mapped_column(JSON)


class Metric(Base):
    __tablename__ = "metrics"
    # A plain UniqueConstraint over nullable columns does NOT enforce identity in
    # SQLite (NULLs compare distinct), so a league-scope metric (team_id/week NULL)
    # could be inserted many times. Use four *partial* unique indexes so identity
    # holds for every NULL/non-NULL combination of (team_id, week).
    #
    # Phase 33 measured what happens with only `sqlite_where`: PostgreSQL ignores
    # that kwarg but STILL CREATES THE INDEX, without its predicate. All four
    # became FULL unique indexes, and `(league_id, key)` unique forbids per-team
    # metrics outright — the second team metric in a league fails. The app did not
    # degrade on PostgreSQL, it stopped on the first write. `postgresql_where` is
    # the fix and it is not optional.
    __table_args__ = (
        Index(
            "uq_metric_league",  # league metric, no week
            "league_id", "key",
            unique=True,
            sqlite_where=text("team_id IS NULL AND week IS NULL"),
            postgresql_where=text("team_id IS NULL AND week IS NULL"),
        ),
        Index(
            "uq_metric_league_week",  # league weekly metric
            "league_id", "key", "week",
            unique=True,
            sqlite_where=text("team_id IS NULL AND week IS NOT NULL"),
            postgresql_where=text("team_id IS NULL AND week IS NOT NULL"),
        ),
        Index(
            "uq_metric_team",  # team metric, no week
            "league_id", "team_id", "key",
            unique=True,
            sqlite_where=text("team_id IS NOT NULL AND week IS NULL"),
            postgresql_where=text("team_id IS NOT NULL AND week IS NULL"),
        ),
        Index(
            "uq_metric_team_week",  # team weekly metric
            "league_id", "team_id", "key", "week",
            unique=True,
            sqlite_where=text("team_id IS NOT NULL AND week IS NOT NULL"),
            postgresql_where=text("team_id IS NOT NULL AND week IS NOT NULL"),
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
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


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
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class AiReport(Base):
    __tablename__ = "ai_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String)  # league | team | portfolio
    kind: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    content_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RawCache(Base):
    __tablename__ = "raw_cache"

    # NOTE: `key` remains the sole primary key during the expand window, and
    # that is a known hole rather than a settled design. Two tenants cannot
    # hold the same cache key, so a colliding INSERT fails with a uniqueness
    # error that row-level security does NOT hide -- which tells the second
    # tenant that the first one has that key. Since a key carries a league id
    # and a hashed SWID, that is an enumeration oracle. The fix is a composite
    # (tenant_id, key) primary key, and it belongs with the contract step that
    # makes these columns NOT NULL, because until then half the rows share a
    # NULL tenant and the composite key would not be unique either.
    key: Mapped[str] = mapped_column(String, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    payload_json: Mapped[dict | None] = mapped_column(JSON)
    # Raw ESPN payloads for private leagues. Audit finding, same as `accounts`.
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )



class AiSpendMonth(Base):
    """One row per UTC month, and the only thing the ceiling is enforced against.

    The ceiling cannot be enforced by reading a SUM and then inserting: two
    processes both read a total under the ceiling, both insert, and the ceiling
    is breached with neither of them wrong at the moment it looked. A single
    counter row updated by a conditional `UPDATE ... WHERE committed + :amount
    <= :ceiling` is atomic in SQLite and in every other engine, so the bound
    holds under parallel reservations without depending on an isolation level
    the local SQLite file does not provide.

    Amounts are integer MICRO-DOLLARS, never floats. A ceiling compared with
    accumulated binary floating point is a ceiling that is sometimes off by a
    representation error, and "sometimes" is not a bound.
    """

    __tablename__ = "ai_spend_months"
    __table_args__ = (UniqueConstraint("month", name="uq_ai_spend_months_month"),)

    # A surrogate `id` rather than `month` as the primary key, to match the
    # convention every other table here follows and that the Phase 30 recovery
    # oracle relies on when it walks the schema. `month` carries the uniqueness.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String, nullable=False)  # "YYYY-MM", UTC
    committed_micro_usd: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class AiSpendEntry(Base):
    """The audit trail: one row per reservation, in one of three states.

    `reserved_micro_usd` is what the ceiling was charged at reserve time and is
    never reduced except by an explicit settle. That is what makes a crashed
    reservation non-free: the charge is recorded before the model call begins,
    so a process that dies mid-call leaves the month charged rather than leaving
    the spend unaccounted.
    """

    __tablename__ = "ai_spend_entries"
    __table_args__ = (
        UniqueConstraint("reservation", name="uq_ai_spend_entries_reservation"),
        Index("ix_ai_spend_entries_month_state", "month", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reservation: Mapped[str] = mapped_column(String, nullable=False)
    month: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    # reserved -> settled | unknown_spent. There is no "released" state: a
    # reservation that was never used still consumed the call's worst case, and
    # a release path is how a crash becomes silently free.
    state: Mapped[str] = mapped_column(String, nullable=False, default="reserved")
    reserved_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_micro_usd: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
