# CI/CD setup (trading-lab)

Bootstrapped from cicd-templates @v1.0.6.

## GitHub

1. Create Environment **dev** (optional: required reviewers for apply).
2. Repo secret **AWS_ROLE_ARN** = `aws-cicd` output `github_actions_app_deploy_role_arn`
3. Repo secret **AWS_APPLY_ROLE_ARN** = `aws-cicd` output `github_actions_app_deploy_apply_role_arn` (optional but recommended)
4. Optional var **ECR_REGISTRY** = `ACCOUNT.dkr.ecr.us-east-1.amazonaws.com`
5. Ensure this repo is listed in `github_actions_deploy_repos` (`aws-cicd`).
   State keys: `apps/<app-name>/dev|feat-<slug>/terraform.tfstate`.

## Vendor secrets (AWS Secrets Manager)

OpenTofu creates secret shell **`trading-lab-vendor-keys`** and sets Lambda env `SECRET_ARN`.
Values are **not** in git or TF state — seed after apply:

```powershell
aws secretsmanager put-secret-value --secret-id trading-lab-vendor-keys --secret-string '{
  "ALPACA_API_KEY":"PK...",
  "ALPACA_API_SECRET":"...",
  "ALPACA_PAPER":"true",
  "FINNHUB_API_KEY":"",
  "UNUSUAL_WHALES_API_KEY":"",
  "GRAFANA_FEED_TOKEN":"long-random-string"
}'
```

Locally: copy `.env.example` → `.env` (leave `SECRET_ARN` unset).

Then set `USE_MOCK_BARS=false` on the Lambda (OpenTofu default) so ticks use
Alpaca IEX bars + **Alpaca paper** bracket orders against the $100k sim account.
Unusual Whales stays off until you subscribe.

## Grafana

See [`grafana/README.md`](grafana/README.md) — Cloud Free + Infinity CSV feed + CloudWatch `TradingLab` EMF.
Dashboard: [`grafana/dashboards/agent-pnl.json`](grafana/dashboards/agent-pnl.json).

## Layout expected

- `api/` FastAPI app
- `tests/`
- `infra/` OpenTofu (lambda-worker + vendor-secrets)
- `grafana/` dashboards + setup notes
- `Dockerfile` (Lambda Web Adapter)
- `pyproject.toml` + `uv.lock`

## Pin

Reusable workflows: `davidali1689/cicd-templates/...@v1.0.6`
