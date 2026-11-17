import json
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")
DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "intraday"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "ayar_daily_history.json"
LOOKBACK_DAYS = 130
REFRESH_LATEST_DAYS = 3

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.tsetmc.com/",
})


def get_json(url):
    response = session.get(url, timeout=(20, 90))
    response.raise_for_status()
    return response.json()


def make_candle(rows, date, start_hour, end_hour, partial=False):
    if not rows:
        return None
    return {
        "date": date,
        "start": f"{start_hour:02d}:00",
        "end": f"{end_hour:02d}:00",
        "timestamp": f"{date}T{start_hour:02d}:00:00+03:30",
        "open": rows[0]["price"],
        "high": max(x["price"] for x in rows),
        "low": min(x["price"] for x in rows),
        "close": rows[-1]["price"],
        "volume": sum(x["volume"] for x in rows),
        "trades": len(rows),
        "partial": partial,
    }


def download_day(daily):
    date = str(daily["date"])
    url = f"{BASE}/Trade/GetTradeHistory/{INS_CODE}/{date}/false"
    payload = get_json(url)
    if not isinstance(payload, dict) or not isinstance(payload.get("tradeHistory"), list):
        raise ValueError(f"ساختار API معاملات برای {date} نامعتبر است")

    raw = payload["tradeHistory"]
    unique = {}
    for item in raw:
        if int(item.get("canceled", 0) or 0) != 0:
            continue
        try:
            number = int(item["nTran"])
            hour = int(item["hEven"])
            price = int(item["pTran"])
            volume = int(item["qTitTran"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"رکورد ناقص در تاریخ {date}")

        if not 120000 <= hour <= 180000 or price <= 0 or volume <= 0:
            raise ValueError(f"ساعت/قیمت/حجم نامعتبر در تاریخ {date}")

        row = {"nTran": number, "hEven": hour, "price": price, "volume": volume}
        previous = unique.get(number)
        if previous is not None and previous != row:
            raise ValueError(f"معامله تکراری متعارض در {date}: {number}")
        unique[number] = row

    rows = sorted(unique.values(), key=lambda x: (x["hEven"], x["nTran"]))
    prices = [x["price"] for x in rows]
    checks = {
        "trade_count": len(rows) == int(daily["trade_count"]),
        "volume": sum(x["volume"] for x in rows) == int(daily["volume"]),
        "minimum": bool(prices) and min(prices) == int(daily["low"]),
        "maximum": bool(prices) and max(prices) == int(daily["high"]),
    }
    if not all(checks.values()):
        raise ValueError(f"تطبیق معاملات با آمار روزانه ناموفق در {date}: {checks}")

    candles_1h = []
    for hour in range(12, 18):
        selected = [x for x in rows if hour * 10000 <= x["hEven"] < (hour + 1) * 10000]
        candle = make_candle(selected, date, hour, hour + 1)
        if candle:
            candles_1h.append(candle)

    candles_4h = []
    for start, end in ((12, 16), (16, 18)):
        selected = [
            x for x in rows
            if start * 10000 <= x["hEven"] < end * 10000
            or (end == 18 and x["hEven"] == 180000)
        ]
        candle = make_candle(selected, date, start, end, partial=(end - start) < 4)
        if candle:
            candles_4h.append(candle)

    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "date": date,
        "timezone": "Asia/Tehran",
        "source": "TSETMC",
        "retrieved_at": datetime.now(TEHRAN).isoformat(),
        "validated": True,
        "validation": checks,
        "raw_records": len(raw),
        "unique_transactions": len(rows),
        "daily_volume": sum(x["volume"] for x in rows),
        "candles_1h": candles_1h,
        "candles_4h": candles_4h,
    }
    path = CACHE_DIR / f"ayar_{date}.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return result


def main():
    if not HISTORY_FILE.exists():
        raise FileNotFoundError("ابتدا تاریخچه روزانه عیار را دریافت کنید")
    source = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    history = source.get("valid_history")
    if not isinstance(history, list) or len(history) < 100:
        raise ValueError("تاریخچه روزانه معتبر برای ساخت تاریخچه درون‌روزی کافی نیست")

    selected = history[-LOOKBACK_DAYS:]
    refresh_dates = {str(x["date"]) for x in selected[-REFRESH_LATEST_DAYS:]}
    fetched = 0
    reused = 0
    failed = []

    for daily in selected:
        date = str(daily["date"])
        path = CACHE_DIR / f"ayar_{date}.json"
        if path.exists() and date not in refresh_dates:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("validated") is True:
                reused += 1
                continue
        try:
            download_day(daily)
            fetched += 1
            time.sleep(0.15)
        except Exception as error:
            failed.append({"date": date, "error": str(error)})
            raise RuntimeError(f"دریافت/اعتبارسنجی داده در {date} شکست خورد: {error}") from error

    print(json.dumps({
        "status": "success",
        "lookback_trading_days": len(selected),
        "downloaded_or_refreshed": fetched,
        "cached_reused": reused,
        "failed": failed,
        "first_date": selected[0]["date"],
        "last_date": selected[-1]["date"],
        "cache_dir": str(CACHE_DIR),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
