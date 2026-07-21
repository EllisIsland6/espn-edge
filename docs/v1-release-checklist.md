# ESPN Edge — v1.0.0 Release Checklist

The single, ordered gate list for cutting **ESPN Edge v1.0.0**. ESPN Edge is a
**locally-run, single-user web app** (not a desktop package, not a hosted/multi-user
service). Work top to bottom; do not sign off a later section until every earlier one
passes. The live ESPN (§5) and Anthropic (§6) smokes each require **explicit human
approval** and are **not** executed as part of a normal release-candidate pass.

> **Tests are offline.** Every automated gate below uses the existing offline
> fakes/mocks — `FakeLlmClient` for the AI layer, recorded fixtures under
> `tests/fixtures/` for ESPN parsing/sync, and Playwright mocking `/api/*`. No gate
> in §2 makes a live ESPN or Anthropic call or spends API tokens.

## 1. Purpose and local single-user v1 scope
- **Purpose:** validate that the committed tree is a shippable v1.0.0 for the local,
  single-user use case, with truthful docs and coherent `1.0.0` version metadata.
- **In scope:** documentation accuracy, project-version metadata, offline gates,
  disposable fresh-install verification, and (separately, human-approved) bounded live
  smokes.
- **Out of scope for the release candidate:** analytics/metrics/formulas, schemas,
  models, migrations, sync, ESPN client/parsing/cache/auth, AI prompts/models/grounding,
  frontend features, and dependency upgrades. None of these change to ship v1.0.0.

## 2. Offline automated gates
All must pass locally, using the existing fakes/mocks. Note on network:
- The **automated tests** make no live ESPN or Anthropic calls — they run on
  fixtures/fakes (`tests/fixtures/`, `FakeLlmClient`) and Playwright `/api` mocks.
- **Dependency installation** (`npm ci`) may reach the npm registry or its local
  cache; that is the only step that may use the network.

```bash
# backend — empty creds prove no live ESPN/Anthropic dependency
PYTHONDONTWRITEBYTECODE=1 ANTHROPIC_API_KEY= ESPN_SWID= ESPN_S2= \
  .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/python -m ruff check api tests

# frontend — run in one subshell so the cd persists across all steps
(
  cd web
  npm ci                    # must exit 0 (mirrors CI)
  npm run lint              # tsc --noEmit
  npm run build
  npm run e2e               # Playwright smoke; mocks /api, no ESPN/Anthropic
)
```

Expected: **182 pytest passed**, ruff clean, tsc clean, build ok, **18 Playwright
passed**. Do not run `npm audit fix` — existing audit advisories are out of scope
(see §4 for the recorded audit ground truth).

## 3. Disposable fresh-install smoke
From a **clean, disposable checkout** (temp dir or fresh clone) with:
- **no** `.env`,
- **no** runtime `data/edge.db*` files, and
- **no** raw-cache payloads beyond the tracked `data/raw_cache/.gitkeep`:

1. `make setup` (installs api + web deps, writes `.env` with a generated `FERNET_KEY`).
2. `make dev` (uvicorn on `127.0.0.1:8000` + Vite on `127.0.0.1:5173`).
3. `curl http://127.0.0.1:8000/api/health` → `status: ok` + season + db path.
4. Load `http://127.0.0.1:5173` → Portfolio Board renders its empty state; no crash.
5. Confirm the documented command count in `README.md` matches reality.
6. Tear down the disposable checkout (no secrets or DB are committed).

## 4. Security and repository-hygiene gate
```bash
git diff --check                       # no whitespace/conflict markers
git status --short                     # only the intended files changed
```
- Verify no secrets are tracked: `.env`, `data/edge.db*`, `data/raw_cache/*` (except
  `.gitkeep`), Fernet/Anthropic keys, SWID/`espn_s2` cookies.
- Confirm localhost-only posture: `api_host` defaults to `127.0.0.1`; CORS allows only
  `localhost`/`127.0.0.1` on the web port.
- Confirm account API responses stay credential-safe (validation errors are redacted;
  `AccountOut` exposes only `id`, `label`, `status`, `created_at`).

### Dependency-audit ground truth (recorded, not remediated)
- Full `npm audit` (in `web/`) currently reports **2 vulnerabilities — 1 high and 1
  moderate** — all in the **Vite/esbuild development toolchain** (dev server / build),
  not shipped runtime code.
- `npm audit --omit=dev` currently reports **0 production vulnerabilities**.
- **Do not run `npm audit fix`** (or `--force`) as part of this release — a dependency
  upgrade is out of scope here.
- Accepting the development-server residual risk **or** authorizing a separate dependency
  upgrade to clear these advisories each requires **explicit human approval**.

## 5. Explicitly human-approved live ESPN smoke
**Do not run without explicit human approval. Uses real cookies + network — not part of
a normal RC pass.** Follow [docs/live-smoke.md](live-smoke.md):
- One **public** league via `make verify LEAGUE=<id>`; facts match the ESPN UI.
- One **private** league (real cookies via `.env`/hidden prompt, never argv): add
  account → add league → sync; optional `--cross-check` diff vs `espn-api`.
- Re-auth + diagnostics: an expired cookie flips the account to `needs_reauth`; `/reauth`
  restores `active`; `last_sync_ok`/`last_sync_error` persist honestly.
- Exports (CSV/JSON/XLSX) download and match the board.
- **Seasonal note (mid-2026):** an in-season 2026 league may not exist yet — use an
  available historical league and **explicitly defer** in-season-only checks. Never
  fabricate a seasonal result. Never print/commit/log cookies.

## 6. Explicitly human-approved bounded Anthropic smoke
**Do not run without explicit human approval. May incur Anthropic API charges — not part
of a normal RC pass.** Smallest useful smoke — **one standard-model + one bulk-model
report**:
- One **Trade Finder** (standard model) on a real opponent: structured output validates,
  provenance chip matches the snapshot week/source, every named player is on the real
  rosters (no invented players), cache hit on repeat, `force=true` regenerates.
- One **Weekly Recap** (bulk model, `claude-haiku-4-5`) on a completed week: luck/waiver
  grounding is real.
- With **no key**, the same endpoints return `enabled:false` (covered offline in §2).
- **Never print, commit, or log the API key.** Stop after these two calls.

## 7. Patch-then-repeat-all-gates loop
If any of §2–§6 fails: fix on a branch with the **smallest** change (docs, a focused
offline test, or a small release-blocking fix — never new features), then **re-run all of
§2** (and any affected later section) from the top. No partial-gate sign-off.

## 8. Final release-readiness and green-CI verification
- Worktree clean; `main == origin/main`.
- The final commit is pushed and its **GitHub CI run is green on both jobs**
  (`backend (pytest + ruff)` and `frontend (lint + build + e2e)`) — matched by the exact
  commit SHA, not an older run.
- §2–§4 all pass; §5–§6 either passed or were explicitly deferred with human sign-off.

## 9. Tag / GitHub release — separate, human-approved, later step
Creating the annotated **`v1.0.0`** tag and the GitHub release is a **separate operation
that requires explicit human approval** and happens **only after §8 is fully green**. It
is **not** performed by the release-candidate implementation session. Suggested (later):
`git tag -a v1.0.0 -m "ESPN Edge v1.0.0"` then push the tag and draft the release.

## 10. Post-v1 backlog pointer
Deferred beyond v1.0.0 (do not pull into the release): `waiver_capture` MyEdge component,
a stronger opponent-activity feed, cross-league p5/p95 normalization, legacy `edge_score`
retirement, a true current-roster (`mRoster`) snapshot, weekly-recap history/UI,
scheduler wiring, a Settings/weights screen, desktop packaging, and hosted multi-user
architecture.
