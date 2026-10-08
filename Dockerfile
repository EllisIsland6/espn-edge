# The deployable image: one container, API + built frontend.
#
# WHY ONE CONTAINER
# -----------------
# The alternative is two services, two load-balancer target groups and a CORS
# configuration between them. For a single-operator deployment that is three
# more things to get wrong for no benefit: the frontend is static files, and
# the process that serves the API can serve static files.
#
# WHY NOT api/Dockerfile
# ----------------------
# That one pip-installs a HAND-COPIED duplicate of pyproject.toml's dependency
# list, so the image can drift from what CI tested while both report success
# (tests/test_container_dependencies.py pins them together for exactly that
# reason). This installs the project. The list cannot drift from itself.
#
# WHAT THIS IMAGE DOES NOT DO
# ---------------------------
# It does not run migrations on start. The application connects as a role with
# no DDL rights -- deliberately, see alembic/versions/0016 -- so it *cannot*
# migrate, and an entrypoint that tried would fail every deploy. Migrations
# run as a separate one-off task from this same image with the OWNER
# credentials:
#
#     alembic upgrade head
#
# See docs/aws-deploy.md. Keeping them apart is what makes the privilege split
# real rather than decorative.

# ---------------------------------------------------------------- frontend ---
FROM node:22-alpine AS web
WORKDIR /web
# The lockfile is not optional: `npm ci` requires it, and a glob that matches
# nothing would succeed here and fail confusingly later.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ----------------------------------------------------------------- runtime ---
FROM python:3.12-slim AS runtime

# Pinned to the interpreter CI uses. The suite has been run on 3.12 and on
# 3.14; 3.12 is what `.github/workflows/ci.yml` and this image share, so the
# tested combination is the deployed one.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# `api/` is copied before the install because setuptools needs the package
# present to find it (`[tool.setuptools.packages.find] include = ["api*"]`).
COPY pyproject.toml ./
COPY api ./api
# `".[migrate]"` and not a bare `.`.
#
# A bare `.` installs `[project] dependencies` only, and alembic is not one of
# them -- deliberately, since nothing under `api/` imports it. That left this
# image with no `alembic` module and no `alembic` binary, while the migrate
# task below is defined as `["alembic", "upgrade", "head"]`. Every deploy's
# migrate step would have exited `executable file not found in $PATH`, after
# the service image itself built and ran perfectly: the schema simply would
# never have been created.
#
# Measured, not reasoned: installing from a directory holding only
# `pyproject.toml` and `api/` -- exactly what this layer sees -- produced no
# `bin/alembic` and `ModuleNotFoundError: No module named 'alembic'`.
# tests/test_image_commands.py is what notices if it comes back.
RUN pip install --no-cache-dir ".[migrate]"

# Migrations travel in the image so the one-off task can use the same digest
# as the service. A deploy where the schema and the code come from different
# builds is a deploy that cannot be reasoned about.
COPY alembic ./alembic
COPY alembic.ini ./

COPY --from=web /web/dist ./web-dist
ENV STATIC_DIR=/app/web-dist

# Non-root, and the app cannot write its own code -- but `api/db.py` runs
# `db_file.parent.mkdir()` and `raw_cache_dir.mkdir()` UNCONDITIONALLY, on
# PostgreSQL too, so `/app/data` has to exist and be writable or the process
# dies at import with PermissionError. The previous `chown -R edge:edge /app`
# satisfied that by making the whole tree writable, which contradicted the
# comment above it; ops/preflight-image.sh check 5 asserts the comment and
# would have failed on that image. MEASURED as uid 10001 on a root-owned tree:
# with only /app/data owned by the runtime user, the app boots on PostgreSQL,
# answers /api/health, and `test -w api/main.py` is false.
# COPY preserves the build context's file modes, and a checkout whose files
# are 0600 (an umask of 077 does it) yields a root-owned tree the runtime user
# cannot READ: MEASURED by ops/preflight-image.sh check 6 on 2026-10-08 --
# every check before it passed, including "cannot write its own source", and
# `import api.main` died with PermissionError on /app/api/main.py. The image
# must not depend on the modes of whichever machine built it: readable by
# all, writable by root only, and then the one directory the app needs.
RUN chmod -R u=rwX,go=rX /app \
 && useradd --create-home --uid 10001 edge \
 && mkdir -p /app/data && chown edge:edge /app/data
USER edge

EXPOSE 8000

# No shell form: the signal has to reach uvicorn and not a wrapping shell, or
# a rolling deploy waits out the stop timeout on every task.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
