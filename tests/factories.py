"""Shared test-data builders for BrandSupplierRelationship fixtures."""

from datetime import datetime

from config import SUPPLIER_SCORING_WEIGHTS
from models import (
    SupplierCandidate, SupplierQualificationResult, MarketplacePolicy, EvidenceItem,
    BrandSupplierRelationship, OutreachDraft, ManagerDecision, DraftScore,
)
from supplier_scoring import assess_candidate


def make_candidate(**overrides) -> SupplierCandidate:
    defaults = dict(
        legal_business_name="Acme Wholesale LLC",
        website="https://acmewholesale.com",
        physical_address="123 Main St, Baltimore, MD",
        role="authorized_distributor",
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="named as authorized distributor on brand site",
            evidence_state="VERIFIED", source_type="official_brand_manufacturer_site",
            source_url="https://brand.example.com/distributors", source_title="Find a Distributor",
            excerpt="Acme Wholesale LLC is our authorized US distributor.",
            retrieved_at="2026-09-15T10:00:00",
        )],
    )
    defaults.update(overrides)
    return SupplierCandidate(**defaults)


def make_qualification(name: str, score: int = 80, **overrides) -> SupplierQualificationResult:
    defaults = dict(
        supplier_legal_name=name,
        dimension_scores={k: score for k in SUPPLIER_SCORING_WEIGHTS},
        rationale="Test rationale",
        missing_information=["Opening order amount not yet confirmed"],
    )
    defaults.update(overrides)
    return SupplierQualificationResult(**defaults)


def make_relationship(
    brand_name="Lemax",
    supplier_id="sup_test123",
    candidate: SupplierCandidate | None = None,
    qualification: SupplierQualificationResult | None = None,
    with_outreach: bool = True,
) -> BrandSupplierRelationship:
    candidate = candidate or make_candidate()
    qualification = qualification or make_qualification(candidate.legal_business_name, score=80)
    assessment = assess_candidate(candidate, qualification)

    drafts: dict[str, OutreachDraft] = {}
    manager_decision: ManagerDecision | None = None
    if with_outreach:
        drafts = {
            "relationship": OutreachDraft(subject="Introduction from R&T", body="Hello...", strategy="relationship"),
            "procurement": OutreachDraft(subject="Wholesale account inquiry", body="Hello...", strategy="procurement"),
            "partnership": OutreachDraft(subject="Partnership opportunity", body="Hello...", strategy="partnership"),
        }
        manager_decision = ManagerDecision(
            draft_scores=[DraftScore(strategy=s, score=80, explanation="ok") for s in drafts],
            winning_strategy="procurement", winning_reason="Most direct and likely to get a response.",
        )

    now = datetime.now().isoformat(timespec="seconds")
    return BrandSupplierRelationship(
        brand_name=brand_name, supplier_id=supplier_id, assessment=assessment,
        outreach_drafts=drafts, manager_decision=manager_decision,
        lifecycle_state=assessment.lifecycle_state,
        first_researched_at=now, last_researched_at=now, research_run_ids=["run_test"],
    )
