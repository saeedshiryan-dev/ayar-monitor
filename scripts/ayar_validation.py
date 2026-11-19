import json
import math
import os
from datetime import datetime
from pathlib import Path
from statistics import stdev
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TEHRAN = ZoneInfo("Asia/Tehran")
FEE_PER_SIDE = float(os.getenv("AYAR_FEE_PER_SIDE", "0.0015"))
OOS_START = "20240101"


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ema(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        current += (values[i] - current) * alpha
        out[i] = current
    return out


def summarize(returns, max_drawdown):
    if not returns:
        return {}
    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
    avg = sum(returns) / len(returns)
    sigma = stdev(returns) if len(returns) > 1 else 0.0
    sharpe = (avg / sigma * math.sqrt(252)) if sigma else None
    return {
        "sessions": len(returns),
        "total_return_pct": round((equity - 1.0) * 100, 4),
        "cagr_pct": round((equity ** (252.0 / len(returns)) - 1.0) * 100, 4),
        "annualized_volatility_pct": round(sigma * math.sqrt(252) * 100, 4),
        "sharpe_ratio_rf0": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown_pct": round(max_drawdown * 100, 4),
    }


def run_period(closes, dates, fast, slow, first_i, fee, initial_position=0):
    position = initial_position
    strategy_returns = []
    benchmark_returns = []
    equity = peak = 1.0
    max_dd = 0.0
    benchmark_equity = benchmark_peak = 1.0
    benchmark_dd = 0.0
    trade_entry_equity = None
    closed_trades = []
    transitions = 0

    for i in range(first_i, len(closes)):
        signal = 1 if fast[i - 1] is not None and slow[i - 1] is not None and fast[i - 1] > slow[i - 1] else 0
        change = abs(signal - position)
        raw_return = closes[i] / closes[i - 1] - 1.0
        net_return = (1.0 + raw_return) * ((1.0 - fee) ** change) - 1.0
        if change:
            transitions += 1
            if signal == 1:
                trade_entry_equity = equity * (1.0 - fee)
            elif trade_entry_equity is not None:
                exit_equity = equity * (1.0 + raw_return) * (1.0 - fee)
                closed_trades.append(exit_equity / trade_entry_equity - 1.0)
                trade_entry_equity = None
        equity *= 1.0 + net_return
        position = signal
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1.0)
        strategy_returns.append(net_return)

        benchmark_return = raw_return
        benchmark_equity *= 1.0 + benchmark_return
        benchmark_peak = max(benchmark_peak, benchmark_equity)
        benchmark_dd = min(benchmark_dd, benchmark_equity / benchmark_peak - 1.0)
        benchmark_returns.append(benchmark_return)

    return {
        "strategy": summarize(strategy_returns, max_dd),
        "buy_and_hold": summarize(benchmark_returns, benchmark_dd),
        "position_changes": transitions,
        "completed_round_trips": len(closed_trades),
        "trade_win_rate_pct": round(sum(x > 0 for x in closed_trades) / len(closed_trades) * 100, 2) if closed_trades else None,
        "average_closed_trade_return_pct": round(sum(closed_trades) / len(closed_trades) * 100, 4) if closed_trades else None,
    }


def build_technical(history):
    rows = sorted(history, key=lambda x: x["date"])
    dates = [str(x["date"]) for x in rows]
    closes = [float(x["close"]) for x in rows]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    first_valid = next((i for i in range(len(rows)) if fast[i] is not None and slow[i] is not None), None)
    if first_valid is None or len(rows) - first_valid < 100:
        raise ValueError("Not enough validated daily history for the EMA20/EMA50 test")

    full = run_period(closes, dates, fast, slow, first_valid + 1, FEE_PER_SIDE, 0)
    oos_i = next((i for i in range(first_valid + 1, len(rows)) if dates[i] >= OOS_START), None)
    oos = None
    if oos_i is not None:
        prior_position = 1 if fast[oos_i - 1] > slow[oos_i - 1] else 0
        oos = run_period(closes, dates, fast, slow, oos_i, FEE_PER_SIDE, prior_position)

    signals = []
    for i in range(first_valid, len(rows)):
        bullish = fast[i] > slow[i]
        forward = {}
        for horizon in (1, 5, 10):
            if i + horizon < len(rows):
                forward[f"{horizon}d_return_pct"] = round((closes[i + horizon] / closes[i] - 1.0) * 100, 4)
            else:
                forward[f"{horizon}d_return_pct"] = None
        signals.append({
            "date": dates[i],
            "close": int(closes[i]),
            "ema20": round(fast[i], 4),
            "ema50": round(slow[i], 4),
            "signal": "bullish_hold" if bullish else "cash_avoid",
            "forward_returns": forward,
        })

    horizon_stats = {}
    for horizon in (1, 5, 10):
        key = f"{horizon}d_return_pct"
        ready = [x for x in signals if x["forward_returns"][key] is not None]
        bull = [x for x in ready if x["signal"] == "bullish_hold"]
        bear = [x for x in ready if x["signal"] == "cash_avoid"]
        horizon_stats[str(horizon)] = {
            "evaluated_signals": len(ready),
            "bullish_positive_rate_pct": round(sum(x["forward_returns"][key] > 0 for x in bull) / len(bull) * 100, 2) if bull else None,
            "cash_negative_rate_pct": round(sum(x["forward_returns"][key] < 0 for x in bear) / len(bear) * 100, 2) if bear else None,
        }

    latest = signals[-1]
    return {
        "model": "EMA20_above_EMA50_long_or_cash",
        "model_version": "1.0.0",
        "execution_assumption": "signal at close; position change at that close; following close-to-close return counted; no look-ahead",
        "fee_assumption_per_side_pct": round(FEE_PER_SIDE * 100, 4),
        "fee_assumption_note": "Assumption only; replace with verified broker/fund transaction costs before using as a live performance estimate.",
        "history": {
            "valid_sessions": len(rows),
            "first_date": dates[0],
            "last_date": dates[-1],
            "warmup_sessions": first_valid,
        },
        "full_sample": full,
        "out_of_sample_from_2024": oos,
        "forward_signal_hit_rates": horizon_stats,
        "latest_signal": {
            "date": latest["date"],
            "close": latest["close"],
            "ema20": latest["ema20"],
            "ema50": latest["ema50"],
            "signal": latest["signal"],
        },
        "signal_history": signals,
    }


def update_fundamental_ledger(fair_nav, fund_history):
    ledger_path = DATA / "ayar_fundamental_forecast_ledger.json"
    ledger = read_json(ledger_path, {"schema_version": "1.0", "forecasts": []})
    if not isinstance(ledger.get("forecasts"), list):
        raise ValueError("Fundamental forecast ledger has invalid schema")

    actual_by_date = {}
    for item in fund_history.get("history", []):
        date = str(item.get("date", ""))[:10].replace("-", "")
        value = item.get("redemption_nav")
        if len(date) == 8 and isinstance(value, (int, float)) and value > 0:
            actual_by_date[date] = int(value)

    for item in ledger["forecasts"]:
        actual = actual_by_date.get(item.get("as_of_date", "").replace("-", ""))
        if actual:
            item["official_redemption_nav_rial"] = actual
            item["absolute_error_pct"] = round(abs(item["predicted_nav_rial"] / actual - 1.0) * 100, 6)
            item["signed_error_pct"] = round((item["predicted_nav_rial"] / actual - 1.0) * 100, 6)
            item["scored"] = True

    calculation = fair_nav.get("calculation", {})
    basis = str(calculation.get("indicative_nav_basis", ""))
    as_of = str(calculation.get("valuation_as_of_date") or fair_nav.get("calculated_at", ""))[:10]
    prediction = calculation.get("indicative_nav_rial")
    independent = (
        calculation.get("full_nav_status") == "complete"
        and isinstance(prediction, (int, float))
        and prediction > 0
        and basis != "official_nav_same_session_anchor"
        and "anchor" not in basis.lower()
        and len(as_of) == 10
    )
    if independent:
        date_key = as_of.replace("-", "")
        if not any(x.get("as_of_date", "").replace("-", "") == date_key for x in ledger["forecasts"]):
            ledger["forecasts"].append({
                "as_of_date": as_of,
                "predicted_nav_rial": int(prediction),
                "basis": basis,
                "model_version": calculation.get("component_valuation", {}).get("model_version"),
                "scored": False,
                "forecast_type": "independent_same_session_fair_nav_estimate",
            })

    scored = [x for x in ledger["forecasts"] if x.get("scored")]
    errors = [x["absolute_error_pct"] for x in scored]
    result = {
        "forecast_count": len(ledger["forecasts"]),
        "scored_forecast_count": len(scored),
        "mean_absolute_percentage_error_pct": round(sum(errors) / len(errors), 6) if errors else None,
        "mean_signed_error_pct": round(sum(x["signed_error_pct"] for x in scored) / len(scored), 6) if scored else None,
        "status": "scored" if scored else "waiting_for_independent_forecasts",
        "important_note": "Official NAV same-session anchors are excluded; they reproduce the official NAV and are not independent forecasts.",
    }
    write_json(ledger_path, ledger)
    return result


def main():
    history_data = read_json(DATA / "ayar_daily_history.json")
    if not history_data or history_data.get("validated") is not True:
        raise ValueError("Validated daily history is missing")
    history = history_data.get("valid_history")
    if not isinstance(history, list) or len(history) < 200:
        raise ValueError("At least 200 validated trading sessions are required")
    dates = [x.get("date") for x in history]
    if len(dates) != len(set(dates)):
        raise ValueError("Duplicate dates found in valid history")
    for item in history:
        if any(not isinstance(item.get(k), (int, float)) or item[k] <= 0 for k in ("open", "high", "low", "close")):
            raise ValueError(f"Invalid OHLC record: {item.get('date')}")

    technical = build_technical(history)
    fair_nav = read_json(DATA / "ayar_fair_nav.json", {})
    fund_history = read_json(DATA / "ayar_fund_history.json", {})
    fundamental = update_fundamental_ledger(fair_nav, fund_history)

    result = {
        "schema_version": "1.0.0",
        "symbol": "عیار",
        "generated_at": datetime.now(TEHRAN).isoformat(),
        "status": "prototype_backtest_and_forward_tracking",
        "technical": technical,
        "fundamental": fundamental,
        "limitations": [
            "EMA20/EMA50 is a transparent baseline, not an optimized strategy or investment recommendation.",
            "Historical backtest does not guarantee future performance; the number of completed trades is reported and may be small.",
            "Transaction cost is an explicit assumption, not a verified fee schedule.",
            "Official-NAV-anchored values are not treated as independent fundamental forecasts.",
            "Fundamental forecast accuracy remains pending until independent same-session estimates can be compared with official NAV.",
        ],
    }
    write_json(DATA / "ayar_validation.json", result)
    print("VALIDATION_SUCCESS")
    print("Valid sessions:", technical["history"]["valid_sessions"])
    print("Date range:", technical["history"]["first_date"], "to", technical["history"]["last_date"])
    print("EMA20/50 full sample strategy CAGR:", technical["full_sample"]["strategy"].get("cagr_pct"))
    print("Buy-and-hold full sample CAGR:", technical["full_sample"]["buy_and_hold"].get("cagr_pct"))
    print("EMA20/50 full sample max drawdown:", technical["full_sample"]["strategy"].get("max_drawdown_pct"))
    print("Out-of-sample sessions:", (technical["out_of_sample_from_2024"] or {}).get("strategy", {}).get("sessions"))
    print("Fundamental validation:", fundamental["status"], "scored:", fundamental["scored_forecast_count"])


if __name__ == "__main__":
    main()
