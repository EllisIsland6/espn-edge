"""Application configuration, loaded from environment / .env (SPEC 2.10, Appendix A).

The season and the ESPN base host live here as single constants so they are never
hardcoded inline elsewhere (SPEC guardrail 11).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the `api` package directory.
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    season: int = 2026
    fernet_key: str = ""
    anthropic_api_key: str = ""
    db_path: str = "./data/edge.db"
    sync_cron: str = "0 5 * * *"

    # ESPN read API host — hardcoded in exactly one place (SPEC 2.2).
    espn_api_host: str = "https://lm-api-reads.fantasy.espn.com"

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_port: int = 5173

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (ROOT / p).resolve()

    @property
    def raw_cache_dir(self) -> Path:
        return self.db_file.parent / "raw_cache"

    @property
    def sqlalchemy_url(self) -> str:
        return f"sqlite:///{self.db_file}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
