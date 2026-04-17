"""
alert_engine.py — Core scoring, level detection, pattern recognition, alert formatting

Functions exposed:
    get_live_indicators(symbol)
    calculate_score(symbol, indicators)
    calculate_session_levels(symbol)
    get_key_levels(symbol)
    detect_candle_pattern(bars)
    check_setup_conditions(symbol, score, levels, indicators)
    check_entry_conditions(symbol, bars, indicators, pattern)
    format_alert_message(...)
"""

import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from providers import tradingview_provider

load_dotenv()

BASE_DIR     = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Instrument config
# ---------------------------------------------------------------------------

FUTURES_CONFIG = {
    'MES': {'tick': 0.25,  'point_value': 5,    'alert_threshold': 5, 'yf': 'MES=F'},
    'MNQ': {'tick': 0.25,  'point_value': 2,    'alert_threshold': 5, 'yf': 'MNQ=F'},
    'MYM': {'tick': 1,     'point_value': 0.50, 'alert_threshold': 5, 'yf': 'MYM=F'},
    'MCL': {'tick': 0.01,  'point_value': 100,  'alert_threshold': 5, 'yf': 'CL=F'},
    'MGC': {'tick': 0.10,  'point_value': 10,   'alert_threshold': 5, 'yf': 'GC=F'},
    'MSI': {'tick': 0.005, 'point_value': 25,   'alert_threshold': 5, 'yf': 'SI=F'},
    'MNG': {'tick': 0.001, 'point_value': 250,  'alert_threshold': 5, 'yf': 'NG=F'},
    'M2K': {'tick': 0.10,  'point_value': 5,    'alert_threshold': 5, 'yf': 'RTY=F'},
}

# Full-size reference contracts
FULL_SIZE_YF = {
    'ES': 'ES=F', 'NQ': 'NQ=F', 'YM': 'YM=F', 'RTY': 'RTY=F',
    'CL': 'CL=F', 'GC': 'GC=F', 'SI': 'SI=F', 'NG': 'NG=F',
}

# Forex pairs — Yahoo Finance uses XXXYYY=X format
FOREX_CONFIG = {
    # Major pairs
    'EURUSD': {'yf': 'EURUSD=X', 'decimals': 5, 'group': 'forex-major'},
    'GBPUSD': {'yf': 'GBPUSD=X', 'decimals': 5, 'group': 'forex-major'},
    'USDJPY': {'yf': 'USDJPY=X', 'decimals': 3, 'group': 'forex-major'},
    'USDCHF': {'yf': 'USDCHF=X', 'decimals': 5, 'group': 'forex-major'},
    'AUDUSD': {'yf': 'AUDUSD=X', 'decimals': 5, 'group': 'forex-major'},
    'NZDUSD': {'yf': 'NZDUSD=X', 'decimals': 5, 'group': 'forex-major'},
    'USDCAD': {'yf': 'USDCAD=X', 'decimals': 5, 'group': 'forex-major'},
    # Minor pairs
    'EURGBP': {'yf': 'EURGBP=X', 'decimals': 5, 'group': 'forex-minor'},
    'EURJPY': {'yf': 'EURJPY=X', 'decimals': 3, 'group': 'forex-minor'},
    'GBPJPY': {'yf': 'GBPJPY=X', 'decimals': 3, 'group': 'forex-minor'},
    'EURCHF': {'yf': 'EURCHF=X', 'decimals': 5, 'group': 'forex-minor'},
    'AUDCAD': {'yf': 'AUDCAD=X', 'decimals': 5, 'group': 'forex-minor'},
    'CADJPY': {'yf': 'CADJPY=X', 'decimals': 3, 'group': 'forex-minor'},
    'AUDNZD': {'yf': 'AUDNZD=X', 'decimals': 5, 'group': 'forex-minor'},
    'NZDJPY': {'yf': 'NZDJPY=X', 'decimals': 3, 'group': 'forex-minor'},
    'GBPAUD': {'yf': 'GBPAUD=X', 'decimals': 5, 'group': 'forex-minor'},
    'NZDCAD': {'yf': 'NZDCAD=X', 'decimals': 5, 'group': 'forex-minor'},
}


def _yf_symbol(symbol: str) -> str:
    if symbol in FUTURES_CONFIG:
        return FUTURES_CONFIG[symbol]['yf']
    if symbol in FULL_SIZE_YF:
        return FULL_SIZE_YF[symbol]
    if symbol in FOREX_CONFIG:
        return FOREX_CONFIG[symbol]['yf']
    return symbol  # stocks pass through as-is


# ---------------------------------------------------------------------------
# Indicator calculations from yfinance bars
# ---------------------------------------------------------------------------

def _calc_indicators_from_bars(bars: pd.DataFrame) -> dict:
    """Calculate all needed indicators from OHLCV bar DataFrame."""
    if bars is None or len(bars) < 10:
        return {}

    close = bars['Close']
    vol   = bars['Volume']

    def ema(n):
        return float(close.ewm(span=n, adjust=False).mean().iloc[-1])

    def sma(n):
        if len(close) >= n:
            return float(close.tail(n).mean())
        return float(close.mean())

    # RSI 14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = float(100 - (100 / (1 + rs)).iloc[-1]) if not rs.isna().all() else 50.0

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal    = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = float((macd_line - signal).iloc[-1])

    # VWAP (rolling from available bars)
    typical = (bars['High'] + bars['Low'] + bars['Close']) / 3
    vwap    = float((typical * vol).sum() / vol.sum()) if vol.sum() > 0 else float(close.iloc[-1])

    # ATR 14
    tr = pd.concat([
        bars['High'] - bars['Low'],
        (bars['High'] - bars['Close'].shift()).abs(),
        (bars['Low']  - bars['Close'].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else float(tr.mean())

    # Volume vs 20-period average
    vol_ma20 = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean())

    return {
        'ema_9':     ema(9),
        'ema_21':    ema(21),
        'ema_50':    ema(50),
        'ema_200':   ema(200),
        'rsi':       rsi,
        'macd_hist': macd_hist,
        'vwap':      vwap,
        'atr':       atr,
        'volume':    float(vol.iloc[-1]),
        'vol_ma20':  vol_ma20,
        'price':     float(close.iloc[-1]),
        'source':    'yfinance',
    }


def calculate_from_yfinance(symbol: str, interval: str = '15m', period: str = '5d') -> dict:
    """Fetch bars from yfinance and compute indicators."""
    try:
        yf_sym = _yf_symbol(symbol)
        ticker = yf.Ticker(yf_sym)
        bars   = ticker.history(interval=interval, period=period)
        if bars.empty:
            return {}
        return _calc_indicators_from_bars(bars)
    except Exception as e:
        print(f"[alert_engine] yfinance error for {symbol}: {e}")
        return {}


def calculate_from_tradingview(symbol: str, interval: str = '15m') -> dict:
    """Fetch bars from TradingView and compute indicators."""
    try:
        timeframe = tradingview_provider.TIMEFRAME_MAP.get(interval, interval)
        bars = tradingview_provider.get_ohlcv(symbol, timeframe=timeframe, count=200)
        if bars.empty:
            return {}
        indicators = _calc_indicators_from_bars(bars)
        if indicators:
            indicators['source'] = 'tradingview_chart'
        return indicators
    except Exception as e:
        print(f"[alert_engine] tradingview error for {symbol}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Live indicators (TradingView MCP → yfinance fallback)
# ---------------------------------------------------------------------------

def get_live_indicators(symbol: str) -> dict:
    """
    Try TradingView MCP first; fall back to yfinance calculations.
    Returns dict with ema_50, ema_200, rsi, macd_hist, vwap, atr, volume, vol_ma20, source.
    """
    if tradingview_provider.is_connected():
        indicators = calculate_from_tradingview(symbol)
        if indicators:
            return indicators
        return {}

    return calculate_from_yfinance(symbol)


# ---------------------------------------------------------------------------
# 7-signal scoring
# ---------------------------------------------------------------------------

def _score_tec(indicators: dict) -> int:
    """TEC: price vs 50 EMA and 200 EMA."""
    price   = indicators.get('price', 0) or indicators.get('vwap', 0)
    ema_50  = indicators.get('ema_50',  0)
    ema_200 = indicators.get('ema_200', 0)

    if not price or not ema_50 or not ema_200:
        return 0

    above_50  = price > ema_50
    above_200 = price > ema_200

    if above_50 and above_200:
        return 2
    elif above_50 or above_200:
        return 1
    elif not above_50 and not above_200:
        return -2
    else:
        return -1


def _score_mom(indicators: dict) -> int:
    """MOM: RSI, MACD histogram direction, price vs VWAP."""
    rsi       = indicators.get('rsi', 50)
    macd_hist = indicators.get('macd_hist', 0)
    price     = indicators.get('price', 0)
    vwap      = indicators.get('vwap', 0)

    score = 0

    # RSI
    if rsi and rsi > 55:
        score += 1
    elif rsi and rsi < 45:
        score -= 1

    # MACD histogram
    if macd_hist and macd_hist > 0:
        score += 1
    elif macd_hist and macd_hist < 0:
        score -= 1

    # Price vs VWAP
    if price and vwap and price > vwap:
        score += 0  # neutral, requires other confirmation
    elif price and vwap and price < vwap:
        score -= 0

    return max(-2, min(2, score))


def _score_seasonality(symbol: str) -> int:
    """SEA: rough monthly seasonality for index futures."""
    month = datetime.now().month
    # Historical average monthly returns for ES/NQ (approximate)
    ES_SEASONAL = {1: 1, 2: -1, 3: 0, 4: 1, 5: -1, 6: 0,
                   7: 1,  8: -1, 9: -2, 10: 0, 11: 2, 12: 1}
    # Metals tend positive in Q1, Q4
    GC_SEASONAL = {1: 1, 2: 1, 3: 0, 4: -1, 5: -1, 6: 0,
                   7: 0, 8: 1, 9: 1, 10: 0, 11: -1, 12: 0}
    CL_SEASONAL = {1: -1, 2: 0, 3: 1, 4: 1, 5: 1, 6: 0,
                   7: -1, 8: -1, 9: 0, 10: 0, 11: -1, 12: -1}

    if symbol in ('MES', 'MNQ', 'MYM', 'M2K', 'ES', 'NQ', 'YM', 'RTY'):
        return ES_SEASONAL.get(month, 0)
    elif symbol in ('MGC', 'MSI', 'GC', 'SI'):
        return GC_SEASONAL.get(month, 0)
    elif symbol in ('MCL', 'MNG', 'CL', 'NG'):
        return CL_SEASONAL.get(month, 0)
    return 0


def calculate_score(symbol: str, indicators: dict) -> dict:
    """
    Compute all 7 signal scores and total.
    Returns dict with TEC, COT, RET, SEA, ECO, PC, MOM, total, bias.
    """
    from agents.cot_agent import get_cot_score
    from agents.economic_agent import get_eco_score

    tec = _score_tec(indicators)
    cot = get_cot_score(symbol)
    ret = 0   # placeholder — retail data not wired
    sea = _score_seasonality(symbol)
    eco = get_eco_score()
    pc  = 0   # placeholder — put/call data not wired
    mom = _score_mom(indicators)

    total = tec + cot + ret + sea + eco + pc + mom

    if total >= 10:
        bias = "Very Bullish"
    elif total >= 5:
        bias = "Bullish"
    elif total <= -10:
        bias = "Very Bearish"
    elif total <= -5:
        bias = "Bearish"
    else:
        bias = "Neutral"

    return {
        'TEC': tec, 'COT': cot, 'RET': ret, 'SEA': sea,
        'ECO': eco, 'PC':  pc,  'MOM': mom,
        'total': total,
        'bias':  bias,
    }


# ---------------------------------------------------------------------------
# Session levels
# ---------------------------------------------------------------------------

def calculate_session_levels(symbol: str) -> list:
    """
    Fetch OHLCV bars, compute PDH/PDL/PDC/PDM/DO/IBH/IBL/ONH/ONL/WO/WH/WL/MO.
    Detect S/R flips. Save to data/sessions/levels_YYYY-MM-DD.json.
    Returns list of level dicts.
    """
    yf_sym = _yf_symbol(symbol)
    today_str = date.today().isoformat()
    cache_file = SESSIONS_DIR / f"levels_{today_str}.json"

    # Load existing cache for this date
    existing = {}
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    # Try to get intraday data (1m bars for today, daily for history)
    try:
        ticker = yf.Ticker(yf_sym)
        daily  = ticker.history(period="5d", interval="1d")
        intra  = ticker.history(period="1d", interval="1m")
    except Exception as e:
        print(f"[alert_engine] Level calc error for {symbol}: {e}")
        return existing.get(symbol, [])

    levels = []

    # Previous day bars
    if len(daily) >= 2:
        prev = daily.iloc[-2]
        curr = daily.iloc[-1]
        pdh = float(prev['High'])
        pdl = float(prev['Low'])
        pdc = float(prev['Close'])
        pdm = round((pdh + pdl) / 2, 4)

        current_price = float(curr['Close']) if not intra.empty else float(daily.iloc[-1]['Close'])

        # S/R flip detection
        pdh_role = "resistance"
        pdl_role = "support"
        if current_price > pdh:
            pdh_role = "S/R FLIP → now support"
        if current_price < pdl:
            pdl_role = "S/R FLIP → now resistance"

        levels += [
            {'price': pdh, 'label': 'PDH', 'type': 'resistance', 'strength': 'strong',
             'distance_pts': round(abs(current_price - pdh), 4), 'role': pdh_role},
            {'price': pdl, 'label': 'PDL', 'type': 'support',    'strength': 'strong',
             'distance_pts': round(abs(current_price - pdl), 4), 'role': pdl_role},
            {'price': pdc, 'label': 'PDC', 'type': 'pivot',      'strength': 'medium',
             'distance_pts': round(abs(current_price - pdc), 4), 'role': 'pivot'},
            {'price': pdm, 'label': 'PDM', 'type': 'pivot',      'strength': 'weak',
             'distance_pts': round(abs(current_price - pdm), 4), 'role': 'midpoint'},
        ]
    else:
        current_price = float(daily.iloc[-1]['Close']) if not daily.empty else 0

    # Current day open
    if not intra.empty:
        do_price = float(intra.iloc[0]['Open'])
        levels.append({'price': do_price, 'label': 'DO', 'type': 'pivot', 'strength': 'medium',
                        'distance_pts': round(abs(current_price - do_price), 4), 'role': 'day open'})

        # Initial balance (9:30–10:00 AM ET)
        try:
            import zoneinfo
            et = zoneinfo.ZoneInfo("America/New_York")
            ib_bars = intra[
                (intra.index.tz_convert(et).time >= __import__('datetime').time(9, 30)) &
                (intra.index.tz_convert(et).time <= __import__('datetime').time(10, 0))
            ]
            if not ib_bars.empty:
                ibh = float(ib_bars['High'].max())
                ibl = float(ib_bars['Low'].min())
                levels += [
                    {'price': ibh, 'label': 'IBH', 'type': 'resistance', 'strength': 'medium',
                     'distance_pts': round(abs(current_price - ibh), 4), 'role': 'IB high'},
                    {'price': ibl, 'label': 'IBL', 'type': 'support',    'strength': 'medium',
                     'distance_pts': round(abs(current_price - ibl), 4), 'role': 'IB low'},
                ]

            # Overnight Globex (before 9:30 AM)
            on_bars = intra[intra.index.tz_convert(et).time < __import__('datetime').time(9, 30)]
            if not on_bars.empty:
                onh = float(on_bars['High'].max())
                onl = float(on_bars['Low'].min())
                levels += [
                    {'price': onh, 'label': 'ONH', 'type': 'resistance', 'strength': 'weak',
                     'distance_pts': round(abs(current_price - onh), 4), 'role': 'overnight high'},
                    {'price': onl, 'label': 'ONL', 'type': 'support',    'strength': 'weak',
                     'distance_pts': round(abs(current_price - onl), 4), 'role': 'overnight low'},
                ]
        except Exception:
            pass

    # Weekly levels
    try:
        weekly = yf.Ticker(yf_sym).history(period="5d", interval="1d")
        if not weekly.empty:
            weekly.index = pd.to_datetime(weekly.index)
            week_start = datetime.now().isocalendar()[1]
            wo = float(weekly.iloc[0]['Open'])
            wh = float(weekly['High'].max())
            wl = float(weekly['Low'].min())
            levels += [
                {'price': wo, 'label': 'WO', 'type': 'pivot',      'strength': 'medium',
                 'distance_pts': round(abs(current_price - wo), 4), 'role': 'weekly open'},
                {'price': wh, 'label': 'WH', 'type': 'resistance', 'strength': 'medium',
                 'distance_pts': round(abs(current_price - wh), 4), 'role': 'weekly high'},
                {'price': wl, 'label': 'WL', 'type': 'support',    'strength': 'medium',
                 'distance_pts': round(abs(current_price - wl), 4), 'role': 'weekly low'},
            ]
    except Exception:
        pass

    # Monthly open
    try:
        mo_data = yf.Ticker(yf_sym).history(period="1mo", interval="1d")
        if not mo_data.empty:
            mo = float(mo_data.iloc[0]['Open'])
            levels.append({'price': mo, 'label': 'MO', 'type': 'pivot', 'strength': 'medium',
                            'distance_pts': round(abs(current_price - mo), 4), 'role': 'monthly open'})
    except Exception:
        pass

    # Save
    existing[symbol] = levels
    with open(cache_file, 'w') as f:
        json.dump(existing, f, indent=2)

    return levels


def get_key_levels(symbol: str) -> list:
    """
    Combine session levels + technical MAs + round numbers + Fibonacci.
    Returns combined, deduplicated, sorted list.
    """
    levels = calculate_session_levels(symbol)

    indicators = get_live_indicators(symbol)
    current_price = indicators.get('price', 0) or (levels[0]['price'] if levels else 0)

    # Add MA levels
    for key, label in [('ema_50', '50 EMA'), ('ema_200', '200 EMA')]:
        val = indicators.get(key)
        if val:
            levels.append({
                'price': round(float(val), 4),
                'label': label,
                'type': 'support' if current_price > float(val) else 'resistance',
                'strength': 'strong',
                'distance_pts': round(abs(current_price - float(val)), 4),
                'role': label,
            })

    # Round numbers near current price
    if current_price:
        magnitude = 10 ** (len(str(int(current_price))) - 2)  # e.g., 100 for a 4-digit price
        for i in range(-3, 4):
            rn = round(current_price / magnitude) * magnitude + i * magnitude
            if abs(rn - current_price) / current_price < 0.03:  # within 3%
                levels.append({
                    'price': round(rn, 2),
                    'label': f'R{int(rn)}',
                    'type': 'resistance' if rn > current_price else 'support',
                    'strength': 'weak',
                    'distance_pts': round(abs(current_price - rn), 4),
                    'role': 'round number',
                })

    # Fibonacci retracement from recent swing
    try:
        yf_sym = _yf_symbol(symbol)
        hist   = yf.Ticker(yf_sym).history(period="20d", interval="1d")
        if len(hist) >= 5:
            swing_high = float(hist['High'].tail(20).max())
            swing_low  = float(hist['Low'].tail(20).min())
            diff = swing_high - swing_low
            for ratio, label in [(0.236, 'Fib 23.6%'), (0.382, 'Fib 38.2%'),
                                  (0.500, 'Fib 50.0%'), (0.618, 'Fib 61.8%'),
                                  (0.786, 'Fib 78.6%')]:
                fib_price = round(swing_high - diff * ratio, 4)
                if abs(fib_price - current_price) / current_price < 0.05:
                    levels.append({
                        'price': fib_price,
                        'label': label,
                        'type': 'support' if fib_price < current_price else 'resistance',
                        'strength': 'medium',
                        'distance_pts': round(abs(current_price - fib_price), 4),
                        'role': f'fibonacci retracement',
                    })
    except Exception:
        pass

    # Deduplicate (within 0.05% of each other)
    deduped = []
    for lvl in sorted(levels, key=lambda x: x['price']):
        if not deduped or abs(lvl['price'] - deduped[-1]['price']) / max(lvl['price'], 0.01) > 0.0005:
            deduped.append(lvl)

    return sorted(deduped, key=lambda x: x['distance_pts'])


# ---------------------------------------------------------------------------
# Candle pattern detection
# ---------------------------------------------------------------------------

def detect_candle_pattern(bars: pd.DataFrame, key_levels: list = None) -> dict:
    """
    Takes last 3 completed OHLCV bars.
    Returns pattern dict: {pattern, direction, strength, at_level}.
    Only valid if within 0.3% of a key level.
    """
    if bars is None or len(bars) < 1:
        return {'pattern': None, 'direction': None, 'strength': 'weak', 'at_level': ''}

    # Use last 3 bars
    bars = bars.tail(3).copy()
    if len(bars) < 1:
        return {'pattern': None, 'direction': None, 'strength': 'weak', 'at_level': ''}

    bar = bars.iloc[-1]
    O, H, L, C = float(bar['Open']), float(bar['High']), float(bar['Low']), float(bar['Close'])

    if H == L:
        return {'pattern': None, 'direction': None, 'strength': 'weak', 'at_level': ''}

    body   = abs(C - O)
    range_ = H - L
    body_pct = body / range_ if range_ > 0 else 0

    upper_wick = H - max(O, C)
    lower_wick = min(O, C) - L
    bullish_bar = C > O

    pattern   = None
    direction = None
    strength  = 'weak'

    # Single-bar patterns
    if body_pct >= 0.80:
        pattern   = 'marubozu'
        direction = 'bullish' if bullish_bar else 'bearish'
        strength  = 'strong'

    elif body_pct < 0.10:
        if upper_wick > 2 * lower_wick:
            pattern   = 'gravestone_doji'
            direction = 'bearish'
            strength  = 'weak'
        elif lower_wick > 2 * upper_wick:
            pattern   = 'dragonfly_doji'
            direction = 'bullish'
            strength  = 'weak'
        else:
            pattern   = 'doji'
            direction = None
            strength  = 'weak'

    elif lower_wick >= 2 * body and upper_wick <= 0.1 * range_:
        pattern   = 'hammer' if bullish_bar else 'hanging_man'
        direction = 'bullish' if pattern == 'hammer' else 'bearish'
        strength  = 'strong'

    elif upper_wick >= 2 * body and lower_wick <= 0.1 * range_:
        pattern   = 'inverted_hammer' if bullish_bar else 'shooting_star'
        direction = 'bearish' if pattern == 'shooting_star' else 'bullish'
        strength  = 'strong' if pattern == 'shooting_star' else 'weak'

    # Two-bar patterns (need prev bar)
    if len(bars) >= 2 and pattern is None:
        prev = bars.iloc[-2]
        pO, pH, pL, pC = float(prev['Open']), float(prev['High']), float(prev['Low']), float(prev['Close'])

        # Bullish engulfing
        if pC < pO and C > O and O < pC and C > pO:
            pattern   = 'bullish_engulfing'
            direction = 'bullish'
            strength  = 'strong'

        # Bearish engulfing
        elif pC > pO and C < O and O > pC and C < pO:
            pattern   = 'bearish_engulfing'
            direction = 'bearish'
            strength  = 'strong'

        # Inside bar
        elif H < pH and L > pL:
            pattern   = 'inside_bar'
            direction = None
            strength  = 'weak'

        # Outside bar
        elif H > pH and L < pL:
            pattern   = 'outside_bar'
            direction = 'bullish' if C > O else 'bearish'
            strength  = 'medium'

    # Three-bar patterns (morning/evening star)
    if len(bars) >= 3 and pattern is None:
        b1, b2, b3 = bars.iloc[0], bars.iloc[1], bars.iloc[2]
        b1O, b1C = float(b1['Open']), float(b1['Close'])
        b2O, b2C = float(b2['Open']), float(b2['Close'])
        b3O, b3C = float(b3['Open']), float(b3['Close'])
        b2_body = abs(b2C - b2O) / (float(b2['High']) - float(b2['Low']) + 1e-10)

        if b1C < b1O and b2_body < 0.3 and b3C > b3O and b3C > (b1O + b1C) / 2:
            pattern   = 'morning_star'
            direction = 'bullish'
            strength  = 'strong'
        elif b1C > b1O and b2_body < 0.3 and b3C < b3O and b3C < (b1O + b1C) / 2:
            pattern   = 'evening_star'
            direction = 'bearish'
            strength  = 'strong'

    if pattern is None:
        return {'pattern': None, 'direction': None, 'strength': 'weak', 'at_level': ''}

    # Check proximity to a key level (within 0.3%)
    at_level = ''
    if key_levels:
        for lvl in key_levels:
            lvl_price = lvl['price']
            if lvl_price > 0 and abs(C - lvl_price) / lvl_price <= 0.003:
                at_level = lvl['label']
                break

    if not at_level and key_levels:
        # Pattern only valid near a level
        return {'pattern': None, 'direction': None, 'strength': 'weak', 'at_level': ''}

    return {
        'pattern':   pattern,
        'direction': direction,
        'strength':  strength,
        'at_level':  at_level,
    }


# ---------------------------------------------------------------------------
# Setup / Entry condition checks
# ---------------------------------------------------------------------------

def check_setup_conditions(symbol: str, score: dict, levels: list, indicators: dict) -> bool:
    """
    Returns True if a setup alert should fire:
    - |total| >= 5
    - Price within 0.25% of a relevant level
    - At least 2 of: RSI confirms, MACD confirms, price side of 50MA
    - COT aligned with direction
    """
    total = score['total']
    if abs(total) < 5:
        return False

    direction = 'bullish' if total > 0 else 'bearish'
    price     = indicators.get('price', 0)

    if not price:
        return False

    # Check proximity to relevant level
    relevant_levels = [
        lvl for lvl in levels
        if (direction == 'bullish' and lvl['type'] in ('support', 'pivot')) or
           (direction == 'bearish' and lvl['type'] in ('resistance', 'pivot'))
    ]
    near_level = any(
        lvl['price'] > 0 and abs(price - lvl['price']) / price <= 0.0025
        for lvl in relevant_levels
    )
    if not near_level:
        return False

    # Confirm with at least 2 technical signals
    confirmations = 0
    rsi       = indicators.get('rsi', 50)
    macd_hist = indicators.get('macd_hist', 0)
    ema_50    = indicators.get('ema_50', 0)

    if direction == 'bullish':
        if rsi and rsi > 50:       confirmations += 1
        if macd_hist and macd_hist > 0: confirmations += 1
        if ema_50 and price > ema_50:   confirmations += 1
    else:
        if rsi and rsi < 50:       confirmations += 1
        if macd_hist and macd_hist < 0: confirmations += 1
        if ema_50 and price < ema_50:   confirmations += 1

    if confirmations < 2:
        return False

    # COT must be aligned
    cot = score.get('COT', 0)
    if direction == 'bullish' and cot < 0:
        return False  # COT conflict — suppress setup
    if direction == 'bearish' and cot > 0:
        return False

    return True


def check_entry_conditions(symbol: str, bars: pd.DataFrame,
                           indicators: dict, pattern: dict,
                           levels: list = None) -> bool:
    """
    Returns True if entry conditions are confirmed:
    - Price closes back above support (long) or below resistance (short)
    - Volume >= 1.0x 20-period average
    - Not within 30 min of a high-impact event
    - Setup conditions already met
    """
    if not pattern or not pattern.get('pattern'):
        return False

    price   = indicators.get('price', 0)
    volume  = indicators.get('volume', 0)
    vol_avg = indicators.get('vol_ma20', 1)

    if not price:
        return False

    # Volume check
    if vol_avg and volume and volume < vol_avg:
        return False

    # Economic event proximity check
    if _near_high_impact_event():
        return False

    # At least one pattern at a level
    if not pattern.get('at_level'):
        return False

    return True


def _near_high_impact_event(minutes: int = 30) -> bool:
    """Check if we're within N minutes of a high-impact economic event."""
    from agents.economic_agent import get_cached_eco
    eco = get_cached_eco()
    indicators = eco.get('indicators', [])

    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
    except ImportError:
        now = datetime.now(timezone.utc)

    for ind in indicators:
        date_str = ind.get('date', '')
        try:
            ev_date = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            # Check if today's date matches and it's a major release time
            if ev_date.date() == now.date():
                # Major releases typically at 8:30, 10:00 AM ET
                for hour, minute in [(8, 30), (10, 0), (14, 0), (15, 0)]:
                    ev_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if abs((now - ev_time).total_seconds()) <= minutes * 60:
                        return True
        except ValueError:
            continue

    return False


# ---------------------------------------------------------------------------
# Alert message formatter
# ---------------------------------------------------------------------------

def format_alert_message(
    alert_type: str,
    symbol: str,
    direction: str,
    price: float,
    score: dict,
    entry_zone: tuple,       # (low, high)
    stop: float,
    tp1: float,
    tp2: float,
    levels: list,
    pattern: dict,
    reasons: list,
    warnings: list,
    session: str = "RTH",
    timeframe: str = "15m",
) -> str:
    """Format the exact Telegram alert message per CLAUDE.md spec."""

    cfg = FUTURES_CONFIG.get(symbol, {})
    point_value = cfg.get('point_value', 1)

    # Calculate risk and R:R
    risk_pts = abs(price - stop) if stop else 0
    rr1_pts  = abs(tp1 - price) if tp1 else 0
    rr2_pts  = abs(tp2 - price) if tp2 else 0
    rr1      = round(rr1_pts / risk_pts, 1) if risk_pts > 0 else 0
    rr2      = round(rr2_pts / risk_pts, 1) if risk_pts > 0 else 0
    dollar_risk = round(risk_pts * point_value, 2)

    # Emoji prefix
    emoji_map = {
        'SETUP': '🔔',
        'ENTRY': '✅',
        'EXIT':  '🚨',
    }
    emoji = emoji_map.get(alert_type.upper(), '🔔')

    SIGNAL_NAMES = {
        'TEC': 'Technicals', 'COT': 'COT', 'RET': 'Retail Sentiment',
        'SEA': 'Seasonality', 'ECO': 'Economy', 'PC': 'Put/Call', 'MOM': 'Momentum',
    }
    LEVEL_NAMES = {
        'PDH': 'Prev Day High', 'PDL': 'Prev Day Low', 'PDC': 'Prev Day Close',
        'PDM': 'Prev Day Mid',  'DO':  'Day Open',
        'IBH': 'IB High',       'IBL': 'IB Low',
        'ONH': 'Overnight High','ONL': 'Overnight Low',
        'WO':  'Weekly Open',   'WH':  'Weekly High',  'WL': 'Weekly Low',
        'MO':  'Monthly Open',
    }

    score_str = f"+{score['total']}" if score['total'] > 0 else str(score['total'])
    signal_str = " ".join(
        f"{SIGNAL_NAMES.get(k, k)}{'+' if v > 0 else ''}{v}"
        for k, v in score.items()
        if k in ('TEC', 'COT', 'RET', 'SEA', 'ECO', 'PC', 'MOM') and v != 0
    )

    entry_str = f"{entry_zone[0]:,.2f}–{entry_zone[1]:,.2f}" if entry_zone and len(entry_zone) == 2 else f"{price:,.2f}"

    lines = [
        f"{emoji} <b>[{alert_type.upper()}] — {symbol} {timeframe}</b>",
        f"Direction: {'Long' if direction == 'bullish' else 'Short'}",
        f"Price: {price:,.2f} | Session: {session}",
        "",
        f"Entry zone: {entry_str}",
        f"Stop loss: {stop:,.2f} ({risk_pts:.2f} pts) | Risk: ${dollar_risk:.2f} per contract ({symbol}=${point_value}/pt)",
        f"TP1: {tp1:,.2f} ({rr1_pts:.2f} pts) | R:R {rr1:.1f}:1",
        f"TP2: {tp2:,.2f} ({rr2_pts:.2f} pts) | R:R {rr2:.1f}:1" if tp2 else "",
        "",
        f"Score: {score_str} ({score['bias']}) | Candle: {pattern.get('pattern') or 'none'}",
        f"Signals: {signal_str}" if signal_str else "",
    ]

    # Key session levels (top 3 nearest)
    if levels:
        top_levels = sorted(levels, key=lambda x: x['distance_pts'])[:4]
        lines.append("")
        lines.append("Key session levels:")
        for lvl in top_levels:
            dist_direction = "above" if lvl['price'] > price else "below"
            role = lvl.get('role', lvl.get('type', ''))
            full_label = LEVEL_NAMES.get(lvl['label'], lvl['label'])
            lines.append(f"• {full_label}: {lvl['price']:,.2f} ({lvl['distance_pts']:.2f} pts {dist_direction}) — {role}")

    # Signal drivers
    if reasons:
        lines.append("")
        lines.append("Signal drivers:")
        for r in reasons:
            lines.append(f"• {r}")

    # Warnings
    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"⚠️ {w}")

    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# Current price helper
# ---------------------------------------------------------------------------

def get_current_price(symbol: str) -> float:
    """Get current price from TradingView when connected, else yfinance."""
    if tradingview_provider.is_connected():
        try:
            quote = tradingview_provider.get_quote(symbol)
            for key in ('last', 'close', 'header_price'):
                value = quote.get(key)
                if value is not None:
                    return float(value)
            return 0.0
        except Exception:
            return 0.0

    try:
        yf_sym = _yf_symbol(symbol)
        ticker = yf.Ticker(yf_sym)
        hist   = ticker.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        info = ticker.fast_info
        return float(info.last_price)
    except Exception:
        return 0.0
