"""Phase 31 — synthetic fixture factory.

Seeded, never recorded. No real capture is an input to generation: every value
here is derived from an integer seed, so a corpus is reproducible byte-for-byte
from its seed alone and carries no provenance from live ESPN data.

Fixture loading is allowlisted by *path grammar plus membership*.

SCOPE, stated rather than implied: `load_synthetic` and `assert_loadable` have
no callers outside the tests. An earlier version of this docstring claimed they
stop hosted mode reaching a recorded capture; that was a conclusion the module
had not established, since the functions sit on no production path. The hosted
boundary that actually exists is `Settings.app_mode` in `api/config.py`, which
is structural and frozen. These helpers are a correct allowlist waiting for a
caller, and `load_synthetic` has no realpath containment check -- latent rather
than live, for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# A fixture path is lowercase, underscore-separated, .json, no traversal.
PATH_GRAMMAR = re.compile(r"^[a-z][a-z0-9_]*\.json$")

# Membership allowlist. `real_*` is deliberately absent and cannot be added by
# grammar alone; see `assert_loadable`.
SYNTHETIC_ALLOWLIST = frozenset(
    {
        "public_league.json",
        "players_pool.json",
        "current_roster.json",
        "pro_schedule_2026.json",
        "ffc_adp_ppr_10_2026.json",
    }
)


class FixtureError(RuntimeError):
    """Raised for a path that is not a permitted synthetic fixture."""


def assert_loadable(name: str, *, allow_recorded: bool = False) -> None:
    """Grammar first, then membership. Both must pass.

    Grammar alone is not enough: `real_league_2026.json` satisfies the grammar.
    Membership alone is not enough either, because it would permit traversal in
    a future caller that builds names dynamically.
    """
    if not PATH_GRAMMAR.match(name):
        raise FixtureError(f"fixture name violates path grammar: {name!r}")
    if name.startswith("real_") and not allow_recorded:
        raise FixtureError(f"recorded capture is not loadable here: {name!r}")
    if not allow_recorded and name not in SYNTHETIC_ALLOWLIST:
        raise FixtureError(f"fixture is not on the synthetic allowlist: {name!r}")


def load_synthetic(name: str) -> dict:
    assert_loadable(name)
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- generation


def _digest(seed: int, *parts: object) -> str:
    material = "|".join([str(seed), *(str(p) for p in parts)])
    return hashlib.sha256(material.encode()).hexdigest()


def synthetic_swid(seed: int, member_index: int) -> str:
    """A shape-valid, deliberately low-entropy placeholder.

    Low entropy is the point: the provenance scanner asserts on content, so a
    generated value must be distinguishable from a real identifier by entropy
    rather than by an allowlist of known fakes.
    """
    tail = _digest(seed, "swid", member_index)[:4].upper()
    return f"{{{tail}0000-0000-0000-0000-{member_index:012d}}}"


def synthetic_member(seed: int, index: int) -> dict:
    return {
        "id": synthetic_swid(seed, index),
        "displayName": f"synthetic_member_{index:03d}",
        "firstName": "Synthetic",
        "lastName": f"Member{index:03d}",
    }


def build_league(seed: int, *, league_index: int = 0, season: int = 2026,
                 team_count: int = 10) -> dict:
    """One deterministic synthetic league view."""
    members = [synthetic_member(seed, league_index * 100 + i) for i in range(team_count)]
    teams = [
        {
            "id": i + 1,
            "abbrev": f"S{i:02d}",
            "location": "Synthetic",
            "nickname": f"Team{i:02d}",
            "owners": [members[i]["id"]],
            "record": {"overall": {"wins": i % 4, "losses": 3 - (i % 4), "ties": 0}},
            "points": float(90 + (i * 7) % 60),
        }
        for i in range(team_count)
    ]
    return {
        "id": 900_000 + league_index,
        "seasonId": season,
        "status": {
            "currentMatchupPeriod": 2,
            "latestScoringPeriod": 1,
            "finalScoringPeriod": 17,
            "isActive": True,
        },
        "settings": {
            "name": f"Synthetic League {league_index:03d}",
            "size": team_count,
            "scoringSettings": {"scoringItems": []},
            "rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 2, "4": 2, "6": 1}},
            "scheduleSettings": {"playoffTeamCount": 4},
            "draftSettings": {"type": "SNAKE"},
        },
        "members": members,
        "draftDetail": {"drafted": True, "picks": []},
        "teams": teams,
        "schedule": [],
    }


def build_corpus(seed: int, *, leagues: int = 115, season: int = 2026) -> list[dict]:
    """A full synthetic corpus. 115 leagues is the contract's stated shape."""
    return [build_league(seed, league_index=i, season=season) for i in range(leagues)]


def build_colliding_tenants(seed: int, *, season: int = 2026) -> list[dict]:
    """Two leagues sharing an espn id across tenants.

    Tenant isolation is Phase 36's problem, but the corpus has to be able to
    express the collision before that phase can test against it.
    """
    a = build_league(seed, league_index=0, season=season)
    b = build_league(seed + 1, league_index=0, season=season)
    b["settings"]["name"] = "Synthetic League 000 (other tenant)"
    return [a, b]


# ---------------------------------------------------------------- malformed


@dataclass(frozen=True)
class MalformedVariant:
    name: str
    payload: object
    why: str


def malformed_variants(seed: int) -> list[MalformedVariant]:
    """Adversarial shapes a parser must survive without crashing the process."""
    base = build_league(seed)

    def mutate(fn):
        import copy

        clone = copy.deepcopy(base)
        fn(clone)
        return clone

    return [
        MalformedVariant("empty-object", {}, "no keys at all"),
        MalformedVariant("null-teams", mutate(lambda d: d.update(teams=None)),
                         "teams present but null"),
        MalformedVariant("teams-not-a-list", mutate(lambda d: d.update(teams={})),
                         "wrong container type"),
        MalformedVariant("missing-settings", mutate(lambda d: d.pop("settings")),
                         "required view absent"),
        MalformedVariant("member-without-id",
                         mutate(lambda d: d["members"][0].pop("id")),
                         "member missing its identifier"),
        MalformedVariant("owner-referencing-unknown-member",
                         mutate(lambda d: d["teams"][0].update(owners=["{NOPE}"])),
                         "dangling owner reference"),
        MalformedVariant("negative-size",
                         mutate(lambda d: d["settings"].update(size=-1)),
                         "impossible league size"),
        MalformedVariant("string-where-number-expected",
                         mutate(lambda d: d["teams"][0].update(points="not-a-number")),
                         "type confusion in a numeric field"),
        MalformedVariant("deeply-nested", {"a": {"b": {"c": {"d": {"e": {}}}}}},
                         "unexpected nesting depth"),
    ]
