"""Independent, component-level valuation engine for Ayar fund.

No stale quote is treated as a current quote. The engine returns a full NAV
only when every non-zero portfolio component has a valid valuation for as_of.
"""
from datetime import date
from typing import Any, Dict, Optional


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _quote_return(base_price: Any, current_price: Any,
                  base_date: str, current_date: str):
    base = _num(base_price)
    current = _num(current_price)
    if base is None or current is None:
        return None, "missing_base_or_current_quote"
    if not base_date or not current_date:
        return None, "missing_quote_date"
    if date.fromisoformat(current_date) < date.fromisoformat(base_date):
        return None, "current_quote_precedes_base_date"
    if current_date == base_date:
        return 0.0, "same_session_anchor"
    return current / base - 1.0, "direct_market_quote"


def _gold_spot_return(inputs: Dict[str, Any]):
    xau0 = _num(inputs.get("xau_base_usd_oz"))
    xau1 = _num(inputs.get("xau_now_usd_oz"))
    fx0 = _num(inputs.get("usd_base_toman"))
    fx1 = _num(inputs.get("usd_now_toman"))
    d0 = inputs.get("xau_base_date")
    d1 = inputs.get("xau_now_date")
    fd0 = inputs.get("usd_base_date")
    fd1 = inputs.get("usd_now_date")
    if None in (xau0, xau1, fx0, fx1) or not all((d0, d1, fd0, fd1)):
        return None, "missing_spot_or_fx_input"
    if date.fromisoformat(d1) < date.fromisoformat(d0) or date.fromisoformat(fd1) < date.fromisoformat(fd0):
        return None, "spot_quote_precedes_base_date"
    if d1 != fd1:
        return None, "gold_and_fx_quotes_not_synchronized"
    return (xau1 / xau0) * (fx1 / fx0) - 1.0, "fine_gold_spot_proxy_xauusd_times_usd"


def value_components(data: Dict[str, Any], as_of_date: str) -> Dict[str, Any]:
    inputs = data["inputs"]
    snapshot = data["component_market_snapshot"]
    weights = inputs["portfolio_weights_pct"]
    nav0 = float(inputs["base_redemption_nav_rial"])
    base_date = inputs["base_nav_date"]
    if snapshot["snapshot_date"] != base_date:
        raise ValueError("Portfolio component anchor quotes must match the base NAV date")

    definitions = [
        ("gold_bullion", "شمش طلا", "spot_index_proxy", "gold"),
        ("coin_certificates", "گواهی سپرده سکه", "direct_exchange_quote", "coin"),
        ("silver", "شمش نقره", "direct_exchange_quote", "silver"),
        ("coin_forward", "سلف استاندارد سکه مرکزی", "direct_exchange_quote_and_contract_payoff", "forward"),
        ("bank_deposit", "سپرده بانکی", "accrual_yield", "bank"),
    ]
    components = []
    total_weight = sum(float(v) for v in weights.values()) + float(inputs["unclassified_weight_pct"])
    if abs(total_weight - 100.0) > 0.15:
        raise ValueError(f"Portfolio weights do not reconcile: {total_weight}")

    for key, label, method, kind in definitions:
        weight = float(weights[key])
        base_value = nav0 * weight / 100.0
        ret = None
        status = "not_valued"
        detail = {}

        if kind == "gold":
            ret, status = _gold_spot_return(inputs)
            detail = {
                "proxy": "XAUUSD × USD/IRR",
                "purity_and_exact_fine_gold_quantity": "not_disclosed_in_monthly_portfolio_extract",
                "premium_discount_and_transaction_costs": "not_observable",
            }
        elif kind == "coin":
            q = snapshot["coin_certificate"]
            ret, status = _quote_return(
                q.get("price_toman_per_certificate"),
                q.get("current_price_toman_per_certificate"),
                q["date"], q.get("current_quote_date", as_of_date)
            )
            detail = {
                "unit": q["unit"],
                "base_price_toman": q.get("price_toman_per_certificate"),
                "base_quote_date": q["date"],
                "premium_vs_fine_gold_at_anchor_pct": q.get("premium_pct"),
                "current_quote_date": q.get("current_quote_date"),
                "current_price_toman": q.get("current_price_toman_per_certificate"),
            }
        elif kind == "silver":
            q = snapshot["silver_bar_certificate"]
            ret, status = _quote_return(
                q.get("price_toman_per_gram"),
                q.get("current_price_toman_per_gram"),
                q["date"], q.get("current_quote_date", as_of_date)
            )
            detail = {
                "unit": "toman_per_gram",
                "base_price_toman_per_gram": q.get("price_toman_per_gram"),
                "base_quote_date": q["date"],
                "current_price_toman_per_gram": q.get("current_price_toman_per_gram"),
                "current_quote_date": q.get("current_quote_date"),
            }
        elif kind == "forward":
            q = snapshot["coin_forward"]
            ret, status = _quote_return(
                q.get("closing_price_rial_per_paper"),
                q.get("current_price_rial_per_paper"),
                q["quote_date"], q.get("current_quote_date", as_of_date)
            )
            detail = {
                "symbol": q.get("symbol"),
                "unit": "rial_per_paper",
                "papers_held_at_month_end": q.get("holding", {}).get("papers_held"),
                "base_price_rial_per_paper": q.get("closing_price_rial_per_paper"),
                "current_price_rial_per_paper": q.get("current_price_rial_per_paper"),
                "maturity_date": q.get("maturity_date_gregorian"),
                "conditional_maturity_floor_toman_per_coin": q.get("conditional_maturity_floor_toman_per_coin"),
                "maturity_floor_is_not_early_exit_price": True,
                "quantity_after_month_end_confirmed": False,
            }
        elif kind == "bank":
            rate_raw = inputs.get("bank_deposit_annual_rate_pct")
            rate = float(rate_raw) if rate_raw is not None and float(rate_raw) >= 0 else None
            start = inputs.get("bank_deposit_accrual_start_date")
            if rate is not None and start:
                days = (date.fromisoformat(as_of_date) - date.fromisoformat(start)).days
                if days >= 0:
                    ret = rate / 100.0 * days / 365.0
                    status = "simple_daily_accrual_from_disclosed_rate"
            detail = {
                "annual_rate_pct": inputs.get("bank_deposit_annual_rate_pct"),
                "accrual_start_date": inputs.get("bank_deposit_accrual_start_date"),
            }

        value = base_value * (1.0 + ret) if ret is not None else None
        components.append({
            "key": key,
            "name_fa": label,
            "weight_pct": round(weight, 8),
            "base_allocated_nav_rial": round(base_value, 2),
            "valuation_method": method,
            "return_pct": round(ret * 100.0, 6) if ret is not None else None,
            "valued_amount_rial": round(value, 2) if value is not None else None,
            "status": status,
            "detail": detail,
        })

    residual_weight = float(inputs["unclassified_weight_pct"])
    residual_value = nav0 * residual_weight / 100.0
    components.append({
        "key": "unclassified_residual",
        "name_fa": "مانده طبقه‌بندی‌نشده گزارش",
        "weight_pct": round(residual_weight, 8),
        "base_allocated_nav_rial": round(residual_value, 2),
        "valuation_method": "unclassified_no_mark",
        "return_pct": None,
        "valued_amount_rial": None,
        "status": "unclassified_requires_source_breakdown",
        "detail": {"do_not_assume_zero_return": True},
    })

    missing = [c["key"] for c in components if c["weight_pct"] > 0 and c["valued_amount_rial"] is None]
    all_valued = not missing
    valued_sum = sum(c["valued_amount_rial"] for c in components if c["valued_amount_rial"] is not None)
    base_sum = sum(c["base_allocated_nav_rial"] for c in components)
    return {
        "model_version": "2.0.0",
        "as_of_date": as_of_date,
        "base_nav_date": base_date,
        "base_official_redemption_nav_rial": nav0,
        "base_allocations_reconcile": abs(base_sum - nav0) <= 0.05,
        "portfolio_weight_sum_pct": round(total_weight, 8),
        "components": components,
        "known_component_value_sum_rial": round(valued_sum, 2),
        "full_indicative_nav_rial": round(valued_sum) if all_valued else None,
        "full_nav_status": "complete" if all_valued else "blocked_missing_component_marks",
        "missing_component_marks": missing,
        "no_stale_quote_carried_forward": True,
        "no_unpriced_component_assigned_zero_return": True,
        "limitations": [
            "شمش با شاخص طلای خالص XAUUSD×دلار به‌عنوان proxy قیمت‌گذاری می‌شود؛ وزن دقیق طلای خالص، عیار، هزینه نگهداری و پریمیوم/تخفیف فروش در گزارش تجمیعی موجود نیست.",
            "گواهی سکه و نقره فقط با قیمت معامله هم‌تاریخ ارزش‌گذاری می‌شوند؛ در نبود قیمت جلسه جاری، ارزش روز آن‌ها نامعلوم می‌ماند.",
            "سلف با قیمت مستقیم نماد و جدا از طلای نقدی ارزش‌گذاری می‌شود؛ کف سررسید فقط مشروط به مفاد قرارداد است و جای قیمت خروج زودهنگام را نمی‌گیرد.",
            "سپرده بانکی به نرخ و تاریخ شروع سود نیاز دارد؛ مانده طبقه‌بندی‌نشده نیز تا دریافت تفکیک رسمی قابل ارزش‌گذاری مستقل نیست.",
            "وزن‌ها مربوط به گزارش پایان مهر هستند؛ برای ارزش روز، تعداد واقعی دارایی‌ها و ترکیب پرتفوی روز لازم است."
        ],
    }
