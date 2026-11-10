import json
import tempfile
import unittest
from pathlib import Path

from social_media_telegram_message import SocialMessageError, render_message


class SocialMediaTelegramMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.valuation_path = self.root / "valuation_history.json"
        self.valuation_path.write_text(
            json.dumps(
                [
                    {
                        "ticker": "ALHC",
                        "data_cutoff_at": "2026-09-23T00:00:00Z",
                        "valuation": {
                            "grade": {"grade": "B", "score": 70},
                            "business_quality": {"grade": "C", "score": 50},
                        },
                    },
                    {
                        "ticker": "ALHC",
                        "data_cutoff_at": "2026-09-24T00:00:00Z",
                        "valuation": {
                            "grade": {"grade": "A-", "score": 81.4438},
                            "business_quality": {"grade": "C+", "score": 62.159},
                        },
                    },
                    {
                        "ticker": "FIGR",
                        "data_cutoff_at": "2026-09-24T00:00:00Z",
                        "valuation": {
                            "grade": {"grade": "A+", "score": 98},
                            "business_quality": {"grade": "C", "score": 56.4205},
                        },
                    },
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self):
        return {
            "today": {
                "expiration": "11/20",
                "positions": [
                    {
                        "ticker": "ALHC",
                        "contract": "$7.50P",
                        "dte": 57,
                        "actions": "Sell 5 @ $1.00–$1.05 · BTC 1 @ $1.05",
                        "net_premium": 405,
                        "collateral": 3000,
                        "open_contracts": 4,
                    },
                    {
                        "ticker": "FIGR",
                        "contract": "$32.50P",
                        "dte": 57,
                        "actions": "Sell 2 @ $3.90",
                        "net_premium": 780,
                        "collateral": 6500,
                        "open_contracts": 2,
                    },
                ],
            },
            "ytd": {
                "successful_aggregated_positions": 5,
                "open_positions": 3,
                "closed_profitable_positions": 2,
                "closed_positions": 2,
                "assignments": 0,
                "realized_profit": 1630,
                "cash_position": 13063.08,
                "premiums_on_open_positions": 1434,
                "net_premium_collected": 3064,
                "total_cash": 13063.08,
            },
            "valuation_history_path": str(self.valuation_path),
        }

    def test_renders_required_message_and_user_formulas(self):
        message = render_message(self.payload())
        self.assertIn("📊 **Today’s Wheel | 11/20**", message)
        self.assertIn("**ALHC $7.50P · 57 DTE**", message)
        self.assertIn("🏷️ Value A− (81.44) · 🏢 Quality C+ (62.16)", message)
        self.assertIn("**New premium: +$1,185 (12.47% of new collateral)**", message)
        self.assertIn("**New collateral: $9,500**", message)
        self.assertIn("**+$1,630 (14.02%)**", message)
        self.assertIn("**$3,064 (23.46%) before fees**", message)
        self.assertIn("Closed wins: **2/2 · 100%**", message)
        self.assertNotIn("ROC", message)
        self.assertNotIn("STO", message)

    def test_uses_latest_record_for_duplicate_ticker(self):
        message = render_message(self.payload())
        self.assertIn("Value A− (81.44)", message)
        self.assertNotIn("Value B (70.00)", message)

    def test_profit_taking_renders_realized_economics(self):
        payload = self.payload()
        payload["today"]["positions"] = [{
            "ticker": "ZZZZ",
            "contract": "$8.00P",
            "dte": 0,
            "actions": "BTC 4 @ $0.20",
            "event_type": "profit_taking",
            "net_premium": 163.84,
            "collateral": 0,
            "collateral_released": 3200,
            "open_contracts": 0,
            "original_premium": 244,
            "closing_debit": 80,
            "realized_profit_gross": 164,
            "realized_profit_net": 163.84,
            "fees": 0.16,
        }]
        records = json.loads(self.valuation_path.read_text(encoding="utf-8"))
        records.append({"ticker": "ZZZZ", "data_cutoff_at": "2026-09-25T00:00:00Z", "valuation": {"grade": {"grade": "F", "score": 32.04}, "business_quality": {"grade": "D", "score": 43.85}}})
        self.valuation_path.write_text(json.dumps(records), encoding="utf-8")
        message = render_message(payload)
        self.assertIn("✅ **Profit taken**", message)
        self.assertIn("Original premium: **+$244**", message)
        self.assertIn("Closing debit: **-$80**", message)
        self.assertIn("Realized profit: **+$164 gross**", message)
        self.assertIn("Net profit: **+$163.84 after $0.16 fees**", message)
        self.assertIn("Collateral released: **$3,200** · 0 open", message)
        self.assertNotIn("-$80 net", message)

    def test_runtime_input_controls_ticker_output(self):
        valuation_records = json.loads(self.valuation_path.read_text(encoding="utf-8"))
        valuation_records.append(
            {
                "ticker": "ZZZZ",
                "data_cutoff_at": "2026-09-25T00:00:00Z",
                "valuation": {
                    "grade": {"grade": "B+", "score": 75},
                    "business_quality": {"grade": "B", "score": 70},
                },
            }
        )
        self.valuation_path.write_text(json.dumps(valuation_records), encoding="utf-8")
        payload = self.payload()
        payload["today"]["positions"][0]["ticker"] = "ZZZZ"
        message = render_message(payload)
        self.assertIn("ZZZZ", message)
        self.assertNotIn("ALHC", message)

    def test_missing_ticker_valuation_is_rejected(self):
        payload = self.payload()
        payload["today"]["positions"][0]["ticker"] = "MISSING"
        with self.assertRaisesRegex(SocialMessageError, "No matching valuation record"):
            render_message(payload)

    def test_success_denominator_must_be_positive(self):
        payload = self.payload()
        payload["ytd"]["successful_aggregated_positions"] = 3
        payload["ytd"]["open_positions"] = 3
        with self.assertRaisesRegex(SocialMessageError, "success-rate denominator"):
            render_message(payload)


if __name__ == "__main__":
    unittest.main()
