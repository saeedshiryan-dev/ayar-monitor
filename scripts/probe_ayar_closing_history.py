import json
import time
from pathlib import Path

import requests

INS_CODE = "34144395039913458"
SAMPLES = ["20200329", "20260621", "20261117"]
DAILY = json.loads(Path("data/ayar_daily_history.json").read_text(encoding="utf-8"))
DAILY_MAP = {str(x["date"]): x for x in DAILY["valid_history"]}


def hms(v):
    v = int(v)
    return v // 10000, (v // 100) % 100, v % 100


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
            rows = r.json().get("closingPriceHistory", [])
            rows = sorted(rows, key=lambda x: int(x.get("hEven", 0)))
            item["row_count"] = len(rows)
            item["keys"] = sorted(rows[0].keys()) if rows else []
            item["first_row"] = rows[0] if rows else None
            item["last_row"] = rows[-1] if rows else None
            session_rows = [x for x in rows if (9,0,0) <= hms(x.get("hEven",0)) <= (12,30,59)]
            item["session_row_count"] = len(session_rows)
            positive = [x for x in rows if float(x.get("zTotTran") or 0) > 0]
            item["positive_rows"] = len(positive)
            item["positive_first"] = positive[0] if positive else None
            item["positive_last"] = positive[-1] if positive else None
            positive_prices = [int(x["pDrCotVal"]) for x in positive if x.get("pDrCotVal") is not None]
            positive_closing = [int(x["pClosing"]) for x in positive if x.get("pClosing") is not None]
            item["positive_pclosing_range"] = {
                "low": min(positive_closing) if positive_closing else None,
                "high": max(positive_closing) if positive_closing else None,
            }
            item["positive_cumulative_monotonic"] = {
                "count": all(float(a.get("zTotTran") or 0) <= float(b.get("zTotTran") or 0) for a,b in zip(positive, positive[1:])),
                "volume": all(float(a.get("qTotTran5J") or 0) <= float(b.get("qTotTran5J") or 0) for a,b in zip(positive, positive[1:])),
            }
            item["positive_price_range"] = {
                "low": min(positive_prices) if positive_prices else None,
                "high": max(positive_prices) if positive_prices else None,
            }
            item["session_first"] = session_rows[0] if session_rows else None
            item["session_last"] = session_rows[-1] if session_rows else None
            for label, subset in (("all", rows), ("session", session_rows)):
                block = {}
                for field in ("pClosing", "pDrCotVal", "zTotTran", "qTotTran5J", "qTotCap"):
                    vals = [float(x[field]) for x in subset if x.get(field) is not None]
                    block[field] = {
                        "first": vals[0] if vals else None,
                        "last": vals[-1] if vals else None,
                        "min": min(vals) if vals else None,
                        "max": max(vals) if vals else None,
                    }
                item[label] = block
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
