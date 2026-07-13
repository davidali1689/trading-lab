# Vendor API keys shell — values seeded outside OpenTofu (CLI/Console).
# Secret name pattern: {name_prefix}-vendor-keys (matches aws-cicd *-vendor-keys*).

variable "name_prefix" {
  type = string
}

variable "common_tags" {
  type    = map(string)
  default = {}
}

resource "aws_secretsmanager_secret" "vendor_keys" {
  name                    = "${var.name_prefix}-vendor-keys"
  description             = "Alpaca / Finnhub / Unusual Whales keys for ${var.name_prefix}"
  recovery_window_in_days = 7
  tags                    = var.common_tags
}

# Placeholder only — real values via `aws secretsmanager put-secret-value`.
resource "aws_secretsmanager_secret_version" "vendor_keys" {
  secret_id = aws_secretsmanager_secret.vendor_keys.id
  secret_string = jsonencode({
    ALPACA_API_KEY         = ""
    ALPACA_API_SECRET      = ""
    ALPACA_PAPER           = "true"
    FINNHUB_API_KEY        = ""
    UNUSUAL_WHALES_API_KEY = ""
    GRAFANA_FEED_TOKEN     = ""
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

output "secret_arn" {
  value = aws_secretsmanager_secret.vendor_keys.arn
}

output "secret_name" {
  value = aws_secretsmanager_secret.vendor_keys.name
}
