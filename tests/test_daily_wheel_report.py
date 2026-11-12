"""Tests for scripts/daily_wheel_report.py (D-28 code version).

Synthetic order fixtures in the live get_option_orders shape; the
ledger math is checked against hand-computed values, including the
Nov 2026 FIGR $20P round trip (+$390 in, -$190 out, +$200 realized).
Run with the system python3:  python3 -m unittest tests.test_daily_wheel_report
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import daily_wheel_report as dwr


def order(day, symbol, strike, expiry, side, effect, qty, price, premium, otype="put"):
    return {
        "state": "filled",
        "created_at": f"{day}T18:00:00Z",
        "quantity": str(qty),
        "price": str(price),
        "processed_premium": str(premium),
        "chain_symbol": symbol,
        "legs": [{"side": side, "position_effect": effect,
                  "strike_price": str(strike), "expiration_date": expiry,
                  "option_type": otype}],
    }


class LedgerTests(unittest.TestCase):
    def setUp(self):
        orders = [
            # FIGR $20P: sell 2 @1.95 (+390), BTC 2 @0.95 (-190) -> +200 win
            order("2026-11-05", "FIGR", 20, "2026-12-18", "sell", "open", 2, 1.95, 390),
            order("2026-11-11", "FIGR", 20, "2026-12-18", "buy", "close", 2, 0.95, 190),
            # LOSS $10P: sell 1 @1.00 (+100), BTC 1 @1.50 (-150) -> -50 loss
            order("2026-11-02", "LOSS", 10, "2026-12-18", "sell", "open", 1, 1.00, 100),
            order("2026-11-09", "LOSS", 10, "2026-12-18", "buy", "close", 1, 1.50, 150),
            # OPEN $4P: sell 3 @0.25 (+75), still open
            order("2026-11-11", "OPEN", 4, "2026-12-18", "sell", "open", 3, 0.25, 75),
            # SCAL $10P: sell 2, sell 1 (max short 3), BTC 3 -> collateral 3000
            order("2026-10-01", "SCAL", 10, "2026-11-20", "sell", "open", 2, 2.00, 400),
            order("2026-10-02", "SCAL", 10, "2026-11-20", "sell", "open", 1, 2.00, 200),
            order("2026-10-20", "SCAL", 10, "2026-11-20", "buy", "close", 3, 0.50, 150),
            # RCLD $7.50P (ALHC pattern): sell 2, sell 1, BTC 1, sell 2, BTC 4
            # peak short 4 but 5 contracts opened -> collateral 5 x 750 = 3750
            order("2026-08-10", "RCLD", 7.5, "2026-11-20", "sell", "open", 2, 1.00, 200),
            order("2026-08-11", "RCLD", 7.5, "2026-11-20", "sell", "open", 1, 1.00, 100),
            order("2026-08-20", "RCLD", 7.5, "2026-11-20", "buy", "close", 1, 0.50, 50),
            order("2026-08-25", "RCLD", 7.5, "2026-11-20", "sell", "open", 2, 1.00, 200),
            order("2026-09-05", "RCLD", 7.5, "2026-11-20", "buy", "close", 4, 0.50, 200),
            # a cancelled order must be ignored
            {**order("2026-11-11", "OPEN", 4, "2026-12-18", "sell", "open", 1, 0.25, 25),
             "state": "cancelled"},
        ]
        self.contracts = {c.symbol: c for c in dwr.build_contracts(orders)}

    def test_win_contract(self):
        c = self.contracts["FIGR"]
        self.assertTrue(c.is_closed)
        self.assertEqual(c.realized, 200)
        self.assertEqual(c.collateral, 4000)
        self.assertEqual(c.close_day, date(2026, 11, 11))

    def test_loss_contract(self):
        c = self.contracts["LOSS"]
        self.assertTrue(c.is_closed)
        self.assertEqual(c.realized, -50)

    def test_open_contract(self):
        c = self.contracts["OPEN"]
        self.assertFalse(c.is_closed)
        self.assertEqual(c.net_qty, -3)
        self.assertEqual(c.collateral, 1200)

    def test_max_short_tracks_peak(self):
        c = self.contracts["SCAL"]
        self.assertEqual(c.max_short, 3)
        self.assertEqual(c.collateral, 3000)
        self.assertEqual(c.realized, 450)

    def test_collateral_counts_every_opening(self):
        # Recycled contract: peak short 4, but 5 opened — collateral must
        # count all 5 so the denominator matches the premium numerator.
        c = self.contracts["RCLD"]
        self.assertEqual(c.max_short, 4)
        self.assertEqual(c.total_opened, 5)
        self.assertEqual(c.collateral, 3750)
        self.assertTrue(c.is_closed)

    def test_letter_bands(self):
        self.assertEqual(dwr.letter(96.0), "A+")
        self.assertEqual(dwr.letter(83.43), "A-")
        self.assertEqual(dwr.letter(80.8), "A-")
        self.assertEqual(dwr.letter(69.6), "B-")
        self.assertEqual(dwr.letter(61.8), "C+")
        self.assertEqual(dwr.letter(57.19), "C")
        self.assertEqual(dwr.letter(46.1), "D+")
        self.assertEqual(dwr.letter(12.0), "F")

    def test_report_for_close_day(self):
        scores = {"FIGR": (64.6, 46.1), "OPEN": (55.8, 80.8)}
        md = dwr.build_report(list(self.contracts.values()), date(2026, 11, 11),
                              scores, 9, date(2026, 10, 1), date(2026, 11, 11))
        self.assertIn("**Today's Wheel | 11/11**", md)
        self.assertIn("Sell 3 @ $0.25", md)
        self.assertIn("BTC 2 @ $0.95", md)
        self.assertIn("**+$200**", md)
        # YTD: closed FIGR +200, LOSS -50, SCAL +450, RCLD +250 -> realized 850, wins 3/4
        self.assertIn("**3/4 · 75%**", md)
        # D-33: YTD % are on starting capital ($10,000 seed): 850/10000, 925/10000
        self.assertIn("**+$850 (8.50%)**", md)
        # net premiums: (390-190)+(100-150)+(400+200-150)+(200+100+200-50-200)+75 = 925
        self.assertIn("**$925 (9.25%) before fees**", md)
        self.assertIn("realized ÷ starting capital", md)

    def test_no_fills_day_returns_none(self):
        md = dwr.build_report(list(self.contracts.values()), date(2026, 11, 6),
                              {}, 9, date(2026, 10, 1), date(2026, 11, 11))
        self.assertIsNone(md)


if __name__ == "__main__":
    unittest.main()
