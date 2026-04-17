"""
refresh_dashboard.py — Runs every 5 minutes, 9:30 AM – 4:15 PM ET

- Fetches latest prices for all symbols via yfinance
- Loads COT and economic score caches
- Builds live support/resistance levels for dashboard symbols
- Injects fresh data into dashboard/index.html (replaces /* LIVE_DATA_INJECT */ block)
- Prints heartbeat line

Usage:
    python scripts/refresh_dashboard.py         # one-shot refresh
    python scripts/refresh_dashboard.py --loop  # run every 5 minutes
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()
from providers import tradingview_provider

BASE_DIR       = Path(__file__).parent.parent
DASHBOARD_FILE = BASE_DIR / "dashboard" / "index.html"

SYMBOLS = ['MES', 'MNQ', 'MYM', 'MCL', 'MGC', 'MSI', 'MNG', 'M2K',
           'ES', 'NQ', 'YM', 'CL', 'GC', 'SI', 'NG',
           'SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'MSFT',
           # Forex — major
           'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'NZDUSD', 'USDCAD',
           # Forex — minor
           'EURGBP', 'EURJPY', 'GBPJPY', 'EURCHF', 'AUDCAD', 'CADJPY',
           'AUDNZD', 'NZDJPY', 'GBPAUD', 'NZDCAD']

# Symbols that need a full score calculation (futures + stocks; forex scores default neutral)
SCORE_SYMBOLS = ['MES', 'MNQ', 'MYM', 'MCL', 'MGC', 'MSI', 'MNG', 'M2K',
                 'ES', 'NQ', 'YM', 'CL', 'GC', 'SI', 'NG',
                 'SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'MSFT']

YF_MAP = {
    'MES': 'MES=F', 'MNQ': 'MNQ=F', 'MYM': 'MYM=F', 'MCL': 'CL=F',
    'MGC': 'GC=F',  'MSI': 'SI=F',  'MNG': 'NG=F',  'M2K': 'RTY=F',
    # Full-size reference contracts
    'ES':  'ES=F',  'NQ':  'NQ=F',  'YM':  'YM=F',
    'CL':  'CL=F',  'GC':  'GC=F',  'SI':  'SI=F',  'NG':  'NG=F',
    'SPY': 'SPY',   'QQQ': 'QQQ',   'AAPL': 'AAPL', 'NVDA': 'NVDA',
    'TSLA': 'TSLA', 'MSFT': 'MSFT',
    # Forex
    'EURUSD': 'EURUSD=X', 'GBPUSD': 'GBPUSD=X', 'USDJPY': 'USDJPY=X',
    'USDCHF': 'USDCHF=X', 'AUDUSD': 'AUDUSD=X', 'NZDUSD': 'NZDUSD=X',
    'USDCAD': 'USDCAD=X', 'EURGBP': 'EURGBP=X', 'EURJPY': 'EURJPY=X',
    'GBPJPY': 'GBPJPY=X', 'EURCHF': 'EURCHF=X', 'AUDCAD': 'AUDCAD=X',
    'CADJPY': 'CADJPY=X', 'AUDNZD': 'AUDNZD=X', 'NZDJPY': 'NZDJPY=X',
    'GBPAUD': 'GBPAUD=X', 'NZDCAD': 'NZDCAD=X',
}

FUTURES_CONFIG = {
    'MES':  {'point_value': 5,    'group': 'indices'},
    'MNQ':  {'point_value': 2,    'group': 'indices'},
    'MYM':  {'point_value': 0.50, 'group': 'indices'},
    'MCL':  {'point_value': 100,  'group': 'commodities'},
    'MGC':  {'point_value': 10,   'group': 'metals'},
    'MSI':  {'point_value': 25,   'group': 'metals'},
    'MNG':  {'point_value': 250,  'group': 'commodities'},
    'M2K':  {'point_value': 5,    'group': 'indices'},
    'SPY':  {'point_value': 100,  'group': 'stocks'},
    'QQQ':  {'point_value': 100,  'group': 'stocks'},
    'AAPL': {'point_value': 100,  'group': 'stocks'},
    'NVDA': {'point_value': 100,  'group': 'stocks'},
    'TSLA': {'point_value': 100,  'group': 'stocks'},
    'MSFT': {'point_value': 100,  'group': 'stocks'},
}


# ---------------------------------------------------------------------------
# Market hours
# ---------------------------------------------------------------------------

def _is_refresh_hours() -> bool:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        if now.weekday() >= 5:
            return False
        return (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16) or \
               (now.hour == 16 and now.minute <= 15)
    except Exception:
        return True


def _et_time_str() -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(timezone.utc).astimezone(et).strftime("%H:%M")
    except Exception:
        return datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Fetch prices
# ---------------------------------------------------------------------------

def _fetch_prices() -> dict:
    prices = {}
    tv_prices = {}

    if tradingview_provider.is_connected():
        try:
            tv_prices = tradingview_provider.get_watchlist_quotes()
            prices.update({
                sym: payload for sym, payload in tv_prices.items()
                if sym in SYMBOLS and payload.get("price") is not None
            })
        except Exception as e:
            print(f"[refresh] TradingView watchlist price error: {e}")

        try:
            active_symbol = tradingview_provider.get_active_chart_symbol()
            if active_symbol in SYMBOLS:
                quote = tradingview_provider.get_quote()
                last = quote.get("last") or quote.get("close") or quote.get("header_price")
                if last is not None:
                    existing = prices.get(active_symbol, {})
                    price = round(float(last), 4)
                    prev = existing.get("prev")
                    change_pct = existing.get("change_pct")
                    prices[active_symbol] = {
                        "price": price,
                        "prev": prev,
                        "change_pct": change_pct,
                        "source": "tradingview_active_chart",
                    }
        except Exception as e:
            print(f"[refresh] TradingView active chart quote error: {e}")

    missing_symbols = [sym for sym in SYMBOLS if not prices.get(sym, {}).get("price")]
    if not missing_symbols:
        return prices

    tickers_str = " ".join(YF_MAP[s] for s in missing_symbols)
    try:
        data = yf.download(tickers_str, period="2d", interval="1d",
                           group_by="ticker", auto_adjust=True, progress=False)
    except Exception as e:
        print(f"[refresh] yfinance batch error: {e}")
        return prices

    for sym in missing_symbols:
        yf_sym = YF_MAP[sym]
        try:
            if len(missing_symbols) > 1:
                closes = data[yf_sym]['Close'] if yf_sym in data.columns.get_level_values(0) else None
            else:
                closes = data['Close']
            if closes is None or len(closes) < 2:
                continue
            closes_clean = closes.dropna()
            if len(closes_clean) < 2:
                continue
            prev  = float(closes_clean.iloc[-2])
            price = float(closes_clean.iloc[-1])
            change_pct = ((price - prev) / prev * 100) if prev else 0
            prices[sym] = {'price': round(price, 4), 'prev': round(prev, 4),
                           'change_pct': round(change_pct, 2), 'source': 'yfinance'}
        except Exception:
            # Fallback: individual fetch
            try:
                ticker = yf.Ticker(yf_sym)
                hist   = ticker.history(period="2d", interval="1d")
                if len(hist) >= 2:
                    prev  = float(hist['Close'].iloc[-2])
                    price = float(hist['Close'].iloc[-1])
                    chg   = (price - prev) / prev * 100 if prev else 0
                    prices[sym] = {'price': round(price,4), 'prev': round(prev,4),
                                   'change_pct': round(chg,2), 'source': 'yfinance'}
            except Exception:
                pass

    return prices


# ---------------------------------------------------------------------------
# Load scores from caches
# ---------------------------------------------------------------------------

def _load_scores() -> dict:
    from alerts.alert_engine import calculate_from_tradingview, calculate_from_yfinance, calculate_score, FOREX_CONFIG
    neutral = {'total': 0, 'bias': 'Neutral', 'TEC':0,'COT':0,'RET':0,'SEA':0,'ECO':0,'PC':0,'MOM':0}
    scores = {}
    active_symbol = tradingview_provider.get_active_chart_symbol() if tradingview_provider.is_connected() else ""
    for sym in SCORE_SYMBOLS:
        try:
            if sym == active_symbol and tradingview_provider.is_connected():
                indicators = calculate_from_tradingview(sym)
            else:
                indicators = calculate_from_yfinance(sym)
            score = calculate_score(sym, indicators)
            scores[sym] = {k: v for k, v in score.items()}
        except Exception:
            scores[sym] = dict(neutral)
    # Forex pairs: COT score from cot_latest if available, rest neutral
    cot_file = BASE_DIR / "data" / "cot" / "cot_latest.json"
    cot_data = {}
    if cot_file.exists():
        try:
            with open(cot_file) as f:
                cot_data = json.load(f).get('data', {})
        except Exception:
            pass
    for sym in FOREX_CONFIG:
        entry = dict(neutral)
        if sym in cot_data:
            entry['COT'] = cot_data[sym].get('score', 0)
            entry['total'] = entry['COT']
        if sym == active_symbol and tradingview_provider.is_connected():
            try:
                indicators = calculate_from_tradingview(sym)
                score = calculate_score(sym, indicators)
                entry.update({k: v for k, v in score.items()})
            except Exception:
                pass
        scores[sym] = entry
    return scores


def _load_cot() -> dict:
    cot_file = BASE_DIR / "data" / "cot" / "cot_latest.json"
    if cot_file.exists():
        try:
            with open(cot_file) as f:
                cached = json.load(f)
            return cached.get('data', {})
        except Exception:
            pass
    return {}


def _load_eco() -> dict:
    eco_file = BASE_DIR / "data" / "economic" / "eco_latest.json"
    if eco_file.exists():
        try:
            with open(eco_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {'score': 0, 'indicators': []}


def _load_cached_levels() -> dict:
    from datetime import date
    today_file = BASE_DIR / "data" / "sessions" / f"levels_{date.today().isoformat()}.json"
    if today_file.exists():
        try:
            with open(today_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _refresh_level_distances(levels: list, current_price: float) -> list:
    refreshed = []
    for level in levels or []:
        item = dict(level)
        if current_price:
            try:
                item["distance_pts"] = round(abs(current_price - float(item["price"])), 4)
            except Exception:
                pass
        refreshed.append(item)
    return refreshed


def _load_levels(prices: dict) -> dict:
    from alerts.alert_engine import calculate_session_levels

    cached_levels = _load_cached_levels()
    live_levels = {}

    for sym in SYMBOLS:
        levels = cached_levels.get(sym, [])
        if not levels:
            try:
                levels = calculate_session_levels(sym)
            except Exception as e:
                print(f"[refresh] Level calc error for {sym}: {e}")
                levels = cached_levels.get(sym, [])

        if not levels:
            levels = cached_levels.get(sym, [])

        current_price = (prices.get(sym) or {}).get("price", 0)
        live_levels[sym] = _refresh_level_distances(levels, current_price)

    return live_levels


# ---------------------------------------------------------------------------
# Build LIVE_DATA JSON
# ---------------------------------------------------------------------------

def build_live_data(prices: dict, scores: dict, cot: dict, eco: dict, levels: dict) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now_str = datetime.now(timezone.utc).astimezone(et).strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    data = {
        'updated':   now_str,
        'prices':    prices,
        'scores':    scores,
        'cot':       cot,
        'eco':       eco,
        'levels':    levels,
        'sentiment': {
            'put_call':       0.92,   # placeholder — no live source
            'aaii_bull':      38,
            'aaii_bear':      34,
            'btc_gold_ratio': 28.4,
        },
        'iv': {},  # populated by stock_alerts when available
    }

    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Inject into HTML
# ---------------------------------------------------------------------------

def inject_into_html(live_data_json: str) -> bool:
    if not DASHBOARD_FILE.exists():
        print(f"[refresh] Dashboard not found: {DASHBOARD_FILE}")
        return False

    with open(DASHBOARD_FILE, 'r') as f:
        content = f.read()

    # Find the inject block: between /* LIVE_DATA_INJECT */ and /* END_LIVE_DATA */
    start_marker = '/* LIVE_DATA_INJECT */'
    end_marker   = '/* END_LIVE_DATA */'

    start_idx = content.find(start_marker)
    end_idx   = content.find(end_marker)

    if start_idx == -1 or end_idx == -1:
        print("[refresh] Could not find inject markers in index.html")
        return False

    new_block = (
        f"{start_marker}\n"
        f"const LIVE_DATA = {live_data_json};\n"
    )

    new_content = content[:start_idx] + new_block + content[end_idx:]

    with open(DASHBOARD_FILE, 'w') as f:
        f.write(new_content)

    return True


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def print_heartbeat(prices: dict, scores: dict) -> None:
    ts = _et_time_str()
    parts = []
    for sym in ['MES', 'MNQ', 'MCL', 'MGC']:
        p = prices.get(sym, {})
        s = scores.get(sym, {})
        if p.get('price'):
            chg_pct = p.get('change_pct', 0)
            sign = '+' if chg_pct >= 0 else ''
            parts.append(f"{sym}={p['price']:,.1f}({sign}{chg_pct:.1f}%)")
    print(f"[{ts} ET] Dashboard refreshed — {' | '.join(parts)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_once() -> None:
    print(f"[refresh] Fetching prices...")
    prices = _fetch_prices()

    print(f"[refresh] Loading scores...")
    try:
        scores = _load_scores()
    except Exception as e:
        print(f"[refresh] Score load error: {e}")
        scores = {}

    cot    = _load_cot()
    eco    = _load_eco()
    levels = _load_levels(prices)

    live_data_json = build_live_data(prices, scores, cot, eco, levels)
    success = inject_into_html(live_data_json)

    if success:
        print_heartbeat(prices, scores)
    else:
        print("[refresh] HTML inject failed — check markers in index.html")


def run_loop() -> None:
    print("[refresh] Starting dashboard refresh loop (every 5 min, market hours)")
    while True:
        if _is_refresh_hours():
            try:
                run_once()
            except Exception as e:
                print(f"[refresh] Error: {e}")
        else:
            ts = _et_time_str()
            print(f"[{ts} ET] Outside refresh hours — sleeping")
        time.sleep(300)  # 5 minutes


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--loop', action='store_true', help='Run every 5 minutes')
    args = parser.parse_args()

    if args.loop:
        run_loop()
    else:
        run_once()
