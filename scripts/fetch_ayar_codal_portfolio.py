import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from openpyxl import load_workbook
from io import BytesIO

SYMBOL = "عیار"
BASE = "https://search.codal.ir/api/search/v2/q"
# Last report link independently cross-checked from Codal's disclosure listing.
# Keep it as an explicit dated fallback; never label it as a newly fetched report.
FALLBACK_REPORT = {
    "title": "صورت وضعیت پورتفوی صندوق سرمایه گذاری دوره ۱ ماهه منتهی به ۱۴۰۵/۰۷/۳۰",
    "period_end_jalali": "1405/07/30",
    "publish_date": "1405/08/03",
    "tracing_no": None,
    "company_name": "صندوق طلای عیار مفید",
    "excel_url": "https://excel.codal.ir/service/Excel/GetAll/ufnsMFNlCf60hOOObOOO9l4CQQQaQQQQ8w%3D%3D/0",
    "attachment_url": "https://www.codal.ir/Reports/Attachment.aspx?LetterSerial=ufnsMFNlCf60hOOObOOO9l4CQQQaQQQQ8w%3D%3D",
}
OUT = Path("data/ayar_codal_portfolio.json")
OUT.parent.mkdir(parents=True, exist_ok=True)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.codal.ir/",
}

def norm(value):
    s = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", s.replace("ي", "ی").replace("ك", "ک")).strip()

def ascii_digits(value):
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return str(value or "").translate(table)

def local_name(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]

def parse_letters(response):
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" in content_type:
        payload = response.json()
        letters = payload.get("Letters", payload.get("letters", []))
        return letters if isinstance(letters, list) else []
    import xml.etree.ElementTree as ET
    root = ET.fromstring(response.content)
    letters = []
    for node in root.iter():
        if local_name(node.tag) == "CodalLetterHeaderDto":
            item = {}
            for child in list(node):
                item[local_name(child.tag)] = child.text
            letters.append(item)
    return letters

def parse_jalali_from_title(title):
    text = ascii_digits(title)
    matches = re.findall(r"(14\d{2})\s*[/\-]\s*(\d{1,2})\s*[/\-]\s*(\d{1,2})", text)
    if not matches:
        return None
    y, m, d = matches[-1]
    return f"{int(y):04d}/{int(m):02d}/{int(d):02d}"

def serializable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    params = {
        "PageNumber": 1, "PageSize": 50, "Symbol": SYMBOL,
        "Category": 3, "LetterType": -1, "Length": -1,
        "Audited": "true", "NotAudited": "true",
        "Consolidatable": "true", "NotConsolidatable": "true",
        "Childs": "false", "Mains": "true", "Publisher": "false",
        "TracingNo": -1, "search": "true",
    }
    report = None
    report_date = None
    title = None
    search_error = None
    for attempt in range(2):
        try:
            response = session.get(BASE, params=params, timeout=(8, 25))
            response.raise_for_status()
            letters = parse_letters(response)
            matches = []
            for item in letters:
                candidate_title = norm(item.get("Title") or item.get("title"))
                normalized_title = candidate_title.replace("ي", "ی").replace("ك", "ک")
                if "صورت وضعیت پورتفوی صندوق سرمایه گذاری" in normalized_title or "صورت وضعیت پرتفوی صندوق سرمایه گذاری" in normalized_title:
                    candidate_date = parse_jalali_from_title(candidate_title)
                    if candidate_date:
                        matches.append((candidate_date, item, candidate_title))
            if matches:
                report_date, report, title = max(matches, key=lambda x: x[0])
                break
            search_error = "گزارش ماهانه پرتفوی در نتایج جستجوی کدال پیدا نشد"
        except Exception as exc:
            search_error = f"{type(exc).__name__}: {exc}"
    if report is None:
        report = {
            "ExcelUrl": FALLBACK_REPORT["excel_url"],
            "AttachmentUrl": FALLBACK_REPORT["attachment_url"],
            "TracingNo": FALLBACK_REPORT["tracing_no"],
            "CompanyName": FALLBACK_REPORT["company_name"],
            "PublishDateTime": FALLBACK_REPORT["publish_date"],
        }
        report_date = FALLBACK_REPORT["period_end_jalali"]
        title = FALLBACK_REPORT["title"]
        report_source = "Codal official Excel service; dated fallback report"
    else:
        report_source = "Codal official search API + Excel service"

    excel_url = report.get("ExcelUrl") or report.get("excelUrl")
    attachment_url = report.get("AttachmentUrl") or report.get("attachmentUrl")
    if not excel_url:
        raise RuntimeError("کدال برای آخرین گزارش پرتفوی لینک ExcelUrl ارائه نکرد")

    download_url = urljoin("https://www.codal.ir/", excel_url)
    book_response = session.get(download_url, timeout=(15, 90))
    book_response.raise_for_status()
    content = book_response.content
    if not content.startswith(b"PK"):
        raise RuntimeError("خروجی Excel کدال فایل XLSX معتبر نیست: " + book_response.text[:300])

    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            values = [serializable(v) for v in row]
            while values and values[-1] is None:
                values.pop()
            if any(v is not None for v in values):
                rows.append(values)
        sheets.append({
            "name": ws.title,
            "max_row": ws.max_row,
            "max_column": ws.max_column,
            "rows": rows,
        })
    wb.close()

    output = {
        "status": "ok",
        "symbol": SYMBOL,
        "source": report_source,
        "search_api_error": search_error,
        "retrieved_at": datetime.now().astimezone().isoformat(),
        "report": {
            "title": title,
            "period_end_jalali": report_date,
            "publish_date": ascii_digits(report.get("PublishDateTime") or report.get("publishDateTime")),
            "tracing_no": report.get("TracingNo") or report.get("tracingNo"),
            "company_name": report.get("CompanyName") or report.get("companyName"),
            "excel_url": download_url,
            "attachment_url": urljoin("https://www.codal.ir/", attachment_url) if attachment_url else None,
            "is_fallback_report": report_source.endswith("dated fallback report"),
        },
        "workbook": {
            "filename": "Codal Ayar monthly portfolio report",
            "sheet_count": len(sheets),
            "sheets": sheets,
        },
        "notes_fa": [
            "داده‌های جدول‌ها عین مقادیر فایل Excel منتشرشده در کدال هستند.",
            "تاریخ دوره گزارش از عنوان اطلاعیه استخراج شده و با تاریخ دریافت فایل یکسان نیست.",
            "هیچ درصد یا تبدیل واحدی بدون تشخیص عنوان ستون و واحد گزارش ساخته نشده است."
        ],
    }
    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)
    print(json.dumps({
        "status": output["status"],
        "report": output["report"],
        "sheet_count": len(sheets),
        "sheets": [{"name": s["name"], "rows": len(s["rows"])} for s in sheets],
        "output": str(OUT),
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
