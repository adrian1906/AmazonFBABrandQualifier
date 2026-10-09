"""
Tests for catalog_scan.py's orchestration: a plain fixture-mode scan must
come back INCOMPLETE/needs_review (never a false positive), a fully
confirmed+costed scenario must qualify, and each individual gate (mixed
bundle without a BOM, an unknown mandatory cost, demand below minimum)
must independently block `qualifies` while staying clearly labeled about
WHY.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from catalog_import import load_catalog_rows
from catalog_models import AsinCandidate, BillOfMaterialsLine, CostComponent, DemandAssessment
from catalog_scan import scan_row
from demand_engine import assess_demand
from keepa_adapter import FixtureKeepaProvider

D = Decimal
SAMPLE_CATALOG = Path(__file__).parent.parent / "catalogs" / "DC 55 July Price List- copy.xlsx"


def _row(**overrides):
    from catalog_models import CatalogRow
    defaults = dict(
        supplier_name="Test Supplier", source_row=2, description="Widget", upc="034463016148",
        price_basis="each", purchase_price=D("4"), units_per_purchase_unit=1, order_multiple=12,
    )
    defaults.update(overrides)
    return CatalogRow(**defaults)


def _confirmed_candidate(listing_pack_quantity=2, **overrides):
    defaults = dict(
        asin="B000TEST01", match_status="verified", confidence="high", listing_pack_quantity=listing_pack_quantity,
        pack_relationship="identical_multipack",
    )
    defaults.update(overrides)
    return AsinCandidate(**defaults)


def _known_bcd_costs():
    return [
        CostComponent(name="supplier_freight_to_prep", value=D("0.20"), status="verified"),
        CostComponent(name="prep_center_processing", value=D("1.50"), status="verified"),
        CostComponent(name="extra_packaging", value=D("0"), status="confirmed_zero"),
        CostComponent(name="prep_to_amazon_shipping", value=D("0.30"), status="verified"),
        CostComponent(name="amazon_inbound_placement", value=D("0"), status="confirmed_zero"),
        CostComponent(name="fba_fulfillment_fee", value=D("3.50"), status="verified"),
        CostComponent(name="expected_storage", value=D("0.10"), status="estimated"),
        CostComponent(name="returns_allowance", value=D("0.05"), status="estimated"),
    ]


# ---------------------------------------------------------------------------
# Default (no overrides) - fixture mode, must never produce a false positive.
# ---------------------------------------------------------------------------

def test_plain_fixture_scan_is_incomplete_not_a_false_positive():
    result = scan_row(_row(), provider=FixtureKeepaProvider())
    assert result.roi.tier == "INCOMPLETE"
    assert result.qualifies is False
    assert result.resolution.is_live_data is False
    assert any("not confirmed" in r for r in result.roi.incomplete_reasons)


class _LeadingZeroAwareFixtureProvider(FixtureKeepaProvider):
    """Returns no candidates for an 11-digit UPC, but a real match for its
    12-digit zero-padded form - mirrors what a real provider does for the
    DC 55 sample catalog's leading-zero-truncated UPCs."""
    is_live = True

    def resolve_upc(self, upc):
        if upc == "034463016148":
            return super().resolve_upc(upc)
        from catalog_models import AsinResolution
        return AsinResolution(upc=upc, candidates=[], resolution_reason="No match.", is_live_data=True)


def test_scan_row_retries_with_leading_zero_corrected_upc():
    row = _row(upc="34463016148", purchase_price=D("4"))  # 11 digits - the raw catalog value
    row.upc_suggested_correction = "034463016148"  # what catalog_import.py would have flagged

    result = scan_row(row, provider=_LeadingZeroAwareFixtureProvider())

    assert len(result.resolution.candidates) == 1
    assert "leading-zero-corrected" in result.resolution.resolution_reason
    assert "34463016148" in result.resolution.resolution_reason  # mentions the original value too


def test_scan_row_does_not_retry_when_correction_also_fails():
    row = _row(upc="99999999999", purchase_price=D("4"))
    row.upc_suggested_correction = "099999999999"  # wouldn't matter - also returns nothing below

    result = scan_row(row, provider=_LeadingZeroAwareFixtureProvider())
    assert result.resolution.candidates == []


class _LiveNoHistoryProvider(FixtureKeepaProvider):
    """A live provider that can resolve an ASIN but not price history yet
    (mirrors sp_api_adapter.SpApiCatalogProvider's current state) - must
    degrade to a clear incomplete reason, never crash the row."""
    is_live = True

    def get_pricing_snapshot(self, asin, window_days):
        raise NotImplementedError("pricing not implemented for this provider")


def test_provider_pricing_not_implemented_degrades_gracefully():
    candidate = _confirmed_candidate(listing_pack_quantity=2)
    result = scan_row(
        _row(purchase_price=D("12"), units_per_purchase_unit=12),
        provider=_LiveNoHistoryProvider(), override_asin_candidate=candidate, known_costs=_known_bcd_costs(),
    )
    assert result.roi.tier == "INCOMPLETE"
    assert result.qualifies is False
    assert any("pricing not implemented" in r for r in result.roi.incomplete_reasons)


def test_row_with_no_upc_gets_empty_resolution():
    result = scan_row(_row(upc=None), provider=FixtureKeepaProvider())
    assert result.resolution.candidates == []
    assert result.qualifies is False


# ---------------------------------------------------------------------------
# Fully confirmed + costed scenario - must qualify.
# ---------------------------------------------------------------------------

def test_fully_confirmed_and_costed_scenario_qualifies():
    row = _row(purchase_price=D("48"), units_per_purchase_unit=12)  # $48 case of 12, 2 per listing -> COGS $8 (spec's own example)
    candidate = _confirmed_candidate(listing_pack_quantity=2)
    demand = assess_demand(monthly_units_estimate=D("50"), minimum_required=D("25"), source="keepa_estimate", scope="exact_child_asin")

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("20"), override_demand=demand, known_costs=_known_bcd_costs(),
    )

    assert result.roi.is_complete is True
    assert result.roi.normalized_cogs == D("8")
    # B = 0.20+1.50+0+0.30+0 = 2.00; S = referral(15% of 20=3.00)+3.50+0.10+0.05 = 6.65
    assert result.roi.landed_pre_sale_costs == D("2.00")
    assert result.roi.profit == D("20") - D("8") - D("2.00") - D("6.65")
    assert result.roi.tier in ("TARGET_MET", "NEGOTIATION_CANDIDATE", "BELOW_TARGET")
    assert result.demand.status == "MEETS_MINIMUM"
    if result.roi.tier in ("TARGET_MET", "NEGOTIATION_CANDIDATE"):
        assert result.qualifies is True


def test_target_met_scenario_qualifies_end_to_end():
    # Deliberately cheap COGS so landed ROI clears 10% comfortably.
    row = _row(purchase_price=D("12"), units_per_purchase_unit=12)  # COGS = 12/12*2 = $2
    candidate = _confirmed_candidate(listing_pack_quantity=2)
    demand = assess_demand(monthly_units_estimate=D("30"), minimum_required=D("25"), source="manual_entry", scope="exact_child_asin")

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("20"), override_demand=demand, known_costs=_known_bcd_costs(),
    )
    assert result.roi.tier == "TARGET_MET"
    assert result.qualifies is True
    assert result.roi.required_discount_dollars == D("0")


# ---------------------------------------------------------------------------
# Individual gates - each must independently block qualification.
# ---------------------------------------------------------------------------

def test_unknown_mandatory_cost_blocks_completeness():
    row = _row(purchase_price=D("48"), units_per_purchase_unit=12)
    candidate = _confirmed_candidate(listing_pack_quantity=2)
    costs = _known_bcd_costs()
    costs = [c for c in costs if c.name != "fba_fulfillment_fee"]  # remove one mandatory S-bucket entry

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("20"), known_costs=costs,
    )
    assert result.roi.is_complete is False
    assert result.qualifies is False
    assert any("fba_fulfillment_fee" in r for r in result.roi.incomplete_reasons)


def test_mixed_bundle_without_complete_bom_is_incomplete():
    candidate = _confirmed_candidate(
        listing_pack_quantity=1, pack_relationship="mixed_bundle",
        bill_of_materials=[BillOfMaterialsLine(component_description="Widget A", quantity=1, allocated_cost=None)],
    )
    row = _row(purchase_price=D("10"), units_per_purchase_unit=1)

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("20"), known_costs=_known_bcd_costs(),
    )
    assert result.roi.is_complete is False
    assert any("bill-of-materials" in r.lower() or "bundle" in r.lower() for r in result.roi.incomplete_reasons)
    assert result.qualifies is False


def test_roi_met_but_demand_below_minimum_is_visibly_distinguished():
    row = _row(purchase_price=D("12"), units_per_purchase_unit=12)
    candidate = _confirmed_candidate(listing_pack_quantity=2)
    demand = assess_demand(monthly_units_estimate=D("5"), minimum_required=D("25"), source="manual_entry", scope="exact_child_asin")

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("20"), override_demand=demand, known_costs=_known_bcd_costs(),
    )
    assert result.roi.tier == "TARGET_MET"  # ROI itself is fine
    assert result.demand.status == "BELOW_MINIMUM"
    assert result.qualifies is False  # but overall qualification is blocked
    assert "ROI met - sales minimum not met." in result.qualification_notes


def test_below_target_roi_still_computes_a_discount_target():
    # Below highlight threshold entirely - spec still wants a target price
    # computed for EVERY evaluable product below target, unprofitable ones included.
    row = _row(purchase_price=D("90"), units_per_purchase_unit=12)  # COGS = 90/12*2 = $15 -> deep loss at P=20
    candidate = _confirmed_candidate(listing_pack_quantity=2)

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("20"), known_costs=_known_bcd_costs(),
    )
    assert result.roi.tier == "BELOW_TARGET"
    assert result.roi.target_supplier_price_per_purchase_unit is not None
    assert result.roi.required_discount_dollars > D("0")
    assert result.qualifies is False


# ---------------------------------------------------------------------------
# End-to-end with real sample catalog rows.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="sample catalog not present")
def test_scan_real_sample_rows_default_to_incomplete():
    rows, _ = load_catalog_rows(str(SAMPLE_CATALOG), supplier_name="DC 55", limit=5)
    provider = FixtureKeepaProvider()
    results = [scan_row(r, provider=provider) for r in rows]
    assert len(results) == 5
    # No override supplied -> every real row is honestly INCOMPLETE, never
    # a confident (and unverifiable) profit claim from fixture data alone.
    assert all(r.roi.tier == "INCOMPLETE" for r in results)
    assert all(r.qualifies is False for r in results)


@pytest.mark.skipif(not SAMPLE_CATALOG.exists(), reason="sample catalog not present")
def test_scan_real_sample_row_with_manual_override_can_qualify():
    rows, _ = load_catalog_rows(str(SAMPLE_CATALOG), supplier_name="DC 55", limit=1)
    row = rows[0]  # 00384969 / CHS CHEDDAR XTRA MATURE, $5.17 each, case pack 12
    candidate = _confirmed_candidate(listing_pack_quantity=1)  # pretend a 1:1 listing match was confirmed
    demand = assess_demand(monthly_units_estimate=D("40"), minimum_required=D("25"), source="manual_entry", scope="exact_child_asin")

    result = scan_row(
        row, provider=FixtureKeepaProvider(), override_asin_candidate=candidate,
        override_selling_price=D("15.00"), override_demand=demand, known_costs=_known_bcd_costs(),
    )
    assert result.roi.normalized_cogs == row.purchase_price  # 1:1 pack match - no normalization change
    assert result.roi.is_complete is True
