"""DB-backed raw-JSON cache (SPEC 2.10) — read/write-through on the raw_cache table."""

from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy.orm import Session

from ..models import RawCache
from ..tenancy import current_tenant_id
from .espn import RawCacheStore, _forbid_in_hosted_mode


class DBRawCache(RawCacheStore):
    def __init__(self, session: Session, ttl: timedelta = timedelta(hours=6)):
        super().__init__(ttl=ttl)
        self.session = session

    def get(self, key: str) -> dict | None:
        row = self.session.get(RawCache, key)
        if row is None or row.payload_json is None:
            return None
        if not self.fresh(row.fetched_at):
            return None
        return row.payload_json

    def set(self, key: str, payload: dict) -> None:
        # Hosted synthetic mode may read the raw cache but never write it, so a
        # synthetic deployment cannot accumulate provider-shaped state.
        _forbid_in_hosted_mode("raw cache write")

        from datetime import datetime

        row = self.session.get(RawCache, key)
        if row is None:
            row = RawCache(key=key, tenant_id=current_tenant_id(self.session))
            self.session.add(row)
        row.payload_json = payload
        row.fetched_at = datetime.now(UTC)
        self.session.flush()
