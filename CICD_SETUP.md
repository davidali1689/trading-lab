# CI/CD setup (trading-lab)

**Local-only as of 2026-08-04 — GitHub Actions removed** (`.github/workflows/` deleted).
Everything runs from this repo on the workstation via `scripts/deploy_local.py`.

## Flow

```powershell
# Gate (run before any apply/destroy)
python scripts/pre_deploy_check.py

# CI + plan (no changes)
python scripts/deploy_local.py

# Ship infra changes (reuses current ECR image)
python scripts/deploy_local.py --skip-ci --apply

# Ship code changes (needs local Docker for image build+push)
python scripts/deploy_local.py --build --apply
```

What it does: `ruff check` + `ruff format --check` + `pytest` → optional
`docker build/push` (ECR tag = UTC timestamp) → `tofu fmt/init/validate/plan`
→ `--apply` runs `tofu apply` → `/health` smoke on the Lambda Function URL.

Without `--build`/`--image-tag`, the deploy reuses the latest tag already in
ECR — correct for infra-only patches where the running image stays the same.

## State

S3 backend bucket is local-only: copy `infra/backend.hcl.example` → `infra/backend.hcl`.
State key `apps/trading-lab/dev/terraform.tfstate`, DynamoDB lock table `platform-tflock`.
Alert email (optional): `infra/local.tfvars` with `alert_email = "you@example.com"`.

## Vendor secrets (AWS Secrets Manager)

OpenTofu creates secret shell **`trading-lab-vendor-keys`** and sets Lambda env `SECRET_ARN`.
Values are **not** in git or TF state — seed after apply:

```powershell
aws secretsmanager put-secret-value --secret-id trading-lab-vendor-keys --secret-string '{
  "ALPACA_API_KEY":"PK...",
  "ALPACA_API_SECRET":"...",
  "ALPACA_PAPER":"true",
  "FINNHUB_API_KEY":"",
  "UNUSUAL_WHALES_API_KEY":""
}'
```

Locally: copy `.env.example` → `.env` (leave `SECRET_ARN` unset).

## Grafana

Removed 2026-08-04 (dashboards, Infinity datasources, `/grafana/*` feed endpoints,
CSV exports). Journal lives in S3 (`journals/`, `scorecards/`, `scoreboards/`,
`proposals/`, `misses/`) — read directly or via the terminal tools.
