variable "name_prefix" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "lambda_memory" {
  type    = number
  default = 512
}

variable "lambda_timeout" {
  type    = number
  default = 120
}

variable "environment_variables" {
  type    = map(string)
  default = {}
}

variable "common_tags" {
  type    = map(string)
  default = {}
}

variable "journal_bucket_arn" {
  type    = string
  default = ""
}

locals {
  lambda_name = "${var.name_prefix}-worker"
}

resource "aws_iam_role" "lambda" {
  name = "${local.lambda_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "journal_s3" {
  name = "${local.lambda_name}-journal-s3"
  role = aws_iam_role.lambda.id

  # Always attach when module is used — count on ARN fails plan (known after apply).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListBucket"]
      Resource = [
        var.journal_bucket_arn,
        "${var.journal_bucket_arn}/*",
      ]
    }]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = 14
  tags              = var.common_tags
}

resource "aws_lambda_function" "worker" {
  function_name = local.lambda_name
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repository_url}:${var.image_tag}"
  memory_size   = var.lambda_memory
  timeout       = var.lambda_timeout
  architectures = ["x86_64"]

  environment {
    variables = var.environment_variables
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
  tags       = var.common_tags
}

resource "aws_lambda_function_url" "worker" {
  function_name      = aws_lambda_function.worker.function_name
  authorization_type = "NONE"
  invoke_mode        = "BUFFERED"
}

resource "aws_lambda_permission" "url" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.worker.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

output "lambda_function_arn" {
  value = aws_lambda_function.worker.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.worker.function_name
}

output "lambda_function_url" {
  value = aws_lambda_function_url.worker.function_url
}

output "lambda_role_name" {
  value = aws_iam_role.lambda.name
}
