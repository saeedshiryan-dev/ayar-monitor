import unittest

from scripts.ayar_validation import ema, summarize, run_period


class AyarValidationTests(unittest.TestCase):
    def test_ema_seed_and_length(self):
        values = [1, 2, 3, 4, 5]
        result = ema(values, 3)
        self.assertEqual(len(result), 5)
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])
        self.assertAlmostEqual(result[2], 2.0)

    def test_summarize_flat_series(self):
        result = summarize([0.0, 0.0, 0.0], 0.0)
        self.assertEqual(result["total_return_pct"], 0.0)
        self.assertIsNone(result["sharpe_ratio_rf0"])

    def test_backtest_has_no_lookahead(self):
        closes = [10, 11, 12, 13, 14, 15, 16]
        fast = [None, 11, 12, 13, 14, 15, 16]
        slow = [None, 10, 11, 12, 13, 14, 15]
        result = run_period(closes, [str(x) for x in range(7)], fast, slow, 2, 0.0, 0)
        self.assertEqual(result["strategy"]["sessions"], 5)
        self.assertGreater(result["strategy"]["total_return_pct"], 0)

    def test_indicator_lab_reports_drawdown_and_trade_metrics(self):
        from scripts.ayar_indicator_lab import build_report
        history = []
        price = 100
        for i in range(260):
            price *= 1.01 if i % 20 < 10 else 0.99
            history.append({"date": f"2025{i//30+1:02d}{i%30+1:02d}", "close": price, "volume": 1000 + i})
        report = build_report(history)
        item = report["strategies"]["ema20_50"]["out_of_sample_from_2024"]
        self.assertIn("max_drawdown_pct", item)
        self.assertIn("average_trade_return_pct", item)
        self.assertIn("average_winning_trade_pct", item)
        self.assertIn("average_losing_trade_pct", item)


if __name__ == "__main__":
    unittest.main()
