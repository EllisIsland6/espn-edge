"""Phase 31 unit 3 — the scanner must assert on content, not shape.

Acceptance criterion 4 requires a two-sided control: a real-entropy identifier
fails the build, and a low-entropy synthetic placeholder of identical shape does
not. Without both sides a scanner can be green for the wrong reason — which is
exactly how this phase's own false alarm happened.
"""

from __future__ import annotations

import json
import statistics
import uuid
from pathlib import Path

import pytest

from api.services.fixtures import (
    FixtureError,
    assert_loadable,
    build_colliding_tenants,
    build_corpus,
    build_league,
    malformed_variants,
    synthetic_swid,
)
from api.services.provenance import (
    _ID_PLAUSIBLE_MAGNITUDE,
    _MIN_IDENTIFIER_ENTROPY_BITS,
    _MIN_LONG_TOKEN_CHARS,
    _MIN_REDACTED_DIGITS,
    _NAME_FIELDS,
    _PLAYER_PATH_MARKERS,
    ManifestError,
    ProvenanceError,
    ScanResult,
    _content_digest,
    _redact,
    assert_clean,
    assert_manifest_complete,
    build_manifest,
    looks_like_real_identifier,
    scan_file,
    scan_payload,
    shannon_entropy_bits,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------- two-sided control


def test_a_real_entropy_identifier_is_detected():
    """Side one: a genuine random GUID must be caught."""
    for _ in range(25):
        assert looks_like_real_identifier(str(uuid.uuid4()).upper())


def test_a_synthetic_placeholder_of_identical_shape_is_not():
    """Side two: same shape, scrubbed content, must NOT be caught.

    A shape-only scanner passes side one and fails side two. That asymmetry is
    the whole point of the control.
    """
    for index in range(25):
        assert not looks_like_real_identifier(synthetic_swid(4242, index))


def test_the_two_sides_are_not_near_the_threshold():
    """Guard against a threshold that happens to work by luck -- asserted
    against the threshold the scanner actually uses.

    This read `min(real) > 3.0` and `max(fake) < 2.0`, and 3.0 is not the
    production floor: `_MIN_IDENTIFIER_ENTROPY_BITS` is 2.5. So a test whose
    stated purpose is to stop a lucky threshold was guarding a number the
    scanner does not use, with a margin the real distribution crosses.

    Measured over 50,000 `uuid4()` draws: min 2.8585, p01 3.2653, median
    3.6289, max 3.9528. Seven draws (0.014%) fell below 3.0 and **none** fell
    below the production floor, so the old bound tripped roughly once in 290
    runs -- a coin-flip failure that announces itself as a provenance-scanner
    defect. It fired on the first run of this suite the way CI runs it (Linux,
    Python 3.12), at `min = 2.9747`.

    The synthetic side, over 9,950 placeholders: min 0.3373, median 1.0559,
    max 1.1809 -- 1.32 bits of headroom below the floor, and not one of them
    is flagged as a real identifier.

    A failure here now means something true: a real identifier whose entropy
    does not clear the floor the scanner gates on.
    """
    real = [shannon_entropy_bits(str(uuid.uuid4()).replace("-", "")) for _ in range(25)]
    fake = [
        shannon_entropy_bits(synthetic_swid(7, i).strip("{}").replace("-", ""))
        for i in range(25)
    ]
    # Both populations on the correct side of the floor the scanner uses.
    assert min(real) > _MIN_IDENTIFIER_ENTROPY_BITS, min(real)
    assert max(fake) < _MIN_IDENTIFIER_ENTROPY_BITS, max(fake)
    # And not by a hair -- stated on the medians, which do not carry the tail
    # that made the old bound flaky. This is the "not near the threshold"
    # claim: a property of the two distributions rather than of one sample's
    # unluckiest draw.
    assert statistics.median(real) - _MIN_IDENTIFIER_ENTROPY_BITS > 0.5
    assert _MIN_IDENTIFIER_ENTROPY_BITS - statistics.median(fake) > 0.5


def test_scanner_reports_candidates_separately_from_findings():
    """A shape match is a candidate, not a finding. The counts must differ."""
    payload = {"members": [{"id": synthetic_swid(1, i)} for i in range(9)]}
    result = scan_payload(payload)
    assert result.candidates == 9
    assert result.findings == []
    assert result.clean is True


def test_scanner_catches_a_real_identifier_in_a_payload():
    payload = {"members": [{"id": str(uuid.uuid4()).upper()}]}
    result = scan_payload(payload)
    assert result.candidates == 1
    assert len(result.findings) == 1
    assert result.clean is False


def test_findings_never_disclose_the_offending_value():
    """The Finding must not carry the value at all, not merely omit it in describe().

    Checking only describe() would pass even if the raw value were stored on the
    dataclass, where any future formatter could surface it.
    """
    secret = str(uuid.uuid4()).upper()
    result = scan_payload({"members": [{"id": secret}]})
    finding = result.findings[0]
    described = finding.describe()
    assert secret not in described
    assert secret.strip("{}") not in described
    assert "members" in described
    # No attribute anywhere on the finding may contain the value or its hex body.
    body = secret.replace("-", "").strip("{}")
    for value in vars(finding).values():
        rendered = str(value)
        assert secret not in rendered
        assert body not in rendered


# ------------------------------------------------------------ real fixtures


def test_the_shipped_fixtures_are_clean():
    """The corrected version of this phase's false alarm, as a standing test."""
    result = assert_clean(FIXTURES)
    assert result.candidates >= 36, "shape candidates should still be present"
    assert result.findings == []


def test_assert_clean_raises_on_a_real_identifier(tmp_path):
    (tmp_path / "leaky.json").write_text(
        json.dumps({"members": [{"id": str(uuid.uuid4()).upper()}]}), encoding="utf-8"
    )
    with pytest.raises(ProvenanceError) as caught:
        assert_clean(tmp_path)
    assert "leaky.json" in str(caught.value)


# ---------------------------------------------------------------- manifest


def test_every_shipped_fixture_is_declared_and_scrubbed():
    declared = assert_manifest_complete(FIXTURES)
    recorded = {k: v for k, v in declared.items() if v["origin"] == "recorded"}
    assert recorded, "the recorded captures should be declared, not hidden"
    assert all(entry["scrubbed"] for entry in recorded.values())


def test_an_undeclared_fixture_fails_the_build(tmp_path):
    (tmp_path / "provenance.json").write_text(
        json.dumps(build_manifest(tmp_path)), encoding="utf-8"
    )
    (tmp_path / "sneaked_in.json").write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "sneaked_in.json" in str(caught.value)


def test_an_unscrubbed_recorded_capture_fails_the_build(tmp_path):
    (tmp_path / "real_capture.json").write_text(
        json.dumps({"members": [{"id": str(uuid.uuid4()).upper()}]}), encoding="utf-8"
    )
    manifest = build_manifest(tmp_path)
    assert manifest["fixtures"]["real_capture.json"]["scrubbed"] is False
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "real_capture.json" in str(caught.value)


# ------------------------------------------------------------ path grammar


@pytest.mark.parametrize(
    "name",
    ["../etc/passwd", "Public_League.json", "public league.json", "public_league.txt",
     "public_league.json/", "", ".json", "9lives.json"],
)
def test_paths_violating_the_grammar_are_rejected(name):
    """Assert on the *reason*, not merely that something raised.

    Mutation testing caught this: with the grammar relaxed to accept anything,
    an earlier version of this test still passed, because the allowlist check
    rejected these names independently. The test claimed to verify the grammar
    while actually being satisfied by a different check. Pinning the message
    makes the grammar itself load-bearing.
    """
    with pytest.raises(FixtureError) as caught:
        assert_loadable(name)
    assert "path grammar" in str(caught.value)


@pytest.mark.parametrize(
    "name",
    ["../etc/passwd", "Public_League.json", "public league.json", ".json"],
)
def test_the_grammar_rejects_before_any_allowlist_lookup(name):
    """Even with the allowlist bypassed, the grammar must still reject."""
    with pytest.raises(FixtureError) as caught:
        assert_loadable(name, allow_recorded=True)
    assert "path grammar" in str(caught.value)


def test_a_recorded_capture_is_not_loadable_even_though_it_matches_the_grammar():
    """Grammar alone is insufficient: real_league_2026.json is a valid name."""
    with pytest.raises(FixtureError) as caught:
        assert_loadable("real_league_2026.json")
    assert "recorded capture" in str(caught.value)


def test_an_unknown_but_well_formed_name_is_rejected():
    with pytest.raises(FixtureError) as caught:
        assert_loadable("plausible_but_absent.json")
    assert "allowlist" in str(caught.value)


def test_allowlisted_synthetic_fixtures_load():
    assert_loadable("public_league.json")


# ------------------------------------------------------------ determinism


def test_a_corpus_is_reproducible_from_its_seed_alone():
    assert build_corpus(1234) == build_corpus(1234)


def test_different_seeds_produce_different_corpora():
    assert build_corpus(1234) != build_corpus(1235)


def test_the_corpus_has_the_contract_stated_shape():
    corpus = build_corpus(1234)
    assert len(corpus) == 115
    assert all(league["seasonId"] == 2026 for league in corpus)


def test_the_generated_corpus_contains_no_real_identifiers():
    result = scan_payload(build_corpus(99), "corpus")
    assert result.candidates > 1000, "the corpus should contain GUID-shaped values"
    assert result.findings == []


def test_colliding_tenants_share_an_id_but_differ():
    a, b = build_colliding_tenants(7)
    assert a["id"] == b["id"]
    assert a["members"][0]["id"] != b["members"][0]["id"]


def test_malformed_variants_are_distinct_and_described():
    variants = malformed_variants(3)
    assert len(variants) >= 8
    assert len({v.name for v in variants}) == len(variants)
    assert all(v.why for v in variants)


def test_a_league_is_generated_not_recorded():
    """No recorded capture may be an input to generation."""
    league = build_league(5)
    assert league["settings"]["name"].startswith("Synthetic")
    assert all(m["displayName"].startswith("synthetic_") for m in league["members"])


# ------------------------------------------- review-driven hardening (unit 3b)


def _declared(manifest: dict) -> dict:
    """Classify and review every entry, as an operator would.

    `origin` is a human declaration now: `build_manifest` writes "undeclared" for
    any file it has not seen before, so a new fixture cannot pass the gate until
    someone says what it is. This helper is that someone.
    """
    for name, entry in manifest["fixtures"].items():
        if entry.get("origin") not in ("recorded", "synthetic"):
            entry["origin"] = (
                "recorded" if Path(name).name.lower().startswith("real_") else "synthetic"
            )
        entry["names_reviewed"] = True
        entry["ids_reviewed"] = True
    return manifest


def _planted(tmp_path, relative: str, payload: dict) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_a_lying_manifest_cannot_declare_a_capture_scrubbed(tmp_path):
    """The gate must re-scan, not trust its own declaration.

    Review demonstrated a hand-written entry claiming scrubbed:true over a file
    of live GUIDs passing green, while the docstring claimed the build would
    fail. That is a check asserting a conclusion it never established.
    """
    _planted(tmp_path, "real_capture.json",
             {"members": [{"id": str(uuid.uuid4()).upper()}]})
    (tmp_path / "provenance.json").write_text(json.dumps({
        "version": 1,
        "fixtures": {"real_capture.json": {
            "origin": "recorded", "scrubbed": True,
            "shape_candidates": 1, "real_identifier_findings": 0,
            "name_fields": 0, "names_reviewed": True,
        }},
    }), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "real_capture.json" in str(caught.value)


def test_a_capture_one_directory_down_is_still_caught(tmp_path):
    """Criterion 11 says 'added to the tree', not 'added to the top level'.

    Review planted recorded/real_league_2027.json and both gates stayed green,
    because each was bound to a top-level glob.
    """
    _planted(tmp_path, "recorded/real_league_2027.json",
             {"members": [{"id": str(uuid.uuid4()).upper()}]})
    with pytest.raises(ProvenanceError):
        assert_clean(tmp_path)


def test_a_nested_capture_must_also_be_declared(tmp_path):
    _planted(tmp_path, "clean.json", {"ok": True})
    (tmp_path / "provenance.json").write_text(
        json.dumps(build_manifest(tmp_path)), encoding="utf-8")
    _planted(tmp_path, "nested/real_hidden.json", {"members": [{"id": "{AAAA0000-0000-0000-0000-000000000001}"}]})
    with pytest.raises(ManifestError):
        assert_manifest_complete(tmp_path)


def test_name_fields_require_an_explicit_reviewed_declaration(tmp_path):
    """Names cannot be classified by content, so they must be declared.

    Four content rules were tried and each failed; the honest gate forces a
    human decision instead of guessing.
    """
    _planted(tmp_path, "real_named.json",
             {"members": [{"displayName": "Someone Real"}]})
    manifest = build_manifest(tmp_path)
    entry = manifest["fixtures"]["real_named.json"]
    assert entry["name_fields"] == 1
    entry["names_reviewed"] = False
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "name fields" in str(caught.value)


def test_declaring_names_reviewed_allows_the_build(tmp_path):
    _planted(tmp_path, "real_named.json",
             {"members": [{"displayName": "placeholder"}]})
    manifest = build_manifest(tmp_path)
    entry = manifest["fixtures"]["real_named.json"]
    # BOTH are required for a recorded capture now, whatever the counts say:
    # gating the requirement on the scanner's own counts meant the human was
    # asked only where the scanner had already seen the risk.
    entry["names_reviewed"] = True
    entry["ids_reviewed"] = True
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert assert_manifest_complete(tmp_path)


def test_dict_keys_and_large_integers_are_scanned():
    """Review found the walker yielding string values only."""
    keyed = {"byOwner": {str(uuid.uuid4()).upper(): {"ok": True}}}
    assert scan_payload(keyed).findings, "a GUID-valued dict key must be caught"
    assert scan_payload({"leagueId": 17739342}).scanned > 0


def test_a_guid_valued_key_is_not_echoed_into_the_error():
    """The one class the scanner could not detect was the one it printed."""
    secret = str(uuid.uuid4()).upper()
    result = scan_payload({"byOwner": {secret: {"id": secret}}})
    for finding in result.findings:
        assert secret not in finding.json_path
        assert secret not in finding.describe()


def test_a_long_non_hex_secret_is_not_starved_into_a_pass():
    """_hex_body stripped a non-hex secret to empty and waved it through."""
    # Deliberately avoids a-f: those ARE hex, so a first version of this test
    # never reached the starvation branch it claimed to cover.
    alphabet = "ghijklmnopqrstuvwxyz"
    secret = "".join(alphabet[i % len(alphabet)] for i in range(120))
    from api.services.provenance import _hex_body

    assert _hex_body(secret) == "", "the fixture must actually starve the hex body"
    assert looks_like_real_identifier(secret, "long-token")
    assert not looks_like_real_identifier(secret, "guid")


def test_urls_are_not_treated_as_credentials():
    payload = {"teams": [{"logo": "https://example.invalid/" + "a" * 60}]}
    assert scan_payload(payload).findings == []


# ------------------------------------- second-review hardening (unit 3c)
# Every fix below is paired with a test. The previous round shipped three code
# fixes with no tests and review caught all three by mutation.


def test_a_nested_capture_sharing_a_basename_is_not_excused(tmp_path):
    """The sweep found the file and then excused it.

    A `p.name` fallback let a capture nested under a subdirectory collapse onto
    an already-declared top-level file of the same name, defeating the sweep
    added to close the previous nested-capture finding.
    """
    _planted(tmp_path, "real_league_2025.json", {"members": [{"displayName": "x"}]})
    (tmp_path / "provenance.json").write_text(
        json.dumps(_declared(build_manifest(tmp_path))), encoding="utf-8")
    _planted(tmp_path, "recorded/real_league_2025.json",
             {"members": [{"displayName": "Someone Real"}]})
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "recorded/real_league_2025.json" in str(caught.value)


@pytest.mark.parametrize("prefix", ["https://e.invalid/?s2=", "http://x/?t=", "//x/?t="])
def test_a_secret_behind_a_url_prefix_is_still_caught(prefix):
    """A two-character `//` prefix was a complete evasion.

    The whole value was skipped on a URL prefix, but a credential's home in a
    URL is the query string.
    """
    secret = "".join("ghijklmnopqrstuvwxyz"[i % 20] for i in range(120))
    assert scan_payload({"t": prefix + secret}).findings
    assert scan_payload({"t": secret}).findings


def test_a_genuine_url_is_still_not_a_credential():
    assert scan_payload(
        {"teams": [{"logo": "https://example.invalid/" + "a" * 60}]}
    ).findings == []


def test_numeric_ids_are_counted_and_declared_not_pretend_detected():
    """An earlier branch yielded ints to matchers that could never match."""
    result = scan_payload({"leagueId": 17739342}, "real_x.json", recorded=True)
    assert result.id_fields == 1
    assert result.findings == []


def test_a_capture_with_id_fields_requires_an_ids_reviewed_declaration(tmp_path):
    _planted(tmp_path, "real_ids.json", {"leagueId": 17739342})
    manifest = build_manifest(tmp_path)
    manifest["fixtures"]["real_ids.json"]["ids_reviewed"] = False
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "id fields" in str(caught.value)


def test_a_declaration_stops_vouching_when_the_content_changes(tmp_path):
    """Counts alone let a declaration vouch for bytes that changed next day."""
    _planted(tmp_path, "real_named.json", {"members": [{"displayName": "placeholder"}]})
    (tmp_path / "provenance.json").write_text(
        json.dumps(_declared(build_manifest(tmp_path))), encoding="utf-8")
    assert assert_manifest_complete(tmp_path)
    # Same path, same count, different value.
    _planted(tmp_path, "real_named.json", {"members": [{"displayName": "Someone Real"}]})
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "disagrees with a rescan" in str(caught.value)


def test_regenerating_preserves_a_declaration_only_while_content_holds(tmp_path):
    _planted(tmp_path, "real_named.json", {"members": [{"displayName": "placeholder"}]})
    (tmp_path / "provenance.json").write_text(
        json.dumps(_declared(build_manifest(tmp_path))), encoding="utf-8")
    assert build_manifest(tmp_path)["fixtures"]["real_named.json"]["names_reviewed"]
    _planted(tmp_path, "real_named.json", {"members": [{"displayName": "changed"}]})
    assert not build_manifest(tmp_path)["fixtures"]["real_named.json"]["names_reviewed"]


@pytest.mark.parametrize("name", ["real_x.JSON", "real_x.json.orig", "real_capture.har"])
def test_the_sweep_covers_any_extension(tmp_path, name):
    _planted(tmp_path, "clean.json", {"ok": True})
    (tmp_path / "provenance.json").write_text(
        json.dumps(build_manifest(tmp_path)), encoding="utf-8")
    (tmp_path / name).write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert name in str(caught.value)


def test_a_numeric_key_is_redacted_from_the_error(tmp_path):
    """`_redact` covered GUID keys only; numeric keys are the ESPN shape."""
    secret = str(uuid.uuid4()).upper()
    result = scan_payload({"17739342": {"id": secret}})
    assert result.findings
    for finding in result.findings:
        assert "17739342" not in finding.json_path
        assert "17739342" not in finding.describe()


def test_the_manifest_disagreement_branch_is_load_bearing(tmp_path):
    """Review found every rescan comparison deletable with all tests green."""
    _planted(tmp_path, "clean.json", {"ok": True})
    manifest = _declared(build_manifest(tmp_path))
    manifest["fixtures"]["clean.json"]["shape_candidates"] = 999
    manifest["fixtures"]["clean.json"]["real_identifier_findings"] = 7
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "disagrees with a rescan" in str(caught.value)


# -------------------------------------- third-review hardening (unit 3d)
# The previous round replaced a whole-value URL skip with a query/fragment-only
# scan. That closed the "//" evasion and opened a new one: a credential sitting
# in a PATH segment became invisible. Both rounds shipped a placement rule, and
# placement was the wrong axis. Round three replaced it with segmentation, and
# these tests pin BOTH directions of that change: every placement is caught, and
# the genuine URLs the skip existed to protect are still clean.

_URL_PLACEMENTS = {
    "path-only": "https://h.invalid/{s}",
    "path-mid": "https://h.invalid/a/{s}/b",
    "path-with-extension": "https://h.invalid/{s}.json",
    "query-first": "https://h.invalid/p?t={s}",
    "query-later": "https://h.invalid/p?a=1&t={s}",
    "fragment": "https://h.invalid/p#{s}",
    "userinfo": "https://{s}@h.invalid/p",
    "matrix-param": "http://h.invalid/p;t={s}",
    "protocol-relative": "//h.invalid/{s}",
}

# Real ESPN URL shapes. Each scores well above the entropy floor as ONE token,
# so whole-value scoring flags all three; segmentation clears them because a
# URL's structural parts are short words and ids, not long opaque runs.
_GENUINE_URLS = [
    "https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/phi.png",
    "https://g.espncdn.com/lm-static/logo-packs/core/Football-Sunday/Football-Sunday-1.svg",
    "https://secure.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/4362628.png&w=350&h=254",
]


def _long_secret() -> str:
    return "".join("ghijklmnopqrstuvwxyz"[i % 20] for i in range(120))


@pytest.mark.parametrize("placement", sorted(_URL_PLACEMENTS))
def test_a_credential_is_caught_in_every_url_placement(placement):
    """Query-and-fragment-only scanning made a path-segment credential invisible.

    The round-two fix closed the "//" evasion by scanning the part of the value
    after "?" or "#". A credential in `https://host/<token>/x` sits in neither,
    so it passed clean. No placement is privileged now.
    """
    value = _URL_PLACEMENTS[placement].format(s=_long_secret())
    assert scan_payload({"t": value}).findings, placement


@pytest.mark.parametrize("url", _GENUINE_URLS)
def test_segmentation_not_luck_is_what_clears_a_genuine_url(url):
    """The two-sided control: the URL skip was load-bearing and still is.

    If this only asserted cleanliness it would pass for a scanner that had
    stopped detecting anything, so it first establishes that whole-value scoring
    WOULD have flagged the URL, then shows segmentation is the reason it does
    not: every structural segment is shorter than the long-token floor.
    """
    from api.services.provenance import _MIN_LONG_TOKEN_CHARS, _url_segments

    assert looks_like_real_identifier(url, "long-token"), (
        "precondition: whole-value scoring must flag this URL, or the test proves nothing"
    )
    assert scan_payload({"logo": url}).findings == []
    assert max(len(part) for part in _url_segments(url)) < _MIN_LONG_TOKEN_CHARS


def test_two_different_undecidable_sets_cannot_share_a_digest():
    """The digest joined "path=value" pairs with "|", so values containing the
    separators collided with an entirely different pair set.

    Both payloads below produce the identical joined material, so under the
    removed join one declaration vouched for the other's content. The digest is
    what makes a human declaration binding; a collision un-binds it silently.
    """
    one = scan_payload({"settings": {"name": "1|.teams[0].name=2"}}, recorded=True)
    two = scan_payload(
        {"settings": {"name": "1"}, "teams": [{"name": "2"}]}, recorded=True
    )
    def naive(result):
        return "|".join(f"{p}={v}" for p, v in sorted(result.undecidable))

    assert one.undecidable != two.undecidable
    assert naive(one) == naive(two), (
        "precondition: these must collide under the removed join, or the test proves nothing"
    )
    assert one.undecidable_digest != two.undecidable_digest


# ------------------------------------------- manifest-identity fixes (unit 3d)
# Making the digest full-length pushed it past the long-token floor, so the
# scanner began flagging its own record. Truncating the digest back would have
# weakened a gate to silence a scanner; excluding the manifest wholesale would
# have hidden the one part of it a human writes. These pin the narrow exclusion
# actually taken, and the basename hole found while taking it.


def test_a_capture_named_like_the_manifest_is_not_excused(tmp_path):
    """Both manifest exclusions compared a basename, not a path.

    A capture planted at `recorded/provenance.json` was dropped from the sweep
    AND from the declaration requirement -- the same escape hatch review closed
    on the fixture side, still open on the manifest side.
    """
    _planted(tmp_path, "clean.json", {"ok": True})
    (tmp_path / "provenance.json").write_text(
        json.dumps(_declared(build_manifest(tmp_path))), encoding="utf-8")
    _planted(tmp_path, "recorded/provenance.json", {"swid": str(uuid.uuid4()).upper()})
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "recorded/provenance.json" in str(caught.value)


def test_a_real_identifier_in_a_human_reviewed_note_is_still_caught(tmp_path):
    """The exclusion drops one machine field, not the human-written part.

    `reviewed_note` is free text an operator types while reviewing member names.
    It is exactly where a real league or member name would land, so the manifest
    is scanned, not skipped.
    """
    _planted(tmp_path, "clean.json", {"ok": True})
    manifest = _declared(build_manifest(tmp_path))
    manifest["fixtures"]["clean.json"]["reviewed_note"] = (
        "checked against SWID " + str(uuid.uuid4()).upper()
    )
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProvenanceError) as caught:
        assert_clean(tmp_path)
    assert "provenance.json" in str(caught.value)


def test_the_dropped_digest_field_is_covered_by_a_stricter_gate(tmp_path):
    """The justification for dropping the field, asserted rather than claimed.

    `undecidable_digest` leaves the entropy scan because the manifest gate
    recomputes it from the fixture. If that were not true the drop would be a
    hiding place; this is the test that establishes it.
    """
    _planted(tmp_path, "clean.json", {"ok": True})
    manifest = _declared(build_manifest(tmp_path))
    manifest["fixtures"]["clean.json"]["undecidable_digest"] = str(uuid.uuid4()).upper()
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "disagrees with a rescan" in str(caught.value)


def test_a_finding_names_the_file_it_is_in_not_a_basename(tmp_path):
    """A sweep finding used to name the basename, sending an operator to the
    wrong file when two directories hold the same name."""
    _planted(tmp_path, "recorded/leaky.json", {"swid": str(uuid.uuid4()).upper()})
    with pytest.raises(ProvenanceError) as caught:
        assert_clean(tmp_path)
    assert "recorded/leaky.json" in str(caught.value)


# ------------------------------------------- third-review hardening (unit 3e)
# Round three's reviewers mutated ~97 branches, constants and comparisons in
# provenance.py and found 51 survived: thresholds, the token alphabet, the
# digest's `sorted()`, four of five name fields, the player exclusion and three
# of five rescan comparisons were all deletable with the suite green. A constant
# no test pins is a constant anyone may quietly change. These pin them.

_LEAGUE_NAME = "The Invented Family League"
_MEMBER_NAME = "Invented Person"
_CAPTURE = {
    "settings": {"name": _LEAGUE_NAME},
    "members": [{"displayName": _MEMBER_NAME, "firstName": "Invented", "lastName": "Person"}],
    "teams": [{"location": "Invented", "nickname": "Squad", "abbrev": "IPX"}],
    "id": "82640194",
}


def _gate(tmp_path):
    """Run both gates, returning ("GREEN"|message, "GREEN"|message)."""
    try:
        assert_clean(tmp_path)
        content = "GREEN"
    except ProvenanceError as error:
        content = str(error)
    try:
        assert_manifest_complete(tmp_path)
        manifest = "GREEN"
    except ManifestError as error:
        manifest = str(error)
    return content, manifest


def _baseline(tmp_path):
    """A declared, green fixture tree to plant things into."""
    _planted(tmp_path, "clean.json", {"ok": True})
    (tmp_path / "provenance.json").write_text(
        json.dumps(_declared(build_manifest(tmp_path))), encoding="utf-8")
    return tmp_path


# ----------------------------------------------- nothing is exempt by extension


@pytest.mark.parametrize(
    "name",
    ["espn_capture.har", "capture.JSON", "league.json.bak", "members.ndjson", "README.md"],
)
def test_no_file_is_outside_the_gate_because_of_its_extension(tmp_path, name):
    """Both gates were bound to `*.json` and the any-extension sweep fired only
    on a `real_` prefix, so anything that was neither was in neither set.

    A `.har` is the most likely artifact of "record a live capture to debug
    this", and `README.md` is the file this phase already found a real league
    name in. Both reported green.
    """
    _baseline(tmp_path)
    (tmp_path / name).write_text(json.dumps(_CAPTURE), encoding="utf-8")
    _, manifest = _gate(tmp_path)
    assert name in manifest


def test_a_non_json_file_is_scanned_line_by_line(tmp_path):
    _baseline(tmp_path)
    (tmp_path / "NOTES.md").write_text(
        f"# notes\n\nswid {str(uuid.uuid4()).upper()}\n", encoding="utf-8")
    content, _ = _gate(tmp_path)
    assert "NOTES.md" in content and "line-3" in content


# ------------------------------------------------- origin is a human decision


def test_a_capture_under_a_natural_name_cannot_pass(tmp_path):
    """The whole name and id control was switched on by a filename prefix.

    A capture saved as anything but `real_*` was scanned as synthetic -- zero
    name fields, zero id fields -- and `build_manifest` then auto-declared it
    reviewed because its own counts were zero. Nobody was asked anything.
    """
    _baseline(tmp_path)
    (tmp_path / "league_capture.json").write_text(json.dumps(_CAPTURE), encoding="utf-8")
    _, manifest = _gate(tmp_path)
    assert "league_capture.json" in manifest

    # Regenerating the manifest is the operator's obvious next move. It must not
    # be the thing that unblocks the build.
    regenerated = build_manifest(tmp_path)
    (tmp_path / "provenance.json").write_text(json.dumps(regenerated), encoding="utf-8")
    entry = regenerated["fixtures"]["league_capture.json"]
    assert entry["origin"] == "undeclared"
    assert entry["names_reviewed"] is False and entry["ids_reviewed"] is False
    assert entry["name_fields"] >= 6, "an undeclared file is scanned on the strict side"
    _, manifest = _gate(tmp_path)
    assert "no origin declaration" in manifest


def test_a_real_capture_cannot_be_relabelled_synthetic(tmp_path):
    _planted(tmp_path, "real_cap.json", dict(_CAPTURE))
    manifest = _declared(build_manifest(tmp_path))
    manifest["fixtures"]["real_cap.json"]["origin"] = "synthetic"
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "declared as synthetic" in str(caught.value)


@pytest.mark.parametrize("declaration", ["names_reviewed", "ids_reviewed"])
def test_a_recorded_capture_needs_both_declarations_whatever_the_counts(tmp_path, declaration):
    """The requirement used to fire only where the scanner had counted
    something, so it could never backstop the scanner's blind spots."""
    _planted(tmp_path, "real_cap.json", {"ok": True})  # zero names, zero ids
    manifest = _declared(build_manifest(tmp_path))
    manifest["fixtures"]["real_cap.json"][declaration] = False
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "real_cap.json" in str(caught.value)


def test_build_manifest_never_auto_declares_a_recorded_capture(tmp_path):
    _planted(tmp_path, "real_cap.json", {"ok": True})
    entry = build_manifest(tmp_path)["fixtures"]["real_cap.json"]
    assert entry["names_reviewed"] is False and entry["ids_reviewed"] is False


def test_the_manifest_has_no_free_text_field(tmp_path):
    """A note field is a channel for the one content class nothing can detect."""
    _baseline(tmp_path)
    manifest = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    manifest["fixtures"]["clean.json"]["reviewed_note"] = f"checked {_LEAGUE_NAME}"
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "unknown fields" in str(caught.value)


# ------------------------------------ a declaration must vouch for bytes


def test_a_declaration_does_not_survive_a_change_the_scanner_cannot_see(tmp_path):
    """`undecidable_digest` covers only what the scanner classified.

    A JSON string id matches neither the GUID rule nor the 32-character token
    rule, so a capture re-recorded against a different real league id had a
    byte-identical undecidable digest: the carry-forward held and both gates
    stayed green.
    """
    _planted(tmp_path, "real_cap.json", {**_CAPTURE, "id": "11111111"})
    (tmp_path / "provenance.json").write_text(
        json.dumps(_declared(build_manifest(tmp_path))), encoding="utf-8")
    assert assert_manifest_complete(tmp_path)

    before = scan_file(tmp_path / "real_cap.json", recorded=True).undecidable_digest
    _planted(tmp_path, "real_cap.json", {**_CAPTURE, "id": "82640194"})
    after = scan_file(tmp_path / "real_cap.json", recorded=True).undecidable_digest
    assert before == after, "precondition: the change must be invisible to the projection"

    with pytest.raises(ManifestError):
        assert_manifest_complete(tmp_path)
    assert build_manifest(tmp_path)["fixtures"]["real_cap.json"]["names_reviewed"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("scrubbed", False),
        ("real_identifier_findings", 7),
        ("name_fields", 9),
        ("id_fields", 9),
        ("undecidable_digest", "0" * 64),
        ("content_digest", "0" * 64),
    ],
)
def test_every_rescan_comparison_is_load_bearing(tmp_path, field, value):
    """Three of five comparisons were deletable with the suite green; one test
    was setting two fields at once and only one of them mattered."""
    _planted(tmp_path, "real_cap.json", dict(_CAPTURE))
    manifest = _declared(build_manifest(tmp_path))
    manifest["fixtures"]["real_cap.json"][field] = value
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "disagrees with a rescan" in str(caught.value)


def test_a_stale_manifest_entry_is_named_not_crashed_on(tmp_path):
    _baseline(tmp_path)
    manifest = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    manifest["fixtures"]["gone.json"] = dict(manifest["fixtures"]["clean.json"])
    (tmp_path / "provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "gone.json" in str(caught.value)


# ------------------------------------------------- the gate must not leak


def test_scan_result_never_prints_the_values_it_holds():
    """`Finding` is value-free and tested; its sibling held raw names and had the
    dataclass default repr. pytest prints the receiver of a failing assertion
    with no flags, so a count assertion drifting would have put member names in
    a public CI log."""
    result = scan_payload(_CAPTURE, "x.json", recorded=True)
    assert result.undecidable, "precondition: it must be holding values"
    rendered = repr(result)
    for value in (_LEAGUE_NAME, _MEMBER_NAME, "Squad", "IPX"):
        assert value not in rendered
    assert f"name_fields={result.name_fields}" in rendered


@pytest.mark.parametrize(
    "segment,redacted",
    [
        ("82640194", True),
        ("82640194:2026", True),   # raw-cache key: <leagueId>:<season>
        ("2026_82640194", True),   # raw-cache key: <season>_<leagueId>
        ("82640194.json", True),
        ("The Invented Family League", True),   # a name used as a key
        ("Ed A", True),                         # short enough that only the
                                                # whitespace clause catches it
        ("AbCd12+/EfGh34ijKlMn==", True),       # base64 id, under the token floor
        ("12345", False),                       # below the identifier digit floor
        ("lineupSlotCounts", False),
        ("scoringPeriodId", False),
        ("displayName", False),
    ],
)
def test_path_segments_that_are_themselves_identifiers_are_redacted(segment, redacted):
    """Round two closed this for a bare digit run; one separator reopened it, and
    a name-valued or base64 key was never covered."""
    assert (_redact(segment) == "<redacted>") is redacted


# --------------------------------------------------------- pinned constants


def test_the_entropy_floor_sits_strictly_between_the_two_measured_populations():
    """Both 2.0 and 2.69 survived as floor values.

    2.69 is the measured minimum over real v4 GUID hex bodies and 1.18 the
    measured maximum over factory placeholders. A floor at either end has zero
    margin on one side, so the constant is pinned to the interior.

    Any value strictly inside that interval separates the two populations
    perfectly, so a mutation from 2.5 to 2.0 survives this test on purpose: it
    changes no outcome on either measured population. What must not happen is
    drift past either end, and that is what this asserts.
    """
    assert 1.18 < _MIN_IDENTIFIER_ENTROPY_BITS < 2.69


@pytest.mark.parametrize(
    "token",
    [
        "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6",            # plain alphanumeric
        "a1B2c3%D4e5F6g7H8i9J0k1L2m3N4o5P6",           # percent, as in espn_s2
        "a1B2c3/D4+e5F6g7H8i9J0k1L2m3N4o5P6=",         # base64 punctuation
        "a1B2c3.D4:e5F6-g7H8_i9J0k1L2m3N4o5P6",        # dot, colon, dash, underscore
    ],
)
def test_the_long_token_alphabet_is_pinned(token):
    """The only long-token fixture in the suite was 120 lowercase letters, so
    every character the cookie rule exists for was unexercised and narrowing the
    class to `[A-Za-z0-9]` survived."""
    assert scan_payload({"t": token}).candidates >= 1, token


def test_the_long_token_floor_is_pinned_at_both_edges():
    body = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    assert len(body) > _MIN_LONG_TOKEN_CHARS
    assert scan_payload({"t": body[: _MIN_LONG_TOKEN_CHARS]}).candidates == 1
    assert scan_payload({"t": body[: _MIN_LONG_TOKEN_CHARS - 1]}).candidates == 0


def test_the_id_magnitude_derives_from_the_redaction_floor():
    """The module used to hold both that a six-digit number is identifier-shaped
    enough to redact from a log and that it is structural noise not worth
    counting. Legacy ESPN league ids are five and six digits."""
    assert _ID_PLAUSIBLE_MAGNITUDE == 10 ** (_MIN_REDACTED_DIGITS - 1)


@pytest.mark.parametrize(
    "payload,counted",
    [
        ({"leagueId": 82640194}, 1),
        ({"leagueId": 123456}, 1),
        ({"leagueId": 17739342.0}, 1),   # a round-trip through JS or a spreadsheet
        ({"wins": 3, "losses": 11, "size": 10, "seasonId": 2026, "points": 117}, 0),
    ],
)
def test_the_id_count_is_two_sided(payload, counted):
    assert scan_payload(payload, recorded=True).id_fields == counted


def test_the_digest_is_order_independent_and_full_length():
    """Removing `sorted()` and truncating to eight hex characters both survived,
    against a comment saying the digest is full and sorted."""
    one = ScanResult(undecidable=[(".a", "x"), (".b", "y")])
    two = ScanResult(undecidable=[(".b", "y"), (".a", "x")])
    assert one.undecidable_digest == two.undecidable_digest
    assert len(one.undecidable_digest) == 64


# ------------------------------------------------------ name scope, two-sided


# Written out rather than derived from `_NAME_FIELDS`: parametrising over the
# set under test means deleting an entry also deletes its own test, so four of
# these stayed deletable with the suite green even after the sweep was added.
_EXPECTED_NAME_FIELDS = (
    "abbrev", "displayname", "firstname", "lastname", "location", "nickname",
)


def test_the_name_field_set_is_exactly_what_the_suite_checks():
    assert _NAME_FIELDS == frozenset(_EXPECTED_NAME_FIELDS)


@pytest.mark.parametrize("field", _EXPECTED_NAME_FIELDS)
def test_every_name_field_is_load_bearing(field):
    """Four of five were individually deletable with the suite green."""
    assert scan_payload({"members": [{field: "Invented Person"}]},
                        recorded=True).name_fields == 1, field


@pytest.mark.parametrize(
    "payload,counted",
    [
        ({"topics": [{"messages": [{"content": "note from Invented Person"}]}]}, 1),
        ({"settings": {"name": _LEAGUE_NAME}}, 1),
        ({"teams": [{"name": "Invented Squad"}]}, 1),
        ({_LEAGUE_NAME: {"teams": []}}, 1),          # a name used as a dict key
        ({"players": [{"firstName": "Public"}]}, 0),  # public NFL data
        ({"athletes": [{"firstName": "Public"}]}, 0),
    ],
)
def test_the_name_scope_is_two_sided(payload, counted):
    assert scan_payload(payload, recorded=True).name_fields == counted


@pytest.mark.parametrize("marker", _PLAYER_PATH_MARKERS)
def test_every_player_marker_is_load_bearing(marker):
    payload = {f"{marker}s": [{"firstName": "Public", "lastName": "Player"}]}
    assert scan_payload(payload, recorded=True).name_fields == 0, marker


def test_a_name_used_as_a_key_is_counted_but_never_printed():
    """The `<key>` guard it replaced was unreachable -- `<key>` is appended
    before the field match -- and its side effect was that a name used as a dict
    key was never counted at all."""
    result = scan_payload({_LEAGUE_NAME: {"swid": str(uuid.uuid4()).upper()}},
                          recorded=True)
    assert result.name_fields == 1
    assert all(_LEAGUE_NAME not in finding.describe() for finding in result.findings)


# ------------------------------------------------ URLs anywhere, not offset 0


@pytest.mark.parametrize(
    "template",
    ["{u}", "logo: {u}", "url({u})", "[logo]({u})", " {u}", "see {u} for the image"],
)
def test_a_genuine_url_is_clean_wherever_it_sits_in_the_value(template):
    """The exemption was `startswith`, so one leading space or a `logo: ` label
    turned a real ESPN logo URL into a finding."""
    url = "https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/phi.png"
    assert scan_payload({"t": template.format(u=url)}).findings == []


def test_a_credential_beside_a_url_in_one_value_is_still_caught():
    """URLs are split out and the remainder scored whole; dropping the remainder
    left everything outside the URL unscanned."""
    secret = "".join("ghijklmnopqrstuvwxyz"[i % 20] for i in range(120))
    url = "https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/phi.png"
    assert scan_payload({"t": f"{secret} see {url}"}).findings
    assert scan_payload({"t": f"see {url} then {secret}"}).findings


def test_an_opaque_token_containing_a_double_slash_is_not_split_into_pieces():
    """Segmenting on any `//` would let a credential break into sub-floor parts.
    The URL match requires a word boundary, so an internal `//` is not one."""
    token = "AbCd12//EfGh34ijKlMnOpQrStUvWxYz01234567890abcXYZ"
    assert scan_payload({"t": token}).findings


# ------------------------------------------------------- structural hazards


def test_a_directory_named_like_a_fixture_does_not_crash_the_gate(tmp_path):
    _baseline(tmp_path)
    (tmp_path / "weird.json").mkdir()
    content, manifest = _gate(tmp_path)
    assert content == "GREEN" and manifest == "GREEN"


def test_a_symlinked_directory_is_refused_not_silently_skipped(tmp_path, monkeypatch):
    """`rglob` does not descend a symlinked directory, so the gate reported
    having examined a tree it never entered."""
    _baseline(tmp_path)
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    (tmp_path / "linked").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ManifestError) as caught:
        assert_manifest_complete(tmp_path)
    assert "symlink" in str(caught.value)


def test_the_content_digest_is_over_the_file_not_the_parse(tmp_path):
    target = _planted(tmp_path, "a.json", {"x": 1})
    before = _content_digest(target)
    target.write_text(json.dumps({"x": 1}, indent=2), encoding="utf-8")
    assert _content_digest(target) != before
