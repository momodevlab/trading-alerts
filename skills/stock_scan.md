# /stock-scan

Scan all stocks in the universe (STOCK_SCAN_UNIVERSE + STOCK_WATCHLIST_CORE) for high-conviction setups and produce a ranked table.

## Steps

1. Run `python scripts/daily_stock_scan.py --no-telegram` to get today's scan results. If a cached scan file exists for today (`data/sessions/active_stocks_YYYY-MM-DD.json`), read it instead of re-running the scan.

2. Format the full ranked output:

```
STOCK SCAN — [DATE] [TIME] ET
Scanned: 32 symbols | Alert threshold: ±7 | Today's active finds: 3
═══════════════════════════════════════════════════════════════════════

⚡ ALERT-READY TODAY (|score| ≥ 7)
────────────────────────────────────────────────────────────────────
Symbol  Score   Bias          Top Drivers           Nearest Level    Earnings
──────────────────────────────────────────────────────────────────
NVDA    +8      Bullish       TEC+2 ECO+2 MOM+2     50 SMA $880 (1.4%) —
META    +8      Bullish       TEC+2 ECO+2 MOM+2     PDC $585 (0.3%) —
GLD     +7      Bullish       TEC+2 COT+2 MOM+2     PDL $218 (0.5%) —
TSLA    -7      Bearish       TEC-2 MOM-2 ECO-2     PDH $295 (0.8%) ⚠️ 18d

ALL SYMBOLS RANKED
────────────────────────────────────────────────────────────────────
Symbol  Score  Bias          Breakdown
──────────────────────────────────────
NVDA    +8     Bullish       TEC+2 COT0 RET0 SEA0 ECO+2 PC0 MOM+2
META    +8     Bullish       TEC+2 COT0 RET0 SEA0 ECO+2 PC0 MOM+2
QQQ     +7     Bullish       TEC+2 COT0 RET0 SEA+1 ECO+2 PC0 MOM+1
SPY     +7     Bullish       TEC+2 COT0 RET0 SEA+1 ECO+2 PC0 MOM+1
...
TSLA    -7     Bearish       TEC-2 COT0 RET0 SEA0 ECO-2 PC0 MOM-2
```

3. Flag any symbols with earnings within 21 days:
   - ≤ 7 days: `⚠️ Earnings in X days` (suppress swing alert)
   - 8–21 days: `⚠️ Earnings in ~X days` (warning only)

4. Below the table, note:
   - **Today's active watchlist** (core + scan finds being monitored)
   - How many scan finds were added to today's `stock_alerts.py` session

## Alert-ready definition
A symbol is "Alert-ready" if |score| ≥ 7. These symbols meet the threshold
for a Telegram alert if other conditions (level proximity, volume, candle pattern) are also met.

## Rules
- Never suggest placing a trade — analysis only
- COT is always 0 for individual stocks (not applicable)
- Always include earnings warning when within 21 days
- Note IV rank for any symbol where options data is available
