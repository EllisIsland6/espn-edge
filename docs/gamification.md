# Gamification presentation contract

This pass changes how existing metrics are presented. It does not change any
Edge Score, Edge Index, playoff, MyEdge, LeagueSoftness, draft, or lineup
formula.

## Metric snapshots

Historical scores did not exist before this pass, so `metric_snapshots` is a
new SQLite table:

| column | meaning |
| --- | --- |
| `id` | integer primary key |
| `league_id` | league FK, cascade delete |
| `team_id` | team FK, cascade delete |
| `batch_id` | dependency-free UUID token shared by one clean sync event |
| `key` | `edge_score` or `edge_index_score` |
| `period` | completed fantasy week; `0` before a completed week |
| `value_float` | persisted score from the existing metrics table |
| `recorded_at` | UTC write time |

`(league_id, batch_id, team_id, key)` is unique. Every clean sync appends a new
batch and existing events are never updated or deleted by sync. Momentum selects
the newest event from each distinct fantasy period, so same-period re-syncs are
retained for audit history without becoming extra momentum periods. Two different
periods with the same score still produce a real flat result.

Snapshot rows are written in a savepoint after a clean metrics recompute. An
interrupted batch rolls back in full and marks that sync with
`snapshots_failed`; the next sync can retry normally. Partial ESPN syncs do not
record history. Deleting a league cascades its snapshots, so deleting and
re-adding a league starts fresh even if SQLite reuses the numeric league ID.

This corrects the experimental table created by commits `2d6d16d`/`673bf6e`.
SQLite `create_all` cannot add `batch_id` or replace the old period uniqueness
constraint. If that table already exists, stop `make dev`, run `make db-reset`,
restart with `make dev`, then re-add/discover leagues and sync them. Do not run
against the old table; there is no in-place migration path for this local,
re-syncable data.

## Delta and streak rules

- `pending`: the current score is null. Old history is never shown as current.
- `first_sync`: fewer than two period snapshots exist. Delta is null.
- `up` / `down`: latest period minus previous period, rounded to one decimal.
- `flat`: the two latest period values are equal. It is not styled as a gain or
  loss.
- A streak counts consecutive non-zero movements in the current direction.
- A reversal resets the direction to a one-move streak.
- A flat period breaks the streak. It neither extends nor pauses momentum.
- The UI only renders a streak badge at two or more consecutive moves.

The Portfolio Board uses Edge Index momentum because Edge Index is already its
primary score. League Overview uses the legacy within-league Edge Score and its
matching momentum. The ring and accompanying number always come from the same
API value.

## Achievement thresholds

Achievements are presentation facts derived by the backend:

- `Sync healthy`: the most recent sync completed without errors.
- `Lineup 90%+`: lineup efficiency is at least `0.90` (inclusive).
- `Draft value`: persisted draft surplus is greater than `0.0`.
- `All-play 60%+`: all-play win rate is at least `0.60` (inclusive).

Missing inputs create no badge. The UI does not render disabled placeholders.

## Portfolio tiers

Tier membership uses the existing backend grade unchanged: A, B, C, D, F, then
Pending. Within a tier, rows sort by Edge Index descending, then league name,
then local league ID. The latter two rules make equal scores deterministic.
Filtering by account preserves account grouping and sorts each account's rows
with the same score/name/ID rule.

## Motion

Score rings transition from empty to their real value only when scored data
arrives. The ring's visible number, accessible label, and raw `data-value` all
come from the same API metric; this replaces the redundant flat score box rather
than inventing a second score display. AI Brief content uses one reveal animation
per distinct generated payload, remembered for the lifetime of the league detail
page. Regeneration reveals once again; unrelated renders and AI tab round-trips
do not replay cached content. The global `prefers-reduced-motion` rule disables
both animations, and the ring's state hook skips its tween entirely.
