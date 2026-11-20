import csv
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import local

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INS_CODE = "34144395039913458"
BASE = "https://cdn.tsetmc.com/api"
HISTORY_FILE = Path("data/ayar_daily_history.json")
OUT = Path("build/intraday")
THREAD_LOCAL = local()


def get_session():
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        retry = Retry(
            total=7, connect=7, read=7, status=7, backoff_factor=2.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]), raise_on_status=False,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.tsetmc.com/",
        })
        THREAD_LOCAL.session = session
    return session


def parse_h_even(value):
    value = int(value)
    hour, rem = divmod(value, 10000)
    minute, second = divmod(rem, 100)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"Invalid TSETMC hEven value: {value}")
    return hour, minute, second


def fetch_and_validate(daily):
    date = str(daily["date"])
    hosts = ("https://cdn.tsetmc.com/api",)
    attempts = []
    best = None

    for base in hosts:
        for include_canceled in ("false", "true"):
            url = f"{base}/Trade/GetTradeHistory/{INS_CODE}/{date}/{include_canceled}"
            try:
                response = get_session().get(url, timeout=(20, 120))
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("tradeHistory"), list):
                    raise ValueError("Unexpected API response structure")
                raw = payload["tradeHistory"]
                by_number = {}
                for row in raw:
                    if int(row.get("canceled", 0) or 0) != 0:
                        continue
                    number = str(row.get("nTran"))
                    if number in ("None", ""):
                        raise ValueError("Trade row missing nTran")
                    signature = (int(row["hEven"]), int(float(row["pTran"])), int(row["qTitTran"]))
                    old = by_number.get(number)
                    if old is not None:
                        old_sig = (int(old["hEven"]), int(float(old["pTran"])), int(old["qTitTran"]))
                        if signature != old_sig:
                            raise ValueError(f"Conflicting duplicate nTran={number}")
                    else:
                        by_number[number] = row

                normalized = []
                for row in by_number.values():
                    h, m, s = parse_h_even(row["hEven"])
                    normalized.append({
                        "date": date, "hour": h, "minute": m, "second": s,
                        "nTran": int(row["nTran"]), "price": int(float(row["pTran"])),
                        "volume": int(row["qTitTran"]),
                    })
                normalized.sort(key=lambda x: (x["hour"], x["minute"], x["second"], x["nTran"]))
                if not normalized:
                    attempts.append({"url": url, "raw_rows": len(raw), "active_rows": 0})
                    continue

                prices = [x["price"] for x in normalized]
                volume = sum(x["volume"] for x in normalized)
                checks = {
                    "trade_count": len(normalized) == int(daily["trade_count"]),
                    "volume": volume == int(daily["volume"]),
                    "low": min(prices) == int(daily["low"]),
                    "high": max(prices) == int(daily["high"]),
                    "open": prices[0] == int(daily["open"]),
                    "last": prices[-1] == int(daily.get("last", daily["close"])),
                }
                hard_checks = all(checks[k] for k in ("trade_count", "volume", "low", "high"))
                score = sum(checks[k] for k in ("trade_count", "volume", "low", "high", "open", "last"))
                candidate = {
                    "url": url, "response": response, "raw_rows": len(raw),
                    "trades": normalized, "checks": checks, "score": score,
                    "hard_checks": hard_checks, "volume": volume, "prices": prices,
                }
                attempts.append({
                    "url": url, "raw_rows": len(raw), "active_rows": len(normalized),
                    "checks": checks, "score": score,
                })
                if best is None or candidate["score"] > best["score"]:
                    best = candidate
                # Exact daily trade count, volume and range means no trade data is missing.
                if hard_checks:
                    break
            except Exception as exc:
                attempts.append({"url": url, "error": str(exc)})

        if best is not None and best["hard_checks"]:
            break

    if best is None:
        raise ValueError("No usable active trades from either TSETMC host/flag; attempts=" +
                         json.dumps(attempts, ensure_ascii=False))

    if not best["hard_checks"]:
        prices = best["prices"]
        raise ValueError("Daily trade data remains incomplete after endpoint fallbacks: " +
            json.dumps({
                "best_checks": best["checks"], "attempts": attempts,
                "intraday_count": len(best["trades"]), "daily_count": int(daily["trade_count"]),
                "intraday_volume": best["volume"], "daily_volume": int(daily["volume"]),
                "intraday_low": min(prices), "daily_low": int(daily["low"]),
                "intraday_high": max(prices), "daily_high": int(daily["high"]),
            }, ensure_ascii=False))

    report = {
        "date": date, "source_url": best["url"],
        "raw_rows": best["raw_rows"], "active_unique_trades": len(best["trades"]),
        "volume": best["volume"], "checks": best["checks"],
        "passed": True,
        "warnings": [k for k in ("open", "last") if not best["checks"][k]],
        "attempts": attempts,
    }
    return date, best["trades"], report

def aggregate(trades, hours):
    groups = defaultdict(list)
    for t in trades:
        bucket = (t["hour"] // hours) * hours
        groups[bucket].append(t)
    bars = []
    for bucket in sorted(groups):
        rows = groups[bucket]
        prices = [x["price"] for x in rows]
        bars.append({
            "date": rows[0]["date"],
            "time": f"{bucket:02d}:00:00",
            "open": prices[0], "high": max(prices), "low": min(prices),
            "close": prices[-1], "volume": sum(x["volume"] for x in rows),
            "trade_count": len(rows),
        })
    return bars


def write_csv(path, rows):
    fields = ["date", "time", "open", "high", "low", "close", "volume", "trade_count"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    source = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    days = source.get("valid_history", [])
    if not days:
        raise RuntimeError("Validated daily history list is empty")
    OUT.mkdir(parents=True, exist_ok=True)
    reports, hourly_all, four_hour_all, failures = [], [], [], []
    started = time.time()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_and_validate, day): day for day in days}
        for index, future in enumerate(as_completed(futures), 1):
            day = futures[future]
            try:
                date, trades, report = future.result()
                reports.append(report)
                hourly_all.extend(aggregate(trades, 1))
                four_hour_all.extend(aggregate(trades, 4))
            except Exception as exc:
                failures.append({"date": str(day.get("date")), "error": str(exc)})
            if index % 50 == 0 or index == len(days):
                print(json.dumps({
                    "completed": index, "total": len(days), "passed": len(reports),
                    "failed": len(failures), "elapsed_seconds": round(time.time() - started, 1),
                }))
    reports.sort(key=lambda x: x["date"])
    hourly_all.sort(key=lambda x: (x["date"], x["time"]))
    four_hour_all.sort(key=lambda x: (x["date"], x["time"]))
    write_csv(OUT / "ayar_1h.csv", hourly_all)
    write_csv(OUT / "ayar_4h.csv", four_hour_all)
    summary = {
        "symbol": "عیار", "source": "TSETMC real trades",
        "timezone": "Asia/Tehran", "hour_bucket_anchor": "local clock hour",
        "four_hour_bucket_anchor": "local clock 00:00",
        "history_first_date": days[0]["date"], "history_last_date": days[-1]["date"],
        "expected_trading_days": len(days), "validated_days": len(reports),
        "failed_days": len(failures), "one_hour_candles": len(hourly_all),
        "four_hour_candles": len(four_hour_all),
        "total_transactions": sum(x["active_unique_trades"] for x in reports),
        "total_volume": sum(x["volume"] for x in reports),
        "daily_validation": reports, "failures": failures,
        "warning_days": sum(bool(x.get("warnings")) for x in reports),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    (OUT / "validation_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        k: summary[k] for k in (
            "expected_trading_days", "validated_days", "failed_days",
            "one_hour_candles", "four_hour_candles", "total_transactions",
            "total_volume", "elapsed_seconds"
        )
    }, ensure_ascii=False))
    if failures or len(reports) != len(days):
        raise SystemExit(f"INCOMPLETE: {len(failures)} daily downloads/reconciliations failed")
    print("SUCCESS: Full intraday history downloaded; all daily OHLCV/count checks passed.")


if __name__ == "__main__":
    main()
