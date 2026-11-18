import json
from datetime import datetime, time
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
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.tsetmc.com/",
})


def get_json(url):
    response = SESSION.get(url, timeout=(15, 45))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("پاسخ API معاملات ساختار معتبر ندارد")
    return payload


def make_candle(rows, date, start, end, now):
    if not rows:
        return None
    end_time = time(end, 0) if end < 18 else time(18, 0)
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
    payload = get_json(url)
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
