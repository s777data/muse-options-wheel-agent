import unittest
import json
import tempfile
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import order_lifecycle as lifecycle


class OrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.reviewed = {
            "type": "limit",
            "quantity": "1",
            "price": "4.30",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
            "legs": [{"option_id": "opt-1", "side": "sell", "position_effect": "open", "ratio_quantity": 1}],
            "tick_size": "0.10",
            "chain_symbol": "FIGR",
            "min_credit": "4.00",
        }
        self.config = lifecycle.Config("acct", "telegram:123", 3, 60, Decimal("0.05"), Decimal("4.00"))

    def test_filled_order_not_replaced_and_notifies(self):
        calls = []

        def fake_mcp(tool, args):
            calls.append((tool, args))
            if tool == "place_option_order":
                return {"order": {"id": "o1", "chain_symbol": "FIGR", "state": "confirmed", "quantity": "1", "price": "4.30", "legs": self.reviewed["legs"]}}
            if tool == "get_option_orders":
                return {"orders": [{"id": "o1", "state": "filled", "processed_quantity": "1", "pending_quantity": "0", "legs": [{**self.reviewed["legs"][0], "option_type": "put", "strike_price": "32.50", "expiration_date": "2026-11-20", "executions": [{"quantity": "1", "price": "4.30", "timestamp": "2026-09-25T16:00:00Z"}]}]}]}
            raise AssertionError(tool)

        with patch.object(lifecycle, "call_mcp", side_effect=fake_mcp), patch.object(lifecycle, "send_telegram") as notify, patch.object(lifecycle, "setup_cron", return_value=True) as schedule:
            result = lifecycle.run(self.reviewed, self.config, sleep_fn=lambda _: None)
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(notify.call_count, 2)
        schedule.assert_called_once()
        self.assertTrue(result["cron_created"])
        self.assertEqual([tool for tool, _ in calls], ["place_option_order", "get_option_orders"])

    def test_setup_cron_schedules_next_day_after_cutoff(self):
        captured = {}

        def fake_run(command, *, env=None, input_text=None):
            captured["command"] = command
            return ""

        now = datetime(2026, 10, 28, 14, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        with patch.object(lifecycle, "_run", side_effect=fake_run):
            self.assertTrue(lifecycle.setup_cron(self.config, {"chain_symbol": "FIGR"}, now=now))

        self.assertIn("2026-10-29T13:05:00-07:00", captured["command"])
        self.assertIn("--name", captured["command"])
        self.assertIn("order-fill-report-FIGR", captured["command"])

    def test_setup_cron_schedules_same_day_before_cutoff(self):
        captured = {}

        def fake_run(command, *, env=None, input_text=None):
            captured["command"] = command
            return ""

        now = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        with patch.object(lifecycle, "_run", side_effect=fake_run):
            self.assertTrue(lifecycle.setup_cron(self.config, {"chain_symbol": "FIGR"}, now=now))

        self.assertIn("2026-10-28T13:05:00-07:00", captured["command"])

    def test_unfilled_order_cancels_then_reprices_and_fills(self):
        status_calls = 0
        placed_prices = []

        def fake_mcp(tool, args):
            nonlocal status_calls
            if tool == "place_option_order":
                placed_prices.append(args["price"])
                return {"order": {"id": f"o{len(placed_prices)}", "chain_symbol": "FIGR", "state": "confirmed", "quantity": "1", "price": args["price"], "legs": self.reviewed["legs"]}}
            if tool == "get_option_orders":
                status_calls += 1
                if status_calls == 1:
                    return {"orders": [{"id": "o1", "state": "confirmed", "pending_quantity": "1", "legs": self.reviewed["legs"]}]}
                return {"orders": [{"id": "o1", "state": "cancelled", "pending_quantity": "0", "legs": self.reviewed["legs"]}]} if status_calls == 2 else {"orders": [{"id": "o2", "state": "filled", "processed_quantity": "1", "pending_quantity": "0", "legs": [{**self.reviewed["legs"][0], "option_type": "put", "strike_price": "32.50", "expiration_date": "2026-11-20", "executions": [{"quantity": "1", "price": "4.10", "timestamp": "2026-09-25T16:00:00Z"}]}]}]}
            if tool == "cancel_option_order":
                return {"accepted": True}
            if tool == "get_option_quotes":
                return {"results": [{"quote": {"bid_price": "4.00", "ask_price": "4.30"}}]}
            raise AssertionError(tool)

        with patch.object(lifecycle, "call_mcp", side_effect=fake_mcp), patch.object(lifecycle, "send_telegram"), patch.object(lifecycle, "setup_cron", return_value=False):
            result = lifecycle.run(self.reviewed, self.config, sleep_fn=lambda _: None)
        self.assertEqual(result["status"], "filled")
        self.assertEqual(placed_prices, ["4.30", "4.20"])
        self.assertEqual(result["attempts"], 2)

    def test_replacement_price_stays_inside_quote_and_tick(self):
        quote = {"bid_price": "4.00", "ask_price": "4.30"}
        self.assertEqual(lifecycle.replacement_price(self.reviewed, quote, Decimal("0.05")), Decimal("4.20"))

    def test_telegram_notification_is_deduplicated_by_provider_destination_and_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "dedup.json"
            with patch.object(lifecycle, "_run") as command:
                self.assertTrue(lifecycle.send_telegram("telegram:123", "same message", state_path=state_path))
                self.assertFalse(lifecycle.send_telegram("telegram:123", "same message", state_path=state_path))
                self.assertTrue(lifecycle.send_telegram("telegram:456", "same message", state_path=state_path))
                self.assertTrue(lifecycle.send_telegram("telegram:123", "different message", state_path=state_path))
            self.assertEqual(command.call_count, 3)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["version"], 1)
            self.assertEqual(len(state["notifications"]), 3)
            self.assertTrue(all(entry["provider"] == "telegram" for entry in state["notifications"].values()))

    def test_failed_telegram_send_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "dedup.json"
            with patch.object(lifecycle, "_run", side_effect=lifecycle.LifecycleError("send failed")):
                with self.assertRaises(lifecycle.LifecycleError):
                    lifecycle.send_telegram("telegram:123", "not sent", state_path=state_path)
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
