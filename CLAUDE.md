# Trading System — Project Context

## Who I am
I am an active trader who trades futures micros and minis (MES, MNQ, MCL, MGC,
MYM, MSI, MNG, M2K) manually. I currently have a small account (a few hundred
dollars) and am NOT automating any trades. The system is purely for research,
analysis, and trade alerts. I execute all trades myself in my own platform.

## What we are building
A local research and alert system with three layers:

1. **Live dashboard** — browser-based HTML that auto-refreshes with prices,
   S/R levels, COT data, sentiment, economic heat maps, and an intraday
   entry/exit planner I use as a reference before taking a trade manually
2. **Research engine** — multi-agent analysis on any ticker or futures contract
   via slash commands, outputs structured reports with entry/exit/sizing
3. **Futures alert system** — monitors MES, MNQ, MCL, MGC, MYM, MSI, MNG, M2K
   and fires Telegram + terminal alerts when entry/exit conditions are met.
   I place all trades manually in my own platform after receiving an alert.

## What this system does NOT do
- No broker API connections
- No automated order placement of any kind
- No account balance tracking
- No position sizing calculations tied to a live account
- This is a pure information and alerting tool

## Project structure
```
trading-system/
├── CLAUDE.md                    ← this file
├── .env                         ← API keys (never commit)
├── requirements.txt
├── README.md
├── dashboard/
│   ├── index.html               ← main dashboard (auto-refreshes every 60s)
│   └── assets/
├── data/
│   ├── cot/                     ← CFTC COT data cache (JSON)
│   ├── economic/                ← FMP economic data cache (JSON)
│   ├── sessions/                ← daily morning briefs + level files
│   ├── reports/                 ← trade analysis reports (text)
│   └── alerts_log.json          ← history of all fired alerts
├── agents/
│   ├── research_agent.py        ← 5-agent analyzer: tech, fundamental,
│   │                               sentiment, risk, thesis synthesis
│   ├── cot_agent.py             ← CFTC COT fetcher, parser, scorer
│   ├── economic_agent.py        ← FMP economic heat map builder
│   ├── sentiment_agent.py       ← AAII, put/call ratio, retail sentiment
│   └── news_agent.py            ← AI news feed per asset via web search
├── alerts/
│   ├── futures_alerts.py        ← monitors futures, fires alerts
│   ├── alert_engine.py          ← evaluates conditions, formats messages
│   └── notifier.py              ← sends Telegram + prints to terminal
├── skills/
│   ├── trade_analyze.md         ← /trade-analyze slash command
│   ├── trade_quick.md           ← /trade-quick slash command
│   ├── morning_brief.md         ← /morning-brief slash command
│   ├── sr_levels.md             ← /sr-levels slash command
│   ├── futures_scan.md          ← /futures-scan slash command
│   └── register_position.md    ← /register-position slash command
└── scripts/
    ├── refresh_dashboard.py     ← runs every 5 min during market hours
    ├── morning_setup.py         ← runs at 8:30 ET daily
    ├── calculate_daily_levels.py← runs at 9:15 ET daily
    └── market_close.py          ← runs at 16:15 ET daily
```

## API keys (stored in .env — never hardcode)
```
# Financial Modeling Prep — free at financialmodelingprep.com (250 req/day)
FMP_API_KEY=

# Telegram — create bot via @BotFather, get chat ID via @userinfobot
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# TradingView MCP (required — TradingView Desktop must be running)
TRADINGVIEW_MCP_PORT=9222
```

## My instruments

### Futures I trade manually (alerts-only)
| Symbol | Full name | Exchange | Point value |
|--------|-----------|----------|-------------|
| MES | Micro E-mini S&P 500 | CME | $5/pt |
| MNQ | Micro E-mini Nasdaq 100 | CME | $2/pt |
| MYM | Micro E-mini Dow | CBOT | $0.50/pt |
| M2K | Micro E-mini Russell 2000 | CME | $5/pt |
| MCL | Micro WTI Crude Oil | NYMEX | $100/barrel |
| MGC | Micro Gold | COMEX | $10/oz |
| MSI | Micro Silver | COMEX | $25/oz |
| MNG | Micro Natural Gas | NYMEX | $250/mmBtu |

Also track full-size contracts for reference: ES, NQ, YM, RTY, CL, GC, SI, NG.

## Scoring system (EdgeFinder-style)
Every asset scores -14 to +14 across 7 signals. Each signal: -2, -1, 0, +1, +2.

| Signal | Source | Bullish | Bearish |
|--------|---------|---------|---------|
| TEC | Moving averages (50/200) | Price above both MAs | Price below both MAs |
| COT | CFTC non-commercial net | Net long + adding longs | Net short + adding shorts |
| RET | Retail sentiment (contrarian) | >60% retail short | >60% retail long |
| SEA | 10yr monthly seasonality avg | Historically positive month | Historically negative month |
| ECO | FMP economic data vs forecast | Economy beating forecasts | Economy missing forecasts |
| P/C | Put/call ratio extreme | Ratio > 1.2 (fear = buy) | Ratio < 0.7 (greed = sell) |
| MOM | Price momentum / trend | Strong uptrend | Strong downtrend |

Thresholds: +5 = Bullish, +10 = Very Bullish, -5 = Bearish, -10 = Very Bearish

## Futures alert system

### Philosophy
The system watches markets 24/5 and sends me a Telegram message when a
high-probability setup is forming. I read the alert on my phone, decide if I
agree with the setup, and place the trade myself in my trading platform.

### Alert types

**1. Setup alert** — a potential entry is forming. All must be true:
- Score >= +5 (bullish) or <= -5 (bearish)
- Price within 0.25% of a key S/R level
- At least 2 of: RSI confirming, MACD confirming, price above/below key MA
- COT aligned with direction

**2. Entry alert** — conditions confirmed, this is the trigger. All must be true:
- Setup alert already active for this symbol
- Price closes a 5m or 15m bar back above support (long) or below resistance (short)
- Volume above the 20-period average on that bar
- No major economic event within 30 minutes
- A qualifying candlestick pattern present at the level

**3. Exit alert** — for open positions I've registered manually:
- Price hits TP1
- Price hits TP2
- Price hits stop loss level
- Score flips direction
- Major news event that could reverse the move

### Alert Telegram format
```
🔔 [ALERT TYPE] — [SYMBOL] [TIMEFRAME]
Direction: Long / Short
Price: [current] | Session: [RTH/Globex]

Entry zone: [price range]
Stop loss: [price] ([N pts]) | Risk: $[X] per contract (MES=$5/pt)
TP1: [price] ([N pts]) | R:R [X.X]:1
TP2: [price] ([N pts]) | R:R [X.X]:1

Score: [+N] ([bias]) | Candle: [pattern or "none"]

Key session levels:
• PDH: [price] ([N pts] above/below) — [role]
• PDC: [price] ([N pts] above/below) — [role]
• PDL: [price] ([N pts] above/below) — [role]

Signal drivers:
• [COT status]
• [MA alignment]
• [RSI / MACD]
• [Candle pattern]
• [Volume vs avg]

⚠️ [Warnings: news nearby, S/R flip, COT/tech divergence, low volume]
```

Note: Dollar risk per contract uses the point value from the instrument table
above so I know exactly what a 1-contract loss costs me.

### Registering an open position for exit monitoring
After I take a trade, I tell the system:
`/register-position MES long 5615 stop=5578 tp1=5670 tp2=5720`

The system saves it to data/alerts_log.json and monitors for exit conditions.

### Alert rules
1. Never repeat the same alert within 4 hours unless price moved significantly
2. Do not alert within 15 minutes of market close (4:45 PM ET for RTH)
3. Do not alert within 30 minutes of a high-impact economic event
4. If COT and technicals conflict: fire alert but explicitly flag the conflict
5. Minimum score threshold: +5 or -5. No alerts on neutral setups.

### Futures config
```python
FUTURES_CONFIG = {
    'MES': {'tick': 0.25, 'point_value': 5,   'alert_threshold': 5},
    'MNQ': {'tick': 0.25, 'point_value': 2,   'alert_threshold': 5},
    'MYM': {'tick': 1,    'point_value': 0.50, 'alert_threshold': 5},
    'MCL': {'tick': 0.01, 'point_value': 100, 'alert_threshold': 5},
    'MGC': {'tick': 0.10, 'point_value': 10,  'alert_threshold': 5},
    'MSI': {'tick': 0.005,'point_value': 25,  'alert_threshold': 5},
    'MNG': {'tick': 0.001,'point_value': 250, 'alert_threshold': 5},
    'M2K': {'tick': 0.10, 'point_value': 5,   'alert_threshold': 5},
}
```

## Candlestick pattern recognition

Patterns are evaluated on the last 2–3 completed bars on the alert timeframe.
A qualifying pattern at a key S/R level is required for an entry alert.

### Patterns to detect
**Bullish (at support):** hammer, inverted hammer, bullish engulfing,
dragonfly doji, bullish marubozu, morning star
**Bearish (at resistance):** shooting star, hanging man, bearish engulfing,
gravestone doji, bearish marubozu, evening star
**Structure:** inside bar (consolidation before breakout), outside bar
(engulfing the prior range), doji (indecision at extreme)

### Detection rules
- Body size = (|close - open|) / (high - low)
- Lower wick = min(open,close) - low
- Upper wick = high - max(open,close)
- Pattern only valid within 0.3% of a key S/R level
- Strong pattern = +1 to score, no pattern = 0, wrong pattern at level = -1

## Previous day and session levels

Calculated fresh each day before the open, saved to
`data/sessions/levels_YYYY-MM-DD.json`.

### Levels calculated daily
```
PDH  — Previous day high
PDL  — Previous day low
PDC  — Previous day close
PDM  — Previous day midpoint
DO   — Current day open (first 1-min bar)
IBH  — Initial balance high (high of first 30 min of RTH)
IBL  — Initial balance low (low of first 30 min of RTH)
WO   — Weekly open
WH   — Weekly high so far
WL   — Weekly low so far
MO   — Monthly open
ONH  — Overnight (Globex) high
ONL  — Overnight (Globex) low
```

### S/R flip detection
When price breaks PDH from below and comes back to test it → relabel as support.
When price breaks PDL from above and comes back to test it → relabel as resistance.
Label these in the alert: `PDH: 5,648 — S/R FLIP → now support`

### Gap analysis at open (9:30 AM ET)
Compare DO to PDC:
- Gap up > 0.3%: note gap fill level as potential early resistance
- Gap down > 0.3%: note gap fill level as potential early support
- Include in morning brief and first alert of the day

## TradingView MCP — required integration

### Installation
```bash
git clone https://github.com/LewisWJackson/tradingview-mcp-jackson.git \
  ~/tradingview-mcp-jackson
cd ~/tradingview-mcp-jackson && npm install
cp rules.example.json rules.json
```

Add to ~/.claude/.mcp.json (merge, do not overwrite):
```json
{
  "mcpServers": {
    "tradingview": {
      "command": "node",
      "args": ["~/tradingview-mcp-jackson/src/server.js"]
    }
  }
}
```

Launch TradingView Desktop with debug port before running anything:
- Mac: `~/tradingview-mcp-jackson/scripts/launch_tv_debug_mac.sh`
- Windows: `tradingview-mcp-jackson\scripts\launch_tv_debug.bat`

### rules.json (pre-fill with my context)
```json
{
  "watchlist": ["MES","MNQ","MYM","MCL","MGC","MSI","MNG","M2K"],
  "bias_criteria": {
    "bullish": "price above 50 EMA and 200 EMA, RSI above 50, MACD histogram positive, price above VWAP",
    "bearish": "price below 50 EMA and 200 EMA, RSI below 50, MACD histogram negative, price below VWAP"
  },
  "risk_rules": [
    "alerts only — I place all trades manually",
    "minimum 1.5:1 R:R on every alerted setup",
    "no alerts within 15 minutes of market close",
    "no alerts within 30 minutes of high-impact economic events"
  ],
  "indicators_to_read": [
    "EMA 9","EMA 21","EMA 50","EMA 200",
    "RSI 14","MACD (12,26,9)","Volume","VWAP","ATR 14"
  ]
}
```

### How TradingView MCP is used

**In alert_engine.py:** On each check, try to read live indicator values from
the open TradingView chart via `chart_get_state` and `data_get_study_values`.
Fall back to yfinance calculations if TradingView is not connected.

**In sr_levels skill:** When connected, read any levels already drawn on the
chart via `data_get_pine_lines` and `data_get_pine_labels`. Draw calculated
S/R levels back onto the chart using `draw_shape`.

**In morning_brief skill:** Read daily chart state for each watchlist symbol,
draw PDH/PDL/PDC lines on each chart, take a screenshot to
`data/sessions/screenshots/YYYY-MM-DD/`.

**Startup check:** On launch, `futures_alerts.py` calls `tv_health_check`.
Prints status clearly — either "TradingView MCP connected — using live data"
or "TradingView not connected — falling back to yfinance". Re-checks every
10 minutes and switches to live data if TradingView reconnects.

### Timeframe mapping
| Alert type | TradingView timeframe |
|---|---|
| Scalp (5m alert) | '5' |
| Intraday (15m alert) | '15' |
| Swing (1h alert) | '60' |
| Morning brief | 'D' then '60' |

## COT data agent

- Source: CFTC zip file (adjust year dynamically)
  `https://www.cftc.gov/files/dea/history/fut_fin_txt_{YEAR}.zip`
- Parse for: ES, NQ, YM, GC, SI, CL, NG, EUR FX, GBP, JPY
- Extract: non-commercial longs, shorts, net position, week-over-week change
- Score -2 to +2 per rules in scoring system table above
- Cache to `data/cot/cot_latest.json`
- Re-download only on Friday after 3:35 PM ET or if cache > 24 hours old

## Economic data agent

- Source: FMP API `/v3/economic?apikey={FMP_API_KEY}`
- Track for US: GDP, NFP, CPI YoY, Core CPI, PCE, PPI, ISM Mfg,
  ISM Services, Retail Sales, Unemployment Rate, Initial Jobless Claims
- Compare actual vs forecast: blue = beat, red = miss, gray = in-line
- Note inflation inversion: high inflation beat = bullish USD, bearish stocks
- Score overall US economy -2 to +2
- Cache to `data/economic/eco_latest.json`, re-fetch max once per 6 hours
- Free tier = 250 req/day — cache aggressively

## Dashboard

Single self-contained `dashboard/index.html` that auto-refreshes prices every
60 seconds using Yahoo Finance query API (free, no key).

Sections:
1. **Header** — live ET clock, last refresh time, live pulse indicator
2. **Symbol selector + timeframe pills** — MES, MNQ, MYM, MCL, MGC, MSI,
   plus EURUSD, GBPUSD for reference
3. **Watchlist table** — Symbol | Bias badge | TEC|COT|RET|SEA|ECO|P/C|MOM
   dots | Score. Click row → intraday panel opens.
4. **Filter pills** — All / Very Bullish / Bullish / Bearish / Very Bearish /
   Indices / Metals / Commodities
5. **Intraday panel** (opens on row click):
   - Price ladder: all S/R levels color-coded, distance in pts from current
     price. Click a level to load it into entry planner.
   - Entry/exit planner: Entry, Stop, Target inputs → auto-calculates R:R,
     dollar risk per contract using point_value from FUTURES_CONFIG
   - Pre-loaded setups (break & retest, fade) with one-click load
   - Technical signals: 50MA, 200MA, RSI, MACD, ADX
   - Session levels block: PDH, PDL, PDC, IBH, IBL, ONH, ONL
6. **Sentiment strip** — Put/Call gauge, AAII sentiment bars, BTC/Gold ratio
7. **COT section** — biggest institutional buys vs sells this week
8. **Economic heat map** — US data vs forecast, color-coded
9. **Four Claude buttons** — "Find best entry ↗", "Futures scan ↗",
   "Morning brief ↗", "Daily bias check ↗"

## Slash commands

| Command | What it does |
|---------|-------------|
| `/trade-analyze MES` | Full 5-agent analysis, saves report to data/reports/ |
| `/trade-quick MGC` | 60-second snapshot with score and key levels |
| `/morning-brief` | Watchlist scan, top setups, key events, saves brief |
| `/sr-levels MNQ 15m` | Key S/R levels for symbol + timeframe, draws on TV chart |
| `/futures-scan` | Scan all 8 micros, rank by score and setup quality |
| `/register-position MES long 5615 stop=5578 tp1=5670 tp2=5720` | Register open position for exit monitoring |
| `/strategies` | Full strategy reference — Gap Fill, VWAP Pullback, ORB, EMA Pullback with conditions, levels, and backtest results |
| `/rr-calculator MES 5200 5192 250` | Calculate exact R:R, TP1/TP2, and position sizing for any trade |
| `/account-plan 250` | Account requirements, margin breakdown, forex vs futures guide, growth roadmap |

## Scripts schedule

| Script | When | What |
|--------|------|------|
| `scripts/setup_tradingview_mcp.sh` | One-time setup | Installs TV MCP |
| `scripts/calculate_daily_levels.py` | 9:15 AM ET | PDH/PDL/PDC/IBH/IBL for all symbols, sends Telegram morning levels |
| `scripts/morning_setup.py` | 8:30 AM ET | COT + economic refresh, morning brief, saves to data/sessions/ |
| `scripts/refresh_dashboard.py` | Every 5 min, market hours | Fetches prices, injects data into index.html |
| `alerts/futures_alerts.py` | Continuous, 6 AM – 5 PM ET | Monitors all 8 micros, fires Telegram alerts |
| `scripts/market_close.py` | 4:15 PM ET | EOD summary, active registered positions status |

## Trading rules Claude must follow

1. Never suggest placing an order — information and alerts only
2. Always show stop loss and at least one take profit with every setup
3. Minimum 1.5:1 R:R — do not alert on setups below this
4. No alerts within 15 minutes of market close
5. No alerts within 30 minutes of a high-impact economic event
6. If COT and technicals conflict: alert but flag the conflict explicitly
7. Do not repeat the same alert within 4 hours
8. Dollar risk per alert must use the correct point_value per instrument
9. Never ask about account size or suggest position sizing based on capital —
   I determine my own position size

## Stock and options alerts

### Stock watchlist
```python
STOCK_WATCHLIST = [
    'SPY',   # S&P 500 ETF
    'QQQ',   # Nasdaq 100 ETF
    'AAPL',  # Apple
    'NVDA',  # Nvidia
    'TSLA',  # Tesla
    'MSFT',  # Microsoft
]
```

### Stock alert thresholds
- Score >= +7 or <= -7 to fire (higher bar than futures which uses +5/-5)
- This filters out weaker signals and keeps alerts high-conviction only
- Re-alert cooldown: 8 hours (longer than futures 4h cooldown)

### Stock alert types

**1. Swing setup alert** — multi-day opportunity forming
Fires when:
- Score >= +7 or <= -7
- Price at or near a key daily S/R level (within 0.5%)
- RSI not overbought (< 70 for longs) or oversold (> 30 for shorts)
- Above/below the 50-day MA in the direction of the trade
- Volume confirmation on the daily bar (above 20-day average)
- Label: `📈 SWING SETUP` or `📉 SWING SETUP`

**2. Day trade alert** — intraday momentum setup
Fires when:
- Score >= +7 or <= -7
- Price breaks and closes above key intraday resistance (long)
  or below key intraday support (short) on the 15m chart
- VWAP aligned with direction
- Volume spike (>= 1.5x the 20-period average on trigger bar)
- Within the first 2 hours of the session (9:30–11:30 AM ET) or
  power hour (2:30–4:00 PM ET) — highest probability windows
- Label: `⚡ DAY TRADE` or `⚡ DAY TRADE SHORT`

### Stock alert Telegram format
```
📈 SWING SETUP — NVDA
Direction: Long | Timeframe: Daily / Swing (2–5 days)
Price: $892.40 | Score: +8 (Bullish)

Setup: Pullback to 50-day MA + key support zone
Entry zone: $888–$895
Stop: $872 ($20 risk per share)
TP1: $920 (3.1% | R:R 1.4:1)
TP2: $945 (5.9% | R:R 2.7:1)

Score breakdown: TEC+2 COT+1 SEA+1 ECO+2 MOM+2
Candle: Hammer at 50MA support (strong)

Options idea (if trading options):
→ Buy NVDA call, 30–45 DTE, delta 0.45–0.55
→ Target strike near $900–$910
→ Defined risk: premium paid is max loss

⚠️ Earnings in 12 days — consider closing before then
```

```
⚡ DAY TRADE — SPY
Direction: Long | Timeframe: Intraday
Price: $562.18 | Score: +7 (Bullish)

Setup: VWAP reclaim + break above morning resistance
Entry: $562–$563 (market open momentum)
Stop: $559.50 ($2.50–$3 risk)
TP1: $565.50 (R:R 1.5:1)
TP2: $568 (R:R 2.5:1)

Score: TEC+2 COT+1 SEA+1 ECO+2 MOM+1
Volume: 1.8x average — strong confirmation

Options idea (if trading options):
→ Buy SPY call, 0–7 DTE (weekly), delta 0.50–0.60
→ Target strike: $563–$565
→ Close before end of session — no overnight holds on weeklies

⚠️ Fed speaker at 2:00 PM — watch for volatility
```

### Options alert logic

Since you are buying directional options (calls for bullish, puts for bearish),
the options section of each stock alert provides:

1. **Direction**: call (bullish setup) or put (bearish setup)
2. **DTE guidance**:
   - Swing trade setup → 30–45 DTE (gives the trade time to work)
   - Day trade setup → 0–7 DTE weekly (short-dated, faster move)
3. **Delta target**:
   - Aggressive: 0.55–0.70 delta (more expensive, moves faster)
   - Moderate: 0.40–0.55 delta (recommended default)
   - Conservative: 0.25–0.40 delta (cheaper, needs bigger move)
4. **Strike guidance**: approximately ATM or one strike OTM based on entry zone
5. **Earnings warning**: always flag if earnings are within 21 days —
   IV crush risk after earnings can destroy a long options position even
   if the stock moves your way

The system does NOT specify an exact strike and expiry — that depends on
what's available on your broker platform and your risk tolerance. The alert
gives you the framework; you select the specific contract.

### Options-specific warnings to always include
- `⚠️ Earnings in X days — IV elevated, consider closing before report`
- `⚠️ Ex-dividend date in X days — early assignment risk on calls`
- `⚠️ IV Rank above 60 — options are expensive right now, consider smaller size`
- `⚠️ Low IV — options are cheap, this is actually a good time to buy`
- `⚠️ Weekly option — high theta decay, close same day if not moving`

### Stock vs futures alert separation in Telegram
Use distinct emoji prefixes so they're easy to scan in your Telegram feed:
- `🔔` — futures setup alert (MES, MNQ, etc.)
- `✅` — futures entry alert (confirmed trigger)
- `🚨` — futures exit alert (stop/TP hit)
- `📈` — stock swing setup (bullish)
- `📉` — stock swing setup (bearish)
- `⚡` — stock day trade alert
- `📊` — morning brief / daily levels
- `⚠️` — warning / economic event
- `💰` — options-specific note inside a stock alert

### STOCK_CONFIG
```python
STOCK_CONFIG = {
    'SPY':  {'type': 'etf',   'alert_threshold': 7, 'swing': True, 'day': True},
    'QQQ':  {'type': 'etf',   'alert_threshold': 7, 'swing': True, 'day': True},
    'AAPL': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': False},
    'NVDA': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': False},
    'TSLA': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': True},
    'MSFT': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': False},
}
```

Day trade alerts are only enabled for SPY, QQQ, and TSLA — these have the
liquidity and intraday volatility that makes day trading worthwhile.
AAPL, NVDA, MSFT are swing-only alerts.

### Earnings calendar check
Before firing any stock alert, check if earnings are within 21 days.
Use FMP endpoint: `GET /v3/earning_calendar?from={today}&to={21_days_out}&apikey={KEY}`
- Within 7 days: include strong warning, consider not alerting at all for
  swing setups (too much binary risk)
- Within 8–21 days: include warning, note IV elevation risk
- Beyond 21 days: no earnings note needed

### Stock scoring — same 7-signal system, different data sources
| Signal | Stock source |
|--------|-------------|
| TEC | Price vs 50-day and 200-day MA (daily chart) |
| COT | Not applicable for individual stocks — use 0 (neutral) |
| RET | Short interest ratio (high short interest + rising price = contrarian bullish) |
| SEA | Monthly seasonality for SPY/QQQ; skip for individual stocks |
| ECO | US economic score from economic_agent (same as futures) |
| P/C | Equity put/call ratio for SPY/QQQ; individual stock P/C if available |
| MOM | Price momentum: 20-day rate of change, RSI trend, MACD direction |

Note: COT does not apply to individual stocks so that signal is always 0.
Maximum possible score for stocks is therefore +12 / -12.
Adjust thresholds accordingly: +7 still represents strong conviction.

### How stock monitoring integrates into existing system
Add `stock_alerts.py` to the `alerts/` folder alongside `futures_alerts.py`.
Both run concurrently. Both use the same `notifier.py` to send Telegram.
`scripts/refresh_dashboard.py` pulls from both when updating the dashboard.
Morning brief includes both futures and stock top setups.

## Stock and options alert system

### Stock watchlist (monitored alongside futures)
```python
STOCK_WATCHLIST = [
    # Indices (ETFs)
    'SPY', 'QQQ', 'IWM', 'DIA',
    # Big tech
    'AAPL', 'NVDA', 'TSLA', 'MSFT', 'AMZN', 'META', 'GOOGL',
    # High IV / active options
    'PLTR', 'AMD', 'SOFI', 'RIVN', 'MSTR', 'COIN',
    # Energy / commodities proxy
    'XLE', 'GLD', 'USO',
]
```

The scoring system (same 7-signal system) applies to stocks too.
TEC, SEA, ECO, MOM are calculated the same way.
COT is not available for individual stocks — that slot scores 0 for stocks.
P/C ratio is per-symbol using CBOE data where available, otherwise 0.
RET uses retail sentiment data where available, otherwise 0.

### Stock alert conditions
Same threshold as futures: score >= +5 or <= -5.
Additional requirements before firing a stock alert:
- Price closes above a key level with volume > 1.2x 20-day average (long)
- Price closes below a key level with volume > 1.2x 20-day average (short)
- Qualifying candlestick pattern on the daily or 1h chart
- Not within 5 days of earnings — check earnings calendar via FMP
- Not within 1 day of an ex-dividend date for covered call alerts

### Stock alert Telegram format
```
📈 [ALERT TYPE] — [SYMBOL] [TIMEFRAME]
Direction: Long / Short
Price: $[current] | Change: [+/-X.X%]
Score: [+N] ([bias])

Technical setup:
• [MA alignment]
• [RSI status]
• [Candle pattern at level]
• [Volume: Xx average]

Key levels:
• Resistance: $[price] ([N pts])
• Support: $[price] ([N pts])
• Stop zone: $[price]

⚠️ [Warnings: earnings in X days, ex-div date, low IV, etc.]

👇 Options setup below
```

### Options alert — immediately follows the stock alert

When a stock alert fires, the system generates an options recommendation
in the same Telegram message (or as an immediate follow-up message).

#### For bullish stock alerts — two options:

**Option A — Buy a call (directional, limited risk):**
```
📊 OPTIONS — [SYMBOL] CALL
Strategy: Buy call
Contract: [SYMBOL] $[strike]C exp [date] ([DTE] DTE)
Current ask: ~$[premium]
Break-even: $[strike + premium]
Max risk: $[premium * 100] per contract
Target: $[price at TP1] → option value ~$[estimated]
IV rank: [X]% — [High/Medium/Low] IV environment

Best for: if you expect a fast move to TP1
```

**Option B — Sell a cash-secured put (income, if assigned you own stock):**
```
📊 OPTIONS — [SYMBOL] CSP
Strategy: Sell cash-secured put
Contract: [SYMBOL] $[strike]P exp [date] ([DTE] DTE)
Premium collected: ~$[premium] ($[premium*100] per contract)
Cash required: $[strike * 100]
Break-even at expiry: $[strike - premium]
Max profit: $[premium * 100] (if expires worthless)
Assignment price: $[strike] (you buy 100 shares here)

Best for: if you want to own [SYMBOL] at a discount, or just collect premium
```

#### For bearish stock alerts — two options:

**Option A — Buy a put (directional):**
```
📊 OPTIONS — [SYMBOL] PUT
Strategy: Buy put
Contract: [SYMBOL] $[strike]P exp [date] ([DTE] DTE)
Current ask: ~$[premium]
Break-even: $[strike - premium]
Max risk: $[premium * 100] per contract
Target: $[price at TP1] → option value ~$[estimated]
IV rank: [X]% — [High/Medium/Low]
```

**Option B — Sell a covered call (income, if you already own shares):**
```
📊 OPTIONS — [SYMBOL] COVERED CALL
Strategy: Sell covered call
Contract: [SYMBOL] $[strike]C exp [date] ([DTE] DTE)
Premium collected: ~$[premium] ($[premium*100] per contract)
Required: 100 shares of [SYMBOL]
Break-even: current price - premium
Called away at: $[strike]

Best for: if you already own shares and want income while waiting
Note: only relevant if you own 100 shares
```

### How to calculate options contract details (no broker API needed)

Use yfinance to pull the options chain:
```python
import yfinance as yf

def get_options_suggestion(symbol, direction, stock_price, target_price,
                            stop_price, prefer_dte_range=(7, 45)):
    ticker = yf.Ticker(symbol)

    # Get available expiry dates
    expiries = ticker.options  # list of expiry date strings

    # Filter to prefer_dte_range
    from datetime import datetime, date
    today = date.today()
    valid_expiries = [
        e for e in expiries
        if prefer_dte_range[0] <=
           (datetime.strptime(e, '%Y-%m-%d').date() - today).days
           <= prefer_dte_range[1]
    ]
    if not valid_expiries:
        valid_expiries = expiries[:3]  # fallback to nearest

    best_expiry = valid_expiries[0]
    chain = ticker.option_chain(best_expiry)

    if direction == 'bullish':
        # For buying a call: ATM or slightly OTM (delta ~0.40-0.50)
        calls = chain.calls
        # Find strike closest to stock_price * 1.02 (slightly OTM)
        target_strike = stock_price * 1.02
        call = calls.iloc[(calls['strike'] - target_strike).abs().argsort()[:1]]

        # For selling a CSP: strike at or below stop price
        puts = chain.puts
        csp_strike = stop_price
        put = puts.iloc[(puts['strike'] - csp_strike).abs().argsort()[:1]]

        return {
            'buy_call': {
                'strike': float(call['strike'].iloc[0]),
                'expiry': best_expiry,
                'ask': float(call['ask'].iloc[0]),
                'bid': float(call['bid'].iloc[0]),
                'iv': float(call['impliedVolatility'].iloc[0]),
                'delta': float(call.get('delta', {0: 'n/a'}).iloc[0]
                               if 'delta' in call else 'n/a'),
            },
            'sell_csp': {
                'strike': float(put['strike'].iloc[0]),
                'expiry': best_expiry,
                'bid': float(put['bid'].iloc[0]),
                'iv': float(put['impliedVolatility'].iloc[0]),
            }
        }
    else:  # bearish
        puts = chain.puts
        target_strike = stock_price * 0.98
        put = puts.iloc[(puts['strike'] - target_strike).abs().argsort()[:1]]

        calls = chain.calls
        cc_strike = stock_price * 1.03
        call = calls.iloc[(calls['strike'] - cc_strike).abs().argsort()[:1]]

        return {
            'buy_put': {
                'strike': float(put['strike'].iloc[0]),
                'expiry': best_expiry,
                'ask': float(put['ask'].iloc[0]),
                'bid': float(put['bid'].iloc[0]),
                'iv': float(put['impliedVolatility'].iloc[0]),
            },
            'sell_cc': {
                'strike': float(call['strike'].iloc[0]),
                'expiry': best_expiry,
                'bid': float(call['bid'].iloc[0]),
                'iv': float(call['impliedVolatility'].iloc[0]),
            }
        }
```

### IV environment classification
Use the 52-week IV range from yfinance to classify:
- IV rank = (current IV - 52w low IV) / (52w high IV - 52w low IV) * 100
- IV rank >= 50%: HIGH IV → favor selling options (CSP, covered call)
  — premium is rich, selling makes more sense
- IV rank < 50%: LOW/MEDIUM IV → favor buying options (calls, puts)
  — premium is cheaper, buying gives better value

Include this in every options alert so I can make an informed choice.

### Earnings check (critical for options)
Before generating any options alert, check the FMP earnings calendar:
- If earnings within 5 days: add warning "⚠️ EARNINGS in X days —
  IV will spike then collapse. Buying options before earnings is very
  risky. Consider waiting until after the report."
- If earnings within 1 day: do not suggest buying options at all.
  Only suggest selling if the setup is extremely high conviction.

### Stock alert cooldown rules
- Do not repeat the same stock/options alert within 24 hours
  (stocks move slower than futures, 4h cooldown is too short)
- Do not alert on a stock if it has already moved more than 3% today
  (the easy money is gone, risk/reward is worse)
- Flag if the stock is in a clear sector downtrend even if individual
  score is bullish — include sector ETF score as context

### Full combined alert example (bullish SPY)
```
📈 SETUP ALERT — SPY 1h
Direction: Long
Price: $562.40 | Change: +0.3%
Score: +8 (Bullish)

Technical setup:
• Price above 50 EMA (558.20) and 200 EMA (541.80)
• RSI 14: 58 — bullish, room to run
• Hammer candle at PDC support (560.10)
• Volume: 1.4x 20-day average

Key levels:
• Resistance: $567.50 (previous week high)
• Support: $560.10 (PDC — acting as support)
• Stop zone: $557.00 (below 50 EMA)

⚠️ No earnings risk | Sector (XLK) also bullish | IV rank: 38%

━━━━━━━━━━━━━━━━━━━━
📊 OPTIONS — SPY CALLS

IV rank 38% → LOW IV → Favor BUYING options

Option A — Buy call (directional):
Contract: SPY $565C exp Apr 25 (11 DTE)
Ask: ~$3.20 | Break-even: $568.20
Max risk: $320 per contract
If SPY hits $567.50 (TP1): option ~$4.80 (+50%)

Option B — Sell CSP (income):
Contract: SPY $557P exp Apr 25 (11 DTE)
Premium: ~$1.45 ($145 per contract)
Cash required: $55,700
Break-even at expiry: $555.55
```

## Updated STOCK_CONFIG
```python
STOCK_CONFIG = {
    # Scoring threshold to fire an alert
    'alert_threshold': 5,
    # Minimum volume multiplier vs 20-day average
    'volume_multiplier': 1.2,
    # Earnings buffer — no buy-options alerts within this many days
    'earnings_buffer_days': 5,
    # Cooldown between repeated alerts on same stock
    'alert_cooldown_hours': 24,
    # Max intraday move before skipping alert (move already happened)
    'max_intraday_move_pct': 3.0,
    # DTE range preferences
    'preferred_dte_min': 7,
    'preferred_dte_max': 45,
}
```

## Stock watchlist — updated configuration

### Two-tier watchlist system

**Tier 1 — Always monitored (checked every 5 minutes during market hours):**
```python
STOCK_WATCHLIST_CORE = [
    'SPY',   # S&P 500 ETF — swing + day trade alerts
    'QQQ',   # Nasdaq 100 ETF — swing + day trade alerts
    'AAPL',  # Apple — swing only
    'NVDA',  # Nvidia — swing + day trade
    'TSLA',  # Tesla — swing + day trade
    'MSFT',  # Microsoft — swing only
]
```

**Tier 2 — Daily opportunity scan (checked once per day at 9:00 AM ET):**
A broader universe scanned every morning for high-conviction setups.
Any stock that crosses +7/-7 gets added to that day's active watchlist
and monitored like a Tier 1 stock for the rest of the session.

```python
STOCK_SCAN_UNIVERSE = [
    # Mega cap tech
    'META', 'GOOGL', 'AMZN', 'AMD', 'INTC', 'AVGO',
    # Financials
    'JPM', 'BAC', 'GS',
    # Energy
    'XOM', 'CVX',
    # Sector ETFs
    'XLK', 'XLE', 'XLF', 'XLV', 'GLD', 'SLV',
    # High-beta / momentum names
    'PLTR', 'COIN', 'MSTR', 'SOFI', 'RIVN',
]
```

### Daily opportunity scan (scripts/morning_setup.py)
At 9:00 AM ET, before the open:
1. Calculate scores for all 30+ symbols in STOCK_SCAN_UNIVERSE
2. Any symbol with score >= +7 or <= -7 → add to that day's active list
3. Save active list to `data/sessions/active_stocks_YYYY-MM-DD.json`
4. Include top 3 scan finds in the morning brief Telegram message:
```
🔍 Today's scan finds (score >= +7):
• META +9 — Breaking out above 200MA, strong momentum
• GLD +8 — COT bullish, safe-haven demand rising
• COIN -8 — Below all MAs, risk-off environment

These will be monitored for alerts today alongside the core watchlist.
```
5. If nothing crosses the threshold: "No scan finds today — monitoring core watchlist only"

### Updated STOCK_CONFIG
```python
STOCK_CONFIG = {
    # Tier 1 — always monitored
    'SPY':  {'tier': 1, 'threshold': 7, 'swing': True, 'day': True},
    'QQQ':  {'tier': 1, 'threshold': 7, 'swing': True, 'day': True},
    'AAPL': {'tier': 1, 'threshold': 7, 'swing': True, 'day': False},
    'NVDA': {'tier': 1, 'threshold': 7, 'swing': True, 'day': True},
    'TSLA': {'tier': 1, 'threshold': 7, 'swing': True, 'day': True},
    'MSFT': {'tier': 1, 'threshold': 7, 'swing': True, 'day': False},

    # Tier 2 — added dynamically each morning by the opportunity scan
    # stock_alerts.py loads these from active_stocks_YYYY-MM-DD.json at startup
    # They get swing alerts only (no day trade) unless they are also in
    # the high-liquidity list: SPY, QQQ, TSLA, NVDA
}
```

### Day trade eligibility
Day trade alerts are only enabled for stocks with sufficient intraday
liquidity and volatility. Tier 2 scan finds get swing alerts only by default.
Exception: if a Tier 2 find is NVDA, TSLA, or another known high-vol name,
it can also get day trade alerts — check average daily volume >= 20M shares.
