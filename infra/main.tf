# Unattended market clock: EventBridge Scheduler → Lambda worker.
# Backend configured by cicd-templates deploy job / local -backend-config.
# State key: apps/trading-lab/dev/terraform.tfstate

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    grafana = {
      source  = "grafana/grafana"
      version = "~> 3.25"
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
      Project     = "trading-lab"
      ManagedBy   = "opentofu"
      Environment = var.environment
      CostCenter  = "trading-lab"
      Client      = "internal"
    }
  }
}

# Dummy URL/auth when enable_grafana=false so plan works without Cloud credentials.
provider "grafana" {
  url  = var.grafana_url != "" ? var.grafana_url : "https://grafana.invalid"
  auth = var.grafana_auth != "" ? var.grafana_auth : "disabled"
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

variable "kill_switch" {
  type    = string
  default = "0"
}

variable "enable_grafana" {
  description = "Provision Infinity datasources + dashboard into platform Grafana Cloud."
  type        = bool
  default     = false
}

variable "grafana_url" {
  type      = string
  default   = ""
  sensitive = true
}

variable "grafana_auth" {
  type      = string
  default   = ""
  sensitive = true
}

variable "grafana_feed_token" {
  description = "Same value as Secrets Manager GRAFANA_FEED_TOKEN."
  type        = string
  default     = ""
  sensitive   = true
}

variable "grafana_folder_uid" {
  type    = string
  default = "apps-trading-lab"
}

locals {
  image_uri = var.image_uri != "" ? var.image_uri : (
    var.ecr_repository_url != "" ? "${var.ecr_repository_url}:${var.image_tag}" : ""
  )
  common_tags = {
    Project     = "trading-lab"
    Environment = var.environment
    CostCenter  = "trading-lab"
    Client      = "internal"
    ManagedBy   = "opentofu"
  }
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

module "lambda_worker" {
  source = "./modules/lambda-worker"
  count  = local.image_uri != "" ? 1 : 0

  name_prefix        = var.name_prefix
  ecr_repository_url = var.ecr_repository_url != "" ? var.ecr_repository_url : split(":", local.image_uri)[0]
  image_tag          = var.image_tag
  lambda_memory      = 512
  lambda_timeout     = 180
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
  }
}

module "market_scheduler" {
  source = "./modules/market-scheduler"
  count  = local.image_uri != "" ? 1 : 0

  name_prefix          = var.name_prefix
  lambda_function_arn  = module.lambda_worker[0].lambda_function_arn
  lambda_function_name = module.lambda_worker[0].lambda_function_name
  schedule_timezone    = "America/New_York"
  common_tags          = local.common_tags
}

module "grafana_dashboard" {
  source = "./modules/grafana-dashboard"
  count  = var.enable_grafana && local.image_uri != "" ? 1 : 0

  app_name            = var.name_prefix
  folder_uid          = var.grafana_folder_uid
  function_url        = module.lambda_worker[0].lambda_function_url
  grafana_feed_token  = var.grafana_feed_token
  dashboard_json_path = "${path.module}/../grafana/dashboards/agent-pnl.json"
  enable              = true
}

output "lambda_function_url" {
  value = try(module.lambda_worker[0].lambda_function_url, null)
}

output "journal_bucket" {
  value = module.journal_bucket.bucket_name
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

output "auto_run_note" {
  value = "ET Mon-Fri: 08:00 prep, 09:30-16:00 ticks, 16:05 eod, 18:00 next-day prep. Entries RTH only."
}

output "grafana_trades_uid" {
  value = try(module.grafana_dashboard[0].trades_uid, null)
}

output "grafana_skips_uid" {
  value = try(module.grafana_dashboard[0].skips_uid, null)
}
