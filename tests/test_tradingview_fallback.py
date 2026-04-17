import unittest
from unittest.mock import patch

from alerts import alert_engine
from providers import tradingview_provider


class TradingViewFallbackTests(unittest.TestCase):
    def setUp(self):
        tradingview_provider._STATUS_CACHE["result"] = None
        tradingview_provider._STATUS_CACHE["checked_at"] = 0.0

    def test_yfinance_used_only_when_tradingview_is_disconnected(self):
        with patch.object(alert_engine.tradingview_provider, "is_connected", return_value=False), \
             patch.object(alert_engine, "calculate_from_yfinance", return_value={"source": "yfinance"}) as yf_calc:
            result = alert_engine.get_live_indicators("MES")

        self.assertEqual(result["source"], "yfinance")
        yf_calc.assert_called_once_with("MES")

    def test_yfinance_not_used_when_tradingview_is_connected(self):
        with patch.object(alert_engine.tradingview_provider, "is_connected", return_value=True), \
             patch.object(alert_engine, "calculate_from_tradingview", return_value={}) as tv_calc, \
             patch.object(alert_engine, "calculate_from_yfinance", return_value={"source": "yfinance"}) as yf_calc:
            result = alert_engine.get_live_indicators("MES")

        self.assertEqual(result, {})
        tv_calc.assert_called_once_with("MES")
        yf_calc.assert_not_called()

    def test_current_price_prefers_tradingview_when_connected(self):
        with patch.object(alert_engine.tradingview_provider, "is_connected", return_value=True), \
             patch.object(alert_engine.tradingview_provider, "get_quote", return_value={"last": 5678.25}):
            price = alert_engine.get_current_price("MES")

        self.assertEqual(price, 5678.25)

    def test_tradingview_status_is_cached_between_checks(self):
        status_payload = {"success": True, "cdp_connected": False}
        with patch.object(tradingview_provider, "_run_tv_cli", return_value=status_payload) as cli_call:
            first = tradingview_provider.get_status()
            second = tradingview_provider.get_status()

        self.assertFalse(first["connected"])
        self.assertFalse(second["connected"])
        cli_call.assert_called_once_with(["status"], timeout=12)


if __name__ == "__main__":
    unittest.main()
