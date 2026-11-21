import json
import random
import time
from datetime import datetime, time as datetime_time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from ayar_market_status import get_market_status

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")
OUTPUT = Path("data/ayar_intraday_live.json")
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Referer": "https://www.tsetmc.com/",
    "Origin": "https://www.tsetmc.com",
})


def get_json(url, attempts=5):
    """Fetch TSETMC JSON with bounded retries for transient gateway/rate-limit errors."""
    last_error = None
    for attempt in range(attempts):
        try:
            response = SESSION.get(url, timeout=(10, 30))
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as error:
                raise ValueError(
                    f"پاسخ TSETMC معتبر و JSON نیست؛ HTTP {response.status_code}"
                ) from error
            if not isinstance(payload, dict):
                raise ValueError("پاسخ API معاملات ساختار معتبر ندارد")
            return payload
        except requests.RequestException as error:
            last_error = error
            status = getattr(getattr(error, "response", None), "status_code", None)
            retryable = (
                status in (408, 425, 429, 500, 502, 503, 504)
                or isinstance(error, (requests.Timeout, requests.ConnectionError))
            )
            if not retryable or attempt == attempts - 1:
                break
            delay = min(30, 2 ** attempt) + random.uniform(0.2, 1.2)
            print(
                f"TSETMC transient error (HTTP {status or 'network'}); "
                f"retry {attempt + 2}/{attempts} in {delay:.1f}s"
            )
            time.sleep(delay)
    raise last_error


def make_candle(rows, date, start, end, now):
    if not rows:
        return None
    end_time = datetime_time(end, 0) if end < 18 else datetime_time(18, 0)
    return {
        "date": date,
        "start": f"{start:02d}:00",
        "end": f"{end:02d}:00",
        "timestamp": f"{date}T{start:02d}:00:00+03:30",
        "open": rows[0]["price"],
        "high": max(x["price"] for x in rows),
        "low": min(x["price"] for x in rows),
        "close": rows[-1]["price"],
        "volume": sum(x["volume"] for x in rows),
        "trades": len(rows),
        "partial": now.time().replace(tzinfo=None) < end_time,
        "provisional": True,
    }


def main():
    now = datetime.now(TEHRAN)
    date = now.strftime("%Y%m%d")
    market = get_market_status(now)
    result = {
        "symbol": "عیار", "source": "TSETMC GetTrade",
        "date": date, "timezone": "Asia/Tehran",
        "retrieved_at": now.isoformat(), "status": "not_available",
        "trade_count": 0, "candles_1h": [], "candles_4h": [],
        "note_fa": "کندل‌های موقت از endpoint معاملات زنده TSETMC؛ آمار نهایی روز نیست.",
    }

    if market.get("status") != "open":
        result["reason"] = "بازار در وضعیت معاملات پیوسته نیست"
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return

    url = f"{BASE}/Trade/GetTrade/{INS_CODE}"
    try:
        payload = get_json(url)
    except requests.RequestException as error:
        result["status"] = "not_available"
        result["reason"] = f"اختلال موقت TSETMC پس از چند تلاش: {str(error)[:250]}"
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temp = OUTPUT.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(OUTPUT)
        print(json.dumps(result, ensure_ascii=False))
        return

    raw = payload.get("trade") or payload.get("tradeHistory")
    if not isinstance(raw, list):
        raise ValueError("آرایه trade در پاسخ GetTrade TSETMC موجود نیست")

    unique = {}
    for item in raw:
        if not isinstance(item, dict) or int(item.get("canceled", 0) or 0) != 0:
            continue
        try:
            number = int(item["nTran"])
            hhmmss = int(item["hEven"])
            price = int(item["pTran"])
            volume = int(item["qTitTran"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 120000 <= hhmmss <= 180000 or price <= 0 or volume <= 0:
            continue
        hour = hhmmss // 10000
        minute = (hhmmss // 100) % 100
        second = hhmmss % 100
        if hour > now.hour or (hour == now.hour and (minute, second) > (now.minute, now.second)):
            continue
        row = {"nTran": number, "hEven": hhmmss, "price": price, "volume": volume}
        unique[number] = row

    rows = sorted(unique.values(), key=lambda x: (x["hEven"], x["nTran"]))
    quote_trade_count = None
    quote_retrieved_at = None
    live_quote_file = Path("data/ayar_live.json")
    if live_quote_file.exists():
        try:
            quote_snapshot = json.loads(live_quote_file.read_text(encoding="utf-8"))
            quote_trade_count = int(quote_snapshot.get("tsetmc_quote_trade_count") or 0) or None
            quote_retrieved_at = quote_snapshot.get("retrieved_at")
        except (OSError, ValueError, TypeError):
            quote_trade_count = None
            quote_retrieved_at = None

    observed_delta = quote_trade_count - len(rows) if quote_trade_count is not None else None
    comparable = False
    quote_age_seconds = None
    if quote_retrieved_at:
        try:
            quote_dt = datetime.fromisoformat(quote_retrieved_at)
            quote_age_seconds = abs((now - quote_dt).total_seconds())
            comparable = quote_age_seconds <= 5
        except ValueError:
            pass
    count_reconciled = (observed_delta == 0) if comparable and observed_delta is not None else None
    reconciliation_status = (
        "reconciled" if count_reconciled is True else
        "mismatch" if count_reconciled is False else
        "not_comparable_time_gap"
    )
    result.update({
        "quote_trade_count": quote_trade_count,
        "quote_retrieved_at": quote_retrieved_at,
        "quote_age_seconds": round(quote_age_seconds, 2) if quote_age_seconds is not None else None,
        "observed_trade_count_delta": observed_delta,
        "trade_count_difference": observed_delta if comparable else None,
        "count_reconciled": count_reconciled,
        "count_reconciliation_status": reconciliation_status,
        "quality_note_fa": (
            "تعداد معاملات در دو snapshot هم‌زمان برابر است."
            if count_reconciled is True else
            "دو snapshot حداکثر ۵ ثانیه اختلاف زمانی دارند اما تعداد معاملات برابر نیست."
            if count_reconciled is False else
            "snapshot قیمت و معاملات هم‌زمان نیستند؛ اختلاف مشاهده‌شده معیار قطعی کسری معاملات نیست."
        ),
    })
    if not rows:
        result["reason"] = "هنوز معامله قابل استفاده‌ای از API معاملات زنده دریافت نشد"
    else:
        candles_1h = []
        for hour in range(12, 18):
            selected = [x for x in rows if hour * 10000 <= x["hEven"] < (hour + 1) * 10000]
            candle = make_candle(selected, date, hour, hour + 1, now)
            if candle:
                candles_1h.append(candle)

        candles_4h = []
        for start, end in ((12, 16),):
            selected = [x for x in rows if start * 10000 <= x["hEven"] < end * 10000]
            candle = make_candle(selected, date, start, end, now)
            if candle:
                candles_4h.append(candle)

        result.update({
            "status": "ok", "trade_count": len(rows),
            "candles_1h": candles_1h, "candles_4h": candles_4h,
            "first_trade_time": f"{rows[0]['hEven']:06d}",
            "last_trade_time": f"{rows[-1]['hEven']:06d}",
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUTPUT)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
