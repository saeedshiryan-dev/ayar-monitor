#!/usr/bin/env python3
"""Fetch synchronized Ayar component quotes after the commodity market close.

Sources: Bourseeye commodity certificates and the public Iran Mercantile Exchange standard-salaf history.
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
SAHMINO_API_URL = "https://sahmino.com/api/v1/instruments/sske/prices"
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



def parse_sahmino_api_payload(payload, url):
    data = payload.get("data")
    if isinstance(data, list):
        points = data
        unit = payload.get("unit")
    elif isinstance(data, dict):
        points = data.get("points") or data.get("prices") or data.get("history")
        unit = data.get("unit") or payload.get("unit")
        if not points and any(k in data for k in ("close", "closingPrice", "closePrice")):
            points = [data]
    else:
        raise ValueError("sahmino_api_data_missing")
    if not isinstance(points, list) or not points:
        raise ValueError("sahmino_api_price_points_missing")
    point = next((p for p in reversed(points) if isinstance(p, dict)), None)
    if not point:
        raise ValueError("sahmino_api_latest_point_missing")
    date_value = point.get("date") or point.get("sessionDate") or point.get("tradingDate")
    close_value = point.get("close") or point.get("closingPrice") or point.get("closePrice")
    point_unit = point.get("unit") or unit
    if not date_value or close_value is None:
        raise ValueError("sahmino_api_date_or_close_missing")
    date_text = str(date_value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
        date_iso = date_text
    elif re.fullmatch(r"\d{8}", date_text):
        date_iso = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
    else:
        raise ValueError("sahmino_api_date_format_unknown")
    normalized_unit = str(point_unit or "").lower()
    if normalized_unit not in ("rial", "rials", "irr", "rial_per_paper"):
        raise ValueError("sahmino_api_unit_not_rial")
    price = number(str(close_value))
    if price <= 0:
        raise ValueError("sahmino_api_close_nonpositive")
    gd = datetime.strptime(date_iso, "%Y-%m-%d").date()
    jd = jdatetime.date.fromgregorian(date=gd)
    return {
        "key": "coin_forward",
        "symbol": "عسکه۲",
        "price": price,
        "unit": "rial_per_paper",
        "jalali_date": f"{jd.year}/{jd.month:02d}/{jd.day:02d}",
        "date": date_iso,
        "source": url,
        "price_type": "daily_close",
        "volume": point.get("volume"),
    }



def normalize_symbol(value):
    return clean(value).replace(" ", "").replace("‌", "")


def parse_ime_salaf_frame(history, source_url="https://www.ime.co.ir/standard-transactions.html"):
    """Select the exact Aske2 instrument from public IME daily salaf history."""
    if history is None or getattr(history, "empty", True):
        raise ValueError("ime_salaf_history_empty")
    required = {"Symbol", "Code", "GregorianDate", "JalaliDate", "ClosingPrice", "TradeVolume"}
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"ime_salaf_schema_missing:{','.join(sorted(missing))}")
    target = normalize_symbol("عسکه۲")
    matches = history[history["Symbol"].map(normalize_symbol) == target].copy()
    if matches.empty:
        symbols = sorted({clean(x) for x in history["Symbol"].dropna().tolist()})
        raise ValueError("ime_aske2_symbol_not_found:" + ",".join(symbols[:20]))
    matches["_date"] = matches["GregorianDate"].astype(str).str[:10]
    latest_date = matches["_date"].max()
    latest = matches[matches["_date"] == latest_date]
    if len(latest) != 1:
        raise ValueError(f"ime_aske2_duplicate_daily_rows:{latest_date}:{len(latest)}")
    row = latest.iloc[0]
    date_iso = str(row["_date"])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_iso):
        raise ValueError("ime_aske2_gregorian_date_invalid")
    price = number(row["ClosingPrice"])
    if price <= 0:
        raise ValueError("ime_aske2_close_nonpositive")
    return {
        "key": "coin_forward",
        "symbol": clean(row["Symbol"]),
        "instrument_code": clean(row["Code"]),
        "price": price,
        "volume": number(row["TradeVolume"]),
        "unit": "rial_per_paper",
        "jalali_date": clean(row["JalaliDate"]).replace("-", "/"),
        "date": date_iso,
        "source": source_url,
        "price_type": "official_daily_close",
    }


def ime_salaf_quote():
    # fima is an MIT-licensed client for public IME endpoints; no API key/token.
    from fima.IME import get_all_ime_salaf_trades
    history = get_all_ime_salaf_trades(start_date="1405-06-21")
    return parse_ime_salaf_frame(history)


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


def apply_base_forward_anchor(quotes, errors, data, expected_session):
    """Reuse the already verified Aske2 anchor only on that exact same session."""
    if "coin_forward" in quotes or len(quotes) != 3:
        return False
    dates = {q["date"] for q in quotes.values()}
    if len(dates) != 1:
        return False
    common = next(iter(dates))
    inputs = data.get("inputs", {})
    snapshot = data.get("component_market_snapshot", {}).get("coin_forward", {})
    base_date = inputs.get("base_nav_date")
    anchor_date = snapshot.get("quote_date")
    anchor_price = snapshot.get("closing_price_rial_per_paper")
    if not (common == expected_session == base_date == anchor_date and anchor_price):
        return False
    quotes["coin_forward"] = {
        "key": "coin_forward",
        "symbol": "عسکه۲",
        "price": int(anchor_price),
        "unit": "rial_per_paper",
        "jalali_date": snapshot.get("quote_jalali"),
        "date": anchor_date,
        "source": snapshot.get("source_price_history", "https://www.ime.co.ir/standard-transactions.html"),
        "price_type": "same_session_base_anchor",
        "anchor_fallback": True,
    }
    if "coin_forward" in errors:
        errors["coin_forward_refresh_error"] = errors.pop("coin_forward")
    return True

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
        quotes["coin_forward"] = ime_salaf_quote()
    except Exception as exc:
        errors["coin_forward"] = f"{type(exc).__name__}: {str(exc)[:220]}"

    expected_session = expected_exchange_session_date(now)
    data = json.loads(FAIR_NAV.read_text(encoding="utf-8"))
    base_anchor_fallback_used = apply_base_forward_anchor(quotes, errors, data, expected_session)
    dates = {q["date"] for q in quotes.values()}
    common_date = next(iter(dates)) if len(dates) == 1 and len(quotes) == 4 else None
    macro_sync = False
    macro_reason = "component_quotes_not_synchronized"
    if common_date:
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
        "base_anchor_fallback_used": base_anchor_fallback_used,
        "macro_sync_reason": macro_reason,
        "promoted_to_valuation_model": promoted,
        "quotes": quotes,
        "errors": errors,
        "rules": [
            "Only explicitly dated last-trade/daily-close quotes are accepted.",
            "All four component quotes must share the latest scheduled exchange session before promotion.",
            "USD and XAU macro quotes must also match that exchange session; the official NAV anchor is used only when the session equals the base NAV date.",
            "Thursday and Friday are mapped to the preceding Wednesday; unmodeled holidays still block promotion when quote dates differ.",
            "A previously verified base Aske2 quote may only anchor the exact same base-NAV exchange session; it is never carried into a later session.",
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
