"""DB-backed raw-JSON cache (SPEC 2.10) — read/write-through on the raw_cache table.

THE LOOKUP IS TENANT-SCOPED IN THE QUERY, NOT ONLY BY POLICY
------------------------------------------------------------
Both reads used to be `session.get(RawCache, key)` -- a primary-key lookup on
`key` alone, back when that was the whole key. It returned **whichever
tenant's row held that key**, and nothing in this module said otherwise. On
PostgreSQL the row-level security policy filtered it, and in single-operator
mode on SQLite there was only ever one tenant, so it worked. But "correct
because a policy elsewhere hides the mistake" is one layer away from a
cross-tenant read, and the policy is not in force on the offline path at all.

Now the key is `(tenant_id, key)` and the lookups name both columns. The
tenant comes from `current_tenant_id`, which raises on an unbound session, so
a caller that forgot to bind gets an error rather than another tenant's cached
ESPN payload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RawCache
from ..tenancy import current_tenant_id
from .espn import RawCacheStore, _forbid_in_hosted_mode


class DBRawCache(RawCacheStore):
    def __init__(self, session: Session, ttl: timedelta = timedelta(hours=6)):
        super().__init__(ttl=ttl)
        self.session = session

    def _row(self, key: str) -> RawCache | None:
        """This tenant's row for this key, or None.

        Written as a query rather than `session.get(RawCache, (tenant, key))`
        because a composite-key `get` takes a tuple in primary-key order, and
        a silent reordering there would read the wrong row rather than fail.
        Naming the columns cannot be got wrong quietly.
        """
        return self.session.execute(
            select(RawCache).where(
                RawCache.tenant_id == current_tenant_id(self.session),
                RawCache.key == key,
            )
        ).scalar_one_or_none()

    def get(self, key: str) -> dict | None:
        row = self._row(key)
        if row is None or row.payload_json is None:
            return None
        if not self.fresh(row.fetched_at):
            return None
        return row.payload_json

    def set(self, key: str, payload: dict) -> None:
        # Hosted synthetic mode may read the raw cache but never write it, so a
        # synthetic deployment cannot accumulate provider-shaped state.
        _forbid_in_hosted_mode("raw cache write")

        row = self._row(key)
        if row is None:
            row = RawCache(key=key, tenant_id=current_tenant_id(self.session))
            self.session.add(row)
        row.payload_json = payload
        row.fetched_at = datetime.now(UTC)
        self.session.flush()
