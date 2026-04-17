"""
daily_stock_scan.py — Runs at 9:00 AM ET daily (called from morning_setup.py)

Scans the full STOCK_SCAN_UNIVERSE for high-conviction setups (score >= +7 or <= -7).
Saves results to data/sessions/active_stocks_YYYY-MM-DD.json.
Sends scan summary to Telegram (included in morning brief).

Usage:
    python scripts/daily_stock_scan.py
    python scripts/daily_stock_scan.py --no-telegram
"""

import argparse
import json
import sys
from datetime import datetime, timezone, date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from alerts.stock_alerts import (
    STOCK_SCAN_UNIVERSE,
    STOCK_WATCHLIST_CORE,
    calculate_stock_score,
    _calc_stock_indicators,
    STOCK_ALERT_CONFIG,
)
from alerts.notifier import fire_alert

BASE_DIR     = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

SCAN_THRESHOLD = 7


# ---------------------------------------------------------------------------
# Score a single symbol
# ---------------------------------------------------------------------------

def _score_symbol(symbol: str) -> dict:
    try:
        ticker  = yf.Ticker(symbol)
        hist_d  = ticker.history(period="60d", interval="1d")
        if hist_d is None or hist_d.empty:
            return None

        indicators = _calc_stock_indicators(hist_d)
        if not indicators:
            return None

        score = calculate_stock_score(symbol, indicators)
        price = indicators.get('price', 0)

        # Quick reason summary
        reasons = []
        if score['TEC'] > 0:
            ema50  = indicators.get('ema_50', 0)
            ema200 = indicators.get('ema_200', 0)
            if ema50 and ema200:
                reasons.append(f"Above 50 SMA ({ema50:,.2f}) + 200 SMA ({ema200:,.2f})")
        elif score['TEC'] < 0:
            reasons.append("Below key moving averages")
        if score['MOM'] > 0:
            reasons.append("Positive momentum (MACD + RSI)")
        elif score['MOM'] < 0:
            reasons.append("Negative momentum")
        if score['ECO'] > 0:
            reasons.append("Economy beating forecasts")
        elif score['ECO'] < 0:
            reasons.append("Economy missing forecasts")

        return {
            'symbol': symbol,
            'price':  round(price, 2),
            'score':  score['total'],
            'bias':   score['bias'],
            'reason': " + ".join(reasons[:2]) if reasons else "Score-based",
            'breakdown': {k: v for k, v in score.items() if k in ('TEC','COT','RET','SEA','ECO','PC','MOM')},
        }
    except Exception as e:
        print(f"  [{symbol}] Scan error: {e}")
        return None


# ---------------------------------------------------------------------------
# Format scan summary message
# ---------------------------------------------------------------------------

def _format_scan_message(active: list, scanned: int, today_str: str) -> str:
    try:
        import zoneinfo
        et  = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        day = now.strftime("%a %b %-d")
    except Exception:
        day = today_str

    lines = [f"🔍 <b>Daily Stock Scan — {day}</b>",
             f"Scanned {scanned} symbols | Threshold: ±{SCAN_THRESHOLD}",
             ""]

    if not active:
        lines.append("No symbols crossed the +7/-7 threshold today.")
        return "\n".join(lines)

    bullish = sorted([s for s in active if s['score'] > 0],  key=lambda x: -x['score'])
    bearish = sorted([s for s in active if s['score'] <= 0], key=lambda x: x['score'])

    if bullish:
        lines.append("📈 <b>Bullish finds:</b>")
        for s in bullish:
            score_str = f"+{s['score']}"
            lines.append(f"  • {s['symbol']:<6} {score_str:<5} ({s['bias']}) — {s['reason']}")

    if bearish:
        if bullish: lines.append("")
        lines.append("📉 <b>Bearish finds:</b>")
        for s in bearish:
            lines.append(f"  • {s['symbol']:<6} {s['score']:<5} ({s['bias']}) — {s['reason']}")

    lines.append("")
    lines.append(f"These symbols are now added to today's active watchlist.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(send_telegram: bool = True) -> list:
    today_str  = date.today().isoformat()
    cache_file = SESSIONS_DIR / f"active_stocks_{today_str}.json"

    # Full universe to scan (universe + core, deduplicated)
    all_symbols = list(dict.fromkeys(STOCK_SCAN_UNIVERSE + STOCK_WATCHLIST_CORE))

    print(f"[daily_stock_scan] Scanning {len(all_symbols)} symbols...")

    results = []
    for sym in all_symbols:
        print(f"  [{sym}] ...", end=" ", flush=True)
        r = _score_symbol(sym)
        if r:
            results.append(r)
            score_str = f"+{r['score']}" if r['score'] > 0 else str(r['score'])
            print(f"score {score_str}")
        else:
            print("skip")

    # Filter to threshold
    active = [r for r in results if abs(r['score']) >= SCAN_THRESHOLD]

    # Sort: most bullish first
    active_sorted = sorted(active, key=lambda x: -x['score'])

    # Save
    output = {
        'date':       today_str,
        'scan_time':  datetime.now(timezone.utc).isoformat(),
        'threshold':  SCAN_THRESHOLD,
        'scanned':    len(results),
        'active':     active_sorted,
        'all_ranked': sorted(results, key=lambda x: -x['score']),
    }
    with open(cache_file, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nScan saved to {cache_file}")
    print(f"Active finds ({len(active_sorted)}): {[s['symbol'] for s in active_sorted]}")

    # Send Telegram
    if send_telegram:
        msg = _format_scan_message(active_sorted, len(results), today_str)
        if active_sorted:
            fire_alert(msg, alert_type='MORNING', symbol='SCAN')
        else:
            print("[daily_stock_scan] No scan finds — no Telegram message sent")

    return active_sorted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()
    run(send_telegram=not args.no_telegram)
