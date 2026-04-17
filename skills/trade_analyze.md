# /trade-analyze {SYMBOL}

Full 5-agent analysis of any futures or stock symbol. Saves a structured report to `data/reports/SYMBOL_YYYYMMDD.txt`.

## Steps

### Agent 1 — Technical Analysis
- Run `python alerts/futures_alerts.py --test {SYMBOL}` (or stock equivalent) to get live score, indicators, and levels
- Note: price vs 50 EMA, 200 EMA, VWAP; RSI value; MACD histogram direction; ATR
- Identify the highest-priority S/R levels (within 2% of current price)
- Note current candle pattern if any
- Output: technical bias, key levels, entry trigger to watch

### Agent 2 — COT / Institutional Flow
- Load `data/cot/cot_latest.json` — find the entry for this symbol (use MICRO_TO_COT mapping)
- Report: net position, week-over-week change, score (+2 to -2)
- Note if institutions are adding to or reducing exposure this week
- Flag any extreme positioning (net > 2x historical average)

### Agent 3 — Economic / Macro Context
- Load `data/economic/eco_latest.json`
- Report: overall US economy score, last 3 most impactful data points
- Web search for: "{SYMBOL} current news", "{SYMBOL} analyst price target", "{SYMBOL} COT report"
- For futures: note any supply/demand fundamentals (oil inventory, gold safe-haven demand, etc.)
- Output: macro tailwind or headwind, any upcoming catalysts

### Agent 4 — Seasonality + Sentiment
- Reference the monthly seasonality data in CLAUDE.md for this symbol
- For stocks: check if within 21 days of earnings (use FMP endpoint or web search)
- For stocks with options: estimate IV environment from `data/economic/` or web search
- Note: AAII sentiment, any extreme retail positioning
- Output: seasonal bias, sentiment score

### Agent 5 — Risk Matrix + Trade Synthesis
Synthesize all 4 agents into:

```
═══════════════════════════════════════
TRADE ANALYSIS: {SYMBOL} — {DATE}
═══════════════════════════════════════

SCORE DASHBOARD
  TEC: +X  COT: +X  RET: +X  SEA: +X  ECO: +X  P/C: +X  MOM: +X
  TOTAL: +X  →  Bias: [Very Bullish / Bullish / Neutral / Bearish / Very Bearish]

BULL CASE
  • [3 reasons the trade works]
  • Key level to reclaim: [price]
  • Catalyst: [what would confirm]

BEAR CASE  
  • [3 reasons the trade fails]
  • Invalidation level: [price]
  • Risk: [what would confirm]

ENTRY ZONE
  Direction: Long / Short
  Entry: [price range]
  Stop loss: [price] — [N pts from entry]
  TP1: [price] — R:R [X]:1
  TP2: [price] — R:R [X]:1
  Risk per contract: $[N] ([pts] × $[point_value]/pt)

RISK MATRIX
  Min R:R required: 1.5:1
  TP1 R:R: [X]:1  ← [MEETS / FAILS minimum]
  TP2 R:R: [X]:1
  COT aligned: Yes / No / Conflict ⚠️
  Score threshold met: Yes (+5 min) / No

⚠️ WARNINGS
  • [Any conflicts, news risk, earnings, volume concerns]

VERDICT
  [1-2 sentence synthesis: is this a high-conviction setup?]
═══════════════════════════════════════
```

## After analysis
- Save the full report to `data/reports/{SYMBOL}_{YYYYMMDD}.txt`
- Print the report
- Confirm save path

## Rules
- Dollar risk always uses the correct point_value from FUTURES_CONFIG
- Minimum 1.5:1 R:R — if no level supports this, state it explicitly
- Never suggest placing an order
- If COT and TEC conflict, flag it prominently in the report
