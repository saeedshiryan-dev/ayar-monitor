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
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.tsetmc.com/",
})

def get_json(url):
    response = session.get(url, timeout=(15, 45))
    response.raise_for_status()
    return response.json()

def records(payload, keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("ساختار پاسخ رسمی TSETMC قابل شناسایی نیست")

def main():
    now = datetime.now(TEHRAN)
    day = now.strftime("%Y%m%d")
    market = get_market_status(now)

    # آمار رسمی روز جاری؛ فیلدهای قیمت پایانی و آخرین معامله از TSETMC
    daily_payload = get_json(
        f"{BASE}/ClosingPrice/GetClosingPriceDailyList/{INS_CODE}/10"
    )
    daily_rows = records(
        daily_payload,
        ["closingPriceDaily", "closingPriceDailyList"]
    )
    today_row = next(
        (r for r in daily_rows if str(r.get("dEven")) == day),
        None
    )

    # معاملات ریز روز جاری برای زمان دقیق آخرین معامله
    trade_payload = get_json(
        f"{BASE}/Trade/GetTradeHistory/{INS_CODE}/{day}/false"
    )
    trade_rows = records(trade_payload, ["tradeHistory"])
    valid = []
    for row in trade_rows:
        if int(row.get("canceled", 0) or 0) != 0:
            continue
        try:
            valid.append({
                "number": int(row["nTran"]),
                "time_raw": int(row["hEven"]),
                "price": int(row["pTran"]),
                "volume": int(row["qTitTran"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    valid.sort(key=lambda x: (x["time_raw"], x["number"]))

    def format_time(raw):
        value = f"{raw:06d}"
        return f"{value[0:2]}:{value[2:4]}:{value[4:6]}"

    latest_trade = valid[-1] if valid else None

    # در صورت نبود معامله امروز، آخرین جلسه موجود را از پاسخ روزانه پیدا کن.
    if today_row is None:
        prior = [r for r in daily_rows if str(r.get("dEven", "")).isdigit()]
        prior.sort(key=lambda r: str(r["dEven"]))
        last_session = prior[-1] if prior else None
    else:
        last_session = today_row

    if last_session is None:
        raise ValueError("هیچ رکورد قیمت روزانه‌ای از TSETMC دریافت نشد")

    closing = int(last_session.get("pClosing", 0) or 0)
    official_last = int(last_session.get("pDrCotVal", 0) or 0)

    # قیمت لحظه‌ای فقط وقتی معامله‌ای امروز ثبت شده باشد؛ در غیر این صورت null.
    live_price = latest_trade["price"] if latest_trade else None
    trade_time = format_time(latest_trade["time_raw"]) if latest_trade else None

    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "source": "TSETMC",
        "source_api": "cdn.tsetmc.com/api",
        "retrieved_at": now.isoformat(),
        "timezone": "Asia/Tehran",
        "market": market,
        "session_date": str(last_session.get("dEven", "")),
        "today_has_trade": bool(latest_trade),
        "live_price": live_price,
        "live_price_unit": "ریال",
        "closing_price": closing if closing > 0 else None,
        "last_trade_price": (
            latest_trade["price"] if latest_trade
            else official_last if official_last > 0 else None
        ),
        "last_trade_time": trade_time,
        "last_trade_volume": latest_trade["volume"] if latest_trade else None,
        "official_daily_last_price": official_last if official_last > 0 else None,
        "price_note_fa": (
            "قیمت لحظه‌ای برابر آخرین معامله ثبت‌شده امروز است."
            if latest_trade else
            "امروز معامله‌ای در پاسخ TSETMC ثبت نشده؛ قیمت لحظه‌ای اعلام نمی‌شود و فقط آخرین جلسه گزارش می‌شود."
        ),
    }

    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
