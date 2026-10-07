#!/usr/bin/env python3
"""CDK app for the private, single-operator ESPN Edge deployment.

    cd infra
    pip install -r requirements.txt
    npx cdk synth      # no AWS calls, proves the stack is well-formed
    npx cdk deploy     # makes changes and costs money

STATUS: this stack has been SYNTHESIZED and never DEPLOYED. The environments
it was written in have no AWS credentials. Treat `cdk diff` output as the
first real review, not this file.

Two shapes, chosen by whether you pass a certificate:

    npx cdk deploy -c certificate_arn=arn:aws:acm:... -c domain=edge.example.com
        HTTPS listener + Cognito at the load balancer. AWS performs the
        sign-in before traffic reaches the application.

    npx cdk deploy -c allow_cidr=203.0.113.4/32
        HTTP listener reachable only from that address. No certificate, no
        domain, no Cognito.

The second exists because `authenticate-cognito` requires an HTTPS listener,
which requires an ACM certificate, which requires a domain you control. If the
domain is not ready, this ships today and the listener rule changes later; the
application is identical either way.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_elasticloadbalancingv2_actions as elb_actions
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class EspnEdgeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        image_tag = self.node.try_get_context("image_tag") or "1"
        certificate_arn = self.node.try_get_context("certificate_arn")
        domain = self.node.try_get_context("domain")
        allow_cidr = self.node.try_get_context("allow_cidr")

        zone_name = self.node.try_get_context("zone_name")
        hosted_zone = None
        if zone_name:
            hosted_zone = route53.HostedZone.from_lookup(
                self, "Zone", domain_name=zone_name
            )
        if certificate_arn and not domain:
            raise ValueError(
                "-c certificate_arn=... also needs -c domain=edge.example.com. "
                "Cognito's callback URL is an absolute https:// address, so "
                "the hostname cannot be inferred from the load balancer."
            )

        if not certificate_arn and not allow_cidr:
            raise ValueError(
                "pass either -c certificate_arn=... (HTTPS + Cognito) or "
                "-c allow_cidr=1.2.3.4/32 (private, no certificate). Deploying "
                "with neither would put an unauthenticated application on the "
                "public internet, which is not a default worth having."
            )

        # Two AZs: RDS requires a subnet group spanning at least two, even for
        # a single-AZ instance. One NAT gateway rather than one per AZ --
        # this is a single-operator deployment and NATs are the largest
        # standing cost in it.
        vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=1)

        # ---------------------------------------------------------- secrets
        # Generated here so they exist exactly once and never pass through a
        # terminal or a repository.
        #
        # FERNET_KEY is NOT generated here, and that is deliberate: it
        # decrypts stored ESPN cookies, so a stack replacement that minted a
        # new one would make every stored credential undecryptable without
        # failing loudly. Create it by hand, once, and reference it.
        fernet = secretsmanager.Secret.from_secret_name_v2(
            self, "FernetKey", "espn-edge/fernet-key"
        )
        session_secret = secretsmanager.Secret(
            self,
            "SessionSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=64, exclude_punctuation=True
            ),
        )
        operator_hash = secretsmanager.Secret.from_secret_name_v2(
            self, "OperatorHash", "espn-edge/operator-password-hash"
        )
        anthropic_key = secretsmanager.Secret.from_secret_name_v2(
            self, "AnthropicKey", "espn-edge/anthropic-api-key"
        )

        # ------------------------------------------------------------- RDS
        db = rds.DatabaseInstance(
            self,
            "Database",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_4
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON, ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            publicly_accessible=False,
            allocated_storage=20,
            storage_encrypted=True,
            # Backups are the recovery story on AWS. The Restic/Keychain path
            # in this repository assumes the operator's laptop and is not
            # wired here; `RECOVERY_REQUIRED=false` below says so to the app.
            backup_retention=Duration.days(7),
            delete_automated_backups=False,
            deletion_protection=True,
            removal_policy=RemovalPolicy.SNAPSHOT,
            database_name="edge",
            credentials=rds.Credentials.from_generated_secret("edgemaster"),
        )

        # ------------------------------------------------------- the service
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc)

        image = ecs.ContainerImage.from_registry(
            f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/espn-edge:{image_tag}"
        )

        # The application connects as `edge_app`, which this stack cannot
        # create: creating a login role means choosing a password, and that
        # belongs with the database rather than with a CloudFormation
        # template. Create the roles and this secret by hand once --
        # docs/aws-deploy.md section 2 -- then deploy.
        app_db_url = secretsmanager.Secret.from_secret_name_v2(
            self, "AppDatabaseUrl", "espn-edge/database-url-app"
        )

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "Service",
            cluster=cluster,
            cpu=512,
            memory_limit_mib=1024,
            desired_count=1,
            public_load_balancer=True,
            protocol=(
                elbv2.ApplicationProtocol.HTTPS
                if certificate_arn
                else elbv2.ApplicationProtocol.HTTP
            ),
            certificate=(
                None
                if not certificate_arn
                else __import__(
                    "aws_cdk.aws_certificatemanager", fromlist=["Certificate"]
                ).Certificate.from_certificate_arn(self, "Cert", certificate_arn)
            ),
            # `domain_name` without `domain_zone` is rejected at synth time:
            # the pattern wants to create the DNS record itself and needs the
            # hosted zone to do it. Caught by `cdk synth`, which is the only
            # verification available without an account.
            #
            # So the zone is optional. With `-c zone_name=example.com` the
            # record is created here; without it the certificate is still
            # attached and you point DNS at the load balancer yourself, which
            # is what a domain hosted somewhere else needs.
            domain_name=domain if hosted_zone else None,
            domain_zone=hosted_zone,
            redirect_http=bool(certificate_arn),
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=image,
                container_port=8000,
                environment={
                    # All three are required with no default: the process
                    # refuses to start without them. Measured by booting it.
                    "APP_MODE": "private_operator",
                    "TELEMETRY_ENABLED": "false",
                    "TELEMETRY_REPORT_PATH": "/tmp/telemetry-report.md",
                    # Required on PostgreSQL. Discovery cannot work: the app
                    # is a NOBYPASSRLS role and FORCE ROW LEVEL SECURITY
                    # hides `tenants` until a tenant is bound, so the query
                    # that finds the tenant needs one already bound. A fresh
                    # migrated database has exactly one tenant, id 1.
                    "TENANT_ID": "1",
                    "SEASON": "2026",
                    "RECOVERY_REQUIRED": "false",
                    "STATIC_DIR": "/app/web-dist",
                },
                secrets={
                    "DATABASE_URL": ecs.Secret.from_secrets_manager(app_db_url),
                    "FERNET_KEY": ecs.Secret.from_secrets_manager(fernet),
                    "SESSION_SECRET": ecs.Secret.from_secrets_manager(session_secret),
                    "OPERATOR_PASSWORD_HASH": ecs.Secret.from_secrets_manager(
                        operator_hash
                    ),
                    "ANTHROPIC_API_KEY": ecs.Secret.from_secrets_manager(anthropic_key),
                },
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix="espn-edge",
                    log_retention=logs.RetentionDays.ONE_MONTH,
                ),
            ),
        )

        # `/api/health` is unauthenticated by design and reports the real
        # backend. ALB health checks do not pass through the Cognito action,
        # so they need no exemption.
        service.target_group.configure_health_check(
            path="/api/health",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(10),
        )
        # A slow first boot should not look like a crash loop.
        service.target_group.set_attribute(
            "deregistration_delay.timeout_seconds", "15"
        )

        db.connections.allow_default_port_from(
            service.service, "the application reaches PostgreSQL"
        )

        # ------------------------------------------------------------ access
        if certificate_arn:
            user_pool = cognito.UserPool(
                self,
                "UserPool",
                self_sign_up_enabled=False,  # one operator; invitations only
                sign_in_aliases=cognito.SignInAliases(email=True),
                removal_policy=RemovalPolicy.RETAIN,
            )
            client = user_pool.add_client(
                "AlbClient",
                generate_secret=True,
                o_auth=cognito.OAuthSettings(
                    flows=cognito.OAuthFlows(authorization_code_grant=True),
                    scopes=[cognito.OAuthScope.OPENID],
                    callback_urls=[f"https://{domain}/oauth2/idpresponse"],
                ),
            )
            cognito_domain = user_pool.add_domain(
                "Domain",
                cognito_domain=cognito.CognitoDomainOptions(
                    domain_prefix=f"espn-edge-{self.account[:8]}"
                ),
            )
            service.listener.add_action(
                "Authenticate",
                action=elb_actions.AuthenticateCognitoAction(
                    user_pool=user_pool,
                    user_pool_client=client,
                    user_pool_domain=cognito_domain,
                    next=elbv2.ListenerAction.forward([service.target_group]),
                ),
            )
        else:
            # No certificate: the load balancer is reachable from one address
            # and nowhere else. Narrower than Cognito in reach, weaker in
            # kind -- it authenticates a network location, not a person.
            service.load_balancer.connections.allow_from(
                ec2.Peer.ipv4(allow_cidr), ec2.Port.tcp(80), "operator only"
            )

        # --------------------------------------------- the migration task
        # Separate, and run by hand. The application role has no DDL rights,
        # so the app cannot migrate and an entrypoint that tried would fail
        # every deploy. Same image digest as the service: a deploy where the
        # schema and the code come from different builds cannot be reasoned
        # about.
        owner_db_url = secretsmanager.Secret.from_secret_name_v2(
            self, "OwnerDatabaseUrl", "espn-edge/database-url-owner"
        )
        migrate = ecs.FargateTaskDefinition(self, "MigrateTask", cpu=512, memory_limit_mib=1024)
        migrate.add_container(
            "migrate",
            image=image,
            command=["alembic", "upgrade", "head"],
            environment={
                "APP_MODE": "private_operator",
                "TELEMETRY_ENABLED": "false",
                "TELEMETRY_REPORT_PATH": "/tmp/telemetry-report.md",
                # Which role revision 0016 grants. If it does not exist the
                # migration fails loudly naming this variable, rather than
                # skipping -- a silent skip is the dead-on-arrival deploy.
                "APP_DB_ROLE": "edge_app",
            },
            secrets={
                "DATABASE_URL": ecs.Secret.from_secrets_manager(owner_db_url),
                "FERNET_KEY": ecs.Secret.from_secrets_manager(fernet),
                "SESSION_SECRET": ecs.Secret.from_secrets_manager(session_secret),
                "OPERATOR_PASSWORD_HASH": ecs.Secret.from_secrets_manager(operator_hash),
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="espn-edge-migrate",
                log_retention=logs.RetentionDays.ONE_MONTH,
            ),
        )

        cdk.CfnOutput(self, "LoadBalancerDns", value=service.load_balancer.load_balancer_dns_name)
        cdk.CfnOutput(self, "DatabaseEndpoint", value=db.db_instance_endpoint_address)
        cdk.CfnOutput(self, "MigrateTaskDefinition", value=migrate.task_definition_arn)
        cdk.CfnOutput(self, "ClusterName", value=cluster.cluster_name)


app = cdk.App()
EspnEdgeStack(
    app,
    "EspnEdgeStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
)
app.synth()
