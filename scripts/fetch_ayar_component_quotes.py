#!/usr/bin/env python3
"""Fetch synchronized Ayar component quotes after the commodity market close.

Sources: Bourseeye commodity certificates and Sahmino's Aske2 quote history.
Only same-session, explicitly dated quotes are promoted into the fair-NAV input.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

import jdatetime
import requests

ROOT = Path(__file__).resolve().parents[1]
FAIR_NAV = ROOT / "data" / "ayar_fair_nav.json"
OUT = ROOT / "data" / "ayar_component_market_quotes.json"
MACRO = ROOT / "data" / "ayar_macro_live.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
MONTHS = "فروردین اردیبهشت خرداد تیر مرداد شهریور مهر آبان آذر دی بهمن اسفند".split()
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        if data and data.strip():
            self.parts.append(data.strip())


def clean(text):
    return re.sub(r"\s+", " ", str(text or "").translate(DIGITS).replace("ي", "ی").replace("ك", "ک")).strip()


def number(value):
    return int(re.sub(r"[,٬\s]", "", clean(value)))


def jalali_date(day, month, year):
    return jdatetime.date(int(year), MONTHS.index(month) + 1, int(day)).togregorian().isoformat()


def get_text(url):
    response = requests.get(url, headers=HEADERS, timeout=(10, 35))
    response.raise_for_status()
    parser = TextOnly()
    parser.feed(response.text)
    return clean(" ".join(parser.parts))


def bourseeye_quote(url, key, price_pattern):
    text = get_text(url)
    date_match = re.search(r"در آخرین روز معاملاتی\s*\(\s*(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(14\d{2})\s*\)", text)
    price_match = re.search(price_pattern, text)
    if not date_match or not price_match:
        raise ValueError(f"date_or_price_not_found:{key}")
    day, month, year = date_match.groups()
    return {
        "key": key,
        "price": number(price_match.group(1)),
        "unit": "toman_per_certificate" if key != "silver" else "toman_per_gram",
        "jalali_date": f"{year}/{MONTHS.index(month)+1:02d}/{int(day):02d}",
        "date": jalali_date(day, month, year),
        "source": url,
        "price_type": "last_trade",
    }


def parse_sahmino_quote_text(text, url):
    # Parse the first dated row under Sahmino's recent-history table.
    # Its change cell contains Persian words and a decimal percentage, so
    # extract the first four numeric fields (OHLC) and the final volume
    # from that row instead of trying to parse the localized change string.
    text = clean(text).replace("|", " ").replace("│", " ")
    history_start = text.find("تاریخچه")
    if history_start >= 0:
        text = text[history_start:]
    date_pattern = r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(14\d{2})"
    date_match = re.search(date_pattern, text)
    if not date_match:
        raise ValueError("aske2_history_date_not_found")
    tail = text[date_match.end():]
    next_date = re.search(date_pattern, tail)
    row_text = tail[:next_date.start()] if next_date else tail
    numeric_fields = re.findall(r"[\d,٬]+", row_text)
    if len(numeric_fields) < 5:
        raise ValueError("aske2_history_ohlc_volume_not_found")
    day, month, year = date_match.groups()
    close = number(numeric_fields[3])
    return {
        "key": "coin_forward",
        "symbol": "عسکه۲",
        "price": close,
        "volume": number(numeric_fields[-1]),
        "unit": "rial_per_paper",
        "jalali_date": f"{year}/{MONTHS.index(month)+1:02d}/{int(day):02d}",
        "date": jalali_date(day, month, year),
        "source": url,
        "price_type": "daily_close",
    }


def sahmino_quote(url):
    return parse_sahmino_quote_text(get_text(url), url)


def expected_exchange_session_date(now):
    # Commodity-certificate sessions are scheduled Saturday through Wednesday.
    # Thursday and Friday use the most recent scheduled session, unless an
    # intervening exchange holiday makes the dated quote older than this rule.
    session_day = now.date()
    if now.weekday() == 3:  # Thursday
        session_day -= timedelta(days=1)
    elif now.weekday() == 4:  # Friday
        session_day -= timedelta(days=2)
    return session_day.isoformat()



def apply_macro_quotes_for_session(data, session_date, macro_data=None):
    """Use only same-session USD and XAU quotes for the valuation date."""
    inputs = data["inputs"]
    base_date = inputs["base_nav_date"]
    if session_date == base_date:
        inputs["xau_now_usd_oz"] = inputs["xau_base_usd_oz"]
        inputs["xau_now_date"] = base_date
        inputs["usd_now_toman"] = inputs["usd_base_toman"]
        inputs["usd_now_date"] = base_date
        return True, "same_session_base_nav_anchor"
    if macro_data is None:
        try:
            macro_data = json.loads(MACRO.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"macro_snapshot_unavailable:{type(exc).__name__}"
    if macro_data.get("status") != "ok":
        return False, "macro_snapshot_not_ok"
    usd = macro_data.get("usd") or {}
    xau = macro_data.get("xau") or {}
    try:
        usd_date = datetime.fromisoformat(usd["updated_at"]).astimezone(ZoneInfo("Asia/Tehran")).date().isoformat()
        xau_date = datetime.fromisoformat(xau["updated_at"]).astimezone(ZoneInfo("Asia/Tehran")).date().isoformat()
        usd_price = float(usd.get("display_price") or usd.get("sell"))
        xau_price = float(xau.get("display_price") or xau.get("sell"))
    except Exception as exc:
        return False, f"macro_fields_invalid:{type(exc).__name__}"
    if usd_date != session_date or xau_date != session_date:
        return False, f"macro_quotes_not_same_session:usd={usd_date},xau={xau_date}"
    if usd_price <= 0 or xau_price <= 0:
        return False, "macro_quote_nonpositive"
    inputs["usd_now_toman"] = usd_price
    inputs["usd_now_date"] = session_date
    inputs["xau_now_usd_oz"] = xau_price
    inputs["xau_now_date"] = session_date
    inputs["macro_quote_source"] = macro_data.get("source")
    inputs["macro_quote_retrieved_at"] = macro_data.get("retrieved_at")
    return True, "same_session_macro_quotes"

def main():
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    retrieved_at = now.isoformat()
    sources = {
        "coin_certificate": ("https://bourseeye.com/commodities/1504/سکه-طلا",
            r"آخرین معامله،\s*هر گواهی\s*([\d,٬]+)\s*تومان"),
        "gold_bar_certificate": ("https://bourseeye.com/commodities/1503/شمش-طلا",
            r"آخرین معامله،\s*هر گواهی\s*([\d,٬]+)\s*تومان"),
        "silver": ("https://bourseeye.com/commodities/1507/شمش-نقره",
            r"آخرین معامله،\s*هر گواهی\s*([\d,٬]+)\s*تومان"),
    }
    quotes, errors = {}, {}
    for key, (url, price_pattern) in sources.items():
        try:
            quotes[key] = bourseeye_quote(url, key, price_pattern)
        except Exception as exc:
            errors[key] = f"{type(exc).__name__}: {str(exc)[:220]}"
    try:
        quotes["coin_forward"] = sahmino_quote("https://sahmino.com/symbol/sske?tab=chart")
    except Exception as exc:
        errors["coin_forward"] = f"{type(exc).__name__}: {str(exc)[:220]}"

    dates = {q["date"] for q in quotes.values()}
    common_date = next(iter(dates)) if len(dates) == 1 and len(quotes) == 4 else None
    expected_session = expected_exchange_session_date(now)
    macro_sync = False
    macro_reason = "component_quotes_not_synchronized"
    data = None
    if common_date:
        data = json.loads(FAIR_NAV.read_text(encoding="utf-8"))
        macro_sync, macro_reason = apply_macro_quotes_for_session(data, common_date)
    promoted = common_date == expected_session and macro_sync
    output = {
        "status": "synchronized_latest_exchange_session" if promoted else "quotes_not_synchronized_or_not_current",
        "retrieved_at": retrieved_at,
        "expected_session_date_gregorian": expected_session,
        "calendar_rule": "Iran commodity-certificate sessions scheduled Saturday-Wednesday; Thursday/Friday resolve to the preceding Wednesday unless a holiday intervenes.",
        "common_quote_date_gregorian": common_date,
        "common_quote_date_jalali": next(iter(quotes.values()))["jalali_date"] if common_date else None,
        "macro_quotes_synchronized": macro_sync,
        "macro_sync_reason": macro_reason,
        "promoted_to_valuation_model": promoted,
        "quotes": quotes,
        "errors": errors,
        "rules": [
            "Only explicitly dated last-trade/daily-close quotes are accepted.",
            "All four component quotes must share the latest scheduled exchange session before promotion.",
            "USD and XAU macro quotes must also match that exchange session; the official NAV anchor is used only when the session equals the base NAV date.",
            "Thursday and Friday are mapped to the preceding Wednesday; unmodeled holidays still block promotion when quote dates differ.",
            "No previous-session quote is carried forward beyond the latest scheduled exchange session.",
            "Gold-bar certificate is recorded as a market reference only; it is not treated as a separately disclosed portfolio holding."
        ],
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if promoted:
        snap = data["component_market_snapshot"]
        q = quotes
        snap["coin_certificate"]["current_price_toman_per_certificate"] = q["coin_certificate"]["price"]
        snap["coin_certificate"]["current_quote_date"] = q["coin_certificate"]["date"]
        snap["coin_certificate"]["current_quote_source"] = q["coin_certificate"]["source"]
        snap["gold_bar_certificate"]["current_price_toman_per_certificate"] = q["gold_bar_certificate"]["price"]
        snap["gold_bar_certificate"]["current_quote_date"] = q["gold_bar_certificate"]["date"]
        snap["gold_bar_certificate"]["current_quote_source"] = q["gold_bar_certificate"]["source"]
        snap["silver_bar_certificate"]["current_price_toman_per_gram"] = q["silver"]["price"]
        snap["silver_bar_certificate"]["current_quote_date"] = q["silver"]["date"]
        snap["silver_bar_certificate"]["current_quote_source"] = q["silver"]["source"]
        snap["coin_forward"]["current_price_rial_per_paper"] = q["coin_forward"]["price"]
        snap["coin_forward"]["current_quote_date"] = q["coin_forward"]["date"]
        snap["coin_forward"]["current_quote_source"] = q["coin_forward"]["source"]
        snap["current_quote_session_date"] = common_date
        snap["current_quote_session_jalali"] = q["coin_certificate"]["jalali_date"]
        data["inputs"]["valuation_as_of_date"] = common_date
        data["inputs"]["latest_common_exchange_session"] = common_date
        data["inputs"]["latest_common_exchange_session_jalali"] = q["coin_certificate"]["jalali_date"]
        data["inputs"]["next_session_quotes_available"] = True
        data["inputs"]["latest_component_quotes_retrieved_at"] = retrieved_at
        tmp = FAIR_NAV.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(FAIR_NAV)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
