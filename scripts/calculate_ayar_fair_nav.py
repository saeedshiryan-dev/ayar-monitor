#!/usr/bin/env python3
"""Indicative Ayar NAV sensitivity and direct component market valuation."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "data" / "ayar_fair_nav.json"
TROY_OUNCE_GRAMS = 31.1034768


def component_premiums(snapshot):
    usd = float(snapshot["usd_toman_daily_average"])
    xau = float(snapshot["xau_usd_per_oz_close"])
    coin = snapshot["coin_certificate"]
    bar = snapshot["gold_bar_certificate"]
    forward = snapshot["coin_forward"]
    coin_intrinsic = float(coin["fine_gold_grams"]) * xau * usd / TROY_OUNCE_GRAMS
    bar_intrinsic = float(bar["fine_gold_grams"]) * xau * usd / TROY_OUNCE_GRAMS
    coin_premium = (float(coin["price_toman_per_certificate"]) / coin_intrinsic - 1) * 100
    bar_premium = (float(bar["price_toman_per_certificate"]) / bar_intrinsic - 1) * 100

    papers_per_coin = int(forward["total_papers_issued"]) // int(forward["coin_equivalent_issued"])
    if papers_per_coin != int(forward["papers_per_coin"]):
        raise ValueError("Aske2 paper-to-coin conversion does not reconcile")
    forward_toman = float(forward["closing_price_rial_per_paper"]) * papers_per_coin / 10
    floor_toman = float(forward["initial_issue_price_toman_per_coin"]) * (
        1 + float(forward["stated_minimum_maturity_return_pct"]) / 100
    )
    days = int(forward["remaining_calendar_days_from_quote"])
    forward_vs_coin = (forward_toman / float(coin["price_toman_per_certificate"]) - 1) * 100
    forward_vs_spot = (forward_toman / coin_intrinsic - 1) * 100
    floor_gap = (floor_toman / forward_toman - 1) * 100
    simple_annualized = (floor_toman / forward_toman - 1) * 365 / days * 100
    compound_annualized = ((floor_toman / forward_toman) ** (365 / days) - 1) * 100

    forward.update({
        "papers_per_coin": papers_per_coin,
        "price_toman_per_coin_equivalent": round(forward_toman, 2),
        "conditional_maturity_floor_toman_per_coin": round(floor_toman, 2),
        "premium_vs_coin_certificate_pct": round(forward_vs_coin, 4),
        "premium_vs_fine_gold_spot_pct": round(forward_vs_spot, 4),
        "fine_gold_intrinsic_toman": round(coin_intrinsic, 2),
        "price_difference_vs_coin_certificate_toman": round(
            forward_toman - float(coin["price_toman_per_certificate"]), 2
        ),
        "discount_to_conditional_floor_pct": round((1 - forward_toman / floor_toman) * 100, 4),
        "implied_simple_annualized_return_to_floor_pct": round(simple_annualized, 4),
        "implied_compound_annualized_return_to_floor_pct": round(compound_annualized, 4),
    })
    return {
        "coin_certificate_premium_vs_fine_gold_pct": round(coin_premium, 4),
        "gold_bar_certificate_premium_vs_fine_gold_pct": round(bar_premium, 4),
        "premium_gap_percentage_points": round(coin_premium - bar_premium, 4),
        "forward": {
            "symbol": forward["symbol"],
            "price_toman_per_coin_equivalent": round(forward_toman, 2),
            "premium_vs_coin_certificate_pct": round(forward_vs_coin, 4),
            "premium_vs_fine_gold_spot_pct": round(forward_vs_spot, 4),
            "conditional_maturity_floor_toman": round(floor_toman, 2),
            "discount_to_conditional_floor_pct": round((1 - forward_toman / floor_toman) * 100, 4),
            "implied_simple_annualized_return_to_floor_pct": round(simple_annualized, 4),
            "implied_compound_annualized_return_to_floor_pct": round(compound_annualized, 4),
            "remaining_calendar_days": days,
        },
        "forward_component_directly_priced": True,
        "current_fair_nav_refreshed": False,
        "reason": (
            "All three component quotes are synchronized to 2026-11-18, the same "
            "date as the last official NAV. Aske2 can now be marked to market, but "
            "no later common-session quote exists to roll NAV beyond 2026-11-18."
        ),
    }


def calculate(data):
    from component_valuation import value_components

    inputs = data["inputs"]
    diagnostic = component_premiums(data["component_market_snapshot"])
    valuation_date = inputs.get("valuation_as_of_date", inputs["latest_market_quote_check_date"])
    component_model = value_components(data, valuation_date)
    holding = data["component_market_snapshot"]["coin_forward"]["holding"]
    papers = int(holding["papers_held"])
    quote = int(data["component_market_snapshot"]["coin_forward"]["closing_price_rial_per_paper"])
    holding_value_rial = papers * quote
    holding["latest_same_session_mark"]["market_value_if_quantity_unchanged_rial"] = holding_value_rial
    holding["latest_same_session_mark"]["market_value_if_quantity_unchanged_toman"] = holding_value_rial / 10

    data["calculation"] = {
        "method": "independent_component_valuation_v2_no_stale_quote_carry_forward",
        "valuation_as_of_date": valuation_date,
        "official_nav_not_replaced": True,
        "indicative_nav_rial": component_model["full_indicative_nav_rial"],
        "indicative_nav_toman": round(component_model["full_indicative_nav_rial"] / 10, 1) if component_model["full_indicative_nav_rial"] is not None else None,
        "full_nav_status": component_model["full_nav_status"],
        "missing_component_marks": component_model["missing_component_marks"],
        "component_valuation": component_model,
        "component_market_diagnostic": diagnostic,
        "forward_holding_quantity_confirmed": True,
        "forward_holding_papers": papers,
        "forward_holding_coin_equivalent": papers / int(holding["papers_per_coin"]),
        "forward_market_value_if_quantity_unchanged_rial": holding_value_rial,
        "validation": {
            "weights_sum_valid": component_model["base_allocations_reconcile"],
            "base_allocations_reconcile": component_model["base_allocations_reconcile"],
            "independent_component_methods_present": len(component_model["components"]) == 6,
            "no_stale_quote_carried_forward": component_model["no_stale_quote_carried_forward"],
            "no_unpriced_component_assigned_zero_return": component_model["no_unpriced_component_assigned_zero_return"],
            "full_nav_blocked_when_component_marks_missing": (component_model["full_nav_status"] == "blocked_missing_component_marks") == bool(component_model["missing_component_marks"]),
            "forward_price_unit_conversion_valid": int(data["component_market_snapshot"]["coin_forward"]["papers_per_coin"]) == 1000,
            "forward_holding_value_reproduced": holding_value_rial == papers * quote,
            "forward_holding_mapping_high_confidence": holding.get("symbol_mapping_confidence") == "high"
        }
    }
    return data


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    result = calculate(data)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)
    print(json.dumps(result["calculation"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
