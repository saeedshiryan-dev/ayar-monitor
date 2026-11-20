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
BASE_REPORT = Path("data/intraday/validation_report.json")
BASE_1H = Path("data/intraday/ayar_1h.csv")
BASE_4H = Path("data/intraday/ayar_4h.csv")
OUT = Path("build/intraday_incremental")
THREAD_LOCAL = local()
REQUEST_LOCK = Lock()
NEXT_REQUEST_AT = 0.0


def get_session():
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        retry = Retry(
            total=1, connect=1, read=1, status=1, backoff_factor=0.5,
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
        NEXT_REQUEST_AT = time.monotonic() + 0.7
    return get_session().get(url, timeout=(10, 20))


def parse_h_even(value):
    value = int(value)
    hour, rem = divmod(value, 10000)
    minute, second = divmod(rem, 100)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"Invalid TSETMC hEven value: {value}")
    return hour, minute, second


def fetch_day(daily):
    date = str(daily["date"])
    errors = []
    for base in HOSTS:
        url = f"{base}/Trade/GetTradeHistory/{INS_CODE}/{date}/false"
        try:
            response = paced_get(url)
            response.raise_for_status()
            payload = response.json()
            raw = payload.get("tradeHistory") if isinstance(payload, dict) else None
            if not isinstance(raw, list):
                raise ValueError("Unexpected API response structure")
            unique = {}
            for row in raw:
                if int(row.get("canceled", 0) or 0) != 0:
                    continue
                ntran = str(row.get("nTran"))
                if ntran in ("None", ""):
                    raise ValueError("Missing nTran")
                sig = (int(row["hEven"]), int(float(row["pTran"])), int(row["qTitTran"]))
                if ntran in unique:
                    old = unique[ntran]
                    if sig != (int(old["hEven"]), int(float(old["pTran"])), int(old["qTitTran"])):
                        raise ValueError(f"Conflicting duplicate transaction {ntran}")
                else:
                    unique[ntran] = row
            trades = []
            for row in unique.values():
                h, m, s = parse_h_even(row["hEven"])
                trades.append({
                    "date": date, "hour": h, "minute": m, "second": s,
                    "nTran": int(row["nTran"]), "price": int(float(row["pTran"])),
                    "volume": int(row["qTitTran"]),
                })
            trades.sort(key=lambda x: (x["hour"], x["minute"], x["second"], x["nTran"]))
            if not trades:
                raise ValueError(f"No active trades (raw_rows={len(raw)})")
            prices = [x["price"] for x in trades]
            checks = {
                "trade_count": len(trades) == int(daily["trade_count"]),
                "volume": sum(x["volume"] for x in trades) == int(daily["volume"]),
                "low": min(prices) == int(daily["low"]),
                "high": max(prices) == int(daily["high"]),
                "open": prices[0] == int(daily["open"]),
                "last": prices[-1] == int(daily.get("last", daily["close"])),
            }
            if not all(checks[k] for k in ("trade_count", "volume", "low", "high")):
                raise ValueError("Daily reconciliation failed: " + json.dumps(checks))
            return {
                "date": date, "trades": trades,
                "report": {
                    "date": date, "source_url": url, "raw_rows": len(raw),
                    "active_unique_trades": len(trades),
                    "volume": sum(x["volume"] for x in trades),
                    "checks": checks, "passed": True,
                    "warnings": [k for k in ("open", "last") if not checks[k]],
                },
            }
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})
    raise RuntimeError(json.dumps({"date": date, "attempts": errors}, ensure_ascii=False))


def aggregate(trades, hours):
    groups = defaultdict(list)
    for t in trades:
        groups[(t["hour"] // hours) * hours].append(t)
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


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Required validated baseline missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    fields = ["date", "time", "open", "high", "low", "close", "volume", "trade_count"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_checkpoint(days, reports, hourly, four_hour, failures, started, base_report):
    reports.sort(key=lambda x: x["date"])
    hourly.sort(key=lambda x: (x["date"], x["time"]))
    four_hour.sort(key=lambda x: (x["date"], x["time"]))
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "ayar_1h.csv", hourly)
    write_csv(OUT / "ayar_4h.csv", four_hour)
    summary = {
        "symbol": "عیار", "source": "TSETMC real trades; incremental merge with validated baseline",
        "timezone": "Asia/Tehran", "history_first_date": days[0]["date"],
        "history_last_date": days[-1]["date"], "expected_trading_days": len(days),
        "validated_days": len(reports), "failed_days": len(failures),
        "one_hour_candles": len(hourly), "four_hour_candles": len(four_hour),
        "total_transactions": sum(int(x["active_unique_trades"]) for x in reports),
        "total_volume": sum(int(x["volume"]) for x in reports),
        "daily_validation": reports, "failures": failures,
        "baseline_validated_days": int(base_report.get("validated_days", 0)),
        "newly_processed_days": len(reports) - int(base_report.get("validated_days", 0)),
        "elapsed_seconds": round(time.time() - started, 1),
        "checkpoint": True,
    }
    (OUT / "validation_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=2)
    args = p.parse_args()
    if args.workers not in (1, 2):
        raise SystemExit("workers must be 1 or 2")

    daily_source = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    days = sorted(daily_source.get("valid_history", []), key=lambda x: str(x["date"]))
    if not days:
        raise RuntimeError("Validated daily history is empty")
    base_report = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    reports = list(base_report.get("daily_validation", []))
    report_by_date = {str(x["date"]): x for x in reports if x.get("passed", True)}
    failures_by_date = {str(x["date"]): x for x in base_report.get("failures", [])}
    hourly = read_csv(BASE_1H)
    four_hour = read_csv(BASE_4H)

    # Keep the already validated baseline untouched; only fetch missing or previously failed days.
    todo = [d for d in days if str(d["date"]) not in report_by_date]
    failures = []
    started = time.time()
    print(json.dumps({
        "event": "incremental_plan", "daily_history_days": len(days),
        "already_validated_days": len(report_by_date), "pending_days": len(todo),
        "previous_failures": len(failures_by_date),
    }), flush=True)

    for index, daily in enumerate(todo, 1):
        date = str(daily["date"])
        try:
            result = fetch_day(daily)
            reports.append(result["report"])
            hourly.extend(aggregate(result["trades"], 1))
            four_hour.extend(aggregate(result["trades"], 4))
            failures_by_date.pop(date, None)
            print(json.dumps({
                "event": "date_passed", "date": date, "pending_completed": index,
                "pending_total": len(todo), "validated": len(reports),
                "elapsed_seconds": round(time.time() - started, 1),
            }), flush=True)
        except Exception as exc:
            failure = {"date": date, "error": str(exc)}
            failures_by_date[date] = failure
            print(json.dumps({
                "event": "date_failed", "date": date, "pending_completed": index,
                "pending_total": len(todo), "error": str(exc),
            }), flush=True)
        failures = list(failures_by_date.values())
        save_checkpoint(days, reports, hourly, four_hour, failures, started, base_report)

    failures = list(failures_by_date.values())
    save_checkpoint(days, reports, hourly, four_hour, failures, started, base_report)
    print(json.dumps({
        "event": "finished", "validated": len(reports), "failed": len(failures),
        "hourly_candles": len(hourly), "four_hour_candles": len(four_hour),
    }), flush=True)
    if failures:
        raise SystemExit(f"INCOMPLETE: {len(failures)} dates remain unvalidated; baseline remains unchanged")


if __name__ == "__main__":
    main()
