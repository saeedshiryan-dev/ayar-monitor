import unittest
from scripts.component_valuation import value_components


class ComponentValuationTests(unittest.TestCase):
    def sample(self):
        return {
            "inputs": {
                "base_redemption_nav_rial": 100000,
                "base_nav_date": "2026-11-18",
                "portfolio_weights_pct": {
                    "gold_bullion": 80, "coin_certificates": 10,
                    "silver": 5, "coin_forward": 4, "bank_deposit": 1
                },
                "unclassified_weight_pct": 0,
                "xau_base_usd_oz": 100, "xau_now_usd_oz": 110,
                "xau_base_date": "2026-11-18", "xau_now_date": "2026-11-19",
                "usd_base_toman": 1000, "usd_now_toman": 1000,
                "usd_base_date": "2026-11-18", "usd_now_date": "2026-11-19"
            },
            "component_market_snapshot": {
                "snapshot_date": "2026-11-18",
                "coin_certificate": {"price_toman_per_certificate": 100, "date": "2026-11-18"},
                "silver_bar_certificate": {"price_toman_per_gram": 10, "date": "2026-11-18"},
                "coin_forward": {"closing_price_rial_per_paper": 100, "quote_date": "2026-11-18"}
            }
        }

    def test_missing_component_marks_block_full_nav(self):
        result = value_components(self.sample(), "2026-11-19")
        self.assertIsNone(result["full_indicative_nav_rial"])
        self.assertEqual(result["full_nav_status"], "blocked_missing_component_marks")
        self.assertIn("coin_certificates", result["missing_component_marks"])
        self.assertTrue(result["no_unpriced_component_assigned_zero_return"])

    def test_gold_spot_is_independent_and_correct(self):
        result = value_components(self.sample(), "2026-11-19")
        gold = next(x for x in result["components"] if x["key"] == "gold_bullion")
        self.assertAlmostEqual(gold["return_pct"], 10.0, places=5)
        self.assertAlmostEqual(gold["valued_amount_rial"], 88000, places=2)

    def test_same_day_anchor_reproduces_base_nav_when_all_quotes_present(self):
        data = self.sample()
        data["inputs"]["bank_deposit_annual_rate_pct"] = 0
        data["inputs"]["bank_deposit_accrual_start_date"] = "2026-11-18"
        data["component_market_snapshot"]["coin_certificate"]["current_price_toman_per_certificate"] = 100
        data["component_market_snapshot"]["coin_certificate"]["current_quote_date"] = "2026-11-18"
        data["component_market_snapshot"]["silver_bar_certificate"]["current_price_toman_per_gram"] = 10
        data["component_market_snapshot"]["silver_bar_certificate"]["current_quote_date"] = "2026-11-18"
        data["component_market_snapshot"]["coin_forward"]["current_price_rial_per_paper"] = 100
        data["component_market_snapshot"]["coin_forward"]["current_quote_date"] = "2026-11-18"
        data["inputs"]["xau_now_usd_oz"] = 100
        data["inputs"]["xau_now_date"] = "2026-11-18"
        data["inputs"]["usd_now_date"] = "2026-11-18"
        result = value_components(data, "2026-11-18")
        self.assertEqual(result["full_nav_status"], "complete")
        self.assertEqual(result["full_indicative_nav_rial"], 100000)
        self.assertTrue(result["base_allocations_reconcile"])


if __name__ == "__main__":
    unittest.main()
