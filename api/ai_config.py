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
SCHEMA_VERSION = "v2"

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
