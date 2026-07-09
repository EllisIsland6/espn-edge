"""Pydantic output schemas for the five AI report kinds (SPEC §7).

These are the structured-output contracts: the model is constrained to them and we
re-validate before storing. Enums are Literals so invalid labels are rejected.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StrategyLabel = Literal[
    "Zero RB",
    "Hero RB",
    "Robust RB",
    "Anchor WR",
    "Elite TE",
    "Late-Round QB",
    "Balanced/BPA",
    "Autodraft/Absent",
]
Grade = Literal["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"]
Confidence = Literal["low", "medium", "high"]
DifficultyTier = Literal["Very Soft", "Soft", "Average", "Tough", "Very Tough"]
VerdictLabel = Literal["Advantaged", "Neutral", "Disadvantaged"]


class DraftRecap(BaseModel):
    strategy_label: StrategyLabel
    secondary_label: StrategyLabel | None = None
    grade: Grade
    confidence: Confidence
    summary: str = Field(..., description="≤120 words, grounded in the provided picks only")
    key_values: list[str] = Field(default_factory=list)
    key_reaches: list[str] = Field(default_factory=list)


class LeagueBrief(BaseModel):
    difficulty_tier: DifficultyTier
    narrative: str
    exploit_plan: list[str] = Field(..., description="~3 concrete bullets for my team")


class AdvantageVerdict(BaseModel):
    verdict_label: VerdictLabel
    paragraph: str
    highest_leverage_move: str


class WeeklyRecap(BaseModel):
    headline: str
    body: str
    luck_notes: list[str] = Field(default_factory=list)
    waiver_highlights: list[str] = Field(default_factory=list)


class TradeProposal(BaseModel):
    i_give: list[str]
    i_get: list[str]
    rationale: str


class TradeFinder(BaseModel):
    proposals: list[TradeProposal] = Field(default_factory=list)
    note: str = "Advisory only — the app never executes trades on ESPN."


# kind -> schema, for the service to look up.
from .ai_config import (  # noqa: E402  (import after models to avoid cycle noise)
    KIND_ADVANTAGE_VERDICT,
    KIND_DRAFT_RECAP,
    KIND_LEAGUE_BRIEF,
    KIND_TRADE_FINDER,
    KIND_WEEKLY_RECAP,
)

SCHEMA_BY_KIND: dict[str, type[BaseModel]] = {
    KIND_DRAFT_RECAP: DraftRecap,
    KIND_LEAGUE_BRIEF: LeagueBrief,
    KIND_ADVANTAGE_VERDICT: AdvantageVerdict,
    KIND_WEEKLY_RECAP: WeeklyRecap,
    KIND_TRADE_FINDER: TradeFinder,
}
