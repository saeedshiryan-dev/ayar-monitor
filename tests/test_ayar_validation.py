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
        item = report["strategies"]["ema20_50"]["out_of_sample_from_2024"]["metrics"]
        self.assertIn("max_drawdown_pct", item)
        self.assertIn("net_pnl_toman", item)
        self.assertIn("profit_factor", item)
        self.assertIn("average_win_toman", item)
        self.assertIn("average_loss_toman", item)


if __name__ == "__main__":
    unittest.main()



class AyarPnlBacktestTests(unittest.TestCase):
    def test_execution_is_next_session_and_integer_units(self):
        from scripts.ayar_indicator_lab import simulate_long_cash
        rows = [
            {"date":"20240101","open":1000,"close":1000},
            {"date":"20240102","open":1000,"close":1100},
            {"date":"20240103","open":1200,"close":1200},
        ]
        result = simulate_long_cash(rows, [1,0,0], 0, initial_capital=100000, fee=0, slippage=0)
        self.assertEqual(result["trades"][0]["entry_date"], "20240102")
        self.assertEqual(result["trades"][0]["exit_date"], "20240103")
        self.assertEqual(result["trades"][0]["quantity"], 1000)
        self.assertEqual(result["metrics"]["net_pnl_toman"], 20000)

    def test_costs_reduce_trade_profit(self):
        from scripts.ayar_indicator_lab import simulate_long_cash
        rows = [
            {"date":"20240101","open":1000,"close":1000},
            {"date":"20240102","open":1000,"close":1100},
            {"date":"20240103","open":1200,"close":1200},
        ]
        free = simulate_long_cash(rows, [0,1,0], 0, initial_capital=100000, fee=0, slippage=0)
        costly = simulate_long_cash(rows, [1,0,0], 0, initial_capital=100000, fee=0.002, slippage=0.001)
        self.assertGreater(free["metrics"]["net_pnl_toman"], costly["metrics"]["net_pnl_toman"])

    def test_period_starts_in_cash_and_forces_final_exit(self):
        from scripts.ayar_indicator_lab import simulate_long_cash
        rows = [
            {"date":"20240101","open":1000,"close":1000},
            {"date":"20240102","open":1000,"close":1050},
            {"date":"20240103","open":1050,"close":1100},
        ]
        result = simulate_long_cash(rows, [1,1,1], 0, initial_capital=100000, fee=0, slippage=0)
        self.assertEqual(result["trades"][0]["entry_date"], "20240102")
        self.assertEqual(result["trades"][0]["exit_date"], "20240103")
        self.assertEqual(result["metrics"]["round_trips"], 1)
