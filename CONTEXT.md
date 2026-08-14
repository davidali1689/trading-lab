# Context / Ubiquitous Language

**gainer_sniper** — fifth paper sniper; trades live Alpaca top gainers in the first hour while the move is still early (+2% to +15%), not the EOD chase print.
**gainer window** — 09:30–10:30 ET; after 10:30 the agent is silent and extra symbols drop off the tick set.
**live gainer scan** — once-per-tick Alpaca movers pull during the gainer window; unioned onto the 08:00 watchlist (cap 8 extras); snapshot at `gainers/{day}/first_hour.json`.
**early-band** — day-gain still in +2% to +15%; skip at/above 15% (EOD +40–150% names are the miss report, not the entry).
**bucket A** — miss-harvest: never on the frozen watchlist or journal (the usual fate of EOD top gainers).
**found_by_agent** — journal attribution key for which setup agent found the trade.
