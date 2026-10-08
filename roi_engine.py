"""
Deterministic cost/ROI math for the FBA Catalog Analyzer (spec sections 4-5).

Every function here uses decimal.Decimal, never float, and no LLM ever
computes any of these numbers - "Use deterministic decimal arithmetic, not
LLM-generated arithmetic." This module is pure and side-effect-free: it
doesn't know about catalogs, Keepa, or Amazon fees. catalog_scan.py
assembles P/C/B/S from a CatalogScanResult's cost_ledger and calls in here;
if any mandatory cost is still "unknown" at that point, catalog_scan.py
never calls compute_roi/max_allowable_cogs at all - it builds an
INCOMPLETE RoiResult directly, so an unknown cost can never be silently
treated as zero.

See tests/test_roi_engine.py for the spec's own worked acceptance checks
(section 9, items 1-4), which this module is written to satisfy exactly.
"""

from decimal import Decimal, ROUND_DOWN
from typing import Optional

from catalog_models import QualificationTier, RoiResult

TWO_PLACES = Decimal("0.01")


def normalize_cogs(
    purchase_price: Decimal,
    units_per_purchase_unit: int,
    listing_pack_quantity: int,
) -> Decimal:
    """Per-Amazon-sellable-unit COGS, normalized from whatever unit the
    supplier actually quotes to the Amazon listing's own pack count.
    Spec example: a $48 case of 12 identical items, 2 of which make up one
    Amazon listing -> ($48 / 12) * 2 = $8. A case is never counted as one
    Amazon sellable unit."""
    if units_per_purchase_unit <= 0:
        raise ValueError("units_per_purchase_unit must be positive")
    if listing_pack_quantity <= 0:
        raise ValueError("listing_pack_quantity must be positive")
    price_per_base_item = purchase_price / units_per_purchase_unit
    return price_per_base_item * listing_pack_quantity


def classify_tier(
    qualifying_roi: Optional[Decimal],
    target_roi: Decimal,
    highlight_threshold: Decimal,
    is_complete: bool,
) -> QualificationTier:
    """TARGET_MET requires qualifying_roi >= target_roi (exactly at target
    meets it). NEGOTIATION_CANDIDATE requires qualifying_roi STRICTLY
    greater than highlight_threshold (exactly at the highlight threshold
    does NOT qualify - spec section 9 item 4: "exactly 8% is not
    highlighted"). Comparisons always use the unrounded Decimal."""
    if not is_complete or qualifying_roi is None:
        return "INCOMPLETE"
    if qualifying_roi >= target_roi:
        return "TARGET_MET"
    if qualifying_roi > highlight_threshold:
        return "NEGOTIATION_CANDIDATE"
    return "BELOW_TARGET"


def compute_roi(
    selling_price: Decimal,
    normalized_cogs: Decimal,
    landed_pre_sale_costs: Decimal,
    remaining_modeled_costs: Decimal,
    *,
    qualifying_convention: str = "landed",
    target_roi: Decimal = Decimal("0.10"),
    highlight_threshold: Decimal = Decimal("0.08"),
) -> RoiResult:
    """Profit and both ROI conventions for one fully-known scenario. Zero
    or negative denominators never qualify via an infinite ratio - the
    corresponding ROI comes back as None, and classify_tier treats a None
    qualifying_roi the same as incomplete (never a false TARGET_MET)."""
    P, C, B, S = selling_price, normalized_cogs, landed_pre_sale_costs, remaining_modeled_costs
    profit = P - C - B - S

    landed_denominator = C + B
    merchandise_denominator = C

    landed_roi = profit / landed_denominator if landed_denominator > 0 else None
    merchandise_roi = profit / merchandise_denominator if merchandise_denominator > 0 else None
    margin = profit / P if P > 0 else None

    qualifying_roi = landed_roi if qualifying_convention == "landed" else merchandise_roi
    tier = classify_tier(qualifying_roi, target_roi, highlight_threshold, is_complete=True)

    return RoiResult(
        selling_price=P, normalized_cogs=C, landed_pre_sale_costs=B, remaining_modeled_costs=S,
        profit=profit, landed_cost_roi=landed_roi, merchandise_cost_roi=merchandise_roi, margin=margin,
        qualifying_roi_convention=qualifying_convention, qualifying_roi=qualifying_roi,
        target_roi=target_roi, highlight_threshold=highlight_threshold,
        is_complete=True, tier=tier,
    )


def max_allowable_cogs(
    selling_price: Decimal,
    landed_pre_sale_costs: Decimal,
    remaining_modeled_costs: Decimal,
    target_roi: Decimal,
    *,
    convention: str = "landed",
) -> Optional[Decimal]:
    """The highest per-sellable-unit COGS that still clears target_roi,
    holding B and S fixed (section 5's closed-form solution, which assumes
    B and S are independent of purchase cost). None means no feasible
    positive COGS exists under these assumptions - report "no feasible
    positive product purchase price," never a negative or zero target."""
    P, B, S = selling_price, landed_pre_sale_costs, remaining_modeled_costs
    one_plus_t = Decimal(1) + target_roi

    if convention == "landed":
        # Profit = P - C - B - S; landed ROI = Profit / (C + B) = T
        # => C = (P - S) / (1 + T) - B
        max_c = (P - S) / one_plus_t - B
    else:
        # merchandise ROI = Profit / C = T => C = (P - B - S) / (1 + T)
        max_c = (P - B - S) / one_plus_t

    return max_c if max_c > 0 else None


def target_supplier_price(
    max_allowable_cogs_value: Decimal,
    units_per_purchase_unit: int,
    listing_pack_quantity: int,
    quote_precision: Decimal = TWO_PLACES,
) -> Decimal:
    """Convert a max per-listing-unit COGS back to the supplier's own
    quote basis (e.g. per case), rounded DOWN to the supplier's valid
    quote precision - rounding up could under-shoot the target ROI once
    recomputed, so this always rounds toward the buyer's disadvantage on
    price, never the target's."""
    if listing_pack_quantity <= 0:
        raise ValueError("listing_pack_quantity must be positive")
    max_price_per_purchase_unit = max_allowable_cogs_value * units_per_purchase_unit / listing_pack_quantity
    return max_price_per_purchase_unit.quantize(quote_precision, rounding=ROUND_DOWN)


def required_discount(
    current_purchase_price: Decimal,
    target_purchase_price: Decimal,
) -> tuple[Decimal, Decimal]:
    """(dollar discount, percent discount) off the current quoted price.
    Zero - never negative - when the target is already at or above the
    current price (section 5: "At or above target, report zero required
    discount.")."""
    if target_purchase_price >= current_purchase_price:
        return Decimal("0"), Decimal("0")
    dollar = current_purchase_price - target_purchase_price
    if current_purchase_price == 0:
        return dollar, Decimal("0")  # can't express a % discount off a $0 quote
    percent = dollar / current_purchase_price * 100
    return dollar, percent


def solve_target_supplier_price(
    *,
    current_purchase_price: Decimal,
    units_per_purchase_unit: int,
    listing_pack_quantity: int,
    selling_price: Decimal,
    landed_pre_sale_costs: Decimal,
    remaining_modeled_costs: Decimal,
    target_roi: Decimal,
    highlight_threshold: Decimal,
    qualifying_convention: str = "landed",
    quote_precision: Decimal = TWO_PLACES,
) -> RoiResult:
    """End-to-end convenience: given a known-complete cost scenario at the
    CURRENT supplier price, compute the actual ROI, and - whenever it's
    below target - the supplier price that would reach target_roi, rounded
    down, with the ROI recomputed at that rounded price to verify it
    actually clears the target (spec section 5's acceptance check). Always
    computes the discount for every evaluable product below target,
    including unprofitable ones and ones at or below the highlight
    threshold - only an infeasible (non-positive) max COGS skips it."""
    normalized_cogs = normalize_cogs(current_purchase_price, units_per_purchase_unit, listing_pack_quantity)
    result = compute_roi(
        selling_price, normalized_cogs, landed_pre_sale_costs, remaining_modeled_costs,
        qualifying_convention=qualifying_convention, target_roi=target_roi, highlight_threshold=highlight_threshold,
    )

    if result.tier == "TARGET_MET":
        result.target_supplier_price_per_purchase_unit = current_purchase_price
        result.required_discount_dollars = Decimal("0")
        result.required_discount_percent = Decimal("0")
        result.recomputed_roi_at_target_price = result.qualifying_roi
        return result

    max_c = max_allowable_cogs(
        selling_price, landed_pre_sale_costs, remaining_modeled_costs, target_roi, convention=qualifying_convention,
    )
    if max_c is None:
        result.incomplete_reasons.append(
            "No feasible positive supplier price reaches the target ROI under the current B/S assumptions."
        )
        return result

    result.max_allowable_cogs = max_c
    target_price = target_supplier_price(max_c, units_per_purchase_unit, listing_pack_quantity, quote_precision)
    result.target_supplier_price_per_purchase_unit = target_price

    dollar, percent = required_discount(current_purchase_price, target_price)
    result.required_discount_dollars = dollar
    result.required_discount_percent = percent

    # Verify the rounded-down price actually meets the target once recomputed.
    recomputed_cogs = normalize_cogs(target_price, units_per_purchase_unit, listing_pack_quantity)
    recomputed = compute_roi(
        selling_price, recomputed_cogs, landed_pre_sale_costs, remaining_modeled_costs,
        qualifying_convention=qualifying_convention, target_roi=target_roi, highlight_threshold=highlight_threshold,
    )
    result.recomputed_roi_at_target_price = recomputed.qualifying_roi

    return result
