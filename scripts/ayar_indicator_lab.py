import json
import math
from pathlib import Path
from statistics import stdev
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OOS_START = "20240101"
INITIAL_CAPITAL_TOMAN = 100_000_000
FEE_PER_SIDE = 0.0015
SLIPPAGE_PER_SIDE = 0.0010
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
    avg_gain, avg_loss = sum(gains[:period]) / period, sum(losses[:period]) / period

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
    out, rolling = [None] * len(values), 0.0
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
    signal, j = [None] * len(values), 0
    for i, x in enumerate(line):
        if x is not None:
            if sig_valid[j] is not None:
                signal[i] = sig_valid[j]
            j += 1
    return line, signal


def _metrics(equity_curve, dates, trades, start_capital, max_dd_pct, max_dd_toman, exposure, sessions):
    ending = equity_curve[-1]["equity_toman"] if equity_curve else start_capital
    total_return = ending / start_capital - 1
    years = sessions / 252 if sessions else 0
    cagr = (ending / start_capital) ** (1 / years) - 1 if years and ending > 0 else None
    trade_pnls = [t["net_pnl_toman"] for t in trades]
    wins = [x for x in trade_pnls if x > 0]
    losses = [x for x in trade_pnls if x <= 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    returns = []
    prev = start_capital
    for point in equity_curve:
        returns.append(point["equity_toman"] / prev - 1 if prev else 0)
        prev = point["equity_toman"]
    vol = stdev(returns) if len(returns) > 1 else 0
    avg = sum(returns) / len(returns) if returns else 0
    streak = worst_streak = 0
    for pnl in trade_pnls:
        streak = streak + 1 if pnl <= 0 else 0
        worst_streak = max(worst_streak, streak)
    return {
        "starting_capital_toman": round(start_capital, 2),
        "ending_capital_toman": round(ending, 2),
        "net_pnl_toman": round(ending - start_capital, 2),
        "total_return_pct": round(total_return * 100, 4),
        "cagr_pct": round(cagr * 100, 4) if cagr is not None else None,
        "max_drawdown_pct": round(max_dd_pct * 100, 4),
        "max_drawdown_toman": round(max_dd_toman, 2),
        "round_trips": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else (None if not gross_profit else "infinite"),
        "average_trade_pnl_toman": round(sum(trade_pnls) / len(trades), 2) if trades else None,
        "average_win_toman": round(sum(wins) / len(wins), 2) if wins else None,
        "average_loss_toman": round(sum(losses) / len(losses), 2) if losses else None,
        "largest_win_toman": round(max(wins), 2) if wins else None,
        "largest_loss_toman": round(min(losses), 2) if losses else None,
        "max_consecutive_losses": worst_streak,
        "exposure_pct": round(exposure / sessions * 100, 2) if sessions else None,
        "sharpe_ratio_rf0": round(avg / vol * math.sqrt(252), 4) if vol else None,
        "turnover_toman": round(sum(t["buy_value_toman"] + t["sell_value_toman"] for t in trades), 2),
    }


def simulate_long_cash(rows, positions, start_index, initial_capital=INITIAL_CAPITAL_TOMAN,
                       fee=FEE_PER_SIDE, slippage=SLIPPAGE_PER_SIDE, force_liquidate=True):
    """Signal at prior close; execute at next open, or next close if open is unavailable.
    Prices in source are rial; portfolio capital and P&L are toman. Integer fund units only.
    """
    cash, units, entry = float(initial_capital), 0, None
    trades, curve = [], []
    peak, max_dd_pct, max_dd_toman = initial_capital, 0.0, 0.0
    exposure = 0
    for i in range(start_index + 1, len(rows)):
        row, prev_row = rows[i], rows[i - 1]
        close = float(row["close"]) / 10.0
        prev_close = float(prev_row["close"]) / 10.0
        raw_open = float(row.get("open") or 0)
        use_open = raw_open > 0
        market_exec = raw_open / 10.0 if use_open else close
        desired = int(bool(positions[i - 1]))
        # Overnight mark on existing units before an open execution.
        if units and use_open:
            cash += 0  # value is marked through the updated reference price below
        if desired and units == 0:
            exec_price = market_exec * (1 + slippage)
            qty = int(cash // (exec_price * (1 + fee)))
            if qty > 0:
                buy_value = qty * exec_price
                buy_fee = buy_value * fee
                cash -= buy_value + buy_fee
                entry = {"date": row["date"], "price_toman": exec_price, "quantity": qty,
                         "buy_value_toman": buy_value, "buy_fee_toman": buy_fee,
                         "entry_equity_toman": cash + qty * close}
                units = qty
        elif not desired and units > 0:
            exec_price = market_exec * (1 - slippage)
            sell_value = units * exec_price
            sell_fee = sell_value * fee
            cash += sell_value - sell_fee
            net_pnl = (sell_value - sell_fee) - (entry["buy_value_toman"] + entry["buy_fee_toman"])
            trades.append({
                "entry_date": entry["date"], "exit_date": row["date"],
                "entry_price_toman": round(entry["price_toman"], 4),
                "exit_price_toman": round(exec_price, 4), "quantity": units,
                "buy_value_toman": round(entry["buy_value_toman"], 2),
                "sell_value_toman": round(sell_value, 2),
                "fees_toman": round(entry["buy_fee_toman"] + sell_fee, 2),
                "net_pnl_toman": round(net_pnl, 2),
                "return_pct": round(net_pnl / (entry["buy_value_toman"] + entry["buy_fee_toman"]) * 100, 4),
                "holding_sessions": i - next(j for j in range(start_index + 1, i + 1) if rows[j]["date"] == entry["date"])
            })
            units, entry = 0, None
        if units:
            exposure += 1
        equity = cash + units * close
        peak = max(peak, equity)
        dd = equity / peak - 1 if peak else 0
        max_dd_pct = min(max_dd_pct, dd)
        max_dd_toman = max(max_dd_toman, peak - equity)
        curve.append({"date": row["date"], "equity_toman": round(equity, 2)})
    if force_liquidate and units and rows:
        row = rows[-1]
        exec_price = (float(row["close"]) / 10.0) * (1 - slippage)
        sell_value, sell_fee = units * exec_price, units * exec_price * fee
        cash += sell_value - sell_fee
        net_pnl = (sell_value - sell_fee) - (entry["buy_value_toman"] + entry["buy_fee_toman"])
        trades.append({
            "entry_date": entry["date"], "exit_date": row["date"],
            "entry_price_toman": round(entry["price_toman"], 4), "exit_price_toman": round(exec_price, 4),
            "quantity": units, "buy_value_toman": round(entry["buy_value_toman"], 2),
            "sell_value_toman": round(sell_value, 2),
            "fees_toman": round(entry["buy_fee_toman"] + sell_fee, 2),
            "net_pnl_toman": round(net_pnl, 2),
            "return_pct": round(net_pnl / (entry["buy_value_toman"] + entry["buy_fee_toman"]) * 100, 4),
            "holding_sessions": sum(1 for r in rows if entry["date"] <= r["date"] <= row["date"]) - 1
        })
        if curve:
            curve[-1]["equity_toman"] = round(cash, 2)
    metrics = _metrics(curve, [x["date"] for x in rows], trades, initial_capital,
                       max_dd_pct, max_dd_toman, exposure, len(curve))
    return {"metrics": metrics, "trades": trades, "equity_curve": curve}


def build_report(history):
    rows = sorted((x for x in history if float(x.get("close") or 0) > 0), key=lambda x: x["date"])
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
    rsi3070, state = [], 0
    for value in r14:
        if value is not None and value < 30:
            state = 1
        elif value is not None and value > 70:
            state = 0
        rsi3070.append(state)
    positions["rsi30_70_hold"] = rsi3070

    full_start = WARMUP
    oos_start = next((i for i, d in enumerate(dates) if d >= OOS_START), None)
    if oos_start is None:
        raise ValueError("OOS period from 2024 is missing")
    strategy_results = {}
    def compact_curve(result, every=5):
        points = result["equity_curve"]
        sampled = points[::every]
        if points and (not sampled or sampled[-1]["date"] != points[-1]["date"]):
            sampled.append(points[-1])
        result["equity_curve"] = sampled
        return result

    for key, signal in positions.items():
        full = simulate_long_cash(rows, signal, full_start)
        oos = compact_curve(simulate_long_cash(rows, signal, max(full_start, oos_start)))
        strategy_results[key] = {
            "full_sample": {"metrics": full["metrics"]},
            "out_of_sample_from_2024": oos
        }

    benchmark_signal = [1] * len(rows)
    full_benchmark = simulate_long_cash(rows, benchmark_signal, full_start)
    oos_benchmark = compact_curve(simulate_long_cash(rows, benchmark_signal, max(full_start, oos_start)))
    strategy_results["buy_and_hold"] = {
        "full_sample": {"metrics": full_benchmark["metrics"]},
        "out_of_sample_from_2024": oos_benchmark
    }

    volume_stats = {}
    for label, predicate in (
        ("above_sma20", lambda i: vma[i] is not None and volumes[i] > vma[i]),
        ("at_or_below_sma20", lambda i: vma[i] is not None and volumes[i] <= vma[i]),
    ):
        for period, first in (("full_sample", full_start), ("out_of_sample_from_2024", max(full_start, oos_start))):
            samples = [closes[i + 10] / closes[i] - 1 for i in range(first, len(closes) - 10) if predicate(i)]
            volume_stats[f"{label}_{period}"] = {
                "samples": len(samples),
                "positive_10d_rate_pct": round(sum(x > 0 for x in samples) / len(samples) * 100, 2) if samples else None,
                "average_10d_return_pct": round(sum(samples) / len(samples) * 100, 4) if samples else None
            }

    return {
        "model_version": "2.0.0",
        "generated_at": datetime.now(ZoneInfo("Asia/Tehran")).isoformat(),
        "history": {"valid_sessions": len(rows), "first_date": dates[0], "last_date": dates[-1], "warmup_sessions": WARMUP},
        "assumptions": {
            "starting_capital_toman": INITIAL_CAPITAL_TOMAN,
            "fee_per_side_pct": FEE_PER_SIDE * 100,
            "slippage_per_side_pct": SLIPPAGE_PER_SIDE * 100,
            "execution": "signal at prior close; execute at next session open; if open is missing/zero, execute at next close as a conservative timing proxy",
            "price_unit_conversion": "source prices are rial; portfolio accounting is toman (rial / 10)",
            "position_sizing": "long/cash, integer fund units, all available cash, no leverage",
            "end_of_period": "any open position is liquidated at the final close and exit costs are charged",
            "oos_start": OOS_START,
            "annualization_sessions": 252
        },
        "strategies": strategy_results,
        "volume_filter": volume_stats,
        "limitations": [
            "Starting capital of 100 million toman is an illustrative assumption and can be changed; it is not a recommendation.",
            "Commission and slippage are estimates, not verified current broker/fund tariffs; results should be rerun with the user's actual costs.",
            "The model assumes integer fund units, all-in long-only positions, no leverage, no tax, no order queue, no partial fills and no liquidity/market-impact constraints.",
            "When next-session open is absent or zero, next-session close is used as an execution proxy; this is less realistic than tick-level execution.",
            "Signals are generated from daily closing prices and executed no earlier than the next session.",
            "A strategy and buy-and-hold each start independently in cash at the beginning of each test period; inherited positions are not carried into OOS.",
            "Open positions are liquidated at the end of the test period and the exit fee/slippage are included.",
            "OOS from 2024 covers one particular market regime and does not establish future predictive power; avoid selecting a strategy solely by historical CAGR."
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
        m = item["out_of_sample_from_2024"]["metrics"]
        print(name, "OOS P&L toman", m["net_pnl_toman"], "return", m["total_return_pct"],
              "maxDD", m["max_drawdown_pct"], "round trips", m["round_trips"],
              "win rate", m["win_rate_pct"], "PF", m["profit_factor"])


if __name__ == "__main__":
    main()
