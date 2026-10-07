"""Application configuration, loaded from environment / .env (SPEC 2.10, Appendix A).

The season and the ESPN base host live here as single constants so they are never
hardcoded inline elsewhere (SPEC guardrail 11).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the `api` package directory.
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # Phase 31: needed so the per-field `frozen` on `app_mode` is enforced on
        # assignment. A whole-model `frozen=True` was tried first and rejected: it
        # froze every unrelated field and broke existing settings mutation in
        # `tests/test_ai.py`. Only the security-critical field is immutable.
        validate_assignment=True,
    )

    # Phase 31 hosted data safety. The mode is explicit and structural: in
    # `public_synthetic` the provider class cannot be constructed, the credential
    # path cannot be reached, and the raw cache cannot be written. Nothing here is
    # a tuning knob, and no call site is trusted to remember a flag.
    # `get_settings()` returns a cached singleton, so the value is fixed for the
    # life of the process.
    #
    # REQUIRED, with no default. The contract's Synthesis says "`Settings` gains
    # a required, explicit mode", and an earlier version gave it a default
    # instead -- which meant a deployment reached the dangerous configuration by
    # OMITTING a setting while the safe one had to be expressed. Review measured
    # the consequence: a container with only an API key set ran 500 generations
    # with no ledger row and no ceiling. Now `Settings()` refuses to construct
    # without it, so a process that has not said what it is does not start.
    #
    # `frozen` is a typo guard on top of that, not the boundary: it refuses
    # `settings.app_mode = ...`, and `object.__setattr__` still gets through. The
    # boundary is that the value must be declared before the process exists.
    app_mode: Literal["private_operator", "public_synthetic"] = Field(frozen=True)

    season: int = 2026

    # The tenant this deployment acts as, when it is configured rather than
    # discovered.
    #
    # Discovery -- `resolve_tenant_id`'s `SELECT id FROM tenants` -- cannot
    # work on PostgreSQL, and that was measured rather than reasoned about:
    # the app connects as a NOSUPERUSER NOBYPASSRLS role, `tenants` has
    # FORCE ROW LEVEL SECURITY, and the policy hides every row until
    # `app.tenant_id` is bound. So the query that exists to find the tenant
    # needs a tenant already bound to see anything. As `edge_app` with
    # nothing bound, `SELECT count(*) FROM tenants` returns 0; with
    # `SET LOCAL app.tenant_id='1'` it returns 1. Every request 500s with
    # `TenantNotResolved` on an otherwise perfectly migrated database.
    #
    # SQLite has no row-level security, so discovery works there and the
    # offline suite never saw it. This is what booting against a real
    # PostgreSQL found on the first request.
    #
    # Unset means discover, which keeps SQLite and the suite unchanged.
    tenant_id: int | None = None

    # Where the BUILT frontend lives, when this process serves it too.
    #
    # Empty means do not serve static files at all, which is the development
    # and test shape: Vite serves the frontend on its own port and proxies
    # /api here. A path means one container serves both, which is what the
    # hosted deployment does -- it removes a second service, a second load
    # balancer target and the CORS configuration between them.
    static_dir: str = ""
    fernet_key: str = ""
    anthropic_api_key: str = ""
    # Operator-supplied model prices in micro-USD per million tokens, keyed by
    # model id, e.g. {"claude-sonnet-5": [3000000, 15000000]}. Empty by default
    # and deliberately so: see `api.ai_config.price_micro_usd_per_mtok` for why a
    # default price table would make the spend ceiling arithmetic over a guess.
    ai_price_micro_usd_per_mtok: dict[str, tuple[int, int]] = Field(default_factory=dict)
    db_path: str = "./data/edge.db"
    sync_cron: str = "0 5 * * *"

    # ESPN read API host — hardcoded in exactly one place (SPEC 2.2).
    # Phase 35. Empty means SQLite at `db_path`; a URL means PostgreSQL is the
    # schema authority and Alembic owns DDL. Defaulted to empty rather than
    # required, because every existing local install and the offline suite must
    # keep working untouched — unlike the telemetry flag, where both directions
    # of a default were wrong.
    database_url: str = ""

    espn_api_host: str = "https://lm-api-reads.fantasy.espn.com"

    # Phase 32 provider telemetry. Both are REQUIRED, deliberately: a defaulted
    # flag means an operator who never decided gets whichever default we picked,
    # and both directions are wrong. Off-by-default silently discards the
    # measurement the phase exists to take; on-by-default silently starts
    # recording provider behaviour on someone's machine. A deployment that omits
    # them fails to start, which is the same fail-closed shape as `app_mode`.
    # `frozen` as well as required: without it, `get_settings().telemetry_enabled
    # = True` succeeds on the lru_cached singleton under `validate_assignment`, and
    # every `EspnService` built afterwards records. The comment used to claim "the
    # same fail-closed shape as `app_mode`" while omitting the half that makes
    # `app_mode` fail closed.
    telemetry_enabled: bool = Field(..., frozen=True)
    telemetry_report_path: str = Field(..., frozen=True)
    # Fantasy Football Calculator ADP host — free/no-key source with attribution
    # (SPEC 2.9). Kept in one place just like ESPN's host.
    ffc_api_host: str = "https://fantasyfootballcalculator.com"
    ffc_adp_ttl_hours: int = 24
    opportunity_ttl_hours: int = 24

    # Phase 30 private-local recovery. Recovery stays opt-in until the operator
    # has completed the physical-volume, key-custody, retention, and restore
    # gates; once required, protected writes fail closed on missing/stale state.
    recovery_required: bool = False
    recovery_repository: str = ""
    recovery_restic_path: str = ""
    recovery_restic_version: str = "0.19.1"
    recovery_restic_sha256: str = ""
    recovery_credential_command: str = ""
    recovery_state_path: str = "./data/recovery-state.json"
    recovery_lock_path: str = "./data/recovery.lock"
    recovery_scratch_path: str = "./data/recovery-scratch"
    # Phase 30 safety bounds are fixed contract values, not tuning knobs.
    recovery_stale_after_seconds: Literal[86_400] = 86_400
    recovery_api_min_interval_seconds: int = Field(default=60, ge=60)

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_port: int = 5173

    @property
    def is_public_synthetic(self) -> bool:
        return self.app_mode == "public_synthetic"

    @model_validator(mode="after")
    def private_recovery_requires_loopback_bind(self) -> Settings:
        if self.recovery_required and self.api_host not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("private recovery requires a loopback API bind")
        return self

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (ROOT / p).resolve()

    @property
    def raw_cache_dir(self) -> Path:
        return self.db_file.parent / "raw_cache"

    def _local_path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (ROOT / p).resolve()

    @property
    def recovery_state_file(self) -> Path:
        return self._local_path(self.recovery_state_path)

    @property
    def recovery_lock_file(self) -> Path:
        return self._local_path(self.recovery_lock_path)

    @property
    def recovery_scratch_dir(self) -> Path:
        return self._local_path(self.recovery_scratch_path)

    @property
    def sqlalchemy_url(self) -> str:
        """The engine URL. PostgreSQL when configured, SQLite otherwise.

        Phase 35 makes PostgreSQL the *release* authority without making it the
        only thing that runs: `database_url` empty keeps the local SQLite path
        exactly as it was, so the offline suite and an operator's local database
        are untouched. Anything non-empty is used verbatim, which is what lets a
        CI lane or a container point at a real server without another setting.
        """
        return self.database_url or f"sqlite:///{self.db_file}"

    @property
    def is_postgres(self) -> bool:
        return self.sqlalchemy_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
