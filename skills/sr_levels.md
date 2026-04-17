# /sr-levels {SYMBOL} {TIMEFRAME}

Calculate and display all key S/R levels for a symbol on the given timeframe. Optionally draw them on the TradingView chart.

Examples:
- `/sr-levels MES 15m`
- `/sr-levels MGC 1h`
- `/sr-levels SPY D`

## Steps

1. Import and call `get_key_levels(SYMBOL)` from `alerts/alert_engine.py`:
   ```python
   import sys; sys.path.insert(0, '.')
   from alerts.alert_engine import get_key_levels, get_current_price
   levels = get_key_levels('SYMBOL')
   price  = get_current_price('SYMBOL')
   ```

2. Also load today's session levels from `data/sessions/levels_YYYY-MM-DD.json` if available.

3. If TradingView MCP is connected: read any drawn levels via `data_get_pine_lines` and `data_get_pine_labels`, merge with calculated levels, then draw calculated levels back onto the chart using `draw_shape`.

4. Format and print the full levels table:

```
S/R LEVELS — {SYMBOL} {TIMEFRAME}
Current price: {price}
════════════════════════════════════════════════════════

Level        Price      Type        Distance    Strength
────────────────────────────────────────────────────────
★ IBH       5,638      Resistance  ↑ 17 pts   Medium    ← HOT ZONE
★ PDC       5,621      Pivot       ↕  0 pts   Medium    ← HOT ZONE (at price)
★ PDH       5,648      Resistance  ↑ 27 pts   Strong
  ONH       5,631      Resistance  ↑ 10 pts   Weak
  IBL       5,605      Support     ↓ 16 pts   Medium
  PDL       5,578      Support     ↓ 43 pts   Strong
  50 EMA    5,550      Support     ↓ 71 pts   Strong
  WO        5,580      Pivot       ↓ 41 pts   Medium
  Fib 38.2% 5,560      Support     ↓ 61 pts   Medium
  200 EMA   5,420      Support     ↓ 201 pts  Strong

★ = Hot zone (within 0.5% of current price)

KEY OBSERVATIONS
• Price is between IBL (support) and IBH (resistance) — inside the IB range
• PDH at 5,648 is the nearest resistance — a break above opens to [next level]
• Strongest support cluster: PDL 5,578 + WO 5,580 (within 2 pts)
• 50 EMA rising, price above — technical support at 5,550
```

5. Note any S/R flip conditions:
   - If price is above PDH → relabel PDH as support, note: "PDH: S/R FLIP — now acting as support"
   - If price is below PDL → relabel PDL as resistance

6. If TradingView MCP is connected:
   - Draw calculated levels as horizontal lines on the current chart
   - Confirm: "Levels drawn on TradingView chart"
   - If not connected: "TradingView not connected — calculated levels shown above only"

## Hot zone definition
A level is marked ★ (hot zone) if current price is within **0.5%** of the level.
Hot zones are the most likely areas for price reaction — watch these closely.

## Rules
- Always show distance in points (using the correct unit for the instrument)
- Always note the S/R type based on whether price is above or below the level
- Flag any S/R flip conditions explicitly
