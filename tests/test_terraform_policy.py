"""Phase 42's guarantees, asserted against the Terraform as written.

The accepted architecture (docs/sprint-9/03-architecture.md) rejects a list of
resources by name -- NAT gateway, ALB, interface endpoint, WAF, KMS custody
key, private stack -- and the phase's acceptance says "policy tests assert
forbidden resources/permissions". This file is those tests. It parses the
HCL with python-hcl2 and inspects what the stack DECLARES; it does not need
a provider, a registry or an account, which is the point: the environments
this was written in have none of the three, and the guarantees are about the
text of the plan, which is what a reviewer reads.

What this cannot establish: that the HCL is valid Terraform. python-hcl2
parses syntax and shape, not provider schemas. `terraform validate` and the
first `terraform plan` are the operator's, and the README says so.

Two registers live here in the shape this repository uses for known gaps --
entries that fail when they stop being true:

- `KNOWN_MISSING_ENTRYPOINTS` names commands the task definitions run that
  the image cannot run yet, with the reason, and the worker service is held
  at desired_count 0 while its entry exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import hcl2
import pytest

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "infra/terraform/stack"
BOOTSTRAP = ROOT / "infra/terraform/bootstrap"

#: Resource types the architecture rejects at the selected scale. Any one of
#: them in the stack root fails, whatever its arguments.
FORBIDDEN_RESOURCE_TYPES = {
    "aws_nat_gateway": "no NAT gateway -- $36.50/month to hide a source address",
    "aws_lb": "no ALB -- CloudFront is the single public origin",
    "aws_alb": "no ALB",
    "aws_lb_listener": "no ALB",
    "aws_wafv2_web_acl": "no WAF",
    "aws_kms_key": "no custody key -- there is nothing to decrypt in a synthetic deployment",
    "aws_kms_grant": "no custody key grant",
    "aws_iam_user": "no IAM users; roles assumed by OIDC only",
    "aws_iam_access_key": "no long-lived keys",
    "aws_rds_cluster": "no Aurora; single-AZ RDS instance",
}

#: Commands in task definitions that the image cannot run yet, each with
#: the reason. `test_the_missing_entrypoint_register_does_not_outlive_its_truth`
#: fails when the module appears, so an entry cannot be forgotten.
KNOWN_MISSING_ENTRYPOINTS = {
    "api.worker": (
        "Phase 39 narrowed: drain() exists, the forever-loop that would call "
        "materialize_due does not, and writing it trips "
        "tests/test_outbox_schedules_unwired.py by design."
    ),
}


# ------------------------------------------------------------------ loading

def _strip(value):
    """python-hcl2 8.x keeps the quote characters on string literals and on
    block labels (`'"aws_db_instance"'`), marks blocks with `__is_block__`
    and attaches `__comments__`. Normalise all of that away so the tests
    read like the HCL does. Interpolations (`${...}`) are left as they are.

    The instrument check at the bottom of this file asserts this function
    sees real resources: the first version of the loader assumed the 4.x
    shape, found nothing, and every assertion above it passed against an
    empty dict.
    """
    if isinstance(value, dict):
        return {
            _strip(k): _strip(v)
            for k, v in value.items()
            if k not in ("__is_block__", "__comments__", "__start_line__", "__end_line__")
        }
    if isinstance(value, list):
        return [_strip(v) for v in value]
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _load(directory: Path) -> dict:
    """Every resource/data/variable/output/locals block in a root, merged."""
    merged: dict[str, dict] = {"resource": {}, "data": {}, "variable": {}, "output": {}, "locals": {}}
    for path in sorted(directory.glob("*.tf")):
        with path.open(encoding="utf-8") as fh:
            doc = _strip(hcl2.load(fh))
        for kind in ("resource", "data"):
            for block in doc.get(kind, []):
                for rtype, named in block.items():
                    merged[kind].setdefault(rtype, {}).update(named)
        for kind in ("variable", "output"):
            for block in doc.get(kind, []):
                merged[kind].update(block)
        for block in doc.get("locals", []):
            merged["locals"].update(block)
    return merged


@pytest.fixture(scope="module")
def stack() -> dict:
    return _load(STACK)


@pytest.fixture(scope="module")
def bootstrap() -> dict:
    return _load(BOOTSTRAP)


def _resources(doc: dict, rtype: str) -> dict:
    return doc["resource"].get(rtype, {})


def _one(doc: dict, rtype: str) -> dict:
    found = _resources(doc, rtype)
    assert len(found) == 1, f"expected exactly one {rtype}, found {sorted(found)}"
    return next(iter(found.values()))


def _as_list(value) -> list[dict]:
    """A `locals` value that is a list of objects. python-hcl2 may hand it
    back parsed, or as the HCL expression text; both are accepted, and a
    value that is neither fails loudly rather than iterating a string."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        inner = value[2:-1] if value.startswith("${") and value.endswith("}") else value
        return _strip(hcl2.loads(f"x = {inner}"))["x"]
    raise AssertionError(f"unexpected locals shape: {type(value).__name__}")


def _container_definitions(task: dict) -> list[dict]:
    """The task definition's container list.

    python-hcl2 renders `jsonencode([...])` as the string
    `${jsonencode([...])}`; the inner literal is HCL, not JSON, so it is
    re-read through the HCL parser as an expression.
    """
    raw = task["container_definitions"]
    inner = raw[len("${jsonencode(") : -len(")}")] if raw.startswith("${jsonencode(") else raw
    return _strip(hcl2.loads(f"x = {inner}"))["x"]


# ------------------------------------------------- what must NOT be there

def test_no_forbidden_resource_types_in_the_stack(stack):
    present = {rtype: reason for rtype, reason in FORBIDDEN_RESOURCE_TYPES.items() if rtype in stack["resource"]}
    assert present == {}, f"the architecture rejects these and the stack declares them: {present}"


def test_the_only_vpc_endpoint_is_the_free_s3_gateway(stack):
    endpoints = _resources(stack, "aws_vpc_endpoint")
    assert len(endpoints) == 1, sorted(endpoints)
    (ep,) = endpoints.values()
    assert ep["vpc_endpoint_type"] == "Gateway"
    assert "s3" in ep["service_name"]


def test_private_route_table_has_no_route_out(stack):
    """A NAT would appear as a default route; so would an IGW on the private table."""
    tables = _resources(stack, "aws_route_table")
    assert "private" in tables and "public" in tables
    assert "route" not in tables["private"], tables["private"]
    public_routes = tables["public"]["route"]
    assert any("gateway_id" in r for r in public_routes), public_routes


def test_the_host_admits_only_cloudfront_on_443(stack):
    sg = _resources(stack, "aws_security_group")["host"]
    ingress = sg["ingress"]
    assert len(ingress) == 1, ingress
    (rule,) = ingress
    assert rule["from_port"] == 443 and rule["to_port"] == 443
    assert "prefix_list_ids" in rule and "cidr_blocks" not in rule, rule
    assert "cloudfront_origin_facing" in str(rule["prefix_list_ids"])


def test_no_ssh_and_no_key_pair(stack):
    host = _one(stack, "aws_instance")
    assert "key_name" not in host
    sg = _resources(stack, "aws_security_group")["host"]
    assert not any(r.get("from_port") == 22 for r in sg["ingress"])


def test_the_database_is_private_single_az_encrypted(stack):
    db = _one(stack, "aws_db_instance")
    assert db["publicly_accessible"] is False
    assert db["multi_az"] is False
    assert db["storage_encrypted"] is True
    assert db["manage_master_user_password"] is True, "the owner password must never be in state or a variable"
    assert db["deletion_protection"] is True
    assert db["backup_retention_period"] == "${var.db_backup_retention_days}"
    assert "kms_key_id" not in db, "storage uses the RDS-managed key; no custody KMS key"
    # Amendment C6: placement is RDS's choice unless the variable pins it.
    assert db["availability_zone"] == "${var.db_availability_zone}"
    var = _load(STACK)["variable"]["db_availability_zone"]
    assert "default" in var and var["default"] is None, var


def test_the_database_subnet_group_uses_only_private_subnets(stack):
    group = _one(stack, "aws_db_subnet_group")
    assert all("db_private" in s for s in group["subnet_ids"]), group["subnet_ids"]


def test_the_spa_bucket_is_private_and_fronted_by_oac(stack):
    block = _resources(stack, "aws_s3_bucket_public_access_block")["spa"]
    assert all(block[k] is True for k in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"))
    assert "aws_s3_bucket_website_configuration" not in stack["resource"]
    dist = _one(stack, "aws_cloudfront_distribution")
    spa_origin = next(o for o in dist["origin"] if o["origin_id"] == "spa")
    assert "origin_access_control_id" in spa_origin


def test_cloudfront_does_not_rewrite_api_status_codes(stack):
    """Found in the first real plan: `custom_error_response` is
    distribution-wide, so a 403/404 -> 200 index.html mapping would have
    turned every API error into the SPA shell. Deep links are handled by a
    viewer-request function on the SPA behaviour only."""
    dist = _one(stack, "aws_cloudfront_distribution")
    assert "custom_error_response" not in dist, (
        "custom_error_response applies to every origin, the API included; "
        "use the SPA-behaviour function instead"
    )
    default = dist["default_cache_behavior"][0]
    assoc = default.get("function_association")
    assert assoc and assoc[0]["event_type"] == "viewer-request", default
    assert "spa_rewrite" in assoc[0]["function_arn"]
    api = next(b for b in dist["ordered_cache_behavior"] if b["path_pattern"] == "/api/*")
    assert "function_association" not in api, "the rewrite must never see API paths"
    fn = _one(stack, "aws_cloudfront_function")
    assert "/api/" not in fn["code"] or "indexOf('.')" in fn["code"]


def test_cloudfront_reaches_the_origin_over_https_with_the_verify_header(stack):
    dist = _one(stack, "aws_cloudfront_distribution")
    api_origin = next(o for o in dist["origin"] if o["origin_id"] == "api")
    assert api_origin["custom_origin_config"][0]["origin_protocol_policy"] == "https-only"
    assert api_origin["custom_header"][0]["name"] == "X-Origin-Verify"
    api_behavior = next(b for b in dist["ordered_cache_behavior"] if b["path_pattern"] == "/api/*")
    assert api_behavior["viewer_protocol_policy"] == "https-only"
    assert api_behavior["cache_policy_id"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", "the API must use Managed-CachingDisabled"


# ------------------------------------------------------- custody and mode

def test_every_task_runs_public_synthetic_and_no_fernet_key(stack):
    tasks = _resources(stack, "aws_ecs_task_definition")
    assert set(tasks) == {"web", "worker", "migrate"}, sorted(tasks)
    # The environment is a local; its literal is what we check.
    env = _as_list(stack["locals"]["app_environment"])
    by_name = {e["name"]: e["value"] for e in env}
    assert by_name["APP_MODE"] == "public_synthetic"
    assert "FERNET_KEY" not in by_name
    secrets = {s["name"] for s in _as_list(stack["locals"]["app_secrets"])}
    assert "FERNET_KEY" not in secrets
    assert "DATABASE_URL" in secrets and "OIDC_CLIENT_SECRET" in secrets


def test_every_variable_the_stack_sets_is_one_the_application_reads(stack):
    """The same invariant tests/test_deployment_environment.py holds for the
    CDK artefact, for this root. Settings fields, upper-cased, plus the
    migration's os.environ read."""
    source = (ROOT / "api/config.py").read_text(encoding="utf-8")
    fields = {m.upper() for m in re.findall(r"^\s{4}([a-z_][a-z0-9_]*)\s*:", source, re.M)}
    read = fields | {"APP_DB_ROLE"}
    names = {e["name"] for e in _as_list(stack["locals"]["app_environment"])} | {
        s["name"] for s in _as_list(stack["locals"]["app_secrets"])
    }
    unread = sorted(names - read - {"SESSION_SECRET"})  # SESSION_SECRET: registered unread, see test_deployment_environment
    assert unread == [], f"the stack sets {unread}, which nothing reads"


def test_images_are_pinned_by_variable_never_latest(stack):
    for name in ("app_image", "caddy_image"):
        assert "latest" not in stack["locals"][name]
        assert "var." in stack["locals"][name]
    for repo in _resources(stack, "aws_ecr_repository").values():
        assert repo["image_tag_mutability"] == "IMMUTABLE"


# ------------------------------------------------------------- the worker

def test_the_worker_service_is_parked_while_its_entrypoint_is_missing(stack):
    services = _resources(stack, "aws_ecs_service")
    worker = services["worker"]
    task = _resources(stack, "aws_ecs_task_definition")["worker"]
    (container,) = _container_definitions(task)
    command = container["command"]
    assert command[:2] == ["python", "-m"], command
    module = command[2]
    if module in KNOWN_MISSING_ENTRYPOINTS:
        assert worker["desired_count"] == 0, (
            f"{module} is registered as missing but the worker service would start "
            f"{worker['desired_count']} task(s) of it"
        )
    else:
        assert (ROOT / (module.replace(".", "/") + ".py")).exists() or (ROOT / module.replace(".", "/") / "__main__.py").exists()


def test_the_missing_entrypoint_register_does_not_outlive_its_truth():
    present = sorted(
        m for m in KNOWN_MISSING_ENTRYPOINTS
        if (ROOT / (m.replace(".", "/") + ".py")).exists() or (ROOT / m.replace(".", "/") / "__main__.py").exists()
    )
    assert present == [], (
        f"{present} now exist(s). Remove it from KNOWN_MISSING_ENTRYPOINTS and "
        "raise the worker service's desired_count."
    )


def test_the_migrate_task_runs_alembic_with_the_owner_url(stack):
    task = _resources(stack, "aws_ecs_task_definition")["migrate"]
    (container,) = _container_definitions(task)
    assert container["command"] == ["alembic", "upgrade", "head"]
    secrets = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert "database_url_owner" in secrets["DATABASE_URL"]
    # The environment is a `concat(...)` expression over the shared local;
    # the two facts that matter are readable from its text: the role name
    # revision 0016 grants, and that the OIDC settings are stripped (a
    # migration has no front door).
    environment = str(container["environment"])
    assert 'APP_DB_ROLE' in environment and '"edge_app"' in environment, environment
    assert 'e.name != "OIDC_ISSUER"' in environment, environment


# ------------------------------------------------------------- the alarms

def test_the_alarm_catalog_file_matches_phase_41(stack):
    """ops/render_alarms.py --check, as a test: the JSON Terraform reads must
    be what the Python catalog renders, or the alarms applied are not the
    alarms the suite pins."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "ops/render_alarms.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_every_catalog_alarm_treats_missing_data_as_breaching():
    data = json.loads((STACK / "alarms.generated.json").read_text(encoding="utf-8"))
    assert len(data["alarms"]) == 10, sorted(data["alarms"])
    for name, alarm in data["alarms"].items():
        assert alarm["TreatMissingData"] == "breaching", name


# ----------------------------------------------------------- the bootstrap

def test_the_deploy_role_is_bounded_and_the_boundary_denies_the_rejected_actions(bootstrap):
    role = _resources(bootstrap, "aws_iam_role")["deploy"]
    assert "permissions_boundary" in role
    boundary = bootstrap["data"]["aws_iam_policy_document"]["deploy_boundary"]
    denies = [s for s in boundary["statement"] if s.get("effect") == "Deny"]
    (deny,) = [s for s in denies if "condition" not in s]
    for action in ("ec2:CreateNatGateway", "elasticloadbalancing:*", "wafv2:*", "kms:CreateKey", "iam:CreateUser", "iam:CreateAccessKey", "rds:CreateDBCluster"):
        assert action in deny["actions"], action
    # The stack creates the free S3 *gateway* endpoint; an unconditional Deny on
    # CreateVpcEndpoint refused it. Found by reading the first plan against the
    # boundary, before any apply. The conditional statement is tested below.
    assert "ec2:CreateVpcEndpoint" not in deny["actions"]
    # And the Deny on kms:CreateGrant, meant to keep a custody key out, stopped
    # EBS and ACM creating grants on the account's AWS-managed keys: the first
    # apply past the lock lost the host and the certificate to it. CreateKey is
    # the custody line; CreateGrant is how every AWS service uses aws/* keys.
    assert "kms:CreateGrant" not in deny["actions"]


def test_aws_managed_keys_are_usable_only_through_the_stacks_services(bootstrap):
    boundary = bootstrap["data"]["aws_iam_policy_document"]["deploy_boundary"]
    kms = next(s for s in boundary["statement"] if s.get("sid") == "AwsManagedKeysViaService")
    assert "kms:CreateGrant" in kms["actions"]
    assert "kms:CreateKey" not in kms["actions"], "creating a key is the custody line"
    assert not any(a == "kms:*" for a in kms["actions"])
    (cond,) = kms["condition"]
    assert cond["test"] == "StringEquals"
    assert cond["variable"] == "kms:ViaService"
    assert sorted(cond["values"]) == sorted(
        f"{svc}.${{var.region}}.amazonaws.com" for svc in ("ec2", "rds", "acm", "secretsmanager")
    ), cond["values"]


def test_interface_endpoints_stay_denied_while_the_s3_gateway_is_allowed(bootstrap, stack):
    boundary = bootstrap["data"]["aws_iam_policy_document"]["deploy_boundary"]
    (deny,) = [s for s in boundary["statement"] if s.get("effect") == "Deny" and "condition" in s]
    assert deny["actions"] == ["ec2:CreateVpcEndpoint"]
    # Scoped to the endpoint ARN: on the vpc and route-table resources the same
    # call touches, ec2:VpceServiceName is absent, and StringNotEquals on an
    # absent key is true -- unscoped, the statement would deny the gateway too.
    assert deny["resources"] == ["arn:aws:ec2:*:*:vpc-endpoint/*"]
    (cond,) = deny["condition"]
    assert cond["test"] == "StringNotEquals"
    assert cond["variable"] == "ec2:VpceServiceName"
    assert cond["values"] == ["com.amazonaws.${var.region}.s3"]
    # The premise, held: the stack's one endpoint is exactly that service.
    (endpoint,) = _resources(stack, "aws_vpc_endpoint").values()
    assert endpoint["service_name"] == "com.amazonaws.${var.region}.s3"


def test_the_boundary_can_tag_the_instance_profile_the_stack_creates(bootstrap, stack):
    # The provider's default_tags land on aws_iam_instance_profile as well, and
    # CreateInstanceProfile with tags needs iam:TagInstanceProfile on top of
    # iam:CreateInstanceProfile. Found by reading the plan against the boundary.
    assert _resources(stack, "aws_iam_instance_profile"), "premise: the stack has an instance profile"
    assert "default_tags" in (STACK / "versions.tf").read_text(encoding="utf-8"), "premise: default_tags"
    boundary = bootstrap["data"]["aws_iam_policy_document"]["deploy_boundary"]
    allow = next(s for s in boundary["statement"] if s.get("sid") == "StackServices")
    for action in ("iam:CreateInstanceProfile", "iam:TagInstanceProfile"):
        assert action in allow["actions"], action


def test_the_boundary_lets_the_deploy_role_take_the_state_lock(bootstrap):
    # A permissions boundary caps every policy on the role, the inline `state`
    # policy included. The first apply (run #1, 2026-10-08) died in 16 seconds
    # acquiring the DynamoDB lock because the boundary named no DynamoDB
    # action; the plan workflow had never noticed because it plans with
    # -lock=false. The apply workflow must lock, so the boundary must allow it,
    # on the lock table and nowhere else.
    boundary = bootstrap["data"]["aws_iam_policy_document"]["deploy_boundary"]
    lock = next(s for s in boundary["statement"] if s.get("sid") == "StateLock")
    assert sorted(lock["actions"]) == ["dynamodb:DeleteItem", "dynamodb:GetItem", "dynamodb:PutItem"]
    assert lock["resources"] == ["${aws_dynamodb_table.lock.arn}"], lock["resources"]
    # The premise: the apply workflow does not disable locking.
    apply_yml = (ROOT / ".github/workflows/terraform-apply.yml").read_text(encoding="utf-8")
    assert "-lock=false" not in apply_yml


def test_the_plan_role_cannot_write(bootstrap):
    attachments = _resources(bootstrap, "aws_iam_role_policy_attachment")
    plan_policies = [a["policy_arn"] for a in attachments.values() if "plan" in str(a["role"])]
    assert plan_policies == ["arn:aws:iam::aws:policy/ReadOnlyAccess"], plan_policies
    inline = _resources(bootstrap, "aws_iam_role_policy")["plan_state"]
    assert "state_access" in inline["policy"]


def test_roles_trust_only_this_repository_via_oidc(bootstrap):
    for name in ("plan_trust", "deploy_trust"):
        doc = bootstrap["data"]["aws_iam_policy_document"][name]
        (stmt,) = doc["statement"]
        assert stmt["actions"] == ["sts:AssumeRoleWithWebIdentity"]
        subs = [c for c in stmt["condition"] if c["variable"].endswith(":sub")]
        assert subs, name
        assert all("var.github_repository" in v for v in subs[0]["values"]), subs[0]["values"]
    deploy_sub = next(c for c in bootstrap["data"]["aws_iam_policy_document"]["deploy_trust"]["statement"][0]["condition"] if c["variable"].endswith(":sub"))
    assert deploy_sub["values"] == ["repo:${var.github_repository}:environment:production"]


def test_the_budget_exists_before_anything_else_can(bootstrap):
    budget = _one(bootstrap, "aws_budgets_budget")
    kinds = {n["notification_type"] for n in budget["notification"]}
    assert kinds == {"ACTUAL", "FORECASTED"}


# ------------------------------------------------------------ instruments

def test_the_parses_are_actually_parsing_something(stack, bootstrap):
    assert len(stack["resource"]) >= 20, sorted(stack["resource"])
    assert sum(len(v) for v in stack["resource"].values()) >= 50
    assert "aws_cloudfront_distribution" in stack["resource"]
    assert "aws_ecs_task_definition" in stack["resource"]
    assert len(bootstrap["resource"]) >= 8
    # The forbidden-type detector must be able to fire.
    fake = {"resource": {"aws_nat_gateway": {"x": {}}}}
    assert any(t in fake["resource"] for t in FORBIDDEN_RESOURCE_TYPES)
    # And the jsonencode re-parse must round-trip a real container list.
    task = _resources(stack, "aws_ecs_task_definition")["web"]
    names = [c["name"] for c in _container_definitions(task)]
    assert names == ["caddy", "app"], names
