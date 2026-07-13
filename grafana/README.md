# Grafana — trading-lab

Uses the **platform** Grafana Cloud Free stack (`aws-foundation` `modules/grafana-cloud`), not a per-app Cloud org.
Agent Factory / Mission Control is unrelated.

## Architecture

| Layer | Owner | Detail |
|-------|--------|--------|
| Cloud org, folder `Apps/trading-lab`, shared `cloudwatch` | aws-foundation | UID `apps-trading-lab` |
| Infinity `trading-lab-trades` / `trading-lab-skips` + dashboard | this repo | `infra/modules/grafana-dashboard` |
| CSV feed + EMF | Lambda | `/grafana/*.csv`, namespace `TradingLab` |

## One-time (platform)

Follow [`aws-foundation/modules/grafana-cloud/README.md`](../../aws-foundation/modules/grafana-cloud/README.md):

1. Grafana Cloud Free stack + service account token → secret `platform-grafana-cloud`
2. Link AWS account for CloudWatch
3. Foundation apply with `enable_grafana_provisioning = true` and `GRAFANA_URL` / `GRAFANA_AUTH`

## App wiring

1. Seed **feed** token in `trading-lab-vendor-keys` (merge; do not wipe Alpaca keys):

```json
"GRAFANA_FEED_TOKEN": "long-random-string"
```

2. Deploy apply with Grafana enabled (local or CI secrets — do not commit):

```hcl
enable_grafana     = true
grafana_feed_token = "..." # same as GRAFANA_FEED_TOKEN
```

```powershell
$env:GRAFANA_URL  = "https://YOURSTACK.grafana.net"
$env:GRAFANA_AUTH = "glsa_..."
# TF_VAR_grafana_url / TF_VAR_grafana_auth also work
```

3. Open folder **Apps / trading-lab** → dashboard **Trading Lab — Agent P&L**.

Function URL:

```
https://o5khd5m66qh6sbcodnzkvhm6re0uefds.lambda-url.us-east-1.on.aws/
```

Verify feed:

```powershell
$token = "..." # GRAFANA_FEED_TOKEN
Invoke-WebRequest "https://o5khd5m66qh6sbcodnzkvhm6re0uefds.lambda-url.us-east-1.on.aws/grafana/trades.csv" -Headers @{ "X-Grafana-Token" = $token }
```

404 until first EOD/postmarket persist (or `POST /run` `{"phase":"eod","force":true}`).

## Conventions (new apps)

| Item | Pattern |
|------|---------|
| Folder | `apps-<app>` |
| Infinity | `<app>-trades`, `<app>-skips` |
| Shared CW | `cloudwatch` |

Use the **grafana-app** skill in `dev-agent-team`.
