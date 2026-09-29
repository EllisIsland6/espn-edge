"""Phase 31 — fixture provenance: prove content is synthetic, not merely shaped like it.

Why this module asserts on content rather than shape
----------------------------------------------------
An earlier Phase 31 survey flagged 18 "SWID-shaped GUID strings" in the recorded
fixtures as a live member-data exposure. They were not. Every one was a
low-entropy placeholder that had been scrubbed at capture time, and the value the
test suite itself documents as a fixed fake was among them. The scan matched a
*pattern* and reported an exposure without ever establishing the thing it claimed.

That is the same defect class as the vacuous gates recorded in Phase 30 (E30.58,
E30.60): a check that fires without proving its own claim. So this scanner is
built the other way round. A shape match is only a *candidate*. A candidate is a
finding only if its content also carries enough entropy to be a real identifier.
The accepted contract requires this to be proven by a two-sided control: a
real-entropy identifier must fail the build, and a low-entropy synthetic
placeholder of identical shape must not.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Shape candidates. Matching one of these proves nothing on its own.
_GUID = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9%_\-+/=.:]{32,}")

# Measured, not assumed. Over 1,000,000 random v4 GUIDs the minimum observed
# Shannon entropy over the 32-char hex body is 2.69 bits (mean 3.61; 4.0 is the
# unreachable ceiling, not the average). Generated placeholders top out at 1.18.
# The floor sits at 2.5: 0.19 bits of margin on the real side, 1.3 on the
# synthetic side. That margin is real but narrower than an earlier version of
# this comment claimed, and it narrows slowly as more captures are sampled.
#
# A `_MIN_DISTINCT_HEX` conjunct was removed after review measured it
# non-load-bearing: generated placeholders reach 7 distinct hex characters,
# above the floor of 6, so it never separated the two sides. One condition that
# works is better than two where one is decorative.
_MIN_IDENTIFIER_ENTROPY_BITS = 2.5

# Long tokens are scored over the whole candidate, not a hex projection. Review
# found a 120-char non-hex secret whose hex body stripped to empty and was
# therefore waved through as a candidate-with-no-finding.
_MIN_LONG_TOKEN_CHARS = 32

# Path segments of this many digits are redacted; a real ESPN league id is 8.
_MIN_REDACTED_DIGITS = 6

# Member- and league-identifying fields, detected structurally because no
# entropy or regex rule can recognise a human name.
#
# SCOPE, stated rather than assumed: this applies only to RECORDED captures.
# A first implementation flagged every name field in every fixture and produced
# 36 findings, nearly all false: NFL player names in the ADP fixture are public
# data, not member PII, and synthetic fixtures carry invented names by
# construction. Flagging those would have blocked a legitimate build -- the same
# over-reach made once already in this phase's unit 2. The risk this rule exists
# for is member and league names inside recorded ESPN captures, so that is
# exactly what it covers. Synthetic fixtures are covered instead by the factory's
# generation guarantee: no recorded capture is an input to generation.
_NAME_FIELDS = frozenset(
    # `abbrev` is the third member of the operator-chosen team triple alongside
    # `location` and `nickname`, and is very commonly owner initials or a
    # surname. Review found it outside the scope the comment above claims.
    {"displayname", "firstname", "lastname", "nickname", "location", "abbrev"}
)
# `name` only where it identifies a league or a member's team, never a player.
# `?view=mCommunication` returns member-authored league chat: free text that
# routinely carries member names, and which no entropy rule can recognise.
_SCOPED_NAME_PATHS = (
    (".settings.name", None),
    (".name", ".teams"),
    (".content", ".messages"),
    (".content", ".topics"),
)
# Public NFL player names are out of scope. ESPN serves them under `player*` on
# some endpoints and `athletes[]` on others; the substring test covered only the
# first, so an `athletes` capture forced a declaration over public data.
_PLAYER_PATH_MARKERS = ("player", "athlete")

# A URL matches the long-token rule as ONE token and a real ESPN logo URL scores
# 3.75 bits -- above the floor -- so URL values must be handled or they are false
# positives. They are handled by segmentation, not by placement; see _url_segments.
_URL_PREFIXES = ("http://", "https://", "//")

# A URL anywhere in the value, not only at offset 0. `startswith` meant one
# leading space, a `logo: ` label or markdown brackets turned a genuine ESPN
# logo URL into a finding. The leading-boundary lookbehind is what keeps this
# from matching the `//` inside an opaque token and splitting a real credential
# into sub-floor pieces.
_URL_IN_TEXT = re.compile(
    r"""(?:(?<=^)|(?<=[\s(\[<"']))(?:https?://|//)[^\s)\]>"']+"""
)

_URL_SEPARATORS = re.compile(r"[/?#&=;]+")


def _url_segments(value: str) -> list[str]:
    """Split a URL-shaped value into its structural segments.

    Placement was the wrong axis. Skipping the whole value made "//" + secret a
    complete evasion; scanning only query and fragment then made a secret in a
    PATH segment invisible. A URL's structural parts are short words and ids
    while a credential is one long opaque run, so every segment is scored
    independently and no part of the URL is privileged.
    """
    return [part for part in _URL_SEPARATORS.split(value) if part]


def _long_token_sources(value: str) -> list[str]:
    """Split URLs out of a value; scan the remainder whole.

    Scanning the whole value flags genuine URLs; scanning only segments would
    let an opaque credential containing "//" split into sub-floor pieces. So
    URLs are extracted at a word boundary and segmented, and everything that is
    not part of a URL is still scored as one run.
    """
    urls = _URL_IN_TEXT.findall(value)
    if not urls:
        return [value]
    remainder = _URL_IN_TEXT.sub(" ", value)
    return [remainder, *(seg for url in urls for seg in _url_segments(url))]


def shannon_entropy_bits(value: str) -> float:
    """Bits of entropy per character. Empty string is zero, not an error."""
    if not value:
        return 0.0
    counts = Counter(value)
    total = len(value)
    return -sum(
        (n / total) * math.log2(n / total) for n in counts.values()
    )


def _hex_body(candidate: str) -> str:
    return re.sub(r"[^0-9A-Fa-f]", "", candidate)


def looks_like_real_identifier(candidate: str, kind: str = "guid") -> bool:
    """True only when content, not shape, indicates a real identifier.

    A scrubbed placeholder keeps the shape but collapses the content. GUIDs are
    scored over their hex body; long tokens are scored over the whole candidate,
    because a hex projection of a non-hex secret starves to nothing and would
    otherwise pass.
    """
    # Long tokens are scored over the whole candidate. An earlier version scored
    # every kind over a hex projection, so a 120-char secret drawn from non-hex
    # letters starved to an empty body and was waved through as a candidate with
    # no finding. A guard for that empty-body case was then added and later
    # removed as dead: a GUID match is hex by construction, so `body` is never
    # empty for the only kind that uses the projection.
    body = _hex_body(candidate) if kind == "guid" else candidate
    return bool(body) and shannon_entropy_bits(body) >= _MIN_IDENTIFIER_ENTROPY_BITS


def _redact(segment: str) -> str:
    """Path segments can themselves be identifiers when a payload is id-keyed.

    Review found `json_path` echoing a GUID-valued dict key verbatim into the
    build log through ProvenanceError: the one identifier class the scanner
    could not detect was the one it printed.
    """
    if (
        _GUID.fullmatch(segment)
        or len(segment) >= _MIN_LONG_TOKEN_CHARS
        # Numeric keys are the common ESPN shape and a real league id is exactly
        # this. Redacting only GUID keys left that class printing verbatim.
        #
        # `segment.isdigit()` required the WHOLE key to be digits, so one
        # separator reopened it: `<leagueId>:<season>` and `<season>_<leagueId>`
        # are ordinary raw-cache key shapes and both printed verbatim. Any digit
        # run of identifier length anywhere in the key is enough.
        or re.search(rf"\d{{{_MIN_REDACTED_DIGITS},}}", segment)
        # A key containing whitespace is human-authored text, not a structural
        # ESPN key -- a league name used as a dict key is exactly this shape.
        or any(character.isspace() for character in segment)
        # A base64 identifier is 22 characters, under the long-token floor, so it
        # printed verbatim while being undetectable as a value. Structural ESPN
        # keys of that length are alphabetic camelCase; identifiers are not.
        or (len(segment) >= 16 and not segment.isalpha())
    ):
        return "<redacted>"
    return segment


@dataclass(frozen=True)
class Finding:
    path: str
    kind: str
    json_path: str
    entropy_bits: float
    distinct_chars: int

    def describe(self) -> str:
        """Secret-free. Never includes the offending value."""
        return (
            f"{self.path}: {self.kind} at {self.json_path} "
            f"(entropy {self.entropy_bits:.2f} bits, {self.distinct_chars} distinct chars)"
        )


@dataclass(repr=False)
class ScanResult:
    """Holds the undecidable VALUES, so unlike `Finding` it is not safe to print.

    `Finding` is value-free by construction and a test checks every attribute.
    Its sibling carried raw member and league names and had the dataclass default
    `__repr__`, so any failing assertion whose receiver is a ScanResult -- pytest
    rewrites and prints the receiver with no flags -- would have put those names
    into a public CI log. That is the gate moving the leak, not stopping it.
    """

    scanned: int = 0
    candidates: int = 0
    name_fields: int = 0
    id_fields: int = 0
    undecidable: list[tuple[str, str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def undecidable_digest(self) -> str:
        """Digest over content no rule can classify, so a declaration binds.

        Counts alone let a declaration keep vouching after every name value was
        replaced at the same paths: the count matched, the gate stayed green.
        """
        # json.dumps rather than a "path=value" join joined by "|": that join was
        # ambiguous, so one value containing "|" or "=" collided with a different
        # pair set entirely. Full digest, not truncated.
        material = json.dumps(sorted(self.undecidable), separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()

    def __repr__(self) -> str:
        return (
            f"ScanResult(scanned={self.scanned}, candidates={self.candidates}, "
            f"name_fields={self.name_fields}, id_fields={self.id_fields}, "
            f"undecidable={len(self.undecidable)} values digest "
            f"{self.undecidable_digest[:12]}..., findings={len(self.findings)})"
        )

    @property
    def clean(self) -> bool:
        return not self.findings


# Numbers below this are structural (team index, week, slot); at or above it
# they are plausibly real ESPN league/member identifiers.
#
# This was 1_000_000 while `_MIN_REDACTED_DIGITS` was 6: the module simultaneously
# held that a six-digit number is identifier-shaped enough to redact from a log
# and that it is structural noise not worth counting. Legacy ESPN league ids are
# five and six digits, so they were invisible to the count AND absent from the
# digest. One constant now derives from the other so they cannot disagree again.
_ID_PLAUSIBLE_MAGNITUDE = 10 ** (_MIN_REDACTED_DIGITS - 1)


def _walk(node, prefix: str = ""):
    """Yield every scannable scalar, including dict keys and large integers.

    Review found `_walk` yielding string values only, so a GUID used as a dict
    key and a numeric league id were both invisible. ESPN payloads key by id
    routinely, so neither was hypothetical.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{_redact(str(key))}"
            if isinstance(key, str):
                yield f"{path}<key>", key
            yield from _walk(value, path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{prefix}[{index}]")
    elif isinstance(node, str):
        yield prefix, node
    elif isinstance(node, bool):
        return
    elif isinstance(node, (int, float)) and abs(node) >= _ID_PLAUSIBLE_MAGNITUDE:
        # Floats too: `{"leagueId": 17739342.0}` is what a round-trip through a
        # spreadsheet or a JS client produces, and it was skipped entirely.
        yield f"{prefix}<int>", repr(node)


def scan_payload(payload, path: str = "<payload>", *, recorded: bool = False) -> ScanResult:
    """Scan a parsed JSON payload for real identifiers.

    `recorded` enables member/league name-field detection, which applies only to
    captures recorded from live ESPN. See `_NAME_FIELDS` for why.
    """
    result = ScanResult()
    name_values: list[tuple[str, str]] = []
    for json_path, value in _walk(payload):
        result.scanned += 1
        if json_path.endswith("<int>"):
            # Numeric ids are undecidable by content for exactly the reason names
            # are: a real ESPN league id and a synthetic one are both just digits.
            # An earlier version yielded them to the pattern matchers, where they
            # could never match, producing a branch that read like detection and
            # detected nothing -- the same defect as the `_MIN_DISTINCT_HEX`
            # conjunct removed last round. They are counted and declared instead.
            if recorded:
                result.id_fields += 1
                result.undecidable.append((json_path, value))
            continue
        lowered_path = json_path.lower()
        field = json_path.rsplit(".", 1)[-1].split("[")[0].lower()
        is_name_field = field in _NAME_FIELDS or any(
            lowered_path.endswith(suffix) and (within is None or within in lowered_path)
            for suffix, within in _SCOPED_NAME_PATHS
        )
        # A dict key containing whitespace is human-authored text, and ESPN
        # payloads are keyed by identifier routinely: `{"<league name>": {...}}`
        # is a real shape. The previous `not json_path.endswith("<key>")` guard
        # was unreachable -- `<key>` is appended before the field match, so no
        # key yield could ever set `is_name_field` -- and its side effect was
        # that a name used as a key was never counted at all.
        is_name_key = json_path.endswith("<key>") and any(
            character.isspace() for character in value
        )
        if (
            recorded
            and (is_name_field or is_name_key)
            and not any(marker in lowered_path for marker in _PLAYER_PATH_MARKERS)
            and value.strip()
        ):
            name_values.append((json_path, value))
        for kind, pattern in (("guid", _GUID), ("long-token", _LONG_TOKEN)):
            if kind == "long-token":
                sources = _long_token_sources(value)
            else:
                sources = [value]
            for candidate in [c for src in sources for c in pattern.findall(src)]:
                # A GUID is 36-38 chars and so also matches the long-token rule.
                # Counting it under both kinds would double every candidate and
                # report two findings for one identifier.
                if kind == "long-token" and _GUID.fullmatch(candidate.strip("{}")):
                    continue
                result.candidates += 1
                if looks_like_real_identifier(candidate, kind):
                    body = _hex_body(candidate) if kind == "guid" else candidate
                    result.findings.append(
                        Finding(
                            path=path,
                            kind=kind,
                            json_path=json_path or "<root>",
                            entropy_bits=shannon_entropy_bits(body or candidate),
                            distinct_chars=len(set((body or candidate).lower())),
                        )
                    )

    # Name fields are COUNTED, never auto-classified.
    #
    # Four content rules were tried and each failed differently: a synthetic
    # prefix allowlist (brittle), non-emptiness (flagged nine identical scrubbed
    # placeholders), entropy (a scrubbed "Test League" scores like a real league
    # name), and uniqueness (scrubbed placeholders are distinct AND patterned).
    # A scrubbed name and a real name are not reliably separable by content, so
    # any rule claiming to do it is wrong in one direction or the other.
    #
    # So this reports a count plus a digest, and the manifest must carry an
    # explicit human declaration. A gate that forces a decision it cannot make is
    # honest; a gate that guesses and calls it detection is the pattern this
    # phase exists to remove.
    result.candidates += len(name_values)
    result.name_fields += len(name_values)
    result.undecidable.extend(name_values)
    return result


def _payload_for_scan(path: Path):
    """Parse a fixture, or fall back to scanning its text line by line.

    Both gates were bound to `*.json`, and the separate any-extension sweep fired
    only on a `real_` prefix, so the intersection was empty for anything that was
    neither: `espn_capture.har`, `league.json.bak`, `members.ndjson`, and
    `README.md` -- the file this phase already found a real league name in -- all
    sat outside every gate and reported green. A `.har` is the single most likely
    artifact of "record a live capture to debug this", so nothing may be exempt
    by extension. Non-JSON text is keyed by line number: no value is skipped, and
    the location is still useful to whoever has to fix it.
    """
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return {
            f"line-{number}": line
            for number, line in enumerate(raw.splitlines(), 1)
            if line.strip()
        }


def scan_file(path: Path, label: str | None = None, *, recorded: bool = False) -> ScanResult:
    """Scan one file. `label` is what findings name; it defaults to the basename.

    `scan_directory` passes the path relative to the sweep root, because a
    finding that names `real_league_2025.json` when the offending file is
    `recorded/real_league_2025.json` sends an operator to the wrong file.

    `recorded` is now passed in by the caller rather than derived from the
    filename here; see `_declared_recorded`.
    """
    return scan_payload(_payload_for_scan(path), label or path.name, recorded=recorded)


def _fixture_files(directory: Path, pattern: str = "*") -> list[Path]:
    """Every file under the tree, any extension, symlinked directories refused.

    `rglob` silently does not descend a symlinked directory, so the gate would
    report having examined a tree it never entered. It refuses rather than skips.
    """
    for candidate in sorted(directory.rglob("*")):
        if candidate.is_symlink() and candidate.is_dir():
            raise ManifestError(
                "symlinked directory in the fixture tree cannot be swept: "
                f"{candidate.relative_to(directory).as_posix()}"
            )
    return sorted(p for p in directory.rglob(pattern) if p.is_file())


def scan_directory(directory: Path, pattern: str = "*") -> ScanResult:
    combined = ScanResult()
    declared = _declared_entries(directory)
    for path in _fixture_files(directory, pattern):
        label = path.relative_to(directory).as_posix()
        if _is_manifest(path, directory):
            one = scan_payload(
                _manifest_scan_projection(
                    json.loads(path.read_text(encoding="utf-8"))
                ),
                label,
                # The manifest's fixture keys are paths a human chose and its
                # declaration fields are operator-authored. An earlier version
                # scanned it with the default `recorded=False` while a comment
                # claimed a real league name in it would be caught; the name rule
                # was structurally off, so the comment described a control that
                # did not exist.
                recorded=True,
            )
        else:
            one = scan_file(path, label, recorded=_declared_recorded(path, declared, directory))
        combined.scanned += one.scanned
        combined.candidates += one.candidates
        combined.name_fields += one.name_fields
        combined.id_fields += one.id_fields
        combined.undecidable.extend(one.undecidable)
        combined.findings.extend(one.findings)
    return combined


class ProvenanceError(RuntimeError):
    """Raised when a fixture carries real identifier content."""


def assert_clean(directory: Path, pattern: str = "*") -> ScanResult:
    """Fail the build on any real identifier. Message names paths, never values."""
    result = scan_directory(directory, pattern)
    if not result.clean:
        joined = "; ".join(f.describe() for f in result.findings)
        raise ProvenanceError(f"real identifier content in fixtures: {joined}")
    return result


# ------------------------------------------------------------------ manifest

MANIFEST_NAME = "provenance.json"


class ManifestError(RuntimeError):
    """Raised when a fixture is present without a declared provenance."""


def _is_manifest(path: Path, root: Path) -> bool:
    """The manifest is one exact path, never a basename anywhere in the tree.

    Both exclusion sites compared `path.name`, so a capture planted at
    `recorded/provenance.json` was excluded from the sweep AND from the
    declaration requirement -- the same basename escape hatch review already
    found once on the fixture side, still open on the manifest side.
    """
    return path.relative_to(root).as_posix() == MANIFEST_NAME


# Both digests are full sha256 values, so they read as high-entropy long tokens
# and the scanner flagged its own record. They are dropped from the manifest's
# content scan -- only the manifest's, only these two fields -- because each is
# pinned by a STRICTER gate than entropy: `assert_manifest_complete` recomputes
# both from the fixture and raises on any disagreement, so nothing can hide
# there across a single build, and a test asserts that rather than claiming it.
#
# Everything else in the manifest is scanned, and scanned with `recorded=True`,
# because its fixture keys and declarations are operator-authored. An earlier
# version of this comment claimed that a real league name in a free-text
# `reviewed_note` would be caught. It would not have been: no entropy rule can
# recognise a name, and the manifest was scanned with the name rule off. There
# is no free-text field now -- `_MANIFEST_ENTRY_KEYS` is exhaustive -- which is a
# control, where the old comment was a wish.
_MANIFEST_DERIVED_FIELDS = frozenset({"undecidable_digest", "content_digest"})


# A manifest entry may carry these keys and nothing else. An unknown key is a
# free-text channel into the one file in the tree a human types by hand, which is
# where a real league name would land -- and no entropy rule can recognise a
# name, so the only workable control is to have no free-text field at all. The
# previous `reviewed_note` was exactly that channel and is gone.
_MANIFEST_ENTRY_KEYS = frozenset(
    {
        "origin",
        "scrubbed",
        "shape_candidates",
        "real_identifier_findings",
        "name_fields",
        "id_fields",
        "undecidable_digest",
        "content_digest",
        "names_reviewed",
        "ids_reviewed",
    }
)
_ORIGINS = ("recorded", "synthetic")
_UNDECLARED = "undeclared"


def _declared_entries(directory: Path) -> dict:
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ManifestError("provenance manifest is not an object")
    fixtures = loaded.get("fixtures", {})
    if not isinstance(fixtures, dict):
        raise ManifestError("provenance manifest `fixtures` is not an object")
    return fixtures


def _declared_recorded(path: Path, declared: dict, directory: Path) -> bool:
    """Whether to scan this file as a recorded capture.

    This was `path.name.lower().startswith("real_")`, so the entire member-name
    and id declaration control was switched on by a filename convention. A
    capture saved under any other name was scanned as synthetic: zero name
    fields, zero id fields, and `build_manifest` then auto-declared it reviewed
    because its own counts were zero. The human was asked exactly where the
    scanner had already seen the risk and skipped exactly where it had not.

    Origin is a human declaration now. The naming convention survives only as a
    floor, and anything not yet declared is scanned as recorded, because the
    strict side is the safe side for a file nobody has classified.
    """
    entry = declared.get(path.relative_to(directory).as_posix())
    if isinstance(entry, dict) and entry.get("origin") in _ORIGINS:
        return entry["origin"] == "recorded"
    return True


def _content_digest(path: Path) -> str:
    """Digest over the file's bytes.

    `undecidable_digest` binds a declaration to the subset of content the scanner
    classified, which is not the file. Review re-recorded a capture with a
    different real league id -- a JSON string id matches neither the GUID rule
    nor the 32-character token rule, so it is in no class -- and the undecidable
    digest was byte-identical, the carry-forward held, and both gates stayed
    green. A declaration must vouch for bytes.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_scan_projection(payload):
    fixtures = payload.get("fixtures") if isinstance(payload, dict) else None
    if not isinstance(fixtures, dict):
        return payload
    projected = dict(payload)
    projected["fixtures"] = {
        name: (
            {k: v for k, v in entry.items() if k not in _MANIFEST_DERIVED_FIELDS}
            if isinstance(entry, dict)
            else entry
        )
        for name, entry in fixtures.items()
    }
    return projected


def build_manifest(directory: Path, pattern: str = "*") -> dict:
    """Declare origin and scrub status for every fixture, keyed by relative path.

    A prior human declaration is carried forward ONLY when the undecidable
    content digest is unchanged. Regenerating used to reset every declaration to
    false, which pushes the next operator to flip it back to unblock the build
    without re-reviewing -- the precise risk the declaration exists to manage.
    """
    prior = _declared_entries(directory)
    entries = {}
    for path in _fixture_files(directory, pattern):
        if _is_manifest(path, directory):
            continue
        key = path.relative_to(directory).as_posix()
        previous = prior.get(key) if isinstance(prior.get(key), dict) else None
        content = _content_digest(path)
        unchanged = bool(previous) and previous.get("content_digest") == content
        result = scan_file(
            path, recorded=_declared_recorded(path, prior, directory)
        )
        # A file the naming convention marks is recorded whatever anyone declares.
        # Everything else that is genuinely new is `undeclared`, which the gate
        # refuses: a new file is exactly the moment a human must classify it, and
        # auto-declaring `synthetic` there was how a live capture passed green.
        if path.name.lower().startswith("real_"):
            origin = "recorded"
        elif unchanged and previous.get("origin") in _ORIGINS:
            origin = previous["origin"]
        else:
            origin = _UNDECLARED
        entry = {
            "origin": origin,
            "scrubbed": result.clean,
            "shape_candidates": result.candidates,
            "real_identifier_findings": len(result.findings),
            "name_fields": result.name_fields,
            "id_fields": result.id_fields,
            # Digest over the undecidable content, so a declaration binds to what
            # was actually reviewed. Without it a declaration keeps vouching for
            # bytes that changed the next day.
            "undecidable_digest": result.undecidable_digest,
            # Digest over the whole file, because the line above covers only what
            # the scanner could classify. See `_content_digest`.
            "content_digest": content,
            # Human declarations. The scanner cannot decide these; see scan_payload.
            "names_reviewed": False,
            "ids_reviewed": False,
        }
        if unchanged:
            for carried in ("names_reviewed", "ids_reviewed"):
                if carried in previous:
                    entry[carried] = previous[carried]
        entries[key] = entry
    return {"version": 1, "fixtures": entries}


def assert_manifest_complete(directory: Path, pattern: str = "*") -> dict:
    """Every fixture must be declared, and every recorded capture scrubbed.

    This closes the gap the unit-3 survey found: `real_*.json` was tracked with
    no ignore rule, so the naming convention implied a local-only guarantee the
    repository did not enforce. A future unscrubbed capture would have been
    committed silently. Now it fails the build.
    """
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.exists():
        raise ManifestError(f"no provenance manifest at {manifest_path.name}")
    declared = _declared_entries(directory)

    # Every file, any extension. See `_payload_for_scan` for why nothing is
    # exempt by suffix.
    present = {
        p.relative_to(directory).as_posix()
        for p in _fixture_files(directory, pattern)
        if not _is_manifest(p, directory)
    }
    undeclared = sorted(present - set(declared))
    if undeclared:
        raise ManifestError(f"fixtures present without a manifest entry: {undeclared}")
    stale = sorted(set(declared) - present)
    if stale:
        raise ManifestError(f"manifest declares missing fixtures: {stale}")

    # Re-scan rather than trust the declaration. An earlier version read the
    # `scrubbed` flag out of the manifest, so a hand-written entry claiming
    # `scrubbed: true` over a file full of live identifiers passed green while
    # this docstring claimed the build would fail. That is a gate asserting a
    # conclusion it never established -- the exact pattern this phase exists to
    # remove -- so the flag is now evidence to be checked, not evidence itself.
    disagreed: list[str] = []
    unscrubbed: list[str] = []
    unreviewed: list[str] = []
    mislabelled: list[str] = []
    undeclared_origin: list[str] = []
    for name, entry in sorted(declared.items()):
        if not isinstance(entry, dict):
            raise ManifestError(f"manifest entry is not an object: {name}")
        unknown = sorted(set(entry) - _MANIFEST_ENTRY_KEYS)
        if unknown:
            raise ManifestError(
                f"manifest entry {name} carries unknown fields: {unknown}"
            )
        origin = entry.get("origin")
        if origin not in _ORIGINS:
            undeclared_origin.append(name)
            continue
        if Path(name).name.lower().startswith("real_") and origin != "recorded":
            mislabelled.append(name)
            continue
        rescan = scan_file(directory / name, recorded=origin == "recorded")
        if entry.get("content_digest") != _content_digest(directory / name):
            disagreed.append(name)
        if not rescan.clean:
            unscrubbed.append(name)
        if bool(entry.get("scrubbed")) is not rescan.clean:
            disagreed.append(name)
        if entry.get("real_identifier_findings") != len(rescan.findings):
            disagreed.append(name)
        if entry.get("name_fields") != rescan.name_fields:
            disagreed.append(name)
        if entry.get("id_fields") != rescan.id_fields:
            disagreed.append(name)
        if entry.get("undecidable_digest") != rescan.undecidable_digest:
            disagreed.append(name)
        # Required for every recorded capture, not only where the scanner
        # happened to count something. Gating on the counts meant the human was
        # asked exactly where the scanner already saw the risk and skipped
        # wherever its scope missed -- so it could never backstop the scanner's
        # blind spots, which is the whole reason a human declaration exists.
        if origin == "recorded":
            if entry.get("names_reviewed") is not True:
                unreviewed.append(f"{name} ({rescan.name_fields} name fields)")
            if entry.get("ids_reviewed") is not True:
                unreviewed.append(f"{name} ({rescan.id_fields} id fields)")
    if undeclared_origin:
        raise ManifestError(
            "fixtures carry no origin declaration (write \"recorded\" or "
            f"\"synthetic\"): {sorted(undeclared_origin)}"
        )
    if mislabelled:
        raise ManifestError(
            f"real_* captures declared as synthetic: {sorted(mislabelled)}"
        )
    if disagreed:
        raise ManifestError(
            f"manifest disagrees with a rescan of: {sorted(set(disagreed))}"
        )
    if unscrubbed:
        raise ManifestError(f"recorded captures are not scrubbed: {unscrubbed}")
    if unreviewed:
        raise ManifestError(
            "captures carry name fields with no reviewed declaration: "
            f"{sorted(unreviewed)}"
        )

    # The separate `real_*` any-extension sweep that used to live here is gone:
    # `present` now covers every file at any depth with any extension, keyed on
    # the relative path with no basename fallback, so the sweep was a strictly
    # weaker duplicate of the declaration requirement above.
    return declared
