from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo
import json

TEHRAN = ZoneInfo("Asia/Tehran")

# ساعات معاملاتی صندوق‌های کالایی طلا؛ طبق زمان‌بندی فعلی بورس کالای ایران (۱۲ تا ۱۸)
# زمان‌بندی را با ساعت سهام/ETFهای عادی اشتباه نگیرید.
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}  # شنبه تا چهارشنبه
PRE_MARKET_START = time(11, 45)
MARKET_OPEN = time(12, 0)
MARKET_CLOSE = time(18, 0)

HOLIDAYS_FILE = Path("data/market_holidays.json")


def load_holidays():
    """تاریخ‌های تعطیلی رسمی/استثنایی اعلام‌شده را از فایل محلی می‌خواند.
    قالب تاریخ‌ها: YYYYMMDD؛ این فایل باید با اطلاعیه‌های رسمی به‌روز شود.
    """
    try:
        payload = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
        dates = payload.get("closed_dates", [])
        return {str(value).strip() for value in dates}
    except FileNotFoundError:
        return set()
    except (json.JSONDecodeError, OSError, AttributeError, TypeError):
        # در صورت خرابی تقویم، وضعیت را با فرض روز کاری محاسبه می‌کنیم؛
        # هشدار در خروجی درج می‌شود تا این فرض پنهان نماند.
        return None


def get_market_status(now=None):
    now = now or datetime.now(TEHRAN)
    local = now.astimezone(TEHRAN)
    t = local.time().replace(tzinfo=None)
    day = local.strftime("%Y%m%d")
    holidays = load_holidays()

    if local.weekday() not in TRADING_WEEKDAYS:
        status = "closed_weekend"
        label = "تعطیل هفتگی"
    elif holidays is not None and day in holidays:
        status = "closed_holiday"
        label = "تعطیل رسمی/استثنایی طبق تقویم ثبت‌شده"
    elif t < PRE_MARKET_START:
        status = "before_market"
        label = "قبل از پیش‌گشایش"
    elif t < MARKET_OPEN:
        status = "pre_open"
        label = "پیش‌گشایش"
    elif t < MARKET_CLOSE:
        status = "open"
        label = "ساعت معاملات پیوسته"
    else:
        status = "closed"
        label = "پایان ساعت معاملات"

    calendar_note = (
        "تعطیلات رسمی/استثنایی فقط در صورت ثبت تاریخ در data/market_holidays.json شناسایی می‌شوند."
        if holidays is not None else
        "هشدار: فایل تقویم تعطیلات خوانده نشد؛ وضعیت تعطیلی رسمی قابل تأیید نیست."
    )

    return {
        "status": status,
        "label_fa": label,
        "timezone": "Asia/Tehran",
        "checked_at": local.isoformat(),
        "scheduled_pre_open": "11:45",
        "scheduled_open": "12:00",
        "scheduled_close": "18:00",
        "calendar_note": calendar_note,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_market_status(), ensure_ascii=False, indent=2))
