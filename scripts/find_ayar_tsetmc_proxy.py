#!/usr/bin/env python3
"""Find a short-lived Iranian HTTPS CONNECT proxy that can reach TSETMC trade APIs."""
import json
import os
import time
import requests
from ayar_market_status import get_market_status

INS_CODE = "34144395039913458"
DATE = __import__("datetime").datetime.now(
    __import__("zoneinfo").ZoneInfo("Asia/Tehran")
).strftime("%Y%m%d")
PROXY_LIST = "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/IR/data.json"
ENDPOINTS = (
    (f"http://cdn.tsetmc.com/api/Trade/GetTrade/{INS_CODE}",
     ("trade", "tradeHistory")),
    (f"http://cdn.tsetmc.com/api/Trade/GetTradeHistory/{INS_CODE}/{DATE}/false",
     ("tradeHistory", "trade", "trades")),
    (f"https://cdn.tsetmc.com/api/Trade/GetTradeHistory/{INS_CODE}/{DATE}/false",
     ("tradeHistory", "trade", "trades")),
    (f"https://cdn.tsetmc.com/api/Trade/GetTrade/{INS_CODE}",
     ("trade", "tradeHistory")),
    (f"https://cdn.tsetmc.com/api/Trade/GetTradeIntraDay/{INS_CODE}",
     ("tradeIntraDay", "trade", "trades", "tradeHistory")),
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
    "Origin": "https://www.tsetmc.com",
}


def main():
    if get_market_status().get("status") != "open":
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
            env.write("TSETMC_PROXY=\n")
        print("Market closed; proxy discovery skipped.")
        return
    selected = None
    try:
        response = requests.get(PROXY_LIST, timeout=(5, 10))
        response.raise_for_status()
        candidates = response.json()
        candidates = [
            item for item in candidates
            if item.get("protocol") == "http"
            and item.get("geolocation", {}).get("country") == "IR"
            and str(item.get("proxy", "")).startswith("http://")
        ]
        print(f"Testing {min(8, len(candidates))} Iranian proxy candidates.")
        for index, item in enumerate(candidates[:8], 1):
            proxy = item["proxy"]
            proxies = {"http": proxy, "https": proxy}
            for url, keys in ENDPOINTS:
                try:
                    request_url = url + ("?_= " + str(int(time.time()))).replace(" ", "") if "/GetTradeIntraDay/" in url else url
                    r = requests.get(request_url, proxies=proxies, headers=HEADERS, timeout=(3, 5))
                    r.raise_for_status()
                    payload = r.json()
                    if not isinstance(payload, dict):
                        continue
                    rows = next((payload.get(k) for k in keys
                                 if isinstance(payload.get(k), list)), None)
                    if rows is not None:
                        sample = rows[0] if rows else None
                        aggregate_ok = False
                        aggregate_quality = {}
                        if "/GetTradeIntraDay/" in url and rows:
                            try:
                                hms = [int(x.get("hEven") or 0) for x in rows if isinstance(x, dict)]
                                times = []
                                for value in hms:
                                    hh, mm, ss = value // 10000, (value // 100) % 100, value % 100
                                    times.append(hh * 3600 + mm * 60 + ss)
                                volumes = sum(int(float(x.get("volume") or 0)) for x in rows if isinstance(x, dict))
                                quote = json.loads(open("data/ayar_live.json", encoding="utf-8").read())
                                quote_volume = int(quote.get("tsetmc_quote_total_volume") or 0)
                                now = __import__("datetime").datetime.now(
                                    __import__("zoneinfo").ZoneInfo("Asia/Tehran")
                                )
                                last_age = now.hour * 3600 + now.minute * 60 + now.second - max(times)
                                volume_gap_pct = abs(volumes - quote_volume) / quote_volume * 100 if quote_volume else 999
                                max_gap = max((b-a for a,b in zip(sorted(times), sorted(times)[1:])), default=99999)
                                aggregate_ok = (
                                    min(times) <= 12*3600 + 5*60
                                    and 0 <= last_age <= 180
                                    and max_gap <= 180
                                )
                                aggregate_quality = {
                                    "last_age_seconds": last_age,
                                    "max_gap_seconds": max_gap,
                                    "aggregate_volume": volumes,
                                    "quote_volume": quote_volume,
                                    "volume_gap_pct": round(volume_gap_pct, 3),
                                }
                            except Exception as exc:
                                aggregate_quality = {"validation_error": str(exc)[:160]}
                        print(json.dumps({
                            "candidate": index,
                            "ip": item.get("ip"),
                            "port": item.get("port"),
                            "endpoint": request_url,
                            "row_count": len(rows),
                            "row_fields": sorted(sample.keys()) if isinstance(sample, dict) else None,
                            "sample_first": sample,
                            "sample_last": rows[-1] if rows else None,
                            "aggregate_quality": aggregate_quality,
                        }, ensure_ascii=False), flush=True)
                        if len(rows) >= 1000 and "/GetTradeIntraDay/" not in url:
                            selected = proxy
                            print(
                                f"Working full-session TSETMC trade proxy found at candidate {index} "
                                f"({item.get('ip')}:{item.get('port')}); "
                                f"endpoint returned {len(rows)} rows."
                            )
                            break
                        if aggregate_ok:
                            selected = proxy
                            os.environ["TSETMC_AGGREGATE_ONLY"] = "1"
                            print(
                                f"Working recent TSETMC intraday-aggregate proxy found at candidate {index} "
                                f"({item.get('ip')}:{item.get('port')}); "
                                f"endpoint returned {len(rows)} reconciled rows."
                            )
                            break
                except Exception as exc:
                    print(f"Candidate {index} endpoint failed: {type(exc).__name__}")
            if selected:
                break
        if not selected:
            print("No Iranian proxy passed the TSETMC trade-endpoint test; direct access will remain enabled.")
    except Exception as exc:
        print(f"Proxy discovery unavailable: {type(exc).__name__}: {str(exc)[:180]}")

    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
        env.write(f"TSETMC_PROXY={selected or ''}\n")
        env.write(f"TSETMC_AGGREGATE_ONLY={os.environ.get('TSETMC_AGGREGATE_ONLY', '')}\n")


if __name__ == "__main__":
    main()
