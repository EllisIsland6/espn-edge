# Test fixtures

Two sets, both consumed offline by the parser/sync tests (SPEC 12).

## Toy league (hand-authored)
- `public_league.json`, `players_pool.json` — a synthetic 4-team PPR league with a
  hand-computed truth table (see `tests/test_parse.py`, `tests/test_sync.py`). Used
  for the full sync-pipeline + idempotency tests.

## Real league (recorded + sanitized) — primary set
Recorded live on **2026-07-07** from ESPN league **17739342** ("EA Fantasy Football",
8-team PPR) via the verify CLI, then **field-projected and PII-sanitized**:

- `real_league_2025.json` — completed season: settings, members, teams (records/
  standings), full draft (168 picks incl. D/ST).
- `real_league_2026.json` — pre-draft: settings, teams (zeroed), empty draft.
- `real_matchups_2025.json` / `_2026.json` — `mMatchupScore` schedule (teamId + points).
- `real_boxscore_2025_wk1.json` — one week-1 matchup's rosters (starter/bench + points).
- `current_roster.json` / `pro_schedule_2026.json` — synthetic, PII-free current-period
  roster, projection, injury, matchup, opponent, and kickoff coverage for team-detail tests.
- `real_players_2025.json` / `_2026.json` — trimmed `kona_player_info` pool (ADP/rank).

**Sanitization** (recorder: kept out of the repo, in the session scratchpad):
- Only the fields our parsers read are projected in — no raw multi-MB blobs, so no
  hidden PII and small files.
- Member SWIDs, `memberId`, and team `owners` → deterministic fake GUIDs.
- The account's real SWID → the fixed fake `{FADE0000-…-000000000001}` so
  `detect_my_team` still resolves to the real team (**espn_team_id=1**).
- Member display/first/last names → `MemberN`; team names → `Team {id}` (real team
  names can embed people's names); any `*email*` field → `redacted@example.com`.
- NFL player names (`fullName`) are public and kept.

Verified: no real SWID, GUID, or `@` survives in any `real_*.json` (asserted by the
recorder and re-checked before commit). Numeric values are unchanged and were
eyeball-verified against the ESPN UI.
