# Grafana — trading-lab

Uses the **platform** Grafana Cloud Free stack (`aws-foundation` `modules/grafana-cloud`), not a per-app Cloud org.
Agent Factory / Mission Control is unrelated.

## Architecture

| Layer | Owner | Detail |
|-------|--------|--------|
| Cloud org, folder `Apps/trading-lab`, shared `cloudwatch` | aws-foundation | UID `apps-trading-lab` |
| Infinity `trading-lab-trades` / `trading-lab-skips` / `trading-lab-watchlist` + dashboard | this repo | `infra/modules/grafana-dashboard` |
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

2. Deploy apply with Grafana enabled (defaults on; CI loads creds from Secrets Manager):

```hcl
enable_grafana = true
```

## CI / Deploy

GHA loads `GRAFANA_CLOUD_URL` + `GRAFANA_SERVICE_ACCOUNT_TOKEN` from Secrets Manager
`platform-grafana-cloud`, and `GRAFANA_FEED_TOKEN` from `trading-lab-vendor-keys`.
No GitHub Grafana secrets required. `enable_grafana` defaults to `true`.

Local override still works:

```powershell
$env:TF_VAR_grafana_url  = "https://YOURSTACK.grafana.net"
$env:TF_VAR_grafana_auth = "glsa_..."
```

3. Open folder **Apps / trading-lab** → dashboard **Trading Lab — Agent P&L** (includes **Daily watchlist** panel).

Function URL:

```
https://o5khd5m66qh6sbcodnzkvhm6re0uefds.lambda-url.us-east-1.on.aws/
```

Verify feeds:

```powershell
$token = "..." # GRAFANA_FEED_TOKEN
$base = "https://o5khd5m66qh6sbcodnzkvhm6re0uefds.lambda-url.us-east-1.on.aws"
Invoke-WebRequest "$base/grafana/trades.csv" -Headers @{ "X-Grafana-Token" = $token }
Invoke-WebRequest "$base/grafana/watchlist.csv" -Headers @{ "X-Grafana-Token" = $token }
```

Trades/skips 404 until first EOD/postmarket persist. Watchlist CSV is written on each premarket/postmarket scan (or falls back from `watchlists/latest.json`).

## Mobile / desktop apps

Grafana Labs does **not** ship a full dashboards mobile app. Use the browser:

- Phone: open `https://goldcaiman1684.grafana.net` → add to home screen
- Desktop: same URL in Chrome/Edge

The App Store / Play Store **Grafana IRM** app is only for on-call alerts, not dashboards.

## Conventions (new apps)

| Item | Pattern |
|------|---------|
| Folder | `apps-<app>` |
| Infinity | `<app>-trades`, `<app>-skips` |
| Shared CW | `cloudwatch` |

Use the **grafana-app** skill in `dev-agent-team`.
