#!/usr/bin/env bash
#
# Build the deployable image and assert the things that cannot be asserted
# without building it. Run this before `docker push`, on a machine where
# `docker build` can reach a registry.
#
# WHY THIS FILE EXISTS
# --------------------
# The image was authored in an environment with no reachable container
# registry (`registry-1.docker.io` denied by egress policy), so every claim
# about the BUILT image was reasoning, not measurement. Reasoning already got
# one of them wrong: `pip install .` omitted alembic, so the migrate task's
# `alembic upgrade head` would have exited `executable file not found in
# $PATH` while the service image built, started and reported healthy. The
# install layer was reproduced without Docker and the defect found, but
# "the built image has a working alembic" is still a claim about a build.
#
# So: the checks live here, and they run where the build runs. Each one names
# what fails and why it matters. The script exits non-zero on the first
# failure -- there is no point pushing an image that fails check 2.
#
#   ./ops/preflight-image.sh              # build and check
#   ./ops/preflight-image.sh my-tag       # same, with a tag you choose
#
set -euo pipefail

TAG="${1:-espn-edge:preflight}"
# The selected stack runs on a Graviton host (t4g.small, arm64), so the image
# that is checked must be the image that ships. The CDK draft targeted amd64
# Fargate; set PREFLIGHT_PLATFORM=linux/amd64 to check an image for it. On
# Apple silicon, arm64 is the native build and the checks run without
# emulation; amd64 is the slow one.
PLATFORM="${PREFLIGHT_PLATFORM:-linux/arm64}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '   \033[32mPASS\033[0m  %s\n' "$*"; }
die()  { printf '   \033[31mFAIL\033[0m  %s\n' "$*" >&2; exit 1; }

# `docker run` with no network and a writable /tmp only: nothing here should
# need either, and a check that quietly reached the internet would be
# measuring something else.
inimage() { docker run --rm --network none "$TAG" "$@"; }

command -v docker >/dev/null || die "no docker on PATH. This script has to run where the build runs."

say "0. the harness can say no"
# Every check below is a command that must succeed inside the image. If
# `docker run` reported success for a command that does not exist, all of them
# would pass while establishing nothing -- which is the exact failure shape
# this file was written for, one level up. So prove the detector works first,
# using the base image rather than one we have not built yet.
if docker run --rm --network none --entrypoint sh python:3.12-slim \
      -c 'command -v definitely-not-a-real-binary' >/dev/null 2>&1; then
    die "a nonexistent binary was reported present. The harness cannot fail, so no check below means anything."
fi
ok "a nonexistent binary is reported absent"

say "1. the image builds"
docker build --platform "$PLATFORM" -t "$TAG" "$HERE" || die "docker build failed; everything below is moot"
ok "built $TAG"

say "2. the migrate task's command exists (the defect this file was written for)"
inimage alembic --version >/dev/null 2>&1 \
    || die "no working \`alembic\` in the image. infra/app.py runs command=[\"alembic\",\"upgrade\",\"head\"]; that task would exit 'executable file not found in \$PATH' and the schema would never be created. Check that the Dockerfile installs \".[migrate]\" and not a bare \".\"."
ok "alembic $(inimage alembic --version 2>/dev/null | tr -d '\r')"
inimage python -c 'import alembic' || die "alembic is on PATH but not importable -- a broken install, worse than a missing one."
ok "alembic imports"
inimage alembic upgrade --help >/dev/null 2>&1 || die "\`alembic upgrade\` is not a valid subcommand in this alembic."
ok "\`alembic upgrade\` is a real subcommand"

say "3. the service's own CMD exists"
inimage uvicorn --version >/dev/null 2>&1 || die "no \`uvicorn\` in the image, which is the image's CMD."
ok "uvicorn present"

say "3b. the PostgreSQL driver the deploy document names is importable"
# The alembic defect one layer down: `postgresql+psycopg2://` was written in
# every deploy document and no driver was declared anywhere, so both tasks
# would have raised ModuleNotFoundError at engine creation. A driver is
# imported, not executed, so no command check sees it.
inimage python -c 'import psycopg2' \
    || die "psycopg2 does not import. DATABASE_URL is postgresql+psycopg2://, so the service and the migrate task both die at engine creation. pyproject.toml must declare psycopg2-binary."
ok "psycopg2 imports"

say "4. the frontend was actually built and copied"
inimage test -f /app/web-dist/index.html \
    || die "/app/web-dist/index.html is missing. api/main.py raises RuntimeError at import when STATIC_DIR has no index.html, so this would be a crash loop, not a missing page."
ok "web-dist/index.html present"
ASSETS=$(inimage sh -c 'ls /app/web-dist/assets 2>/dev/null | wc -l' | tr -d '[:space:]')
[ "${ASSETS:-0}" -ge 1 ] || die "web-dist/assets is empty: index.html exists but the bundle does not, so the page would load and render nothing."
ok "web-dist/assets holds $ASSETS file(s)"

say "5. the privilege claims the Dockerfile makes"
UID_IN=$(inimage id -u | tr -d '[:space:]')
[ "$UID_IN" = "10001" ] || die "running as uid $UID_IN, not 10001. The Dockerfile claims non-root."
ok "runs as uid 10001"
if inimage sh -c 'test -w /app/api/main.py'; then
    die "the app can write its own source. A chown -R on /app makes the runtime user the owner of its code; only /app/data should be writable."
fi
ok "cannot write its own source"
# ...but api/db.py mkdirs under /app/data unconditionally, PostgreSQL included.
# MEASURED: with /app/data absent or root-owned the process dies at import
# with PermissionError, so this is the one directory that must be writable.
inimage sh -c 'test -d /app/data && test -w /app/data' \
    || die "/app/data is missing or not writable by the runtime user. api/db.py runs db_file.parent.mkdir() and raw_cache_dir.mkdir() at import on every backend; the process would exit with PermissionError before serving."
ok "/app/data is writable (the one directory db.py needs)"

say "6. it imports, and refuses to start without the required settings"
# APP_MODE, TELEMETRY_ENABLED and TELEMETRY_REPORT_PATH are required with no
# default. That refusal is a feature (docs/aws-deploy.md s4), so check the
# refusal happens BEFORE checking the success -- a container that started
# without them would be the defect.
if docker run --rm --network none --entrypoint python "$TAG" -c 'import api.main' >/dev/null 2>&1; then
    die "the app imported with no APP_MODE. It is supposed to refuse: a hosted image that silently defaulted to private_operator would enable the ESPN provider and the credential decrypt path."
fi
ok "refuses to construct Settings with no APP_MODE"
docker run --rm --network none \
    -e APP_MODE=public_synthetic -e TELEMETRY_ENABLED=false \
    -e TELEMETRY_REPORT_PATH=/tmp/telemetry-report.md \
    --entrypoint python "$TAG" -c 'import api.main; print("imported")' >/dev/null \
    || die "the app does not import even with the required settings given."
ok "imports with the required settings"

say "7. it serves, and /api/health reports the backend"
CID=$(docker run -d --rm -p 18000:8000 \
    -e APP_MODE=public_synthetic -e TELEMETRY_ENABLED=false \
    -e TELEMETRY_REPORT_PATH=/tmp/telemetry-report.md \
    "$TAG")
cleanup() { docker stop "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

BODY=""
for _ in $(seq 1 30); do
    if BODY=$(curl -fsS --max-time 2 http://127.0.0.1:18000/api/health 2>/dev/null); then break; fi
    sleep 1
done
[ -n "$BODY" ] || { docker logs "$CID" 2>&1 | tail -30; die "/api/health never answered. Container logs above."; }
ok "/api/health answered"
printf '         %s\n' "$BODY"
case "$BODY" in
    *'"backend"'*) ok 'the response carries "backend"' ;;
    *) die 'the health response has no "backend" field; api/routers/health.py is not the version that reports it.' ;;
esac
case "$BODY" in
    *sqlite*) ok "backend is sqlite, as expected with no DATABASE_URL" ;;
    *) printf '   \033[33mNOTE\033[0m  backend is not sqlite -- this run picked up a DATABASE_URL from somewhere.\n' ;;
esac

# The SPA mount: `/` must serve the built index, and `/api/` must never fall
# through to it.
ROOT_CT=$(curl -fsS -o /dev/null -w '%{content_type}' --max-time 3 http://127.0.0.1:18000/ || true)
case "$ROOT_CT" in
    text/html*) ok "/ serves HTML (the SPA mount is live)" ;;
    *) die "/ returned content-type '${ROOT_CT:-nothing}', not HTML. STATIC_DIR is set but the mount is not serving." ;;
esac
NOPE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:18000/api/does-not-exist || true)
[ "$NOPE" = "404" ] || die "/api/does-not-exist returned $NOPE, not 404. If the SPA fallback is catching /api/ paths, every API typo silently returns the app shell."
ok "/api/ does not fall through to the SPA"

say "RESULT"
printf '   %d checks passed. \033[32mThis image is safe to push.\033[0m\n' "$PASS"
printf '   Not checked here: anything needing AWS. See docs/aws-deploy.md.\n\n'
