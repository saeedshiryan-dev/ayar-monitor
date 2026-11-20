import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

INS_CODE = "34144395039913458"
SAMPLES = ["20200329", "20260621", "20261117"]
TEMPLATES = [
    "http://tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://www.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://www.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://old.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://old.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://service.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://service.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://cdn.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://cdn.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://cdn7.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://cdn7.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://cdn8.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://cdn8.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "http://cdn9.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
    "https://cdn9.tsetmc.com/Loader.aspx?ParTree=15131P&i={ins}&d={date}",
]

def main():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "http://tsetmc.com/",
    })
    out = []
    for date in SAMPLES:
        for template in TEMPLATES:
            url = template.format(ins=INS_CODE, date=date)
            item = {"date": date, "url": url}
            try:
                r = s.get(url, timeout=(5, 12), allow_redirects=True)
                body = r.text
                item.update({
                    "status": r.status_code,
                    "final_url": r.url,
                    "length": len(body),
                    "content_type": r.headers.get("content-type"),
                    "title": (re.search(r"<title[^>]*>(.*?)</title>", body, re.I|re.S).group(1).strip()[:120]
                              if re.search(r"<title[^>]*>(.*?)</title>", body, re.I|re.S) else None),
                    "has_intra": "IntraTradeData" in body,
                    "has_trade": "trade" in body.lower(),
                    "script_src": re.findall(r'<script[^>]+src=["\\']([^"\\']+)', body, re.I)[:8],
                    "prefix": re.sub(r"\\s+", " ", body[:300])[:300],
                })
            except Exception as e:
                item["error"] = str(e)[:300]
            out.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
            time.sleep(0.2)
    Path("build/loader_probe.json").parent.mkdir(parents=True, exist_ok=True)
    Path("build/loader_probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
