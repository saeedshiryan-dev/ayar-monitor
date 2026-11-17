import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")
DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "intraday"
OUTPUT_FILE = DATA_DIR / "ayar_intraday_indicators.json"


def ema(values, period):
    if len(values) < period:
        raise ValueError(f"داده کافی برای EMA{period}: {len(values)}")
    alpha = 2 / (period + 1)
    result = [None] * (period - 1)
    current = sum(values[:period]) / period
    result.append(current)
    for value in values[period:]:
        current = (value - current) * alpha + current
        result.append(current)
    return result


def rsi(values, period=14):
    if len(values) <= period:
        raise ValueError("داده کافی برای RSI14 وجود ندارد")
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain, loss):
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100 - 100 / (1 + rs)

    result = [None] * period
    result.append(value(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.append(value(avg_gain, avg_loss))
    return result


def sma(values, period):
    result = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def build_timeframe(candles, timeframe):
    candles.sort(key=lambda x: (x["date"], x["start"]))
    if len(candles) < 200:
        raise ValueError(f"برای {timeframe} فقط {len(candles)} کندل موجود است؛ حداقل ۲۰۰ لازم است")

    dates = [x["date"] + " " + x["start"] for x in candles]
    closes = [float(x["close"]) for x in candles]
    volumes = [int(x["volume"]) for x in candles]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    rsi14 = rsi(closes, 14)
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    macd = [
        None if fast is None or slow is None else fast - slow
        for fast, slow in zip(ema12, ema26)
    ]
    valid_macd = [x for x in macd if x is not None]
    signal_valid = ema(valid_macd, 9)
    signal = [None] * (len(macd) - len(signal_valid)) + signal_valid
    histogram = [
        None if line is None or sig is None else line - sig
        for line, sig in zip(macd, signal)
    ]
    vol_sma = sma(volumes, 20)
    vol_ratio = [
        None if avg in (None, 0) else volume / avg
        for volume, avg in zip(volumes, vol_sma)
    ]

    def rounded(values, digits=2):
        return [None if x is None else round(x, digits) for x in values]

    latest = {
        "datetime": dates[-1],
        "open": candles[-1]["open"],
        "high": candles[-1]["high"],
        "low": candles[-1]["low"],
        "close": closes[-1],
        "volume": volumes[-1],
        "trades": candles[-1]["trades"],
        "partial": candles[-1].get("partial", False),
        "EMA20": round(ema20[-1], 2),
        "EMA50": round(ema50[-1], 2),
        "EMA200": round(ema200[-1], 2),
        "RSI14": round(rsi14[-1], 2),
        "MACD": round(macd[-1], 2),
        "MACD_signal": round(signal[-1], 2),
        "MACD_histogram": round(histogram[-1], 2),
        "Volume_SMA20": round(vol_sma[-1], 2),
        "Volume_Ratio": round(vol_ratio[-1], 3) if vol_ratio[-1] is not None else None,
    }
    return {
        "candle_count": len(candles),
        "first_candle": dates[0],
        "last_candle": dates[-1],
        "latest": latest,
        "candles": candles,
        "indicators": {
            "EMA20": rounded(ema20),
            "EMA50": rounded(ema50),
            "EMA200": rounded(ema200),
            "RSI14": rounded(rsi14),
            "MACD": rounded(macd),
            "MACD_signal": rounded(signal),
            "MACD_histogram": rounded(histogram),
            "Volume": volumes,
            "Volume_SMA20": rounded(vol_sma),
            "Volume_Ratio": rounded(vol_ratio, 3),
        },
    }


def main():
    if not CACHE_DIR.exists():
        raise FileNotFoundError("پوشه تاریخچه درون‌روزی پیدا نشد")
    all_1h, all_4h = [], []
    files = sorted(CACHE_DIR.glob("ayar_*.json"))
    for path in files:
        day = json.loads(path.read_text(encoding="utf-8"))
        if day.get("validated") is not True:
            raise ValueError(f"فایل اعتبارسنجی‌نشده: {path.name}")
        all_1h.extend(day.get("candles_1h", []))
        all_4h.extend(day.get("candles_4h", []))

    result = {
        "symbol": "عیار",
        "source": "TSETMC",
        "timezone": "Asia/Tehran",
        "calculated_at": datetime.now(TEHRAN).isoformat(),
        "method": "EMA seeded by SMA; RSI Wilder; MACD 12-26-9; volume SMA20",
        "validation": {
            "daily_trade_reconciliation": "Each cached day passed trade-count, volume, low and high checks",
            "cached_days": len(files),
            "minimum_candles_per_timeframe": 200,
        },
        "timeframes": {
            "1h": build_timeframe(all_1h, "1h"),
            "4h": build_timeframe(all_4h, "4h"),
        },
    }
    temp = OUTPUT_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUTPUT_FILE)
    print("SUCCESS:", OUTPUT_FILE)
    print(json.dumps({
        "cached_days": len(files),
        "1h": result["timeframes"]["1h"]["latest"],
        "4h": result["timeframes"]["4h"]["latest"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
