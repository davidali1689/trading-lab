# Changelog

## [Unreleased]

### Added

- **Miss harvest** (`postmarket` 18:00 ET): deterministic top liquid gainers vs journal/watchlist (buckets A/B/C), persisted under `s3://…/misses/{day}/` and per-agent shards. Penny floor remains `$5`.
- **Friday weekend pack** (`weekly_coaches`, Fri 18:05 ET): deterministic **scorecard** (improving/flat/worse + `propose_revert` flag) then four strategy coaches (Grok 4.3 high) → `scorecards/` + `proposals/` for weekend review.
- **Weekly strategy coaches**: one coach per strategy; proposals `pending_green_light` only — no auto-apply, no orders.
- **Coach IAM module** (`infra/modules/coach-iam`): tagged role for Bedrock / future AgentCore (`Application`, `Repo`, `BedrockCaller`).
- Cost-attribution tags on Lambda worker IAM role (`Application`, `Repo`, `BedrockCaller`, `Workload`).
- Env: `COACH_MODEL_ID`, `COACH_REASONING_EFFORT`, `MISS_HARVEST_TOP_N`.
- README documenting the improvement loop and tagging.

### Changed

- Lambda timeout raised to 300s for Friday coach fan-out.
- Market scheduler + `SCHEDULES` include `weekly_coaches`.
- Provider / `common_tags` now include `Application` + `Repo` for Bedrock IAM principal cost tracking.
