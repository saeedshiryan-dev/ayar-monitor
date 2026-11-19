import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.fetch_ayar_component_quotes import (
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

    def test_wednesday_uses_current_session(self):
        now = datetime(2026, 11, 18, 18, 30, tzinfo=ZoneInfo("Asia/Tehran"))
        self.assertEqual(expected_exchange_session_date(now), "2026-11-18")


if __name__ == "__main__":
    unittest.main()
