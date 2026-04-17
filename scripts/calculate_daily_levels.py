"""
calculate_daily_levels.py — Runs at 9:15 AM ET before the open

- Calculates PDH, PDL, PDC, IBH, IBL, ONH, ONL, DO, WO, WH, WL, MO for all futures
- Saves to data/sessions/levels_YYYY-MM-DD.json
- If TradingView MCP connected: draws PDH/PDL/PDC on each chart
- Sends Telegram morning levels summary

Usage:
    python scripts/calculate_daily_levels.py
    python scripts/calculate_daily_levels.py --no-telegram
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from providers import tradingview_provider
from alerts.alert_engine import FUTURES_CONFIG, FOREX_CONFIG, calculate_session_levels, get_current_price, _yf_symbol
from alerts.notifier import fire_alert

BASE_DIR     = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# TradingView MCP — draw levels
# ---------------------------------------------------------------------------

def _draw_tv_levels(symbol: str, levels: list) -> None:
    """Attempt to draw PDH/PDL/PDC on TradingView chart via MCP."""
    for lvl in levels:
        if lvl['label'] not in ('PDH', 'PDL', 'PDC'):
            continue
        color = {
            'PDH': '#e74c3c',
            'PDL': '#27ae60',
            'PDC': '#3498db',
        }[lvl['label']]
        tradingview_provider.draw_horizontal_line(
            symbol=symbol,
            price=lvl['price'],
            text=f"{lvl['label']} {lvl['price']:,.2f}",
            color=color,
            timeframe="D",
        )


# ---------------------------------------------------------------------------
# Format morning levels message
# ---------------------------------------------------------------------------

def _format_morning_levels(all_levels: dict, today_str: str) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        day_name = now.strftime("%a %b %-d")
    except Exception:
        day_name = today_str

    lines = [f"📊 <b>Morning levels — {day_name}</b>\n"]

    all_symbols = list(FUTURES_CONFIG.keys()) + list(FOREX_CONFIG.keys())
    for symbol in all_symbols:
        levels = all_levels.get(symbol, [])
        if not levels:
            continue

        by_label = {l['label']: l['price'] for l in levels}

        parts = []
        for lbl in ['PDH', 'PDC', 'PDL', 'ONH', 'ONL', 'WO']:
            if lbl in by_label:
                price = by_label[lbl]
                # Format based on instrument
                if symbol in FOREX_CONFIG:
                    dec = FOREX_CONFIG[symbol]['decimals']
                    fmt = f"{price:.{dec}f}"
                elif symbol in ('MGC', 'GC'):
                    fmt = f"{price:,.1f}"
                elif symbol in ('MCL', 'CL'):
                    fmt = f"{price:.1f}"
                elif symbol in ('MSI', 'SI'):
                    fmt = f"{price:.3f}"
                elif symbol in ('MNG', 'NG'):
                    fmt = f"{price:.3f}"
                else:
                    fmt = f"{price:,.0f}"
                parts.append(f"{lbl} {fmt}")

        if parts:
            lines.append(f"<b>{symbol}:</b>  {' | '.join(parts)}")

    lines.append("")

    # Gap analysis
    gap_lines = []
    for symbol in ('MES', 'MNQ', 'MCL', 'MGC'):
        levels = all_levels.get(symbol, [])
        by_label = {l['label']: l['price'] for l in levels}
        current = get_current_price(symbol)
        pdc = by_label.get('PDC', 0)
        if current and pdc:
            gap_pct = (current - pdc) / pdc * 100
            if abs(gap_pct) > 0.03:
                direction = "above" if gap_pct > 0 else "below"
                sign      = "+" if gap_pct > 0 else ""
                gap_lines.append(f"{symbol} {sign}{gap_pct:.2f}% {direction} PDC")

    if gap_lines:
        lines.append(f"Gap: {' | '.join(gap_lines)}")

    # Key events today
    try:
        from agents.economic_agent import get_cached_eco
        eco = get_cached_eco()
        today = date.today().isoformat()
        today_events = [
            ind for ind in eco.get('indicators', [])
            if ind.get('date', '') == today
        ]
        if today_events:
            lines.append("")
            event_names = ", ".join(e['label'] for e in today_events[:3])
            lines.append(f"⚠️ Key today: {event_names}")
    except Exception:
        pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(send_telegram: bool = True) -> None:
    today_str  = date.today().isoformat()
    cache_file = SESSIONS_DIR / f"levels_{today_str}.json"

    print(f"[calculate_daily_levels] Calculating levels for {today_str}...")

    all_levels = {}

    tv_connected = tradingview_provider.is_connected()

    all_symbols = list(FUTURES_CONFIG.keys()) + list(FOREX_CONFIG.keys())
    for symbol in all_symbols:
        print(f"  [{symbol}] calculating...")
        try:
            levels = calculate_session_levels(symbol)
            all_levels[symbol] = levels

            if tv_connected and symbol in FUTURES_CONFIG:
                _draw_tv_levels(symbol, levels)
                print(f"  [{symbol}] levels drawn on TradingView")
        except Exception as e:
            print(f"  [{symbol}] Error: {e}")
            all_levels[symbol] = []

    # Save consolidated
    with open(cache_file, 'w') as f:
        json.dump(all_levels, f, indent=2)
    print(f"\nLevels saved to {cache_file}")

    # Send Telegram morning levels
    if send_telegram:
        msg = _format_morning_levels(all_levels, today_str)
        fire_alert(msg, alert_type='LEVELS', symbol='ALL')
        print("Morning levels sent.")
    else:
        print("Morning levels prepared; Telegram skipped (--no-telegram).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()
    run(send_telegram=not args.no_telegram)
