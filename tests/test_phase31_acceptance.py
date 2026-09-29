"""Phase 31 `hosted-data-safety` — the eleven acceptance criteria, in one file.

The deep tests for each mechanism live in `test_hosted_mode.py`,
`test_provenance.py` and `test_spend.py`. This file is the close-out gate: one
assertion per contract criterion, exercising the real code path, so a future
reader can confirm in one run that the phase still holds rather than inferring it
from a list of test names.

Criteria 9 and 10 are the suites themselves (`make test`, `ruff check`, and the
Phase 30 recovery gates) and are not re-run from inside pytest.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.config import get_settings
from api.db import Base
from api.models import AiSpendEntry, AiSpendMonth
from api.services.fixtures import (
    FixtureError,
    assert_loadable,
    build_colliding_tenants,
    build_corpus,
)
from api.services.provenance import (
    ManifestError,
    ProvenanceError,
    assert_clean,
    assert_manifest_complete,
    build_manifest,
    looks_like_real_identifier,
)
from api.services.spend import (
    CEILING_MICRO_USD,
    STATE_RESERVED,
    STATE_SETTLED,
    STATE_UNKNOWN,
    SpendCeilingExceeded,
    SpendLedger,
    SpendLedgerError,
    utc_month,
)

ROOT = Path(__file__).resolve().parents[1]
GATE_MODEL = "acceptance-model"


def _declared(directory: Path) -> None:
    manifest = build_manifest(directory)
    for name, entry in manifest["fixtures"].items():
        entry["origin"] = (
            "recorded" if Path(name).name.lower().startswith("real_") else "synthetic"
        )
        entry["names_reviewed"] = True
        entry["ids_reviewed"] = True
    (directory / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")


# ---------------------------------------------- 1 & 2: the hosted data boundary


@pytest.mark.parametrize(
    "target,expression",
    [
        ("EspnService construction", "EspnService(None)"),
        ("credential custody", "encrypt('x')"),
    ],
)
def test_criteria_1_and_2_hosted_mode_cannot_reach_real_data(target, expression):
    """Run in a child process: `app_mode` is fixed for the life of a process, so
    a same-process test would be asserting against a mode this one is not in."""
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from api.services.espn import EspnService, HostedModeForbidden\n"
        "from api.crypto import encrypt\n"
        "try:\n"
        f"    {expression}\n"
        "    print('ALLOWED')\n"
        "except HostedModeForbidden as error:\n"
        "    print('FORBIDDEN', error)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={
            "PYTHONPATH": str(ROOT),
            "APP_MODE": "public_synthetic",
            "PATH": "/usr/bin:/bin",
            "DB_PATH": str(Path(tempfile.mkdtemp()) / "hosted.db"),
            "FERNET_KEY": "",
            "RECOVERY_REQUIRED": "false",
        },
    )
    assert proc.stdout.startswith("FORBIDDEN"), (target, proc.stdout, proc.stderr[-400:])
    # Stable and secret-free: the message names the operation, never a value.
    for forbidden in ("espn_s2", "swid", "sk-", "cookie="):
        assert forbidden not in proc.stdout.lower()


# ------------------------------------------------- 3: the fixture path grammar


@pytest.mark.parametrize(
    "path",
    ["../etc/passwd", "Real_League.json", "a b.json", "x.JSON", "real_league_2025.json",
     "unknown_but_well_formed.json"],
)
def test_criterion_3_unknown_and_malformed_fixture_paths_raise(path):
    with pytest.raises(FixtureError):
        assert_loadable(path)


# ------------------------------------- 4 & 11: provenance, on content not shape


@pytest.mark.parametrize(
    "probe",
    [
        {"swid": str(uuid.uuid4()).upper()},
        {"espn_s2": "".join("ghijklmnopqrstuvwxyz"[i % 20] for i in range(160))},
        {"members": [{"displayName": "Someone Real"}]},
        {"settings": {"name": "The Real League"}},
    ],
    ids=["swid", "cookie", "member-name", "league-name"],
)
def test_criterion_4_adversarial_probes_fail_the_build(tmp_path, probe):
    (tmp_path / "real_probe.json").write_text(json.dumps(probe), encoding="utf-8")
    (tmp_path / "provenance.json").write_text(
        json.dumps(build_manifest(tmp_path)), encoding="utf-8"
    )
    with pytest.raises((ProvenanceError, ManifestError)):
        assert_clean(tmp_path)
        assert_manifest_complete(tmp_path)


def test_criterion_4_is_two_sided_on_content_not_shape():
    """The correction this phase owes the record: a first pass reported eighteen
    already-scrubbed placeholders as a live exposure. Shape is not content."""
    assert looks_like_real_identifier(str(uuid.uuid4()).upper())
    assert not looks_like_real_identifier("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")


def test_criterion_11_a_capture_cannot_be_added_without_a_declaration(tmp_path):
    (tmp_path / "clean.json").write_text('{"ok": true}', encoding="utf-8")
    _declared(tmp_path)
    (tmp_path / "real_new_capture.json").write_text(
        json.dumps({"swid": str(uuid.uuid4()).upper()}), encoding="utf-8"
    )
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "real_new_capture.json" in str(caught.value)


def test_the_shipped_tree_passes_both_provenance_gates():
    declared = assert_manifest_complete(ROOT / "tests" / "fixtures")
    result = assert_clean(ROOT / "tests" / "fixtures")
    assert declared and result.findings == []
    assert result.candidates > 0, "a sweep that finds no candidates is not a sweep"


# ------------------------------------------------- 5: deterministic synthetics


def test_criterion_5_seeds_reproduce_identical_corpora():
    same = json.dumps(build_corpus(4242), sort_keys=True)
    assert same == json.dumps(build_corpus(4242), sort_keys=True)
    assert same != json.dumps(build_corpus(4243), sort_keys=True)
    assert len(build_corpus(4242)) == 115
    assert len(build_colliding_tenants(4242)) >= 2


# ------------------------------------------------------- 6, 7 & 8: the ceiling


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    monkeypatch.setattr(
        get_settings(),
        "ai_price_micro_usd_per_mtok",
        {GATE_MODEL: (1_000_000, 0)},
        raising=False,
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'acceptance.db'}",
        connect_args={"check_same_thread": False, "timeout": 0},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


def test_criterion_6_three_outcomes_are_each_persisted(ledger_db):
    session = ledger_db()
    ledger = SpendLedger(session)

    def hold():
        return ledger.reserve(
            kind="k", model=GATE_MODEL, input_tokens=100_000, max_output_tokens=0
        )

    settled = hold()
    ledger.settle(settled, 40_000)
    lost = hold()
    ledger.record_unknown_spent(lost)
    crashed = hold()

    rows = {row.reservation: row for row in session.scalars(select(AiSpendEntry))}
    assert rows[settled.reservation].state == STATE_SETTLED
    assert rows[settled.reservation].settled_micro_usd == 40_000
    assert rows[lost.reservation].state == STATE_UNKNOWN
    assert rows[lost.reservation].settled_micro_usd is None
    assert rows[crashed.reservation].state == STATE_RESERVED
    # The money, not just the three state strings: a lost response and a crash
    # each stay charged at their full hold.
    assert ledger.committed_micro_usd(utc_month()) == 40_000 + 100_000 + 100_000
    session.close()


def test_criterion_7_parallel_reservations_cannot_exceed_the_ceiling(ledger_db):
    each, workers = 250_000, 40
    granted, refused = [], []
    barrier, lock = threading.Barrier(workers), threading.Lock()

    def attempt():
        session = ledger_db()
        try:
            barrier.wait(timeout=30)
            SpendLedger(session).reserve(
                kind="k", model=GATE_MODEL, input_tokens=each, max_output_tokens=0
            )
            with lock:
                granted.append(1)
        except SpendCeilingExceeded:
            with lock:
                refused.append("ceiling")
        except SpendLedgerError as error:
            with lock:
                refused.append(type(error).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=attempt) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    audit = ledger_db()
    committed = audit.scalar(
        select(AiSpendMonth.committed_micro_usd).where(AiSpendMonth.month == utc_month())
    )
    entries = len(audit.scalars(select(AiSpendEntry)).all())
    audit.close()

    assert len(granted) + len(refused) == workers, "every worker must reach a verdict"
    assert set(refused) == {"ceiling"}, sorted(set(refused))
    assert committed <= CEILING_MICRO_USD
    assert committed == len(granted) * each
    assert entries == len(granted), "a refused reservation leaves no row"
    assert len(granted) == CEILING_MICRO_USD // each == 20


def test_criterion_8_has_no_retry_and_no_queue():
    """The behavioural halves live in `test_spend.py`; this is the structural
    one, and it is named for what it establishes rather than implying more."""
    sources = "".join(
        (ROOT / "api" / "services" / name).read_text(encoding="utf-8")
        for name in ("spend.py", "ai.py")
    )
    for construct in ("time.sleep", "backoff", "Queue(", "celery", "apply_async"):
        assert construct not in sources, construct
