"""Helpers for talking to TradingView Desktop through the bundled tv CLI."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pandas as pd

TV_SYMBOL_MAP = {
    "MES": "MES1!",
    "MNQ": "MNQ1!",
    "MYM": "MYM1!",
    "M2K": "M2K1!",
    "MCL": "MCL1!",
    "MGC": "MGC1!",
    "MSI": "MSI1!",
    "MNG": "MNG1!",
    "ES": "ES1!",
    "NQ": "NQ1!",
    "YM": "YM1!",
    "RTY": "RTY1!",
    "CL": "CL1!",
    "GC": "GC1!",
    "SI": "SI1!",
    "NG": "NG1!",
    "SPY": "AMEX:SPY",
    "QQQ": "NASDAQ:QQQ",
    "AAPL": "NASDAQ:AAPL",
    "NVDA": "NASDAQ:NVDA",
    "TSLA": "NASDAQ:TSLA",
    "MSFT": "NASDAQ:MSFT",
    "EURUSD": "OANDA:EURUSD",
    "GBPUSD": "OANDA:GBPUSD",
    "USDJPY": "OANDA:USDJPY",
    "USDCHF": "OANDA:USDCHF",
    "AUDUSD": "OANDA:AUDUSD",
    "NZDUSD": "OANDA:NZDUSD",
    "USDCAD": "OANDA:USDCAD",
    "EURGBP": "OANDA:EURGBP",
    "EURJPY": "OANDA:EURJPY",
    "GBPJPY": "OANDA:GBPJPY",
    "EURCHF": "OANDA:EURCHF",
    "AUDCAD": "OANDA:AUDCAD",
    "CADJPY": "OANDA:CADJPY",
    "AUDNZD": "OANDA:AUDNZD",
    "NZDJPY": "OANDA:NZDJPY",
    "GBPAUD": "OANDA:GBPAUD",
    "NZDCAD": "OANDA:NZDCAD",
}

DASHBOARD_SYMBOLS = [
    "MES", "MNQ", "MYM", "MCL", "MGC", "MSI", "MNG", "M2K",
    "ES", "NQ", "YM", "CL", "GC", "SI", "NG",
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT",
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "GBPJPY", "EURCHF", "AUDCAD", "CADJPY",
    "AUDNZD", "NZDJPY", "GBPAUD", "NZDCAD",
]

TV_TO_APP_SYMBOL_MAP = {value: key for key, value in TV_SYMBOL_MAP.items()}

TIMEFRAME_MAP = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "4h": "240",
    "1d": "D",
    "1w": "W",
}

_STATUS_CACHE: dict[str, object] = {"result": None, "checked_at": 0.0}


def _tv_cli_path() -> str:
    cli_path = os.getenv(
        "TRADINGVIEW_CLI_PATH",
        str(Path.home() / "tradingview-mcp-jackson" / "src" / "cli" / "index.js"),
    )
    return os.path.expanduser(cli_path)


def _tv_node_bin() -> str:
    return os.getenv("TRADINGVIEW_NODE_BIN", "node")


def _normalize_symbol(symbol: str) -> str:
    return TV_SYMBOL_MAP.get(symbol, symbol)


def _symbol_without_exchange(symbol: str) -> str:
    return str(symbol or "").split(":")[-1].strip()


def normalize_symbol_from_tradingview(symbol: str) -> str:
    if symbol in TV_TO_APP_SYMBOL_MAP:
        return TV_TO_APP_SYMBOL_MAP[symbol]
    base = _symbol_without_exchange(symbol)
    return TV_TO_APP_SYMBOL_MAP.get(base, base)


def get_dashboard_watchlist_map() -> dict[str, str]:
    return {symbol: TV_SYMBOL_MAP.get(symbol, symbol) for symbol in DASHBOARD_SYMBOLS}


def _normalize_timeframe(timeframe: str) -> str:
    return TIMEFRAME_MAP.get(str(timeframe).lower(), str(timeframe))


def _status_timeout_seconds() -> int:
    try:
        return max(1, int(float(os.getenv("TRADINGVIEW_STATUS_TIMEOUT", "12"))))
    except (TypeError, ValueError):
        return 12


def _status_cache_ttl_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("TRADINGVIEW_STATUS_TTL_SECONDS", "30")))
    except (TypeError, ValueError):
        return 30.0


def _run_tv_cli(args: list[str], timeout: int = 20) -> dict:
    cli_path = _tv_cli_path()
    if not Path(cli_path).exists():
        return {
            "success": False,
            "error": f"TradingView CLI not found at {cli_path}",
            "connected": False,
        }

    cmd = [_tv_node_bin(), cli_path, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc), "connected": False}

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    payload = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None

    if proc.returncode == 0 and isinstance(payload, dict):
        return payload

    message = (
        (payload or {}).get("error")
        if isinstance(payload, dict)
        else stderr or stdout or f"tv CLI exited with code {proc.returncode}"
    )
    return {
        "success": False,
        "error": message,
        "connected": False,
        "returncode": proc.returncode,
    }


def get_status(force: bool = False) -> dict:
    now = time.time()
    ttl = _status_cache_ttl_seconds()
    cached = _STATUS_CACHE.get("result")
    checked_at = float(_STATUS_CACHE.get("checked_at", 0.0) or 0.0)

    if not force and isinstance(cached, dict) and now - checked_at < ttl:
        return dict(cached)

    result = _run_tv_cli(["status"], timeout=_status_timeout_seconds())
    result["connected"] = bool(result.get("success") and result.get("cdp_connected"))
    _STATUS_CACHE["result"] = dict(result)
    _STATUS_CACHE["checked_at"] = now
    return result


def is_connected() -> bool:
    return bool(get_status().get("connected"))


def ensure_chart(symbol: str, timeframe: str = "15") -> bool:
    tv_symbol = _normalize_symbol(symbol)
    tv_timeframe = _normalize_timeframe(timeframe)

    sym_result = _run_tv_cli(["symbol", tv_symbol], timeout=15)
    if not sym_result.get("success"):
        return False

    tf_result = _run_tv_cli(["timeframe", tv_timeframe], timeout=15)
    return bool(tf_result.get("success"))


def get_ohlcv(symbol: str | None = None, timeframe: str = "15", count: int = 200) -> pd.DataFrame:
    if symbol and not ensure_chart(symbol, timeframe):
        return pd.DataFrame()

    raw = _run_tv_cli(["ohlcv", "--count", str(count)], timeout=20)
    bars = raw.get("bars", []) if raw.get("success") else []
    if not bars:
        return pd.DataFrame()

    frame = pd.DataFrame(bars)
    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    frame = frame.rename(columns=rename_map)
    if "time" in frame.columns:
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame = frame.set_index("time")
    return frame


def get_quote(symbol: str | None = None, timeframe: str = "15") -> dict:
    if symbol and not ensure_chart(symbol, timeframe):
        return {}
    raw = _run_tv_cli(["quote"], timeout=15)
    return raw if raw.get("success") else {}


def get_active_chart_symbol() -> str:
    status = get_status()
    return normalize_symbol_from_tradingview(status.get("chart_symbol", ""))


def get_watchlist_quotes() -> dict[str, dict]:
    raw = _run_tv_cli(["watchlist", "get"], timeout=15)
    if not raw.get("success"):
        return {}

    results = {}
    for item in raw.get("symbols", []):
        symbol = normalize_symbol_from_tradingview(item.get("symbol", ""))
        if not symbol:
            continue

        try:
            price = float(str(item.get("last", "")).replace(",", ""))
        except (TypeError, ValueError):
            price = None

        change_pct_raw = str(item.get("change_percent", "") or "").replace("%", "").replace(",", "").strip()
        try:
            change_pct = float(change_pct_raw)
        except (TypeError, ValueError):
            change_pct = None

        change_raw = str(item.get("change", "") or "").replace(",", "").strip()
        try:
            change_value = float(change_raw)
        except (TypeError, ValueError):
            change_value = None

        prev = None
        if price is not None and change_value is not None:
            prev = round(price - change_value, 4)
        elif price is not None and change_pct not in (None, -100):
            prev = round(price / (1 + (change_pct / 100.0)), 4)

        results[symbol] = {
            "price": round(price, 4) if price is not None else None,
            "prev": prev,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "source": "tradingview_watchlist",
        }

    return results


def draw_horizontal_line(
    symbol: str,
    price: float,
    text: str,
    color: str,
    timeframe: str = "D",
) -> dict:
    if not ensure_chart(symbol, timeframe):
        return {"success": False, "error": "TradingView chart unavailable"}

    now = int(time.time())
    overrides = json.dumps({"linecolor": color, "textcolor": color})
    return _run_tv_cli(
        [
            "draw",
            "shape",
            "--type",
            "horizontal_line",
            "--price",
            str(price),
            "--time",
            str(now),
            "--text",
            text,
            "--overrides",
            overrides,
        ],
        timeout=20,
    )
