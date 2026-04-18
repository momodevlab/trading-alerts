"""
tradovate_client.py — Tradovate REST API client for automated order execution

Paper trading by default. Switch to live only when you're confident.

Required .env variables:
  TRADOVATE_USERNAME      — your Tradovate login email
  TRADOVATE_PASSWORD      — your Tradovate password
  TRADOVATE_APP_ID        — app name you registered at tradovate.com/profile
  TRADOVATE_APP_VERSION   — e.g. "1.0"
  TRADOVATE_CID           — client ID from Tradovate developer portal
  TRADOVATE_SECRET        — client secret from Tradovate developer portal
  TRADOVATE_PAPER         — "true" = demo account, "false" = live (default: true)
  AUTO_TRADE_ENABLED      — "true" to execute real orders (default: false)
  ACCOUNT_SIZE            — starting account size in USD (default: 250)
  MAX_DAILY_LOSS_PCT      — stop trading if daily loss exceeds this % (default: 6)
  MAX_CONTRACTS           — max contracts per trade (default: 1)
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PAPER            = os.getenv("TRADOVATE_PAPER",       "true").lower() == "true"
AUTO_TRADE       = os.getenv("AUTO_TRADE_ENABLED",    "false").lower() == "true"
ACCOUNT_SIZE     = float(os.getenv("ACCOUNT_SIZE",    "250"))
MAX_LOSS_PCT     = float(os.getenv("MAX_DAILY_LOSS_PCT", "6"))
MAX_CONTRACTS    = int(os.getenv("MAX_CONTRACTS",     "1"))

BASE_URL = (
    "https://demo.tradovateapi.com/v1" if PAPER
    else "https://live.tradovateapi.com/v1"
)

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Contract month helpers
# ---------------------------------------------------------------------------

# CME quarterly months: H=Mar, M=Jun, U=Sep, Z=Dec
_QUARTERLY = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}

# Symbols that roll quarterly (equity index + FX)
_QUARTERLY_SYMBOLS = {'MES', 'MNQ', 'MYM', 'M2K', 'MSI'}

# Symbols that roll monthly (energy, metals)
_MONTHLY_CODES = {
    1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
    7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z',
}


def get_front_month_contract(symbol: str) -> str:
    """
    Return the active front-month contract symbol for Tradovate.
    E.g. MES → MESM6 (June 2026)

    Rolls approximately 10 days before expiry:
    - Quarterly (MES, MNQ, MYM, M2K, MSI): 3rd Friday of Mar/Jun/Sep/Dec
    - Monthly (MCL, MGC, MNG): 1st of each month
    """
    now = datetime.now(timezone.utc)
    year = now.year
    month = now.month
    day = now.day

    sym = symbol.upper()

    if sym in _QUARTERLY_SYMBOLS:
        # Find next quarterly expiry month
        quarterly_months = [3, 6, 9, 12]
        for qm in quarterly_months:
            # Estimate expiry: 3rd Friday of qm
            # Simple approximation: 15th–21st of the month
            expiry_approx = date(year, qm, 15)
            roll_date = expiry_approx - timedelta(days=10)
            if date(year, month, day) < roll_date:
                yr_code = str(year)[-1]
                return f"{sym}{_QUARTERLY[qm]}{yr_code}"
        # If past Dec expiry, roll to March next year
        return f"{sym}H{str(year + 1)[-1]}"

    else:
        # Monthly roll (MCL, MGC, MNG)
        # Roll 10 days before end of current month
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        roll_day = last_day - 10
        if day >= roll_day:
            # Roll to next month
            next_m = month + 1 if month < 12 else 1
            next_y = year if month < 12 else year + 1
            return f"{sym}{_MONTHLY_CODES[next_m]}{str(next_y)[-1]}"
        else:
            return f"{sym}{_MONTHLY_CODES[month]}{str(year)[-1]}"


# ---------------------------------------------------------------------------
# Tradovate Client
# ---------------------------------------------------------------------------

class TradovateClient:
    """
    Thin wrapper around Tradovate REST API.
    Handles auth token refresh, order placement, and safety checks.
    """

    def __init__(self):
        self.username    = os.getenv("TRADOVATE_USERNAME", "")
        self.password    = os.getenv("TRADOVATE_PASSWORD", "")
        self.app_id      = os.getenv("TRADOVATE_APP_ID", "TradingAlerts")
        self.app_version = os.getenv("TRADOVATE_APP_VERSION", "1.0")
        self.cid         = int(os.getenv("TRADOVATE_CID", "0"))
        self.secret      = os.getenv("TRADOVATE_SECRET", "")

        self._token        = None
        self._token_expiry = 0.0
        self._account_id   = None
        self._account_spec = None

        self._daily_pnl    = 0.0   # tracked in-memory, reset at midnight
        self._pnl_date     = date.today()
        self._trades_today = 0

        self.paper = PAPER
        self.base  = BASE_URL

        mode = "PAPER/DEMO" if PAPER else "⚠️ LIVE"
        log.info(f"[Tradovate] Mode: {mode} | Auto-trade: {AUTO_TRADE}")

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _authenticate(self) -> bool:
        """Get or refresh access token. Returns True on success."""
        if not self.username or not self.password or not self.secret:
            log.warning("[Tradovate] Missing credentials — check .env")
            return False

        payload = {
            "name":       self.username,
            "password":   self.password,
            "appId":      self.app_id,
            "appVersion": self.app_version,
            "cid":        self.cid,
            "sec":        self.secret,
        }
        try:
            r = requests.post(
                f"{self.base}/auth/accesstokenrequest",
                json=payload, timeout=10
            )
            data = r.json()
            if "accessToken" not in data:
                log.error(f"[Tradovate] Auth failed: {data.get('errorText', data)}")
                return False

            self._token        = data["accessToken"]
            # Token valid for 80 minutes — refresh at 70 minutes
            self._token_expiry = time.time() + 4200
            log.info("[Tradovate] Authenticated successfully")
            return True
        except Exception as e:
            log.error(f"[Tradovate] Auth error: {e}")
            return False

    def _headers(self) -> dict:
        if not self._token or time.time() > self._token_expiry:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    def _get(self, path: str) -> Optional[dict]:
        try:
            r = requests.get(f"{self.base}{path}", headers=self._headers(), timeout=10)
            return r.json()
        except Exception as e:
            log.error(f"[Tradovate] GET {path} error: {e}")
            return None

    def _post(self, path: str, payload: dict) -> Optional[dict]:
        try:
            r = requests.post(
                f"{self.base}{path}",
                headers=self._headers(),
                json=payload, timeout=10
            )
            return r.json()
        except Exception as e:
            log.error(f"[Tradovate] POST {path} error: {e}")
            return None

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def _load_account(self) -> bool:
        """Load account ID and spec on first use."""
        if self._account_id:
            return True
        data = self._get("/account/list")
        if not data or not isinstance(data, list) or len(data) == 0:
            log.error("[Tradovate] No accounts found")
            return False
        acct = data[0]
        self._account_id   = acct["id"]
        self._account_spec = acct["name"]
        log.info(f"[Tradovate] Account: {self._account_spec} (ID {self._account_id})")
        return True

    def get_cash_balance(self) -> float:
        """Return current cash balance."""
        if not self._load_account():
            return 0.0
        data = self._post(
            "/cashBalance/getCashBalanceSnapshot",
            {"accountId": self._account_id}
        )
        if data and "totalCashValue" in data:
            return float(data["totalCashValue"])
        return 0.0

    def get_positions(self) -> list:
        """Return list of open positions."""
        data = self._get("/position/list")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Safety checks
    # ------------------------------------------------------------------

    def _reset_daily_if_needed(self):
        today = date.today()
        if today != self._pnl_date:
            self._daily_pnl    = 0.0
            self._trades_today = 0
            self._pnl_date     = today

    def _within_daily_loss_limit(self) -> bool:
        """Return True if we have NOT exceeded the daily loss limit."""
        self._reset_daily_if_needed()
        max_loss = ACCOUNT_SIZE * (MAX_LOSS_PCT / 100)
        if self._daily_pnl <= -max_loss:
            log.warning(
                f"[Tradovate] Daily loss limit hit: {self._daily_pnl:.2f} "
                f"(limit: -{max_loss:.2f})"
            )
            return False
        return True

    def _within_trade_limit(self) -> bool:
        """Return True if under 3 trades today (PDT-style discipline)."""
        self._reset_daily_if_needed()
        if self._trades_today >= 3:
            log.warning(f"[Tradovate] 3-trade daily limit reached")
            return False
        return True

    def is_safe_to_trade(self) -> bool:
        """All safety checks must pass before any order is placed."""
        if not AUTO_TRADE:
            log.info("[Tradovate] AUTO_TRADE_ENABLED=false — alert only, no order")
            return False
        if not self._within_daily_loss_limit():
            return False
        if not self._within_trade_limit():
            return False
        return True

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_bracket_order(
        self,
        symbol:    str,
        direction: str,     # 'long' or 'short'
        qty:       int,
        entry:     float,
        stop:      float,
        tp1:       float,
        strategy:  str = "",
    ) -> Optional[dict]:
        """
        Place a limit entry order with a bracket (stop + TP1).
        Uses Tradovate's placeOSO endpoint.

        Returns the order response dict, or None on failure.
        Logs everything to data/trade_log.json.
        """
        if not self._load_account():
            return None

        contract = get_front_month_contract(symbol)
        action   = "Buy" if direction == "long" else "Sell"
        opp      = "Sell" if direction == "long" else "Buy"

        risk_pts  = abs(entry - stop)
        tp1_pts   = abs(tp1 - entry)
        pv        = self._point_value(symbol)
        risk_usd  = round(risk_pts * pv, 2)
        tp1_usd   = round(tp1_pts * pv, 2)

        payload = {
            "entryOrder": {
                "accountSpec": self._account_spec,
                "accountId":   self._account_id,
                "action":      action,
                "symbol":      contract,
                "orderQty":    qty,
                "orderType":   "Limit",
                "price":       round(entry, 4),
                "isAutomated": True,
            },
            "brackets": [
                {
                    "qty":       qty,
                    "stopPrice": round(stop, 4),
                    "target":    round(tp1, 4),
                }
            ],
        }

        mode_tag = "[PAPER]" if self.paper else "[LIVE]"
        log.info(
            f"[Tradovate] {mode_tag} Placing {direction.upper()} {qty}× {contract} | "
            f"Entry: {entry} | Stop: {stop} ({risk_pts:.2f}pts ${risk_usd}) | "
            f"TP1: {tp1} ({tp1_pts:.2f}pts ${tp1_usd})"
        )

        response = self._post("/order/placeOSO", payload)

        # Log regardless of success/failure
        self._log_trade({
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "mode":        "paper" if self.paper else "live",
            "strategy":    strategy,
            "symbol":      symbol,
            "contract":    contract,
            "direction":   direction,
            "qty":         qty,
            "entry":       entry,
            "stop":        stop,
            "tp1":         tp1,
            "risk_pts":    risk_pts,
            "risk_usd":    risk_usd,
            "tp1_usd":     tp1_usd,
            "order_response": response,
            "status":      "placed" if response and "orderId" in str(response) else "failed",
        })

        if response:
            self._trades_today += 1
            log.info(f"[Tradovate] Order response: {response}")
        else:
            log.error("[Tradovate] Order placement failed — check credentials and account")

        return response

    def cancel_all_orders(self) -> None:
        """Cancel all open orders — use in emergencies."""
        if not self._load_account():
            return
        orders = self._get("/order/list")
        if not isinstance(orders, list):
            return
        for order in orders:
            if order.get("ordStatus") in ("Working", "PendingNew"):
                oid = order.get("id")
                self._post("/order/cancelOrder", {"orderId": oid})
                log.info(f"[Tradovate] Cancelled order {oid}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _point_value(symbol: str) -> float:
        pv = {
            'MES': 5.0, 'MNQ': 2.0, 'MYM': 0.50,
            'M2K': 5.0, 'MCL': 100.0, 'MGC': 10.0,
            'MSI': 25.0, 'MNG': 250.0,
        }
        return pv.get(symbol.upper(), 1.0)

    def _log_trade(self, entry: dict) -> None:
        try:
            log_data = []
            if TRADE_LOG.exists():
                with open(TRADE_LOG) as f:
                    log_data = json.load(f)
            log_data.append(entry)
            with open(TRADE_LOG, "w") as f:
                json.dump(log_data, f, indent=2, default=str)
        except Exception as e:
            log.error(f"[Tradovate] Failed to write trade log: {e}")

    def status_summary(self) -> str:
        """Return a one-line status string for logging."""
        mode = "PAPER" if self.paper else "LIVE"
        auto = "ON" if AUTO_TRADE else "OFF"
        bal  = self.get_cash_balance()
        return (
            f"Tradovate {mode} | Auto-trade {auto} | "
            f"Balance: ${bal:.2f} | "
            f"Today: {self._trades_today}/3 trades | "
            f"Daily P&L: ${self._daily_pnl:+.2f}"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: Optional[TradovateClient] = None


def get_client() -> TradovateClient:
    """Return the shared TradovateClient instance."""
    global _client
    if _client is None:
        _client = TradovateClient()
    return _client


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    client = TradovateClient()
    print(f"\nPaper mode: {client.paper}")
    print(f"Auto-trade: {AUTO_TRADE}")

    print("\nAuthenticating...")
    ok = client._authenticate()
    if not ok:
        print("❌ Auth failed — check TRADOVATE_* credentials in .env")
        sys.exit(1)

    print("✓ Authenticated")
    client._load_account()
    print(f"✓ Account: {client._account_spec} (ID {client._account_id})")

    bal = client.get_cash_balance()
    print(f"✓ Balance: ${bal:.2f}")

    positions = client.get_positions()
    print(f"✓ Open positions: {len(positions)}")

    print("\nContract mapping:")
    for sym in ['MES', 'MNQ', 'MCL', 'MGC', 'MNG']:
        print(f"  {sym} → {get_front_month_contract(sym)}")

    print(f"\n{client.status_summary()}")
    print("\n✅ Tradovate connection test complete")
