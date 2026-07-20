# Agent setup guide

How each trading-lab agent is configured, sized, and gated.
Book principles distilled in `docs/trading-book-notes.md`.

---

## Portfolio budget (dynamic — platform equity)

Budget is **never hardcoded**. Every tick reads **current equity** from the
trading platform (Alpaca paper today; live later), then splits it.

| Rule | Value |
|------|--------|
| Equity source | Alpaca account equity (compounds daily with P&amp;L) |
| Slice size | `current_equity / 5` (e.g. $100k → **$20k**/agent; $105k → **$21k**) |
| Max open positions | **3** (three of five slices) |
| Max daily loss | **1 slice of that day’s equity** |
| Unused slices | Stay cash — never force a trade to fill them |

Implementation: `trading_lab.execution.budget` via `make_risk_gate` (paper) and
`run_vertical_slice` (sim — still pulls Alpaca equity when keys exist).

**Never force a trade.** Missing setup → `SKIP` / `NO_TRADE` is a successful outcome.

---

## Shared rules

### All agents (`agents.common.execution`)

- `never_force_trade = true`
- HoldPlan required on every ENTER
- Log every SKIP with `found_by_agent`
- Size = one budget slice unless explicitly overridden in tests

### Sniper family (`agents.sniper.shared_execution`)

- Intraday only — flat by EOD
- Scale out 50% at halfway to target → stop to breakeven
- 15-minute cool-off after a stop (anti-revenge)
- HVN→LVN deferred

### Swing family (`agents.swing.shared_execution`)

- Prefer ≥1 overnight session (multi-day setup)
- Scale 50% at **+4%** → BE; **final target 12%** (micro/penny **20%**)
- **>8% profit is OK** on multi-day rallies
- Stop 3% (5% penny) or exit if daily close &lt; 8-EMA
- Same-day exit allowed when risk rules fire

---

## Agents

### 1. `large_cap_sniper` (sniper / intraday)

| | |
|--|--|
| Cap band | ≥ $10B (plus mega liquid names: SPY, QQQ, AAPL, …) |
| Bars | 1Min |
| Target / stop | 3–4% / 1.5–2% (defaults 3.5% / 1.75%) |
| Key gates | RVOL &gt; 1.5 (1.25 paper); above VWAP; SPY/QQQ aligned; catalyst |
| Hold | Flat by EOD |
| Book ideas | Trade with the tape (Livermore/Murphy); risk defined first (Elder/Tharp) |

### 2. `mid_cap_sniper` (sniper / intraday)

| | |
|--|--|
| Cap band | $2B – &lt; $10B |
| Bars | 1Min |
| Target / stop | 6–8% / 2.5–3.5% (defaults **8%** / **3%**) |
| Key gates | RVOL ≥ 2 (≥1.5 paper); above VWAP; SPY/QQQ aligned; catalyst |
| Hold | Flat by EOD |
| Book ideas | Strength + volume (O’Neil / Minervini); selectivity over activity |

### 3. `speculative_sniper` (sniper / intraday)

| | |
|--|--|
| Cap band | &lt; $2B (screener default when cap unknown) |
| Bars | 1Min |
| Target / stop | ≥8% aim 12% / 3–5% (defaults **10%** / **4%**) |
| Key gates | RVOL &gt; 5 (4 paper); float &lt; 20M; RSI &lt; 80; clear catalyst |
| Hold | Flat by EOD |
| Book ideas | Only trade the clear pitch; no catalyst → SKIP |

### 4. `swing_momentum` (swing / multi-day)

| | |
|--|--|
| Cap band | All tiers (RVOL gates differ by large / mid / micro) |
| Bars | 1Day |
| Target / stop | Final **12%** (micro **20%**); scale at +4%; stop 3% / 5% penny |
| Key gates | SPY or QQQ &gt; 20-DMA; price &gt; 8-EMA; RVOL by tier; prefer Power Hour submits |
| Hold | Min 1 overnight; typical ~3; max 10 |
| Book ideas | Market filter + relative strength (O’Neil); let winners run on multi-day moves |

**Submit timing:** swing evaluates every tick; **orders only in power hour** (15:30–16:00 ET) unless forced in tests.

---

## Routing (paper tick)

```
symbol → resolve_sniper_agent(cap)
       → large | mid | speculative
       → evaluate → risk gate → Alpaca paper (or SKIP)
       → also evaluate swing_momentum (submit only in power hour)
```

Mid-cap gap closed: $2B–$10B → `mid_cap_sniper`.

---

## Attribution & journal

- Every trade/skip tagged `found_by_agent`
- Same ledger for sim / paper / live
- Snipers flattened at EOD; swing may stay overnight

---

## Promotion path

`sim / backtest → paper → live` only after journal expectancy looks sane.
Sizing always tracks platform equity (paper ~$100k today → ~$20k/slice).

---

## Improvement loop (missed gainers)

Ops-only — **never** places orders.

| Cadence | Who | Output |
|---------|-----|--------|
| Daily 18:00 `postmarket` | Deterministic harvest | `misses/{day}/report.json` + `by_agent/*.json` |
| Fri 18:05 `weekly_coaches` | Scorecard job + 4 coaches | `scorecards/{week}.json` + `proposals/{week}/*.json` |

Buckets: **A** never watchlisted, **B** skipped/no ENTER, **C** traded but weak capture.  
Proposals stay `pending_green_light` until you approve; guardrails are not tunable by coaches.  
Model: `COACH_MODEL_ID` (default Grok 4.3, `COACH_REASONING_EFFORT=high`).
