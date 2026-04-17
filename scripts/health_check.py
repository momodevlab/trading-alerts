"""
health_check.py — quick provider and integration health report.

Checks:
- TradingView MCP reachability
- Yahoo Finance quote fetch
- FMP/provider reachability
- Telegram configuration
- cache freshness
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import requests
import yfinance as yf
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from providers.fmp_provider import provider_status
from providers import tradingview_provider

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
ECO_CACHE = BASE_DIR / "data" / "economic" / "eco_latest.json"
COT_CACHE = BASE_DIR / "data" / "cot" / "cot_latest.json"


def _status_icon(status: str) -> str:
    return {
        "ok": "OK",
        "degraded": "WARN",
        "disabled": "INFO",
    }.get(status, "WARN")


def _check_tradingview() -> dict:
    try:
        with urlopen("http://localhost:9222/json/list", timeout=2) as resp:
            targets = json.load(resp)
    except URLError:
        return {"status": "degraded", "detail": "CDP not reachable on localhost:9222"}
    except Exception as e:
        return {"status": "degraded", "detail": f"CDP check failed: {e}"}

    non_chart_markers = (
        "/app/new-tab/",
        "/app/window/",
        "/app/tooltip/",
        "/app/browser-api-container/",
        "/app/renderer-services/",
    )
    chart_targets = [
        t for t in targets
        if t.get("type") == "page"
        and t.get("url")
        and not any(marker in t.get("url", "") for marker in non_chart_markers)
    ]

    if not chart_targets:
        return {
            "status": "degraded",
            "detail": "CDP connected; open a chart tab in TradingView Desktop",
        }

    status = tradingview_provider.get_status()
    if status.get("connected") and status.get("api_available"):
        symbol = status.get("chart_symbol", "unknown")
        resolution = status.get("chart_resolution", "unknown")
        return {"status": "ok", "detail": f"connected to {symbol} on {resolution}"}

    if status.get("cdp_connected"):
        return {
            "status": "degraded",
            "detail": "CDP connected but no chart tab is active in TradingView",
        }

    detail = status.get("error", "not connected")
    return {"status": "degraded", "detail": detail}


def _check_yahoo() -> dict:
    try:
        hist = yf.Ticker("SPY").history(period="2d", interval="1d")
        if hist is None or hist.empty:
            return {"status": "degraded", "detail": "SPY returned no rows"}
        price = float(hist["Close"].iloc[-1])
        return {"status": "ok", "detail": f"SPY latest close {price:,.2f}"}
    except Exception as e:
        return {"status": "degraded", "detail": f"quote fetch failed: {e}"}


def _check_telegram() -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        return {"status": "ok", "detail": "bot token and chat id configured"}
    return {"status": "degraded", "detail": "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"}


def _check_cache(path: Path, label: str) -> dict:
    if not path.exists():
        return {"status": "degraded", "detail": f"{label} cache missing"}
    try:
        with open(path) as f:
            json.load(f)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age_minutes = (datetime.now(timezone.utc) - mtime).total_seconds() / 60
        return {"status": "ok", "detail": f"{label} cache age {age_minutes:.0f} min"}
    except Exception as e:
        return {"status": "degraded", "detail": f"{label} cache unreadable: {e}"}


def run() -> int:
    macro = provider_status()
    checks = [
        ("TradingView MCP", _check_tradingview()),
        ("Yahoo Finance", _check_yahoo()),
        ("Macro Provider", macro),
        ("Telegram", _check_telegram()),
    ]

    if macro.get("status") != "disabled":
        checks.append(("Economic Cache", _check_cache(ECO_CACHE, "economic")))

    checks.append(("COT Cache", _check_cache(COT_CACHE, "COT")))

    print("\nTrading System Health Check")
    print("=" * 64)
    worst = 0
    for label, result in checks:
        status = result.get("status", "degraded")
        detail = result.get("detail", "")
        print(f"{_status_icon(status):<5} {label:<18} {detail}")
        if status == "degraded":
            worst = max(worst, 1)
    print("=" * 64)
    return worst


if __name__ == "__main__":
    raise SystemExit(run())
