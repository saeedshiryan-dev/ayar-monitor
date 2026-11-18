import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
FUND_TYPE = "5"  # TSETMC commodity funds
TARGET_NAME = "عیار"
TEHRAN = ZoneInfo("Asia/Tehran")
OUT = Path("data/ayar_fund_history.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
})

def get_json(url):
    response = session.get(url, timeout=(20, 60))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"ساختار پاسخ TSETMC نامعتبر است: {url}")
    return payload

def norm(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text.replace("ي", "ی").replace("ك", "ک")).strip()

def get_field(row, *names):
    if not isinstance(row, dict):
        return None
    lookup = {str(k).replace("_", "").lower(): v for k, v in row.items()}
    for name in names:
        key = name.replace("_", "").lower()
        if key in lookup and lookup[key] not in (None, ""):
            return lookup[key]
    return None

def as_number(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None

def unwrap_list(payload, key):
    value = payload.get(key)
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [value]
    return []

def find_stats(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == "stats" and isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        for value in node.values():
            found = find_stats(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_stats(value)
            if found is not None:
                return found
    return None

def date_value(row):
    value = get_field(row, "UpdateGregorianDate", "dEven", "date", "updateDate")
    if value is None:
        return None
    return str(value)

def normalize_stat(row):
    return {
        "date": date_value(row),
        "redemption_nav": as_number(get_field(row, "RedemptionNAV", "pRedTran")),
        "subscription_nav": as_number(get_field(row, "SubscriptionNAV", "pSubTran")),
        "statistical_nav": as_number(get_field(row, "StatisticalNAV", "pClosing", "iClose")),
        "total_nav": as_number(get_field(row, "TotalNAV")),
        "source_record": row,
    }

def main():
    now = datetime.now(TEHRAN)
    listing_payload = get_json(f"{BASE}/Fund/GetFunds/{FUND_TYPE}")
    funds = unwrap_list(listing_payload, "funds")
    if not funds:
        raise ValueError("فهرست صندوق‌های کالایی TSETMC خالی یا فاقد ساختار معتبر است")

    candidates = []
    for item in funds:
        name = get_field(item, "mfName", "FundName", "fundName", "name")
        if name and TARGET_NAME in norm(name):
            reg_no = get_field(item, "regNo", "RegistrationNumber", "registrationNumber")
            if reg_no is not None:
                candidates.append((str(reg_no), str(name), item))

    unique = {item[0]: item for item in candidates}
    if len(unique) != 1:
        visible = [
            {"name": get_field(x, "mfName", "FundName", "fundName", "name"),
             "registration_number": get_field(x, "regNo", "RegistrationNumber", "registrationNumber")}
            for x in funds if TARGET_NAME in norm(get_field(x, "mfName", "FundName", "fundName", "name"))
        ]
        raise ValueError("شناسه ثبت صندوق عیار به‌صورت یکتا از فهرست رسمی پیدا نشد: " +
                         json.dumps(visible, ensure_ascii=False))

    reg_no, fund_name, listing_record = next(iter(unique.values()))
    detail_payload = get_json(f"{BASE}/Fund/GetFundInDetail/{reg_no}")
    detail = detail_payload.get("fund")
    if detail is None:
        raise ValueError("پاسخ GetFundInDetail فاقد کلید fund است")
    stats = find_stats(detail)
    if stats is None:
        raise ValueError("تاریخچه NAV در پاسخ جزئیات صندوق (Stats) پیدا نشد")

    history = [normalize_stat(row) for row in stats]
    history = [row for row in history if row["date"]]
    if not history:
        raise ValueError("رکورد تاریخ‌دار در تاریخچه NAV صندوق وجود ندارد")

    # Preserve provider order and source rows; do not sort or interpolate provider data.
    dated = [row["date"] for row in history]
    output = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "source": "TSETMC Fund/GetFunds/5 + Fund/GetFundInDetail",
        "retrieved_at": now.isoformat(),
        "timezone": "Asia/Tehran",
        "status": "ok",
        "fund": {
            "name": fund_name,
            "registration_number": reg_no,
            "fund_type": FUND_TYPE,
            "listing_record": listing_record,
        },
        "history_count": len(history),
        "first_date_in_source_order": dated[0],
        "last_date_in_source_order": dated[-1],
        "history": history,
        "latest_record": history[-1],
        "detail_metadata": detail if isinstance(detail, dict) else {"provider_detail": detail},
        "data_notes_fa": [
            "تاریخچه و مقادیر فقط از پاسخ رسمی TSETMC نگهداری شده‌اند؛ ردیف‌های نامعتبر حذف نشده‌اند مگر فاقد تاریخ باشند.",
            "total_nav عین فیلد TotalNAV منبع است؛ تا زمان تأیید واحد و تعریف دقیق، نباید با NAV هر واحد یا ارزش بازار جمع/مقایسه شود.",
            "source_record و detail_metadata برای ممیزی و حفظ فیلدهای خام منبع نگهداری می‌شوند."
        ],
    }

    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)
    print(json.dumps({
        "status": output["status"],
        "fund_name": fund_name,
        "registration_number": reg_no,
        "history_count": len(history),
        "first_date": dated[0],
        "last_date": dated[-1],
        "latest_record": output["latest_record"],
        "output": str(OUT),
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
