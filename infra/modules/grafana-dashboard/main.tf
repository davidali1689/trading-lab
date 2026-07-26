# App-owned Grafana: Infinity CSV feeds + dashboard into platform folder Apps/<app>.
# Requires GRAFANA_URL + GRAFANA_AUTH (or variables). Feed token stays in app secrets.

terraform {
  required_providers {
    grafana = {
      source = "grafana/grafana"
    }
  }
}

variable "app_name" {
  type    = string
  default = "trading-lab"
}

variable "folder_uid" {
  description = "Platform folder UID (apps-<app>)."
  type        = string
  default     = "apps-trading-lab"
}

variable "function_url" {
  description = "Lambda Function URL base (trailing slash optional)."
  type        = string
}

variable "grafana_feed_token" {
  description = "X-Grafana-Token value (from trading-lab-vendor-keys)."
  type        = string
  sensitive   = true
}

variable "dashboard_json_path" {
  description = "Path to dashboard JSON file."
  type        = string
}

variable "enable" {
  type    = bool
  default = true
}

locals {
  base           = trimsuffix(var.function_url, "/")
  feed_host      = trimprefix(local.base, "https://")
  trades_uid      = "${var.app_name}-trades"
  skips_uid       = "${var.app_name}-skips"
  watchlist_uid   = "${var.app_name}-watchlist"
  postmortem_uid  = "${var.app_name}-postmortem"
  scoreboard_uid  = "${var.app_name}-scoreboard"
  infinity_json = {
    auth_method  = "apiKey"
    apiKeyKey    = "X-Grafana-Token"
    apiKeyType   = "header"
    httpMethod   = "GET"
    allowedHosts = [local.feed_host, "https://${local.feed_host}"]
  }
}

resource "grafana_data_source" "trades" {
  count = var.enable ? 1 : 0

  type = "yesoreyeram-infinity-datasource"
  name = "${var.app_name} trades CSV"
  uid  = local.trades_uid
  url  = "${local.base}/grafana/trades.csv"

  json_data_encoded = jsonencode(local.infinity_json)
  secure_json_data_encoded = jsonencode({
    apiKeyValue = var.grafana_feed_token
  })

  lifecycle {
    precondition {
      condition     = length(var.grafana_feed_token) > 0
      error_message = "grafana_feed_token must be non-empty (GRAFANA_FEED_TOKEN in trading-lab-vendor-keys)."
    }
  }
}

resource "grafana_data_source" "skips" {
  count = var.enable ? 1 : 0

  type = "yesoreyeram-infinity-datasource"
  name = "${var.app_name} skips CSV"
  uid  = local.skips_uid
  url  = "${local.base}/grafana/skips.csv"

  json_data_encoded = jsonencode(local.infinity_json)
  secure_json_data_encoded = jsonencode({
    apiKeyValue = var.grafana_feed_token
  })

  lifecycle {
    precondition {
      condition     = length(var.grafana_feed_token) > 0
      error_message = "grafana_feed_token must be non-empty (GRAFANA_FEED_TOKEN in trading-lab-vendor-keys)."
    }
  }
}

resource "grafana_data_source" "watchlist" {
  count = var.enable ? 1 : 0

  type = "yesoreyeram-infinity-datasource"
  name = "${var.app_name} watchlist JSON"
  uid  = local.watchlist_uid
  url  = "${local.base}/grafana/watchlist.json"

  json_data_encoded = jsonencode(local.infinity_json)
  secure_json_data_encoded = jsonencode({
    apiKeyValue = var.grafana_feed_token
  })

  lifecycle {
    precondition {
      condition     = length(var.grafana_feed_token) > 0
      error_message = "grafana_feed_token must be non-empty (GRAFANA_FEED_TOKEN in trading-lab-vendor-keys)."
    }
  }
}

resource "grafana_data_source" "postmortem" {
  count = var.enable ? 1 : 0

  type = "yesoreyeram-infinity-datasource"
  name = "${var.app_name} postmortem JSON"
  uid  = local.postmortem_uid
  url  = "${local.base}/grafana/postmortem.json"

  json_data_encoded = jsonencode(local.infinity_json)
  secure_json_data_encoded = jsonencode({
    apiKeyValue = var.grafana_feed_token
  })

  lifecycle {
    precondition {
      condition     = length(var.grafana_feed_token) > 0
      error_message = "grafana_feed_token must be non-empty (GRAFANA_FEED_TOKEN in trading-lab-vendor-keys)."
    }
  }
}

resource "grafana_data_source" "scoreboard" {
  count = var.enable ? 1 : 0

  type = "yesoreyeram-infinity-datasource"
  name = "${var.app_name} scoreboard JSON"
  uid  = local.scoreboard_uid
  url  = "${local.base}/grafana/scoreboard.json"

  json_data_encoded = jsonencode(local.infinity_json)
  secure_json_data_encoded = jsonencode({
    apiKeyValue = var.grafana_feed_token
  })

  lifecycle {
    precondition {
      condition     = length(var.grafana_feed_token) > 0
      error_message = "grafana_feed_token must be non-empty (GRAFANA_FEED_TOKEN in trading-lab-vendor-keys)."
    }
  }
}

resource "grafana_dashboard" "agent_pnl" {
  count = var.enable ? 1 : 0

  folder      = var.folder_uid
  config_json = file(var.dashboard_json_path)
  overwrite   = true

  depends_on = [
    grafana_data_source.trades,
    grafana_data_source.skips,
    grafana_data_source.watchlist,
    grafana_data_source.postmortem,
    grafana_data_source.scoreboard,
  ]
}

output "trades_uid" {
  value = try(grafana_data_source.trades[0].uid, local.trades_uid)
}

output "skips_uid" {
  value = try(grafana_data_source.skips[0].uid, local.skips_uid)
}

output "watchlist_uid" {
  value = try(grafana_data_source.watchlist[0].uid, local.watchlist_uid)
}

output "postmortem_uid" {
  value = try(grafana_data_source.postmortem[0].uid, local.postmortem_uid)
}

output "scoreboard_uid" {
  value = try(grafana_data_source.scoreboard[0].uid, local.scoreboard_uid)
}

output "dashboard_uid" {
  value = try(grafana_dashboard.agent_pnl[0].uid, "trading-lab-agent-pnl")
}
