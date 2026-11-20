import json
import time
from pathlib import Path

import requests

INS_CODE = "34144395039913458"
SAMPLES = ["20200329", "20260621", "20261117"]
DAILY = json.loads(Path("data/ayar_daily_history.json").read_text(encoding="utf-8"))
DAILY_MAP = {str(x["date"]): x for x in DAILY["valid_history"]}


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.tsetmc.com/",
    })
    results = []
    for date in SAMPLES:
        item = {"date": date}
        url = f"https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceHistory/{INS_CODE}/{date}"
        item["url"] = url
        try:
            r = session.get(url, timeout=(8, 30))
            item["http_status"] = r.status_code
            r.raise_for_status()
            payload = r.json()
            rows = payload.get("closingPriceHistory", [])
            item["row_count"] = len(rows)
            item["keys"] = sorted(rows[0].keys()) if rows else []
            item["first_row"] = rows[0] if rows else None
            item["last_row"] = rows[-1] if rows else None
            for field in ("pClosing", "pDrCotVal", "zTotTran", "qTotTran5J", "qTotCap"):
                vals = [float(x[field]) for x in rows if x.get(field) is not None]
                item[field] = {
                    "first": vals[0] if vals else None,
                    "last": vals[-1] if vals else None,
                    "min": min(vals) if vals else None,
                    "max": max(vals) if vals else None,
                    "monotonic_non_decreasing": all(a <= b for a, b in zip(vals, vals[1:])),
                }
            daily = DAILY_MAP[date]
            item["daily"] = {
                "trade_count": int(daily["trade_count"]),
                "volume": int(daily["volume"]),
                "low": int(daily["low"]),
                "high": int(daily["high"]),
                "open": int(daily["open"]),
                "last": int(daily.get("last", daily["close"])),
            }
        except Exception as exc:
            item["error"] = str(exc)
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
        time.sleep(0.5)
    Path("build/closing_history_probe.json").parent.mkdir(parents=True, exist_ok=True)
    Path("build/closing_history_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
