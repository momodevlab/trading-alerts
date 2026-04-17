# /morning-brief

Generate the full morning trading brief. Equivalent to running `scripts/morning_setup.py` but formats the output for reading here in Claude Code.

## Steps

1. Run `python scripts/morning_setup.py` to refresh all caches and generate the brief file.

2. Read the saved brief from `data/sessions/YYYY-MM-DD_morning.txt`.

3. Format and display the full brief here:

```
═══════════════════════════════════════════════════════════════
MORNING BRIEF — [Weekday, Month Day, Year]  [HH:MM ET]
═══════════════════════════════════════════════════════════════

MARKET OVERVIEW
Overall bias: [Bullish/Bearish/Neutral] — [1 sentence why]
Economy score: [+N] | COT trend: [direction]

────────────────────────────
TOP 3 LONG SETUPS (FUTURES)
────────────────────────────
1. [SYMBOL] +[N] ([Bias])
   Entry zone: [price range]
   Stop: [price] | TP1: [price] (R:R [X]:1) | TP2: [price] (R:R [X]:1)
   Key drivers: [TEC/COT/etc.]
   Risk per contract: $[N]
   Watch: [what triggers the entry]

2. [SYMBOL] ...
3. [SYMBOL] ...

────────────────────────────
TOP 3 SHORT SETUPS (FUTURES)
────────────────────────────
1. [SYMBOL] [N] ([Bias])
   ...

────────────────────────────
TOP STOCK SETUPS TODAY
────────────────────────────
📈 [SYMBOL] +[N] — [Setup summary]. Entry $[range]
📉 [SYMBOL] [N]  — [Setup summary]. Watch $[resistance]
⚠️ Earnings this week: [SYMBOL (day/time)]

────────────────────────────
REGISTERED POSITIONS STATUS
────────────────────────────
[Load data/alerts_log.json, show any open POSITION entries:]
  [SYMBOL] [dir] @ [entry] | Stop: [price] | TP1: [price] | Current: [price]
  Status: [above/below entry, distance to TP1 and stop]
[If no open positions: "No registered positions"]

────────────────────────────
TODAY'S EVENTS
────────────────────────────
[HH:MM ET] [Event name] — [impact level]
[If no high-impact events: "No high-impact events today ✓"]

════════════════════════════
ALL FUTURES RANKED
  MES +6  MGC +8  MNQ +7  ...  MCL -3
════════════════════════════
```

4. Save the formatted brief as `data/sessions/YYYY-MM-DD_morning.txt`

5. Confirm: "Brief saved to data/sessions/YYYY-MM-DD_morning.txt"

## Rules
- Minimum score threshold for top setups: ±5 futures, ±7 stocks
- Always include dollar risk per contract for each setup (uses correct point_value)
- Always check registered positions and report their current status
- R:R must be at least 1.5:1 to be included as a top setup
