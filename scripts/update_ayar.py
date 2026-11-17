
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
})


def get_json(url):
    response = session.get(url, timeout=(20, 120))
    response.raise_for_status()
    return response.json()


def get_records(data, possible_keys):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in possible_keys:
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise ValueError("ساختار پاسخ API قابل شناسایی نیست")


def main():
    today = datetime.now(TEHRAN).date()
    day = today.strftime("%Y%m%d")

    print("Ayar date:", day)

    # دریافت معاملات کامل روز
    trade_url = (
        f"{BASE}/Trade/GetTradeHistory/"
        f"{INS_CODE}/{day}/false"
    )

    trade_data = get_json(trade_url)
    trades = get_records(
        trade_data,
        ["tradeHistory"]
    )

    # اگر هنوز معامله‌ای ثبت نشده، فقط این اسکریپت پایان می‌یابد
    if not trades:
        print("WARNING: امروز هنوز معامله‌ای ثبت نشده است.")
        print("Intraday data skipped; historical data will continue.")
        return

    # دریافت آمار روزانه برای کنترل صحت
    daily_url = (
        f"{BASE}/ClosingPrice/GetClosingPriceDailyList/"
        f"{INS_CODE}/10"
    )

    daily_data = get_json(daily_url)
    daily_records = get_records(
        daily_data,
        ["closingPriceDaily", "closingPriceDailyList"]
    )

    daily = next(
        (
            item for item in daily_records
            if str(item.get("dEven")) == day
        ),
        None
    )

    if daily is None:
        raise ValueError("آمار روزانه امروز در دسترس نیست")

    # پاک‌سازی و مرتب‌سازی معاملات
    valid = []

    for item in trades:
        if item.get("canceled", 0) != 0:
            continue

        try:
            n = int(item["nTran"])
            h = int(item["hEven"])
            price = int(item["pTran"])
            volume = int(item["qTitTran"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("رکورد معامله ناقص است")

        if not (120000 <= h <= 180000):
            raise ValueError("ساعت معامله خارج از بازه مورد انتظار است")

        if price <= 0 or volume <= 0:
            raise ValueError("قیمت یا حجم معامله نامعتبر است")

        valid.append({
            "nTran": n,
            "hEven": h,
            "price": price,
            "volume": volume,
        })

    if not valid:
        print("WARNING: هیچ معامله معتبر باقی نماند.")
        print("Intraday data skipped; historical data will continue.")
        return

    valid.sort(key=lambda x: (x["hEven"], x["nTran"]))

    # کنترل تطبیق با آمار رسمی روزانه
    total_volume = sum(x["volume"] for x in valid)
    prices = [x["price"] for x in valid]
    last_trade = valid[-1]["price"]

    checks = {
        "trade_count": len(valid) == int(daily["zTotTran"]),
        "volume": total_volume == int(daily["qTotTran5J"]),
        "minimum": min(prices) == int(daily["priceMin"]),
        "maximum": max(prices) == int(daily["priceMax"]),
        "last_trade": last_trade == int(daily["pDrCotVal"]),
    }

    if not all(checks.values()):
        raise ValueError(
            "تطبیق داده ناموفق است: "
            + json.dumps(checks, ensure_ascii=False)
        )

    # ساخت کندل‌ها
    def make_candle(rows, start, end):
        if not rows:
            return None

        return {
            "start": start,
            "end": end,
            "open": rows[0]["price"],
            "high": max(x["price"] for x in rows),
            "low": min(x["price"] for x in rows),
            "close": rows[-1]["price"],
            "volume": sum(x["volume"] for x in rows),
            "trades": len(rows),
        }

    candles_1h = []

    for hour in range(12, 18):
        start = hour * 10000
        end = (hour + 1) * 10000

        rows = [
            x for x in valid
            if start <= x["hEven"] < end
        ]

        candle = make_candle(
            rows,
            f"{hour:02d}:00",
            f"{hour+1:02d}:00"
        )

        if candle:
            candles_1h.append(candle)

    candles_4h = []

    for start_hour, end_hour in [(12, 16), (16, 18)]:
        start = start_hour * 10000
        end = end_hour * 10000

        rows = [
            x for x in valid
            if start <= x["hEven"] < end
        ]

        candle = make_candle(
            rows,
            f"{start_hour:02d}:00",
            f"{end_hour:02d}:00"
        )

        if candle:
            candle["partial"] = (end_hour - start_hour) < 4
            candles_4h.append(candle)

    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "date": today.isoformat(),
        "timezone": "Asia/Tehran",
        "source": "TSETMC",
        "retrieved_at": datetime.now(TEHRAN).isoformat(),
        "validated": True,
        "validation": checks,
        "daily": {
            "last_trade": int(daily["pDrCotVal"]),
            "closing_price": int(daily["pClosing"]),
            "yesterday_price": int(daily["priceYesterday"]),
            "minimum": int(daily["priceMin"]),
            "maximum": int(daily["priceMax"]),
            "volume": total_volume,
            "trade_count": len(valid),
        },
        "candles_1h": candles_1h,
        "candles_4h": candles_4h,
    }

    output = DATA_DIR / f"ayar_{day}.json"
    temporary = output.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temporary.replace(output)

    print("SUCCESS:", output)
    print("Trades:", len(valid))
    print("Volume:", total_volume)
    print("Last price:", last_trade)


if __name__ == "__main__":
    main()
    
