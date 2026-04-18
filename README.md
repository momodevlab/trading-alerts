# Trading Research & Alert System

A research, dashboard, alert, and automated execution system for futures and forex trading.
Monitors markets 24/5, scores setups across 7 signals, sends Telegram alerts, and
automatically executes orders through connected broker accounts.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | `python3 --version` |
| Node.js | 18+ | Required for TradingView MCP |
| TradingView Desktop | Latest | Paid subscription required for MCP access |
| pip packages | See requirements.txt | Installed in step 4 |

---

## Setup

### 1. Clone / extract the project

```bash
# The project lives in ~/Desktop/Trading/
cd ~/Desktop/Trading
```

### 2. Copy and fill in your API keys

```bash
cp .env.example .env
```

Edit `.env`:
```
MARKET_DATA_PROVIDER=none       # default: disable macro provider for now
FMP_API_KEY=your_key_here       # optional; only needed if MARKET_DATA_PROVIDER=fmp
TELEGRAM_BOT_TOKEN=your_token   # from @BotFather on Telegram
TELEGRAM_CHAT_ID=your_chat_id   # from @userinfobot on Telegram
TRADINGVIEW_MCP_PORT=9222       # leave as-is unless you have a conflict
```

### 3. Optional: Get an FMP API key

1. Go to [financialmodelingprep.com](https://financialmodelingprep.com)
2. Sign up for a free account (250 requests/day — enough for this system)
3. Copy your API key into `.env`

### 4. Set up Telegram alerts

**Step 1 — Create your bot:**
1. Open Telegram, search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the token into `TELEGRAM_BOT_TOKEN` in `.env`

**Step 2 — Get your Chat ID:**
1. Search for `@userinfobot` in Telegram
2. Send it any message
3. Copy the `Id:` number into `TELEGRAM_CHAT_ID` in `.env`

**Step 3 — Start the bot:**
1. Find your bot in Telegram and send it `/start`
2. Test it after setup with: `python alerts/notifier.py`

### 5. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 6. TradingView MCP setup

The TradingView MCP gives the alert engine access to live chart data and indicator
values directly from your open TradingView charts. It's optional — the system falls
back to yfinance if TradingView isn't connected.

```bash
bash scripts/setup_tradingview_mcp.sh
```

This script:
- Clones the MCP repo and installs dependencies
- Writes a pre-configured `rules.json` with your watchlist
- Updates `~/.claude/.mcp.json` to register the MCP server

After running it:
1. Open **TradingView Desktop** (must be the desktop app, not browser)
2. Launch with debug port:
   ```bash
   bash scripts/launch_tradingview_debug.sh
   ```
   *(Windows: `tradingview-mcp-jackson\scripts\launch_tv_debug.bat`)*
3. **Restart Claude Code** so it picks up the new MCP server
4. Test the connection: type `tv_health_check` in Claude Code

If you launch from VS Code / Codex and TradingView opens but CDP never appears, the usual cause is `ELECTRON_RUN_AS_NODE=1` leaking into the app process. The launcher above clears that automatically.

The alert monitor will print:
- `TradingView MCP connected — using live data` ✓
- `TradingView not connected — falling back to yfinance` (if not running)

---

## Running the system

### Open the dashboard

```bash
python -m http.server 8080 --directory dashboard
```
Then open [http://localhost:8080](http://localhost:8080) in your browser.

### Start the dashboard refresh loop

```bash
python scripts/refresh_dashboard.py --loop
```
Refreshes prices every 5 minutes during market hours (9:30 AM – 4:15 PM ET).

### Start the futures alert monitor

```bash
python alerts/futures_alerts.py
```
Monitors MES, MNQ, MYM, MCL, MGC, MSI, MNG, M2K every 60 seconds during
6 AM – 5 PM ET, Mon–Fri. Fires Telegram + terminal alerts when setup or entry
conditions are met.

### Start the stock alert monitor

```bash
python alerts/stock_alerts.py
```
Monitors SPY, QQQ, AAPL, NVDA, TSLA, MSFT (core) plus daily scan finds.
Runs every 5 minutes during market hours (9:30 AM – 4:00 PM ET only).

### Run both monitors at once (one terminal)

```bash
python scripts/start_all.py
```

---

## Daily scripts (optional, run manually or via cron)

| Script | When | Command |
|---|---|---|
| Morning setup | 8:30 AM ET | `python scripts/morning_setup.py` |
| Daily levels + chart lines | 9:15 AM ET | `python scripts/calculate_daily_levels.py` |
| Daily stock scan | 9:00 AM ET (auto) | run via `morning_setup.py` |

**Cron examples:**
```bash
# Edit crontab: crontab -e
30 8 * * 1-5  cd ~/Desktop/Trading && python scripts/morning_setup.py
15 9 * * 1-5  cd ~/Desktop/Trading && python scripts/calculate_daily_levels.py
```

---

## Slash commands (use in Claude Code)

| Command | What it does |
|---|---|
| `/futures-scan` | Scan all 8 micros, ranked table with setup quality |
| `/trade-analyze MES` | Full 5-agent analysis, saves report to data/reports/ |
| `/trade-quick MGC` | 60-second snapshot with score and key levels |
| `/morning-brief` | Full morning brief: top setups, events, positions |
| `/sr-levels MNQ 15m` | All S/R levels for symbol + timeframe, draws on TV chart |
| `/register-position MES long 5615 stop=5578 tp1=5670` | Register position for exit monitoring |
| `/register-position MES long 5615 stop=5578 tp1=5670 tp2=5720` | With TP2 |
| `/stock-scan` | Scan all stocks + universe, ranked by score |

---

## Registering an open position for exit monitoring

After you take a trade manually, tell the system so it can watch for exit conditions:

```
/register-position MES long 5615 stop=5578 tp1=5670 tp2=5720
/register-position MGC short 3218 stop=3241 tp1=3180
/register-position SPY long 562.50 stop=558.00 tp1=568.00
```

The system will fire alerts when:
- TP1 or TP2 is hit
- Stop loss level is hit
- Score flips direction
- Major economic event could affect the position

---

## Testing without TradingView

Run a single check on any symbol — prints full evaluation, does NOT send Telegram:

```bash
python alerts/futures_alerts.py --test MES
python alerts/futures_alerts.py --test ALL
python alerts/stock_alerts.py --test SPY
python alerts/stock_alerts.py --test ALL
```

---

## Project structure

```
trading-system/
├── CLAUDE.md                    ← full system spec and rules
├── .env                         ← API keys (never commit)
├── .env.example                 ← template
├── requirements.txt
├── README.md
├── dashboard/
│   └── index.html               ← self-contained dashboard (auto-refreshes)
├── data/
│   ├── cot/cot_latest.json      ← CFTC COT cache
│   ├── economic/eco_latest.json ← FMP economic data cache
│   ├── sessions/                ← daily levels + morning briefs
│   ├── reports/                 ← trade analysis reports
│   └── alerts_log.json          ← history of all alerts + registered positions
├── agents/
│   ├── cot_agent.py             ← CFTC COT fetcher, parser, scorer
│   └── economic_agent.py        ← FMP economic heat map builder
├── brokers/
│   ├── oanda_client.py          ← Oanda v20 REST client (forex, active)
│   └── tradovate_client.py      ← Tradovate REST client (futures, dormant until $500)
├── alerts/
│   ├── futures_alerts.py        ← main monitoring loop + broker execution
│   ├── stock_alerts.py          ← stock + options monitoring loop
│   ├── alert_engine.py          ← scoring, levels, patterns, formatting
│   └── notifier.py              ← Telegram + terminal + log
├── skills/
│   ├── futures_scan.md          ← /futures-scan
│   ├── trade_analyze.md         ← /trade-analyze
│   ├── morning_brief.md         ← /morning-brief
│   ├── sr_levels.md             ← /sr-levels
│   ├── register_position.md     ← /register-position
│   └── stock_scan.md            ← /stock-scan
└── scripts/
    ├── setup_tradingview_mcp.sh ← one-time TV MCP setup
    ├── morning_setup.py         ← 8:30 AM ET daily brief
    ├── calculate_daily_levels.py← 9:15 AM ET levels + TV chart lines
    ├── daily_stock_scan.py      ← 9:00 AM ET scan universe
    ├── refresh_dashboard.py     ← every 5 min market hours
    └── start_all.py             ← launch everything from one terminal
```

---

## Alert format reference

All Telegram alerts use these emoji prefixes so you can scan them quickly:

| Emoji | Meaning |
|---|---|
| 🔔 | Futures setup alert |
| ✅ | Futures entry alert (confirmed trigger) |
| 🚨 | Futures exit alert (stop/TP hit) |
| 📈 | Stock swing setup (bullish) |
| 📉 | Stock swing setup (bearish) |
| ⚡ | Stock day trade alert |
| 📊 | Morning brief / daily levels |
| ⚠️ | Warning / economic event |
| 📈 (ORDER PLACED) | Auto-executed order confirmation |
| ⚠️ (ORDER FAILED) | Auto-execution failed — manual entry needed |

---

## Broker integration

| Broker | Asset class | Status | Activation |
|--------|-------------|--------|------------|
| Oanda (v20 REST) | Forex — all major/minor pairs | Active | `AUTO_TRADE_ENABLED=true` in `.env` |
| Tradovate | Futures — MES, MNQ, MYM, MCL, MGC, MSI, MNG, M2K | Dormant | Activate when account ≥ $500 |

When a strategy signal fires:
1. Telegram alert sent immediately
2. Oanda/Tradovate order placed automatically (limit order with stop + TP attached)
3. Telegram confirmation sent with order details, lot size, and dollar risk

Lot size is calculated automatically from `ACCOUNT_SIZE` and `RISK_PCT` so risk
stays consistent as the account grows.

**To disable auto-execution** (alerts only): set `AUTO_TRADE_ENABLED=false` in `.env`.

---

## Notes

- **Forex automation** — Oanda v20 REST API, 24/5 across Asian/London/NY sessions
- **Futures automation** — Tradovate, dormant until account is funded to $500+
- **FMP free tier** — 250 requests/day. The system caches aggressively: COT 24h, economic 6h
- **Score system** — 7 signals × (-2 to +2) = -14 to +14 total. Alert threshold: ±5 futures, ±7 stocks
- **R:R minimum** — 1.5:1 required for any alerted setup
- **Dollar risk** — always calculated using the correct point value per instrument (e.g. MES = $5/pt)
