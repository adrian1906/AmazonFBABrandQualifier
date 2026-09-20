"""
Regression test for a pre-existing bug found while building the Supplier
Qualifier: find_results() compared a raw (space-containing) name fragment
against slugified (underscore-containing) filenames, so a multi-word
company name fragment - the common case, and the exact usage documented in
review_one.py's own docstring - never matched its own saved file.
"""

from models import Prospect, ResearchFindings, QualificationResult, OutreachDraft, ManagerDecision, DraftScore
from persistence import save_result, find_results
from workflow import WorkflowResult


def _make_result(company_name: str) -> WorkflowResult:
    prospect = Prospect(company_name=company_name)
    research = ResearchFindings(company_name=company_name)
    qualification = QualificationResult(
        overall_score=80, recommendation="PURSUE",
        wholesale_relationship_availability=80, product_business_fit=80,
        reseller_program_accessibility=80, contact_information_availability=80,
        marketplace_compatibility=80, amazon_resale_clarity=80,
        professional_operations_evidence=80, barriers_or_restrictions=80,
        recommended_next_action="contact them",
    )
    drafts = {"relationship": OutreachDraft(subject="Hi", body="Body", strategy="relationship")}
    manager = ManagerDecision(
        draft_scores=[DraftScore(strategy="relationship", score=80, explanation="ok")],
        winning_strategy="relationship", winning_reason="best",
    )
    return WorkflowResult(prospect=prospect, research=research, qualification=qualification, drafts=drafts, manager_decision=manager)


def test_find_results_matches_multi_word_company_name(tmp_path):
    result = _make_result("Northwind Outdoor Gear Co.")
    save_result(result, directory=tmp_path)

    matches = find_results("Northwind Outdoor", directory=tmp_path)
    assert len(matches) == 1

    matches = find_results("northwind outdoor gear co", directory=tmp_path)
    assert len(matches) == 1


def test_find_results_still_matches_single_word_fragment(tmp_path):
    result = _make_result("Diamine")
    save_result(result, directory=tmp_path)
    assert len(find_results("diamine", directory=tmp_path)) == 1
