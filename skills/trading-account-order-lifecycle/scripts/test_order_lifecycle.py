import unittest
import json
import os
import sys
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
        self.config = lifecycle.Config("acct", "claude-chat", 3, 60, Decimal("0.05"), Decimal("4.00"))

    def test_filled_order_not_replaced_and_notifies(self):
        calls = []

        def fake_mcp(tool, args):
            calls.append((tool, args))
            if tool == "place_option_order":
                return {"order": {"id": "o1", "chain_symbol": "FIGR", "state": "confirmed", "quantity": "1", "price": "4.30", "legs": self.reviewed["legs"]}}
            if tool == "get_option_orders":
                return {"orders": [{"id": "o1", "state": "filled", "processed_quantity": "1", "pending_quantity": "0", "legs": [{**self.reviewed["legs"][0], "option_type": "put", "strike_price": "32.50", "expiration_date": "2026-11-20", "executions": [{"quantity": "1", "price": "4.30", "timestamp": "2026-09-25T16:00:00Z"}]}]}]}
            raise AssertionError(tool)

        with patch.object(lifecycle, "_lifecycle_enabled", return_value=True), patch.object(lifecycle, "call_mcp", side_effect=fake_mcp), patch.object(lifecycle, "record_notification") as notify, patch.object(lifecycle, "schedule_fill_report", return_value=True) as schedule:
            result = lifecycle.run(self.reviewed, self.config, sleep_fn=lambda _: None)
        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(notify.call_count, 2)
        schedule.assert_called_once()
        self.assertTrue(result["fill_report_scheduled"])
        self.assertEqual([tool for tool, _ in calls], ["place_option_order", "get_option_orders"])

    # D-26: the single-shot entry point closes out a fill (notify +
    # fill-report artifact) and never schedules a report when unfilled
    def test_place_single_filled_closes_out(self):
        def fake_mcp(tool, args):
            if tool == "place_option_order":
                return {"order": {"id": "o9", "chain_symbol": "SMCI", "state": "unconfirmed", "quantity": "1", "price": "2.46", "legs": self.reviewed["legs"]}}
            if tool == "get_option_orders":
                return {"orders": [{"id": "o9", "chain_symbol": "SMCI", "state": "filled", "processed_quantity": "1", "pending_quantity": "0", "legs": [{**self.reviewed["legs"][0], "option_type": "put", "strike_price": "30.00", "expiration_date": "2026-12-18", "executions": [{"quantity": "1", "price": "2.46", "timestamp": "2026-11-11T19:38:25Z"}]}]}]}
            raise AssertionError(tool)

        with patch.object(lifecycle, "_lifecycle_enabled", return_value=True), patch.object(lifecycle, "call_mcp", side_effect=fake_mcp), patch.object(lifecycle, "record_notification") as notify, patch.object(lifecycle, "schedule_fill_report", return_value=True) as schedule:
            result = lifecycle.place_single(self.reviewed, self.config, Decimal("2.46"))
        self.assertEqual(result["status"], "filled")
        self.assertTrue(result["fill_report_scheduled"])
        schedule.assert_called_once()
        self.assertEqual(notify.call_count, 2)

    def test_place_single_unfilled_schedules_nothing(self):
        def fake_mcp(tool, args):
            if tool == "place_option_order":
                return {"order": {"id": "o9", "chain_symbol": "SMCI", "state": "unconfirmed", "quantity": "1", "price": "2.46", "legs": self.reviewed["legs"]}}
            if tool == "get_option_orders":
                return {"orders": [{"id": "o9", "chain_symbol": "SMCI", "state": "confirmed", "processed_quantity": "0", "pending_quantity": "1", "legs": [{**self.reviewed["legs"][0], "option_type": "put", "strike_price": "30.00", "expiration_date": "2026-12-18"}]}]}
            raise AssertionError(tool)

        with patch.object(lifecycle, "_lifecycle_enabled", return_value=True), patch.object(lifecycle, "call_mcp", side_effect=fake_mcp), patch.object(lifecycle, "record_notification"), patch.object(lifecycle, "schedule_fill_report", return_value=True) as schedule:
            result = lifecycle.place_single(self.reviewed, self.config, Decimal("2.46"))
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(result["fill_report_scheduled"])
        schedule.assert_not_called()

    def test_fill_report_records_next_day_after_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary) / "fill_reports"
            now = datetime(2026, 10, 28, 14, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
            self.assertTrue(lifecycle.schedule_fill_report(self.config, {"id": "o1", "chain_symbol": "FIGR", "state": "filled"}, now=now, report_dir=report_dir))
            artifact = json.loads((report_dir / "fill-report-FIGR-o1.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["kind"], "fill_report_request")
        self.assertEqual(artifact["delivery"], "claude_chat")
        self.assertEqual(artifact["deliver_after"], "2026-10-29T13:05:00-07:00")

    def test_fill_report_records_same_day_before_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            report_dir = Path(temporary) / "fill_reports"
            now = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
            self.assertTrue(lifecycle.schedule_fill_report(self.config, {"id": "o1", "chain_symbol": "FIGR", "state": "filled"}, now=now, report_dir=report_dir))
            artifact = json.loads((report_dir / "fill-report-FIGR-o1.json").read_text(encoding="utf-8"))
        self.assertEqual(artifact["deliver_after"], "2026-10-28T13:05:00-07:00")

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

        with patch.object(lifecycle, "_lifecycle_enabled", return_value=True), patch.object(lifecycle, "call_mcp", side_effect=fake_mcp), patch.object(lifecycle, "record_notification"), patch.object(lifecycle, "schedule_fill_report", return_value=False):
            result = lifecycle.run(self.reviewed, self.config, sleep_fn=lambda _: None)
        self.assertEqual(result["status"], "filled")
        self.assertEqual(placed_prices, ["4.30", "4.20"])
        self.assertEqual(result["attempts"], 2)

    def test_replacement_price_stays_inside_quote_and_tick(self):
        quote = {"bid_price": "4.00", "ask_price": "4.30"}
        self.assertEqual(lifecycle.replacement_price(self.reviewed, quote, Decimal("0.05")), Decimal("4.20"))

    def test_notification_is_deduplicated_by_provider_destination_and_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "dedup.json"
            outbox_path = Path(temporary) / "outbox.jsonl"
            self.assertTrue(lifecycle.record_notification("claude-chat", "same message", state_path=state_path, outbox_path=outbox_path))
            self.assertFalse(lifecycle.record_notification("claude-chat", "same message", state_path=state_path, outbox_path=outbox_path))
            self.assertTrue(lifecycle.record_notification("claude-chat-alt", "same message", state_path=state_path, outbox_path=outbox_path))
            self.assertTrue(lifecycle.record_notification("claude-chat", "different message", state_path=state_path, outbox_path=outbox_path))
            records = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 3)
            self.assertTrue(all(r["provider"] == "claude_chat" for r in records))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["version"], 1)
            self.assertEqual(len(state["notifications"]), 3)
            self.assertTrue(all(entry["provider"] == "claude_chat" for entry in state["notifications"].values()))

    def test_failed_notification_record_is_not_marked_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocker = Path(temporary) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            state_path = Path(temporary) / "dedup.json"
            with self.assertRaises(lifecycle.LifecycleError):
                lifecycle.record_notification("claude-chat", "not recorded", state_path=state_path, outbox_path=blocker / "outbox.jsonl")
            self.assertFalse(state_path.exists())


class Stage0TransportAndGuardTests(unittest.TestCase):
    """D-24 Stage 0: lifecycle reads ride the wheel transport; writes
    stay blocked at the guard AND at the transport."""

    def setUp(self):
        self.config = lifecycle.Config("acct", "claude-chat", 3, 60, Decimal("0.05"), None)
        self.reviewed = {
            "type": "limit", "quantity": "1", "price": "4.30",
            "time_in_force": "gfd", "market_hours": "regular_hours",
            "legs": [{"option_id": "opt-1", "side": "sell", "position_effect": "open", "ratio_quantity": 1}],
        }

    # (a) read path goes through the (mocked) wheel transport
    def test_call_mcp_reads_via_transport(self):
        calls = []

        def fake_transport(tool, args):
            calls.append((tool, args))
            if tool == "get_option_orders":
                return {"data": {"orders": [{"id": "o9", "state": "filled"}]}}
            if tool == "get_option_positions":
                return {"data": {"results": [{"option_id": "opt-1", "quantity": "1"}]}}
            if tool == "review_option_order":
                return {"data": {"order_checks": [], "estimated_cost": "0"}}
            raise AssertionError(tool)

        with patch.object(lifecycle, "_transport_call", side_effect=fake_transport):
            row = lifecycle.status(self.config, "o9")
            positions = lifecycle.call_mcp("get_option_positions", {"account_number": "acct"})
            review = lifecycle.call_mcp("review_option_order", {"account_number": "acct"})
        self.assertEqual(row["state"], "filled")
        self.assertEqual(positions["results"][0]["option_id"], "opt-1")
        self.assertEqual(review["order_checks"], [])
        self.assertEqual([tool for tool, _ in calls],
                         ["get_option_orders", "get_option_positions", "review_option_order"])

    # (b) the dual-control guard: neither control alone is enough
    def test_place_cancel_run_raise_without_both_arming_controls(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(lifecycle.OrderLifecycleDisabled):
                lifecycle.place(self.reviewed, self.config, Decimal("4.30"))
            with self.assertRaises(lifecycle.OrderLifecycleDisabled):
                lifecycle.cancel(self.config, "o1")
            with self.assertRaises(lifecycle.OrderLifecycleDisabled):
                lifecycle.run(self.reviewed, self.config, sleep_fn=lambda _: None)
        with tempfile.TemporaryDirectory() as temporary:
            flag = Path(temporary) / "armed.flag"
            # env only, no flag file
            with patch.dict(os.environ, {"WHEEL_ORDER_LIFECYCLE_ENABLE": "1",
                                         "WHEEL_ORDER_LIFECYCLE_FLAG": str(flag)}, clear=True):
                with self.assertRaises(lifecycle.OrderLifecycleDisabled):
                    lifecycle.place(self.reviewed, self.config, Decimal("4.30"))
            # flag file only, no env
            flag.write_text("armed", encoding="utf-8")
            with patch.dict(os.environ, {"WHEEL_ORDER_LIFECYCLE_FLAG": str(flag)}, clear=True):
                with self.assertRaises(lifecycle.OrderLifecycleDisabled):
                    lifecycle.place(self.reviewed, self.config, Decimal("4.30"))

    # (c) D-25 (Stage 2): the transport's ORDER_TOOLS category holds
    # exactly place/cancel (the lifecycle guard is the control); every
    # OTHER write tool still raises WriteToolDisabled before any
    # subprocess. place/cancel themselves are never invoked in tests.
    def test_transport_raises_write_tool_disabled_for_order_writes(self):
        repo_root = str(Path(lifecycle.__file__).resolve().parents[3])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from scripts.robinhood_transport import (
            ORDER_TOOLS, WRITE_TOOLS, WriteToolDisabled, call_robinhood_tool)
        self.assertEqual(ORDER_TOOLS,
                         {"place_option_order", "cancel_option_order"})
        self.assertNotIn("place_option_order", WRITE_TOOLS)
        self.assertNotIn("cancel_option_order", WRITE_TOOLS)
        with self.assertRaises(WriteToolDisabled):
            call_robinhood_tool("place_equity_order", {"account_number": "acct"})
        with self.assertRaises(WriteToolDisabled):
            call_robinhood_tool("exercise_option", {"account_number": "acct"})
        # ... and the lifecycle's own call path surfaces the same block
        with self.assertRaises(WriteToolDisabled):
            lifecycle.call_mcp("place_equity_order", {"account_number": "acct"})


if __name__ == "__main__":
    unittest.main()
