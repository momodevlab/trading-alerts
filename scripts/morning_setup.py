"""
morning_setup.py — Runs at 8:30 AM ET daily

- Refreshes COT cache if stale
- Fetches latest economic data
- Calculates scores for all watchlist symbols
- Identifies top 3 bullish and top 3 bearish setups
- Runs daily stock scan (adds high-conviction stocks to today's watchlist)
- Checks FMP economic calendar for today's events
- Saves morning brief to data/sessions/YYYY-MM-DD_morning.txt
- Sends brief to Telegram

Usage:
    python scripts/morning_setup.py
    python scripts/morning_setup.py --no-telegram
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from alerts.alert_engine import FUTURES_CONFIG, get_live_indicators, calculate_score, get_current_price
from alerts.notifier import fire_alert
from providers.fmp_provider import fetch_todays_economic_events

BASE_DIR     = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Today's economic events
# ---------------------------------------------------------------------------

def _get_todays_events() -> list:
    """Fetch today's economic calendar from the configured provider."""
    try:
        events = fetch_todays_economic_events()
        # Filter high-impact
        high = [e for e in events if str(e.get('impact', '')).lower() in ('high', '3')]
        return high
    except Exception as e:
        print(f"[morning_setup] Calendar fetch error: {e}")
        return []


# ---------------------------------------------------------------------------
# Score all futures
# ---------------------------------------------------------------------------

def _score_all_futures() -> list:
    results = []
    for symbol in FUTURES_CONFIG.keys():
        try:
            indicators = get_live_indicators(symbol)
            price      = get_current_price(symbol)
            if not indicators.get('price'):
                indicators['price'] = price
            score = calculate_score(symbol, indicators)
            results.append({
                'symbol':     symbol,
                'price':      price,
                'score':      score['total'],
                'bias':       score['bias'],
                'breakdown':  {k: v for k, v in score.items() if k in ('TEC','COT','RET','SEA','ECO','PC','MOM')},
            })
        except Exception as e:
            print(f"  [{symbol}] Score error: {e}")
    return sorted(results, key=lambda x: x['score'], reverse=True)


# ---------------------------------------------------------------------------
# Score stocks (core watchlist)
# ---------------------------------------------------------------------------

def _score_stocks() -> list:
    try:
        from alerts.stock_alerts import STOCK_WATCHLIST_CORE, calculate_stock_score
        results = []
        for sym in STOCK_WATCHLIST_CORE:
            try:
                import yfinance as yf
                ticker = yf.Ticker(sym)
                hist   = ticker.history(period="60d", interval="1d")
                if hist.empty:
                    continue

                # Basic indicators from daily bars
                close = hist['Close']
                indicators = {
                    'price':   float(close.iloc[-1]),
                    'ema_50':  float(close.ewm(span=50,  adjust=False).mean().iloc[-1]),
                    'ema_200': float(close.ewm(span=200, adjust=False).mean().iloc[-1]),
                    'rsi':     50.0,  # simplified
                    'macd_hist': 0.0,
                    'vwap':    float(close.iloc[-1]),
                    'volume':  float(hist['Volume'].iloc[-1]),
                    'vol_ma20': float(hist['Volume'].tail(20).mean()),
                    'source':  'yfinance_daily',
                }
                score = calculate_stock_score(sym, indicators)
                results.append({
                    'symbol':    sym,
                    'price':     indicators['price'],
                    'score':     score['total'],
                    'bias':      score['bias'],
                    'breakdown': {k: v for k, v in score.items() if k in ('TEC','COT','RET','SEA','ECO','PC','MOM')},
                })
            except Exception as e:
                print(f"  [{sym}] Stock score error: {e}")
        return sorted(results, key=lambda x: x['score'], reverse=True)
    except ImportError:
        return []


# ---------------------------------------------------------------------------
# Format morning brief
# ---------------------------------------------------------------------------

def _format_brief(futures_scores: list, stock_scores: list, events: list, today_str: str) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        day_name = now.strftime("%A %B %-d, %Y  %H:%M ET")
    except Exception:
        day_name = today_str

    lines = [
        f"📊 <b>Morning Brief — {day_name}</b>",
        "",
    ]

    # Top 3 bullish futures
    bullish = [f for f in futures_scores if f['score'] >= 5][:3]
    bearish = [f for f in reversed(futures_scores) if f['score'] <= -5][:3]

    if bullish:
        lines.append("📈 <b>Top bullish futures setups:</b>")
        for f in bullish:
            bd = f['breakdown']
            bd_str = " ".join(f"{k}:{'+' if v>0 else ''}{v}" for k, v in bd.items() if v != 0)
            score_str = f"+{f['score']}" if f['score'] > 0 else str(f['score'])
            lines.append(f"  • {f['symbol']} {score_str} ({f['bias']})  {bd_str}")
    else:
        lines.append("📈 No strong bullish futures setups today")

    lines.append("")

    if bearish:
        lines.append("📉 <b>Top bearish futures setups:</b>")
        for f in bearish:
            bd = f['breakdown']
            bd_str = " ".join(f"{k}:{'+' if v>0 else ''}{v}" for k, v in bd.items() if v != 0)
            score_str = str(f['score'])
            lines.append(f"  • {f['symbol']} {score_str} ({f['bias']})  {bd_str}")
    else:
        lines.append("📉 No strong bearish futures setups today")

    lines.append("")

    # Full ranking
    lines.append("<b>All futures ranked:</b>")
    for f in futures_scores:
        score_str = f"+{f['score']}" if f['score'] > 0 else str(f['score'])
        lines.append(f"  {f['symbol']:<6} {score_str:>4}  {f['bias']}")

    # Stock setups
    if stock_scores:
        lines.append("")
        lines.append("📊 <b>Top stock setups today:</b>")
        top_bull_stocks = [s for s in stock_scores if s['score'] >= 7][:2]
        top_bear_stocks = [s for s in reversed(stock_scores) if s['score'] <= -7][:2]

        for s in top_bull_stocks:
            score_str = f"+{s['score']}"
            lines.append(f"📈 {s['symbol']} {score_str} — {s['bias']}")
        for s in top_bear_stocks:
            lines.append(f"📉 {s['symbol']} {s['score']} — {s['bias']}")

        if not top_bull_stocks and not top_bear_stocks:
            lines.append("  No stock setups at threshold today")

    # Today's economic events
    if events:
        lines.append("")
        lines.append("⚠️ <b>High-impact events today:</b>")
        for ev in events[:5]:
            name    = ev.get('event', ev.get('name', 'Event'))
            ev_time = ev.get('date', '')[-8:][:5] if len(ev.get('date','')) > 10 else ev.get('time','')
            lines.append(f"  • {name} at {ev_time} ET")
    else:
        lines.append("")
        lines.append("✅ No high-impact economic events scheduled today")

    lines.append("")
    lines.append("<i>System: research and alerts only — trade decisions are yours.</i>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(send_telegram: bool = True) -> None:
    today_str  = date.today().isoformat()
    brief_file = SESSIONS_DIR / f"{today_str}_morning.txt"

    print(f"[morning_setup] Starting morning setup for {today_str}...")

    # 1. Refresh COT if stale
    try:
        from agents.cot_agent import fetch_cot
        print("  Checking COT cache...")
        fetch_cot()
    except Exception as e:
        print(f"  COT error: {e}")

    # 2. Refresh economic data
    try:
        from agents.economic_agent import fetch_economic
        print("  Fetching economic data...")
        fetch_economic()
    except Exception as e:
        print(f"  Economic data error: {e}")

    # 3. Score futures
    print("  Scoring futures watchlist...")
    futures_scores = _score_all_futures()

    # 4. Score stocks
    print("  Scoring stocks watchlist...")
    stock_scores = _score_stocks()

    # 5. Today's events
    print("  Fetching today's calendar...")
    events = _get_todays_events()

    # 6. Run daily stock scan
    try:
        from scripts.daily_stock_scan import run as run_scan
        print("  Running daily stock scan...")
        run_scan(send_telegram=False)  # included in morning brief
    except Exception as e:
        print(f"  Stock scan error: {e}")

    # 7. Format and save
    brief_text = _format_brief(futures_scores, stock_scores, events, today_str)

    with open(brief_file, 'w') as f:
        f.write(brief_text.replace('<b>', '').replace('</b>', '').replace('<i>', '').replace('</i>', ''))
    print(f"\nBrief saved to {brief_file}")

    # 8. Send to Telegram
    if send_telegram:
        fire_alert(brief_text, alert_type='MORNING', symbol='ALL')
        print("Morning brief sent.")
    else:
        print("Morning brief prepared; Telegram skipped (--no-telegram).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-telegram', action='store_true')
    args = parser.parse_args()
    run(send_telegram=not args.no_telegram)
