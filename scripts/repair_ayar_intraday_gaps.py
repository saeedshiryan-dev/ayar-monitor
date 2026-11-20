#!/usr/bin/env python3
import csv
import json
import shutil
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import build_ayar_intraday_candles as builder

SEED_DIR = Path("build/seed")
OUT = Path("build/intraday")
PUBLISHED = Path("data/intraday")


def make_session():
    retry = Retry(
        total=3, connect=3, read=3, status=3, backoff_factor=1.0,
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
    return session


def repair_one(day):
    builder.THREAD_LOCAL.session = make_session()
    try:
        return builder.fetch_and_validate(day)
    finally:
        try:
            del builder.THREAD_LOCAL.session
        except AttributeError:
            pass


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def validate_candles(hourly, four_hour, valid_dates, daily_map):
    result = {"days_checked": len(valid_dates), "hourly_errors": [], "four_hour_errors": [], "ohlc_errors": [], "auction_warnings": []}
    by_timeframe = {"1h": defaultdict(list), "4h": defaultdict(list)}
    for row in hourly:
        by_timeframe["1h"][row["date"]].append(row)
    for row in four_hour:
        by_timeframe["4h"][row["date"]].append(row)

    for date in sorted(valid_dates):
        daily = daily_map[date]
        for label, rows_by_date in by_timeframe.items():
            rows = rows_by_date.get(date, [])
            if not rows:
                target = result["hourly_errors"] if label == "1h" else result["four_hour_errors"]
                target.append({"date": date, "error": "no candles"})
                continue
            volume = sum(int(x["volume"]) for x in rows)
            count = sum(int(x["trade_count"]) for x in rows)
            target = result["hourly_errors"] if label == "1h" else result["four_hour_errors"]
            if volume != int(daily["volume"]) or count != int(daily["trade_count"]):
                target.append({
                    "date": date, "volume": volume, "daily_volume": int(daily["volume"]),
                    "trade_count": count, "daily_trade_count": int(daily["trade_count"]),
                })
            high = max(int(x["high"]) for x in rows)
            low = min(int(x["low"]) for x in rows)
            if high != int(daily["high"]) or low != int(daily["low"]):
                result["ohlc_errors"].append({
                    "date": date, "timeframe": label, "high": high, "daily_high": int(daily["high"]),
                    "low": low, "daily_low": int(daily["low"]),
                })
            if rows[0]["open"] != str(daily["open"]) and int(rows[0]["open"]) != int(daily["open"]):
                result["auction_warnings"].append({"date": date, "timeframe": label, "field": "open"})
            if int(rows[-1]["close"]) != int(daily["last"]):
                result["auction_warnings"].append({"date": date, "timeframe": label, "field": "last"})
    result["passed"] = not (result["hourly_errors"] or result["four_hour_errors"] or result["ohlc_errors"])
    return result


def main():
    seed_report_path = SEED_DIR / "validation_report.json"
    seed_1h_path = SEED_DIR / "ayar_1h.csv"
    seed_4h_path = SEED_DIR / "ayar_4h.csv"
    for path in (seed_report_path, seed_1h_path, seed_4h_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing seed artifact file: {path}")

    daily_source = json.loads(Path("data/ayar_daily_history.json").read_text(encoding="utf-8"))
    days = daily_source["valid_history"]
    daily_map = {str(x["date"]): x for x in days}
    seed_report = json.loads(seed_report_path.read_text(encoding="utf-8"))
    hourly = read_csv(seed_1h_path)
    four_hour = read_csv(seed_4h_path)

    valid_reports = {str(x["date"]): x for x in seed_report["daily_validation"]}
    seed_failures = {str(x["date"]): x for x in seed_report["failures"]}
    pending = [daily_map[d] for d in sorted(seed_failures) if d in daily_map]
    repaired_reports = []
    new_hourly, new_four_hour, new_failures = [], [], []
    started = time.time()

    print(json.dumps({"seed_validated": len(valid_reports), "seed_failed": len(pending), "expected": len(days)}), flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(repair_one, day): day for day in pending}
        for idx, future in enumerate(as_completed(futures), 1):
            day = futures[future]
            date = str(day["date"])
            try:
                _, trades, report = future.result()
                repaired_reports.append(report)
                new_hourly.extend(builder.aggregate(trades, 1))
                new_four_hour.extend(builder.aggregate(trades, 4))
                print(json.dumps({"date": date, "repair": "passed", "transactions": report["active_unique_trades"]}), flush=True)
            except Exception as exc:
                new_failures.append({"date": date, "previous_error": seed_failures[date].get("error"), "repair_error": str(exc)})
                print(json.dumps({"date": date, "repair": "failed", "error": str(exc)[:500]}), flush=True)

    hourly.extend(new_hourly)
    four_hour.extend(new_four_hour)
    hourly.sort(key=lambda x: (x["date"], x["time"]))
    four_hour.sort(key=lambda x: (x["date"], x["time"]))

    valid_reports.update({str(x["date"]): x for x in repaired_reports})
    failures = new_failures
    validation = validate_candles(hourly, four_hour, valid_reports.keys(), daily_map)

    OUT.mkdir(parents=True, exist_ok=True)
    builder.write_csv(OUT / "ayar_1h.csv", hourly)
    builder.write_csv(OUT / "ayar_4h.csv", four_hour)

    all_reports = [valid_reports[d] for d in sorted(valid_reports)]
    summary = {
        "symbol": "عیار",
        "source": "TSETMC raw individual trades; seed run 44623122817 plus targeted gap repair",
        "timezone": "Asia/Tehran",
        "hour_bucket_anchor": "local clock hour",
        "four_hour_bucket_anchor": "local clock 00:00",
        "history_first_date": days[0]["date"],
        "history_last_date": days[-1]["date"],
        "expected_trading_days": len(days),
        "seed_validated_days": int(seed_report["validated_days"]),
        "repaired_days": len(repaired_reports),
        "validated_days": len(valid_reports),
        "failed_days": len(failures),
        "one_hour_candles": len(hourly),
        "four_hour_candles": len(four_hour),
        "total_transactions": sum(int(x["active_unique_trades"]) for x in all_reports),
        "total_volume": sum(int(x["volume"]) for x in all_reports),
        "candle_validation": validation,
        "daily_validation": all_reports,
        "failures": failures,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    (OUT / "validation_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    PUBLISHED.mkdir(parents=True, exist_ok=True)
    for name in ("ayar_1h.csv", "ayar_4h.csv", "validation_report.json"):
        shutil.copy2(OUT / name, PUBLISHED / name)

    print(json.dumps({
        "expected_trading_days": summary["expected_trading_days"],
        "seed_validated_days": summary["seed_validated_days"],
        "repaired_days": summary["repaired_days"],
        "validated_days": summary["validated_days"],
        "failed_days": summary["failed_days"],
        "one_hour_candles": summary["one_hour_candles"],
        "four_hour_candles": summary["four_hour_candles"],
        "candle_validation_passed": validation["passed"],
        "elapsed_seconds": summary["elapsed_seconds"],
    }, ensure_ascii=False), flush=True)

    if failures or len(valid_reports) != len(days) or not validation["passed"]:
        raise SystemExit("INCOMPLETE: some trading dates remain missing or candle aggregation checks failed")
    print("SUCCESS: all 1h and 4h candles validated against daily TSETMC data.", flush=True)


if __name__ == "__main__":
    main()
