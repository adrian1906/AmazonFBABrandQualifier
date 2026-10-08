"""
Data models for the FBA Catalog Analyzer.

Turns a distributor price list (catalogs/*.xlsx or .csv) into per-row
candidate Amazon ASIN matches, landed-cost ROI, and a target supplier
price/discount - see roi_engine.py (the deterministic math),
catalog_import.py (intake/column-mapping), keepa_adapter.py (ASIN
resolution/price history - fixture/demo mode unless KEEPA_API_KEY is set,
see its docstring), demand_engine.py (minimum monthly sales gating), and
catalog_scan.py (orchestration).

Mirrors this project's existing conventions: named, typed fields over open
dicts (like models.py), and the evidence-grading pattern from
models.EvidenceItem - "never silently treat an inference as a verified
fact." The central rule this module exists to enforce: an unknown
mandatory cost, an unresolved pack count, or a missing ASIN match must
produce an explicitly INCOMPLETE/needs-review result, never a result that
merely looks profitable because a gap was quietly treated as zero.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Cost ledger (section 4) - every dollar figure carries its own provenance
# and status.
# ---------------------------------------------------------------------------

CostStatus = Literal["verified", "estimated", "assumed", "unknown", "confirmed_zero"]
CostBasis = Literal["per_each", "per_listing_unit", "per_case", "per_order", "percent_of_price"]


class CostComponent(BaseModel):
    name: str
    value: Optional[Decimal] = None  # None + status="unknown" - never silently treated as 0
    basis: CostBasis = "per_listing_unit"
    status: CostStatus = "unknown"
    source: Optional[str] = None
    effective_date: Optional[date] = None
    notes: Optional[str] = None

    def is_known(self) -> bool:
        return self.status != "unknown" and self.value is not None


# ---------------------------------------------------------------------------
# Catalog intake (section 1)
# ---------------------------------------------------------------------------

PriceBasis = Literal["each", "inner_pack", "case"]


class QuantityPriceBreak(BaseModel):
    min_quantity: int
    price: Decimal


class CatalogRow(BaseModel):
    """One row of a distributor price list, as literally stated by the
    supplier - nothing here is inferred or converted. UPC/EAN/GTIN is kept
    as the exact text from the file (leading zeros intact); validation
    happens alongside the raw value, never by mutating it."""

    supplier_name: str
    catalog_version: Optional[str] = None
    catalog_date: Optional[date] = None
    source_row: int  # 1-based row number in the original file, for traceability

    supplier_sku: Optional[str] = None
    brand: Optional[str] = None
    description: str
    model: Optional[str] = None
    size: Optional[str] = None
    uom: Optional[str] = None
    flavor_or_variant: Optional[str] = None

    upc: Optional[str] = None  # exactly as read from the file - never mutated, even when it looks truncated
    upc_valid: Optional[bool] = None  # GS1 checksum result for `upc` AS GIVEN; None = no UPC, or not a standard 8/12/13/14-digit length
    # A very common real-world artifact: some system upstream treated the
    # UPC column as a number and dropped its leading zero(s). When
    # zero-padding `upc` up to 12 digits produces a checksum-valid UPC-A,
    # that reconstructed value goes here - a reviewable SUGGESTION,
    # distinct from `upc` itself, which is left exactly as read.
    upc_suggested_correction: Optional[str] = None

    price_basis: PriceBasis = "each"
    purchase_price: Optional[Decimal] = None
    currency: str = "USD"
    units_per_purchase_unit: int = 1  # how many "price_basis" units make up one order/case unit
    order_multiple: Optional[int] = None  # MOQ / case pack - must order in multiples of this many eaches
    quantity_breaks: list[QuantityPriceBreak] = Field(default_factory=list)

    availability: Optional[str] = None
    country_of_origin: Optional[str] = None
    # This supplier's OWN sell-through, not Amazon sales - section 6 is
    # explicit that total/distributor-side sales are not "my expected
    # sales." Kept for context only; never fed into demand_engine as if it
    # were an Amazon demand signal.
    distributor_trailing_12mo_units_sold: Optional[int] = None
    distributor_sales_ytd_units: Optional[int] = None

    categories: list[str] = Field(default_factory=list)
    raw_fields: dict[str, str] = Field(default_factory=dict)  # every column this file had, verbatim, for audit


class ColumnMapping(BaseModel):
    """canonical_field -> the exact header text found in one specific file,
    plus the catalog-wide price-basis assumption. Saved per supplier (see
    catalog_persistence.save_mapping_profile) so a repeat catalog from the
    same source doesn't need remapping."""
    supplier_name: str
    mapping: dict[str, str]
    price_basis: PriceBasis = "each"


class CatalogImportWarning(BaseModel):
    source_row: int
    message: str


class CatalogImportPreview(BaseModel):
    """What the UI shows before anything is committed - section 1's rule
    that column mapping and a preview must be shown before an import is
    committed."""
    detected_mapping: dict[str, str]
    unmapped_required_fields: list[str]
    price_basis_assumption: PriceBasis
    price_basis_note: str  # always shown prominently - this is a guess, not a fact read from the file
    sample_rows: list[CatalogRow]
    total_row_count: int
    warnings: list[CatalogImportWarning] = Field(default_factory=list)
    # None = not checked (the default fast preview only reads sample_size
    # rows - a full duplicate scan means reading every row, which is slow
    # on a 90k+ row catalog). Pass thorough=True to preview_catalog() to
    # populate this for real before committing an import.
    duplicate_upc_count: Optional[int] = None


# ---------------------------------------------------------------------------
# ASIN resolution and product matching (section 2)
# ---------------------------------------------------------------------------

MatchStatus = Literal["verified", "needs_review", "rejected", "unmatched"]

PackRelationship = Literal[
    "identical_item",                 # 1: one identical retail item
    "identical_multipack",             # 2: identical-item multipack
    "mixed_bundle",                     # 3: mixed bundle, different components
    "supplier_case_not_retail_pack",   # 4: supplier shipping case, not the retail listing pack
]


class BillOfMaterialsLine(BaseModel):
    component_description: str
    component_upc: Optional[str] = None
    quantity: int
    allocated_cost: Optional[Decimal] = None  # unknown blocks a mixed-bundle ROI - see roi_engine/catalog_scan


class AsinCandidate(BaseModel):
    asin: str
    is_variation_child: bool = True
    parent_asin: Optional[str] = None
    title: Optional[str] = None
    # The CONFIRMED retail pack count for this child ASIN - distinct from
    # Amazon's raw itemPackageQuantity/numberOfItems field below, which is
    # never substituted for a confirmed count on its own (spec section 2).
    listing_pack_quantity: Optional[int] = None
    item_package_quantity_raw: Optional[int] = None
    pack_relationship: Optional[PackRelationship] = None
    bill_of_materials: list[BillOfMaterialsLine] = Field(default_factory=list)

    match_status: MatchStatus = "unmatched"
    confidence: Literal["high", "medium", "low"] = "low"
    match_reasons: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)

    overridden_by_user: bool = False
    override_note: Optional[str] = None


class AsinResolution(BaseModel):
    """All candidates a UPC returned, with evidence - "never select the
    most profitable candidate merely because it is profitable." The scan
    engine only treats a row as resolved when exactly one candidate is
    verified, or the user has explicitly overridden one."""
    upc: str
    candidates: list[AsinCandidate] = Field(default_factory=list)
    resolved_asin: Optional[str] = None
    resolution_reason: Optional[str] = None
    is_live_data: bool = False  # False = keepa_adapter fixture/demo mode


# ---------------------------------------------------------------------------
# Price history (section 3)
# ---------------------------------------------------------------------------

PriceSeriesName = Literal["buy_box_new_fba", "lowest_new_fba", "lowest_new_all", "amazon_retail"]


class PricePoint(BaseModel):
    at: datetime
    price: Optional[Decimal] = None  # None = out-of-stock/missing sentinel at this instant, NOT $0
    shipping_included: bool = True


class PriceWindowStats(BaseModel):
    window_days: int
    coverage_days: float  # how much of window_days actually has data - flags short/seasonal-insufficient history
    raw_minimum: Optional[Decimal] = None
    raw_minimum_at: Optional[datetime] = None
    time_weighted_mean: Optional[Decimal] = None
    time_weighted_median: Optional[Decimal] = None
    out_of_stock_days: float = 0.0
    is_stale: bool = False  # most recent observation is older than "now" by more than the window
    suspicious_brief_low: bool = False  # raw_minimum held for an unusually short duration - flagged, not deleted


class PriceSeries(BaseModel):
    name: PriceSeriesName
    retrieved_at: datetime
    points: list[PricePoint] = Field(default_factory=list)
    source: str = "keepa"
    is_live_data: bool = False


class PricingSnapshot(BaseModel):
    asin: str
    series: list[PriceSeries] = Field(default_factory=list)
    current_buy_box: Optional[Decimal] = None
    window_stats: dict[int, PriceWindowStats] = Field(default_factory=dict)  # keyed by window_days
    planning_price: Optional[Decimal] = None  # lower of current Buy Box and raw valid minimum for the chosen window
    planning_price_basis: Optional[str] = None  # which rule/series produced it - always shown, never silent
    requires_review: bool = False  # e.g. had to fall back off the default shipping-inclusive new-FBA series


# ---------------------------------------------------------------------------
# Demand / minimum monthly sales (addendum)
# ---------------------------------------------------------------------------

DemandStatus = Literal["MEETS_MINIMUM", "BELOW_MINIMUM", "UNKNOWN_NEEDS_REVIEW"]
DemandSource = Literal["keepa_estimate", "smartscout", "manual_entry", "sales_rank_proxy", "unavailable"]
DemandScope = Literal["exact_child_asin", "parent_or_combined_variations", "unknown"]


class DemandAssessment(BaseModel):
    monthly_units_estimate: Optional[Decimal] = None
    is_lower_bound: bool = False  # e.g. a reported "100+" - passes only if the bound itself clears the threshold
    source: DemandSource = "unavailable"
    retrieved_at: Optional[datetime] = None
    measurement_period_days: Optional[int] = None
    scope: DemandScope = "unknown"
    minimum_required: Decimal = Decimal("0")
    status: DemandStatus = "UNKNOWN_NEEDS_REVIEW"
    notes: Optional[str] = None

    # Section 6's sales-share extension - optional, user-entered.
    estimated_sales_share: Optional[Decimal] = None  # e.g. 0.10 = expect to win ~10% of total unit sales
    projected_monthly_units_for_rt: Optional[Decimal] = None
    months_to_sell_proposed_order: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# ROI / eligibility result (sections 4-5-6)
# ---------------------------------------------------------------------------

QualificationTier = Literal["TARGET_MET", "NEGOTIATION_CANDIDATE", "BELOW_TARGET", "INCOMPLETE"]


class RoiResult(BaseModel):
    selling_price: Optional[Decimal] = None  # P
    normalized_cogs: Optional[Decimal] = None  # C
    landed_pre_sale_costs: Optional[Decimal] = None  # B
    remaining_modeled_costs: Optional[Decimal] = None  # S
    profit: Optional[Decimal] = None

    landed_cost_roi: Optional[Decimal] = None  # profit / (C + B)
    merchandise_cost_roi: Optional[Decimal] = None  # profit / C
    margin: Optional[Decimal] = None  # profit / P

    qualifying_roi_convention: Literal["landed", "merchandise"] = "landed"
    qualifying_roi: Optional[Decimal] = None  # whichever convention gates qualification

    target_roi: Decimal = Decimal("0.10")
    highlight_threshold: Decimal = Decimal("0.08")

    max_allowable_cogs: Optional[Decimal] = None  # solved from target_roi, holding B and S fixed
    target_supplier_price_per_purchase_unit: Optional[Decimal] = None
    required_discount_dollars: Optional[Decimal] = None
    required_discount_percent: Optional[Decimal] = None
    recomputed_roi_at_target_price: Optional[Decimal] = None  # verifies the rounded-down price actually clears target

    is_complete: bool = False  # False whenever a mandatory cost is unknown - never silently "profitable"
    incomplete_reasons: list[str] = Field(default_factory=list)
    tier: QualificationTier = "INCOMPLETE"


class EligibilityCheck(BaseModel):
    """Separate from ROI - a product can meet 10% ROI while still needing
    approval or pack verification. Never inferred from a profitable ASIN
    or barcode match."""
    listing_approval_status: Literal["approved", "ungated_required", "unknown"] = "unknown"
    supplier_brand_authorization_status: Literal["authorized", "not_authorized", "unknown"] = "unknown"
    expiration_or_shelf_life_flag: bool = False
    hazmat_or_meltable_flag: bool = False
    ip_concern_flag: bool = False
    pack_bundle_compliance_status: Literal["compliant", "noncompliant", "unknown"] = "unknown"
    checked_at: Optional[datetime] = None
    checked_via: Optional[str] = None  # e.g. "SP-API Listings Restrictions" or "manual Seller Central check"
    notes: list[str] = Field(default_factory=list)


class CatalogScanResult(BaseModel):
    """One fully evaluated catalog row - the unit this feature's reports
    and exports are built from."""
    row: CatalogRow
    resolution: AsinResolution
    pricing: Optional[PricingSnapshot] = None
    cost_ledger: list[CostComponent] = Field(default_factory=list)
    roi: RoiResult = Field(default_factory=RoiResult)
    demand: DemandAssessment = Field(default_factory=DemandAssessment)
    eligibility: EligibilityCheck = Field(default_factory=EligibilityCheck)

    # A screening candidate, never a guaranteed purchase (section 6) - True
    # only when match is verified, cost data is complete, ROI tier is
    # TARGET_MET or NEGOTIATION_CANDIDATE, and demand status MEETS_MINIMUM.
    qualifies: bool = False
    qualification_notes: list[str] = Field(default_factory=list)

    data_freshness: Optional[datetime] = None
    data_completeness_pct: Optional[Decimal] = None


class CatalogScanRun(BaseModel):
    run_id: str
    supplier_name: str
    catalog_path: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    history_window_days: int = 90
    target_roi: Decimal = Decimal("0.10")
    highlight_threshold: Decimal = Decimal("0.08")
    minimum_monthly_sales: Decimal = Decimal("25")
    roi_convention: Literal["landed", "merchandise"] = "landed"
    is_live_data: bool = False
    results: list[CatalogScanResult] = Field(default_factory=list)
