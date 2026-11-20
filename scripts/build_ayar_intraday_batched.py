import argparse
import csv
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, local

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INS_CODE = "34144395039913458"
HOSTS = ("https://cdn.tsetmc.com/api", "https://cdn10.tsetmc.com/api")
HISTORY_FILE = Path("data/ayar_daily_history.json")
OUT = Path("build/intraday_batched")
THREAD_LOCAL = local()
REQUEST_LOCK = Lock()
NEXT_REQUEST_AT = 0.0


def get_session():
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        retry = Retry(
            total=2, connect=2, read=2, status=2, backoff_factor=0.8,
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


def paced_get(url):
    global NEXT_REQUEST_AT
    with REQUEST_LOCK:
        wait = max(0.0, NEXT_REQUEST_AT - time.monotonic())
        if wait:
            time.sleep(wait)
        NEXT_REQUEST_AT = time.monotonic() + 0.8
    return get_session().get(url, timeout=(12, 35))


def parse_h_even(value):
    value = int(value)
    hour, rem = divmod(value, 10000)
    minute, second = divmod(rem, 100)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"Invalid TSETMC hEven value: {value}")
    return hour, minute, second


def fetch_and_validate(daily):
    date = str(daily["date"])
    attempts = []
    best = None
    for base in HOSTS:
        url = f"{base}/Trade/GetTradeHistory/{INS_CODE}/{date}/false"
        try:
            response = paced_get(url)
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
            trades = []
            for row in by_number.values():
                h, m, s = parse_h_even(row["hEven"])
                trades.append({
                    "date": date, "hour": h, "minute": m, "second": s,
                    "nTran": int(row["nTran"]), "price": int(float(row["pTran"])),
                    "volume": int(row["qTitTran"]),
                })
            trades.sort(key=lambda x: (x["hour"], x["minute"], x["second"], x["nTran"]))
            if not trades:
                attempts.append({"url": url, "raw_rows": len(raw), "active_rows": 0})
                continue
            prices = [x["price"] for x in trades]
            volume = sum(x["volume"] for x in trades)
            checks = {
                "trade_count": len(trades) == int(daily["trade_count"]),
                "volume": volume == int(daily["volume"]),
                "low": min(prices) == int(daily["low"]),
                "high": max(prices) == int(daily["high"]),
                "open": prices[0] == int(daily["open"]),
                "last": prices[-1] == int(daily.get("last", daily["close"])),
            }
            hard = all(checks[k] for k in ("trade_count", "volume", "low", "high"))
            score = sum(checks.values())
            attempts.append({"url": url, "raw_rows": len(raw), "active_rows": len(trades), "checks": checks})
            candidate = (score, trades, checks, len(raw), volume, hard, url)
            if best is None or score > best[0]:
                best = candidate
            if hard:
                break
        except Exception as exc:
            attempts.append({"url": url, "error": str(exc)})
    if best is None:
        raise ValueError("No usable active trades: " + json.dumps(attempts, ensure_ascii=False))
    score, trades, checks, raw_rows, volume, hard, url = best
    if not hard:
        raise ValueError("Daily reconciliation failed: " + json.dumps({
            "checks": checks, "active_count": len(trades),
            "daily_count": int(daily["trade_count"]), "active_volume": volume,
            "daily_volume": int(daily["volume"]), "attempts": attempts,
        }, ensure_ascii=False))
    report = {
        "date": date, "source_url": url, "raw_rows": raw_rows,
        "active_unique_trades": len(trades), "volume": volume,
        "checks": checks, "passed": True,
        "warnings": [k for k in ("open", "last") if not checks[k]],
        "attempts": attempts,
    }
    return date, trades, report


def aggregate(trades, hours):
    groups = defaultdict(list)
    for trade in trades:
        groups[(trade["hour"] // hours) * hours].append(trade)
    bars = []
    for bucket, rows in sorted(groups.items()):
        prices = [x["price"] for x in rows]
        bars.append({
            "date": rows[0]["date"], "time": f"{bucket:02d}:00:00",
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


def save_checkpoint(days, reports, hourly, four_hour, failures, started):
    reports.sort(key=lambda x: x["date"])
    hourly.sort(key=lambda x: (x["date"], x["time"]))
    four_hour.sort(key=lambda x: (x["date"], x["time"]))
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "ayar_1h.csv", hourly)
    write_csv(OUT / "ayar_4h.csv", four_hour)
    summary = {
        "symbol": "عیار", "source": "TSETMC real trades",
        "timezone": "Asia/Tehran", "hour_bucket_anchor": "local clock hour",
        "four_hour_bucket_anchor": "local clock 00:00",
        "history_first_date": days[0]["date"], "history_last_date": days[-1]["date"],
        "expected_trading_days": len(days), "validated_days": len(reports),
        "failed_days": len(failures), "one_hour_candles": len(hourly),
        "four_hour_candles": len(four_hour),
        "total_transactions": sum(x["active_unique_trades"] for x in reports),
        "total_volume": sum(x["volume"] for x in reports),
        "daily_validation": reports, "failures": failures,
        "warning_days": sum(bool(x.get("warnings")) for x in reports),
        "elapsed_seconds": round(time.time() - started, 1),
        "checkpoint": True,
    }
    (OUT / "validation_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.batch_size < 1 or args.workers < 1 or args.workers > 3:
        raise SystemExit("batch-size must be positive; workers must be 1..3")
    source = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    days = source.get("valid_history", [])
    if not days:
        raise RuntimeError("Validated daily history list is empty")
    OUT.mkdir(parents=True, exist_ok=True)
    reports, hourly, four_hour, failures = [], [], [], []
    started = time.time()
    total = len(days)
    for batch_start in range(0, total, args.batch_size):
        batch = days[batch_start:batch_start + args.batch_size]
        print(json.dumps({
            "event": "batch_started", "from": batch_start + 1,
            "to": batch_start + len(batch), "total_days": total,
        }), flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fetch_and_validate, day): day for day in batch}
            for future in as_completed(futures):
                day = futures[future]
                try:
                    date, trades, report = future.result()
                    reports.append(report)
                    hourly.extend(aggregate(trades, 1))
                    four_hour.extend(aggregate(trades, 4))
                except Exception as exc:
                    failures.append({"date": str(day.get("date")), "error": str(exc)})
                completed = len(reports) + len(failures)
                print(json.dumps({
                    "event": "day_completed", "completed": completed,
                    "total": total, "passed": len(reports), "failed": len(failures),
                    "date": str(day.get("date")), "elapsed_seconds": round(time.time() - started, 1),
                }), flush=True)
        save_checkpoint(days, reports, hourly, four_hour, failures, started)
        print(json.dumps({
            "event": "checkpoint_saved", "completed": min(batch_start + len(batch), total),
            "total": total, "passed": len(reports), "failed": len(failures),
            "hourly_candles": len(hourly), "four_hour_candles": len(four_hour),
        }), flush=True)
    save_checkpoint(days, reports, hourly, four_hour, failures, started)
    if failures or len(reports) != total:
        raise SystemExit(f"INCOMPLETE: {len(failures)} dates failed; checkpoint files retained as workflow artifacts")
    print("SUCCESS: all dates passed hard daily reconciliation checks.", flush=True)


if __name__ == "__main__":
    main()
