from datetime import datetime, time
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")
# صندوق‌های کالایی طلا: شنبه تا چهارشنبه، 12:00 تا 18:00
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}
MARKET_OPEN = time(12, 0)
MARKET_CLOSE = time(18, 0)

def get_market_status(now=None):
    now = now or datetime.now(TEHRAN)
    local = now.astimezone(TEHRAN)
    t = local.time().replace(tzinfo=None)

    if local.weekday() not in TRADING_WEEKDAYS:
        status = "closed_weekend"
        label = "تعطیل هفتگی"
    elif t < MARKET_OPEN:
        status = "before_market"
        label = "قبل از شروع معاملات"
    elif t < MARKET_CLOSE:
        status = "open"
        label = "ساعت معاملات (مشروط به تعطیل نبودن رسمی بازار)"
    else:
        status = "closed"
        label = "پایان ساعت معاملات"

    return {
        "status": status,
        "label_fa": label,
        "timezone": "Asia/Tehran",
        "checked_at": local.isoformat(),
        "scheduled_open": "12:00",
        "scheduled_close": "18:00",
        "calendar_note": "تقویم تعطیلات رسمی و اطلاعیه‌های استثنایی باید جداگانه کنترل شود."
    }

if __name__ == "__main__":
    import json
    print(json.dumps(get_market_status(), ensure_ascii=False, indent=2))
