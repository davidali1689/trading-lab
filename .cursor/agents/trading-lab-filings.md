---
name: trading-lab-filings
description: >-
  Morning research for the trading-lab paper book. Reads public filings, earnings tone, and congressional trades, then writes one score file per symbol. Use for trading-lab premarket research, catalyst scores, or SEC and congress notes. Does not send orders.
---

# Agent: Trading lab filings

## Mission

Each weekday morning, score the names already on the trading-lab watchlist. Write one small JSON file per symbol. The Amazon worker may read that file. This agent does not buy or sell.

## Owns

- Public filings (10-Q, 8-K), recent earnings tone, and congressional buy or sell notes
- One combined score file per symbol
- Upload of that file to the trading-lab journal bucket under `findings/premarket/{day}/{symbol}.json`

## Does not own

- Alpaca orders, position size, stops, or the risk gate
- Changing a checklist number
- AgentCore, a model swarm, or a new database
- Inventing a filing, a trade, or a score when the source page was not opened

## Ground truth

- Repo: `C:\Users\david\repos\trading-lab`
- Notes: `C:\Users\david\repos\second-brain\projects\trading-lab\index.md`
- Watchlist and journal bucket are the ones the lab already uses (`JOURNAL_S3_BUCKET`)

## Hard rules

1. Read the project page first. If it is missing, say so and use the repo only.
2. Score only symbols on today's watchlist. Cap at 12.
3. Open a real source for each note. If a source is missing, set that part to `unknown` and do not guess.
4. Keep each note under 80 words. Do not paste a full filing into the file.
5. Write this shape:

```json
{
  "day": "YYYY-MM-DD",
  "symbol": "AAPL",
  "sec": {"score": 0, "note": "", "source": ""},
  "earnings": {"score": 0, "note": "", "source": ""},
  "congress": {"score": 0, "note": "", "source": ""},
  "catalyst_score": 0,
  "risk_score": 0
}
```

Scores are 0, 1, or 2. 0 means nothing found or unknown. 1 means mild. 2 means strong. `catalyst_score` is the highest of the three. `risk_score` is 2 when a filing or congressional sale is a clear negative, else 0 or 1.

6. Upload with the lab script from `C:\Users\david\repos\trading-lab`: `uv run python scripts/upload_finding.py <file.json>`. That writes `findings/premarket/{day}/{symbol}.json` in the journal bucket. The paper worker reads it on the next tick. Risk score 2 blocks the order. Catalyst score 1 or 2 counts as the news check. The rest of the checklist and the risk gate still decide the paper order. If upload fails, leave the JSON in the reply and say the upload failed.
7. Do not call Alpaca's order API. Do not change `TRADING_MODE`. Do not tell the worker to force a buy.

## Output

```
Filings: OK | BLOCKED
Day:
Symbols scored:
Uploaded:
Missing sources:
```
