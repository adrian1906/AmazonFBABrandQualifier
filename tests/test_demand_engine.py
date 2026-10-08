"""
Tests for demand_engine.py's minimum-monthly-sales gating - spec addendum
section, including its explicitly required edge cases: exactly at the
minimum, below the minimum, missing data, a "100+" lower-bound, and
parent-vs-child sales ambiguity.
"""

from decimal import Decimal

from demand_engine import assess_demand, estimate_own_monthly_sales

D = Decimal
MIN_25 = D("25")


def test_exactly_at_minimum_meets():
    result = assess_demand(monthly_units_estimate=D("25"), minimum_required=MIN_25, source="keepa_estimate", scope="exact_child_asin")
    assert result.status == "MEETS_MINIMUM"


def test_below_minimum():
    result = assess_demand(monthly_units_estimate=D("24.99"), minimum_required=MIN_25, source="keepa_estimate", scope="exact_child_asin")
    assert result.status == "BELOW_MINIMUM"


def test_missing_data_is_unknown_not_a_fail_or_pass():
    result = assess_demand(monthly_units_estimate=None, minimum_required=MIN_25, source="unavailable")
    assert result.status == "UNKNOWN_NEEDS_REVIEW"


def test_lower_bound_at_or_above_threshold_passes():
    # A reported "100+" against a 25-unit minimum: the bound itself clears it.
    result = assess_demand(
        monthly_units_estimate=D("100"), minimum_required=MIN_25, source="smartscout",
        is_lower_bound=True, scope="exact_child_asin",
    )
    assert result.status == "MEETS_MINIMUM"


def test_lower_bound_below_threshold_is_inconclusive_not_a_fail():
    # A reported "10+" against a 25-unit minimum: true value could be
    # above or below 25 - must NOT be marked BELOW_MINIMUM.
    result = assess_demand(
        monthly_units_estimate=D("10"), minimum_required=MIN_25, source="smartscout",
        is_lower_bound=True, scope="exact_child_asin",
    )
    assert result.status == "UNKNOWN_NEEDS_REVIEW"


def test_parent_or_combined_variation_scope_forces_review_even_if_high():
    # A high number that applies to the parent/combined variations, not
    # confirmed for this specific child ASIN, must not pass automatically.
    result = assess_demand(
        monthly_units_estimate=D("500"), minimum_required=MIN_25, source="keepa_estimate",
        scope="parent_or_combined_variations",
    )
    assert result.status == "UNKNOWN_NEEDS_REVIEW"


def test_sales_rank_proxy_source_still_classifies_but_is_labeled():
    # A sales-rank-drop proxy is allowed to classify, but the source field
    # preserves that it's a proxy, not a real units estimate - callers
    # must label the limitation wherever this is displayed.
    result = assess_demand(monthly_units_estimate=D("30"), minimum_required=MIN_25, source="sales_rank_proxy", scope="exact_child_asin")
    assert result.status == "MEETS_MINIMUM"
    assert result.source == "sales_rank_proxy"


def test_estimate_own_monthly_sales_projection():
    demand = assess_demand(monthly_units_estimate=D("200"), minimum_required=MIN_25, source="keepa_estimate", scope="exact_child_asin")
    updated = estimate_own_monthly_sales(demand, estimated_sales_share=D("0.10"), proposed_order_units=100)
    assert updated.projected_monthly_units_for_rt == D("20.0")
    assert updated.months_to_sell_proposed_order == D("5")
    # Original is untouched - never mutated in place.
    assert demand.estimated_sales_share is None


def test_estimate_own_monthly_sales_without_order_size_skips_months_projection():
    demand = assess_demand(monthly_units_estimate=D("200"), minimum_required=MIN_25, source="keepa_estimate", scope="exact_child_asin")
    updated = estimate_own_monthly_sales(demand, estimated_sales_share=D("0.10"))
    assert updated.projected_monthly_units_for_rt == D("20.0")
    assert updated.months_to_sell_proposed_order is None
