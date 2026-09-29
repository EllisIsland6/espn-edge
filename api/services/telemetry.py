"""Provider telemetry for Phase 32 — the seams, built before the contract.

WHY THIS EXISTS BEFORE A SIGNED CONTRACT
----------------------------------------
Three rounds of contract revision were rejected and every surviving disagreement
was a question about code. So the seams were written first, they are probed by
`tests/test_telemetry_seams.py`, and the contract will be written against what
the probes report.

Nothing here is on by default. `Recorder.disabled()` is the module's resting
state and the provider carries no telemetry until a recorder is handed to it.

WHAT MAY REACH A RECORD, AND THE FOUR TIMES THIS WAS WRONG
-----------------------------------------------------------
Closed enums, ints and bools. The history is the specification, because each
fix was defeated by the next round:

* Round 4 -- the claim was "there is nowhere to put one", resting on the absence
  of a `str` field. A frozen dataclass validates nothing, and the probe guarding
  the claim passed on a `Record` holding a league URL, because under PEP 563 it
  compared annotation *source text*.
* Round 5 -- the replacement validated the TABLE (`for name in _FIELD_TYPES`)
  rather than the INSTANCE, so a subclass's extra field and a stray attribute
  were invisible; and the enum check was `isinstance`, which honours a
  `__class__` property.
* Round 6 -- the round-5 fixes closed the *spelling* of three vectors rather
  than the vectors. `MappingProxyType` did not make the rule table immutable:
  `gc.get_referents(proxy)[0]` returns the backing dict, and one write to it
  disarmed both the validation and the value-free `__repr__`, because both read
  the same table. `__setstate__` re-validated *after* writing, so it was itself
  a mutation primitive against `frozen=True` on a record already filed. And
  `type(x) is Shape` stops an impostor but not a *genuine* member whose
  `_name_` and `_value_` have been rewritten -- enum members are ordinary
  mutable objects, and both reprs printed the payload through `.name`.

The pattern across all three round-6 findings: a fix that changes which NAME
reaches a thing is defeated by another name. The two round-5 fixes that held --
`slots=True` and `__init_subclass__` -- changed what the object IS. So:

* the rule table is a **tuple of pairs**, not a mapping. A tuple has no mutable
  backing object for `gc.get_referents` to hand out.
* enum members are never rendered through `.name` or `.value`. Every printed or
  counted token comes from `_TOKENS`, an immutable tuple captured at import and
  looked up by **identity**, so rewriting a member's fields changes nothing.
  The counters hold those tokens, not the members, so the structures most
  likely to be exported carry no live object at all.
* `__setstate__` validates the incoming state **before** it writes anything.

RESIDUALS -- what this module does NOT close
--------------------------------------------
Stated rather than claimed closed, because three rounds of claiming closure
produced three rounds of findings:

* **`Record.shape` and `Record.outcome` hold live enum members.** Anything that
  reads `record.shape.value` instead of `_token(record.shape)` can still carry a
  rewritten payload. This module never does — a probe AST-scans it and fails on
  any `.name`/`.value` access — and its own serialisation no longer does either:
  `__getstate__` emits tokens. The obligation that remains is on whatever reads
  the field directly.
* **`object.__setattr__`, `Record.__post_init__ = ...`,
  `RateGate.__setattr__ = object.__setattr__`, and writing
  `shared_gate.__closure__[0].cell_contents` are all reachable from any
  in-process code.** Python has no way to prevent any of them. Validation is a
  control against *mistakes*, not against code that has already decided to
  defeat it. Earlier text claimed the gate left only "visibly irregular" paths;
  that was wrong twice over — the list above was incomplete, and `del
  gate._sealed` was entirely ordinary syntax until `__delattr__` was added.
* **The range bounds are not a privacy control, and they do not refuse every
  identifier.** They are sized so a nine- or ten-digit id is outside every one
  of them (64 MiB for a body, ten minutes for a latency), which the earlier
  `1 << 40` / `1 << 32` were not. But 9,998,887 bytes is a plausible body size,
  so a seven-digit league id in `wire_bytes` or `decoded_bytes` passes — and it
  is asserted that it passes, in `test_what_the_bounds_do_and_do_not_catch`,
  rather than left for a reviewer to discover. No range check distinguishes a
  measurement from an identifier. The precondition stays on the caller: an int
  field carries a measurement.
* **Every identity table here is a rebindable module global.** `_TOKENS`,
  `_FIELD_RULES`, `_FIELD_BOUNDS`, `_RULE_TOKENS` and `_NAMEABLE_TYPES` are
  tuples and frozensets, so nothing can be inserted into one -- but
  `telemetry._FIELD_BOUNDS = ()` is a single statement and every bound is gone,
  and `telemetry._NAMEABLE_TYPES |= {...}` puts an arbitrary class name back in
  a message. `shared_gate` was moved into a closure precisely because a module
  global "could simply be rebound"; the tables were not, and the suite itself
  uses this vector when it monkeypatches `_TOKENS`. Listed rather than fixed:
  the tables must stay readable by the tests that pin them.
* **`Recorder` does not catch `BaseException`, by design, and that is a channel.**
  A mapping whose `keys()` raises `BaseException` propagates its message into the
  caller, and neither `attempts` nor `dropped` increments. The alternative --
  swallowing `KeyboardInterrupt` -- is worse and is pinned against by
  `test_a_keyboard_interrupt_is_not_swallowed`. `Recorder`'s "never raises into
  the caller" means never for an `Exception`.
* **`_drop` itself is unguarded**, so anything that makes it raise -- `counters`
  rebound to something hostile, per the first bullet -- raises from inside the
  handler that exists not to. It touches only ints and dicts under a lock, and a
  guard around it would be a branch no test could reach.
* **A `Counters` round-tripped through JSON prints every key as invalid.** The
  repr tests token membership by identity, and a key rebuilt from text is equal
  but not identical. The structure is safe to serialise; what comes back is not
  the same object, and an importer must re-key through `_token`.
* **`dataclasses.asdict(record)` still emits live enum members.** `__getstate__`
  and pickle no longer do; `asdict` reads the fields directly and this module
  cannot intercept it. An exporter must use `__getstate__` or `_token`.

`Shape` is chosen by the caller that built the URL, never derived by matching an
assembled one: the five ESPN families are not disjoint under prefix matching
(`season_url` is a strict prefix of three others) and the pre-2018 form puts the
league id in the trailing segment, so a matcher is both ambiguous and one bug
away from recording an identifier.
"""

from __future__ import annotations

import pathlib
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum

# One request per second, SPEC 2.10.
GATE_INTERVAL_SECONDS = 1.0

_SAFE_TYPE_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,39}\Z")

# A closed allowlist, because a 40-character identifier-shaped name is ample room
# for a league id and the filter alone only blocked the sloppiest payloads. The
# message's job is to name the FIELD, which is trustworthy; the type is a nicety
# not worth a channel.
_NAMEABLE_TYPES = frozenset(
    {"int", "bool", "str", "bytes", "float", "NoneType", "list", "dict", "tuple", "set"}
)


def _type_name(value: object) -> str:
    """A type's name, but only from a closed allowlist.

    A class can be created at runtime with an arbitrary `__name__`, and
    `league_0000000_seasons_2024` is identifier-shaped, so a regex is not enough.
    """
    name = getattr(type(value), "__name__", "")
    if not isinstance(name, str) or not _SAFE_TYPE_NAME.match(name):
        return "<unprintable>"
    return name if name in _NAMEABLE_TYPES else "<other>"


class Shape(Enum):
    """Which URL builder ran. Chosen at the call site, never parsed back out.

    There is deliberately no `UNKNOWN` member. An `UNKNOWN` bucket turns a wiring
    bug into a row that looks like data: it cannot be audited, it is a legal
    value so it cannot be excluded, and it is where a future "record the path so
    we can tell them apart" patch would land -- which for the pre-2018 family
    records the league id. Without it, an undeclared shape is a counted drop.
    """

    LEAGUE_MODERN = "league_modern"        # seasons/{season}/segments/0/leagues/{id}
    LEAGUE_HISTORY = "league_history"      # leagueHistory/{id}, id is the LAST segment
    PLAYERS_DEFAULTS = "players_defaults"  # seasons/{season}/segments/0/leaguedefaults/3
    PLAYERS_SEASON = "players_season"      # seasons/{season}/players
    SEASON = "season"                      # seasons/{season}


class Outcome(Enum):
    """The classifications a recorder at the request boundary could assign.

    **Nothing in this module assigns one.** The mapping from an httpx result to a
    member is unit 3's wiring, which does not exist yet, so no probe measures it
    and this docstring does not claim one does. An earlier version said a
    truncated body "records as `TRANSPORT_ERROR`... It is now measured" and cited
    a test that constructs no `Outcome` at all.

    What the probes DO establish about the boundary, and what this enum is
    shaped around:

    * A `text/html` login bounce and a JSON success are both plain 200s by
      status, because `resp.json()` runs in `_json_or_auth` after `_request`
      returns. The response HEADERS are in scope at the boundary and they
      differ, so the distinction is recoverable without instrumenting
      `_json_or_auth` -- the one function that must not be instrumented, because
      `resp.request.url` is in scope there. `Record.content_type_json` carries it.
    * A genuinely truncated body never reaches the boundary as a 200: short bytes
      against a declared Content-Length raise `httpx.RemoteProtocolError`, an
      `HTTPError`, which the retry loop catches. Measured on a real loopback
      socket.
    * A 4xx is returned, not retried and not raised. 429/500/502/503/504 are
      retried to exhaustion. Both measured, for every status named.
    """

    OK = "ok"                              # returned to the caller
    RETRYABLE_STATUS = "retryable_status"   # a status _request retries, then slept
    TRANSPORT_ERROR = "transport_error"     # httpx.HTTPError, slept and retried
    EXHAUSTED = "exhausted"                 # the final attempt failed and _request raised


class CacheVerdict(Enum):
    """Decided where each verdict is actually decidable, which is not one place.

    Measured, not assumed:

    * A HIT returns before `_request` is entered, so it produces **no HTTP
      record at all** -- a hit rate counted at the request boundary is
      structurally zero, in the flattering direction.
    * On the only path that produces a BYPASS, the cache is never consulted, so
      a verdict taken at the consultation cannot be `BYPASS`.
    * A retry does **not** re-consult the cache: three HTTP attempts against one
      consultation, because the consultation sits in `fetch_views`, outside
      `_request`'s loop. That withdrew a finding Agent 1 had accepted.
    """

    HIT = "hit"
    MISS = "miss"        # absent OR stale: `DBRawCache.get` returns None for both
    BYPASS = "bypass"    # bust_cache=True, or no cache configured


# ---------------------------------------------------------------- the tokens
#
# Captured at import, looked up by IDENTITY, and immutable: a tuple's elements
# cannot be replaced, and `gc.get_referents` on it hands back only the members
# and the strings themselves. Rewriting `Shape.SEASON._name_` -- which is legal,
# because enum members are ordinary mutable objects, and which defeated both
# reprs in round 6 -- does not change what is printed or counted here.
_TOKENS: tuple[tuple[object, str], ...] = (
    (Shape.LEAGUE_MODERN, "league_modern"),
    (Shape.LEAGUE_HISTORY, "league_history"),
    (Shape.PLAYERS_DEFAULTS, "players_defaults"),
    (Shape.PLAYERS_SEASON, "players_season"),
    (Shape.SEASON, "season"),
    (Outcome.OK, "ok"),
    (Outcome.RETRYABLE_STATUS, "retryable_status"),
    (Outcome.TRANSPORT_ERROR, "transport_error"),
    (Outcome.EXHAUSTED, "exhausted"),
    (CacheVerdict.HIT, "hit"),
    (CacheVerdict.MISS, "miss"),
    (CacheVerdict.BYPASS, "bypass"),
)




#: How a RULE's type is named in a message. `expected.__name__` was read raw,
#: two lines from the allowlist built because "a class can be created at runtime
#: with an arbitrary `__name__`" -- and `Shape` is an ordinary mutable class
#: reachable as a module global, so setting `Shape.__name__` put a payload into
#: every rejection message for that field. Identity lookup over an immutable
#: table, like everything else here.
_RULE_TOKENS: tuple[tuple[type, str], ...] = (
    (int, "int"),
    (bool, "bool"),
)


def _is_token(value: object) -> bool:
    """True only for one of the exact strings in `_TOKENS`, by identity."""
    return any(value is text for _, text in _TOKENS)


def _rule_name(expected: object) -> str:
    for candidate, text in _RULE_TOKENS:
        if candidate is expected:
            return text
    return "<enum>"


def _token(member: object) -> str:
    """The rendering of a closed-enum member. The ONLY sanctioned one.

    Identity comparison against an immutable table, never `member.name` or
    `member.value` -- those are writable on a genuine member and were the
    round-6 P1.
    """
    for candidate, text in _TOKENS:
        if candidate is member:
            return text
    return "<not a member>"


# ------------------------------------------------------------- the field rules
#
# A tuple of pairs, not a mapping. `MappingProxyType` looked immutable and was
# not: `gc.get_referents(proxy)[0]` returns the backing dict, and one write to it
# disarmed the validation AND the value-free `__repr__`, because both read this
# table. A tuple has no backing object to hand out.
_FIELD_RULES: tuple[tuple[str, type], ...] = (
    ("shape", Shape),
    ("outcome", Outcome),
    ("attempt", int),
    ("status", int),
    ("wire_bytes", int),
    ("decoded_bytes", int),
    ("net_ms", int),
    ("gate_ms", int),
    ("throttle_ms", int),
    ("backoff_ms", int),
    ("etag", bool),
    ("last_modified", bool),
    ("content_type_json", bool),
)

# Sanity bounds where a real one exists. NOT a privacy control -- a league id is
# a number and no range check can tell it from a byte count -- but sized so they
# catch the wiring slip that puts one where a measurement belongs. The first
# version used `1 << 40` (a terabyte for a JSON body) and `1 << 32` ms (49 days
# for one attempt), which a seven-digit league id passes comfortably; review was
# right that a bound that loose buys nothing it claims to. These do refuse one.
_FIELD_BOUNDS: tuple[tuple[str, int, int], ...] = (
    ("attempt", 1, 64),
    ("status", 0, 599),
    ("wire_bytes", -1, 1 << 26),        # 64 MiB; an ESPN league payload is ~MBs
    ("decoded_bytes", -1, 1 << 26),
    ("net_ms", 0, 600_000),             # 10 minutes; the retry ladder tops out at 15s
    ("gate_ms", 0, 600_000),
    ("throttle_ms", 0, 600_000),
    ("backoff_ms", 0, 600_000),
)


class RecordRejected(TypeError):
    """A value reached a `Record` field that the field's type or range forbids.

    Raised at construction and converted by `Recorder.record` into a counted
    drop. The message names the FIELD and, from a closed allowlist, the
    offending value's TYPE -- never the value.
    """


@dataclass(frozen=True, slots=True)
class Record:
    """One HTTP attempt. Every field is a closed enum, an int or a bool.

    True of this class and of nothing else: it is sealed against subclassing, so
    the sentence cannot be made false by adding a field elsewhere. `slots=True`
    means there is no `__dict__` to bolt an extra attribute onto.
    """

    shape: Shape
    outcome: Outcome
    attempt: int            # 1-based, within one _request call
    status: int             # 0 when no response was received
    wire_bytes: int         # Content-Length as sent, -1 when absent (chunked)
    decoded_bytes: int      # len(resp.content), -1 when no body was read
    net_ms: int             # strictly around the client call: DNS+TCP+TLS+headers+body
    gate_ms: int            # waiting on the process-wide rate gate
    throttle_ms: int        # waiting on the existing per-key throttle
    backoff_ms: int         # slept AFTER this attempt, before the next or the raise
    etag: bool              # response carried an ETag
    last_modified: bool     # response carried a Last-Modified
    # The boundary CAN tell a login bounce from a JSON success, because the
    # headers are in scope even though the body has not been parsed.
    content_type_json: bool

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError(
            "Record is sealed: a subclass's extra fields are outside the privacy "
            "check, and a subclass that overrides __post_init__ has no check at all"
        )

    def __post_init__(self) -> None:
        _check((spec.name, getattr(self, spec.name)) for spec in fields(self))

    def __getstate__(self) -> dict[str, object]:
        """One explicit pickle format, rather than whichever one the interpreter
        happens to use for a frozen slotted dataclass.

        `dataclass(slots=True)` generates a `__getstate__` returning a list of
        field values on this interpreter, and the generic protocol-2 form is a
        `(None, {slot: value})` tuple. A `__setstate__` that sniffs among those
        shapes leaves branches only some interpreter version reaches, and
        untestable is how a privacy control ends up unverified.
        """
        state: dict[str, object] = {}
        for name, expected in _FIELD_RULES:
            value = getattr(self, name)
            # Enum fields serialise as their TOKEN, not the member. RESIDUALS
            # used to hand this to unit 3's exporter as an obligation; the
            # module's own sanctioned serialisation format can simply not carry
            # a live object whose `_value_` someone rewrote.
            state[name] = _token(value) if expected in (Shape, Outcome) else value
        return state

    def __setstate__(self, state: object) -> None:
        """Validate the whole incoming state, THEN write.

        Round 6: this method assigned every field first and validated after, so
        calling it on a record already sitting in `recorder.records` mutated that
        record in place -- past `frozen=True` -- and although the validation then
        raised, the payload was already in the slots and the object was still in
        the list, and `__getstate__`, `pickle` and `dataclasses.asdict` all
        carried it. Re-validating after the write made the write the delivery
        mechanism.
        """
        if not isinstance(state, Mapping):
            raise RecordRejected(f"state must be a mapping, got {_type_name(state)}")
        names = tuple(name for name, _ in _FIELD_RULES)
        missing = [name for name in names if name not in state]
        if missing:
            raise RecordRejected(f"state is missing {missing[0]}")
        unexpected = [key for key in state if key not in names]
        if unexpected:
            # The count, never the key. This was the module's only direct
            # value-bearing interpolation: it echoed 32 characters of an
            # attacker-controlled key and ran its `__str__` to do it.
            raise RecordRejected(f"state has {len(unexpected)} unexpected key(s)")
        values = tuple(
            (name, _member(state[name], expected) if expected in (Shape, Outcome) else state[name])
            for name, expected in _FIELD_RULES
        )
        _check(values)                      # raises before anything is written
        for name, value in values:
            object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        """Value-free for anything not already known to be an in-range int or bool.

        The bound check is repeated here rather than trusted from construction:
        `object.__setattr__` can put an out-of-range int on a live record, and
        the repr is the last place a value is seen before it reaches a log.

        Drops are silent -- `Recorder.record` swallows the rejection and logs
        nothing -- so `repr` and a pytest assertion diff are the only places a
        rejected value would ever become visible, and CI logs are the most-copied
        artifact in this project. Enum members render through `_token`, never
        `.name`, which is writable.
        """
        parts = []
        for name, expected in _FIELD_RULES:
            value = getattr(self, name)
            if expected in (Shape, Outcome):
                parts.append(f"{name}={_token(value)}")
            elif type(value) is expected and _within_bounds(name, value):
                parts.append(f"{name}={value!r}")
            else:
                parts.append(f"{name}=<invalid {_type_name(value)}>")
        return f"Record({', '.join(parts)})"


def _within_bounds(name: str, value: int) -> bool:
    for bounded, low, high in _FIELD_BOUNDS:
        if bounded == name:
            return low <= value <= high
    return True


def _member(token: object, expected: type) -> object:
    """Resolve a serialised token back to the member it names, by identity."""
    for candidate, text in _TOKENS:
        if text == token and type(candidate) is expected:
            return candidate
    return token            # unresolved: `_check` refuses it a moment later


def _check(values) -> None:
    """The one validation path, shared by construction and by unpickling.

    Identity, not `isinstance`: `isinstance` honours a `__class__` property, so
    an impostor passes it; and `bool` is a subclass of `int`, so the identity
    form is also what keeps `True` out of a byte count and `1` out of a flag.
    """
    # `seen` exists only for the bounds pass below; completeness is guaranteed
    # by both callers (a constructed dataclass has every field, and
    # `__setstate__` rejects a missing key before it gets here), so there is no
    # missing-field branch to reach.
    seen = {}
    for name, value in values:
        expected = None
        for rule_name, rule_type in _FIELD_RULES:
            if rule_name == name:
                expected = rule_type
                break
        if expected is None:
            raise RecordRejected(f"{name} has no validation rule")
        if type(value) is not expected:
            raise RecordRejected(f"{name} must be {_rule_name(expected)}, got {_type_name(value)}")
        if expected in (Shape, Outcome) and _token(value) == "<not a member>":
            raise RecordRejected(f"{name} is not a member of its enum")
        seen[name] = value
    for name, low, high in _FIELD_BOUNDS:
        if name in seen and not (low <= seen[name] <= high):
            raise RecordRejected(f"{name} is out of range")


@dataclass
class Counters:
    """Totals. `cache` and the per-shape maps are keyed by **token strings**, not
    by enum members, so the structures most likely to be serialised into a
    published artifact hold no live object whose fields someone could rewrite.

    `attempts` counts attempts. `filed` counts rows retained. Past the cap they
    diverge, which is the point: a rate computed against `filed` would drift in
    the flattering direction, so `attempts` keeps counting.
    """

    attempts: int = 0
    filed: int = 0
    dropped: int = 0
    overflowed: int = 0
    cache: dict[tuple[str, str], int] = field(default_factory=dict)

    def __repr__(self) -> str:
        cache = {}
        for key, count in self.cache.items():
            shape, verdict = key if type(key) is tuple and len(key) == 2 else (key, None)
            # Membership in the closed token set, not merely "is a str": two
            # arbitrary strings are a perfectly well-typed pair, and round 6's
            # probe for this only exercised keys whose halves were the wrong
            # TYPE, so a key of two attacker-supplied strings printed in full.
            # Identity against the table, not `in` a frozenset: membership runs
            # the candidate's `__hash__`/`__eq__`, so an object that hashes like
            # a token and compares equal to anything was judged safe and then
            # rendered through its own `__str__`, unbounded. `_token` was
            # converted to identity in round 6; this line was missed.
            safe = _is_token(shape) and _is_token(verdict)
            # No length bound: `safe` requires both halves to be identity-matched
            # members of `_TOKENS`, every one of which is a short literal, so a
            # truncation here could never fire. It was added defensively and a
            # control-removal run showed nothing held it -- correctly, because
            # there was nothing to hold.
            name = f"{shape}/{verdict}" if safe else f"<invalid key {len(cache) + 1}>"
            cache[name] = count if type(count) is int else "<invalid count>"
        return (
            f"Counters(attempts={self.attempts}, filed={self.filed}, "
            f"dropped={self.dropped}, overflowed={self.overflowed}, cache={cache})"
        )


def _shape_of(values: object) -> object:
    """The `shape` of a mapping that has already failed, without running its code.

    The recovery path was `values.get("shape") if isinstance(values, Mapping)`,
    outside any guard — and it runs on every malformed input, which is the whole
    point of the path. Four inputs raised straight out of `record()`: a
    `Mapping` with no usable `get`, a `get` that raises (carrying its own message
    into the caller's traceback), a key whose `__eq__` raises, and an object
    whose `__class__` property raises, which `isinstance` evaluates. Worse than
    a raise: the attempt went uncounted, so the counters under-reported exactly
    the pathological attempts the percentiles exist to capture.

    `type(values) is dict` rather than `isinstance`, so no `__class__` property
    runs; `dict.get` unbound, so no overridden `get` runs; and a guard around
    the lookup itself, because a key's `__eq__` still executes during it.
    """
    if type(values) is not dict:
        return None
    try:
        return dict.get(values, "shape")
    except Exception:
        return None


class Recorder:
    """Collects records. Never raises into the caller; counts its own failures.

    A dropped record is not merely a missing row: drops correlate with the
    pathological attempts -- long bodies, exception paths, contention -- that the
    percentiles exist to capture, so any aggregate derived from a shape that
    dropped anything is biased, not just incomplete. The exporter's job is to
    carry that fact next to the number, which is why `dropped` is per-shape.
    `overflowed` is per-shape for the same reason, and is a **head** bias: past
    the cap the EARLIEST rows are the ones kept, so a percentile from a recorder
    that overflowed is unusable rather than merely truncated.

    `record(values)` takes a **mapping**, positionally. It used to take `**values`
    and its docstring promised it never raises into the caller; round 6 measured
    `record(**{1: 2})` raising `TypeError: keywords must be strings` at argument
    binding, before the guarded body is entered -- and a dict built from parsed
    data is exactly where a non-string key comes from. A single positional
    parameter has no keyword binding to fail.

    Updates are serialised. All three counter sites share one lock.
    """

    #: Rows held in memory for the life of the recorder; unit 3 appends one per
    #: HTTP attempt in a long-lived process. Past this, rows are dropped and
    #: counted rather than growing without bound.
    MAX_RECORDS = 50_000

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.counters = Counters()
        self.dropped_by_shape: dict[str, int] = {}
        self.overflowed_by_shape: dict[str, int] = {}
        self._lock = threading.Lock()

    @classmethod
    def disabled(cls) -> None:
        """The resting state. A provider holding None records nothing."""
        return None

    def record(self, values: Mapping[str, object]) -> None:
        """File one attempt. A rejected field costs a row, not a request."""
        converted = None
        try:
            converted = dict(values)
            record = Record(**converted)  # type: ignore[arg-type]
            token = _token(record.shape)
            with self._lock:
                self.counters.attempts += 1
                if len(self.records) >= self.MAX_RECORDS:
                    self.counters.overflowed += 1
                    self.overflowed_by_shape[token] = self.overflowed_by_shape.get(token, 0) + 1
                    return
                self.records.append(record)
                self.counters.filed += 1
        except Exception:
            # `converted` when the conversion succeeded: `_shape_of` is hardened
            # against a hostile mapping by only accepting an exact `dict`, and
            # that hardening silently cost per-shape attribution for every
            # `OrderedDict`, `defaultdict`, `ChainMap` and `MappingProxyType` --
            # measured, all landing in the total only. An exporter would then see
            # a clean shape and publish an unbiased-looking percentile for a
            # shape that dropped rows, which is the one outcome the per-shape
            # counter exists to prevent.
            self._drop(
                _shape_of(values if converted is None else converted),
                counts_as_attempt=True,
            )

    def cache_verdict(self, shape: Shape, verdict: CacheVerdict) -> None:
        try:
            if type(shape) is not Shape or type(verdict) is not CacheVerdict:
                raise RecordRejected("cache verdict takes closed enum members only")
            key = (_token(shape), _token(verdict))
            if "<not a member>" in key:
                raise RecordRejected("cache verdict takes closed enum members only")
            with self._lock:
                self.counters.cache[key] = self.counters.cache.get(key, 0) + 1
        except Exception:
            self._drop(shape)

    def _drop(self, shape: object, counts_as_attempt: bool = False) -> None:
        """Count a drop, per shape when the shape itself was well-formed.

        A drop whose `shape` is the thing that was malformed cannot be
        attributed, so it lands only in the total. That asymmetry is deliberate:
        `dropped` and `sum(dropped_by_shape.values())` are allowed to differ, and
        the exporter must not treat either as the other.
        """
        with self._lock:
            if counts_as_attempt:
                self.counters.attempts += 1
            self.counters.dropped += 1
            if type(shape) is Shape:
                token = _token(shape)
                if token != "<not a member>":
                    self.dropped_by_shape[token] = self.dropped_by_shape.get(token, 0) + 1


class RateGate:
    """One request START per interval, for whoever holds this instance.

    The existing `_throttle` keys by account, so N accounts start N requests in
    the same second and each is individually compliant; a deployment-wide budget
    cannot be expressed per key. This class is the MECHANISM; `shared_gate()` is
    the process-wide policy.

    Configuration is **sealed after construction**. Round 5 found
    `shared_gate().interval = 0.0`; a read-only property was added; round 6 then
    found `_interval`, and `_sleep` -- where a no-op sleeper made `wait()` report
    1.0 s of throttling against 0.0 s of real blocking, fabricating the very
    number this phase exists to measure, with every probe green. So the seal
    covers all three, and `_last_start` is the only attribute that may change.
    `object.__setattr__` still bypasses it; see the module's RESIDUALS.

    The clock and the sleep are injectable HERE and nowhere else: `shared_gate()`
    takes no arguments, precisely so nobody can hand the process-wide gate a
    no-op sleeper.
    """

    __slots__ = ("_clock", "_interval", "_last_start", "_lock", "_sealed", "_sleep")

    def __init__(
        self,
        interval: float = GATE_INTERVAL_SECONDS,
        clock=time.monotonic,
        sleeper=time.sleep,
    ):
        self._interval = interval
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._last_start: float | None = None
        self._sealed = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False) and name != "_last_start":
            raise AttributeError(f"{name} is fixed after construction")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        """The half of the seal that was missing.

        `__setattr__` reads the seal as `getattr(self, "_sealed", False)`, so
        `del gate._sealed` made that default fire and the next plain assignment
        was permitted — ordinary syntax, no guard, and it reproduced the
        measurement-fabrication finding the seal was added to fix. The word
        `del` appeared nowhere in the suite, so the control-removal harness had
        never exercised deletion against it.
        """
        raise AttributeError(f"{name} cannot be deleted")

    @property
    def interval(self) -> float:
        return self._interval

    def wait(self) -> float:
        """Block until a request may start. Returns seconds waited.

        The lock is not decoration: without it, N callers read the same
        `_last_start`, compute the same `due` and all start together -- a
        thundering herd, measured at seven simultaneous starts in a one-request
        slot, which is the failure the gate exists to prevent.
        """
        with self._lock:
            now = self._clock()
            if self._last_start is None:
                self._last_start = now
                return 0.0
            due = self._last_start + self._interval
            waited = due - now
            if waited > 0:
                self._sleep(waited)
                self._last_start = due
                return waited
            # The late-arrival branch: the caller already waited longer than the
            # interval on its own, so the gate owes it nothing and the next slot
            # is measured from NOW, not from the stale `due`. Anchoring to `due`
            # would let a process that idled for a minute fire a minute's worth
            # of backdated slots at once. `waited == 0` exactly lands here too,
            # and must NOT sleep zero seconds to say so: `gate_ms` is one of the
            # numbers this phase exists to measure.
            self._last_start = now
            return 0.0


def _build_shared_gate_accessor():
    """The process-wide gate, built once at import and held in a closure cell.

    It was a lazily-initialised module global. Two things followed, both measured
    in round 6: the global could simply be rebound
    (`telemetry._shared_gate = RateGate(interval=0.0)`) -- one statement, fewer
    than the two public calls the round-5 disarm needed -- and the lazy
    initialisation needed a lock whose probe was hard to make deterministic.
    Building eagerly removes the `None` state, so there is no construction race
    to test for, and a closure cell has no module-level name to rebind.

    This is a seal, not immutability: `shared_gate.__closure__[0].cell_contents`
    is writable, as is anything in Python. See the module's RESIDUALS. What it
    buys is that every remaining path is visibly irregular rather than ordinary.
    """
    gate = RateGate(interval=GATE_INTERVAL_SECONDS)

    def shared_gate() -> RateGate:
        """The process-wide gate. Takes no arguments, deliberately."""
        return gate

    return shared_gate


shared_gate = _build_shared_gate_accessor()


# ---------------------------------------------------------------------------
# Unit 3: the shared recorder, and the offline report exporter.
#
# The recorder accessor mirrors `shared_gate`: built once at import, held in a
# closure cell, no module-level name bound to the instance. Same reason as the
# gate -- a module global "could simply be rebound", which is one statement.
#
# The exporter is here because `telemetry.py` is the only owned module that can
# host it: `sync.py` and the routers are Forbidden and `config.py` holds the
# flag. Everything it publishes goes through the renderers below, which apply
# the contract's grid and ceilings. No renderer reads `.name` or `.value` off a
# member -- `_token` is the only path, and the module's AST probe enforces it.
# ---------------------------------------------------------------------------

#: Publication grid and ceilings (contract criterion 4). Every ceiling is a
#: literal. A value above its ceiling is rendered without any digit of itself
#: and counted in `censored`; a value at or below it is put on its grid.
_MS_GRID_MS = 50
_MS_CEILING_MS = 30_000
_BYTE_BUCKET = 1 << 16          # 64 KiB
_BYTE_CEILING = 1 << 26         # 64 MiB
_ATTEMPT_CEILING = 8
_STATUS_CEILING = 599
_SEASON_CEILING = 9999
_WEEK_CEILING = 18
_LEAGUE_CEILING = 999
_COUNT_CEILING = 99_999

#: Below this many samples a shape is published as thin rather than summarised.
_THIN_BELOW = 5

_OVER = "over-ceiling"


def _render_count(value: int) -> str:
    """An integer count, or the over-ceiling marker. Carries no digit when over."""
    if not isinstance(value, int) or isinstance(value, bool):
        return _OVER
    if value < 0 or value > _COUNT_CEILING:
        return _OVER
    return str(value)


def _render_small(value: int, ceiling: int) -> str:
    """A small bounded integer: season, week, leagues, attempt, status."""
    if not isinstance(value, int) or isinstance(value, bool):
        return _OVER
    if value < 0 or value > ceiling:
        return _OVER
    return str(value)


def _render_ms(value: int) -> str:
    """Duration on a 50 ms grid, in seconds to two decimals, ceiling 30.00 s.

    The ceiling is the control and the grid is the mitigation, and they do
    different amounts of work: 50 ms over a six-digit value destroys about 5.6
    bits, which is not a control, so the ceiling is what refuses one. Every
    value of six digits and up is over it, and so is the top of the five-digit
    range. Below the ceiling a duration publishes, which is the residual the
    contract names.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return _OVER
    if value > _MS_CEILING_MS:
        return _OVER
    stepped = -(-value // _MS_GRID_MS) * _MS_GRID_MS
    return f"{stepped / 1000:.2f} s"


def _render_bytes(value: int) -> str:
    """Byte count bucketed to 64 KiB and rendered as a KiB or MiB multiple.

    Never a raw byte integer. A 64 KiB bucket printed in bytes is a multiple of
    65536, so every bucket from the second upward is a six-digit run and the
    grammar's digit rule would reject it -- the two rules together admitted two
    publishable values. The rendering is the fix, not cosmetics.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return _OVER
    if value == -1:
        return "absent"
    if value < 0 or value > _BYTE_CEILING:
        return _OVER
    buckets = -(-value // _BYTE_BUCKET)
    kib = buckets * 64
    if kib < 1024:
        return f"{kib} KiB"
    return f"{kib / 1024:.1f} MiB"


def _censored_ms(values: tuple) -> int:
    return sum(1 for v in values if not isinstance(v, int) or v < 0 or v > _MS_CEILING_MS)


def _percentile(values: tuple, pct: int) -> int | None:
    """Nearest-rank percentile. None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    idx = -(-pct * len(ordered) // 100) - 1
    if idx < 0:
        idx = 0
    return ordered[idx]


def _upper_bound(events: int, trials: int) -> str:
    """A rate, or an honest refusal to state one.

    `trials == 0` is not a rate with a wide interval -- it is not a measurement,
    and printing `3/N` for it is a division by zero dressed as rigour.
    """
    if trials <= 0:
        return "not measured - no call site"
    if events == 0:
        n = _render_count(trials)
        return f"0/{n} observed; 95 pct upper bound near 3/{n}"
    return f"{_render_count(events)}/{_render_count(trials)} observed"


REPORT_BEGIN = "<!-- PHASE32-REPORT-BEGIN -->"
REPORT_END = "<!-- PHASE32-REPORT-END -->"

#: Every shape the report prints, in a fixed order, so an absent row cannot be
#: mistaken for "not exercised". Written out rather than derived from `Shape`, so
#: adding a member is a visible change to this list too -- and the suite pins this
#: tuple against `Shape`'s own members, because "written out" on its own let a
#: member be REMOVED here and the probe that checked for its row read the same
#: tuple and stopped looking. That is the round-6 defect, and the pin is the fix.
_REPORT_SHAPES = ("league_modern", "league_history", "players_defaults", "players_season", "season")

#: Shapes with no caller under `api/` -- verified from source, not inferred:
#: `fetch_player_pool` has no caller and `players_url(defaults=False)` is never
#: invoked at all. `trials == 0` for these, so no interval is printed.
_NO_CALL_SITE = frozenset({"players_defaults", "players_season"})


def _rows_by_shape(recorder) -> dict:
    """Group filed rows by shape token. Identity lookup, never a member's name."""
    out: dict = {token: [] for token in _REPORT_SHAPES}
    for record in recorder.records:
        token = _token(record.shape)
        if token in out:
            out[token].append(record)
    return out


def _table(header: tuple, rows: list) -> list:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return lines


def render_report(recorder, *, season: int, week: int, leagues: int) -> str:
    """The offline report. Every number goes through a renderer; nothing is f-strung raw.

    This is a MODEL of occupancy, not an observation of it: every input is a
    fixture or a loopback socket, so the ESPN network term is unmeasured and the
    report says so in those words rather than letting the arithmetic read as
    complete.
    """
    by_shape = _rows_by_shape(recorder)
    counters = recorder.counters

    lines = [REPORT_BEGIN, "", "# phase 32 provider behaviour report", ""]
    lines += [
        "generated offline from recorded attempts. espn service traffic only.",
        "no provider network call was made. the espn network term is unmeasured.",
        "",
        "## context",
        "",
    ]
    lines += _table(
        ("field", "value"),
        [
            ["season", _render_small(season, _SEASON_CEILING)],
            ["week", _render_small(week, _WEEK_CEILING)],
            ["leagues", _render_small(leagues, _LEAGUE_CEILING)],
            ["attempts", _render_count(counters.attempts)],
            ["filed", _render_count(counters.filed)],
            ["dropped", _render_count(counters.dropped)],
            ["overflowed", _render_count(counters.overflowed)],
        ],
    )

    lines += ["", "## shapes", ""]
    shape_rows = []
    for token in _REPORT_SHAPES:
        rows = by_shape[token]
        n = len(rows)
        if token in _NO_CALL_SITE:
            label = "no call site"
        elif n == 0:
            label = "not exercised"
        elif n < _THIN_BELOW:
            label = "n too small"
        else:
            label = "observed"
        cells = [token, label, _render_count(n)]
        for attr in ("net_ms", "gate_ms", "throttle_ms", "backoff_ms"):
            sample = tuple(getattr(r, attr) for r in rows)
            for pct in (50, 95):
                p = _percentile(sample, pct)
                cells.append("-" if p is None else _render_ms(p))
        for attr in ("wire_bytes", "decoded_bytes"):
            sample = tuple(getattr(r, attr) for r in rows if getattr(r, attr) >= 0)
            p = _percentile(sample, 50)
            cells.append("absent" if p is None else _render_bytes(p))
        cells.append(_render_count(recorder.dropped_by_shape.get(token, 0)))
        cells.append(_render_count(recorder.overflowed_by_shape.get(token, 0)))
        censored = sum(_censored_ms(tuple(getattr(r, a) for r in rows))
                       for a in ("net_ms", "gate_ms", "throttle_ms", "backoff_ms"))
        cells.append(_render_count(censored))
        shape_rows.append(cells)
    lines += _table(
        (
            "shape", "label", "n",
            "net p50", "net p95", "gate p50", "gate p95",
            "throttle p50", "throttle p95", "backoff p50", "backoff p95",
            "wire p50", "decoded p50", "dropped", "overflowed", "censored",
        ),
        shape_rows,
    )

    lines += ["", "## outcomes", ""]
    outcome_rows = []
    for token in _REPORT_SHAPES:
        tally: dict = {}
        for record in by_shape[token]:
            key = _token(record.outcome)
            tally[key] = tally.get(key, 0) + 1
        for key in sorted(tally):
            outcome_rows.append([token, key, _render_count(tally[key])])
    lines += _table(("shape", "outcome", "count"), outcome_rows)

    lines += ["", "## statuses", ""]
    status_rows = []
    for token in _REPORT_SHAPES:
        tally = {}
        for record in by_shape[token]:
            tally[record.status] = tally.get(record.status, 0) + 1
        for key in sorted(tally):
            status_rows.append(
                [token, _render_small(key, _STATUS_CEILING), _render_count(tally[key])]
            )
    lines += _table(("shape", "status", "count"), status_rows)

    lines += ["", "## cache", ""]
    # Every other cell in this report goes through `_token` or a renderer; these
    # two went through bare `str()`. Review measured the suite's own malformed-key
    # fixture publishing a full league URL into this table -- the round-6
    # `Counters.__repr__` finding re-opened on the publication path, and into a
    # committed file rather than a log. The same gate `Counters.__repr__` uses is
    # applied here, and a key whose SHAPE was never checked is not indexed at all.
    cache_rows = []
    unpublishable = 0
    for key, count in counters.cache.items():
        if (
            type(key) is tuple
            and len(key) == 2
            and _is_token(key[0])
            and _is_token(key[1])
        ):
            cache_rows.append([key[0], key[1], _render_count(count)])
        else:
            unpublishable += 1
    cache_rows.sort()
    if unpublishable:
        cache_rows.append(["unpublishable", "unpublishable", _render_count(unpublishable)])
    lines += _table(("shape", "verdict", "count"), cache_rows)

    lines += ["", "## rates", ""]
    attempts = counters.attempts
    errors = sum(1 for r in recorder.records if _token(r.outcome) != "ok")
    # `k[1]` was indexed unguarded, on a key whose shape had not been checked.
    hits = sum(
        v
        for k, v in counters.cache.items()
        if type(k) is tuple and len(k) == 2 and k[1] == "hit"
    )
    verdicts = sum(counters.cache.values())
    # The numerators come from FILED rows and the denominator from ATTEMPTS, so a
    # drop or an overflow biases every rate in the flattering direction. The module
    # docstring assigns the exporter the job of carrying that fact next to the
    # number rather than leaving it to be inferred, so it travels in the table.
    unclassified = attempts - counters.filed
    codes = [r.status for r in recorder.records]
    lines += _table(
        ("rate", "value"),
        [
            # Both labels name their denominator, because a rate whose denominator
            # is implied is the defect this phase recorded thirty-plus times. The
            # hit rate here is counted WHERE HITS ARE DECIDED; the same rate counted
            # at the request boundary is a structural zero and is listed as one
            # below, so the two framings are never conflated into one number.
            ["error rate over recorded attempts", _upper_bound(errors, attempts)],
            ["attempts not classified", _render_count(unclassified)],
            ["cache hit rate where decided", _upper_bound(hits, verdicts)],
            ["status 304", _upper_bound(sum(1 for c in codes if c == 304), attempts)],
            ["players defaults", _upper_bound(0, 0)],
            ["players season", _upper_bound(0, 0)],
        ],
    )

    lines += ["", "## structural zeros", ""]
    lines += _table(
        ("field", "reason"),
        [
            ["gate ms", "no call site - the rate gate is not called by this phase"],
            ["status 304", "dead by design until conditional requests exist"],
            ["cache hit at request boundary", "a hit never reaches the request seam"],
            ["players defaults", "no caller under api"],
            ["players season", "builder never invoked"],
        ],
    )

    lines += ["", "## limits", ""]
    lines += _table(
        ("claim", "limit"),
        [
            ["byte figures", "bucketed to 64 kib; bounds precision not linkability"],
            ["durations", "50 ms grid under a 30 s ceiling; a value below it publishes"],
            ["identifier width", "no lower bound is established by this phase"],
            ["espn network term", "unmeasured; every input is a fixture or loopback"],
            ["occupancy", "a model, not an observation"],
            ["scope", "espn service traffic only; discovery and cross check are ungated"],
        ],
    )
    lines += ["", REPORT_END, ""]
    return "\n".join(lines)


def write_report(recorder, *, season: int, week: int, leagues: int, path=None) -> str:
    """Render the report and write it where the (required) setting points.

    The artifact was generated by an ad-hoc script, which made it unreproducible,
    left `telemetry_report_path` a required setting with no reader, and let the
    committed file drift from anything the suite had ever rendered. This is the
    committed entry point: same renderer, one destination, and the returned text is
    what was written so a caller can check it.
    """
    from ..config import get_settings

    text = render_report(recorder, season=season, week=week, leagues=leagues)
    target = pathlib.Path(path) if path is not None else pathlib.Path(
        get_settings().telemetry_report_path
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return text


def _build_shared_recorder_accessor():
    """The process-wide recorder, built once at import and held in a closure cell.

    Same reasoning as `shared_gate`: a module global could simply be rebound in
    one statement, and a closure cell has no module-level name to rebind. This is
    a seal, not immutability -- the cell contents are writable, as is anything in
    Python. See the module's RESIDUALS.
    """
    recorder = Recorder()

    def shared_recorder() -> Recorder:
        """The process-wide recorder. Takes no arguments, deliberately."""
        return recorder

    return shared_recorder


shared_recorder = _build_shared_recorder_accessor()
