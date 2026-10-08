"""
The FBA Catalog Analyzer spec's own worked acceptance checks (section 9,
items 1-4), plus the zero/negative-denominator and rounding edge cases
section 5 calls out explicitly. These are pinned to the spec's literal
numbers so a future change to roi_engine.py can't silently drift from the
agreed-upon math.
"""

from decimal import Decimal

from roi_engine import (
    classify_tier, compute_roi, max_allowable_cogs, normalize_cogs,
    required_discount, solve_target_supplier_price, target_supplier_price,
)

D = Decimal


def test_normalize_cogs_case_to_listing_pack():
    # Spec item 1: $48 case / 12 items / 2 per Amazon listing => $8.
    assert normalize_cogs(D("48"), 12, 2) == D("8")


def test_compute_roi_worked_example():
    # Spec item 2: P=20, C=8, B=2, S=7 => profit 3; landed ROI 30%;
    # merchandise ROI 37.5%; margin 15%.
    result = compute_roi(D("20"), D("8"), D("2"), D("7"))
    assert result.profit == D("3")
    assert result.landed_cost_roi == D("0.3")
    assert result.merchandise_cost_roi == D("0.375")
    assert result.margin == D("0.15")
    assert result.qualifying_roi == result.landed_cost_roi  # default convention is landed


def test_max_allowable_cogs_and_target_price_worked_example():
    # Spec item 3: for P=20, B=2, S=7 at 10% landed ROI, max C = 9.818181...;
    # a two-item listing sourced in cases of 12 implies max case quote
    # $58.909090..., rounded DOWN to $58.90, and recalculation must meet 10%.
    max_c = max_allowable_cogs(D("20"), D("2"), D("7"), D("0.10"), convention="landed")
    # Exact closed form: (P - S) / (1 + T) - B
    expected_max_c = (D("20") - D("7")) / D("1.10") - D("2")
    assert max_c == expected_max_c
    assert round(max_c, 6) == D("9.818182")

    price = target_supplier_price(max_c, units_per_purchase_unit=12, listing_pack_quantity=2)
    assert price == D("58.90")

    # Recalculation must meet (>=) 10% landed ROI at the rounded-down price.
    recomputed_cogs = normalize_cogs(price, 12, 2)
    recomputed = compute_roi(D("20"), recomputed_cogs, D("2"), D("7"))
    assert recomputed.landed_cost_roi >= D("0.10")


def test_exactly_eight_percent_is_not_highlighted_exactly_ten_meets_target():
    # Spec item 4: exactly 8% is not highlighted; exactly 10% meets target;
    # comparisons use unrounded values.
    assert classify_tier(D("0.08"), target_roi=D("0.10"), highlight_threshold=D("0.08"), is_complete=True) == "BELOW_TARGET"
    assert classify_tier(D("0.10"), target_roi=D("0.10"), highlight_threshold=D("0.08"), is_complete=True) == "TARGET_MET"
    assert classify_tier(D("0.0800001"), target_roi=D("0.10"), highlight_threshold=D("0.08"), is_complete=True) == "NEGOTIATION_CANDIDATE"


def test_zero_and_negative_denominators_never_qualify_via_infinity():
    # C + B == 0 (e.g. a free sample with no landed costs) must not produce
    # an infinite/undefined ROI that looks like a pass.
    result = compute_roi(D("20"), D("0"), D("0"), D("7"))
    assert result.landed_cost_roi is None
    assert result.qualifying_roi is None
    assert classify_tier(result.qualifying_roi, D("0.10"), D("0.08"), is_complete=True) == "INCOMPLETE"


def test_incomplete_cost_data_is_never_classified_as_qualifying():
    # A None qualifying_roi (e.g. costs never resolved at all) must come
    # back INCOMPLETE regardless of is_complete flag drift.
    assert classify_tier(None, D("0.10"), D("0.08"), is_complete=False) == "INCOMPLETE"


def test_required_discount_zero_at_or_above_target():
    dollar, percent = required_discount(D("50.00"), D("50.00"))
    assert dollar == D("0") and percent == D("0")
    dollar, percent = required_discount(D("50.00"), D("55.00"))  # target above current - already met
    assert dollar == D("0") and percent == D("0")


def test_required_discount_below_target():
    dollar, percent = required_discount(D("58.90909090909"), D("58.90"))
    assert round(dollar, 10) == D("0.0090909091")
    assert percent > D("0") and percent < D("1")


def test_max_allowable_cogs_infeasible_returns_none():
    # S alone exceeds P - no positive COGS can ever reach a positive ROI.
    assert max_allowable_cogs(D("10"), D("1"), D("20"), D("0.10")) is None


def test_solve_target_supplier_price_end_to_end_below_target():
    result = solve_target_supplier_price(
        current_purchase_price=D("48"),
        units_per_purchase_unit=12,
        listing_pack_quantity=2,
        selling_price=D("20"),
        landed_pre_sale_costs=D("2"),
        remaining_modeled_costs=D("7"),
        target_roi=D("0.10"),
        highlight_threshold=D("0.08"),
    )
    # normalized COGS at $48/case = $8 -> profit 3, landed ROI 30% - already above target.
    assert result.tier == "TARGET_MET"
    assert result.required_discount_dollars == D("0")


def test_solve_target_supplier_price_end_to_end_below_target_real_gap():
    # A pricier case quote that actually needs a discount to reach target.
    result = solve_target_supplier_price(
        current_purchase_price=D("90"),  # normalized COGS = 90/12*2 = 15 -> profit -2, negative ROI
        units_per_purchase_unit=12,
        listing_pack_quantity=2,
        selling_price=D("20"),
        landed_pre_sale_costs=D("2"),
        remaining_modeled_costs=D("7"),
        target_roi=D("0.10"),
        highlight_threshold=D("0.08"),
    )
    assert result.tier == "BELOW_TARGET"
    assert result.target_supplier_price_per_purchase_unit == D("58.90")
    assert result.required_discount_dollars == D("31.10")
    assert result.recomputed_roi_at_target_price >= D("0.10")


def test_solve_target_supplier_price_infeasible():
    result = solve_target_supplier_price(
        current_purchase_price=D("90"),
        units_per_purchase_unit=12,
        listing_pack_quantity=2,
        selling_price=D("10"),
        landed_pre_sale_costs=D("1"),
        remaining_modeled_costs=D("20"),  # S alone exceeds P
        target_roi=D("0.10"),
        highlight_threshold=D("0.08"),
    )
    assert result.tier != "TARGET_MET"
    assert result.target_supplier_price_per_purchase_unit is None
    assert any("No feasible" in r for r in result.incomplete_reasons)
