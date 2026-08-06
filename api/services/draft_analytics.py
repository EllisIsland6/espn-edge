"""Portfolio draft ADP + strategy read models (Phase 23)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..edge_config import STRATEGY_LABELS
from ..models import AdpSnapshot, DraftPick, League, Metric, Player, Team
from .ffc_adp import (
    FfcPlayer,
    dedupe_ffc_requests,
    latest_ffc_snapshot,
    normalize_team,
    parse_ffc_players,
    player_identity_key,
)
from .metrics import (
    DRAFT_ADP_SOURCE_DISAGREEMENT,
    DRAFT_VALUE_CAPTURE_ESPN,
    DRAFT_VALUE_CAPTURE_FFC,
    EDGE_INDEX_SCORE,
    StrategyPick,
    classify_draft_strategy,
    read_draft_strategies,
)
from .portfolio_filters import PortfolioFilters, filtered_league_ids

_PRIMARY_STRATEGY_LABELS = tuple(
    label
    for label in STRATEGY_LABELS
    if label in {"Zero RB", "Hero RB", "Robust RB", "Balanced/BPA", "Autodraft/Absent"}
)
_SECONDARY_STRATEGY_LABELS = tuple(
    label for label in STRATEGY_LABELS if label in {"Anchor WR", "Elite TE", "Late-Round QB"}
)


def _pct(numer: float, denom: float) -> float:
    return round(numer / denom * 100.0, 1) if denom else 0.0


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _mean_raw(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _median(values: list[float]) -> float | None:
    return _round(float(median(values))) if values else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * pct / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return round(ordered[low], 3)
    weight = rank - low
    return round(ordered[low] * (1.0 - weight) + ordered[high] * weight, 3)


def _percentile_rank(value: float | None, samples: list[float]) -> float | None:
    if value is None or not samples:
        return None
    less = sum(1 for sample in samples if sample < value)
    equal = sum(1 for sample in samples if sample == value)
    return round((less + equal * 0.5) / len(samples) * 100.0, 1)


def _stddev(values: list[float]) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round(math.sqrt(variance), 3)


def _ci95(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    mean = sum(values) / len(values)
    stddev = _stddev(values)
    if stddev is None:
        return None, None
    margin = 1.96 * stddev / math.sqrt(len(values))
    return round(mean - margin, 3), round(mean + margin, 3)


def _intervals_overlap(rows: list[dict]) -> bool:
    intervals = [
        (row["ci95_low"], row["ci95_high"])
        for row in rows
        if row["ci95_low"] is not None and row["ci95_high"] is not None
    ]
    for index, (low, high) in enumerate(intervals):
        for other_low, other_high in intervals[index + 1 :]:
            if max(low, other_low) <= min(high, other_high):
                return True
    return False


def _source_label(source: str) -> str:
    return "vs. draft-time ADP" if source == "espn" else "vs. current market ADP"


def _league_scope(session: Session, filters: PortfolioFilters) -> list[League]:
    ids = filtered_league_ids(session, filters)
    if not ids:
        return []
    return list(session.scalars(select(League).where(League.id.in_(ids))))


def _my_teams(session: Session, leagues: list[League]) -> list[Team]:
    if not leagues:
        return []
    return list(
        session.scalars(
            select(Team).where(
                Team.league_id.in_([league.id for league in leagues]),
                Team.is_me.is_(True),
            )
        )
    )


def _fresh_snapshot(snapshot: AdpSnapshot | None) -> bool:
    if snapshot is None:
        return False
    pulled_at = snapshot.pulled_at
    if pulled_at.tzinfo is None:
        pulled_at = pulled_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - pulled_at < timedelta(hours=get_settings().ffc_adp_ttl_hours)


def _source_sets(session: Session, leagues: list[League], season: int) -> list[dict]:
    out = []
    for req in dedupe_ffc_requests(leagues, season):
        snap = latest_ffc_snapshot(session, req.used_format, req.used_teams, req.year)
        out.append(
            {
                "requested_format": req.requested_format,
                "requested_teams": req.requested_teams,
                "used_format": req.used_format,
                "used_teams": req.used_teams,
                "year": req.year,
                "exact_match": req.exact_match,
                "pulled_at": snap.pulled_at if snap else None,
                "stale": (not _fresh_snapshot(snap)) if snap else True,
            }
        )
    return out


def _ffc_snapshot_players(
    session: Session, leagues: list[League], season: int
) -> dict[tuple[str, str, str], FfcPlayer]:
    players: dict[tuple[str, str, str], FfcPlayer] = {}
    for req in dedupe_ffc_requests(leagues, season):
        snapshot = latest_ffc_snapshot(session, req.used_format, req.used_teams, req.year)
        if snapshot is None:
            continue
        for player in parse_ffc_players(snapshot.payload_json or {}):
            players.setdefault(
                player_identity_key(player.name, player.position, player.nfl_team), player
            )
    return players


def build_draft_adp(session: Session, filters: PortfolioFilters) -> dict:
    leagues = _league_scope(session, filters)
    season = filters.season or get_settings().season
    teams = _my_teams(session, leagues)
    team_ids = [team.id for team in teams]
    league_by_id = {league.id: league for league in leagues}
    if not team_ids:
        return {
            "season": season,
            "teams_in_scope": 0,
            "coverage": {
                "teams_in_scope": 0,
                "auction_teams": 0,
                "keeper_picks": 0,
                "eligible_picks": 0,
                "picks_with_espn_adp": 0,
                "picks_with_ffc_adp": 0,
                "picks_without_espn_adp": 0,
                "picks_without_ffc_adp": 0,
                "ffc_matched_players": 0,
                "ffc_unmatched_players": 0,
                "ffc_snapshot_excluded_players": 0,
                "ffc_resolution_failures": 0,
            },
            "source_sets": _source_sets(session, leagues, season),
            "teams": [],
            "by_round": [],
            "by_position": [],
            "biggest_values": [],
            "biggest_reaches": [],
            "unmatched_players": [],
        }

    rows = session.execute(
        select(
            League.id,
            League.name,
            League.draft_type,
            Team.id,
            Team.name,
            DraftPick.overall,
            DraftPick.round,
            DraftPick.keeper,
            DraftPick.value_delta,
            DraftPick.adp_at_draft,
            Player.espn_player_id,
            Player.name,
            Player.position,
            Player.nfl_team,
            Player.ffc_id,
            Player.ffc_adp,
        )
        .join(League, League.id == DraftPick.league_id)
        .join(Team, Team.id == DraftPick.team_id)
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(DraftPick.team_id.in_(team_ids))
    ).all()
    auction_league_ids = {
        league.id for league in leagues if (league.draft_type or "").upper() == "AUCTION"
    }
    eligible = [
        row
        for row in rows
        if row[0] not in auction_league_ids and not bool(row[7]) and row[5] is not None
    ]

    metrics_rows = session.execute(
        select(Metric.team_id, Metric.key, Metric.value_float).where(
            Metric.team_id.in_(team_ids),
            Metric.key.in_(
                (
                    DRAFT_VALUE_CAPTURE_ESPN,
                    DRAFT_VALUE_CAPTURE_FFC,
                    DRAFT_ADP_SOURCE_DISAGREEMENT,
                )
            ),
            Metric.week.is_(None),
        )
    ).all()
    by_team_metric: dict[int, dict[str, float]] = defaultdict(dict)
    for team_id, key, value in metrics_rows:
        if team_id is not None and value is not None:
            by_team_metric[team_id][key] = value
    team_by_id = {team.id: team for team in teams}
    team_rows = []
    for team in teams:
        league = league_by_id.get(team.league_id)
        values = by_team_metric.get(team.id, {})
        team_rows.append(
            {
                "league_id": team.league_id,
                "league_name": league.name if league else None,
                "team_id": team.id,
                "team_name": team.name,
                "draft_type": league.draft_type if league else None,
                "draft_value_capture_espn": values.get(DRAFT_VALUE_CAPTURE_ESPN),
                "draft_value_capture_ffc": values.get(DRAFT_VALUE_CAPTURE_FFC),
                "draft_adp_source_disagreement": values.get(DRAFT_ADP_SOURCE_DISAGREEMENT),
            }
        )
    median_fields = (
        "draft_value_capture_espn",
        "draft_value_capture_ffc",
        "draft_adp_source_disagreement",
    )
    medians = {
        field: _median([float(row[field]) for row in team_rows if row[field] is not None])
        for field in median_fields
    }
    for row in team_rows:
        for field in median_fields:
            median_value = medians[field]
            row[f"{field}_portfolio_median"] = median_value
            row[f"{field}_vs_portfolio_median"] = (
                _round(float(row[field]) - median_value)
                if row[field] is not None and median_value is not None
                else None
            )

    def grouped(field_index: int) -> list[dict]:
        grouped_values: dict[tuple[str, str], list[float]] = defaultdict(list)
        grouped_team_values: dict[tuple[str, str, int], list[float]] = defaultdict(list)
        counts: Counter[tuple[str, str]] = Counter()
        for row in eligible:
            label = str(row[field_index] or "?")
            counts[("espn", label)] += 1
            counts[("ffc", label)] += 1
            if row[8] is not None:
                delta = float(row[8])
                grouped_values[("espn", label)].append(delta)
                grouped_team_values[("espn", label, row[3])].append(delta)
            if row[15] is not None:
                delta = float(row[15]) - float(row[5])
                grouped_values[("ffc", label)].append(delta)
                grouped_team_values[("ffc", label, row[3])].append(delta)

        def team_means(source: str, label: str) -> list[float]:
            return [
                sum(team_values) / len(team_values)
                for (s, b, _team_id), team_values in grouped_team_values.items()
                if s == source and b == label
            ]

        return [
            {
                "source": source,
                "source_label": _source_label(source),
                "bucket": label,
                "avg_delta": _mean(values),
                "picks_with_adp": len(values),
                "eligible_picks": counts[(source, label)],
                "portfolio_median_delta": _median(team_means(source, label)),
                "portfolio_p25_delta": _percentile(team_means(source, label), 25),
                "portfolio_p75_delta": _percentile(team_means(source, label), 75),
                "mean_percentile": _percentile_rank(
                    sum(values) / len(values) if values else None,
                    team_means(source, label),
                ),
            }
            for (source, label), values in sorted(grouped_values.items())
        ]

    pick_rows = []
    for row in eligible:
        league_id, league_name, draft_type, team_id, team_name = row[:5]
        overall, round_no, _keeper, value_delta, adp_at_draft = row[5:10]
        player_id, player_name, position, nfl_team, _ffc_id, ffc_adp = row[10:16]
        for source, delta, adp in (
            ("espn", value_delta, adp_at_draft),
            ("ffc", (ffc_adp - overall if ffc_adp is not None else None), ffc_adp),
        ):
            if delta is None:
                continue
            pick_rows.append(
                {
                    "source": source,
                    "source_label": _source_label(source),
                    "league_id": league_id,
                    "league_name": league_name,
                    "team_id": team_id,
                    "team_name": (
                        team_by_id.get(team_id).name if team_id in team_by_id else team_name
                    ),
                    "espn_player_id": player_id,
                    "player_name": player_name,
                    "position": position,
                    "nfl_team": normalize_team(nfl_team),
                    "overall": overall,
                    "round": round_no,
                    "adp": adp,
                    "delta": round(float(delta), 1),
                    "draft_type": draft_type,
                }
            )
    biggest_values = sorted(
        pick_rows, key=lambda row: (-row["delta"], row["overall"] or 9999)
    )[:10]
    biggest_reaches = sorted(
        pick_rows, key=lambda row: (row["delta"], row["overall"] or 9999)
    )[:10]

    ffc_player_ids = {row[10] for row in eligible if row[10] is not None}
    snapshot_players = _ffc_snapshot_players(session, leagues, season)
    unmatched_players = []
    for row in eligible:
        if row[10] is None or row[15] is not None:
            continue
        source_player = snapshot_players.get(player_identity_key(row[11], row[12], row[13]))
        if source_player is None:
            reason = "not_in_snapshot"
        elif source_player.adp is None:
            reason = "missing_adp"
        else:
            reason = "resolution_failed"
        unmatched_players.append(
            {
                "espn_player_id": row[10],
                "player_name": row[11],
                "position": row[12],
                "nfl_team": normalize_team(row[13]),
                "reason": reason,
            }
        )
    # De-dupe unmatched by player id.
    unmatched_by_id = {row["espn_player_id"]: row for row in unmatched_players}
    exclusion_positions = Counter(
        row["position"] or "?"
        for row in unmatched_by_id.values()
        if row["reason"] == "not_in_snapshot"
    )
    snapshot_exclusions = sum(exclusion_positions.values())
    resolution_failures = sum(
        1 for row in unmatched_by_id.values() if row["reason"] == "resolution_failed"
    )
    coverage_notes = [
        "ESPN = vs. draft-time ADP.",
        "FFC = vs. current market ADP.",
        "Auction teams and keeper picks are excluded from pick-number ADP means.",
    ]
    if snapshot_exclusions:
        position_summary = ", ".join(
            f"{count} {position}" for position, count in sorted(exclusion_positions.items())
        )
        coverage_notes.append(
            "FFC snapshot exclusions: "
            f"{snapshot_exclusions} drafted players are not listed in the current response "
            f"({position_summary})."
        )
    if resolution_failures:
        coverage_notes.append(
            f"FFC resolver failures: {resolution_failures} listed players were not applied."
        )
    return {
        "season": season,
        "teams_in_scope": len(teams),
        "coverage": {
            "teams_in_scope": len(teams),
            "auction_teams": sum(1 for team in teams if team.league_id in auction_league_ids),
            "keeper_picks": sum(1 for row in rows if bool(row[7])),
            "eligible_picks": len(eligible),
            "picks_with_espn_adp": sum(1 for row in eligible if row[8] is not None),
            "picks_with_ffc_adp": sum(1 for row in eligible if row[15] is not None),
            "picks_without_espn_adp": sum(1 for row in eligible if row[8] is None),
            "picks_without_ffc_adp": sum(1 for row in eligible if row[15] is None),
            "ffc_matched_players": len(
                {row[10] for row in eligible if row[15] is not None}
            ),
            "ffc_unmatched_players": len(
                ffc_player_ids - {row[10] for row in eligible if row[15] is not None}
            ),
            "ffc_snapshot_excluded_players": snapshot_exclusions,
            "ffc_resolution_failures": resolution_failures,
            "notes": coverage_notes,
        },
        "source_sets": _source_sets(session, leagues, season),
        "teams": team_rows,
        "by_round": grouped(6),
        "by_position": grouped(12),
        "biggest_values": biggest_values,
        "biggest_reaches": biggest_reaches,
        "unmatched_players": list(unmatched_by_id.values()),
    }


def _trigger_pick_details(
    session: Session,
    league_id: int,
    team_id: int,
    triggers: dict[str, list[int]],
) -> dict[str, list[dict]]:
    if not triggers:
        return {}
    overalls = {overall for values in triggers.values() for overall in values}
    rows = session.execute(
        select(DraftPick.overall, Player.name, Player.position, Player.nfl_team)
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(
            DraftPick.league_id == league_id,
            DraftPick.team_id == team_id,
            DraftPick.overall.in_(overalls),
        )
    ).all()
    by_overall = {
        overall: {
            "overall": overall,
            "player_name": name,
            "position": position,
            "nfl_team": normalize_team(nfl_team),
        }
        for overall, name, position, nfl_team in rows
    }
    return {
        label: [by_overall[overall] for overall in values if overall in by_overall]
        for label, values in triggers.items()
    }


def build_strategies(session: Session, filters: PortfolioFilters) -> dict:
    leagues = _league_scope(session, filters)
    teams = _my_teams(session, leagues)
    league_by_id = {league.id: league for league in leagues}
    auction_league_ids = {
        league.id for league in leagues if (league.draft_type or "").upper() == "AUCTION"
    }

    metric_rows: dict[int, object] = {}
    for league in leagues:
        for row in read_draft_strategies(session, league.id):
            metric_rows[row.team_id] = row
    edge_rows = dict(
        session.execute(
            select(Metric.team_id, Metric.value_float).where(
                Metric.team_id.in_([team.id for team in teams]) if teams else False,
                Metric.key == EDGE_INDEX_SCORE,
                Metric.week.is_(None),
            )
        ).all()
    )

    out_rows = []
    for team in teams:
        league = league_by_id.get(team.league_id)
        if league is None:
            continue
        row = metric_rows.get(team.id)
        if row is None:
            continue
        trigger_details = _trigger_pick_details(session, league.id, team.id, row.triggers)
        out_rows.append(
            {
                "league_id": league.id,
                "league_name": league.name,
                "team_id": team.id,
                "team_name": team.name,
                "draft_type": league.draft_type,
                "primary_label": row.primary_label,
                "primary_confidence": row.primary_confidence,
                "secondary_label": row.secondary_label,
                "secondary_confidence": row.secondary_confidence,
                "edge_index_score": edge_rows.get(team.id),
                "triggering_picks": trigger_details,
            }
        )

    primary_counts = Counter(row["primary_label"] for row in out_rows)
    secondary_counts = Counter(row["secondary_label"] for row in out_rows if row["secondary_label"])
    denom = len(out_rows)
    edge_by_primary: dict[str, list[float]] = defaultdict(list)
    for row in out_rows:
        if row["edge_index_score"] is not None:
            edge_by_primary[row["primary_label"]].append(row["edge_index_score"])

    keeper_picks = session.scalar(
        select(func.count())
        .select_from(DraftPick)
        .join(Team, Team.id == DraftPick.team_id)
        .where(
            Team.id.in_([team.id for team in teams]) if teams else False,
            DraftPick.keeper.is_(True),
        )
    ) or 0
    edge_summaries = []
    for label in _PRIMARY_STRATEGY_LABELS:
        values = edge_by_primary[label]
        mean_raw = _mean_raw(values)
        ci_low, ci_high = _ci95(values)
        edge_summaries.append(
            {
                "label": label,
                "mean_edge_index_score": _mean(values),
                "mean_edge_index_score_unrounded": mean_raw,
                "stddev_edge_index_score": _stddev(values),
                "ci95_low": ci_low,
                "ci95_high": ci_high,
                "teams_with_edge_index": len(values),
            }
        )
    uncertainty_note = (
        "Differences are not distinguishable at this sample: at least two 95% "
        "confidence intervals overlap."
        if _intervals_overlap(edge_summaries)
        else "Uncertainty is shown per label; small groups still carry high variance."
    )
    return {
        "season": filters.season or get_settings().season,
        "teams_in_scope": len(teams),
        "coverage": {
            "teams_in_scope": len(teams),
            "qualifying_teams": denom,
            "auction_teams": sum(1 for team in teams if team.league_id in auction_league_ids),
            "keeper_picks": keeper_picks,
            "missing_strategy_teams": len(teams) - denom,
            "notes": [
                "Auction teams are excluded from the snake-draft strategy classifier.",
                "Keeper picks are excluded from strategy triggers.",
            ],
        },
        "primary_distribution": [
            {
                "label": label,
                "count": primary_counts[label],
                "pct": _pct(primary_counts[label], denom),
            }
            for label in _PRIMARY_STRATEGY_LABELS
        ],
        "secondary_distribution": [
            {
                "label": label,
                "count": secondary_counts[label],
                "pct": _pct(secondary_counts[label], denom),
            }
            for label in _SECONDARY_STRATEGY_LABELS
        ],
        "mean_edge_index_by_primary": edge_summaries,
        "comparison_note": (
            "Strategy/Edge Index comparisons are descriptive, not causal; strategy is "
            "confounded with draft slot, league, and opponent quality."
        ),
        "uncertainty_note": uncertainty_note,
        "teams": out_rows,
    }


def classify_current_team_without_persisting(
    session: Session, league: League, team: Team
) -> dict | None:
    """Utility for audits/sanity tables; does not write metrics."""
    if (league.draft_type or "").upper() == "AUCTION":
        return None
    rows = session.execute(
        select(
            DraftPick.overall,
            Player.position,
            DraftPick.keeper,
            DraftPick.autodraft,
            DraftPick.espn_player_id,
            Player.name,
        )
        .join(Player, Player.espn_player_id == DraftPick.espn_player_id, isouter=True)
        .where(DraftPick.league_id == league.id, DraftPick.team_id == team.id)
        .order_by(DraftPick.overall)
    ).all()
    picks = [
        StrategyPick(
            overall=overall,
            position=position,
            keeper=bool(keeper),
            autodraft=bool(autodraft),
            espn_player_id=player_id,
            player_name=name,
        )
        for overall, position, keeper, autodraft, player_id, name in rows
    ]
    result = classify_draft_strategy(
        picks,
        league_size=int(league.size or 1),
        team_autodrafted=bool(team.autodrafted),
        auction=False,
    )
    if result is None:
        return None
    return {
        "league_id": league.id,
        "league_name": league.name,
        "team_id": team.id,
        "team_name": team.name,
        "primary_label": result.primary.label,
        "primary_confidence": result.primary.confidence,
        "secondary_label": result.secondary.label if result.secondary else None,
        "secondary_confidence": result.secondary.confidence if result.secondary else None,
        "primary_triggers": list(result.primary.trigger_overalls),
        "secondary_triggers": list(result.secondary.trigger_overalls) if result.secondary else [],
        "keeper_picks_excluded": result.excluded_keepers,
    }
