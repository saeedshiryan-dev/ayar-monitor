import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from ayar_market_status import get_market_status

BASE = "https://cdn.tsetmc.com/api"
INS_CODE = "34144395039913458"
TEHRAN = ZoneInfo("Asia/Tehran")
OUT = Path("data/ayar_live.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
    "Origin": "https://www.tsetmc.com",
})

def get_json(url):
    last_error = None
    for attempt in range(4):
        try:
            response = session.get(url, timeout=(15, 45))
            if response.status_code in (429, 500, 502, 503, 504):
                response.raise_for_status()
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise ValueError(f"پاسخ غیر JSON از TSETMC: {url}") from exc
            if not isinstance(data, (dict, list)):
                raise ValueError(f"ساختار پاسخ نامعتبر از TSETMC: {url}")
            return data
        except requests.RequestException as error:
            last_error = error
            if attempt == 3:
                break
            time.sleep(2 ** attempt)
    raise last_error

def unwrap(payload, key):
    value = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    return None

def as_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0

def format_time(raw):
    value = f"{as_int(raw):06d}"
    return f"{value[0:2]}:{value[2:4]}:{value[4:6]}" if value != "000000" else None

def main():
    now = datetime.now(TEHRAN)
    today = now.strftime("%Y%m%d")
    market = get_market_status(now)

    # منبع اصلی مظنه زنده: snapshot رسمی اطلاعات قیمت نماد
    quote_payload = get_json(f"{BASE}/ClosingPrice/GetClosingPriceInfo/{INS_CODE}")
    quote = unwrap(quote_payload, "closingPriceInfo")
    if not isinstance(quote, dict):
        raise ValueError("فیلد closingPriceInfo در پاسخ TSETMC وجود ندارد")

    # تاریخچه روزانه برای بازیابی آخرین جلسه واقعی، نه فرض کردن روز تقویمی جاری
    daily_payload = get_json(f"{BASE}/ClosingPrice/GetClosingPriceDailyList/{INS_CODE}/0")
    daily_rows = unwrap(daily_payload, "closingPriceDaily")
    if not isinstance(daily_rows, list):
        raise ValueError("فیلد closingPriceDaily در پاسخ TSETMC وجود ندارد")
    daily_rows = [r for r in daily_rows if isinstance(r, dict) and str(r.get("dEven", "")).isdigit()]
    daily_rows.sort(key=lambda r: str(r["dEven"]))

    quote_date = str(as_int(quote.get("dEven") or quote.get("finalLastDate")))
    quote_trades = as_int(quote.get("zTotTran"))
    quote_last = as_int(quote.get("pDrCotVal"))
    quote_close = as_int(quote.get("pClosing"))

    # فقط وقتی snapshot واقعاً متعلق به امروز و دارای معامله است، قیمت لحظه‌ای می‌دهیم.
    quote_is_today = quote_date == today
    live_price = quote_last if quote_is_today and quote_trades > 0 and quote_last > 0 else None
    last_trade_time = format_time(quote.get("lastHEven") or quote.get("hEven")) if live_price else None

    # fallback کنترل‌شده: API معاملات روز جاری. این مسیر به تنهایی مجوز انتساب
    # معامله به امروز نیست؛ تاریخ snapshot باید با امروز تطبیق داشته باشد.
    trade_rows = []
    trade_error = None
    try:
        trade_payload = get_json(f"{BASE}/Trade/GetTrade/{INS_CODE}")
        candidate = unwrap(trade_payload, "trade")
        if isinstance(candidate, list):
            trade_rows = [r for r in candidate if isinstance(r, dict)]
    except Exception as exc:
        trade_error = str(exc)[:300]

    # NAV رسمی ETF از endpoint اختصاصی صندوق در TSETMC.
    # NAV ابطال/صدور بر حسب ریال است؛ تاریخ و ساعت انتشار را جدا نگه می‌داریم.
    nav = {
        "status": "unavailable",
        "source": "TSETMC Fund/GetETFByInsCode",
        "unit": "ریال",
        "redemption": None,
        "subscription": None,
        "published_date": None,
        "published_time": None,
        "retrieved_at": now.isoformat(),
        "market_price_basis": None,
        "premium_discount_percent": None,
        "cross_check_status": "not_available",
        "error": None,
    }
    etf_nav = None
    etf_error = None
    try:
        etf_payload = get_json(f"{BASE}/Fund/GetETFByInsCode/{INS_CODE}")
        etf_value = unwrap(etf_payload, "etf")
        if isinstance(etf_value, list):
            etf_rows = [x for x in etf_value if isinstance(x, dict)]
            etf_nav = max(etf_rows, key=lambda x: (as_int(x.get("deven")), as_int(x.get("hEven")))) if etf_rows else None
        elif isinstance(etf_value, dict):
            etf_nav = etf_value
        if not isinstance(etf_nav, dict):
            raise ValueError("فیلد etf در پاسخ TSETMC موجود نیست")
    except Exception as exc:
        etf_error = str(exc)[:300]

    nav_redemption = as_int(etf_nav.get("pRedTran")) if etf_nav else 0
    nav_subscription = as_int(etf_nav.get("pSubTran")) if etf_nav else 0
    nav_date = as_int(etf_nav.get("deven")) if etf_nav else 0
    nav_time = as_int(etf_nav.get("hEven")) if etf_nav else 0
    quote_redemption = as_int(quote.get("pRedTran"))
    quote_subscription = as_int(quote.get("pSubTran"))

    # Fallback فقط به فیلد NAV همان snapshot رسمی قیمت؛ منبع fallback صریحاً ثبت می‌شود.
    if nav_redemption > 0:
        nav["status"] = "ok"
        nav["redemption"] = nav_redemption
        nav["subscription"] = nav_subscription or None
        nav["published_date"] = str(nav_date) if nav_date else quote_date
        nav["published_time"] = format_time(nav_time) if nav_time else None
        nav["cross_check_status"] = "matched" if quote_redemption > 0 and quote_redemption == nav_redemption else (
            "different_snapshot_values" if quote_redemption > 0 else "quote_nav_unavailable"
        )
    elif quote_redemption > 0:
        nav["status"] = "fallback_quote_snapshot"
        nav["source"] = "TSETMC ClosingPrice/GetClosingPriceInfo.pRedTran"
        nav["redemption"] = quote_redemption
        nav["subscription"] = quote_subscription or None
        nav["published_date"] = quote_date
        nav["published_time"] = format_time(quote.get("hEven"))
        nav["cross_check_status"] = "etf_endpoint_unavailable"
        nav["error"] = etf_error
    else:
        nav["status"] = "unavailable"
        nav["cross_check_status"] = "both_sources_unavailable"
        nav["error"] = etf_error or "NAV ابطال معتبر از دو فیلد رسمی TSETMC دریافت نشد"

    # حباب/تخفیف با آخرین NAV ابطال رسمی منتشرشده محاسبه می‌شود؛ زمان NAV جداگانه نمایش داده می‌شود.
    comparison_price = live_price or (quote_last if quote_last > 0 else None)
    if nav["redemption"] and comparison_price:
        nav["market_price_basis"] = "live_price" if live_price else "last_official_price"
        nav["premium_discount_percent"] = round(
            (comparison_price / nav["redemption"] - 1) * 100, 4
        )

    # دریافت دفتر سفارش زنده (۵ سطح خرید و فروش) از BestLimits رسمی TSETMC
    orderbook = {
        "status": "unavailable",
        "source": "TSETMC BestLimits",
        "retrieved_at": now.isoformat(),
        "unit": "ریال",
        "buy": [],
        "sell": [],
        "buy_total_volume": 0,
        "sell_total_volume": 0,
        "buy_total_orders": 0,
        "sell_total_orders": 0,
        "note_fa": None,
        "error": None,
    }
    try:
        book_payload = get_json(f"{BASE}/BestLimits/{INS_CODE}")
        raw_levels = unwrap(book_payload, "bestLimits")
        if not isinstance(raw_levels, list):
            raise ValueError("فیلد bestLimits در پاسخ TSETMC وجود ندارد یا آرایه نیست")

        # ترتیب سطوح مطابق number (۱ بهترین سطح)؛ فیلدهای رسمی Dem=خرید و Of=فروش
        valid_levels = [r for r in raw_levels if isinstance(r, dict)]
        valid_levels.sort(key=lambda r: as_int(r.get("number")) or 999)
        for level in valid_levels[:5]:
            buy = {
                "level": as_int(level.get("number")),
                "price": as_int(level.get("pMeDem") or level.get("pd")),
                "volume": as_int(level.get("qTitMeDem") or level.get("qd")),
                "orders": as_int(level.get("zOrdMeDem") or level.get("zd")),
            }
            sell = {
                "level": as_int(level.get("number")),
                "price": as_int(level.get("pMeOf") or level.get("po")),
                "volume": as_int(level.get("qTitMeOf") or level.get("qo")),
                "orders": as_int(level.get("zOrdMeOf") or level.get("zo")),
            }
            if buy["price"] > 0 or buy["volume"] > 0 or buy["orders"] > 0:
                orderbook["buy"].append(buy)
            if sell["price"] > 0 or sell["volume"] > 0 or sell["orders"] > 0:
                orderbook["sell"].append(sell)

        orderbook["buy_total_volume"] = sum(x["volume"] for x in orderbook["buy"])
        orderbook["sell_total_volume"] = sum(x["volume"] for x in orderbook["sell"])
        orderbook["buy_total_orders"] = sum(x["orders"] for x in orderbook["buy"])
        orderbook["sell_total_orders"] = sum(x["orders"] for x in orderbook["sell"])
        if orderbook["buy"] or orderbook["sell"]:
            orderbook["status"] = "ok"
            orderbook["note_fa"] = "۵ سطح برتر دفتر سفارش از TSETMC دریافت شد؛ قیمت‌ها ریال هستند."
        else:
            orderbook["status"] = "empty"
            orderbook["note_fa"] = "TSETMC پاسخ داد اما در حال حاضر ردیف سفارش فعالی برنگرداند."
    except Exception as exc:
        orderbook["status"] = "error"
        orderbook["error"] = str(exc)[:300]
        orderbook["note_fa"] = "دریافت دفتر سفارش ناموفق بود؛ اطلاعات قیمت مستقل از دفتر سفارش ذخیره شد."

    # انتخاب آخرین جلسه از تاریخچه روزانه و مقایسه با snapshot
    last_session = daily_rows[-1] if daily_rows else None
    if quote_is_today:
        session_date = today
        closing_price = quote_close or (as_int(last_session.get("pClosing")) if last_session else 0)
        official_last = quote_last
    else:
        session_date = str(last_session.get("dEven")) if last_session else quote_date
        closing_price = as_int(last_session.get("pClosing")) if last_session else quote_close
        official_last = as_int(last_session.get("pDrCotVal")) if last_session else quote_last

    # اگر API قیمت زنده تاریخ امروز را نشان نداد، آن را به عنوان آخرین جلسه قبلی ثبت می‌کنیم.
    result = {
        "symbol": "عیار",
        "insCode": INS_CODE,
        "source": "TSETMC",
        "source_api": "cdn.tsetmc.com/api",
        "retrieved_at": now.isoformat(),
        "timezone": "Asia/Tehran",
        "market": market,
        "session_date": session_date,
        "today_has_trade": bool(live_price),
        "live_price": live_price,
        "live_price_unit": "ریال",
        "closing_price": closing_price or None,
        "last_trade_price": live_price or (official_last if official_last > 0 else None),
        "last_trade_time": last_trade_time,
        "last_trade_volume": None,
        "official_daily_last_price": official_last if official_last > 0 else None,
        "tsetmc_quote_date": quote_date,
        "tsetmc_quote_trade_count": quote_trades,
        "tsetmc_quote_total_volume": as_int(quote.get("qTotTran5J")),
        "trade_endpoint_rows": len(trade_rows),
        "trade_endpoint_error": trade_error,
        "orderbook": orderbook,
        "nav": nav,
        "price_note_fa": (
            "قیمت لحظه‌ای از snapshot قیمت TSETMC دریافت شد."
            if live_price is not None else
            "snapshot قیمت TSETMC هنوز تاریخ امروز یا تعداد معامله مثبت ندارد؛ قیمت لحظه‌ای عمداً خالی گذاشته شده و آخرین جلسه واقعی گزارش می‌شود."
        ),
    }

    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUT)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
