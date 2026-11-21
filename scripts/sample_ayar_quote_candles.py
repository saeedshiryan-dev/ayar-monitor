import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")
SAMPLES_FILE = Path("data/ayar_quote_samples.jsonl")
CANDLES_FILE = Path("data/ayar_quote_candles.json")
POLL_SECONDS = 30
SAMPLE_COUNT = 9  # about four minutes; workflow is scheduled every five minutes
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Referer": "https://www.tsetmc.com/",
    "Origin": "https://www.tsetmc.com",
}


def as_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return None


def fetch_snapshot(session):
    url = f"{BASE}/ClosingPrice/GetClosingPriceInfo/{INS_CODE}"
    last_error = None
    for attempt in range(2):
        try:
            response = session.get(url, timeout=(10, 25))
            response.raise_for_status()
            payload = response.json()
            quote = payload.get("closingPriceInfo") if isinstance(payload, dict) else None
            if not isinstance(quote, dict):
                raise ValueError("TSETMC response has no closingPriceInfo object")
            now = datetime.now(TEHRAN)
            quote_date = str(as_int(quote.get("dEven") or quote.get("finalLastDate")) or "")
            price = as_int(quote.get("pDrCotVal"))
            trade_count = as_int(quote.get("zTotTran"))
            total_volume = as_int(quote.get("qTotTran5J"))
            if quote_date != now.strftime("%Y%m%d"):
                raise ValueError(f"Quote date {quote_date} does not match Tehran date {now:%Y%m%d}")
            if not price or price <= 0 or trade_count is None or trade_count <= 0:
                raise ValueError("No valid live trade price/count in TSETMC snapshot")
            return {
                "timestamp": now.isoformat(),
                "session_date": quote_date,
                "price": price,
                "trade_count_cumulative": trade_count,
                "volume_cumulative": total_volume,
                "source": "TSETMC ClosingPrice/GetClosingPriceInfo",
                "sample_type": "sampled_quote_not_trade_tick",
            }
        except (requests.RequestException, ValueError, TypeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(2)
    raise RuntimeError(str(last_error))


def load_samples():
    if not SAMPLES_FILE.exists():
        return []
    rows = []
    for line in SAMPLES_FILE.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict) and item.get("timestamp") and item.get("session_date"):
                rows.append(item)
        except json.JSONDecodeError:
            continue
    return rows


def make_candle(rows, start_hour, end_hour, now):
    if not rows:
        return None
    prices = [x["price"] for x in rows if isinstance(x.get("price"), int) and x["price"] > 0]
    if not prices:
        return None
    counts = [x.get("trade_count_cumulative") for x in rows]
    counts = [x for x in counts if isinstance(x, int)]
    volumes = [x.get("volume_cumulative") for x in rows]
    volumes = [x for x in volumes if isinstance(x, int)]
    gaps = []
    stamps = [datetime.fromisoformat(x["timestamp"]) for x in rows]
    for a, b in zip(stamps, stamps[1:]):
        gaps.append((b - a).total_seconds())
    expected_end = datetime.combine(stamps[0].date(), datetime.min.time(), tzinfo=TEHRAN) + timedelta(hours=end_hour)
    return {
        "date": rows[0]["session_date"],
        "start": f"{start_hour:02d}:00",
        "end": f"{end_hour:02d}:00",
        "timestamp": f'{rows[0]["session_date"]}T{start_hour:02d}:00:00+03:30',
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume_delta_sampled": max(0, volumes[-1] - volumes[0]) if len(volumes) >= 2 else None,
        "trade_count_delta_sampled": max(0, counts[-1] - counts[0]) if len(counts) >= 2 else None,
        "sample_count": len(rows),
        "max_sample_gap_seconds": max(gaps) if gaps else None,
        "partial": now < expected_end,
        "provisional": True,
        "source_type": "sampled_quote_reconstruction",
        "quality_note_fa": "OHLC از نمونه‌های ۳۰ثانیه‌ای قیمت تابلو بازسازی شده؛ بین نمونه‌ها ممکن است سقف/کف یا معاملات جا افتاده باشد."
    }


def build_candles(samples):
    now = datetime.now(TEHRAN)
    by_date = {}
    for row in samples:
        by_date.setdefault(row["session_date"], []).append(row)
    output = {
        "symbol": "عیار",
        "source": "TSETMC ClosingPrice/GetClosingPriceInfo",
        "sampling_interval_seconds": POLL_SECONDS,
        "sampling_method": "repeated_quote_snapshots",
        "updated_at": now.isoformat(),
        "status": "ok" if samples else "not_available",
        "warning_fa": "این‌ها کندل رسمی معاملات نیستند؛ کندل‌های بازسازی‌شده از نمونه‌های تابلو هستند و فقط برای تحلیل کمکی مناسب‌اند.",
        "timeframes": {"1h": [], "4h": []}
    }
    for date in sorted(by_date):
        rows = sorted(by_date[date], key=lambda x: x["timestamp"])
        for hour in range(12, 18):
            selected = [x for x in rows if hour <= datetime.fromisoformat(x["timestamp"]).hour < hour + 1]
            candle = make_candle(selected, hour, hour + 1, now)
            if candle:
                output["timeframes"]["1h"].append(candle)
        for start, end in ((12, 16), (16, 18)):
            selected = [x for x in rows if start <= datetime.fromisoformat(x["timestamp"]).hour < end]
            candle = make_candle(selected, start, end, now)
            if candle:
                output["timeframes"]["4h"].append(candle)
    return output


def main():
    SAMPLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)
    collected = []
    for index in range(SAMPLE_COUNT):
        try:
            sample = fetch_snapshot(session)
            collected.append(sample)
            print(json.dumps({"sample": index + 1, **sample}, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({"sample": index + 1, "status": "failed", "error": str(exc)}, ensure_ascii=False))
        if index < SAMPLE_COUNT - 1:
            time.sleep(POLL_SECONDS)

    existing = load_samples()
    combined = {(
        x.get("timestamp"), x.get("trade_count_cumulative"), x.get("price")
    ): x for x in existing}
    for x in collected:
        combined[(x["timestamp"], x["trade_count_cumulative"], x["price"])] = x
    cutoff = datetime.now(TEHRAN) - timedelta(days=7)
    retained = []
    for x in combined.values():
        try:
            if datetime.fromisoformat(x["timestamp"]) >= cutoff:
                retained.append(x)
        except (ValueError, TypeError):
            continue
    retained.sort(key=lambda x: x["timestamp"])
    temp = SAMPLES_FILE.with_suffix(".tmp")
    temp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\\n" for x in retained), encoding="utf-8")
    temp.replace(SAMPLES_FILE)

    candles = build_candles(retained)
    temp = CANDLES_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(candles, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CANDLES_FILE)
    print(json.dumps({
        "collected_this_run": len(collected),
        "retained_samples": len(retained),
        "one_hour_candles": len(candles["timeframes"]["1h"]),
        "four_hour_candles": len(candles["timeframes"]["4h"]),
        "output": str(CANDLES_FILE),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
