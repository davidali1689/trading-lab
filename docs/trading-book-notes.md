# Trading book notes — distilled

Actionable ideas from the core bookshelf. Prefer principles over indicator recipes.
Map to **trading-lab**: rules agents, risk gate, journal, no LLM on entries.

---

## How to use this

1. Steal **process** first (risk, journal, psychology).
2. Steal **setup language** second (momentum, screens).
3. Ignore anything that fights your lab rules: never force trades, log SKIPs, size from risk.

---

## Mark Douglas — *Trading in the Zone*

- Edge is probabilistic: any single trade can lose; the edge shows over a series.
- Think in **probabilities**, not predictions. “Next trade” is unknown; process is known.
- The market owes you nothing. Revenge trading and “being right” destroy accounts.
- Consistency comes from executing the same rules without emotional negotiation.
- Fear of missing out and fear of being wrong cause most discretionary errors.
- Define risk **before** entry. Once in, manage per plan — don’t invent a new plan mid-trade.
- Winning traders accept losses as a business cost, not a personal failure.
- **Lab map:** hard gates → ENTER or SKIP; journal every outcome; kill switch / daily loss stop = “stop negotiating.”

---

## Brett Steenbarger — *The Psychology of Trading*

- Trading performance is a skill under stress; train it like a performance domain.
- Self-awareness beats motivation: know your tilt triggers (loss streak, FOMO, boredom).
- Review process quality, not only P&amp;L. Good process + bad day ≠ “broken system.”
- Keep a journal of **state** (sleep, emotion, distraction) alongside trades.
- Best growth: deliberate practice on one bottleneck at a time (entries, sizing, exits).
- Overtrading is often emotional regulation, not opportunity.
- **Lab map:** Grafana/journal for process metrics; postmortems after sessions, not during ticks.

---

## Edwin Lefèvre — *Reminiscences of a Stock Operator* (Jesse Livermore)

- Don’t fight the tape. Trend and tape reading beat opinions.
- Big money is made in the **big swing**, not nickel-and-diming noise.
- Sit on your hands when there is no clear opportunity.
- Pyramiding only into strength; never average into a loser as a “strategy.”
- Tips and narratives are dangerous; price and position management are real.
- A losing streak means reduce size / step away — not “try harder.”
- **Lab map:** sniper flat by EOD; swing holds multi-session only when trend gates pass; SKIP is success.

---

## Van Tharp — *Trade Your Way to Financial Freedom*

- System = entry + exit + **position sizing** + psychology. Most people only design entry.
- Measure trades in **R** (risk units): risk $1 to make expectable multiples of R.
- Expectancy = (win% × avg win) − (loss% × avg loss). Optimize expectancy, not win rate alone.
- Position size is the main lever for survival and growth on a small account.
- Different “market types” need different systems (trend vs chop). Don’t force one system everywhere.
- Objectives first: what drawdown can you survive? Size so ruin is unlikely.
- **Lab map:** size from stop distance and equity %; daily loss as % of equity; track R and expectancy per agent.

---

## Alexander Elder — *The New Trading for a Living*

### Three M’s
- **Mind** — discipline, no revenge, accept losses.
- **Method** — clear screens / setups with written rules.
- **Money** — risk per trade and total exposure caps.

### Risk (classic Elder framing)
- Risk a small, fixed fraction of equity per trade (often cited ~1–2%; small accounts may need slightly higher *percent* but never all-in).
- Know max daily / weekly loss → stop trading when hit.
- Never add to losers; scale out or trail winners per plan.

### Method ideas (adapt, don’t copy blindly)
- **Triple screen:** higher timeframe = bias; intermediate = setup; lower = entry timing.
- Indicators confirm; price action / trend leads. Don’t stack five indicators that say the same thing.
- Journal every trade: entry reason, exit reason, emotion, lesson.

### Psychology
- Trading is a business. Businesses have rules, records, and risk limits.
- **Lab map:** swing daily bias + sniper 1m entry ≈ triple screen; HoldPlan + risk gate ≈ Money; Bedrock postmortem ≈ review, not entry.

---

## John Murphy — *Technical Analysis of the Financial Markets*

- Charting is organized history of supply/demand — not prophecy.
- Trend: higher highs/higher lows (up) or the reverse (down). Trade with the dominant trend when possible.
- Support/resistance, breakouts, and volume confirmation are the durable toolkit.
- Timeframes must agree or you fight yourself (same idea as Elder’s screens).
- Indicators lag; use them to confirm, not to invent a story.
- Intermarket context (indexes, sectors, rates) matters for stock selection.
- **Lab map:** SPY/QQQ alignment gates; RVOL; VWAP; 8-EMA / 20-DMA already encode Murphy-style confirmation.

---

## William O’Neil — *How to Make Money in Stocks* (CAN SLIM DNA)

- Buy strength in leading stocks/groups, not “cheap” laggards hoping for mean reversion (unless that *is* your system).
- Earnings and institutional sponsorship matter for swing/position momentum.
- Cut losses quickly (classic O’Neil discipline: small, predefined loss).
- Let winners work; don’t take tiny profits on real leaders out of fear.
- Market direction filter: when indexes are weak, raise the bar or stay flat.
- Base breakouts / proper entry points beat chasing extended moves.
- **Lab map:** swing RVOL + RS vs SPY/QQQ + index &gt; 20-DMA; speculative sniper wants catalyst + volume, not bargain hunting.

---

## Mark Minervini et al. — *Momentum Masters* (interview themes)

- Focus on **relative strength** and liquidity; avoid junk you can’t exit.
- Wait for your pitch — selectivity over activity.
- Risk first: know the stop before the target.
- Position building into strength; reduce into weakness.
- Study your own stats; copy setups that match *your* temperament and account size.
- Consistency of routine (scan → watchlist → execute → review) beats inspiration.
- **Lab map:** watchlist scan at premarket; power-hour preference for swing; mid/large/spec snipers as separate setup books.

---

## David Aronson — *Evidence-Based Technical Analysis*

- Most TA lore is untested storytelling. Demand out-of-sample evidence.
- Beware data mining and overfitting (“perfect” rules on past charts).
- Use statistical discipline: walk-forward, multiple testing awareness, realistic costs.
- A rule that isn’t testable isn’t an edge — it’s a belief.
- **Lab map:** walk-forward bake-off; same bars for all agents; journal SKIPs; promote sim → paper → live only on expectancy.

---

## Marcos López de Prado — *Advances in Financial Machine Learning* (selective notes)

- Finance data leaks easily (labels, Purged CV, embargo). Naïve backtests lie.
- Meta-labeling / secondary models: primary model proposes, secondary filters — still **deterministic gates** in our lab, not chatty agents.
- Feature research ≠ strategy. Bet sizing and risk dominate returns.
- Overfit is the default; complexity needs stronger validation.
- **Lab map:** keep Bedrock off the entry path; use ML later for post-trade analytics if ever — not for forced ENTERs.

---

## Cross-book checklist (print this)

| Principle | Source vibe | trading-lab habit |
|-----------|-------------|-------------------|
| Probabilities over certainty | Douglas | ENTER/SKIP from gates |
| Process journal | Elder, Steenbarger | SQLite + Grafana |
| Size from risk (R) | Tharp, Elder | equity-% risk, stop-based qty |
| Trend / strength bias | Murphy, O’Neil, Minervini | SPY/QQQ, RVOL, RS |
| Don’t force trades | Livermore, Douglas | `never_force_trade` |
| Prove edge out of sample | Aronson, de Prado | walk-forward, paper first |
| Cut losers, manage winners | O’Neil, Elder, sniper/swing rules | stops, scale-out, EOD flatten |

---

## Reading order (practical)

1. Douglas — psychology / probability  
2. Elder (*New Trading for a Living*) — full business frame  
3. Tharp — sizing & expectancy (critical for ~$2k live)  
4. O’Neil or Minervini — momentum craft for swing  
5. Murphy — reference when naming patterns  
6. Aronson — when tightening backtests  
7. de Prado — only if you go deep quant later  
8. Steenbarger — ongoing performance work  
9. Livermore — reread when you feel invincible  

---

## Explicitly not from these books

- Guaranteed win rates  
- “Secret” indicators  
- Averaging down as a lifestyle  
- Letting an LLM decide entries  

---

*Notes are educational distillations of widely discussed ideas, not a substitute for the books or for your own written rules.*
