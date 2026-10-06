"""Independent Phase 30 source→bundle→restore oracle.

This test intentionally does not import the production catalog, tag, hash,
decode, or manifest helpers. Its expected values are calculated directly from
SQLite storage classes and the frozen format-v1 rules in the accepted contract.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from api import models  # noqa: F401
from api.db import Base
from api.services.recovery import (
    NOT_BUNDLED,
    build_logical_bundle,
    restore_bundle_to_scratch,
)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _rows_digest(rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        encoded = _canonical(row)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _expected_tag(value, declared: str):
    if value is None:
        return ["null", None]
    kind = declared.upper()
    if kind == "JSON":
        return ["json", json.loads(value)]
    if kind == "BOOLEAN":
        assert type(value) is int and value in (0, 1)
        return ["bool", bool(value)]
    if "INT" in kind:
        assert type(value) is int
        return ["int", value]
    if kind in {"FLOAT", "REAL", "DOUBLE", "NUMERIC"}:
        assert type(value) is float
        return ["float", value.hex()]
    if "BLOB" in kind:
        import base64

        assert isinstance(value, bytes)
        return ["bytes", base64.b64encode(value).decode()]
    assert isinstance(value, str)
    return ["datetime" if kind == "DATETIME" else "text", value]


def _storage_class(declared: str) -> str:
    kind = declared.upper()
    if kind == "BOOLEAN" or "INT" in kind:
        return "integer"
    if kind in {"FLOAT", "REAL", "DOUBLE", "NUMERIC"}:
        return "real"
    if "BLOB" in kind:
        return "blob"
    return "text"


def _value(table: str, column: dict, row_number: int, fk_columns: set[str]):
    name = column["name"]
    declared = column["type"].upper()
    nullable = not column["notnull"] and not column["pk"]
    if row_number == 3 and nullable:
        return None
    if (column["pk"] and "INT" in declared) or name in fk_columns:
        return row_number
    if declared == "BOOLEAN":
        return row_number % 2
    if "INT" in declared:
        if name == "season":
            return 2023 + row_number
        return row_number * 1000 + column["cid"]
    if declared in {"FLOAT", "REAL", "DOUBLE", "NUMERIC"}:
        return float(row_number) + (column["cid"] + 1) / 1000
    if declared == "JSON":
        # Deliberately non-canonical source order/spacing.
        return json.dumps({"z": row_number, "column": name, "table": table}, indent=1)
    if declared == "DATETIME":
        return f"2026-08-{10 + row_number:02d} 01:02:03.{column['cid']:06d}+00:00"
    if "BLOB" in declared:
        return f"blob::{table}::{name}::{row_number}".encode()
    return f"text::{table}::{name}::{row_number}::Ω::🏈"


def _source_fixture(path: Path) -> tuple[dict[str, list[dict]], dict[str, dict[str, str]]]:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    expected: dict[str, list[dict]] = {}
    declarations: dict[str, dict[str, str]] = {}
    primary_keys: dict[str, str] = {}
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        # `create_all` is not inert: an `after_create` listener on `tenants`
        # seeds the single default tenant. This oracle's whole claim is
        # "exactly three synthetic rows per table, and every retained value is
        # covered" -- a fourth row nobody generated breaks the claim before the
        # comparison starts, and here it broke it loudly, with
        # `UNIQUE constraint failed: tenants.id` when row 1 was inserted.
        #
        # So start from empty. Reverse dependency order even with foreign keys
        # already off, because the order is the thing a reader checks and it
        # should be right whether or not the pragma is.
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(f'DELETE FROM "{table.name}"')
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            left = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
            assert left == 0, f"{table} did not start empty: {left} row(s)"
        for table in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            columns = [dict(row) for row in connection.execute(f"PRAGMA table_info({quoted_table})")]
            fk_columns = {
                row[3] for row in connection.execute(f"PRAGMA foreign_key_list({quoted_table})")
            }
            declarations[table] = {column["name"]: column["type"] for column in columns}
            keyed = sorted(
                ((c["pk"], c["name"]) for c in columns if c["pk"]), key=lambda i: i[0]
            )
            assert keyed, f"{table} has no primary key; the oracle cannot address its rows"
            primary_keys[table] = keyed[0][1]
            rows = []
            for number in (1, 2, 3):
                row = {
                    column["name"]: _value(table, column, number, fk_columns)
                    for column in columns
                }
                rows.append(row)
                names = ",".join(f'"{name}"' for name in row)
                placeholders = ",".join("?" for _ in row)
                connection.execute(
                    f"INSERT INTO {quoted_table} ({names}) VALUES ({placeholders})",
                    tuple(row.values()),
                )
            expected[table] = rows
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
        for table, rows in expected.items():
            for index, row in enumerate(rows, start=1):
                for column, value in row.items():
                    if value is None:
                        continue
                    quoted_table = '"' + table.replace('"', '""') + '"'
                    quoted_column = '"' + column.replace('"', '""') + '"'
                    # Derived from the schema, not from a hardcoded set of
                    # three names. The set was `{"id", "espn_player_id",
                    # "key"}` and it raised StopIteration as soon as a table
                    # arrived whose key is none of those -- `worker_heartbeats`
                    # is keyed on `owner`. A list of names ages into a
                    # landmine; `PRAGMA table_info` cannot go stale.
                    pk = primary_keys[table]
                    actual = connection.execute(
                        f"SELECT typeof({quoted_column}) FROM {quoted_table} WHERE \"{pk}\"=?",
                        (row[pk],),
                    ).fetchone()[0]
                    assert actual == _storage_class(declarations[table][column]), (
                        table,
                        column,
                        index,
                        actual,
                    )
        return expected, declarations
    finally:
        connection.close()


def test_independent_oracle_covers_every_retained_value_and_detects_substitution(tmp_path):
    source = tmp_path / "source.db"
    source_rows, declarations = _source_fixture(source)
    bundle, manifest = build_logical_bundle(source)
    payload = json.loads(bundle)
    # `NOT_BUNDLED`, not a literal `{"raw_cache"}`: format v2 leaves seven
    # tables out of the bundle, and a hardcoded exclusion here would assert an
    # inventory that drifts the moment another one is added.
    retained_tables = set(source_rows) - NOT_BUNDLED
    assert set(payload["tables"]) == retained_tables
    assert set(manifest["tables"]) == retained_tables

    expected_encoded: dict[str, list[dict]] = {}
    original_statuses = []
    for table in sorted(retained_tables):
        encoded_rows = []
        for source_row in source_rows[table]:
            if table == "accounts":
                original_statuses.append(
                    {"id": source_row["id"], "status": source_row["status"]}
                )
            encoded = {}
            for column, source_value in source_row.items():
                adjusted = source_value
                if table == "accounts" and column == "swid":
                    # Per-row distinct as of format v3. The oracle recomputes
                    # the substitution independently, so it spells the form out
                    # rather than calling the function under test.
                    adjusted = "{REAUTH-REQUIRED-" + str(source_row["id"]) + "}"
                elif table == "accounts" and column == "espn_s2_encrypted":
                    adjusted = "not-a-fernet-token"
                elif table == "accounts" and column == "status":
                    adjusted = "needs_reauth"
                elif table == "teams" and column == "owner_swids_json":
                    adjusted = None
                encoded[column] = _expected_tag(adjusted, declarations[table][column])
            encoded_rows.append(encoded)
        expected_encoded[table] = encoded_rows
        assert payload["tables"][table] == encoded_rows
        assert manifest["tables"][table] == {
            "rows": len(encoded_rows),
            "sha256": _rows_digest(encoded_rows),
        }
    assert manifest["account_statuses"] == original_statuses
    expected_safe = hashlib.sha256(
        _canonical({"account_statuses": original_statuses, "tables": manifest["tables"]})
    ).hexdigest()
    assert manifest["safe_content_digest"] == expected_safe

    restored = tmp_path / "restored.db"
    restore_bundle_to_scratch(bundle, restored)
    connection = sqlite3.connect(restored)
    connection.row_factory = sqlite3.Row
    try:
        for table in sorted(retained_tables):
            columns = list(declarations[table])
            quoted = '"' + table.replace('"', '""') + '"'
            pk = next(name for name in columns if name in {"id", "espn_player_id", "key"})
            actual_rows = [dict(row) for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY \"{pk}\"")]
            for expected, actual in zip(expected_encoded[table], actual_rows, strict=True):
                for column, tagged in expected.items():
                    tag, value = tagged
                    restored_value = actual[column]
                    if tag == "json":
                        assert json.loads(restored_value) == value
                    elif tag == "float":
                        assert restored_value.hex() == value
                    elif tag == "bool":
                        assert restored_value == int(value)
                    else:
                        assert restored_value == value
        assert connection.execute("SELECT count(*) FROM raw_cache").fetchone()[0] == 0
    finally:
        connection.close()

    # Independent omission/substitution detector: mutate one retained value and
    # show that the test oracle differs without calling production validators.
    tampered = json.loads(bundle)
    first_table = sorted(retained_tables)[0]
    first_column = next(iter(tampered["tables"][first_table][0]))
    tampered["tables"][first_table][0][first_column] = ["text", "substituted"]
    assert tampered["tables"][first_table] != expected_encoded[first_table]
