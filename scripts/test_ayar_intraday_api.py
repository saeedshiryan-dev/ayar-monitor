import json
import time
from pathlib import Path

import requests

INS_CODE = "34144395039913458"
BASE = "https://cdn.tsetmc.com/api"
HISTORY_FILE = Path("data/ayar_daily_history.json")


def main():
    source = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    days = source.get("valid_history", [])
    if len(days) < 3:
        raise RuntimeError("Not enough validated daily history to test.")

    sample = days[-3:]
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.tsetmc.com/",
    })

    failures = 0
    for daily in sample:
        date = str(daily["date"])
        url = f"{BASE}/Trade/GetTradeHistory/{INS_CODE}/{date}/false"
        try:
            response = session.get(url, timeout=(20, 90))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("tradeHistory"), list):
                raise ValueError(f"Unexpected API response structure: {type(payload).__name__}")

            trades = payload["tradeHistory"]
            active = []
            for trade in trades:
                if int(trade.get("canceled", 0) or 0) != 0:
                    continue
                active.append(trade)

            volume = sum(int(t["qTitTran"]) for t in active)
            prices = [int(t["pTran"]) for t in active if int(t["pTran"]) > 0]
            checks = {
                "trade_count": len(active) == int(daily["trade_count"]),
                "volume": volume == int(daily["volume"]),
                "minimum": bool(prices) and min(prices) == int(daily["low"]),
                "maximum": bool(prices) and max(prices) == int(daily["high"]),
            }
            print(json.dumps({
                "date": date,
                "http_status": response.status_code,
                "raw_records": len(trades),
                "active_records": len(active),
                "daily_trade_count": int(daily["trade_count"]),
                "daily_volume": int(daily["volume"]),
                "intraday_volume": volume,
                "checks": checks,
                "passed": all(checks.values()),
            }, ensure_ascii=False))
            if not all(checks.values()):
                failures += 1
        except Exception as exc:
            failures += 1
            print(json.dumps({"date": date, "error": str(exc)}, ensure_ascii=False))
        time.sleep(1)

    if failures:
        raise SystemExit(f"Historical intraday validation failed on {failures}/{len(sample)} sample days.")
    print("SUCCESS: Historical intraday endpoint validated against daily TSETMC data.")


if __name__ == "__main__":
    main()

# Trigger validation run after enabling push workflow trigger.
