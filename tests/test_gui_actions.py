"""
Tests for the GUI's sync action wrappers - no Streamlit involved, agent
calls mocked via tests/helpers.py's FakeRunner (same pattern as the rest
of the suite), and all persistence redirected to tmp_path by the autouse
isolated_supplier_data fixture in conftest.py.
"""

from datetime import datetime

import gui_actions as actions
import persistence
import supplier_persistence
from models import (
    DraftScore, ManagerDecision, OutreachDraft, Prospect, QualificationResult,
    ResearchFindings, SupplierResearchFindings, SupplierCandidate, MarketplacePolicy,
    EvidenceItem, SupplierQualificationResult,
)
from tests.factories import make_relationship
from tests.helpers import patch_runner
from workflow import WorkflowResult

_BRAND_OUTREACH_RESPONSES = {
    "Relationship Outreach Agent": OutreachDraft(subject="Hi", body="Body 1", strategy="relationship"),
    "Procurement Outreach Agent": OutreachDraft(subject="Hi", body="Body 2", strategy="procurement"),
    "Strategic Partnership Outreach Agent": OutreachDraft(subject="Hi", body="Body 3", strategy="partnership"),
    "Outreach Manager": ManagerDecision(
        draft_scores=[DraftScore(strategy=s, score=80, explanation="ok") for s in ("relationship", "procurement", "partnership")],
        winning_strategy="procurement", winning_reason="best",
    ),
}


def _brand_qualification() -> QualificationResult:
    return QualificationResult(
        overall_score=80, recommendation="PURSUE",
        wholesale_relationship_availability=80, product_business_fit=80,
        reseller_program_accessibility=80, contact_information_availability=80,
        marketplace_compatibility=80, amazon_resale_clarity=80,
        professional_operations_evidence=80, barriers_or_restrictions=80,
        recommended_next_action="contact them",
    )


def test_run_single_brand_lookup_saves_and_lists(monkeypatch):
    responses = {
        "Brand Research Agent": ResearchFindings(company_name="Lemax", company_description="Toy village brand"),
        "Qualification Agent": _brand_qualification(),
        **_BRAND_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    result = actions.run_single_brand_lookup("Lemax", "", "", allow_web_search=False)

    assert result.prospect.company_name == "Lemax"
    saved = actions.list_brand_results()
    assert len(saved) == 1
    assert saved[0].prospect.company_name == "Lemax"


def test_approve_brand_outreach_writes_outbox():
    prospect = Prospect(company_name="Lemax")
    research = ResearchFindings(company_name="Lemax")
    qualification = _brand_qualification()
    drafts = {"relationship": OutreachDraft(subject="Hi", body="Body", strategy="relationship")}
    manager = ManagerDecision(
        draft_scores=[DraftScore(strategy="relationship", score=80, explanation="ok")],
        winning_strategy="relationship", winning_reason="best",
    )
    result = WorkflowResult(prospect=prospect, research=research, qualification=qualification, drafts=drafts, manager_decision=manager)

    path = actions.approve_brand_outreach(result)
    assert path.exists()
    assert "NOT SENT" in path.read_text(encoding="utf-8")


def test_regenerate_brand_outreach_updates_and_resaves(monkeypatch):
    prospect = Prospect(company_name="Lemax")
    research = ResearchFindings(company_name="Lemax")
    qualification = _brand_qualification()
    drafts = {"relationship": OutreachDraft(subject="Old", body="Old body", strategy="relationship")}
    manager = ManagerDecision(
        draft_scores=[DraftScore(strategy="relationship", score=80, explanation="ok")],
        winning_strategy="relationship", winning_reason="best",
    )
    result = WorkflowResult(prospect=prospect, research=research, qualification=qualification, drafts=drafts, manager_decision=manager)
    persistence.save_result(result)

    patch_runner(monkeypatch, _BRAND_OUTREACH_RESPONSES)
    updated = actions.regenerate_brand_outreach(result)

    assert updated.winning_draft.subject == "Hi"
    assert len(actions.list_brand_results()) == 1  # re-saved in place count-wise (new timestamped file is fine either way)


def test_run_single_supplier_lookup_persists_relationship(monkeypatch):
    candidate = SupplierCandidate(
        legal_business_name="Acme Wholesale LLC", website="https://acmewholesale.com",
        role="authorized_distributor", brands_carried=["Lemax"],
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PERMITTED", scope="general")],
        evidence=[EvidenceItem(
            claim_field="brand_authorization", claim_value="verified", evidence_state="VERIFIED",
            source_type="official_brand_manufacturer_site",
        )],
    )
    findings = SupplierResearchFindings(brand_name="Lemax", candidates=[candidate])
    responses = {
        "Supplier Research Agent": findings,
        "Supplier Qualification Agent": SupplierQualificationResult(
            supplier_legal_name="Acme Wholesale LLC",
            dimension_scores={k: 80 for k in [
                "Brand authorization evidence", "Marketplace/channel compatibility",
                "Invoice and supply-chain defensibility", "Account accessibility for R&T",
                "Business legitimacy and contact quality", "Catalog/data usability",
                "Commercial terms and initial-order accessibility", "Geographic/service fit",
                "Operational fulfillment fit", "Risk profile",
            ]},
            rationale="good",
        ),
        "Supplier Relationship Outreach Agent": OutreachDraft(subject="Hi", body="Body", strategy="relationship"),
        "Supplier Procurement Outreach Agent": OutreachDraft(subject="Hi", body="Body", strategy="procurement"),
        "Supplier Strategic Partnership Outreach Agent": OutreachDraft(subject="Hi", body="Body", strategy="partnership"),
        "Outreach Manager": ManagerDecision(
            draft_scores=[DraftScore(strategy=s, score=80, explanation="ok") for s in ("relationship", "procurement", "partnership")],
            winning_strategy="procurement", winning_reason="best",
        ),
    }
    patch_runner(monkeypatch, responses)

    result = actions.run_single_supplier_lookup("Lemax", "", allow_web_search=False)

    assert len(result.relationships) == 1
    all_rels = actions.list_supplier_relationships()
    assert len(all_rels) == 1
    assert all_rels[0].brand_name == "Lemax"


def test_approve_supplier_relationship_writes_outbox_and_advances_lifecycle():
    rel = make_relationship()
    supplier_persistence.save_relationship(rel)

    path = actions.approve_supplier_relationship(rel)
    assert path.exists()
    assert rel.lifecycle_state == "CONTACT_APPROVED"


def test_list_pending_supplier_relationships_excludes_contacted():
    rel_pending = make_relationship(brand_name="Brand A", supplier_id="sup_a")
    rel_contacted = make_relationship(brand_name="Brand B", supplier_id="sup_b")
    rel_contacted.lifecycle_state = "CONTACTED"
    supplier_persistence.save_relationship(rel_pending)
    supplier_persistence.save_relationship(rel_contacted)

    pending = actions.list_pending_supplier_relationships()
    brand_names = {r.brand_name for r in pending}
    assert brand_names == {"Brand A"}


def test_list_pending_excludes_relationships_without_outreach():
    rel_no_outreach = make_relationship(brand_name="Brand C", supplier_id="sup_c", with_outreach=False)
    supplier_persistence.save_relationship(rel_no_outreach)

    pending = actions.list_pending_supplier_relationships()
    assert all(r.brand_name != "Brand C" for r in pending)


def test_list_supplier_batch_ids_reflects_saved_manifests():
    assert actions.list_supplier_batch_ids() == []
    supplier_persistence.save_batch_manifest("supbatch_1", {"batch_id": "supbatch_1", "created_at": datetime.now().isoformat()})
    assert actions.list_supplier_batch_ids() == ["supbatch_1"]
