# One VPC, one public subnet for the host, two private subnets for the RDS
# subnet group (RDS requires two AZs in the group even for a Single-AZ
# instance; the instance itself lives in the host's AZ). The private subnets
# have NO route to the internet and need none: nothing in them initiates
# outbound traffic. That is the whole reason there is no NAT gateway, and
# the architecture document's answer to "why expose an EC2 public IP":
# the EIP is the host's egress and CloudFront's origin, not broad ingress.

locals {
  az_primary   = "${var.region}a"
  az_secondary = "${var.region}b"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "espn-edge" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_subnet" "app_public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.0.0/24"
  availability_zone       = local.az_primary
  map_public_ip_on_launch = false # the host gets an EIP explicitly
  tags                    = { Name = "espn-edge-app-public" }
}

resource "aws_subnet" "db_private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.42.10.0/24"
  availability_zone = local.az_primary
  tags              = { Name = "espn-edge-db-private-a" }
}

resource "aws_subnet" "db_private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.42.11.0/24"
  availability_zone = local.az_secondary
  tags              = { Name = "espn-edge-db-private-b" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "app_public" {
  subnet_id      = aws_subnet.app_public.id
  route_table_id = aws_route_table.public.id
}

# The private route table has no default route. Deliberately: a route to a
# NAT is the resource this design does not buy, and the policy test asserts
# no private route leaves the VPC.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table_association" "db_private_a" {
  subnet_id      = aws_subnet.db_private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "db_private_b" {
  subnet_id      = aws_subnet.db_private_b.id
  route_table_id = aws_route_table.private.id
}

# S3 gateway endpoint: $0, and it keeps ECR layer pulls and SPA deploys off
# the public path. It is a GATEWAY endpoint -- a route-table entry -- not an
# interface endpoint, which is the kind the architecture rejects.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
}

# ------------------------------------------------------- security groups
# CloudFront reaches the origin over 443 from its origin-facing address
# ranges, published as a managed prefix list. Nothing else reaches the host:
# no SSH (SSM Session Manager is the console), no 80 (ACME is DNS-01).
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "host" {
  name        = "espn-edge-host"
  description = "ECS host: 443 from CloudFront origin-facing only; all egress"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTPS from CloudFront origin-facing ranges"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id]
  }

  egress {
    description = "ESPN, Anthropic, ECR, Secrets Manager, CloudWatch, ACME"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "espn-edge-db"
  description = "PostgreSQL from the host only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from the ECS host"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.host.id]
  }
}
