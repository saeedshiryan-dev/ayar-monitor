#!/usr/bin/env python3
"""Independent structural and cross-timeframe validation for Ayar trade-derived candles."""
import csv
import json
from collections import defaultdict
from pathlib import Path

DATA = Path("data")
INTRADAY = DATA / "intraday"
HISTORY = DATA / "ayar_daily_history.json"
REPORT = INTRADAY / "validation_report.json"
H1_FILE = INTRADAY / "ayar_1h.csv"
H4_FILE = INTRADAY / "ayar_4h.csv"
QUARANTINE = INTRADAY / "quarantined_4h.csv"
FIELDS = ["date", "time", "open", "high", "low", "close", "volume", "trade_count"]


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def as_int(row, key):
    value = int(row[key])
    return value


def structural_errors(rows, timeframe):
    errors = []
    seen = set()
    for row in rows:
        key = (row.get("date", ""), row.get("time", ""))
        if key in seen:
            errors.append({"key": key, "error": "duplicate_candle"})
        seen.add(key)
        try:
            o, h, l, c = (as_int(row, k) for k in ("open", "high", "low", "close"))
            volume, trades = as_int(row, "volume"), as_int(row, "trade_count")
            hour, minute = map(int, row["time"][:5].split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("invalid_time")
            if timeframe == "1h" and minute != 0:
                raise ValueError("1h_not_hour_aligned")
            if timeframe == "4h" and (minute != 0 or hour % 4 != 0):
                raise ValueError("4h_not_aligned_to_four_hour_bucket")
            if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c) or l > h:
                raise ValueError("invalid_ohlc_relationship")
            if volume < 0 or trades <= 0:
                raise ValueError("invalid_volume_or_trade_count")
        except Exception as exc:
            errors.append({"key": key, "error": str(exc)})
    return errors


def aggregate(rows):
    rows = sorted(rows, key=lambda x: x["time"])
    return {
        "open": as_int(rows[0], "open"),
        "high": max(as_int(x, "high") for x in rows),
        "low": min(as_int(x, "low") for x in rows),
        "close": as_int(rows[-1], "close"),
        "volume": sum(as_int(x, "volume") for x in rows),
        "trade_count": sum(as_int(x, "trade_count") for x in rows),
    }


def main():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    history = json.loads(HISTORY.read_text(encoding="utf-8"))
    if history.get("validated") is not True:
        raise ValueError("Daily history is not marked validated")

    daily_rows = history.get("valid_history", [])
    daily_by_date = {str(x["date"]): x for x in daily_rows}
    expected_dates = set(daily_by_date)
    passed_reports = {
        str(x["date"]): x for x in report.get("daily_validation", [])
        if x.get("passed") is True
    }
    passed_dates = set(passed_reports)

    h1 = read_csv(H1_FILE)
    h4_original = read_csv(H4_FILE)
    errors = {
        "h1_structure": structural_errors(h1, "1h"),
        "h1_daily_reconciliation": [],
        "h4_structure": structural_errors(h4_original, "4h"),
        "h4_missing_for_h1_bucket": [],
        "h4_mismatch_vs_h1": [],
    }

    h1_by_date = defaultdict(list)
    h1_by_bucket = defaultdict(list)
    for row in h1:
        h1_by_date[row["date"]].append(row)
        hour = int(row["time"][:2])
        bucket = f"{(hour // 4) * 4:02d}:00:00"
        h1_by_bucket[(row["date"], bucket)].append(row)

    # Every transaction-validated day must reconcile from the 1h bars back to
    # the official daily history: count, volume, open, high, low and final trade.
    for date in sorted(passed_dates):
        day_bars = h1_by_date.get(date, [])
        daily = daily_by_date.get(date)
        daily_report = passed_reports[date]
        if not day_bars or not daily:
            errors["h1_daily_reconciliation"].append({
                "date": date, "error": "missing_h1_or_daily_history"
            })
            continue
        a = aggregate(day_bars)
        expected = {
            "trade_count": int(daily_report["active_unique_trades"]),
            "volume": int(daily_report["volume"]),
            "open": int(daily["open"]),
            "high": int(daily["high"]),
            "low": int(daily["low"]),
            "last": int(daily.get("last", daily["close"])),
        }
        actual = {
            "trade_count": a["trade_count"], "volume": a["volume"],
            "open": a["open"], "high": a["high"], "low": a["low"],
            "last": a["close"],
        }
        mismatches = {k: {"expected": expected[k], "actual": actual[k]}
                      for k in expected if expected[k] != actual[k]}
        if mismatches:
            errors["h1_daily_reconciliation"].append({
                "date": date, "mismatches": mismatches
            })

    unexpected_h1_dates = sorted(set(h1_by_date) - passed_dates)
    for date in unexpected_h1_dates:
        errors["h1_daily_reconciliation"].append({
            "date": date, "error": "h1_day_has_no_passed_transaction_reconciliation"
        })

    h4_by_key = {(x["date"], x["time"]): x for x in h4_original}
    h1_bucket_keys = set(h1_by_bucket)
    h4_keys = set(h4_by_key)

    for key in sorted(h1_bucket_keys - h4_keys):
        errors["h4_missing_for_h1_bucket"].append({
            "date": key[0], "time": key[1], "error": "missing_4h_aggregate"
        })

    # An H4 bar without any constituent H1 bars cannot be independently
    # corroborated. Preserve it in quarantine, but remove it from indicators.
    orphan_keys = sorted(h4_keys - h1_bucket_keys)
    old_quarantine = read_csv(QUARANTINE) if QUARANTINE.exists() else []
    quarantined = {(x["date"], x["time"]): x for x in old_quarantine}
    for key in orphan_keys:
        quarantined[key] = h4_by_key[key]
    if quarantined:
        write_csv(QUARANTINE, [quarantined[k] for k in sorted(quarantined)])

    h4 = [x for x in h4_original if (x["date"], x["time"]) not in set(orphan_keys)]
    write_csv(H4_FILE, h4)

    for key in sorted(h1_bucket_keys & set((x["date"], x["time"]) for x in h4)):
        a = aggregate(h1_by_bucket[key])
        b = {k: as_int(h4_by_key[key], k)
             for k in ("open", "high", "low", "close", "volume", "trade_count")}
        mismatch = {k: {"h1_aggregate": a[k], "h4_candle": b[k]}
                    for k in a if a[k] != b[k]}
        if mismatch:
            errors["h4_mismatch_vs_h1"].append({
                "date": key[0], "time": key[1], "mismatches": mismatch
            })

    h1_dates = set(h1_by_date)
    h4_complete_dates = {
        x["date"] for x in h4
        if int(x["time"][:2]) + 4 <= 16
    }
    missing_h1 = sorted(expected_dates - h1_dates)
    missing_h4 = sorted(expected_dates - h4_complete_dates)
    all_errors = sum(len(v) for v in errors.values())
    passed = all_errors == 0

    report["candle_validation"] = {
        "passed": passed,
        "status": ("passed" if passed and not (missing_h1 or missing_h4)
                   else "passed_with_coverage_gaps" if passed
                   else "failed"),
        "method": "OHLC invariants + daily TSETMC reconciliation + exact H1-to-H4 aggregation",
        "days_checked": len(passed_dates),
        "h1_rows_checked": len(h1),
        "h4_rows_checked": len(h4),
        "h4_orphan_rows_quarantined_this_run": len(orphan_keys),
        "h4_quarantined_rows_total": len(quarantined),
        "coverage_complete": not (missing_h1 or missing_h4),
        "errors": errors,
        "warnings": [
            "Historical days without transaction-validated source data are excluded from indicator inputs."
        ] + ([f"{len(orphan_keys)} orphan H4 rows moved to quarantined_4h.csv."]
             if orphan_keys else []),
    }
    report["timeframe_coverage"] = {
        "expected_trading_days": len(expected_dates),
        "1h": {
            "validated_days": len(h1_dates & expected_dates),
            "missing_days": len(missing_h1),
            "missing_date_list": missing_h1,
            "candles": len(h1),
            "closing_auction_candles": sum(1 for x in h1 if x["time"][:2] == "18"),
        },
        "4h": {
            "validated_days": len(h4_complete_dates & expected_dates),
            "missing_days": len(missing_h4),
            "missing_date_list": missing_h4,
            "candles": len(h4),
            "partial_session_remainder_candles": sum(
                1 for x in h4 if int(x["time"][:2]) + 4 > 16
            ),
        },
    }
    report["validated_days"] = len(passed_dates)
    report["failed_days"] = len(report.get("failures", []))
    report["one_hour_candles"] = len(h1)
    report["four_hour_candles"] = len(h4)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REPORT)

    print(json.dumps({
        "event": "candle_validation_complete",
        "passed": passed,
        "status": report["candle_validation"]["status"],
        "h1_rows": len(h1), "h4_rows": len(h4),
        "h4_orphan_rows_quarantined": len(orphan_keys),
        "h1_daily_reconciled_days": len(passed_dates),
        "h1_missing_days": len(missing_h1),
        "h4_missing_days": len(missing_h4),
        "errors": {k: len(v) for k, v in errors.items()},
    }, ensure_ascii=False), flush=True)
    if not passed:
        raise SystemExit("CANDLE_VALIDATION_FAILED: canonical indicator calculation blocked")


if __name__ == "__main__":
    main()
