import json
import math
from pathlib import Path
from statistics import stdev
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OOS_START = "20240101"
FEE = 0.0015
WARMUP = 200


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        current += alpha * (values[i] - current)
        out[i] = current
    return out


def rsi(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(values[i] - values[i - 1], 0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    def value(g, l):
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / l)
    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def sma(values, period):
    out = [None] * len(values)
    rolling = 0.0
    for i, value in enumerate(values):
        rolling += value
        if i >= period:
            rolling -= values[i - period]
        if i >= period - 1:
            out[i] = rolling / period
    return out


def macd(values):
    fast, slow = ema(values, 12), ema(values, 26)
    line = [None if fast[i] is None or slow[i] is None else fast[i] - slow[i] for i in range(len(values))]
    valid = [x for x in line if x is not None]
    sig_valid = ema(valid, 9)
    signal = [None] * len(values)
    j = 0
    for i, x in enumerate(line):
        if x is not None:
            if sig_valid[j] is not None:
                signal[i] = sig_valid[j]
            j += 1
    return line, signal


def performance(returns, positions, dates, closes, start_i, fee=FEE):
    equity = peak = 1.0
    max_dd = 0.0
    equity_curve = []
    trade_returns = []
    entry_equity = None
    changes = 0
    exposure = 0
    net_returns = []
    for i in range(start_i, len(closes)):
        signal = int(positions[i - 1])
        prior = 0 if i == WARMUP + 1 else (int(positions[i - 2]) if i >= 2 else 0)
        change = abs(signal - prior)
        raw = closes[i] / closes[i - 1] - 1
        final_exit = int(i == len(closes) - 1 and signal == 1)
        gross = 1 + raw if signal else 1.0
        net = gross * ((1 - fee) ** (change + final_exit)) - 1
        if change:
            changes += 1
            if signal:
                entry_equity = equity * (1 - fee)
            elif entry_equity is not None:
                trade_returns.append((equity * (1 - fee)) / entry_equity - 1)
                entry_equity = None
        if final_exit and entry_equity is not None:
            trade_returns.append((equity * (1 + raw) * (1 - fee)) / entry_equity - 1)
            entry_equity = None
        equity *= 1 + net
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
        equity_curve.append(equity)
        net_returns.append(net)
        exposure += signal
    n = len(net_returns)
    avg = sum(net_returns) / n if n else 0
    vol = stdev(net_returns) if n > 1 else 0
    years = n / 252 if n else 0
    cagr = equity ** (1 / years) - 1 if years > 0 and equity > 0 else None
    wins = [x for x in trade_returns if x > 0]
    losses = [x for x in trade_returns if x <= 0]
    return {
        "sessions": n,
        "total_return_pct": round((equity - 1) * 100, 4),
        "cagr_pct": round(cagr * 100, 4) if cagr is not None else None,
        "sharpe_ratio_rf0": round(avg / vol * math.sqrt(252), 4) if vol else None,
        "max_drawdown_pct": round(max_dd * 100, 4),
        "position_changes": changes,
        "completed_round_trips": len(trade_returns),
        "exposure_pct": round(exposure / n * 100, 2) if n else None,
        "trade_win_rate_pct": round(len(wins) / len(trade_returns) * 100, 2) if trade_returns else None,
        "average_trade_return_pct": round(sum(trade_returns) / len(trade_returns) * 100, 4) if trade_returns else None,
        "average_winning_trade_pct": round(sum(wins) / len(wins) * 100, 4) if wins else None,
        "average_losing_trade_pct": round(sum(losses) / len(losses) * 100, 4) if losses else None,
    }


def build_report(history):
    rows = sorted(history, key=lambda x: x["date"])
    dates = [str(x["date"]) for x in rows]
    closes = [float(x["close"]) for x in rows]
    volumes = [float(x.get("volume", 0)) for x in rows]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    r14 = rsi(closes, 14)
    mline, msig = macd(closes)
    vma = sma(volumes, 20)
    positions = {
        "ema20_50": [int(a is not None and b is not None and a > b) for a, b in zip(e20, e50)],
        "price_ema200": [int(a is not None and c > a) for a, c in zip(e200, closes)],
        "rsi14_above50": [int(a is not None and a > 50) for a in r14],
        "macd_above_signal": [int(a is not None and b is not None and a > b) for a, b in zip(mline, msig)],
        "breakout_20": [int(i >= 20 and closes[i] > max(closes[i-20:i])) for i in range(len(closes))],
    }
    rsi3070 = []
    state = 0
    for value in r14:
        if value is not None and value < 30:
            state = 1
        elif value is not None and value > 70:
            state = 0
        rsi3070.append(state)
    positions["rsi30_70_hold"] = rsi3070

    full_start = WARMUP + 1
    oos_start = next((i for i, d in enumerate(dates) if d >= OOS_START), None)
    result = {}
    for key, pos in positions.items():
        result[key] = {
            "full_sample": performance(pos, pos, dates, closes, full_start),
            "out_of_sample_from_2024": performance(pos, pos, dates, closes, max(full_start, oos_start)) if oos_start is not None else None,
        }
    # Volume is a descriptive filter, not a standalone long/cash strategy.
    volume_stats = {}
    for label, predicate in (
        ("above_sma20", lambda i: vma[i] is not None and volumes[i] > vma[i]),
        ("at_or_below_sma20", lambda i: vma[i] is not None and volumes[i] <= vma[i]),
    ):
        for period, first in (("full_sample", full_start), ("out_of_sample_from_2024", max(full_start, oos_start or full_start))):
            samples = []
            for i in range(first, len(closes) - 10):
                if predicate(i):
                    samples.append(closes[i + 10] / closes[i] - 1)
            volume_stats[f"{label}_{period}"] = {
                "samples": len(samples),
                "positive_10d_rate_pct": round(sum(x > 0 for x in samples) / len(samples) * 100, 2) if samples else None,
                "average_10d_return_pct": round(sum(samples) / len(samples) * 100, 4) if samples else None,
            }
    benchmark = [1] * len(closes)
    result["buy_and_hold"] = {
        "full_sample": performance(benchmark, benchmark, dates, closes, full_start),
        "out_of_sample_from_2024": performance(benchmark, benchmark, dates, closes, max(full_start, oos_start)) if oos_start is not None else None,
    }
    return {
        "model_version": "1.0.0",
        "generated_at": datetime.now(ZoneInfo("Asia/Tehran")).isoformat(),
        "history": {"valid_sessions": len(rows), "first_date": dates[0], "last_date": dates[-1], "warmup_sessions": WARMUP},
        "assumptions": {
            "fee_per_side_pct": FEE * 100,
            "signal_timing": "signal from prior close; position held for next close-to-close return",
            "oos_start": OOS_START,
            "annualization_sessions": 252,
        },
        "strategies": result,
        "volume_filter": volume_stats,
        "limitations": [
            "These are independent, simple indicator rules, not an optimized combined model.",
            "Signals are formed from the previous close; the next close-to-close return is measured to avoid look-ahead.",
            "Average trade returns include assumed entry and exit fees; incomplete open trades at period end are not included.",
            "The 0.15% fee per side is an assumption and must be replaced by verified fund and broker costs.",
            "OOS from 2024 contains one market regime and is not proof of durable predictive power.",
            "Overlapping forward-return samples are not independent observations; hit rates are descriptive, not statistical significance tests.",
            "Buy-and-hold and strategy results are sensitive to the selected start date and market regime."
        ]
    }


def main():
    source = json.loads((DATA / "ayar_daily_history.json").read_text(encoding="utf-8"))
    if source.get("validated") is not True:
        raise ValueError("Validated daily history is missing")
    history = source.get("valid_history")
    if not isinstance(history, list) or len(history) < 500:
        raise ValueError("Insufficient validated daily history")
    report = build_report(history)
    target = DATA / "ayar_indicator_lab.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    print("INDICATOR_LAB_SUCCESS", report["history"], "strategies:", len(report["strategies"]))
    for name, item in report["strategies"].items():
        if name != "buy_and_hold":
            s = item["out_of_sample_from_2024"]
            print(name, "OOS CAGR", s["cagr_pct"], "maxDD", s["max_drawdown_pct"], "avg trade", s["average_trade_return_pct"], "trades", s["completed_round_trips"])


if __name__ == "__main__":
    main()
