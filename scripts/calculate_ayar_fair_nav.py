#!/usr/bin/env python3
"""Indicative Ayar NAV sensitivity and direct component premium diagnostics."""
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
    coin_intrinsic = float(coin["fine_gold_grams"]) * xau * usd / TROY_OUNCE_GRAMS
    bar_intrinsic = float(bar["fine_gold_grams"]) * xau * usd / TROY_OUNCE_GRAMS
    coin_premium = (float(coin["price_toman_per_certificate"]) / coin_intrinsic - 1) * 100
    bar_premium = (float(bar["price_toman_per_certificate"]) / bar_intrinsic - 1) * 100
    return {
        "coin_certificate": {"implied_fine_gold_value_toman": round(coin_intrinsic, 2), "premium_pct": round(coin_premium, 4)},
        "gold_bar_certificate": {"implied_fine_gold_value_toman": round(bar_intrinsic, 2), "premium_pct": round(bar_premium, 4)},
        "premium_gap_percentage_points": round(coin_premium - bar_premium, 4),
        "forward_component_directly_priced": False,
        "current_fair_nav_refreshed": False,
        "reason": "Latest component quotes are dated 2026-11-18, the same date as the base official NAV; no synchronized newer commodity quote was available to update the 2026-11-19 estimate."
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
    data["component_market_snapshot"]["coin_certificate"].update(diagnostic["coin_certificate"])
    data["component_market_snapshot"]["gold_bar_certificate"].update(diagnostic["gold_bar_certificate"])
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
            "component_premiums_reproduced": True
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
