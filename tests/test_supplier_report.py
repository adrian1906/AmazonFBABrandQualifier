from config import SUPPLIER_SCORING_WEIGHTS
from models import SupplierCandidate, SupplierQualificationResult, MarketplacePolicy
from supplier_scoring import assess_candidate
from supplier_report import (
    format_relationship_detail, format_ranked_report, format_do_not_pursue_section,
    format_missing_information_queue, format_contact_now_queue, format_brand_supplier_matrix,
)
from tests.factories import make_relationship


def test_relationship_detail_preserves_citation_and_retrieval_date():
    rel = make_relationship()
    text = format_relationship_detail(rel)
    assert "https://brand.example.com/distributors" in text
    assert "2026-09-15T10:00:00" in text
    assert "VERIFIED" in text


def test_ranked_report_orders_by_score_descending():
    high = make_relationship(brand_name="Brand High", supplier_id="sup_high")

    weak_candidate = SupplierCandidate(legal_business_name="Weak Co", role="unclear_intermediary")
    weak_qual = SupplierQualificationResult(
        supplier_legal_name="Weak Co", dimension_scores={k: 10 for k in SUPPLIER_SCORING_WEIGHTS}, rationale="weak"
    )
    low = make_relationship(brand_name="Brand Low", supplier_id="sup_low", candidate=weak_candidate, qualification=weak_qual, with_outreach=False)

    report = format_ranked_report([low, high])
    assert report.index("Acme Wholesale LLC") < report.index("Weak Co")


def test_do_not_pursue_section_includes_gate_reason():
    candidate = SupplierCandidate(
        legal_business_name="Prohibited Amazon Co", role="authorized_distributor",
        marketplace_policies=[MarketplacePolicy(marketplace="amazon", permission="PROHIBITED", scope="brand_specific")],
    )
    qualification = SupplierQualificationResult(
        supplier_legal_name="Prohibited Amazon Co", dimension_scores={k: 90 for k in SUPPLIER_SCORING_WEIGHTS}, rationale="x"
    )
    rel = make_relationship(brand_name="Some Brand", supplier_id="sup_bad", candidate=candidate, qualification=qualification, with_outreach=False)

    section = format_do_not_pursue_section([rel])
    assert "Prohibited Amazon Co" in section
    assert "PROHIBITED" in section.upper()
    assert rel.assessment.recommendation == "DO_NOT_PURSUE"


def test_missing_information_queue_lists_open_items():
    rel = make_relationship()
    queue = format_missing_information_queue([rel])
    assert "Opening order amount not yet confirmed" in queue


def test_contact_now_queue_only_includes_contact_now():
    contact_now = make_relationship(brand_name="Brand X", supplier_id="sup_x")
    assert contact_now.assessment.recommendation == "CONTACT_NOW"

    weak_candidate = SupplierCandidate(legal_business_name="Weak Co", role="unclear_intermediary")
    weak_qual = SupplierQualificationResult(
        supplier_legal_name="Weak Co", dimension_scores={k: 10 for k in SUPPLIER_SCORING_WEIGHTS}, rationale="weak"
    )
    not_ready = make_relationship(brand_name="Brand Y", supplier_id="sup_y", candidate=weak_candidate, qualification=weak_qual, with_outreach=False)

    queue = format_contact_now_queue([contact_now, not_ready])
    assert "Acme Wholesale LLC" in queue
    assert "Weak Co" not in queue


def test_brand_supplier_matrix_groups_by_brand():
    rel_a = make_relationship(brand_name="Brand A", supplier_id="sup_a")
    rel_b = make_relationship(brand_name="Brand B", supplier_id="sup_b")
    matrix = format_brand_supplier_matrix([rel_a, rel_b])
    assert "Brand A:" in matrix
    assert "Brand B:" in matrix
