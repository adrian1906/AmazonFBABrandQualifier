"""
End-to-end Supplier Qualifier pipeline tests, using frozen/mocked agent
responses (see tests/helpers.py's FakeRunner) so no network or paid-API
call happens. Covers the deterministic fixture scenarios from the spec:

  1. a brand with a direct reseller program
  2. a brand that officially names a U.S. distributor
  3. a regional manufacturer representative that must refer to a stocking distributor
  4. a wholesaler that lists a brand but lacks manufacturer-confirmed authorization
  5. a supplier whose Amazon permission is unknown
  6. a supplier whose Amazon resale is prohibited
  7. one supplier connected to multiple brands

allow_web_search=False in every test disables the escalation pass (it's
gated on `allow_web_search`), keeping each scenario to one research call
and one qualification call - escalation's merge logic is covered directly
in test_escalation_merge.py.
"""

from config import SUPPLIER_SCORING_WEIGHTS
from models import (
    SupplierResearchFindings, SupplierCandidate, SupplierQualificationResult,
    MarketplacePolicy, EvidenceItem, OutreachDraft, ManagerDecision, DraftScore,
)
from supplier_scoring import advance_lifecycle_state, LifecycleGateError
from supplier_workflow import run_supplier_research_for_brand
import supplier_persistence
from tests.helpers import patch_runner

import pytest


def _dims(value: int) -> dict[str, int]:
    return {k: value for k in SUPPLIER_SCORING_WEIGHTS}


_OUTREACH_RESPONSES = {
    "Supplier Relationship Outreach Agent": OutreachDraft(subject="Intro", body="Hello, we're R&T...", strategy="relationship"),
    "Supplier Procurement Outreach Agent": OutreachDraft(subject="Wholesale account inquiry", body="Hello...", strategy="procurement"),
    "Supplier Strategic Partnership Outreach Agent": OutreachDraft(subject="Partnership", body="Hello...", strategy="partnership"),
    "Outreach Manager": ManagerDecision(
        draft_scores=[DraftScore(strategy=s, score=80, explanation="ok") for s in ("relationship", "procurement", "partnership")],
        winning_strategy="procurement", winning_reason="Most direct.",
    ),
}


async def test_scenario_1_direct_reseller_program(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Lemax Corporation", website="https://lemax.com", role="manufacturer_direct",
        contact_method="https://lemax.com/dealer-application",
        brands_carried=["Lemax"],
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="This IS the brand's own direct dealer program",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://lemax.com/dealer-application", retrieved_at="2026-09-01T00:00:00",
        )],
    )
    findings = SupplierResearchFindings(brand_name="Lemax", direct_brand_program_found="yes", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Lemax Corporation", dimension_scores=_dims(85), rationale="direct program"),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result = await run_supplier_research_for_brand("Lemax", allow_web_search=False, use_cache=False)

    rel = result.relationships[0]
    assert rel.assessment.recommendation == "CONTACT_NOW"
    assert "authorization_not_verified" not in rel.assessment.score_breakdown.gates_triggered
    assert rel.outreach_drafts  # outreach was drafted


async def test_scenario_2_officially_named_us_distributor(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Northeast Gift Distributors Inc", website="https://negiftdist.com",
        role="authorized_distributor", brands_carried=["Pacific Giftware"],
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="account_specific")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="Named on brand's official distributor locator page",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://pacificgiftware.example.com/distributors", retrieved_at="2026-09-01T00:00:00",
        )],
    )
    findings = SupplierResearchFindings(brand_name="Pacific Giftware", direct_brand_program_found="no", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Northeast Gift Distributors Inc", dimension_scores=_dims(80), rationale="named distributor"),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result = await run_supplier_research_for_brand("Pacific Giftware", allow_web_search=False, use_cache=False)

    rel = result.relationships[0]
    assert rel.assessment.recommendation == "CONTACT_NOW"
    assert rel.assessment.candidate.role == "authorized_distributor"


async def test_scenario_3_manufacturer_rep_must_refer_to_distributor(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Mid-Atlantic Rep Group", website="https://midatlanticreps.com",
        role="manufacturer_representative", brands_carried=["BRK"],
        provides_itemized_invoices="no",
    )
    findings = SupplierResearchFindings(brand_name="BRK", direct_brand_program_found="unknown", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Mid-Atlantic Rep Group", dimension_scores=_dims(55), rationale="rep only"),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result = await run_supplier_research_for_brand("BRK", allow_web_search=False, use_cache=False)

    rel = result.relationships[0]
    assert "rep_without_stocking_evidence" in rel.assessment.score_breakdown.gates_triggered
    assert any("referral contact" in note for note in rel.assessment.gate_notes)
    assert rel.assessment.recommendation in ("CONTACT_NOW", "INVESTIGATE_FURTHER")  # still contactable, as a referral


async def test_scenario_4_wholesaler_lists_brand_without_confirmed_authorization(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Budget Home Goods Wholesale", website="https://budgethomegoods.com",
        role="stocking_wholesaler", brands_carried=["HEM"],
        provides_itemized_invoices="yes",
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="Lists HEM products for sale on their own site",
            evidence_state="DISTRIBUTOR_CLAIM", source_type="distributor_or_supplier_site",
            source_url="https://budgethomegoods.com/brands/hem", retrieved_at="2026-09-01T00:00:00",
        )],
    )
    findings = SupplierResearchFindings(brand_name="HEM", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Budget Home Goods Wholesale", dimension_scores=_dims(65), rationale="unverified"),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result = await run_supplier_research_for_brand("HEM", allow_web_search=False, use_cache=False)

    rel = result.relationships[0]
    assert "authorization_not_verified" in rel.assessment.score_breakdown.gates_triggered
    with pytest.raises(LifecycleGateError):
        advance_lifecycle_state(rel.assessment, "APPROVED_FOR_PURCHASE")


async def test_scenario_5_amazon_permission_unknown_stays_unknown(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Super Snouts Direct Supply Co", website="https://supersnoutssupply.com",
        role="authorized_distributor", brands_carried=["Super Snouts"],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="Confirmed via brand's dealer list",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://supersnouts.example.com/dealers", retrieved_at="2026-09-01T00:00:00",
        )],
        # No marketplace_policies entry at all - Amazon status was simply never found.
    )
    findings = SupplierResearchFindings(brand_name="Super Snouts", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Super Snouts Direct Supply Co", dimension_scores=_dims(75), rationale="good but amazon status unknown"),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result = await run_supplier_research_for_brand("Super Snouts", allow_web_search=False, use_cache=False)

    rel = result.relationships[0]
    assert "amazon_permission_unknown" in rel.assessment.score_breakdown.gates_triggered
    with pytest.raises(LifecycleGateError):
        advance_lifecycle_state(rel.assessment, "APPROVED_FOR_ASIN_ANALYSIS")


async def test_scenario_6_amazon_resale_prohibited_overrides_high_score(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Diamine Ink UK Direct", website="https://diamine.co.uk",
        role="manufacturer_direct", brands_carried=["Diamine"],
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PROHIBITED", scope="brand_specific")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="This is the brand's own site",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://diamine.co.uk", retrieved_at="2026-09-01T00:00:00",
        )],
    )
    findings = SupplierResearchFindings(brand_name="Diamine", direct_brand_program_found="yes", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Diamine Ink UK Direct", dimension_scores=_dims(95), rationale="excellent except amazon"),
        # Deliberately no outreach mocks - DO_NOT_PURSUE must mean outreach is never drafted, so these must never be called.
    }
    patch_runner(monkeypatch, responses)

    result = await run_supplier_research_for_brand("Diamine", allow_web_search=False, use_cache=False)

    rel = result.relationships[0]
    assert rel.assessment.recommendation == "DO_NOT_PURSUE"
    assert rel.outreach_drafts == {}
    assert rel.manager_decision is None


async def test_scenario_7_one_supplier_connected_to_multiple_brands(monkeypatch):
    shared_candidate_alpha = SupplierCandidate(
        legal_business_name="Shared Regional Supplier LLC", website="https://sharedsupplier.com",
        role="stocking_wholesaler", brands_carried=["Zebra"],
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="Verified via brand dealer list",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://zebra.example.com/dealers", retrieved_at="2026-09-01T00:00:00",
        )],
    )
    shared_candidate_beta = SupplierCandidate(
        legal_business_name="Shared Regional Supplier, Inc.", website="https://sharedsupplier.com/wholesale",
        role="stocking_wholesaler", brands_carried=["Midori"],
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="Verified via brand dealer list",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://midori.example.com/dealers", retrieved_at="2026-09-01T00:00:00",
        )],
    )
    findings_zebra = SupplierResearchFindings(brand_name="Zebra", candidates=[shared_candidate_alpha])
    findings_midori = SupplierResearchFindings(brand_name="Midori", candidates=[shared_candidate_beta])

    responses = {
        "Supplier Research Agent": [findings_zebra, findings_midori],
        "Supplier Qualification Agent": SupplierQualificationResult(supplier_legal_name="Shared Regional Supplier", dimension_scores=_dims(80), rationale="good"),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result_zebra = await run_supplier_research_for_brand("Zebra", allow_web_search=False, use_cache=False)
    result_midori = await run_supplier_research_for_brand("Midori", allow_web_search=False, use_cache=False)

    id_zebra = result_zebra.relationships[0].supplier_id
    id_midori = result_midori.relationships[0].supplier_id
    assert id_zebra == id_midori

    all_for_supplier = supplier_persistence.find_relationships(supplier_id=id_zebra)
    assert len(all_for_supplier) == 2
    brand_names = {supplier_persistence.load_relationship(p).brand_name for p in all_for_supplier}
    assert brand_names == {"Zebra", "Midori"}
