import json
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
    retry = Retry(total=4, connect=4, read=4, status=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset(["GET"]), raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
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
            response = session.get(url, timeout=(25, 180))
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

            # TSETMC may repeat an identical trade row under the same nTran.
            # Keep one row per transaction number, but reject conflicting duplicates.
            unique_by_number = {}
            for trade in active:
                number = str(trade.get("nTran"))
                signature = (
                    int(trade.get("hEven", 0) or 0),
                    int(trade.get("pTran", 0) or 0),
                    int(trade.get("qTitTran", 0) or 0),
                )
                if number in unique_by_number:
                    previous = unique_by_number[number]
                    previous_signature = (
                        int(previous.get("hEven", 0) or 0),
                        int(previous.get("pTran", 0) or 0),
                        int(previous.get("qTitTran", 0) or 0),
                    )
                    if signature != previous_signature:
                        raise ValueError(f"Conflicting duplicate nTran={number}")
                else:
                    unique_by_number[number] = trade

            normalized = list(unique_by_number.values())
            volume = sum(int(t["qTitTran"]) for t in normalized)
            prices = [int(t["pTran"]) for t in normalized if int(t["pTran"]) > 0]
            checks = {
                "trade_count": len(normalized) == int(daily["trade_count"]),
                "volume": volume == int(daily["volume"]),
                "minimum": bool(prices) and min(prices) == int(daily["low"]),
                "maximum": bool(prices) and max(prices) == int(daily["high"]),
            }
            print(json.dumps({
                "date": date,
                "http_status": response.status_code,
                "raw_records": len(trades),
                "active_records": len(active),
                "unique_transactions": len(normalized),
                "daily_trade_count": int(daily["trade_count"]),
                "daily_volume": int(daily["volume"]),
                "intraday_volume": volume,
                "checks": checks,
                "passed": all(checks.values()),
            }, ensure_ascii=False))
            if not all(checks.values()):
                failures += 1
                from collections import Counter, defaultdict

                canceled_counts = Counter(str(t.get("canceled", "<missing>")) for t in trades)
                grouped = defaultdict(list)
                for trade in active:
                    grouped[str(trade.get("nTran"))].append(trade)

                duplicate_groups = [
                    {
                        "nTran": key,
                        "records": len(rows),
                        "quantities": [int(row.get("qTitTran", 0) or 0) for row in rows],
                        "prices": [int(row.get("pTran", 0) or 0) for row in rows],
                        "times": [int(row.get("hEven", 0) or 0) for row in rows],
                    }
                    for key, rows in grouped.items() if len(rows) > 1
                ]
                unique_first = {}
                unique_last = {}
                for key, rows in grouped.items():
                    unique_first[key] = rows[0]
                    unique_last[key] = rows[-1]

                print(json.dumps({
                    "diagnostics": {
                        "canceled_distribution": dict(canceled_counts),
                        "active_distinct_nTran": len(grouped),
                        "active_duplicate_group_count": len(duplicate_groups),
                        "active_duplicate_extra_rows": sum(x["records"] - 1 for x in duplicate_groups),
                        "active_volume_raw": sum(int(t["qTitTran"]) for t in active),
                        "active_volume_unique_first": sum(int(t["qTitTran"]) for t in unique_first.values()),
                        "active_volume_unique_last": sum(int(t["qTitTran"]) for t in unique_last.values()),
                        "duplicate_groups_sample": duplicate_groups[:30],
                        "trade_fields": sorted(trades[0].keys()) if trades else [],
                        "sample_first_trade": trades[0] if trades else None,
                        "sample_last_trade": trades[-1] if trades else None,
                    }
                }, ensure_ascii=False))
        except Exception as exc:
            failures += 1
            print(json.dumps({"date": date, "error": str(exc), "retry_policy": "4 retries with exponential backoff; connect/read timeout 25/180s"}, ensure_ascii=False))
        time.sleep(1)

    if failures:
        raise SystemExit(f"Historical intraday validation failed on {failures}/{len(sample)} sample days.")
    print("SUCCESS: Historical intraday endpoint validated against daily TSETMC data.")


if __name__ == "__main__":
    main()

# Trigger validation run after enabling push workflow trigger.
# Intraday history endpoint availability is checked by this workflow.
