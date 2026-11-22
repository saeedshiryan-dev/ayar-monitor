import json
import os
import random
import time
from datetime import datetime, time as datetime_time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from ayar_market_status import get_market_status

BASES = ("https://cdn.tsetmc.com/api", "https://cdn10.tsetmc.com/api")
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")
OUTPUT = Path("data/ayar_intraday_live.json")
SESSION = requests.Session()
proxy = os.environ.get("TSETMC_PROXY", "").strip()
if proxy:
    # HTTPS uses CONNECT tunneling and normal TLS certificate verification.
    SESSION.proxies.update({"http": proxy, "https": proxy})

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
# Quote snapshots work directly from the runner; only trade-detail requests
# use the regional proxy so a slow proxy cannot block price/NAV reconciliation.
DIRECT_SESSION = requests.Session()
DIRECT_SESSION.headers.update(SESSION.headers)


def get_json(url, attempts=2, session=None):
    """Fetch TSETMC JSON with bounded retries for transient gateway/rate-limit errors."""
    last_error = None
    for attempt in range(attempts):
        try:
            response = (session or SESSION).get(url, timeout=(8, 15))
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

    # GetTrade has intermittent 502s. Fall back to the dated trade-history
    # endpoint and the second official CDN host before declaring data unavailable.
    endpoints = []
    for base in BASES:
        endpoints.append((f"{base}/Trade/GetTrade/{INS_CODE}", ("trade", "tradeHistory")))
    for base in BASES:
        for include_canceled in ("false", "true"):
            endpoints.append((
                f"{base}/Trade/GetTradeHistory/{INS_CODE}/{date}/{include_canceled}",
                ("tradeHistory", "trade", "trades")
            ))
    raw = None
    source_url = None
    endpoint_errors = []
    for url, keys in endpoints:
        try:
            payload = get_json(url)
            candidate = next((payload.get(key) for key in keys
                              if isinstance(payload.get(key), list)), None)
            if candidate is None or not candidate:
                raise ValueError("پاسخ endpoint آرایه معاملات خالی/نامعتبر دارد")
            raw, source_url = candidate, url
            break
        except Exception as error:
            endpoint_errors.append({"url": url, "error": str(error)[:180]})

    if raw is None:
        # Last-resort official TSETMC endpoint: intraday OHLCV aggregates.
        # It is accepted only when it covers the session from the open, ends
        # close to now, has no large time gaps, and its summed volume matches
        # the official cumulative quote volume.
        try:
            aggregate_url = (
                f"{BASES[0]}/Trade/GetTradeIntraDay/{INS_CODE}?_={int(time.time())}"
            )
            aggregate_payload = get_json(aggregate_url, session=SESSION)
            aggregate_raw = next((
                aggregate_payload.get(key)
                for key in ("tradeIntraDay", "trade", "trades", "tradeHistory")
                if isinstance(aggregate_payload.get(key), list)
            ), None)
            aggregate_rows = []
            for item in aggregate_raw or []:
                if not isinstance(item, dict):
                    continue
                try:
                    hms = int(item["hEven"])
                    values = {
                        "hEven": hms,
                        "open": int(float(item["openPrice"])),
                        "high": int(float(item["maxPrice"])),
                        "low": int(float(item["minPrice"])),
                        "close": int(float(item["closePrice"])),
                        "volume": int(float(item["volume"])),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                if not 120000 <= hms <= 180000 or min(values["open"], values["high"], values["low"], values["close"]) <= 0 or values["volume"] < 0:
                    continue
                aggregate_rows.append(values)
            aggregate_rows.sort(key=lambda x: x["hEven"])
            if len(aggregate_rows) < 10:
                raise ValueError("تعداد میله‌های تجمیعی TSETMC کافی نیست")

            times = []
            for item in aggregate_rows:
                hms = item["hEven"]
                times.append((hms // 10000) * 3600 + ((hms // 100) % 100) * 60 + (hms % 100))
            now_seconds = now.hour * 3600 + now.minute * 60 + now.second
            last_age = now_seconds - times[-1]
            max_gap = max((b-a for a, b in zip(times, times[1:])), default=99999)
            aggregate_volume = sum(x["volume"] for x in aggregate_rows)

            quote_payload = get_json(
                f"{BASES[0]}/ClosingPrice/GetClosingPriceInfo/{INS_CODE}",
                session=DIRECT_SESSION,
            )
            quote_info = quote_payload.get("closingPriceInfo") if isinstance(quote_payload, dict) else None
            if not isinstance(quote_info, dict) or str(quote_info.get("dEven") or quote_info.get("finalLastDate")) != date:
                raise ValueError("snapshot رسمی حجم معاملات برای تطبیق aggregate در دسترس نیست")
            quote_total_volume = int(quote_info.get("qTotTran5J") or 0)
            volume_gap_pct = abs(aggregate_volume - quote_total_volume) / quote_total_volume * 100 if quote_total_volume else 999
            if (times[0] > 12 * 3600 + 300 or last_age < -30 or last_age > 180
                    or max_gap > 180 or volume_gap_pct > 2.0):
                raise ValueError(
                    f"aggregate ناقص/قدیمی است: last_age={last_age}s, "
                    f"max_gap={max_gap}s, volume_gap={volume_gap_pct:.2f}%"
                )

            def aggregate_candle(items, start_hour, end_hour):
                if not items:
                    return None
                items = sorted(items, key=lambda x: x["hEven"])
                partial = (end_hour * 3600) > now_seconds
                return {
                    "date": date,
                    "start": f"{start_hour:02d}:00",
                    "end": f"{end_hour:02d}:00",
                    "open": items[0]["open"],
                    "high": max(x["high"] for x in items),
                    "low": min(x["low"] for x in items),
                    "close": items[-1]["close"],
                    "volume": sum(x["volume"] for x in items),
                    "trade_count": None,
                    "sample_count": len(items),
                    "partial": partial,
                    "provisional": True,
                    "source_type": "TSETMC GetTradeIntraDay OHLCV aggregate",
                }

            candles_1h = []
            for hour in range(12, 18):
                subset = [x for x in aggregate_rows if x["hEven"] // 10000 == hour]
                candle = aggregate_candle(subset, hour, hour + 1)
                if candle:
                    candles_1h.append(candle)
            candles_4h = []
            for start_hour, end_hour in ((12, 16), (16, 18)):
                subset = [x for x in aggregate_rows if start_hour <= x["hEven"] // 10000 < end_hour]
                candle = aggregate_candle(subset, start_hour, end_hour)
                if candle:
                    candles_4h.append(candle)
            result.update({
                "status": "ok",
                "source": aggregate_url,
                "source_type": "TSETMC intraday OHLCV aggregate",
                "reconciliation_basis": "aggregate_volume_and_time_coverage",
                "trade_count": None,
                "quote_total_volume": quote_total_volume,
                "aggregate_volume": aggregate_volume,
                "volume_gap_percent": round(volume_gap_pct, 4),
                "volume_reconciled": True,
                "count_reconciled": None,
                "count_reconciliation_status": "not_applicable_aggregate_source",
                "volume_reconciliation_status": "reconciled",
                "first_bar_time": aggregate_rows[0]["hEven"],
                "last_bar_time": aggregate_rows[-1]["hEven"],
                "last_bar_age_seconds": last_age,
                "max_bar_gap_seconds": max_gap,
                "candles_1h": candles_1h,
                "candles_4h": candles_4h,
                "quality_note_fa": "کندل‌ها از OHLCV تجمیعی رسمی TSETMC ساخته شده‌اند؛ حجم کل با تابلو و پوشش زمانی با ساعت بازار تطبیق دارد، اما تعداد ریزمعاملات در این endpoint ارائه نمی‌شود.",
            })
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            temp = OUTPUT.with_suffix(".tmp")
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(OUTPUT)
            print(json.dumps(result, ensure_ascii=False))
            return
        except Exception as aggregate_error:
            endpoint_errors.append({
                "url": "Trade/GetTradeIntraDay",
                "error": str(aggregate_error)[:250],
            })

        result["reason"] = "همه مسیرهای رسمی دریافت معاملات ناموفق بودند"
        result["endpoint_errors"] = endpoint_errors
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temp = OUTPUT.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(OUTPUT)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit("LIVE_TRADE_DATA_NOT_VALID: " + result["reason"])
    result["source"] = source_url
    result["endpoint_errors"] = endpoint_errors

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
    # Re-fetch the official quote AFTER the trade list, so the reconciliation
    # compares two near-simultaneous snapshots rather than an older workflow file.
    quote_trade_count = None
    quote_total_volume = None
    quote_retrieved_at = None
    quote_age_seconds = None
    try:
        quote_payload = get_json(f"{BASES[0]}/ClosingPrice/GetClosingPriceInfo/{INS_CODE}", session=DIRECT_SESSION)
        quote_info = quote_payload.get("closingPriceInfo") if isinstance(quote_payload, dict) else None
        if isinstance(quote_info, dict) and str(quote_info.get("dEven") or quote_info.get("finalLastDate")) == date:
            quote_trade_count = int(quote_info.get("zTotTran") or 0) or None
            quote_total_volume = int(quote_info.get("qTotTran5J") or 0) or None
            quote_retrieved_at = datetime.now(TEHRAN).isoformat()
            quote_age_seconds = 0.0
    except Exception as error:
        endpoint_errors.append({"url": "ClosingPrice/GetClosingPriceInfo", "error": str(error)[:180]})

    # A stored snapshot is a safe fallback only when it is genuinely recent.
    if quote_trade_count is None:
        live_quote_file = Path("data/ayar_live.json")
        if live_quote_file.exists():
            try:
                snapshot = json.loads(live_quote_file.read_text(encoding="utf-8"))
                stamp = datetime.fromisoformat(snapshot.get("retrieved_at", ""))
                age = abs((datetime.now(TEHRAN) - stamp).total_seconds())
                if age <= 5 and str(snapshot.get("tsetmc_quote_date")) == date:
                    quote_trade_count = int(snapshot.get("tsetmc_quote_trade_count") or 0) or None
                    quote_total_volume = int(snapshot.get("tsetmc_quote_total_volume") or 0) or None
                    quote_retrieved_at = snapshot.get("retrieved_at")
                    quote_age_seconds = age
            except (OSError, ValueError, TypeError):
                pass

    # If the first non-empty endpoint returned a truncated list, test the
    # remaining official variants instead of stopping at the first HTTP 200.
    # A candidate is accepted only when both cumulative count and volume match.
    if quote_trade_count is not None and quote_total_volume is not None:
        for candidate_url, candidate_keys in endpoints:
            if candidate_url == source_url:
                continue
            try:
                candidate_payload = get_json(candidate_url)
                candidate_raw = next((candidate_payload.get(key) for key in candidate_keys
                                      if isinstance(candidate_payload.get(key), list)), None)
                if not candidate_raw:
                    continue
                candidate_unique = {}
                for item in candidate_raw:
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
                    hour, minute, second = hhmmss // 10000, (hhmmss // 100) % 100, hhmmss % 100
                    if hour > now.hour or (hour == now.hour and (minute, second) > (now.minute, now.second)):
                        continue
                    candidate_unique[number] = {
                        "nTran": number, "hEven": hhmmss, "price": price, "volume": volume
                    }
                candidate_rows = sorted(candidate_unique.values(), key=lambda x: (x["hEven"], x["nTran"]))
                if (len(candidate_rows) == quote_trade_count
                        and sum(x["volume"] for x in candidate_rows) == quote_total_volume):
                    raw, rows, source_url = candidate_raw, candidate_rows, candidate_url
                    result["source_url"] = source_url
                    result["fallback_selected_after_reconciliation"] = True
                    break
            except Exception as error:
                endpoint_errors.append({"url": candidate_url, "error": str(error)[:180]})

    observed_delta = quote_trade_count - len(rows) if quote_trade_count is not None else None
    observed_volume_delta = quote_total_volume - sum(x["volume"] for x in rows) if quote_total_volume is not None else None
    comparable = quote_age_seconds is not None and quote_age_seconds <= 5
    count_reconciled = (observed_delta == 0) if comparable and observed_delta is not None else None
    volume_reconciled = (observed_volume_delta == 0) if comparable and observed_volume_delta is not None else None
    reconciliation_status = (
        "reconciled" if count_reconciled is True else
        "mismatch" if count_reconciled is False else
        "not_comparable_time_gap"
    )
    result.update({
        "source_url": source_url,
        "quote_trade_count": quote_trade_count,
        "quote_total_volume": quote_total_volume,
        "quote_retrieved_at": quote_retrieved_at,
        "quote_age_seconds": round(quote_age_seconds, 2) if quote_age_seconds is not None else None,
        "observed_trade_count_delta": observed_delta,
        "observed_volume_delta": observed_volume_delta,
        "count_reconciled": count_reconciled,
        "volume_reconciled": volume_reconciled,
        "count_reconciliation_status": reconciliation_status,
        "volume_reconciliation_status": (
            "reconciled" if volume_reconciled is True else
            "mismatch" if volume_reconciled is False else
            "not_comparable_time_gap"
        ),
        "quality_note_fa": (
            "تعداد و حجم معاملات با snapshot رسمی هم‌زمان تطبیق کامل دارد."
            if count_reconciled is True and volume_reconciled is True else
            "فهرست معاملات با snapshot رسمی تابلو تطبیق کامل ندارد؛ کندل معتبر منتشر نمی‌شود."
        ),
    })
    if not rows:
        result["reason"] = "هیچ معامله فعال و قابل استفاده‌ای دریافت نشد"
    elif not comparable or count_reconciled is not True or volume_reconciled is not True:
        result["status"] = "incomplete"
        result["reason"] = "تعداد یا حجم معاملات با snapshot رسمی تابلو تطبیق کامل ندارد؛ کندل معتبر منتشر نشد."
        result["trade_count"] = len(rows)
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
    # A green Actions run must mean usable, reconciled trade data was produced.
    # Persist the explicit failure snapshot first, then fail the job so later
    # indicator steps cannot silently reuse stale candles.
    if market.get("status") == "open" and result.get("status") != "ok":
        raise SystemExit("LIVE_TRADE_DATA_NOT_VALID: " + str(result.get("reason", "unknown")))


if __name__ == "__main__":
    main()
