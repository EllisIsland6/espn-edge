"""Provision an invited person as their own tenant -- 0017's transaction, generalised.

    export DATABASE_URL=postgresql+psycopg2://edge_owner@127.0.0.1:15432/edge?sslmode=require
    export OIDC_ISSUER=https://cognito-idp.us-east-1.amazonaws.com/<pool-id>
    python ops/provision_user.py provision <email> <cognito-sub>
    python ops/provision_user.py revoke <cognito-sub>

Runs as the OWNER (`edge_owner`) through the SSM tunnel, the password in
`PGPASSWORD` so the URL carries none. Prints ids (provision) or counts (revoke)
and nothing else: no email and no subject appear in any output, success or
failure, because those are member identifiers and the terminal is a log.

## Why a script and not three INSERTs

`alembic/versions/0017_identities.py` records the shape FORCE ROW LEVEL
SECURITY allows the owner: reserve the id, bind it, insert with it. A new
tenant per person (ADR-012: nothing is shared across invitees) adds the same
step one level up -- `tenants` is policied on `id = current_tenant()`, so the
tenant id is reserved and bound before its own row is inserted. In one
transaction, as the owner:

    app.tenant_id := nextval(tenants_id_seq)      INSERT tenants
    app.user_id   := nextval(users_id_seq)        INSERT users
                                                  INSERT memberships
                                                  INSERT identities (unpolicied)

Idempotent on the identity: a subject that is already known prints the ids
it already has and changes nothing. The slug comes from the email's local
part; `uq_tenants_slug` decides uniqueness (unique constraints see every
row, policies notwithstanding), and a collision retries with a suffix inside
a savepoint.

## Revoke

Deletes one person who is their own tenant, in the order the foreign keys
and the policies permit, and prints a count per table. Refuses the
operator's tenant (`TENANT_ID`, default 1), a user with more than one
membership, and a tenant with more than one member -- those are not the
shape this script was written for, and guessing would delete someone else's
rows. Cascades (leagues -> teams -> ...) run under referential integrity,
which bypasses row security; the explicit deletes are the ones with no
cascade: leagues before accounts (leagues.account_id), the person's own AI
key (ai_credentials, revision 0018), then jobs, schedules and outbox, which
are unpolicied and keyed by tenant.

Measured on PostgreSQL 16 as a NOSUPERUSER NOBYPASSRLS owner in
`tests/test_provision_user.py` (the PostgreSQL half runs only where a
cluster is reachable and says so when it is not).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

TENANT_GUC = "app.tenant_id"
USER_GUC = "app.user_id"
SLUG_MAX = 40
SLUG_ATTEMPTS = 50

#: Tenant-scoped rows with no ON DELETE CASCADE from `tenants`, in the order
#: the foreign keys permit: leagues reference accounts, so leagues go first;
#: a league's own children cascade. Then the person's rows by user id, then
#: the membership, the user, the tenant.
REVOKE_ORDER: tuple[tuple[str, str], ...] = (
    ("leagues", "tenant_id"),
    ("accounts", "tenant_id"),
    ("ai_credentials", "tenant_id"),
    ("raw_cache", "tenant_id"),
    ("jobs", "tenant_id"),
    ("schedules", "tenant_id"),
    ("outbox", "tenant_id"),
    ("app_sessions", "user_id"),
    ("identities", "user_id"),
    ("memberships", "tenant_id"),
    ("users", "id"),
    ("tenants", "id"),
)


@dataclass(frozen=True)
class Provisioned:
    tenant_id: int
    user_id: int
    membership_id: int
    created: bool


class Refused(RuntimeError):
    """A refusal with a message that names no person."""


def derive_slug(email: str) -> str:
    """The email's local part, lower-cased, non-alphanumerics folded to one
    hyphen, trimmed to SLUG_MAX. Empty becomes "member"."""
    local = email.split("@", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local).strip("-")[:SLUG_MAX].strip("-")
    return slug or "member"


def candidate_slugs(base: str) -> Iterator[str]:
    yield base
    for n in range(2, 2 + SLUG_ATTEMPTS):
        yield f"{base[: SLUG_MAX - len(str(n)) - 1]}-{n}"


def _bind(conn: Connection, guc: str, value: int) -> None:
    conn.execute(text("SELECT set_config(:name, :value, true)"), {"name": guc, "value": str(value)})


def _existing(conn: Connection, issuer: str, subject: str) -> Provisioned | None:
    row = conn.execute(
        text("SELECT user_id FROM identities WHERE issuer = :i AND subject = :s"),
        {"i": issuer, "s": subject},
    ).first()
    if row is None:
        return None
    user_id = int(row[0])
    _bind(conn, USER_GUC, user_id)
    membership = conn.execute(
        text("SELECT id, tenant_id FROM memberships WHERE user_id = :u ORDER BY id"),
        {"u": user_id},
    ).first()
    if membership is None:
        raise Refused(
            "the identity exists but its user holds no membership; not this script's shape"
        )
    return Provisioned(int(membership[1]), user_id, int(membership[0]), created=False)


def _insert_tenant(conn: Connection, tenant_id: int, base: str) -> str:
    for slug in candidate_slugs(base):
        savepoint = conn.begin_nested()
        try:
            conn.execute(
                text("INSERT INTO tenants (id, slug, created_at) VALUES (:id, :slug, now())"),
                {"id": tenant_id, "slug": slug},
            )
        except IntegrityError as exc:
            savepoint.rollback()
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
            if constraint != "uq_tenants_slug":
                raise
            continue
        savepoint.commit()
        return slug
    raise Refused(f"no free slug after {SLUG_ATTEMPTS} suffixes")


def provision(conn: Connection, email: str, subject: str, issuer: str) -> Provisioned:
    """One transaction, as the owner: the caller holds it open (engine.begin())."""
    found = _existing(conn, issuer, subject)
    if found is not None:
        return found
    tenant_id = int(conn.execute(text("SELECT nextval('tenants_id_seq')")).scalar_one())
    _bind(conn, TENANT_GUC, tenant_id)
    _insert_tenant(conn, tenant_id, derive_slug(email))
    user_id = int(conn.execute(text("SELECT nextval('users_id_seq')")).scalar_one())
    _bind(conn, USER_GUC, user_id)
    conn.execute(
        text("INSERT INTO users (id, email, created_at) VALUES (:id, :email, now())"),
        {"id": user_id, "email": email},
    )
    membership_id = int(
        conn.execute(
            text(
                "INSERT INTO memberships (tenant_id, user_id, role) "
                "VALUES (:t, :u, 'member') RETURNING id"
            ),
            {"t": tenant_id, "u": user_id},
        ).scalar_one()
    )
    conn.execute(
        text(
            "INSERT INTO identities (issuer, subject, user_id, created_at) "
            "VALUES (:i, :s, :u, now())"
        ),
        {"i": issuer, "s": subject, "u": user_id},
    )
    return Provisioned(tenant_id, user_id, membership_id, created=True)


def revoke(conn: Connection, subject: str, issuer: str, operator_tenant_id: int) -> dict[str, int]:
    """One transaction, as the owner. Refuses every shape but "one person, their own tenant"."""
    row = conn.execute(
        text("SELECT user_id FROM identities WHERE issuer = :i AND subject = :s"),
        {"i": issuer, "s": subject},
    ).first()
    if row is None:
        raise Refused("no identity for that subject at that issuer")
    user_id = int(row[0])
    _bind(conn, USER_GUC, user_id)
    tenants = [
        int(r[0])
        for r in conn.execute(
            text("SELECT tenant_id FROM memberships WHERE user_id = :u"), {"u": user_id}
        ).all()
    ]
    if len(tenants) != 1:
        raise Refused(
            f"the user holds {len(tenants)} memberships; "
            "this script revokes one person who is their own tenant"
        )
    tenant_id = tenants[0]
    if tenant_id == operator_tenant_id:
        raise Refused("refusing to revoke the operator's tenant")
    _bind(conn, TENANT_GUC, tenant_id)
    members = int(
        conn.execute(
            text("SELECT count(*) FROM memberships WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar_one()
    )
    if members != 1:
        raise Refused(
            f"the tenant has {members} members; "
            "this script revokes one person who is their own tenant"
        )
    keys = {"tenant_id": tenant_id, "user_id": user_id, "id": None}
    counts: dict[str, int] = {}
    for table, column in REVOKE_ORDER:
        value = tenant_id if table == "tenants" else user_id if table == "users" else keys[column]
        result = conn.execute(text(f"DELETE FROM {table} WHERE {column} = :v"), {"v": value})
        counts[table] = int(result.rowcount)
    if counts["tenants"] != 1 or counts["users"] != 1:
        raise Refused("the tenant or the user did not delete; the transaction is rolled back")
    return counts


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="provision_user.py",
        description=(
            "Provision an invited person as their own tenant, or revoke one. "
            "Prints ids or counts only."
        ),
    )
    parser.add_argument(
        "--issuer",
        default=os.environ.get("OIDC_ISSUER", ""),
        help="the OIDC issuer the subject belongs to (default: $OIDC_ISSUER)",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DATABASE_URL", ""),
        help="the OWNER's SQLAlchemy URL, password via PGPASSWORD (default: $DATABASE_URL)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("provision", help="create tenant, user, membership and identity")
    p.add_argument("email")
    p.add_argument("subject", help="the identity provider's sub claim")
    r = sub.add_parser("revoke", help="delete one person who is their own tenant")
    r.add_argument("subject", help="the identity provider's sub claim")
    r.add_argument(
        "--operator-tenant-id",
        type=int,
        default=int(os.environ.get("TENANT_ID", "1")),
        help="the tenant this script refuses to touch (default: $TENANT_ID or 1)",
    )
    args = parser.parse_args(argv)
    if not args.issuer:
        parser.error("an issuer is required: --issuer or $OIDC_ISSUER")
    if not args.url:
        parser.error("a database URL is required: --url or $DATABASE_URL")
    if not args.url.startswith("postgresql"):
        parser.error("the owner URL must be postgresql://; row security is real nowhere else")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    engine = create_engine(args.url, future=True)
    try:
        with engine.begin() as conn:
            if args.command == "provision":
                done = provision(conn, args.email, args.subject, args.issuer)
                print(
                    f"tenant_id={done.tenant_id} user_id={done.user_id} "
                    f"membership_id={done.membership_id}"
                )
            else:
                counts = revoke(conn, args.subject, args.issuer, args.operator_tenant_id)
                print(" ".join(f"{table}={n}" for table, n in counts.items()))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
