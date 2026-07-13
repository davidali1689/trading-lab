# Grafana — trading-lab

Near-live RTH activity + daily journal P&L in **Grafana Cloud Free**.
Agent Factory / Mission Control is unrelated — do not wire it here.

## Architecture

| Layer | Source | Refresh |
|-------|--------|---------|
| Tick activity | CloudWatch EMF namespace `TradingLab` (`TickCount`, `Orders`, `Skips`) | ~1 min during RTH |
| P&L / skips | S3 `grafana/latest/{trades,skips}.csv` via Function URL | After EOD 16:05 / postmarket 18:00 ET |

## One-time Grafana Cloud setup

1. Create a [Grafana Cloud Free](https://grafana.com/products/cloud/) stack.
2. Install plugin **Infinity** (`yesoreyeram-infinity-datasource`).
3. Add **CloudWatch** datasource (AWS account with read on `TradingLab` metrics, region `us-east-1`).
4. Add two Infinity datasources (UIDs must match the dashboard, or remount panels):

| UID | URL |
|-----|-----|
| `infinity-trades` | `https://<function-url>/grafana/trades.csv` |
| `infinity-skips` | `https://<function-url>/grafana/skips.csv` |

HTTP headers on both:

```
X-Grafana-Token: <same value as Secrets Manager GRAFANA_FEED_TOKEN>
```

Parse `pnl_usd`, `entry_px`, `exit_px`, `qty` as **number**. Time fields: `entry_ts` / `exit_ts` / `ts` (ISO8601).

5. Import [`dashboards/agent-pnl.json`](dashboards/agent-pnl.json).
6. Filter panels with template variable **`$agent`** (`found_by_agent`).

Function URL (prod):

```
https://4yfzwjgwvyubbj7ygf322scwgi0xmeld.lambda-url.us-east-1.on.aws/
```

## Seed the feed token

Keep Alpaca keys; add a random token:

```powershell
# merge with existing secret values — do not wipe Alpaca keys
aws secretsmanager get-secret-value --secret-id trading-lab-vendor-keys --query SecretString --output text
# then put-secret-value including GRAFANA_FEED_TOKEN
```

Example shape:

```json
{
  "ALPACA_API_KEY": "PK...",
  "ALPACA_API_SECRET": "...",
  "ALPACA_PAPER": "true",
  "FINNHUB_API_KEY": "",
  "UNUSUAL_WHALES_API_KEY": "",
  "GRAFANA_FEED_TOKEN": "long-random-string"
}
```

Lambda hydrates `GRAFANA_FEED_TOKEN` from `SECRET_ARN` on cold start.

## Verify feed

```powershell
$token = "..." # GRAFANA_FEED_TOKEN
$base = "https://4yfzwjgwvyubbj7ygf322scwgi0xmeld.lambda-url.us-east-1.on.aws"
Invoke-WebRequest "$base/grafana/trades.csv" -Headers @{ "X-Grafana-Token" = $token }
```

401 = bad/missing token. 404 = no EOD export yet (force persist after a tick day, or `POST /run` with `{"phase":"eod","force":true}`).

## Local export (optional)

```powershell
uv run python -c "from trading_lab.journal import export_journal_csv; print(export_journal_csv('data/journal.sqlite','data/grafana'))"
```

## CloudWatch metrics

Emitted on each RTH tick summary:

- Namespace: `TradingLab`
- Dimensions: `symbol`, `agent`, `status`
- Metrics: `TickCount`, `Orders`, `Skips`
