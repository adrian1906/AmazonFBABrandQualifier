"""
Unit tests for the deterministic gates in supplier_scoring.py - these are
the hard business/safety rules from the spec, enforced in plain Python so
they can't drift regardless of what an LLM decides.
"""

import pytest

from models import SupplierCandidate, SupplierQualificationResult, MarketplacePolicy, EvidenceItem
from supplier_scoring import assess_candidate, advance_lifecycle_state, LifecycleGateError

_ALL_HIGH = {
    "Brand authorization evidence": 95, "Marketplace/channel compatibility": 95,
    "Invoice and supply-chain defensibility": 95, "Account accessibility for R&T": 95,
    "Business legitimacy and contact quality": 95, "Catalog/data usability": 95,
    "Commercial terms and initial-order accessibility": 95, "Geographic/service fit": 95,
    "Operational fulfillment fit": 95, "Risk profile": 95,
}


def _qual(name: str, scores: dict[str, int] | None = None) -> SupplierQualificationResult:
    return SupplierQualificationResult(supplier_legal_name=name, dimension_scores=scores or dict(_ALL_HIGH), rationale="test")


def test_distributor_claim_is_not_treated_as_verified_authorization():
    """A distributor simply listing/claiming a brand must not be auto-verified."""
    candidate = SupplierCandidate(
        legal_business_name="Acme Wholesale LLC",
        website="https://acmewholesale.com",
        role="authorized_distributor",
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="lists the brand on their site",
            evidence_state="DISTRIBUTOR_CLAIM", source_type="distributor_or_supplier_site",
        )],
    )
    assessment = assess_candidate(candidate, _qual("Acme Wholesale LLC"))
    assert "authorization_not_verified" in assessment.score_breakdown.gates_triggered
    with pytest.raises(LifecycleGateError):
        advance_lifecycle_state(assessment, "APPROVED_FOR_PURCHASE")


def test_unknown_amazon_permission_stays_unknown_not_approved():
    candidate = SupplierCandidate(legal_business_name="No Policy Stated Co", role="authorized_distributor")
    assessment = assess_candidate(candidate, _qual("No Policy Stated Co"))
    assert "amazon_permission_unknown" in assessment.score_breakdown.gates_triggered
    with pytest.raises(LifecycleGateError):
        advance_lifecycle_state(assessment, "APPROVED_FOR_ASIN_ANALYSIS")


def test_prohibited_amazon_resale_overrides_high_score():
    candidate = SupplierCandidate(
        legal_business_name="Great Distributor Inc",
        role="authorized_distributor",
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PROHIBITED", scope="brand_specific")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="verified on brand site",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
        )],
    )
    assessment = assess_candidate(candidate, _qual("Great Distributor Inc"))  # all dimensions scored 95
    assert assessment.recommendation == "DO_NOT_PURSUE"
    assert "amazon_resale_prohibited" in assessment.score_breakdown.gates_triggered


def test_manufacturer_rep_without_stocking_evidence_is_flagged_as_referral_only():
    candidate = SupplierCandidate(legal_business_name="Regional Rep Co", role="manufacturer_representative", provides_itemized_invoices="unknown")
    assessment = assess_candidate(candidate, _qual("Regional Rep Co"))
    assert "rep_without_stocking_evidence" in assessment.score_breakdown.gates_triggered
    assert any("referral contact" in note for note in assessment.gate_notes)


def test_manufacturer_rep_with_confirmed_stocking_evidence_is_not_flagged():
    candidate = SupplierCandidate(
        legal_business_name="Rep That Also Stocks Co", role="manufacturer_representative",
        provides_itemized_invoices="yes",
        evidence=[EvidenceItem(
            claim_field="stocking_evidence", claim_value="invoices directly from its own warehouse",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
        )],
    )
    assessment = assess_candidate(candidate, _qual("Rep That Also Stocks Co"))
    assert "rep_without_stocking_evidence" not in assessment.score_breakdown.gates_triggered


def test_liquidator_role_is_never_recommended():
    candidate = SupplierCandidate(legal_business_name="Bulk Liquidation Co", role="marketplace_broker_liquidator")
    assessment = assess_candidate(candidate, _qual("Bulk Liquidation Co"))
    assert assessment.recommendation == "DO_NOT_PURSUE"


def test_risk_flag_keyword_forces_do_not_pursue_even_with_good_role_and_score():
    candidate = SupplierCandidate(
        legal_business_name="Suspicious Source LLC", role="stocking_wholesaler",
        risk_flags=["Suspicious ungating claims reported by other sellers"],
    )
    assessment = assess_candidate(candidate, _qual("Suspicious Source LLC"))
    assert assessment.recommendation == "DO_NOT_PURSUE"
    assert "liquidation_or_unverifiable_risk" in assessment.score_breakdown.gates_triggered


def test_automatic_pipeline_never_assigns_beyond_researched():
    candidate = SupplierCandidate(
        legal_business_name="Verified Direct Co", role="manufacturer_direct",
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="this is R&T's direct account with the brand",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
        )],
    )
    assessment = assess_candidate(candidate, _qual("Verified Direct Co"))
    assert assessment.lifecycle_state == "RESEARCHED"
    # a fully verified, permitted candidate CAN be manually advanced by a human -
    # advance_lifecycle_state does not itself set the state, only validates it:
    assert advance_lifecycle_state(assessment, "APPROVED_FOR_PURCHASE") == "APPROVED_FOR_PURCHASE"


def test_score_breakdown_weights_sum_matches_config():
    from config import SUPPLIER_SCORING_WEIGHTS
    candidate = SupplierCandidate(legal_business_name="Mid Co", role="authorized_distributor")
    assessment = assess_candidate(candidate, _qual("Mid Co", {k: 50 for k in SUPPLIER_SCORING_WEIGHTS}))
    assert assessment.score_breakdown.final_score == 50  # all dims at 50% of their weight -> 50% of 100 total
