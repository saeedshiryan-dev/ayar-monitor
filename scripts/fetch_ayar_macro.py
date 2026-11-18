import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API = "https://api.alanchand.com/"
TEHRAN = ZoneInfo("Asia/Tehran")
OUT = Path("data/ayar_macro_live.json")
TIMEOUT = (10, 25)


def now_tehran():
    return datetime.now(TEHRAN)


def parse_time(value):
    if not value:
        raise ValueError("فیلد زمان به‌روزرسانی در پاسخ API وجود ندارد")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TEHRAN)
    return parsed.astimezone(TEHRAN)


def number(item, keys):
    for key in keys:
        value = item.get(key)
        try:
            value = float(value)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    raise ValueError("قیمت معتبر در فیلدهای " + ", ".join(keys) + " پیدا نشد")


def fetch_item(token, category, symbol):
    response = requests.get(
        API,
        params={"type": category, "symbols": symbol},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get(symbol), dict):
        raise ValueError(f"ساختار پاسخ {symbol} معتبر نیست: {str(payload)[:250]}")
    item = payload[symbol]
    updated_raw = item.get("updated_at") or item.get("updatedAt") or item.get("as_of")
    updated = parse_time(updated_raw)
    age = (now_tehran() - updated).total_seconds()
    if age < -300:
        raise ValueError(f"زمان منبع {symbol} بیش از ۵ دقیقه در آینده است")
    if age > 20 * 60:
        raise ValueError(f"قیمت {symbol} کهنه است؛ سن داده {int(age / 60)} دقیقه")
    return {
        "symbol": symbol,
        "name": item.get("name"),
        "buy": number(item, ["buy", "bid"]),
        "sell": number(item, ["sell", "ask", "price", "last"]),
        "updated_at": updated.isoformat(),
        "age_seconds": max(0, int(age)),
        "raw_source_fields": {
            key: item.get(key)
            for key in ("open", "high", "low", "dayChange", "dolar_rate")
            if key in item
        },
    }


def write(payload):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)


def main():
    now = now_tehran()
    token = os.environ.get("ALANCHAND_TOKEN", "").strip()
    if not token:
        write({
            "status": "missing_token",
            "source": "AlanChand API",
            "retrieved_at": now.isoformat(),
            "error_fa": "توکن API تنظیم نشده است؛ Secret با نام ALANCHAND_TOKEN لازم است.",
            "usd": None,
            "xau": None,
        })
        print("ALANCHAND_TOKEN is not configured; wrote an explicit blocked status.")
        return

    try:
        usd = fetch_item(token, "currencies", "usd")
        xau = fetch_item(token, "golds", "usd_xau")
        result = {
            "status": "ok",
            "source": "AlanChand API",
            "retrieved_at": now_tehran().isoformat(),
            "timezone": "Asia/Tehran",
            "refresh_claim_minutes": 5,
            "max_accepted_age_minutes": 20,
            "usd": {
                **usd,
                "unit": "تومان به‌ازای هر دلار",
                "display_price": usd["sell"],
            },
            "xau": {
                **xau,
                "unit": "دلار به‌ازای هر اونس تروا",
                "display_price": xau["sell"],
            },
            "validation": {
                "both_quotes_present": True,
                "both_timestamps_fresh": True,
                "usd_source_timestamp": usd["updated_at"],
                "xau_source_timestamp": xau["updated_at"],
            },
        }
        write(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Never replace a previous good quote with an invalid/empty response.
        previous = {}
        if OUT.exists():
            try:
                previous = json.loads(OUT.read_text(encoding="utf-8"))
            except Exception:
                previous = {}
        write({
            "status": "error",
            "source": "AlanChand API",
            "retrieved_at": now_tehran().isoformat(),
            "error_fa": str(exc)[:500],
            "last_good": previous if previous.get("status") == "ok" else None,
            "usd": None,
            "xau": None,
        })
        raise


if __name__ == "__main__":
    main()
