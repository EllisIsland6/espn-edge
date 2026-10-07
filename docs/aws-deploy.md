# Deploying ESPN Edge to AWS — private, single-operator

**Status of this document.** Every claim marked MEASURED was verified against a
real PostgreSQL 16 and a real running application. Everything marked
UNVERIFIED has never been executed, because the environments this was written
in have no AWS credentials and no Docker daemon. The split is deliberate: a
runbook that does not distinguish the two is a runbook that will waste a day.

---

## 0. Read this first: HTTPS is a hard prerequisite

The chosen design puts **Cognito on the load balancer**, so AWS performs the
sign-in before any traffic reaches the application. That is the fastest route
to something genuinely protected and it needs no code in this repository.

But an ALB can only do `authenticate-cognito` on an **HTTPS listener**, and an
HTTPS listener needs an **ACM certificate**, and a publicly trusted ACM
certificate needs a **domain name you control**.

So, before anything else, pick one:

| Option | Cost / time | Consequence |
| --- | --- | --- |
| **Register a domain in Route 53** | ~$12–15/yr, usable in minutes | Cleanest. ACM issues and validates automatically. |
| **Use a domain you already own** | free | Add a CNAME/ALIAS to the ALB and a DNS validation record for ACM. |
| **Skip ALB auth; use a security group allowlist** | free | No Cognito, no certificate. Reachable only from your IP. A changed home IP locks you out, and nobody else can see it. |

There is no fourth option that keeps Cognito. A self-signed certificate on an
ALB is not accepted by `authenticate-cognito`.

**If you do not have a domain by Thursday, deploy with the security-group
allowlist and add Cognito after.** The application is identical either way;
only the listener rule changes. That ordering costs nothing and removes the
dependency from the critical path.

---

## 1. What this deploys

```
          internet
             │  HTTPS :443
      ┌──────▼───────────────────────────┐
      │ Application Load Balancer        │
      │   rule 1: authenticate-cognito   │  ← AWS does the sign-in
      │   rule 2: forward → target group │
      └──────┬───────────────────────────┘
             │  HTTP :8000  (private subnets)
      ┌──────▼───────────────────────────┐
      │ ECS Fargate service, 1 task      │
      │   one container:                 │
      │     uvicorn api.main:app         │
      │     + the built frontend         │
      └──────┬───────────────────────────┘
             │  :5432
      ┌──────▼───────────────────────────┐
      │ RDS PostgreSQL 16, private       │
      └──────────────────────────────────┘

  Secrets Manager: FERNET_KEY, SESSION_SECRET, OPERATOR_PASSWORD_HASH,
                   the two database URLs, ANTHROPIC_API_KEY
```

**One container, not two.** The frontend is static files and the process that
serves the API can serve them. Two services would mean two target groups and a
CORS configuration between them — three more things to get wrong for no
benefit at this size. MEASURED: the API serves the real built `web/dist`,
including hashed assets and client-side routes, and `/api/*` still wins.

---

## 2. The database roles — do this before the first migration

MEASURED, and this is the step most likely to be skipped: `alembic upgrade
head` on a fresh database produces **29 tables and zero privileges** for the
application role. Row-level security is enabled and forced and every policy is
correct, and the app still cannot read one row, because nothing has granted it
anything. Revision `0016` grants them, but it does **not** create the role —
that means a password, which belongs with whatever provisions the database.

Connect to the new RDS instance as the master user and run:

```sql
-- The owner runs migrations. It needs DDL rights and nothing else.
CREATE ROLE edge_owner LOGIN PASSWORD '<owner-password>' NOSUPERUSER NOBYPASSRLS;

-- The application. NOSUPERUSER NOBYPASSRLS is not optional:
-- FORCE ROW LEVEL SECURITY does not constrain a superuser or a BYPASSRLS
-- role, so isolation would be silently off and nothing in the schema would
-- show it. api/db.py refuses to start if this role has either attribute.
CREATE ROLE edge_app LOGIN PASSWORD '<app-password>' NOSUPERUSER NOBYPASSRLS;

CREATE DATABASE edge OWNER edge_owner;
\c edge
GRANT ALL ON SCHEMA public TO edge_owner;
```

MEASURED: with those roles, the full chain `0001 → 0016` applies cleanly as
`edge_owner`, privileges go from 0 to 113, and a table created afterwards by
the owner is automatically reachable by `edge_app` (`ALTER DEFAULT
PRIVILEGES`). `alembic_version` is readable and **not** writable by the app.

---

## 3. Secrets — and one that can never be rotated casually

Create these in Secrets Manager before the first deploy.

| Secret | How to generate | Notes |
| --- | --- | --- |
| `FERNET_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` | **Generate once and never change it.** It decrypts the stored ESPN `espn_s2` cookies. A new key does not fail loudly — it makes every stored credential undecryptable, and the symptom is re-auth prompts with no explanation. |
| `SESSION_SECRET` | 48+ random chars | **Read by nothing** today — there is no endpoint that mints a session. Wired ahead of the identity provider. |
| `OPERATOR_PASSWORD_HASH` | any value | **Read by nothing.** Wired ahead of the identity provider. See the warning below before you treat it as a factor. |
| `DATABASE_URL_OWNER` | `postgresql+psycopg2://edge_owner:…@<rds-endpoint>:5432/edge` | Only the migration task gets this. |
| `DATABASE_URL_APP` | `postgresql+psycopg2://edge_app:…@<rds-endpoint>:5432/edge` | The service gets this. |
| `ANTHROPIC_API_KEY` | from the Anthropic console | Optional. Empty string disables the AI features cleanly. |

---

## 4. Environment the container must be given

MEASURED by booting it: three of these are **required with no default** and the
process refuses to start without them — which is intended, and is why they are
listed rather than assumed.

| Variable | Value | Required |
| --- | --- | --- |
| `APP_MODE` | `private_operator` | **yes** (no default) |
| `TELEMETRY_ENABLED` | `false` | **yes** (no default) |
| `TELEMETRY_REPORT_PATH` | `/tmp/telemetry-report.md` | **yes** (no default) |
| `DATABASE_URL` | from `DATABASE_URL_APP` | yes, or it uses SQLite |
| `TENANT_ID` | `1` | **yes on PostgreSQL** — see below |
| `SEASON` | `2026` | defaults to 2026 |
| `RECOVERY_REQUIRED` | `false` | the Restic/Keychain path is laptop-shaped; use RDS snapshots |
| `STATIC_DIR` | `/app/web-dist` | set by the image |
| `ESPN_API_HOST` | leave unset for the real host | |

### Why `TENANT_ID` is required on PostgreSQL

MEASURED, and this is the defect that booting against a real database found:
the application used to *discover* its tenant with `SELECT id FROM tenants`.
That cannot work. The app connects as a NOBYPASSRLS role, `tenants` has FORCE
ROW LEVEL SECURITY, and the policy hides every row until `app.tenant_id` is
bound — so the query that exists to find the tenant needs a tenant already
bound to return anything. As `edge_app` with nothing bound it reads **0** rows;
with `SET LOCAL app.tenant_id='1'` it reads **1**. Every request 500s with
`TenantNotResolved` on a perfectly migrated database. SQLite has no row-level
security, so the offline suite never saw it.

A fresh migrated database has exactly one tenant, id **1**, slug `default`
(MEASURED). Confirm with `SELECT id, slug FROM tenants;` as the master user.

A wrong value does not silently serve an empty database: the id is verified
*after* binding, which is the only order in which the check can see anything.
MEASURED — `TENANT_ID=99` and `TENANT_ID=0` both refuse with the reason.

---

## 5. Build and push the image

UNVERIFIED — no Docker daemon was available. The three stages inside it were
verified separately:

- `npm ci && npm run build` produces `web/dist` with hashed assets — MEASURED.
- `pip install .` installs the project non-editable and `api` imports from
  `site-packages` with all 16 routes registered — MEASURED.
- `uvicorn api.main:app` with `STATIC_DIR` pointing at the real `dist`, against
  real PostgreSQL: `/api/health` truthful, `/` serves the app, hashed assets
  200, client-side routes 200, `/api/accounts` 200, duplicate POST 409 —
  MEASURED.

What is unverified is the image *assembly* itself.

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1
REPO=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/espn-edge

aws ecr create-repository --repository-name espn-edge --region $REGION
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# linux/amd64 matters: an arm64 image built on an Apple-silicon Mac will not
# run on an x86_64 Fargate task, and the failure is a cryptic exec error.
docker build --platform linux/amd64 -t espn-edge:1 .
docker tag espn-edge:1 $REPO:1
docker push $REPO:1
```

---

## 6. Migrate — as a one-off task, never on app start

The application role has no DDL rights, deliberately, so the app **cannot**
migrate. An entrypoint that tried would fail every deploy. Run the same image
with the owner credentials and the app role named:

```bash
aws ecs run-task --cluster espn-edge \
  --task-definition espn-edge-migrate \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<private>],securityGroups=[<db-client-sg>]}" \
  --overrides '{"containerOverrides":[{"name":"migrate",
      "command":["alembic","upgrade","head"],
      "environment":[{"name":"APP_DB_ROLE","value":"edge_app"}]}]}'
```

`APP_DB_ROLE` tells `0016` which role to grant. It defaults to `edge_app`, and
if the role is absent the migration **fails loudly** naming the variable rather
than skipping — MEASURED, because a silent skip produces exactly the
dead-on-arrival deployment this section exists to prevent.

Keeping the schema and the code in the same image digest is deliberate: a
deploy where they come from different builds cannot be reasoned about.

### The defect this section had, and how it was found

For one commit this section was correct and **impossible**. `Dockerfile`
installed the project with a bare `pip install .`, which installs
`[project] dependencies` and nothing else — and alembic is deliberately not
one of them, because nothing under `api/` imports it.

So the image would have built, the service would have started, `/api/health`
would have returned `ok`, and *this* command would have exited
`executable file not found in $PATH`. The schema would never have been
created, and every signal pointing at the image was green. The same shape as
the forty-odd others on record: a check green while establishing something
other than what it claims — here, the image was verified as an image, and
nothing verified it against the commands something else had been told to run
with it.

MEASURED both ways, in a directory holding only `pyproject.toml` and `api/`,
which is exactly what that layer of the image sees:

| install | `bin/alembic` | `import alembic` |
| --- | --- | --- |
| `pip install .` | absent | `ModuleNotFoundError` |
| `pip install ".[migrate]"` | present | 1.20.0 |

The fix is the `[migrate]` extra, and `tests/test_image_commands.py` is what
notices if it comes back: it resolves the argv[0] of every `command=[...]` in
`infra/app.py` and of the image's own `CMD` to the distribution that provides
it, and fails if the Dockerfile's install does not cover that distribution.
Control-removed both ways — reverting the Dockerfile to a bare `.` fails three
of its five tests by name.

### UNVERIFIED: the image's library versions are not the tested versions

`pyproject.toml` carries **floors only** — no lockfile, no upper bounds except
`nflreadpy`. `pip install ".[migrate]"` on 2026-10-07 resolved:

| declared | resolved |
| --- | --- |
| `pandas>=2.2` | **3.0.6** |
| `sqlalchemy>=2.0` | **2.1.3** |
| `anthropic>=0.40` | **1.11.0** |
| `fastapi>=0.115` | 0.142.2 (starlette 1.7.0) |
| `pydantic>=2.9` | 2.13.5 |

Two of those cross a major boundary. The interpreter is pinned to CI's 3.12
and the Dockerfile says so; the libraries are not pinned to anything, so the
image built next week is a different image. The suite was run against this
exact resolved set (see the status note for the result) — but that is one
day's resolution, not a guarantee. **The honest fix is a lockfile** (`pip
compile`/`uv lock` into a `requirements.lock` the image installs), and it is
not done. Until it is, an image build is a dependency upgrade nobody reviewed.

---

## 7. Verify, in this order

```bash
curl -s https://<your-domain>/api/health
```

Expect:

```json
{"status":"ok","season":2026,"db_path":"...","backend":"postgresql://<rds-endpoint>:5432/edge"}
```

`backend` is the field that matters. It is read off the live connection URL,
with the user and password stripped, because this endpoint is what the load
balancer polls unauthenticated and everything in it is public. MEASURED: before
this field existed, `/api/health` reported a SQLite path for an application
connected to PostgreSQL.

- `db_path` still showing a SQLite-shaped path is **expected and harmless** —
  it is the SQLite setting, kept for the Phase 0 acceptance criterion.
- `backend` saying `sqlite` means `DATABASE_URL` did not reach the container.
  That is the single most likely misconfiguration.

Then: load `/` in a browser (Cognito should challenge first), and `GET
/api/accounts` should return `[]` rather than a 500.

---

## 8. ALB health checks and Cognito

Target group health check path: **`/api/health`**, expecting 200.

ALB health checks do not pass through the `authenticate-cognito` action, so
they do not need an exemption. If you later move auth into the application,
that changes, and the health path must stay unauthenticated.

---

## 9. What this deployment does NOT include

Stated so none of it is discovered in production:

1. **Background work does not run.** `jobs`, `schedules` and `outbox` exist,
   are tested and are measured, but nothing in the application writes them —
   there is no worker or scheduler process. Syncs happen when a request
   triggers them. `tests/test_outbox_schedules_unwired.py` fails the build if
   that changes without the tenantless-row guard being decided.
2. **Recovery is not wired for AWS.** The Restic/Keychain path assumes the
   operator's laptop. Use **RDS automated backups plus a manual snapshot
   before each deploy**, and set `RECOVERY_REQUIRED=false`.
3. **No live ESPN read has ever been authorized or performed**, so the
   provider's real latency and failure behaviour are unmeasured. The first real
   sync on AWS is the first time that code meets ESPN.
4. **One task, no autoscaling.** A single Fargate task at this size; a restart
   is a brief outage.
5. **THERE IS NO APPLICATION LOGIN.** This said the opposite for one commit --
   "two gates rather than one" -- and that is the dangerous direction to be
   wrong in, so it is spelled out here.

   `api/routers/auth.py` has `/me` and `/logout` and **deliberately no endpoint
   that mints a session**: minting needs an OIDC ID token, verifying one needs
   a JWT library this environment cannot install, and hand-rolling it is where
   `alg: none` and key-confusion bugs live. In `private_operator` mode
   `_bound_session` binds the configured tenant with no cookie, which is why
   the app serves at all.

   So `OPERATOR_PASSWORD_HASH` and `SESSION_SECRET` are read by nothing --
   grep the repository, they appear only in `infra/app.py` and this file.
   **Whatever sits in front of the load balancer is the only gate there is.**

   With `-c certificate_arn=...` that gate is Cognito, which is a real one.
   With `-c allow_cidr=...` it is an IP allowlist and nothing else: a
   completely unauthenticated application reachable from that range. Use a
   `/32`, check it is the address you think it is, and remember a home address
   changes. `tests/test_deployment_environment.py` pins both the unread
   wiring and this warning's presence.

---

## 10. Rollback

1. `aws ecs update-service --service espn-edge --task-definition espn-edge:<previous>`
2. Migrations: every revision from `0012` on has a tested `downgrade`, and
   `0012`, `0013` and `0015` **refuse** rather than destroy data when a
   downgrade would collide. `alembic downgrade <rev>` from the migration task.
3. Restore the pre-deploy RDS snapshot if the schema moved and the data must
   come back with it.
