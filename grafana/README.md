# Grafana — trading-lab

Uses the **platform** Grafana Cloud Free stack (`aws-foundation` `modules/grafana-cloud`), not a per-app Cloud org.
Agent Factory / Mission Control is unrelated.

## Architecture

| Layer | Owner | Detail |
|-------|--------|--------|
| Cloud org, folder `Apps/trading-lab`, shared `cloudwatch` | aws-foundation | UID `apps-trading-lab` |
| Infinity `trading-lab-trades` / `-skips` / `-watchlist` / `-postmortem` + dashboard | this repo | `infra/modules/grafana-dashboard` |
| CSV/JSON feed + EMF | Lambda | `/grafana/*`, namespace `TradingLab` |

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

2. Deploy apply with Grafana enabled on **main** only (`name_prefix = trading-lab`). Feature stacks skip Grafana so they cannot overwrite the shared dashboard UID.

```hcl
enable_grafana = true
```

## CI / Deploy

GHA loads `GRAFANA_CLOUD_URL` + `GRAFANA_SERVICE_ACCOUNT_TOKEN` from Secrets Manager
`platform-grafana-cloud`, and `GRAFANA_FEED_TOKEN` from `trading-lab-vendor-keys`.
No GitHub Grafana secrets required. `enable_grafana` defaults to `true` on main.

Push path filters include `grafana/**` so dashboard-only changes trigger Deploy.

Local override still works:

```powershell
$env:TF_VAR_grafana_url  = "https://YOURSTACK.grafana.net"
$env:TF_VAR_grafana_auth = "glsa_..."
```

3. Open folder **Apps / trading-lab** → dashboard **Trading Lab - Agent P&L**.
   - **Daily watchlist** panel uses Infinity JSON (`/grafana/watchlist.json`, `root_selector: rows`).
   - Trade stats use **latest journal CSV** (`pnl_booked_usd` / `is_closed`) — all-time snapshot, not dashboard time-range filters.
   - **EOD postmortem** uses `/grafana/postmortem.json`.
   - After Deploy apply, confirm Infinity datasources still have `X-Grafana-Token` = `GRAFANA_FEED_TOKEN`.

Function URL (from tofu output `lambda_function_url`, not a hard-coded host):

```powershell
# After apply:
# tofu -chdir=infra output -raw lambda_function_url
$base = "<lambda_function_url>"   # no trailing slash
$token = "..."                    # GRAFANA_FEED_TOKEN from trading-lab-vendor-keys
Invoke-WebRequest "$base/grafana/trades.csv" -Headers @{ "X-Grafana-Token" = $token }
Invoke-WebRequest "$base/grafana/watchlist.json" -Headers @{ "X-Grafana-Token" = $token }
Invoke-WebRequest "$base/grafana/postmortem.json" -Headers @{ "X-Grafana-Token" = $token }
```

Trades/skips return **header-only CSV** until first journal persist (panels stay green). Watchlist JSON is live from `get_watchlist()`. Postmortem returns an empty stub until first EOD.

Grafana Cloud URL: use the stack host from secret `platform-grafana-cloud` / tofu env (not hard-coded in this README).

## Mobile / desktop apps

Grafana Labs does **not** ship a full dashboards mobile app. Use the browser:

- Phone: open your Grafana Cloud stack URL → add to home screen
- Desktop: same URL in Chrome/Edge

The App Store / Play Store **Grafana IRM** app is only for on-call alerts, not dashboards.

## Conventions (new apps)

| Item | Pattern |
|------|---------|
| Folder | `apps-<app>` |
| Infinity | `<app>-trades`, `<app>-skips`, `<app>-watchlist`, `<app>-postmortem` |
| Shared CW | `cloudwatch` |
| Provision | main / canonical `name_prefix` only |

Use the **grafana-app** skill in `dev-agent-team`.
