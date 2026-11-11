"""D-31: tests for scripts/fill_report_dispatch.py (synthetic fixtures)."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import fill_report_dispatch as dispatch  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 12, 13, 5, tzinfo=PT)


class FakeTransport:
    def __init__(self, orders):
        self.orders = orders

    def call_mcp(self, tool, args):
        assert tool == "get_option_orders"
        return {"data": {"orders": self.orders}}


def filled_order(order_id="ord-1", symbol="SMCI"):
    return {
        "id": order_id, "state": "filled", "chain_symbol": symbol,
        "direction": "credit", "processed_quantity": "1.00000",
        "legs": [{"position_effect": "open", "executions": [
            {"quantity": "1.00000", "price": "2.46",
             "timestamp": "2026-11-12T18:38:00Z"}]}],
    }


def write_artifact(directory, name, **overrides):
    artifact = {
        "kind": "fill_report_request", "status": "pending",
        "deliver_after": (NOW - timedelta(minutes=1)).isoformat(),
        "order_id": "ord-1", "chain_symbol": "SMCI",
        "account_number": "858226392",
    }
    artifact.update(overrides)
    path = Path(directory) / name
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.artifacts = root / "fill_reports"
        self.out = root / "out"
        self.log = root / "dispatch_log.jsonl"
        self.artifacts.mkdir()
        date = NOW.strftime("%Y-%m-%d")
        stub = ("import pathlib; "
                f"pathlib.Path(r'{self.out}').mkdir(parents=True, exist_ok=True); "
                f"pathlib.Path(r'{self.out / (date + '.md')}').write_text('day'); "
                f"pathlib.Path(r'{self.out / (date + '.png')}').write_bytes(b'png')")
        self.day_cmd = [sys.executable, "-c", stub]

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, transport):
        return dispatch.run_dispatch(
            NOW, self.artifacts, self.out, self.log, "858226392",
            transport=transport, day_report_cmd=self.day_cmd)

    def test_nothing_due(self):
        write_artifact(self.artifacts, "future.json",
                       deliver_after=(NOW + timedelta(hours=2)).isoformat())
        result = self.run_it(FakeTransport([]))
        self.assertEqual(result["dispatched"], 0)

    def test_verified_fill_dispatches_and_marks_delivered(self):
        path = write_artifact(self.artifacts, "a.json")
        write_artifact(self.artifacts, "done.json", status="delivered")
        result = self.run_it(FakeTransport([filled_order()]))
        self.assertEqual(result["dispatched"], 1)
        self.assertEqual(result["failures"], [])
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "delivered")
        self.assertEqual(saved["delivered_via"], "systemd_dispatch (D-31)")
        dispatch_file = Path(result["dispatch_file"])
        self.assertTrue(dispatch_file.exists())
        self.assertIn("SMCI", dispatch_file.read_text(encoding="utf-8"))
        self.assertTrue(self.log.exists())

    def test_unfilled_order_stays_pending_and_fails(self):
        path = write_artifact(self.artifacts, "a.json")
        order = filled_order()
        order["state"] = "confirmed"
        result = self.run_it(FakeTransport([order]))
        self.assertEqual(result["dispatched"], 0)
        self.assertEqual(len(result["failures"]), 1)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "pending")


if __name__ == "__main__":
    unittest.main()
