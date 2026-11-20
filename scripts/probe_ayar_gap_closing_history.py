# Re-run marker: validate all previously missing dates with the closing-history fallback.
import json
import time
from pathlib import Path

import requests

INS_CODE = "34144395039913458"
REPORT = Path("data/intraday/validation_report.json")
DAILY = json.loads(Path("data/ayar_daily_history.json").read_text(encoding="utf-8"))
DAILY_MAP = {str(x["date"]): x for x in DAILY["valid_history"]}


def main():
    previous = json.loads(REPORT.read_text(encoding="utf-8"))
    gaps = [str(x["date"]) for x in previous.get("failures", [])]
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.tsetmc.com/",
    })
    results = []
    for index, date in enumerate(gaps, 1):
        item = {"date": date}
        url = f"https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceHistory/{INS_CODE}/{date}"
        item["url"] = url
        try:
            response = session.get(url, timeout=(12, 60))
            item["http_status"] = response.status_code
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("closingPriceHistory", [])
            rows = sorted(rows, key=lambda x: (
                int(x.get("hEven") or 0),
                float(x.get("zTotTran") or 0),
                float(x.get("qTotTran5J") or 0),
            ))
            positive = [x for x in rows if float(x.get("zTotTran") or 0) > 0]
            daily = DAILY_MAP[date]
            prices = [int(x["pDrCotVal"]) for x in positive if x.get("pDrCotVal") is not None]
            last = positive[-1] if positive else {}
            first = positive[0] if positive else {}
            checks = {
                "trade_count": bool(positive) and int(float(last.get("zTotTran") or 0)) == int(daily["trade_count"]),
                "volume": bool(positive) and int(float(last.get("qTotTran5J") or 0)) == int(daily["volume"]),
                "low": bool(prices) and min(prices) == int(daily["low"]),
                "high": bool(prices) and max(prices) == int(daily["high"]),
                "open": bool(prices) and prices[0] == int(daily["open"]),
                "last": bool(prices) and prices[-1] == int(daily.get("last", daily["close"])),
            }
            item.update({
                "rows": len(rows), "positive_rows": len(positive),
                "final_count": int(float(last.get("zTotTran") or 0)) if last else None,
                "final_volume": int(float(last.get("qTotTran5J") or 0)) if last else None,
                "first_price": prices[0] if prices else None,
                "last_price": prices[-1] if prices else None,
                "low": min(prices) if prices else None,
                "high": max(prices) if prices else None,
                "checks": checks,
                "daily": {k: int(daily[k]) for k in ("trade_count","volume","low","high","open")},
                "daily_last": int(daily.get("last", daily["close"])),
                "passed_all": all(checks.values()),
                "sample_first": first,
                "sample_last": last,
            })
        except Exception as exc:
            item["error"] = str(exc)
        results.append(item)
        print(json.dumps({
            "index": index, "total": len(gaps), "date": date,
            "status": item.get("http_status"), "positive_rows": item.get("positive_rows"),
            "checks": item.get("checks"), "error": item.get("error"),
        }, ensure_ascii=False), flush=True)
        time.sleep(0.8)
    out = Path("build/gap_closing_history_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(1 for x in results if x.get("passed_all"))
    print(json.dumps({"gaps": len(gaps), "fetched": len(results), "passed_all": passed,
                      "failed": len(results)-passed, "artifact": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
