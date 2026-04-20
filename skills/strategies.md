# /strategies

Display the full strategy reference — all active trading strategies in this system, how each one works, when to use it, and what the backtested results show.

## Output format

```
═══════════════════════════════════════════════════════════════
STRATEGY REFERENCE  —  Last backtest: Feb–Apr 2025 (59 days)
═══════════════════════════════════════════════════════════════

STRATEGY STACK BY MARKET
────────────────────────────────────────────────────────────
FUTURES  (MES, MNQ, MYM, M2K, MCL, MGC, MSI, MNG)
  1. Gap Fill          — 9:30 AM ET open, once/day/symbol
  2. VWAP Pullback     — RTH session, any time
  3. ORB               — 9:45–10:15 AM ET only

FOREX  (17 pairs, 24/5 session-filtered)
  1. EMA 9/21 Pullback — Asian / London / NY session filtered

═══════════════════════════════════════════════════════════════
STRATEGY 1 — GAP FILL  (Primary morning signal)
═══════════════════════════════════════════════════════════════

Best in:   Choppy / volatile / news-driven markets
Avoid:     Metals (MGC, MSI) — gold/silver gaps are structural, don't fill

When it fires:
  • 9:30–9:44 AM ET — the first 15m candle of the RTH session
  • Gap UP   > 0.3% from PDC → short fade back toward PDC
  • Gap DOWN > 0.3% from PDC → long fade back toward PDC
  • Opening bar must confirm: bearish close for gap up, bullish close for gap down
  • Maximum one signal per symbol per day

Levels:
  Entry:  Above bar high (long) or below bar low (short)
  Stop:   1.0 × ATR beyond bar extreme
  TP1:    PDC — full gap fill — EXIT FULL POSITION here
  TP2:    25% runner if TP1 fills quickly (PDC ± 50% of gap size)

Exit rules:
  • Close entire position at TP1 (PDC)
  • If PDC not reached by 10:30 AM ET → close the trade regardless

Backtest results (Feb–Apr 2025 bear market, 59 days):
  MES:  60% WR  |  PF 1.12  |  10 signals
  MNQ:  70% WR  |  PF 1.75  |  10 signals  ← best result

═══════════════════════════════════════════════════════════════
STRATEGY 2 — VWAP PULLBACK  (Intraday trend signal)
═══════════════════════════════════════════════════════════════

Best in:   Trending markets (ADX > 20 on 15m chart)
Avoid:     Choppy / ranging / news-driven days (ADX < 20)

Conditions (ALL required):
  1. Price above EMA 50 (uptrend bias)
  2. EMA 9 > EMA 21 (short-term momentum intact)
  3. ADX >= 20 (trending market confirmed)
  4. Previous bar touched VWAP ±0.20% OR EMA 21 ±0.20%
  5. RSI 35–65 on the pullback (healthy, not extreme)
  6. MACD histogram positive OR rising (curr > prev)
  7. Bullish candle at the level (hammer, engulfing, dragonfly, marubozu)
  8. Trigger bar volume >= 0.4× 20-bar average

Levels:
  Entry:  1 tick above trigger bar high
  Stop:   Lower of: pullback bar low OR entry − 1.2×ATR
  TP1:    Entry + 1.5R  — exit 40%, stop → breakeven
  TP2:    Entry + 2.5R  — exit 40%
  Trail:  1.5×ATR trailing stop for final 20%

Backtest results (Feb–Apr 2025, improved parameters):
  MES:  29.9% WR  |  PF 0.70  |  67 signals
  MNQ:  37.3% WR  |  PF 0.95  |  59 signals
  Note: tested in bear market — expect 45–55% WR in trending conditions

═══════════════════════════════════════════════════════════════
STRATEGY 3 — ORB  (Opening Range Breakout)
═══════════════════════════════════════════════════════════════

Best in:   Trending open days with clear momentum
Avoid:     Choppy open, major news days (fade risk)
Time gate: 9:45–10:15 AM ET only

Conditions (ALL required):
  1. ORB high/low defined by first 15m candle (9:30–9:44 AM)
  2. Current bar closes ABOVE ORB high
  3. Price above VWAP
  4. EMA 9 > EMA 21
  5. ADX >= 18
  6. Volume >= 1.5× 20-bar average on breakout bar
  7. Breakout bar is bullish (close > open)

Levels:
  Entry:  1 tick above breakout bar high
  Stop:   Lower of: just below ORB high OR entry − 1.2×ATR
  TP1:    Entry + 1.5R  — exit 40%, stop → breakeven
  TP2:    Entry + 2.5R  — exit 40%
  Trail:  1.5×ATR trailing stop for final 20%

═══════════════════════════════════════════════════════════════
STRATEGY 4 — EMA 9/21 PULLBACK  (Forex intraday trend)
═══════════════════════════════════════════════════════════════

Best in:   Trending sessions for the specific currency pair
Session filter:
  Asian  (7 PM–4 AM ET):  JPY, AUD, NZD pairs
  London (3 AM–12 PM ET): EUR, GBP pairs
  NY     (8 AM–5 PM ET):  all 17 pairs

Conditions (ALL required):
  1. Price above EMA 50 (uptrend)
  2. EMA 9 > EMA 21 (momentum intact)
  3. ADX >= 20 (trending market)
  4. Pullback bar touched EMA 9/21 zone ±0.20%
  5. RSI 35–65
  6. MACD positive OR rising (curr > prev)
  7. Bullish candle at zone (no volume filter — forex volume unreliable)
  8. Trigger bar is bullish (close > open)

Levels:
  Entry:  1 pip above trigger bar high
  Stop:   Lower of: pullback bar low − 2 pips OR entry − 1.2×ATR
  TP1:    Entry + 1.5R  — exit 40%
  TP2:    Entry + 2.5R  — exit 40%
  Trail:  1.5×ATR trailing stop for final 20%

Backtest results (Feb–Apr 2025):
  EURUSD:  39.2% WR  |  PF 1.12  ← profitable even in bear market
  AUDUSD:  32.7% WR  |  PF 0.79

═══════════════════════════════════════════════════════════════
REGIME GUIDE — WHICH STRATEGY TO USE TODAY
═══════════════════════════════════════════════════════════════

Check ADX on the 15m chart of MES or NQ before the open:

  ADX > 25, price trending up  → VWAP Pullback + ORB are primary
  ADX < 20, price ranging      → Gap Fill is primary, skip VWAP/ORB
  Gap > 0.3% at open          → Always check Gap Fill first regardless of ADX
  High-impact news day         → Gap Fill only (VWAP/ORB too risky)

The alert system handles this automatically — it runs all strategies
simultaneously and fires the appropriate alert based on conditions.
```

## Rules
- Always show dollar risk per contract when displaying levels
- Reference FUTURES_CONFIG point values for all dollar calculations
- Strategy win rates are from Feb–Apr 2025 backtest — a bear market period
  In trending markets, expect VWAP/EMA Pullback to improve to 45–55%
- The system executes these strategies automatically — display is for reference only
