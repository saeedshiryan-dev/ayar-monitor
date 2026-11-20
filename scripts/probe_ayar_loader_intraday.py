import ast
import json
import re
import time
from pathlib import Path

import requests

INS_CODE = "34144395039913458"
SAMPLES = ["20200329", "20200415", "20200922", "20261117"]
SERVERS = [0, 7, 8, 9]
DAILY = json.loads(Path("data/ayar_daily_history.json").read_text(encoding="utf-8"))
DAILY_MAP = {str(x["date"]): x for x in DAILY["valid_history"]}


def parse_rows(page):
    marker = "var IntraTradeData=["
    if marker not in page:
        return None
    body = page.split(marker, 1)[1].split("];", 1)[0]
    return ast.literal_eval("[" + body + "]")


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Referer": "https://www.tsetmc.com/",
    })
    results = []
    for date in SAMPLES:
        daily = DAILY_MAP[date]
        for server in SERVERS:
            host = "cdn.tsetmc.com" if server == 0 else f"cdn{server}.tsetmc.com"
            url = f"https://{host}/Loader.aspx?ParTree=15131P&i={INS_CODE}&d={date}"
            item = {"date": date, "server": server, "url": url}
            try:
                response = session.get(url, timeout=(20, 60))
                item["http_status"] = response.status_code
                response.raise_for_status()
                rows = parse_rows(response.text)
                if rows is None:
                    item["error"] = "IntraTradeData variable not found"
                    item["page_prefix"] = response.text[:180]
                else:
                    item["raw_rows"] = len(rows)
                    candidates = {}
                    for label, keep in [
                        ("all", lambda row: True),
                        ("not_discarded", lambda row: len(row) < 5 or str(row[4]).lower() in ("0", "false", "none", "")),
                    ]:
                        active = [r for r in rows if keep(r)]
                        try:
                            times = [str(r[1]) for r in active]
                            prices = [int(float(r[3])) for r in active]
                            volumes = [int(float(r[2])) for r in active]
                            counts = [int(float(r[0])) for r in active]
                            candidates[label] = {
                                "rows": len(active),
                                "volume": sum(volumes),
                                "count_sum": sum(counts),
                                "low": min(prices) if prices else None,
                                "high": max(prices) if prices else None,
                                "first_time": times[0] if times else None,
                                "last_time": times[-1] if times else None,
                                "first_price": prices[0] if prices else None,
                                "last_price": prices[-1] if prices else None,
                                "matches_daily": {
                                    "rows_eq_trade_count": len(active) == int(daily["trade_count"]),
                                    "volume_eq_daily": sum(volumes) == int(daily["volume"]),
                                    "low_eq_daily": bool(prices) and min(prices) == int(daily["low"]),
                                    "high_eq_daily": bool(prices) and max(prices) == int(daily["high"]),
                                },
                            }
                        except Exception as exc:
                            candidates[label] = {"parse_error": str(exc)}
                    item["candidates"] = candidates
                    item["sample_row"] = rows[0] if rows else None
            except Exception as exc:
                item["error"] = str(exc)
            results.append(item)
            print(json.dumps(item, ensure_ascii=False))
            time.sleep(0.4)
    Path("build/loader_probe.json").parent.mkdir(parents=True, exist_ok=True)
    Path("build/loader_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
