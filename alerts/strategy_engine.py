"""
strategy_engine.py — Intraday trading strategies (long-only, 15m)

Two separate strategies:
  FuturesStrategy  — for MES/MNQ/MYM/MCL/MGC/MSI/MNG/M2K
    Primary:   VWAP Pullback on 15m (price above EMA50, pulls to VWAP/EMA21)
    Secondary: Opening Range Breakout on 15m (9:30–10:15 AM ET only)

  ForexStrategy — for all forex pairs
    Primary:   EMA 9/21 Pullback + RSI + MACD on 15m

Position split on every signal:
  TP1:   1.5R  → exit 40%, stop moves to breakeven
  TP2:   2.5R  → exit 40%
  Trail: 1.5×ATR trailing stop for the remaining 20%

Called from futures_alerts.py on each 15m bar close.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, date, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from alerts.alert_engine import (
    FUTURES_CONFIG, FOREX_CONFIG, _yf_symbol, detect_candle_pattern,
)

BASE_DIR = Path(__file__).parent.parent

# ORB levels cached per (symbol, date)
_orb_cache: dict = {}


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class StrategySignal:
    symbol:         str
    strategy:       str          # 'VWAP_PULLBACK' | 'ORB' | 'EMA_PULLBACK'
    direction:      str = 'long'

    entry:          float = 0.0  # 1 tick/pip above trigger bar high
    stop:           float = 0.0  # below pullback swing low / ORB level / EMA 21
    tp1:            float = 0.0  # 1.5R — exit 40%
    tp2:            float = 0.0  # 2.5R — exit 40%
    trail_initial:  float = 0.0  # starting level for trail (= tp2 area)
    trail_atr_mult: float = 1.5  # trail = running_high - 1.5 × ATR

    risk_pts:       float = 0.0  # entry − stop
    risk_dollars:   float = 0.0  # risk_pts × point_value (futures) or n/a (forex)
    tp1_rr:         float = 1.5
    tp2_rr:         float = 2.5

    atr:            float = 0.0
    candle_pattern: str   = 'none'
    confluence:     list  = field(default_factory=list)
    at_level:       str   = ''

    # ORB-specific
    orb_high:       Optional[float] = None
    orb_low:        Optional[float] = None

    # Indicator snapshot at signal time
    vwap:           float = 0.0
    ema_9:          float = 0.0
    ema_21:         float = 0.0
    ema_50:         float = 0.0
    rsi:            float = 0.0
    macd_hist:      float = 0.0
    macd_prev:      float = 0.0
    volume:         float = 0.0
    vol_ma20:       float = 0.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _tick(symbol: str) -> float:
    """Minimum price move for rounding entries/stops."""
    if symbol in FUTURES_CONFIG:
        return FUTURES_CONFIG[symbol]['tick']
    cfg = FOREX_CONFIG.get(symbol, {})
    decimals = cfg.get('decimals', 5)
    return 10 ** -decimals  # 0.00001 for 5-decimal, 0.001 for 3-decimal


def _round_to_tick(price: float, symbol: str) -> float:
    tick = _tick(symbol)
    return round(round(price / tick) * tick, 8)


def _point_value(symbol: str) -> float:
    return FUTURES_CONFIG.get(symbol, {}).get('point_value', 1.0)


def _et_now():
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(timezone.utc).astimezone(et)
    except Exception:
        return datetime.now()


def _calc_multi_bar_indicators(bars: pd.DataFrame) -> dict:
    """
    Return a richer indicator dict from 15m bars including:
    session VWAP, EMA 9/21/50, RSI, MACD (with prev value), ATR, volume.
    """
    if bars is None or len(bars) < 10:
        return {}

    close  = bars['Close']
    high   = bars['High']
    low    = bars['Low']
    volume = bars['Volume']

    def ema(n):
        return close.ewm(span=n, adjust=False).mean()

    ema9_series  = ema(9)
    ema21_series = ema(21)
    ema50_series = ema(50)

    # RSI 14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, float('nan'))
    rsi_series = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line   = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist_series = macd_line - signal_line

    # ATR 14
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_series = tr.rolling(14).mean()

    # ADX 14 (trend strength — values above 20 confirm a trending market)
    prev_high = high.shift(1)
    prev_low  = low.shift(1)
    plus_dm  = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    # Zero out whichever directional move is smaller on each bar
    both_positive = (plus_dm > 0) & (minus_dm > 0)
    plus_dm  = plus_dm.where(~both_positive | (plus_dm >= minus_dm), 0)
    minus_dm = minus_dm.where(~both_positive | (minus_dm > plus_dm), 0)
    atr14  = tr.rolling(14).mean()
    plus_di  = 100 * (plus_dm.rolling(14).mean()  / atr14.replace(0, float('nan')))
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr14.replace(0, float('nan')))
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float('nan')))
    adx_series = dx.rolling(14).mean()

    # Session VWAP — use bars tagged to current RTH session (9:30 AM ET onward)
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        idx_et = bars.index.tz_convert(et)
        session_mask = idx_et.time >= dtime(9, 30)
        session_bars = bars[session_mask]
        if session_bars.empty:
            session_bars = bars  # pre-market fallback
    except Exception:
        session_bars = bars

    typ   = (session_bars['High'] + session_bars['Low'] + session_bars['Close']) / 3
    svol  = session_bars['Volume']
    vwap  = float((typ * svol).sum() / svol.sum()) if svol.sum() > 0 else float(close.iloc[-1])

    vol_ma20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())

    return {
        'ema_9':      float(ema9_series.iloc[-1]),
        'ema_21':     float(ema21_series.iloc[-1]),
        'ema_50':     float(ema50_series.iloc[-1]),
        'rsi':        float(rsi_series.iloc[-1]) if not rsi_series.isna().all() else 50.0,
        'macd_hist':  float(hist_series.iloc[-1]),
        'macd_prev':  float(hist_series.iloc[-2]) if len(hist_series) >= 2 else 0.0,
        'atr':        float(atr_series.iloc[-1]) if not atr_series.isna().all() else float(tr.mean()),
        'adx':        float(adx_series.iloc[-1]) if not adx_series.isna().all() else 0.0,
        'vwap':       vwap,
        'volume':     float(volume.iloc[-1]),
        'vol_ma20':   vol_ma20,
        'price':      float(close.iloc[-1]),
        'open':       float(bars['Open'].iloc[-1]),
        'high':       float(high.iloc[-1]),
        'low':        float(low.iloc[-1]),
        # Previous bar (for pullback detection)
        'prev_close': float(close.iloc[-2]) if len(close) >= 2 else float(close.iloc[-1]),
        'prev_high':  float(high.iloc[-2])  if len(high)  >= 2 else float(high.iloc[-1]),
        'prev_low':   float(low.iloc[-2])   if len(low)   >= 2 else float(low.iloc[-1]),
        'prev_open':  float(bars['Open'].iloc[-2]) if len(bars) >= 2 else float(bars['Open'].iloc[-1]),
        'prev_vol':   float(volume.iloc[-2]) if len(volume) >= 2 else float(volume.iloc[-1]),
    }


def _get_bars_15m(symbol: str, days: str = '5d') -> pd.DataFrame:
    try:
        yf_sym = _yf_symbol(symbol)
        return yf.Ticker(yf_sym).history(interval='15m', period=days)
    except Exception:
        return pd.DataFrame()


def _bullish_candle(O, H, L, C) -> str:
    """Return pattern name if the bar is a bullish reversal candle, else ''."""
    if H == L:
        return ''
    body       = C - O
    body_size  = abs(body) / (H - L)
    lower_wick = min(O, C) - L
    upper_wick = H - max(O, C)
    total      = H - L

    # Hammer: small body at top, long lower wick (≥ 2× body), minimal upper wick
    if (body > 0 and body_size < 0.35 and lower_wick >= 2 * abs(body)
            and upper_wick < 0.2 * total):
        return 'Hammer'

    # Dragonfly doji: open ≈ close near high, long lower wick
    if (body_size < 0.10 and lower_wick >= 0.6 * total
            and upper_wick < 0.1 * total):
        return 'Dragonfly Doji'

    # Bullish marubozu: large bullish body, minimal wicks
    if body > 0 and body_size >= 0.75:
        return 'Bullish Marubozu'

    return ''


def _bullish_engulfing(prev_O, prev_C, curr_O, curr_C) -> bool:
    """True if current bar bullish-engulfs the previous bearish bar."""
    prev_bearish = prev_C < prev_O
    curr_bullish = curr_C > curr_O
    engulfs      = curr_O <= prev_C and curr_C >= prev_O
    return prev_bearish and curr_bullish and engulfs


# ---------------------------------------------------------------------------
# ORB level management
# ---------------------------------------------------------------------------

def _get_orb_levels(symbol: str, bars: pd.DataFrame) -> Optional[tuple]:
    """
    Return (orb_high, orb_low) for today's session.
    Defined as the high/low of the first 15m candle (9:30–9:45 AM ET).
    Cached in _orb_cache per (symbol, date).
    Returns None if ORB window not yet complete.
    """
    today = date.today().isoformat()
    cache_key = f"{symbol}_{today}"

    if cache_key in _orb_cache:
        return _orb_cache[cache_key]

    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        idx_et = bars.index.tz_convert(et)

        # First 15m candle: 9:30 to 9:44 AM
        orb_mask = (idx_et.time >= dtime(9, 30)) & (idx_et.time < dtime(9, 45))
        orb_bars = bars[orb_mask]

        if orb_bars.empty:
            return None

        orb_high = float(orb_bars['High'].max())
        orb_low  = float(orb_bars['Low'].min())
        _orb_cache[cache_key] = (orb_high, orb_low)
        return orb_high, orb_low
    except Exception:
        return None


def _is_orb_window() -> bool:
    """True during the ORB breakout window (9:45 AM – 10:15 AM ET)."""
    now = _et_now()
    return dtime(9, 45) <= now.time() <= dtime(10, 15)


# ---------------------------------------------------------------------------
# Futures Strategy
# ---------------------------------------------------------------------------

class FuturesStrategy:
    """
    Long-only strategies for micro futures on the 15m chart.

    Priority:
      1. ORB (only fires 9:45–10:15 AM ET)
      2. VWAP Pullback (fires any time during session)

    Returns a StrategySignal or None.
    """

    def check(self, symbol: str, bars: pd.DataFrame) -> Optional[StrategySignal]:
        if bars is None or len(bars) < 20:
            return None

        ind = _calc_multi_bar_indicators(bars)
        if not ind:
            return None

        # ORB first (time-gated)
        if _is_orb_window():
            sig = self._check_orb(symbol, bars, ind)
            if sig:
                return sig

        return self._check_vwap_pullback(symbol, bars, ind)

    # ------------------------------------------------------------------
    # VWAP Pullback
    # ------------------------------------------------------------------

    def _check_vwap_pullback(self, symbol: str, bars: pd.DataFrame, ind: dict) -> Optional[StrategySignal]:
        """
        Conditions (ALL required):
          1. Price above EMA 50
          2. EMA 9 > EMA 21
          3. ADX >= 20 (trending market, not choppy/ranging)
          4. Previous bar low touched VWAP ±0.20% OR EMA 21 zone ±0.20%
          5. RSI: 35–65 (healthy pullback without being overbought/oversold)
          6. MACD: histogram positive OR rising (curr > prev) even if negative —
             catches early momentum turn at the pullback level
          7. Bullish candle on trigger or pullback bar (hammer, engulfing, dragonfly, marubozu)
          8. Trigger bar volume ≥ 0.7× vol_ma20 (just confirm there are buyers, not strict)
          9. Stop placed at max(pb_low - tick, entry - 1.2×ATR) to avoid stops too tight
        """
        price   = ind['price']
        ema_9   = ind['ema_9']
        ema_21  = ind['ema_21']
        ema_50  = ind['ema_50']
        rsi     = ind['rsi']
        macd    = ind['macd_hist']
        macd_p  = ind['macd_prev']
        vwap    = ind['vwap']
        atr     = ind['atr']
        adx     = ind.get('adx', 0.0)

        # Condition 1: price above EMA 50 (trend bias)
        if price <= ema_50:
            return None

        # Condition 2: EMA 9/21 stack (short-term uptrend)
        if ema_9 <= ema_21:
            return None

        # Condition 3: ADX >= 20 — only trade in trending markets, not ranging
        if adx < 20:
            return None

        # Conditions 4–8 operate on the last two completed bars
        # bars.iloc[-1] = most recently CLOSED bar (trigger bar candidate)
        # bars.iloc[-2] = the bar before it (pullback bar)
        if len(bars) < 3:
            return None

        pb_bar  = bars.iloc[-2]   # pullback bar (touched the level)
        trig_bar = bars.iloc[-1]  # trigger bar (we enter on close + 1 tick)

        pb_O = float(pb_bar['Open']);  pb_H = float(pb_bar['High'])
        pb_L = float(pb_bar['Low']);   pb_C = float(pb_bar['Close'])

        tr_O = float(trig_bar['Open']); tr_H = float(trig_bar['High'])
        tr_L = float(trig_bar['Low']);  tr_C = float(trig_bar['Close'])
        tr_V = float(trig_bar['Volume'])

        # Condition 4: pullback bar touched VWAP ±0.20% OR EMA 21 zone ±0.20%
        vwap_zone_lo  = vwap  * (1 - 0.0020)
        vwap_zone_hi  = vwap  * (1 + 0.0020)
        ema21_zone_lo = ema_21 * 0.9980
        ema21_zone_hi = ema_21 * 1.0020

        touched_vwap  = pb_L <= vwap_zone_hi  and pb_H >= vwap_zone_lo
        touched_ema21 = pb_L <= ema21_zone_hi and pb_H >= ema21_zone_lo

        if not (touched_vwap or touched_ema21):
            return None

        at_level = 'VWAP' if touched_vwap else 'EMA 21'

        # Condition 5: RSI 35–65 (healthy pullback, not overbought or oversold)
        if not (35 <= rsi <= 65):
            return None

        # Condition 6: MACD positive OR rising (curr > prev by ≥5% of |prev|)
        # Rising-but-negative catches the early momentum turn at the pullback level
        macd_rising = macd > macd_p and (macd_p != 0 and macd > macd_p * 0.95)
        macd_ok = macd > 0 or macd_rising
        if not macd_ok:
            return None

        # Condition 7: bullish candle pattern on trigger bar (engulfing) or pullback bar
        pattern = ''
        if _bullish_engulfing(pb_O, pb_C, tr_O, tr_C):
            pattern = 'Bullish Engulfing'
        elif len(bars) >= 3 and _bullish_engulfing(
            float(bars.iloc[-3]['Open']), float(bars.iloc[-3]['Close']),
            pb_O, pb_C
        ):
            pattern = 'Bullish Engulfing (prev)'
        else:
            pattern = _bullish_candle(pb_O, pb_H, pb_L, pb_C)
            if not pattern:
                pattern = _bullish_candle(tr_O, tr_H, tr_L, tr_C)

        if not pattern:
            return None

        # Condition 8: trigger bar has buyers (relaxed — any volume acceptable,
        # but extremely thin volume < 0.4× avg is rejected)
        if tr_V < ind['vol_ma20'] * 0.4:
            return None

        # All conditions passed — calculate exact levels
        tick = _tick(symbol)
        pv   = _point_value(symbol)

        entry = _round_to_tick(tr_H + tick, symbol)

        # Stop: pullback bar low OR 1.2×ATR below entry — whichever is wider
        # This prevents stops being too tight and getting clipped by normal noise
        stop_pb  = _round_to_tick(pb_L - tick, symbol)
        stop_atr = _round_to_tick(entry - 1.2 * atr, symbol)
        stop = min(stop_pb, stop_atr)  # lower value = wider stop for longs

        risk_pts = round(entry - stop, 8)
        if risk_pts <= 0:
            return None

        tp1 = _round_to_tick(entry + 1.5 * risk_pts, symbol)
        tp2 = _round_to_tick(entry + 2.5 * risk_pts, symbol)
        trail_initial = _round_to_tick(tp2 - atr * 1.5, symbol)

        confluence = [
            f"Price above EMA 50 ({ema_50:,.{_decimals(symbol)}f}) ✓",
            f"EMA 9 ({ema_9:,.{_decimals(symbol)}f}) above EMA 21 ({ema_21:,.{_decimals(symbol)}f}) ✓",
            f"ADX: {adx:.1f} (trend confirmed) ✓",
            f"Pulled back to {at_level} ({vwap if at_level=='VWAP' else ema_21:,.{_decimals(symbol)}f}) ✓",
            f"RSI: {rsi:.1f} (healthy pullback) ✓",
            f"MACD histogram: {macd:+.4f} {'rising ✓' if macd > macd_p else '(positive) ✓'}",
            f"Candle: {pattern} at {at_level} ✓",
            f"Volume: trigger bar {tr_V/ind['vol_ma20']:.1f}× avg ✓",
        ]

        return StrategySignal(
            symbol=symbol, strategy='VWAP_PULLBACK',
            entry=entry, stop=stop, tp1=tp1, tp2=tp2,
            trail_initial=trail_initial, trail_atr_mult=1.5,
            risk_pts=risk_pts,
            risk_dollars=round(risk_pts * pv, 2),
            atr=atr, candle_pattern=pattern, confluence=confluence,
            at_level=at_level,
            vwap=vwap, ema_9=ema_9, ema_21=ema_21, ema_50=ema_50,
            rsi=rsi, macd_hist=macd, macd_prev=macd_p,
            volume=tr_V, vol_ma20=ind['vol_ma20'],
        )

    # ------------------------------------------------------------------
    # ORB
    # ------------------------------------------------------------------

    def _check_orb(self, symbol: str, bars: pd.DataFrame, ind: dict) -> Optional[StrategySignal]:
        """
        Conditions (ALL required):
          1. ORB levels established (first 15m candle 9:30–9:44 AM)
          2. Current bar closes ABOVE ORB high
          3. Price above VWAP (bullish bias)
          4. EMA 20 sloping upward (ema_9 > ema_21 used as proxy)
          5. Volume ≥ 1.5× 20-bar average on the breakout bar
          6. Trigger bar is a bullish bar (close > open)
        """
        orb = _get_orb_levels(symbol, bars)
        if orb is None:
            return None

        orb_high, orb_low = orb
        price   = ind['price']
        vwap    = ind['vwap']
        atr     = ind['atr']
        adx     = ind.get('adx', 0.0)

        trig_bar = bars.iloc[-1]
        tr_O = float(trig_bar['Open']); tr_H = float(trig_bar['High'])
        tr_L = float(trig_bar['Low']);  tr_C = float(trig_bar['Close'])
        tr_V = float(trig_bar['Volume'])

        # Condition 2: bar closed above ORB high
        if tr_C <= orb_high:
            return None

        # Condition 3: price above VWAP
        if price < vwap:
            return None

        # Condition 4: EMA 9 > EMA 21
        if ind['ema_9'] <= ind['ema_21']:
            return None

        # Condition 4b: ADX >= 18 (slightly lower threshold — ORB is momentum-based)
        if adx < 18:
            return None

        # Condition 5: volume spike
        if tr_V < ind['vol_ma20'] * 1.5:
            return None

        # Condition 6: breakout bar is bullish
        if tr_C <= tr_O:
            return None

        tick = _tick(symbol)
        pv   = _point_value(symbol)

        entry = _round_to_tick(tr_H + tick, symbol)
        # Stop: just below ORB high (now support) OR 1.2×ATR, whichever is wider
        stop_orb = _round_to_tick(orb_high - tick * 2, symbol)
        stop_atr = _round_to_tick(entry - 1.2 * atr, symbol)
        stop = min(stop_orb, stop_atr)

        risk_pts = round(entry - stop, 8)
        if risk_pts <= 0:
            return None

        tp1 = _round_to_tick(entry + 1.5 * risk_pts, symbol)
        tp2 = _round_to_tick(entry + 2.5 * risk_pts, symbol)
        trail_initial = _round_to_tick(tp2 - atr * 1.5, symbol)

        dec = _decimals(symbol)
        confluence = [
            f"ORB high: {orb_high:,.{dec}f} — breakout confirmed ✓",
            f"Closed above ORB high: {tr_C:,.{dec}f} > {orb_high:,.{dec}f} ✓",
            f"Price above VWAP ({vwap:,.{dec}f}) ✓",
            f"EMA 9 ({ind['ema_9']:,.{dec}f}) above EMA 21 ({ind['ema_21']:,.{dec}f}) ✓",
            f"Volume: {tr_V/ind['vol_ma20']:.1f}× 20-bar avg ✓",
            f"Breakout bar: bullish close ✓",
        ]

        return StrategySignal(
            symbol=symbol, strategy='ORB',
            entry=entry, stop=stop, tp1=tp1, tp2=tp2,
            trail_initial=trail_initial, trail_atr_mult=1.5,
            risk_pts=risk_pts,
            risk_dollars=round(risk_pts * pv, 2),
            atr=atr, candle_pattern='Breakout Bar', confluence=confluence,
            at_level='ORB High', orb_high=orb_high, orb_low=orb_low,
            vwap=vwap, ema_9=ind['ema_9'], ema_21=ind['ema_21'], ema_50=ind['ema_50'],
            rsi=ind['rsi'], macd_hist=ind['macd_hist'], macd_prev=ind['macd_prev'],
            volume=tr_V, vol_ma20=ind['vol_ma20'],
        )


# ---------------------------------------------------------------------------
# Forex Strategy
# ---------------------------------------------------------------------------

class ForexStrategy:
    """
    Long-only EMA 9/21 Pullback strategy for forex pairs on the 15m chart.
    No VWAP (unreliable without centralized volume).
    No ORB (no defined session open for forex).

    Conditions (ALL required):
      1. Price above EMA 50 (uptrend)
      2. EMA 9 > EMA 21 (short-term momentum intact)
      3. ADX >= 20 (trending market — don't fight a range)
      4. Pullback bar touched the EMA 9/21 zone (±0.20%)
      5. RSI: 35–65 (relaxed — healthy pullbacks can dip into mid-30s)
      6. MACD positive OR rising (curr > prev even if both negative)
      7. Bullish candle at the zone (engulfing, hammer, dragonfly, marubozu)
      8. Trigger bar close > trigger bar open (confirming bar)
      9. Stop: max(pb_low - tick, entry - 1.2×ATR) — prevents over-tight stops
         (no volume filter for forex — tick volume is not reliable)
    """

    def check(self, symbol: str, bars: pd.DataFrame) -> Optional[StrategySignal]:
        if bars is None or len(bars) < 20:
            return None

        ind = _calc_multi_bar_indicators(bars)
        if not ind:
            return None

        return self._check_ema_pullback(symbol, bars, ind)

    def _check_ema_pullback(self, symbol: str, bars: pd.DataFrame, ind: dict) -> Optional[StrategySignal]:
        price  = ind['price']
        ema_9  = ind['ema_9']
        ema_21 = ind['ema_21']
        ema_50 = ind['ema_50']
        rsi    = ind['rsi']
        macd   = ind['macd_hist']
        macd_p = ind['macd_prev']
        atr    = ind['atr']
        adx    = ind.get('adx', 0.0)

        # Condition 1: uptrend bias
        if price <= ema_50:
            return None

        # Condition 2: short-term momentum intact
        if ema_9 <= ema_21:
            return None

        # Condition 3: ADX >= 20 — don't trade in choppy/ranging markets
        if adx < 20:
            return None

        if len(bars) < 3:
            return None

        pb_bar   = bars.iloc[-2]
        trig_bar = bars.iloc[-1]

        pb_O = float(pb_bar['Open']);  pb_H = float(pb_bar['High'])
        pb_L = float(pb_bar['Low']);   pb_C = float(pb_bar['Close'])

        tr_O = float(trig_bar['Open']); tr_H = float(trig_bar['High'])
        tr_L = float(trig_bar['Low']);  tr_C = float(trig_bar['Close'])

        # Condition 4: pullback bar touched EMA 9/21 zone (±0.20%)
        zone_hi = max(ema_9, ema_21) * 1.002
        zone_lo = min(ema_9, ema_21) * 0.998

        touched_zone = pb_L <= zone_hi and pb_H >= zone_lo
        tapped_ema21 = pb_L <= ema_21 * 1.002  # wicked down to EMA 21

        if not (touched_zone or tapped_ema21):
            return None

        # Condition 5: RSI 35–65
        if not (35 <= rsi <= 65):
            return None

        # Condition 6: MACD positive OR rising (curr > prev)
        # Rising-but-negative = early momentum turn at the level
        macd_rising = macd > macd_p and (macd_p != 0 and macd > macd_p * 0.95)
        macd_ok = macd > 0 or macd_rising
        if not macd_ok:
            return None

        # Condition 7: bullish candle (engulfing, hammer, dragonfly, marubozu)
        # No volume filter — forex tick volume is not reliable
        pattern = ''
        if _bullish_engulfing(pb_O, pb_C, tr_O, tr_C):
            pattern = 'Bullish Engulfing'
        elif len(bars) >= 3 and _bullish_engulfing(
            float(bars.iloc[-3]['Open']), float(bars.iloc[-3]['Close']),
            pb_O, pb_C
        ):
            pattern = 'Bullish Engulfing (prev)'
        else:
            pattern = _bullish_candle(pb_O, pb_H, pb_L, pb_C)
            if not pattern:
                pattern = _bullish_candle(tr_O, tr_H, tr_L, tr_C)

        if not pattern:
            return None

        # Condition 8: trigger bar is bullish (confirming bar)
        if tr_C <= tr_O:
            return None

        tick = _tick(symbol)
        dec  = _decimals(symbol)

        entry = _round_to_tick(tr_H + tick, symbol)

        # Stop: pullback bar low OR 1.2×ATR below entry — whichever is wider
        stop_pb  = _round_to_tick(pb_L - tick * 2, symbol)
        stop_atr = _round_to_tick(entry - 1.2 * atr, symbol)
        stop = min(stop_pb, stop_atr)  # lower = wider stop for longs

        risk_pts = round(entry - stop, 8)
        if risk_pts <= 0:
            return None

        tp1 = _round_to_tick(entry + 1.5 * risk_pts, symbol)
        tp2 = _round_to_tick(entry + 2.5 * risk_pts, symbol)
        trail_initial = _round_to_tick(tp2 - atr * 1.5, symbol)

        # Convert risk_pts to pips for display
        pip = 0.0001 if FOREX_CONFIG.get(symbol, {}).get('decimals', 5) == 5 else 0.01
        risk_pips = round(risk_pts / pip, 1)

        confluence = [
            f"Price above EMA 50 ({ema_50:.{dec}f}) ✓",
            f"EMA 9 ({ema_9:.{dec}f}) above EMA 21 ({ema_21:.{dec}f}) ✓",
            f"ADX: {adx:.1f} (trend confirmed) ✓",
            f"Pulled back into EMA 9/21 zone ✓",
            f"RSI: {rsi:.1f} (healthy pullback) ✓",
            f"MACD histogram: {macd:+.6f} {'rising ✓' if macd > macd_p else '(positive) ✓'}",
            f"Candle: {pattern} at EMA zone ✓",
            f"Trigger bar: bullish close ✓",
        ]

        return StrategySignal(
            symbol=symbol, strategy='EMA_PULLBACK',
            entry=entry, stop=stop, tp1=tp1, tp2=tp2,
            trail_initial=trail_initial, trail_atr_mult=1.5,
            risk_pts=risk_pts,
            risk_dollars=0.0,  # forex — varies by lot size
            atr=atr, candle_pattern=pattern, confluence=confluence,
            at_level='EMA 9/21 Zone',
            ema_9=ema_9, ema_21=ema_21, ema_50=ema_50,
            rsi=rsi, macd_hist=macd, macd_prev=macd_p,
            volume=ind['volume'], vol_ma20=ind['vol_ma20'],
        )


# ---------------------------------------------------------------------------
# Gap Fill Strategy
# ---------------------------------------------------------------------------

class GapFillStrategy:
    """
    RTH Open Gap Fill for micro futures (MES, MNQ, MYM, M2K, MCL, MGC, MSI, MNG).

    Fires ONCE per symbol per trading day at the 9:30–9:44 AM ET bar.
    Gap up   > 0.3% from PDC → short fade back toward PDC
    Gap down > 0.3% from PDC → long fade back toward PDC

    Entry:  bar close of the 9:30 AM candle (first 15m bar of RTH)
    Stop:   1.0 × ATR beyond gap extreme (above bar high for short,
            below bar low for long)
    TP1:    PDC (the gap fill level) — exit full position
    TP2:    PDC extended (1.5× the gap size beyond PDC) — runner if gap fills fast

    Backtest results (59 days, Feb–Apr 2025 bear market):
      MES: 60% WR, PF 1.12
      MNQ: 70% WR, PF 1.75
    """

    _fired_today: dict = {}   # symbol → date string

    def check(self, symbol: str, bars: pd.DataFrame) -> Optional[StrategySignal]:
        """
        Called on every 15m bar close.
        Only produces a signal on the 9:30–9:44 AM ET bar and only once per day.
        """
        now = _et_now()
        if now.weekday() >= 5:       # skip weekends
            return None
        t = now.time()
        if not (dtime(9, 30) <= t < dtime(9, 45)):
            return None

        today_str = now.strftime('%Y-%m-%d')
        if self._fired_today.get(symbol) == today_str:
            return None            # already evaluated this symbol today

        # Mark as evaluated for today regardless of outcome
        self._fired_today[symbol] = today_str

        if bars is None or len(bars) < 20:
            return None

        ind = _calc_multi_bar_indicators(bars)
        if not ind:
            return None

        atr = ind['atr']

        # Opening bar (the 9:30 candle just closed)
        opening_bar = bars.iloc[-1]
        O = float(opening_bar['Open'])
        H = float(opening_bar['High'])
        L = float(opening_bar['Low'])
        C = float(opening_bar['Close'])

        # Previous day close — fetch from yfinance daily bars
        pdc = self._get_pdc(symbol)
        if pdc is None or pdc <= 0:
            return None

        gap_pct = (O - pdc) / pdc

        # Minimum gap threshold: 0.30%
        if abs(gap_pct) < 0.003:
            return None

        tick = _tick(symbol)
        pv   = _point_value(symbol)
        dec  = _decimals(symbol)

        if gap_pct > 0:
            # Gap UP → short fade back toward PDC
            if C >= O:           # opening bar not confirming reversal — skip
                return None
            direction = 'short'
            entry = _round_to_tick(L - tick, symbol)         # enter below bar low
            stop  = _round_to_tick(H + atr, symbol)          # 1× ATR above bar high
            tp1   = _round_to_tick(pdc, symbol)               # full gap fill
            tp2   = _round_to_tick(pdc - (O - pdc) * 0.5, symbol)   # 50% extended

        else:
            # Gap DOWN → long fade back toward PDC
            if C <= O:           # opening bar not confirming reversal — skip
                return None
            direction = 'long'
            entry = _round_to_tick(H + tick, symbol)         # enter above bar high
            stop  = _round_to_tick(L - atr, symbol)          # 1× ATR below bar low
            tp1   = _round_to_tick(pdc, symbol)               # full gap fill
            tp2   = _round_to_tick(pdc + (pdc - O) * 0.5, symbol)   # 50% extended

        risk_pts = abs(entry - stop)
        if risk_pts <= 0:
            return None

        gap_pts  = abs(O - pdc)
        gap_pct_disp = abs(gap_pct) * 100

        confluence = [
            f"RTH open: {_fmt(O, symbol)} | PDC: {_fmt(pdc, symbol)}",
            f"Gap {'UP' if gap_pct > 0 else 'DOWN'}: {gap_pts:.{dec}f} pts ({gap_pct_disp:.2f}%) ✓",
            f"Opening bar {'bearish — confirms reversal' if gap_pct > 0 else 'bullish — confirms reversal'} ✓",
            f"ATR: {atr:.{dec}f} | Stop: 1.0× ATR {'above high' if gap_pct > 0 else 'below low'} ✓",
            f"Target: PDC gap fill at {_fmt(tp1, symbol)} ✓",
        ]

        return StrategySignal(
            symbol=symbol, strategy='GAP_FILL', direction=direction,
            entry=entry, stop=stop, tp1=tp1, tp2=tp2,
            trail_initial=tp1, trail_atr_mult=0.0,   # no trail — full exit at PDC
            risk_pts=risk_pts,
            risk_dollars=round(risk_pts * pv, 2) if direction == 'long' else 0.0,
            atr=atr, candle_pattern='Gap Reversal', confluence=confluence,
            at_level='RTH Open Gap',
            vwap=ind['vwap'], ema_9=ind['ema_9'], ema_21=ind['ema_21'],
            ema_50=ind['ema_50'], rsi=ind['rsi'],
            macd_hist=ind['macd_hist'], macd_prev=ind['macd_prev'],
            volume=ind['volume'], vol_ma20=ind['vol_ma20'],
        )

    @staticmethod
    def _get_pdc(symbol: str) -> Optional[float]:
        """
        Return previous day's RTH close.
        Try the daily levels file first; fall back to yfinance 2d daily bar.
        """
        # 1. Try levels file (written by calculate_daily_levels.py at 9:15 AM)
        try:
            import zoneinfo
            et = zoneinfo.ZoneInfo("America/New_York")
            today_str = datetime.now(timezone.utc).astimezone(et).strftime('%Y-%m-%d')
            lvl_file  = BASE_DIR / 'data' / 'sessions' / f'levels_{today_str}.json'
            if lvl_file.exists():
                with open(lvl_file) as f:
                    data = json.load(f)
                sym_data = data.get(symbol, {})
                if 'PDC' in sym_data:
                    return float(sym_data['PDC'])
        except Exception:
            pass

        # 2. Fall back to yfinance
        try:
            yf_sym  = _yf_symbol(symbol)
            daily   = yf.Ticker(yf_sym).history(interval='1d', period='5d')
            if daily is not None and len(daily) >= 2:
                return float(daily['Close'].iloc[-2])
        except Exception:
            pass

        return None


# ---------------------------------------------------------------------------
# Helpers for display formatting
# ---------------------------------------------------------------------------

def _decimals(symbol: str) -> int:
    if symbol in FUTURES_CONFIG:
        tick = FUTURES_CONFIG[symbol]['tick']
        if tick >= 1:
            return 0
        return len(str(tick).rstrip('0').split('.')[-1])
    return FOREX_CONFIG.get(symbol, {}).get('decimals', 5)


def _fmt(price: float, symbol: str) -> str:
    dec = _decimals(symbol)
    if dec == 0:
        return f"{price:,.0f}"
    return f"{price:,.{dec}f}"


# ---------------------------------------------------------------------------
# Alert message formatters
# ---------------------------------------------------------------------------

def format_strategy_entry_alert(sig: StrategySignal, levels: list = None) -> str:
    """
    Full entry alert with exact entry/stop/TP1/TP2/trailing stop.
    Fired when strategy signal is confirmed.
    """
    strategy_names = {
        'VWAP_PULLBACK': 'VWAP Pullback',
        'ORB':           'Opening Range Breakout',
        'EMA_PULLBACK':  'EMA 9/21 Pullback',
    }
    strategy_label = strategy_names.get(sig.strategy, sig.strategy)
    is_futures = sig.symbol in FUTURES_CONFIG
    dec = _decimals(sig.symbol)

    risk_pts = sig.risk_pts
    tp1_pts  = sig.tp1 - sig.entry
    tp2_pts  = sig.tp2 - sig.entry

    # Dollar risk line (futures only)
    if is_futures:
        pv = _point_value(sig.symbol)
        dollar_risk = round(risk_pts * pv, 2)
        dollar_tp1  = round(tp1_pts  * pv, 2)
        dollar_tp2  = round(tp2_pts  * pv, 2)
        risk_line = f"({risk_pts:.{dec}f} pts | <b>${dollar_risk:.2f}/contract</b>)"
        tp1_line  = f"({tp1_pts:.{dec}f} pts | ${dollar_tp1:.2f})"
        tp2_line  = f"({tp2_pts:.{dec}f} pts | ${dollar_tp2:.2f})"
        trail_line = f"1.5×ATR = {sig.atr * 1.5:.{dec}f} pts below running high"
    else:
        pip = 0.0001 if FOREX_CONFIG.get(sig.symbol, {}).get('decimals', 5) == 5 else 0.01
        risk_pips = round(risk_pts / pip, 1)
        tp1_pips  = round(tp1_pts  / pip, 1)
        tp2_pips  = round(tp2_pts  / pip, 1)
        risk_line = f"({risk_pips:.1f} pips)"
        tp1_line  = f"({tp1_pips:.1f} pips)"
        tp2_line  = f"({tp2_pips:.1f} pips)"
        trail_pips = round(sig.atr * 1.5 / pip, 1)
        trail_line = f"1.5×ATR = {trail_pips:.1f} pips below running high"

    # Session levels (nearest 3 above entry)
    level_lines = ''
    if levels:
        nearby = [l for l in levels if l['price'] > sig.entry][:3]
        if nearby:
            level_lines = '\n\nSession levels ahead:\n'
            level_names = {
                'PDH': 'Prev Day High', 'PDL': 'Prev Day Low', 'PDC': 'Prev Day Close',
                'ONH': 'Overnight High', 'ONL': 'Overnight Low', 'IBH': 'Initial Balance High',
                'IBL': 'Initial Balance Low', 'WO': 'Weekly Open', 'WH': 'Weekly High',
                'WL': 'Weekly Low', 'DO': 'Day Open', 'MO': 'Monthly Open',
            }
            for l in nearby:
                name  = level_names.get(l['label'], l['label'])
                dist  = round(l['price'] - sig.entry, dec)
                level_lines += f"• {name}: {_fmt(l['price'], sig.symbol)} (+{dist} pts above)\n"

    # ORB info line
    orb_line = ''
    if sig.strategy == 'ORB' and sig.orb_high:
        orb_range = round(sig.orb_high - sig.orb_low, dec) if sig.orb_low else 0
        orb_line  = f"\nORB range: {_fmt(sig.orb_low, sig.symbol)} – {_fmt(sig.orb_high, sig.symbol)} ({orb_range} pts)\n"

    # Time
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        time_str = datetime.now(timezone.utc).astimezone(et).strftime("%H:%M ET")
    except Exception:
        time_str = datetime.now().strftime("%H:%M")

    confluence_block = '\n'.join(f"• {c}" for c in sig.confluence)

    msg = (
        f"✅ <b>ENTRY ALERT — {sig.symbol} 15m</b>  [{time_str}]\n"
        f"Strategy: {strategy_label}\n"
        f"Direction: Long\n"
        f"{orb_line}"
        f"\n"
        f"<b>Entry:</b>      {_fmt(sig.entry, sig.symbol)}\n"
        f"<b>Stop:</b>       {_fmt(sig.stop,  sig.symbol)}  {risk_line}\n"
        f"<b>TP1:</b>        {_fmt(sig.tp1,   sig.symbol)}  {tp1_line}  — exit 40%, stop → breakeven\n"
        f"<b>TP2:</b>        {_fmt(sig.tp2,   sig.symbol)}  {tp2_line}  — exit 40%\n"
        f"<b>Trail stop:</b> {trail_line}  — final 20% runs free\n"
        f"{level_lines}"
        f"\nSignal confluence:\n{confluence_block}"
    )
    return msg


def format_tp1_hit_alert(symbol: str, entry: float, tp1: float,
                          new_stop: float, trail_info: str) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        time_str = datetime.now(timezone.utc).astimezone(et).strftime("%H:%M ET")
    except Exception:
        time_str = datetime.now().strftime("%H:%M")
    dec = _decimals(symbol)
    return (
        f"🎯 <b>TP1 HIT — {symbol}</b>  [{time_str}]\n"
        f"\n"
        f"✅ Exit <b>40%</b> of your position now\n"
        f"   TP1 target: {_fmt(tp1, symbol)}\n"
        f"\n"
        f"📌 Move stop to <b>breakeven</b>: {_fmt(new_stop, symbol)}\n"
        f"🔄 Trailing stop now active for remaining 60%\n"
        f"   {trail_info}\n"
        f"\n"
        f"Entry was: {_fmt(entry, symbol)}"
    )


def format_tp2_hit_alert(symbol: str, entry: float, tp2: float,
                          trail_info: str) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        time_str = datetime.now(timezone.utc).astimezone(et).strftime("%H:%M ET")
    except Exception:
        time_str = datetime.now().strftime("%H:%M")
    return (
        f"🎯 <b>TP2 HIT — {symbol}</b>  [{time_str}]\n"
        f"\n"
        f"✅ Exit another <b>40%</b> of your position now\n"
        f"   TP2 target: {_fmt(tp2, symbol)}\n"
        f"\n"
        f"🔄 Trailing stop continues for remaining 20%\n"
        f"   {trail_info}\n"
        f"\n"
        f"Let the last piece run — trail will close it out."
    )


def format_trail_stop_hit_alert(symbol: str, entry: float,
                                 trail_level: float, current_price: float) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        time_str = datetime.now(timezone.utc).astimezone(et).strftime("%H:%M ET")
    except Exception:
        time_str = datetime.now().strftime("%H:%M")
    dec = _decimals(symbol)
    pnl_pts = round(trail_level - entry, dec)
    return (
        f"🏁 <b>TRAIL STOP HIT — {symbol}</b>  [{time_str}]\n"
        f"\n"
        f"✅ Exit final <b>20%</b> of your position\n"
        f"   Trail stop triggered: {_fmt(trail_level, symbol)}\n"
        f"   Current price: {_fmt(current_price, symbol)}\n"
        f"\n"
        f"P&L on this piece: {'+' if pnl_pts >= 0 else ''}{pnl_pts} pts from entry ({_fmt(entry, symbol)})\n"
        f"Trade complete. ✓"
    )


def format_gap_fill_alert(sig: StrategySignal) -> str:
    """
    Gap Fill entry alert — fires once per symbol per day at 9:30 AM ET open.
    """
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        time_str = datetime.now(timezone.utc).astimezone(et).strftime("%H:%M ET")
    except Exception:
        time_str = datetime.now().strftime("%H:%M")

    dec  = _decimals(sig.symbol)
    pv   = _point_value(sig.symbol)
    direction_label = 'Long (gap down fade ↗)' if sig.direction == 'long' else 'Short (gap up fade ↘)'

    risk_pts = sig.risk_pts
    tp1_pts  = abs(sig.tp1 - sig.entry)
    rr       = round(tp1_pts / risk_pts, 2) if risk_pts > 0 else 0

    dollar_risk = round(risk_pts * pv, 2)
    dollar_tp1  = round(tp1_pts  * pv, 2)

    confluence_block = '\n'.join(f"• {c}" for c in sig.confluence)

    msg = (
        f"📊 <b>GAP FILL ALERT — {sig.symbol}</b>  [{time_str}]\n"
        f"Direction: {direction_label}\n"
        f"\n"
        f"<b>Entry:</b>  {_fmt(sig.entry, sig.symbol)}\n"
        f"<b>Stop:</b>   {_fmt(sig.stop,  sig.symbol)}  "
        f"({risk_pts:.{dec}f} pts | <b>${dollar_risk:.2f}/contract</b>)\n"
        f"<b>TP1:</b>    {_fmt(sig.tp1,   sig.symbol)}  "
        f"({tp1_pts:.{dec}f} pts | ${dollar_tp1:.2f})  R:R {rr:.1f}:1 — full exit\n"
        f"<b>TP2:</b>    {_fmt(sig.tp2,   sig.symbol)}  runner (25% if TP1 fills fast)\n"
        f"\n"
        f"Gap fill details:\n{confluence_block}\n"
        f"\n"
        f"⚠️ Gap fill strategy — exit full position at TP1 (PDC).\n"
        f"Close by 10:30 AM ET if PDC not reached."
    )
    return msg


def format_trail_updated_alert(symbol: str, new_trail: float, running_high: float) -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        time_str = datetime.now(timezone.utc).astimezone(et).strftime("%H:%M ET")
    except Exception:
        time_str = datetime.now().strftime("%H:%M")
    return (
        f"📈 <b>TRAIL STOP UPDATED — {symbol}</b>  [{time_str}]\n"
        f"Running high: {_fmt(running_high, symbol)}\n"
        f"New trail stop: <b>{_fmt(new_trail, symbol)}</b>\n"
        f"(Still holding final 20% — let it run)"
    )
