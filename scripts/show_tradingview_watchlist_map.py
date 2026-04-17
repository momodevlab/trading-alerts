"""
show_tradingview_watchlist_map.py — print the explicit dashboard<->TradingView mapping.

Usage:
    python scripts/show_tradingview_watchlist_map.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from providers import tradingview_provider


def main() -> int:
    expected = tradingview_provider.get_dashboard_watchlist_map()
    live = tradingview_provider.get_watchlist_quotes() if tradingview_provider.is_connected() else {}

    missing = [symbol for symbol in expected if symbol not in live]
    payload = {
        "connected": tradingview_provider.is_connected(),
        "expected_count": len(expected),
        "live_count": len(live),
        "expected_map": expected,
        "missing_from_live_watchlist": missing,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
