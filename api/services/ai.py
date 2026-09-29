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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_config import MAX_TOKENS, SCHEMA_VERSION
from ..ai_schemas import SCHEMA_BY_KIND
from ..config import get_settings
from ..models import AiReport
from .recovery import assert_recovery_write_allowed
from .spend import (
    Reservation,
    SpendLedger,
    SpendLedgerError,
    TokenUsage,
    actual_micro_usd,
)

log = logging.getLogger("espn.ai")


class AiDisabledError(RuntimeError):
    """AI requested but no API key configured. Callers should guard on `enabled`."""


# Marks a response the spend bound served from cache instead of generating. Two
# values so the caller can tell an exact-input cache hit from another scope's
# report standing in for one that was never generated.
CACHED_ONLY_KEY = "cached_only"


class AiError(RuntimeError):
    """Model call failed. Message is safe to surface (no secrets)."""


class LlmClient(Protocol):
    # Set by the client to the provider's own token counts for the call it just
    # made, or left None when the client cannot report them. A settle with no
    # usage can only restate the reservation, so this is what makes
    # reserve-then-settle two steps rather than one step written twice.
    last_usage: TokenUsage | None

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int,
        reservation: Reservation | None = None,
    ) -> dict:
        """`reservation` is the charge already recorded for THIS call.

        It is on the protocol rather than only at the call site because the
        contract rates a runtime flag check at one call site as procedural: a
        missed site reopens the hole and the guarantee scales with reviewer
        diligence. Carrying the reservation in the signature makes "call the
        model without having charged for it" something a caller has to write on
        purpose. `AnthropicLlmClient` refuses a required-but-absent one; see
        there for what this does and does not achieve.
        """
        ...


class AnthropicLlmClient:
    """Production client using the official SDK + structured outputs."""

    def __init__(self, api_key: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        # Read back by AiService to settle a reservation against the real token
        # counts. Without it a settle could only restate the reservation, which
        # would make "reserve then settle" a two-step way of doing one step.
        self.last_usage: TokenUsage | None = None

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int,
        reservation: Reservation | None = None,
    ) -> dict:
        import anthropic

        # The honest scope of this guard: it cannot know on its own whether a
        # bound was required, so it asks. What it buys is that the only client
        # that can actually spend money refuses to do so when the ledger says a
        # charge was mandatory and none arrived -- including from a future call
        # site that forgets, which is the failure mode a single call-site check
        # cannot cover.
        if reservation is None and _spend_bound_required():
            raise AiError("model call attempted without a spend reservation")

        # Logs and error messages never include the key, prompts, cookies, SWID,
        # espn_s2, or raw model output — only the exception class.
        try:
            resp = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                # These are short, grounded classifications. Sonnet 5 otherwise enables
                # adaptive thinking by default, which shares this output-token budget and
                # can consume it before the structured text block is emitted.
                thinking={"type": "disabled"},
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

        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.last_usage = TokenUsage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            )
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            stop_reason = getattr(resp, "stop_reason", None)
            content_types = [
                getattr(block, "type", "unknown") for block in getattr(resp, "content", [])
            ]
            log.warning(
                "anthropic returned no parsed output (stop_reason=%s, content_types=%s)",
                stop_reason,
                content_types,
            )
            if stop_reason == "max_tokens":
                raise AiError("model response reached its output token limit; try again")
            raise AiError("model returned no parsed output")
        try:
            return parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else dict(parsed)
        except Exception as exc:
            log.warning("could not serialize parsed output: %s", type(exc).__name__)
            raise AiError("model output could not be parsed") from exc


def _spend_bound_required() -> bool:
    """Always in `public_synthetic`; in `private_operator` once prices exist.

    Hosted mode is unconditional because the phase exists to make a hosted
    process structurally unable to authorize unbounded spend, and a bound a
    hosted deployment can switch off by leaving configuration empty is not
    structural. Private-operator mode is gated on configured prices because the
    contract states that private behaviour is unchanged, and a ledger with no
    configured rate can only refuse -- enforcing it by default would turn
    "unchanged" into "AI turned off". Configuring a rate is the operator opting
    in to their own ceiling.

    Review flagged that both clauses are off by default, so a deployment that
    sets neither has no bound. That is a deployment-configuration finding, not
    something this predicate can fix without contradicting the contract; it is
    recorded in E31.5 alongside the existing `api/Dockerfile` item.
    """
    settings = get_settings()
    return settings.is_public_synthetic or bool(settings.ai_price_micro_usd_per_mtok)


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
        "Propose 1-2 trades that swap a positional surplus of mine for a deficit (and vice "
        "versa), using the deterministic by_position surplus/deficit and each roster's players. "
        "HARD RULES: every i_give entry MUST be one exact player name from me.players, and every "
        "i_get entry MUST be one exact player name from opponent.players — copy the names "
        "verbatim. One player per list item; never combine names like 'A + B'. Never name a "
        "player not in the supplied rosters. roster_snapshot tells you the grounding: with "
        "grounding_source='lineup_snapshot' reason from that week's roster + proj_ros; with "
        "'drafted_roster' note these are drafted players who may have since moved. If "
        "projections_stale is true or projection_coverage is low, lean on positional depth "
        "(counts) and lower your confidence rather than citing exact projected points. If there "
        "is no sound trade, return an empty proposals list with a short note. Advisory only."
    ),
}


class AiSpendBlockedError(AiError):
    """The spend bound refused the call and no cached report exists.

    Deliberately terminal. The contract's criterion 8 is "cached-only generation
    with **no queued retry**": a retry against a monthly ceiling is a busy-wait
    until the calendar changes, and a queue is an unbounded backlog of calls that
    were refused for cost.

    It subclasses `AiError` so the existing routers turn it into the ordinary
    secret-free error envelope they already return for a failed generation,
    rather than a 500. A new sibling class would have needed every router touched
    to get the same outcome, and any router that was missed would have answered a
    refused-for-cost call with a stack trace.
    """


def compute_input_hash(kind: str, model: str, facts: dict) -> str:
    payload = json.dumps(
        {"kind": kind, "model": model, "schema_version": SCHEMA_VERSION, "facts": facts},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# --- Phase 22: trade-finder player-name validation -------------------------
def _norm_name(name: object) -> str:
    """Normalize a player name for harmless case/whitespace differences (exact match only —
    no substring/fuzzy matching)."""
    return " ".join(str(name).strip().lower().split()) if name else ""


def trade_name_tables(facts: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Normalized→canonical name maps for (my roster, opponent roster) from the supplied facts."""

    def table(players: list[dict]) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in players or []:
            nm = p.get("name")
            key = _norm_name(nm)
            if key:
                out.setdefault(key, nm)
        return out

    return table((facts.get("me") or {}).get("players")), table(
        (facts.get("opponent") or {}).get("players")
    )


def _resolve_names(items: object, table: dict[str, str]) -> list[str] | None:
    """Map each item to its canonical DB name via exact normalized match; None if any item
    is empty, not a real player on the supplied roster, or a combined string (which never
    matches a single canonical name)."""
    if not isinstance(items, list) or not items:
        return None
    out: list[str] = []
    for it in items:
        key = _norm_name(it)
        if not key or key not in table:
            return None
        out.append(table[key])
    return out


def validate_trade_proposals(
    content: dict, my_names: dict[str, str], opp_names: dict[str, str]
) -> dict:
    """Drop any proposal whose i_give/i_get names a player not on the supplied rosters; keep the
    valid ones (canonicalized). If none survive, return an empty proposals list + advisory note.
    Never fabricates players."""
    kept: list[dict] = []
    for p in content.get("proposals") or []:
        give = _resolve_names(p.get("i_give"), my_names)
        get = _resolve_names(p.get("i_get"), opp_names)
        if give is None or get is None:
            continue
        kept.append({**p, "i_give": give, "i_get": get})
    if not kept:
        return {
            **content,
            "proposals": [],
            "note": "No grounded trade found from the supplied rosters.",
        }
    return {**content, "proposals": kept}


def enrich_trade_player_refs(content: dict, facts: dict) -> dict:
    """Add response-only player references for portraits without changing stored AI reports.

    Trade proposal names have already been validated against these exact roster facts. The
    sidecars let clients render ESPN identities while keeping the LLM schema and cache stable.
    """

    def table(players: list[dict]) -> dict[str, dict]:
        refs: dict[str, dict] = {}
        for player in players or []:
            name = player.get("name")
            key = _norm_name(name)
            if key:
                refs[key] = {
                    "espn_player_id": player.get("espn_player_id"),
                    "name": name,
                    "position": player.get("position"),
                }
        return refs

    my_refs = table((facts.get("me") or {}).get("players"))
    opponent_refs = table((facts.get("opponent") or {}).get("players"))

    def resolve(names: list[str], refs: dict[str, dict]) -> list[dict]:
        return [
            refs.get(
                _norm_name(name),
                {"espn_player_id": None, "name": name, "position": None},
            )
            for name in names or []
        ]

    proposals = []
    for proposal in content.get("proposals") or []:
        proposals.append(
            {
                **proposal,
                "i_give_players": resolve(proposal.get("i_give") or [], my_refs),
                "i_get_players": resolve(proposal.get("i_get") or [], opponent_refs),
            }
        )
    return {**content, "proposals": proposals}


class AiService:
    def __init__(self, session: Session, client: LlmClient | None = None):
        self.session = session
        self._injected = client is not None
        self.client = client
        self._api_key = get_settings().anthropic_api_key

    @property
    def enabled(self) -> bool:
        return self._injected or bool(self._api_key)

    # ---- spend bound -------------------------------------------------------
    def _ledger(self) -> SpendLedger:
        """A ledger on its OWN session, never the caller's.

        It used to borrow `self.session`, so its `commit()` published whatever
        the request had pending and its `rollback()` on a ceiling refusal
        destroyed an `AiReport` the same request had already generated and
        flushed -- measured. The ledger needs its own transaction regardless:
        committing the charge before the model call is the entire design.
        """
        from ..db import SessionLocal

        return SpendLedger(session_factory=SessionLocal)

    @staticmethod
    def _spend_bound_required() -> bool:
        """Whether a reservation must succeed before the model may be called.

        Always in `public_synthetic`: the phase exists to make a hosted process
        structurally unable to authorize unbounded spend, and a bound that a
        hosted deployment can switch off is not structural.

        In `private_operator` only once the operator has configured model prices.
        The contract states that private-operator behaviour is unchanged, and a
        ledger with no configured rate can only refuse, so enforcing it by
        default would turn "unchanged" into "AI turned off". Configuring a rate
        is the operator opting in to their own ceiling.
        """
        return _spend_bound_required()

    @staticmethod
    def _input_token_bound(system: str, user: str) -> int:
        """A deliberate over-estimate of the prompt's token count.

        One token per CHARACTER. Real tokenizers emit far fewer than that for
        English and roughly one per character for CJK, so this never
        under-reserves for any script -- and under-reserving is the direction
        that lets a ceiling be stepped over. The reservation is reconciled to the
        provider's own counts at settle, so the over-estimate costs nothing but a
        briefly larger hold.
        """
        return len(system) + len(user)

    def _cached_only(
        self, league_id: int, kind: str, input_hash: str, reason: Exception
    ) -> dict:
        """Serve the cache, or fail. Never call the model, never queue a retry.

        The returned content carries `CACHED_ONLY_KEY`. Without it a refused
        `week=9` request returned the stored week-1 report with nothing marking
        it as substituted, and for a kind with no scope key there was no tell at
        all. The contract's forbidden-paths clause exempts "any strictly required
        cached-only trip indicator", so one was contemplated; this is it.
        """
        log.warning("ai spend bound refused a call: %s", type(reason).__name__)
        hit = self._find_by_hash(league_id, kind, input_hash)
        if hit is not None:
            return {**(hit.content_json or {}), CACHED_ONLY_KEY: "exact"}
        newest = self.latest(league_id, kind)
        if newest is not None:
            return {**(newest.content_json or {}), CACHED_ONLY_KEY: "substituted"}
        raise AiSpendBlockedError(
            "AI generation is unavailable within the current spend bound and "
            "no cached report exists for this league and kind"
        ) from reason

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

    # ---- reservation closure ----------------------------------------------
    def _settle(
        self, reservation: Reservation | None, model: str, usage: TokenUsage | None
    ) -> None:
        if reservation is None:
            return
        try:
            with self._ledger() as ledger:
                if usage is None:
                    # Nothing to reconcile against, so the conservative hold
                    # stands. Recording it as settled at the reserved amount is
                    # the honest statement: the charge is final and was never
                    # refined.
                    ledger.settle(reservation, reservation.reserved_micro_usd)
                else:
                    ledger.settle(reservation, actual_micro_usd(model, usage))
        except Exception as error:
            # The tokens are already spent and the content is already valid, so
            # a bookkeeping failure must not discard the caller's result. The
            # month stays charged at the worst case, which is the safe side.
            #
            # Not just `SpendLedgerError`: a client whose `last_usage` is not a
            # `TokenUsage` makes `actual_micro_usd` raise `AttributeError` here,
            # AFTER the call was billed, and that escaped as a 500 while the
            # entry stayed `reserved`. Anything that goes wrong in reconciling a
            # call that already happened leaves the conservative hold standing.
            log.warning("ai spend settle failed: %s", type(error).__name__)

    def _record_unknown(self, reservation: Reservation | None) -> None:
        if reservation is None:
            return
        try:
            with self._ledger() as ledger:
                ledger.record_unknown_spent(reservation)
        except Exception as error:
            log.warning("ai spend unknown-state write failed: %s", type(error).__name__)

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
        post_validate: Callable[[dict], dict] | None = None,
    ) -> dict:
        """Return the report content dict (cached when inputs unchanged).

        `post_validate` (Phase 22, used only by trade_finder) runs on the schema-validated
        content before persistence — e.g. to drop trade proposals naming players absent from
        the supplied rosters. Other report kinds pass None and are unaffected."""
        if not self.enabled:
            raise AiDisabledError()
        schema = SCHEMA_BY_KIND[kind]
        input_hash = compute_input_hash(kind, model, facts)

        if not force:
            hit = self._find_by_hash(league_id, kind, input_hash)
            if hit is not None:
                return hit.content_json or {}

        assert_recovery_write_allowed()
        client = self._ensure_client()
        user_prompt = f"{_TASK.get(kind, '')}\n\nFACTS:\n{json.dumps(facts, default=str)}"

        # One binding for the output budget and one for the prompt, used by BOTH
        # the reservation and the call. Review mutated the reservation to charge
        # for an empty prompt, and the call to request ten times the reserved
        # output budget, and the suite stayed green for both -- the under-reserve
        # direction this module's own docstring names as the one that lets a
        # ceiling be stepped over.
        output_budget = MAX_TOKENS
        reserved_input = self._input_token_bound(_SYSTEM, user_prompt)

        reservation: Reservation | None = None
        if self._spend_bound_required():
            # The caller's pending work is COMMITTED here, not rolled back.
            #
            # The ledger writes on its own connection, and on one SQLite file a
            # second connection cannot write while this one holds a write lock,
            # so the caller's transaction has to be closed before the charge can
            # be recorded independently -- and recording the charge before the
            # call is what makes a crashed call non-free. The previous shape
            # borrowed this session and rolled it BACK on a ceiling refusal,
            # which destroyed an `AiReport` the same request had already
            # generated. Nothing of THIS call is written yet at this point, so
            # the only thing committed is work that was already finished.
            self.session.commit()
            ledger = self._ledger()
            try:
                reservation = ledger.reserve(
                    kind=kind,
                    model=model,
                    input_tokens=reserved_input,
                    max_output_tokens=output_budget,
                )
            except SpendLedgerError as error:
                # Ceiling breach and ledger failure are the same outcome by
                # design: in both cases the spend bound could not be
                # established, so the call must not happen.
                return self._cached_only(league_id, kind, input_hash, error)
            finally:
                ledger.close()

        # Cleared first so a client that does not report usage cannot settle
        # this call against the previous call's token counts.
        try:
            client.last_usage = None
        except AttributeError:  # a client that does not accept the attribute
            pass
        try:
            raw = client.complete_json(
                model=model,
                system=_SYSTEM,
                user=user_prompt,
                schema=schema,
                max_tokens=output_budget,
                reservation=reservation,
            )
        except BaseException:
            # The call left this process. Whether the provider billed it is not
            # knowable here, so the month keeps the full reservation and the
            # entry says the spend is unknown rather than implying it was free.
            self._record_unknown(reservation)
            raise
        self._settle(reservation, model, getattr(client, "last_usage", None))
        try:
            validated = schema.model_validate(raw)  # SPEC: validate before storing
        except ValidationError as exc:
            raise AiError(f"model output failed {kind} schema validation") from exc
        content = validated.model_dump(mode="json")
        if post_validate is not None:
            content = post_validate(content)
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
