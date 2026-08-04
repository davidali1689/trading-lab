# Remediation plan — 2026-08-04 findings

Source: live investigation of S3 journal bucket (scorecards, misses, proposals,
watchlists, journals) + CloudWatch worker logs + cumulative trade journal.
Scope: everything except `grafana/` dashboards.

## Findings

| ID | Finding | Evidence | Severity |
|----|---------|----------|----------|
| F1 | Watchlist `$5` floor bypassed when screener `price=null` (most_actives rows) → sub-$1 names ENTER | ENSC ($0.39–0.43), ZBAO ($0.29), YYAI ($0.12) entered 2026-08-04; `watchlist.py` only rejects when price present | High |
| F2 | No per-symbol repeat-entry guard | ENSC 3 entries 08-04 (all losers); ZYBT 6 entries 07-21 (3 spec + 3 swing) | High |
| F3 | Stop integrity failure on penny names | ZYBT: planned 4% stop, actual exit −42.6%, −$8,745 vs ~$820 intended (bulk of cumulative −$4,150) | Critical |
| F4 | Lambda `/tmp` disk full → `POST /events` 500 | `OSError: [Errno 28] No space left on device` in worker logs | High |
| F5 | `exit_reassess` 403 `insufficient qty` (shares `held_for_orders` by bracket legs) | AAPL/NVDA errors; positions can't be managed/closed by reassess | High |
| F6 | Coach loop broken: mantle IAM denied, fallback model ID invalid → W31 proposals never written; `proposals/latest/` stale W30 | CloudWatch: `bedrock-mantle:CreateInference` denied; Converse ValidationException on `moonshot.kimi-k2-thinking` | Medium |
| F7 | Scorecard `capture_rate=0` / `gainers_captured=0` while miss harvest shows `C_entered_missed_move` (DFNS +5.7% vs +122%) | scorecards W30/W31 vs misses/2026-08-03 | Medium |
| F8 | Cap routing dead-ends: unknown Finnhub cap → speculative; `mid_cap_sniper` 0 trades AND 0 skips; INTC/PLTR not in `LARGE_CAP_SYMBOLS` | scorecards W30/W31; INTC routed speculative 08-04 | Medium |
| F9 | No late-day first-entry guard for intraday speculative (timestamps in journal are UTC; entries at 13:28/14:08 ET are legal but leave <2.5h to EOD flatten) | trades.csv 08-04 | Low |
| F10 | Daily-loss gate uses realized P&L only; losing morning didn't block later entries | 08-04: closed −$1,026, more entries after | Medium |

## Fixes (this session — code + unit tests)

- **F1** `selection/watchlist.py`: when screener price is null, resolve last trade
  price via Alpaca snapshot before the `MIN_PRICE` gate; reject if unresolvable
  or < $5. Unit test: null-price active under $5 is rejected.
- **F2** `pipeline/paper_agents.py` + journal: max 1 ENTER per symbol per day per
  agent (env `MAX_ENTRIES_PER_SYMBOL_DAY`, default 1). Skip reason reuses
  `RISK_BLOCKED` with `detail=repeat_entry_symbol_day`. Unit test.
- **F3** `pipeline/paper_submit.py` / broker: after bracket submit, verify both
  exit legs (TP + stop) exist on Alpaca; if stop leg missing → flatten
  immediately (fail-safe) + journal. Unit test with mock broker.
- **F4** `journal/persist.py`: after successful S3 upload, vacuum + truncate the
  local sqlite and remove stale `/tmp` artifacts; keep single canonical path.
  Unit test on temp dir.
- **F5** `pipeline/exit_reassess.py`: before `close_position`, cancel open orders
  for the symbol unconditionally (bracket legs hold shares); already done in some
  branches — make it uniform incl. SCALE_AND_TRAIL partial close. Unit test.
- **F6** `improvement/coach_client.py`: default fallback → valid Converse model
  (`amazon.nova-pro-v1:0` stays); make primary default env-driven and document
  `COACH_MODEL_ID`. IAM (`bedrock-mantle:CreateInference`) = deploy-gated, below.
- **F7** `improvement/scorecard.py`: `gainers_captured` counts a gainer when the
  journal has any trade for that symbol attributed to the agent (aligns with
  harvest bucket C). Unit test.
- **F8** `pipeline/paper_agents.py`: extend static cap lists (INTC, PLTR, SOFI,
  NOK, AAL, …) into `LARGE_CAP_SYMBOLS` / new `MID_CAP_SYMBOLS`. Unit test.
- **F9** speculative: block *first* ENTER after 15:00 ET (env
  `SPEC_LAST_ENTRY_ET`, default `15:00`). Unit test.
- **F10** risk gate: include open-position unrealized P&L from broker marks in
  the daily-loss check when available. Unit test.

## Deploy-gated (NOT this session — needs pre-deploy gates + confirmation)

- IAM: worker role `bedrock-mantle:CreateInference` (infra/modules coach IAM).
- Lambda ephemeral storage bump if /tmp cleanup proves insufficient.
- Redeploy worker image; regenerate W31 proposals after coach fix.

## E2E confirmation (this session)

`tests/test_e2e_day_lifecycle.py` — full simulated day with mock broker +
mock market data:

1. Watchlist build: null-price active under $5 rejected; ≥$5 kept.
2. Tick: routed agent evaluates; first ENTER submits bracket with both legs.
3. Repeat tick same symbol → skip (`repeat_entry_symbol_day`).
4. Bracket-leg verification: missing stop leg → immediate flatten.
5. Exit reassess with held_for_orders → cancels orders then closes cleanly.
6. Persist: journal uploads; /tmp truncated.
7. Miss harvest + scorecard: captured gainer shows `gainers_captured ≥ 1`.

## Not doing

- No coach proposal auto-apply (still manual green-light).
- No live-trading changes; paper only.
- No Grafana dashboard changes.
- No `tofu apply` without gates + explicit confirmation.
