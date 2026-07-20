# Dedicated tagged IAM role for future AgentCore coach runtimes.
# v1 Friday coaches run on the Lambda worker role; this role is ready when
# AgentCore runtimes are wired (same Application/Repo tags for cost tracking).

variable "name_prefix" {
  type = string
}

variable "common_tags" {
  type    = map(string)
  default = {}
}

variable "journal_bucket_arn" {
  type = string
}

locals {
  role_name = "${var.name_prefix}-strategy-coach"
}

resource "aws_iam_role" "coach" {
  name = local.role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
      {
        Effect    = "Allow"
        Principal = { Service = "bedrock-agentcore.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(var.common_tags, {
    Application   = try(var.common_tags["Application"], var.name_prefix)
    Repo          = try(var.common_tags["Repo"], var.name_prefix)
    BedrockCaller = "true"
    Workload      = "strategy-coach"
    AgentFamily   = "improvement"
  })
}

resource "aws_iam_role_policy" "coach_bedrock" {
  name = "${local.role_name}-bedrock"
  role = aws_iam_role.coach.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:Converse",
        "bedrock:InvokeModelWithResponseStream",
      ]
      Resource = [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:*:inference-profile/*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "coach_s3" {
  name = "${local.role_name}-misses-s3"
  role = aws_iam_role.coach.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
      ]
      Resource = [
        var.journal_bucket_arn,
        "${var.journal_bucket_arn}/*",
      ]
    }]
  })
}

output "coach_role_arn" {
  value = aws_iam_role.coach.arn
}

output "coach_role_name" {
  value = aws_iam_role.coach.name
}
