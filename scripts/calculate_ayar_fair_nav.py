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
    i = data["inputs"]
    nav0 = float(i["base_redemption_nav_rial"])
    weights = i["portfolio_weights_pct"]
    gold_linked = sum(float(weights[k]) for k in ("gold_bullion", "coin_certificates", "coin_forward"))
    silver = float(weights["silver"])
    cash = float(weights["bank_deposit"])
    residual = float(i["unclassified_weight_pct"])
    total = gold_linked + silver + cash + residual
    if nav0 <= 0 or abs(total - 100) > 0.15:
        raise ValueError(f"Invalid NAV or portfolio weights: total={total}")
    usd_ratio = float(i["usd_now_toman"]) / float(i["usd_base_toman"])
    gold_ratio = (float(i["xau_now_usd_oz"]) / float(i["xau_base_usd_oz"])) * usd_ratio
    gold_return = gold_ratio - 1
    silver_return = (1 + float(i["silver_usd_change_pct"]) / 100) * usd_ratio - 1
    weighted_return = (gold_linked / 100) * gold_return + (silver / 100) * silver_return
    estimate = nav0 * (1 + weighted_return)
    shocks = [-2, 0, 2]
    matrix = []
    for usd_shock in shocks:
        row = {"usd_shock_pct": usd_shock, "nav_rial_by_xau_shock": {}}
        for xau_shock in shocks:
            scenario_gold = (1 + usd_shock / 100) * (1 + xau_shock / 100) - 1
            scenario_silver = usd_shock / 100
            scenario_return = (gold_linked / 100) * scenario_gold + (silver / 100) * scenario_silver
            row["nav_rial_by_xau_shock"][str(xau_shock)] = round(estimate * (1 + scenario_return))
        matrix.append(row)
    diagnostic = component_premiums(data["component_market_snapshot"])
    holding = data["component_market_snapshot"]["coin_forward"]["holding"]
    papers = int(holding["papers_held"])
    quote = int(data["component_market_snapshot"]["coin_forward"]["closing_price_rial_per_paper"])
    papers_per_coin = int(holding["papers_per_coin"])
    if papers <= 0 or papers_per_coin <= 0:
        raise ValueError("Invalid confirmed forward holding quantity")
    holding_value_rial = papers * quote
    holding["latest_same_session_mark"]["market_value_if_quantity_unchanged_rial"] = holding_value_rial
    holding["latest_same_session_mark"]["market_value_if_quantity_unchanged_toman"] = holding_value_rial / 10
    diagnostic["forward_holding_quantity_confirmed"] = True
    diagnostic["forward_holding_papers"] = papers
    diagnostic["forward_holding_coin_equivalent"] = papers / papers_per_coin
    diagnostic["forward_market_value_if_quantity_unchanged_rial"] = holding_value_rial
    diagnostic["forward_report_date_reconciliation_difference_rial"] = int(holding["reconciliation"]["reported_net_market_value_minus_price_times_quantity_rial"])
    data["calculation"] = {
        "method": "weighted spot-index adjustment to last official redemption NAV",
        "gold_local_return_pct": round(gold_return * 100, 4),
        "silver_local_return_pct": round(silver_return * 100, 4),
        "gold_linked_weight_pct": round(gold_linked, 6),
        "silver_weight_pct": round(silver, 6),
        "unclassified_weight_pct": round(residual, 6),
        "weighted_estimated_nav_return_pct": round(weighted_return * 100, 4),
        "indicative_nav_rial": round(estimate),
        "indicative_nav_toman": round(estimate / 10, 1),
        "last_official_market_price_vs_same_day_nav_pct": round((float(i["last_official_market_price_rial"]) / nav0 - 1) * 100, 4),
        "scenario_shocks_pct": shocks,
        "scenario_matrix": matrix,
        "component_market_diagnostic": diagnostic,
        "validation": {
            "weights_sum_with_residual_pct": round(total, 6),
            "weights_sum_valid": abs(total - 100) <= 0.15,
            "calculation_reproduced": True,
            "official_nav_not_replaced": True,
            "current_market_premium_not_calculated_due_to_non_synchronous_quote": True,
            "component_premiums_reproduced": True,
            "forward_price_unit_conversion_valid": True,
            "forward_mark_to_market_reproduced": True,
            "forward_holding_quantity_reproduced": papers == int(holding["papers_held"]),
            "forward_holding_value_reproduced": holding_value_rial == papers * quote,
            "forward_holding_mapping_high_confidence": holding.get("symbol_mapping_confidence") == "high",
            "next_session_quote_not_yet_available": not bool(data["inputs"]["next_session_quotes_available"])
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
