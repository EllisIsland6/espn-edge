"""AI layer tunables — models, enums, schema version (SPEC §7).

Models follow SPEC §7's tier choice (mid-tier for one-off analyses, small/fast for
bulk recaps); ids are current per the claude-api reference. Swap to claude-opus-4-8
for maximum quality over cost. See docs/phase-4-ai.md.
"""

from __future__ import annotations

# One-off analyses (draft recap, league brief, advantage verdict, trade finder).
STANDARD_MODEL = "claude-sonnet-5"
# Bulk / high-volume (weekly recaps).
BULK_MODEL = "claude-haiku-4-5"

# Bump when an output schema or prompt changes so cached reports invalidate.
# v2 (Phase 18): league_brief + advantage_verdict prompts re-grounded on the Edge Index.
# v3 (Phase 19): weekly_recap regrounded on all-play/luck + waiver transactions.
# v4 (Phase 22): trade_finder regrounded on roster snapshots + player-name validation.
SCHEMA_VERSION = "v4"

# Small outputs (≤120-word summaries + a few bullets) — modest cap.
MAX_TOKENS = 2048

# Report kinds (used as ai_reports.kind and in cache keys).
KIND_DRAFT_RECAP = "draft_recap"
KIND_LEAGUE_BRIEF = "league_brief"
KIND_ADVANTAGE_VERDICT = "advantage_verdict"
KIND_WEEKLY_RECAP = "weekly_recap"
KIND_TRADE_FINDER = "trade_finder"

# Fixed strategy-label enum for draft recaps (SPEC §7.1).
STRATEGY_LABELS = (
    "Zero RB",
    "Hero RB",
    "Robust RB",
    "Anchor WR",
    "Elite TE",
    "Late-Round QB",
    "Balanced/BPA",
    "Autodraft/Absent",
)


class UnpricedModel(LookupError):
    """No operator-configured price for this model, so its spend cannot be bounded."""


def price_micro_usd_per_mtok(model: str) -> tuple[int, int]:
    """(input, output) price in micro-USD per million tokens, from configuration.

    There is deliberately NO default price table in this repository. A default
    would be a number invented here and then trusted by a ceiling, and a ceiling
    computed from an invented rate bounds nothing -- it would read as enforcement
    while being arithmetic over a guess. Rates are operator-supplied via
    `Settings.ai_price_micro_usd_per_mtok`, and a model with no configured rate
    raises, which the ledger turns into cached-only generation.

    Micro-USD per million tokens keeps the arithmetic in integers end to end: at
    this scale a rate is a whole number, so nothing is rounded on the way in.
    """
    from .config import get_settings

    table = get_settings().ai_price_micro_usd_per_mtok or {}
    rate = table.get(model)
    if rate is None:
        raise UnpricedModel(f"no configured price for model {model!r}")
    try:
        rate_in, rate_out = (int(rate[0]), int(rate[1]))
    except (TypeError, ValueError, IndexError, KeyError) as error:
        raise UnpricedModel(f"malformed price for model {model!r}") from error
    if rate_in < 0 or rate_out < 0:
        raise UnpricedModel(f"negative price for model {model!r}")
    return rate_in, rate_out
