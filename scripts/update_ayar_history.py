
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
    url = (
        f"{BASE}/ClosingPrice/"
        f"GetClosingPriceDailyList/{INS_CODE}/0"
    )

    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(20, 120)
    )
    response.raise_for_status()

    raw = response.json()
    records = raw.get("closingPriceDaily")

    if not isinstance(records, list) or not records:
        raise ValueError("تاریخچه روزانه دریافت نشد")

    history = []

    for item in records:
        try:
            history.append({
                "date": str(item["dEven"]),
                "open": int(item["priceFirst"]),
                "high": int(item["priceMax"]),
                "low": int(item["priceMin"]),
                "close": int(item["pClosing"]),
                "last": int(item["pDrCotVal"]),
                "volume": int(item["qTotTran5J"]),
                "trade_count": int(item["zTotTran"])
            })
        except (KeyError, TypeError, ValueError):
            raise ValueError("رکورد تاریخچه ناقص یا نامعتبر است")

    history.sort(key=lambda x: x["date"])

    dates = [x["date"] for x in history]

    if len(dates) != len(set(dates)):
        raise ValueError("تاریخ‌های تکراری در داده وجود دارد")

    if len(history) < 200:
        print("WARNING: کمتر از 200 رکورد روزانه دریافت شد")

    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "source": "TSETMC",
        "retrieved_at": datetime.now(TEHRAN).isoformat(),
        "timezone": "Asia/Tehran",
        "validated": True,
        "record_count": len(history),
        "first_date": history[0]["date"],
        "last_date": history[-1]["date"],
        "history": history
    }

    output = DATA_DIR / "ayar_daily_history.json"
    temporary = output.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temporary.replace(output)

    print("SUCCESS:", output)
    print("Records:", len(history))
    print("First date:", history[0]["date"])
    print("Last date:", history[-1]["date"])


if __name__ == "__main__":
    main()
  
