import json
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://nerkhara.com/api/v1/latest"
OUT = Path("data/ayar_macro_live.json")
TIMEOUT = (10, 25)
MAX_AGE_SECONDS = 20 * 60


def parse_utc(value):
    if not value:
        raise ValueError("زمان به‌روزرسانی منبع موجود نیست")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_rate(symbol, expected_unit):
    response = requests.get(
        f"{BASE}/{symbol}.json",
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    item = payload.get("item") if isinstance(payload, dict) else None
    if not isinstance(item, dict) or item.get("id") != symbol:
        raise ValueError(f"ساختار پاسخ {symbol} معتبر نیست")
    price = float(item.get("price") or 0)
    if price <= 0:
        raise ValueError(f"قیمت {symbol} معتبر نیست")
    if item.get("unit") != expected_unit:
        raise ValueError(f"واحد {symbol} غیرمنتظره است: {item.get('unit')}")
    updated = parse_utc(item.get("updated_utc"))
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    if item.get("stale") is True or age < -300 or age > MAX_AGE_SECONDS:
        raise ValueError(f"داده {symbol} تازه نیست؛ سن داده {int(age / 60)} دقیقه")
    return {
        "symbol": symbol,
        "name": (item.get("name") or {}).get("fa"),
        "buy": price,
        "sell": price,
        "display_price": price,
        "unit": "تومان به‌ازای هر دلار" if symbol == "usd" else "دلار به‌ازای هر اونس تروا",
        "updated_at": updated.isoformat(),
        "age_seconds": max(0, int(age)),
        "source_method": item.get("method"),
        "source_count": item.get("sources"),
        "change_pct": item.get("change_pct"),
        "source_page": (item.get("page") or {}).get("fa"),
    }


def write(payload):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)


def main():
    now = datetime.now(timezone.utc)
    try:
        usd = fetch_rate("usd", "IRT")
        xau = fetch_rate("xau", "USD")
        result = {
            "status": "ok",
            "source": "NerkhAra public API",
            "source_url": "https://nerkhara.com/en/api/",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "timezone": "UTC",
            "refresh_claim_minutes": 10,
            "max_accepted_age_minutes": 20,
            "usd": usd,
            "xau": xau,
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
        previous = {}
        if OUT.exists():
            try:
                previous = json.loads(OUT.read_text(encoding="utf-8"))
            except Exception:
                pass
        write({
            "status": "error",
            "source": "NerkhAra public API",
            "retrieved_at": now.isoformat(),
            "error_fa": str(exc)[:500],
            "last_good": previous if previous.get("status") == "ok" else None,
            "usd": None,
            "xau": None,
        })
        raise


if __name__ == "__main__":
    main()
