import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.fetch_ayar_component_quotes import (
    apply_macro_quotes_for_session,
    expected_exchange_session_date,
    parse_sahmino_quote_text,
)


class ComponentQuoteParserTests(unittest.TestCase):
    def test_sahmino_history_row_extracts_daily_close_not_high(self):
        sample = (
            "تاریخچه اخیر | ۲۷ آبان ۱۴۰۵ | ۲٬۴۸۱٬۱۲۱ | ۲٬۴۹۶٬۳۰۸ | "
            "۲٬۴۸۰٬۱۱۱ | ۲٬۴۸۵٬۱۵۲ | ▼نزولی ۰٫۴۵٪ | ۲۹۵٬۶۰۷"
        )
        quote = parse_sahmino_quote_text(
            sample, "https://sahmino.com/symbol/sske?tab=chart"
        )
        self.assertEqual(quote["price"], 2485152)
        self.assertEqual(quote["date"], "2026-11-18")
        self.assertEqual(quote["price_type"], "daily_close")
        self.assertEqual(quote["volume"], 295607)

    def test_thursday_uses_wednesday_exchange_session(self):
        now = datetime(2026, 11, 19, 17, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        self.assertEqual(expected_exchange_session_date(now), "2026-11-18")

    def test_friday_uses_wednesday_exchange_session(self):
        now = datetime(2026, 11, 20, 17, 0, tzinfo=ZoneInfo("Asia/Tehran"))
        self.assertEqual(expected_exchange_session_date(now), "2026-11-18")


    def test_base_nav_session_uses_same_date_macro_anchor(self):
        data = {"inputs": {
            "base_nav_date": "2026-11-18",
            "xau_base_usd_oz": 3727.64,
            "usd_base_toman": 266730,
        }}
        ok, reason = apply_macro_quotes_for_session(data, "2026-11-18", {})
        self.assertTrue(ok)
        self.assertEqual(reason, "same_session_base_nav_anchor")
        self.assertEqual(data["inputs"]["xau_now_date"], "2026-11-18")
        self.assertEqual(data["inputs"]["usd_now_date"], "2026-11-18")

    def test_macro_quotes_from_wrong_date_are_rejected(self):
        data = {"inputs": {"base_nav_date": "2026-11-18"}}
        macro = {
            "status": "ok",
            "usd": {"updated_at": "2026-11-19T12:34:43+00:00", "display_price": 268237.5},
            "xau": {"updated_at": "2026-11-19T12:44:43+00:00", "display_price": 3714.94},
        }
        ok, reason = apply_macro_quotes_for_session(data, "2026-11-21", macro)
        self.assertFalse(ok)
        self.assertIn("macro_quotes_not_same_session", reason)

    def test_macro_quotes_from_matching_date_are_accepted(self):
        data = {"inputs": {"base_nav_date": "2026-11-18"}}
        macro = {
            "status": "ok",
            "source": "test",
            "retrieved_at": "2026-11-21T13:00:00+00:00",
            "usd": {"updated_at": "2026-11-21T12:34:43+00:00", "display_price": 266730},
            "xau": {"updated_at": "2026-11-21T12:44:43+00:00", "display_price": 3727.64},
        }
        ok, reason = apply_macro_quotes_for_session(data, "2026-11-21", macro)
        self.assertTrue(ok)
        self.assertEqual(reason, "same_session_macro_quotes")
        self.assertEqual(data["inputs"]["xau_now_date"], "2026-11-21")
        self.assertEqual(data["inputs"]["usd_now_date"], "2026-11-21")

    def test_wednesday_uses_current_session(self):
        now = datetime(2026, 11, 18, 18, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        self.assertEqual(expected_exchange_session_date(now), "2026-11-18")


if __name__ == "__main__":
    unittest.main()
