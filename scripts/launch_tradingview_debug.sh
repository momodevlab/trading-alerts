#!/bin/bash
set -euo pipefail

PORT="${1:-9222}"
APP_BUNDLE=""

find_app_bundle() {
  local candidate=""

  for candidate in "/Applications/TradingView.app" "$HOME/Applications/TradingView.app"; do
    if [ -d "$candidate" ]; then
      APP_BUNDLE="$candidate"
      return 0
    fi
  done

  candidate=$(mdfind "kMDItemCFBundleIdentifier == 'com.tradingview.tradingviewapp.desktop'" 2>/dev/null | head -1)
  if [ -n "$candidate" ] && [ -d "$candidate" ]; then
    APP_BUNDLE="$candidate"
    return 0
  fi

  return 1
}

wait_for_cdp() {
  local tries="${1:-20}"
  local i=""

  echo "Waiting for CDP on http://localhost:$PORT ..."
  for i in $(seq 1 "$tries"); do
    if env -u ELECTRON_RUN_AS_NODE -u NODE_OPTIONS curl -fsS "http://localhost:$PORT/json/version" \
      > /tmp/tradingview_cdp_version.json 2>/dev/null; then
      echo "CDP ready at http://localhost:$PORT"
      python3 -m json.tool /tmp/tradingview_cdp_version.json 2>/dev/null || cat /tmp/tradingview_cdp_version.json
      rm -f /tmp/tradingview_cdp_version.json
      return 0
    fi
    sleep 1
  done

  rm -f /tmp/tradingview_cdp_version.json
  return 1
}

if ! find_app_bundle; then
  echo "Error: TradingView.app not found."
  echo "Checked: /Applications/TradingView.app, ~/Applications/TradingView.app"
  exit 1
fi

echo "Found TradingView bundle: $APP_BUNDLE"
echo "Launching with ELECTRON_RUN_AS_NODE and NODE_OPTIONS cleared..."

pkill -f "TradingView" 2>/dev/null || true
sleep 2

env -u ELECTRON_RUN_AS_NODE -u NODE_OPTIONS open -na "$APP_BUNDLE" --args "--remote-debugging-port=$PORT"

if wait_for_cdp 25; then
  exit 0
fi

echo ""
echo "Warning: TradingView launched but CDP is still not responding on port $PORT."
echo "Manual checks:"
echo "  1. Confirm TradingView stays open in the Dock"
echo "  2. Open a chart tab inside TradingView"
echo "  3. Run: env -u ELECTRON_RUN_AS_NODE -u NODE_OPTIONS curl http://localhost:$PORT/json/version"
exit 1
