from models import SupplierCandidate, SupplierResearchFindings, MarketplacePolicy, EvidenceItem
from supplier_workflow import _needs_escalation, _merge_escalation


def test_needs_escalation_flags_unknown_authorization_and_amazon_and_identity():
    candidate = SupplierCandidate(legal_business_name="Mystery Co")
    missing = _needs_escalation(candidate)
    assert "brand_authorization" in missing
    assert "amazon_marketplace_permission" in missing
    assert "legal_identity_or_location" in missing


def test_needs_escalation_empty_when_everything_known():
    candidate = SupplierCandidate(
        legal_business_name="Known Co", website="https://known.com", physical_address="1 Main St",
        role="authorized_distributor", provides_itemized_invoices="yes",
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="verified", evidence_state="VERIFIED",
            source_type="official_brand_manufacturer_site",
        )],
    )
    assert _needs_escalation(candidate) == []


def test_merge_escalation_fills_unknown_fields_without_overwriting_known_ones():
    candidate = SupplierCandidate(legal_business_name="Mystery Co", physical_address=None)
    escalation_findings = SupplierResearchFindings(
        brand_name="X",
        candidates=[SupplierCandidate(legal_business_name="Mystery Co", physical_address="123 Elm St, Baltimore, MD")],
    )
    _merge_escalation(candidate, escalation_findings)
    assert candidate.physical_address == "123 Elm St, Baltimore, MD"


def test_merge_escalation_flags_conflict_instead_of_silently_overwriting():
    candidate = SupplierCandidate(legal_business_name="Mystery Co", physical_address="1 First St")
    escalation_findings = SupplierResearchFindings(
        brand_name="X",
        candidates=[SupplierCandidate(legal_business_name="Mystery Co", physical_address="2 Second St")],
    )
    _merge_escalation(candidate, escalation_findings)
    assert candidate.physical_address == "1 First St"  # not silently overwritten
    conflicts = [e for e in candidate.evidence if e.evidence_state == "CONFLICTING"]
    assert any(e.claim_field == "physical_address" for e in conflicts)


def test_merge_escalation_appends_new_evidence_without_duplicating():
    item = EvidenceItem(claim_field="brand_authorization", claim_value="verified", evidence_state="VERIFIED", source_type="official_brand_manufacturer_site")
    candidate = SupplierCandidate(legal_business_name="Mystery Co", evidence=[item])
    escalation_findings = SupplierResearchFindings(
        brand_name="X",
        candidates=[SupplierCandidate(legal_business_name="Mystery Co", evidence=[item])],  # same evidence again
    )
    _merge_escalation(candidate, escalation_findings)
    assert len(candidate.evidence) == 1  # not duplicated
