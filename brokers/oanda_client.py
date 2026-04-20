"""
oanda_client.py — Oanda v20 REST API client for automated forex order execution

Practice (paper) trading by default. Switch to live only when confident.

Required .env variables:
  OANDA_ACCOUNT_ID     — your account ID, e.g. 101-001-12345678-001
                         (shown at oanda.com → Manage Funds → API Access)
  OANDA_API_TOKEN      — personal access token from the same page
  OANDA_PAPER          — "true" = practice account, "false" = live (default: true)
  AUTO_TRADE_ENABLED   — "true" to execute real orders (default: false)
  ACCOUNT_SIZE         — starting account size in USD (default: 100)
  MAX_DAILY_LOSS_PCT   — stop trading if daily loss exceeds this % (default: 6)
  MAX_TRADES_PER_DAY   — max trades per day (default: 3)
  RISK_PCT             — risk per trade as % of account (default: 2)
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Allow imports from project root (alerts/notifier)
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PAPER         = os.getenv("OANDA_PAPER",          "true").lower() == "true"
AUTO_TRADE    = os.getenv("AUTO_TRADE_ENABLED",    "false").lower() == "true"
ACCOUNT_SIZE  = float(os.getenv("ACCOUNT_SIZE",   "100"))
MAX_LOSS_PCT  = float(os.getenv("MAX_DAILY_LOSS_PCT", "6"))
MAX_TRADES    = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
RISK_PCT      = float(os.getenv("RISK_PCT",        "2"))

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if PAPER else
    "https://api-fxtrade.oanda.com"
)

BASE_DIR       = Path(__file__).parent.parent
TRADE_LOG      = BASE_DIR / "data" / "trade_log.json"
OPEN_TRADES_DB = BASE_DIR / "data" / "oanda_open_trades.json"
TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Instrument helpers
# ---------------------------------------------------------------------------

# Convert our internal symbol (EURUSD) → Oanda instrument name (EUR_USD)
def _to_oanda_instrument(symbol: str) -> str:
    s = symbol.upper()
    return f"{s[:3]}_{s[3:]}"


# Pip size for each quote currency
_JPY_PAIRS = {'JPY'}  # quote currency → 1 pip = 0.01
_PIP_SIZE = {
    'USD': 0.0001, 'EUR': 0.0001, 'GBP': 0.0001,
    'CHF': 0.0001, 'CAD': 0.0001, 'AUD': 0.0001,
    'NZD': 0.0001, 'JPY': 0.01,
}


def pip_size(symbol: str) -> float:
    """Return the pip size for a forex pair."""
    quote = symbol[-3:].upper()
    return _PIP_SIZE.get(quote, 0.0001)


def price_to_pips(symbol: str, price_distance: float) -> float:
    """Convert a price distance to pips."""
    return price_distance / pip_size(symbol)


def calc_units(symbol: str, direction: str,
               entry: float, stop: float, risk_usd: float) -> int:
    """
    Calculate the number of Oanda units to trade given a dollar risk amount.

    Oanda units:
      positive = buy (long)
      negative = sell (short)

    Formula:
      units = risk_usd / stop_distance_in_USD_per_unit

    For USD-quoted pairs (EURUSD, GBPUSD, AUDUSD, NZDUSD):
      1 unit, 0.0001 move = $0.0001  →  units = risk_usd / stop_dist

    For JPY-quoted pairs (USDJPY, EURJPY, etc.):
      1 unit, 0.01 JPY move ≈ 0.01/entry USD  →  units = risk_usd / (stop_dist / entry)

    For other non-USD quote (USDCHF, USDCAD, EURCHF, etc.):
      Approximate by assuming rough 1:1 relationship — adequate for micro sizing.
    """
    stop_dist = abs(entry - stop)
    if stop_dist == 0:
        return 0

    quote = symbol[-3:].upper()

    if quote == 'USD':
        # EURUSD, GBPUSD, AUDUSD, NZDUSD — exact
        units_float = risk_usd / stop_dist
    elif quote == 'JPY':
        # USDJPY, EURJPY, GBPJPY, CADJPY, NZDJPY
        # 1 unit moves stop_dist JPY → stop_dist/entry USD
        units_float = risk_usd / (stop_dist / entry)
    else:
        # USDCHF, USDCAD, EURCHF, AUDCAD, NZDCAD, GBPAUD, AUDNZD, EURGBP
        # Approximate: treat as if quote ≈ USD (within ~5% for major pairs)
        units_float = risk_usd / stop_dist

    # Round to nearest 100 units, minimum 1,000 (micro lot)
    units = max(round(units_float / 100) * 100, 1000)

    return units if direction == 'long' else -units


# ---------------------------------------------------------------------------
# Oanda Client
# ---------------------------------------------------------------------------

class OandaClient:
    """
    Thin wrapper around Oanda v20 REST API.
    Handles order placement with automatic lot sizing and safety checks.
    """

    def __init__(self):
        self.account_id = os.getenv("OANDA_ACCOUNT_ID", "")
        self.token      = os.getenv("OANDA_API_TOKEN",  "")
        self.paper      = PAPER
        self.base       = BASE_URL

        self._daily_pnl    = 0.0
        self._pnl_date     = date.today()
        self._trades_today = 0

        mode = "PRACTICE" if PAPER else "⚠️ LIVE"
        log.info(f"[Oanda] Mode: {mode} | Auto-trade: {AUTO_TRADE}")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type":  "application/json",
        }

    def _get(self, path: str) -> Optional[dict]:
        try:
            r = requests.get(
                f"{self.base}{path}",
                headers=self._headers(), timeout=10
            )
            return r.json()
        except Exception as e:
            log.error(f"[Oanda] GET {path} error: {e}")
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
            log.error(f"[Oanda] POST {path} error: {e}")
            return None

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    def get_account_summary(self) -> Optional[dict]:
        """Return account summary (balance, NAV, margin used, etc.)."""
        if not self.account_id:
            log.warning("[Oanda] OANDA_ACCOUNT_ID not set")
            return None
        data = self._get(f"/v3/accounts/{self.account_id}/summary")
        return data.get("account") if data else None

    def get_balance(self) -> float:
        """Return current account balance in USD."""
        summary = self.get_account_summary()
        if summary and "balance" in summary:
            return float(summary["balance"])
        return 0.0

    def get_open_trades(self) -> list:
        """Return list of open trades."""
        data = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        return data.get("trades", []) if data else []

    def _get_available_balance(self) -> float:
        """
        Return the margin available for new trades from Oanda live.
        Uses marginAvailable (what Oanda will actually let you trade with),
        which accounts for the 50% hold on new deposits and any open margin.
        """
        summary = self.get_account_summary()
        if summary:
            return float(summary.get("marginAvailable", 0))
        return 0.0

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
        self._reset_daily_if_needed()
        max_loss = ACCOUNT_SIZE * (MAX_LOSS_PCT / 100)
        if self._daily_pnl <= -max_loss:
            log.warning(
                f"[Oanda] Daily loss limit hit: ${self._daily_pnl:.2f} "
                f"(limit: -${max_loss:.2f})"
            )
            return False
        return True

    def _within_trade_limit(self) -> bool:
        self._reset_daily_if_needed()
        if self._trades_today >= MAX_TRADES:
            log.warning(f"[Oanda] {MAX_TRADES}-trade daily limit reached")
            return False
        return True

    def is_safe_to_trade(self) -> bool:
        """All safety checks must pass before any order is placed."""
        if not self.account_id or not self.token:
            print("[Oanda] ❌ Missing credentials — OANDA_ACCOUNT_ID or OANDA_API_TOKEN not set")
            return False
        if not AUTO_TRADE:
            print("[Oanda] ⏸  AUTO_TRADE_ENABLED=false — alert only, no order placed")
            return False
        if not self._within_daily_loss_limit():
            print(f"[Oanda] 🛑 Daily loss limit hit — no more trades today")
            return False
        if not self._within_trade_limit():
            print(f"[Oanda] 🛑 {MAX_TRADES}-trade daily limit reached — no more trades today")
            return False
        return True

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol:    str,
        direction: str,     # 'long' or 'short'
        entry:     float,   # None = market order, otherwise limit
        stop:      float,
        tp1:       float,
        strategy:  str = "",
        order_type: str = "LIMIT",   # "MARKET" or "LIMIT"
    ) -> Optional[dict]:
        """
        Place a forex order with stop loss and take profit.

        Lot size is calculated from the live available balance fetched from Oanda
        right before placing, using RISK_PCT. This means:
          - Account at $60 available → risk $1.20/trade (2%)
          - Account at $120 available → risk $2.40/trade (2%)
          - Scales automatically as the account grows.

        Returns the Oanda order response dict, or None on failure.
        Logs to data/trade_log.json.
        """
        # Fetch live available balance — never use stale ACCOUNT_SIZE for sizing
        live_balance = self._get_available_balance()
        if live_balance <= 0:
            log.error("[Oanda] Could not fetch available balance — skipping order")
            return None

        risk_usd = live_balance * (RISK_PCT / 100)
        units    = calc_units(symbol, direction, entry, stop, risk_usd)

        if units == 0:
            log.error(f"[Oanda] Could not calculate units for {symbol}")
            return None

        instrument = _to_oanda_instrument(symbol)
        decimals   = 5 if pip_size(symbol) == 0.0001 else 3
        stop_pips  = price_to_pips(symbol, abs(entry - stop))
        tp1_pips   = price_to_pips(symbol, abs(tp1 - entry))

        # Build order body
        order_body: dict = {
            "instrument":     instrument,
            "units":          str(units),
            "stopLossOnFill": {
                "price":       f"{stop:.{decimals}f}",
                "timeInForce": "GTC",
            },
            "takeProfitOnFill": {
                "price":       f"{tp1:.{decimals}f}",
                "timeInForce": "GTC",
            },
        }

        if order_type == "MARKET":
            order_body["type"] = "MARKET"
        else:
            order_body["type"]          = "LIMIT"
            order_body["price"]         = f"{entry:.{decimals}f}"
            order_body["timeInForce"]   = "GTC"

        mode_tag = "[PRACTICE]" if self.paper else "[LIVE]"
        abs_units = abs(units)
        lot_size  = abs_units / 100_000
        log.info(
            f"[Oanda] {mode_tag} {direction.upper()} {symbol} | "
            f"{abs_units} units ({lot_size:.3f} lots) | "
            f"Entry: {entry:.{decimals}f} | Stop: {stop:.{decimals}f} "
            f"({stop_pips:.1f} pips ${risk_usd:.2f}) | "
            f"TP1: {tp1:.{decimals}f} ({tp1_pips:.1f} pips)"
        )

        response = self._post(
            f"/v3/accounts/{self.account_id}/orders",
            {"order": order_body}
        )

        # Determine success — Oanda returns orderCreateTransaction on success
        success = bool(
            response and (
                "orderCreateTransaction" in response or
                "orderFillTransaction"   in response
            )
        )

        self._log_trade({
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "broker":          "oanda",
            "mode":            "practice" if self.paper else "live",
            "strategy":        strategy,
            "symbol":          symbol,
            "instrument":      instrument,
            "direction":       direction,
            "units":           units,
            "lot_size":        round(abs_units / 100_000, 5),
            "entry":           entry,
            "stop":            stop,
            "tp1":             tp1,
            "stop_pips":       round(stop_pips, 1),
            "tp1_pips":        round(tp1_pips, 1),
            "risk_usd":        round(risk_usd, 2),
            "order_type":      order_type,
            "order_response":  response,
            "status":          "placed" if success else "failed",
        })

        if success:
            self._trades_today += 1
            log.info(f"[Oanda] Order placed successfully")

            # Extract trade ID from response for exit monitoring
            trade_id = None
            if response:
                fill = response.get("orderFillTransaction", {})
                trade_id = fill.get("tradeOpened", {}).get("tradeID")
                if not trade_id:
                    # Limit order — trade ID assigned when filled; track order ID for now
                    create = response.get("orderCreateTransaction", {})
                    trade_id = create.get("id")

            self._save_open_trade({
                "trade_id":    trade_id,
                "symbol":      symbol,
                "instrument":  instrument,
                "direction":   direction,
                "units":       units,
                "entry":       entry,
                "stop":        stop,
                "tp1":         tp1,
                "stop_pips":   round(stop_pips, 1),
                "tp1_pips":    round(tp1_pips, 1),
                "risk_usd":    round(risk_usd, 2),
                "strategy":    strategy,
                "opened_at":   datetime.now(timezone.utc).isoformat(),
                "alerted_tp1": False,
                "alerted_sl":  False,
            })

            self._send_execution_alert(
                symbol=symbol, direction=direction,
                units=units, entry=entry, stop=stop, tp1=tp1,
                stop_pips=stop_pips, tp1_pips=tp1_pips,
                risk_usd=risk_usd, lot_size=round(abs(units) / 100_000, 5),
                order_type=order_type, strategy=strategy,
                live_balance=live_balance,
            )
        else:
            error_msg = ""
            cancel_reason = ""
            if response:
                error_msg     = response.get("errorMessage", "")
                # Oanda puts cancel reason inside orderCancelTransaction
                cancel_tx     = response.get("orderCancelTransaction", {})
                cancel_reason = cancel_tx.get("reason", error_msg or str(response))
            log.error(f"[Oanda] Order failed/cancelled: {cancel_reason or error_msg}")
            self._send_cancel_alert(symbol, direction, entry, stop, tp1,
                                    risk_usd, cancel_reason or error_msg, strategy)

        return response

    # ------------------------------------------------------------------
    # Open trade tracking + exit monitoring
    # ------------------------------------------------------------------

    def _save_open_trade(self, trade: dict) -> None:
        """Persist a newly placed trade to the open trades DB."""
        trades = self._load_open_trades()
        trades.append(trade)
        with open(OPEN_TRADES_DB, "w") as f:
            json.dump(trades, f, indent=2, default=str)

    def _load_open_trades(self) -> list:
        if not OPEN_TRADES_DB.exists():
            return []
        try:
            with open(OPEN_TRADES_DB) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_all_open_trades(self, trades: list) -> None:
        with open(OPEN_TRADES_DB, "w") as f:
            json.dump(trades, f, indent=2, default=str)

    def check_exits(self) -> None:
        """
        Poll Oanda for the status of every tracked open trade.
        Fires Telegram alerts when:
          - Stop loss is hit  (trade closed at a loss)
          - TP1 is hit        (trade closed at a profit)

        Call this from the main monitoring loop every 60 seconds.
        """
        if not self.account_id or not self.token:
            return

        tracked = self._load_open_trades()
        if not tracked:
            return

        changed = False

        for trade in tracked:
            if trade.get("closed"):
                continue

            trade_id = trade.get("trade_id")
            symbol   = trade.get("symbol", "")
            dec      = 5 if pip_size(symbol) == 0.0001 else 3

            if not trade_id:
                continue

            # Fetch current trade state from Oanda
            data = self._get(f"/v3/accounts/{self.account_id}/trades/{trade_id}")
            if not data:
                continue

            t = data.get("trade", {})
            state       = t.get("state", "")
            realized_pl = float(t.get("realizedPL", 0))
            close_price = None

            if t.get("averageClosePrice"):
                close_price = float(t["averageClosePrice"])

            if state == "CLOSED":
                trade["closed"] = True
                changed = True

                entry     = trade.get("entry", 0)
                stop      = trade.get("stop",  0)
                tp1       = trade.get("tp1",   0)
                direction = trade.get("direction", "long")
                risk_usd  = trade.get("risk_usd", 0)
                strategy  = trade.get("strategy", "")

                # Update daily P&L
                self._daily_pnl += realized_pl

                if realized_pl >= 0:
                    # Closed at profit → TP hit
                    self._send_tp_alert(
                        symbol=symbol, direction=direction,
                        entry=entry, close_price=close_price or tp1,
                        tp1=tp1, realized_pl=realized_pl,
                        strategy=strategy, dec=dec,
                    )
                else:
                    # Closed at loss → stop hit
                    self._send_stop_alert(
                        symbol=symbol, direction=direction,
                        entry=entry, close_price=close_price or stop,
                        stop=stop, realized_pl=realized_pl,
                        risk_usd=risk_usd, strategy=strategy, dec=dec,
                    )

        if changed:
            self._save_all_open_trades(tracked)

    def _send_tp_alert(self, symbol, direction, entry, close_price,
                       tp1, realized_pl, strategy, dec) -> None:
        try:
            from alerts.notifier import fire_alert
            pips = price_to_pips(symbol, abs(close_price - entry))
            msg = (
                f"✅ <b>TP HIT — {symbol}</b>\n"
                f"Direction: {direction.capitalize()} | Strategy: {strategy}\n\n"
                f"Entry:  {entry:.{dec}f}\n"
                f"Close:  {close_price:.{dec}f}  (+{pips:.1f} pips)\n"
                f"P&amp;L: <b>+${realized_pl:.2f}</b> 🟢\n\n"
                f"Daily P&amp;L: ${self._daily_pnl:+.2f}"
            )
            fire_alert(msg, alert_type="TP_HIT", symbol=symbol)
        except Exception as e:
            log.error(f"[Oanda] Failed to send TP alert: {e}")

    def _send_stop_alert(self, symbol, direction, entry, close_price,
                         stop, realized_pl, risk_usd, strategy, dec) -> None:
        try:
            from alerts.notifier import fire_alert
            pips = price_to_pips(symbol, abs(close_price - entry))
            msg = (
                f"🚨 <b>STOPPED OUT — {symbol}</b>\n"
                f"Direction: {direction.capitalize()} | Strategy: {strategy}\n\n"
                f"Entry:  {entry:.{dec}f}\n"
                f"Stop:   {stop:.{dec}f}\n"
                f"Close:  {close_price:.{dec}f}  (-{pips:.1f} pips)\n"
                f"P&amp;L: <b>${realized_pl:.2f}</b> 🔴\n\n"
                f"Daily P&amp;L: ${self._daily_pnl:+.2f} "
                f"(limit: -${ACCOUNT_SIZE * MAX_LOSS_PCT / 100:.2f})"
            )
            fire_alert(msg, alert_type="STOP_HIT", symbol=symbol)
        except Exception as e:
            log.error(f"[Oanda] Failed to send stop alert: {e}")

    def _send_execution_alert(self, symbol, direction, units, entry, stop,
                               tp1, stop_pips, tp1_pips, risk_usd, lot_size,
                               order_type, strategy, live_balance=0.0) -> None:
        """Send Telegram confirmation when an order is successfully placed."""
        try:
            from alerts.notifier import fire_alert
            dec   = 5 if pip_size(symbol) == 0.0001 else 3
            arrow = "📈" if direction == "long" else "📉"
            rr    = round(tp1_pips / stop_pips, 1) if stop_pips else 0
            mode  = "LIVE" if not self.paper else "PRACTICE"

            msg = (
                f"{arrow} <b>ORDER PLACED [{mode}] — {symbol}</b>\n"
                f"Direction: {direction.capitalize()} | Strategy: {strategy}\n"
                f"Type: {order_type}\n\n"
                f"Entry:  {entry:.{dec}f}\n"
                f"Stop:   {stop:.{dec}f}  ({stop_pips:.1f} pips | ${risk_usd:.2f} risk)\n"
                f"TP1:    {tp1:.{dec}f}  ({tp1_pips:.1f} pips | R:R {rr}:1)\n\n"
                f"Size: {abs(units):,} units ({lot_size:.5f} lots)\n"
                f"Balance: ${live_balance:.2f} | Risk: {RISK_PCT}% = ${risk_usd:.2f}\n"
                f"Daily P&amp;L: ${self._daily_pnl:+.2f}"
            )
            fire_alert(msg, alert_type="ORDER_PLACED", symbol=symbol)
        except Exception as e:
            print(f"[Oanda] ❌ Failed to send execution alert: {e}")

    def _send_cancel_alert(self, symbol: str, direction: str, entry: float,
                           stop: float, tp1: float, risk_usd: float,
                           reason: str, strategy: str) -> None:
        """Send Telegram alert when Oanda cancels or rejects an order."""
        try:
            from alerts.notifier import fire_alert
            dec = 5 if pip_size(symbol) == 0.0001 else 3

            # Make the reason human-readable
            reason_map = {
                "INSUFFICIENT_MARGIN": "Insufficient margin — available balance too low for this lot size",
                "MARKET_HALTED":       "Market halted — trading paused on this pair",
                "CLOSING_MARKET":      "Market closing — order rejected near session end",
                "ACCOUNT_NOT_TRADEABLE_ON_FILL": "Account not tradeable",
            }
            friendly = reason_map.get(reason, reason)

            live_balance = self._get_available_balance()

            msg = (
                f"⚠️ <b>ORDER CANCELLED — {symbol}</b>\n"
                f"Direction: {direction.capitalize()} | Strategy: {strategy}\n\n"
                f"Entry: {entry:.{dec}f} | Stop: {stop:.{dec}f} | TP: {tp1:.{dec}f}\n\n"
                f"Reason: {friendly}\n"
                f"Available balance: ${live_balance:.2f} | Attempted risk: ${risk_usd:.2f}\n\n"
                f"No position opened — manual entry needed if you want this trade."
            )
            fire_alert(msg, alert_type="ORDER_CANCELLED", symbol=symbol)
        except Exception as e:
            log.error(f"[Oanda] Failed to send cancel alert: {e}")

    def close_all_trades(self) -> None:
        """Close all open trades — use in emergencies."""
        trades = self.get_open_trades()
        for trade in trades:
            tid = trade.get("id")
            if tid:
                self._post(
                    f"/v3/accounts/{self.account_id}/trades/{tid}/close",
                    {"units": "ALL"}
                )
                log.info(f"[Oanda] Closed trade {tid}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
            log.error(f"[Oanda] Failed to write trade log: {e}")

    def status_summary(self) -> str:
        """Return a one-line status string for logging."""
        mode      = "PRACTICE" if self.paper else "LIVE"
        auto      = "ON" if AUTO_TRADE else "OFF"
        available = self._get_available_balance()
        risk      = round(available * RISK_PCT / 100, 2)
        return (
            f"Oanda {mode} | Auto-trade {auto} | "
            f"Available: ${available:.2f} | Risk/trade: ${risk:.2f} ({RISK_PCT}%) | "
            f"Today: {self._trades_today}/{MAX_TRADES} trades | "
            f"Daily P&L: ${self._daily_pnl:+.2f}"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_client: Optional[OandaClient] = None


def get_client() -> OandaClient:
    """Return the shared OandaClient instance."""
    global _client
    if _client is None:
        _client = OandaClient()
    return _client


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    client = OandaClient()
    print(f"\nPractice mode: {client.paper}")
    print(f"Auto-trade:    {AUTO_TRADE}")
    print(f"Account size:  ${ACCOUNT_SIZE}")
    print(f"Risk/trade:    {RISK_PCT}% = ${ACCOUNT_SIZE * RISK_PCT / 100:.2f}")

    if not client.account_id or not client.token:
        print("\n⚠️  OANDA_ACCOUNT_ID or OANDA_API_TOKEN not set in .env")
        print("   Add them and re-run this test.")
        sys.exit(1)

    print("\nFetching account summary...")
    summary = client.get_account_summary()
    if not summary:
        print("❌ Could not reach Oanda API — check credentials")
        sys.exit(1)

    balance = float(summary.get("balance", 0))
    nav     = float(summary.get("NAV", 0))
    margin  = float(summary.get("marginUsed", 0))
    print(f"✓ Balance:     ${balance:.2f}")
    print(f"✓ NAV:         ${nav:.2f}")
    print(f"✓ Margin used: ${margin:.2f}")

    trades = client.get_open_trades()
    print(f"✓ Open trades: {len(trades)}")

    print("\nLot size preview (2% risk on $100 account, 20-pip stop):")
    for pair in ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD']:
        entry = 1.1000 if 'JPY' not in pair else 148.00
        stop  = entry - 0.0020 if 'JPY' not in pair else entry - 0.20
        u = calc_units(pair, 'long', entry, stop, 2.0)
        lots = abs(u) / 100_000
        print(f"  {pair}: {abs(u):,} units ({lots:.4f} lots)")

    print(f"\n{client.status_summary()}")
    print("\n✅ Oanda connection test complete")
