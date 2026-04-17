# /rr-calculator {SYMBOL} {ENTRY} {STOP} [ACCOUNT_SIZE]

Calculate exact R:R, dollar risk, TP1, TP2, and position sizing for a trade.
If ACCOUNT_SIZE is not provided, use $250 as the default.

## Steps

1. Look up the point_value for {SYMBOL} from FUTURES_CONFIG in CLAUDE.md:
   MES=$5/pt, MNQ=$2/pt, MYM=$0.50/pt, M2K=$5/pt,
   MCL=$100/pt, MGC=$10/pt, MSI=$25/pt, MNG=$250/pt

2. For forex pairs, use pip value based on lot size:
   Micro lot (0.01):  EURUSD/GBPUSD/AUDUSD = $0.10/pip
   Mini  lot (0.10):  = $1.00/pip
   Standard lot (1.0): = $10.00/pip

3. Calculate the following and display in the format below.

## Output format

```
═══════════════════════════════════════════
R:R CALCULATOR — {SYMBOL}
═══════════════════════════════════════════

Trade setup:
  Entry:       {ENTRY}
  Stop:        {STOP}
  Risk (pts):  {entry - stop} pts
  Risk ($):    {risk_pts × point_value} per contract

Account: ${ACCOUNT_SIZE}
  1% risk budget:  ${account × 0.01}
  2% risk budget:  ${account × 0.02}  ← recommended max per trade

─────────────────────────────────────────
TAKE PROFIT LEVELS
─────────────────────────────────────────
  TP1  (1.5R):  {entry + 1.5 × risk_pts}
               +{1.5 × risk_pts} pts  |  +${1.5 × dollar_risk}  |  R:R 1.5:1

  TP2  (2.5R):  {entry + 2.5 × risk_pts}
               +{2.5 × risk_pts} pts  |  +${2.5 × dollar_risk}  |  R:R 2.5:1

  Gap Fill TP  (0.75R):  {entry + 0.75 × risk_pts}
               +{0.75 × risk_pts} pts  |  +${0.75 × dollar_risk}

─────────────────────────────────────────
POSITION SIZING (by risk budget)
─────────────────────────────────────────
  To risk 1% (${account × 0.01}):
    Max contracts: {floor(1% budget / dollar_risk)}  ← round DOWN always

  To risk 2% (${account × 0.02}):
    Max contracts: {floor(2% budget / dollar_risk)}

  ⚠️ If max contracts = 0, your stop is too wide for this account size.
     Either widen your account or tighten the stop closer to a key level.

─────────────────────────────────────────
RISK CHECK
─────────────────────────────────────────
  Dollar risk (1 contract):  ${dollar_risk}
  As % of account:           {dollar_risk / account × 100:.1f}%

  Status: [PASS if ≤ 2% | CAUTION if 2–5% | FAIL if > 5%]

  Minimum R:R required: 1.5:1
  TP1 R:R: {tp1_rr}:1  →  [MEETS / FAILS minimum]
═══════════════════════════════════════════
```

## Rules
- Always round contract count DOWN (never up) — never over-size
- Never suggest placing an order
- If dollar risk exceeds 5% of account on 1 contract, show a clear warning
- Point values come from FUTURES_CONFIG — never hardcode
- For forex: always specify lot size used in the calculation
- Always show the Gap Fill TP (0.75R) alongside the standard TP1/TP2
  since Gap Fill uses a smaller target than VWAP/ORB strategies
