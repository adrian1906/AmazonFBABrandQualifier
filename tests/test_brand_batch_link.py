"""
Mode A (integrated) selection logic: PURSUE-by-default, include/exclude
overrides, and traceability back to the originating brand batch. No agent
calls are involved here - this is pure CSV/JSON selection logic.
"""

import csv
from pathlib import Path

import brand_batch_link
import persistence
from models import Prospect, ResearchFindings, QualificationResult, OutreachDraft, ManagerDecision, DraftScore
from workflow import WorkflowResult


def _make_result(company_name: str, status: str, score: int) -> WorkflowResult:
    prospect = Prospect(company_name=company_name, company_description=f"{company_name} description")
    research = ResearchFindings(company_name=company_name, verified_facts=["fact A"])
    qualification = QualificationResult(
        overall_score=score, recommendation=status,
        wholesale_relationship_availability=score, product_business_fit=score,
        reseller_program_accessibility=score, contact_information_availability=score,
        marketplace_compatibility=score, amazon_resale_clarity=score,
        professional_operations_evidence=score, barriers_or_restrictions=score,
        key_reasons=["good fit"], recommended_next_action="contact them",
    )
    drafts = {"relationship": OutreachDraft(subject="Hi", body="Body", strategy="relationship")}
    manager = ManagerDecision(
        draft_scores=[DraftScore(strategy="relationship", score=80, explanation="ok")],
        winning_strategy="relationship", winning_reason="best",
    )
    return WorkflowResult(prospect=prospect, research=research, qualification=qualification, drafts=drafts, manager_decision=manager)


def _write_summary(results_dir: Path, rows: list[dict]) -> Path:
    path = results_dir / "summary_20260828_140000.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "company_name", "qualification_score", "recommendation", "winning_strategy", "winning_subject", "recommended_next_action"])
        for i, row in enumerate(rows, start=1):
            writer.writerow([i, row["company_name"], row["qualification_score"], row["recommendation"], "relationship", "Hi", "contact them"])
    return path


def _setup(tmp_path, monkeypatch, rows) -> Path:
    results_dir = tmp_path / "batch_results"
    results_dir.mkdir()
    for row in rows:
        save_target = _make_result(row["company_name"], row["recommendation"], row["qualification_score"])
        persistence.save_result(save_target, directory=results_dir)
    summary_path = _write_summary(results_dir, rows)

    monkeypatch.setattr(brand_batch_link, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(
        brand_batch_link, "find_results",
        lambda name_fragment: persistence.find_results(name_fragment, directory=results_dir),
    )
    return summary_path


def test_default_selects_only_pursue_brands(tmp_path, monkeypatch):
    rows = [
        {"company_name": "Brand A", "recommendation": "PURSUE", "qualification_score": 85},
        {"company_name": "Brand B", "recommendation": "INVESTIGATE", "qualification_score": 55},
        {"company_name": "Brand C", "recommendation": "REJECT", "qualification_score": 10},
    ]
    summary_path = _setup(tmp_path, monkeypatch, rows)

    batch_id, entries = brand_batch_link.load_brand_batch(str(summary_path))

    assert {e.company_name for e in entries} == {"Brand A"}
    assert batch_id == "summary_20260828_140000"


def test_include_overrides_status_and_exclude_removes_a_pursue_brand(tmp_path, monkeypatch):
    rows = [
        {"company_name": "Brand A", "recommendation": "PURSUE", "qualification_score": 85},
        {"company_name": "Brand B", "recommendation": "HOLD", "qualification_score": 40},
    ]
    summary_path = _setup(tmp_path, monkeypatch, rows)

    _, entries = brand_batch_link.load_brand_batch(str(summary_path), include=["Brand B"], exclude=["Brand A"])
    assert {e.company_name for e in entries} == {"Brand B"}


def test_investigate_hold_reject_are_never_auto_selected(tmp_path, monkeypatch):
    rows = [
        {"company_name": "Investigate Co", "recommendation": "INVESTIGATE", "qualification_score": 55},
        {"company_name": "Hold Co", "recommendation": "HOLD", "qualification_score": 45},
        {"company_name": "Reject Co", "recommendation": "REJECT", "qualification_score": 5},
    ]
    summary_path = _setup(tmp_path, monkeypatch, rows)

    _, entries = brand_batch_link.load_brand_batch(str(summary_path))
    assert entries == []


def test_context_notes_carry_brand_qualifier_findings(tmp_path, monkeypatch):
    rows = [{"company_name": "Brand A", "recommendation": "PURSUE", "qualification_score": 85}]
    summary_path = _setup(tmp_path, monkeypatch, rows)

    _, entries = brand_batch_link.load_brand_batch(str(summary_path))
    assert "Brand A description" in entries[0].research_notes
    assert "fact A" in entries[0].research_notes
    assert entries[0].qualification_status == "PURSUE"
    assert entries[0].qualification_score == 85
