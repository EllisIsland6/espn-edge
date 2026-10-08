# Single-AZ PostgreSQL 16 on a burstable micro (var.db_instance_class), in the private subnets, reachable
# only from the host's security group. Encrypted with the RDS-managed key:
# the architecture's "no KMS custody key" guarantee is about the application
# custody key that would decrypt ESPN cookies -- there are none here -- and
# not about storage encryption, which stays on.

resource "aws_db_subnet_group" "main" {
  name       = "espn-edge"
  subnet_ids = [aws_subnet.db_private_a.id, aws_subnet.db_private_b.id]
}

resource "aws_db_parameter_group" "pg16" {
  name   = "espn-edge-pg16"
  family = "postgres16"

  # Forced row-level security is a property of the tables (revision 0005);
  # the parameter group only pins what the suite measured against.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "espn-edge"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 50
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "edge"
  username = "edge_owner"
  # Managed by RDS in Secrets Manager; never in state as plaintext, never in
  # a variable. The migrate task reads it from there.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.pg16.name
  # Unpinned by default (amendment C6): pinned to the host's AZ, the first
  # apply met InsufficientDBInstanceCapacity for db.t4g.micro/gp3 there and
  # RDS could not consider the subnet group's other AZ. Cross-AZ traffic
  # between host and database is $0.01/GB each way -- nothing at this scale.
  availability_zone      = var.db_availability_zone

  multi_az            = false
  publicly_accessible = false

  backup_retention_period = var.db_backup_retention_days
  backup_window           = "08:00-09:00" # UTC, 04:00 local; after the 03:00 sync window
  maintenance_window      = "Sun:09:00-Sun:10:00"
  copy_tags_to_snapshot   = true

  deletion_protection       = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "espn-edge-final"

  performance_insights_enabled = false # paid above the free 7-day tier on some classes; the catalog watches what matters
  auto_minor_version_upgrade   = true
}
