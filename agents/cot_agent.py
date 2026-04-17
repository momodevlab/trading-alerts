"""
cot_agent.py — CFTC Commitments of Traders (COT) fetcher, parser, scorer

Downloads TWO CFTC reports:
  - Financial Futures (fut_fin_txt): equity indices + forex → uses Lev_Money columns
  - Disaggregated (fut_disagg_txt): commodities (GC, SI, CL, NG) → uses M_Money columns

Scores each market -2 to +2 based on Leveraged Money / Managed Money net positioning.

Usage:
    python agents/cot_agent.py
    → prints ranked table, most bullish to most bearish

Cache: data/cot/cot_latest.json
Refresh: Fridays after 3:35 PM ET (CFTC releases ~3:30 PM ET), or if >24h old
"""

import csv
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR  = Path(__file__).parent.parent
CACHE_DIR = BASE_DIR / "data" / "cot"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "cot_latest.json"

# ---------------------------------------------------------------------------
# Market name → symbol mappings (verified from actual CFTC CSV headers)
# ---------------------------------------------------------------------------

# Financial Futures file (fut_fin_txt) — uses Lev_Money columns
FIN_MARKET_MAP = {
    "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE":    "ES",
    "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE":        "NQ",
    "DJIA x $5 - CHICAGO BOARD OF TRADE":              "YM",
    "EURO FX - CHICAGO MERCANTILE EXCHANGE":            "EURUSD",
    "BRITISH POUND - CHICAGO MERCANTILE EXCHANGE":      "GBPUSD",
    "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE":       "USDJPY",
}

# Disaggregated file (fut_disagg_txt) — uses M_Money columns
DISAGG_MARKET_MAP = {
    "GOLD - COMMODITY EXCHANGE INC.":                  "GC",
    "SILVER - COMMODITY EXCHANGE INC.":                "SI",
    "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE":     "CL",
    "NAT GAS NYME - NEW YORK MERCANTILE EXCHANGE":     "NG",
}

# Micro symbols map to their full-size COT equivalents
MICRO_TO_COT = {
    "MES": "ES",   "MNQ": "NQ",   "MYM": "YM",
    "MCL": "CL",   "MGC": "GC",   "MSI": "SI",
    "MNG": "NG",   "M2K": "RTY",
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
}


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _fin_url(year: int) -> str:
    return f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"


def _disagg_url(year: int) -> str:
    return f"https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"


def _download_zip(url: str) -> bytes:
    print(f"[cot_agent] Downloading: {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def _parse_zip_csv(zip_bytes: bytes, market_map: dict, long_col: str, short_col: str,
                   long_chg_col: str, short_chg_col: str) -> dict:
    """
    Extract CSV from zip and parse positions for the given market_map.
    Returns dict keyed by our symbol.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
        raw = z.read(csv_name).decode("latin-1")

    reader = csv.DictReader(io.StringIO(raw))
    rows_by_market: dict = {}

    for row in reader:
        market = row.get("Market_and_Exchange_Names", "").strip()
        if market in market_map:
            sym = market_map[market]
            rows_by_market.setdefault(sym, []).append(row)

    results = {}
    for sym, rows in rows_by_market.items():
        rows_sorted = sorted(
            rows,
            key=lambda r: r.get("Report_Date_as_YYYY-MM-DD", ""),
            reverse=True,
        )
        if not rows_sorted:
            continue

        latest = rows_sorted[0]
        prev   = rows_sorted[1] if len(rows_sorted) > 1 else None

        def _int(r, key):
            try:
                return int(str(r.get(key, "0")).replace(",", ""))
            except (ValueError, TypeError):
                return 0

        longs  = _int(latest, long_col)
        shorts = _int(latest, short_col)
        net    = longs - shorts

        if prev:
            p_longs  = _int(prev, long_col)
            p_shorts = _int(prev, short_col)
        else:
            p_longs, p_shorts = longs, shorts
        p_net = p_longs - p_shorts

        # Use change columns if available, else compute from consecutive rows
        long_chg  = _int(latest, long_chg_col)
        short_chg = _int(latest, short_chg_col)
        if long_chg == 0 and short_chg == 0 and prev:
            long_chg  = longs  - p_longs
            short_chg = shorts - p_shorts

        results[sym] = {
            "report_date":  latest.get("Report_Date_as_YYYY-MM-DD", ""),
            "longs":        longs,
            "shorts":       shorts,
            "net":          net,
            "prev_longs":   p_longs,
            "prev_shorts":  p_shorts,
            "prev_net":     p_net,
            "net_change":   net - p_net,
            "long_change":  long_chg,
            "short_change": short_chg,
        }

    return results


def _parse_fin_csv(zip_bytes: bytes) -> dict:
    """Parse Financial Futures report using Lev_Money columns."""
    return _parse_zip_csv(
        zip_bytes,
        market_map  = FIN_MARKET_MAP,
        long_col    = "Lev_Money_Positions_Long_All",
        short_col   = "Lev_Money_Positions_Short_All",
        long_chg_col  = "Change_in_Lev_Money_Long_All",
        short_chg_col = "Change_in_Lev_Money_Short_All",
    )


def _parse_disagg_csv(zip_bytes: bytes) -> dict:
    """Parse Disaggregated report using M_Money (Managed Money) columns."""
    return _parse_zip_csv(
        zip_bytes,
        market_map  = DISAGG_MARKET_MAP,
        long_col    = "M_Money_Positions_Long_All",
        short_col   = "M_Money_Positions_Short_All",
        long_chg_col  = "Change_in_M_Money_Long_All",
        short_chg_col = "Change_in_M_Money_Short_All",
    )


# ---------------------------------------------------------------------------
# Scoring: -2 to +2
# ---------------------------------------------------------------------------

def _score_cot(sym_data: dict) -> int:
    """
    Score net positioning on -2 to +2 scale.

    +2 = net long AND adding longs (both size and direction positive)
    +1 = net long OR adding longs (one condition)
     0 = neutral / mixed
    -1 = net short OR adding shorts
    -2 = net short AND adding shorts
    """
    net        = sym_data.get("net", 0)
    net_change = sym_data.get("net_change", 0)

    net_positive   = net > 0
    net_increasing = net_change > 0

    if net_positive and net_increasing:
        return 2
    elif net_positive and not net_increasing:
        return 1
    elif not net_positive and net_increasing:
        return -1
    else:
        return -2


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_is_fresh() -> bool:
    """
    Cache is fresh if:
    - File exists AND less than 24 hours old AND
    - Not Friday after 3:35 PM ET (new data available)
    """
    if not CACHE_FILE.exists():
        return False

    stat  = CACHE_FILE.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    age   = datetime.now(timezone.utc) - mtime
    if age > timedelta(hours=24):
        return False

    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except ImportError:
        return True

    now_et = datetime.now(timezone.utc).astimezone(et)
    if now_et.weekday() == 4:  # Friday
        release_time = now_et.replace(hour=15, minute=35, second=0, microsecond=0)
        if now_et >= release_time:
            mtime_et = mtime.astimezone(et)
            if mtime_et < release_time:
                return False  # stale — new data available

    return True


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(data: dict) -> None:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"[cot_agent] Cache saved to {CACHE_FILE}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_cot(force: bool = False) -> dict:
    """
    Return COT data dict (keyed by symbol).
    Downloads both Financial Futures and Disaggregated reports.
    Uses cache if fresh.
    """
    if not force and _cache_is_fresh():
        cached = _load_cache()
        if cached:
            print("[cot_agent] Using cached COT data.")
            return cached["data"]

    year = datetime.now().year
    raw_data = {}

    # --- Financial Futures (equity indices + forex) ---
    for yr in [year, year - 1]:
        try:
            fin_bytes  = _download_zip(_fin_url(yr))
            fin_data   = _parse_fin_csv(fin_bytes)
            raw_data.update(fin_data)
            print(f"[cot_agent] Financial futures parsed: {list(fin_data.keys())}")
            break
        except Exception as e:
            print(f"[cot_agent] Financial futures download failed ({yr}): {e}")

    # --- Disaggregated (commodities) ---
    for yr in [year, year - 1]:
        try:
            disagg_bytes = _download_zip(_disagg_url(yr))
            disagg_data  = _parse_disagg_csv(disagg_bytes)
            raw_data.update(disagg_data)
            print(f"[cot_agent] Disaggregated parsed: {list(disagg_data.keys())}")
            break
        except Exception as e:
            print(f"[cot_agent] Disaggregated download failed ({yr}): {e}")

    if not raw_data:
        cached = _load_cache()
        if cached:
            print("[cot_agent] All downloads failed — returning stale cache.")
            return cached["data"]
        return {}

    # Enrich with score
    for sym, d in raw_data.items():
        d["score"] = _score_cot(d)

    _save_cache(raw_data)
    return raw_data


def get_cot_score(symbol: str) -> int:
    """
    Return COT score for a symbol (micro or full-size).
    Returns 0 if not found.
    """
    full_sym = MICRO_TO_COT.get(symbol, symbol)
    data = fetch_cot()
    if full_sym in data:
        return data[full_sym].get("score", 0)
    return 0


def get_cached_cot() -> dict:
    """Return cached COT data without triggering a download."""
    cached = _load_cache()
    if cached:
        return cached["data"]
    return {}


# ---------------------------------------------------------------------------
# CLI: ranked table
# ---------------------------------------------------------------------------

def _print_table(data: dict) -> None:
    if not data:
        print("[cot_agent] No data available.")
        return

    scored = sorted(data.items(), key=lambda x: x[1].get("score", 0), reverse=True)

    header = f"{'Symbol':<10} {'Score':>5} {'Net':>12} {'Net Chg':>10} {'Longs':>10} {'Shorts':>10} {'Date':<12}"
    print("\n" + "=" * len(header))
    print("COT Leveraged/Managed Money Positioning  (most bullish → most bearish)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for sym, d in scored:
        score     = d.get("score", 0)
        score_str = f"+{score}" if score > 0 else str(score)
        net_chg   = d.get("net_change", 0)
        chg_str   = f"+{net_chg:,}" if net_chg > 0 else f"{net_chg:,}"
        net_val   = d.get("net", 0)
        net_str   = f"+{net_val:,}" if net_val > 0 else f"{net_val:,}"
        print(
            f"{sym:<10} {score_str:>5} {net_str:>12} {chg_str:>10} "
            f"{d.get('longs',0):>10,} {d.get('shorts',0):>10,} {d.get('report_date',''):<12}"
        )
    print("=" * len(header))


if __name__ == "__main__":
    force = "--force" in sys.argv
    data  = fetch_cot(force=force)
    _print_table(data)
