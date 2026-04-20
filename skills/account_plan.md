# /account-plan [ACCOUNT_SIZE]

Display the full account requirements, margin breakdown, and growth roadmap.
If ACCOUNT_SIZE is not provided, use $250 as the default.

## Output format

```
═══════════════════════════════════════════════════════════════
ACCOUNT PLAN  —  Balance: ${ACCOUNT_SIZE}
═══════════════════════════════════════════════════════════════

WHAT YOU NEED TO START
─────────────────────────────────────────────────────────────
Broker:         Tradovate, NinjaTrader, or Webull  (all $0 minimum)
Platform fee:   $0–$10/month (Tradovate free tier available)
Data feed:      Free (yfinance fallback) or TradingView Desktop

Intraday margin requirements (per contract, held while trade is open):
  MES:   ~$40–50    (S&P 500 micro)
  MNQ:   ~$100–120  (Nasdaq micro)
  MYM:   ~$50–70    (Dow micro)
  M2K:   ~$50–70    (Russell micro)
  MCL:   ~$100–150  (Crude Oil micro)
  MGC:   ~$80–120   (Gold micro)

  Overnight margin is 3–5× higher — close all positions by 4:00 PM ET
  to avoid overnight margin requirements.

WHAT MUST STAY IN THE ACCOUNT
─────────────────────────────────────────────────────────────
Maintenance margin:  ~$35 MES  |  ~$85 MNQ  (broker auto-liquidates below this)
Safety buffer rule:  Never use more than 50% of account as margin

  With ${ACCOUNT_SIZE} account:
    Max margin in use:    ${account × 0.50}  (50% rule)
    MES contracts viable: {floor(account × 0.50 / 50)} contract(s) max
    MNQ contracts viable: {floor(account × 0.50 / 110)} contract(s) max

  ⚠️ If max contracts = 0 for MES, your account is below the practical
     minimum for futures. Use forex micro lots instead (see below).

RECOMMENDED VEHICLE BY ACCOUNT SIZE
─────────────────────────────────────────────────────────────
Under $500:
  → Forex micro lots (0.01 lot = $0.10/pip on EURUSD)
  → Broker: Oanda or IBKR  ($0 minimum, micro lots available)
  → 20-pip stop = $2.00 risk  →  fits 1% of a $200 account ✓
  → VWAP/EMA Pullback and Gap Fill strategies both work on forex pairs

$500–$1,000:
  → 1× MES contract  (2% risk = $10–20, supports 2–4 point stops)
  → Start gap fill strategy at open, VWAP pullback during session

$1,000–$3,000:
  → 1–2× MES  or  1× MNQ
  → Full strategy stack — Gap Fill + VWAP Pullback + ORB

$3,000–$10,000:
  → Scale up MES/MNQ contracts
  → Add stock swing alerts (SPY/QQQ options)

$10,000–$25,000:
  → ES or NQ full-size contracts
  → Options strategies (defined risk spreads)

$25,000+:
  → PDT rule no longer applies (eliminated April 14, 2026)
  → Full discretionary trading across all instruments

RISK MANAGEMENT RULES
─────────────────────────────────────────────────────────────
Max risk per trade:    2% of account  →  ${account × 0.02}
Max daily loss:        6% of account  →  ${account × 0.06}  (3 trades × 2%)
  If daily loss limit hit: STOP trading for the day, no exceptions.

Never risk more than 2% on a single trade.
Never have more than 3 active positions at once.
Never hold futures overnight unless account is above maintenance margin × 3.

GROWTH PROJECTION  (responsible compounding)
─────────────────────────────────────────────────────────────
Assumptions:
  Gap Fill strategy:    70% WR  |  avg +0.23R per trade
  VWAP/EMA Pullback:   40% WR  |  avg +0.05R per trade (bear market)
                        50% WR  |  avg +0.15R per trade (trending market)
  Trades per day:       2 (conservative — staying within 3-trade limit)
  Trading days/month:   20

Conservative scenario (bear market, Gap Fill only):
  Avg R per day:    0.23R × 0.70 win rate × 1 gap fill = +0.16R/day
  Monthly gain:     ~3.2R/month
  On $250 (2% risk = $5/trade):  +$16/month  →  $266 after month 1

Trending market scenario:
  Gap Fill + 1 VWAP signal/day
  Avg R per day:    +0.30R/day
  Monthly gain:     ~6R/month
  On $500 (2% risk = $10/trade):  +$60/month  →  $560 after month 1

Milestones:
  $500   → trade 1× MES consistently
  $1,000 → trade 1× MNQ, add ORB strategy
  $5,000 → trade 2–3 contracts, add stock swing alerts
  $25,000 → full unrestricted trading

⚠️ REALISTIC EXPECTATION
─────────────────────────────────────────────────────────────
Growing $250 → $25,000 takes time even with consistent edge.
At 6R/month compounding: ~18–24 months of consistent execution.
Do not increase risk % to grow faster — that path blows accounts.
The edge compounds. Protect the account first.
═══════════════════════════════════════════════════════════════
```

## Rules
- Always show the forex micro lot option for accounts under $500
- Dollar amounts always use the provided ACCOUNT_SIZE (or $250 default)
- Point values and margin numbers come from FUTURES_CONFIG and CLAUDE.md
- Growth projections use backtest win rates from the actual system backtest
- Always show the daily loss limit and note the system halts automatically if hit
- Note: at $500 the system automatically switches from forex to futures execution
