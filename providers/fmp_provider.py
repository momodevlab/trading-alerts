"""
fmp_provider.py — shared provider wrapper for FMP-backed data.

This keeps endpoint details in one place so scripts and agents can swap
providers later without changing their business logic.
"""

import os
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE_V3 = "https://financialmodelingprep.com/api/v3"
FMP_BASE_STABLE = "https://financialmodelingprep.com/stable"


def get_active_provider_name() -> str:
    """Return the configured macro/calendar provider name."""
    return os.getenv("MARKET_DATA_PROVIDER", "none").strip().lower() or "none"


def _provider_disabled() -> bool:
    return get_active_provider_name() != "fmp"


def _provider_headers() -> dict:
    return {"User-Agent": "trading-system/1.0"}


def provider_status(timeout: int = 10) -> dict:
    """
    Return a lightweight provider health snapshot.
    status: ok | degraded | disabled
    """
    provider = get_active_provider_name()
    if provider != "fmp":
        return {
            "provider": provider,
            "status": "disabled",
            "detail": f"{provider} provider is selected; FMP wrapper skipped",
        }

    if not FMP_API_KEY:
        return {
            "provider": "fmp",
            "status": "degraded",
            "detail": "FMP_API_KEY not configured",
        }

    url = f"{FMP_BASE_V3}/is-the-market-open?apikey={FMP_API_KEY}"
    try:
        resp = requests.get(url, timeout=timeout, headers=_provider_headers())
        if resp.status_code == 200:
            return {
                "provider": "fmp",
                "status": "ok",
                "detail": "reachable",
            }
        if resp.status_code in (401, 402, 403):
            return {
                "provider": "fmp",
                "status": "degraded",
                "detail": f"reachable but access limited ({resp.status_code})",
            }
        return {
            "provider": "fmp",
            "status": "degraded",
            "detail": f"unexpected status {resp.status_code}",
        }
    except Exception as e:
        return {
            "provider": "fmp",
            "status": "degraded",
            "detail": f"request failed: {e}",
        }


def fetch_economic_calendar() -> list:
    """
    Fetch the macro calendar from the configured provider.
    Returns [] when unavailable or not supported on the current plan.
    """
    if _provider_disabled():
        print(f"[fmp_provider] Provider '{get_active_provider_name()}' not implemented for economic calendar yet.")
        return []

    if not FMP_API_KEY:
        print("[fmp_provider] FMP_API_KEY not set — economic calendar unavailable.")
        return []

    url = f"{FMP_BASE_STABLE}/economic-calendar?apikey={FMP_API_KEY}"
    try:
        resp = requests.get(url, timeout=30, headers=_provider_headers())
        if resp.status_code in (402, 403):
            print("[fmp_provider] Economic calendar requires a paid FMP plan.")
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[fmp_provider] Economic calendar error: {e}")
        return []


def fetch_todays_economic_events() -> list:
    """
    Fetch today's macro events from the configured provider.
    """
    if _provider_disabled():
        print(f"[fmp_provider] Provider '{get_active_provider_name()}' not implemented for today's event calendar yet.")
        return []

    if not FMP_API_KEY:
        return []

    today = date.today()
    tomorrow = today + timedelta(days=1)
    url = (
        f"{FMP_BASE_V3}/economic_calendar"
        f"?from={today}&to={tomorrow}&apikey={FMP_API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=20, headers=_provider_headers())
        if resp.status_code in (402, 403):
            print(f"[fmp_provider] Today's event calendar unavailable on current FMP plan ({resp.status_code}).")
            return []
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[fmp_provider] Today's event calendar error: {e}")
        return []


def fetch_earnings_calendar(symbol: str, days_ahead: int = 30) -> list:
    """
    Fetch earnings-calendar rows for a symbol from the configured provider.
    """
    if _provider_disabled():
        print(f"[fmp_provider] Provider '{get_active_provider_name()}' not implemented for earnings calendar yet.")
        return []

    if not FMP_API_KEY:
        return []

    today = date.today()
    out_date = today + timedelta(days=days_ahead)
    url = (
        f"{FMP_BASE_V3}/earning_calendar"
        f"?from={today}&to={out_date}&apikey={FMP_API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=20, headers=_provider_headers())
        if resp.status_code in (402, 403):
            print(f"[fmp_provider] Earnings calendar unavailable on current FMP plan ({resp.status_code}).")
            return []
        resp.raise_for_status()
        payload = resp.json()
        return [
            ev for ev in payload
            if ev.get("symbol", "").upper() == symbol.upper()
        ]
    except Exception as e:
        print(f"[fmp_provider] Earnings calendar error for {symbol}: {e}")
        return []
