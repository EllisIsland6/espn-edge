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
RUN pip install --no-cache-dir .

# Migrations travel in the image so the one-off task can use the same digest
# as the service. A deploy where the schema and the code come from different
# builds is a deploy that cannot be reasoned about.
COPY alembic ./alembic
COPY alembic.ini ./

COPY --from=web /web/dist ./web-dist
ENV STATIC_DIR=/app/web-dist

# Non-root, and the app needs no write access to its own code.
RUN useradd --create-home --uid 10001 edge && chown -R edge:edge /app
USER edge

EXPOSE 8000

# No shell form: the signal has to reach uvicorn and not a wrapping shell, or
# a rolling deploy waits out the stop timeout on every task.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
