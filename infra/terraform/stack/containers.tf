# Two ECR repositories (the application image from the root Dockerfile, and
# the Caddy image from ops/caddy), each keeping the current digest and two
# prior; two ECS services on the single host. Images are referenced by an
# immutable tag the release workflow writes -- never `latest`, which is a
# deploy whose contents nobody can state.

resource "aws_ecr_repository" "app" {
  name                 = "espn-edge/app"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "caddy" {
  name                 = "espn-edge/caddy"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

locals {
  ecr_keep_three = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "current + 2 prior digests"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy     = local.ecr_keep_three
}

resource "aws_ecr_lifecycle_policy" "caddy" {
  repository = aws_ecr_repository.caddy.name
  policy     = local.ecr_keep_three
}

# ------------------------------------------------------------ task roles
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: what the ECS agent needs to START a task -- pull from ECR,
# read the task's secrets, write logs. Not what the task does once running.
resource "aws_iam_role" "task_execution" {
  name               = "espn-edge-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "task_execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.database_url_app.arn,
      aws_secretsmanager_secret.database_url_owner.arn,
      aws_secretsmanager_secret.session_secret.arn,
      aws_secretsmanager_secret.anthropic_api_key.arn,
      aws_secretsmanager_secret.oidc_client_secret.arn,
      aws_secretsmanager_secret.origin_verify.arn,
      aws_db_instance.main.master_user_secret[0].secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_secrets.json
}

# Web task role: the API needs nothing from AWS at run time today (metrics
# publishing lands with the CloudWatch sink). An empty role is still a role,
# so a future grant is a diff and not a surprise.
resource "aws_iam_role" "task_web" {
  name               = "espn-edge-task-web"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role" "task_worker" {
  name               = "espn-edge-task-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

# Caddy's DNS-01: amendment C3 restricts it to exactly one TXT record in one
# zone, UPSERT/DELETE only, so the origin host can renew its own certificate
# and cannot touch the application's A/alias/NS records.
data "aws_iam_policy_document" "caddy_dns01" {
  statement {
    actions   = ["route53:ListHostedZonesByName", "route53:GetChange"]
    resources = ["*"]
  }
  statement {
    actions   = ["route53:ChangeResourceRecordSets"]
    resources = [data.aws_route53_zone.main.arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsRecordTypes"
      values   = ["TXT"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "route53:ChangeResourceRecordSetsActions"
      values   = ["UPSERT", "DELETE"]
    }
    condition {
      test     = "ForAllValues:StringLike"
      variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
      values   = ["_acme-challenge.${var.origin_hostname}"]
    }
  }
}

resource "aws_iam_role_policy" "task_web_dns01" {
  name   = "caddy-dns01"
  role   = aws_iam_role.task_web.id
  policy = data.aws_iam_policy_document.caddy_dns01.json
}

# --------------------------------------------------------------- logging
resource "aws_cloudwatch_log_group" "web" {
  name              = "/espn-edge/web"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/espn-edge/worker"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/espn-edge/migrate"
  retention_in_days = 90
}

# ------------------------------------------------------- the environment
# Mirrors docs/aws-deploy.md s4 and the CDK artefact. public_synthetic is the
# accepted custody boundary; no FERNET_KEY exists in this deployment.
locals {
  app_image   = "${aws_ecr_repository.app.repository_url}:${var.app_image_tag}"
  caddy_image = "${aws_ecr_repository.caddy.repository_url}:${var.caddy_image_tag}"

  app_environment = [
    { name = "APP_MODE", value = "public_synthetic" },
    { name = "TELEMETRY_ENABLED", value = "false" },
    { name = "TELEMETRY_REPORT_PATH", value = "/tmp/telemetry-report.md" },
    { name = "TENANT_ID", value = tostring(var.tenant_id) },
    { name = "SEASON", value = tostring(var.season) },
    { name = "RECOVERY_REQUIRED", value = "false" },
    { name = "STATIC_DIR", value = "/app/web-dist" },
    { name = "OIDC_ISSUER", value = local.oidc_issuer },
    { name = "OIDC_CLIENT_ID", value = aws_cognito_user_pool_client.app.id },
    { name = "OIDC_REDIRECT_URI", value = "https://${var.domain}/api/auth/callback" },
  ]

  app_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url_app.arn },
    { name = "SESSION_SECRET", valueFrom = aws_secretsmanager_secret.session_secret.arn },
    { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.anthropic_api_key.arn },
    { name = "OIDC_CLIENT_SECRET", valueFrom = aws_secretsmanager_secret.oidc_client_secret.arn },
  ]
}

# --------------------------------------------------------------- web task
# Caddy terminates TLS for the origin name and forwards to the API over the
# task's bridge network. Only Caddy publishes a host port.
resource "aws_ecs_task_definition" "web" {
  family                   = "espn-edge-web"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task_web.arn

  container_definitions = jsonencode([
    {
      name         = "caddy"
      image        = local.caddy_image
      essential    = true
      memory       = 128
      cpu          = 128
      portMappings = [{ containerPort = 443, hostPort = 443, protocol = "tcp" }]
      links        = ["app"]
      environment = [
        { name = "ORIGIN_HOSTNAME", value = var.origin_hostname },
        { name = "ACME_EMAIL", value = var.operator_email },
        { name = "AWS_REGION", value = var.region },
        { name = "AWS_HOSTED_ZONE_ID", value = data.aws_route53_zone.main.zone_id },
      ]
      secrets = [
        { name = "ORIGIN_VERIFY", valueFrom = aws_secretsmanager_secret.origin_verify.arn },
      ]
      mountPoints = [{ sourceVolume = "caddy-data", containerPath = "/data" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "caddy"
        }
      }
    },
    {
      name        = "app"
      image       = local.app_image
      essential   = true
      memory      = 1024
      cpu         = 512
      environment = local.app_environment
      secrets     = local.app_secrets
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status == 200 else 1)\""]
        interval    = 30
        timeout     = 10
        retries     = 3
        startPeriod = 60
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "app"
        }
      }
    },
  ])

  # Caddy's certificates and ACME account survive a task replacement, so a
  # redeploy is not a reissue (and not a Let's Encrypt rate-limit event).
  volume {
    name      = "caddy-data"
    host_path = "/var/lib/espn-edge/caddy"
  }
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "EC2"

  # One host, one task that owns port 443: the replacement cannot start until
  # the old one stops. Amendment C4's cutover is a second host, not a second
  # task on this one.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

# ------------------------------------------------------------ worker task
resource "aws_ecs_task_definition" "worker" {
  family                   = "espn-edge-worker"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task_worker.arn

  container_definitions = jsonencode([
    {
      name        = "worker"
      image       = local.app_image
      essential   = true
      memory      = 512
      cpu         = 256
      command     = ["python", "-m", "api.worker"]
      environment = local.app_environment
      secrets     = local.app_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "worker"
        }
      }
    },
  ])
}

# desired_count is ZERO, deliberately, and tests/test_terraform_policy.py
# holds it there. `python -m api.worker` does not exist: Phase 39 was
# narrowed to the queue, leases, retries and a single `drain()` pass, and
# the forever-loop that would call `materialize_due` is the production
# caller that tests/test_outbox_schedules_unwired.py exists to catch --
# writing it means deciding the tenantless-schedule policy that phase
# deferred. The task definition is here so the plan shows the shape; the
# service starts nothing until the entrypoint exists and the register entry
# in tests/test_image_commands.py (KNOWN_MISSING_ENTRYPOINTS) is removed.
resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 0
  launch_type     = "EC2"

  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

# ----------------------------------------------------------- migrate task
# Run on demand with the OWNER credentials, never on service start
# (docs/aws-deploy.md s6). Same image digest as the service.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "espn-edge-migrate"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  execution_role_arn       = aws_iam_role.task_execution.arn

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = local.app_image
      essential = true
      memory    = 512
      cpu       = 256
      command   = ["alembic", "upgrade", "head"]
      environment = concat(
        [for e in local.app_environment : e if e.name != "OIDC_ISSUER" && e.name != "OIDC_CLIENT_ID" && e.name != "OIDC_REDIRECT_URI"],
        [{ name = "APP_DB_ROLE", value = "edge_app" }],
      )
      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url_owner.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.migrate.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "migrate"
        }
      }
    },
  ])
}
