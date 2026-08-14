# trading-lab

Paper trading lab: rule-based sniper + swing agents on Alpaca, scheduled via EventBridge → Lambda, journaled to S3, with a **missed-gainer improvement loop**.

## Session clock (America/New_York)

| When | Phase | What runs |
|------|--------|-----------|
| 08:00 Mon–Fri | `premarket` | Build watchlist (Alpaca movers/actives, min price $5) |
| 09:30–16:00 | `tick` | Sniper + swing evaluates (entries RTH only) |
| 16:05 | `eod` | Flatten snipers, persist journal, EOD postmortem |
| 18:00 | `postmarket` | Next-day watchlist + **miss harvest → S3** |
| Fri 18:05 | `weekly_coaches` | **Weekend pack:** scorecard (better/worse) + **strategy coaches** → S3 |

Entries never run after hours. `never_force_trade` and risk guardrails are immutable.

## Strategies (`found_by_agent`)

- `large_cap_sniper`
- `mid_cap_sniper`
- `speculative_sniper`
- `gainer_sniper`
- `swing_momentum`

See [`docs/agents.md`](docs/agents.md).

## Missed-gainer loop

1. **Daily harvest** (deterministic, no LLM): top liquid gainers vs watchlist/journal → buckets  
   - **A** never on watchlist  
   - **B** watched / skipped, no ENTER  
   - **C** traded but weak capture vs the move  
   Written to `s3://$JOURNAL_S3_BUCKET/misses/{day}/` (+ `by_agent/{agent_id}.json`).
2. **Friday weekend pack** (`weekly_coaches` 18:05):
   - **Scorecard** (deterministic): per-strategy capture rate + expectancy + drawdown → `improving` / `flat` / `worse` vs prior week → `scorecards/{week}.json`. `propose_revert` is a **flag only** (no auto-rollback).
   - **Strategy coaches** (Grok 4.3 `effort=high`): each reads misses + its scorecard slice → `proposals/{week}/{agent_id}.json` (`pending_green_light`).
3. **You** review over the weekend; overlay apply is manual — coaches cannot submit orders.

Coach model env: `COACH_MODEL_ID=xai.grok-4.3` (Mantle), fallback `moonshot.kimi-k2-thinking`, `COACH_MISS_DAYS=5` (each coach reads up to 5 daily `misses/{day}/by_agent/{agent}.json` files). Production: `MOCK_BEDROCK=false`.

## Cost tags (Bedrock)

IAM roles that call Bedrock are tagged for Cost Explorer / CUR principal attribution:

| Tag | Value |
|-----|--------|
| `Application` | `trading-lab` |
| `Repo` | `trading-lab` |
| `BedrockCaller` | `true` |
| `Workload` | `trading-lab-worker` or `strategy-coach` |

Roles: Lambda worker role + `trading-lab-strategy-coach` (future AgentCore). Activate these as cost allocation tags in Billing after the first Bedrock call (24–48h).

## Layout

- `api/` — FastAPI worker (`/events`, `/run`)
- `src/trading_lab/` — agents, eval, journal, improvement (harvest + coaches)
- `infra/` — OpenTofu (Lambda, scheduler, journal bucket, coach IAM)
- `tests/`
- `grafana/` — dashboards ([`grafana/README.md`](grafana/README.md))
- [`CICD_SETUP.md`](CICD_SETUP.md) — GHA + vendor secrets

## Local

```powershell
cd trading-lab
uv sync
uv run pytest
# Manual phase (mock keys OK for unit paths):
uv run python -c "from trading_lab.improvement.miss_harvest import build_miss_report; print(build_miss_report(journal_path='/tmp/x.sqlite', injected_gainers=[], watchlist_symbols=[]))"
```

## Deploy

Follow workspace pre-deploy gates (`python scripts/pre_deploy_check.py --app trading-lab`) and confirm before `tofu apply`. CI/CD: [`CICD_SETUP.md`](CICD_SETUP.md).
