"""
Minimum-monthly-sales demand gating (the FBA Catalog Analyzer spec's
addendum). A product qualifies only when it meets BOTH the ROI target
(roi_engine.py) AND this demand floor - meeting one without the other
must stay visibly distinguishable ("ROI met - sales minimum not met"),
never collapsed into a single pass/fail.

Nothing here ever treats missing sales data as zero, and a reported lower
bound ("100+") only passes when the bound itself clears the threshold -
otherwise the result is inconclusive, not a silent pass. Total
ASIN/distributor-side sales are NOT the same thing as R&T's own expected
sales (section 6) - see estimate_own_monthly_sales below for the
optional, explicitly user-entered sales-share extension.
"""

from decimal import Decimal
from typing import Optional

from catalog_models import DemandAssessment, DemandScope, DemandSource


def assess_demand(
    *,
    monthly_units_estimate: Optional[Decimal],
    minimum_required: Decimal,
    source: DemandSource = "unavailable",
    is_lower_bound: bool = False,
    scope: DemandScope = "unknown",
    measurement_period_days: Optional[int] = None,
    retrieved_at=None,
    notes: Optional[str] = None,
) -> DemandAssessment:
    """Classify demand status from a single monthly-units estimate.

    - No estimate at all (source == "unavailable" or value is None) ->
      UNKNOWN_NEEDS_REVIEW. Never treated as zero, never a pass.
    - scope == "parent_or_combined_variations" -> UNKNOWN_NEEDS_REVIEW
      regardless of the number, since it may not represent THIS specific
      child ASIN (spec: "flag the scope and require review").
    - is_lower_bound (e.g. a reported "100+") -> MEETS_MINIMUM only if the
      bound itself is >= minimum_required; otherwise UNKNOWN_NEEDS_REVIEW
      (a "10+" lower bound against a 25-unit minimum is inconclusive, not
      a fail - the true value could be above or below 25).
    - Otherwise: MEETS_MINIMUM if the estimate >= minimum_required, else
      BELOW_MINIMUM.
    """
    if monthly_units_estimate is None or source == "unavailable":
        status = "UNKNOWN_NEEDS_REVIEW"
    elif scope == "parent_or_combined_variations":
        status = "UNKNOWN_NEEDS_REVIEW"
    elif is_lower_bound:
        status = "MEETS_MINIMUM" if monthly_units_estimate >= minimum_required else "UNKNOWN_NEEDS_REVIEW"
    else:
        status = "MEETS_MINIMUM" if monthly_units_estimate >= minimum_required else "BELOW_MINIMUM"

    return DemandAssessment(
        monthly_units_estimate=monthly_units_estimate,
        is_lower_bound=is_lower_bound,
        source=source,
        retrieved_at=retrieved_at,
        measurement_period_days=measurement_period_days,
        scope=scope,
        minimum_required=minimum_required,
        status=status,
        notes=notes,
    )


def estimate_own_monthly_sales(
    demand: DemandAssessment, estimated_sales_share: Decimal, proposed_order_units: Optional[int] = None,
) -> DemandAssessment:
    """Section 6's optional extension: given a total-market monthly-units
    estimate and a user-entered assumption about what SHARE of that R&T
    expects to win (never assumed equal among sellers - that assumption is
    always explicit and user-supplied), project R&T's own monthly units
    and, if an order size is given, how many months it would take to sell
    through it. Returns a new DemandAssessment - does not mutate the input."""
    updated = demand.model_copy(deep=True)
    updated.estimated_sales_share = estimated_sales_share
    if demand.monthly_units_estimate is not None:
        projected = demand.monthly_units_estimate * estimated_sales_share
        updated.projected_monthly_units_for_rt = projected
        if proposed_order_units is not None and projected > 0:
            updated.months_to_sell_proposed_order = Decimal(proposed_order_units) / projected
    return updated
