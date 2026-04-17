"""
economic_agent.py — FMP economic data fetcher, scorer, heat-map printer

Fetches US macro indicators from Financial Modeling Prep, compares actual vs
forecast, scores the overall US economy -2 to +2, caches to JSON.

Important inversion: CPI/PPI/PCE beat = bullish USD but bearish stocks
(higher inflation → higher rates → bad for equities). Score is presented from
a stocks/risk-on perspective, not USD perspective.

Usage:
    python agents/economic_agent.py
    → prints color-coded heat map table

Cache: data/economic/eco_latest.json (refreshes at most once per 6 hours)
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from providers.fmp_provider import fetch_economic_calendar, get_active_provider_name

load_dotenv()

BASE_DIR   = Path(__file__).parent.parent
CACHE_DIR  = BASE_DIR / "data" / "economic"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "eco_latest.json"

# FMP event name substrings → our label + inflation_inversion flag
INDICATORS = [
    ("GDP Growth Rate",           "GDP",              False),
    ("Non Farm Payrolls",         "NFP",              False),
    ("CPI YoY",                   "CPI YoY",          True),   # beat = bad for stocks
    ("Core CPI",                  "Core CPI",         True),
    ("PCE Price Index",           "PCE",              True),
    ("PPI",                       "PPI",              True),
    ("ISM Manufacturing PMI",     "ISM Mfg",          False),
    ("ISM Services PMI",          "ISM Services",     False),
    ("Retail Sales MoM",          "Retail Sales",     False),
    ("Unemployment Rate",         "Unemployment",     True),   # beat (lower) = better economy
    ("Initial Jobless Claims",    "Jobless Claims",   True),   # lower is better
]

# Indicators where LOWER actual = better outcome (inversion)
LOWER_IS_BETTER = {"Unemployment", "Jobless Claims"}

CACHE_TTL_HOURS = 6


# ---------------------------------------------------------------------------
# Provider fetch
# ---------------------------------------------------------------------------

def _fetch_provider_economic() -> list:
    """
    Fetch economic calendar from the configured provider.
    Returns [] on access limits or failures — system continues with ECO score = 0.
    """
    provider = get_active_provider_name()
    raw = fetch_economic_calendar()
    if not raw:
        if provider == "fmp":
            print("[eco_agent] Economic calendar unavailable — ECO score = 0 (neutral).")
        else:
            print(f"[eco_agent] Provider '{provider}' returned no macro data — ECO score = 0 (neutral).")
    return raw


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _find_indicator(event_name: str):
    """Match FMP event name to our indicator list. Returns (label, inversion) or None."""
    for fmp_substr, label, inversion in INDICATORS:
        if fmp_substr.lower() in event_name.lower():
            return label, inversion
    return None


def _parse_events(raw_events: list) -> list:
    """
    Filter last 30 days of events, extract beat/miss/inline for our indicators.
    Returns list of dicts.
    """
    from datetime import date
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    results = []

    for ev in raw_events:
        date_str = ev.get("date", "")
        try:
            ev_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            try:
                ev_date = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        if ev_date < cutoff:
            continue

        name = ev.get("event", ev.get("name", ""))
        match = _find_indicator(name)
        if not match:
            continue

        label, inversion = match

        actual   = ev.get("actual")
        estimate = ev.get("estimate", ev.get("forecast"))

        # Determine beat/miss/inline
        if actual is None or actual == "":
            outcome = "inline"
        elif estimate is None or estimate == "":
            outcome = "inline"
        else:
            try:
                act = float(actual)
                est = float(estimate)
                if abs(act - est) < 1e-10:
                    outcome = "inline"
                elif label in LOWER_IS_BETTER:
                    # Lower is better: actual < estimate = beat
                    outcome = "beat" if act < est else "miss"
                else:
                    outcome = "beat" if act > est else "miss"
            except (ValueError, TypeError):
                outcome = "inline"

        # Inversion for inflation indicators: beat = bearish for stocks
        stocks_signal = outcome
        if inversion and outcome == "beat":
            stocks_signal = "miss_stocks"   # bad for stocks
        elif inversion and outcome == "miss":
            stocks_signal = "beat_stocks"   # good for stocks

        results.append({
            "date":           ev_date.strftime("%Y-%m-%d"),
            "label":          label,
            "actual":         actual,
            "estimate":       estimate,
            "outcome":        outcome,          # raw beat/miss/inline
            "stocks_signal":  stocks_signal,    # from stocks perspective
            "inversion":      inversion,
        })

    # Deduplicate: keep most recent per label
    seen = {}
    for r in sorted(results, key=lambda x: x["date"], reverse=True):
        if r["label"] not in seen:
            seen[r["label"]] = r
    return list(seen.values())


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_score(parsed: list) -> int:
    """
    Score overall US economy -2 to +2 from stocks perspective.
    Counts beats vs misses from the stocks_signal field.
    """
    beats  = sum(1 for r in parsed if r["stocks_signal"] in ("beat", "beat_stocks"))
    misses = sum(1 for r in parsed if r["stocks_signal"] in ("miss", "miss_stocks"))
    total  = beats + misses

    if total == 0:
        return 0

    ratio = (beats - misses) / total  # -1 to +1

    if ratio >= 0.6:
        return 2
    elif ratio >= 0.2:
        return 1
    elif ratio <= -0.6:
        return -2
    elif ratio <= -0.2:
        return -1
    else:
        return 0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_is_fresh() -> bool:
    if not CACHE_FILE.exists():
        return False
    stat  = CACHE_FILE.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime) < timedelta(hours=CACHE_TTL_HOURS)


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(parsed: list, score: int) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "score":      score,
        "indicators": parsed,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[eco_agent] Cache saved to {CACHE_FILE}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_economic(force: bool = False) -> dict:
    """
    Return {'score': int, 'indicators': list, 'fetched_at': str}.
    Uses cache if fresh.
    """
    if not force and _cache_is_fresh():
        cached = _load_cache()
        if cached:
            print("[eco_agent] Using cached economic data.")
            return cached

    raw    = _fetch_provider_economic()
    parsed = _parse_events(raw) if raw else []

    if not parsed:
        # Use stale cache or neutral
        cached = _load_cache()
        if cached:
            print("[eco_agent] No new data — returning stale cache.")
            return cached
        return {"score": 0, "indicators": [], "fetched_at": ""}

    score = _compute_score(parsed)
    _save_cache(parsed, score)
    return {"score": score, "indicators": parsed, "fetched_at": datetime.now(timezone.utc).isoformat()}


def get_eco_score() -> int:
    """Return cached economic score without triggering a fetch."""
    cached = _load_cache()
    if cached:
        return cached.get("score", 0)
    return 0


def get_cached_eco() -> dict:
    """Return full cached economic data."""
    return _load_cache() or {"score": 0, "indicators": []}


# ---------------------------------------------------------------------------
# CLI: heat-map table
# ---------------------------------------------------------------------------

BEAT_COLOR  = "\033[94m"   # blue
MISS_COLOR  = "\033[91m"   # red
NEUT_COLOR  = "\033[90m"   # gray
RESET       = "\033[0m"

def _color(outcome: str, text: str) -> str:
    if outcome in ("beat",):
        return f"{BEAT_COLOR}{text}{RESET}"
    elif outcome in ("miss",):
        return f"{MISS_COLOR}{text}{RESET}"
    elif outcome in ("beat_stocks",):
        return f"{BEAT_COLOR}[INV]{RESET} {text}"
    elif outcome in ("miss_stocks",):
        return f"{MISS_COLOR}[INV]{RESET} {text}"
    else:
        return f"{NEUT_COLOR}{text}{RESET}"


def _print_heatmap(eco: dict) -> None:
    score      = eco.get("score", 0)
    indicators = eco.get("indicators", [])
    fetched    = eco.get("fetched_at", "unknown")[:19]

    score_label = {2: "Strong", 1: "Positive", 0: "Neutral", -1: "Weak", -2: "Poor"}.get(score, "?")
    score_str   = f"+{score}" if score > 0 else str(score)

    print("\n" + "=" * 72)
    print(f"US Economic Heat Map   Score: {score_str} ({score_label})   as of {fetched}")
    print("[INV] = inflation inversion — beat = bullish USD, bearish stocks")
    print("=" * 72)
    print(f"{'Indicator':<18} {'Date':<12} {'Actual':>10} {'Forecast':>10} {'Signal':<20}")
    print("-" * 72)

    order = {label: i for i, (_, label, _) in enumerate(INDICATORS)}
    sorted_inds = sorted(indicators, key=lambda x: order.get(x["label"], 99))

    for r in sorted_inds:
        actual_s   = str(r["actual"])   if r["actual"]   is not None else "—"
        estimate_s = str(r["estimate"]) if r["estimate"] is not None else "—"
        signal_raw = r["stocks_signal"]

        signal_label = {
            "beat":        "BEAT",
            "miss":        "MISS",
            "inline":      "In-line",
            "beat_stocks": "BEAT (USD bullish)",
            "miss_stocks": "MISS (USD bearish)",
        }.get(signal_raw, signal_raw)

        colored_signal = _color(signal_raw, signal_label)
        print(f"{r['label']:<18} {r['date']:<12} {actual_s:>10} {estimate_s:>10} {colored_signal}")

    print("=" * 72)
    beats  = sum(1 for r in indicators if r["stocks_signal"] in ("beat", "beat_stocks"))
    misses = sum(1 for r in indicators if r["stocks_signal"] in ("miss", "miss_stocks"))
    inlines = sum(1 for r in indicators if r["stocks_signal"] == "inline")
    print(f"Last 30 days: {beats} beats | {misses} misses | {inlines} in-line → stocks score: {score_str}")
    print("=" * 72)


if __name__ == "__main__":
    force = "--force" in sys.argv
    eco   = fetch_economic(force=force)
    _print_heatmap(eco)
