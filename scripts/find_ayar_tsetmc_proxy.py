#!/usr/bin/env python3
"""Find a short-lived Iranian HTTPS CONNECT proxy that can reach TSETMC trade APIs."""
import json
import os
import requests
from ayar_market_status import get_market_status

INS_CODE = "34144395039913458"
DATE = __import__("datetime").datetime.now(
    __import__("zoneinfo").ZoneInfo("Asia/Tehran")
).strftime("%Y%m%d")
PROXY_LIST = "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/IR/data.json"
ENDPOINTS = (
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
        print(f"Testing {min(15, len(candidates))} Iranian proxy candidates.")
        for index, item in enumerate(candidates[:15], 1):
            proxy = item["proxy"]
            proxies = {"http": proxy, "https": proxy}
            for url, keys in ENDPOINTS:
                try:
                    r = requests.get(url, proxies=proxies, headers=HEADERS, timeout=(3, 5))
                    r.raise_for_status()
                    payload = r.json()
                    if not isinstance(payload, dict):
                        continue
                    rows = next((payload.get(k) for k in keys
                                 if isinstance(payload.get(k), list)), None)
                    if rows is not None:
                        sample = rows[0] if rows else None
                        print(json.dumps({
                            "candidate": index,
                            "ip": item.get("ip"),
                            "port": item.get("port"),
                            "endpoint": url,
                            "row_count": len(rows),
                            "row_fields": sorted(sample.keys()) if isinstance(sample, dict) else None,
                            "sample_first": sample,
                            "sample_last": rows[-1] if rows else None,
                        }, ensure_ascii=False), flush=True)
                        # The full-day transaction endpoints must return a
                        # substantial list. Reject short intraday fragments.
                        if len(rows) >= 1000 and "/GetTradeIntraDay/" not in url:
                            selected = proxy
                            print(
                                f"Working full-session TSETMC trade proxy found at candidate {index} "
                                f"({item.get('ip')}:{item.get('port')}); "
                                f"endpoint returned {len(rows)} rows."
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


if __name__ == "__main__":
    main()
