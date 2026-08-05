# Push-based error alerting: ERROR log filter + alarm → SNS email.
# Catches caught exceptions too — the filter reads the log stream, not the
# Lambda Errors metric (a caught Bedrock failure never increments Errors).
# Spec: second-brain projects/trading-lab/decisions/2026-08-04-cloudwatch-error-alerting.md

variable "name_prefix" {
  type = string
}

variable "alert_email" {
  description = "SNS email subscription endpoint. Empty = topic only, no subscription."
  type        = string
  default     = ""
}

variable "lambda_functions" {
  description = "Map of alarm-name slug => Lambda function name (e.g. { worker = \"...-worker\" })."
  type        = map(string)
}

variable "error_log_pattern" {
  description = "CloudWatch Logs filter pattern (space-separated ?terms are OR'd; multi-word phrases must be quoted — unquoted '28' alone substring-matches Lambda REPORT lines)."
  type        = string
  default     = "?ERROR ?Traceback ?\"Errno 28\" ?ValidationException"
}

variable "common_tags" {
  type    = map(string)
  default = {}
}

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-alerts"
  tags = var.common_tags
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alert_email != "" ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each = var.lambda_functions

  name           = "${var.name_prefix}-${each.key}-errors"
  log_group_name = "/aws/lambda/${each.value}"
  pattern        = var.error_log_pattern

  metric_transformation {
    name      = "${each.key}-error-count"
    namespace = var.name_prefix
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each = var.lambda_functions

  alarm_name          = "${var.name_prefix}-${each.key}-errors"
  alarm_description   = "ERROR/Traceback/Errno 28/ValidationException in /aws/lambda/${each.value} log stream"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = aws_cloudwatch_log_metric_filter.errors[each.key].metric_transformation[0].name
  namespace           = var.name_prefix
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  tags = var.common_tags
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  value = [for a in aws_cloudwatch_metric_alarm.errors : a.alarm_name]
}
