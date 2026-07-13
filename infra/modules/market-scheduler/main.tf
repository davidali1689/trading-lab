variable "name_prefix" {
  type = string
}

variable "lambda_function_arn" {
  type = string
}

variable "lambda_function_name" {
  type = string
}

variable "schedule_timezone" {
  type    = string
  default = "America/New_York"
}

variable "common_tags" {
  type    = map(string)
  default = {}
}

resource "aws_iam_role" "scheduler" {
  name = "${var.name_prefix}-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.common_tags
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "${var.name_prefix}-scheduler-invoke"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = var.lambda_function_arn
    }]
  })
}

resource "aws_lambda_permission" "scheduler" {
  statement_id  = "AllowEventBridgeScheduler"
  action        = "lambda:InvokeFunction"
  function_name = var.lambda_function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = "arn:aws:scheduler:*:*:schedule/${var.name_prefix}-*"
}

locals {
  schedules = {
    premarket = {
      description = "08:00 ET Mon-Fri prep (no entries)"
      expression  = "cron(0 8 ? * MON-FRI *)"
      input       = jsonencode({ phase = "premarket" })
    }
    rth_open_hour = {
      description = "Every minute 09:30-09:59 ET Mon-Fri"
      expression  = "cron(30-59 9 ? * MON-FRI *)"
      input       = jsonencode({ phase = "tick" })
    }
    rth_mid = {
      description = "Every minute 10:00-15:59 ET Mon-Fri"
      expression  = "cron(* 10-15 ? * MON-FRI *)"
      input       = jsonencode({ phase = "tick" })
    }
    eod = {
      description = "16:05 ET flatten + S3 journal persist"
      expression  = "cron(5 16 ? * MON-FRI *)"
      input       = jsonencode({ phase = "eod" })
    }
    postmarket = {
      description = "18:00 ET next-day prep then idle (no entries)"
      expression  = "cron(0 18 ? * MON-FRI *)"
      input       = jsonencode({ phase = "postmarket" })
    }
  }
}

resource "aws_scheduler_schedule" "market" {
  for_each = local.schedules

  name                         = "${var.name_prefix}-${each.key}"
  group_name                   = "default"
  state                        = "ENABLED"
  description                  = each.value.description
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = var.lambda_function_arn
    role_arn = aws_iam_role.scheduler.arn
    input    = each.value.input

    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 1
    }
  }
}

# Alarm if premarket Lambda errors (missed 08:00 wake)
resource "aws_cloudwatch_metric_alarm" "premarket_errors" {
  alarm_name          = "${var.name_prefix}-premarket-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_description   = "Premarket/worker Lambda errors — check 08:00 ET wake"

  dimensions = {
    FunctionName = var.lambda_function_name
  }

  tags = var.common_tags
}

output "schedule_names" {
  value = [for s in aws_scheduler_schedule.market : s.name]
}

output "premarket_alarm_name" {
  value = aws_cloudwatch_metric_alarm.premarket_errors.alarm_name
}
