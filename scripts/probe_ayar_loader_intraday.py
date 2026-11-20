import json
import time
from pathlib import Path

import requests

INS_CODE = "34144395039913458"
SAMPLES = ["20200329", "20260621"]
HOSTS = [f"https://cdn{i}.tsetmc.com/api" for i in range(1, 10)]
DAILY = json.loads(Path("data/ayar_daily_history.json").read_text(encoding="utf-8"))
DAILY_MAP = {str(x["date"]): x for x in DAILY["valid_history"]}


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.tsetmc.com/",
    })
    results = []
    for date in SAMPLES:
        daily = DAILY_MAP[date]
        for host in HOSTS:
            url = f"{host}/Trade/GetTradeHistory/{INS_CODE}/{date}/false"
            item = {"date": date, "host": host, "url": url}
            try:
                response = session.get(url, timeout=(4, 8))
                item["http_status"] = response.status_code
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("tradeHistory", [])
                item["raw_rows"] = len(rows)
                by_id = {}
                conflicts = 0
                for row in rows:
                    key = str(row.get("nTran"))
                    if key in by_id and by_id[key] != row:
                        conflicts += 1
                    by_id[key] = row
                active = [r for r in by_id.values() if int(r.get("canceled") or 0) == 0]
                prices = [int(r["pTran"]) for r in active]
                item["unique_rows"] = len(by_id)
                item["active_rows"] = len(active)
                item["conflicting_duplicate_ids"] = conflicts
                item["volume"] = sum(int(r["qTitTran"]) for r in active)
                item["low"] = min(prices) if prices else None
                item["high"] = max(prices) if prices else None
                item["daily"] = {
                    "trade_count": int(daily["trade_count"]),
                    "volume": int(daily["volume"]),
                    "low": int(daily["low"]),
                    "high": int(daily["high"]),
                }
                item["matches"] = {
                    "count": len(active) == int(daily["trade_count"]),
                    "volume": item["volume"] == int(daily["volume"]),
                    "low": bool(prices) and item["low"] == int(daily["low"]),
                    "high": bool(prices) and item["high"] == int(daily["high"]),
                }
            except Exception as exc:
                item["error"] = str(exc)
            results.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
            time.sleep(0.1)
    Path("build/loader_probe.json").parent.mkdir(parents=True, exist_ok=True)
    Path("build/loader_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
