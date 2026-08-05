# Unattended market clock: EventBridge Scheduler → Lambda worker.
# Backend: local scripts/deploy_local.py (S3 state + DynamoDB lock).
# State key: apps/trading-lab/dev/terraform.tfstate

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }

  backend "s3" {
    bucket         = "platform-tfstate-b667becb"
    key            = "apps/trading-lab/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "platform-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Application = "trading-lab"
      Repo        = "trading-lab"
      Project     = "trading-lab"
      ManagedBy   = "opentofu"
      Environment = var.environment
      CostCenter  = "trading-lab"
      Client      = "internal"
    }
  }
}

data "aws_secretsmanager_secret_version" "vendor_keys" {
  secret_id = "trading-lab-vendor-keys"
}

locals {
  image_uri = var.image_uri != "" ? var.image_uri : (
    var.ecr_repository_url != "" ? "${var.ecr_repository_url}:${var.image_tag}" : ""
  )
  # Application + Repo required for Bedrock IAM-principal cost attribution.
  common_tags = {
    Application = "trading-lab"
    Repo        = "trading-lab"
    Project     = "trading-lab"
    Environment = var.environment
    CostCenter  = "trading-lab"
    Client      = "internal"
    ManagedBy   = "opentofu"
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "name_prefix" {
  type    = string
  default = "trading-lab"
}

variable "ecr_repository_url" {
  type    = string
  default = ""
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "image_uri" {
  type    = string
  default = ""
}

variable "watchlist_size" {
  description = "Max symbols from daily Alpaca movers/actives scan (no hardcoded names)."
  type        = number
  default     = 12
}

variable "enable_coach_iam" {
  description = "Create dedicated strategy-coach IAM role (needs iam:CreateRole)."
  type        = bool
  default     = false
}

variable "kill_switch" {
  type    = string
  default = "0"
}

variable "alert_email" {
  description = "SNS email subscription for CloudWatch error alerts. Empty = topic only, no subscription."
  type        = string
  default     = "davidali1689@gmail.com"
}

module "journal_bucket" {
  source      = "./modules/journal-bucket"
  name_prefix = var.name_prefix
  common_tags = local.common_tags
}

module "vendor_secrets" {
  source      = "./modules/vendor-secrets"
  name_prefix = var.name_prefix
  common_tags = local.common_tags
}

# Optional: dedicated coach IAM for future AgentCore.
# v1 coaches run on the Lambda worker role (tagged Application/Repo/BedrockCaller).
module "coach_iam" {
  count              = var.enable_coach_iam ? 1 : 0
  source             = "./modules/coach-iam"
  name_prefix        = var.name_prefix
  common_tags        = local.common_tags
  journal_bucket_arn = module.journal_bucket.bucket_arn
}

module "lambda_worker" {
  source = "./modules/lambda-worker"
  count  = local.image_uri != "" ? 1 : 0

  name_prefix        = var.name_prefix
  ecr_repository_url = var.ecr_repository_url != "" ? var.ecr_repository_url : split(":", local.image_uri)[0]
  image_tag          = var.image_tag
  lambda_memory      = 512
  lambda_timeout     = 300
  common_tags        = local.common_tags
  journal_bucket_arn = module.journal_bucket.bucket_arn
  vendor_secret_arn  = module.vendor_secrets.secret_arn
  environment_variables = {
    TRADING_MODE      = "paper"
    USE_MOCK_BARS     = "false"
    WATCHLIST_SIZE    = tostring(var.watchlist_size)
    JOURNAL_PATH      = "/tmp/trading-lab-journal.sqlite"
    JOURNAL_S3_BUCKET = module.journal_bucket.bucket_name
    KILL_SWITCH       = var.kill_switch
    TZ                = "UTC"
    SECRET_ARN        = module.vendor_secrets.secret_arn
    ALPACA_PAPER      = "true"
    # Live coaches for Fri weekend pack (CI still sets MOCK_BEDROCK=true in tests).
    MOCK_BEDROCK            = "false"
    BEDROCK_MODEL_ID        = "amazon.nova-lite-v1:0"
    COACH_MODEL_ID          = "xai.grok-4.3"
    COACH_FALLBACK_MODEL_ID = "moonshot.kimi-k2-thinking"
    COACH_AWS_REGION        = "us-east-1"
    COACH_REASONING_EFFORT  = "high"
    COACH_MISS_DAYS         = "5" # Mon–Fri harvest shards per strategy coach
    MISS_HARVEST_TOP_N      = "20"
  }
}

module "alerts" {
  source = "./modules/cloudwatch-alerts"
  count  = local.image_uri != "" ? 1 : 0

  name_prefix      = var.name_prefix
  alert_email      = var.alert_email
  lambda_functions = { worker = module.lambda_worker[0].lambda_function_name }
  common_tags      = local.common_tags
}

module "market_scheduler" {
  source = "./modules/market-scheduler"
  count  = local.image_uri != "" ? 1 : 0

  name_prefix          = var.name_prefix
  lambda_function_arn  = module.lambda_worker[0].lambda_function_arn
  lambda_function_name = module.lambda_worker[0].lambda_function_name
  schedule_timezone    = "America/New_York"
  alarm_actions        = [module.alerts[0].sns_topic_arn]
  common_tags          = local.common_tags
}

output "lambda_function_url" {
  value = try(module.lambda_worker[0].lambda_function_url, null)
}

output "journal_bucket" {
  value = module.journal_bucket.bucket_name
}

output "strategy_coach_role_arn" {
  description = "Tagged IAM role for strategy coaches / future AgentCore (Bedrock cost attribution)."
  value       = try(module.coach_iam[0].coach_role_arn, null)
}

output "vendor_secret_arn" {
  value = module.vendor_secrets.secret_arn
}

output "vendor_secret_name" {
  value = module.vendor_secrets.secret_name
}

output "schedule_names" {
  value = try(module.market_scheduler[0].schedule_names, [])
}

output "premarket_alarm" {
  value = try(module.market_scheduler[0].premarket_alarm_name, null)
}

output "alert_topic_arn" {
  value = try(module.alerts[0].sns_topic_arn, null)
}

output "alert_alarms" {
  value = try(module.alerts[0].alarm_names, [])
}

output "auto_run_note" {
  value = "ET Mon-Fri: 08:00 prep, 09:30-16:00 ticks, 16:05 eod, 18:00 postmarket+miss harvest; Fri 18:05 weekly coaches. Entries RTH only."
}
