#!/bin/bash
set -e

echo "=== TradingView MCP Setup ==="
echo ""

# Clone the repo
if [ -d "$HOME/tradingview-mcp-jackson" ]; then
  echo "Repo already exists at ~/tradingview-mcp-jackson — pulling latest..."
  cd "$HOME/tradingview-mcp-jackson"
  git pull
else
  echo "Cloning tradingview-mcp-jackson..."
  git clone https://github.com/LewisWJackson/tradingview-mcp-jackson.git \
    ~/tradingview-mcp-jackson
  cd ~/tradingview-mcp-jackson
fi

echo "Installing npm dependencies..."
npm install

# Write rules.json directly (pre-filled with this system's watchlist and rules)
cat > ~/tradingview-mcp-jackson/rules.json << 'RULES_EOF'
{
  "watchlist": [
    "MES1!", "MNQ1!", "MYM1!", "MCL1!", "MGC1!", "MSI1!", "MNG1!", "M2K1!",
    "ES1!", "NQ1!", "YM1!", "CL1!", "GC1!", "SI1!", "NG1!",
    "AMEX:SPY", "NASDAQ:QQQ", "NASDAQ:AAPL", "NASDAQ:NVDA", "NASDAQ:TSLA", "NASDAQ:MSFT",
    "OANDA:EURUSD", "OANDA:GBPUSD", "OANDA:USDJPY", "OANDA:USDCHF", "OANDA:AUDUSD", "OANDA:NZDUSD", "OANDA:USDCAD",
    "OANDA:EURGBP", "OANDA:EURJPY", "OANDA:GBPJPY", "OANDA:EURCHF", "OANDA:AUDCAD", "OANDA:CADJPY",
    "OANDA:AUDNZD", "OANDA:NZDJPY", "OANDA:GBPAUD", "OANDA:NZDCAD"
  ],
  "bias_criteria": {
    "bullish": "price above 50 EMA and 200 EMA, RSI above 50, MACD histogram positive, price above VWAP",
    "bearish": "price below 50 EMA and 200 EMA, RSI below 50, MACD histogram negative, price below VWAP"
  },
  "risk_rules": [
    "alerts only — I place all trades manually in my own platform",
    "minimum 1.5:1 R:R on every alerted setup",
    "no alerts within 15 minutes of market close (4:45 PM ET)",
    "no alerts within 30 minutes of high-impact economic events",
    "do not repeat the same alert within 4 hours",
    "if COT and technicals conflict: alert but flag the conflict explicitly",
    "never suggest placing an order — information and alerts only",
    "dollar risk per alert must use the correct point_value per instrument"
  ],
  "instruments": {
    "MES": {"full_name": "Micro E-mini S&P 500", "exchange": "CME",   "point_value": 5,   "tick": 0.25},
    "MNQ": {"full_name": "Micro E-mini Nasdaq 100", "exchange": "CME",   "point_value": 2,   "tick": 0.25},
    "MYM": {"full_name": "Micro E-mini Dow",        "exchange": "CBOT",  "point_value": 0.50,"tick": 1},
    "M2K": {"full_name": "Micro E-mini Russell 2000","exchange": "CME",   "point_value": 5,   "tick": 0.10},
    "MCL": {"full_name": "Micro WTI Crude Oil",     "exchange": "NYMEX", "point_value": 100, "tick": 0.01},
    "MGC": {"full_name": "Micro Gold",              "exchange": "COMEX", "point_value": 10,  "tick": 0.10},
    "MSI": {"full_name": "Micro Silver",            "exchange": "COMEX", "point_value": 25,  "tick": 0.005},
    "MNG": {"full_name": "Micro Natural Gas",       "exchange": "NYMEX", "point_value": 250, "tick": 0.001}
  },
  "scoring_system": {
    "signals": ["TEC", "COT", "RET", "SEA", "ECO", "PC", "MOM"],
    "range_per_signal": [-2, 2],
    "total_range": [-14, 14],
    "thresholds": {
      "very_bullish": 10,
      "bullish": 5,
      "bearish": -5,
      "very_bearish": -10
    },
    "alert_threshold_futures": 5,
    "alert_threshold_stocks": 7
  },
  "indicators_to_read": [
    "EMA 9",
    "EMA 21",
    "EMA 50",
    "EMA 200",
    "RSI 14",
    "MACD (12,26,9)",
    "Volume",
    "VWAP",
    "ATR 14"
  ],
  "timeframes": {
    "scalp":    "5",
    "intraday": "15",
    "swing":    "60",
    "daily":    "D"
  },
  "session_levels": ["PDH", "PDL", "PDC", "PDM", "DO", "IBH", "IBL", "WO", "WH", "WL", "MO", "ONH", "ONL"],
  "alert_rules": {
    "cooldown_futures_hours": 4,
    "cooldown_stocks_hours": 24,
    "min_rr_ratio": 1.5,
    "price_proximity_pct": 0.25,
    "pattern_proximity_pct": 0.30,
    "no_alert_before_close_min": 15,
    "no_alert_before_event_min": 30
  }
}
RULES_EOF

echo "rules.json written."

# Update ~/.claude/.mcp.json
MCP_CONFIG="$HOME/.claude/.mcp.json"
mkdir -p "$HOME/.claude"
[ ! -f "$MCP_CONFIG" ] && echo '{"mcpServers":{}}' > "$MCP_CONFIG"

python3 - << 'PYEOF'
import json, os
path = os.path.expanduser("~/.claude/.mcp.json")
with open(path) as f:
    cfg = json.load(f)
cfg.setdefault("mcpServers", {})["tradingview"] = {
    "command": "node",
    "args": [os.path.expanduser("~/tradingview-mcp-jackson/src/server.js")]
}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("MCP config updated:", path)
PYEOF

echo ""
echo "============================================"
echo " TradingView MCP setup complete!"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Verify ~/tradingview-mcp-jackson/rules.json looks correct"
echo "  2. Open TradingView Desktop (paid subscription required)"
echo "  3. Launch with debug port:"
echo "       bash scripts/launch_tradingview_debug.sh"
echo "  4. Restart Claude Code (so it picks up the new MCP server)"
echo "  5. Test the connection:"
echo "       tv_health_check"
echo ""
echo "The alert system will print:"
echo "  'TradingView MCP connected — using live data'  ✓"
echo "  'TradingView not connected — falling back to yfinance'  (if not running)"
echo ""
