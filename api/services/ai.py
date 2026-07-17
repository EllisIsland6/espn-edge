"""AI analysis service (SPEC §7) — backend-only Anthropic integration.

Isolated from ESPN fetching and UI. Additive: with no ANTHROPIC_API_KEY the service
is `enabled = False` and never constructs or calls the SDK. Grounded prompts (facts
only), structured output validated with pydantic before storing, and caching by input
hash in `ai_reports`. No secrets are logged or returned. See docs/phase-4-ai.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_config import MAX_TOKENS, SCHEMA_VERSION
from ..ai_schemas import SCHEMA_BY_KIND
from ..config import get_settings
from ..models import AiReport

log = logging.getLogger("espn.ai")


class AiDisabledError(RuntimeError):
    """AI requested but no API key configured. Callers should guard on `enabled`."""


class AiError(RuntimeError):
    """Model call failed. Message is safe to surface (no secrets)."""


class LlmClient(Protocol):
    def complete_json(
        self, *, model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int
    ) -> dict: ...


class AnthropicLlmClient:
    """Production client using the official SDK + structured outputs."""

    def __init__(self, api_key: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete_json(
        self, *, model: str, system: str, user: str, schema: type[BaseModel], max_tokens: int
    ) -> dict:
        import anthropic

        # Logs and error messages never include the key, prompts, cookies, SWID,
        # espn_s2, or raw model output — only the exception class.
        try:
            resp = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.APIError as exc:
            log.warning("anthropic API error: %s", type(exc).__name__)
            raise AiError(f"model call failed ({type(exc).__name__})") from exc
        except ValidationError as exc:  # parse() validated the output and it didn't fit
            log.warning("anthropic parse validation failed")
            raise AiError("model output could not be parsed") from exc
        except Exception as exc:  # any other SDK/parse failure — stay user-safe, no 500
            log.warning("anthropic parse failed: %s", type(exc).__name__)
            raise AiError("model output could not be parsed") from exc

        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            log.warning("anthropic returned no parsed output")
            raise AiError("model returned no parsed output")
        try:
            return parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else dict(parsed)
        except Exception as exc:
            log.warning("could not serialize parsed output: %s", type(exc).__name__)
            raise AiError("model output could not be parsed") from exc


_SYSTEM = (
    "You are a fantasy football analyst for ESPN Edge. You are given ONLY computed facts "
    "from the user's league database, as JSON. Narrate and classify strictly from those "
    "facts. Do NOT invent players, teams, statistics, or any number not present in the "
    "facts. Be concise, concrete, and specific. Return output that exactly matches the "
    "requested schema."
)

_TASK: dict[str, str] = {
    "draft_recap": (
        "Classify this team's draft strategy and write a recap. strategy_label and "
        "secondary_label MUST come from the allowed enum. summary ≤120 words. key_values "
        "= picks taken well after their ADP (positive value_delta); key_reaches = picks "
        "taken well before ADP (negative value_delta). If every pick is autodrafted, use "
        "Autodraft/Absent."
    ),
    "league_brief": (
        "Assess how exploitable this league is for the team with is_me=true. Set "
        "difficulty_tier and write a short narrative grounded PRIMARILY in each team's "
        "edge_index_score (the full SPEC §6 Edge Index) and edge_index_verdict, plus records "
        "and autodraft flags; give ~3 concrete exploit_plan bullets. legacy_edge_score is a "
        "deprecated within-league proxy — you may mention it only as secondary context, never "
        "as the primary signal."
    ),
    "advantage_verdict": (
        "In plain English, explain whether the is_me team is advantaged in this league and "
        "why. Lead with edge_index_score (the full Edge Index = 0.5·MyEdge + 0.5·"
        "LeagueSoftness) and edge_index_verdict, using my_edge_components and "
        "league_softness_components to explain the drivers; also weigh record, standing, "
        "points, and playoff_odds. legacy_edge_score is a deprecated within-league proxy — "
        "mention it only as secondary context. Set verdict_label, write one paragraph, and "
        "give the single highest_leverage_move."
    ),
    "weekly_recap": (
        "Write a short league newsletter recap of this week's matchups: a headline and body "
        "grounded only in the provided game scores (use winner/loser/margin). Write luck_notes "
        "ONLY from week_all_play and season_all_play — e.g. a team that lost despite a top "
        "all-play week, or a big season luck_delta; do not compute all-play yourself. Write "
        "waiver_highlights ONLY from the transactions list (team, type, player_in, player_out, "
        "bid). If transactions is empty, call it a quiet transaction week — do NOT invent any "
        "moves, players, or numbers not present in the facts."
    ),
    "trade_finder": (
        "Given my positional counts vs the opponent's, propose 1-2 trades that address a "
        "positional surplus of mine for a deficit (and vice versa). Advisory only."
    ),
}


def compute_input_hash(kind: str, model: str, facts: dict) -> str:
    payload = json.dumps(
        {"kind": kind, "model": model, "schema_version": SCHEMA_VERSION, "facts": facts},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class AiService:
    def __init__(self, session: Session, client: LlmClient | None = None):
        self.session = session
        self._injected = client is not None
        self.client = client
        self._api_key = get_settings().anthropic_api_key

    @property
    def enabled(self) -> bool:
        return self._injected or bool(self._api_key)

    def _ensure_client(self) -> LlmClient:
        if self.client is None:
            if not self._api_key:
                raise AiDisabledError()
            self.client = AnthropicLlmClient(self._api_key)
        return self.client

    # ---- persistence helpers ----------------------------------------------
    def _find_by_hash(self, league_id: int, kind: str, input_hash: str) -> AiReport | None:
        return self.session.scalar(
            select(AiReport).where(
                AiReport.league_id == league_id,
                AiReport.kind == kind,
                AiReport.input_hash == input_hash,
            )
        )

    def latest(self, league_id: int, kind: str) -> AiReport | None:
        return self.session.scalar(
            select(AiReport)
            .where(AiReport.league_id == league_id, AiReport.kind == kind)
            .order_by(AiReport.created_at.desc(), AiReport.id.desc())
        )

    def all_reports(self, league_id: int, kind: str) -> list[AiReport]:
        return list(
            self.session.scalars(
                select(AiReport)
                .where(AiReport.league_id == league_id, AiReport.kind == kind)
                .order_by(AiReport.created_at.desc(), AiReport.id.desc())
            )
        )

    def _latest_where_content(
        self, league_id: int, kind: str, key: str, value: object
    ) -> AiReport | None:
        """Newest stored report of `kind` whose content_json[key] == value (Phase 20/21).

        The scope key (week / opponent_team_id) is persisted in content_json via generate's
        `extra`, so no schema column is needed. all_reports is newest-first, so the first match
        is the newest for that scope — never another scope's report the way plain `latest(kind)`
        would. Legacy reports lacking the key are skipped (never returned for a scope)."""
        for row in self.all_reports(league_id, kind):
            if (row.content_json or {}).get(key) == value:
                return row
        return None

    def latest_for_week(self, league_id: int, kind: str, week: int) -> AiReport | None:
        """Newest stored report for a specific week (Phase 20)."""
        return self._latest_where_content(league_id, kind, "week", week)

    def latest_for_opponent(
        self, league_id: int, kind: str, opponent_team_id: int
    ) -> AiReport | None:
        """Newest stored report for a specific trade-finder opponent (Phase 21)."""
        return self._latest_where_content(league_id, kind, "opponent_team_id", opponent_team_id)

    # ---- generation --------------------------------------------------------
    def generate(
        self,
        *,
        kind: str,
        scope: str,
        league_id: int,
        model: str,
        facts: dict,
        force: bool = False,
        extra: dict | None = None,
    ) -> dict:
        """Return the report content dict (cached when inputs unchanged)."""
        if not self.enabled:
            raise AiDisabledError()
        schema = SCHEMA_BY_KIND[kind]
        input_hash = compute_input_hash(kind, model, facts)

        if not force:
            hit = self._find_by_hash(league_id, kind, input_hash)
            if hit is not None:
                return hit.content_json or {}

        client = self._ensure_client()
        raw = client.complete_json(
            model=model,
            system=_SYSTEM,
            user=f"{_TASK.get(kind, '')}\n\nFACTS:\n{json.dumps(facts, default=str)}",
            schema=schema,
            max_tokens=MAX_TOKENS,
        )
        try:
            validated = schema.model_validate(raw)  # SPEC: validate before storing
        except ValidationError as exc:
            raise AiError(f"model output failed {kind} schema validation") from exc
        content = validated.model_dump(mode="json")
        if extra:
            content = {**content, **extra}

        row = self._find_by_hash(league_id, kind, input_hash)
        if row is None:
            row = AiReport(league_id=league_id, kind=kind, scope=scope, input_hash=input_hash)
            self.session.add(row)
        row.model = model
        row.content_json = content
        row.created_at = datetime.now(UTC)
        self.session.flush()
        return content
