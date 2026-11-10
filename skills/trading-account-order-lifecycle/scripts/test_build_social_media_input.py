import json
import tempfile
import unittest
from pathlib import Path

from build_social_media_input import build_payload


class BuildSocialMediaInputTests(unittest.TestCase):
    def test_builds_normalized_payload_from_broker_snapshot(self):
        snapshot = {
            "as_of": "2026-09-25",
            "orders": {
                "orders": [
                    {"state": "cancelled", "processed_quantity": "0", "legs": []},
                    {
                        "state": "filled", "processed_quantity": "1", "chain_symbol": "ZZZZ",
                        "legs": [{"option_id": "open", "position_effect": "open", "side": "sell",
                                   "expiration_date": "2026-12-18", "strike_price": "25", "option_type": "put",
                                   "trade_value_multiplier": "100", "executions": [{"price": "2.25", "quantity": "1", "trade_date": "2026-09-25"}]}],
                    },
                    {
                        "state": "filled", "processed_quantity": "1", "chain_symbol": "YYYY",
                        "legs": [{"option_id": "closed", "position_effect": "open", "side": "sell",
                                   "expiration_date": "2026-09-25", "strike_price": "10", "option_type": "put",
                                   "trade_value_multiplier": "100", "executions": [{"price": "1.00", "quantity": "1", "trade_date": "2026-01-10"}]}],
                    },
                    {
                        "state": "filled", "processed_quantity": "1", "chain_symbol": "YYYY",
                        "legs": [{"option_id": "closed", "position_effect": "close", "side": "buy",
                                   "expiration_date": "2026-09-25", "strike_price": "10", "option_type": "put",
                                   "trade_value_multiplier": "100", "executions": [{"price": "0.25", "quantity": "1", "trade_date": "2026-02-10"}]}],
                    },
                ]
            },
            "positions": {"positions": [{"option_id": "open", "quantity": "1", "average_price": "-225"}]},
            "portfolio": {"cash": "10000"},
            "realized_pnl": {"total_returns": "75"},
        }
        with tempfile.TemporaryDirectory() as directory:
            valuation_path = Path(directory) / "valuation.json"
            valuation_path.write_text("[]", encoding="utf-8")
            payload = build_payload(snapshot, valuation_path)
        self.assertEqual(payload["today"]["positions"][0]["ticker"], "ZZZZ")
        self.assertEqual(payload["today"]["positions"][0]["net_premium"], 225.0)
        self.assertEqual(payload["today"]["positions"][0]["collateral"], 2500.0)
        self.assertEqual(payload["ytd"]["successful_aggregated_positions"], 2)
        self.assertEqual(payload["ytd"]["open_positions"], 1)
        self.assertEqual(payload["ytd"]["closed_profitable_positions"], 1)
        self.assertEqual(payload["ytd"]["realized_profit"], 75.0)

    def test_classifies_full_close_as_profit_taking(self):
        snapshot = {
            "as_of": "2026-09-25",
            "orders": {"orders": [
                {"state": "filled", "processed_quantity": "4", "chain_symbol": "ONDS", "legs": [{"option_id": "onds", "position_effect": "open", "side": "sell", "expiration_date": "2026-09-25", "strike_price": "8", "option_type": "put", "trade_value_multiplier": "100", "executions": [{"price": "0.61", "quantity": "4", "trade_date": "2026-01-10"}]}]},
                {"state": "filled", "processed_quantity": "4", "chain_symbol": "ONDS", "legs": [{"option_id": "onds", "position_effect": "close", "side": "buy", "expiration_date": "2026-09-25", "strike_price": "8", "option_type": "put", "trade_value_multiplier": "100", "executions": [{"price": "0.20", "quantity": "4", "fees": "0.16", "trade_date": "2026-09-25"}]}]},
            ]},
            "positions": {"positions": []},
            "portfolio": {"cash": "10000"},
            "realized_pnl": {"total_returns": "164"},
        }
        with tempfile.TemporaryDirectory() as directory:
            payload = build_payload(snapshot, Path(directory) / "valuation.json")
        position = payload["today"]["positions"][0]
        self.assertEqual(position["event_type"], "profit_taking")
        self.assertEqual(position["original_premium"], 244.0)
        self.assertEqual(position["closing_debit"], 80.0)
        self.assertEqual(position["realized_profit_gross"], 164.0)
        self.assertEqual(position["realized_profit_net"], 163.84)
        self.assertEqual(position["collateral_added"], 0.0)
        self.assertEqual(position["collateral_released"], 3200.0)
        self.assertEqual(position["open_contracts"], 0)

        snapshot = {"as_of": "2026-09-25", "orders": {"orders": []}}
        with self.assertRaisesRegex(ValueError, "No broker-verified"):
            build_payload(snapshot, Path("/tmp/valuation.json"))


if __name__ == "__main__":
    unittest.main()
