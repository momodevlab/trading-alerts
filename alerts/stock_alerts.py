"""
stock_alerts.py — Stock and options alert monitor

Monitors core watchlist every 5 minutes during 9:30 AM – 4:00 PM ET.
At startup, loads today's daily scan finds and adds any scoring >= +7/-7.
Fires Telegram + terminal alerts for swing and day trade setups.

Usage:
    python alerts/stock_alerts.py              # start loop
    python alerts/stock_alerts.py --test SPY   # single check, no Telegram
    python alerts/stock_alerts.py --test ALL   # all symbols, no Telegram
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from providers.fmp_provider import fetch_earnings_calendar

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from alerts.notifier import fire_alert, get_recent_alerts
from agents.economic_agent import get_eco_score

BASE_DIR  = Path(__file__).parent.parent
LOG_FILE  = BASE_DIR / "data" / "alerts_log.json"
# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STOCK_WATCHLIST_CORE = ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'MSFT']

STOCK_SCAN_UNIVERSE = [
    'META', 'GOOGL', 'AMZN', 'AMD', 'INTC', 'AVGO',
    'JPM', 'BAC', 'GS',
    'XOM', 'CVX',
    'XLK', 'XLE', 'XLF', 'XLV', 'GLD', 'SLV',
    'PLTR', 'COIN', 'MSTR', 'SOFI', 'RIVN',
]

STOCK_CONFIG = {
    'SPY':  {'type': 'etf',   'alert_threshold': 7, 'swing': True, 'day': True},
    'QQQ':  {'type': 'etf',   'alert_threshold': 7, 'swing': True, 'day': True},
    'AAPL': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': False},
    'NVDA': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': False},
    'TSLA': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': True},
    'MSFT': {'type': 'stock', 'alert_threshold': 7, 'swing': True, 'day': False},
}

STOCK_ALERT_CONFIG = {
    'alert_threshold':    7,
    'volume_multiplier':  1.2,
    'earnings_buffer_days': 5,
    'alert_cooldown_hours': 24,
    'max_intraday_move_pct': 3.0,
    'preferred_dte_min':  7,
    'preferred_dte_max':  45,
}

DAY_TRADE_ELIGIBLE_MIN_VOLUME = 20_000_000


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_tec_stock(indicators: dict) -> int:
    """TEC: price vs 50-day and 200-day MA."""
    price   = indicators.get('price', 0)
    ema_50  = indicators.get('ema_50', 0)
    ema_200 = indicators.get('ema_200', 0)
    if not price or not ema_50 or not ema_200:
        return 0
    above_50  = price > ema_50
    above_200 = price > ema_200
    if above_50 and above_200:   return 2
    elif above_50 or above_200:  return 1
    elif not above_50 and not above_200: return -2
    else: return -1


def _score_short_interest(symbol: str) -> int:
    """
    RET: short interest ratio (contrarian).
    High short interest + rising price = contrarian bullish.
    Low short interest + falling price = contrarian bearish.
    Returns 0 if data not available (typical for most stocks).
    """
    # Short interest data requires a paid API.
    # Placeholder: return 0 (neutral) for all symbols.
    # Can be wired to a data source later.
    return 0


def _score_seasonality_stock(symbol: str) -> int:
    """SEA: only applicable for SPY and QQQ."""
    if symbol not in ('SPY', 'QQQ'):
        return 0
    month = datetime.now().month
    # S&P seasonal pattern
    seasonal = {1:1, 2:-1, 3:0, 4:1, 5:-1, 6:0,
                7:1, 8:-1, 9:-2, 10:0, 11:2, 12:1}
    return seasonal.get(month, 0)


def _score_momentum_stock(indicators: dict) -> int:
    """MOM: ROC + RSI + MACD direction."""
    rsi       = indicators.get('rsi', 50)
    macd_hist = indicators.get('macd_hist', 0)
    roc_20    = indicators.get('roc_20', 0)

    score = 0
    if rsi and rsi > 55:   score += 1
    elif rsi and rsi < 45: score -= 1

    if macd_hist and macd_hist > 0: score += 1
    elif macd_hist and macd_hist < 0: score -= 1

    if roc_20 and roc_20 > 0: score += 0
    elif roc_20 and roc_20 < 0: score -= 0

    return max(-2, min(2, score))


def _score_put_call_stock(symbol: str) -> int:
    """P/C: equity put/call ratio — placeholder until CBOE data wired."""
    return 0


def calculate_stock_score(symbol: str, indicators: dict) -> dict:
    """
    7-signal scoring for stocks. COT is always 0 (not applicable).
    Max score: +12/-12 (COT excluded). Alert threshold: +7/-7.
    """
    tec = _score_tec_stock(indicators)
    cot = 0
    ret = _score_short_interest(symbol)
    sea = _score_seasonality_stock(symbol)

    try:
        from agents.economic_agent import get_eco_score
        eco = get_eco_score()
    except Exception:
        eco = 0

    pc  = _score_put_call_stock(symbol)
    mom = _score_momentum_stock(indicators)

    total = tec + cot + ret + sea + eco + pc + mom

    if total >= 10:   bias = "Very Bullish"
    elif total >= 5:  bias = "Bullish"
    elif total <= -10: bias = "Very Bearish"
    elif total <= -5:  bias = "Bearish"
    else:             bias = "Neutral"

    return {'TEC':tec,'COT':cot,'RET':ret,'SEA':sea,
            'ECO':eco,'PC':pc,'MOM':mom,'total':total,'bias':bias}


# ---------------------------------------------------------------------------
# Indicator calculation from daily + intraday bars
# ---------------------------------------------------------------------------

def _calc_stock_indicators(hist_daily: pd.DataFrame, hist_15m: pd.DataFrame = None) -> dict:
    if hist_daily is None or hist_daily.empty:
        return {}

    close   = hist_daily['Close']
    vol     = hist_daily['Volume']

    def ema(n):
        return float(close.ewm(span=n, adjust=False).mean().iloc[-1]) if len(close) >= n else 0.0

    # RSI 14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = float(100 - (100 / (1 + rs)).iloc[-1]) if not rs.isna().all() else 50.0

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal    = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = float((macd_line - signal).iloc[-1])

    # 20-day ROC
    roc_20 = float(close.pct_change(20).iloc[-1] * 100) if len(close) >= 20 else 0.0

    # VWAP from intraday
    vwap = float(close.iloc[-1])
    if hist_15m is not None and not hist_15m.empty:
        typical = (hist_15m['High'] + hist_15m['Low'] + hist_15m['Close']) / 3
        tot_vol = hist_15m['Volume'].sum()
        if tot_vol > 0:
            vwap = float((typical * hist_15m['Volume']).sum() / tot_vol)

    vol_ma20 = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean())

    # Intraday high/low for day-trade check
    intraday_high = low = float(close.iloc[-1])
    if hist_15m is not None and not hist_15m.empty:
        intraday_high = float(hist_15m['High'].max())
        low           = float(hist_15m['Low'].min())

    return {
        'price':         float(close.iloc[-1]),
        'ema_50':        ema(50),
        'ema_200':       ema(200),
        'rsi':           rsi,
        'macd_hist':     macd_hist,
        'vwap':          vwap,
        'volume':        float(vol.iloc[-1]),
        'vol_ma20':      vol_ma20,
        'roc_20':        roc_20,
        'intraday_high': intraday_high,
        'intraday_low':  low,
        'source':        'yfinance_daily',
    }


# ---------------------------------------------------------------------------
# Earnings check
# ---------------------------------------------------------------------------

_earnings_cache: dict = {}
_earnings_cache_date: str = ""


def check_earnings_proximity(symbol: str) -> dict:
    """
    Returns {'days_until': int or None, 'warning_level': 'high'|'medium'|'none'}
    """
    global _earnings_cache, _earnings_cache_date
    today = date.today().isoformat()

    # Refresh weekly
    if _earnings_cache_date != today or symbol not in _earnings_cache:
        days_until = _fetch_earnings_days(symbol)
        _earnings_cache[symbol] = days_until
        _earnings_cache_date    = today

    days_until = _earnings_cache.get(symbol)
    if days_until is None:
        return {'days_until': None, 'warning_level': 'none'}
    elif days_until <= 7:
        return {'days_until': days_until, 'warning_level': 'high'}
    elif days_until <= 21:
        return {'days_until': days_until, 'warning_level': 'medium'}
    else:
        return {'days_until': days_until, 'warning_level': 'none'}


def _fetch_earnings_days(symbol: str) -> object:
    """Fetch next earnings date. Try provider first, then yfinance."""
    try:
        events = fetch_earnings_calendar(symbol, days_ahead=30)
        for ev in events:
            ev_date_raw = ev.get('date', '')
            if not ev_date_raw:
                continue
            ev_date = datetime.strptime(ev_date_raw[:10], '%Y-%m-%d').date()
            return (ev_date - date.today()).days
    except Exception:
        pass

    # yfinance fallback
    try:
        info = yf.Ticker(symbol).calendar
        if info is not None and hasattr(info, 'get'):
            ed = info.get('Earnings Date')
            if ed:
                if isinstance(ed, (list, pd.DatetimeIndex)):
                    ed = ed[0]
                if hasattr(ed, 'date'):
                    return (ed.date() - date.today()).days
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# IV rank
# ---------------------------------------------------------------------------

def get_iv_rank(symbol: str) -> dict:
    """
    Calculate IV rank from yfinance options chain.
    iv_rank = (current_iv - 52w_low) / (52w_high - 52w_low) * 100
    """
    try:
        ticker   = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return {'iv_rank': 50, 'environment': 'medium'}

        # Use first available expiry for current IV
        chain = ticker.option_chain(expiries[0])
        near_calls = chain.calls
        near_puts  = chain.puts
        all_iv     = pd.concat([near_calls['impliedVolatility'], near_puts['impliedVolatility']]).dropna()
        if all_iv.empty:
            return {'iv_rank': 50, 'environment': 'medium'}

        current_iv = float(all_iv.median())

        # Approximate 52-week range using multiple expiries
        all_ivs = []
        for exp in expiries[:6]:
            try:
                c = ticker.option_chain(exp)
                all_ivs.extend(c.calls['impliedVolatility'].dropna().tolist())
                all_ivs.extend(c.puts['impliedVolatility'].dropna().tolist())
            except Exception:
                pass

        if len(all_ivs) < 4:
            return {'iv_rank': 50, 'environment': 'medium'}

        iv_low  = min(all_ivs)
        iv_high = max(all_ivs)
        if iv_high <= iv_low:
            return {'iv_rank': 50, 'environment': 'medium'}

        iv_rank = (current_iv - iv_low) / (iv_high - iv_low) * 100
        iv_rank = round(iv_rank, 1)
        env     = 'high' if iv_rank >= 50 else 'low' if iv_rank < 30 else 'medium'

        return {'iv_rank': iv_rank, 'environment': env, 'current_iv': round(current_iv, 4)}
    except Exception:
        return {'iv_rank': 50, 'environment': 'medium'}


# ---------------------------------------------------------------------------
# Options suggestion
# ---------------------------------------------------------------------------

def get_options_suggestion(symbol: str, direction: str, stock_price: float,
                           stop_price: float, target_price: float,
                           prefer_dte_range: tuple = (7, 45)) -> dict:
    """
    Pull live options chain and return suggested contracts.
    """
    try:
        ticker   = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return {}

        today       = date.today()
        valid_exp   = [
            e for e in expiries
            if prefer_dte_range[0] <=
               (datetime.strptime(e, '%Y-%m-%d').date() - today).days
               <= prefer_dte_range[1]
        ]
        if not valid_exp:
            valid_exp = expiries[:3]
        if not valid_exp:
            return {}

        best_expiry = valid_exp[0]
        dte         = (datetime.strptime(best_expiry, '%Y-%m-%d').date() - today).days
        chain       = ticker.option_chain(best_expiry)

        def closest_row(df, target_strike):
            if df.empty:
                return None
            idx = (df['strike'] - target_strike).abs().argsort().iloc[0]
            return df.iloc[idx]

        result = {'expiry': best_expiry, 'dte': dte}

        if direction == 'bullish':
            # Buy call: slightly OTM
            calls = chain.calls
            target_strike = stock_price * 1.02
            call = closest_row(calls, target_strike)
            if call is not None:
                ask   = float(call.get('ask', 0))
                bid   = float(call.get('bid', 0))
                mid   = round((ask + bid) / 2, 2) if ask and bid else ask
                strike = float(call['strike'])
                result['buy_call'] = {
                    'strike':     strike,
                    'ask':        round(ask, 2),
                    'mid':        mid,
                    'iv':         round(float(call.get('impliedVolatility', 0)), 3),
                    'break_even': round(strike + ask, 2),
                    'max_risk':   round(ask * 100, 2),
                }

            # Sell CSP: strike at or below stop
            puts = chain.puts
            csp  = closest_row(puts, stop_price)
            if csp is not None:
                bid = float(csp.get('bid', 0))
                strike = float(csp['strike'])
                result['sell_csp'] = {
                    'strike':       strike,
                    'premium_bid':  round(bid, 2),
                    'cash_required': round(strike * 100, 2),
                    'break_even':   round(strike - bid, 2),
                    'max_profit':   round(bid * 100, 2),
                }

        else:  # bearish
            # Buy put: slightly OTM
            puts = chain.puts
            target_strike = stock_price * 0.98
            put = closest_row(puts, target_strike)
            if put is not None:
                ask   = float(put.get('ask', 0))
                bid   = float(put.get('bid', 0))
                mid   = round((ask + bid) / 2, 2) if ask and bid else ask
                strike = float(put['strike'])
                result['buy_put'] = {
                    'strike':     strike,
                    'ask':        round(ask, 2),
                    'mid':        mid,
                    'iv':         round(float(put.get('impliedVolatility', 0)), 3),
                    'break_even': round(strike - ask, 2),
                    'max_risk':   round(ask * 100, 2),
                }

            # Sell covered call: OTM
            calls = chain.calls
            cc_strike = stock_price * 1.03
            cc = closest_row(calls, cc_strike)
            if cc is not None:
                bid    = float(cc.get('bid', 0))
                strike = float(cc['strike'])
                result['sell_cc'] = {
                    'strike':    strike,
                    'premium_bid': round(bid, 2),
                    'max_profit':  round(bid * 100, 2),
                }

        return result
    except Exception as e:
        return {}


# ---------------------------------------------------------------------------
# Key levels for stocks (daily chart)
# ---------------------------------------------------------------------------

def get_stock_key_levels(symbol: str, hist: pd.DataFrame) -> list:
    """Calculate S/R levels from daily bars."""
    if hist is None or hist.empty:
        return []

    price = float(hist['Close'].iloc[-1])
    levels = []

    # Previous day levels
    if len(hist) >= 2:
        prev = hist.iloc[-2]
        pdh = float(prev['High'])
        pdl = float(prev['Low'])
        pdc = float(prev['Close'])
        levels += [
            {'price': pdh, 'label': 'PDH', 'type': 'resistance', 'strength': 'strong',
             'distance_pts': abs(price - pdh), 'role': 'prev day high'},
            {'price': pdl, 'label': 'PDL', 'type': 'support',    'strength': 'strong',
             'distance_pts': abs(price - pdl), 'role': 'prev day low'},
            {'price': pdc, 'label': 'PDC', 'type': 'pivot',      'strength': 'medium',
             'distance_pts': abs(price - pdc), 'role': 'prev day close'},
        ]

    # MA levels
    close = hist['Close']
    for n, label in [(50, '50 SMA'), (200, '200 SMA')]:
        if len(close) >= n:
            ma = float(close.tail(n).mean())
            levels.append({
                'price': round(ma, 2), 'label': label,
                'type': 'support' if price > ma else 'resistance', 'strength': 'strong',
                'distance_pts': round(abs(price - ma), 2), 'role': label,
            })

    # Fibonacci
    high_20 = float(hist['High'].tail(20).max())
    low_20  = float(hist['Low'].tail(20).min())
    diff    = high_20 - low_20
    for ratio, label in [(0.382, 'Fib 38.2%'), (0.500, 'Fib 50.0%'), (0.618, 'Fib 61.8%')]:
        fib = round(high_20 - diff * ratio, 2)
        if abs(fib - price) / price < 0.05:
            levels.append({
                'price': fib, 'label': label,
                'type': 'support' if fib < price else 'resistance', 'strength': 'medium',
                'distance_pts': round(abs(price - fib), 2), 'role': 'fibonacci',
            })

    return sorted(levels, key=lambda x: x['distance_pts'])


# ---------------------------------------------------------------------------
# Alert condition checks
# ---------------------------------------------------------------------------

def check_swing_conditions(symbol: str, score: dict, levels: list, indicators: dict) -> bool:
    """Returns True if swing alert should fire."""
    total = score['total']
    if abs(total) < STOCK_ALERT_CONFIG['alert_threshold']:
        return False

    direction = 'bullish' if total > 0 else 'bearish'
    price     = indicators.get('price', 0)
    rsi       = indicators.get('rsi', 50)
    ema_50    = indicators.get('ema_50', 0)
    volume    = indicators.get('volume', 0)
    vol_ma20  = indicators.get('vol_ma20', 1)

    if not price:
        return False

    # RSI not at extreme
    if direction == 'bullish' and rsi and rsi >= 70:
        return False
    if direction == 'bearish' and rsi and rsi <= 30:
        return False

    # Price on correct side of 50-day MA
    if direction == 'bullish' and ema_50 and price < ema_50:
        return False
    if direction == 'bearish' and ema_50 and price > ema_50:
        return False

    # Volume confirmation
    if vol_ma20 and volume and volume < vol_ma20 * STOCK_ALERT_CONFIG['volume_multiplier']:
        return False

    # Price near key level (within 0.5%)
    relevant = [
        l for l in levels
        if (direction == 'bullish' and l['type'] in ('support', 'pivot')) or
           (direction == 'bearish' and l['type'] in ('resistance', 'pivot'))
    ]
    near = any(l['price'] > 0 and abs(price - l['price']) / price <= 0.005 for l in relevant)
    if not near:
        return False

    return True


def is_day_trade_eligible_by_volume(hist: pd.DataFrame) -> bool:
    """Check if stock has enough avg volume for day trading (20M shares/day)."""
    if hist is None or hist.empty:
        return False
    avg_vol = float(hist['Volume'].tail(20).mean())
    return avg_vol >= DAY_TRADE_ELIGIBLE_MIN_VOLUME


def check_day_trade_conditions(symbol: str, score: dict, indicators: dict,
                                bars_15m: pd.DataFrame) -> bool:
    """Returns True if day trade alert should fire."""
    cfg = STOCK_CONFIG.get(symbol, {})
    if not cfg.get('day', False):
        return False

    total = score['total']
    if abs(total) < STOCK_ALERT_CONFIG['alert_threshold']:
        return False

    price   = indicators.get('price', 0)
    vwap    = indicators.get('vwap', 0)
    volume  = indicators.get('volume', 0)
    vol_avg = indicators.get('vol_ma20', 1)
    direction = 'bullish' if total > 0 else 'bearish'

    # VWAP alignment
    if direction == 'bullish' and vwap and price < vwap:
        return False
    if direction == 'bearish' and vwap and price > vwap:
        return False

    # Volume spike (1.5x for day trades)
    if vol_avg and volume and volume < vol_avg * 1.5:
        return False

    # Time window: 9:30–11:30 AM or 2:30–4:00 PM ET
    try:
        import zoneinfo
        et  = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        morning_window  = (now.hour == 9 and now.minute >= 30) or now.hour == 10 or (now.hour == 11 and now.minute <= 30)
        afternoon_window = (now.hour == 14 and now.minute >= 30) or (now.hour == 15)
        if not morning_window and not afternoon_window:
            return False
    except Exception:
        pass

    return True


def _intraday_move_pct(hist_15m: pd.DataFrame) -> float:
    """Return today's intraday move as a percentage."""
    if hist_15m is None or hist_15m.empty:
        return 0.0
    try:
        h = float(hist_15m['High'].max())
        l = float(hist_15m['Low'].min())
        return (h - l) / l * 100 if l > 0 else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Alert formatting
# ---------------------------------------------------------------------------

def format_stock_alert(
    alert_type: str,
    symbol: str,
    direction: str,
    price: float,
    score: dict,
    levels: list,
    indicators: dict,
    earnings_info: dict,
    iv_data: dict,
    options_data: dict,
    reasons: list,
    warnings: list,
) -> str:
    """Format the complete stock + options alert message."""

    is_bullish = direction == 'bullish'
    score_str  = f"+{score['total']}" if score['total'] > 0 else str(score['total'])
    sig_str    = " ".join(
        f"{k}{'+' if v>0 else ''}{v}"
        for k, v in score.items()
        if k in ('TEC','COT','RET','SEA','ECO','PC','MOM') and v != 0
    )

    # Determine alert type emoji
    if alert_type == 'SWING':
        emoji = '📈' if is_bullish else '📉'
        type_label = 'SWING SETUP'
    else:  # DAY
        emoji = '⚡'
        type_label = 'DAY TRADE' if is_bullish else 'DAY TRADE SHORT'

    # Nearest support / resistance
    supports    = sorted([l for l in levels if l['type'] == 'support'],    key=lambda x: x['distance_pts'])
    resistances = sorted([l for l in levels if l['type'] == 'resistance'], key=lambda x: x['distance_pts'])

    nearest_sup = supports[0]    if supports    else None
    nearest_res = resistances[0] if resistances else None

    # Stop and target estimates
    if is_bullish:
        stop_price = round(nearest_sup['price'] * 0.995, 2) if nearest_sup else round(price * 0.97, 2)
        tp1_price  = round(nearest_res['price'],          2) if nearest_res else round(price * 1.03, 2)
        tp2_price  = round(tp1_price + abs(tp1_price - price), 2)
    else:
        stop_price = round(nearest_res['price'] * 1.005, 2) if nearest_res else round(price * 1.03, 2)
        tp1_price  = round(nearest_sup['price'],          2) if nearest_sup else round(price * 0.97, 2)
        tp2_price  = round(tp1_price - abs(price - tp1_price), 2)

    risk    = abs(price - stop_price)
    reward1 = abs(tp1_price - price)
    reward2 = abs(tp2_price - price)
    rr1 = round(reward1 / risk, 1) if risk > 0 else 0
    rr2 = round(reward2 / risk, 1) if risk > 0 else 0

    # Change pct (approximate from ROC)
    chg_pct = indicators.get('roc_20', 0)

    lines = [
        f"{emoji} <b>{type_label} — {symbol}</b>",
        f"Direction: {'Long' if is_bullish else 'Short'} | Timeframe: {'Daily / Swing (2–5 days)' if alert_type == 'SWING' else 'Intraday'}",
        f"Price: ${price:,.2f} | Change: {'+' if chg_pct >= 0 else ''}{chg_pct:.1f}% | Score: {score_str} ({score['bias']})",
        "",
        f"Entry zone: ${price*0.999:,.2f}–${price*1.001:,.2f}",
        f"Stop: ${stop_price:,.2f} (${risk:.2f} risk per share)",
        f"TP1: ${tp1_price:,.2f} ({reward1:.1f}% | R:R {rr1:.1f}:1)",
        f"TP2: ${tp2_price:,.2f} ({reward2:.1f}% | R:R {rr2:.1f}:1)",
        "",
        f"Score breakdown: {sig_str}",
    ]

    # Technical setup details
    rsi    = indicators.get('rsi', 50)
    ema_50 = indicators.get('ema_50', 0)
    ema_200 = indicators.get('ema_200', 0)
    vol    = indicators.get('volume', 0)
    vol20  = indicators.get('vol_ma20', 1)
    vol_mult = vol / vol20 if vol20 and vol20 > 0 else 1

    lines += [
        "",
        "Technical setup:",
        f"• {'Above' if price > ema_50 else 'Below'} 50 SMA ({ema_50:,.2f}) | {'Above' if price > ema_200 else 'Below'} 200 SMA ({ema_200:,.2f})",
        f"• RSI 14: {rsi:.0f} — {'bullish, room to run' if 50 < rsi < 70 else 'overbought ⚠️' if rsi >= 70 else 'oversold (bounce potential)' if rsi <= 30 else 'below midline'}",
        f"• Volume: {vol_mult:.1f}x 20-day average",
    ]

    # Key levels
    if nearest_sup or nearest_res:
        lines.append("")
        lines.append("Key levels:")
        if nearest_res:
            lines.append(f"• Resistance: ${nearest_res['price']:,.2f} ({nearest_res['distance_pts']:.2f} pts) — {nearest_res['label']}")
        if nearest_sup:
            lines.append(f"• Support: ${nearest_sup['price']:,.2f} ({nearest_sup['distance_pts']:.2f} pts) — {nearest_sup['label']}")
        lines.append(f"• Stop zone: ${stop_price:,.2f}")

    # Earnings warning
    days = earnings_info.get('days_until')
    warn_level = earnings_info.get('warning_level', 'none')
    if warn_level == 'high' and days:
        lines.append("")
        lines.append(f"⚠️ Earnings in {days} days — binary risk, consider waiting for report")
    elif warn_level == 'medium' and days:
        lines.append("")
        lines.append(f"⚠️ Earnings in ~{days} days — IV elevated, consider closing before report")

    # IV + sector context
    iv_rank = iv_data.get('iv_rank', 50)
    iv_env  = iv_data.get('environment', 'medium')
    lines.append("")
    lines.append(f"⚠️ No earnings risk | IV rank: {iv_rank}% ({iv_env.upper()} IV)")

    # Additional warnings
    for w in warnings:
        lines.append(f"⚠️ {w}")

    # Options section
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    iv_rec = "→ Favor SELLING options (CSP / Covered Call)" if iv_rank >= 50 else "→ Favor BUYING options (calls / puts)"
    lines.append(f"💰 <b>OPTIONS — {symbol} {'CALLS' if is_bullish else 'PUTS'}</b>")
    lines.append(f"IV rank {iv_rank:.0f}% → {'HIGH' if iv_rank>=50 else 'LOW'} IV  {iv_rec}")
    lines.append("")

    dte_note = f"30–45 DTE for swing | 0–7 DTE for day trade" if alert_type == 'SWING' else "0–7 DTE (weekly)"

    if is_bullish:
        bc = options_data.get('buy_call')
        csp = options_data.get('sell_csp')

        if bc:
            lines.append(f"Option A — Buy call (directional):")
            lines.append(f"  Contract: {symbol} ${bc['strike']}C exp {options_data.get('expiry','')} ({options_data.get('dte','')} DTE)")
            lines.append(f"  Ask: ~${bc['ask']} | Break-even: ${bc['break_even']}")
            lines.append(f"  Max risk: ${bc['max_risk']} per contract")
            lines.append(f"  Best for: fast move toward TP1 ({dte_note})")
        else:
            lines.append(f"Option A — Buy call ({dte_note}, delta 0.40–0.55 target)")

        lines.append("")

        if csp:
            lines.append(f"Option B — Sell cash-secured put (income):")
            lines.append(f"  Contract: {symbol} ${csp['strike']}P exp {options_data.get('expiry','')} ({options_data.get('dte','')} DTE)")
            lines.append(f"  Premium: ~${csp['premium_bid']} (${csp['max_profit']} per contract)")
            lines.append(f"  Cash required: ${csp['cash_required']:,.0f} | Break-even: ${csp['break_even']}")
            lines.append(f"  Best for: if you want to own {symbol} at a discount")
        else:
            lines.append(f"Option B — Sell CSP at or below ${stop_price:,.2f} strike")

    else:  # bearish
        bp = options_data.get('buy_put')
        cc = options_data.get('sell_cc')

        if bp:
            lines.append(f"Option A — Buy put (directional):")
            lines.append(f"  Contract: {symbol} ${bp['strike']}P exp {options_data.get('expiry','')} ({options_data.get('dte','')} DTE)")
            lines.append(f"  Ask: ~${bp['ask']} | Break-even: ${bp['break_even']}")
            lines.append(f"  Max risk: ${bp['max_risk']} per contract")
        else:
            lines.append(f"Option A — Buy put ({dte_note}, delta 0.40–0.55 target)")

        lines.append("")

        if cc:
            lines.append(f"Option B — Sell covered call (income, if you own shares):")
            lines.append(f"  Contract: {symbol} ${cc['strike']}C exp {options_data.get('expiry','')} ({options_data.get('dte','')} DTE)")
            lines.append(f"  Premium: ~${cc['premium_bid']} (${cc['max_profit']} per contract)")
            lines.append(f"  Note: requires 100 shares of {symbol}")
        else:
            lines.append(f"Option B — Sell covered call above ${stop_price:,.2f} (if holding shares)")

    # Earnings option warning
    if warn_level == 'high':
        lines.append("")
        lines.append(f"⚠️ EARNINGS in {days} days — HIGH IV CRUSH RISK. Buying options before earnings is very risky.")
    elif days and days <= 1:
        lines.append(f"⚠️ Earnings TOMORROW — do NOT buy options. Consider selling only if very high conviction.")

    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# Daily watchlist builder (Tier 1 + Tier 2 scan finds)
# ---------------------------------------------------------------------------

def build_daily_watchlist() -> list:
    core      = list(STOCK_WATCHLIST_CORE)
    scan_file = BASE_DIR / "data" / "sessions" / f"active_stocks_{date.today()}.json"

    if scan_file.exists():
        try:
            with open(scan_file) as f:
                scan = json.load(f)
            scan_symbols = [s['symbol'] for s in scan.get('active', [])]
            additions    = [s for s in scan_symbols if s not in core]
            if additions:
                print(f"[stock_alerts] Adding scan finds to today's watchlist: {additions}")
            return core + additions
        except Exception:
            pass

    return core


# ---------------------------------------------------------------------------
# Alert de-dupe check
# ---------------------------------------------------------------------------

def _already_alerted_stock(symbol: str, hours: float = 24.0) -> bool:
    recent = get_recent_alerts(symbol=symbol, hours=hours)
    return any(a.get('type') in ('SWING', 'DAY_TRADE') for a in recent)


# ---------------------------------------------------------------------------
# ET time helpers
# ---------------------------------------------------------------------------

def _is_market_hours() -> bool:
    try:
        import zoneinfo
        et  = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        if now.weekday() >= 5: return False
        return (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)
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
# Single symbol check
# ---------------------------------------------------------------------------

def check_stock_symbol(symbol: str, test_mode: bool = False) -> dict:
    result = {'symbol': symbol, 'alerts_fired': []}

    try:
        ticker    = yf.Ticker(symbol)
        hist_d    = ticker.history(period="60d", interval="1d")
        hist_15m  = ticker.history(period="5d",  interval="15m")
    except Exception as e:
        print(f"  [{symbol}] Data fetch error: {e}")
        return result

    if hist_d.empty:
        return result

    indicators = _calc_stock_indicators(hist_d, hist_15m)
    result['indicators'] = indicators
    price = indicators.get('price', 0)
    if not price:
        return result
    result['price'] = price

    score = calculate_stock_score(symbol, indicators)
    result['score'] = score

    levels = get_stock_key_levels(symbol, hist_d)
    result['levels'] = levels

    earnings_info = check_earnings_proximity(symbol)
    result['earnings'] = earnings_info

    iv_data = get_iv_rank(symbol)
    result['iv'] = iv_data

    direction = 'bullish' if score['total'] > 0 else 'bearish'

    swing_met    = check_swing_conditions(symbol, score, levels, indicators)
    day_met      = check_day_trade_conditions(symbol, score, indicators, hist_15m)
    result['swing_met']   = swing_met
    result['day_trade_met'] = day_met

    # Check max intraday move
    intraday_move = _intraday_move_pct(hist_15m)
    if intraday_move > STOCK_ALERT_CONFIG['max_intraday_move_pct']:
        result['suppressed'] = f"Intraday move {intraday_move:.1f}% > 3% — setup already ran"
        return result

    # Suppress swing alert near earnings
    if swing_met and earnings_info['warning_level'] == 'high':
        swing_met = False
        result['suppressed'] = f"Earnings in {earnings_info['days_until']} days — swing alert suppressed"

    if test_mode:
        return result

    # --- Fire alerts ---
    if (swing_met or day_met) and not _already_alerted_stock(symbol, hours=STOCK_ALERT_CONFIG['alert_cooldown_hours']):
        warnings = []
        if score['total'] != 0:
            pass  # could add sector context here

        options_data = {}
        stop_est = price * (0.97 if direction == 'bullish' else 1.03)
        tp1_est  = price * (1.03 if direction == 'bullish' else 0.97)
        try:
            options_data = get_options_suggestion(
                symbol, direction, price, stop_est, tp1_est,
                prefer_dte_range=(
                    STOCK_ALERT_CONFIG['preferred_dte_min'],
                    STOCK_ALERT_CONFIG['preferred_dte_max']
                )
            )
        except Exception:
            pass

        alert_type = 'DAY_TRADE' if day_met else 'SWING'
        msg = format_stock_alert(
            alert_type=alert_type,
            symbol=symbol, direction=direction, price=price,
            score=score, levels=levels, indicators=indicators,
            earnings_info=earnings_info, iv_data=iv_data,
            options_data=options_data, reasons=[], warnings=warnings,
        )
        fire_alert(msg, alert_type=alert_type, symbol=symbol)
        result['alerts_fired'].append(alert_type)

    return result


# ---------------------------------------------------------------------------
# Print test result
# ---------------------------------------------------------------------------

def _print_test_result(result: dict) -> None:
    sym   = result['symbol']
    price = result.get('price', 0)
    score = result.get('score', {})
    iv    = result.get('iv', {})
    earn  = result.get('earnings', {})

    score_str = f"+{score.get('total',0)}" if score.get('total',0) > 0 else str(score.get('total',0))
    bd = " ".join(f"{k}:{'+' if v>0 else ''}{v}" for k, v in score.items()
                  if k in ('TEC','COT','RET','SEA','ECO','PC','MOM'))

    print(f"\n{'='*60}")
    print(f"  {sym} STOCK TEST EVALUATION")
    print(f"{'='*60}")
    print(f"  Price:  ${price:,.2f}")
    print(f"  Score:  {score_str} ({score.get('bias','?')})")
    print(f"  Signals: {bd}")
    print(f"  IV Rank: {iv.get('iv_rank',0):.0f}% ({iv.get('environment','?')} IV)")
    earn_days = earn.get('days_until')
    print(f"  Earnings: {'in ' + str(earn_days) + ' days (' + earn.get('warning_level','') + ')' if earn_days else 'not found'}")
    print(f"  Swing conditions met:     {'YES ✓' if result.get('swing_met') else 'no'}")
    print(f"  Day trade conditions met: {'YES ✓' if result.get('day_trade_met') else 'no'}")
    if result.get('suppressed'):
        print(f"  ⚠️  Suppressed: {result['suppressed']}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

_last_hb_stock = 0

def _maybe_heartbeat_stock(results: list, watchlist: list) -> None:
    global _last_hb_stock
    now = time.time()
    if now - _last_hb_stock < 900:  # every 15 min
        return
    _last_hb_stock = now

    core_count = len(STOCK_WATCHLIST_CORE)
    scan_count = len(watchlist) - core_count
    parts = []
    for r in results[:4]:
        if r.get('price'):
            s = r.get('score', {})
            score_str = f"+{s.get('total',0)}" if s.get('total',0) > 0 else str(s.get('total',0))
            parts.append(f"{r['symbol']}=${r['price']:,.1f}({score_str})")

    ts = _et_time_str()
    scan_note = f" + {scan_count} scan finds" if scan_count > 0 else ""
    print(f"[{ts} ET] Stocks: {core_count} core{scan_note} active | {' | '.join(parts)}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop() -> None:
    print("\n=== Stock Alert Monitor starting ===")
    watchlist = build_daily_watchlist()
    print(f"Monitoring {len(watchlist)} symbols: {', '.join(watchlist)}")

    while True:
        if not _is_market_hours():
            time.sleep(60)
            continue

        results = []
        for sym in watchlist:
            try:
                r = check_stock_symbol(sym)
                results.append(r)
            except Exception as e:
                print(f"  [{sym}] Error: {e}")

        _maybe_heartbeat_stock(results, watchlist)
        time.sleep(300)  # 5 minutes


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stock Alert Monitor")
    parser.add_argument('--test', metavar='SYMBOL', help='Test mode: SPY, NVDA, ... or ALL')
    args = parser.parse_args()

    if args.test:
        test_sym = args.test.upper()
        if test_sym == 'ALL':
            watchlist = build_daily_watchlist()
        else:
            watchlist = [test_sym]

        for sym in watchlist:
            r = check_stock_symbol(sym, test_mode=True)
            _print_test_result(r)
        sys.exit(0)

    run_loop()
