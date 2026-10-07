# The ten alarms from Phase 41's catalog, read from the JSON that
# ops/render_alarms.py generates -- one source of truth, pinned by test. Each
# treats missing data as breaching: amendment C1, "silence is now an alarm
# condition". Until the application's CloudWatch sink publishes (the half
# of Phase 41 this environment could not do), every one of these will sit
# in ALARM. That is correct, and it is the signal that the sink is next.

locals {
  alarm_catalog = jsondecode(file("${path.module}/alarms.generated.json")).alarms
}

resource "aws_sns_topic" "alarms" {
  name = "espn-edge-alarms"
}

resource "aws_sns_topic_subscription" "operator" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.operator_email
}

resource "aws_cloudwatch_metric_alarm" "catalog" {
  for_each = local.alarm_catalog

  alarm_name          = "espn-edge-${each.value.AlarmName}"
  alarm_description   = each.value.AlarmDescription
  namespace           = var.metrics_namespace
  metric_name         = each.value.MetricName
  statistic           = each.value.Statistic
  comparison_operator = each.value.ComparisonOperator
  threshold           = each.value.Threshold
  period              = each.value.Period
  evaluation_periods  = each.value.EvaluationPeriods
  datapoints_to_alarm = each.value.DatapointsToAlarm
  treat_missing_data  = each.value.TreatMissingData
  unit                = each.value.Unit == "None" ? null : each.value.Unit

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

# Three host/database alarms that no application code can publish, because
# they are about the box the code runs on.
resource "aws_cloudwatch_metric_alarm" "host_status" {
  alarm_name          = "espn-edge-host-status-check"
  alarm_description   = "The EC2 host is failing status checks. SSM Session Manager in; if the host is gone, amendment C4's cutover is the recovery."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  treat_missing_data  = "breaching"
  dimensions          = { InstanceId = aws_instance.host.id }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu_credits" {
  alarm_name          = "espn-edge-db-cpu-credit-balance"
  alarm_description   = "db.t4g.micro burst credits are nearly spent; the database is about to be throttled. Amendment C2."
  namespace           = "AWS/RDS"
  metric_name         = "CPUCreditBalance"
  statistic           = "Minimum"
  comparison_operator = "LessThanThreshold"
  threshold           = 20
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  treat_missing_data  = "breaching"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_free_storage" {
  alarm_name          = "espn-edge-db-free-storage"
  alarm_description   = "Under 2 GiB free. Autoscaling storage is on up to 50 GiB; past that is a decision, not a knob."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  comparison_operator = "LessThanThreshold"
  threshold           = 2147483648
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  treat_missing_data  = "breaching"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
