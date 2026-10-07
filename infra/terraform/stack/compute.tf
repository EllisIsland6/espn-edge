# One t4g.small, arm64, ECS-optimized AL2023, registered into one ECS
# cluster. The web task (Caddy + API) and the worker task run on it as two
# services; ECS gives the cgroup separation the architecture asks for.
#
# No SSH key pair, no port 22: the instance role carries the SSM managed
# policy and Session Manager is the console. No public IP from the subnet;
# the EIP below is the address, and it survives a replacement.

data "aws_ssm_parameter" "ecs_ami_arm64" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id"
}

resource "aws_ecs_cluster" "main" {
  name = "espn-edge"

  setting {
    name  = "containerInsights"
    value = "disabled" # paid; the catalog's ten series are what is watched
  }
}

# ----------------------------------------------------------- instance role
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "espn-edge-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

# The ECS agent's own needs (register, pull, log) and Session Manager.
resource "aws_iam_role_policy_attachment" "host_ecs" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "host_ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "host" {
  name = "espn-edge-host"
  role = aws_iam_role.host.name
}

# --------------------------------------------------------------- the host
resource "aws_eip" "host" {
  domain = "vpc"
  tags   = { Name = "espn-edge-host" }
}

resource "aws_instance" "host" {
  ami                         = data.aws_ssm_parameter.ecs_ami_arm64.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.app_public.id
  vpc_security_group_ids      = [aws_security_group.host.id]
  iam_instance_profile        = aws_iam_instance_profile.host.name
  associate_public_ip_address = false

  # T4g burst credits: "standard" so a sustained spike throttles rather than
  # bills. Amendment C2 in the architecture document treats the two credit
  # modes as two different cost decisions; this is the cheap one.
  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    cat >> /etc/ecs/ecs.config <<'CFG'
    ECS_CLUSTER=${aws_ecs_cluster.main.name}
    ECS_ENABLE_CONTAINER_METADATA=true
    ECS_RESERVED_MEMORY=128
    CFG
  EOT

  tags = { Name = "espn-edge-host" }

  lifecycle {
    # A new AMI must not replace the host underneath a running deployment;
    # amendment C4 makes host replacement a deliberate cutover.
    ignore_changes = [ami]
  }
}

resource "aws_eip_association" "host" {
  instance_id   = aws_instance.host.id
  allocation_id = aws_eip.host.id
}

# The origin name CloudFront dials. An A record to the EIP; Caddy holds the
# certificate for this name, obtained by DNS-01 against the same zone.
resource "aws_route53_record" "origin" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.origin_hostname
  type    = "A"
  ttl     = 60
  records = [aws_eip.host.public_ip]
}
