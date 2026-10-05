"""
Tests for batch_runner.py's skip-already-scored behavior: already-scored
brands are skipped by default (--rescore opts back in), so re-running a
batch against a CSV that overlaps a previous run - e.g. after merging a new
SmartScout export with an old one - doesn't silently re-pay to re-score
brands already on file.
"""

import csv

import persistence
from models import DraftScore, ManagerDecision, OutreachDraft, Prospect, QualificationResult, ResearchFindings
from persistence import save_result
from workflow import WorkflowResult

import batch_runner
from tests.helpers import patch_runner


def _brand_qualification() -> QualificationResult:
    return QualificationResult(
        overall_score=80, recommendation="PURSUE",
        wholesale_relationship_availability=80, product_business_fit=80,
        reseller_program_accessibility=80, contact_information_availability=80,
        marketplace_compatibility=80, amazon_resale_clarity=80,
        professional_operations_evidence=80, barriers_or_restrictions=80,
        recommended_next_action="contact them",
    )


def _make_result(company_name: str) -> WorkflowResult:
    drafts = {"relationship": OutreachDraft(subject="Hi", body="Body", strategy="relationship")}
    manager = ManagerDecision(
        draft_scores=[DraftScore(strategy="relationship", score=80, explanation="ok")],
        winning_strategy="relationship", winning_reason="best",
    )
    return WorkflowResult(
        prospect=Prospect(company_name=company_name), research=ResearchFindings(company_name=company_name),
        qualification=_brand_qualification(), drafts=drafts, manager_decision=manager,
    )


def _write_csv(path, brand_names):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Brand Name"])
        for name in brand_names:
            writer.writerow([name])


_OUTREACH_RESPONSES = {
    "Relationship Outreach Agent": OutreachDraft(subject="Hi", body="Body", strategy="relationship"),
    "Procurement Outreach Agent": OutreachDraft(subject="Hi", body="Body", strategy="procurement"),
    "Strategic Partnership Outreach Agent": OutreachDraft(subject="Hi", body="Body", strategy="partnership"),
    "Outreach Manager": ManagerDecision(
        draft_scores=[DraftScore(strategy=s, score=80, explanation="ok") for s in ("relationship", "procurement", "partnership")],
        winning_strategy="procurement", winning_reason="best",
    ),
}


async def test_already_scored_brand_is_skipped_by_default(tmp_path, monkeypatch):
    # conftest.py's autouse isolated_supplier_data fixture already points
    # persistence.RESULTS_DIR at an isolated tmp dir for every test - just
    # save without an explicit directory= so it lands there, matching
    # exactly where run_batch's internal scored_company_names() will look.
    save_result(_make_result("Already Scored Co"))

    csv_path = tmp_path / "export.csv"
    _write_csv(csv_path, ["Already Scored Co", "Brand New Co"])

    responses = {
        "Brand Research Agent": ResearchFindings(company_name="Brand New Co"),
        "Qualification Agent": _brand_qualification(),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    results = await batch_runner.run_batch(str(csv_path), allow_web_search=False)

    assert [r.prospect.company_name for r in results] == ["Brand New Co"]


async def test_rescore_processes_already_scored_brand_anyway(tmp_path, monkeypatch):
    save_result(_make_result("Already Scored Co"))

    csv_path = tmp_path / "export.csv"
    _write_csv(csv_path, ["Already Scored Co"])

    responses = {
        "Brand Research Agent": ResearchFindings(company_name="Already Scored Co"),
        "Qualification Agent": _brand_qualification(),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)

    results = await batch_runner.run_batch(str(csv_path), allow_web_search=False, skip_scored=False)

    assert [r.prospect.company_name for r in results] == ["Already Scored Co"]


async def test_all_brands_already_scored_processes_nothing(tmp_path, monkeypatch):
    save_result(_make_result("Already Scored Co"))

    csv_path = tmp_path / "export.csv"
    _write_csv(csv_path, ["Already Scored Co"])

    # No agent responses registered - if the batch tried to process
    # anything, FakeRunner would raise for an unregistered agent name.
    patch_runner(monkeypatch, {})

    results = await batch_runner.run_batch(str(csv_path), allow_web_search=False)

    assert results == []


async def test_name_with_no_ascii_characters_is_skipped_before_any_api_call(tmp_path, monkeypatch):
    csv_path = tmp_path / "export.csv"
    _write_csv(csv_path, ["丸久小山園", "Brand New Co"])

    responses = {
        "Brand Research Agent": ResearchFindings(company_name="Brand New Co"),
        "Qualification Agent": _brand_qualification(),
        **_OUTREACH_RESPONSES,
    }
    patch_runner(monkeypatch, responses)
    # If the unusable-name brand were researched anyway, FakeRunner would
    # still succeed (its name is irrelevant to agent dispatch) - the real
    # assertion is that it never shows up in the results or on disk.

    results = await batch_runner.run_batch(str(csv_path), allow_web_search=False)

    assert [r.prospect.company_name for r in results] == ["Brand New Co"]
    assert list(persistence.RESULTS_DIR.glob("prospect_*.json")) == []
