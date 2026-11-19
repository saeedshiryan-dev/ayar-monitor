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


if __name__ == "__main__":
    unittest.main()
