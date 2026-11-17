
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")

DATA_DIR = Path("data")
INPUT_FILE = DATA_DIR / "ayar_daily_history.json"
OUTPUT_FILE = DATA_DIR / "ayar_indicators.json"


def ema(values, period):
    if len(values) < period:
        raise ValueError(f"داده کافی برای EMA{period} وجود ندارد")

    multiplier = 2 / (period + 1)
    result = [None] * (period - 1)

    current = sum(values[:period]) / period
    result.append(current)

    for price in values[period:]:
        current = (price - current) * multiplier + current
        result.append(current)

    return result


def rsi(values, period=14):
    if len(values) <= period:
        raise ValueError("داده کافی برای RSI وجود ندارد")

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result = [None] * period

    def calculate(gain, loss):
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    result.append(calculate(avg_gain, avg_loss))

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

        result.append(calculate(avg_gain, avg_loss))

    return result


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError("فایل تاریخچه عیار پیدا نشد")

    source = json.loads(INPUT_FILE.read_text(encoding="utf-8"))

    history = source.get("valid_history")

    if not isinstance(history, list) or len(history) < 200:
        raise ValueError("تاریخچه معتبر برای محاسبه کافی نیست")

    dates = []
    closes = []

    for item in history:
        date = str(item["date"])
        close = int(item["close"])

        if close <= 0:
            raise ValueError(f"قیمت پایانی نامعتبر در تاریخ {date}")

        if dates and date <= dates[-1]:
            raise ValueError("ترتیب تاریخچه نامعتبر یا دارای تاریخ تکراری است")

        dates.append(date)
        closes.append(close)

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    rsi14 = rsi(closes, 14)

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)

    macd_line = []
    signal_input = []

    for fast, slow in zip(ema12, ema26):
        if fast is None or slow is None:
            macd_line.append(None)
        else:
            value = fast - slow
            macd_line.append(value)
            signal_input.append(value)

    signal_values = ema(signal_input, 9)

    macd_signal = [None] * (len(closes) - len(signal_values))
    macd_signal.extend(signal_values)

    macd_histogram = []

    for line, signal in zip(macd_line, macd_signal):
        if line is None or signal is None:
            macd_histogram.append(None)
        else:
            macd_histogram.append(line - signal)

    latest = {
        "date": dates[-1],
        "close": closes[-1],
        "EMA20": round(ema20[-1], 2),
        "EMA50": round(ema50[-1], 2),
        "EMA200": round(ema200[-1], 2),
        "RSI14": round(rsi14[-1], 2),
        "MACD": round(macd_line[-1], 2),
        "MACD_signal": round(macd_signal[-1], 2),
        "MACD_histogram": round(macd_histogram[-1], 2),
    }

    output = {
        "symbol": "عیار",
        "source": "TSETMC",
        "timeframe": "1D",
        "price_basis": "closing_price",
        "timezone": "Asia/Tehran",
        "calculated_at": datetime.now(TEHRAN).isoformat(),
        "record_count": len(history),
        "first_date": dates[0],
        "last_date": dates[-1],
        "latest": latest,
        "indicators": {
            "EMA20": [None if x is None else round(x, 2) for x in ema20],
            "EMA50": [None if x is None else round(x, 2) for x in ema50],
            "EMA200": [None if x is None else round(x, 2) for x in ema200],
            "RSI14": [None if x is None else round(x, 2) for x in rsi14],
            "MACD": [None if x is None else round(x, 2) for x in macd_line],
            "MACD_signal": [None if x is None else round(x, 2) for x in macd_signal],
            "MACD_histogram": [
                None if x is None else round(x, 2)
                for x in macd_histogram
            ],
        }
    }

    temporary = OUTPUT_FILE.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    temporary.replace(OUTPUT_FILE)

    print("SUCCESS:", OUTPUT_FILE)
    print("Records:", len(history))
    print("Latest indicators:")
    print(json.dumps(latest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
      
