"""
futures_alerts.py — Main futures monitoring loop

Monitors MES, MNQ, MYM, MCL, MGC, MSI, MNG, M2K every 60 seconds
during 6 AM – 5 PM ET, Mon–Fri. Fires Telegram + terminal alerts when
setup or entry conditions are met.

Usage:
    python alerts/futures_alerts.py           # start loop
    python alerts/futures_alerts.py --test MES   # single check, no Telegram
    python alerts/futures_alerts.py --test ALL   # all symbols, no Telegram
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from providers import tradingview_provider
from alerts.notifier import fire_alert, get_recent_alerts
from alerts.alert_engine import (
    FUTURES_CONFIG, FOREX_CONFIG,
    get_live_indicators,
    calculate_score,
    get_key_levels,
    detect_candle_pattern,
    check_setup_conditions,
    check_entry_conditions,
    format_alert_message,
    get_current_price,
    _yf_symbol,
)
from alerts.strategy_engine import (
    FuturesStrategy, ForexStrategy, GapFillStrategy,
    format_strategy_entry_alert,
    format_gap_fill_alert,
    format_tp1_hit_alert,
    format_tp2_hit_alert,
    format_trail_stop_hit_alert,
    format_trail_updated_alert,
    _get_bars_15m,
    _fmt,
    _decimals,
)
from brokers.oanda_client import get_client as get_oanda_client
from brokers.tradovate_client import get_client as get_tradovate_client

# Strategy instances (shared across loop iterations)
_futures_strategy  = FuturesStrategy()
_forex_strategy    = ForexStrategy()
_gap_fill_strategy = GapFillStrategy()

# Broker clients
_oanda    = get_oanda_client()
_tradovate = get_tradovate_client()

BASE_DIR  = Path(__file__).parent.parent
LOG_FILE  = BASE_DIR / "data" / "alerts_log.json"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# TradingView MCP health check
# ---------------------------------------------------------------------------

TV_CONNECTED = False

def tv_health_check() -> bool:
    """Check if TradingView MCP is reachable."""
    global TV_CONNECTED
    TV_CONNECTED = tradingview_provider.is_connected()

    if TV_CONNECTED:
        print("[futures_alerts] TradingView MCP connected — using live data")
    else:
        print("[futures_alerts] TradingView not connected — falling back to yfinance")
    return TV_CONNECTED


# ---------------------------------------------------------------------------
# Market hours helpers
# ---------------------------------------------------------------------------

def _et_now():
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(timezone.utc).astimezone(et)
    except Exception:
        return datetime.now()


def _is_market_hours() -> bool:
    """Futures: 6 AM – 5 PM ET, Mon–Fri."""
    now = _et_now()
    if now.weekday() >= 5:
        return False
    return 6 <= now.hour < 17


def _is_forex_market_open() -> bool:
    """
    Forex trades 24/5: Sunday 5 PM ET through Friday 5 PM ET.
    Returns False only on Saturday (all day) and Sunday before 5 PM ET.
    """
    now = _et_now()
    weekday = now.weekday()   # 0=Mon … 6=Sun
    if weekday == 5:          # Saturday — fully closed
        return False
    if weekday == 6 and now.hour < 17:   # Sunday before 5 PM ET — not yet open
        return False
    if weekday == 4 and now.hour >= 17:  # Friday after 5 PM ET — weekend starts
        return False
    return True


# Session windows (ET) → which forex pairs are most active
_ASIAN_PAIRS = {
    'USDJPY', 'AUDUSD', 'NZDUSD', 'AUDJPY', 'NZDJPY',
    'AUDCAD', 'AUDNZD', 'CADJPY', 'NZDCAD',
}
_LONDON_PAIRS = {
    'EURUSD', 'GBPUSD', 'USDCHF', 'USDCAD',
    'EURGBP', 'EURJPY', 'GBPJPY', 'EURCHF', 'GBPAUD',
}
_NY_PAIRS = {
    'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD',
    'NZDUSD', 'USDCAD', 'EURGBP', 'EURJPY', 'GBPJPY',
    'EURCHF', 'AUDCAD', 'CADJPY', 'AUDNZD', 'NZDJPY',
    'GBPAUD', 'NZDCAD',
}

def _active_forex_pairs() -> list:
    """
    Return the forex pairs that are most active in the current session.
    Pairs in multiple sessions are included whenever any session is open.

    Sessions (ET):
      Asian   7 PM – 4 AM
      London  3 AM – 12 PM
      NY      8 AM – 5 PM
    """
    now = _et_now()
    h   = now.hour
    active = set()

    # Asian: 19:00–04:00 ET (spans midnight)
    if h >= 19 or h < 4:
        active |= _ASIAN_PAIRS

    # London: 03:00–12:00 ET
    if 3 <= h < 12:
        active |= _LONDON_PAIRS

    # NY: 08:00–17:00 ET
    if 8 <= h < 17:
        active |= _NY_PAIRS

    # If between sessions (4–7 AM ET: Asian closed, London not yet open)
    # still monitor all majors at reduced activity — include them anyway
    if not active:
        active = set(FOREX_CONFIG.keys())

    return [p for p in FOREX_CONFIG if p in active]


def _is_near_close() -> bool:
    """True if within 15 minutes of 4:45 PM ET (RTH close buffer)."""
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
        close_buf = now.replace(hour=16, minute=45, second=0, microsecond=0)
        return timedelta(0) <= (close_buf - now) <= timedelta(minutes=15)
    except Exception:
        return False


def _et_time_str() -> str:
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        return datetime.now(timezone.utc).astimezone(et).strftime("%H:%M")
    except Exception:
        return datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Recent alert de-dupe
# ---------------------------------------------------------------------------

def _already_alerted(symbol: str, alert_type: str, hours: float = 4.0) -> bool:
    recent = get_recent_alerts(symbol=symbol, hours=hours)
    return any(a.get('type') == alert_type for a in recent)


# ---------------------------------------------------------------------------
# Fetch bars
# ---------------------------------------------------------------------------

def _get_bars(symbol: str, interval: str, period: str) -> pd.DataFrame:
    try:
        yf_sym = _yf_symbol(symbol)
        return yf.Ticker(yf_sym).history(interval=interval, period=period)
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Build reasons + warnings for alert message
# ---------------------------------------------------------------------------

def _build_reasons(score: dict, indicators: dict, pattern: dict) -> list:
    reasons = []
    cot_labels = {2: "COT: Institutions net long + adding longs",
                  1: "COT: Institutions net long",
                 -1: "COT: Institutions net short",
                 -2: "COT: Institutions net short + adding shorts",
                  0: "COT: Neutral / not available"}
    reasons.append(cot_labels.get(score.get('COT', 0), "COT: Neutral"))

    ema_50  = indicators.get('ema_50',  0)
    ema_200 = indicators.get('ema_200', 0)
    price   = indicators.get('price',   0)
    if ema_50 and ema_200 and price:
        if price > ema_50 and price > ema_200:
            reasons.append(f"MA: Price above 50 EMA ({ema_50:,.2f}) and 200 EMA ({ema_200:,.2f})")
        elif price < ema_50 and price < ema_200:
            reasons.append(f"MA: Price below 50 EMA ({ema_50:,.2f}) and 200 EMA ({ema_200:,.2f})")

    rsi = indicators.get('rsi', 50)
    if rsi:
        reasons.append(f"RSI 14: {rsi:.1f}")

    macd = indicators.get('macd_hist', 0)
    if macd is not None:
        direction_str = "positive" if macd > 0 else "negative"
        reasons.append(f"MACD histogram: {macd:.4f} ({direction_str})")

    if pattern and pattern.get('pattern'):
        reasons.append(f"Candle: {pattern['pattern'].replace('_', ' ').title()} at {pattern.get('at_level','')}")

    vol    = indicators.get('volume', 0)
    vol20  = indicators.get('vol_ma20', 1)
    if vol and vol20 and vol20 > 0:
        vol_mult = vol / vol20
        reasons.append(f"Volume: {vol_mult:.1f}x 20-period average")

    return reasons


def _build_warnings(score: dict, indicators: dict, symbol: str) -> list:
    warnings = []
    cot = score.get('COT', 0)
    tec = score.get('TEC', 0)
    if (cot > 0 and tec < 0) or (cot < 0 and tec > 0):
        warnings.append("COT and technicals conflict — lower conviction")

    rsi = indicators.get('rsi', 50)
    if rsi and rsi > 70:
        warnings.append(f"RSI overbought ({rsi:.0f}) — watch for pullback")
    elif rsi and rsi < 30:
        warnings.append(f"RSI oversold ({rsi:.0f}) — watch for bounce")

    if _is_near_close():
        warnings.append("Within 15 minutes of market close — use caution")

    return warnings


# ---------------------------------------------------------------------------
# Suggest entry, stop, TP
# ---------------------------------------------------------------------------

def _suggest_levels(symbol: str, direction: str, price: float,
                    levels: list, indicators: dict) -> tuple:
    """
    Suggest entry zone, stop, TP1, TP2 based on nearby S/R and ATR.
    Returns (entry_zone, stop, tp1, tp2).
    """
    atr = indicators.get('atr', price * 0.005)
    if not atr:
        atr = price * 0.005

    cfg        = FUTURES_CONFIG.get(symbol, {})
    tick       = cfg.get('tick', 0.01)

    if direction == 'bullish':
        # Entry: slightly above current support, Stop: below it, TP: at resistance
        support_levels    = [l for l in levels if l['type'] == 'support'    and l['price'] < price]
        resistance_levels = [l for l in levels if l['type'] == 'resistance' and l['price'] > price]

        nearest_support    = support_levels[0]['price']    if support_levels    else price - atr
        nearest_resistance = resistance_levels[0]['price'] if resistance_levels else price + 2 * atr

        entry_low  = round(price * 0.9998, 2)
        entry_high = round(price * 1.0002, 2)
        stop  = round(nearest_support - atr * 0.5, 2)
        tp1   = round(nearest_resistance, 2)
        tp2   = round(nearest_resistance + atr, 2)

    else:
        support_levels    = [l for l in levels if l['type'] == 'support'    and l['price'] < price]
        resistance_levels = [l for l in levels if l['type'] == 'resistance' and l['price'] > price]

        nearest_support    = support_levels[0]['price']    if support_levels    else price - 2 * atr
        nearest_resistance = resistance_levels[0]['price'] if resistance_levels else price + atr

        entry_low  = round(price * 0.9998, 2)
        entry_high = round(price * 1.0002, 2)
        stop  = round(nearest_resistance + atr * 0.5, 2)
        tp1   = round(nearest_support, 2)
        tp2   = round(nearest_support - atr, 2)

    return (entry_low, entry_high), stop, tp1, tp2


# ---------------------------------------------------------------------------
# Auto-execute helper
# ---------------------------------------------------------------------------

def _auto_execute(symbol: str, direction: str, price: float,
                  stop: float, tp1: float, score: dict) -> None:
    """
    Place an order when an ENTRY alert fires.
    Forex → Oanda (always, until balance >= $500).
    Futures → Tradovate (only when balance >= $500).
    Prints result so Railway logs show what happened.
    """
    is_forex   = symbol in FOREX_CONFIG
    is_futures = symbol in FUTURES_CONFIG
    mode       = _trading_mode()

    if is_forex:
        if _oanda.is_safe_to_trade():
            print(f"  [{symbol}] Placing {direction} order via Oanda — entry {price}")
            _oanda.place_order(
                symbol=symbol,
                direction=direction,
                entry=price,
                stop=stop,
                tp1=tp1,
                strategy=f"ENTRY_SIGNAL score={score.get('total',0)}",
                order_type="MARKET",
            )
        else:
            print(f"  [{symbol}] ENTRY signal — Oanda not safe to trade (check Railway logs)")

    elif is_futures and mode == 'futures':
        if _tradovate.is_safe_to_trade():
            print(f"  [{symbol}] Placing {direction} order via Tradovate — entry {price}")
            _tradovate.place_bracket_order(
                symbol=symbol,
                direction=direction,
                qty=1,
                entry=price,
                stop=stop,
                tp1=tp1,
                strategy=f"ENTRY_SIGNAL score={score.get('total',0)}",
            )


# ---------------------------------------------------------------------------
# Single symbol check
# ---------------------------------------------------------------------------

def check_symbol(symbol: str, test_mode: bool = False) -> dict:
    """
    Full evaluation for one symbol. Returns dict with all results.
    Fires alerts if conditions met (unless test_mode=True).
    """
    result = {'symbol': symbol, 'alerts_fired': []}

    # 1. Current price
    price = get_current_price(symbol)
    result['price'] = price
    if not price:
        print(f"  [{symbol}] Could not get price — skipping")
        return result

    # 2. Bars
    bars_5m  = _get_bars(symbol, '5m',  '2d')
    bars_15m = _get_bars(symbol, '15m', '5d')

    # 3. Live indicators
    indicators = get_live_indicators(symbol)
    if not indicators.get('price') and price:
        indicators['price'] = price
    result['indicators'] = indicators

    # 4. Score
    score = calculate_score(symbol, indicators)
    result['score'] = score

    # 5. Key levels
    levels = get_key_levels(symbol)
    result['levels'] = levels

    # 6. Candle pattern (5m bars)
    pattern = detect_candle_pattern(bars_5m, key_levels=levels)
    result['pattern'] = pattern

    direction = 'bullish' if score['total'] > 0 else 'bearish'

    # 7. Check setup conditions
    setup_met  = check_setup_conditions(symbol, score, levels, indicators)
    result['setup_met'] = setup_met

    # 8. Check entry conditions
    entry_met = False
    if setup_met:
        entry_met = check_entry_conditions(symbol, bars_15m, indicators, pattern, levels)
    result['entry_met'] = entry_met

    if test_mode:
        return result

    # --- Fire alerts ---
    if _is_near_close():
        print(f"  [{symbol}] Near close — suppressing alerts")
        return result

    # --- Score-based alerts (existing system) ---
    entry_zone, stop, tp1, tp2 = _suggest_levels(symbol, direction, price, levels, indicators)
    reasons  = _build_reasons(score, indicators, pattern)
    warnings = _build_warnings(score, indicators, symbol)

    if entry_met and not _already_alerted(symbol, 'ENTRY', hours=4):
        msg = format_alert_message(
            alert_type='ENTRY', symbol=symbol, direction=direction,
            price=price, score=score,
            entry_zone=entry_zone, stop=stop, tp1=tp1, tp2=tp2,
            levels=levels, pattern=pattern, reasons=reasons, warnings=warnings,
        )
        fire_alert(msg, alert_type='ENTRY', symbol=symbol)
        result['alerts_fired'].append('ENTRY')

        # Auto-execute on ENTRY signal
        _auto_execute(symbol, direction, price, stop, tp1, score)

    elif setup_met and not _already_alerted(symbol, 'SETUP', hours=4):
        msg = format_alert_message(
            alert_type='SETUP', symbol=symbol, direction=direction,
            price=price, score=score,
            entry_zone=entry_zone, stop=stop, tp1=tp1, tp2=tp2,
            levels=levels, pattern=pattern, reasons=reasons, warnings=warnings,
        )
        fire_alert(msg, alert_type='SETUP', symbol=symbol)
        result['alerts_fired'].append('SETUP')

    # --- Strategy alerts (VWAP Pullback / ORB / EMA Pullback) ---
    _check_strategy_signal(symbol, levels, result)

    return result


def _check_strategy_signal(symbol: str, levels: list, result: dict) -> None:
    """
    Run all applicable strategies on the latest 15m bars and fire alerts.

    Strategy priority for futures:
      1. Gap Fill   — 9:30–9:44 AM ET only, once per day per symbol
      2. ORB        — 9:45–10:15 AM ET only
      3. VWAP Pullback — any time during RTH

    Forex: EMA 9/21 Pullback only (session-filtered upstream).

    Cooldown: 4 hours per symbol per strategy type.
    """
    bars = _get_bars_15m(symbol)
    if bars is None or len(bars) < 20:
        return

    is_futures = symbol in FUTURES_CONFIG

    # ── Gap Fill (futures only, 9:30–9:44 AM ET) ──────────────────────────
    if is_futures:
        gf_sig = _gap_fill_strategy.check(symbol, bars)
        if gf_sig is not None:
            cooldown_key = 'STRATEGY_GAP_FILL'
            if not _already_alerted(symbol, cooldown_key, hours=20):
                msg = format_gap_fill_alert(gf_sig)
                fire_alert(msg, alert_type=cooldown_key, symbol=symbol)
                result['alerts_fired'].append(cooldown_key)
                print(f"  [{symbol}] GAP FILL — {gf_sig.direction} entry {gf_sig.entry:.2f} "
                      f"→ PDC {gf_sig.tp1:.2f}")

                # Auto-execute futures orders via Tradovate (dormant until $500 funded)
                if _tradovate.is_safe_to_trade():
                    _tradovate.place_bracket_order(
                        symbol=gf_sig.symbol,
                        direction=gf_sig.direction,
                        qty=1,
                        entry=gf_sig.entry,
                        stop=gf_sig.stop,
                        tp1=gf_sig.tp1,
                        strategy=gf_sig.strategy,
                    )
            return   # gap fill takes priority — don't also fire VWAP/ORB at open

    # ── VWAP Pullback / ORB (futures) or EMA Pullback (forex) ─────────────
    if is_futures:
        sig = _futures_strategy.check(symbol, bars)
    else:
        sig = _forex_strategy.check(symbol, bars)

    if sig is None:
        return

    cooldown_type = f'STRATEGY_{sig.strategy}'
    if _already_alerted(symbol, cooldown_type, hours=4):
        return

    msg = format_strategy_entry_alert(sig, levels=levels)
    fire_alert(msg, alert_type=cooldown_type, symbol=symbol)
    result['alerts_fired'].append(cooldown_type)
    print(f"  [{symbol}] Strategy signal: {sig.strategy} — entry {sig.entry}")

    # Auto-execute via broker (only if AUTO_TRADE_ENABLED=true and credentials set)
    if is_futures:
        # Tradovate — futures ($500+ account, dormant until you tell me to activate)
        if _tradovate.is_safe_to_trade():
            _tradovate.place_bracket_order(
                symbol=sig.symbol,
                direction=sig.direction,
                qty=1,
                entry=sig.entry,
                stop=sig.stop,
                tp1=sig.tp1,
                strategy=sig.strategy,
            )
    else:
        # Oanda — forex ($100+ account)
        if _oanda.is_safe_to_trade():
            _oanda.place_order(
                symbol=sig.symbol,
                direction=sig.direction,
                entry=sig.entry,
                stop=sig.stop,
                tp1=sig.tp1,
                strategy=sig.strategy,
                order_type="LIMIT",
            )


# ---------------------------------------------------------------------------
# Position exit monitoring
# ---------------------------------------------------------------------------

def _check_exit_conditions() -> None:
    """
    Check registered open positions for:
      - Initial stop loss hit
      - TP1 hit → fire alert, move stop to breakeven, activate trail
      - TP2 hit → fire alert, continue trail on remaining 20%
      - Trailing stop hit → fire final exit alert
      - Trailing stop ratchet update (when trail moves up significantly)
    """
    if not LOG_FILE.exists():
        return

    try:
        with open(LOG_FILE) as f:
            log = json.load(f)
    except Exception:
        return

    positions = [e for e in log if e.get('type') == 'POSITION']
    if not positions:
        return

    changed = False

    for pos in positions:
        symbol    = pos.get('symbol', '')
        direction = pos.get('direction', 'long')
        entry     = pos.get('entry', 0)
        stop      = pos.get('stop', 0)
        tp1       = pos.get('tp1', 0)
        tp2       = pos.get('tp2')
        exited    = pos.get('exited', False)

        # Trail state
        tp1_hit       = pos.get('tp1_hit', False)
        tp2_hit       = pos.get('tp2_hit', False)
        trail_active  = pos.get('trail_active', False)
        trail_stop    = pos.get('trail_stop', 0.0)
        trail_atr_mult = pos.get('trail_atr_mult', 1.5)
        running_high  = pos.get('running_high', entry)

        if exited or not symbol:
            continue

        price = get_current_price(symbol)
        if not price:
            continue

        # Only handle long positions in strategy alerts (system is long-only for strategy)
        if direction != 'long':
            # Legacy short handling
            if stop and price >= stop:
                msg = (
                    f"🚨 <b>EXIT ALERT — {symbol}</b>\n"
                    f"Direction: Short\n"
                    f"Entry: {entry:,.4f} | Current: {price:,.4f}\n"
                    f"Stop loss hit — review and close position."
                )
                fire_alert(msg, alert_type='EXIT', symbol=symbol)
                pos['exited'] = True
                changed = True
            elif tp1 and price <= tp1:
                msg = (
                    f"🚨 <b>EXIT ALERT — {symbol}</b>\n"
                    f"Direction: Short\n"
                    f"Entry: {entry:,.4f} | Current: {price:,.4f}\n"
                    f"TP1 hit — consider taking profit."
                )
                fire_alert(msg, alert_type='EXIT', symbol=symbol)
                pos['exited'] = True
                changed = True
            continue

        # -------------------------------------------------------
        # LONG position exit logic
        # -------------------------------------------------------

        dec = _decimals(symbol)

        # Update running high
        if price > running_high:
            pos['running_high'] = price
            running_high = price
            changed = True

        # 1. Initial stop loss (before TP1 is hit)
        if not tp1_hit and stop and price <= stop:
            msg = (
                f"🚨 <b>STOP LOSS HIT — {symbol}</b>\n"
                f"Entry: {_fmt(entry, symbol)} | Price: {_fmt(price, symbol)}\n"
                f"Stop: {_fmt(stop, symbol)}\n"
                f"Loss: {round((price - entry), dec)} pts\n\n"
                f"Exit full position."
            )
            fire_alert(msg, alert_type='EXIT', symbol=symbol)
            pos['exited'] = True
            changed = True
            continue

        # 2. TP1 hit — exit 40%, move stop to breakeven, activate trail
        if not tp1_hit and tp1 and price >= tp1:
            pos['tp1_hit']      = True
            pos['stop']         = entry   # stop moves to breakeven
            pos['trail_active'] = True
            pos['trail_stop']   = entry   # trail initializes at breakeven
            pos['running_high'] = price
            tp1_hit      = True
            trail_active = True
            trail_stop   = entry
            changed      = True

            # Compute ATR for trail info
            atr = pos.get('atr', abs(tp1 - entry) / 1.5)
            trail_dist = round(atr * trail_atr_mult, dec)
            trail_info = f"Trail = 1.5×ATR ({trail_dist} pts) below running high"

            msg = format_tp1_hit_alert(
                symbol=symbol, entry=entry, tp1=tp1,
                new_stop=entry, trail_info=trail_info,
            )
            fire_alert(msg, alert_type='TP1_HIT', symbol=symbol)
            continue

        # 3. TP2 hit — exit another 40%, trail continues on 20%
        if tp1_hit and not tp2_hit and tp2 and price >= tp2:
            pos['tp2_hit'] = True
            tp2_hit = True
            changed = True

            atr = pos.get('atr', abs(tp2 - entry) / 2.5)
            trail_dist = round(atr * trail_atr_mult, dec)
            current_trail = round(running_high - atr * trail_atr_mult, dec)
            pos['trail_stop'] = max(trail_stop, current_trail)
            trail_info = f"Trail = {_fmt(pos['trail_stop'], symbol)} ({trail_dist} pts below running high {_fmt(running_high, symbol)})"

            msg = format_tp2_hit_alert(
                symbol=symbol, entry=entry, tp2=tp2,
                trail_info=trail_info,
            )
            fire_alert(msg, alert_type='TP2_HIT', symbol=symbol)
            continue

        # 4. Trailing stop active — ratchet up and check for hit
        if trail_active and tp1_hit:
            atr = pos.get('atr', abs(entry - stop) / 1.0)
            new_trail = round(running_high - atr * trail_atr_mult, dec)

            # Only ratchet upward
            if new_trail > trail_stop:
                old_trail = trail_stop
                pos['trail_stop'] = new_trail
                trail_stop = new_trail
                changed = True

                # Send update alert only if trail moved by at least 0.5× ATR
                if abs(new_trail - old_trail) >= atr * 0.5:
                    msg = format_trail_updated_alert(symbol, new_trail, running_high)
                    fire_alert(msg, alert_type='TRAIL_UPDATE', symbol=symbol)

            # Check if price hit the trail stop
            if trail_stop and price <= trail_stop:
                msg = format_trail_stop_hit_alert(
                    symbol=symbol, entry=entry,
                    trail_level=trail_stop, current_price=price,
                )
                fire_alert(msg, alert_type='TRAIL_HIT', symbol=symbol)
                pos['exited'] = True
                changed = True

    if changed:
        with open(LOG_FILE, 'w') as f:
            json.dump(log, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Register position
# ---------------------------------------------------------------------------

def register_position(symbol: str, direction: str, entry: float,
                      stop: float, tp1: float, tp2: float = None,
                      trail_atr_mult: float = 1.5) -> None:
    """Save an open position to alerts_log.json for exit monitoring."""
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
    except ImportError:
        now = datetime.now(timezone.utc)

    cfg = FUTURES_CONFIG.get(symbol, {})
    point_value = cfg.get('point_value', 1)
    risk_pts    = abs(entry - stop)
    dollar_risk = round(risk_pts * point_value, 2)
    rr1 = round(abs(tp1 - entry) / risk_pts, 1) if risk_pts and tp1 else 0
    rr2 = round(abs(tp2 - entry) / risk_pts, 1) if risk_pts and tp2 else 0

    # Estimate ATR from risk (risk_pts ≈ 1.0×ATR for typical stop placement)
    estimated_atr = risk_pts

    entry_record = {
        'timestamp':      now.isoformat(),
        'type':           'POSITION',
        'symbol':         symbol,
        'direction':      direction,
        'entry':          entry,
        'stop':           stop,
        'tp1':            tp1,
        'tp2':            tp2,
        'exited':         False,
        # Trail state
        'tp1_hit':        False,
        'tp2_hit':        False,
        'trail_active':   False,
        'trail_stop':     0.0,
        'trail_atr_mult': trail_atr_mult,
        'running_high':   entry,
        'atr':            estimated_atr,
        'message':        f"Position registered: {symbol} {direction} @ {entry}",
    }

    log = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f:
                log = json.load(f)
        except Exception:
            pass
    log.append(entry_record)
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2, default=str)

    trail_pts = round(estimated_atr * trail_atr_mult, 4)
    print(f"\n✅ Position registered: {symbol} {direction.upper()} @ {entry:,.4f}")
    print(f"   Stop:  {stop:,.4f} | TP1: {tp1:,.4f}" + (f" | TP2: {tp2:,.4f}" if tp2 else ""))
    print(f"   Trail: {trail_atr_mult}×ATR = ~{trail_pts:.4f} pts below running high (activates at TP1)")
    print(f"   Risk per contract: ${dollar_risk:.2f} ({risk_pts:.4f} pts × ${point_value}/pt)")
    print(f"   R:R → TP1: {rr1:.1f}:1" + (f" | TP2: {rr2:.1f}:1" if tp2 else ""))
    print(f"\n   Monitoring for exit conditions:")
    print(f"   • TP1 ({tp1:,.4f}) → exit 40%, stop moves to breakeven, trail activates")
    if tp2:
        print(f"   • TP2 ({tp2:,.4f}) → exit 40%, trail continues on final 20%")
    print(f"   • Stop loss ({stop:,.4f}) → full exit")
    print(f"   • Trail stop hit → final 20% exit")


# ---------------------------------------------------------------------------
# Print test evaluation
# ---------------------------------------------------------------------------

def _print_test_result(result: dict) -> None:
    symbol = result['symbol']
    price  = result.get('price', 0)
    score  = result.get('score', {})
    inds   = result.get('indicators', {})
    levels = result.get('levels', [])[:5]
    pattern= result.get('pattern', {})

    score_str = f"+{score.get('total',0)}" if score.get('total',0) > 0 else str(score.get('total',0))

    print(f"\n{'='*60}")
    print(f"  {symbol} TEST EVALUATION")
    print(f"{'='*60}")
    print(f"  Price:  {price:,.4f}")
    print(f"  Score:  {score_str} ({score.get('bias','?')})")
    breakdown = " ".join(
        f"{k}:{'+' if v>0 else ''}{v}"
        for k, v in score.items()
        if k in ('TEC','COT','RET','SEA','ECO','PC','MOM')
    )
    print(f"  Breakdown: {breakdown}")
    print(f"  RSI: {inds.get('rsi',0):.1f}  MACD hist: {inds.get('macd_hist',0):.4f}  Source: {inds.get('source','?')}")
    print(f"  Pattern: {pattern.get('pattern') or 'none'} at {pattern.get('at_level','—')}")
    print(f"  Setup conditions met: {'YES ✓' if result.get('setup_met') else 'no'}")
    print(f"  Entry conditions met: {'YES ✓' if result.get('entry_met') else 'no'}")
    if levels:
        print(f"  Nearest levels:")
        for lvl in levels:
            print(f"    {lvl['label']:<6} {lvl['price']:>10,.2f}  ({lvl['distance_pts']:.2f} pts)")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

_last_heartbeat = 0

def _maybe_heartbeat(results: list) -> None:
    global _last_heartbeat
    now = time.time()
    if now - _last_heartbeat < 300:  # every 5 min
        return
    _last_heartbeat = now

    def _fmt_price(p):
        return f"{p:,.4f}" if p < 100 else f"{p:,.2f}"
    parts = [f"{r['symbol']}={_fmt_price(r['price'])}" for r in results if r.get('price')]
    ts = _et_time_str()
    print(f"[{ts} ET] Monitoring: {' | '.join(parts)}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

FUTURES_UNLOCK_BALANCE = 500.0   # USD — switch from forex-only to futures at this balance


def _trading_mode() -> str:
    """
    Return 'forex' if Oanda balance < $500, else 'futures'.
    Falls back to 'forex' if balance can't be fetched.
    """
    try:
        balance = _oanda.get_balance()
        if balance >= FUTURES_UNLOCK_BALANCE:
            return 'futures'
    except Exception:
        pass
    return 'forex'


def run_loop() -> None:
    print("\n=== Futures + Forex Alert Monitor starting ===")
    print(f"  Forex auto-trade until balance reaches ${FUTURES_UNLOCK_BALANCE:.0f}")
    print(f"  At ${FUTURES_UNLOCK_BALANCE:.0f}+: switch to futures trading")
    print("  Forex:    24/5 — session-filtered (Asian/London/NY)")
    tv_health_check()

    last_tv_check   = time.time()
    last_mode_log   = ""
    futures_symbols = list(FUTURES_CONFIG.keys())

    while True:
        # Re-check TradingView every 10 minutes
        if time.time() - last_tv_check > 600:
            tv_health_check()
            last_tv_check = time.time()

        mode    = _trading_mode()
        results = []

        if mode != last_mode_log:
            ts = _et_time_str()
            if mode == 'futures':
                print(f"[{ts} ET] 🎉 Balance >= ${FUTURES_UNLOCK_BALANCE:.0f} — switching to FUTURES mode")
            else:
                bal = _oanda.get_balance()
                print(f"[{ts} ET] FOREX mode — balance ${bal:.2f} (target ${FUTURES_UNLOCK_BALANCE:.0f})")
            last_mode_log = mode

        # --- Futures: only when balance >= $500 AND during market hours ---
        if mode == 'futures' and _is_market_hours():
            for sym in futures_symbols:
                try:
                    r = check_symbol(sym)
                    results.append(r)
                except Exception as e:
                    print(f"  [{sym}] Error: {e}")

        # --- Forex: always 24/5 until balance >= $500, then alerts-only ---
        if _is_forex_market_open():
            active_pairs = _active_forex_pairs()
            for sym in active_pairs:
                try:
                    r = check_symbol(sym)
                    results.append(r)
                except Exception as e:
                    print(f"  [{sym}] Error: {e}")
        elif not _is_market_hours():
            # Both markets closed — sleep longer, print status
            ts = _et_time_str()
            print(f"[{ts} ET] Markets closed — sleeping")
            time.sleep(60)
            continue

        # Check exit conditions for manually registered positions
        try:
            _check_exit_conditions()
        except Exception as e:
            print(f"  [exit monitor] Error: {e}")

        # Check exit conditions for automated Oanda trades (stop/TP hit)
        try:
            _oanda.check_exits()
        except Exception as e:
            print(f"  [oanda exit monitor] Error: {e}")

        _maybe_heartbeat(results)
        time.sleep(60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Futures Alert Monitor")
    parser.add_argument('--test', metavar='SYMBOL', help='Test mode: MES, MNQ, ... or ALL')
    parser.add_argument('--register', nargs='+', metavar='ARG',
                        help='Register position: SYMBOL DIR ENTRY stop=S tp1=T1 [tp2=T2]')
    args = parser.parse_args()

    if args.register:
        # Parse: MES long 5615 stop=5578 tp1=5670 tp2=5720
        raw = args.register
        sym = raw[0].upper()
        dr  = raw[1].lower()
        entry = float(raw[2])
        params = {}
        for part in raw[3:]:
            k, v = part.split('=')
            params[k] = float(v)
        register_position(sym, dr, entry,
                          stop=params.get('stop', 0),
                          tp1=params.get('tp1', 0),
                          tp2=params.get('tp2'))
        sys.exit(0)

    if args.test:
        test_sym = args.test.upper()
        symbols  = list(FUTURES_CONFIG.keys()) if test_sym == 'ALL' else [test_sym]
        tv_health_check()
        for sym in symbols:
            r = check_symbol(sym, test_mode=True)
            _print_test_result(r)
        sys.exit(0)

    run_loop()
