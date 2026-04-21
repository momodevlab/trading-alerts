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

FOREX  (12 pairs, 1h bars, auto-executed via Oanda)
  Group A — Williams Alligator + RSI:
    AUDCAD, USDCAD, CADJPY, EURUSD, AUDUSD, GBPJPY
  Group B — RSI Crossover:
    GBPUSD, USDJPY, USDCHF, EURJPY, NZDJPY, NZDUSD

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
STRATEGY 4 — WILLIAMS ALLIGATOR + RSI  (Forex Group A, 1h)
═══════════════════════════════════════════════════════════════

Pairs:     AUDCAD, USDCAD, CADJPY, EURUSD, AUDUSD, GBPJPY
Timeframe: 1h bars (15m tested and rejected — WR dropped 10–33%)

Alligator lines (SMMA approximation):
  Jaw   = SMMA(13) — slowest
  Teeth = SMMA(8)
  Lips  = SMMA(5)  — fastest

Long entry (ALL required):
  1. Lips > Teeth > Jaw  (alligator eating upward)
  2. RSI crosses above 50 on this bar (prev ≤ 50, curr > 50)
  3. RSI < 65  (not yet overbought)

Short entry (ALL required):
  1. Lips < Teeth < Jaw  (alligator eating downward)
  2. RSI crosses below 50 on this bar (prev ≥ 50, curr < 50)
  3. RSI > 35  (not yet oversold)

Levels:
  Entry:  1 pip above/below current price on signal bar
  Stop:   3-bar swing low/high OR entry − 1.2×ATR (whichever is wider)
  TP1:    Entry + 1.5R  — exit 40%, stop → breakeven
  TP2:    Entry + 2.5R  — exit 40%
  Trail:  1.5×ATR trailing stop for final 20%

Backtest results (90 days, 1h bars):
  AUDCAD: 61.1% WR  |  18 trades  ← best
  USDCAD: 53.3% WR  |  30 trades
  CADJPY: 50.0% WR  |  28 trades
  EURUSD: 48.1% WR  |  27 trades
  AUDUSD: 44.8% WR  |  29 trades
  GBPJPY: 42.9% WR  |  21 trades

═══════════════════════════════════════════════════════════════
STRATEGY 5 — RSI CROSSOVER  (Forex Group B, 1h)
═══════════════════════════════════════════════════════════════

Pairs:     GBPUSD, USDJPY, USDCHF, EURJPY, NZDJPY, NZDUSD
Timeframe: 1h bars

Long entry:  RSI crosses above 40 (oversold recovery)
Short entry: RSI crosses below 60 (overbought fade)

Exit rules:
  Stop:   3-bar swing low/high OR entry − 1.2×ATR
  TP1:    Entry + 1.5R  — exit 40%
  TP2:    Entry + 2.5R  — exit 40%
  Trail:  1.5×ATR trailing stop for final 20%
  Also exit: RSI hits 70 (long) or 30 (short) — momentum exhausted

Backtest results (90 days, 1h bars):
  EURJPY: 68.3% WR  |  PF 1.21  |  41 trades
  USDJPY: 66.7% WR  |  PF 1.51  |  48 trades  ← best PF
  USDCHF: 66.7% WR  |  PF 1.35  |  54 trades
  GBPUSD: 63.0% WR  |  PF 1.28  |  54 trades
  NZDJPY: 52.8% WR  |  PF 0.93  |  53 trades
  NZDUSD: 51.9% WR  |  PF 0.97  |  54 trades

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
- Futures strategy win rates are from Feb–Apr 2025 backtest — a bear market period
- Forex strategy win rates are from 90-day backtest (1h bars)
- 15m was tested for forex and rejected — Alligator WR dropped 10–33%, keep 1h
- The system executes these strategies automatically — display is for reference only
