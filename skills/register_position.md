# /register-position {SYMBOL} {DIR} {ENTRY} stop={S} tp1={T1} [tp2={T2}]

Register an open position for exit monitoring. The system will watch for TP1, TP2, stop loss, score flip, and major news events.

Example:
`/register-position MES long 5615 stop=5578 tp1=5670 tp2=5720`
`/register-position MGC short 3218 stop=3241 tp1=3180`

## Steps

1. Parse the command arguments:
   - SYMBOL: e.g. MES, MGC, SPY
   - DIR: long or short
   - ENTRY: entry price (number)
   - stop=S: stop loss price
   - tp1=T1: first take profit price
   - tp2=T2: second take profit price (optional)

2. Run the registration:
   ```python
   import sys; sys.path.insert(0, '.')
   from alerts.futures_alerts import register_position
   register_position('SYMBOL', 'DIR', ENTRY, stop=S, tp1=T1, tp2=T2)
   ```

3. Confirm the registration with full details:

```
✅ POSITION REGISTERED

Symbol:    MES
Direction: Long
Entry:     5,615.00

Stop loss:  5,578.00  (37 pts below entry)
TP1:        5,670.00  (55 pts above entry) — R:R 1.5:1
TP2:        5,720.00  (105 pts above entry) — R:R 2.8:1

Dollar risk per contract:
  37 pts × $5/pt = $185 per contract

MONITORING — Alerts will fire when:
  📈 TP1 hit: price reaches 5,670.00
  📈 TP2 hit: price reaches 5,720.00
  🚨 Stop hit: price drops to 5,578.00
  ⚠️  Score flip: if MES score goes from Bullish to Bearish
  ⚠️  Major event: high-impact economic release
```

4. Verify the entry is saved to `data/alerts_log.json` with type "POSITION".

5. Check the current price and tell me how far I am from each level:
```
  Current price: 5,621.00
  → Distance to TP1: 49 pts (0.87%)
  → Distance to stop: 43 pts (0.76%)
  → Currently in profit: +6 pts ($30)
```

## Rules
- Dollar risk uses the correct point_value from FUTURES_CONFIG
- If R:R to TP1 is below 1.5:1, note it as a warning but still register
- Never suggest changing the position or adding to it — just register what was provided
- Confirm the save path explicitly
