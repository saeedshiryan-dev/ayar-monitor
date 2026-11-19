import unittest
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.fetch_ayar_component_quotes import (
    apply_base_forward_anchor,
    apply_macro_quotes_for_session,
    expected_exchange_session_date,
    parse_sahmino_api_payload,
    parse_ime_salaf_frame,
    parse_sahmino_quote_text,
)


class ComponentQuoteParserTests(unittest.TestCase):
    def test_sahmino_history_row_extracts_daily_close_not_high(self):
        sample = (
            "تاریخچه اخیر | ۲۷ آبان ۱۴۰۵ | ۲٬۴۸۱٬۱۲۱ | ۲٬۴۹۶٬۳۰۸ | "
            "۲٬۴۸۰٬۱۱۱ | ۲٬۴۸۵٬۱۵۲ | ▼نزولی ۰٫۴۵٪ | ۲۹۵٬۶۰۷"
        )
        quote = parse_sahmino_quote_text(
            sample, "https://www.ime.co.ir/standard-transactions.html"
        )
        self.assertEqual(quote["price"], 2485152)
        self.assertEqual(quote["date"], "2026-11-18")
        self.assertEqual(quote["price_type"], "daily_close")
        self.assertEqual(quote["volume"], 295607)


    def test_sahmino_api_payload_reads_latest_rial_close(self):
        payload = {
            "data": {
                "unit": "rial",
                "points": [
                    {"date": "2026-11-17", "close": 2480000, "volume": 200000},
                    {"date": "2026-11-18", "close": 2485152, "volume": 295607},
                ],
            }
        }
        quote = parse_sahmino_api_payload(payload, "https://sahmino.com/api/v1/instruments/sske/prices")
        self.assertEqual(quote["price"], 2485152)
        self.assertEqual(quote["date"], "2026-11-18")
        self.assertEqual(quote["unit"], "rial_per_paper")


    def test_ime_salaf_frame_selects_exact_aske2_daily_close(self):
        frame = pd.DataFrame([
            {"Symbol": "نماد دیگر", "Code": "X1", "GregorianDate": "2026-11-18",
             "JalaliDate": "1405-08-27", "ClosingPrice": 900, "TradeVolume": 10},
            {"Symbol": "عسکه2", "Code": "SSKE", "GregorianDate": "2026-11-17",
             "JalaliDate": "1405-08-26", "ClosingPrice": 2480000, "TradeVolume": 200000},
            {"Symbol": "عسکه۲", "Code": "SSKE", "GregorianDate": "2026-11-18",
             "JalaliDate": "1405-08-27", "ClosingPrice": 2485152, "TradeVolume": 295607},
        ])
        quote = parse_ime_salaf_frame(frame)
        self.assertEqual(quote["price"], 2485152)
        self.assertEqual(quote["date"], "2026-11-18")
        self.assertEqual(quote["unit"], "rial_per_paper")
        self.assertEqual(quote["price_type"], "official_daily_close")
        self.assertEqual(quote["instrument_code"], "SSKE")

    def test_base_aske2_anchor_only_applies_to_exact_session(self):
        data = {
            "inputs": {"base_nav_date": "2026-11-18"},
            "component_market_snapshot": {
                "coin_forward": {
                    "quote_date": "2026-11-18",
                    "quote_jalali": "1405/08/27",
                    "closing_price_rial_per_paper": 2485152,
                    "source_price_history": "https://sahmino.com/symbol/sske?tab=chart",
                }
            },
        }
        quotes = {
            "coin_certificate": {"date": "2026-11-18"},
            "gold_bar_certificate": {"date": "2026-11-18"},
            "silver": {"date": "2026-11-18"},
        }
        errors = {"coin_forward": "ime_salaf_history_unavailable"}
        used = apply_base_forward_anchor(quotes, errors, data, "2026-11-18")
        self.assertTrue(used)
        self.assertEqual(quotes["coin_forward"]["price"], 2485152)
        self.assertEqual(quotes["coin_forward"]["price_type"], "same_session_base_anchor")
        self.assertIn("coin_forward_refresh_error", errors)

    def test_base_aske2_anchor_is_not_carried_to_future_session(self):
        data = {
            "inputs": {"base_nav_date": "2026-11-18"},
            "component_market_snapshot": {
                "coin_forward": {
                    "quote_date": "2026-11-18",
                    "closing_price_rial_per_paper": 2485152,
                }
            },
        }
        quotes = {
            "coin_certificate": {"date": "2026-11-21"},
            "gold_bar_certificate": {"date": "2026-11-21"},
            "silver": {"date": "2026-11-21"},
        }
        self.assertFalse(apply_base_forward_anchor(quotes, {}, data, "2026-11-21"))
        self.assertNotIn("coin_forward", quotes)

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
