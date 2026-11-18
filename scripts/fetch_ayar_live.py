import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from ayar_market_status import get_market_status

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")
OUT = Path("data/ayar_live.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
    "Origin": "https://www.tsetmc.com",
})

def get_json(url):
    response = session.get(url, timeout=(15, 45))
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError(f"پاسخ غیر JSON از TSETMC: {url}") from exc
    if not isinstance(data, (dict, list)):
        raise ValueError(f"ساختار پاسخ نامعتبر از TSETMC: {url}")
    return data

def unwrap(payload, key):
    value = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    return None

def as_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0

def format_time(raw):
    value = f"{as_int(raw):06d}"
    return f"{value[0:2]}:{value[2:4]}:{value[4:6]}" if value != "000000" else None

def main():
    now = datetime.now(TEHRAN)
    today = now.strftime("%Y%m%d")
    market = get_market_status(now)

    # منبع اصلی مظنه زنده: snapshot رسمی اطلاعات قیمت نماد
    quote_payload = get_json(f"{BASE}/ClosingPrice/GetClosingPriceInfo/{INS_CODE}")
    quote = unwrap(quote_payload, "closingPriceInfo")
    if not isinstance(quote, dict):
        raise ValueError("فیلد closingPriceInfo در پاسخ TSETMC وجود ندارد")

    # تاریخچه روزانه برای بازیابی آخرین جلسه واقعی، نه فرض کردن روز تقویمی جاری
    daily_payload = get_json(f"{BASE}/ClosingPrice/GetClosingPriceDailyList/{INS_CODE}/0")
    daily_rows = unwrap(daily_payload, "closingPriceDaily")
    if not isinstance(daily_rows, list):
        raise ValueError("فیلد closingPriceDaily در پاسخ TSETMC وجود ندارد")
    daily_rows = [r for r in daily_rows if isinstance(r, dict) and str(r.get("dEven", "")).isdigit()]
    daily_rows.sort(key=lambda r: str(r["dEven"]))

    quote_date = str(as_int(quote.get("dEven") or quote.get("finalLastDate")))
    quote_trades = as_int(quote.get("zTotTran"))
    quote_last = as_int(quote.get("pDrCotVal"))
    quote_close = as_int(quote.get("pClosing"))

    # فقط وقتی snapshot واقعاً متعلق به امروز و دارای معامله است، قیمت لحظه‌ای می‌دهیم.
    quote_is_today = quote_date == today
    live_price = quote_last if quote_is_today and quote_trades > 0 and quote_last > 0 else None
    last_trade_time = format_time(quote.get("lastHEven") or quote.get("hEven")) if live_price else None

    # fallback کنترل‌شده: API معاملات روز جاری. این مسیر به تنهایی مجوز انتساب
    # معامله به امروز نیست؛ تاریخ snapshot باید با امروز تطبیق داشته باشد.
    trade_rows = []
    trade_error = None
    try:
        trade_payload = get_json(f"{BASE}/Trade/GetTrade/{INS_CODE}")
        candidate = unwrap(trade_payload, "trade")
        if isinstance(candidate, list):
            trade_rows = [r for r in candidate if isinstance(r, dict)]
    except Exception as exc:
        trade_error = str(exc)[:300]

    # انتخاب آخرین جلسه از تاریخچه روزانه و مقایسه با snapshot
    last_session = daily_rows[-1] if daily_rows else None
    if quote_is_today:
        session_date = today
        closing_price = quote_close or (as_int(last_session.get("pClosing")) if last_session else 0)
        official_last = quote_last
    else:
        session_date = str(last_session.get("dEven")) if last_session else quote_date
        closing_price = as_int(last_session.get("pClosing")) if last_session else quote_close
        official_last = as_int(last_session.get("pDrCotVal")) if last_session else quote_last

    # اگر API قیمت زنده تاریخ امروز را نشان نداد، آن را به عنوان آخرین جلسه قبلی ثبت می‌کنیم.
    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "source": "TSETMC",
        "source_api": "cdn.tsetmc.com/api",
        "retrieved_at": now.isoformat(),
        "timezone": "Asia/Tehran",
        "market": market,
        "session_date": session_date,
        "today_has_trade": bool(live_price),
        "live_price": live_price,
        "live_price_unit": "ریال",
        "closing_price": closing_price or None,
        "last_trade_price": live_price or (official_last if official_last > 0 else None),
        "last_trade_time": last_trade_time,
        "last_trade_volume": None,
        "official_daily_last_price": official_last if official_last > 0 else None,
        "tsetmc_quote_date": quote_date,
        "tsetmc_quote_trade_count": quote_trades,
        "trade_endpoint_rows": len(trade_rows),
        "trade_endpoint_error": trade_error,
        "price_note_fa": (
            "قیمت لحظه‌ای از snapshot قیمت TSETMC دریافت شد."
            if live_price is not None else
            "snapshot قیمت TSETMC هنوز تاریخ امروز یا تعداد معامله مثبت ندارد؛ قیمت لحظه‌ای عمداً خالی گذاشته شده و آخرین جلسه واقعی گزارش می‌شود."
        ),
    }

    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
