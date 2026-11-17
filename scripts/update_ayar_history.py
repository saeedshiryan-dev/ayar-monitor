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


def main():
    url = f"{BASE}/ClosingPrice/GetClosingPriceDailyList/{INS_CODE}/0"

    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(20, 120)
    )
    response.raise_for_status()

    raw = response.json()

    if not isinstance(raw, dict):
        raise ValueError("ساختار پاسخ TSETMC نامعتبر است")

    records = raw.get("closingPriceDaily")

    if not isinstance(records, list) or not records:
        raise ValueError("تاریخچه روزانه دریافت نشد")

    history = []
    valid_history = []
    no_trade_days = []
    excluded_records = []

    for item in records:
        try:
            date = str(item["dEven"])

            if len(date) != 8 or not date.isdigit():
                raise ValueError("تاریخ نامعتبر")

            datetime.strptime(date, "%Y%m%d")

            record = {
                "date": date,
                "open": int(item["priceFirst"]),
                "high": int(item["priceMax"]),
                "low": int(item["priceMin"]),
                "close": int(item["pClosing"]),
                "last": int(item["pDrCotVal"]),
                "volume": int(item["qTotTran5J"]),
                "trade_count": int(item["zTotTran"])
            }

            if record["volume"] < 0 or record["trade_count"] < 0:
                raise ValueError("حجم یا تعداد معاملات منفی است")

            history.append(record)

            if record["volume"] == 0 or record["trade_count"] == 0:
                no_trade_days.append({
                    "date": date,
                    "reason": "بدون معامله"
                })
                continue

            low = record["low"]
            high = record["high"]
            open_price = record["open"]
            close = record["close"]
            last = record["last"]

            reasons = []

            if open_price <= 0:
                reasons.append("قیمت بازگشایی نامعتبر")

            if low <= 0 or high < low:
                reasons.append("بازه کمینه و بیشینه نامعتبر")

            if close <= 0 or last <= 0:
                reasons.append("قیمت پایانی یا آخرین معامله نامعتبر")

            if low > 0 and high >= low:
                if close > 0 and not (low <= close <= high):
                    reasons.append("قیمت پایانی خارج از بازه")

                if last > 0 and not (low <= last <= high):
                    reasons.append("آخرین معامله خارج از بازه")

                if open_price > 0 and not (low <= open_price <= high):
                    reasons.append("قیمت بازگشایی خارج از بازه")

            if reasons:
                excluded_records.append({
                    "date": date,
                    "reason": reasons
                })
                continue

            valid_history.append(record)

        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"رکورد ناقص یا نامعتبر است: {item} | {error}"
            )

    history.sort(key=lambda x: x["date"])
    valid_history.sort(key=lambda x: x["date"])

    dates = [x["date"] for x in history]

    if len(dates) != len(set(dates)):
        raise ValueError("تاریخ‌های تکراری در داده وجود دارد")

    if len(valid_history) < 200:
        raise ValueError("تعداد روزهای معاملاتی معتبر برای تحلیل کافی نیست")

    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "source": "TSETMC",
        "retrieved_at": datetime.now(TEHRAN).isoformat(),
        "timezone": "Asia/Tehran",
        "validated": True,
        "record_count": len(history),
        "valid_record_count": len(valid_history),
        "no_trade_day_count": len(no_trade_days),
        "excluded_record_count": len(excluded_records),
        "first_date": history[0]["date"],
        "last_date": history[-1]["date"],
        "first_valid_date": valid_history[0]["date"],
        "last_valid_date": valid_history[-1]["date"],
        "history": history,
        "valid_history": valid_history,
        "data_quality": {
            "no_trade_days": no_trade_days,
            "excluded_records": excluded_records
        }
    }

    output = DATA_DIR / "ayar_daily_history.json"
    temporary = output.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temporary.replace(output)

    print("SUCCESS:", output)
    print("Total records:", len(history))
    print("Valid trading days:", len(valid_history))
    print("No-trade days:", len(no_trade_days))
    print("Excluded records:", len(excluded_records))
    print("First valid date:", valid_history[0]["date"])
    print("Last valid date:", valid_history[-1]["date"])

    for item in excluded_records:
        print("EXCLUDED:", item)


if __name__ == "__main__":
    main()
