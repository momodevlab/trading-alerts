# /futures-scan

Scan all 8 micro futures (MES, MNQ, MYM, MCL, MGC, MSI, MNG, M2K) and produce a ranked table of current setup quality.

## Steps

1. Run `python alerts/futures_alerts.py --test ALL` to get live scores, indicators, and level data for all symbols.

2. For each symbol, calculate:
   - Total score (-14 to +14) and bias label
   - Top 3 signal drivers (which of TEC/COT/RET/SEA/ECO/PC/MOM are most extreme)
   - Nearest key S/R level and distance in points
   - Setup quality: "Alert-ready" (|score| ≥ 5), "Developing" (|score| 3-4), or "Neutral"
   - Whether COT and technicals **conflict** (COT and TEC signals in opposite directions)

3. Rank from most bullish (highest total) to most bearish (lowest total).

4. Format as a clean table:

```
FUTURES SCAN — [DATE] [TIME] ET
═══════════════════════════════════════════════════════════════

Symbol  Score   Bias          Top Drivers          Nearest Level    Setup
──────────────────────────────────────────────────────────────
MGC     +8      Bullish       COT+2 TEC+2 MOM+2    PDL 3,195 (23pt)  ⚡ Alert-ready
MNQ     +7      Bullish       TEC+2 ECO+2 MOM+2    PDH 19,620 (8pt)  ⚡ Alert-ready
MES     +6      Bullish       TEC+2 COT+1 ECO+1    IBH 5,638 (17pt)  ● Developing
MSI     +6      Bullish       TEC+2 COT+1 MOM+2    PDL 31.85 (0.3pt) ⚡ Alert-ready
MYM     +4      Neutral       TEC+1 COT+1 SEA+1    WO 41,640 (28pt)  ● Developing
M2K     +3      Neutral       TEC+1 SEA+1 ECO+1    PDC 2,028 (14pt)  — Neutral
MNG     +2      Neutral       TEC+1 MOM+1           DO 2.008 (0.01pt) — Neutral
MCL     -3      Neutral       TEC-1 SEA-1 ECO-1    ONH 62.1 (0.7pt)  — Neutral

⚠️ COT/TECH CONFLICTS: None detected
```

5. Below the table, add:
   - **Best long candidate**: highest positive score with setup conditions met
   - **Best short candidate**: lowest negative score with setup conditions met
   - **Notable conflicts**: any symbol where COT direction ≠ TEC direction

6. If `--test ALL` fails (TradingView not connected), note it and use yfinance fallback data.

## Rules
- Never suggest placing a trade — analysis only
- Always include the COT conflict flag if present
- Include point value reminder for dollar risk awareness
