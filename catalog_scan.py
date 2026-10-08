"""
Orchestrates one catalog row through ASIN resolution, cost-ledger
assembly, the deterministic ROI engine, demand gating, and eligibility -
into one CatalogScanResult (FBA Catalog Analyzer spec sections 2-6).

The central rule enforced here, not left to any one piece: a row is never
treated as a qualifying, profitable candidate unless its ASIN match is
confirmed AND every mandatory cost is known AND the ROI/demand checks both
pass. Each of those is checked independently and all of them gate
`qualifies` - see scan_row's docstring for exactly what "confirmed" means
for a match.

See catalog_batch.py for running this over every row in an imported
catalog (the CLI entry point - mirrors batch_runner.py's pattern).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from catalog_models import (
    AsinCandidate, CatalogRow, CatalogScanResult, CostComponent, DemandAssessment,
    EligibilityCheck, RoiResult,
)
from config import (
    CATALOG_DEFAULT_HISTORY_DAYS, CATALOG_HIGHLIGHT_ROI_THRESHOLD, CATALOG_MIN_MONTHLY_SALES,
    CATALOG_PREP_COST_PER_UNIT, CATALOG_REFERRAL_RATE_FALLBACK, CATALOG_ROI_CONVENTION, CATALOG_TARGET_ROI,
)
from demand_engine import assess_demand
from keepa_adapter import KeepaProvider, get_keepa_provider
from roi_engine import solve_target_supplier_price

# Mandatory cost-ledger line items (spec section 4). Everything else is
# computed, not looked up by name. Split into the two denominators
# roi_engine.compute_roi expects: B (landed, pre-sale cash costs) and S
# (everything else modeled). An entry missing from `known_costs` AND not
# one of the two with a built-in provisional default below stays
# status="unknown" - and an unknown MANDATORY entry blocks completeness.
B_BUCKET_NAMES = [
    "supplier_freight_to_prep", "prep_center_processing", "extra_packaging",
    "prep_to_amazon_shipping", "amazon_inbound_placement",
]
S_BUCKET_NAMES = ["referral_fee", "fba_fulfillment_fee", "expected_storage", "returns_allowance"]

# Names in B/S that this module seeds a provisional default for even when
# the caller supplies nothing - everything else in those lists starts
# status="unknown" on purpose (spec: "Leave unknown required costs as
# unknown; require explicit confirmation to use zero").
_DEFAULTED_NAMES = {"prep_center_processing", "referral_fee"}


def _merge_known_costs(
    known_costs: Optional[list[CostComponent]], selling_price: Optional[Decimal],
    prep_cost_per_unit: Decimal, referral_rate_fallback: Decimal,
) -> dict[str, CostComponent]:
    by_name: dict[str, CostComponent] = {}

    by_name["prep_center_processing"] = CostComponent(
        name="prep_center_processing", value=prep_cost_per_unit, basis="per_listing_unit", status="assumed",
        notes="PROVISIONAL default ($1.50/finished Amazon sellable unit) - not R&T's confirmed prep center quote.",
    )
    if selling_price is not None:
        by_name["referral_fee"] = CostComponent(
            name="referral_fee", value=referral_rate_fallback * selling_price, basis="percent_of_price", status="assumed",
            notes=f"PROVISIONAL fallback rate ({referral_rate_fallback:.0%}) - use a product-specific Amazon fee when known.",
        )

    for name in B_BUCKET_NAMES + S_BUCKET_NAMES:
        by_name.setdefault(name, CostComponent(name=name, value=None, status="unknown"))

    for component in known_costs or []:
        by_name[component.name] = component  # explicit input always wins over any built-in default

    return by_name


def _sum_bucket(by_name: dict[str, CostComponent], names: list[str]) -> tuple[Optional[Decimal], list[str]]:
    """(sum, reasons) - sum is None if any named component isn't known,
    and reasons lists exactly which ones are missing (shown to the user,
    never silently dropped)."""
    missing = [n for n in names if not by_name[n].is_known()]
    if missing:
        return None, [f"Unknown mandatory cost: {n}" for n in missing]
    total = sum((by_name[n].value for n in names), Decimal("0"))
    return total, []


def resolve_and_build_cost_ledger(
    row: CatalogRow,
    candidate: Optional[AsinCandidate],
    selling_price: Optional[Decimal],
    *,
    known_costs: Optional[list[CostComponent]] = None,
    prep_cost_per_unit: Decimal = CATALOG_PREP_COST_PER_UNIT,
    referral_rate_fallback: Decimal = CATALOG_REFERRAL_RATE_FALLBACK,
) -> tuple[list[CostComponent], Optional[Decimal], Optional[Decimal], Optional[Decimal], list[str]]:
    """Builds the full cost ledger and returns (ledger, C, B, S, incomplete_reasons).
    C/B/S are None (not 0) wherever they can't be fully determined yet."""
    reasons: list[str] = []
    ledger_by_name = _merge_known_costs(known_costs, selling_price, prep_cost_per_unit, referral_rate_fallback)

    normalized_cogs = None
    if row.purchase_price is None:
        reasons.append("Unknown mandatory cost: product_cogs (no purchase price on this catalog row)")
    elif candidate is None or candidate.listing_pack_quantity is None:
        reasons.append("Unknown mandatory cost: product_cogs (listing pack quantity not confirmed)")
    else:
        from roi_engine import normalize_cogs
        normalized_cogs = normalize_cogs(row.purchase_price, row.units_per_purchase_unit, candidate.listing_pack_quantity)
    ledger_by_name["product_cogs"] = CostComponent(
        name="product_cogs", value=normalized_cogs, basis="per_listing_unit",
        status="verified" if normalized_cogs is not None else "unknown",
        source=f"{row.supplier_name} catalog row {row.source_row}",
    )

    if candidate is not None and candidate.pack_relationship == "mixed_bundle":
        unknown_bom = [line for line in candidate.bill_of_materials if line.allocated_cost is None]
        if not candidate.bill_of_materials or unknown_bom:
            reasons.append("Mixed bundle missing a complete bill-of-materials cost allocation - cannot compute COGS.")
            ledger_by_name["product_cogs"] = CostComponent(name="product_cogs", value=None, status="unknown")

    b_total, b_reasons = _sum_bucket(ledger_by_name, B_BUCKET_NAMES)
    s_total, s_reasons = _sum_bucket(ledger_by_name, S_BUCKET_NAMES)
    reasons += b_reasons + s_reasons

    return list(ledger_by_name.values()), ledger_by_name["product_cogs"].value, b_total, s_total, reasons


def scan_row(
    row: CatalogRow,
    *,
    provider: Optional[KeepaProvider] = None,
    history_days: int = CATALOG_DEFAULT_HISTORY_DAYS,
    target_roi: Decimal = CATALOG_TARGET_ROI,
    highlight_threshold: Decimal = CATALOG_HIGHLIGHT_ROI_THRESHOLD,
    roi_convention: str = CATALOG_ROI_CONVENTION,
    minimum_monthly_sales: Decimal = CATALOG_MIN_MONTHLY_SALES,
    prep_cost_per_unit: Decimal = CATALOG_PREP_COST_PER_UNIT,
    referral_rate_fallback: Decimal = CATALOG_REFERRAL_RATE_FALLBACK,
    override_asin_candidate: Optional[AsinCandidate] = None,
    override_selling_price: Optional[Decimal] = None,
    override_demand: Optional[DemandAssessment] = None,
    known_costs: Optional[list[CostComponent]] = None,
) -> CatalogScanResult:
    """Evaluate one CatalogRow end-to-end.

    A match only counts as "confirmed" when override_asin_candidate is
    supplied with match_status="verified" and a listing_pack_quantity set -
    i.e. a human (or, once implemented, verified live evidence) has
    actually confirmed the pack relationship. Neither provider here ever
    sets match_status="verified" on its own (FixtureKeepaProvider can't;
    LiveKeepaProvider deliberately doesn't - see keepa_adapter.py), so a
    plain scan with no override always comes back needs_review/INCOMPLETE,
    which is the correct, honest default rather than a false positive.
    """
    provider = provider or get_keepa_provider()
    resolution = provider.resolve_upc(row.upc or "")

    candidate = override_asin_candidate
    if candidate is None and resolution.candidates and len(resolution.candidates) == 1:
        candidate = resolution.candidates[0]
    if override_asin_candidate is not None:
        resolution.resolved_asin = override_asin_candidate.asin if override_asin_candidate.match_status == "verified" else None

    confirmed_match = bool(candidate and candidate.match_status == "verified" and candidate.listing_pack_quantity)

    pricing = None
    selling_price = override_selling_price
    if candidate and selling_price is None:
        pricing = provider.get_pricing_snapshot(candidate.asin, history_days) if provider.is_live or override_asin_candidate else None
        if pricing:
            selling_price = pricing.planning_price

    ledger, cogs, b_total, s_total, incomplete_reasons = resolve_and_build_cost_ledger(
        row, candidate if confirmed_match else None, selling_price,
        known_costs=known_costs, prep_cost_per_unit=prep_cost_per_unit, referral_rate_fallback=referral_rate_fallback,
    )

    if not confirmed_match:
        incomplete_reasons.insert(0, "ASIN match not confirmed (needs_review, rejected, unmatched, or pack count unresolved).")
    if selling_price is None:
        incomplete_reasons.append("No selling price available (no live/override price, or insufficient history).")

    if incomplete_reasons or cogs is None or b_total is None or s_total is None or selling_price is None:
        roi = RoiResult(
            selling_price=selling_price, normalized_cogs=cogs, landed_pre_sale_costs=b_total, remaining_modeled_costs=s_total,
            qualifying_roi_convention=roi_convention, target_roi=target_roi, highlight_threshold=highlight_threshold,
            is_complete=False, incomplete_reasons=incomplete_reasons, tier="INCOMPLETE",
        )
    else:
        roi = solve_target_supplier_price(
            current_purchase_price=row.purchase_price,
            units_per_purchase_unit=row.units_per_purchase_unit,
            listing_pack_quantity=candidate.listing_pack_quantity,
            selling_price=selling_price, landed_pre_sale_costs=b_total, remaining_modeled_costs=s_total,
            target_roi=target_roi, highlight_threshold=highlight_threshold, qualifying_convention=roi_convention,
        )

    demand = override_demand or assess_demand(monthly_units_estimate=None, minimum_required=minimum_monthly_sales)
    eligibility = EligibilityCheck()

    qualifies = (
        confirmed_match
        and roi.is_complete
        and roi.tier in ("TARGET_MET", "NEGOTIATION_CANDIDATE")
        and demand.status == "MEETS_MINIMUM"
        and eligibility.supplier_brand_authorization_status != "not_authorized"
        and eligibility.listing_approval_status != "ungated_required"
    )

    notes: list[str] = []
    if roi.is_complete and roi.tier in ("TARGET_MET", "NEGOTIATION_CANDIDATE") and demand.status != "MEETS_MINIMUM":
        notes.append("ROI met - sales minimum not met.")
    elif roi.is_complete and roi.tier == "BELOW_TARGET" and demand.status == "MEETS_MINIMUM":
        notes.append("Sales minimum met - ROI target not met.")
    if confirmed_match and roi.tier == "INCOMPLETE":
        notes.append("Screening candidate only - cost data incomplete, not a guaranteed purchase.")
    if qualifies:
        notes.append("Screening candidate - meets ROI and demand thresholds, but still needs sourcing-readiness review (eligibility, pack verification).")

    return CatalogScanResult(
        row=row, resolution=resolution, pricing=pricing, cost_ledger=ledger, roi=roi, demand=demand, eligibility=eligibility,
        qualifies=qualifies, qualification_notes=notes, data_freshness=datetime.now(timezone.utc),
    )
