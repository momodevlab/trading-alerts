"""
notifier.py — Telegram + terminal alerts + persistent log

Functions:
    send_telegram(message)   async, sends to TELEGRAM_CHAT_ID
    print_alert(message)     prints to terminal with timestamp + separator
    fire_alert(message, alert_type, symbol)  calls both + appends to alerts_log.json

Run directly to send a test message:
    python alerts/notifier.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

ALERTS_LOG = Path(__file__).parent.parent / "data" / "alerts_log.json"
ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

async def send_telegram(message: str) -> bool:
    """Send message to Telegram. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[notifier] Telegram not configured — skipping send.")
        return False

    try:
        from telegram import Bot
        from telegram.constants import ParseMode
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return True
    except Exception as e:
        # Fallback: try plain text if HTML parse failed
        try:
            from telegram import Bot
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            return True
        except Exception as e2:
            print(f"[notifier] Telegram error: {e2}")
            return False


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

def print_alert(message: str) -> None:
    """Print alert to terminal with ET timestamp and separator."""
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except ImportError:
        et = None

    now = datetime.now(timezone.utc)
    if et:
        now = now.astimezone(et)
    ts = now.strftime("%Y-%m-%d %H:%M:%S ET")

    sep = "─" * 60
    print(f"\n{sep}")
    print(f"[{ts}]")
    print(message)
    print(sep)


# ---------------------------------------------------------------------------
# Persistent log
# ---------------------------------------------------------------------------

def _load_log() -> list:
    if ALERTS_LOG.exists():
        try:
            with open(ALERTS_LOG) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_log(entries: list) -> None:
    with open(ALERTS_LOG, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def fire_alert(message: str, alert_type: str = "INFO", symbol: str = "") -> None:
    """
    Send Telegram + print to terminal + append to alerts_log.json.

    alert_type: e.g. "SETUP", "ENTRY", "EXIT", "MORNING", "LEVELS", "INFO"
    symbol:     e.g. "MES", "SPY", ""
    """
    # 1. Terminal
    print_alert(message)

    # 2. Telegram — always use asyncio.run() for a fresh, reliable event loop
    try:
        asyncio.run(send_telegram(message))
    except Exception as e:
        print(f"[notifier] Telegram send failed: {e}")

    # 3. Append to log
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now_et = datetime.now(timezone.utc).astimezone(et)
    except ImportError:
        now_et = datetime.now(timezone.utc)

    entry = {
        "timestamp": now_et.isoformat(),
        "type": alert_type,
        "symbol": symbol,
        "message": message,
    }
    log = _load_log()
    log.append(entry)
    _save_log(log)


def get_recent_alerts(symbol: str = "", hours: float = 4.0) -> list:
    """Return alerts for a symbol fired within the last N hours."""
    from datetime import timedelta
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(timezone.utc).astimezone(et)
    except ImportError:
        now = datetime.now(timezone.utc)

    cutoff = now - timedelta(hours=hours)
    log = _load_log()
    results = []
    for entry in log:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                if not symbol or entry.get("symbol", "") == symbol:
                    results.append(entry)
        except (ValueError, KeyError):
            continue
    return results


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_msg = (
        "🔔 <b>TEST ALERT — Trading System Online</b>\n"
        "\n"
        "This is a test message from the trading research system.\n"
        "Telegram notifications are working correctly.\n"
        "\n"
        "<i>System: futures + stocks research and alert tool</i>\n"
        "<i>No trades are placed automatically.</i>"
    )

    print("Sending test alert...")
    fire_alert(test_msg, alert_type="TEST", symbol="TEST")
    print("\nTest complete. Check Telegram.")
