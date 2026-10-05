"""
Regression test for a pre-existing bug found while building the Supplier
Qualifier: find_results() compared a raw (space-containing) name fragment
against slugified (underscore-containing) filenames, so a multi-word
company name fragment - the common case, and the exact usage documented in
review_one.py's own docstring - never matched its own saved file.
"""

import json
from datetime import datetime, timedelta

from models import Prospect, ResearchFindings, QualificationResult, OutreachDraft, ManagerDecision, DraftScore
from persistence import save_result, find_results, scored_company_names, result_to_dict, _slugify, has_usable_name
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


def test_scored_company_names_reflects_saved_results(tmp_path):
    save_result(_make_result("Diamine"), directory=tmp_path)
    save_result(_make_result("Towa"), directory=tmp_path)

    names = scored_company_names(directory=tmp_path)

    assert "diamine" in names
    assert "towa" in names
    assert "unrelated company" not in names


def test_scored_company_names_matches_regardless_of_case_or_punctuation(tmp_path):
    save_result(_make_result("Northwind Outdoor Gear Co."), directory=tmp_path)

    names = scored_company_names(directory=tmp_path)

    from entity_resolution import normalize_company_name
    assert normalize_company_name("northwind outdoor gear co") in names


def test_scored_company_names_empty_directory(tmp_path):
    assert scored_company_names(directory=tmp_path / "does_not_exist") == set()


def _save_result_at(result: WorkflowResult, directory, when: datetime) -> None:
    """Writes a saved-result file with an explicit (possibly backdated)
    filename timestamp, bypassing save_result()'s datetime.now() stamping -
    for testing staleness, which real saves can't control."""
    directory.mkdir(exist_ok=True)
    stamp = when.strftime("%Y%m%d_%H%M%S")
    path = directory / f"{_slugify(result.prospect.company_name)}_{stamp}.json"
    path.write_text(json.dumps(result_to_dict(result), indent=2), encoding="utf-8")


def test_scored_company_names_excludes_stale_result(tmp_path):
    from entity_resolution import normalize_company_name

    old = datetime.now() - timedelta(days=200)
    _save_result_at(_make_result("Old News Co"), tmp_path, old)
    key = normalize_company_name("Old News Co")

    assert key not in scored_company_names(directory=tmp_path, max_age_days=90)
    # No max_age_days at all - the old default behavior - still counts it.
    assert key in scored_company_names(directory=tmp_path)


def test_scored_company_names_includes_fresh_result_within_window(tmp_path):
    from entity_resolution import normalize_company_name

    recent = datetime.now() - timedelta(days=10)
    _save_result_at(_make_result("Fresh Co"), tmp_path, recent)

    assert normalize_company_name("Fresh Co") in scored_company_names(directory=tmp_path, max_age_days=90)


def test_scored_company_names_uses_newest_result_per_company(tmp_path):
    from entity_resolution import normalize_company_name

    # An old save and a fresh save for the SAME company - the fresh one
    # should win, so the company still counts as scored.
    old = datetime.now() - timedelta(days=200)
    recent = datetime.now() - timedelta(days=5)
    _save_result_at(_make_result("Re-Scored Co"), tmp_path, old)
    _save_result_at(_make_result("Re-Scored Co"), tmp_path, recent)

    assert normalize_company_name("Re-Scored Co") in scored_company_names(directory=tmp_path, max_age_days=90)


def test_has_usable_name_false_for_no_ascii_characters():
    assert has_usable_name("丸久小山園") is False
    assert has_usable_name("「ノーブランド品」") is False


def test_has_usable_name_true_for_ordinary_names():
    assert has_usable_name("Diamine") is True
    assert has_usable_name("Northwind Outdoor Gear Co.") is True


def test_has_usable_name_true_for_mixed_script():
    # Any ASCII-representable character at all is enough to produce a
    # distinguishable filename.
    assert has_usable_name("丸久小山園 USA") is True
